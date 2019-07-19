import datetime
import imp
import mock
import os
import argparse

import pytest

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)
mozphab.SHOW_SPINNER = False


def test_check_revision_id():
    check_revision_id = mozphab.check_revision_id

    assert check_revision_id("123") == 123
    assert check_revision_id("D123") == 123
    assert check_revision_id("https://phabricator.example.com/D123") == 123
    assert check_revision_id("https://phabricator.example.com/D123?") == 123
    with pytest.raises(argparse.ArgumentTypeError):
        check_revision_id("D")
    with pytest.raises(argparse.ArgumentTypeError):
        check_revision_id("https://example.com/")


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
        "title\n\n"
        "Differential Revision: http://phabricator.test/D2\n\n"
        "Depends on D1" == prep("title", "", 2, "http://phabricator.test", depends_on=1)
    )
    assert (
        "title\n\n"
        "some\n"
        "summary\n\n"
        "Differential Revision: http://phabricator.test/D2\n\n"
        "Depends on D1"
        == prep("title", "some\nsummary", 2, "http://phabricator.test", depends_on=1)
    )


@mock.patch("mozphab.ConduitAPI.call")
def test_get_revisions(call_conduit):
    repo = mozphab.Repository(None, None, "dummy")
    mozphab.conduit.set_args_from_repo(repo)
    get_revs = mozphab.conduit.get_revisions

    # sanity checks
    with pytest.raises(ValueError):
        get_revs(ids=[1], phids=["PHID-1"])
    with pytest.raises(ValueError):
        get_revs(ids=None)
    with pytest.raises(ValueError):
        get_revs(phids=None)

    call_conduit.return_value = {"data": [dict(id=1, phid="PHID-1")]}

    # differential.revision.search by revision-id
    assert len(get_revs(ids=[1])) == 1
    call_conduit.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(ids=[1]), attachments=dict(reviewers=True)),
    )

    # differential.revision.search by phid
    call_conduit.reset_mock()
    mozphab.cache.reset()
    assert len(get_revs(phids=["PHID-1"])) == 1
    call_conduit.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(phids=["PHID-1"]), attachments=dict(reviewers=True)),
    )

    # differential.revision.search by revision-id with duplicates
    call_conduit.reset_mock()
    mozphab.cache.reset()
    assert len(get_revs(ids=[1, 1])) == 2
    call_conduit.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(ids=[1]), attachments=dict(reviewers=True)),
    )

    # differential.revision.search by phid with duplicates
    call_conduit.reset_mock()
    mozphab.cache.reset()
    assert len(get_revs(phids=["PHID-1", "PHID-1"])) == 2
    call_conduit.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(phids=["PHID-1"]), attachments=dict(reviewers=True)),
    )

    # ordering of results must match input
    call_conduit.reset_mock()
    mozphab.cache.reset()
    call_conduit.return_value = {
        "data": [
            dict(id=1, phid="PHID-1"),
            dict(id=2, phid="PHID-2"),
            dict(id=3, phid="PHID-3"),
        ]
    }
    assert get_revs(ids=[2, 1, 3]) == [
        dict(id=2, phid="PHID-2"),
        dict(id=1, phid="PHID-1"),
        dict(id=3, phid="PHID-3"),
    ]

    assert get_revs(phids=["PHID-2", "PHID-1", "PHID-3"]) == [
        dict(id=2, phid="PHID-2"),
        dict(id=1, phid="PHID-1"),
        dict(id=3, phid="PHID-3"),
    ]


@mock.patch("mozphab.ConduitAPI.call")
def test_get_diffs(call_conduit):
    conduit = mozphab.conduit
    get_diffs = conduit.get_diffs

    call_conduit.return_value = {}
    call_conduit.return_value = dict(
        data=[dict(phid="PHID-1"), dict(phid="PHID-2"), dict(phid="PHID-3")]
    )
    assert get_diffs(["PHID-2", "PHID-1", "PHID-3"]) == {
        "PHID-1": dict(phid="PHID-1"),
        "PHID-2": dict(phid="PHID-2"),
        "PHID-3": dict(phid="PHID-3"),
    }


@mock.patch("mozphab.ConduitAPI.call")
def test_get_related_phids(m_call):
    get_related_phids = mozphab.conduit.get_related_phids

    m_call.return_value = {}
    assert [] == get_related_phids("aaa", include_abandoned=True)
    m_call.assert_called_once_with(
        "edge.search", {"sourcePHIDs": ["aaa"], "types": ["revision.parent"]}
    )

    m_call.side_effect = [
        dict(data=[dict(destinationPHID="bbb")]),
        dict(data=[dict(destinationPHID="aaa")]),
        dict(),
    ]
    assert ["bbb", "aaa"] == get_related_phids("ccc", include_abandoned=True)

    m_call.side_effect = [
        dict(data=[dict(destinationPHID="bbb")]),
        dict(data=[dict(destinationPHID="aaa")]),
        dict(),
        dict(
            data=[
                dict(id=1, phid="aaa", fields=dict(status=dict(value="-"))),
                dict(id=2, phid="bbb", fields=dict(status=dict(value="abandoned"))),
            ]
        ),
    ]
    assert ["aaa"] == get_related_phids("ccc", include_abandoned=False)


