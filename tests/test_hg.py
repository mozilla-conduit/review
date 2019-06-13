import copy
import imp
import mock
import os
import pytest

from conftest import create_temp_fn
from subprocess import CalledProcessError

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)
mozphab.SHOW_SPINNER = False


@mock.patch("mozphab.Mercurial.hg_out")
def test_get_successor(m_hg_hg_out, hg):
    m_hg_hg_out.return_value = []
    assert (None, None) == hg._get_successor("x")

    m_hg_hg_out.return_value = ["1 abcde"]
    assert ["1", "abcde"] == hg._get_successor("x")

    m_hg_hg_out.return_value = ["a", "b"]
    with pytest.raises(mozphab.Error):
        hg._get_successor("x")


@mock.patch("mozphab.Mercurial._get_successor")
@mock.patch("mozphab.Mercurial.rebase_commit")
@mock.patch("mozphab.Mercurial._get_parent")
def test_finalize(m_get_parent, m_hg_rebase, m_hg_get_successor, hg):
    commits = [
        {"rev": "1", "node": "aaa", "orig-node": "aaa"},
        {"rev": "2", "node": "bbb", "orig-node": "bbb"},
        {"rev": "3", "node": "ccc", "orig-node": "ccc"},
    ]

    m_get_parent.return_value = "different:than_others"
    m_hg_get_successor.return_value = (None, None)
    hg.finalize(copy.deepcopy(commits))
    assert m_hg_rebase.call_count == 2
    assert m_hg_rebase.call_args_list == [
        mock.call(
            {"rev": "2", "node": "bbb", "orig-node": "bbb"},
            {"rev": "1", "node": "aaa", "orig-node": "aaa"},
        ),
        mock.call(
            {"rev": "3", "node": "ccc", "orig-node": "ccc"},
            {"rev": "2", "node": "bbb", "orig-node": "bbb"},
        ),
    ]

    m_get_parent.side_effect = ("first", "aaa", "last")
    m_hg_rebase.reset_mock()
    hg.finalize(commits)
    m_hg_rebase.assert_called_once_with(
        {"rev": "3", "node": "ccc", "orig-node": "ccc"},
        {"rev": "2", "node": "bbb", "orig-node": "bbb"},
    )

    m_hg_get_successor.reset_mock()
    m_get_parent.side_effect = None
    m_get_parent.return_value = "different:than_others"
    m_hg_get_successor.side_effect = [(None, None), ("4", "ddd")]
    _commits = commits[:]
    hg.finalize(_commits)
    assert m_hg_get_successor.call_count == 2
    assert m_hg_get_successor.call_args_list == [mock.call("bbb"), mock.call("ccc")]
    assert _commits == [
        {"rev": "1", "node": "aaa", "orig-node": "aaa"},
        {"rev": "2", "node": "bbb", "orig-node": "bbb"},
        {"rev": "3", "node": "ddd", "orig-node": "ccc", "name": "4:ddd"},
    ]

    m_hg_rebase.reset_mock()
    m_hg_get_successor.side_effect = None
    m_hg_get_successor.return_value = (None, None)
    _commits = commits[:]
    _commits[0]["node"] = "AAA"  # node has been amended
    hg.finalize(_commits)
    assert m_hg_rebase.call_count == 2


@mock.patch("mozphab.Mercurial.rebase_commit")
def test_finalize_no_evolve(m_hg_rebase, hg):
    hg.use_evolve = False
    hg.finalize([dict(rev="1", node="aaa"), dict(rev="2", node="bbb")])
    assert m_hg_rebase.not_called()


@mock.patch("mozphab.config")
@mock.patch("mozphab.parse_config")
@mock.patch("mozphab.Mercurial.hg_out")
@mock.patch("mozphab.Mercurial.hg_log")
def test_set_args(m_hg_hg_log, m_hg_hg_out, m_parse_config, m_config, hg):
    class Args:
        def __init__(self, start="(auto)", end=".", safe_mode=False):
            self.start_rev = start
            self.end_rev = end
            self.safe_mode = safe_mode

    with pytest.raises(mozphab.Error):
        hg.set_args(Args())

    hg._hg = []
    m_config.safe_mode = False
    m_parse_config.return_value = {"ui.username": "username", "extensions.evolve": ""}
    hg.set_args(Args())
    assert ["--config", "extensions.rebase="] == hg._hg
    assert hg.use_evolve == True
    assert hg.has_shelve == False

    # safe_mode
    safe_mode_options = (
        ["--config", "extensions.rebase="]
        + ["--config", "ui.username=username"]
        + ["--config", "extensions.evolve="]
    )
    hg._hg = []
    hg.set_args(Args(safe_mode=True))
    assert safe_mode_options == hg._hg

    m_config.safe_mode = True
    hg._hg = []
    hg.set_args(Args())
    assert safe_mode_options == hg._hg

    # no evolve
    m_config.safe_mode = False
    hg._hg = []
    m_parse_config.return_value = {"ui.username": "username", "extensions.shelve": ""}
    hg.set_args(Args())
    assert (
        ["--config", "extensions.rebase="]
        + ["--config", "experimental.evolution.createmarkers=true"]
        + ["--config", "extensions.strip="]
    ) == hg._hg
    assert hg.use_evolve == False
    assert hg.has_shelve == True

    m_hg_hg_log.side_effect = [("1234567890123",), ("0987654321098",)]
    hg.set_args(Args())
    assert "123456789012::098765432109" == hg.revset

    m_hg_hg_log.side_effect = IndexError
    with pytest.raises(mozphab.Error):
        hg.set_args(Args())


