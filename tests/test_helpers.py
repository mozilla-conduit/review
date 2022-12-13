# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import builtins
import datetime
import pytest
import subprocess
import unittest

from pathlib import Path
from unittest import mock

from mozphab.commands import submit
from mozphab import (
    conduit,
    detect_repository,
    environment,
    exceptions,
    helpers,
    mozphab,
    simplecache,
    subprocess_wrapper,
)


class Helpers(unittest.TestCase):
    @mock.patch("builtins.open")
    @mock.patch("mozphab.helpers.json")
    def test_read_json_field(self, m_json, m_open):
        m_open.side_effect = FileNotFoundError
        self.assertEqual(None, helpers.read_json_field(["nofile"], ["not existing"]))

        m_open.side_effect = NotADirectoryError
        with self.assertRaises(NotADirectoryError):
            helpers.read_json_field(["nofile"], ["not existing"])

        m_open.side_effect = ValueError()
        self.assertEqual(None, helpers.read_json_field(["nofile"], ["not existing"]))

        m_open.side_effect = None
        m_json.load.return_value = dict(a="value A", b=3)
        self.assertEqual(None, helpers.read_json_field(["filename"], ["not existing"]))
        self.assertEqual("value A", helpers.read_json_field(["filename"], ["a"]))

        m_json.load.side_effect = (
            dict(a="value A", b=3),
            dict(b="value B", c=dict(a="value CA")),
        )
        self.assertEqual(3, helpers.read_json_field(["file_a", "file_b"], ["b"]))
        m_json.load.side_effect = (
            dict(b="value B", c=dict(a="value CA")),
            dict(a="value A", b=3),
        )
        self.assertEqual(
            "value B", helpers.read_json_field(["file_b", "file_a"], ["b"])
        )
        m_json.load.side_effect = (
            dict(a="value A", b=3),
            dict(b="value B", c=dict(a="value CA")),
        )
        self.assertEqual(
            "value CA", helpers.read_json_field(["file_a", "file_b"], ["c", "a"])
        )

    @mock.patch.object(builtins, "input")
    @mock.patch("mozphab.mozphab.sys")
    def test_prompt(self, m_sys, m_input):
        input_response = None

        def _input(_):
            return input_response

        m_input.side_effect = _input

        # Default
        input_response = ""
        self.assertEqual("AAA", helpers.prompt("", ["AAA", "BBB"]))

        # Escape
        m_sys.exit.side_effect = SystemExit()
        with self.assertRaises(SystemExit):
            input_response = chr(27)
            helpers.prompt("", ["AAA"])

        with self.assertRaises(SystemExit):
            input_response = chr(27)
            helpers.prompt("")

        input_response = "aaa"
        self.assertEqual("AAA", helpers.prompt("", ["AAA", "BBB"]))
        input_response = "a"
        self.assertEqual("AAA", helpers.prompt("", ["AAA", "BBB"]))
        input_response = "b"
        self.assertEqual("BBB", helpers.prompt("", ["AAA", "BBB"]))
        input_response = "abc"
        self.assertEqual("abc", helpers.prompt(""))

    @mock.patch("mozphab.detect_repository.probe_repo")
    def test_repo_from_args(self, m_probe):
        # TODO test walking the path
        repo = None

        def probe_repo(_):
            return repo

        m_probe.side_effect = probe_repo

        class Args:
            def __init__(self, path=None):
                self.path = path

        with self.assertRaises(exceptions.Error):
            detect_repository.repo_from_args(Args(path="some path"))

        repo = mock.MagicMock()
        args = Args(path="some path")
        self.assertEqual(repo, mozphab.repo_from_args(args))
        repo.set_args.assert_called_once_with(args)

    def test_strip_differential_revision_from_commit_body(self):
        self.assertEqual("", helpers.strip_differential_revision("\n\n"))
        self.assertEqual(
            "",
            helpers.strip_differential_revision(
                "\nDifferential Revision: http://phabricator.test/D123"
            ),
        )
        self.assertEqual(
            "",
            helpers.strip_differential_revision(
                "Differential Revision: http://phabricator.test/D123"
            ),
        )
        self.assertEqual(
            "title",
            helpers.strip_differential_revision(
                "title\nDifferential Revision: http://phabricator.test/D123"
            ),
        )
        self.assertEqual(
            "title",
            helpers.strip_differential_revision(
                "title\n\nDifferential Revision: http://phabricator.test/D123"
            ),
        )
        self.assertEqual(
            "title\n\nsummary",
            helpers.strip_differential_revision(
                "title\n\n"
                "summary\n\n"
                "Differential Revision: http://phabricator.test/D123"
            ),
        )

    def test_amend_commit_message_body_with_new_revision_url(self):
        self.assertEqual(
            "\nDifferential Revision: http://phabricator.test/D123",
            submit.amend_revision_url("", "http://phabricator.test/D123"),
        )
        self.assertEqual(
            "title\n\nDifferential Revision: http://phabricator.test/D123",
            submit.amend_revision_url("title", "http://phabricator.test/D123"),
        )
        self.assertEqual(
            "\nDifferential Revision: http://phabricator.test/D123",
            submit.amend_revision_url(
                "\nDifferential Revision: http://phabricator.test/D999",
                "http://phabricator.test/D123",
            ),
        )

    @mock.patch("mozphab.helpers.os.access")
    @mock.patch("mozphab.helpers.os.path")
    @mock.patch("mozphab.helpers.which")
    def test_which_path(self, m_which, m_os_path, m_os_access):
        m_os_path.exists.side_effect = (True, False)
        m_os_access.return_value = True
        m_os_path.isdir.return_value = False

        path = "x"
        self.assertEqual(path, helpers.which_path(path))
        m_which.assert_not_called()
        helpers.which_path(path)
        m_which.assert_called_once_with(path)


