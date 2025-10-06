# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from pathlib import Path
from unittest import mock

import pytest
from callee import Contains

from mozphab import environment, exceptions, mozphab
from mozphab.commits import Commit

from .conftest import create_temp_fn


@pytest.mark.parametrize(
    "revisions, expected",
    (
        (
            [
                "a1b2c3d4e5f6g7h8i9j0aabbccddeeff00112233",  # oldest unpublished
                "b1c2d3e4f5g6h7i8j9k0aabbccddeeff00112234",
                "c1d2e3f4g5h6i7j8k9l0aabbccddeeff00112235",  # newest unpublished
                "-1234567890abcdef1234567890abcdef12345678",  # last published commit
            ],
            "c1d2e3f4g5h6i7j8k9l0aabbccddeeff00112235",
        ),
        (["-abcdef1234567890abcdef1234567890abcdef12"], None),
        ([], None),
    ),
)
@mock.patch("mozphab.git.Git.git_out")
def test_first_unpublished(m_git_git_out, git, revisions, expected):
    m_git_git_out.return_value = revisions

    result = git._get_first_unpublished_node()
    assert result == expected, "Incorrect first unpublished node detected."


@mock.patch("mozphab.git.config")
@mock.patch("mozphab.git.Git.git_out")
def test_get_base_remotes_with_git_remote(mock_git_out, mock_config, git):
    mock_config.git_remote = ["custom"]
    mock_git_out.return_value = ["origin", "upstream"]

    remotes = git.get_base_remotes()
    assert remotes == [
        "custom"
    ], "`git_remote` config option should override any found remotes."
    mock_git_out.assert_not_called()


@mock.patch("mozphab.git.config")
@mock.patch("mozphab.git.Git.git_out")
def test_get_base_remotes_with_remote_arg(mock_git_out, mock_config, git):
    mock_config.git_remote = ["origin"]
    git.args.upstream = ["custom"]

    remotes = git.get_base_remotes()
    assert remotes == ["custom"], "--upstream arg should take priority."
    mock_git_out.assert_not_called()


@mock.patch("mozphab.git.config")
@mock.patch("mozphab.git.Git.git_out")
def test_get_base_remotes_single(
    mock_git_out, mock_config, git, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO)
    mock_config.git_remote = []
    mock_git_out.return_value = ["onlyremote"]

    remotes = git.get_base_remotes()

    assert remotes == [
        "onlyremote"
    ], "Single remote in the repo should be returned as the base."
    assert caplog.messages == ["Using the only available remote: onlyremote"]


@mock.patch("mozphab.git.config")
@mock.patch("mozphab.git.Git.git_out")
def test_get_base_remotes_with_origin(
    mock_git_out, mock_config, git, caplog: pytest.LogCaptureFixture
):
    mock_config.git_remote = []
    mock_git_out.return_value = ["upstream", "origin", "custom"]

    remotes = git.get_base_remotes()

    assert remotes == [
        "origin"
    ], "`origin` should be returned when multiple remotes are found."
    assert caplog.messages == [Contains("Defaulting to 'origin'")]


@mock.patch("mozphab.git.config")
@mock.patch("mozphab.git.Git.git_out")
def test_get_base_remotes_multiple_without_origin(
    mock_git_out, mock_config, git, caplog: pytest.LogCaptureFixture
):
    mock_config.git_remote = []
    mock_git_out.return_value = ["upstream", "custom"]

    remotes = git.get_base_remotes()

    assert remotes == [
        "upstream",
        "custom",
    ], "All remotes should be returned when multiple remotes found without `origin`."
    assert caplog.messages == [
        Contains("Multiple remotes found, and no `origin` present.")
    ]


@mock.patch("mozphab.git.Git.get_base_remotes")
def test_get_base_remote_args_with_result(mock_get_base_remotes, git):
    mock_get_base_remotes.return_value = ["origin", "custom"]
    remote_args = git.get_base_remote_args()
    assert remote_args == ["--remotes=origin", "--remotes=custom"]


