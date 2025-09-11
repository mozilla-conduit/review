# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from unittest import mock

import pytest
from callee import Contains

from mozphab import exceptions, mozphab
from mozphab.commands import reorganise
from mozphab.commits import Commit


@pytest.mark.parametrize(
    "phids,transactions,abandoned",
    [
        # No change
        (("A", "A"), {}, set()),
        (("ABC", "ABC"), {}, set()),
        (([], ["A"]), {}, set()),
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
            set(),
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
            set(),
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
            set(),
        ),
        (
            ("ABC", "C"),
            {
                "A": [
                    {"type": "children.remove", "value": ["B"]},
                ],
                "B": [
                    {"type": "children.remove", "value": ["C"]},
                    {"type": "abandon", "value": True},
                ],
            },
            {"A"},
        ),
        # Reorder
        (
            ("AB", "BA"),
            {
                "A": [{"type": "children.remove", "value": ["B"]}],
                "B": [{"type": "children.set", "value": ["A"]}],
            },
            set(),
        ),
        (
            ("ABC", "BC"),
            {
                "A": [
                    {"type": "children.remove", "value": ["B"]},
                    {"type": "abandon", "value": True},
                ]
            },
            set(),
        ),
        (
            ("ABC", "ACB"),
            {
                "A": [{"type": "children.set", "value": ["C"]}],
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "children.set", "value": ["B"]}],
            },
            set(),
        ),
        (
            ("ABC", "BAC"),
            {
                "A": [{"type": "children.set", "value": ["C"]}],
                "B": [{"type": "children.set", "value": ["A"]}],
            },
            set(),
        ),
        (
            ("ABC", "CAB"),
            {
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "children.set", "value": ["A"]}],
            },
            set(),
        ),
        (
            ("ABC", "CBA"),
            {
                "A": [{"type": "children.remove", "value": ["B"]}],
                "B": [{"type": "children.set", "value": ["A"]}],
                "C": [{"type": "children.set", "value": ["B"]}],
            },
            set(),
        ),
        # Insert
        (("ABC", "DABC"), {"D": [{"type": "children.set", "value": ["A"]}]}, set()),
        (
            ("ABC", "ADBC"),
            {
                "A": [{"type": "children.set", "value": ["D"]}],
                "D": [{"type": "children.set", "value": ["B"]}],
            },
            set(),
        ),
        (
            ("ABC", "ABDC"),
            {
                "B": [{"type": "children.set", "value": ["D"]}],
                "D": [{"type": "children.set", "value": ["C"]}],
            },
            set(),
        ),
        (
            ("ABC", "BCAD"),
            {
                "A": [{"type": "children.set", "value": ["D"]}],
                "C": [{"type": "children.set", "value": ["A"]}],
            },
            set(),
        ),
        (([], ["A", "B"]), {"A": [{"type": "children.set", "value": ["B"]}]}, set()),
        # Insert and reorder
        (
            ("ABC", "DCAB"),
            {
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "children.set", "value": ["A"]}],
                "D": [{"type": "children.set", "value": ["C"]}],
            },
            set(),
        ),
        (
            ("ABC", "CDAB"),
            {
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "children.set", "value": ["D"]}],
                "D": [{"type": "children.set", "value": ["A"]}],
            },
            set(),
        ),
        (
            ("ABC", "CADB"),
            {
                "A": [{"type": "children.set", "value": ["D"]}],
                "B": [{"type": "children.remove", "value": ["C"]}],
                "C": [{"type": "children.set", "value": ["A"]}],
                "D": [{"type": "children.set", "value": ["B"]}],
            },
            set(),
        ),
        (
            ("ABC", "CABD"),
            {
                "B": [{"type": "children.set", "value": ["D"]}],
                "C": [{"type": "children.set", "value": ["A"]}],
            },
            set(),
        ),
        # Nothing in common
        (("A", "B"), {"A": [{"type": "abandon", "value": True}]}, set()),
    ],
)
def test_stack_transactions_with_abandon(phids, transactions, abandoned):
    remote_phids, local_phids = phids
    assert (
        reorganise.stack_transactions(remote_phids, local_phids, abandoned)
        == transactions
    )


