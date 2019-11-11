import builtins
import datetime
import imp
import json
import mock
import os
import pytest
import subprocess
import unittest

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)
mozphab.SHOW_SPINNER = False


class Helpers(unittest.TestCase):
    @mock.patch("builtins.open")
    @mock.patch("mozphab.json")
    def test_read_json_field(self, m_json, m_open):
        m_open.side_effect = FileNotFoundError
        self.assertEqual(None, mozphab.read_json_field(["nofile"], ["not existing"]))

        m_open.side_effect = NotADirectoryError
        with self.assertRaises(NotADirectoryError):
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
        self.assertEqual(
            "value B", mozphab.read_json_field(["file_b", "file_a"], ["b"])
        )
        m_json.load.side_effect = (
            dict(a="value A", b=3),
            dict(b="value B", c=dict(a="value CA")),
        )
        self.assertEqual(
            "value CA", mozphab.read_json_field(["file_a", "file_b"], ["c", "a"])
        )

    @mock.patch.object(builtins, "input")
    @mock.patch("mozphab.sys")
    def test_prompt(self, m_sys, m_input):
        input_response = None

        def _input(_):
            return input_response

        m_input.side_effect = _input

        # Default
        input_response = ""
        self.assertEqual("AAA", mozphab.prompt("", ["AAA", "BBB"]))

        # Escape
        m_sys.exit.side_effect = SystemExit()
        with self.assertRaises(SystemExit):
            input_response = chr(27)
            mozphab.prompt("", ["AAA"])

        with self.assertRaises(SystemExit):
            input_response = chr(27)
            mozphab.prompt("")

        input_response = "aaa"
        self.assertEqual("AAA", mozphab.prompt("", ["AAA", "BBB"]))
        input_response = "a"
        self.assertEqual("AAA", mozphab.prompt("", ["AAA", "BBB"]))
        input_response = "b"
        self.assertEqual("BBB", mozphab.prompt("", ["AAA", "BBB"]))
        input_response = "abc"
        self.assertEqual("abc", mozphab.prompt(""))

    @mock.patch("mozphab.probe_repo")
    def test_repo_from_args(self, m_probe):
        # TODO test walking the path
        repo = None

        def probe_repo(_):
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
            "\n\nSummary:\n\n\n"
            "\n\nTest Plan:"
            "\n\nReviewers: "
            "\n\nSubscribers:"
            "\n\nBug #: ",
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
                "title\n\n"
                "summary\n\n"
                "Differential Revision: http://phabricator.test/D123"
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
    @mock.patch("mozphab.which")
    def test_which_path(self, m_which, _os_environ, m_os_path, m_os_access):
        m_os_path.exists.side_effect = (True, False)
        m_os_access.return_value = True
        m_os_path.isdir.return_value = False

        path = "x"
        self.assertEqual(path, mozphab.which_path(path))
        m_which.assert_not_called()
        mozphab.which_path(path)
        m_which.assert_called_once_with(path)


@mock.patch("mozphab.ConduitAPI.call")
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
    assert [] == mozphab.check_for_invalid_reviewers(reviewers)


@mock.patch("mozphab.ConduitAPI.call")
def test_disabled_reviewers(call_conduit):
    reviewers = dict(granted=[], request=["alice", "goober"])
    call_conduit.side_effect = (
        # user.query
        [
            dict(userName="alice", phid="PHID-USER-1"),
            dict(userName="goober", phid="PHID-USER-2", roles=[u"disabled"]),
        ],
        # project.search
        {"data": [], "maps": {"slugMap": {}}},
    )
    expected_errors = [dict(name="goober", disabled=True)]
    assert expected_errors == mozphab.check_for_invalid_reviewers(reviewers)


@mock.patch("mozphab.ConduitAPI.call")
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

    errors = mozphab.check_for_invalid_reviewers(reviewers)
    errors.sort(key=lambda k: k["name"])
    assert expected_errors == errors


