# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest import mock
import pytest

from mozphab import exceptions, mozphab
from mozphab.commands import reorganise


@pytest.mark.parametrize(
    "phids,transactions",
    [
        # No change
        (("A", "A"), {}),
        (("ABC", "ABC"), {}),
        (([], ["A"]), {}),
        # Abandon
        (
            ("ABC", "A"),
            {
                "A": [{"type": "children.remove", "value": ["B"]}],
                "B": [
                    {"type": "children.remove", "value": ["C"]},
                    {"type": "abandon", "value": True},
                ],
                "C": [{"type": "abandon", "value": True}],
            },
        ),
        (
            ("ABC", "B"),
            {
                "A": [
                    {"type": "children.remove", "value": ["B"]},
                    {"type": "abandon", "value": True},
                ],
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "abandon", "value": True}],
            },
        ),
        (
            ("ABC", "C"),
            {
                "A": [
                    {"type": "children.remove", "value": ["B"]},
                    {"type": "abandon", "value": True},
                ],
                "B": [
                    {"type": "children.remove", "value": ["C"]},
                    {"type": "abandon", "value": True},
                ],
            },
        ),
        # Reorder
        (
            ("AB", "BA"),
            {
                "A": [{"type": "children.remove", "value": ["B"]}],
                "B": [{"type": "children.set", "value": ["A"]}],
            },
        ),
        (
            ("ABC", "BC"),
            {
                "A": [
                    {"type": "children.remove", "value": ["B"]},
                    {"type": "abandon", "value": True},
                ]
            },
        ),
        (
            ("ABC", "ACB"),
            {
                "A": [{"type": "children.set", "value": ["C"]}],
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "children.set", "value": ["B"]}],
            },
        ),
        (
            ("ABC", "BAC"),
            {
                "A": [{"type": "children.set", "value": ["C"]}],
                "B": [{"type": "children.set", "value": ["A"]}],
            },
        ),
        (
            ("ABC", "CAB"),
            {
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "children.set", "value": ["A"]}],
            },
        ),
        (
            ("ABC", "CBA"),
            {
                "A": [{"type": "children.remove", "value": ["B"]}],
                "B": [{"type": "children.set", "value": ["A"]}],
                "C": [{"type": "children.set", "value": ["B"]}],
            },
        ),
        # Insert
        (("ABC", "DABC"), {"D": [{"type": "children.set", "value": ["A"]}]}),
        (
            ("ABC", "ADBC"),
            {
                "A": [{"type": "children.set", "value": ["D"]}],
                "D": [{"type": "children.set", "value": ["B"]}],
            },
        ),
        (
            ("ABC", "ABDC"),
            {
                "B": [{"type": "children.set", "value": ["D"]}],
                "D": [{"type": "children.set", "value": ["C"]}],
            },
        ),
        (
            ("ABC", "BCAD"),
            {
                "A": [{"type": "children.set", "value": ["D"]}],
                "C": [{"type": "children.set", "value": ["A"]}],
            },
        ),
        (([], ["A", "B"]), {"A": [{"type": "children.set", "value": ["B"]}]}),
        # Insert and reorder
        (
            ("ABC", "DCAB"),
            {
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "children.set", "value": ["A"]}],
                "D": [{"type": "children.set", "value": ["C"]}],
            },
        ),
        (
            ("ABC", "CDAB"),
            {
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "children.set", "value": ["D"]}],
                "D": [{"type": "children.set", "value": ["A"]}],
            },
        ),
        (
            ("ABC", "CADB"),
            {
                "A": [{"type": "children.set", "value": ["D"]}],
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "children.set", "value": ["A"]}],
                "D": [{"type": "children.set", "value": ["B"]}],
            },
        ),
        (
            ("ABC", "CABD"),
            {
                "B": [{"type": "children.set", "value": ["D"]}],
                "C": [{"type": "children.set", "value": ["A"]}],
            },
        ),
        # Nothing in common
        (("A", "B"), {"A": [{"type": "abandon", "value": True}]}),
    ],
)
def test_prepare_transactions(phids, transactions):
    assert reorganise.stack_transactions(*phids) == transactions