@pytest.mark.parametrize(
    "phids,transactions,abandoned",
    [
        (
            ("ABC", "A"),
            {
                "A": [{"type": "children.remove", "value": ["B"]}],
                "B": [
                    {"type": "children.remove", "value": ["C"]},
                ],
            },
            set(),
        ),
        (
            ("ABC", "B"),
            {
                "A": [
                    {"type": "children.remove", "value": ["B"]},
                ],
                "B": [{"type": "children.remove", "value": ["C"]}],
            },
            set(),
        ),
        (
            ("ABC", "C"),
            {
                "A": [
                    {"type": "children.remove", "value": ["B"]},
                ],
                "B": [
                    {"type": "children.remove", "value": ["C"]},
                ],
            },
            set(),
        ),
        (
            ("ABC", "C"),
            {
                "A": [
                    {"type": "children.remove", "value": ["B"]},
                ],
                "B": [
                    {"type": "children.remove", "value": ["C"]},
                ],
            },
            {"A"},
        ),
        (
            ("ABC", "BC"),
            {
                "A": [
                    {"type": "children.remove", "value": ["B"]},
                ]
            },
            set(),
        ),
        # Nothing in common
        (("A", "B"), {}, set()),
    ],
)
def test_stack_transactions_no_abandon(phids, transactions, abandoned):
    remote_phids, local_phids = phids
    assert (
        reorganise.stack_transactions(
            remote_phids, local_phids, abandoned, no_abandon=True
        )
        == transactions
    )


@pytest.mark.parametrize(
    "stacks,expected",
    [
        (
            ({"A": None}, [Commit(rev_id=1)], ["A"]),
            (["A"], ["A"], set(), {"no_abandon": False}),
        ),
        (
            ({"B": None}, [Commit(rev_id=1)], ["A"]),
            (["B"], ["A"], set(), {"no_abandon": False}),
        ),
        (
            ({"A": "B", "B": None}, [Commit(rev_id=1)], ["A"]),
            (["A", "B"], ["A"], set(), {"no_abandon": False}),
        ),
        (
            (
                {"A": None},
                [Commit(rev_id=1), Commit(rev_id=2)],
                ["A", "B"],
            ),
            (["A"], ["A", "B"], set(), {"no_abandon": False}),
        ),
        (
            ({"A": None}, [Commit(rev_id=1), Commit(rev_id=2)], ["A", "B"]),
            (["A"], ["A", "B"], set(), {"no_abandon": True}),
        ),
    ],
)
@mock.patch("mozphab.commands.reorganise.stack_transactions")
@mock.patch("mozphab.conduit.ConduitAPI.check")
@mock.patch("mozphab.commands.reorganise.augment_commits_from_body")
@mock.patch("mozphab.commands.reorganise.convert_stackgraph_to_linear")
@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.ids_to_phids")
@mock.patch("mozphab.conduit.ConduitAPI.phids_to_ids")
@mock.patch("mozphab.conduit.ConduitAPI.edit_revision")
def test_reorg_calling_stack_transactions(
    _edit_revision,
    _phid2id,
    m_id2phid,
    _get_revs,
    m_convert_stackgraph_to_linear,
    _augment_commits,
    _check,
    m_trans,
    git,
    stacks,
    expected,
):
    *args, kwargs = expected

    class Args:
        yes = True
        no_abandon = kwargs["no_abandon"]
        force = False
        no_abandon_unconnected = False
        no_hyperlinks = False
        verbose = False

    phabstack, commits, rev_ids = stacks
    m_convert_stackgraph_to_linear.return_value = phabstack
    mozphab.conduit.set_repo(git)
    mozphab.conduit.repo.commit_stack = mock.Mock()
    mozphab.conduit.repo.commit_stack.return_value = commits
    m_id2phid.return_value = rev_ids
    reorganise.reorganise(git, Args())
    m_trans.assert_called_once_with(*args, **kwargs)


