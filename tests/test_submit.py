# coding=utf-8
import copy
import imp
import mock
import os
import sys
import unittest
import uuid


mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)
mozphab.SHOW_SPINNER = False


def reviewers_dict(reviewers=None):
    return dict(
        request=reviewers[0] if reviewers else [],
        granted=reviewers[1] if reviewers else [],
    )


def commit(bug_id=None, reviewers=None, body=u"", name="", title="", rev_id=None):
    return {
        "name": name,
        "title": title,
        "bug-id": bug_id,
        "reviewers": reviewers_dict(reviewers),
        "body": body,
        "rev-id": rev_id,
        "node": uuid.uuid4().get_hex(),
    }


# noinspection PyPep8Naming,PyBroadException
class Commits(unittest.TestCase):
    def _assertNoError(self, callableObj, *args):
        try:
            callableObj(*args)
        except mozphab.Error:
            info = sys.exc_info()
            self.fail("%s raised" % repr(info[0]))

    def _assertError(self, callableObj, *args):
        try:
            callableObj(*args)
        except mozphab.Error:
            return
        except Exception:
            info = sys.exc_info()
            self.fail("%s raised" % repr(info[0]))
        self.fail("%s failed to raise Error" % callableObj)

    @mock.patch("mozphab.check_for_invalid_reviewers")
    def test_commit_validation(self, check_reviewers):
        check_reviewers.return_value = []
        repo = mozphab.Repository("", "", "dummy")
        check = repo.check_commits_for_submit

        self._assertNoError(check, [])
        self._assertNoError(check, [commit("1", (["r"], []))])
        self._assertNoError(
            check,
            [
                commit("1", (["r1"], [])),
                commit("2", (["r1"], [])),
                commit("3", (["r1", "r2"], [])),
            ],
        )
        self._assertNoError(check, [commit("1", None)])
        self._assertNoError(check, [commit("1", (["r"], [])), commit("1", None)])

        self._assertError(check, [commit(None, (["r"], []))])
        self._assertError(check, [commit("", (["r"], []))])
        self._assertError(
            check, [commit("1", (["r"], []), body="Summary: blah\nReviewers: r")]
        )

        self._assertError(check, [commit("1", (["r"], [])), commit("", (["r"], []))])

    @mock.patch("mozphab.check_for_invalid_reviewers")
    def test_invalid_reviewers_fails_the_stack_validation_check(self, check_reviewers):
        class Args:
            def __init__(self):
                self.force = False

        def fail_gonzo(reviewers):
            # Replace the check_for_invalid_reviewers() function with something that
            # fails if "gonzo" is in the reviewers list.
            if "gonzo" in reviewers["request"]:
                return [dict(name="gonzo")]
            elif "goober" in reviewers["request"]:
                return [dict(name="goober", until="string")]
            else:
                return []

        check_reviewers.side_effect = fail_gonzo
        repo = mozphab.Repository("", "", "dummy")
        repo.args = Args()

        self._assertError(
            repo.check_commits_for_submit,
            (
                # Build a stack with an invalid reviewer in the middle.
                [
                    commit("1", (["alice"], [])),
                    commit("2", (["bob", "gonzo"], [])),
                    commit("3", (["charlie"], [])),
                ]
            ),
        )

        self._assertError(
            repo.check_commits_for_submit,
            (
                # Build a stack with an unavailable reviewer in the middle.
                [
                    commit("1", (["alice"], [])),
                    commit("2", (["bob", "goober"], [])),
                    commit("3", (["charlie"], [])),
                ]
            ),
        )

        repo.args.force = True
        self._assertNoError(
            repo.check_commits_for_submit,
            (
                [
                    commit("1", (["alice"], [])),
                    commit("2", (["bob", "goober"], [])),
                    commit("3", (["charlie"], [])),
                ]
            ),
        )

    def test_commit_preview(self):
        build = mozphab.build_commit_title

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

    @mock.patch("mozphab.build_commit_title")
    def test_update_commit_title_previews(self, m_build_commit_title):
        m_build_commit_title.side_effect = lambda x: x["title"] + " preview"
        commits = [dict(title="a"), dict(title="b")]
        mozphab.update_commit_title_previews(commits)
        self.assertEqual(
            [
                {"title": "a", "title-preview": "a preview"},
                {"title": "b", "title-preview": "b preview"},
            ],
            commits,
        )

    def test_replace_request_reviewers(self):
        replace = mozphab.replace_reviewers
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
        replace = mozphab.replace_reviewers
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
        replace = mozphab.replace_reviewers
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

    @mock.patch("mozphab.ConduitAPI.get_revisions")
    @mock.patch("mozphab.logger")
    def test_show_commit_stack(self, mock_logger, m_get_revisions):
        class Repository:
            phab_url = "http://phab/"
            path = "x"
            api_url = "x"
            dot_path = "x"

        repo = Repository()
        mozphab.conduit.set_repo(repo)

        m_get_revisions.return_value = [{"fields": {"bugzilla.bug-id": "1"}}]

        mozphab.show_commit_stack([])
        self.assertFalse(mock_logger.info.called, "logger.info() shouldn't be called")
        self.assertFalse(
            mock_logger.warning.called, "logger.warning() shouldn't be called"
        )

        mozphab.show_commit_stack(
            [{"name": "aaa000", "title-preview": "A"}], validate=False
        )
        mock_logger.info.assert_called_with("(New) aaa000 A")
        self.assertFalse(
            mock_logger.warning.called, "logger.warning() shouldn't be called"
        )
        mock_logger.reset_mock()

        mozphab.show_commit_stack(
            [{"name": "aaa000", "title-preview": "A", "rev-id": "12", "bug-id": "1"}],
            validate=False,
        )
        mock_logger.info.assert_called_with("(D12) aaa000 A")
        self.assertFalse(
            mock_logger.warning.called, "logger.warning() shouldn't be called"
        )
        mock_logger.reset_mock()

        mozphab.show_commit_stack(
            [
                {"name": "aaa000", "title-preview": "A"},
                {"name": "bbb000", "title-preview": "B"},
            ],
            validate=False,
        )
        self.assertEqual(2, mock_logger.info.call_count)
        self.assertEqual(
            [mock.call("(New) bbb000 B"), mock.call("(New) aaa000 A")],
            mock_logger.info.call_args_list,
        )
        mock_logger.reset_mock()

        mozphab.show_commit_stack(
            [
                {
                    "name": "aaa000",
                    "title-preview": "A",
                    "bug-id-orig": 2,
                    "bug-id": 1,
                    "reviewers": {"granted": [], "request": ["one"]},
                }
            ],
            validate=True,
        )
        mock_logger.info.assert_called_with("(New) aaa000 A")
        mock_logger.warning.assert_called_with("!! Bug ID changed from 2 to 1")
        mock_logger.reset_mock()

        mozphab.show_commit_stack(
            [
                {
                    "name": "aaa000",
                    "title-preview": "A",
                    "bug-id-orig": None,
                    "reviewers": {"granted": [], "request": []},
                }
            ],
            validate=True,
        )
        mock_logger.warning.assert_called_with("!! Missing reviewers")
        mock_logger.reset_mock()

        mozphab.show_commit_stack(
            [{"name": "aaa000", "title-preview": "A", "rev-id": "123", "bug-id": "1"}],
            validate=False,
            show_rev_urls=True,
        )
        mock_logger.warning.assert_called_with("-> http://phab/D123")

        m_get_revisions.reset_mock()
        m_get_revisions.return_value = [
            {"id": "1", "phid": "PHID-1", "fields": {"bugzilla.bug-id": "1"}},
            {"id": "2", "phid": "PHID-2", "fields": {"bugzilla.bug-id": "1"}},
        ]
        # we're changing bug id in the first revision to 2
        mozphab.show_commit_stack(
            [
                {
                    "name": "aaa000",
                    "title-preview": "A",
                    "bug-id-orig": 1,
                    "bug-id": "2",
                    "rev-id": "1",
                    "reviewers": {"granted": [], "request": [""]},
                },
                {
                    "name": "bbb000",
                    "title-preview": "B",
                    "bug-id-orig": "1",
                    "bug-id": "1",
                    "rev-id": "2",
                    "reviewers": {"granted": [], "request": [""]},
                },
            ],
            validate=True,
        )
        assert mock_logger.warning.call_args_list[1] == mock.call(
            "!! Bug ID in Phabricator revision will be changed from 1 to 2"
        )
        assert m_get_revisions.call_count == 3
        mock_logger.reset_mock()

    @mock.patch("mozphab.update_commit_title_previews")
    def test_update_commits_from_args(self, m_update_title):
        m_update_title.side_effect = lambda x: x

        update = mozphab.update_commits_from_args

        class Args:
            def __init__(self, reviewer=None, blocker=None, bug=None):
                self.reviewer = reviewer
                self.blocker = blocker
                self.bug = bug

        _commits = [
            {"title": "A", "reviewers": dict(granted=[], request=[]), "bug-id": None},
            {"title": "B", "reviewers": dict(granted=[], request=["one"]), "bug-id": 1},
        ]

        # No change if noreviewer  args provided
        commits = copy.deepcopy(_commits)
        commits[1]["reviewers"]["granted"].append("two")
        with mock.patch("mozphab.config") as m_config:
            m_config.always_blocking = False
            update(commits, Args())
            self.assertEqual(
                commits,
                [
                    {
                        "title": "A",
                        "reviewers": dict(granted=[], request=[]),
                        "bug-id": None,
                    },
                    {
                        "title": "B",
                        "reviewers": dict(granted=["two"], request=["one"]),
                        "bug-id": 1,
                    },
                ],
            )

        # Adding and removing reviewers, forcing the bug ID
        commits = copy.deepcopy(_commits)
        update(commits, Args(reviewer=["two", "three"], bug=2))
        self.assertEqual(
            commits,
            [
                {
                    "title": "A",
                    "reviewers": dict(granted=["two", "three"], request=[]),
                    "bug-id": 2,
                },
                {
                    "title": "B",
                    "reviewers": dict(granted=["two", "three"], request=[]),
                    "bug-id": 2,
                },
            ],
        )

        # Removing duplicates
        commits = copy.deepcopy(_commits)
        update(
            commits,
            Args(
                reviewer=["Two", "two", "two!", "three", "Three", "THREE!"],
                blocker=["Two", "THREE!", "three", "two", "three"],
            ),
        )
        self.assertEqual(
            commits,
            [
                {
                    "title": "A",
                    "reviewers": dict(granted=["two!", "THREE!"], request=[]),
                    "bug-id": None,
                },
                {
                    "title": "B",
                    "reviewers": dict(granted=["two!", "THREE!"], request=[]),
                    "bug-id": 1,
                },
            ],
        )

        # Adding blocking reviewers via args
        commits = copy.deepcopy(_commits)
        commits[1]["reviewers"]["request"].append("three")
        commits[0]["reviewers"]["granted"].append("four")
        commits[0]["reviewers"]["granted"].append("five")
        update(
            commits, Args(reviewer=["one", "two!", "four"], blocker=["three", "four!"])
        )
        self.assertEqual(
            commits,
            [
                {
                    "title": "A",
                    "reviewers": dict(
                        granted=["one", "four!", "three!", "two!"], request=[]
                    ),
                    "bug-id": None,
                },
                {
                    "title": "B",
                    "reviewers": dict(
                        granted=["four!", "two!"], request=["one", "three!"]
                    ),
                    "bug-id": 1,
                },
            ],
        )

        # Forcing blocking reviewers
        commits = copy.deepcopy(_commits)
        commits[1]["reviewers"]["granted"].append("two")
        with mock.patch("mozphab.config") as m_config:
            m_config.always_blocking = True
            update(commits, Args())
            self.assertEqual(
                commits,
                [
                    {
                        "title": "A",
                        "reviewers": dict(granted=[], request=[]),
                        "bug-id": None,
                    },
                    {
                        "title": "B",
                        "reviewers": dict(granted=["two!"], request=["one!"]),
                        "bug-id": 1,
                    },
                ],
            )


