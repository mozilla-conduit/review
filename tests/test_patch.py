# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
from typing import Any
from unittest import mock

import pytest

from mozphab import exceptions, helpers, mozphab
from mozphab.commands import patch
from mozphab.config import Config
from mozphab.repository import Repository

from .conftest import with_stack_graph


def test_resolve_branch_name():
    class Args(argparse.Namespace):
        def __init__(self, name: str | None = None, no_commit: bool = False):
            self.name = name
            self.no_commit = no_commit

    rev_id = "123"

    args = Args(name="branch")
    config = Config(should_access_file=False)

    assert (
        patch.resolve_branch_name(args, config, rev_id) == "branch"
    ), "Branch name passed via args should take precedent."

    args = Args(no_commit=True)
    assert (
        patch.resolve_branch_name(args, config, rev_id) is None
    ), "No branch name should be used when `no_commit` is `True`."

    args = Args()
    assert (
        patch.resolve_branch_name(args, config, rev_id) == "phab-D123"
    ), "Branch name should match the default when not changed."

    config.branch_name_template = "phab/D{rev_id}"
    assert (
        patch.resolve_branch_name(args, config, rev_id) == "phab/D123"
    ), "Branch name should change based on the configuration."

    config.branch_name_template = "phabrev"
    assert (
        patch.resolve_branch_name(args, config, rev_id) == "phabrev"
    ), "Using template strings should be optional."

    config.create_commit = False
    assert (
        patch.resolve_branch_name(args, config, rev_id) is None
    ), "When create_commit is False in config no branch should be used."
    config.create_commit = True


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
    m_get_diffs.return_value = {"DIFFPHID-1": {"id": 1}}
    phid, diff = patch.get_diff_by_id(1)
    assert phid == "DIFFPHID-1", "Should return PHID as first element"
    assert diff["id"] == 1, "Should return diff dict as second element"

    m_get_diffs.return_value = {}
    with pytest.raises(exceptions.NotFoundError):
        patch.get_diff_by_id(1)

    m_get_diffs.return_value = {"DIFFPHID-1": {"id": 1}, "DIFFPHID-2": {"id": 2}}
    with pytest.raises(exceptions.Error):
        patch.get_diff_by_id(1)


def test_update_revision_with_new_diff():
    revs = [REV_1]
    patch.update_revision_with_new_diff(revs, DIFF_3)
    assert (
        revs[0]["fields"]["diffPHID"] == "DIFFPHID-3"
    ), "Should update related revision"

    revs = [REV_1, REV_2]
    patch.update_revision_with_new_diff(revs, DIFF_1)
    assert (
        revs[0]["fields"]["diffPHID"] == "DIFFPHID-1"
    ), "Should update related revision when given multiple revisions"

    # Should raise an error for unrelated diff
    with pytest.raises(exceptions.Error):
        patch.update_revision_with_new_diff(revs, DIFF_4)


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


def test_base_ref():
    assert patch.get_base_ref({"fields": {}}) is None

    diff = {"fields": {"refs": [{"identifier": "sha1", "type": "sometype"}]}}
    assert patch.get_base_ref(diff) is None

    diff = {"fields": {"refs": [{"identifier": "sha1", "type": "base"}]}}
    assert patch.get_base_ref(diff) == "sha1"


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


def test_get_patch_date():
    # `dateModified` is excluded: because `diffPHID` always points at the
    # newest diff, `dateModified` only exceeds `dateCreated` for non-content
    # edits (comments, reviewer changes) -- using it would move the rebase
    # cutoff forward for reasons unrelated to the patch's actual content.
    assert patch.get_patch_date({"fields": {"dateCreated": 100}}) == 100

    # No creation date available.
    assert patch.get_patch_date({"fields": {}}) is None


