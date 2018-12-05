# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import imp
import json
import mock
import os
import pytest
import sys

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


@pytest.fixture
@mock.patch("mozphab.Git.git_out")
@mock.patch("mozphab.Git._get_current_head")
@mock.patch("mozphab.Config")
@mock.patch("mozphab.os.path")
@mock.patch("mozphab.which")
@mock.patch("mozphab.Repository._phab_url")
def git(
    m_repository_phab_url,
    m_which,
    m_os_path,
    m_config,
    m_git_get_current_head,
    m_git_git_out,
):
    m_os_path.join = os.path.join
    m_os_path.exists.return_value = True
    m_which.return_value = True
    m_os_path.isfile.return_value = False
    m_git_get_current_head.return_value = "branch"
    m_git_git_out.return_value = ["user.email=email"]
    return mozphab.Git("x")


@pytest.fixture
@mock.patch("mozphab.Mercurial.hg_out")
@mock.patch("mozphab.Config")
@mock.patch("mozphab.os.path")
@mock.patch("mozphab.which")
@mock.patch("mozphab.Repository._phab_url")
def hg(m_repository_phab_url, m_which, m_os_path, m_config, m_hg_hg_out, safe_environ):
    m_os_path.join = os.path.join
    m_os_path.exists.return_value = True
    m_which.return_value = True
    m_os_path.isfile.return_value = False
    m_hg_hg_out.side_effect = [
        "Mercurial Distributed SCM (version 4.7.1)",
        ["ui.username=username", "extensions.evolve="],
    ]
    return mozphab.Mercurial("x")


@pytest.fixture
def safe_environ(monkeypatch):
    # Make sure we preserve the system defaults.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    # Disable logging to keep the testrunner output clean
    monkeypatch.setattr(mozphab, "init_logging", mock.MagicMock())


@pytest.fixture
def in_process(monkeypatch, safe_environ):
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
        raise t, v, tb

    monkeypatch.setattr(sys, "exit", reraise)

    # Disable uploading a new commit title and summary to Phabricator.  This operation
    # is safe to skip and doing so makes it easier to test other arc_out call sites.
    monkeypatch.setattr(mozphab, "update_phabricator_commit_summary", mock.MagicMock())

    def arc_ping(self, *args):
        return True

    def arc_out(self, *args, **kwargs):
        # Return alice as the only valid reviewer name from Phabricator.
        # See https://phabricator.services.mozilla.com/api/user.search
        return json.dumps(
            {
                "error": None,
                "errorMessage": None,
                "response": {"data": [{"fields": {"username": "alice"}}]},
            }
        )

    def check_call_by_line(*args, **kwargs):
        return ["Revision URI: http://example.test/D123"]

    monkeypatch.setattr(mozphab, "arc_ping", arc_ping)
    monkeypatch.setattr(mozphab, "arc_out", arc_out)
    monkeypatch.setattr(mozphab, "check_call_by_line", check_call_by_line)
