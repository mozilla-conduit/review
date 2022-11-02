# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import io
import json
import os
import pytest
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from glean import testing
from pathlib import Path
from unittest import mock

# NOTE: Set the environment variables before importing any of mozphab modules.
os.environ["MOZPHAB_NO_USER_CONFIG"] = "1"

from mozphab.commands import submit  # noqa: E402
from mozphab.git import Git  # noqa: E402
from mozphab.mercurial import Mercurial  # noqa: E402

from mozphab import (
    conduit,
    config as config_module,
    environment,
    mozphab,
    repository,
    simplecache,
    user,
)  # noqa: E402


environment.SHOW_SPINNER = False
environment.HTTP_ALLOWED = True


def create_temp_fn(*filenames):
    m_temp_fn = mock.Mock()
    if len(filenames) > 1:
        m_temp_fn.__enter__ = mock.Mock(side_effect=filenames)
    else:
        m_temp_fn.__enter__ = mock.Mock(return_value=filenames[0])
    m_temp_fn.__exit__ = mock.Mock(return_value=None)
    return m_temp_fn


def search_diff(diff=1, phid="PHID-DIFF-1", node="aaa000aaa000"):
    return {
        "id": diff,
        "phid": phid,
        "attachments": {"commits": {"commits": [{"identifier": node}]}},
    }


def search_rev(
    rev=1,
    phid="PHID-DREV-1",
    bug="1",
    status="needs-review",
    closed=False,
    author="PHID-USER-1",
    diff="PHID-DIFF-1",
    reviewers=None,
    is_draft=False,
):
    reviewers = reviewers or []
    return {
        "id": rev,
        "phid": phid,
        "fields": {
            "bugzilla.bug-id": bug,
            "status": {"value": status, "closed": closed},
            "authorPHID": author,
            "diffPHID": diff,
            "isDraft": is_draft,
        },
        "attachments": {
            "reviewers": {"reviewers": [{"reviewerPHID": r} for r in reviewers]}
        },
    }


def assert_attributes(hunk, expected):
    for name, value in expected.items():
        if hunk.__getattribute__(name) != value:
            raise AssertionError(
                "%s: expected '%s', got '%s'"
                % (name, value, hunk.__getattribute__(name))
            )


@pytest.fixture
def posix_linesep():
    """POSIX line separator (i.e. newline character)."""
    return "\n"


@pytest.fixture
def hg_sha():
    def ret():
        return hg_out("id", "-i").rstrip("\n")

    return ret


@pytest.fixture
def git_sha():
    def ret():
        return git_out("log", "--format=%H", "-1").rstrip("\n")

    return ret


@pytest.fixture
def init_sha(in_process, git_repo_path, git_sha):
    return git_sha()


@pytest.fixture(autouse=True)
def reset_cache():
    simplecache.cache.reset()


@pytest.fixture()
def repo_phab_url():
    with mock.patch("mozphab.repository.Repository._phab_url") as xmock:
        xmock.return_value = "http://phab.test"
        yield xmock


@pytest.fixture
def data_file():
    return Path(__file__).parent / "data" / "img.png"


@pytest.fixture
def git_command():
    mozphab.config.git_command = ["git"]
    return mozphab.config


@pytest.fixture
@mock.patch("mozphab.gitcommand.GitCommand.output")
@mock.patch("mozphab.git.Git._get_current_head")
@mock.patch("mozphab.repository.os.path")
@mock.patch("mozphab.helpers.which")
@mock.patch("mozphab.repository.read_json_field")
def git(
    m_read_json_field,
    m_which,
    m_os_path,
    m_git_get_current_head,
    m_git_git_out,
    repo_phab_url,
    git_command,
):
    m_read_json_field.return_value = "TEST"
    m_os_path.join = os.path.join
    m_os_path.exists.return_value = True
    m_which.return_value = True
    m_os_path.isfile.return_value = False
    m_git_git_out.side_effect = ("git version 2.25.0",)
    m_git_get_current_head.return_value = "branch"
    git = Git("x")
    git._phab_vcs = "git"
    return git


