import errno
import exceptions
import imp
import json
import mock
import os
import sys
import unittest

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


class Helpers(unittest.TestCase):
    @mock.patch("__builtin__.open")
    @mock.patch("mozphab.json")
    def test_read_json_field(self, m_json, m_open):
        m_open.side_effect = IOError(errno.ENOENT, "Not a file")
        self.assertEqual(None, mozphab.read_json_field(["nofile"], ["not existing"]))

        m_open.side_effect = IOError(errno.ENOTDIR, "Not a directory")
        with self.assertRaises(IOError):
            mozphab.read_json_field(["nofile"], ["not existing"])

        m_open.side_effect = ValueError()
        self.assertEqual(None, mozphab.read_json_field(["nofile"], ["not existing"]))

        m_open.side_effect = None
        m_json.load.return_value = dict(a="value A", b=3)
        self.assertEqual(None, mozphab.read_json_field(["filename"], ["not existing"]))
        self.assertEqual("value A", mozphab.read_json_field(["filename"], ["a"]))

        m_json.load.side_effect = (
            dict(a="value A", b=3),
            dict(b="value B", c=dict(a="value CA")),
        )
        self.assertEqual(3, mozphab.read_json_field(["file_a", "file_b"], ["b"]))
        m_json.load.side_effect = (
            dict(b="value B", c=dict(a="value CA")),
            dict(a="value A", b=3),
        )
        self.assertEqual("value B", mozphab.read_json_field(["file_b", "file_a"], ["b"]))
        m_json.load.side_effect = (
            dict(a="value A", b=3),
            dict(b="value B", c=dict(a="value CA")),
        )
        self.assertEqual(
            "value CA", mozphab.read_json_field(["file_a", "file_b"], ["c", "a"])
        )

    @mock.patch("__builtin__.termios", create=True)
    @mock.patch("__builtin__.tty", create=True)
    @mock.patch("mozphab.sys")
    @unittest.skip(
        "Figure out the way to mock termios and tty imported within function"
    )
    def test_get_char(self, m_sys, m_tty, m_termios):
        m_sys.stdin.read.return_value = "x"
        self.assertEqual("x", mozphab.get_char())
        m_termios.tcgetattr.assert_called_once()
        m_tty.setcbreak.assert_called_once()

    @mock.patch("mozphab.get_char")
    @mock.patch("mozphab.sys")
    def test_prompt(self, m_sys, m_get_char):
        char = None

        def get_char():
            return char

        m_get_char.side_effect = get_char

        # Return key
        char = chr(10)
        self.assertEqual("AAA", mozphab.prompt("", ["AAA", "BBB"]))
        m_sys.stdout.write.assert_called_with("AAA\n")

        # ^C, Escape
        char = chr(13)
        self.assertEqual("AAA", mozphab.prompt("", ["AAA", "BBB"]))
        m_sys.stdout.write.assert_called_with("AAA\n")

        m_sys.exit.side_effect = SystemExit()
        with self.assertRaises(SystemExit):
            char = chr(3)
            mozphab.prompt("", ["AAA"])
        m_sys.stdout.write.assert_called_with("^C\n")

        with self.assertRaises(SystemExit):
            char = chr(27)
            mozphab.prompt("", ["AAA"])
        m_sys.stdout.write.assert_called_with("^C\n")

        char = "b"
        self.assertEqual("BBB", mozphab.prompt("", ["AAA", "BBB"]))
        m_sys.stdout.write.assert_called_with("BBB\n")

    @mock.patch("mozphab.probe_repo")
    def test_repo_from_args(self, m_probe):
        # TODO test walking the path
        repo = None

        def probe_repo(path):
            return repo

        m_probe.side_effect = probe_repo

        class Args:
            def __init__(self, path=None):
                self.path = path

        with self.assertRaises(mozphab.Error):
            mozphab.repo_from_args(Args(path="some path"))

        repo = mock.MagicMock()
        args = Args(path="some path")
        self.assertEqual(repo, mozphab.repo_from_args(args))
        repo.set_args.assert_called_once_with(args)

    def test_arc_message(self):
        self.assertEqual(
            "Title\n\nSummary:\nMessage\n\n\n\nTest Plan:\n\n"
            "Reviewers: reviewer\n\nSubscribers:\n\nBug #: 1",
            mozphab.arc_message(
                dict(title="Title", body="Message", reviewers="reviewer", bug_id=1)
            ),
        )

        self.assertEqual(
            "Title\n\nSummary:\nMessage\n\nDepends on D123\n\nTest Plan:\n\n"
            "Reviewers: reviewer\n\nSubscribers:\n\nBug #: 1",
            mozphab.arc_message(
                dict(
                    title="Title",
                    body="Message",
                    reviewers="reviewer",
                    bug_id=1,
                    depends_on="Depends on D123",
                )
            ),
        )

        self.assertEqual(
            "\n\nSummary:\n\n\n\n\nTest Plan:\n\nReviewers: \n\nSubscribers:\n\nBug #: ",
            mozphab.arc_message(
                dict(title=None, body=None, reviewers=None, bug_id=None)
            ),
        )

    def test_strip_differential_revision_from_commit_body(self):
        self.assertEqual("", mozphab.strip_differential_revision("\n\n"))
        self.assertEqual(
            "",
            mozphab.strip_differential_revision(
                "\nDifferential Revision: http://phabricator.test/D123"
            ),
        )
        self.assertEqual(
            "",
            mozphab.strip_differential_revision(
                "Differential Revision: http://phabricator.test/D123"
            ),
        )
        self.assertEqual(
            "title",
            mozphab.strip_differential_revision(
                "title\nDifferential Revision: http://phabricator.test/D123"
            ),
        )
        self.assertEqual(
            "title",
            mozphab.strip_differential_revision(
                "title\n\nDifferential Revision: http://phabricator.test/D123"
            ),
        )
        self.assertEqual(
            "title\n\nsummary",
            mozphab.strip_differential_revision(
                "title\n\nsummary\n\nDifferential Revision: http://phabricator.test/D123"
            ),
        )

    def test_amend_commit_message_body_with_new_revision_url(self):
        self.assertEqual(
            "\nDifferential Revision: http://phabricator.test/D123",
            mozphab.amend_revision_url("", "http://phabricator.test/D123"),
        )
        self.assertEqual(
            "title\n\nDifferential Revision: http://phabricator.test/D123",
            mozphab.amend_revision_url("title", "http://phabricator.test/D123"),
        )
        self.assertEqual(
            "\nDifferential Revision: http://phabricator.test/D123",
            mozphab.amend_revision_url(
                "\nDifferential Revision: http://phabricator.test/D999",
                "http://phabricator.test/D123",
            ),
        )

    @mock.patch("mozphab.os.access")
    @mock.patch("mozphab.os.path")
    @mock.patch("mozphab.os.environ")
    def test_which(self, m_os_environ, m_os_path, m_os_access):
        m_os_environ.get.return_value = "/one:/two"
        m_os_path.expanduser = lambda x: x
        m_os_path.normcase = lambda x: x
        m_os_path.join = lambda x, y: "%s/%s" % (x, y)
        m_os_path.exists.side_effect = (False, True)
        m_os_access.return_value = True
        m_os_path.isdir.return_value = False

        path = "x"
        self.assertEqual("/two/x", mozphab.which(path))

    @mock.patch("mozphab.os.access")
    @mock.patch("mozphab.os.path")
    @mock.patch("mozphab.os.environ")
    @mock.patch("mozphab.which")
    def test_which(self, m_which, m_os_environ, m_os_path, m_os_access):
        m_os_path.exists.side_effect = (True, False)
        m_os_access.return_value = True
        m_os_path.isdir.return_value = False

        path = "x"
        self.assertEqual(path, mozphab.which_path(path))
        m_which.assert_not_called()
        mozphab.which_path(path)
        m_which.assert_called_once_with(path)
