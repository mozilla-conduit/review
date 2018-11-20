import imp
import mock
import os
import pytest

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


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
def test_finalize(m_hg_rebase, m_hg_get_successor, hg):
    commits = [
        dict(rev="1", node="aaa"),
        dict(rev="2", node="bbb"),
        dict(rev="3", node="ccc"),
    ]

    m_hg_get_successor.return_value = (None, None)
    hg.finalize(commits[:])
    assert m_hg_rebase.call_count == 2
    assert m_hg_rebase.call_args_list == [
        mock.call(dict(rev="2", node="bbb"), dict(rev="1", node="aaa")),
        mock.call(dict(rev="3", node="ccc"), dict(rev="2", node="bbb"))
    ]

    m_hg_get_successor.side_effect = [(None, None), ("4", "ddd")]
    hg.finalize(commits)
    assert m_hg_get_successor.called_once_with(dict(rev="3", node="ccc"), "ddd", "4")
    assert commits == [
        dict(rev="1", node="aaa"),
        dict(rev="2", node="bbb"),
        dict(rev="3", node="ddd", name="4:ddd"),
    ]


@mock.patch("mozphab.Mercurial.rebase_commit")
def test_finalize_no_evolve(m_hg_rebase, hg):
    hg.use_evolve = False
    hg.finalize([dict(rev="1", node="aaa"), dict(rev="2", node="bbb")])
    assert m_hg_rebase.not_called()
