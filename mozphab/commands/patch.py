# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import re

from mozphab.conduit import conduit
from mozphab.config import (
    Config,
    config,
)
from mozphab.exceptions import CommandError, Error, NonLinearException, NotFoundError
from mozphab.helpers import prepare_body, prompt, short_node
from mozphab.logger import logger
from mozphab.mercurial import Mercurial
from mozphab.patch import apply_patch
from mozphab.repository import (
    Repository,
)
from mozphab.spinner import wait_message


def get_base_ref(diff: dict) -> str | None:
    """Given a diff, return the base revision SHA the diff was based on."""
    for ref in diff["fields"].get("refs", []):
        if ref["type"] == "base":
            return ref["identifier"]


def get_patch_date(diff: dict) -> int | None:
    """Return the diff's creation date, used as the rebase-fallback cutoff."""
    return diff["fields"].get("dateCreated")


def find_public_base(repo: Repository, base_node: str) -> str | None:
    """Try to resolve `base_node` to a public (landed) commit.

    Makes two symmetric attempts around a single fetch: check whether
    `base_node` already exists locally and is public, and if not, fetch from
    upstream and check again. Returns the resolved node, or `None` if it
    can't be found at all, or isn't public even after fetching.
    """
    try:
        with wait_message("Checking base revision %s.." % short_node(base_node)):
            resolved = repo.check_node(base_node)
        if repo.is_public(resolved):
            return resolved
    except NotFoundError:
        pass

    with wait_message("Fetching from upstream.."):
        repo.fetch_from_upstream()

    try:
        with wait_message("Re-checking base revision %s.." % short_node(base_node)):
            resolved = repo.check_node(base_node)
    except NotFoundError:
        return None

    return resolved if repo.is_public(resolved) else None


def resolve_base_node(
    repo: Repository, base_node: str, before: int | None = None
) -> str | None:
    """Resolve the commit to apply a patch to when using the rebase strategy.

    Uses the diff's original base commit if it's public (landed). The base
    commit may not be public, eg. because it only exists as part of another,
    unlanded patch stack -- in that case fall back to the most recent commit
    known to have landed on mozilla-central at or before `before` (the
    patch's own timestamp), so we don't rebase onto changes that landed
    after the patch was written.

    Returns `None` if neither can be found, eg. in a repository that has no
    autoland-to-mozilla-central merges at all.
    """
    resolved = find_public_base(repo, base_node)
    if resolved is not None:
        return resolved

    with wait_message("Looking for the latest landed revision.."):
        landing_node = repo.get_latest_landing_node(before=before)

    if not landing_node:
        logger.warning(
            "Base revision %s could not be resolved to a public commit, and no "
            "landed mozilla-central revision could be found to rebase onto.",
            short_node(base_node),
        )
        return None

    logger.warning(
        "Base revision %s is not public (it may belong to another, unlanded "
        "patch stack). Applying the patch at %s instead.",
        short_node(base_node),
        short_node(landing_node),
    )
    return landing_node


def get_diff_author_and_date(diff: dict) -> tuple[str | None, int | None]:
    """Return the author (as `name <email>`) and creation date of `diff`."""
    try:
        diff_commits = diff["attachments"]["commits"]["commits"]
        author = "%s <%s>" % (
            diff_commits[0]["author"]["name"],
            diff_commits[0]["author"]["email"],
        )
    except (IndexError, KeyError):
        author = None

    return author, diff.get("fields", {}).get("dateCreated")


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
) -> str | None:
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


def _apply_commits(
    repo: Repository, revs: list[dict], diffs: dict, raw_diffs: dict
) -> None:
    """Apply each diff in `revs` as a new commit on the currently checked out base.

    Raises Error if a patch fails to apply.
    """
    for rev in revs:
        body = prepare_body(
            rev["fields"]["title"],
            rev["fields"]["summary"],
            rev["id"],
            repo.phab_url,
        )
        diff = diffs[rev["fields"]["diffPHID"]]
        raw = raw_diffs[rev["id"]]
        author, date_created = get_diff_author_and_date(diff)

        try:
            with wait_message("Applying D%s.." % rev["id"]):
                repo.apply_patch(raw, body, author, date_created)
        except CommandError:
            raise Error("Patch failed to apply")

        if rev["id"] != revs[-1]["id"]:
            logger.info("D%s applied", rev["id"])


