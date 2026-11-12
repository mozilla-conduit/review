# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import copy
import unittest
import uuid
from typing import Any
from unittest import mock

import pytest
from callee import Contains

from mozphab import exceptions, helpers, mozphab, repository
from mozphab.commands import submit
from mozphab.commits import Commit, MergeConflictDiffs
from mozphab.conduit import ConduitAPIError
from mozphab.config import Config
from mozphab.diff import Diff
from mozphab.models import CreateRawDiffResult, Transaction, TransactionType
from mozphab.user import UserData

from .conftest import search_remote_diff, search_revision


def reviewers_dict(reviewers=None):
    return {
        "request": reviewers[0] if reviewers else [],
        "granted": reviewers[1] if reviewers else [],
    }


def commit(
    bug_id=None,
    reviewers=None,
    rev_id=None,
    node=None,
    title="",
    body="",
    wip=False,
    bug_id_orig=None,
    submit=True,
):
    node = node or f"{uuid.uuid4().hex}12345678"
    return Commit(
        name=helpers.short_node(node),
        title=title,
        title_preview=title,
        bug_id=bug_id,
        bug_id_orig=bug_id_orig,
        reviewers=reviewers_dict(reviewers),
        body=body,
        rev_id=rev_id,
        node=node,
        orig_node=node,
        submit=submit,
        wip=wip,
    )