@pytest.mark.parametrize(
    "stacks,expected",
    [
        (({"A": None}, [{"rev-id": 1, "rev-phid": "A"}]), (["A"], ["A"])),
        (({"B": None}, [{"rev-id": 1, "rev-phid": "A"}]), (["B"], ["A"])),
        (
            ({"A": "B", "B": None}, [{"rev-id": 1, "rev-phid": "A"}]),
            (["A", "B"], ["A"]),
        ),
        (
            (
                {"A": None},
                [{"rev-id": 1, "rev-phid": "A"}, {"rev-id": 2, "rev-phid": "B"}],
            ),
            (["A"], ["A", "B"]),
        ),
    ],
)
@mock.patch("mozphab.commands.reorganise.stack_transactions")
@mock.patch("mozphab.conduit.ConduitAPI.check")
@mock.patch("mozphab.commands.reorganise.augment_commits_from_body")
@mock.patch("mozphab.conduit.ConduitAPI.get_stack")
@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.ids_to_phids")
@mock.patch("mozphab.conduit.ConduitAPI.phids_to_ids")
@mock.patch("mozphab.conduit.ConduitAPI.edit_revision")
def test_reorg_calling_stack_transactions(
    _edit_revision,
    _phid2id,
    m_id2phid,
    _get_revs,
    m_remote_stack,
    _augment_commits,
    _check,
    m_trans,
    git,
    stacks,
    expected,
):
    class Args:
        yes = True

    phabstack, commits = stacks
    m_remote_stack.return_value = phabstack
    mozphab.conduit.set_repo(git)
    mozphab.conduit.repo.commit_stack = mock.Mock()
    mozphab.conduit.repo.commit_stack.return_value = commits
    m_id2phid.return_value = [c["rev-phid"] for c in commits]
    reorganise.reorganise(git, Args())
    m_trans.assert_called_once_with(*expected)


@mock.patch("mozphab.conduit.ConduitAPI.check")
def test_conduit_broken(m_check):
    m_check.return_value = False
    with pytest.raises(exceptions.Error) as e:
        reorganise.reorganise(None, None)

    assert str(e.value) == "Failed to use Conduit API"


@mock.patch("mozphab.conduit.ConduitAPI.check")
@mock.patch("mozphab.commands.reorganise.augment_commits_from_body")
def test_commits_invalid(_augment, _check, git):
    mozphab.conduit.set_repo(git)
    mozphab.conduit.repo.commit_stack = mock.Mock()
    mozphab.conduit.repo.commit_stack.return_value = []
    with pytest.raises(exceptions.Error) as e:
        reorganise.reorganise(git, None)

    assert str(e.value) == "Failed to find any commits to reorganise."

    mozphab.conduit.repo.commit_stack.return_value = [{"rev-id": None, "name": "A"}]
    with pytest.raises(exceptions.Error) as e:
        reorganise.reorganise(git, None)

    error = str(e.value)
    assert error.startswith("Found new commit in the local stack: A.")


@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.check")
@mock.patch("mozphab.commands.reorganise.augment_commits_from_body")
@mock.patch("mozphab.conduit.ConduitAPI.get_stack")
@mock.patch("mozphab.conduit.ConduitAPI.phids_to_ids")
@mock.patch("mozphab.commands.reorganise.walk_llist")
@mock.patch("mozphab.commands.reorganise.logger")
def test_remote_stack_invalid(
    m_logger, m_walk, m_ids, m_stack, _augment, _check, _call, git
):
    mozphab.conduit.set_repo(git)
    mozphab.conduit.repo.commit_stack = mock.Mock()

    m_stack.return_value = {"A": "B"}
    m_ids.return_value = ["A", "B", "C"]
    mozphab.conduit.repo.commit_stack.return_value = [{"rev-id": 1, "name": "A"}]
    m_walk.side_effect = exceptions.Error("TEST")
    with pytest.raises(exceptions.Error) as e:
        reorganise.reorganise(git, None)

    assert str(e.value) == "TEST"
    m_logger.error.assert_called_once_with(
        "Remote stack is not linear.\nDetected stack:\nA <- B <- C"
    )


@pytest.mark.parametrize(
    "llist,error_text",
    (
        (dict(a="b", c=None), "Multiple heads"),
        (dict(a="b", b=None, c="d", d=None), "Multiple heads"),
        (dict(), "Failed to find head"),
        (dict(a="b", b="c", c="a"), "Failed to find head"),
        (dict(a="b", b="c", c="b"), "Dependency loop"),
    ),
)
def test_walk_llist_errors(llist, error_text):
    walk = reorganise.walk_llist
    with pytest.raises(exceptions.Error) as e:
        walk(llist)

    assert str(e.value).startswith(error_text)


@pytest.mark.parametrize(
    "llist,nodes",
    (
        (dict(a=None), ["a"]),
        (dict(a="b", b=None), ["a", "b"]),
        (dict(a="b"), ["a", "b"]),
        (dict(b="a", a=None), ["b", "a"]),
        (dict(a="b", b="c", c=None), ["a", "b", "c"]),
    ),
)
def test_walk_llist(llist, nodes):
    assert reorganise.walk_llist(llist) == nodes


@pytest.mark.parametrize(
    "params,result",
    (
        ([], {}),
        (["A"], {"A": None}),
        (["A", "B"], {"A": "B", "B": None}),
        (["A", "A"], {"A": None}),
    ),
)
def test_to_llist(params, result):
    assert reorganise.to_llist(params) == result
