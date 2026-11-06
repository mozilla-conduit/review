# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from mozphab.conduit import conduit
from mozphab.config import (
    Config,
    config,
)
from mozphab.exceptions import CommandError, Error, NonLinearException, NotFoundError
from mozphab.helpers import assert_never, prepare_body, prompt, short_node
from mozphab.logger import logger
from mozphab.mercurial import Mercurial
from mozphab.models import RemoteDiff, Revision, revision_monogram
from mozphab.patch import apply_patch
from mozphab.repository import (
    PatchAttempt,
    PatchRefs,
    Repository,
)
from mozphab.spinner import wait_message


class ApplyMode(Enum):
    """How `patch` applies the patches it downloads."""

    # Commit each patch, the default.
    COMMIT = "commit"

    # Apply the patches to the working tree without committing them, from
    # `--no-commit` or `patch.create_commit = False`.
    WORKING_TREE = "working-tree"

    # Print the patches rather than applying them, from `--raw`.
    PRINT = "print"


@dataclass(frozen=True)
class PatchOptions:
    """The command line flags and config knobs that control `patch`, resolved once."""

    revision_id: int
    diff_id: int | None = None
    mode: ApplyMode = ApplyMode.COMMIT
    # Where to apply the patches: `base`, `here` or a node.
    apply_to: str = "base"
    name: str | None = None
    branch_name_template: str = "phab-D{rev_id}"
    # The refs to create, named once the revisions are known.
    refs: PatchRefs = PatchRefs(create_branch=True, create_bookmark=True)
    skip_dependencies: bool = False
    include_abandoned: bool = False
    yes: bool = False
    # Patch a revision's children without asking.
    always_full_stack: bool = False
    force_vcs: bool = False

    @classmethod
    def from_args(cls, args: argparse.Namespace, config: Config) -> "PatchOptions":
        """Build the options from the parsed command line and the user's config."""
        # `--raw` overrides not committing.
        if args.raw:
            mode = ApplyMode.PRINT
        elif args.no_commit or not config.create_commit:
            mode = ApplyMode.WORKING_TREE
        else:
            mode = ApplyMode.COMMIT

        return cls(
            revision_id=args.revision_id,
            diff_id=args.diff_id,
            mode=mode,
            apply_to=args.apply_to or config.apply_patch_to,
            name=args.name,
            branch_name_template=config.branch_name_template,
            refs=PatchRefs(
                create_branch=not args.no_branch and config.create_branch,
                create_bookmark=not args.no_bookmark and config.create_bookmark,
                create_topic=not args.no_topic and config.create_topic,
            ),
            skip_dependencies=args.skip_dependencies,
            include_abandoned=args.include_abandoned,
            yes=args.yes,
            always_full_stack=config.always_full_stack,
            force_vcs=args.force_vcs,
        )


@dataclass(frozen=True)
class StackShape:
    """The revisions around the requested one in its stack, from its `stackGraph`."""

    # From the requested revision's parent up to the root.
    ancestor_phids: tuple[str, ...] = ()

    # From the requested revision's child down to the leaf.
    child_phids: tuple[str, ...] = ()

    # Whether a revision below the requested one has more than one child, in
    # which case `child_phids` is empty.
    children_non_linear: bool = False


class FailurePolicy(Enum):
    """What `patch` does when the patches don't apply at the target."""

    RAISE = "raise"
    # Apply at the closest public base instead, then rebase onto the target.
    RETRY = "retry"
    # Warn before retrying, and ask first unless `--yes` was given.
    CONFIRM_RETRY = "confirm-retry"


@dataclass(frozen=True)
class PatchPlan:
    """What `patch` applies, and where, decided before the repository changes."""

    mode: ApplyMode
    # The revisions to patch, root first.
    revisions: tuple[Revision, ...]
    # The diff each revision is patched from, keyed by the revision's PHID.
    diffs: Mapping[str, RemoteDiff]
    # The node to apply the patches at, or `""` when printing them.
    target_node: str = ""
    refs: PatchRefs = PatchRefs()
    # The name to give the `refs`, or `None` to create none.
    ref_name: str | None = None
    # The base the first diff recorded, and when it was created.
    diff_base_node: str | None = None
    diff_base_date: int | None = None
    on_failure: FailurePolicy = FailurePolicy.RAISE
    # The branch checked out before patching, if any.
    original_branch: str | None = None
    assume_yes: bool = False


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


def get_diff_author_and_date(diff: RemoteDiff) -> tuple[str | None, int | None]:
    """Return the author (as `name <email>`) and creation date of `diff`."""
    author = None
    if diff.commits:
        commit = diff.commits[0]
        if commit.author_name is not None and commit.author_email is not None:
            author = f"{commit.author_name} <{commit.author_email}>"

    return author, diff.date_created