class TestUpdateCommitSummary(unittest.TestCase):
    def test_update_revision_description(self):
        c = commit(
            rev_id="D123",
            title="hi!",
            body="hello!  µ-benchmarks are a thing.\n\n"
            "Differential Revision: http://phabricator.test/D123",
        )
        r = dict(fields=dict(title="", summary=""))
        t = []

        expected = [
            dict(type="title", value="hi!"),
            dict(type="summary", value="hello!  µ-benchmarks are a thing."),
        ]
        mozphab.update_revision_description(t, c, r)

        self.assertListEqual(t, expected)

    def test_update_revision_description_no_op(self):
        c = commit(
            rev_id="D123",
            title="hi!",
            body="hello!\n\nDifferential Revision: http://phabricator.test/D123",
        )
        r = dict(fields=dict(title="hi!", summary="hello!\n\n"))
        t = []

        expected = []
        mozphab.update_revision_description(t, c, r)

        self.assertListEqual(t, expected)

    @mock.patch("mozphab.ConduitAPI.get_users")
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
                dict(phid="PHID-USER-1"),
                dict(phid="PHID-USER-2"),
                dict(phid="PHID-USER-3"),
            ],
            [dict(phid="PHID-USER-1")],
            [dict(phid="PHID-USER-2"), dict(phid="PHID-USER-3")],
        )
        c = commit(rev_id="123", reviewers=[["alice", "bob!"], ["frankie!"]])
        t = []

        expected = [
            dict(
                type="reviewers.set",
                value=["PHID-USER-1", "blocking(PHID-USER-2)", "blocking(PHID-USER-3)"],
            )
        ]
        mozphab.update_revision_reviewers(t, c)

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
        self.assertEqual(None, mozphab.parse_api_error(api_response))

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
            mozphab.parse_api_error(api_response),
        )


if __name__ == "__main__":
    unittest.main()