@mock.patch("mozphab.check_call")
def test_apply_patch(m_check_call):
    mozphab.apply_patch("diff", "x")
    m_check_call.assert_called_once()


def test_base_ref():
    assert mozphab.get_base_ref({"fields": {}}) is None

    diff = {"fields": {"refs": [{"identifier": "sha1", "type": "sometype"}]}}
    assert mozphab.get_base_ref(diff) is None

    diff = {"fields": {"refs": [{"identifier": "sha1", "type": "base"}]}}
    assert mozphab.get_base_ref(diff) == "sha1"


@mock.patch("mozphab.ConduitAPI.call")
@mock.patch("mozphab.Git.is_worktree_clean")
@mock.patch("mozphab.config")
@mock.patch("mozphab.ConduitAPI.check")
@mock.patch("mozphab.ConduitAPI.get_revisions")
@mock.patch("mozphab.ConduitAPI.get_ancestor_phids")
@mock.patch("mozphab.ConduitAPI.get_successor_phids")
@mock.patch("mozphab.ConduitAPI.get_diffs")
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
    m_get_revisions,
    m_git_check_conduit,
    m_config,
    m_git_is_worktree_clean,
    m_call_conduit,
    git,
):
    mozphab.conduit.set_args_from_repo(git)

    m_git_check_conduit.return_value = False
    m_config.arc_command = "arc"
    with pytest.raises(mozphab.Error):
        mozphab.patch(git, None)

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
        ):
            self.revision_id = revision_id
            self.no_commit = no_commit
            self.raw = raw
            self.apply_to = apply_to
            self.yes = yes
            self.skip_dependencies = skip_dependencies
            self.include_abandoned = include_abandoned

    m_git_check_conduit.return_value = True
    m_git_is_worktree_clean.return_value = False
    with pytest.raises(mozphab.Error):
        mozphab.patch(git, Args())

    m_git_is_worktree_clean.return_value = True
    m_get_revisions.return_value = []
    args = Args()
    with pytest.raises(mozphab.Error):
        mozphab.patch(git, args)

    m_config.always_full_stack = False
    m_get_successor_phids.return_value = []
    m_get_ancestor_phids.return_value = []
    m_get_base_ref.return_value = "sha111"
    m_call_conduit.return_value = "raw"  # differential.getrawdiff
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
            "fields": dict(dateCreated=1547806078),
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

    m_git_apply_patch.reset_mock()
    m_get_diffs.return_value = {
        "DIFFPHID-1": {
            "id": 1,
            "fields": dict(dateCreated=1547806078),
            "attachments": dict(
                commits=dict(
                    commits=[
                        dict(
                            author=dict(
                                name="user", email="author@example.com", epoch=None
                            )
                        )
                    ]
                )
            ),
        }
    }
    mozphab.patch(git, Args())
    m_git_apply_patch.assert_called_once_with(
        "raw",
        "commit message",
        "user <author@example.com>",
        datetime.datetime.fromtimestamp(1547806078).isoformat(),
    )

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
    m_get_successor_phids.reset_mock()
    mozphab.patch(git, Args(raw=True, skip_dependencies=True))
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
    m_call_conduit.side_effect = ("raw",)
    with pytest.raises(mozphab.Error):
        mozphab.patch(git, Args())

    m_git_apply_patch.reset_mock()
    m_apply_patch.reset_mock()
    mozphab.patch(git, Args(no_commit=True))
    m_git_apply_patch.assert_not_called()
    m_apply_patch.assert_called_once()

    m_logger.reset_mock()
    m_call_conduit.side_effect = ("raw",)
    mozphab.patch(git, Args(raw=True))
    m_logger.info.assert_called_once_with("raw")

    # ########## multiple revisions
    m_logger.reset_mock()
    m_get_revisions.side_effect = ([REV_1], [REV_2])
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1, "DIFFPHID-2": DIFF_2}
    m_get_ancestor_phids.return_value = ["PHID-2"]
    m_call_conduit.side_effect = ("raw2", "raw1")
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
    m_get_revisions.side_effect = ([REV_1], [REV_2])
    m_get_successor_phids.side_effect = (["PHID-2"], ["PHID-2"], [])
    m_get_ancestor_phids.return_value = []
    m_call_conduit.side_effect = ("raw2", "raw1")
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1, "DIFFPHID-2": DIFF_2}
    mozphab.patch(git, Args(revision_id=1, raw=True, yes=True))
    assert m_get_revisions.call_args_list == [
        mock.call(ids=[1]),
        mock.call(phids=["PHID-2"]),
    ]

    # multiple successors
    m_get_revisions.reset_mock()
    m_get_revisions.side_effect = ([REV_1], [REV_2])
    m_get_successor_phids.side_effect = (mozphab.NonLinearException,)
    m_call_conduit.side_effect = ("raw",)
    m_get_diffs.return_value = {"DIFFPHID-1": DIFF_1}
    mozphab.patch(git, Args(revision_id=1, raw=True, yes=True))
    m_get_revisions.assert_called_once_with(ids=[1])


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