def get_diff_by_id(diff_id: int) -> RemoteDiff:
    """Return the diff with the ID `diff_id` from Phabricator."""
    diff_dict = conduit.get_diffs(ids=[diff_id])
    if not diff_dict:
        raise NotFoundError(f"Could not find diff with ID of {diff_id}.")

    if len(diff_dict) != 1:
        raise Error(f"Unexpected result received from Phabricator for Diff {diff_id}.")

    return next(iter(diff_dict.values()))


def with_requested_diff(
    diffs: Mapping[str, RemoteDiff], diff: RemoteDiff
) -> dict[str, RemoteDiff]:
    """Return the diffs keyed by revision PHID, with `diff` replacing its revision's.

    Raises:
    * Error if `diff` doesn't belong to any of the revisions
    """
    if diff.revision_phid not in diffs:
        raise Error(f"Diff {diff.id} is not related to any revision in the stack.")
    return {**diffs, diff.revision_phid: diff}


def resolve_branch_name(options: PatchOptions, rev_id: int) -> str | None:
    """Resolve the branch name for the resulting patch.

    Use the value passed from `--name` on the CLI if possible. If
    `--no-commit` is passed, we don't need a branch name since we won't
    be committing to the VCS. Otherwise, format the patch from the
    `patch.branch_name_template` config knob.

    `patch.branch_name_template` supports a single format string, `rev_id`.
    """
    if options.name:
        # Return the value passed from the CLI.
        return options.name

    if options.mode is not ApplyMode.COMMIT:
        # Nothing is committed, so there's no branch to name.
        return None

    # Build the branch name from the configured template.
    return options.branch_name_template.format(rev_id=rev_id)


def apply_commits(
    repo: Repository,
    plan: PatchPlan,
    raw_diffs: dict[int, str],
    attempt: PatchAttempt,
) -> None:
    """Apply each patch in `plan` as a new commit on the base `attempt` checked out.

    Raises Error if a patch fails to apply.
    """
    revs = plan.revisions
    for rev in revs:
        body = prepare_body(
            rev.title,
            rev.summary,
            rev.id,
            repo.phab_url,
        )
        diff = plan.diffs[rev.phid]
        raw = raw_diffs[rev.id]
        author, date_created = get_diff_author_and_date(diff)

        try:
            with wait_message("Applying %s.." % rev.monogram):
                repo.apply_patch(raw, body, author, date_created, attempt=attempt)
        except CommandError:
            raise Error("Patch failed to apply")

        if rev.id != revs[-1].id:
            logger.info("%s applied", rev.monogram)


def resolve_target_node(
    repo: Repository,
    options: PatchOptions,
    diff_base_node: str | None,
    base_diff: RemoteDiff,
) -> str:
    """Return the node to apply the patches at, as `--apply-to` says.

    `diff_base_node` is the base the first diff recorded, if any.
    """
    if options.apply_to == "here":
        return repo.get_current_node()

    if options.apply_to == "base":
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
                return repo.check_node(diff_base_node)
        except NotFoundError as e:
            if options.mode is ApplyMode.WORKING_TREE:
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
            public_base_node = resolve_base_node(
                repo, diff_base_node, before=base_diff.date_created
            )
            if not public_base_node:
                raise Error(
                    "Unable to find a commit to apply %s at.\n"
                    "Use `--apply-to here` to patch the current commit."
                    % revision_monogram(options.revision_id)
                )
            return public_base_node

    # Otherwise `--apply-to` names the commit to apply the patches on top of.
    try:
        with wait_message("Checking target %s.." % short_node(options.apply_to)):
            return repo.check_node(options.apply_to)
    except NotFoundError as e:
        msg = "Unknown target revision: %s" % short_node(options.apply_to)
        if str(e):
            msg += "\n%s" % str(e)
        raise Error(msg)


def fetch_revision_stack(options: PatchOptions) -> list[Revision] | None:
    """Return the revisions to patch, root first, or `None` if the user stops.

    Asks whether to include the children of the requested revision, unless
    `--yes` or `patch.always_full_stack` says to.
    """
    revision = fetch_revision(options.revision_id)
    if options.skip_dependencies:
        return [revision]

    shape = stack_shape(revision)
    related_by_phid = fetch_related_revisions(shape.ancestor_phids + shape.child_phids)
    ancestors, children = filter_stack(
        shape, related_by_phid, options.include_abandoned
    )

    include = ask_include_children(options, children, shape.children_non_linear)
    if include is None:
        return None
    return [*ancestors, revision, *(children if include else ())]


