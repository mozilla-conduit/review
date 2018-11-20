# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import imp
import json
import os
import subprocess
import sys

import mock
import pytest

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


def hg(*args):
    args = ["hg"] + list(args)
    return subprocess.check_output(args)


@pytest.fixture
def in_process(monkeypatch, safe_environ):
    """Set up an environment to run moz-phab within the current process."""
    # Make sure other tests didn't leak and mess up the module-level
    # global variables :/
    monkeypatch.setattr(mozphab, "IS_WINDOWS", False)
    monkeypatch.setattr(mozphab, "DEBUG", False)
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


@pytest.fixture
def repo_path(monkeypatch, tmp_path):
    """Build a usable HG repository. Return the pathlib.Path to the repo."""
    phabricator_uri = "http://example.test"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(str(repo_path))
    arcconfig = repo_path / ".arcconfig"
    arcconfig.write_text(unicode(json.dumps({"phabricator.uri": phabricator_uri})))
    hg("init")
    return repo_path


def test_submit_create(in_process, repo_path):
    testfile = repo_path / "X"
    testfile.write_text(u"a")
    hg("add")
    hg("commit", "--message", "A r?alice")

    mozphab.main(["submit", "--yes", "--bug", "1"])

    log = hg("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """\
Bug 1 - A r?alice

Differential Revision: http://example.test/D123
"""
    assert log == expected


def test_submit_update(in_process, repo_path):
    testfile = repo_path / "X"
    testfile.write_text(u"a")
    hg("add")

    # Write out our commit message as if the program had already run and appended
    # a Differential Revision keyword to the commit body for tracking.
    hg(
        "commit",
        "--message",
        """\
Bug 1 - A

Differential Revision: http://example.test/D123
""",
    )

    mozphab.main(["submit", "--yes", "--bug", "1"])

    log = hg("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """\
Bug 1 - A

Differential Revision: http://example.test/D123
"""
    assert log == expected
