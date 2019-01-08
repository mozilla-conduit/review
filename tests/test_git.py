import imp
import mock
import os
import pytest

from conftest import create_temp_fn

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


@mock.patch("mozphab.Git.git_out")
def test_cherry(m_git_git_out, git):
    m_git_git_out.side_effect = (mozphab.CommandError, ["output"])
    assert git._cherry(["cherry"], ["one", "two"]) == ["output"]
    m_git_git_out.assert_has_calls(
        [mock.call(["cherry", "one"]), mock.call(["cherry", "two"])]
    )


@mock.patch("mozphab.Git.git_out")
@mock.patch("mozphab.Git._cherry")
def test_first_unpublished(m_git_cherry, m_git_git_out, git):
    class Args:
        def __init__(self, upstream=None, start_rev="(auto)"):
            self.upstream = upstream
            self.start_rev = start_rev

    m_git_git_out.side_effect = (["a", "b"], ["c"], ["d"])
    m_git_cherry.side_effect = (["- sha1", "+ sha2"], [], None, [])
    git.args = Args()
    first = git._get_first_unpublished_node
    assert "sha2" == first()
    m_git_cherry.assert_called_with(["cherry", "--abbrev=12"], ["a", "b"])
    assert first() is None
    with pytest.raises(mozphab.Error):
        first()
        m_git_cherry.assert_called_with(["cherry", "--abbrev=12", "upstream"], [])

    git.args = Args(upstream=["upstream"])
    first()
    m_git_cherry.assert_called_with(["cherry", "--abbrev=12", "upstream"], [])


