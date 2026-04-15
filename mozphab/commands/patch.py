# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import re
import subprocess
from typing import Optional

from mozphab.conduit import conduit
from mozphab.config import (
    Config,
    config,
)
from mozphab.exceptions import Error, NonLinearException, NotFoundError
from mozphab.helpers import prepare_body, prompt, short_node
from mozphab.logger import logger
from mozphab.mercurial import Mercurial
from mozphab.patch import apply_patch
from mozphab.repository import (
    Repository,
)
from mozphab.spinner import wait_message


def get_base_ref(diff: dict) -> Optional[str]:
    """Given a diff, return the base revision SHA the diff was based on."""
    for ref in diff["fields"].get("refs", []):
        if ref["type"] == "base":
            return ref["identifier"]


def get_diff_by_id(diff_id: int) -> tuple[str, dict]:
    """Retrieves a diff from Phabricator

    Args:
    * diff_id: the ID of the diff to retrieve

    Returns:
    * a tuple containing:
        * the PHID of the requested diff
        * the dictionary returned by Phabricator
    """
    diff_dict = conduit.get_diffs(ids=[diff_id])
    if not diff_dict:
        raise NotFoundError(f"Could not find diff with ID of {diff_id}.")

    if len(diff_dict) != 1:
        raise Error(f"Unexpected result received from Phabricator for Diff {diff_id}.")

    requested_diff_phid = list(diff_dict.keys())[0]
    return requested_diff_phid, diff_dict[requested_diff_phid]


def update_revision_with_new_diff(revs: list[dict], diff: dict) -> None:
    """Updates the revision to point to the given diff if they are related

    Args:
    * revs: list of revisions
    * diff: the new diff to point to

    Returns:
    * None if the diff is related to a revision in the list

    Raises:
    * Error if no relation is found
    """
    for rev in revs:
        if diff["fields"]["revisionPHID"] == rev["phid"]:
            rev["fields"]["diffPHID"] = diff["phid"]
            return

    raise Error(f"Diff {diff['id']} is not related to any revision in the stack.")


def resolve_branch_name(
    args: argparse.Namespace, config: Config, rev_id: str
) -> Optional[str]:
    """Resolve the branch name for the resulting patch.

    Use the value passed from `--name` on the CLI if possible. If
    `--no-commit` is passed, we don't need a branch name since we won't
    be committing to the VCS. Otherwise, format the patch from the
    `patch.branch_name_template` config knob.

    `patch.branch_name_template` supports a single format string, `rev_id`.
    """
    if args.name:
        # Return the value passed from the CLI.
        return args.name

    if args.no_commit or not config.create_commit:
        # `no_commit` implies no branch name.
        return None

    # Build the branch name from the configured template.
    return config.branch_name_template.format(rev_id=rev_id)


def _get_ancestors_from_stack_graph(
    stack_graph: dict[str, list[str]], target_phid: str
) -> list[str]:
    """Walk the stackGraph to find ancestor PHIDs of the target revision.

    Returns a list of ancestor PHIDs ordered from direct parent to root.
    Raises NonLinearException if any ancestor has multiple parents.
    """
    ancestors = []
    current = target_phid
    seen = {target_phid}
    while True:
        parents = stack_graph.get(current, [])
        if not parents:
            break
        if len(parents) > 1:
            raise NonLinearException()
        parent = parents[0]
        if parent in seen:
            break
        seen.add(parent)
        ancestors.append(parent)
        current = parent
    return ancestors


def _get_children_from_stack_graph(
    stack_graph: dict[str, list[str]], target_phid: str
) -> list[str]:
    """Walk the stackGraph to find child PHIDs of the target revision.

    Returns a list of child PHIDs ordered from direct child to leaf.
    Raises NonLinearException if any revision has multiple children.
    """
    # Build reverse mapping: parent_phid -> [child_phids]
    children_map: dict[str, list[str]] = {}
    for phid, parents in stack_graph.items():
        for parent in parents:
            children_map.setdefault(parent, []).append(phid)

    children = []
    current = target_phid
    seen = {target_phid}
    while True:
        kids = children_map.get(current, [])
        if not kids:
            break
        if len(kids) > 1:
            raise NonLinearException()
        child = kids[0]
        if child in seen:
            break
        seen.add(child)
        children.append(child)
        current = child
    return children


