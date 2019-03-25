import datetime
import imp
import mock
import os

import pytest

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


def test_strip_depends_on():
    strip = mozphab.strip_depends_on

    assert "" == strip("Depends on D123")
    assert "" == strip("\n Depends on D1\n")
    assert "title" == strip("title\n\nDepends on D1")
    assert "title\n\nbefore\n\nafter" == strip("title\n\nbefore\nDepends on D1\nafter")
    assert "Depends on DA" == strip("Depends on DA")


def test_prepare_body():
    prep = mozphab.prepare_body
    assert "\n\nDifferential Revision: http://phabricator.test/D1" == prep(
        "", "", 1, "http://phabricator.test"
    )
    assert (
        "\n\nDifferential Revision: http://phabricator.test/D2\n\nDepends on D1"
        == prep("", "", 2, "http://phabricator.test", depends_on=1)
    )
    assert (
        "title\n\nDifferential Revision: http://phabricator.test/D2\n\nDepends on D1"
        == prep("title", "", 2, "http://phabricator.test", depends_on=1)
    )
    assert (
        "title\n\nsome\nsummary\n\nDifferential Revision: http://phabricator.test/D2\n\nDepends on D1"
        == prep("title", "some\nsummary", 2, "http://phabricator.test", depends_on=1)
    )


@mock.patch("mozphab.arc_call_conduit")
def test_get_revisions(m_arc):
    get_revs = mozphab.get_revisions
    with pytest.raises(ValueError):
        get_revs("x", ids=[1], phids=[1])

    m_arc.return_value = {}
    assert get_revs("x", ids=[1]) is None
    m_arc.assert_called_with(
        "differential.revision.search", dict(constraints=dict(ids=[1])), "x"
    )
    m_arc.reset_mock()
    assert get_revs("x", phids=[1]) is None
    m_arc.assert_called_with(
        "differential.revision.search", dict(constraints=dict(phids=[1])), "x"
    )

    m_arc.reset_mock()
    m_arc.return_value = {
        "data": [
            dict(id=1, phid="PHID-1"),
            dict(id=2, phid="PHID-2"),
            dict(id=3, phid="PHID-3"),
        ]
    }
    assert get_revs("x", ids=[2, 1, 3]) == [
        dict(id=2, phid="PHID-2"),
        dict(id=1, phid="PHID-1"),
        dict(id=3, phid="PHID-3"),
    ]

    assert get_revs("x", phids=["PHID-2", "PHID-1", "PHID-3"]) == [
        dict(id=2, phid="PHID-2"),
        dict(id=1, phid="PHID-1"),
        dict(id=3, phid="PHID-3"),
    ]


@mock.patch("mozphab.arc_call_conduit")
def test_get_diffs(m_arc):
    get_diffs = mozphab.get_diffs

    m_arc.return_value = {}
    m_arc.return_value = dict(
        data=[dict(phid="PHID-1"), dict(phid="PHID-2"), dict(phid="PHID-3")]
    )
    assert get_diffs(["PHID-2", "PHID-1", "PHID-3"], "x") == {
        "PHID-1": dict(phid="PHID-1"),
        "PHID-2": dict(phid="PHID-2"),
        "PHID-3": dict(phid="PHID-3"),
    }


@mock.patch("mozphab.get_related_phids")
def test_successor(m_get_related):
    m_get_related.return_value = []
    assert not mozphab.has_successor("SOME-PHID", "x")
    m_get_related.assert_called_once_with(
        "SOME-PHID", None, "x", relation="child", proceed=False
    )

    m_get_related.return_value = ["A-PHID"]
    assert mozphab.has_successor("SOME-PHID", "x")


@mock.patch("mozphab.arc_call_conduit")
def test_get_related_phids(m_arc):
    get_phids = mozphab.get_related_phids

    m_arc.return_value = {}

    assert [] == get_phids("aaa", None, "x")
    m_arc.assert_called_once_with(
        "edge.search", {"sourcePHIDs": ["aaa"], "types": ["revision.parent"]}, "x"
    )

    assert ["bbb"] == get_phids("aaa", ["bbb"], "x")

    m_arc.side_effect = [
        dict(data=[dict(destinationPHID="bbb")]),
        dict(data=[dict(destinationPHID="aaa")]),
        dict(),
    ]
    assert ["bbb", "aaa"] == get_phids("ccc", None, "x")

    m_arc.side_effect = [
        dict(data=[dict(destinationPHID="bbb")]),
        dict(data=[dict(destinationPHID="aaa")]),
        dict(),
    ]
    assert ["bbb"] == get_phids("ccc", None, "x", proceed=False)


