# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import sys

from collections import OrderedDict
from typing import (
    Container,
    Dict,
    List,
    Optional,
)

from mozphab.conduit import conduit
from mozphab.exceptions import Error
from mozphab.logger import logger
from mozphab.helpers import augment_commits_from_body, prompt
from mozphab.repository import Repository
from mozphab.spinner import wait_message
from mozphab.telemetry import telemetry


def to_llist(revisions: List[str]) -> Dict[str, Optional[str]]:
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


def walk_llist(
    llist: Dict[str, Optional[str]], allow_multiple_heads: bool = False
) -> List[str]:
    """Parse the llist for multiple heads and return a unique list of elements.

    Parameters:
        llist (dict): A linked list. {A_id: B_id, B_id: None}
        allow_multiple_heads (bool): Fail if multiple heads found.
    """
    referenced_children = [child for child in llist.values() if child]

    # Find head
    head = None
    for parent in sorted(llist.keys()):
        if parent not in referenced_children:
            if head:
                if not allow_multiple_heads:
                    raise Error("Multiple heads found.")
                break
            head = parent
    if not head:
        raise Error("Failed to find head.")

    # Walk list, checking for loops
    nodes = []
    while head:
        nodes.append(head)
        child = llist.get(head)
        if child and child in nodes:
            raise Error("Dependency loop")

        head = child

    return nodes


def stack_transactions(
    remote_phids: List[str],
    local_phids: List[str],
    abandoned_revisions: Container[str],
    no_abandon: bool = False,
) -> Dict[str, List[Dict]]:
    """Prepare transactions to set the stack as provided in local_phids.

    Returns a dict of transactions for PHID as defined in
    `differential.revision.edit`.
    """
    remote_list = to_llist(remote_phids)
    local_list = to_llist(local_phids)

    transactions = OrderedDict()
    for revision in remote_phids + local_phids:
        transactions[revision] = []

    # Remote child no longer in stack
    remote_revisions_missing_from_local = [
        r for r in remote_phids if r not in local_phids
    ]
    for revision in remote_revisions_missing_from_local:
        child = remote_list[revision]
        if child is not None:
            transactions[revision].append(("children.remove", [child]))

        remote_list[revision] = None
        walk_llist(remote_list, allow_multiple_heads=True)

    # New revisions
    local_revisions_missing_from_remote = [
        r for r in local_phids if r not in remote_phids
    ]
    for revision in local_revisions_missing_from_remote:
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
    remote_revisions_found_in_local = [r for r in remote_phids if r in local_phids]
    for revision in remote_revisions_found_in_local:
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
    for revision in remote_revisions_missing_from_local:
        if no_abandon:
            # `--no-abandon` should cause revisions to never be abandoned.
            logger.debug(
                f"Skipping abandon transaction for {revision} due to `--no-abandon`."
            )
        elif revision not in abandoned_revisions:
            # Avoid abandoning a revision if it is already in `abandoned` state.
            transactions[revision].append(("abandon", True))

        del remote_list[revision]
        walk_llist(remote_list, allow_multiple_heads=True)

    assert ":".join(walk_llist(remote_list)) == ":".join(walk_llist(local_list))

    conduit_transactions = {}
    for revision, transaction_list in transactions.items():
        if not transaction_list:
            continue
        conduit_transactions.setdefault(revision, [])

        for trans_type, trans_value in transaction_list:
            conduit_transactions[revision].append(
                {"type": trans_type, "value": trans_value}
            )

    return conduit_transactions


def convert_stackgraph_to_linear(
    stack_graph: Dict[str, List[str]],
    phid_to_id: Dict[str, int],
) -> Dict[str, Optional[str]]:
    """Converts the `stackGraph` data from Phabricator to a linear format.

    Ensures each revision has only a single successor revision.
    """
    linear_stackgraph = {}

    for successor_phid, predecessor_phid_list in stack_graph.items():
        for predecessor_phid in predecessor_phid_list:
            # If we find a predecessor node in our linear stackgraph then
            # that node has multiple children.
            if predecessor_phid in linear_stackgraph:
                # Get the ID of the revision with multiple children.
                rev_id = phid_to_id[predecessor_phid]

                raise Error(f"Revision D{rev_id} has multiple children.")

            linear_stackgraph[predecessor_phid] = successor_phid

    # Set any heads as having no successor. Heads are in the values of
    # `linear_stackgraph` but not yet added as keys.
    heads = (
        successor
        # Use `list` here to avoid inspecting the list while we iterate over it,
        # causing a `RuntimeError: dictionary changed size during iteration`.
        for successor in list(linear_stackgraph.values())
        if successor not in linear_stackgraph
    )
    for head in heads:
        linear_stackgraph[head] = None

    return linear_stackgraph