@mock.patch("mozphab.git.Git.get_base_remotes")
def test_get_base_remote_args_empty(mock_get_base_remotes, git):
    mock_get_base_remotes.return_value = []
    remote_args = git.get_base_remote_args()
    assert remote_args == ["--remotes"]


@mock.patch("mozphab.git.Git.git_out")
def test_branches_to_rebase(m_git_git_out, git):
    git_find = git._find_branches_to_rebase

    # No branch returned - not a real case - we don't work without branches
    m_git_git_out.return_value = []
    assert {} == git_find([Commit(orig_node="_aaa", node="aaa")])

    # No amend, no branches to rebase
    m_git_git_out.return_value = ["branch"]
    assert {} == git_find([Commit(orig_node="aaa", node="aaa")])

    # One commit, one branch
    m_git_git_out.return_value = ["branch"]
    assert {"branch": ["aaa", "_aaa"]} == git_find(
        [Commit(orig_node="_aaa", node="aaa")]
    )

    # Two commits one branch
    m_git_git_out.return_value = ["branch"]
    assert {"branch": ["bbb", "_bbb"]} == git_find(
        [Commit(orig_node="_aaa", node="aaa"), Commit(orig_node="_bbb", node="bbb")]
    )

    # Two branches one commit
    # ... (branch1)
    # | ... (branch2)
    # |/
    # * aaa
    # More realistic output from the git command
    m_git_git_out.return_value = ["*  branch1", "  branch2"]
    assert {"branch1": ["aaa", "_aaa"], "branch2": ["aaa", "_aaa"]} == git_find(
        [Commit(orig_node="_aaa", node="aaa")]
    )

    # ... (branch1)
    # | * bbb (branch2)
    # |/
    # * aaa
    m_git_git_out.side_effect = (["branch1", "branch2"], ["branch2"])
    assert {"branch1": ["aaa", "_aaa"], "branch2": ["bbb", "_bbb"]} == git_find(
        [Commit(orig_node="_aaa", node="aaa"), Commit(orig_node="_bbb", node="bbb")]
    )

    # * ... (master)
    # | * ... (feature1)
    # | | * ... (feature2)
    # | |/
    # |/|
    # | | * ddd (feature1_1)
    # | |/
    # | * ccc
    # |/
    # * bbb
    # * aaa

    m_git_git_out.side_effect = (
        ["master", "feature1", "feature1_1", "feature2"],  # aaa
        ["master", "feature1", "feature1_1", "feature2"],  # bbb
        ["feature1", "feature1_1"],  # ccc
        ["feature1_1"],  # ddd
    )
    assert {
        "master": ["bbb", "_bbb"],
        "feature1": ["ccc", "_ccc"],
        "feature2": ["bbb", "_bbb"],
        "feature1_1": ["ddd", "_ddd"],
    } == git_find(
        [
            Commit(orig_node="_aaa", node="aaa"),
            Commit(orig_node="_bbb", node="bbb"),
            Commit(orig_node="_ccc", node="ccc"),
            Commit(orig_node="_ddd", node="ddd"),
        ]
    )


def test_get_direct_children(git):
    get_children = git._get_direct_children
    rev_list = ["aaa bbb ccc", "bbb", "ccc ddd"]
    assert ["bbb", "ccc"] == get_children("aaa", rev_list)
    assert [] == get_children("bbb", rev_list)
    assert ["ddd"] == get_children("ccc", rev_list)
    assert [] == get_children("xxx", rev_list)


def test_is_child(git):
    is_child = git._is_child
    # * ccc
    # * bbb
    # * aaa
    nodes = ["ccc", "bbb ccc", "aaa bbb"]
    assert is_child("aaa", "bbb", nodes)
    assert is_child("aaa", "ccc", nodes)
    assert is_child("bbb", "ccc", nodes)
    assert not is_child("bbb", "aaa", nodes)
    assert not is_child("aaa", "aaa", nodes)
    assert not is_child("bbb", "bbb", nodes)
    assert not is_child("ccc", "ccc", nodes)

    # * ddd
    # | * ccc
    # | | * eee
    # | |/
    # | * bbb
    # |/
    # * aaa
    nodes = ["ddd", "ccc", "eee", "bbb ccc eee", "aaa bbb ddd"]
    assert is_child("aaa", "bbb", nodes)
    assert is_child("aaa", "ccc", nodes)
    assert is_child("aaa", "ddd", nodes)
    assert is_child("aaa", "eee", nodes)
    assert is_child("bbb", "ccc", nodes)
    assert is_child("bbb", "eee", nodes)
    assert not is_child("bbb", "ddd", nodes)
    assert not is_child("ccc", "ddd", nodes)


