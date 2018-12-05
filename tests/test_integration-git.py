# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import imp
import json
import os
import subprocess

import mock
import pytest

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)

env = os.environ.copy()
initial_sha = None


def git_out(*args):
    args = ["git"] + list(args)
    env["DEBUG"] = "1"
    return subprocess.check_output(args, env=env)


def get_sha():
    return git_out("log", "--format=%H", "-1").rstrip("\n")


@pytest.fixture
def repo_path(monkeypatch, tmp_path):
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
def init_sha(in_process, repo_path):
    return get_sha()


def test_submit_create(in_process, repo_path, init_sha):
    testfile = repo_path / "X"
    testfile.write_text(u"a")
    git_out("add", ".")
    git_out("commit", "--message", "A r?alice")

    mozphab.main(["submit", "--yes", "--bug", "1", init_sha])

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """\
Bug 1 - A r?alice

Differential Revision: http://example.test/D123
"""
    assert log == expected


@pytest.mark.skip("Commit body has an extra line at the end.")
def test_submit_update(in_process, repo_path, init_sha):
    testfile = repo_path / "X"
    testfile.write_text(u"a")
    git_out("add", ".")

    # Write out our commit message as if the program had already run and appended
    # a Differential Revision keyword to the commit body for tracking.
    git_out(
        "commit",
        "--message",
        """\
Bug 1 - A

Differential Revision: http://example.test/D123
""",
    )

    mozphab.main(["submit", "--yes", "--bug", "1", init_sha])

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """\
Bug 1 - A

Differential Revision: http://example.test/D123
"""
    assert log == expected
