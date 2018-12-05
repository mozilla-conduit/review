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


def hg(*args):
    args = ["hg"] + list(args)
    return subprocess.check_output(args)


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