@mock.patch("mozphab.conduit.ConduitAPI.check")
def test_conduit_broken(m_check):
    m_check.return_value = False

    class Args:
        force = False
        no_abandon_unconnected = False
        no_hyperlinks = False
        verbose = False

    with pytest.raises(exceptions.Error) as e:
        reorganise.reorganise(None, Args())

    assert str(e.value) == "Failed to use Conduit API"


@mock.patch("mozphab.conduit.ConduitAPI.check")
@mock.patch("mozphab.commands.reorganise.augment_commits_from_body")
def test_commits_invalid(_augment, _check, git):
    class Args:
        force = False
        no_abandon_unconnected = False
        no_hyperlinks = False
        verbose = False

    mozphab.conduit.set_repo(git)
    mozphab.conduit.repo.commit_stack = mock.Mock()
    mozphab.conduit.repo.commit_stack.return_value = []
    with pytest.raises(exceptions.Error) as e:
        reorganise.reorganise(git, Args())

    assert str(e.value) == "Failed to find any commits to reorganise."

    mozphab.conduit.repo.commit_stack.return_value = [Commit(rev_id=None, name="A")]
    with pytest.raises(exceptions.Error) as e:
        reorganise.reorganise(git, Args())

    error = str(e.value)
    assert error.startswith("Found new commit in the local stack: A.")


@mock.patch("mozphab.conduit.ConduitAPI.phids_to_ids")
@mock.patch("mozphab.commands.reorganise.walk_llist")
def test_remote_stack_invalid(m_walk, m_ids, git, caplog: pytest.LogCaptureFixture):
    class Args:
        force = False
        no_abandon_unconnected = False
        no_hyperlinks = False
        verbose = False

    caplog.set_level(logging.ERROR)
    m_ids.return_value = ["A", "B", "C"]
    m_walk.side_effect = exceptions.Error("TEST")

    with (
        mock.patch("mozphab.conduit.ConduitAPI.check"),
        mock.patch("mozphab.git.Git.commit_stack"),
        mock.patch("mozphab.conduit.ConduitAPI.get_revisions"),
        mock.patch("mozphab.commands.reorganise.convert_stackgraph_to_linear"),
        pytest.raises(exceptions.Error, match="TEST"),
    ):
        reorganise.reorganise(git, Args())

    assert caplog.messages == [
        "Remote stack is not linear.\nDetected stack:\nA <- B <- C"
    ]


