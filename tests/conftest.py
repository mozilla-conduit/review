# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import imp
import json
import mock
import os
import pytest
import subprocess
import sys

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


def create_temp_fn(*filenames):
    m_temp_fn = mock.Mock()
    if len(filenames) > 1:
        m_temp_fn.__enter__ = mock.Mock(side_effect=filenames)
    else:
        m_temp_fn.__enter__ = mock.Mock(return_value=filenames[0])
    m_temp_fn.__exit__ = mock.Mock(return_value=None)
    return m_temp_fn


@pytest.fixture(autouse=True)
def reset_cache():
    mozphab.cache.reset()


@pytest.fixture
@mock.patch("mozphab.Git.git_out")
@mock.patch("mozphab.Git._get_current_head")
@mock.patch("mozphab.Config")
@mock.patch("mozphab.os.path")
@mock.patch("mozphab.which")
@mock.patch("mozphab.Repository._phab_url")
@mock.patch("mozphab.read_json_field")
def git(
    m_read_json_field,
    m_repository_phab_url,
    m_which,
    m_os_path,
    m_config,
    m_git_get_current_head,
    m_git_git_out,
):
    m_read_json_field.return_value = "TEST"
    m_os_path.join = os.path.join
    m_os_path.exists.return_value = True
    m_which.return_value = True
    m_os_path.isfile.return_value = False
    m_git_get_current_head.return_value = "branch"
    return mozphab.Git("x")


@pytest.fixture
@mock.patch("mozphab.Mercurial.hg_out")
@mock.patch("mozphab.Config")
@mock.patch("mozphab.os.path")
@mock.patch("mozphab.which")
@mock.patch("mozphab.Repository._phab_url")
@mock.patch("mozphab.read_json_field")
def hg(
    m_read_json_field,
    m_repository_phab_url,
    m_which,
    m_os_path,
    m_config,
    m_hg_hg_out,
    safe_environ,
):
    m_read_json_field.return_value = "TEST"
    m_os_path.join = os.path.join
    m_config.safe_mode = False
    m_os_path.exists.return_value = True
    m_which.return_value = True
    m_os_path.isfile.return_value = False
    m_hg_hg_out.side_effect = [
        "Mercurial Distributed SCM (version 4.7.1)",
        ["ui.username=username", "extensions.evolve="],
    ]
    hg = mozphab.Mercurial("x")
    hg.use_evolve = True
    hg.has_mq = False
    return hg


def hg_out(*args):
    args = ["hg"] + list(args)
    return subprocess.check_output(args)


@pytest.fixture
def hg_repo_path(monkeypatch, tmp_path):
    """Build a usable HG repository. Return the pathlib.Path to the repo."""
    phabricator_uri = "http://example.test"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(str(repo_path))
    arcconfig = repo_path / ".arcconfig"
    arcconfig.write_text(unicode(json.dumps({"phabricator.uri": phabricator_uri})))
    hg_out("init")
    # graphshorten changes `log --graph` output, force to false
    with open(".hg/hgrc", "a") as f:
        f.write("\n[experimental]\ngraphshorten = false\n")
    return repo_path


def git_out(*args):
    env = os.environ.copy()
    args = ["git"] + list(args)
    env["DEBUG"] = "1"
    return subprocess.check_output(args, env=env)


@pytest.fixture
def git_repo_path(monkeypatch, tmp_path):
    """Build a usable Git repository. Return the pathlib.Path to the repo."""
    phabricator_uri = "http://example.test"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(str(repo_path))
    arcconfig = repo_path / ".arcconfig"
    arcconfig.write_text(unicode(json.dumps({"phabricator.uri": phabricator_uri})))
    git_out("init")
    git_out("add", ".")
    git_out("commit", "--message", "initial commit")
    return repo_path


@pytest.fixture
def safe_environ(monkeypatch):
    # Make sure we preserve the system defaults.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    # Disable logging to keep the testrunner output clean
    monkeypatch.setattr(mozphab, "init_logging", mock.MagicMock())


@pytest.fixture
def in_process(monkeypatch, safe_environ, request):
    """Set up an environment to run moz-phab within the current process."""
    # Make sure other tests didn't leak and mess up the module-level
    # global variables :/
    monkeypatch.setattr(mozphab, "IS_WINDOWS", False)
    monkeypatch.setattr(mozphab, "DEBUG", True)
    monkeypatch.setattr(mozphab, "HAS_ANSI", False)
    # Constructing the Mercurial() object modifies os.environ for all tests.
    # Disable update checking.  It modifies the program on disk which we do /not/ want
    # to do during a test run.
    monkeypatch.setattr(mozphab, "check_for_updates", mock.MagicMock())

    # Disable calls to sys.exit() at the end of the script.  Re-raise errors instead
    # to make test debugging easier.
    def reraise(*args, **kwargs):
        t, v, tb = sys.exc_info()
        raise (t, v, tb)

    monkeypatch.setattr(sys, "exit", reraise)

    # Disable uploading a new commit title and summary to Phabricator.  This operation
    # is safe to skip and doing so makes it easier to test other conduit call sites.
    monkeypatch.setattr(mozphab, "update_revision_description", mock.MagicMock())

    def arc_ping(self, *args):
        return True

    def arc_out(self, *args, **kwargs):
        return json.dumps(
            {
                "error": None,
                "errorMessage": None,
                "response": [{"userName": "alice", "phid": "PHID-USER-1"}],
            }
        )

    def check_call_by_line_static(*args, **kwargs):
        return ["Revision URI: http://example.test/D123"]

    # Allow to define the check_call_by_line function in the testing module
    check_call_by_line = getattr(
        request.module, "check_call_by_line", check_call_by_line_static
    )

    # Allow to define the arccall_conduit function in the testing module
    arc_call_conduit = getattr(
        request.module, "arc_call_conduit", mozphab.arc_call_conduit
    )
    monkeypatch.setattr(mozphab, "arc_ping", arc_ping)
    monkeypatch.setattr(mozphab, "arc_out", arc_out)
    monkeypatch.setattr(mozphab, "check_call_by_line", check_call_by_line)
    monkeypatch.setattr(mozphab, "arc_call_conduit", arc_call_conduit)

    def call_conduit_static(self, *args):
        # Return alice as the only valid reviewer name from Phabricator.
        # See https://phabricator.services.mozilla.com/api/user.search
        return [{"userName": "alice"}]

    call_conduit = getattr(request.module, "call_conduit", call_conduit_static)
    monkeypatch.setattr(mozphab.ConduitAPI, "call", call_conduit)

    def read_json_field_local(self, *args):
        if args[0][0] == "phabricator.uri":
            return "http://example.test"
        elif args[0][0] == "repository.callsign":
            return "TEST"

    read_json_field = getattr(request.module, "read_json_field", read_json_field_local)
    monkeypatch.setattr(mozphab, "read_json_field", read_json_field)
