# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import dataclasses
from typing import Any
from unittest import mock

import pytest

from mozphab import exceptions, helpers, mozphab
from mozphab.commands import patch
from mozphab.config import Config
from mozphab.models import RemoteDiff, Revision
from mozphab.repository import PatchAttempt, PatchRefs, Repository

from .conftest import with_stack_graph


def patch_args(**overrides: Any) -> argparse.Namespace:
    """Return the `patch` arguments the parser produces, with `overrides` applied."""
    args = mozphab.parse_args(["patch", "123"])
    for name, value in overrides.items():
        setattr(args, name, value)
    return args


# The refs `patch` asks for with the default config.
DEFAULT_REFS = PatchRefs(create_branch=True, create_bookmark=True)


def test_patch_options_from_args():
    config = Config(should_access_file=False)

    options = patch.PatchOptions.from_args(patch_args(), config)
    assert options == patch.PatchOptions(
        revision_id=123
    ), "The default arguments and config should give the default options."

    options = patch.PatchOptions.from_args(patch_args(apply_to="here"), config)
    assert options.apply_to == "here", "`--apply-to` should set where to patch."

    config.apply_patch_to = "here"
    options = patch.PatchOptions.from_args(patch_args(), config)
    assert (
        options.apply_to == "here"
    ), "`patch.apply_to` should be used when `--apply-to` isn't passed."

    config.always_full_stack = True
    options = patch.PatchOptions.from_args(patch_args(), config)
    assert (
        options.always_full_stack
    ), "`patch.always_full_stack` should be read into the options."

    options = patch.PatchOptions.from_args(patch_args(no_commit=True), config)
    assert not options.create_commit, "`--no-commit` should disable committing."

    config.create_commit = False
    options = patch.PatchOptions.from_args(patch_args(), config)
    assert (
        not options.create_commit
    ), "`patch.create_commit = False` should disable committing."

    config.branch_name_template = "phab/D{rev_id}"
    options = patch.PatchOptions.from_args(patch_args(), config)
    assert (
        options.branch_name_template == "phab/D{rev_id}"
    ), "The branch name template should come from the config."

    options = patch.PatchOptions.from_args(
        patch_args(no_branch=True, no_bookmark=True), config
    )
    assert (
        options.refs == PatchRefs()
    ), "`--no-branch` and `--no-bookmark` should disable their refs."

    config.create_topic = True
    options = patch.PatchOptions.from_args(patch_args(), config)
    assert options.refs.create_topic, "`patch.create_topic` should enable topics."
    options = patch.PatchOptions.from_args(patch_args(no_topic=True), config)
    assert not options.refs.create_topic, "`--no-topic` should override the config."


def test_resolve_branch_name():
    rev_id = 123

    options = patch.PatchOptions(revision_id=123, name="branch")
    assert (
        patch.resolve_branch_name(options, rev_id) == "branch"
    ), "Branch name passed via args should take precedent."

    options = patch.PatchOptions(revision_id=123, create_commit=False)
    assert (
        patch.resolve_branch_name(options, rev_id) is None
    ), "No branch name should be used when not creating a commit."

    options = patch.PatchOptions(revision_id=123)
    assert (
        patch.resolve_branch_name(options, rev_id) == "phab-D123"
    ), "Branch name should match the default when not changed."

    options = patch.PatchOptions(revision_id=123, branch_name_template="phab/D{rev_id}")
    assert (
        patch.resolve_branch_name(options, rev_id) == "phab/D123"
    ), "Branch name should change based on the configuration."

    options = patch.PatchOptions(revision_id=123, branch_name_template="phabrev")
    assert (
        patch.resolve_branch_name(options, rev_id) == "phabrev"
    ), "Using template strings should be optional."


def test_check_revision_id():
    check_revision_id = patch.check_revision_id

    assert check_revision_id("123") == 123
    assert check_revision_id("D123") == 123
    assert check_revision_id("https://phabricator.example.com/D123") == 123
    assert check_revision_id("https://phabricator.example.com/D123?") == 123
    with pytest.raises(argparse.ArgumentTypeError):
        check_revision_id("D")
    with pytest.raises(argparse.ArgumentTypeError):
        check_revision_id("https://example.com/")


@mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
def test_get_diff_by_id(m_get_diffs):
    m_get_diffs.return_value = {"DIFFPHID-1": RemoteDiff(id=1, phid="DIFFPHID-1")}
    assert patch.get_diff_by_id(1) == RemoteDiff(
        id=1, phid="DIFFPHID-1"
    ), "The requested diff should be returned."

    m_get_diffs.return_value = {}
    with pytest.raises(exceptions.NotFoundError):
        patch.get_diff_by_id(1)

    m_get_diffs.return_value = {
        "DIFFPHID-1": RemoteDiff(id=1, phid="DIFFPHID-1"),
        "DIFFPHID-2": RemoteDiff(id=2, phid="DIFFPHID-2"),
    }
    with pytest.raises(exceptions.Error):
        patch.get_diff_by_id(1)


def test_with_requested_diff():
    diffs = {"PHID-1": DIFF_1, "PHID-2": DIFF_2}

    assert patch.with_requested_diff(diffs, DIFF_3) == {
        "PHID-1": DIFF_3,
        "PHID-2": DIFF_2,
    }, "The requested diff should replace its revision's diff."
    assert diffs == {
        "PHID-1": DIFF_1,
        "PHID-2": DIFF_2,
    }, "The diffs passed in should be left unchanged."

    with pytest.raises(exceptions.Error):
        patch.with_requested_diff(diffs, DIFF_4)


def test_get_ancestors_from_stack_graph():
    get_ancestors = patch._get_ancestors_from_stack_graph

    # No parents — empty result.
    assert get_ancestors({"PHID-1": []}, "PHID-1") == []

    # Linear chain: PHID-3 -> PHID-2 -> PHID-1 (root).
    stack = {"PHID-1": [], "PHID-2": ["PHID-1"], "PHID-3": ["PHID-2"]}
    assert get_ancestors(stack, "PHID-3") == ["PHID-2", "PHID-1"]

    # Target in the middle of a chain.
    assert get_ancestors(stack, "PHID-2") == ["PHID-1"]

    # Non-linear: a revision with two parents raises NonLinearException.
    stack = {"PHID-1": [], "PHID-2": [], "PHID-3": ["PHID-1", "PHID-2"]}
    with pytest.raises(exceptions.NonLinearException):
        get_ancestors(stack, "PHID-3")

    # Target PHID not in graph — empty result.
    assert get_ancestors({}, "PHID-UNKNOWN") == []


def test_get_children_from_stack_graph():
    get_children = patch._get_children_from_stack_graph

    # No children — empty result.
    assert get_children({"PHID-1": []}, "PHID-1") == []

    # Linear chain: PHID-1 (root) -> PHID-2 -> PHID-3.
    stack = {"PHID-1": [], "PHID-2": ["PHID-1"], "PHID-3": ["PHID-2"]}
    assert get_children(stack, "PHID-1") == ["PHID-2", "PHID-3"]

    # Target in the middle.
    assert get_children(stack, "PHID-2") == ["PHID-3"]

    # Non-linear: two children of the same parent raises NonLinearException.
    stack = {"PHID-1": [], "PHID-2": ["PHID-1"], "PHID-3": ["PHID-1"]}
    with pytest.raises(exceptions.NonLinearException):
        get_children(stack, "PHID-1")

    # Leaf node — empty result.
    stack = {"PHID-1": [], "PHID-2": ["PHID-1"]}
    assert get_children(stack, "PHID-2") == []


def test_strip_depends_on():
    strip = helpers.strip_depends_on

    assert "" == strip("Depends on D123")
    assert "" == strip("\n Depends on D1\n")
    assert "title" == strip("title\n\nDepends on D1")
    assert "title\n\nbefore\n\nafter" == strip("title\n\nbefore\nDepends on D1\nafter")
    assert "Depends on DA" == strip("Depends on DA")


def test_prepare_body():
    prep = helpers.prepare_body
    assert "\n\nDifferential Revision: http://phabricator.test/D1" == prep(
        "", "", 1, "http://phabricator.test"
    )
    assert "\n\nDifferential Revision: http://phabricator.test/D2" == prep(
        "", "", 2, "http://phabricator.test"
    )
    assert "title\n\n" "Differential Revision: http://phabricator.test/D2" == prep(
        "title", "", 2, "http://phabricator.test"
    )
    assert (
        "title\n\n"
        "some\n"
        "summary\n\n"
        "Differential Revision: http://phabricator.test/D2"
        == prep("title", "some\nsummary", 2, "http://phabricator.test")
    )


@mock.patch("mozphab.gitcommand.which_path")
@mock.patch("mozphab.gitcommand.check_call")
def test_apply_patch(m_check_call, _):
    patch.config.git_command = ["git"]
    patch.apply_patch("diff", "x")
    m_check_call.assert_called_once()