@mock.patch("mozphab.conduit.ConduitAPI.call")
@mock.patch("mozphab.git.Git.is_worktree_clean")
@mock.patch("mozphab.commands.patch.config")
@mock.patch("mozphab.conduit.ConduitAPI.check")
@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
@mock.patch("mozphab.commands.patch.get_base_ref")
@mock.patch("mozphab.commands.patch.get_patch_date")
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
@mock.patch("builtins.print")
def test_patch(
    m_print,
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
    m_get_patch_date,
    m_get_base_ref,
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
    m_get_patch_date.return_value = 1547806078
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

    git.args = Args()
    m_git_check_conduit.return_value = False
    m_config.arc_command = "arc"
    m_config.branch_name_template = "phab-D{rev_id}"
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)

    m_git_check_conduit.return_value = True
    m_git_is_worktree_clean.return_value = False
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)

    m_git_is_worktree_clean.return_value = True
    m_get_revisions.return_value = []
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)

    m_config.always_full_stack = False
    m_get_base_ref.return_value = "sha111"
    m_call_conduit.return_value = "raw"  # differential.getrawdiff
    m_get_revisions.return_value = [
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
    ]
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    m_git_check_node.return_value = "sha111"
    m_prepare_body.return_value = "commit message"
    patch.patch(git, git.args)
    m_git_apply_patch.assert_called_once_with(
        "raw",
        "commit message",
        "user <author@example.com>",
        1547806078,
    )
    m_apply_patch.assert_not_called()
    m_get_base_ref.assert_called_once()
    m_git_before_patch.assert_called_once_with("sha111", "phab-D1")

    m_git_apply_patch.reset_mock()
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    patch.patch(git, git.args)
    m_git_apply_patch.assert_called_once_with(
        "raw",
        "commit message",
        "user <author@example.com>",
        1547806078,
    )

    # --diff-id
    m_get_diffs.side_effect = [
        {"DIFFPHID-1": DIFF_1},
        {"DIFFPHID-3": DIFF_3},
    ]
    git.args = Args(diff_id=3)
    patch.patch(git, git.args)
    m_git_apply_patch.assert_called_with(
        "raw",
        "commit message",
        "user 3 <author@example.com>",
        1547806078,
    )
    m_get_diffs.side_effect = None

    # --diff-id raises NotFoundError
    m_get_diffs.return_value = {}
    git.args = Args(diff_id=100)
    with pytest.raises(exceptions.NotFoundError):
        patch.patch(git, git.args)

    # --diff-id raises Error when belonging to different rev
    m_get_diffs.side_effect = [
        {"DIFFPHID-1": DIFF_1},
        {"DIFFPHID-4": DIFF_4},
    ]
    git.args = Args(diff_id=4)
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)
    m_get_diffs.side_effect = None

    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    m_get_base_ref.return_value = None
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)

    # `--apply-to here` should still work without a base ref (eg. an older
    # diff, or a web-UI upload): apply directly at the current checkout.
    m_git_before_patch.reset_mock()
    m_git_get_current_node.return_value = "current_sha"
    git.args = Args(apply_to="here")
    patch.patch(git, git.args)
    m_git_before_patch.assert_called_once_with("current_sha", "phab-D1")

    m_get_base_ref.return_value = "sha111"
    m_git_apply_patch.reset_mock()
    m_git_before_patch.reset_mock()
    # --raw
    git.args = Args(raw=True)
    patch.patch(git, git.args)
    m_git_before_patch.assert_not_called()
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_not_called()
    m_print.assert_called_with("raw")

    # skip-dependencies
    m_get_revisions.reset_mock()
    git.args = Args(raw=True, skip_dependencies=True)
    patch.patch(git, git.args)
    # Only the initial revision fetch should happen, no related revisions.
    m_get_revisions.assert_called_once()

    m_git_before_patch.reset_mock()
    # --no_commit
    git.args = Args(no_commit=True)
    patch.patch(git, git.args)
    m_git_before_patch.assert_called_once()
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_called_once_with("raw", "x")
    m_git_before_patch.assert_called_once_with("sha111", None)

    m_apply_patch.reset_mock()
    m_git_before_patch.reset_mock()
    # create_commit=False in config
    m_config.create_commit = False
    git.args = Args()
    patch.patch(git, git.args)
    m_git_before_patch.assert_called_once()
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_called_once_with("raw", "x")
    m_git_before_patch.assert_called_once_with("sha111", None)

    m_apply_patch.reset_mock()
    m_config.create_commit = True
    m_git_before_patch.reset_mock()
    # --no_commit --applyto head
    git.args = Args(no_commit=True, apply_to="head")
    patch.patch(git, git.args)

    m_get_base_ref.reset_mock()
    m_apply_patch.reset_mock()
    m_git_rebase_node.reset_mock()
    m_git_before_patch.reset_mock()
    # check_node resolves whatever it's given (identity).
    m_git_check_node.side_effect = lambda n: n
    # --apply_to head: the patch applies directly at the target on the first
    # (optimistic) attempt, since the mocked `apply_patch` never fails --
    # no base resolution or rebase is needed.
    git.args = Args(apply_to="head")
    patch.patch(git, git.args)
    m_get_base_ref.assert_called_once()
    m_git_before_patch.assert_called_once_with("head", "phab-D1")
    m_git_apply_patch.assert_called_once_with(
        "raw",
        "commit message",
        "user <author@example.com>",
        1547806078,
    )
    m_apply_patch.assert_not_called()
    m_git_rebase_node.assert_not_called()

    m_git_before_patch.reset_mock()
    node = "abcdef"
    # --apply-to NODE: same as above, applied directly at `node`.
    git.args = Args(apply_to=node)
    patch.patch(git, git.args)
    m_git_before_patch.assert_called_once_with(node, "phab-D1")
    m_git_rebase_node.assert_not_called()

    m_git_before_patch.reset_mock()
    m_git_get_current_node.return_value = "current_sha"
    # --applyto here: applied directly at whatever is currently checked out.
    git.args = Args(apply_to="here")
    patch.patch(git, git.args)
    m_git_before_patch.assert_called_once_with("current_sha", "phab-D1")
    m_git_rebase_node.assert_not_called()

    m_git_before_patch.reset_mock()
    # --name NAME: default apply-to (base) uses the diff's own base commit.
    git.args = Args(name="feature")
    patch.patch(git, git.args)
    m_git_before_patch.assert_called_once_with("sha111", "feature")
    m_git_rebase_node.assert_not_called()

    # --apply-to base, with a base commit that isn't in the repository (eg. it
    # belongs to another, unlanded stack): the patch is applied at the latest
    # landed revision instead, and there's nothing to rebase onto.
    m_git_before_patch.reset_mock()
    m_git_check_node.side_effect = exceptions.NotFoundError
    m_git_get_latest_landing_node.return_value = "landing_sha"
    git.args = Args()
    patch.patch(git, git.args)
    m_git_before_patch.assert_called_once_with("landing_sha", "phab-D1")
    m_git_rebase_node.assert_not_called()

    # Same, with --no-commit: nothing is committed, so nothing could be
    # rebased from another base later on -- the missing base is an error.
    m_git_before_patch.reset_mock()
    git.args = Args(no_commit=True)
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)
    m_git_before_patch.assert_not_called()
    m_git_check_node.side_effect = lambda n: n
    m_git_fetch_from_upstream.reset_mock()

    # If the application fails, moz-phab applies at a resolved public base
    # instead, and rebases onto the target.
    m_git_before_patch.reset_mock()
    m_git_apply_patch.reset_mock()
    m_git_apply_patch.side_effect = [exceptions.CommandError("boom"), mock.DEFAULT]
    m_git_is_public.return_value = True
    git.args = Args(apply_to="current_sha")
    patch.patch(git, git.args)
    assert m_git_before_patch.call_args_list == [
        mock.call("current_sha", "phab-D1"),
        mock.call("sha111", "phab-D1"),
    ]
    # The failed attempt is undone rather than left behind.
    m_git_discard_patch_attempt.assert_called_once_with("current_sha")
    m_git_rebase_node.assert_called_once_with("sha111", "current_sha")
    m_git_apply_patch.side_effect = None
    m_git_discard_patch_attempt.reset_mock()
    m_git_rebase_node.reset_mock()

    # A conflicting rebase is aborted rather than left in progress.
    m_git_apply_patch.side_effect = [exceptions.CommandError("boom"), mock.DEFAULT]
    m_git_rebase_node.side_effect = exceptions.CommandError("boom")
    git.args = Args(apply_to="current_sha")
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)
    m_git_abort_rebase.assert_called_once()
    m_git_apply_patch.side_effect = None
    m_git_rebase_node.side_effect = None
    m_git_rebase_node.reset_mock()
    m_git_discard_patch_attempt.reset_mock()

    # `--apply-to base` applies at the diff's own base, so there's no other
    # commit to try: the original failure propagates.
    m_git_before_patch.reset_mock()
    m_git_apply_patch.side_effect = exceptions.CommandError("boom")
    git.args = Args()
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)
    m_git_before_patch.assert_called_once_with("sha111", "phab-D1")
    m_git_discard_patch_attempt.assert_not_called()

    # Same when the diff has no base ref at all (eg. an older diff, or a
    # web-UI upload).
    m_git_before_patch.reset_mock()
    m_get_base_ref.return_value = None
    git.args = Args(apply_to="here")
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)
    m_git_before_patch.assert_called_once()
    m_git_discard_patch_attempt.assert_not_called()
    m_git_apply_patch.side_effect = None
    m_get_base_ref.return_value = "sha111"

    # ########## no commit info in diffs
    m_get_diffs.return_value = {
        "DIFFPHID-1": {"id": 1, "attachments": {"commits": {"commits": []}}}
    }
    m_call_conduit.side_effect = ("raw",)
    git.args = Args()
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)

    m_git_apply_patch.reset_mock()
    m_apply_patch.reset_mock()
    git.args = Args(no_commit=True)
    patch.patch(git, git.args)
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_called_once()

    m_print.reset_mock()
    m_call_conduit.side_effect = ("raw",)
    git.args = Args(raw=True)
    patch.patch(git, git.args)
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
    patch.patch(git, git.args)
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
    git.args = Args(apply_to="here")
    with pytest.raises(exceptions.Error) as e:
        patch.patch(git, git.args)
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
    git.args = Args(revision_id=1, raw=True, yes=True)
    patch.patch(git, git.args)
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
    patch.patch(git, git.args)
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
    git.args = Args(revision_id=1, raw=True, yes=True)
    patch.patch(git, git.args)
    # Verify ancestors and children are fetched in a single batched call.
    assert m_get_revisions.call_args_list == [
        mock.call(ids=[1]),
        mock.call(phids=["PHID-2", "PHID-3"]),
    ]
    m_print.assert_has_calls((mock.call("raw2"), mock.call("raw1"), mock.call("raw3")))