def _filter_abandoned_phids(
    phids: list[str], related_by_phid: dict[str, dict]
) -> list[str]:
    """Filter out PHIDs that are abandoned or missing from the response."""
    return [
        p
        for p in phids
        if p in related_by_phid
        and related_by_phid[p]["fields"]["status"]["value"] != "abandoned"
    ]


def _fetch_and_filter_related(
    ancestor_phids: list[str],
    children_phids: list[str],
    include_abandoned: bool,
) -> tuple[list[str], list[str], dict[str, dict]]:
    """Fetch related revisions and filter out abandoned/inaccessible ones.

    Returns updated (ancestor_phids, children_phids, related_by_phid).
    """
    all_related_phids = ancestor_phids + children_phids
    if not all_related_phids:
        return ancestor_phids, children_phids, {}

    with wait_message("Fetching related revisions.."):
        all_related = conduit.get_revisions(phids=all_related_phids)
    related_by_phid = {r["phid"]: r for r in all_related}

    # Filter out abandoned ancestors (always filtered) and any
    # PHIDs that weren't returned (e.g. restricted access).
    ancestor_phids = _filter_abandoned_phids(ancestor_phids, related_by_phid)
    # Filter out abandoned children unless --include-abandoned is set.
    # Also drop PHIDs missing from the response.
    if not include_abandoned:
        children_phids = _filter_abandoned_phids(children_phids, related_by_phid)
    else:
        children_phids = [p for p in children_phids if p in related_by_phid]

    return ancestor_phids, children_phids, related_by_phid