@mock.patch("mozphab.git.Git.git_out")
@mock.patch("mozphab.mozphab.config")
def test_range(m_config, m_git_git_out, git):
    class Args:
        def __init__(self, start="start", end="."):
            self.start_rev = start
            self.end_rev = end
            self.safe_mode = False
            self.single = False

    m_config.safe_mode = False
    m_git_git_out.return_value = ["user.email=email"]
    git.set_args(Args())
    assert git.revset == ("start", ".")


@mock.patch("mozphab.mozphab.config")
@mock.patch("mozphab.gitcommand.parse_config")
@mock.patch("mozphab.git.Git._get_first_unpublished_node")
@mock.patch("mozphab.git.Git.git_out")
def test_set_args(m_git_git_out, m_git_get_first, m_parse_config, m_config, git):
    class Args:
        def __init__(
            self,
            start=environment.DEFAULT_START_REV,
            end=environment.DEFAULT_END_REV,
            safe_mode=False,
            single=False,
        ):
            self.start_rev = start
            self.end_rev = end
            self.safe_mode = safe_mode
            self.single = single

    with pytest.raises(exceptions.Error):
        git.set_args(Args())

    git.git.command = ["git"]
    git.git.safe_mode = False
    m_parse_config.return_value = {"user.email": "email"}
    m_git_get_first.return_value = "aaa"
    git.set_args(Args())
    assert ["git"] == git.git.command
    m_git_get_first.assert_called_once()
    assert git.revset == ("aaa^", ".")

    m_parse_config.return_value = {
        "user.email": "email",
        "user.name": "name",
        "cinnabar.helper": "string",
    }
    git.set_args(Args())
    assert ["git"] == git.git.command
    assert ["cinnabar"] == git.git.extensions

    safe_options = (
        ["git"]
        + ["-c", "user.email=email"]
        + ["-c", "user.name=name"]
        + ["-c", "cinnabar.helper=string"]
    )
    git.set_args(Args(safe_mode=True))
    assert safe_options == git.git.command

    git.git.command = ["git"]
    git.git.safe_mode = True
    git.set_args(Args())
    assert safe_options == git.git.command

    m_config.safe_mode = False
    m_git_get_first.reset_mock()
    git.set_args(Args(start="bbb", end="ccc"))
    m_git_get_first.assert_not_called()
    assert git.revset == ("bbb", "ccc")

    git.set_args(Args(safe_mode=True))
    assert "" == git.git._env["HOME"]
    assert "" == git.git._env["XDG_CONFIG_HOME"]

    m_config.safe_mode = True
    git.set_args(Args())
    assert "" == git.git._env["HOME"]
    assert "" == git.git._env["XDG_CONFIG_HOME"]

    m_git_get_first.reset_mock()
    m_parse_config.return_value = {
        "user.email": "email",
        "user.name": "name",
    }
    git.set_args(Args(single=True))
    m_git_get_first.assert_not_called()
    assert git.revset == ("HEAD^", "HEAD")

    git.set_args(Args(single=True, start="start"))
    m_git_get_first.assert_not_called()
    assert git.revset == ("start^", "start")


@mock.patch("mozphab.git.Git.git_out")
def test_worktree_clean(m_git_out, git):
    m_git_out.return_value = ""
    assert git.is_worktree_clean()

    m_git_out.return_value = ["xxx"]
    assert not git.is_worktree_clean()

    m_git_out.return_value = ["?? one", "?? two"]
    assert git.is_worktree_clean()

    m_git_out.return_value = ["?? one", "?? two", " M xxx"]
    assert not git.is_worktree_clean()