@pytest.fixture
def repo_mock():
    return mock.create_autospec(Repository, instance=True)


@pytest.mark.parametrize(
    "check_node,is_public,landing_node,expected,fetches",
    (
        pytest.param(
            ["sha111"],
            True,
            None,
            "sha111",
            False,
            id="base found locally and public: used directly, no fetch needed",
        ),
        pytest.param(
            [exceptions.NotFoundError(), "sha111"],
            True,
            None,
            "sha111",
            True,
            id="base not found locally, but a fetch reveals it and it's public",
        ),
        pytest.param(
            exceptions.NotFoundError(),
            None,
            "landing_sha",
            "landing_sha",
            True,
            id="base never found (another, unlanded stack): latest landed used",
        ),
        pytest.param(
            ["sha111", "sha111"],
            False,
            "landing_sha",
            "landing_sha",
            True,
            id="base found but not public (another stack): latest landed used",
        ),
        pytest.param(
            exceptions.NotFoundError(),
            None,
            None,
            None,
            True,
            id="base unresolvable and nothing landed to fall back to: no base",
        ),
    ),
)
def test_resolve_base_node(
    repo_mock, check_node, is_public, landing_node, expected, fetches
):
    repo_mock.check_node.side_effect = check_node
    repo_mock.is_public.return_value = is_public
    repo_mock.get_latest_landing_node.return_value = landing_node

    assert patch.resolve_base_node(repo_mock, "sha111") == expected

    assert repo_mock.fetch_from_upstream.called is fetches
    # The landing node is only looked up when the base can't be used.
    assert repo_mock.get_latest_landing_node.called is (expected != "sha111")
    # A base that's never found can't be tested for publicness.
    assert repo_mock.is_public.called is (is_public is not None)


def test_resolve_base_node_passes_before_to_landing_node_lookup(repo_mock):
    # The fallback must be bounded by the patch's own timestamp, so we don't
    # rebase onto something that landed after the patch was written.
    repo_mock.check_node.side_effect = exceptions.NotFoundError()
    repo_mock.get_latest_landing_node.return_value = "landing_sha"

    assert (
        patch.resolve_base_node(repo_mock, "sha111", before=1547806078) == "landing_sha"
    )
    repo_mock.get_latest_landing_node.assert_called_once_with(before=1547806078)