def reorganise(repo: Repository, args: argparse.Namespace):
    """Reorganise the stack on Phabricator to match the stack in the local VCS."""
    telemetry().submission.preparation_time.start()

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
    if not all(localstack_ids):
        names = [c["name"] for c in commits if c["rev-id"] is None]
        plural = len(names) > 1
        raise Error(
            "Found new commit{plural} in the local stack: {names}.\n"
            "Please submit {them} separately and call `moz-phab reorg` again.".format(
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

    # Preload the phabricator stack
    with wait_message("Preloading Phabricator stack revisions..."):
        revisions = conduit.get_revisions(ids=localstack_ids)

    if not revisions:
        raise Error("Could not find revisions on Phabricator.")

    # Merge all the existing stackgraphs into one. Any repeated keys
    # will have the same values.
    stack_graph = {
        predecessor: successors
        for revision in revisions
        for predecessor, successors in revision["fields"]["stackGraph"].items()
    }

    # Fetch data about any revisions that are missing from local stack.
    revisions = conduit.get_revisions(phids=list(stack_graph.keys()))

    phid_to_id = {revision["phid"]: revision["id"] for revision in revisions}

    try:
        # Validate the `stackGraph` field from our remote revisions.
        phabstack = convert_stackgraph_to_linear(stack_graph, phid_to_id)
    except Error:
        logger.error("Remote stack is not linear.")
        raise

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
    abandoned_revisions = {
        revision["phid"]
        for revision in revisions
        if revision["fields"]["status"]["value"] == "abandoned"
    }
    try:
        transactions = stack_transactions(
            phabstack_phids,
            localstack_phids,
            abandoned_revisions,
            no_abandon=args.no_abandon,
        )
    except Error:
        logger.error("Unable to prepare stack transactions.")
        raise

    if not transactions:
        raise Error("Reorganisation is not needed.")

    logger.warning("Stack will be reorganised:")
    for phid, rev_transactions in transactions.items():
        node_id = conduit.phid_to_id(phid)
        if any(transaction["type"] == "abandon" for transaction in rev_transactions):
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

    telemetry().submission.preparation_time.stop()

    if args.yes:
        pass
    else:
        res = prompt("Perform reorganisation", ["Yes", "No"])
        if res == "No":
            sys.exit(1)

    telemetry().submission.process_time.start()

    with wait_message("Applying transactions..."):
        for phid, rev_transactions in transactions.items():
            conduit.apply_transactions_to_revision(
                rev_id=phid,
                transactions=rev_transactions,
            )

    telemetry().submission.process_time.stop()
    logger.info("Stack has been reorganised.")


def add_parser(parser):
    reorg_parser = parser.add_parser("reorg", help="Reorganise commits in Phabricator.")
    reorg_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Reorganise without confirmation (default: False).",
    )
    reorg_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions.",
    )
    reorg_parser.add_argument(
        "--upstream",
        "--remote",
        "-u",
        action="append",
        help='Set upstream branch to detect the starting commit (default: "").',
    )
    reorg_parser.add_argument(
        "start_rev",
        nargs="?",
        default="(auto)",
        help="Start revision of range to reorganise (default: detected).",
    )
    reorg_parser.add_argument(
        "end_rev",
        nargs="?",
        default=".",
        help="End revision of range to reorganise (default: current commit).",
    )
    reorg_parser.add_argument(
        "--no-abandon",
        action="store_true",
        dest="no_abandon",
        help="Do not abandon revisions during reorg.",
    )
    reorg_parser.set_defaults(func=reorganise, needs_repo=True)