def patch(repo: Repository, args: argparse.Namespace):
    """Patch repository from Phabricator's revisions.

    By default:
    * perform sanity checks
    * find the base commit
    * create a new branch/bookmark/topic
    * apply the patches and commit the changes

    args.no_commit or config.create_commit is False - no commit will be created after applying diffs
    args.apply_to - <head|tip|branch> (default: branch)
        branch - find base commit and apply on top of it
        head/tip - apply changes to current commit
    args.raw is True - only print out the diffs (--force doesn't change anything)

    Raises:
    * Error if uncommitted changes are present in the working tree
    * Error if Phabricator revision is not found
    * Error if `--apply-to base` and no base commit found in the first diff
    * Error if base commit not found in repository
    * Error if `--diff-id` does not belong to any revision in the stack
    """
    # Check if raw Conduit API can be used
    with wait_message("Checking connection to Phabricator."):
        # Check if raw Conduit API can be used
        if not conduit.check():
            raise Error("Failed to use Conduit API")

    if not args.raw:
        # Check if local and remote VCS matches
        with wait_message("Checking VCS"):
            repo.check_vcs()

        # Look for any uncommitted changes
        with wait_message("Checking repository.."):
            clean = repo.is_worktree_clean()

        if not clean:
            raise Error(
                "Uncommitted changes present. Please %s them or commit before patching."
                % ("shelve" if isinstance(repo, Mercurial) else "stash")
            )

    # Get the target revision
    with wait_message("Fetching D%s.." % args.revision_id):
        revs = conduit.get_revisions(ids=[args.revision_id])

    if not revs:
        raise Error("Revision not found")

    revision = revs[0]

    if not args.skip_dependencies:
        # Use the stackGraph field from the revision to determine
        # ancestors and children without extra API calls.
        stack_graph = revision["fields"]["stackGraph"]

        try:
            children_phids = _get_children_from_stack_graph(
                stack_graph, revision["phid"]
            )
            non_linear = False
        except NonLinearException:
            children_phids = []
            non_linear = True

        try:
            ancestor_phids = _get_ancestors_from_stack_graph(
                stack_graph, revision["phid"]
            )
        except NonLinearException:
            raise Error("Non linear dependency detected. Unable to patch the stack.")

        # Fetch all related revisions to get metadata and filter out
        # abandoned or inaccessible ones.
        ancestor_phids, children_phids, related_by_phid = _fetch_and_filter_related(
            ancestor_phids, children_phids, args.include_abandoned
        )

        patch_children = True
        if children_phids:
            if args.yes or config.always_full_stack:
                patch_children = True

            else:
                children_msg = (
                    "a child commit" if len(children_phids) == 1 else "child commits"
                )
                res = prompt(
                    "Revision D%s has %s.  Would you like to patch the "
                    "full stack?." % (args.revision_id, children_msg),
                    ["Yes", "No", "Always"],
                )
                if res == "Always":
                    config.always_full_stack = True
                    config.write()

                patch_children = res == "Yes" or res == "Always"

            if patch_children:
                if non_linear and not args.yes:
                    logger.warning(
                        "Revision D%s has a non-linear successor graph.\n"
                        "Unable to apply the full stack.",
                        args.revision_id,
                    )
                    res = prompt("Continue with only part of the stack?", ["Yes", "No"])
                    if res == "No":
                        return

        # Build the final revisions list from already-fetched data.
        child_phids = children_phids if (children_phids and patch_children) else []

        if ancestor_phids or child_phids:
            ancestor_revs = [related_by_phid[p] for p in ancestor_phids]
            child_revs = [related_by_phid[p] for p in child_phids]

            if ancestor_revs:
                revs.extend(ancestor_revs)
                revs.reverse()

            if child_revs:
                revs.extend(child_revs)

    # Set the target id
    rev_id = revs[-1]["id"]

    logger.info(
        "Patching revision%s: %s",
        "s" if len(revs) > 1 else "",
        " ".join(["D%s" % r["id"] for r in revs]),
    )

    # Pull diffs
    with wait_message("Downloading patch information.."):
        diffs = conduit.get_diffs(phids=[r["fields"]["diffPHID"] for r in revs])

    # If a user specifies a diff ID, retrieve the diff and add it to the diff mapping,
    # and overwrite the diffPHID for the relevant revision
    if args.diff_id:
        requested_diff_phid, requested_diff = get_diff_by_id(args.diff_id)
        diffs[requested_diff_phid] = requested_diff
        update_revision_with_new_diff(revs, requested_diff)

    if not args.no_commit and config.create_commit and not args.raw:
        for rev in revs:
            diff = diffs[rev["fields"]["diffPHID"]]
            if not diff["attachments"]["commits"]["commits"]:
                raise Error(
                    "A diff without commit information detected in revision D%s.\n"
                    "Use `--no-commit` to patch the working tree." % rev["id"]
                )

    base_node = None
    if not args.raw:
        args.apply_to = args.apply_to or config.apply_patch_to

        if args.apply_to == "base":
            base_node = get_base_ref(diffs[revs[0]["fields"]["diffPHID"]])

            if not base_node:
                raise Error(
                    "Base commit not found in diff. "
                    "Use `--apply-to here` to patch current commit."
                )
        elif args.apply_to != "here":
            base_node = args.apply_to

        if args.apply_to != "here":
            try:
                with wait_message("Checking %s.." % short_node(base_node)):
                    base_node = repo.check_node(base_node)
            except NotFoundError as e:
                msg = "Unknown revision: %s" % short_node(base_node)
                if str(e):
                    msg += "\n%s" % str(e)

                if args.apply_to == "base":
                    msg += "\nUse --apply-to to set the base commit."

                raise Error(msg)

        branch_name = resolve_branch_name(args, config, rev_id)
        repo.before_patch(base_node, branch_name)

    # Fetch raw diffs in parallel.
    raw_diffs = {}
    with wait_message("Downloading patches.."):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            for rev in revs:
                diff = diffs[rev["fields"]["diffPHID"]]
                futures[rev["id"]] = executor.submit(
                    conduit.call,
                    "differential.getrawdiff",
                    {"diffID": diff["id"]},
                )
            try:
                for rev_id_key, future in futures.items():
                    raw_diffs[rev_id_key] = future.result()
            except Exception:
                executor.shutdown(wait=False, cancel_futures=True)
                raise

    for rev in revs:
        # Prepare the body using just the data from Phabricator
        body = prepare_body(
            rev["fields"]["title"],
            rev["fields"]["summary"],
            rev["id"],
            repo.phab_url,
        )
        diff = diffs[rev["fields"]["diffPHID"]]
        raw = raw_diffs[rev["id"]]

        if args.no_commit or not config.create_commit:
            with wait_message("Applying D%s.." % rev["id"]):
                apply_patch(raw, repo.path)
        else:
            try:
                diff_commits = diff["attachments"]["commits"]["commits"]
                author = "%s <%s>" % (
                    diff_commits[0]["author"]["name"],
                    diff_commits[0]["author"]["email"],
                )
            except (IndexError, KeyError):
                author = None
            try:
                date_created = diff["fields"]["dateCreated"]
            except KeyError:
                date_created = None

            if args.raw:
                # print rather than use logger.info; there's no need for this
                # to be in our logs.
                print(repo.format_patch(raw, body, author, date_created))

            else:
                try:
                    with wait_message("Applying D%s.." % rev["id"]):
                        repo.apply_patch(raw, body, author, date_created)
                except subprocess.CalledProcessError:
                    raise Error("Patch failed to apply")

        if rev["id"] != revs[-1]["id"]:
            logger.info("D%s applied", rev["id"])

    logger.warning("D%s applied", rev_id)