@mock.patch("mozphab.check_call")
def test_apply_patch(m_check_call):
    mozphab.apply_patch("diff", "x")
    m_check_call.assert_called_once()


def test_base_ref():
    assert mozphab.get_base_ref({"fields": {}}) is None

    diff = {"fields": {"refs": [{"identifier": "sha1", "type": "sometype"}]}}
    assert mozphab.get_base_ref({"fields": {}}) is None

    diff = {"fields": {"refs": [{"identifier": "sha1", "type": "base"}]}}
    assert mozphab.get_base_ref(diff) == "sha1"


@mock.patch("mozphab.arc_call_conduit")
@mock.patch("mozphab.Git.is_worktree_clean")
@mock.patch("mozphab.config")
@mock.patch("mozphab.Git.check_arc")
@mock.patch("mozphab.get_revisions")
@mock.patch("mozphab.has_successor")
@mock.patch("mozphab.get_ancestor_phids")
@mock.patch("mozphab.get_successor_phids")
@mock.patch("mozphab.get_diffs")
@mock.patch("mozphab.get_base_ref")
@mock.patch("mozphab.Git.before_patch")
@mock.patch("mozphab.apply_patch")
@mock.patch("mozphab.prepare_body")
@mock.patch("mozphab.Git.apply_patch")
@mock.patch("mozphab.Git.check_node")
@mock.patch("mozphab.logger")
def test_patch(
    m_logger,
    m_git_check_node,
    m_git_apply_patch,
    m_prepare_body,
    m_apply_patch,
    m_git_before_patch,
    m_get_base_ref,
    m_get_diffs,
    m_get_successor_phids,
    m_get_ancestor_phids,
    m_has_successor,
    m_get_revisions,
    m_git_check_arc,
    m_config,
    m_git_is_worktree_clean,
    m_arc_call_conduit,
    git,
):
    m_git_check_arc.return_value = False
    m_config.arc_command = "arc"
    with pytest.raises(mozphab.Error):
        mozphab.patch(git, None)

    class Args:
        def __init__(
            self,
            rev_id="D123",
            no_commit=False,
            raw=False,
            apply_to="base",
            yes=False,
            skip_dependencies=False,
        ):
            self.rev_id = rev_id
            self.no_commit = no_commit
            self.raw = raw
            self.apply_to = apply_to
            self.yes = yes
            self.skip_dependencies = skip_dependencies

    m_git_check_arc.return_value = True
    m_git_is_worktree_clean.return_value = False
    with pytest.raises(mozphab.Error):
        mozphab.patch(git, Args())

    m_git_is_worktree_clean.return_value = True
    m_get_revisions.return_value = []
    args = Args()
    with pytest.raises(mozphab.Error):
        mozphab.patch(git, args)

    m_config.always_full_stack = False
    m_has_successor.return_value = False
    m_get_ancestor_phids.return_value = []
    m_get_base_ref.return_value = "sha111"
    m_arc_call_conduit.return_value = "raw"  # differential.getrawdiff
    m_get_revisions.return_value = [
        dict(
            phid="PHID-1",
            id=1,
            fields=dict(diffPHID="DIFFPHID-1", title="title", summary="summary"),
        )
    ]
    m_get_diffs.return_value = {
        "DIFFPHID-1": {
            "id": 1,
            "attachments": dict(
                commits=dict(
                    commits=[
                        dict(
                            author=dict(
                                name="user",
                                email="author@example.com",
                                epoch=1547806078,
                            )
                        )
                    ]
                )
            ),
        }
    }
    m_git_check_node.return_value = "sha111"
    m_prepare_body.return_value = "commit message"
    mozphab.patch(git, Args())
    m_git_apply_patch.assert_called_once_with(
        "raw",
        "commit message",
        "user <author@example.com>",
        datetime.datetime.fromtimestamp(1547806078).isoformat(),
    )
    m_apply_patch.assert_not_called()
    m_get_base_ref.assert_called_once()
    m_git_before_patch.assert_called_once_with("sha111", "D1")

    m_get_base_ref.return_value = None
    with pytest.raises(mozphab.Error):
        mozphab.patch(git, Args())

    m_get_base_ref.return_value = "sha111"
    m_git_apply_patch.reset_mock()
    m_git_before_patch.reset_mock()
    # --raw
    mozphab.patch(git, Args(raw=True))
    m_git_before_patch.not_called()
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_not_called()
    m_logger.info.assert_called_with("raw")

    # skip-dependencies
    m_has_successor.reset_mock()
    m_get_successor_phids.reset_mock()
    mozphab.patch(git, Args(raw=True, skip_dependencies=True))
    m_has_successor.assert_not_called()
    m_get_successor_phids.assert_not_called()

    m_git_before_patch.reset_mock()
    # --no_commit
    mozphab.patch(git, Args(no_commit=True))
    m_git_before_patch.assert_called_once()
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_called_once_with("raw", "x")
    m_git_before_patch.assert_called_once_with("sha111", None)

    m_git_before_patch.reset_mock()
    # --no_commit --applyto head
    mozphab.patch(git, Args(no_commit=True, apply_to="head"))
    m_git_before_patch.not_called()

    m_get_base_ref.reset_mock()
    m_apply_patch.reset_mock()
    # --apply_to head
    mozphab.patch(git, Args(apply_to="head"))
    m_get_base_ref.assert_not_called()
    m_git_apply_patch.assert_called_once_with(
        "raw",
        "commit message",
        "user <author@example.com>",
        datetime.datetime.fromtimestamp(1547806078).isoformat(),
    )
    m_apply_patch.assert_not_called()

    m_git_before_patch.reset_mock()
    node = "abcdef"
    m_git_check_node.return_value = node
    # --applyto NODE
    mozphab.patch(git, Args(apply_to=node))
    m_git_before_patch.assert_called_once_with(node, "D1")

    m_git_before_patch.reset_mock()
    # --applyto here
    mozphab.patch(git, Args(apply_to="here"))
    m_git_before_patch.assert_called_once_with(None, "D1")

    # ########## no commit info in diffs
    m_get_diffs.return_value = {
        "DIFFPHID-1": {"id": 1, "attachments": dict(commits=dict(commits=[]))}
    }
    m_arc_call_conduit.side_effect = ("raw",)
    with pytest.raises(mozphab.Error):
        mozphab.patch(git, Args())

    m_git_apply_patch.reset_mock()
    m_apply_patch.reset_mock()
    mozphab.patch(git, Args(no_commit=True))
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_called_once()

    m_logger.reset_mock()
    m_arc_call_conduit.side_effect = ("raw",)
    mozphab.patch(git, Args(raw=True))
    m_logger.info.assert_called_once_with("raw")

    # ########## multiple revisions
    m_logger.reset_mock()
    m_get_revisions.side_effect = ([REV_1], [REV_2])
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1, "DIFFPHID-2": DIFF_2}
    m_get_ancestor_phids.return_value = ["PHID-2"]
    m_arc_call_conduit.side_effect = ("raw2", "raw1")
    # --raw 2 revisions in stack
    mozphab.patch(git, Args(raw=True))
    m_logger.info.assert_has_calls((mock.call("raw2"), mock.call("raw1")))

    # node not found
    m_get_revisions.side_effect = None
    m_git_check_node.side_effect = mozphab.NotFoundError
    with pytest.raises(mozphab.Error) as e:
        mozphab.patch(git, Args(apply_to=node))
        assert "Unknown revision: %s\nERROR" % node in e.msg

    # successors
    m_get_revisions.reset_mock()
    m_has_successor.return_value = True
    m_get_revisions.side_effect = ([REV_1], [REV_2])
    m_has_successor.return_value = True
    m_get_successor_phids.side_effect = (["PHID-2"], ["PHID-2"], [])
    m_get_ancestor_phids.return_value = []
    m_arc_call_conduit.side_effect = ("raw2", "raw1")
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1, "DIFFPHID-2": DIFF_2}
    mozphab.patch(git, Args(rev_id="D1", raw=True, yes=True))
    assert m_get_revisions.call_args_list == [
        mock.call("x", ids=[1]),
        mock.call("x", phids=["PHID-2"]),
    ]

    # multiple successors
    m_get_revisions.reset_mock()
    m_get_revisions.side_effect = ([REV_1], [REV_2])
    m_get_successor_phids.side_effect = (mozphab.NonLinearException,)
    m_arc_call_conduit.side_effect = ("raw",)
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    mozphab.patch(git, Args(rev_id="D1", raw=True, yes=True))
    m_get_revisions.assert_called_once_with("x", ids=[1])


REV_1 = dict(
    phid="PHID-1",
    id=1,
    fields=dict(diffPHID="DIFFPHID-1", title="title", summary="summary"),
)

REV_2 = dict(
    phid="PHID-2",
    id=2,
    fields=dict(diffPHID="DIFFPHID-2", title="title", summary="summary"),
)

DIFF_1 = dict(
    id=1,
    attachments=dict(
        commits=dict(
            commits=[dict(author=dict(name="user", email="author@example.com"))]
        )
    ),
)
DIFF_2 = dict(
    id=2,
    attachments=dict(
        commits=dict(
            commits=[dict(author=dict(name="user", email="author@example.com"))]
        )
    ),
)
