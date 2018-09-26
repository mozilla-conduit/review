import copy
import imp
import mock
import os
import sys
import unittest

review = imp.load_source(
    "review", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


def commit(bug_id=None, reviewers=None, body="", name="", title=""):
    return {
        "name": name,
        "title": title,
        "bug-id": bug_id,
        "reviewers": reviewers if reviewers else [],
        "body": body,
    }


# noinspection PyPep8Naming,PyBroadException
class Commits(unittest.TestCase):
    def _assertNoError(self, callableObj, *args):
        try:
            callableObj(*args)
        except review.Error:
            info = sys.exc_info()
            self.fail("%s raised" % repr(info[0]))

    def _assertError(self, callableObj, *args):
        try:
            callableObj(*args)
        except review.Error:
            return
        except Exception:
            info = sys.exc_info()
            self.fail("%s raised" % repr(info[0]))
        self.fail("%s failed to raise Error" % callableObj)

    def test_commit_validation(self):
        repo = review.Repository(None, None, "dummy")
        check = repo.check_commits_for_submit

        self._assertNoError(check, [])
        self._assertNoError(check, [commit("1", ["r"])])
        self._assertNoError(
            check, [commit("1", ["r1"]), commit("2", ["r1"]), commit("3", ["r1", "r2"])]
        )
        self._assertNoError(check, [commit("1", [])])
        self._assertNoError(check, [commit("1", ["r"]), commit("1", [])])

        self._assertError(check, [commit(None, ["r"])])
        self._assertError(check, [commit("", ["r"])])
        self._assertError(
            check, [commit("1", ["r"], body="Summary: blah\nReviewers: r")]
        )

        self._assertError(check, [commit("1", ["r"]), commit("", ["r"])])

    def test_commit_preview(self):
        build = review.build_commit_title

        self.assertEqual(
            "Bug 1, blah, r?turnip",
            build(commit("1", ["turnip"], title="bug 1, blah, r=turnip")),
        )
        self.assertEqual(
            "blah (Bug 1) r?turnip",
            build(commit("1", ["turnip"], title="blah (bug 1) r=turnip")),
        )
        self.assertEqual(
            "Bug 1 - blah r?turnip",
            build(commit("1", ["turnip"], title="blah r?turnip")),
        )

        self.assertEqual(
            "blah r?turnip", build(commit("", ["turnip"], title="blah r=turnip"))
        )
        self.assertEqual(
            "Bug 1 - blah", build(commit("1", [], title="Bug 1 - blah r=turnip"))
        )
        self.assertEqual(
            "Bug 1 - helper_bug2.html",
            build(commit("1", [], title="Bug 1 - helper_bug2.html")),
        )

    @mock.patch('review.build_commit_title')
    def test_update_commit_title_previews(self, m_build_commit_title):
        m_build_commit_title.side_effect = lambda x: x["title"] + " preview"
        commits = [dict(title="a"), dict(title="b")]
        review.update_commit_title_previews(commits)
        self.assertEqual(
            [
                {"title": "a", "title-preview": "a preview"},
                {"title": "b", "title-preview": "b preview"},
            ],
            commits,
        )

    def test_replace_reviewers(self):
        replace = review.replace_reviewers
        self.assertEqual("", replace("", []))
        self.assertEqual("Title", replace("Title", []))
        self.assertEqual("Title\n\nr?one", replace("Title\n\nr?one", []))
        self.assertEqual("r?one", replace("", ["one"]))
        self.assertEqual("r?one", replace("r?one", ["one"]))
        self.assertEqual("r?two", replace("r?two", ["one"]))
        self.assertEqual("r?one", replace("r?one", ["one", "two"]))
        self.assertEqual("r?one,two", replace("r?one,two", ["one"]))
        self.assertEqual("r?one,two", replace("", ["one", "two"]))
        self.assertEqual("Some Title r?one,two", replace("Some Title", ["one", "two"]))
        self.assertEqual(
            "Title r?one\n\nDescr\niption", replace("Title\n\nDescr\niption", ["one"])
        )
        self.assertEqual(
            "Title r?one,two\n\nr?two", replace("Title\n\nr?two", ["one", "two"])
        )

        self.assertEqual("Title", replace("Title r?one", []))
        self.assertEqual("Title", replace("Title r?one,two", []))
        self.assertEqual("Title", replace("Title r?one r?two", []))
        self.assertEqual("Title r?one", replace("Title r?one", ["one"]))
        self.assertEqual("Title r?one one", replace("Title r? one", ["one"]))
        self.assertEqual("Title r?one,two", replace("Title r?one,two", ["one", "two"]))
        self.assertEqual("Title r?one,two", replace("Title r?two", ["one", "two"]))
        self.assertEqual(
            "Title r?one,two", replace("Title r?one r?two", ["one", "two"])
        )
        self.assertEqual("Title r?one", replace("Title r=one", ["one"]))
        self.assertEqual("Title r?one", replace("Title r=one,two", ["one"]))
        self.assertEqual("Title r?one,two", replace("Title r=one,two", ["one", "two"]))
        self.assertEqual(
            "Title r?one,two", replace("Title r=one r=two", ["one", "two"])
        )
        self.assertEqual(
            "Title r?one,two", replace("Title r?one r=two", ["one", "two"])
        )

    @unittest.skip("These tests should pass we should fix the function")
    def test_badly_replaced_reviewers(self):
        replace = review.replace_reviewers
        # r? one
        self.assertEqual("r?one one", replace("r? one", ["one"]))
        # r?one,
        self.assertEqual("r?one", replace("r?one,", ["one"]))
        # Title r?one,two,,two
        self.assertEqual("Title r?one,two", replace("Title r?one,,two", ["one", "two"]))
        # r?one
        self.assertEqual("", replace("r?one", []))
        # r?one,two
        self.assertEqual("", replace("r?one,two", []))
        # r?one
        self.assertEqual("", replace("r?one r?two", []))
        # r?two
        self.assertEqual("r?one,two", replace("r?two", ["one", "two"]))
        # r?one r?one,two
        self.assertEqual("r?one,two", replace("r?one r?two", ["one", "two"]))
        # r=one
        self.assertEqual("r?one", replace("r=one", ["one"]))
        # r=one,two
        self.assertEqual("r?one", replace("r=one,two", ["one"]))
        # r=one,two
        self.assertEqual("r?one,two", replace("r=one,two", ["one", "two"]))
        # r=one, r?one,two
        self.assertEqual("r?one,two", replace("r=one r=two", ["one", "two"]))
        # r? one, r?one,two
        self.assertEqual("r?one,two", replace("r?one r=two", ["one", "two"]))

    @mock.patch("review.logger")
    def test_show_commit_stack(self, mock_logger):
        class Repository:
            phab_url = "http://phab/"

        repo = Repository()

        review.show_commit_stack(repo, [])
        self.assertFalse(mock_logger.info.called, "logger.info() shouldn't be called")
        self.assertFalse(
            mock_logger.warning.called, "logger.warning() shouldn't be called"
        )

        review.show_commit_stack(repo, [{"name": "aaa000", "title-preview": "A"}])
        mock_logger.info.assert_called_with("aaa000 A")
        self.assertFalse(
            mock_logger.warning.called, "logger.warning() shouldn't be called"
        )
        mock_logger.reset_mock()

        review.show_commit_stack(
            repo,
            [
                {"name": "aaa000", "title-preview": "A"},
                {"name": "bbb000", "title-preview": "B"},
            ],
        )
        self.assertEqual(2, mock_logger.info.call_count)
        self.assertEqual(
            [mock.call("bbb000 B"), mock.call("aaa000 A")],
            mock_logger.info.call_args_list,
        )
        mock_logger.reset_mock()

        review.show_commit_stack(
            repo,
            [
                {
                    "name": "aaa000",
                    "title-preview": "A",
                    "bug-id-orig": 2,
                    "bug-id": 1,
                    "reviewers": ["one"],
                }
            ],
            show_warnings=True,
        )
        mock_logger.info.assert_called_with("aaa000 A")
        mock_logger.warning.assert_called_with("!! Bug ID changed from 2 to 1")
        mock_logger.reset_mock()

        review.show_commit_stack(
            repo,
            [
                {
                    "name": "aaa000",
                    "title-preview": "A",
                    "bug-id-orig": None,
                    "reviewers": [],
                }
            ],
            show_warnings=True,
        )
        mock_logger.warning.assert_called_with("!! Missing reviewers")
        mock_logger.reset_mock()

        review.show_commit_stack(
            repo,
            [{"name": "aaa000", "title-preview": "A", "rev-id": "123"}],
            show_rev_urls=True,
        )
        mock_logger.warning.assert_called_with("-> http://phab/D123")

    @mock.patch("review.update_commit_title_previews")
    def test_update_commits_from_args(self, m_update_title):
        m_update_title.side_effect = lambda x: x

        update = review.update_commits_from_args

        class Args:
            def __init__(self, reviewer=None, blocker=None, bug=None):
                self.reviewer = reviewer
                self.blocker = blocker
                self.bug = bug

        _commits = [
            {"title": "A", "reviewers": [], "bug-id": None},
            {"title": "B", "reviewers": ["one"], "bug-id": 1},
        ]
        commits = copy.deepcopy(_commits)
        update(commits, Args(reviewer=["two", "three"], bug=2))
        self.assertEqual(
            commits,
            [
                {"title": "A", "reviewers": ["two", "three"], "bug-id": 2},
                {"title": "B", "reviewers": ["two", "three"], "bug-id": 2},
            ],
        )
        commits = copy.deepcopy(_commits)
        commits[1]["reviewers"].append("two")

        with mock.patch("review.config") as m_config:
            m_config.always_blocking = True
            update(commits, Args())
            self.assertEqual(
                commits,
                [
                    {"title": "A", "reviewers": [], "bug-id": None},
                    {"title": "B", "reviewers": ["one!", "two!"], "bug-id": 1},
                ],
            )


if __name__ == "__main__":
    unittest.main()