@mock.patch("mozphab.conduit.ConduitAPI.call")
def test_valid_reviewers_in_phabricator_returns_no_errors(call_conduit):
    # See https://phabricator.services.mozilla.com/api/user.search
    call_conduit.side_effect = (
        # user.query
        [{"userName": "alice", "phid": "PHID-USER-1"}],
        # project.search
        {
            "data": [{"fields": {"slug": "user-group"}, "phid": "PHID-PROJ-1"}],
            "maps": {
                "slugMap": {
                    "alias1": {"slug": "name1", "projectPHID": "PHID-PROJ-2"},
                    "#alias2": {"slug": "name2", "projectPHID": "PHID-PROJ-3"},
                }
            },
        },
    )
    reviewers = dict(granted=[], request=["alice", "#user-group", "#alias1", "#alias2"])
    assert [] == conduit.conduit.check_for_invalid_reviewers(reviewers)


@mock.patch("mozphab.conduit.ConduitAPI.call")
def test_disabled_reviewers(call_conduit):
    reviewers = dict(granted=[], request=["alice", "goober"])
    call_conduit.side_effect = (
        # user.query
        [
            dict(userName="alice", phid="PHID-USER-1"),
            dict(userName="goober", phid="PHID-USER-2", roles=["disabled"]),
        ],
        # project.search
        {"data": [], "maps": {"slugMap": {}}},
    )
    expected_errors = [dict(name="goober", disabled=True)]
    assert expected_errors == conduit.conduit.check_for_invalid_reviewers(reviewers)


