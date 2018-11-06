import imp
import mock
import os
import pytest

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