def check_revision_id(value: str) -> int:
    """Parse the revision ID from `value`.

    `value` is a `str` which is either `<id>`, `D<id>`,
    or a Phabricator revision URL.
    """
    # D123 or 123
    m = re.search(r"^D?(\d+)$", value)
    if m:
        return int(m.group(1))

    # Full URL
    m = re.search(r"^https?://[^/]+/D(\d+)", value)
    if m:
        return int(m.group(1))

    # Invalid
    raise argparse.ArgumentTypeError(
        "Invalid Revision ID (expected number or URL): %s\n" % value
    )


def add_parser(parser):
    patch_parser = parser.add_parser("patch", help="Patch from Phabricator revision.")
    patch_parser.add_argument(
        "revision_id", type=check_revision_id, help="Revision number."
    )

    # `--apply-to` and `--raw` are mutually exclusive.
    patch_group = patch_parser.add_mutually_exclusive_group()
    patch_group.add_argument(
        "--apply-to",
        "--applyto",
        "-a",
        metavar="TARGET",
        dest="apply_to",
        help="Where to apply the patch? <{NODE}|here|base> (default: %s)."
        % config.apply_patch_to,
    )
    patch_group.add_argument(
        "--raw", action="store_true", help="Prints out the raw diff to the STDOUT."
    )

    patch_parser.add_argument(
        "--diff-id",
        metavar="DIFF_ID",
        dest="diff_id",
        type=int,
        help="The ID of the diff to apply.",
    )
    patch_parser.add_argument(
        "--name",
        "-n",
        dest="name",
        metavar="NAME",
        help="Use the given name for the bookmark, topic, or branch.",
    )
    patch_parser.add_argument(
        "--no-commit",
        "--nocommit",
        action="store_true",
        dest="no_commit",
        help="Do not commit. Applies the changes with the `patch` command.",
    )
    patch_parser.add_argument(
        "--no-bookmark",
        "--nobookmark",
        action="store_true",
        dest="no_bookmark",
        help="(Mercurial only) Do not create the bookmark.",
    )
    patch_parser.add_argument(
        "--no-topic",
        "--notopic",
        action="store_true",
        dest="no_topic",
        help="(Mercurial only) Do not create the topic.",
    )
    patch_parser.add_argument(
        "--no-branch",
        "--nobranch",
        action="store_true",
        dest="no_branch",
        help="(Git only) Do not create the branch.",
    )
    patch_parser.add_argument(
        "--skip-dependencies",
        action="store_true",
        help="Do not search for dependencies; patch only one revision.",
    )
    patch_parser.add_argument(
        "--include-abandoned", action="store_true", help="Apply abandoned revisions."
    )
    patch_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Patch without confirmation (default: False).",
    )
    patch_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions.",
    )
    patch_parser.add_argument(
        "--force-vcs",
        action="store_true",
        help="EXPERIMENTAL: Override VCS compatibility check.",
    )
    patch_parser.set_defaults(func=patch, needs_repo=True)