@mock.patch("mozphab.conduit.ConduitAPI.call")
@mock.patch("mozphab.git.Git.is_worktree_clean")
@mock.patch("mozphab.commands.patch.config")
@mock.patch("mozphab.conduit.ConduitAPI.check")
@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
@mock.patch("mozphab.git.Git.before_patch")
@mock.patch("mozphab.commands.patch.apply_patch")
@mock.patch("mozphab.commands.patch.prepare_body")
@mock.patch("mozphab.git.Git.apply_patch")
@mock.patch("mozphab.git.Git.check_node")
@mock.patch("mozphab.git.Git.is_public")
@mock.patch("mozphab.git.Git.get_current_node")
@mock.patch("mozphab.git.Git.rebase_node")
@mock.patch("mozphab.git.Git.abort_rebase")
@mock.patch("mozphab.git.Git.discard_patch_attempt")
@mock.patch("mozphab.git.Git.fetch_from_upstream")
@mock.patch("mozphab.git.Git.get_latest_landing_node")
@mock.patch("mozphab.commands.patch.prompt")
@mock.patch("mozphab.git.Git.get_current_branch", return_value="original-branch")
@mock.patch("mozphab.commands.patch.logger.warning")
@mock.patch("builtins.print")
def test_patch(
    m_print,
    m_warning,
    m_get_current_branch,
    m_prompt,
    m_git_get_latest_landing_node,
    m_git_fetch_from_upstream,
    m_git_discard_patch_attempt,
    m_git_abort_rebase,
    m_git_rebase_node,
    m_git_get_current_node,
    m_git_is_public,
    m_git_check_node,
    m_git_apply_patch,
    m_prepare_body,
    m_apply_patch,
    m_git_before_patch,
    m_get_diffs,
    m_get_revisions,
    m_git_check_conduit,
    m_config,
    m_git_is_worktree_clean,
    m_call_conduit,
    git,
):
    # The diff's base is treated as public by default, so `resolve_base_node`
    # uses it directly without needing to fetch or fall back.
    m_git_is_public.return_value = True
    mozphab.conduit.set_repo(git)

    class Args(argparse.Namespace):
        def __init__(
            self,
            revision_id=123,
            no_commit=False,
            raw=False,
            apply_to="base",
            yes=False,
            skip_dependencies=False,
            include_abandoned=False,
            force_vcs=False,
            name=None,
            diff_id=None,
            no_branch=False,
            no_bookmark=False,
            no_topic=False,
        ):
            self.revision_id = revision_id
            self.no_commit = no_commit
            self.raw = raw
            self.apply_to = apply_to
            self.yes = yes
            self.skip_dependencies = skip_dependencies
            self.include_abandoned = include_abandoned
            self.force_vcs = force_vcs
            self.name = name
            self.diff_id = diff_id
            self.no_branch = no_branch
            self.no_bookmark = no_bookmark
            self.no_topic = no_topic

    args = Args()
    m_git_check_conduit.return_value = False
    m_config.arc_command = "arc"
    m_config.branch_name_template = "phab-D{rev_id}"
    m_config.create_branch = True
    m_config.create_bookmark = True
    m_config.create_topic = False
    m_git_before_patch.side_effect = lambda node, refs, name, assume_yes: PatchAttempt(
        node
    )
    with pytest.raises(exceptions.Error):
        patch.patch(git, args)

    m_git_check_conduit.return_value = True
    m_git_is_worktree_clean.return_value = False
    with pytest.raises(exceptions.Error):
        patch.patch(git, args)

    m_git_is_worktree_clean.return_value = True
    m_get_revisions.return_value = []
    with pytest.raises(exceptions.Error):
        patch.patch(git, args)

    m_config.always_full_stack = False
    m_call_conduit.return_value = "raw"  # differential.getrawdiff
    m_get_revisions.return_value = [
        Revision.from_conduit(
            {
                "phid": "PHID-1",
                "id": 1,
                "fields": {
                    "diffPHID": "DIFFPHID-1",
                    "title": "title",
                    "summary": "summary",
                    "stackGraph": {"PHID-1": []},
                    "status": {"value": "needs-review"},
                },
            }
        )
    ]
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    m_git_check_node.return_value = "sha111"
    m_prepare_body.return_value = "commit message"
    patch.patch(git, args)
    m_git_apply_patch.assert_called_once_with(
        "raw",
        "commit message",
        "user <author@example.com>",
        1547806078,
        attempt=PatchAttempt("sha111"),
    )
    m_apply_patch.assert_not_called()
    m_git_before_patch.assert_called_once_with(
        "sha111", DEFAULT_REFS, "phab-D1", assume_yes=False
    )

    m_git_apply_patch.reset_mock()
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    patch.patch(git, args)
    m_git_apply_patch.assert_called_once_with(
        "raw",
        "commit message",
        "user <author@example.com>",
        1547806078,
        attempt=PatchAttempt("sha111"),
    )

    # --diff-id
    m_get_diffs.side_effect = [
        {"DIFFPHID-1": DIFF_1},
        {"DIFFPHID-3": DIFF_3},
    ]
    args = Args(diff_id=3)
    patch.patch(git, args)
    m_git_apply_patch.assert_called_with(
        "raw",
        "commit message",
        "user 3 <author@example.com>",
        1547806078,
        attempt=PatchAttempt("sha111"),
    )
    m_get_diffs.side_effect = None

    # --diff-id raises NotFoundError
    m_get_diffs.side_effect = [{"DIFFPHID-1": DIFF_1}, {}]
    args = Args(diff_id=100)
    with pytest.raises(exceptions.NotFoundError):
        patch.patch(git, args)

    # --diff-id raises Error when belonging to different rev
    m_get_diffs.side_effect = [
        {"DIFFPHID-1": DIFF_1},
        {"DIFFPHID-4": DIFF_4},
    ]
    args = Args(diff_id=4)
    with pytest.raises(exceptions.Error):
        patch.patch(git, args)
    m_get_diffs.side_effect = None

    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1_WITHOUT_BASE}
    with pytest.raises(exceptions.Error):
        patch.patch(git, args)

    # `--apply-to here` should still work without a base ref (eg. an older
    # diff, or a web-UI upload): apply directly at the current checkout.
    m_git_before_patch.reset_mock()
    m_git_get_current_node.return_value = "current_sha"
    args = Args(apply_to="here")
    patch.patch(git, args)
    m_git_before_patch.assert_called_once_with(
        "current_sha", DEFAULT_REFS, "phab-D1", assume_yes=False
    )

    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    m_git_apply_patch.reset_mock()
    m_git_before_patch.reset_mock()
    # --raw
    args = Args(raw=True)
    patch.patch(git, args)
    m_git_before_patch.assert_not_called()
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_not_called()
    m_print.assert_called_with("raw")

    # skip-dependencies
    m_get_revisions.reset_mock()
    args = Args(raw=True, skip_dependencies=True)
    patch.patch(git, args)
    # Only the initial revision fetch should happen, no related revisions.
    m_get_revisions.assert_called_once()

    m_git_before_patch.reset_mock()
    # --no_commit
    args = Args(no_commit=True)
    patch.patch(git, args)
    m_git_before_patch.assert_called_once()
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_called_once_with("raw", "x")
    m_git_before_patch.assert_called_once_with(
        "sha111", DEFAULT_REFS, None, assume_yes=False
    )

    m_apply_patch.reset_mock()
    m_git_before_patch.reset_mock()
    # create_commit=False in config
    m_config.create_commit = False
    args = Args()
    patch.patch(git, args)
    m_git_before_patch.assert_called_once()
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_called_once_with("raw", "x")
    m_git_before_patch.assert_called_once_with(
        "sha111", DEFAULT_REFS, None, assume_yes=False
    )

    m_apply_patch.reset_mock()
    m_config.create_commit = True
    m_git_before_patch.reset_mock()
    # --no_commit --applyto head
    args = Args(no_commit=True, apply_to="head")
    patch.patch(git, args)

    m_apply_patch.reset_mock()
    m_git_rebase_node.reset_mock()
    m_git_before_patch.reset_mock()
    # check_node resolves whatever it's given (identity).
    m_git_check_node.side_effect = lambda n: n
    # --apply_to head: the patch applies directly at the target on the first
    # (optimistic) attempt, since the mocked `apply_patch` never fails --
    # no base resolution or rebase is needed.
    args = Args(apply_to="head")
    patch.patch(git, args)
    m_git_before_patch.assert_called_once_with(
        "head", DEFAULT_REFS, "phab-D1", assume_yes=False
    )
    m_git_apply_patch.assert_called_once_with(
        "raw",
        "commit message",
        "user <author@example.com>",
        1547806078,
        attempt=PatchAttempt("head"),
    )
    m_apply_patch.assert_not_called()
    m_git_rebase_node.assert_not_called()

    m_git_before_patch.reset_mock()
    node = "abcdef"
    # --apply-to NODE: same as above, applied directly at `node`.
    args = Args(apply_to=node)
    patch.patch(git, args)
    m_git_before_patch.assert_called_once_with(
        node, DEFAULT_REFS, "phab-D1", assume_yes=False
    )
    m_git_rebase_node.assert_not_called()

    m_git_before_patch.reset_mock()
    m_git_get_current_node.return_value = "current_sha"
    # --applyto here: applied directly at whatever is currently checked out.
    args = Args(apply_to="here")
    patch.patch(git, args)
    m_git_before_patch.assert_called_once_with(
        "current_sha", DEFAULT_REFS, "phab-D1", assume_yes=False
    )
    m_git_rebase_node.assert_not_called()

    m_git_before_patch.reset_mock()
    # --name NAME: default apply-to (base) uses the diff's own base commit.
    args = Args(name="feature")
    patch.patch(git, args)
    m_git_before_patch.assert_called_once_with(
        "sha111", DEFAULT_REFS, "feature", assume_yes=False
    )
    m_git_rebase_node.assert_not_called()

    # --apply-to base, with a base commit that isn't in the repository (eg. it
    # belongs to another, unlanded stack): the patch is applied at the latest
    # landed revision instead, and there's nothing to rebase onto.
    m_git_before_patch.reset_mock()
    m_git_check_node.side_effect = exceptions.NotFoundError
    m_git_get_latest_landing_node.return_value = "landing_sha"
    args = Args()
    patch.patch(git, args)
    m_git_before_patch.assert_called_once_with(
        "landing_sha", DEFAULT_REFS, "phab-D1", assume_yes=False
    )
    m_git_rebase_node.assert_not_called()

    # Same, with --no-commit: nothing is committed, so nothing could be
    # rebased from another base later on -- the missing base is an error.
    m_git_before_patch.reset_mock()
    args = Args(no_commit=True)
    with pytest.raises(exceptions.Error):
        patch.patch(git, args)
    m_git_before_patch.assert_not_called()
    m_git_check_node.side_effect = lambda n: n
    m_git_fetch_from_upstream.reset_mock()

    # If the application fails, moz-phab applies at a resolved public base
    # instead, and rebases onto the target.
    m_git_before_patch.reset_mock()
    m_git_apply_patch.reset_mock()
    m_git_apply_patch.side_effect = [exceptions.CommandError("boom"), mock.DEFAULT]
    m_git_is_public.return_value = True
    args = Args(apply_to="current_sha")
    patch.patch(git, args)
    assert m_git_before_patch.call_args_list == [
        mock.call("current_sha", DEFAULT_REFS, "phab-D1", assume_yes=False),
        mock.call("sha111", DEFAULT_REFS, "phab-D1", assume_yes=False),
    ]
    # The failed attempt is undone rather than left behind.
    m_git_discard_patch_attempt.assert_called_once_with(PatchAttempt("current_sha"))
    m_git_rebase_node.assert_called_once_with("sha111", "current_sha")
    m_git_apply_patch.side_effect = None
    m_git_discard_patch_attempt.reset_mock()
    m_git_rebase_node.reset_mock()

    # A conflicting rebase is aborted rather than left in progress.
    m_git_apply_patch.side_effect = [exceptions.CommandError("boom"), mock.DEFAULT]
    m_git_rebase_node.side_effect = exceptions.CommandError("boom")
    args = Args(apply_to="current_sha")
    with pytest.raises(exceptions.Error, match="original branch was original-branch"):
        patch.patch(git, args)
    m_git_abort_rebase.assert_called_once()
    m_git_apply_patch.side_effect = None
    m_git_rebase_node.side_effect = None
    m_git_rebase_node.reset_mock()
    m_git_discard_patch_attempt.reset_mock()

    # Declining the fallback for `--apply-to here` must leave the checkout
    # alone, without fetching or looking for an older revision.
    m_git_before_patch.reset_mock()
    m_git_check_node.reset_mock()
    m_git_apply_patch.side_effect = exceptions.CommandError("boom")
    m_prompt.return_value = "No"
    args = Args(apply_to="here")
    with pytest.raises(exceptions.Error, match="Patch failed to apply"):
        patch.patch(git, args)
    m_prompt.assert_called_once_with(
        "The patch failed to apply here. Retry at the latest landed revision "
        "and rebase onto current_sha",
        ["No", "Yes"],
    )
    m_git_before_patch.assert_called_once_with(
        "current_sha", DEFAULT_REFS, "phab-D1", assume_yes=False
    )
    m_git_check_node.assert_called_once_with("sha111")
    m_git_fetch_from_upstream.assert_not_called()
    m_git_discard_patch_attempt.assert_called_once_with(
        PatchAttempt("current_sha"),
        keep_branch=True,
        original_branch="original-branch",
    )
    m_git_rebase_node.assert_not_called()
    m_git_discard_patch_attempt.reset_mock()

    # Accepting the offer retains the existing retry and rebase behavior.
    m_prompt.reset_mock()
    m_prompt.return_value = "Yes"
    m_git_before_patch.reset_mock()
    m_git_apply_patch.side_effect = [exceptions.CommandError("boom"), mock.DEFAULT]
    patch.patch(git, args)
    m_git_discard_patch_attempt.assert_called_once_with(PatchAttempt("current_sha"))
    m_git_rebase_node.assert_called_once_with("sha111", "current_sha")
    m_git_apply_patch.side_effect = None
    m_git_discard_patch_attempt.reset_mock()
    m_git_rebase_node.reset_mock()
    m_prompt.reset_mock()

    # `--yes` explicitly opts into the retry without another prompt.
    m_warning.reset_mock()
    m_git_apply_patch.side_effect = [exceptions.CommandError("boom"), mock.DEFAULT]
    args = Args(apply_to="here", yes=True)
    patch.patch(git, args)
    m_prompt.assert_not_called()
    m_warning.assert_any_call(
        "Retrying may switch you off the current branch %s and "
        "leave you at the latest landed revision if the retry "
        "or rebase fails.",
        "original-branch",
    )
    m_git_discard_patch_attempt.assert_called_once_with(PatchAttempt("current_sha"))
    m_git_rebase_node.assert_called_once_with("sha111", "current_sha")
    m_git_apply_patch.side_effect = None
    m_git_discard_patch_attempt.reset_mock()
    m_git_rebase_node.reset_mock()

    # `--apply-to base` applies at the diff's own base, so there's no other
    # commit to try: the original failure propagates.
    m_git_before_patch.reset_mock()
    m_git_apply_patch.side_effect = exceptions.CommandError("boom")
    args = Args()
    with pytest.raises(exceptions.Error):
        patch.patch(git, args)
    m_git_before_patch.assert_called_once_with(
        "sha111", DEFAULT_REFS, "phab-D1", assume_yes=False
    )
    m_git_discard_patch_attempt.assert_not_called()

    # Same when the diff has no base ref at all (eg. an older diff, or a
    # web-UI upload).
    m_git_before_patch.reset_mock()
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1_WITHOUT_BASE}
    args = Args(apply_to="here")
    with pytest.raises(exceptions.Error):
        patch.patch(git, args)
    m_git_before_patch.assert_called_once()
    m_git_discard_patch_attempt.assert_not_called()
    m_git_apply_patch.side_effect = None
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}

    # ########## no commit info in diffs
    m_get_diffs.return_value = {
        "DIFFPHID-1": RemoteDiff(id=1, phid="DIFFPHID-1", base_ref="sha111")
    }
    m_call_conduit.side_effect = ("raw",)
    args = Args()
    with pytest.raises(exceptions.Error):
        patch.patch(git, args)

    m_git_apply_patch.reset_mock()
    m_apply_patch.reset_mock()
    args = Args(no_commit=True)
    patch.patch(git, args)
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_called_once()

    m_print.reset_mock()
    m_call_conduit.side_effect = ("raw",)
    args = Args(raw=True)
    patch.patch(git, args)
    m_print.assert_called_once_with("raw")

    # ########## multiple revisions
    m_print.reset_mock()
    m_get_revisions.reset_mock()
    # REV_2 is parent of REV_1
    rev1 = with_stack_graph(REV_1, {"PHID-1": ["PHID-2"], "PHID-2": []})
    m_get_revisions.side_effect = ([rev1], [REV_2])
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1, "DIFFPHID-2": DIFF_2}
    # Use a function-based side_effect so parallel downloads get deterministic
    # results regardless of thread execution order.
    m_call_conduit.side_effect = lambda m, a: "raw%s" % a["diffID"]
    # --raw 2 revisions in stack
    patch.patch(git, args)
    # Verify ancestors are fetched via a single batched get_revisions call.
    assert m_get_revisions.call_args_list == [
        mock.call(ids=[123]),
        mock.call(phids=["PHID-2"]),
    ]
    m_print.assert_has_calls((mock.call("raw2"), mock.call("raw1")))

    # Optimistic apply fails; the base can't be resolved to a public commit
    # either (not found, even after fetching), and there's no landed
    # revision to fall back to -- the fallback itself raises.
    m_get_revisions.side_effect = None
    m_git_apply_patch.side_effect = exceptions.CommandError("boom")
    m_git_check_node.side_effect = exceptions.NotFoundError
    m_git_get_latest_landing_node.return_value = None
    args = Args(apply_to="here")
    m_prompt.return_value = "Yes"
    with pytest.raises(exceptions.Error) as e:
        patch.patch(git, args)
    m_git_fetch_from_upstream.assert_called_once()
    m_git_apply_patch.side_effect = None
    m_git_check_node.side_effect = lambda n: n

    assert "Patch failed to apply" in str(
        e.value
    ), "The original apply failure should be the one reported."

    # successors
    m_get_revisions.reset_mock()
    # PHID-2 is a child of PHID-1
    rev1 = with_stack_graph(REV_1, {"PHID-1": [], "PHID-2": ["PHID-1"]})
    m_get_revisions.side_effect = ([rev1], [REV_2])
    m_call_conduit.side_effect = lambda m, a: "raw%s" % a["diffID"]
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1, "DIFFPHID-2": DIFF_2}
    args = Args(revision_id=1, raw=True, yes=True)
    patch.patch(git, args)
    assert m_get_revisions.call_args_list == [
        mock.call(ids=[1]),
        mock.call(phids=["PHID-2"]),
    ]

    # multiple successors (non-linear: PHID-2 and PHID-3 are both children of PHID-1)
    m_get_revisions.reset_mock()
    rev1 = with_stack_graph(
        REV_1,
        {"PHID-1": [], "PHID-2": ["PHID-1"], "PHID-3": ["PHID-1"]},
    )
    m_get_revisions.side_effect = ([rev1],)
    m_call_conduit.side_effect = ("raw",)
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    patch.patch(git, args)
    m_get_revisions.assert_called_once_with(ids=[1])

    # ########## ancestors and children batched in single get_revisions call
    m_print.reset_mock()
    m_get_revisions.reset_mock()
    # Stack: PHID-2 (root) -> PHID-1 (target) -> PHID-3 (child)
    rev1 = with_stack_graph(
        REV_1,
        {"PHID-2": [], "PHID-1": ["PHID-2"], "PHID-3": ["PHID-1"]},
    )
    m_get_revisions.side_effect = ([rev1], [REV_2, REV_3])
    m_call_conduit.side_effect = lambda m, a: "raw%s" % a["diffID"]
    m_get_diffs.return_value = {
        "DIFFPHID-1": DIFF_1,
        "DIFFPHID-2": DIFF_2,
        "DIFFPHID-3": DIFF_3,
    }
    args = Args(revision_id=1, raw=True, yes=True)
    patch.patch(git, args)
    # Verify ancestors and children are fetched in a single batched call.
    assert m_get_revisions.call_args_list == [
        mock.call(ids=[1]),
        mock.call(phids=["PHID-2", "PHID-3"]),
    ]
    m_print.assert_has_calls((mock.call("raw2"), mock.call("raw1"), mock.call("raw3")))


