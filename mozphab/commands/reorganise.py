# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import sys

from collections import OrderedDict

from mozphab.conduit import conduit
from mozphab.exceptions import Error
from mozphab.logger import logger
from mozphab.helpers import augment_commits_from_body, prompt
from mozphab.spinner import wait_message
from mozphab.telemetry import telemetry


def to_llist(revisions):
    """Converts a list to a linked list.

    Parameters:
        revisions (list): a list of revisions to create a linked list

    Returns (dict) a linked list in the form `dict(rev1="rev2", rev2="rev3", rev3=None)`
    where `rev2` is the child of `rev1` and the parent of `rev3` which has no children.
    """
    llist = {}
    for i, revision in enumerate(revisions):
        child = revisions[i + 1] if i + 1 < len(revisions) else None
        llist[revision] = child
    return llist


def walk_llist(llist, allow_multiple_heads=False):
    """Parse the llist for multiple heads and return a unique list of elements.

    Parameters:
        llist (dict): A linked list. {A_id: B_id, B_id: None}
        allow_multiple_heads (bool): Fail if multiple heads found.
    """
    referenced_children = [r for r in llist.values() if r]

    # Find head
    node = None
    for n in sorted(llist.keys()):
        if n not in referenced_children:
            if node:
                if not allow_multiple_heads:
                    raise Error("Multiple heads found.")
                break
            node = n
    if not node:
        raise Error("Failed to find head.")

    # Walk list, checking for loops
    nodes = []
    while node:
        nodes.append(node)
        child = llist.get(node)
        if child and child in nodes:
            raise Error("Dependency loop")

        node = child

    return nodes


def stack_transactions(remote_phids, local_phids):
    """Prepare transactions to set the stack as provided in local_phids.

    Returns (OrderedDict) transactions for PHID as defined in
    `differential.revision.edit`.
    """
    remote_list = to_llist(remote_phids)
    local_list = to_llist(local_phids)

    transactions = OrderedDict()
    for revision in remote_phids + local_phids:
        transactions[revision] = []

    # Remote child no longer in stack
    for revision in [r for r in remote_phids if r not in local_phids]:
        child = remote_list[revision]
        if child is not None:
            transactions[revision].append(("children.remove", [child]))

        remote_list[revision] = None
        walk_llist(remote_list, allow_multiple_heads=True)

    # New revisions
    for revision in [r for r in local_phids if r not in remote_phids]:
        child = local_list[revision]
        if child is None:
            if revision in remote_list and remote_list[revision] is not None:
                transactions[revision].append(
                    ("children.remove", [remote_list[revision]])
                )
        else:
            transactions[revision].append(("children.set", [child]))

        remote_list[revision] = child
        walk_llist(remote_list, allow_multiple_heads=True)

    # Modify child
    for revision in [r for r in remote_phids if r in local_phids]:
        if local_list[revision] != remote_list[revision]:
            child = local_list[revision]
            if child is None:
                # Remove child if the last child is a parent remotely
                if revision in remote_list and remote_list[revision] is not None:
                    transactions[revision].append(
                        ("children.remove", [remote_list[revision]])
                    )
            else:
                transactions[revision].append(("children.set", [child]))

            remote_list[revision] = child
            walk_llist(remote_list, allow_multiple_heads=True)

    # Abandon
    for revision in [r for r in remote_phids if r not in local_phids]:
        transactions[revision].append(("abandon", True))
        del remote_list[revision]
        walk_llist(remote_list, allow_multiple_heads=True)

    assert ":".join(walk_llist(remote_list)) == ":".join(walk_llist(local_list))

    conduit_transactions = {}
    for revision, transaction_list in transactions.items():
        if not transaction_list:
            continue
        conduit_transactions.setdefault(revision, [])
        for transaction in transaction_list:
            k, v = transaction
            if k == "children.set" and v:
                v = v
            conduit_transactions[revision].append({"type": k, "value": v})

    return conduit_transactions