# noinspection PyPep8Naming,PyBroadException
class Commits(unittest.TestCase):
    def _assertNoError(self, callableObj, *args, **kwargs):
        try:
            callableObj(*args, **kwargs)
        except exceptions.Error as e:
            self.fail("%s raised" % e)

    def _assertError(self, callableObj, expected, *args, **kwargs):
        with self.assertRaisesRegex(exceptions.Error, expected):
            callableObj(*args, **kwargs)

    @mock.patch("mozphab.conduit.ConduitAPI.get_groups")
    @mock.patch("mozphab.conduit.ConduitAPI.get_users")
    @mock.patch("mozphab.conduit.ConduitAPI.check_for_invalid_reviewers")
    @mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
    def test_commit_stack_validation_errors(
        self, m_revs, check_reviewers, m_get_users, m_get_groups
    ):
        check_reviewers.return_value = []
        m_revs.return_value = []
        m_get_users.return_value = []
        m_get_groups.return_value = []

        _, errors = submit.validate_commit_stack(
            [commit("1", (["r"], []))], submit.SubmitOptions()
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [
                commit("1", (["r1"], [])),
                commit("2", (["r1"], [])),
                commit("3", (["r1", "r2"], [])),
            ],
            submit.SubmitOptions(),
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [commit("1", None)], submit.SubmitOptions()
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [commit("1", (["r"], [])), commit("1", None)], submit.SubmitOptions()
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [commit(None, (["r"], []))], submit.SubmitOptions()
        )
        self.assertEqual(list(errors.values()), [[Contains("Missing bug ID")]])

        _, errors = submit.validate_commit_stack(
            [commit(None, (["r"], []))], submit.SubmitOptions(no_bug=True)
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [commit("", (["r"], []))], submit.SubmitOptions()
        )
        self.assertEqual(list(errors.values()), [[Contains("Missing bug ID")]])

        _, errors = submit.validate_commit_stack(
            [commit("1", (["r"], [])), commit("", (["r"], []))], submit.SubmitOptions()
        )
        self.assertEqual(list(errors.values()), [[Contains("Missing bug ID")]])

        _, errors = submit.validate_commit_stack(
            [commit("1", (["r"], [])), commit("", (["r"], []))],
            submit.SubmitOptions(no_bug=True),
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [commit("1", (["r"], []), body="Summary: blah\nReviewers: r")],
            submit.SubmitOptions(),
        )
        self.assertEqual(list(errors.values()), [[Contains("Contains arc fields")]])

        _, errors = submit.validate_commit_stack(
            [commit(bug_id="1", rev_id=1)], submit.SubmitOptions()
        )
        self.assertEqual(
            list(errors.values()),
            [[Contains("didn't return a query result for revision D1")]],
        )

    @mock.patch("mozphab.conduit.ConduitAPI.get_groups")
    @mock.patch("mozphab.conduit.ConduitAPI.get_users")
    @mock.patch("mozphab.conduit.ConduitAPI.check_for_invalid_reviewers")
    @mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
    @mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
    @mock.patch("mozphab.conduit.ConduitAPI.whoami")
    def test_invalid_reviewers_fails_the_stack_validation_check(
        self, m_whoami, m_diffs, m_revs, check_reviewers, m_get_users, m_get_groups
    ):
        m_get_users.return_value = []
        m_get_groups.return_value = []

        def fail_gonzo(reviewers):
            # Replace the check_for_invalid_reviewers() function with something that
            # fails for certain reviewers.
            if "gonzo" in reviewers["request"]:
                return [{"name": "gonzo"}]
            elif "goober" in reviewers["request"]:
                return [{"name": "goober", "until": "string"}]
            elif "goofus" in reviewers["request"]:
                return [{"name": "goofus", "disabled": True}]
            else:
                return []

        check_reviewers.side_effect = fail_gonzo
        m_revs.return_value = []
        m_diffs.return_value = {"PHID-DIFF-1": search_remote_diff()}
        m_whoami.return_value = {"phid": "PHID-USER-1"}

        _, errors = submit.validate_commit_stack(
            # Build a stack with an invalid reviewer in the middle.
            [
                commit("1", (["alice"], [])),
                commit("2", (["bob", "gonzo"], [])),
                commit("3", (["charlie"], [])),
            ],
            submit.SubmitOptions(),
        )
        self.assertEqual(
            list(errors.values()), [[Contains("gonzo isn't a valid reviewer")]]
        )

        _, errors = submit.validate_commit_stack(
            # Build a stack with an unavailable reviewer in the middle.
            [
                commit("1", (["alice"], [])),
                commit("2", (["bob", "goober"], [])),
                commit("3", (["charlie"], [])),
            ],
            submit.SubmitOptions(),
        )
        self.assertEqual(
            list(errors.values()), [[Contains("goober isn't available until string")]]
        )

        warnings, errors = submit.validate_commit_stack(
            [
                commit("1", (["alice"], [])),
                commit("2", (["bob", "goober"], [])),
                commit("3", (["charlie"], [])),
            ],
            submit.SubmitOptions(force=True),
        )
        self.assertEqual(
            list(warnings.values()), [[Contains("goober isn't available until string")]]
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [commit("1", (["goofus"], []))], submit.SubmitOptions()
        )
        self.assertEqual(list(errors.values()), [[Contains("goofus is disabled")]])

        # An unchanged disabled reviewer should not block unrelated updates.
        m_revs.return_value = [search_revision(reviewers=["PHID-USER-2"])]
        m_diffs.return_value = {"PHID-DIFF-1": search_remote_diff(message="1 r?goofus")}
        _, errors = submit.validate_commit_stack(
            [commit("1", (["goofus"], []), rev_id=1)], submit.SubmitOptions()
        )
        self.assertEqual(errors, {})

        # Only genuinely new reviewers are validated in a mixed reviewer list.
        check_reviewers.reset_mock()
        _, errors = submit.validate_commit_stack(
            [commit("1", (["goofus", "alice"], []), rev_id=1)], submit.SubmitOptions()
        )
        self.assertEqual(errors, {})
        check_reviewers.assert_called_once_with({"request": ["alice"], "granted": []})

        # A newly introduced disabled reviewer should still fail validation.
        m_diffs.return_value = {"PHID-DIFF-1": search_remote_diff(message="1 r?alice")}
        _, errors = submit.validate_commit_stack(
            [commit("1", (["goofus"], []), rev_id=1)], submit.SubmitOptions()
        )
        self.assertEqual(list(errors.values()), [[Contains("goofus is disabled")]])

        # An unresolved new reviewer on an otherwise unchanged revision must
        # still reach validation instead of being treated as no change.
        m_diffs.return_value = {
            "PHID-DIFF-1": search_remote_diff(node="same-node", message="1 r?alice")
        }
        _, errors = submit.validate_commit_stack(
            [commit("1", (["gonzo"], []), rev_id=1, node="same-node")],
            submit.SubmitOptions(),
        )
        self.assertEqual(
            list(errors.values()), [[Contains("gonzo isn't a valid reviewer")]]
        )

    @mock.patch("mozphab.conduit.ConduitAPI.get_groups")
    @mock.patch("mozphab.conduit.ConduitAPI.get_users")
    @mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
    @mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
    @mock.patch("mozphab.conduit.ConduitAPI.whoami")
    @mock.patch("mozphab.conduit.ConduitAPI.check_for_invalid_reviewers")
    def test_validate_duplicate_revision(
        self, check_reviewers, m_whoami, m_diffs, m_revs, m_get_users, m_get_groups
    ):
        check_reviewers.return_value = []
        m_get_users.return_value = []
        m_get_groups.return_value = []
        m_revs.return_value = [
            search_revision(rev=1),
            search_revision(rev=2),
            search_revision(rev=3),
        ]
        m_diffs.return_value = {"PHID-DIFF-1": search_remote_diff()}
        m_whoami.return_value = {"phid": "PHID-USER-1"}

        _, errors = submit.validate_commit_stack(
            [
                commit("1", (["r"], []), node="a"),
                commit("2", (["r"], []), node="b"),
                commit("3", (["r"], []), node="c"),
            ],
            submit.SubmitOptions(),
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [
                commit("1", (["r"], []), rev_id=1, node="a"),
                commit("2", (["r"], []), rev_id=2, node="b"),
                commit("3", (["r"], []), rev_id=3, node="c"),
            ],
            submit.SubmitOptions(),
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [
                commit("1", (["r"], []), rev_id=1, node="a"),
                commit("2", (["r"], []), rev_id=2, node="b"),
                commit("3", (["r"], []), rev_id=1, node="c"),
            ],
            submit.SubmitOptions(),
        )
        self.assertEqual(
            errors, {"c": [Contains("commit a refers to the same one D1")]}
        )

        _, errors = submit.validate_commit_stack(
            [
                commit("1", (["r"], []), rev_id=1, node="a"),
                commit("2", (["r"], []), rev_id=2, node="b"),
                commit("3", (["r"], []), rev_id=1, node="c"),
                commit("4", (["r"], []), rev_id=2, node="d"),
            ],
            submit.SubmitOptions(),
        )
        self.assertEqual(
            errors,
            {
                "c": [Contains("commit a refers to the same one D1")],
                "d": [Contains("commit b refers to the same one D2")],
            },
        )

    @mock.patch("mozphab.conduit.ConduitAPI.get_groups")
    @mock.patch("mozphab.conduit.ConduitAPI.get_users")
    @mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
    @mock.patch("mozphab.conduit.ConduitAPI.check_for_invalid_reviewers")
    def test_reviewer_lookups_batched_across_commits(
        self, m_check_reviewers, m_revs, m_get_users, m_get_groups
    ):
        """All commit reviewers are preloaded with a single get_users /
        get_groups call, rather than one per commit."""
        m_check_reviewers.return_value = []
        m_revs.return_value = []
        m_get_users.return_value = []
        m_get_groups.return_value = []

        submit.validate_commit_stack(
            [
                commit("1", (["alice", "bob"], [])),
                commit("2", (["bob", "#release-managers"], [])),
                commit("3", (["charlie", "#release-managers!"], [])),
            ],
            submit.SubmitOptions(),
        )

        # Single batch call for users; deduped across commits.
        self.assertEqual(m_get_users.call_count, 1)
        self.assertEqual(
            sorted(m_get_users.call_args.args[0]), ["alice", "bob", "charlie"]
        )
        # Single batch call for groups; deduped across commits.
        self.assertEqual(m_get_groups.call_count, 1)
        self.assertEqual(sorted(m_get_groups.call_args.args[0]), ["#release-managers"])

    def test_commit_preview(self):
        build = helpers.build_commit_title

        self.assertEqual(
            "Bug 1, blah, r=turnip",
            build(commit("1", ([], ["turnip"]), title="bug 1, blah, r=turnip")),
        )
        self.assertEqual(
            "blah (Bug 1) r=turnip",
            build(commit("1", ([], ["turnip"]), title="blah (bug 1) r=turnip")),
        )
        self.assertEqual(
            "Bug 1 - blah r?turnip",
            build(commit("1", (["turnip"], []), title="blah r?turnip")),
        )

        self.assertEqual(
            "blah r=turnip", build(commit("", ([], ["turnip"]), title="blah r=turnip"))
        )
        self.assertEqual(
            "Bug 1 - blah", build(commit("1", None, title="Bug 1 - blah r?turnip"))
        )
        self.assertEqual(
            "Bug 1 - blah", build(commit("1", None, title="Bug 1 - blah r=turnip"))
        )
        self.assertEqual(
            "Bug 1 - helper_bug2.html",
            build(commit("1", None, title="Bug 1 - helper_bug2.html")),
        )

    @mock.patch("mozphab.helpers.build_commit_title")
    def test_update_commit_title_previews(self, m_build_commit_title):
        m_build_commit_title.side_effect = lambda x: x.title + " preview"
        commits = [Commit(title="a"), Commit(title="b")]
        helpers.update_commit_title_previews(commits)
        self.assertEqual(
            [
                Commit(title="a", title_preview="a preview"),
                Commit(title="b", title_preview="b preview"),
            ],
            commits,
        )

    def test_replace_request_reviewers(self):
        replace = helpers.replace_reviewers
        self.assertEqual("", replace("", reviewers_dict()))
        self.assertEqual("Title", replace("Title", reviewers_dict()))
        self.assertEqual(
            "Title\n\nr?one r=two", replace("Title\n\nr?one r=two", reviewers_dict())
        )
        self.assertEqual("r?one", replace("", reviewers_dict([["one"], []])))
        self.assertEqual("r?one", replace("r?one", reviewers_dict([["one"], []])))
        self.assertEqual("r?one,two", replace("", reviewers_dict([["one", "two"], []])))
        self.assertEqual(
            "Some Title r?one,two",
            replace("Some Title", reviewers_dict([["one", "two"], []])),
        )
        self.assertEqual(
            "Title r?one\n\nDescr\niption",
            replace("Title\n\nDescr\niption", reviewers_dict([["one"], []])),
        )
        self.assertEqual(
            "Title r?one,two\n\nr?two",
            replace("Title\n\nr?two", reviewers_dict([["one", "two"], []])),
        )

        self.assertEqual("Title", replace("Title r?one", reviewers_dict()))
        self.assertEqual("Title", replace("Title r?one,two", reviewers_dict()))
        self.assertEqual("Title", replace("Title r?one r?two", reviewers_dict()))
        self.assertEqual(
            "Title r?one!", replace("Title r?one!", reviewers_dict([["one!"], []]))
        )
        self.assertEqual(
            "Title r?one", replace("Title r?one!", reviewers_dict([["one"], []]))
        )
        self.assertEqual(
            "Title r?one!", replace("Title r?one", reviewers_dict([["one!"], []]))
        )
        self.assertEqual(
            "Title r?one", replace("Title r?one", reviewers_dict([["one"], []]))
        )
        self.assertEqual(
            "Title r?one one", replace("Title r? one", reviewers_dict([["one"], []]))
        )
        self.assertEqual(
            "Title r?one,two",
            replace("Title r?one,two", reviewers_dict([["one", "two"], []])),
        )
        self.assertEqual(
            "Title r?one,two",
            replace("Title r?two", reviewers_dict([["one", "two"], []])),
        )
        self.assertEqual(
            "Title r?one,two",
            replace("Title r?one r?two", reviewers_dict([["one", "two"], []])),
        )
        self.assertEqual(
            "Title r?one", replace("Title r=one", reviewers_dict([["one"], []]))
        )
        self.assertEqual(
            "Title r?one", replace("Title r=one,two", reviewers_dict([["one"], []]))
        )
        self.assertEqual(
            "Title r?one,two",
            replace("Title r=one,two", reviewers_dict([["one", "two"], []])),
        )
        self.assertEqual(
            "Title r?one,two",
            replace("Title r=one r=two", reviewers_dict([["one", "two"], []])),
        )
        self.assertEqual(
            "Title r?one,two",
            replace("Title r?one r=two", reviewers_dict([["one", "two"], []])),
        )

    def test_replace_granted_reviewers(self):
        replace = helpers.replace_reviewers
        self.assertEqual("r=one", replace("", reviewers_dict([[], ["one"]])))
        self.assertEqual("r=one", replace("r=one", reviewers_dict([[], ["one"]])))
        self.assertEqual("r=one,two", replace("", reviewers_dict([[], ["one", "two"]])))
        self.assertEqual(
            "Some Title r=one,two",
            replace("Some Title", reviewers_dict([[], ["one", "two"]])),
        )
        self.assertEqual(
            "Title r=one\n\nDescr\niption",
            replace("Title\n\nDescr\niption", reviewers_dict([[], ["one"]])),
        )
        self.assertEqual(
            "Title r=one,two\n\nr?two",
            replace("Title\n\nr?two", reviewers_dict([[], ["one", "two"]])),
        )
        self.assertEqual("Title", replace("Title r=one", reviewers_dict()))
        self.assertEqual("Title", replace("Title r=one,two", reviewers_dict()))
        self.assertEqual("Title", replace("Title r=one r=two", reviewers_dict()))
        self.assertEqual(
            "Title r=one", replace("Title r=one", reviewers_dict([[], ["one"]]))
        )
        self.assertEqual(
            "Title r=one!", replace("Title r=one!", reviewers_dict([[], ["one!"]]))
        )
        self.assertEqual(
            "Title r=one", replace("Title r=one!", reviewers_dict([[], ["one"]]))
        )
        self.assertEqual(
            "Title r=one!", replace("Title r=one", reviewers_dict([[], ["one!"]]))
        )
        self.assertEqual(
            "Title r=one one", replace("Title r= one", reviewers_dict([[], ["one"]]))
        )
        self.assertEqual(
            "Title r=one,two",
            replace("Title r=one,two", reviewers_dict([[], ["one", "two"]])),
        )
        self.assertEqual(
            "Title r=one,two",
            replace("Title r=two", reviewers_dict([[], ["one", "two"]])),
        )
        self.assertEqual(
            "Title r=one,two",
            replace("Title r=one r=two", reviewers_dict([[], ["one", "two"]])),
        )
        self.assertEqual(
            "Title r=one", replace("Title r?one", reviewers_dict([[], ["one"]]))
        )
        self.assertEqual(
            "Title r=one", replace("Title r?one,two", reviewers_dict([[], ["one"]]))
        )
        self.assertEqual(
            "Title r=one,two",
            replace("Title r?one,two", reviewers_dict([[], ["one", "two"]])),
        )
        self.assertEqual(
            "Title r=one,two",
            replace("Title r?one r?two", reviewers_dict([[], ["one", "two"]])),
        )
        self.assertEqual(
            "Title r=one,two",
            replace("Title r=one r?two", reviewers_dict([[], ["one", "two"]])),
        )

    def test_replace_mixed_reviewers(self):
        replace = helpers.replace_reviewers
        self.assertEqual(
            "Title r?one r=two", replace("Title", reviewers_dict([["one"], ["two"]]))
        )
        self.assertEqual(
            "Title r?one r=two",
            replace("Title r=one r?two", reviewers_dict([["one"], ["two"]])),
        )
        self.assertEqual(
            "Title r?one r=two",
            replace("Title r?two r=one", reviewers_dict([["one"], ["two"]])),
        )
        self.assertEqual(
            "Title r?one,two r=three",
            replace("Title r=one r?two", reviewers_dict([["one", "two"], ["three"]])),
        )
        self.assertEqual(
            "Title r?one r=two,three",
            replace("Title r=one r?two", reviewers_dict([["one"], ["two", "three"]])),
        )

    @unittest.skip("These tests should pass we should fix the function")
    def test_badly_replaced_reviewers(self):
        replace = helpers.replace_reviewers
        # r?two
        self.assertEqual("r?one", replace("r?two", reviewers_dict([["one"], []])))
        # r=one
        self.assertEqual("r?one", replace("r=one", reviewers_dict([["one"], []])))
        # r? one
        self.assertEqual("r?one one", replace("r? one", reviewers_dict([["one"], []])))
        # r?one,
        self.assertEqual("r?one", replace("r?one,", reviewers_dict([["one"], []])))
        # r?one
        self.assertEqual(
            "r?one,two", replace("r?one", reviewers_dict([["one", "two"], []]))
        )
        # r?one,two
        self.assertEqual("r?one", replace("r?one,two", reviewers_dict([["one"], []])))
        # Title r?one,two,,two
        self.assertEqual(
            "Title r?one,two",
            replace("Title r?one,,two", reviewers_dict([["one"], ["two"], []])),
        )
        # r?one
        self.assertEqual("", replace("r?one", reviewers_dict()))
        # r?one,two
        self.assertEqual("", replace("r?one,two", reviewers_dict()))
        # r?one
        self.assertEqual("", replace("r?one r?two", reviewers_dict()))
        # r?two
        self.assertEqual(
            "r?one,two", replace("r?two", reviewers_dict([["one", "two"], []]))
        )
        # r?one r?one,two
        self.assertEqual(
            "r?one,two", replace("r?one r?two", reviewers_dict([["one", "two"], []]))
        )
        # r=one
        self.assertEqual("r?one", replace("r=one", reviewers_dict([["one"], []])))
        # r=one,two
        self.assertEqual("r?one", replace("r=one,two", reviewers_dict([["one"], []])))
        # r=one,two
        self.assertEqual(
            "r?one,two", replace("r=one,two", reviewers_dict([["one", "two"], []]))
        )
        # r=one, r?one,two
        self.assertEqual(
            "r?one,two", replace("r=one r=two", reviewers_dict([["one", "two"], []]))
        )
        # r? one, r?one,two
        self.assertEqual(
            "r?one,two", replace("r?one r=two", reviewers_dict([["one", "two"], []]))
        )

        # Granted
        # r=two
        self.assertEqual("r=one", replace("r=two", reviewers_dict([[], ["one"]])))
        # r?one
        self.assertEqual("r=one", replace("r?one", reviewers_dict([[], ["one"]])))
        # r?one
        self.assertEqual(
            "r=one,two", replace("r=one", reviewers_dict([[], ["one", "two"]]))
        )
        # r?one,two
        self.assertEqual("r?one", replace("r?one,two", reviewers_dict([["one"], []])))

    def test_show_commit_stack(self):
        submit.conduit.set_repo(repository.Repository("", "", "http://phab"))

        with self.assertLogs() as logging_watcher:
            submit.log_commit_stack_with_messages([commit(node="aaa000", title="A")])
        self.assertEqual(logging_watcher.output, [Contains("(New) aaa000 A")])

        with self.assertLogs() as logging_watcher:
            submit.log_commit_stack_with_messages(
                [commit(rev_id=12, node="aaa000", title="A")]
            )
        self.assertEqual(logging_watcher.output, [Contains("(D12) aaa000 A")])

        with self.assertLogs() as logging_watcher:
            submit.log_commit_stack_with_messages(
                [
                    commit(node="aaa000", title="A"),
                    commit(node="bbb000", title="B"),
                ]
            )
        self.assertEqual(
            logging_watcher.output,
            [Contains("(New) bbb000 B"), Contains("(New) aaa000 A")],
        )

        with self.assertLogs() as logging_watcher:
            submit.show_commit_stack([commit(rev_id=123, node="aaa000", title="A")])
        self.assertEqual(
            logging_watcher.output,
            [Contains("(D123) aaa000 A"), Contains("-> http://phab/D123")],
        )

        # Only show commits that were submitted.
        with self.assertLogs() as logging_watcher:
            submit.show_commit_stack(
                [commit(rev_id=1, submit=False), commit(rev_id=2), commit(rev_id=3)]
            )
        self.assertEqual(
            logging_watcher.output,
            [
                Contains("(D3)"),
                Contains("-> http://phab/D3"),
                Contains("(D2)"),
                Contains("-> http://phab/D2"),
            ],
        )

    @mock.patch("mozphab.conduit.ConduitAPI.get_pending_reviews")
    def test_show_review_queue_reminder(self, m_get_pending_reviews):
        submit.conduit.set_repo(repository.Repository("", "", "http://phab"))
        m_get_pending_reviews.return_value = (3, False)

        def reminder_text(
            commits: list[Commit],
            is_employee: bool = True,
            frequency: int = 3600,
            include_groups: bool = True,
        ):
            """Return what the reminder logged, empty if it stayed quiet."""
            # A fresh `UserData` starts outside the "reminded recently" window.
            user_data = UserData()
            user_data.is_employee = is_employee
            options = submit.SubmitOptions(
                review_queue_reminder_frequency=frequency,
                review_queue_reminder_include_groups=include_groups,
            )
            with (
                mock.patch.object(submit, "logger") as m_logger,
                mock.patch.object(user_data, "save_user_info"),
            ):
                submit.show_review_queue_reminder(commits, options, user_data)

            if not m_logger.warning.called:
                return ""
            args = m_logger.warning.call_args.args
            return args[0] % args[1:]

        def is_reminded(commits, **kwargs):
            return bool(reminder_text(commits, **kwargs))

        self.assertIn(
            "3 revisions waiting on your review",
            reminder_text([commit(rev_id=1)]),
        )
        m_get_pending_reviews.assert_called_with(include_groups=True)
        self.assertIn(
            "3 revisions waiting on your review",
            reminder_text([commit(rev_id=1)], include_groups=False),
        )
        m_get_pending_reviews.assert_called_with(include_groups=False)
        self.assertIn("http://phab/differential/", reminder_text([commit(rev_id=1)]))

        # A capped count is shown as "100+", and one review is singular.
        m_get_pending_reviews.return_value = (100, True)
        self.assertIn("100+ revisions waiting", reminder_text([commit(rev_id=1)]))

        m_get_pending_reviews.return_value = (1, False)
        self.assertIn("1 revision waiting", reminder_text([commit(rev_id=1)]))

        # An empty review queue is not worth mentioning.
        m_get_pending_reviews.return_value = (0, False)
        self.assertFalse(is_reminded([commit(rev_id=1)]))

        # A failure to check the queue must not be shown after a successful
        # submission.
        m_get_pending_reviews.side_effect = ConduitAPIError("nope")
        self.assertFalse(is_reminded([commit(rev_id=1)]))
        m_get_pending_reviews.side_effect = None

        m_get_pending_reviews.return_value = (3, False)

        # Work In Progress revisions don't ask for anyone's time.
        self.assertFalse(is_reminded([commit(rev_id=1, wip=True)]))
        self.assertTrue(
            is_reminded([commit(rev_id=1, wip=True), commit(rev_id=2)]),
            "A stack with a single non-WIP commit should still remind.",
        )

        # Commits that aren't being submitted don't count.
        self.assertFalse(is_reminded([commit(rev_id=1, submit=False)]))

        # Contributors and opted-out users are left alone.
        self.assertFalse(is_reminded([commit(rev_id=1)], is_employee=False))
        self.assertFalse(is_reminded([commit(rev_id=1)], is_employee=None))
        self.assertFalse(is_reminded([commit(rev_id=1)], frequency=0))

        # The review queue isn't queried when the reminder wouldn't be shown.
        m_get_pending_reviews.reset_mock()
        is_reminded([commit(rev_id=1)], is_employee=False)
        is_reminded([commit(rev_id=1, wip=True)])
        m_get_pending_reviews.assert_not_called()

    @mock.patch("mozphab.commands.submit.time.time")
    @mock.patch("mozphab.conduit.ConduitAPI.get_pending_reviews")
    def test_review_queue_reminder_frequency(self, m_get_pending_reviews, m_time):
        submit.conduit.set_repo(repository.Repository("", "", "http://phab"))
        m_get_pending_reviews.return_value = (3, False)
        commits = [commit(rev_id=1)]
        now = 1000000
        m_time.return_value = now

        frequency = 3600

        user_data = UserData()
        user_data.is_employee = True

        options = submit.SubmitOptions(review_queue_reminder_frequency=frequency)

        with (
            mock.patch.object(submit, "logger") as m_logger,
            mock.patch.object(user_data, "save_user_info") as m_save,
        ):
            # The first submission reminds, and records when it did.
            submit.show_review_queue_reminder(commits, options, user_data)
            self.assertTrue(m_logger.warning.called)
            m_save.assert_called_once_with(review_queue_last_reminder=now)
            user_data.review_queue_last_reminder = now

            # Submitting again within the hour stays quiet, without even
            # checking the queue.
            m_logger.reset_mock()
            m_get_pending_reviews.reset_mock()
            m_time.return_value = now + frequency - 1
            submit.show_review_queue_reminder(commits, options, user_data)
            self.assertFalse(m_logger.warning.called)
            m_get_pending_reviews.assert_not_called()

            # Once the window has passed, remind again.
            m_time.return_value = now + frequency
            submit.show_review_queue_reminder(commits, options, user_data)
            self.assertTrue(m_logger.warning.called)
            self.assertEqual(
                m_save.call_args.kwargs,
                {"review_queue_last_reminder": m_time.return_value},
            )
            user_data.review_queue_last_reminder = m_time.return_value

            # A stamp in the future, e.g. after the clock moved backwards,
            # doesn't silence the reminder until the clock catches up.
            m_logger.reset_mock()
            user_data.review_queue_last_reminder = m_time.return_value + 10000
            submit.show_review_queue_reminder(commits, options, user_data)
            self.assertTrue(m_logger.warning.called)

            # Finding an empty queue also uses up the window, so that people
            # with nothing to review aren't re-queried on every submit.
            m_logger.reset_mock()
            m_save.reset_mock()
            m_get_pending_reviews.return_value = (0, False)
            user_data.review_queue_last_reminder = 0
            submit.show_review_queue_reminder(commits, options, user_data)
            self.assertFalse(m_logger.warning.called)
            m_save.assert_called_once_with(
                review_queue_last_reminder=m_time.return_value
            )

    @mock.patch("mozphab.conduit.ConduitAPI.get_groups")
    @mock.patch("mozphab.conduit.ConduitAPI.get_users")
    @mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
    @mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
    @mock.patch("mozphab.conduit.ConduitAPI.whoami")
    @mock.patch("mozphab.conduit.ConduitAPI.check_for_invalid_reviewers")
    def test_validate_commit_stack(
        self,
        m_check_reviewers,
        m_whoami,
        m_get_diffs,
        m_get_revisions,
        m_get_users,
        m_get_groups,
    ):
        def _users(reviewers):
            phids = {
                "alice": "PHID-USER-2",
                "bob": "PHID-USER-3",
            }
            return [
                {"phid": phids[reviewer.rstrip("!")], "userName": reviewer.rstrip("!")}
                for reviewer in reviewers
            ]

        m_get_users.side_effect = _users
        m_get_groups.return_value = []

        def _commit(
            node=None,
            title="A",
            rev=None,
            bug="1",
            bug_orig=None,
            granted=None,
            request=None,
            wip=False,
        ):
            granted = granted or []
            request = request or []
            return commit(
                node=node,
                title=title,
                rev_id=rev,
                bug_id_orig=bug_orig,
                bug_id=bug,
                reviewers=(request, granted),
                wip=wip,
            )

        m_whoami.return_value = {"phid": "PHID-USER-1"}
        m_get_revisions.return_value = [search_revision()]
        m_get_diffs.return_value = {"PHID-DIFF-1": search_remote_diff()}
        m_check_reviewers.return_value = []

        warnings, errors = submit.validate_commit_stack([], submit.SubmitOptions())
        self.assertEqual((warnings, errors), ({}, {}))

        warnings, _ = submit.validate_commit_stack(
            [_commit(bug_orig="2", bug="1", granted=["alice"])], submit.SubmitOptions()
        )
        self.assertEqual(list(warnings.values()), [["Bug ID changed from 2 to 1"]])

        # Submit with missing bug ID.
        warnings, _ = submit.validate_commit_stack(
            [_commit(bug="", request=["bob"])], submit.SubmitOptions(force=True)
        )
        self.assertEqual(list(warnings.values()), [["Missing bug ID"]])

        # Submit with missing reviewers.
        warnings, _ = submit.validate_commit_stack([_commit()], submit.SubmitOptions())
        self.assertEqual(list(warnings.values()), [["Missing reviewers"]])

        # Submit with existing revision reviewers.
        m_get_revisions.return_value = [search_revision(reviewers=["PHID-USER-2"])]
        warnings, errors = submit.validate_commit_stack(
            [_commit(rev=1)], submit.SubmitOptions()
        )
        self.assertEqual((warnings, errors), ({}, {}))

        # Do not update not changed commits
        m_get_revisions.return_value = [
            search_revision(),
            search_revision(rev=2, phid="PHID-REV-2", diff="PHID-DIFF-2"),
        ]
        m_get_diffs.return_value = {
            "PHID-DIFF-1": search_remote_diff(),
            "PHID-DIFF-2": search_remote_diff(),
        }
        # we're changing bug id in the first revision to 2
        warnings, _ = submit.validate_commit_stack(
            [
                _commit(node="aaa000aaa000", rev=1, bug="2", granted=["alice"]),
                _commit(node="bbb000bbb000", title="B", rev=2, granted=["alice"]),
            ],
            submit.SubmitOptions(),
        )
        self.assertEqual(
            warnings, {"aaa000aaa000": [Contains("revision will change from 1 to 2")]}
        )

        # Adding only commit-title reviewers is enough to submit the revision.
        m_get_revisions.return_value = [search_revision(reviewers=["PHID-USER-2"])]
        m_get_diffs.return_value = {
            "PHID-DIFF-1": search_remote_diff(
                node="aaa000aaa000",
                message="A r?alice",
            )
        }
        changed_reviewers_commit = _commit(node="aaa000aaa000", rev=1, granted=["bob"])
        warnings, errors = submit.validate_commit_stack(
            [changed_reviewers_commit], submit.SubmitOptions()
        )
        self.assertEqual((warnings, errors), ({}, {}))
        self.assertTrue(changed_reviewers_commit.submit)

        # Reviewers that were previously requested locally but removed in
        # Phabricator should not be re-added on a reviewer-only resubmit.
        m_get_revisions.return_value = [search_revision(reviewers=[])]
        removed_reviewer_commit = _commit(node="aaa000aaa000", rev=1, granted=["alice"])
        warnings, errors = submit.validate_commit_stack(
            [removed_reviewer_commit], submit.SubmitOptions()
        )
        self.assertEqual(errors, {})
        self.assertEqual(
            warnings, {"aaa000aaa000": [Contains("revision has not changed")]}
        )
        self.assertFalse(removed_reviewer_commit.submit)

        # Resigned reviewers remain in the revision attachment and should not
        # be re-added even when they are newly introduced in the local title.
        m_get_revisions.return_value = [
            search_revision(
                reviewers=[
                    {
                        "reviewerPHID": "PHID-USER-2",
                        "status": "resigned",
                        "isBlocking": False,
                    }
                ]
            )
        ]
        m_get_diffs.return_value = {
            "PHID-DIFF-1": search_remote_diff(
                node="aaa000aaa000",
                message="A",
            )
        }
        resigned_reviewer_commit = _commit(
            node="aaa000aaa000", rev=1, granted=["alice"]
        )
        warnings, errors = submit.validate_commit_stack(
            [resigned_reviewer_commit], submit.SubmitOptions()
        )
        self.assertEqual(errors, {})
        self.assertEqual(
            warnings, {"aaa000aaa000": [Contains("revision has not changed")]}
        )
        self.assertFalse(resigned_reviewer_commit.submit)

        # Reviewers first requested on a WIP diff still need to be added once
        # the revision is ready, even if the commit SHA1 is unchanged.
        m_get_revisions.return_value = [search_revision(reviewers=[])]
        m_get_diffs.return_value = {
            "PHID-DIFF-1": search_remote_diff(
                node="aaa000aaa000",
                message="WIP: A r?alice",
            )
        }
        wip_reviewer_commit = _commit(node="aaa000aaa000", rev=1, granted=["alice"])
        with mock.patch.object(
            mozphab.conduit, "latest_non_wip_diff", return_value=None
        ):
            warnings, errors = submit.validate_commit_stack(
                [wip_reviewer_commit], submit.SubmitOptions()
            )
        self.assertEqual((warnings, errors), ({}, {}))
        self.assertTrue(wip_reviewer_commit.submit)

        m_whoami.return_value = {"phid": "PHID-USER-2"}
        warnings, _ = submit.validate_commit_stack(
            [_commit(rev=1, granted=["alice"])], submit.SubmitOptions()
        )
        self.assertEqual(list(warnings.values()), [[Contains("Commandeer")]])

        # Information about not updating the commit if not changed
        m_whoami.return_value = {"phid": "PHID-USER-1"}
        m_get_revisions.return_value = [search_revision(reviewers=["PHID-USER-2"])]
        m_get_diffs.return_value = {
            "PHID-DIFF-1": search_remote_diff(node="aaa000aaa000")
        }

        warnings, _ = submit.validate_commit_stack(
            [_commit(node="aaa000aaa000", rev=1, granted=["alice"])],
            submit.SubmitOptions(),
        )
        self.assertEqual(
            warnings, {"aaa000aaa000": [Contains("revision has not changed")]}
        )

        # Removing the WIP state from the revision without changing the commit's SHA1
        m_get_revisions.return_value = [search_revision(status="changes-planned")]
        warnings, _ = submit.validate_commit_stack(
            [_commit(node="aaa000aaa000", rev=1, granted=["alice"])],
            submit.SubmitOptions(),
        )
        self.assertEqual(
            warnings,
            {"aaa000aaa000": [Contains('"Changes Planned" status will change')]},
        )

        # Adding the WIP state to the revision without changing the commit's SHA1
        m_get_revisions.return_value = [search_revision()]
        warnings, _ = submit.validate_commit_stack(
            [_commit(node="aaa000aaa000", rev=1, granted=["alice"], wip=True)],
            submit.SubmitOptions(wip=True),
        )
        self.assertEqual(
            warnings,
            {"aaa000aaa000": [Contains('status will change to "Changes Planned"')]},
        )

        # Submit a new patch with reviewers.
        warnings, errors = submit.validate_commit_stack(
            [_commit(request=["alice"])], submit.SubmitOptions()
        )
        self.assertEqual((warnings, errors), ({}, {}))

        # Submit a new patch without reviewers.
        warnings, _ = submit.validate_commit_stack(
            [_commit(rev=None, request=[], wip=True)], submit.SubmitOptions()
        )
        self.assertEqual(
            list(warnings.values()),
            [
                [
                    Contains("Missing reviewers"),
                    Contains('submitted as "Changes Planned"'),
                ]
            ],
        )

        # Submit a new WIP patch with reviewers.
        warnings, errors = submit.validate_commit_stack(
            [_commit(rev=None, request=["alice"], wip=True)],
            submit.SubmitOptions(wip=True),
        )
        self.assertEqual((warnings, errors), ({}, {}))

        # Submit a new patch with the WIP prefix.
        warnings, _ = submit.validate_commit_stack(
            [_commit(title="WIP: A", request=["alice"], wip=True)],
            submit.SubmitOptions(),
        )
        self.assertEqual(
            list(warnings.values()), [[Contains('submitted as "Changes Planned"')]]
        )

        # Submitting a stack of two commits with one revision already on Phabricator
        # should properly set `commit.rev_phid`.
        commits = [_commit(title="submitted", rev=1), _commit(title="new", rev=None)]
        submit.validate_commit_stack(commits, submit.SubmitOptions())
        self.assertEqual(
            commits[0].rev_phid,
            "PHID-DREV-1",
            "`rev_id` should be set for already submitted commit.",
        )
        self.assertIsNone(
            commits[1].rev_phid, "`rev_id` should not be set for newly created commit."
        )

        # Submitting a new uplift clears reviewers and shouldn't warn.
        warnings, errors = submit.validate_commit_stack(
            [_commit(rev=None, granted=[], request=[])],
            submit.SubmitOptions(uplift=submit.UpliftSettings()),
        )
        self.assertEqual((warnings, errors), ({}, {}))

    def test_update_commits_from_options(self):
        def lwr(revs):
            return [r.lower() for r in revs]

        update = submit.update_commits_from_options

        _commits = [
            Commit(title="A", reviewers={"granted": [], "request": []}, bug_id=None),
            Commit(
                title="B",
                reviewers={"granted": [], "request": ["one"]},
                bug_id="1",
            ),
        ]

        # No change if noreviewer  args provided
        commits = copy.deepcopy(_commits)
        commits[1].reviewers["granted"].append("two")
        update(commits, submit.SubmitOptions())
        self.assertEqual(
            commits,
            [
                Commit(
                    title="A",
                    reviewers={"granted": [], "request": []},
                    bug_id=None,
                    wip=True,
                ),
                Commit(
                    title="B",
                    reviewers={"granted": ["two"], "request": ["one"]},
                    bug_id="1",
                    wip=False,
                ),
            ],
        )

        # Adding and removing reviewers, forcing the bug ID
        commits = copy.deepcopy(_commits)
        update(commits, submit.SubmitOptions(reviewers=("two", "three"), bug="2"))
        assert commits[0].title == "A"
        assert commits[0].bug_id == "2"
        assert "two" in commits[0].reviewers["granted"]
        assert "three" in commits[0].reviewers["granted"]

        assert commits[1].title == "B"
        assert commits[1].bug_id == "2"
        assert "two" in commits[1].reviewers["granted"]
        assert "three" in commits[1].reviewers["granted"]

        # Removing duplicates
        commits = copy.deepcopy(_commits)
        update(
            commits,
            submit.SubmitOptions(
                reviewers=("Two", "two", "two!", "three", "Three", "THREE!"),
                blockers=("Two", "THREE!", "three", "two", "three"),
            ),
        )
        assert commits[0].title == "A"
        assert commits[0].bug_id is None
        assert "two!" in lwr(commits[0].reviewers["granted"])
        assert "three!" in lwr(commits[0].reviewers["granted"])

        assert commits[1].title == "B"
        assert commits[1].bug_id == "1"
        assert "two!" in lwr(commits[1].reviewers["granted"])
        assert "three!" in lwr(commits[1].reviewers["granted"])

        # Adding blocking reviewers via args
        commits = copy.deepcopy(_commits)
        commits[1].reviewers["request"].append("three")
        commits[1].reviewers["granted"].append("four")
        commits[1].reviewers["granted"].append("five")
        update(
            commits,
            submit.SubmitOptions(
                reviewers=("one", "two!", "four"),
                blockers=("three", "four!"),
            ),
        )
        assert commits[0].title == "A"
        assert commits[0].bug_id is None
        assert "one" in lwr(commits[0].reviewers["granted"])
        assert "two!" in lwr(commits[0].reviewers["granted"])
        assert "three!" in lwr(commits[0].reviewers["granted"])
        assert "four!" in lwr(commits[0].reviewers["granted"])

        assert commits[1].title == "B"
        assert commits[1].bug_id == "1"
        assert "four!" in commits[1].reviewers["granted"]
        assert "two!" in commits[1].reviewers["granted"]
        assert "one" in commits[1].reviewers["request"]
        assert "three!" in commits[1].reviewers["request"]

        # reviewerless should result in WIP commits
        commits = copy.deepcopy(_commits)
        commits.append(Commit(title="Bug 2 - A", bug_id="2", rev_id=2))
        with mock.patch("mozphab.commands.submit.conduit.has_revision_reviewers") as m:
            m.side_effect = [
                False,  # First commit doesn't have reviewers.
                # Function isn't called for second commit since it has reviewers.
                True,  # For revision ID 2.
            ]
            update(commits, submit.SubmitOptions())
        assert commits[0].wip
        assert not commits[1].wip
        assert not commits[2].wip

        # Force WIP
        commits = copy.deepcopy(_commits)
        update(commits, submit.SubmitOptions(wip=True))
        assert commits[0].wip
        assert commits[1].wip

        # reviewerless with --no-wip shouldn't be WIP
        commits = copy.deepcopy(_commits)
        commits.append(Commit(title="WIP: Bug 2 - A", bug_id="2", wip=True))
        update(commits, submit.SubmitOptions(no_wip=True))
        assert not commits[0].wip
        assert not commits[1].wip
        assert not commits[2].wip

        # Reviewerless uplift commits should not be marked as WIP, since
        # uplift clears reviewers and `#release-managers` is set by automation.
        commits = copy.deepcopy(_commits)
        update(commits, submit.SubmitOptions(uplift=submit.UpliftSettings()))
        assert not commits[0].wip, "Uplift commit without reviewers should not be WIP."
        assert not commits[1].wip, "Uplift commit with reviewers should not be WIP."

        # Forcing blocking reviewers
        commits = copy.deepcopy(_commits)
        commits[1].reviewers["granted"].append("two")
        update(commits, submit.SubmitOptions(always_blocking=True))
        self.assertEqual(
            commits,
            [
                Commit(
                    title="A",
                    reviewers={"granted": [], "request": []},
                    bug_id=None,
                    wip=True,
                ),
                Commit(
                    title="B",
                    reviewers={"granted": ["two!"], "request": ["one!"]},
                    bug_id="1",
                    wip=False,
                ),
            ],
        )

    def test_update_commits_from_options_test_plan_single_commit(self):
        commits = [Commit(title="A", reviewers={"granted": [], "request": []})]
        with mock.patch("mozphab.commands.submit.conduit") as m_conduit:
            m_conduit.has_revision_reviewers.return_value = False
            submit.update_commits_from_options(
                commits, submit.SubmitOptions(test_plan="Run tests")
            )
        self.assertEqual(commits[0].test_plan, "Run tests")

    def test_update_commits_from_options_test_plan_multi_commit_raises(self):
        commits = [
            Commit(title="A", reviewers={"granted": [], "request": []}),
            Commit(title="B", reviewers={"granted": ["r"], "request": []}),
        ]
        with self.assertRaises(exceptions.Error) as cm:
            submit.update_commits_from_options(
                commits, submit.SubmitOptions(test_plan="Run tests")
            )

        self.assertIn("--test-plan", str(cm.exception))

    def test_single_fails_with_end_rev(self):
        # --single is working with one SHA1 provided only
        self._assertNoError(repository.StackSelection, single=True)
        self._assertError(
            repository.StackSelection,
            "Option --single can be used with only one identifier.",
            single=True,
            end_rev="endrev",
        )