@pytest.fixture
@mock.patch("mozphab.repository.os.chdir")
@mock.patch("mozphab.mercurial.Mercurial.repository")
@mock.patch("mozphab.repository.os.path")
@mock.patch("mozphab.helpers.which")
@mock.patch("mozphab.repository.read_json_field")
@mock.patch("mozphab.mercurial.hglib.open")
def hg(
    m_hglib_open,
    m_read_json_field,
    m_which,
    m_os_path,
    m_repository,
    m_os_chdir,
    safe_environ,
    repo_phab_url,
):
    m_read_json_field.return_value = "TEST"
    m_os_path.join = os.path.join
    m_os_path.exists.return_value = True
    m_which.return_value = True
    m_os_path.isfile.return_value = False
    m_repository.version = 4, 7, 1, "bleh"
    m_os_chdir.return_value = True
    mozphab.config.hg_command = ["hg"]
    hg = Mercurial("x")
    hg.use_evolve = True
    hg.has_mq = False
    hg._phab_vcs = "hg"
    return hg


def hg_out(*args):
    args = ["hg"] + list(args)
    return subprocess.check_output(args, universal_newlines=True, encoding="utf-8")


def write_text(filename, content, **kwargs):
    # Always write LF delimited text
    with io.open(filename, "w", newline="\n", **kwargs) as f:
        f.write(content)


def find_script_path(name):
    """Return the fully qualified path to an executable, preferring those installed
    into the current virtualenv."""
    bin_path = Path(sys.executable).parent
    if sys.platform == "win32":
        name = "%s.exe" % name
    filename = bin_path / name
    if filename.exists():
        return str(filename)
    # Check system path
    if shutil.which(name):
        return name
    raise Exception("Failed to find script: %s" % filename)


@pytest.fixture
def hg_repo_path(monkeypatch, tmp_path):
    """Build a usable HG repository. Return the pathlib.Path to the repo."""
    phabricator_uri = "http://example.test"
    repo_path = tmp_path / "hg-repo"
    repo_path.mkdir()
    monkeypatch.chdir(str(repo_path))
    arcconfig = repo_path / ".arcconfig"
    arcconfig.write_text(json.dumps({"phabricator.uri": phabricator_uri}))
    hg_out("init")
    hg_out("add")
    hg_out("commit", "-m", "init")
    # graphshorten changes `log --graph` output, force to false
    with open(".hg/hgrc", "a") as f:
        f.write("\n[experimental]\ngraphshorten = false\n")
    return repo_path


@pytest.fixture
def fresh_global_config(tmp_path):
    """Overrides global ~/.gitconfig.

    Creates a tiny gitconfig file in the temp repo and sets it as the $HOME
    """
    original_env = os.environ.copy()
    env = os.environ
    env["HOME"] = str(tmp_path)
    with open("{}/.gitconfig".format(tmp_path), "w") as f:
        f.write("[user]\n\tname = Developer\n\temail = developer@mozilla.com\n")
    with open("{}/.hgrc".format(tmp_path), "w") as f:
        f.write(
            "[ui]\nusername = Developer <developer@mozilla.com>\n"
            "[extensions]\nevolve =\n"
        )
    yield
    os.environ.clear()
    os.environ.update(original_env)


def git_out(*args):
    env = os.environ.copy()
    args = [
        "git",
        "-c",
        "i18n.logOutputEncoding=UTF-8",
        "-c",
        "i18n.commitEncoding=UTF-8",
    ] + list(args)
    env["DEBUG"] = "1"
    return subprocess.check_output(
        args, env=env, universal_newlines=True, encoding="utf-8"
    )