def reorganise(repo, args):
    telemetry.metrics.mozphab.submission.preparation_time.start()

    with wait_message("Checking connection to Phabricator."):
        # Check if raw Conduit API can be used
        if not conduit.check():
            raise Error("Failed to use Conduit API")

    # Find and preview commits to submits.
    with wait_message("Looking for commits.."):
        commits = repo.commit_stack()

    if not commits:
        raise Error("Failed to find any commits to reorganise.")

    with wait_message("Loading commits.."):
        augment_commits_from_body(commits)

    localstack_ids = [c["rev-id"] for c in commits]
    if None in localstack_ids:
        names = [c["name"] for c in commits if c["rev-id"] is None]
        plural = len(names) > 1
        raise Error(
            "Found new commit{plural} in the local stack: {names}.\n"
            "Please submit {them} separately and call the `reorg` again.".format(
                plural="s" if plural else "",
                them="them" if plural else "it",
                names=", ".join(names),
            )
        )

    logger.warning(
        "Reorganisation based on {} commit{}:".format(
            len(commits),
            "" if len(commits) == 1 else "s",
        )
    )

    # Get PhabricatorStack
    # Errors will be raised later in the `walk_llist` method
    with wait_message("Detecting the remote stack..."):
        try:
            phabstack = conduit.get_stack(localstack_ids)
        except Error:
            logger.error("Remote stack is not linear.")
            raise

    # Preload the phabricator stack
    with wait_message("Preloading Phabricator stack revisions..."):
        conduit.get_revisions(phids=list(phabstack.keys()))

    if phabstack:
        try:
            phabstack_phids = walk_llist(phabstack)
        except Error:
            logger.error(
                "Remote stack is not linear.\n"
                "Detected stack:\n{}".format(
                    " <- ".join(conduit.phids_to_ids(list(phabstack.keys())))
                )
            )
            raise
    else:
        phabstack_phids = []

    localstack_phids = conduit.ids_to_phids(localstack_ids)
    try:
        transactions = stack_transactions(phabstack_phids, localstack_phids)
    except Error:
        logger.error("Unable to prepare stack transactions.")
        raise

    if not transactions:
        raise Error("Reorganisation is not needed.")

    logger.warning("Stack will be reorganised:")
    for phid, rev_transactions in transactions.items():
        node_id = conduit.phid_to_id(phid)
        if "abandon" in [t["type"] for t in rev_transactions]:
            logger.info(" * {} will be abandoned".format(node_id))
        else:
            for t in rev_transactions:
                if t["type"] == "children.set":
                    logger.info(
                        " * {child} will depend on {parent}".format(
                            child=conduit.phid_to_id(t["value"][0]),
                            parent=node_id,
                        )
                    )
                if t["type"] == "children.remove":
                    logger.info(
                        " * {child} will no longer depend on {parent}".format(
                            child=conduit.phid_to_id(t["value"][0]),
                            parent=node_id,
                        )
                    )

    telemetry.metrics.mozphab.submission.preparation_time.stop()

    if args.yes:
        pass
    else:
        res = prompt("Perform reorganisation", ["Yes", "No"])
        if res == "No":
            sys.exit(1)

    telemetry.metrics.mozphab.submission.process_time.start()

    with wait_message("Applying transactions..."):
        for phid, rev_transactions in transactions.items():
            conduit.edit_revision(rev_id=phid, transactions=rev_transactions)

    telemetry.metrics.mozphab.submission.process_time.stop()
    logger.info("Stack has been reorganised.")


def add_parser(parser):
    reorg_parser = parser.add_parser("reorg", help="Reorganise commits in Phabricator")
    reorg_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Reorganise without confirmation (default: False)",
    )
    reorg_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions",
    )
    reorg_parser.add_argument(
        "--upstream",
        "--remote",
        "-u",
        action="append",
        help='Set upstream branch to detect the starting commit (default: "")',
    )
    reorg_parser.add_argument(
        "start_rev",
        nargs="?",
        default="(auto)",
        help="Start revision of range to reorganise (default: detected)",
    )
    reorg_parser.add_argument(
        "end_rev",
        nargs="?",
        default=".",
        help="End revision of range to reorganise (default: current commit)",
    )
    reorg_parser.set_defaults(func=reorganise, needs_repo=True)