REV_1: dict[str, Any] = {
    "phid": "PHID-1",
    "id": 1,
    "fields": {
        "diffPHID": "DIFFPHID-1",
        "title": "title",
        "summary": "summary",
        "status": {"value": "needs-review"},
    },
}

REV_2: dict[str, Any] = {
    "phid": "PHID-2",
    "id": 2,
    "fields": {
        "diffPHID": "DIFFPHID-2",
        "title": "title",
        "summary": "summary",
        "status": {"value": "needs-review"},
    },
}

REV_3: dict[str, Any] = {
    "phid": "PHID-3",
    "id": 3,
    "fields": {
        "diffPHID": "DIFFPHID-3",
        "title": "title",
        "summary": "summary",
        "status": {"value": "needs-review"},
    },
}

DIFF_1: dict[str, Any] = {
    "id": 1,
    "phid": "DIFFPHID-1",
    "fields": {"revisionPHID": "PHID-1", "dateCreated": 1547806078},
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

DIFF_2: dict[str, Any] = {
    "id": 2,
    "phid": "DIFFPHID-2",
    "attachments": {
        "commits": {
            "commits": [{"author": {"name": "user", "email": "author@example.com"}}]
        }
    },
}

DIFF_3: dict[str, Any] = {
    "id": 3,
    "phid": "DIFFPHID-3",
    "fields": {"revisionPHID": "PHID-1", "dateCreated": 1547806078},
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

DIFF_4 = {
    "id": 4,
    "phid": "DIFFPHID-4",
    "fields": {"revisionPHID": "PHID-100"},
    "attachments": {
        "commits": {
            "commits": [{"author": {"name": "user", "email": "author@example.com"}}]
        }
    },
}