@pytest.mark.parametrize(
    "llist,error_text",
    (
        ({"a": "b", "c": None}, "Multiple heads"),
        ({"a": "b", "b": None, "c": "d", "d": None}, "Multiple heads"),
        ({}, "Failed to find head"),
        ({"a": "b", "b": "c", "c": "a"}, "Failed to find head"),
        ({"a": "b", "b": "c", "c": "b"}, "Dependency loop"),
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
        ({"a": None}, ["a"]),
        ({"a": "b", "b": None}, ["a", "b"]),
        ({"a": "b"}, ["a", "b"]),
        ({"b": "a", "a": None}, ["b", "a"]),
        ({"a": "b", "b": "c", "c": None}, ["a", "b", "c"]),
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


@pytest.mark.parametrize(
    "stack_graph,phabstack,message",
    (
        ({"1": []}, {}, "Single node should produce no stack."),
        ({"1": [], "2": []}, {}, "Multiple single nodes should produce no stack."),
        (
            {"1": ["2"], "2": []},
            {"2": "1", "1": None},
            "Two elements should be linked together.",
        ),
        (
            {"1": ["2"], "2": [], "3": []},
            {"2": "1", "1": None},
            "Two elements should be linked together and extra single node is ignored.",
        ),
        # Multiple parents.
        (
            {"1": ["2", "3"], "2": [], "3": []},
            {"3": "1", "2": "1", "1": None},
            "Multiple parents should be allowed.",
        ),
        (
            {"1": ["2", "3"], "2": ["5"], "3": ["4"], "4": [], "5": []},
            {"4": "3", "5": "2", "3": "1", "2": "1", "1": None},
            "Multiple parents should be allowed.",
        ),
    ),
)
def test_convert_stackgraph_to_linear_pass(stack_graph, phabstack, message):
    phids_to_ids = {phid: int(phid) for phid in stack_graph}
    assert (
        reorganise.convert_stackgraph_to_linear(stack_graph, phids_to_ids) == phabstack
    ), message


@pytest.mark.parametrize(
    "stack_graph",
    (
        # Multiple children.
        {"1": ["2"], "2": [], "3": ["2"]},
        ({"1": ["2", "3"], "2": [], "3": ["2"]}),
    ),
)
def test_convert_stackgraph_to_linear_fail(stack_graph):
    phids_to_ids = {phid: int(phid) for phid in stack_graph}
    with pytest.raises(exceptions.Error):
        reorganise.convert_stackgraph_to_linear(stack_graph, phids_to_ids)


@pytest.mark.parametrize(
    "remote_phids,local_phids,abandoned,expected_transactions",
    [
        # Simple case: same revision in both remote and local
        (
            ["A"],
            ["A"],
            set(),
            {
                "A": [
                    {"type": "children.set", "value": []},
                ]
            },
        ),
        # Local revision with child
        (
            ["A"],
            ["A", "B"],
            set(),
            {
                "A": [
                    {"type": "children.set", "value": []},
                    {"type": "children.set", "value": ["B"]},
                ]
            },
        ),
        # Remote revision not in local - should be abandoned
        (
            ["A", "B"],
            ["A"],
            set(),
            {
                "A": [
                    {"type": "children.set", "value": []},
                ],
                "B": [
                    {"type": "abandon", "value": True},
                ],
            },
        ),
        # Remote revision not in local but already abandoned - no abandon transaction
        (
            ["A", "B"],
            ["A"],
            {"B"},
            {
                "A": [
                    {"type": "children.set", "value": []},
                ]
            },
        ),
        # Complex case: reorder with abandonment
        (
            ["A", "B", "C"],
            ["C", "A"],
            set(),
            {
                "A": [
                    {"type": "children.set", "value": []},
                ],
                "B": [
                    {"type": "abandon", "value": True},
                ],
                "C": [
                    {"type": "children.set", "value": []},
                    {"type": "children.set", "value": ["A"]},
                ],
            },
        ),
        # Empty remote, non-empty local
        (
            [],
            ["A", "B"],
            set(),
            {
                "A": [
                    {"type": "children.set", "value": ["B"]},
                ]
            },
        ),
        # Empty local, non-empty remote - abandon all
        (
            ["A", "B"],
            [],
            set(),
            {
                "A": [
                    {"type": "abandon", "value": True},
                ],
                "B": [
                    {"type": "abandon", "value": True},
                ],
            },
        ),
    ],
)
def test_force_stack_transactions(
    remote_phids, local_phids, abandoned, expected_transactions
):
    """Test the force_stack_transactions function with various scenarios."""
    result = reorganise.force_stack_transactions(remote_phids, local_phids, abandoned)
    assert result == expected_transactions


@pytest.mark.parametrize(
    "remote_phids,local_phids,abandoned,no_abandon_unconnected,expected_transactions",
    [
        # With no_abandon_unconnected=True: don't abandon remote revisions not in local
        (
            ["A", "B", "C"],
            ["A"],
            set(),
            True,
            {
                "A": [
                    {"type": "children.set", "value": []},
                ],
                "B": [
                    {"type": "children.set", "value": []},
                ],
                "C": [
                    {"type": "children.set", "value": []},
                ],
            },
        ),
        # With no_abandon_unconnected=False: abandon remote revisions not in local (default behavior)
        (
            ["A", "B", "C"],
            ["A"],
            set(),
            False,
            {
                "A": [
                    {"type": "children.set", "value": []},
                ],
                "B": [
                    {"type": "abandon", "value": True},
                ],
                "C": [
                    {"type": "abandon", "value": True},
                ],
            },
        ),
        # With no_abandon_unconnected=True and already abandoned revision: no change
        (
            ["A", "B"],
            ["A"],
            {"B"},
            True,
            {
                "A": [
                    {"type": "children.set", "value": []},
                ],
                "B": [
                    {"type": "children.set", "value": []},
                ],
            },
        ),
        # Complex case with no_abandon_unconnected=True: reorder but don't abandon
        (
            ["A", "B", "C"],
            ["C", "A"],
            set(),
            True,
            {
                "A": [
                    {"type": "children.set", "value": []},
                ],
                "B": [
                    {"type": "children.set", "value": []},
                ],
                "C": [
                    {"type": "children.set", "value": []},
                    {"type": "children.set", "value": ["A"]},
                ],
            },
        ),
    ],
)
def test_force_stack_transactions_no_abandon_unconnected(
    remote_phids, local_phids, abandoned, no_abandon_unconnected, expected_transactions
):
    """Test the force_stack_transactions function with no_abandon_unconnected flag."""
    result = reorganise.force_stack_transactions(
        remote_phids, local_phids, abandoned, no_abandon_unconnected
    )
    assert result == expected_transactions


@pytest.mark.parametrize(
    "stacks,expected_args",
    [
        # Force mode with all local revisions present
        (
            ({"A": None}, [Commit(rev_id=1)], ["A"], True),
            (["A"], ["A"], set()),
        ),
        # Force mode with reordering
        (
            (
                {"A": "B", "B": None},
                [Commit(rev_id=2), Commit(rev_id=1)],
                ["B", "A"],
                True,
            ),
            (["A", "B"], ["B", "A"], set()),
        ),
    ],
)
@mock.patch("mozphab.commands.reorganise.force_stack_transactions")
@mock.patch("mozphab.commands.reorganise.stack_transactions")
@mock.patch("mozphab.conduit.ConduitAPI.check")
@mock.patch("mozphab.commands.reorganise.augment_commits_from_body")
@mock.patch("mozphab.commands.reorganise.convert_stackgraph_to_linear")
@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.ids_to_phids")
@mock.patch("mozphab.conduit.ConduitAPI.phids_to_ids")
@mock.patch("mozphab.conduit.ConduitAPI.apply_transactions_to_revision")
def test_reorg_force_mode(
    _apply_transactions,
    _phid2id,
    m_id2phid,
    _get_revs,
    m_convert_stackgraph_to_linear,
    _augment_commits,
    _check,
    m_stack_trans,
    m_force_trans,
    git,
    stacks,
    expected_args,
):
    """Test that force mode calls force_stack_transactions instead of stack_transactions."""
    phabstack, commits, rev_ids, force_mode = stacks

    class Args:
        yes = True
        no_abandon = False
        force = force_mode
        no_abandon_unconnected = False
        no_hyperlinks = False
        verbose = False

    m_convert_stackgraph_to_linear.return_value = phabstack
    mozphab.conduit.set_repo(git)
    mozphab.conduit.repo.commit_stack = mock.Mock()
    mozphab.conduit.repo.commit_stack.return_value = commits
    m_id2phid.return_value = rev_ids

    # Mock return values for transactions
    m_force_trans.return_value = {"A": [{"type": "children.set", "value": []}]}
    m_stack_trans.return_value = {"A": [{"type": "children.set", "value": []}]}

    reorganise.reorganise(git, Args())

    if force_mode:
        m_force_trans.assert_called_once_with(
            *expected_args, no_abandon_unconnected=False
        )
        m_stack_trans.assert_not_called()
    else:
        m_stack_trans.assert_called_once()
        m_force_trans.assert_not_called()


@mock.patch("mozphab.conduit.ConduitAPI.check")
@mock.patch("mozphab.commands.reorganise.augment_commits_from_body")
def test_force_mode_requires_all_local_revisions_on_phabricator(_augment, _check, git):
    """Test that force mode requires all local revisions to be present on Phabricator."""

    class Args:
        force = True
        no_abandon_unconnected = False
        no_hyperlinks = False
        verbose = False

    mozphab.conduit.set_repo(git)
    mozphab.conduit.repo.commit_stack = mock.Mock()
    mozphab.conduit.repo.commit_stack.return_value = [Commit(rev_id=None, name="A")]

    with pytest.raises(exceptions.Error) as e:
        reorganise.reorganise(git, Args())

    assert (
        str(e.value)
        == "Force mode requires all local revisions to be present on Phabricator."
    )


@mock.patch("mozphab.conduit.ConduitAPI.check")
def test_no_abandon_unconnected_requires_force(_check):
    """Test that --no-abandon-unconnected requires --force flag."""

    class Args:
        force = False
        no_abandon_unconnected = True
        no_hyperlinks = False
        verbose = False

    _check.return_value = True

    with pytest.raises(exceptions.Error) as e:
        reorganise.reorganise(None, Args())

    assert str(e.value) == "--no-abandon-unconnected can only be used with --force"


@mock.patch("mozphab.commands.reorganise.walk_llist")
def test_force_mode_ignores_remote_stack_errors(
    m_walk, git, caplog: pytest.LogCaptureFixture
):
    """Test that force mode ignores remote stack structure issues."""

    class Args:
        force = True
        yes = True
        no_abandon_unconnected = False
        no_hyperlinks = False
        verbose = False

    caplog.set_level(logging.WARNING, logger="moz-phab")
    m_walk.side_effect = exceptions.Error("Remote stack is not linear")

    # Mock the conduit methods that would be called
    with (
        mock.patch("mozphab.conduit.ConduitAPI.check"),
        mock.patch("mozphab.git.Git.commit_stack"),
        mock.patch("mozphab.conduit.ConduitAPI.get_revisions"),
        mock.patch("mozphab.commands.reorganise.convert_stackgraph_to_linear"),
    ):
        # This should not raise an error in force mode
        reorganise.reorganise(git, Args())

    # Should log a warning instead of an error
    # Check that the specific warning was called
    for record in caplog.records:
        assert record.levelno == logging.WARNING
    assert (
        Contains("Remote stack is not linear, but continuing in force mode")
        in caplog.messages
    )


class TestReorganiseParser:
    """Test the argument parser setup for reorganise command."""

    def test_add_parser(self):
        """Test that the parser is configured correctly for the --force flag."""
        import argparse

        # Create a parent parser to add our command to
        parent_parser = argparse.ArgumentParser()
        subparsers = parent_parser.add_subparsers()

        # Add our reorganise parser
        reorganise.add_parser(subparsers)

        # Test parsing without --force flag
        args = parent_parser.parse_args(["reorg"])
        assert args.force is False
        assert args.yes is False
        assert args.no_abandon is False

        # Test parsing with --force flag
        args = parent_parser.parse_args(["reorg", "--force"])
        assert args.force is True
        assert args.yes is False
        assert args.no_abandon is False

        # Test parsing with multiple flags
        args = parent_parser.parse_args(["reorg", "--force", "--yes", "--no-abandon"])
        assert args.force is True
        assert args.yes is True
        assert args.no_abandon is True

        # Test parsing with revision arguments
        args = parent_parser.parse_args(["reorg", "--force", "abc123", "def456"])
        assert args.force is True
        assert args.start_rev == "abc123"
        assert args.end_rev == "def456"

        # Test parsing with upstream argument
        args = parent_parser.parse_args(["reorg", "--force", "--upstream", "main"])
        assert args.force is True
        assert args.upstream == ["main"]

        # Test parsing with --no-abandon-unconnected flag
        args = parent_parser.parse_args(
            ["reorg", "--force", "--no-abandon-unconnected"]
        )
        assert args.force is True
        assert args.no_abandon_unconnected is True

        # Test parsing without --no-abandon-unconnected flag
        args = parent_parser.parse_args(["reorg", "--force"])
        assert args.force is True
        assert args.no_abandon_unconnected is False