@mock.patch("mozphab.conduit.ConduitAPI.call")
def test_non_existent_reviewers_or_groups_generates_error_list(call_conduit):
    ts = 1543622400
    ts_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    reviewers = dict(
        granted=[],
        request=[
            "alice",
            "goober",
            "goozer",
            "#user-group",
            "#goo-group",
            "#gon-group",
        ],
    )
    call_conduit.side_effect = (
        # user.query
        [
            dict(userName="alice", phid="PHID-USER-1"),
            dict(
                userName="goober",
                phid="PHID-USER-2",
                currentStatus="away",
                currentStatusUntil=ts,
            ),
        ],
        # project.search
        {
            "data": [{"fields": {"slug": "user-group"}, "phid": "PHID-PROJ-1"}],
            "maps": {"slugMap": {}},
        },
    )
    expected_errors = [
        dict(name="#gon-group"),
        dict(name="#goo-group"),
        dict(name="goober", until=ts_str),
        dict(name="goozer"),
    ]

    errors = conduit.conduit.check_for_invalid_reviewers(reviewers)
    errors.sort(key=lambda k: k["name"])
    assert expected_errors == errors


@mock.patch("mozphab.conduit.ConduitAPI.call")
def test_reviewer_case_sensitivity(call_conduit):
    reviewers = dict(granted=[], request=["Alice", "#uSeR-gRoUp"])
    call_conduit.side_effect = (
        # See https://phabricator.services.mozilla.com/conduit/method/user.query/
        [dict(userName="alice", phid="PHID-USER-1")],
        # See https://phabricator.services.mozilla.com/conduit/method/project.search/
        {
            "data": [{"fields": {"slug": "user-group"}, "phid": "PHID-PROJ-1"}],
            "maps": {"slugMap": {}},
        },
    )
    assert [] == conduit.conduit.check_for_invalid_reviewers(reviewers)


def test_get_users_no_users():
    conduit = mozphab.conduit
    assert [] == conduit.get_users([])


@mock.patch("mozphab.conduit.ConduitAPI.call")
def test_get_users_with_user(m_conduit):
    conduit = mozphab.conduit

    user = {"userName": "alice", "phid": "PHID-USER-1"}
    m_conduit.return_value = [user]
    assert [user] == conduit.get_users(["alice"])
    m_conduit.assert_called_once()

    conduit.get_users(["alice"])
    m_conduit.assert_called_once()

    simplecache.cache.reset()
    m_conduit.reset_mock()
    m_conduit.return_value = []
    assert [] == conduit.get_users(["alice"])
    m_conduit.assert_called_once()


def test_simple_cache():
    cache = simplecache.SimpleCache()
    assert cache.get("nothing") is None

    cache.set("something", 123)
    assert cache.get("something") == 123

    assert cache.get("SoMeThInG") == 123

    cache.set("something", "foo")
    assert cache.get("something") == "foo"

    cache.delete("something")
    assert cache.get("something") is None


@mock.patch("subprocess.check_output")
@mock.patch("mozphab.subprocess_wrapper.logger")
def test_check_output(m_logger, m_check_output):
    m_check_output.side_effect = subprocess.CalledProcessError(
        cmd=["some", "cmd"], returncode=2, output="output msg", stderr="stderr msg"
    )
    with pytest.raises(exceptions.CommandError) as e:
        subprocess_wrapper.check_output(["command"])

    assert e.value.status == 2
    assert str(e.value).startswith("command 'command'")
    assert mock.call("stderr msg") in m_logger.debug.call_args_list
    assert mock.call("output msg") in m_logger.debug.call_args_list

    m_check_output.side_effect = ("response \nline \n",)
    assert ["response ", "line"] == subprocess_wrapper.check_output(["command"])

    m_check_output.side_effect = ("response \nline \n",)
    assert ["response ", "line "] == subprocess_wrapper.check_output(
        ["command"], strip=False
    )

    m_check_output.side_effect = ("response \nline \n",)
    assert "response \nline" == subprocess_wrapper.check_output(
        ["command"], split=False
    )


def test_git_find_repo(git_repo_path):
    path = str(git_repo_path)
    assert path == detect_repository.find_repo_root(path)
    subdir = git_repo_path / "test_dir"
    subdir.mkdir()
    assert path == detect_repository.find_repo_root(str(subdir))


def test_hg_find_repo(hg_repo_path):
    path = str(hg_repo_path)
    assert path == detect_repository.find_repo_root(path)