@mock.patch("mozphab.git.Git.git_call")
def test_commit(m_git, git):
    git.commit("some body")
    m_git.assert_called_once()

    m_git.reset_mock()
    git.commit("some body", "user")
    m_git.assert_called_once()


@mock.patch("mozphab.git.Git._hg_to_git")
@mock.patch("mozphab.git.Git.is_node")
@mock.patch("mozphab.git.Git.phab_vcs")
def test_check_node(m_phab_vcs, m_git_is_node, m_hg2git, git):
    node = "aabbcc"
    mozphab.conduit.set_repo(git)
    assert node == git.check_node(node)

    git._phab_vcs = "hg"
    git.git._cinnabar_installed = False
    m_git_is_node.return_value = False
    with pytest.raises(exceptions.NotFoundError) as e:
        git.check_node(node)
    assert "Cinnabar extension not enabled" in str(e.value)

    git.git._cinnabar_installed = True
    m_hg2git.return_value = "0" * 40
    with pytest.raises(exceptions.NotFoundError) as e:
        git.check_node(node)
    assert "Mercurial SHA1 not found" in str(e.value)

    m_hg2git.return_value = "git_aabbcc"
    with pytest.raises(exceptions.NotFoundError) as e:
        git.check_node(node)
    assert "Mercurial SHA1 detected, but commit not found" in str(e.value)

    m_git_is_node.side_effect = (False, True)
    assert "git_aabbcc" == git.check_node(node)


@mock.patch("mozphab.git.Git.git_out")
@mock.patch("mozphab.git.Git.checkout")
@mock.patch("mozphab.git.Git.git_call")
@mock.patch("mozphab.git.prompt")
@mock.patch("mozphab.git.config")
def test_before_patch(
    m_config,
    m_prompt,
    m_git,
    m_checkout,
    m_git_out,
    git,
    caplog: pytest.LogCaptureFixture,
):
    class Args:
        def __init__(
            self,
            rev_id="D123",
            nocommit=False,
            raw=False,
            applyto="base",
            no_branch=False,
            yes=False,
        ):
            self.rev_id = rev_id
            self.nocommit = nocommit
            self.raw = raw
            self.applyto = applyto
            self.no_branch = no_branch
            self.yes = yes

    git.args = Args()
    m_git_out.side_effect = (["  branch"],)
    git.before_patch("sha1", "branch")
    m_checkout.assert_called_with("sha1")
    m_git.assert_called_with(["checkout", "-q", "-b", "branch_1"])

    m_git.reset_mock()
    m_git_out.reset_mock()
    m_checkout.reset_mock()

    m_checkout.reset_mock()
    m_git_out.side_effect = ("the branch name is not here",)
    git.args = Args(applyto="here")
    git.before_patch(None, "branchname")
    m_checkout.assert_not_called()

    m_git.reset_mock()
    m_checkout.reset_mock()
    git.args = Args(applyto="abcdef", nocommit=True)
    git.before_patch("abcdef", None)
    m_checkout.assert_called_once_with("abcdef")
    m_git.assert_not_called()

    m_git.reset_mock()
    m_checkout.reset_mock()
    caplog.clear()
    git.args = Args(no_branch=True, yes=True)
    git.before_patch("abcdef", "name")
    m_checkout.assert_called_once()
    m_git.assert_not_called()
    assert Contains("git checkout -b") in caplog.messages

    m_git.reset_mock()
    m_checkout.reset_mock()
    caplog.clear()
    git.args = Args(no_branch=True)
    git.before_patch("abcdef", "name")
    m_checkout.assert_called_once()
    m_git.assert_not_called()
    m_prompt.assert_called_once()
    assert Contains("git checkout -b") in caplog.messages

    m_git.reset_mock()
    m_checkout.reset_mock()
    caplog.clear()
    m_prompt.reset_mock()
    m_config.create_branch = False
    git.args = Args()
    git.before_patch("abcdef", "name")
    m_checkout.assert_called_once()
    m_git.assert_not_called()
    m_prompt.assert_called_once()
    assert Contains("git checkout -b") in caplog.messages

    m_prompt.return_value = "No"
    with pytest.raises(SystemExit):
        git.before_patch("abcdef", "name")