REV_1 = Revision.from_conduit(
    {
        "phid": "PHID-1",
        "id": 1,
        "fields": {
            "diffPHID": "DIFFPHID-1",
            "title": "title",
            "summary": "summary",
            "status": {"value": "needs-review"},
        },
    }
)

REV_2 = Revision.from_conduit(
    {
        "phid": "PHID-2",
        "id": 2,
        "fields": {
            "diffPHID": "DIFFPHID-2",
            "title": "title",
            "summary": "summary",
            "status": {"value": "needs-review"},
        },
    }
)

REV_3 = Revision.from_conduit(
    {
        "phid": "PHID-3",
        "id": 3,
        "fields": {
            "diffPHID": "DIFFPHID-3",
            "title": "title",
            "summary": "summary",
            "status": {"value": "needs-review"},
        },
    }
)

DIFF_1 = RemoteDiff.from_conduit(
    {
        "id": 1,
        "phid": "DIFFPHID-1",
        "fields": {
            "revisionPHID": "PHID-1",
            "dateCreated": 1547806078,
            "refs": [{"type": "base", "identifier": "sha111"}],
        },
        "attachments": {
            "commits": {
                "commits": [
                    {
                        "author": {
                            "name": "user",
                            "email": "author@example.com",
                            "epoch": 1547806078,
                        }
                    }
                ]
            }
        },
    }
)