class TestUpdateCommitSummary(unittest.TestCase):
    def test_update_revision_description(self):
        c = commit(
            rev_id="D123",
            title="hi!",
            body="hello!  µ-benchmarks are a thing.\n\n"
            "Differential Revision: http://phabricator.test/D123",
        )
        r = {"fields": {"title": "", "summary": ""}}
        t = []

        expected = [
            {"type": "title", "value": "hi!"},
            {"type": "summary", "value": "hello!  µ-benchmarks are a thing."},
        ]
        submit.update_revision_description(t, c, r)

        self.assertListEqual(t, expected)

    def test_update_revision_description_no_op(self):
        c = commit(
            rev_id="D123",
            title="hi!",
            body="hello!\n\nDifferential Revision: http://phabricator.test/D123",
        )
        r = {"fields": {"title": "hi!", "summary": "hello!\n\n"}}
        t = []

        expected = []
        submit.update_revision_description(t, c, r)

        self.assertListEqual(t, expected)

    def test_update_revision_description_with_test_plan(self):
        c = commit(rev_id="D123", title="hi!", body="")
        c.test_plan = "Run unit tests"
        r = {"fields": {"title": "hi!", "summary": "", "testPlan": ""}}
        t = []

        submit.update_revision_description(t, c, r)

        self.assertIn({"type": "testPlan", "value": "Run unit tests"}, t)

    def test_update_revision_description_test_plan_no_op(self):
        c = commit(rev_id="D123", title="hi!", body="")
        c.test_plan = "Run unit tests"
        r = {"fields": {"title": "hi!", "summary": "", "testPlan": "Run unit tests"}}
        t = []

        submit.update_revision_description(t, c, r)

        self.assertNotIn({"type": "testPlan", "value": "Run unit tests"}, t)

    def test_update_revision_description_empty_test_plan_not_sent(self):
        c = commit(rev_id="D123", title="hi!", body="")
        r = {"fields": {"title": "hi!", "summary": "", "testPlan": "existing plan"}}
        t = []

        submit.update_revision_description(t, c, r)

        self.assertFalse(any(tx["type"] == "testPlan" for tx in t))

    @mock.patch("mozphab.conduit.ConduitAPI.get_users")
    def test_set_initial_revision_reviewers(self, m_get_users):
        # From https://phabricator.services.mozilla.com/api/differential.revision.edit
        #
        # Example call format we are aiming for:
        #
        # $ echo '{
        #   "transactions": [
        #     {
        #       "type": "reviewers.set",
        #       "value": ["PHID-USER-1", "blocking(PHID-USER-2)"]
        #     }
        #   ],
        #   "objectIdentifier": "D8095"
        # }' | arc call-conduit --conduit-uri \
        #       https://phabricator.services.mozilla.com/ \
        #       --conduit-token <conduit-token> differential.revision.edit
        m_get_users.side_effect = (
            [
                {"phid": "PHID-USER-1"},
                {"phid": "PHID-USER-2"},
                {"phid": "PHID-USER-3"},
            ],
            [{"phid": "PHID-USER-1"}],
            [{"phid": "PHID-USER-2"}, {"phid": "PHID-USER-3"}],
        )
        c = commit(rev_id="123", reviewers=[["alice", "bob!"], ["frankie!"]])
        t = []

        expected = [
            Transaction(
                TransactionType.REVIEWERS_SET,
                ("PHID-USER-1", "blocking(PHID-USER-2)", "blocking(PHID-USER-3)"),
            )
        ]
        mozphab.conduit.set_initial_revision_reviewers(t, c)

        self.assertEqual(
            m_get_users.call_args_list,
            [
                mock.call(["alice", "bob!", "frankie!"]),
                mock.call(["alice"]),
                mock.call(["bob", "frankie"]),
            ],
        )
        self.assertListEqual(t, expected)

    def test_parse_api_response_with_no_problems(self):
        # Response comes from running:
        # $ echo '{... (some valid update summary JSON) ...}' | \
        #   arc call-conduit differential.revision.edit
        api_response = (
            '{"error":null,"errorMessage":null,"response":{"object":{'
            '"id":56,"phid":"PHID-DREV-ke6jhbdnwd5chtnk2q5w"},'
            '"transactions":[{"phid":"PHID-XACT-DREV-itlxgx7rsjrcnta"}]}} '
        )
        self.assertEqual(None, helpers.parse_api_error(api_response))

    def test_parse_api_response_with_errors(self):
        # Error response from running:
        # $ echo '{}' | arc call-conduit differential.revision.edit
        api_response = (
            '{"error":"ERR-CONDUIT-CORE", '
            '"errorMessage":"ERR-CONDUIT-CORE: Parameter '
            '\\"transactions\\" is not a list of transactions.",'
            '"response":null} '
        )
        self.assertEqual(
            'ERR-CONDUIT-CORE: Parameter "transactions" is not a list of transactions.',
            helpers.parse_api_error(api_response),
        )

    def test_update_revision_no_bug_id(self):
        # Phabricator stores patches with no bug as having an empty string as the
        # bug ID. We should not explicitly update the bug-id if our bug id is "None".
        transactions = []
        submit.update_revision_bug_id(
            transactions, Commit(bug_id=None), {"fields": {"bugzilla.bug-id": ""}}
        )
        self.assertEqual([], transactions)