@mock.patch("mozphab.ConduitAPI.call")
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
    assert [] == mozphab.check_for_invalid_reviewers(reviewers)


@mock.patch("mozphab.arc_out")
def test_api_call_with_no_errors_returns_api_response_json(arc_out):
    # fmt: off
    arc_out.return_value = json.dumps(
        {
            "error": None,
            "errorMessage": None,
            "response": {"data": "ok"}
        }
    )
    # fmt: on
    api_response = mozphab.arc_call_conduit("my.method", {}, "")

    assert api_response == {"data": "ok"}
    arc_out.assert_called_once_with(
        ["call-conduit", "my.method"],
        cwd="",
        log_output_to_console=False,
        stdin=mock.ANY,
        stderr=mock.ANY,
        search_error=mozphab.ARC_CONDUIT_ERROR,
    )


@mock.patch("mozphab.arc_out")
def test_api_call_with_error_raises_exception(arc_out):
    arc_out.return_value = json.dumps(
        {
            "error": "ERR-CONDUIT-CORE",
            "errorMessage": "**sad trombone**",
            "response": None,
        }
    )

    with pytest.raises(mozphab.ConduitAPIError) as err:
        mozphab.arc_call_conduit("my.method", {}, "")
        assert err.message == "**sad trombone**"


@mock.patch("mozphab.arc_out")
def test_arc_ping_with_invalid_certificate_returns_false(arc_out):
    arc_out.side_effect = mozphab.CommandError
    assert not mozphab.arc_ping("")


@mock.patch("mozphab.check_call")
@mock.patch("os.path.exists")
@mock.patch("os.makedirs")
def test_install(_makedirs, m_exists, m_check_call):
    install = mozphab.install_arc_if_required
    m_exists.return_value = True
    install()
    m_check_call.assert_not_called()

    m_exists.return_value = False
    install()
    assert 2 == m_check_call.call_count


def test_get_users_no_users():
    conduit = mozphab.conduit
    assert [] == conduit.get_users([])


@mock.patch("mozphab.ConduitAPI.call")
def test_get_users_with_user(m_conduit):
    conduit = mozphab.conduit

    user = {"userName": "alice", "phid": "PHID-USER-1"}
    m_conduit.return_value = [user]
    assert [user] == conduit.get_users(["alice"])
    m_conduit.assert_called_once()

    conduit.get_users(["alice"])
    m_conduit.assert_called_once()

    mozphab.cache.reset()
    m_conduit.reset_mock()
    m_conduit.return_value = []
    assert [] == conduit.get_users(["alice"])
    m_conduit.assert_called_once()


def test_simple_cache():
    cache = mozphab.SimpleCache()
    assert cache.get("nothing") is None

    cache.set("something", 123)
    assert cache.get("something") == 123

    assert cache.get("SoMeThInG") == 123

    cache.set("something", "foo")
    assert cache.get("something") == "foo"

    cache.delete("something")
    assert cache.get("something") is None


@mock.patch("subprocess.check_output")
@mock.patch("mozphab.logger")
def test_check_output(m_logger, m_check_output):
    m_check_output.side_effect = subprocess.CalledProcessError(
        cmd=["some", "cmd"], returncode=2, output="output msg", stderr="stderr msg"
    )
    with pytest.raises(mozphab.CommandError) as e:
        mozphab.check_output(["command"])

    assert e.value.status == 2
    assert str(e.value).startswith("command 'command'")
    assert mock.call("stderr msg") in m_logger.debug.call_args_list
    assert mock.call("output msg") in m_logger.debug.call_args_list

    m_check_output.side_effect = ("response \nline \n",)
    assert ["response ", "line"] == mozphab.check_output(["command"])

    m_check_output.side_effect = ("response \nline \n",)
    assert ["response ", "line "] == mozphab.check_output(["command"], strip=False)

    m_check_output.side_effect = ("response \nline \n",)
    assert "response \nline" == mozphab.check_output(["command"], split=False)