def test_fail_find_repo():
    path = "/non/existing/path"
    assert detect_repository.find_repo_root(path) is None


@mock.patch("mozphab.detect_repository.Mercurial")
@mock.patch("mozphab.detect_repository.Git")
def test_probe_repo(m_git, m_hg):
    m_hg.return_value = "HG"

    assert "HG" == detect_repository.probe_repo("path")

    m_hg.side_effect = ValueError
    m_git.return_value = "GIT"
    assert "GIT" == detect_repository.probe_repo("path")

    m_git.side_effect = ValueError
    assert detect_repository.probe_repo("path") is None


@mock.patch("mozphab.detect_repository.probe_repo")
def test_repo_from_args(m_probe):
    # TODO test walking the path
    repo = None

    def probe_repo(path):
        return repo

    m_probe.side_effect = probe_repo

    class Args:
        def __init__(self, path=None):
            self.path = path

    with pytest.raises(exceptions.Error):
        detect_repository.repo_from_args(Args(path="some path"))

    repo = mock.MagicMock()
    args = Args(path="some path")
    assert repo == detect_repository.repo_from_args(args)
    repo.set_args.assert_called_once_with(args)


def test_parse_config():
    res = helpers.parse_config(
        ["key=value 1", "key2 = value2 ", "key3=", "key4=one=two=three"]
    )
    assert res == dict(key="value 1", key2="value2", key3="", key4="one=two=three")


def test_parse_config_key_only():
    assert helpers.parse_config(["key"]) == dict()


def test_parse_config_with_filter():
    def _filter(name, value):
        if name != "out":
            return True

    res = helpers.parse_config(["imin=I'm in", "out=not here"], _filter)
    assert res == dict(imin="I'm in")


@mock.patch("os.path.expanduser")
@mock.patch("os.path.join")
@mock.patch("os.path.isfile")
@mock.patch("os.stat")
@mock.patch("os.chmod")
@mock.patch("os.getenv")
def test_get_arcrc_path(m_getenv, m_chmod, m_stat, m_isfile, m_join, m_expand):
    arcrc = helpers.get_arcrc_path

    m_expand.return_value = "arcrc file"
    m_isfile.return_value = False
    arcrc()
    m_chmod.assert_not_called()

    class Stat:
        st_mode = 0o100600

    stat = Stat()
    m_stat.return_value = stat
    m_isfile.return_value = True

    m_chmod.reset_mock()
    simplecache.cache.reset()
    arcrc()
    m_chmod.assert_not_called()

    m_chmod.reset_mock()
    m_getenv.reset_mock()
    m_join.reset_mock()
    m_getenv.side_effect = ("/app_data",)
    stat.st_mode = 0o100640
    simplecache.cache.reset()
    arcrc()
    if environment.IS_WINDOWS:
        m_getenv.assert_called_once_with("APPDATA", "")
        m_join.assert_called_once_with("/app_data", ".arcrc")
    else:
        m_chmod.assert_called_once_with("arcrc file", 0o600)


def test_short_node():
    assert (
        helpers.short_node("b016b6080ff9fa6d9ac459950e24bdcdaa939be0") == "b016b6080ff9"
    )
    assert (
        helpers.short_node("this-is-not-a-sha-this-is-not-a-sha-test")
        == "this-is-not-a-sha-this-is-not-a-sha-test"
    )
    assert helpers.short_node("b016b6080ff9") == "b016b6080ff9"
    assert helpers.short_node("b016b60") == "b016b60"
    assert helpers.short_node("mozilla-central") == "mozilla-central"


def test_temporary_file_unicode():
    message = "ćwikła"
    with helpers.temporary_file(message) as fname:
        with Path(fname).open(encoding="utf-8") as f:
            assert f.readline() == message


