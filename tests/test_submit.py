import imp
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

        self._assertError(check, [commit(None, ["r"])])
        self._assertError(check, [commit("", ["r"])])
        self._assertError(check, [commit("1", [])])
        self._assertError(
            check, [commit("1", ["r"], body="Summary: blah\nReviewers: r")]
        )

        self._assertError(check, [commit("1", ["r"]), commit("", ["r"])])
        self._assertError(check, [commit("1", ["r"]), commit("1", [])])

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


if __name__ == "__main__":
    unittest.main()