@pytest.fixture
def m_conduit():
    with mock.patch("mozphab.commands.submit.conduit") as m:
        m.check.return_value = True
        m.check_for_invalid_reviewers.return_value = []
        m.get_revisions.return_value = []
        m.upload_files_from_diff.return_value = None
        m.submit_diff.return_value = {"phid": "PHID-DIFF-1", "diffid": "1"}
        m.create_revision.return_value = {
            "object": {"id": "123", "phid": "PHID-DREV-123"}
        }
        m.update_revision.return_value = {
            "object": {"id": "456", "phid": "PHID-DREV-456"}
        }
        m.set_diff_property.return_value = None
        m.request_ai_review.return_value = {}
        m.get_pending_reviews.return_value = (0, False)

        # Drive the parallel wrapper through the per-rev mock so tests can
        # keep asserting against ``request_ai_review`` directly while
        # exercising the public ``request_ai_reviews`` API used by submit.
        def fake_request_ai_reviews(rev_ids):
            results = {}
            for rev_id in rev_ids:
                try:
                    m.request_ai_review(rev_id)
                    results[rev_id] = None
                except ConduitAPIError as exc:
                    results[rev_id] = exc
            return results

        m.request_ai_reviews.side_effect = fake_request_ai_reviews
        yield m


@pytest.fixture
def m_repo():
    with mock.patch("mozphab.commands.submit.Repository") as m:
        m.phab_url = "http://example.test"
        m.get_diff.return_value = mock.MagicMock(phid="PHID-DIFF-1", id="1", changes={})
        m.check_vcs.return_value = None
        m.before_submit.return_value = None
        m.after_submit.return_value = None
        m.cleanup.return_value = None
        m.finalize.return_value = None
        m.refresh_commit_stack.return_value = None
        m.amend_commit.return_value = None
        yield m


