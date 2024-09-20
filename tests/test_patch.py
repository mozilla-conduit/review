# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
from typing import Optional
from unittest import mock

import pytest

from mozphab import exceptions, helpers, mozphab
from mozphab.commands import patch
from mozphab.config import Config


def test_resolve_branch_name():
    class Args:
        def __init__(self, name: Optional[str] = None, no_commit: bool = False):
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
    args = {}
    with pytest.raises(exceptions.Error):
        patch.get_diff_by_id(args.get("diff_id"))

    args = {"diff_id": 1}
    m_get_diffs.return_value = {"DIFFPHID-1": {"id": 1}}
    phid, diff = patch.get_diff_by_id(args.get("diff_id"))
    assert phid == "DIFFPHID-1", "Should return PHID as first element"
    assert diff["id"] == 1, "Should return diff dict as second element"

    m_get_diffs.return_value = {}
    with pytest.raises(exceptions.NotFoundError):
        patch.get_diff_by_id(args.get("diff_id"))


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


@mock.patch("mozphab.conduit.ConduitAPI.call")
@mock.patch("mozphab.git.Git.is_worktree_clean")
@mock.patch("mozphab.commands.patch.config")
@mock.patch("mozphab.conduit.ConduitAPI.check")
@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.get_ancestor_phids")
@mock.patch("mozphab.conduit.ConduitAPI.get_successor_phids")
@mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
@mock.patch("mozphab.commands.patch.get_base_ref")
@mock.patch("mozphab.git.Git.before_patch")
@mock.patch("mozphab.commands.patch.apply_patch")
@mock.patch("mozphab.commands.patch.prepare_body")
@mock.patch("mozphab.git.Git.apply_patch")
@mock.patch("mozphab.git.Git.check_node")
@mock.patch("builtins.print")
def test_patch(
    m_print,
    m_git_check_node,
    m_git_apply_patch,
    m_prepare_body,
    m_apply_patch,
    m_git_before_patch,
    m_get_base_ref,
    m_get_diffs,
    m_get_successor_phids,
    m_get_ancestor_phids,
    m_get_revisions,
    m_git_check_conduit,
    m_config,
    m_git_is_worktree_clean,
    m_call_conduit,
    git,
):
    mozphab.conduit.set_repo(git)

    m_git_check_conduit.return_value = False
    m_config.arc_command = "arc"
    m_config.branch_name_template = "phab-D{rev_id}"
    with pytest.raises(exceptions.Error):
        patch.patch(git, None)

    class Args:
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
    m_git_check_conduit.return_value = True
    m_git_is_worktree_clean.return_value = False
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)

    m_git_is_worktree_clean.return_value = True
    m_get_revisions.return_value = []
    with pytest.raises(exceptions.Error):
        patch.patch(git, git.args)

    m_config.always_full_stack = False
    m_get_successor_phids.return_value = []
    m_get_ancestor_phids.return_value = []
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
    git.args = Args(raw=True, skip_dependencies=True)
    m_get_successor_phids.reset_mock()
    patch.patch(git, git.args)
    m_get_successor_phids.assert_not_called()

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
    # --apply_to head
    git.args = Args(apply_to="head")
    patch.patch(git, git.args)
    m_get_base_ref.assert_not_called()
    m_git_apply_patch.assert_called_once_with(
        "raw",
        "commit message",
        "user <author@example.com>",
        1547806078,
    )
    m_apply_patch.assert_not_called()

    m_git_before_patch.reset_mock()
    node = "abcdef"
    m_git_check_node.return_value = node
    # --applyto NODE
    git.args = Args(apply_to=node)
    patch.patch(git, git.args)
    m_git_before_patch.assert_called_once_with(node, "phab-D1")

    m_git_before_patch.reset_mock()
    # --applyto here
    git.args = Args(apply_to="here")
    patch.patch(git, git.args)
    m_git_before_patch.assert_called_once_with(None, "phab-D1")

    m_git_before_patch.reset_mock()
    # --name NAME
    git.args = Args(name="feature")
    patch.patch(git, git.args)
    m_git_before_patch.assert_called_once_with(node, "feature")

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
    m_get_revisions.side_effect = ([REV_1], [REV_2])
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1, "DIFFPHID-2": DIFF_2}
    m_get_ancestor_phids.return_value = ["PHID-2"]
    m_call_conduit.side_effect = ("raw2", "raw1")
    # --raw 2 revisions in stack
    patch.patch(git, git.args)
    m_print.assert_has_calls((mock.call("raw2"), mock.call("raw1")))

    # node not found
    m_get_revisions.side_effect = None
    m_git_check_node.side_effect = exceptions.NotFoundError
    git.args = Args(apply_to=node)
    with pytest.raises(exceptions.Error) as e:
        patch.patch(git, git.args)
        assert "Unknown revision: %s\nERROR" % node in e.msg

    # successors
    m_get_revisions.reset_mock()
    m_get_revisions.side_effect = ([REV_1], [REV_2])
    m_get_successor_phids.side_effect = (["PHID-2"], ["PHID-2"], [])
    m_get_ancestor_phids.return_value = []
    m_call_conduit.side_effect = ("raw2", "raw1")
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1, "DIFFPHID-2": DIFF_2}
    git.args = Args(revision_id=1, raw=True, yes=True)
    patch.patch(git, git.args)
    assert m_get_revisions.call_args_list == [
        mock.call(ids=[1]),
        mock.call(phids=["PHID-2"]),
    ]

    # multiple successors
    m_get_revisions.reset_mock()
    m_get_revisions.side_effect = ([REV_1], [REV_2])
    m_get_successor_phids.side_effect = (exceptions.NonLinearException,)
    m_call_conduit.side_effect = ("raw",)
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    patch.patch(git, git.args)
    m_get_revisions.assert_called_once_with(ids=[1])


REV_1 = {
    "phid": "PHID-1",
    "id": 1,
    "fields": {"diffPHID": "DIFFPHID-1", "title": "title", "summary": "summary"},
}

REV_2 = {
    "phid": "PHID-2",
    "id": 2,
    "fields": {"diffPHID": "DIFFPHID-2", "title": "title", "summary": "summary"},
}

DIFF_1 = {
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

DIFF_2 = {
    "id": 2,
    "phid": "DIFFPHID-2",
    "attachments": {
        "commits": {
            "commits": [{"author": {"name": "user", "email": "author@example.com"}}]
        }
    },
}

DIFF_3 = {
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