@mock.patch("mozphab.Mercurial._status")
def test_clean_worktree(m_status, hg):
    m_status.return_value = {"T": None, "U": None}
    assert hg.is_worktree_clean()

    m_status.return_value = {"T": True, "U": None}
    assert not hg.is_worktree_clean()

    m_status.return_value = {"T": None, "U": True}
    assert hg.is_worktree_clean()

    m_status.return_value = {"T": True, "U": True}
    assert not hg.is_worktree_clean()


@mock.patch("mozphab.Mercurial.hg")
def test_commit(m_hg, hg):
    hg.commit("some body")
    m_hg.called_once()


@mock.patch("mozphab.Mercurial.checkout")
@mock.patch("mozphab.Mercurial.hg_out")
@mock.patch("mozphab.Mercurial.hg")
@mock.patch("mozphab.config")
def test_before_patch(m_config, m_hg, m_hg_out, m_checkout, hg):
    class Args:
        def __init__(
            self,
            rev_id="D123",
            nocommit=False,
            raw=False,
            applyto="base",
            no_bookmark=False,
        ):
            self.rev_id = rev_id
            self.nocommit = nocommit
            self.raw = raw
            self.applyto = applyto
            self.no_bookmark = no_bookmark

    m_config.create_bookmark = True
    m_hg_out.side_effect = ["bookmark"]
    hg.args = Args()
    hg.before_patch("sha1", "bookmark")
    m_checkout.assert_called_with("sha1")
    m_hg_out.assert_called()
    m_hg.assert_called_with(["bookmark", "bookmark_1"])
    m_checkout.assert_called_once_with("sha1")

    m_checkout.reset_mock()
    hg.args = Args(nocommit=True)
    m_hg.reset_mock()
    hg.before_patch("sha1", None)
    m_hg.assert_not_called()
    m_checkout.assert_called_once_with("sha1")

    hg.args = Args(applyto="here")
    m_checkout.reset_mock()
    m_hg_out.reset_mock()
    m_hg_out.side_effect = None
    m_hg_out.return_value = "some book_marks"
    hg.before_patch(None, "bookmark")
    m_hg_out.assert_called_once()
    m_checkout.assert_not_called()

    hg.args = Args(applyto="here")
    m_checkout.reset_mock()
    m_hg_out.reset_mock()
    m_hg_out.side_effect = None
    m_hg_out.return_value = "some book_marks"
    hg.before_patch(None, "bookmark")
    m_hg_out.assert_called_once()
    m_checkout.assert_not_called()

    m_hg_out.reset_mock()
    hg.args = Args(no_bookmark=True)
    hg.before_patch(None, "bookmark")
    m_hg_out.assert_not_called()

    m_config.create_bookmark = False
    m_hg_out.reset_mock()
    hg.args = Args()
    hg.before_patch(None, "bookmark")
    m_hg_out.assert_not_called()


@mock.patch("mozphab.temporary_file")
@mock.patch("mozphab.Mercurial.hg")
def test_apply_patch(m_hg, m_temp_fn, hg):
    m_temp_fn.return_value = create_temp_fn("diff_fn", "body_fn")
    hg.apply_patch("diff", "body", "user", 1)
    m_hg.assert_called_once_with(
        ["import", "diff_fn", "--quiet", "-l", "body_fn", "-u", "user", "-d", 1]
    )
    assert m_temp_fn.call_count == 2


@mock.patch("mozphab.Mercurial.hg_out")
def test_is_node(m_hg_out, hg):
    assert hg.is_node("aabbcc")
    m_hg_out.assert_called_once_with(["identify", "-q", "-r", "aabbcc"])

    m_hg_out.side_effect = mock.Mock(side_effect=CalledProcessError(None, None))
    assert not hg.is_node("aaa")


@mock.patch("mozphab.Mercurial.is_node")
def test_check_node(m_is_node, hg):
    node = "aabbcc"
    m_is_node.return_value = True
    assert node == hg.check_node(node)

    m_is_node.return_value = False
    with pytest.raises(mozphab.NotFoundError) as e:
        hg.check_node(node)

    assert "" == str(e.value)