def patch(repo: Repository, args: argparse.Namespace):
    """Patch repository from Phabricator's revisions.

    By default:
    * perform sanity checks
    * find the target commit
    * create a new branch/bookmark/topic
    * apply the patches and commit the changes

    args.no_commit or config.create_commit is False - no commit will be created after
        applying diffs; patches are applied directly at the target, without any of
        the base-resolution or rebasing described below.
    args.apply_to - <base|here|NODE> (default: base)
        base - apply on top of the diff's own base commit
        here - apply on top of the current commit/checkout
        NODE - apply on top of the given commit
    args.raw is True - only print out the diffs (--force doesn't change anything)

    The patch is applied directly at the target above whenever that's possible.
    When creating commits and it isn't -- the diff's base commit is missing
    locally (eg. it only exists as part of another, unlanded patch stack), or the
    patch doesn't apply at the target -- moz-phab applies at the closest public
    (landed) base instead, then rebases onto the target. This avoids failing
    outright, at the cost of a rebase in that case.

    Raises:
    * Error if uncommitted changes are present in the working tree
    * Error if Phabricator revision is not found
    * Error if `--apply-to base` and no base commit found in the first diff
    * Error if `--apply-to base`, the base commit isn't in the repository, and no
      public commit could be resolved to apply at instead
    * Error if the patch doesn't apply, and applying at a public base isn't an
      option or doesn't help either
    * Error if `--diff-id` does not belong to any revision in the stack
    """
    # The Phabricator ping, the VCS check, and the worktree-cleanness check
    # are independent: run them concurrently when all three are needed.
    # In --raw mode only the ping runs, so skip the pool overhead.
    if args.raw:
        with wait_message("Checking connection to Phabricator."):
            if not conduit.check():
                raise Error("Failed to use Conduit API")
    else:
        with wait_message("Checking environment.."):
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                ping_future = executor.submit(conduit.check)
                vcs_future = executor.submit(repo.check_vcs)
                clean_future = executor.submit(repo.is_worktree_clean)

                if not ping_future.result():
                    raise Error("Failed to use Conduit API")

                # Propagate exceptions from repo.check_vcs.
                vcs_future.result()

                if not clean_future.result():
                    raise Error(
                        "Uncommitted changes present. Please %s them or commit "
                        "before patching."
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

    target_node = ""
    branch_name = None
    no_commit = args.no_commit or not config.create_commit

    base_diff = diffs[revs[0]["fields"]["diffPHID"]]
    diff_base_node = None

    if not args.raw:
        args.apply_to = args.apply_to or config.apply_patch_to
        diff_base_node = get_base_ref(base_diff)

        if args.apply_to == "base":
            if not diff_base_node:
                raise Error(
                    "Base commit not found in diff. "
                    "Use `--apply-to here` to patch current commit."
                )

            # The diff's base commit isn't necessarily in the repository: it
            # can belong to another, unlanded patch stack, or simply have
            # never been fetched. Resolve it here, so `before_patch` is never
            # handed a node that can't be checked out.
            try:
                with wait_message("Checking base %s.." % short_node(diff_base_node)):
                    target_node = repo.check_node(diff_base_node)
            except NotFoundError as e:
                if no_commit:
                    # Nothing is committed, so there's nothing that could be
                    # rebased from a different base later on.
                    msg = "Unknown revision: %s" % short_node(diff_base_node)
                    if str(e):
                        msg += "\n%s" % str(e)
                    msg += "\nUse --apply-to to set the base commit."
                    raise Error(msg)

                # Apply at the closest public (landed) commit instead. This is
                # also where the patch stays: `--apply-to base` has no
                # separate target to rebase onto.
                target_node = resolve_base_node(
                    repo, diff_base_node, before=get_patch_date(base_diff)
                )
                if not target_node:
                    raise Error(
                        "Unable to find a commit to apply D%s at.\n"
                        "Use `--apply-to here` to patch the current commit."
                        % args.revision_id
                    )
        elif args.apply_to == "here":
            target_node = repo.get_current_node()
        else:
            try:
                with wait_message("Checking target %s.." % short_node(args.apply_to)):
                    target_node = repo.check_node(args.apply_to)
            except NotFoundError as e:
                msg = "Unknown target revision: %s" % short_node(args.apply_to)
                if str(e):
                    msg += "\n%s" % str(e)
                raise Error(msg)

        branch_name = resolve_branch_name(args, config, rev_id)

        repo.before_patch(target_node, branch_name)

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

    if no_commit:
        for rev in revs:
            raw = raw_diffs[rev["id"]]
            with wait_message("Applying D%s.." % rev["id"]):
                apply_patch(raw, repo.path)
            if rev["id"] != revs[-1]["id"]:
                logger.info("D%s applied", rev["id"])

    elif args.raw:
        for rev in revs:
            diff = diffs[rev["fields"]["diffPHID"]]
            raw = raw_diffs[rev["id"]]
            body = prepare_body(
                rev["fields"]["title"],
                rev["fields"]["summary"],
                rev["id"],
                repo.phab_url,
            )
            author, date_created = get_diff_author_and_date(diff)

            # print rather than use logger.info; there's no need for this
            # to be in our logs.
            print(repo.format_patch(raw, body, author, date_created))

            if rev["id"] != revs[-1]["id"]:
                logger.info("D%s applied", rev["id"])

    else:
        # Applying at the target is cheap, and avoids rebuilds from files
        # touched only by a rebase, but it fails when the target and the
        # diff's base have diverged.
        try:
            _apply_commits(repo, revs, diffs, raw_diffs)
        except Error:
            base_node = (
                resolve_base_node(
                    repo, diff_base_node, before=get_patch_date(base_diff)
                )
                if args.apply_to != "base" and diff_base_node
                else None
            )
            if base_node is None or base_node == target_node:
                # Nothing else to try: the patch was already applied at the
                # diff's own base, the diff has no base ref (eg. an older
                # diff, or a web-UI upload), or no public commit could be
                # resolved. The original failure is the useful one.
                raise

            logger.warning(
                "Failed to apply at %s; retrying at %s, then rebasing.",
                short_node(target_node),
                short_node(base_node),
            )

            repo.discard_patch_attempt(target_node)
            repo.before_patch(base_node, branch_name)
            _apply_commits(repo, revs, diffs, raw_diffs)

            try:
                with wait_message("Rebasing to %s.." % short_node(target_node)):
                    repo.rebase_node(base_node, target_node)
            except CommandError:
                repo.abort_rebase()
                raise Error(
                    "Failed to rebase the patches to %s; they have been left "
                    "applied at %s." % (short_node(target_node), short_node(base_node))
                )

            logger.info("Successfully rebased patches to %s", short_node(target_node))

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
    # `patch` has no `--upstream` argument, but resolving the base commit goes
    # through the same remote selection as `submit`, which reads it.
    patch_parser.set_defaults(func=patch, needs_repo=True, upstream=None)