def fetch_revision(revision_id: int) -> Revision:
    """Return the revision `revision_id` from Phabricator."""
    with wait_message("Fetching %s.." % revision_monogram(revision_id)):
        revisions = conduit.get_revisions(ids=[revision_id])

    if not revisions:
        raise Error("Revision not found")
    return revisions[0]


def stack_shape(revision: Revision) -> StackShape:
    """Return the PHIDs around `revision` in its stack, from its `stackGraph`.

    Raises `Error` if the ancestors aren't linear, as the stack can't be patched.
    """
    try:
        ancestor_phids = get_ancestors_from_stack_graph(
            revision.stack_graph, revision.phid
        )
    except NonLinearException:
        raise Error("Non linear dependency detected. Unable to patch the stack.")

    try:
        child_phids = get_children_from_stack_graph(revision.stack_graph, revision.phid)
    except NonLinearException:
        return StackShape(
            ancestor_phids=tuple(ancestor_phids), children_non_linear=True
        )
    return StackShape(
        ancestor_phids=tuple(ancestor_phids), child_phids=tuple(child_phids)
    )


def get_ancestors_from_stack_graph(
    stack_graph: Mapping[str, Sequence[str]], target_phid: str
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


def get_children_from_stack_graph(
    stack_graph: Mapping[str, Sequence[str]], target_phid: str
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


def fetch_related_revisions(phids: Sequence[str]) -> dict[str, Revision]:
    """Return the revisions `phids` from Phabricator, keyed by PHID."""
    if not phids:
        return {}

    with wait_message("Fetching related revisions.."):
        revisions = conduit.get_revisions(phids=list(phids))
    return {revision.phid: revision for revision in revisions}


def filter_stack(
    shape: StackShape,
    related_by_phid: Mapping[str, Revision],
    include_abandoned: bool,
) -> tuple[tuple[Revision, ...], tuple[Revision, ...]]:
    """Return the ancestors to patch, root first, and the children, child first.

    Revisions Phabricator didn't return, eg. ones the user can't see, are dropped,
    as are abandoned ancestors and, unless `include_abandoned`, abandoned children.
    """

    def keep(phid: str, allow_abandoned: bool) -> bool:
        revision = related_by_phid.get(phid)
        return revision is not None and (allow_abandoned or not revision.is_abandoned)

    ancestors = tuple(
        related_by_phid[phid]
        for phid in reversed(shape.ancestor_phids)
        if keep(phid, allow_abandoned=False)
    )
    children = tuple(
        related_by_phid[phid]
        for phid in shape.child_phids
        if keep(phid, allow_abandoned=include_abandoned)
    )
    return ancestors, children


def ask_include_children(
    options: PatchOptions, children: Sequence[Revision], children_non_linear: bool
) -> bool | None:
    """Return whether to patch the children too, or `None` if the user stops.

    Asks unless `--yes` or `patch.always_full_stack` says to include them, and
    saves `patch.always_full_stack` when the user answers "Always".
    """
    if not children:
        return False

    if options.yes or options.always_full_stack:
        include = True
    else:
        children_msg = "a child commit" if len(children) == 1 else "child commits"
        answer = prompt(
            "Revision %s has %s.  Would you like to patch the "
            "full stack?." % (revision_monogram(options.revision_id), children_msg),
            ["Yes", "No", "Always"],
        )
        if answer == "Always":
            config.always_full_stack = True
            config.write()
        include = answer in ("Yes", "Always")

    if include and children_non_linear and not options.yes:
        logger.warning(
            "Revision %s has a non-linear successor graph.\n"
            "Unable to apply the full stack.",
            revision_monogram(options.revision_id),
        )
        if prompt("Continue with only part of the stack?", ["Yes", "No"]) == "No":
            return None

    return include


def check_environment(repo: Repository, options: PatchOptions):
    """Check Phabricator can be reached and, unless printing, the repository."""
    # The Phabricator ping, the VCS check, and the worktree-cleanness check
    # are independent: run them concurrently when all three are needed.
    # When printing only the ping runs, so skip the pool overhead.
    if options.mode is ApplyMode.PRINT:
        with wait_message("Checking connection to Phabricator."):
            if not conduit.check():
                raise Error("Failed to use Conduit API")
    else:
        with wait_message("Checking environment.."):
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                ping_future = executor.submit(conduit.check)
                vcs_future = (
                    None if options.force_vcs else executor.submit(repo.check_vcs)
                )
                clean_future = executor.submit(repo.is_worktree_clean)

                if not ping_future.result():
                    raise Error("Failed to use Conduit API")

                # Propagate exceptions from repo.check_vcs.
                if vcs_future:
                    vcs_future.result()

                if not clean_future.result():
                    raise Error(
                        "Uncommitted changes present. Please %s them or commit "
                        "before patching."
                        % ("shelve" if isinstance(repo, Mercurial) else "stash")
                    )


def build_patch_plan(
    repo: Repository, options: PatchOptions, revisions: list[Revision]
) -> PatchPlan:
    """Decide what to apply and where, reading but not changing the repository."""
    with wait_message("Downloading patch information.."):
        fetched = conduit.get_diffs(
            phids=[revision.diff_phid for revision in revisions]
        )
    diffs = {revision.phid: fetched[revision.diff_phid] for revision in revisions}

    # `--diff-id` patches that diff instead of its revision's latest one.
    if options.diff_id:
        diffs = with_requested_diff(diffs, get_diff_by_id(options.diff_id))

    if options.mode is ApplyMode.COMMIT:
        for revision in revisions:
            if not diffs[revision.phid].commits:
                raise Error(
                    "A diff without commit information detected in revision %s.\n"
                    "Use `--no-commit` to patch the working tree." % revision.monogram
                )

    if options.mode is ApplyMode.PRINT:
        return PatchPlan(mode=options.mode, revisions=tuple(revisions), diffs=diffs)

    base_diff = diffs[revisions[0].phid]
    diff_base_node = base_diff.base_ref
    target_node = resolve_target_node(repo, options, diff_base_node, base_diff)
    ref_name = resolve_branch_name(options, revisions[-1].id)

    if options.mode is not ApplyMode.COMMIT:
        return PatchPlan(
            mode=options.mode,
            revisions=tuple(revisions),
            diffs=diffs,
            target_node=target_node,
            refs=options.refs,
            ref_name=ref_name,
            assume_yes=options.yes,
        )

    if options.apply_to == "base" or not diff_base_node:
        on_failure = FailurePolicy.RAISE
    elif options.apply_to == "here" and diff_base_node != target_node:
        on_failure = FailurePolicy.CONFIRM_RETRY
    else:
        on_failure = FailurePolicy.RETRY

    return PatchPlan(
        mode=options.mode,
        revisions=tuple(revisions),
        diffs=diffs,
        target_node=target_node,
        refs=options.refs,
        ref_name=ref_name,
        diff_base_node=diff_base_node,
        diff_base_date=base_diff.date_created,
        on_failure=on_failure,
        original_branch=repo.get_current_branch(),
        assume_yes=options.yes,
    )


def download_raw_diffs(plan: PatchPlan) -> dict[int, str]:
    """Download each revision's raw diff in parallel, keyed by revision ID."""
    raw_diffs = {}
    with wait_message("Downloading patches.."):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            for revision in plan.revisions:
                diff = plan.diffs[revision.phid]
                futures[revision.id] = executor.submit(
                    conduit.call,
                    "differential.getrawdiff",
                    {"diffID": diff.id},
                )
            try:
                for revision_id, future in futures.items():
                    raw_diffs[revision_id] = future.result()
            except Exception:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
    return raw_diffs


def print_patches(repo: Repository, plan: PatchPlan):
    """Print each patch in the format the VCS imports."""
    raw_diffs = download_raw_diffs(plan)
    for revision in plan.revisions:
        diff = plan.diffs[revision.phid]
        body = prepare_body(
            revision.title,
            revision.summary,
            revision.id,
            repo.phab_url,
        )
        author, date_created = get_diff_author_and_date(diff)

        # print rather than use logger.info; there's no need for this
        # to be in our logs.
        print(repo.format_patch(raw_diffs[revision.id], body, author, date_created))

        if revision.id != plan.revisions[-1].id:
            logger.info("%s applied", revision.monogram)


def apply_to_working_tree(repo: Repository, plan: PatchPlan):
    """Apply each patch to the working tree at the target, without committing."""
    repo.before_patch(
        plan.target_node, plan.refs, plan.ref_name, assume_yes=plan.assume_yes
    )
    raw_diffs = download_raw_diffs(plan)
    for revision in plan.revisions:
        with wait_message("Applying %s.." % revision.monogram):
            apply_patch(raw_diffs[revision.id], repo.path)
        if revision.id != plan.revisions[-1].id:
            logger.info("%s applied", revision.monogram)


def confirm_retry(plan: PatchPlan) -> bool:
    """Warn that retrying can move the checkout, and ask unless `--yes` was given."""
    if plan.original_branch:
        logger.warning(
            "Retrying may switch you off the current branch %s and "
            "leave you at the latest landed revision if the retry "
            "or rebase fails.",
            plan.original_branch,
        )
    else:
        logger.warning(
            "Retrying may switch your checkout and leave it at the "
            "latest landed revision if the retry or rebase fails."
        )
    if plan.assume_yes:
        return True

    retry = prompt(
        "The patch failed to apply here. Retry at the latest landed "
        "revision and rebase onto %s" % short_node(plan.target_node),
        ["No", "Yes"],
    )
    return retry == "Yes"


def commit_patches(repo: Repository, plan: PatchPlan):
    """Commit each patch at the target, retrying at a public base if `plan` allows.

    Applying at the target is cheap, and avoids rebuilds from files touched
    only by a rebase, but it fails when the target and the diff's base have
    diverged.
    """
    attempt = repo.before_patch(
        plan.target_node, plan.refs, plan.ref_name, assume_yes=plan.assume_yes
    )
    raw_diffs = download_raw_diffs(plan)

    try:
        apply_commits(repo, plan, raw_diffs, attempt)
        return
    except Error:
        base_node = (
            resolve_base_node(repo, plan.diff_base_node, before=plan.diff_base_date)
            if plan.on_failure is not FailurePolicy.RAISE and plan.diff_base_node
            else None
        )
        if base_node is None or base_node == plan.target_node:
            # Nothing else to try: the patch was already applied at the
            # diff's own base, the diff has no base ref (eg. an older
            # diff, or a web-UI upload), or no public commit could be
            # resolved. The original failure is the useful one.
            raise

        if plan.on_failure is FailurePolicy.CONFIRM_RETRY and not confirm_retry(plan):
            repo.discard_patch_attempt(
                attempt,
                keep_branch=True,
                original_branch=plan.original_branch,
            )
            raise

    logger.warning(
        "Failed to apply at %s; retrying at %s, then rebasing.",
        short_node(plan.target_node),
        short_node(base_node),
    )

    repo.discard_patch_attempt(attempt)
    attempt = repo.before_patch(
        base_node, plan.refs, plan.ref_name, assume_yes=plan.assume_yes
    )
    apply_commits(repo, plan, raw_diffs, attempt)

    try:
        with wait_message("Rebasing to %s.." % short_node(plan.target_node)):
            repo.rebase_node(base_node, plan.target_node)
    except CommandError:
        repo.abort_rebase()
        message = (
            "Failed to rebase the patches to %s; they have been left "
            "applied at %s." % (short_node(plan.target_node), short_node(base_node))
        )
        if plan.original_branch:
            message += " The original branch was %s." % plan.original_branch
        raise Error(message)

    logger.info("Successfully rebased patches to %s", short_node(plan.target_node))


def patch(repo: Repository, args: argparse.Namespace):
    """Patch repository from Phabricator's revisions.

    By default:
    * perform sanity checks
    * find the target commit
    * create a new branch/bookmark/topic
    * apply the patches and commit the changes

    options.mode - how the patches are applied:
        COMMIT - commit each patch, as described below
        WORKING_TREE - apply the patches to the working tree at the target,
            without any of the base-resolution or rebasing described below
        PRINT - only print out the diffs
    options.apply_to - <base|here|NODE> (default: base)
        base - apply on top of the diff's own base commit
        here - apply on top of the current commit/checkout
        NODE - apply on top of the given commit

    The patch is applied directly at the target above whenever that's possible.
    When creating commits and it isn't -- the diff's base commit is missing
    locally (eg. it only exists as part of another, unlanded patch stack), or the
    patch doesn't apply at the target -- moz-phab can apply at the closest public
    (landed) base instead, then rebase onto the target. For `--apply-to here`,
    this fallback requires confirmation unless `--yes` was given.

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
    options = PatchOptions.from_args(args, config)
    check_environment(repo, options)

    revisions = fetch_revision_stack(options)
    if revisions is None:
        return

    logger.info(
        "Patching revision%s: %s",
        "s" if len(revisions) > 1 else "",
        " ".join(revision.monogram for revision in revisions),
    )

    plan = build_patch_plan(repo, options, revisions)
    match plan.mode:
        case ApplyMode.PRINT:
            print_patches(repo, plan)
        case ApplyMode.WORKING_TREE:
            apply_to_working_tree(repo, plan)
        case ApplyMode.COMMIT:
            commit_patches(repo, plan)
        case _:
            assert_never(plan.mode)

    logger.warning("%s applied", revisions[-1].monogram)


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
    # `RepositoryOptions` reads `--path` and `--upstream`, which this command lacks.
    patch_parser.set_defaults(
        func=patch, needs_repo=True, needs_user_data=False, path=None, upstream=None
    )