@mock.patch("mozphab.git.temporary_binary_file")
@mock.patch("mozphab.git.Git.git_call")
@mock.patch("mozphab.git.Git.commit")
def test_apply_patch(m_commit, m_git, m_temp_fn, git):
    m_temp_fn.return_value = create_temp_fn("filename")
    git.apply_patch("diff", "commit message", "user", 1)
    m_git.assert_called_once_with(["apply", "--index", "filename"])
    m_commit.assert_called_with("commit message", "user", 1)
    m_temp_fn.assert_called_once_with(b"diff")


@mock.patch("mozphab.git.Git.git_out")
def test_is_node(m_git_out, git):
    m_git_out.return_value = "commit"
    assert git.is_node("aaa")

    m_git_out.return_value = "something else"
    assert not git.is_node("aaa")

    m_git_out.side_effect = exceptions.CommandError
    assert not git.is_node("aaa")


@mock.patch("mozphab.git.Git.git_out")
def test_is_descendant(m_git_out, git):
    git.revset = ("abc", "def")

    m_git_out.return_value = ""
    assert (
        git.is_descendant("aabbcc") is True
    ), "Call to merge-base with 0 return code should indicate node is a descendant."

    m_git_out.side_effect = exceptions.CommandError("", 1)
    assert (
        git.is_descendant("aabbcc") is False
    ), "Call to merge-base with 1 return code should indicated node is not descendant."

    m_git_out.side_effect = exceptions.CommandError("test", 255)
    with pytest.raises(exceptions.CommandError) as e:
        git.is_descendant("aabbcc")

        assert (
            e.args == "test"
        ), "Original command exception was not raised to the caller."


@mock.patch("mozphab.gitcommand.which")
@mock.patch("mozphab.gitcommand.GitCommand.output")
def test_is_cinnabar_installed(m_git_out, m_which, git, tmp_path):
    def _without_str(calls):
        # Debuggers call __str__ on mocked functions, strip them
        return [c for c in calls if c[0] != "__str__"]

    # cinnabar installed as visible external command
    m_git_out.return_value = ["External commands", "   cinnabar", "   revise"]
    assert git.is_cinnabar_installed
    m_git_out.assert_called_once_with(["help", "--all"])

    # cached request (primed by 'cinnabar installed' test)
    m_git_out.reset_mock()
    m_git_out.return_value = ["External commands", "   cinnabar", "   revise"]
    assert git.is_cinnabar_installed
    m_git_out.assert_not_called()

    # create a fake cinnabar in exec-path for git to find
    cinnabar = Path(tmp_path) / "git-cinnabar"
    cinnabar.write_text("")
    cinnabar.chmod(0o755)

    # cinnabar installed in exec-path
    m_git_out.reset_mock()
    m_git_out.side_effect = [
        ["External commands", "   revise"],  # git help --all
        tmp_path,  # git --exec-path
    ]
    git.git._cinnabar_installed = None
    assert git.is_cinnabar_installed
    assert _without_str(m_git_out.mock_calls) == [
        mock.call(["help", "--all"]),
        mock.call(["--exec-path"], split=False),
    ]

    # remove cinnabar from exec-path so we fall back to looking on the path
    cinnabar.unlink()

    # cinnabar installed somewhere on path
    m_git_out.reset_mock()
    m_git_out.side_effect = [
        ["External commands", "   revise"],  # git help --all
        tmp_path,  # git --exec-path
    ]
    m_which.return_value = str(cinnabar)
    git.git._cinnabar_installed = None
    assert git.is_cinnabar_installed
    assert _without_str(m_git_out.mock_calls) == [
        mock.call(["help", "--all"]),
        mock.call(["--exec-path"], split=False),
    ]

    # cinnabar not installed
    m_git_out.reset_mock()
    m_git_out.side_effect = [
        ["External commands", "   revise"],  # git help --all
        tmp_path,  # git --exec-path
    ]
    m_which.return_value = None
    git.git._cinnabar_installed = None
    assert not git.is_cinnabar_installed
    assert _without_str(m_git_out.mock_calls) == [
        mock.call(["help", "--all"]),
        mock.call(["--exec-path"], split=False),
    ]


