# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import imp
import json
import os
import subprocess

import mock
import pytest

from conftest import git_out

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


arc_call_conduit = mock.Mock()
arc_call_conduit.return_value = [{"userName": "alice", "phid": "PHID-USER-1"}]

initial_sha = None


def get_sha():
    return git_out("log", "--format=%H", "-1").rstrip("\n")


@pytest.fixture
def init_sha(in_process, git_repo_path):
    return get_sha()


def test_submit_create(in_process, git_repo_path, init_sha):
    testfile = git_repo_path / "X"
    testfile.write_text(u"a")
    git_out("add", ".")
    git_out("commit", "--message", "A r?alice")
    testfile = git_repo_path / "untracked"
    testfile.write_text(u"a")

    mozphab.main(["submit", "--yes", "--bug", "1", init_sha])

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """
Bug 1 - A r?alice

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()


def test_submit_different_author(in_process, git_repo_path, init_sha):
    testfile = git_repo_path / "X"
    testfile.write_text(u"a")
    git_out("add", ".")
    git_out(
        "commit",
        "--date",
        "Tue, 22 Jan 2019 13:42:48 +0000",
        "--author",
        "foo <foo@bar.com>",
        "--message",
        "A r?alice",
    )
    testfile.write_text(u"b")
    git_out(
        "commit",
        "--date",
        "Tue, 22 Jan 2019 13:43:48 +0000",
        "--author",
        "bar <bar@foo.com>",
        "--all",
        "--message",
        "B r?alice",
    )

    mozphab.main(["submit", "--yes", "--bug", "1", init_sha])

    log = git_out("log", "--format=%aD+++%an+++%ae", "-2")
    expected = """\
Tue, 22 Jan 2019 13:43:48 +0000+++bar+++bar@foo.com
Tue, 22 Jan 2019 13:42:48 +0000+++foo+++foo@bar.com
"""
    assert log == expected


@pytest.mark.skip("Commit body has an extra line at the end.")
def test_submit_update(in_process, git_repo_path, init_sha):
    testfile = git_repo_path / "X"
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


def test_submit_update_bug_id(in_process, git_repo_path, init_sha):
    arc_call_conduit.reset_mock()
    arc_call_conduit.side_effect = (
        {
            "data": [
                {
                    "id": 123,
                    "phid": "PHID-REV-1",
                    "fields": {"bugzilla.bug-id": "1"},
                    "attachments": {"reviewers": {"reviewers": []}},
                }
            ]
        },  # get reviewers for updated revision
        {"data": {}},
    )
    testfile = git_repo_path / "X"
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

    mozphab.main(["submit", "--yes", "--bug", "2", init_sha])

    assert arc_call_conduit.call_count == 2
    assert arc_call_conduit.call_args_list[1] == mock.call(
        "differential.revision.edit",
        {
            "objectIdentifier": "D123",
            "transactions": [{"type": "bugzilla.bug-id", "value": "2"}],
        },
        mock.ANY,
    )
