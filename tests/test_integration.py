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

review = imp.load_source(
    "review", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


def hg(*args):
    args = ["hg"] + list(args)
    return subprocess.check_output(args)


@pytest.fixture
def in_process(monkeypatch):
    """Set up an environment to run moz-phab within the current process."""
    # Make sure other tests didn't leak and mess up the module-level
    # global variables :/
    monkeypatch.setattr(review, "IS_WINDOWS", False)
    monkeypatch.setattr(review, "DEBUG", False)
    monkeypatch.setattr(review, "HAS_ANSI", False)
    # Constructing the Mercurial() object modifies os.environ for all tests.
    # Make sure we preserve the system defaults.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    # Disable logging to keep the testrunner output clean
    monkeypatch.setattr(review, "init_logging", mock.MagicMock())
    # Disable update checking.  It modifies the program on disk which we do /not/ want
    # to do during a test run.
    monkeypatch.setattr(review, "check_for_updates", mock.MagicMock())

    # Disable calls to sys.exit() at the end of the script.  Re-raise errors instead
    # to make test debugging easier.
    def reraise(*args, **kwargs):
        t, v, tb = sys.exc_info()
        raise t, v, tb

    monkeypatch.setattr(sys, "exit", reraise)

    def arc_ping(self, *args):
        return True

    def arc_out(self, *args, **kwargs):
        pass

    def check_call_by_line(*args, **kwargs):
        return ["Revision URI: http://example.test/D123"]

    monkeypatch.setattr(review, "arc_ping", arc_ping)
    monkeypatch.setattr(review, "arc_out", arc_out)
    monkeypatch.setattr(review, "check_call_by_line", check_call_by_line)


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
    hg("commit", "--message", "A")

    review.main(["submit", "--yes", "--bug", "1"])

    log = hg("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """\
Bug 1 - A

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

    review.main(["submit", "--yes", "--bug", "1"])

    log = hg("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """\
Bug 1 - A

Differential Revision: http://example.test/D123
"""
    assert log == expected