@pytest.fixture
def git_repo_path(monkeypatch, tmp_path):
    """Build a usable Git repository. Return the pathlib.Path to the repo."""
    phabricator_uri = "http://example.test"
    repo_path = tmp_path / "git-repo"
    repo_path.mkdir()
    monkeypatch.chdir(str(repo_path))
    arcconfig = repo_path / ".arcconfig"
    arcconfig.write_text(json.dumps({"phabricator.uri": phabricator_uri}))

    # Use --initial-branch where available to avoid an unnecessary warning
    m = re.search(r"(\d+\.\d+)\.\d+", git_out("version"))
    if m and float(m[1]) >= 2.28:
        git_out("init", "--initial-branch", "main")
    else:
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
def config():
    """Replace the original config with the fake one."""
    with tempfile.NamedTemporaryFile() as temp:
        return config_module.Config(filename=temp.name)


@pytest.fixture
def in_process(monkeypatch, safe_environ, request, config):
    """Set up an environment to run moz-phab within the current process."""
    monkeypatch.setattr("mozphab.config.config", config)
    monkeypatch.setattr("mozphab.git.config", config)
    monkeypatch.setattr("mozphab.mercurial.config", config)
    # Make sure other tests didn't leak and mess up the module-level
    # global variables :/
    monkeypatch.setattr(environment, "DEBUG", True)
    monkeypatch.setattr(environment, "HAS_ANSI", False)
    # Constructing the Mercurial() object modifies os.environ for all tests.
    # Disable update checking.  It modifies the program on disk which we do /not/ want
    # to do during a test run.
    monkeypatch.setattr(mozphab, "check_for_updates", mock.Mock(return_value=None))

    # Disable calls to sys.exit() at the end of the script.  Re-raise errors instead
    # to make test debugging easier.
    def reraise(*args, **kwargs):
        raise

    monkeypatch.setattr(sys, "exit", reraise)

    # Disable uploading a new commit title and summary to Phabricator.  This operation
    # is safe to skip and doing so makes it easier to test other conduit call sites.
    monkeypatch.setattr(submit, "update_revision_description", mock.MagicMock())

    # Modify user_data object to not touch the file
    user.USER_INFO_FILE = mock.Mock()
    user.USER_INFO_FILE.exists.return_value = False
    user.user_data = user.UserData()
    user.user_data.update_from_dict(
        dict(
            user_code=str(uuid.uuid4()),
            is_employee=True,
            installation_id=str(uuid.uuid4()),
            last_check=time.time(),
        )
    )

    # Allow to define the check_call_by_line function in the testing module
    def check_call_by_line_static(*args, **kwargs):
        return ["Revision URI: http://example.test/D123"]

    def call_conduit_static(self, *args):
        # Return alice as the only valid reviewer name from Phabricator.
        # See https://phabricator.services.mozilla.com/api/user.search
        return [{"userName": "alice"}]

    call_conduit = getattr(request.module, "call_conduit", call_conduit_static)
    monkeypatch.setattr(conduit.ConduitAPI, "call", call_conduit)

    def read_json_field_local(self, *args):
        if args[0][0] == "phabricator.uri":
            return "http://example.test"
        elif args[0][0] == "repository.callsign":
            return "TEST"

    read_json_field = getattr(request.module, "read_json_field", read_json_field_local)
    monkeypatch.setattr(repository, "read_json_field", read_json_field)


@pytest.fixture
@mock.patch("mozphab.user.USER_INFO_FILE")
def user_data(m_file):
    m_file.exists.return_value = False
    return user.UserData()


@pytest.fixture(name="reset_glean", scope="function", autouse=True)
def fixture_reset_glean():
    testing.reset_glean(application_id="mozphab", application_version="0.1.86")


@pytest.fixture(autouse=True)
def mock_load_api_token(monkeypatch, request):
    if "no_mock_token" not in request.keywords:
        monkeypatch.setattr("mozphab.conduit.ConduitAPI.load_api_token", mock.MagicMock)
