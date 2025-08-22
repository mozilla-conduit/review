# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import copy
import unittest
import uuid
from unittest import mock

from callee import Contains

from mozphab import environment, exceptions, helpers, mozphab, repository
from mozphab.commands import submit
from mozphab.commits import Commit

from .conftest import search_diff, search_rev


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
        submit=submit,
        wip=wip,
    )


class Args(argparse.Namespace):
    def __init__(
        self,
        command="submit",
        reviewer=None,
        blocker=None,
        bug=None,
        wip=False,
        no_wip=False,
        no_bug=False,
        force=False,
        single=False,
        end_rev=environment.DEFAULT_END_REV,
    ):
        self.command = command
        self.reviewer = reviewer
        self.blocker = blocker
        self.bug = bug
        self.wip = wip
        self.no_wip = no_wip
        self.no_bug = no_bug
        self.force = force
        self.single = single
        self.end_rev = end_rev


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

    @mock.patch("mozphab.conduit.ConduitAPI.check_for_invalid_reviewers")
    @mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
    def test_commit_stack_validation_errors(self, m_revs, check_reviewers):
        check_reviewers.return_value = []
        m_revs.return_value = []

        _, errors = submit.validate_commit_stack([commit("1", (["r"], []))], Args())
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [
                commit("1", (["r1"], [])),
                commit("2", (["r1"], [])),
                commit("3", (["r1", "r2"], [])),
            ],
            Args(),
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack([commit("1", None)], Args())
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [commit("1", (["r"], [])), commit("1", None)], Args()
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack([commit(None, (["r"], []))], Args())
        self.assertEqual(list(errors.values()), [[Contains("Missing bug ID")]])

        _, errors = submit.validate_commit_stack(
            [commit(None, (["r"], []))], Args(no_bug=True)
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack([commit("", (["r"], []))], Args())
        self.assertEqual(list(errors.values()), [[Contains("Missing bug ID")]])

        _, errors = submit.validate_commit_stack(
            [commit("1", (["r"], [])), commit("", (["r"], []))], Args()
        )
        self.assertEqual(list(errors.values()), [[Contains("Missing bug ID")]])

        _, errors = submit.validate_commit_stack(
            [commit("1", (["r"], [])), commit("", (["r"], []))], Args(no_bug=True)
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [commit("1", (["r"], []), body="Summary: blah\nReviewers: r")], Args()
        )
        self.assertEqual(list(errors.values()), [[Contains("Contains arc fields")]])

        _, errors = submit.validate_commit_stack([commit(bug_id="1", rev_id=1)], Args())
        self.assertEqual(
            list(errors.values()),
            [[Contains("didn't return a query result for revision D1")]],
        )

    @mock.patch("mozphab.conduit.ConduitAPI.check_for_invalid_reviewers")
    @mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
    @mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
    @mock.patch("mozphab.conduit.ConduitAPI.whoami")
    def test_invalid_reviewers_fails_the_stack_validation_check(
        self, m_whoami, m_diffs, m_revs, check_reviewers
    ):
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
        m_diffs.return_value = {"PHID-DIFF-1": search_diff()}
        m_whoami.return_value = {"phid": "PHID-USER-1"}

        _, errors = submit.validate_commit_stack(
            # Build a stack with an invalid reviewer in the middle.
            [
                commit("1", (["alice"], [])),
                commit("2", (["bob", "gonzo"], [])),
                commit("3", (["charlie"], [])),
            ],
            Args(),
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
            Args(),
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
            Args(force=True),
        )
        self.assertEqual(
            list(warnings.values()), [[Contains("goober isn't available until string")]]
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [commit("1", (["goofus"], []))], Args()
        )
        self.assertEqual(list(errors.values()), [[Contains("goofus is disabled")]])

        # Never error for an existing revision with reviewers.
        m_revs.return_value = [search_rev(reviewers=["PHID-USER-2"])]
        _, errors = submit.validate_commit_stack(
            [commit("1", (["goofus"], []), rev_id=1)], Args()
        )
        self.assertEqual(errors, {})

    @mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
    @mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
    @mock.patch("mozphab.conduit.ConduitAPI.whoami")
    @mock.patch("mozphab.conduit.ConduitAPI.check_for_invalid_reviewers")
    def test_validate_duplicate_revision(
        self, check_reviewers, m_whoami, m_diffs, m_revs
    ):
        check_reviewers.return_value = []
        m_revs.return_value = [
            search_rev(rev=1),
            search_rev(rev=2),
            search_rev(rev=3),
        ]
        m_diffs.return_value = {"PHID-DIFF-1": search_diff()}
        m_whoami.return_value = {"phid": "PHID-USER-1"}

        _, errors = submit.validate_commit_stack(
            [
                commit("1", (["r"], []), node="a"),
                commit("2", (["r"], []), node="b"),
                commit("3", (["r"], []), node="c"),
            ],
            Args(),
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [
                commit("1", (["r"], []), rev_id=1, node="a"),
                commit("2", (["r"], []), rev_id=2, node="b"),
                commit("3", (["r"], []), rev_id=3, node="c"),
            ],
            Args(),
        )
        self.assertEqual(errors, {})

        _, errors = submit.validate_commit_stack(
            [
                commit("1", (["r"], []), rev_id=1, node="a"),
                commit("2", (["r"], []), rev_id=2, node="b"),
                commit("3", (["r"], []), rev_id=1, node="c"),
            ],
            Args(),
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
            Args(),
        )
        self.assertEqual(
            errors,
            {
                "c": [Contains("commit a refers to the same one D1")],
                "d": [Contains("commit b refers to the same one D2")],
            },
        )

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
        replace = mozphab.replace_reviewers
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

    @mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
    @mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
    @mock.patch("mozphab.conduit.ConduitAPI.whoami")
    @mock.patch("mozphab.conduit.ConduitAPI.check_for_invalid_reviewers")
    def test_validate_commit_stack(
        self, m_check_reviewers, m_whoami, m_get_diffs, m_get_revisions
    ):
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
        m_get_revisions.return_value = [search_rev()]
        m_get_diffs.return_value = {"PHID-DIFF-1": search_diff()}
        m_check_reviewers.return_value = []

        warnings, errors = submit.validate_commit_stack([], Args())
        self.assertEqual((warnings, errors), ({}, {}))

        warnings, _ = submit.validate_commit_stack(
            [_commit(bug_orig="2", bug="1", granted=["alice"])], Args()
        )
        self.assertEqual(list(warnings.values()), [["Bug ID changed from 2 to 1"]])

        # Submit with missing bug ID.
        warnings, _ = submit.validate_commit_stack(
            [_commit(bug="", request=["bob"])], Args(force=True)
        )
        self.assertEqual(list(warnings.values()), [["Missing bug ID"]])

        # Submit with missing reviewers.
        warnings, _ = submit.validate_commit_stack([_commit()], Args())
        self.assertEqual(list(warnings.values()), [["Missing reviewers"]])

        # Submit with existing revision reviewers.
        m_get_revisions.return_value = [search_rev(reviewers=["PHID-USER-2"])]
        warnings, errors = submit.validate_commit_stack([_commit(rev=1)], Args())
        self.assertEqual((warnings, errors), ({}, {}))

        # Do not update not changed commits
        m_get_revisions.return_value = [
            search_rev(),
            search_rev(rev=2, phid="PHID-REV-2", diff="PHID-DIFF-2"),
        ]
        m_get_diffs.return_value = {
            "PHID-DIFF-1": search_diff(),
            "PHID-DIFF-2": search_diff(),
        }
        # we're changing bug id in the first revision to 2
        warnings, _ = submit.validate_commit_stack(
            [
                _commit(node="aaa000aaa000", rev=1, bug="2", granted=["alice"]),
                _commit(node="bbb000bbb000", title="B", rev=2, granted=["alice"]),
            ],
            Args(),
        )
        self.assertEqual(
            warnings, {"aaa000aaa000": [Contains("revision will change from 1 to 2")]}
        )

        m_whoami.return_value = {"phid": "PHID-USER-2"}
        warnings, _ = submit.validate_commit_stack(
            [_commit(rev=1, granted=["alice"])], Args()
        )
        self.assertEqual(list(warnings.values()), [[Contains("Commandeer")]])

        # Information about not updating the commit if not changed
        m_whoami.return_value = {"phid": "PHID-USER-1"}
        m_get_revisions.return_value = [search_rev(reviewers=["PHID-USER-2"])]
        m_get_diffs.return_value = {"PHID-DIFF-1": search_diff(node="aaa000aaa000")}

        warnings, _ = submit.validate_commit_stack(
            [_commit(node="aaa000aaa000", rev=1, granted=["alice"])], Args()
        )
        self.assertEqual(
            warnings, {"aaa000aaa000": [Contains("revision has not changed")]}
        )

        # Removing the WIP state from the revision without changing the commit's SHA1
        m_get_revisions.return_value = [search_rev(status="changes-planned")]
        warnings, _ = submit.validate_commit_stack(
            [_commit(node="aaa000aaa000", rev=1, granted=["alice"])], Args()
        )
        self.assertEqual(
            warnings,
            {"aaa000aaa000": [Contains('"Changes Planned" status will change')]},
        )

        # Adding the WIP state to the revision without changing the commit's SHA1
        m_get_revisions.return_value = [search_rev()]
        warnings, _ = submit.validate_commit_stack(
            [_commit(node="aaa000aaa000", rev=1, granted=["alice"], wip=True)],
            Args(wip=True),
        )
        self.assertEqual(
            warnings,
            {"aaa000aaa000": [Contains('status will change to "Changes Planned"')]},
        )

        # Submit a new patch with reviewers.
        warnings, errors = submit.validate_commit_stack(
            [_commit(request=["alice"])], Args()
        )
        self.assertEqual((warnings, errors), ({}, {}))

        # Submit a new patch without reviewers.
        warnings, _ = submit.validate_commit_stack(
            [_commit(rev=None, request=[], wip=True)], Args()
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
            [_commit(rev=None, request=["alice"], wip=True)], Args(wip=True)
        )
        self.assertEqual((warnings, errors), ({}, {}))

        # Submit a new patch with the WIP prefix.
        warnings, _ = submit.validate_commit_stack(
            [_commit(title="WIP: A", request=["alice"], wip=True)], Args()
        )
        self.assertEqual(
            list(warnings.values()), [[Contains('submitted as "Changes Planned"')]]
        )

        # Submitting a stack of two commits with one revision already on Phabricator
        # should properly set `commit.rev_phid`.
        commits = [_commit(title="submitted", rev=1), _commit(title="new", rev=None)]
        submit.validate_commit_stack(commits, Args())
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
            [_commit(rev=None, granted=[], request=[])], Args(command="uplift")
        )
        self.assertEqual((warnings, errors), ({}, {}))

    def test_update_commits_from_args(self):
        def lwr(revs):
            return [r.lower() for r in revs]

        update = submit.update_commits_from_args

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
        with mock.patch("mozphab.commands.submit.config") as m_config:
            m_config.always_blocking = False
            update(commits, Args())
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
        update(commits, Args(reviewer=["two", "three"], bug="2"))
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
            Args(
                reviewer=["Two", "two", "two!", "three", "Three", "THREE!"],
                blocker=["Two", "THREE!", "three", "two", "three"],
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
            Args(
                reviewer=["one", "two!", "four"],
                blocker=["three", "four!"],
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
            update(commits, Args())
        assert commits[0].wip
        assert not commits[1].wip
        assert not commits[2].wip

        # Force WIP
        commits = copy.deepcopy(_commits)
        update(commits, Args(wip=True))
        assert commits[0].wip
        assert commits[1].wip

        # reviewerless with --no-wip shouldn't be WIP
        commits = copy.deepcopy(_commits)
        commits.append(Commit(title="WIP: Bug 2 - A", bug_id="2", wip=True))
        update(commits, Args(no_wip=True))
        assert not commits[0].wip
        assert not commits[1].wip
        assert not commits[2].wip

        # Forcing blocking reviewers
        commits = copy.deepcopy(_commits)
        commits[1].reviewers["granted"].append("two")
        with mock.patch("mozphab.commands.submit.config") as m_config:
            m_config.always_blocking = True
            update(commits, Args())
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

    def test_single_fails_with_end_rev(self):
        # --single is working with one SHA1 provided only
        repo = repository.Repository("", "", "dummy")

        self._assertNoError(repo.set_args, Args(single=True))
        self._assertError(
            repo.set_args,
            "Option --single can be used with only one identifier.",
            Args(single=True, end_rev="endrev"),
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

    @mock.patch("mozphab.conduit.ConduitAPI.get_users")
    def test_update_revision_reviewers(self, m_get_users):
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
            {
                "type": "reviewers.set",
                "value": [
                    "PHID-USER-1",
                    "blocking(PHID-USER-2)",
                    "blocking(PHID-USER-3)",
                ],
            }
        ]
        mozphab.conduit.update_revision_reviewers(t, c)

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


if __name__ == "__main__":
    unittest.main()