@pytest.mark.parametrize("prefix", ("+", "-", " "))
@pytest.mark.parametrize("linesep", ("\n", "\r\n"))
class TestCreateHunkLines:
    def test_create_hunk_lines_empty_body_do_not_check_eof(self, prefix, linesep):
        """Expect no lines when provided a completely empty file."""
        lines, eof_has_no_new_line = helpers.create_hunk_lines(
            body="", prefix=prefix, check_eof=False
        )
        assert lines == []
        assert eof_has_no_new_line is None

    def test_create_hunk_lines_empty_body_check_eof(self, prefix, linesep):
        """Expect "no newline message" when provided a completely empty file."""
        lines, eof_had_no_newline = helpers.create_hunk_lines(
            body="", prefix=prefix, check_eof=True
        )
        if prefix != "+":
            assert lines == [
                f"\\ No newline at end of file\n",
            ]
            assert eof_had_no_newline
        else:
            assert lines == []
            assert eof_had_no_newline is None

    def test_create_hunk_lines_empty_line_do_not_check_eof(self, prefix, linesep):
        """Expect a prefixed line to be returned and the EOF check to not proceed.

        NOTE: Though this functionality is currently implemented, it does not match
        what Mercurial does with new, empty files that do not have any content. In those
        cases, Mercurial does not provide any hunks at all regardless if a newline
        characters exists or does not exist at the end of the file.
        """
        lines, eof_has_no_new_line = helpers.create_hunk_lines(
            body=f"{linesep}", prefix=prefix, check_eof=False
        )
        assert lines == [f"{prefix}{linesep}"]
        assert eof_has_no_new_line is None

    def test_create_hunk_lines_empty_line_check_eof(self, prefix, linesep):
        """Expect a prefixed line to be returned, and the EOF check to pass.

        NOTE: Though this functionality is currently implemented, it does not match
        what Mercurial does with new, empty files that do not have any content. In those
        cases, Mercurial does not provide any hunks at all regardless if a newline
        characters exists or does not exist at the end of the file.
        """
        lines, eof_had_no_newline = helpers.create_hunk_lines(
            body=f"{linesep}", prefix=prefix, check_eof=True
        )
        assert lines == [f"{prefix}{linesep}"]
        assert not eof_had_no_newline

    def test_create_hunk_lines_no_newline_eof_do_not_check_eof(self, prefix, linesep):
        """Expect a list of two lines, the last line having no line separator."""
        lines, eof_had_no_newline = helpers.create_hunk_lines(
            body=f"hello{linesep}world", prefix=prefix, check_eof=False
        )
        assert lines == [
            f"{prefix}hello{linesep}",
            f"{prefix}world",
        ]
        assert eof_had_no_newline is None

    def test_create_hunk_lines_no_newline_eof_check_eof(self, prefix, linesep):
        """Expect a list of three lines, all having line separators.

        NOTE: The last message is appended as an extra line, to match with what
        Mercurial outputs when showing diffs.
        """
        lines, eof_had_no_newline = helpers.create_hunk_lines(
            body=f"hello{linesep}world", prefix=prefix, check_eof=True
        )
        assert lines == [
            f"{prefix}hello{linesep}",
            f"{prefix}world\n",
            f"\\ No newline at end of file\n",
        ]
        assert eof_had_no_newline

    def test_create_hunk_lines_disallowed_prefix(self, prefix, linesep):
        """Expect an exception to be raised when a disallowed prefix is provided"""
        with pytest.raises(ValueError):
            helpers.create_hunk_lines("hello world", "*")