@mock.patch("mozphab.Git.git_out")
def test_branches_to_rebase(m_git_git_out, git):
    git_find = git._find_branches_to_rebase

    # No branch returned - not a real case - we don't work without branches
    m_git_git_out.return_value = []
    assert dict() == git_find([{"orig-node": "_aaa", "node": "aaa"}])

    # No amend, no branches to rebase
    m_git_git_out.return_value = ["branch"]
    assert dict() == git_find([{"orig-node": "aaa", "node": "aaa"}])

    # One commit, one branch
    m_git_git_out.return_value = ["branch"]
    assert dict(branch=["aaa", "_aaa"]) == git_find(
        [{"orig-node": "_aaa", "node": "aaa"}]
    )

    # Two commits one branch
    m_git_git_out.return_value = ["branch"]
    assert dict(branch=["bbb", "_bbb"]) == git_find(
        [{"orig-node": "_aaa", "node": "aaa"}, {"orig-node": "_bbb", "node": "bbb"}]
    )

    # Two branches one commit
    # ... (branch1)
    # | ... (branch2)
    # |/
    # * aaa
    # More realistic output from the git command
    m_git_git_out.return_value = ["*  branch1", "  branch2"]
    assert dict(branch1=["aaa", "_aaa"], branch2=["aaa", "_aaa"]) == git_find(
        [{"orig-node": "_aaa", "node": "aaa"}]
    )

    # ... (branch1)
    # | * bbb (branch2)
    # |/
    # * aaa
    m_git_git_out.side_effect = (["branch1", "branch2"], ["branch2"])
    assert dict(branch1=["aaa", "_aaa"], branch2=["bbb", "_bbb"]) == git_find(
        [{"orig-node": "_aaa", "node": "aaa"}, {"orig-node": "_bbb", "node": "bbb"}]
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
    assert dict(
        master=["bbb", "_bbb"],
        feature1=["ccc", "_ccc"],
        feature2=["bbb", "_bbb"],
        feature1_1=["ddd", "_ddd"],
    ) == git_find(
        [
            {"orig-node": "_aaa", "node": "aaa"},
            {"orig-node": "_bbb", "node": "bbb"},
            {"orig-node": "_ccc", "node": "ccc"},
            {"orig-node": "_ddd", "node": "ddd"},
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


@mock.patch("mozphab.Git.git_out")
@mock.patch("mozphab.config")
def test_range(m_config, m_git_git_out, git):
    class Args:
        def __init__(self, start="aaa", end="."):
            self.start_rev = start
            self.end_rev = end
            self.safe_mode = False

    m_config.safe_mode = False
    m_git_git_out.return_value = ["user.email=email"]
    git.set_args(Args())
    assert git.revset == ("aaa", ".")


@mock.patch("mozphab.config")
@mock.patch("mozphab.parse_config")
@mock.patch("mozphab.Git._get_first_unpublished_node")
@mock.patch("mozphab.Git.git_out")
def test_set_args(m_git_git_out, m_git_get_first, m_parse_config, m_config, git):
    class Args:
        def __init__(self, start="(auto)", end=".", safe_mode=False):
            self.start_rev = start
            self.end_rev = end
            self.safe_mode = safe_mode

    with pytest.raises(mozphab.Error):
        git.set_args(Args())

    git._git = []
    m_config.safe_mode = False
    m_parse_config.return_value = {"user.email": "email"}
    m_git_get_first.return_value = "aaa"
    git.set_args(Args())
    assert [] == git._git
    m_git_get_first.assert_called_once()
    assert git.revset == ("aaa^", ".")

    m_parse_config.return_value = {
        "user.email": "email",
        "user.name": "name",
        "cinnabar.helper": "string",
    }
    git.set_args(Args())
    assert [] == git._git

    safe_options = (
        ["-c", "user.email=email"]
        + ["-c", "user.name=name"]
        + ["-c", "cinnabar.helper=string"]
    )
    git.set_args(Args(safe_mode=True))
    assert safe_options == git._git

    git._git = []
    m_config.safe_mode = True
    git.set_args(Args())
    assert safe_options == git._git

    m_config.safe_mode = False
    m_git_get_first.reset_mock()
    git.set_args(Args(start="bbb", end="ccc"))
    m_git_get_first.assert_not_called()
    assert git.revset == ("bbb", "ccc")

    git.set_args(Args(safe_mode=True))
    assert "" == git._env["HOME"]
    assert "" == git._env["XDG_CONFIG_HOME"]

    m_config.safe_mode = True
    git.set_args(Args())
    assert "" == git._env["HOME"]
    assert "" == git._env["XDG_CONFIG_HOME"]


@mock.patch("mozphab.Git.git_out")
def test_worktree_clean(m_git_out, git):
    m_git_out.return_value = ""
    assert git.is_worktree_clean()

    m_git_out.return_value = "xxx"
    assert not git.is_worktree_clean()


@mock.patch("mozphab.Git.git")
def test_add(m_git, git):
    git.add()
    assert m_git.called_once_with(["add", "."])


@mock.patch("mozphab.Git.git")
def test_commit(m_git, git):
    git.commit("some body")
    assert m_git.called_once()

    m_git.reset_mock()
    git.commit("some body", "user")
    assert m_git.called_once()


@mock.patch("mozphab.Git.git_out")
@mock.patch("mozphab.Git.is_node")
def test_check_node(m_git_is_node, m_git_out, git):
    node = "aabbcc"
    assert node == git.check_node(node)

    m_git_is_node.return_value = False
    with pytest.raises(mozphab.NotFoundError) as e:
        git.check_node(node)
    assert "Cinnabar extension not enabled" in str(e.value)

    git.extensions = ["cinnabar"]
    m_git_out.return_value = "0" * 40
    with pytest.raises(mozphab.NotFoundError) as e:
        git.check_node(node)
    assert "Mercurial SHA1 not found" in str(e.value)

    m_git_out.return_value = "git_aabbcc"
    with pytest.raises(mozphab.NotFoundError) as e:
        git.check_node(node)
    assert "Mercurial SHA1 detected" in str(e.value)

    m_git_is_node.side_effect = (False, True)
    assert "git_aabbcc" == git.check_node(node)


@mock.patch("mozphab.Git.git_out")
@mock.patch("mozphab.Git.checkout")
@mock.patch("mozphab.Git.git")
def test_before_patch(m_git, m_checkout, m_git_out, git):
    class Args:
        def __init__(self, rev_id="D123", nocommit=False, raw=False, applyto="base"):
            self.rev_id = rev_id
            self.nocommit = nocommit
            self.raw = raw
            self.applyto = applyto

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
    git.args = Args(applyto="abcdef", nocommit=True)
    git.before_patch("abcdef", None)
    m_checkout.assert_called_once_with("abcdef")
    m_git.assert_not_called()


@mock.patch("mozphab.temporary_file")
@mock.patch("mozphab.Git.git")
@mock.patch("mozphab.Git.add")
@mock.patch("mozphab.Git.commit")
def test_apply_patch(m_commit, m_add, m_git, m_temp_fn, git):
    m_temp_fn.return_value = create_temp_fn("filename")
    git.apply_patch("diff", "commit message", "user", 1)
    m_git.assert_called_once_with(["apply", "filename"])
    m_add.assert_called_once()
    m_commit.assert_called_with("commit message", "user", 1)
    m_temp_fn.assert_called_once_with("diff")


@mock.patch("mozphab.Git.git_out")
def test_is_node(m_git_out, git):
    m_git_out.return_value = "commit"
    assert git.is_node("aaa")

    m_git_out.return_value = "something else"
    assert not git.is_node("aaa")

    m_git_out.side_effect = mozphab.CommandError
    assert not git.is_node("aaa")