@mock.patch.object(builtins, "input")
@mock.patch("mozphab.sys")
def test_prompt(m_sys, m_input):
    input_response = None

    def _input(_):
        return input_response

    m_input.side_effect = _input

    # Default
    input_response = ""
    assert "AAA" == mozphab.prompt("", ["AAA", "BBB"])

    # Escape
    m_sys.exit.side_effect = SystemExit()
    with pytest.raises(SystemExit):
        input_response = chr(27)
        mozphab.prompt("", ["AAA"])

    input_response = "aaa"


def test_git_find_repo(git_repo_path):
    path = str(git_repo_path)
    assert path == mozphab.find_repo_root(path)
    subdir = git_repo_path / "test_dir"
    subdir.mkdir()
    assert path == mozphab.find_repo_root(str(subdir))


def test_hg_find_repo(hg_repo_path):
    path = str(hg_repo_path)
    assert path == mozphab.find_repo_root(path)


def test_fail_find_repo():
    path = "/non/existing/path"
    assert mozphab.find_repo_root(path) is None


@mock.patch("mozphab.Mercurial")
@mock.patch("mozphab.Git")
def test_probe_repo(m_git, m_hg):
    m_hg.return_value = "HG"

    assert "HG" == mozphab.probe_repo("path")

    m_hg.side_effect = ValueError
    m_git.return_value = "GIT"
    assert "GIT" == mozphab.probe_repo("path")

    m_git.side_effect = ValueError
    assert mozphab.probe_repo("path") is None


@mock.patch("mozphab.probe_repo")
def test_repo_from_args(m_probe):
    # TODO test walking the path
    repo = None

    def probe_repo(path):
        return repo

    m_probe.side_effect = probe_repo

    class Args:
        def __init__(self, path=None):
            self.path = path

    with pytest.raises(mozphab.Error):
        mozphab.repo_from_args(Args(path="some path"))

    repo = mock.MagicMock()
    args = Args(path="some path")
    assert repo == mozphab.repo_from_args(args)
    repo.set_args.assert_called_once_with(args)


def test_parse_config():
    res = mozphab.parse_config(
        ["key=value 1", "key2 = value2 ", "key3=", "key4=one=two=three"]
    )
    assert res == dict(key="value 1", key2="value2", key3="", key4="one=two=three")


def test_parse_config_key_only():
    assert mozphab.parse_config(["key"]) == dict()


def test_parse_config_with_filter():
    def _filter(name, value):
        if name != "out":
            return True

    res = mozphab.parse_config(["imin=I'm in", "out=not here"], _filter)
    assert res == dict(imin="I'm in")


@mock.patch("os.path.expanduser")
@mock.patch("os.path.isfile")
@mock.patch("os.stat")
@mock.patch("os.chmod")
def test_get_arcrc_path(m_chmod, m_stat, m_isfile, m_expand):
    arcrc = mozphab.get_arcrc_path

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
    mozphab.cache.reset()
    arcrc()
    m_chmod.assert_not_called()

    m_chmod.reset_mock()
    stat.st_mode = 0o100640
    mozphab.cache.reset()
    arcrc()
    m_chmod.assert_called_once_with("arcrc file", 0o600)


def test_short_node():
    assert (
        mozphab.short_node("b016b6080ff9fa6d9ac459950e24bdcdaa939be0") == "b016b6080ff9"
    )
    assert (
        mozphab.short_node("this-is-not-a-sha-this-is-not-a-sha-test")
        == "this-is-not-a-sha-this-is-not-a-sha-test"
    )
    assert mozphab.short_node("b016b6080ff9") == "b016b6080ff9"
    assert mozphab.short_node("b016b60") == "b016b60"
    assert mozphab.short_node("mozilla-central") == "mozilla-central"
