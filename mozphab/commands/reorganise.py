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
    Tuple,
)

from mozphab.conduit import conduit
from mozphab.config import config
from mozphab.exceptions import Error
from mozphab.helpers import BUG_ID_RE, augment_commits_from_body, prompt
from mozphab.logger import logger
from mozphab.repository import Repository
from mozphab.spinner import wait_message
from mozphab.telemetry import telemetry


def create_hyperlink(text: str, url: str) -> str:
    """Create a terminal hyperlink using ANSI escape sequences."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def linkify_revision_id(
    revision_id: str, phab_url: str, hyperlinks_enabled: bool = True
) -> str:
    """Convert revision ID like 'D123456' to a clickable Phabricator link."""
    if not hyperlinks_enabled:
        return revision_id
    # Extract the numeric part (remove 'D' prefix)
    numeric_id = revision_id.removeprefix("D")
    phabricator_url = f"{phab_url}/D{numeric_id}"
    return create_hyperlink(revision_id, phabricator_url)


def linkify_bugs_in_text(
    text: str, bmo_url: str, hyperlinks_enabled: bool = True
) -> str:
    """Find bug numbers in text and make them clickable Bugzilla links."""
    if not hyperlinks_enabled:
        return text

    def replace_bug(match):
        bug_number = match.group(1)
        bugzilla_url = f"{bmo_url}/show_bug.cgi?id={bug_number}"
        return create_hyperlink(f"Bug {bug_number}", bugzilla_url)

    return BUG_ID_RE.sub(replace_bug, text)


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


def remove_or_set_child(
    local_list: Dict, remote_list: Dict, revision: str
) -> Optional[Tuple[str, List[str]]]:
    """Return a `children.*` transaction for the revision based on remote/local state.

    Return `None` if no transaction is required.
    """
    # Set children if child is present.
    child = local_list[revision]
    if child is not None:
        return ("children.set", [child])

    # Remove child if the last child is a parent remotely
    if revision in remote_list and remote_list[revision] is not None:
        return ("children.remove", [remote_list[revision]])

    # No transaction otherwise.
    return None


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

        transaction = remove_or_set_child(local_list, remote_list, revision)
        if transaction:
            transactions[revision].append(transaction)

        remote_list[revision] = child
        walk_llist(remote_list, allow_multiple_heads=True)

    # Modify child
    remote_revisions_found_in_local = [r for r in remote_phids if r in local_phids]
    for revision in remote_revisions_found_in_local:
        if local_list[revision] != remote_list[revision]:
            child = local_list[revision]

            transaction = remove_or_set_child(local_list, remote_list, revision)
            if transaction:
                transactions[revision].append(transaction)

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


def force_stack_transactions(
    remote_phids: List[str],
    local_phids: List[str],
    abandoned_revisions: Container[str],
    no_abandon_unconnected: bool = False,
) -> Dict[str, List[Dict]]:
    """Prepare transactions for force mode: synchronize remote to match local exactly.

    Force mode logic:
    1. Abandon all remote revisions not found locally in the range (unless no_abandon_unconnected is True)
    2. Unlink all revisions (remove all existing parent-children relationships)
    3. Relink revisions to match local state exactly

    Args:
        remote_phids: List of remote PHIDs in the stack
        local_phids: List of local PHIDs in the desired order
        abandoned_revisions: Set of already abandoned revision PHIDs
        no_abandon_unconnected: If True, do not abandon remote revisions not found locally

    Returns a dict of transactions for PHID as defined in `differential.revision.edit`.
    """
    local_list = to_llist(local_phids)
    transactions = OrderedDict()

    # Initialize transactions for all revisions that will be touched
    all_phids = set(remote_phids + local_phids)
    for revision in all_phids:
        transactions[revision] = []

    # Step 1: Abandon remote revisions not found locally
    remote_revisions_missing_from_local = [
        r for r in remote_phids if r not in local_phids
    ]

    if not no_abandon_unconnected:
        for revision in remote_revisions_missing_from_local:
            # Only abandon if not already abandoned
            if revision not in abandoned_revisions:
                transactions[revision].append(("abandon", True))

    # Step 2: Unlink all revisions - we'll use children.set with empty list to
    # remove all children. This effectively unlinks all existing relationships
    for revision in remote_phids:
        if revision not in local_phids and not no_abandon_unconnected:
            # Don't unset children for revisions that will be abandoned - no need to modify
            # their relationships since they'll be abandoned anyway
            continue
        transactions[revision].append(("children.set", []))

    # Step 3: Relink revisions to match local state exactly
    for revision in local_phids:
        child = local_list[revision]
        if child is not None:
            transactions[revision].append(("children.set", [child]))

    # Clean up empty transaction lists
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


def show_revision_glossary(
    transactions: Dict[str, List[Dict]],
    revisions: List[dict],
    repo: Repository,
    hyperlinks_enabled: bool = True,
):
    """Show a glossary with commit messages for all referenced revisions."""
    # Collect all referenced revision PHIDs
    referenced_phids = set()
    for phid, rev_transactions in transactions.items():
        referenced_phids.add(phid)
        for transaction in rev_transactions:
            if transaction["type"] in ["children.set", "children.remove"]:
                if transaction["value"]:
                    referenced_phids.update(transaction["value"])

    if not referenced_phids:
        return

    # Create a mapping from PHID to revision data
    phid_to_revision = {revision["phid"]: revision for revision in revisions}

    # Display the glossary
    logger.info("")
    logger.info("Referenced revisions:")
    for phid in sorted(
        referenced_phids,
        key=lambda p: phid_to_revision[p]["id"] if p in phid_to_revision else 0,
    ):
        if phid not in phid_to_revision:
            continue
        revision_id = f"D{phid_to_revision[phid]['id']}"
        linked_revision_id = linkify_revision_id(
            revision_id, repo.phab_url, hyperlinks_enabled
        )
        if phid in phid_to_revision:
            title = phid_to_revision[phid]["fields"].get("title", "(no title)")
            linked_title = linkify_bugs_in_text(title, repo.bmo_url, hyperlinks_enabled)
            logger.info(" * %s: %s", linked_revision_id, linked_title)
        else:
            logger.info(" * %s: (unknown)", linked_revision_id)


def format_stack(
    phids: List[str],
    title: str,
    phid_to_revision: Dict[str, dict],
    repo: Repository,
    hyperlinks_enabled: bool = True,
) -> None:
    """Format and display a stack of revisions."""
    if not phids:
        logger.info("%s: (empty)", title)
        return
    logger.info("%s:", title)
    # Reverse the order so bottom patch is at the bottom (stack order)
    reversed_phids = list(reversed(phids))
    for phid in reversed_phids:
        revision_id = f"D{phid_to_revision[phid]['id']}"
        linked_revision_id = linkify_revision_id(
            revision_id, repo.phab_url, hyperlinks_enabled
        )
        if phid in phid_to_revision:
            title_text = phid_to_revision[phid]["fields"].get("title", "(no title)")
            linked_title_text = linkify_bugs_in_text(
                title_text, repo.bmo_url, hyperlinks_enabled
            )
            logger.info("○ %s %s", linked_revision_id, linked_title_text)
        else:
            logger.info("○ %s (unknown)", linked_revision_id)


def show_verbose_stack_info(
    phabstack_phids: List[str],
    localstack_phids: List[str],
    revisions: List[dict],
    repo: Repository,
    hyperlinks_enabled: bool = True,
):
    """Show detailed information about the current and future stack shapes."""
    phid_to_revision = {revision["phid"]: revision for revision in revisions}

    logger.info("")
    logger.info("Stack reorganization details:")
    format_stack(
        phabstack_phids,
        "Current remote stack",
        phid_to_revision,
        repo,
        hyperlinks_enabled,
    )
    format_stack(
        localstack_phids,
        "Target local stack",
        phid_to_revision,
        repo,
        hyperlinks_enabled,
    )
    logger.info("")


def reorganise_inner(repo: Repository, args: argparse.Namespace):
    """Reorganise the stack on Phabricator to match the stack in the local VCS."""
    telemetry().submission.preparation_time.start()

    # Validate argument combinations
    if args.no_abandon_unconnected and not args.force:
        raise Error("--no-abandon-unconnected can only be used with --force")

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

    localstack_ids = [commit.rev_id for commit in commits]
    if not all(localstack_ids):
        if args.force:
            raise Error(
                "Force mode requires all local revisions to be present on Phabricator."
            )
        names = [commit.name for commit in commits if commit.rev_id is None]
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
            if args.force:
                # In force mode, we ignore remote stack structure issues
                logger.warning(
                    "Remote stack is not linear, but continuing in force mode.\n"
                    "Detected stack:\n{}".format(
                        " <- ".join(conduit.phids_to_ids(list(phabstack.keys())))
                    )
                )
                phabstack_phids = list(phabstack.keys())
            else:
                logger.error(
                    "Remote stack is not linear.\nDetected stack:\n{}".format(
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

    if args.force:
        transactions = force_stack_transactions(
            phabstack_phids,
            localstack_phids,
            abandoned_revisions,
            no_abandon_unconnected=args.no_abandon_unconnected,
        )
    else:
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
        logger.info("Reorganisation is not needed.")
        return

    # Determine hyperlinks setting from config and args
    hyperlinks_enabled = config.hyperlinks and not args.no_hyperlinks

    if args.force:
        logger.warning("Stack will be forcibly synchronized:")
    else:
        logger.warning("Stack will be reorganised:")

    for phid, rev_transactions in transactions.items():
        if phid not in phid_to_id:
            # Skip PHIDs that are not in our mapping (can happen in tests)
            continue
        node_id = f"D{phid_to_id[phid]}"
        linked_node_id = linkify_revision_id(node_id, repo.phab_url, hyperlinks_enabled)
        if any(transaction["type"] == "abandon" for transaction in rev_transactions):
            logger.info(" * {} will be abandoned".format(linked_node_id))
        else:
            for t in rev_transactions:
                if t["type"] == "children.set":
                    if t["value"] and t["value"][0] in phid_to_id:  # Has children
                        child_id = f"D{phid_to_id[t['value'][0]]}"
                        linked_child_id = linkify_revision_id(
                            child_id, repo.phab_url, hyperlinks_enabled
                        )
                        logger.info(
                            " * {child} will depend on {parent}".format(
                                child=linked_child_id,
                                parent=linked_node_id,
                            )
                        )
                    elif args.force:
                        logger.info(
                            " * {} will have all dependencies removed".format(
                                linked_node_id
                            )
                        )
                if t["type"] == "children.remove":
                    if t["value"] and t["value"][0] in phid_to_id:
                        child_id = f"D{phid_to_id[t['value'][0]]}"
                        linked_child_id = linkify_revision_id(
                            child_id, repo.phab_url, hyperlinks_enabled
                        )
                        logger.info(
                            " * {child} will no longer depend on {parent}".format(
                                child=linked_child_id,
                                parent=linked_node_id,
                            )
                        )

    # Show glossary with commit messages for all referenced revisions (but not in verbose mode)
    if not args.verbose:
        show_revision_glossary(transactions, revisions, repo, hyperlinks_enabled)
    else:
        # Show verbose information at the bottom
        show_verbose_stack_info(
            phabstack_phids, localstack_phids, revisions, repo, hyperlinks_enabled
        )

    telemetry().submission.preparation_time.stop()

    if args.yes:
        pass
    else:
        if args.force:
            res = prompt("Perform force synchronization", ["Yes", "No"])
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


def reorganise(repo: Repository, args: argparse.Namespace):
    """Reorganise the stack on Phabricator to match the stack in the local VCS."""
    try:
        reorganise_inner(repo, args)
    except Error as e:
        # Check if the `force` flag was not set and print a hint about --force mode
        if not args.force:
            logger.warning(
                "Reorganisation failed. You might try using --force to bypass "
                "stack structure checks and force synchronization."
            )
        raise e


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
        help=(
            "Git only: Set remote to detect the starting commit. Overrides "
            "`git.remote` in config."
        ),
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
    reorg_parser.add_argument(
        "--force",
        action="store_true",
        help="Force synchronization: abandon remote revisions not found locally, "
        "unlink all revisions, then relink to match local state.",
    )
    reorg_parser.add_argument(
        "--no-abandon-unconnected",
        action="store_true",
        help="When used with --force, do not abandon remote revisions that are not "
        "connected to the local stack. Useful for managing multiple patch series.",
    )
    reorg_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed information about stack reorganization, including "
        "previous and future stack shapes.",
    )
    reorg_parser.add_argument(
        "--no-hyperlinks",
        action="store_true",
        help="Disable terminal hyperlinks for revision IDs and bug numbers.",
    )
    reorg_parser.set_defaults(func=reorganise, needs_repo=True)