class TestSplitLines:
    """Tests the `helpers.split_lines` method.

    The tests here are meant to illustrate the functionality of `helpers.split_lines`
    method, as there are a few special cases (most notably when an input string begins
    or ends with a newline character.) The tests cover POSIX and DOS style line
    terminators, as well as when an input contains a combination of both."""

    def test_empty_body(self):
        """Expect a single, empty string entry when given an empty body."""
        body = ""
        expected = [""]
        actual = helpers.split_lines(body)
        assert actual == expected

    def test_single_line_no_eof_linesep(self):
        """Expect a single string entry when provided with a string with no newlines."""
        body = "line1"
        expected = ["line1"]
        actual = helpers.split_lines(body)
        assert actual == expected

    def test_two_lines_no_eof_linesep_posix(self):
        """Expect a list of lines and newline characters when given multiple lines."""
        body = "line1\nline2"
        expected = ["line1", "\n", "line2"]
        actual = helpers.split_lines(body)
        assert actual == expected

    def test_two_lines_eof_linesep_posix(self):
        """Expect last entry to be empty string when input is newline terminated."""
        body = "line1\nline2\n"
        expected = ["line1", "\n", "line2", "\n", ""]
        actual = helpers.split_lines(body)
        assert actual == expected

    def test_two_lines_no_eof_linesep_dos(self):
        """Test two lines without EOF CRLF."""
        body = "line1\r\nline2"
        expected = ["line1", "\r\n", "line2"]
        actual = helpers.split_lines(body)
        assert actual == expected

    def test_two_lines_eof_linesep_dos(self):
        """Test two lines with CRLF line terminators. Last entry should be empty."""
        body = "line1\r\nline2\r\n"
        expected = ["line1", "\r\n", "line2", "\r\n", ""]
        actual = helpers.split_lines(body)
        assert actual == expected

    def test_three_lines_mix_linesep_no_eof_linesep(self):
        """Test a mix of dos and posix line separators. Result should include both."""
        body = "line1\r\nline2\nline3"
        expected = ["line1", "\r\n", "line2", "\n", "line3"]
        actual = helpers.split_lines(body)
        assert actual == expected

    def test_three_lines_mix_linesep_eof_linesep(self):
        """Test a mix of line separators with EOF CRLF. Last entry should be empty."""
        body = "line1\r\nline2\nline3\r\n"
        expected = ["line1", "\r\n", "line2", "\n", "line3", "\r\n", ""]
        actual = helpers.split_lines(body)
        assert actual == expected

    def test_three_newline_characters(self):
        """Test three empty lines. First and last entries should be empty."""
        body = "\n\n\n"
        expected = ["", "\n", "", "\n", "", "\n", ""]
        actual = helpers.split_lines(body)
        assert actual == expected

        body = "\n\r\n\n"
        expected = ["", "\n", "", "\r\n", "", "\n", ""]
        actual = helpers.split_lines(body)
        assert actual == expected

        body = "\r\n\r\n\r\n"
        expected = ["", "\r\n", "", "\r\n", "", "\r\n", ""]
        actual = helpers.split_lines(body)
        assert actual == expected

    def test_one_line_followed_by_newline_characters(self):
        """Test one line followed by newlines. Last entry should be empty."""
        body = "line1\n\n\n"
        expected = ["line1", "\n", "", "\n", "", "\n", ""]
        actual = helpers.split_lines(body)
        assert actual == expected

    def test_form_feed_character_mixed_in(self):
        """Test handling of form feed character."""
        body = "line1\nline2\fstill on line2\nline3\n"
        expected = ["line1", "\n", "line2\fstill on line2", "\n", "line3", "\n", ""]
        actual = helpers.split_lines(body)
        assert actual == expected