# `DIFF_1`, as if its diff didn't record a base, eg. one uploaded in the web UI.
DIFF_1_WITHOUT_BASE = dataclasses.replace(DIFF_1, base_ref=None)

DIFF_2 = RemoteDiff.from_conduit(
    {
        "id": 2,
        "phid": "DIFFPHID-2",
        "fields": {
            "dateCreated": 1547806078,
            "refs": [{"type": "base", "identifier": "sha111"}],
        },
        "attachments": {
            "commits": {
                "commits": [{"author": {"name": "user", "email": "author@example.com"}}]
            }
        },
    }
)

DIFF_3 = RemoteDiff.from_conduit(
    {
        "id": 3,
        "phid": "DIFFPHID-3",
        "fields": {
            "revisionPHID": "PHID-1",
            "dateCreated": 1547806078,
            "refs": [{"type": "base", "identifier": "sha111"}],
        },
        "attachments": {
            "commits": {
                "commits": [
                    {
                        "author": {
                            "name": "user 3",
                            "email": "author@example.com",
                            "epoch": 1547806078,
                        }
                    }
                ]
            }
        },
    }
)

DIFF_4 = RemoteDiff.from_conduit(
    {
        "id": 4,
        "phid": "DIFFPHID-4",
        "fields": {"revisionPHID": "PHID-100"},
        "attachments": {
            "commits": {
                "commits": [{"author": {"name": "user", "email": "author@example.com"}}]
            }
        },
    }
)


@pytest.mark.parametrize("force_vcs", [False, True])
@mock.patch("mozphab.conduit.ConduitAPI.check", return_value=False)
def test_patch_force_vcs_skips_the_vcs_check(_check, force_vcs):
    repo = mock.MagicMock(spec=Repository)
    repo.is_worktree_clean.return_value = True

    with pytest.raises(exceptions.Error, match="Failed to use Conduit API"):
        patch.patch(repo, patch_args(force_vcs=force_vcs))

    assert (
        repo.check_vcs.called != force_vcs
    ), "The VCS should be checked unless `--force-vcs` is given."