@mock.patch("mozphab.git.Git.git_out")
def test_unicode_in_windows_env(m_git_out, git, monkeypatch):
    monkeypatch.setattr(environment, "IS_WINDOWS", True)
    git._commit_tree("parent", "tree_hash", "message", "ćwikła", "ćwikła", "date")
    m_git_out.assert_called_once_with(
        ["commit-tree", "-p", "parent", "-F", mock.ANY, "tree_hash"],
        split=False,
        extra_env={
            "GIT_AUTHOR_NAME": "ćwikła",
            "GIT_AUTHOR_EMAIL": "ćwikła",
            "GIT_AUTHOR_DATE": "date",
        },
    )


def test_check_vcs(git):
    class Args:
        def __init__(self, force_vcs=False):
            self.safe_mode = False
            self.force_vcs = force_vcs

    args = Args()
    git.set_args(args)
    assert git.check_vcs()

    git.git._cinnabar_installed = True
    git._phab_vcs = "hg"
    assert git.check_vcs()

    git.git._cinnabar_installed = False

    # This just produces a warning now.
    # See bug 1963878.
    git.check_vcs()

    args = Args(force_vcs=True)
    git.set_args(args)
    assert git.check_vcs()


@mock.patch("mozphab.git.Git._get_commits_info")
@mock.patch("mozphab.git.Git._git_get_children")
@mock.patch("mozphab.git.Git._is_child")
def test_commit_stack_single(_1, _2, _3, git):
    git._get_commits_info.return_value = [
        """\
Tue, 22 Jan 2019 13:42:48 +0000
Conduit User
conduit@mozilla.bugs
4912923
b18312ffe929d3482f1d7b1e9716a1885c7a61b8
aaa000aaa000aaa000aaa000aaa000aaa000aaa0
title

description
"""
    ]
    git.revset = ["HEAD^", "HEAD"]
    git.commit_stack(single=True)
    git._git_get_children.assert_not_called()
    git._is_child.assert_not_called()


@pytest.mark.parametrize(
    "cinnabar_required,remotes,unified_head",
    (
        (True, ["origin"], "remotes/origin/bookmarks/beta"),
        (False, ["origin"], "remotes/origin/beta"),
        (True, ["origin", "upstream"], "remotes/origin/bookmarks/beta"),
        (False, ["origin", "upstream"], "remotes/origin/beta"),
        (True, ["upstream"], "remotes/upstream/bookmarks/beta"),
        (False, ["upstream"], "remotes/upstream/beta"),
    ),
)
@mock.patch("mozphab.git.Git.is_node")
@mock.patch("mozphab.git.Git.is_cinnabar_required", new_callable=mock.PropertyMock)
@mock.patch("mozphab.git.Git.get_base_remotes")
def test_git_get_repo_head_branch(
    m_get_base_remotes,
    m_is_cinnabar_required,
    m_is_node,
    git,
    cinnabar_required,
    remotes,
    unified_head,
):
    m_get_base_remotes.return_value = remotes
    m_is_cinnabar_required.return_value = cinnabar_required

    # If head is not a node in the repo, raise `ValueError`.
    m_is_node.return_value = False
    assert (
        git.get_repo_head_branch() is None
    ), "No found heads should have returned `None`."

    # If head is a node in the repo, should map to a remote branch.
    m_is_node.return_value = True
    assert (
        git.get_repo_head_branch() == unified_head
    ), "beta did not correctly map to a branch"


def test_git_validate_email(git):
    with pytest.raises(exceptions.Error):
        git.validate_email()

    git.git.email = "test@mozilla.com"
    assert git.validate_email() is None, "validate_email() executes without error"