class TestJoinLineseps:
    def test_empty_list(self):
        """Empty list of lines should return empty list."""
        lines = []
        expected = []
        actual = helpers.join_lineseps(lines)
        assert expected == actual

    def test_one_line(self):
        """A list with a single entry should return the same entry back."""
        lines = ["line1"]
        expected = ["line1"]
        actual = helpers.join_lineseps(lines)
        assert expected == actual

    def test_one_line_with_newline(self):
        """A list of two entries should return one entry with the joined string."""
        lines = ["line1", "\n"]
        expected = ["line1\n"]
        actual = helpers.join_lineseps(lines)
        assert expected == actual

    def test_two_lines_no_eof_newline(self):
        """A list of three entries should return the first two joined and the last."""
        lines = ["line1", "\n", "line2"]
        expected = ["line1\n", "line2"]
        actual = helpers.join_lineseps(lines)
        assert expected == actual

    def test_two_lines_with_eof_newline(self):
        """A list of four entries should return a list of two joined strings."""
        lines = ["line1", "\n", "line2", "\n"]
        expected = ["line1\n", "line2\n"]
        actual = helpers.join_lineseps(lines)
        assert expected == actual

    def test_two_empty_lines(self):
        """A list of four entries should return a list of two joined strings.

        This test is basically the same test as above, but added to illustrate the usage
        of this method to join empty lines.
        """
        lines = ["", "\n", "", "\n"]
        expected = ["\n", "\n"]
        actual = helpers.join_lineseps(lines)
        assert expected == actual

    def test_form_feed_character(self):
        """Form feed character should be treated like any other character."""
        lines = ["line1", "\n", "line2\fstill on line2"]
        expected = ["line1\n", "line2\fstill on line2"]
        actual = helpers.join_lineseps(lines)
        assert expected == actual


def test_augment_commits_from_body():
    # The actual parsing is tested in test_commit_parsing; this tests that the commits
    # structure is updated correctly.

    commits = [
        {
            "title": "Bug 1 - test r?reviewer",
            "body": "Differential Revision: https://example.com/D101",
        },
        {
            "title": "WIP: Bug 2 - blah r=blocker!",
            "body": "Differential Revision: https://example.com/D102",
        },
    ]
    helpers.augment_commits_from_body(commits)

    assert commits[0]["rev-id"] == "101"
    assert commits[0]["bug-id"] == "1"
    assert commits[0]["bug-id-orig"] == "1"
    assert commits[0]["reviewers"]["request"] == ["reviewer"]
    assert commits[0]["reviewers"]["granted"] == []
    assert commits[0]["title-preview"] == "Bug 1 - test r?reviewer"
    assert not commits[0]["wip"]

    assert commits[1]["rev-id"] == "102"
    assert commits[1]["bug-id"] == "2"
    assert commits[1]["bug-id-orig"] == "2"
    assert commits[1]["reviewers"]["request"] == []
    assert commits[1]["reviewers"]["granted"] == ["blocker!"]
    assert commits[1]["title-preview"] == "WIP: Bug 2 - blah r=blocker!"
    assert commits[1]["wip"]


def test_move_drev_to_original():
    # Ensure the arguments are returned as-is when `rev_id` is `None`.
    assert helpers.move_drev_to_original("blah", None) == (
        "blah",
        None,
    ), "Passing `None` for `rev_id` should return the arguments."

    # Ensure `Differential Revision` is moved to `Original` and `rev_id` is wiped.
    commit_message = (
        "bug 1: title r?reviewer\n"
        "\n"
        "Differential Revision: http://phabricator.test/D1"
    )
    expected = (
        "bug 1: title r?reviewer\n" "\n" "Original Revision: http://phabricator.test/D1"
    )
    message, rev_id = helpers.move_drev_to_original(commit_message, 1)
    assert (
        message == expected
    ), "`Differential Revision` not re-written to `Original Revision` on uplift."
    assert rev_id is None, "`rev_id` not returned as `None` for new uplift."

    # Ensure `Original` and `Differential` in commit message is recognized as update.
    commit_message = (
        "bug 1: title r?reviewer\n"
        "\n"
        "Original Revision: http://phabricator.test/D1"
        "\n"
        "Differential Revision: http://phabricator.test/D2"
    )
    message, rev_id = helpers.move_drev_to_original(commit_message, 2)
    assert (
        message == commit_message
    ), "Commit message should not have changed when updating an uplift."
    assert rev_id == 2, "`rev_id` should not have changed when updating an uplift."


def test_validate_email():
    invalid_email = "Test User"
    valid_email = "test@mozilla.com"

    assert not helpers.is_valid_email(invalid_email)
    assert helpers.is_valid_email(valid_email)