@pytest.fixture
def m_validate():
    with mock.patch("mozphab.commands.submit.validate_commit_stack") as m:
        m.return_value = ([], {})
        yield m


@pytest.fixture
def m_logger():
    with mock.patch("mozphab.commands.submit.logger") as m:
        yield m


@pytest.fixture
def m_config():
    with mock.patch("mozphab.commands.submit.config") as m:
        yield m


def submit_options(**overrides: Any) -> submit.SubmitOptions:
    """Return the options of a `submit --yes` run, with `overrides` applied."""
    return submit.SubmitOptions(yes=True, **overrides)


def test_ai_review_requested_for_each_commit(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    commits = [commit(bug_id="1"), commit(bug_id="1")]
    m_repo.commit_stack.return_value = commits
    options = submit_options(ai=True)

    submit._submit(m_repo, options, UserData())

    assert m_conduit.request_ai_review.call_count == 2


def test_update_revision_error_names_revision(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    """A failing revision update reports which revision it belongs to."""
    validation_error = (
        "Validation errors:\n"
        "  - You can not request review of this revision because it has "
        "already been closed. You can only request review of open revisions."
    )
    m_repo.commit_stack.return_value = [
        commit(
            bug_id="1",
            rev_id=314841,
            body="Differential Revision: http://example.test/D314841",
        )
    ]
    m_conduit.update_revision.side_effect = ConduitAPIError(validation_error)

    with pytest.raises(exceptions.Error) as e:
        submit._submit(m_repo, submit_options(), UserData())

    assert str(e.value) == f"D314841: Phabricator Error: {validation_error}"


def test_ai_review_not_called_without_flag(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    commits = [commit(bug_id="1")]
    m_repo.commit_stack.return_value = commits
    options = submit_options(ai=False)

    submit._submit(m_repo, options, UserData())

    m_conduit.request_ai_review.assert_not_called()


def test_ai_review_failure_does_not_abort_submission(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    commits = [commit(bug_id="1")]
    m_repo.commit_stack.return_value = commits
    m_conduit.request_ai_review.side_effect = ConduitAPIError("AI service unavailable")
    options = submit_options(ai=True)

    result = submit._submit(m_repo, options, UserData())

    assert result is not None
    m_logger.error.assert_any_call(
        "Failed to request AI review for %s: %s", mock.ANY, mock.ANY
    )


def test_uplift_first_public_parent_uses_train_head(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    """`firstPublicParent` for uplifts should come from the train's head."""
    commits = [commit(bug_id="1")]
    m_repo.commit_stack.return_value = commits
    m_repo.get_repo_head_branch.return_value = "remotes/origin/beta"
    m_repo.resolve_node.return_value = "beta-head-sha"
    m_repo.get_public_node.side_effect = lambda node: node
    # No local rebase needed, but the train head should still be used.
    m_repo.is_descendant.return_value = True

    options = submit_options(
        uplift=submit.UpliftSettings(train="beta", no_rebase=False)
    )

    submit._submit(m_repo, options, UserData())

    m_repo.get_public_base_node.assert_not_called()
    _, _, _, first_public_parent = m_conduit.set_diff_property.call_args[0]
    assert first_public_parent == "beta-head-sha"
    assert m_conduit.create_diff.call_args.kwargs == {
        "is_uplift": True
    }, "Uplift diffs should be created with the uplift creation method."


def test_uplift_no_rebase_off_train_uses_public_base(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    """`--no-rebase` off the train should keep the commits' own public base.

    No rebase happens, so the diffs are still based on their original
    parent -- the train head isn't an ancestor of them at all.
    """
    commits = [commit(bug_id="1")]
    m_repo.commit_stack.return_value = commits
    m_repo.get_repo_head_branch.return_value = "remotes/origin/beta"
    m_repo.get_public_node.side_effect = lambda node: node
    m_repo.get_public_base_node.return_value = "central-public-base"
    m_repo.is_descendant.return_value = False

    options = submit_options(uplift=submit.UpliftSettings(train="beta", no_rebase=True))

    submit._submit(m_repo, options, UserData())

    m_repo.uplift_commits.assert_not_called()
    _, _, _, first_public_parent = m_conduit.set_diff_property.call_args[0]
    assert first_public_parent == "central-public-base"


def test_uplift_no_rebase_on_train_uses_train_head(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    """`--no-rebase` commits already on the train should report the train head."""
    commits = [commit(bug_id="1")]
    m_repo.commit_stack.return_value = commits
    m_repo.get_repo_head_branch.return_value = "remotes/origin/beta"
    m_repo.resolve_node.return_value = "beta-head-sha"
    m_repo.get_public_node.side_effect = lambda node: node
    m_repo.is_descendant.return_value = True

    options = submit_options(uplift=submit.UpliftSettings(train="beta", no_rebase=True))

    submit._submit(m_repo, options, UserData())

    m_repo.uplift_commits.assert_not_called()
    m_repo.get_public_base_node.assert_not_called()
    _, _, _, first_public_parent = m_conduit.set_diff_property.call_args[0]
    assert first_public_parent == "beta-head-sha"


def test_ai_review_skipped_for_non_submitted_commits(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    submitted = commit(bug_id="1")
    skipped = commit(bug_id="1", submit=False)
    m_repo.commit_stack.return_value = [skipped, submitted]
    options = submit_options(ai=True)

    submit._submit(m_repo, options, UserData())

    assert m_conduit.request_ai_review.call_count == 1


def test_ai_review_triggered_by_config(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    commits = [commit(bug_id="1")]
    m_repo.commit_stack.return_value = commits
    options = submit_options(ai=False, ai_review=True)

    submit._submit(m_repo, options, UserData())

    assert m_conduit.request_ai_review.call_count == 1


def test_ai_review_config_hint_shown(m_conduit, m_repo, m_validate, m_logger, m_config):
    commits = [commit(bug_id="1")]
    m_repo.commit_stack.return_value = commits
    options = submit_options(ai=True, ai_review=False)

    submit._submit(m_repo, options, UserData())

    m_logger.info.assert_any_call(
        "Tip: to always request AI review on new revisions, set submit.ai_review to true in %s",
        m_config.filename,
    )


def test_ai_review_skipped_for_updates_with_config(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    # Commit with rev_id set simulates an update to an existing revision.
    commits = [
        commit(
            bug_id="1",
            rev_id=456,
            body="Differential Revision: http://example.test/D456",
        )
    ]
    m_repo.commit_stack.return_value = commits
    options = submit_options(ai=False, ai_review=True)

    submit._submit(m_repo, options, UserData())

    m_conduit.request_ai_review.assert_not_called()


def test_ai_review_requested_for_updates_with_flag(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    # --ai flag should always request AI review, even for updates.
    commits = [
        commit(
            bug_id="1",
            rev_id=456,
            body="Differential Revision: http://example.test/D456",
        )
    ]
    m_repo.commit_stack.return_value = commits
    options = submit_options(ai=True)

    submit._submit(m_repo, options, UserData())

    assert m_conduit.request_ai_review.call_count == 1


def test_ai_review_config_hint_not_shown_when_config_set(
    m_conduit, m_repo, m_validate, m_logger, m_config
):
    commits = [commit(bug_id="1")]
    m_repo.commit_stack.return_value = commits
    options = submit_options(ai=True, ai_review=True)

    submit._submit(m_repo, options, UserData())

    for call in m_logger.info.call_args_list:
        assert "Tip:" not in str(call)


@pytest.mark.parametrize(
    "no_stack, expected_second_parent",
    [
        (True, None),
        (False, "PHID-DREV-123"),
    ],
    ids=["no_stack", "stack_default"],
)
def test_no_stack_linking(
    m_conduit,
    m_repo,
    m_validate,
    m_logger,
    m_config,
    no_stack,
    expected_second_parent,
):
    commits = [commit(bug_id="1"), commit(bug_id="1")]
    m_repo.commit_stack.return_value = commits
    options = submit_options(no_stack=no_stack)

    submit._submit(m_repo, options, UserData())

    assert m_conduit.create_revision.call_count == 2
    first_call, second_call = m_conduit.create_revision.call_args_list
    assert first_call.kwargs["parent_rev_phid"] is None
    assert second_call.kwargs["parent_rev_phid"] == expected_second_parent


def workflow_diff():
    diff = Diff()
    diff.change_for(".github/workflows/ci.yml")
    return diff


@mock.patch("mozphab.commands.submit.prompt", return_value="Yes")
def test_check_override_hints_adds_hint(m_prompt, m_logger):
    c = commit(bug_id="1", title="Bug 1 - x r=foo")
    submit.check_override_hints(c, workflow_diff(), can_prompt=True)
    assert c.title_preview == "Bug 1 - x r=foo DOT_GITHUB_OVERRIDE"
    m_logger.warning.assert_not_called()

    # The reviewers are still parsed from the amended title on the next submit.
    c.title = c.title_preview
    assert helpers.parse_reviewers(c.title)["granted"] == ["foo"]
    c.reviewers = {"granted": ["foo", "bar"], "request": []}
    assert helpers.build_commit_title(c) == "Bug 1 - x r=foo,bar DOT_GITHUB_OVERRIDE"


@mock.patch("mozphab.commands.submit.prompt", return_value="No")
def test_check_override_hints_declined(m_prompt, m_logger):
    c = commit(title="Bug 1 - x")
    submit.check_override_hints(c, workflow_diff(), can_prompt=True)
    assert c.title_preview == "Bug 1 - x"
    m_logger.warning.assert_called_once()


@mock.patch("mozphab.commands.submit.prompt")
def test_check_override_hints_cannot_prompt(m_prompt, m_logger):
    c = commit(title="Bug 1 - x")
    submit.check_override_hints(c, workflow_diff(), can_prompt=False)
    assert c.title_preview == "Bug 1 - x"
    m_prompt.assert_not_called()
    assert "DOT_GITHUB_OVERRIDE" in m_logger.warning.call_args.args[0]


@pytest.mark.parametrize(
    "path,hint",
    [
        ("security/nss/lib/ssl/ssl3con.c", "UPGRADE_NSS_RELEASE"),
        ("nsprpub/pr/src/io/prfile.c", "UPGRADE_NSPR_RELEASE"),
    ],
)
@mock.patch("mozphab.commands.submit.prompt")
def test_check_override_hints_warn_only(m_prompt, m_logger, path, hint):
    c = commit(title="Bug 1 - x")
    diff = Diff()
    diff.change_for(path)
    submit.check_override_hints(c, diff, can_prompt=True)
    assert c.title_preview == "Bug 1 - x"
    m_prompt.assert_not_called()
    assert hint in m_logger.warning.call_args.args[0]


@pytest.mark.parametrize(
    "title,path",
    [
        ("Bug 1 - x DOT_GITHUB_OVERRIDE", ".github/workflows/ci.yml"),
        ("Bug 1 - x UPGRADE_NSS_RELEASE", "security/nss/lib/ssl/ssl3con.c"),
        ("Bug 1 - x", "security/nssutil/x.c"),
        ("Bug 1 - x", ".github/dependabot.yml"),
        ("Bug 1 - x", "src/.github/workflows/ci.yml"),
    ],
)
@mock.patch("mozphab.commands.submit.prompt")
def test_check_override_hints_not_needed(m_prompt, m_logger, title, path):
    c = commit(title=title)
    diff = Diff()
    diff.change_for(path)
    submit.check_override_hints(c, diff, can_prompt=True)
    assert c.title_preview == title
    m_prompt.assert_not_called()
    m_logger.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()


def test_submit_options_from_args():
    """`SubmitOptions.from_args` keeps the flags and marks `uplift` runs."""
    test_config = Config(should_access_file=False)
    options = submit.SubmitOptions.from_args(
        mozphab.parse_args(["submit", "-r", "alice", "-R", "bob", "--wip"]),
        test_config,
    )
    assert options.reviewers == ("alice",), "`-r` should set the reviewers."
    assert options.blockers == ("bob",), "`-R` should set the blocking reviewers."
    assert options.wip, "`--wip` should be kept."
    assert not options.is_uplift, "`submit` shouldn't have uplift settings."

    options = submit.SubmitOptions.from_args(
        mozphab.parse_args(["uplift", "--train", "beta", "--no-rebase"]), test_config
    )
    assert options.uplift == submit.UpliftSettings(
        train="beta", no_rebase=True
    ), "`uplift` should pass its train and `--no-rebase` to `submit`."
    assert options.is_uplift, "`uplift` runs should be marked as uplifts."

    test_config.auto_submit = True
    test_config.ai_review = True
    test_config.always_blocking = True
    test_config.warn_untracked = False
    test_config.review_queue_reminder_frequency = 0
    test_config.review_queue_reminder_include_groups = False
    options = submit.SubmitOptions.from_args(
        mozphab.parse_args(["submit"]), test_config
    )
    assert (
        options.auto_submit
        and options.ai_review
        and options.always_blocking
        and not options.warn_untracked
        and options.review_queue_reminder_frequency == 0
        and not options.review_queue_reminder_include_groups
    ), "The `submit` config settings should be read into the options."


@pytest.mark.parametrize(
    "options,can_prompt",
    [
        (submit.SubmitOptions(), True),
        (submit.SubmitOptions(auto_submit=True), False),
        (submit.SubmitOptions(auto_submit=True, interactive=True), True),
        (submit.SubmitOptions(yes=True, interactive=True), False),
    ],
)
def test_submit_options_can_prompt(options, can_prompt):
    assert (
        options.can_prompt is can_prompt
    ), "`--yes` should never prompt, and `--interactive` should override `auto_submit`."


@pytest.mark.parametrize("force_vcs", [False, True])
def test_force_vcs_skips_the_vcs_check(
    m_conduit, m_repo, m_validate, m_logger, m_config, force_vcs
):
    m_repo.commit_stack.return_value = [commit(bug_id="1")]
    options = submit_options(force_vcs=force_vcs)

    submit._submit(m_repo, options, UserData())

    assert (
        m_repo.check_vcs.called != force_vcs
    ), "The VCS should be checked unless `--force-vcs` is given."


def test_submit_options_from_args_merge_conflict(tmp_path):
    """`--merge-conflict` and `--merge-conflict-commit` set the uplift's conflict."""
    test_config = Config(should_access_file=False)
    conflict_path = tmp_path / "conflict.diff"

    options = submit.SubmitOptions.from_args(
        mozphab.parse_args(
            [
                "uplift",
                "--train",
                "beta",
                "--merge-conflict",
                str(conflict_path),
                "--merge-conflict-commit",
                "abc123",
            ]
        ),
        test_config,
    )
    assert options.uplift is not None, "`uplift` should have uplift settings."
    assert options.uplift.merge_conflict == submit.MergeConflict(
        path=conflict_path, commit="abc123"
    ), "`--merge-conflict` and its commit should be passed to `submit`."
    assert (
        options.merge_conflict is options.uplift.merge_conflict
    ), "`SubmitOptions.merge_conflict` should be the uplift's merge conflict."

    options = submit.SubmitOptions.from_args(
        mozphab.parse_args(["uplift", "--train", "beta"]), test_config
    )
    assert options.uplift is not None, "`uplift` should have uplift settings."
    assert (
        options.uplift.merge_conflict is None
    ), "Uplifts without `--merge-conflict` shouldn't have a merge conflict."

    options = submit.SubmitOptions.from_args(
        mozphab.parse_args(["submit"]), test_config
    )
    assert (
        options.merge_conflict is None
    ), "`submit` without `uplift` shouldn't have a merge conflict."


@pytest.mark.parametrize(
    "flags",
    [
        ["--merge-conflict", "conflict.diff"],
        ["--merge-conflict-commit", "abc123"],
    ],
)
def test_submit_options_from_args_merge_conflict_needs_both_flags(flags):
    """`--merge-conflict` and `--merge-conflict-commit` can't be used alone."""
    test_config = Config(should_access_file=False)
    args = mozphab.parse_args(["uplift", "--train", "beta", *flags])

    with pytest.raises(exceptions.Error, match="must be used together"):
        submit.SubmitOptions.from_args(args, test_config)


@pytest.fixture
def merge_conflict(tmp_path) -> submit.MergeConflict:
    conflict_path = tmp_path / "conflict.diff"
    conflict_path.write_text("<<<<<<< HEAD\n=======\n>>>>>>> theirs\n")
    return submit.MergeConflict(path=conflict_path, commit="abc")


@pytest.fixture
def m_conflict_conduit(m_conduit, m_repo):
    """Return the `m_conduit` of an uplift whose conflict diff is `PHID-DIFF-7`."""
    m_repo.phid = "PHID-BETA"
    m_conduit.create_raw_diff.return_value = CreateRawDiffResult(
        id=7, phid="PHID-DIFF-7"
    )
    return m_conduit


def merge_conflict_options(merge_conflict, **overrides):
    """Return the options of a `moz-phab uplift --merge-conflict` run."""
    return submit_options(
        uplift=submit.UpliftSettings(
            train="beta", no_rebase=True, merge_conflict=merge_conflict
        ),
        **overrides,
    )


def revision_calls(m_conduit) -> list[str]:
    """Return the names of the `conduit` calls creating diffs and revisions."""
    names = {
        "create_raw_diff",
        "create_diff",
        "create_diffs",
        "create_revision",
        "update_revision",
        "edit_revision",
        "apply_transactions_to_revision",
        "set_diff_property",
    }
    return [name for name, _, _ in m_conduit.mock_calls if name in names]


def test_merge_conflict_new_revision(
    m_conflict_conduit, m_repo, m_validate, m_logger, m_config, merge_conflict
):
    """A new revision gets the merge conflict diff first, then its resolution."""
    commits = [commit(bug_id="1", node="abc" + "0" * 37)]
    m_repo.commit_stack.return_value = commits

    submit._submit(m_repo, merge_conflict_options(merge_conflict), UserData())

    assert revision_calls(m_conflict_conduit) == [
        "create_raw_diff",
        "create_diff",
        "create_revision",
        "apply_transactions_to_revision",
        "set_diff_property",
    ], "The conflict diff should be created before the resolution's diff."
    m_conflict_conduit.create_raw_diff.assert_called_once_with(
        "<<<<<<< HEAD\n=======\n>>>>>>> theirs\n", "PHID-BETA"
    )
    assert (
        m_conflict_conduit.create_revision.call_args.args[1] == "PHID-DIFF-7"
    ), "The revision should be created with the conflict diff."
    m_conflict_conduit.apply_transactions_to_revision.assert_called_once_with(
        "123",
        [Transaction(TransactionType.UPDATE, "PHID-DIFF-1")],
    )
    assert (
        m_conflict_conduit.set_diff_property.call_args.args[0] == "1"
    ), "`set_diff_property` should describe the resolution's diff."
    assert commits[0].merge_conflict == MergeConflictDiffs(
        conflict=CreateRawDiffResult(id=7, phid="PHID-DIFF-7"), resolution_id=1
    ), "The conflict and resolution diffs should be recorded on the commit."


def test_find_merge_conflict_commit_matches_original_sha():
    """The merge conflict commit is found by its SHA from before any rebase."""
    rebased = commit(node="def" + "0" * 37)
    rebased.orig_node = "abc" + "0" * 37
    other = commit(node="abd" + "0" * 37)

    assert (
        submit.find_merge_conflict_commit([rebased, other], "abc") is rebased
    ), "`find_merge_conflict_commit` should match the commit's original SHA."


def test_merge_conflict_only_for_matching_commit(
    m_conflict_conduit, m_repo, m_validate, m_logger, m_config, merge_conflict
):
    """Only the commit matching `--merge-conflict-commit` gets the conflict diff."""
    commits = [
        commit(bug_id="1", node="def" + "0" * 37),
        commit(bug_id="1", node="abc" + "0" * 37),
    ]
    m_repo.commit_stack.return_value = commits

    submit._submit(m_repo, merge_conflict_options(merge_conflict), UserData())

    assert revision_calls(m_conflict_conduit) == [
        "create_raw_diff",
        "create_diffs",
        "create_revision",
        "set_diff_property",
        "create_revision",
        "apply_transactions_to_revision",
        "set_diff_property",
    ], "Only the second commit's revision should get the resolution as an update."
    first_diffs = [
        call.args[1] for call in m_conflict_conduit.create_revision.call_args_list
    ]
    assert first_diffs == [
        "PHID-DIFF-1",
        "PHID-DIFF-7",
    ], "Only the matching commit's revision should be created with the conflict."
    assert (
        commits[0].merge_conflict is None
    ), "The other commit shouldn't record a merge conflict."


def test_merge_conflict_skipped_on_update(
    m_conflict_conduit, m_repo, m_validate, m_logger, m_config, merge_conflict
):
    """The conflict isn't uploaded when the commit updates an existing revision."""
    commits = [
        commit(
            bug_id="1",
            node="abc" + "0" * 37,
            body="Differential Revision: http://example.test/D5",
        )
    ]
    m_repo.commit_stack.return_value = commits

    submit._submit(m_repo, merge_conflict_options(merge_conflict), UserData())

    m_conflict_conduit.create_raw_diff.assert_not_called()
    assert (
        m_conflict_conduit.update_revision.call_args.kwargs["diff_phid"]
        == "PHID-DIFF-1"
    ), "The existing revision should be updated with the commit's own diff."
    m_logger.warning.assert_any_call(
        "Not uploading the merge conflict of %s, as it updates %s.",
        commits[0].name,
        "D5",
    )
    assert (
        commits[0].merge_conflict is None
    ), "No merge conflict should be recorded when it isn't uploaded."


@pytest.mark.parametrize(
    "sha,message",
    [
        ("fff", "isn't one of the commits to submit"),
        ("", "isn't one of the commits to submit"),
        ("ab", "matches more than one of the commits"),
    ],
)
def test_merge_conflict_commit_must_match_one_commit(
    m_conflict_conduit,
    m_repo,
    m_validate,
    m_logger,
    m_config,
    merge_conflict,
    sha,
    message,
):
    """`--merge-conflict-commit` must match exactly one commit to submit."""
    m_repo.commit_stack.return_value = [
        commit(bug_id="1", node="abc" + "0" * 37),
        commit(bug_id="1", node="abd" + "0" * 37),
    ]
    options = merge_conflict_options(
        submit.MergeConflict(path=merge_conflict.path, commit=sha)
    )

    with pytest.raises(exceptions.Error, match=message):
        submit._submit(m_repo, options, UserData())

    assert (
        revision_calls(m_conflict_conduit) == []
    ), "Nothing should be submitted when the merge conflict commit doesn't match."
