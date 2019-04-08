# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import imp
import json
import os
import mock
import pytest

from conftest import hg_out

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


arc_call_conduit = mock.Mock()
arc_call_conduit.return_value = [{"userName": "alice", "phid": "PHID-USER-1"}]


def test_submit_create(in_process, hg_repo_path):
    arc_call_conduit.reset_mock()
    testfile = hg_repo_path / "X"
    testfile.write_text(u"a")
    hg_out("add")
    hg_out("commit", "--message", "A r?alice")

    mozphab.main(["submit", "--yes", "--bug", "1"])

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """
Bug 1 - A r?alice

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()


def test_submit_update(in_process, hg_repo_path):
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
        {"data": {}},  # set reviewers response
        {
            "data": [
                {
                    "id": "123",
                    "phid": "PHID-REV-1",
                    "fields": {"bugzilla.bug-id": "1"},
                    "attachments": {
                        "reviewers": {"reviewers": [{"reviewerPHID": "PHID-USER-1"}]}
                    },
                }
            ]
        },  # get reviewers for updated revision
    )
    testfile = hg_repo_path / "X"
    testfile.write_text(u"a")
    hg_out("add")

    # Write out our commit message as if the program had already run and appended
    # a Differential Revision keyword to the commit body for tracking.
    hg_out(
        "commit",
        "--message",
        """\
Bug 1 - A

Differential Revision: http://example.test/D123
""",
    )

    mozphab.main(["submit", "--yes", "--bug", "1"])

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """\
Bug 1 - A

Differential Revision: http://example.test/D123
"""
    assert log == expected
    assert arc_call_conduit.call_count == 2


def test_submit_update_reviewers_not_updated(in_process, hg_repo_path):
    arc_call_conduit.reset_mock()
    arc_call_conduit.side_effect = (
        {
            "data": [
                {
                    "id": 123,
                    "phid": "PHID-REV-1",
                    "fields": {"bugzilla.bug-id": "1"},
                    "attachments": {
                        "reviewers": {"reviewers": [{"reviewerPHID": "PHID-USER-1"}]}
                    },
                }
            ]
        },  # get reviewers for updated revision
        [{"userName": "alice", "phid": "PHID-USER-1"}],
    )
    testfile = hg_repo_path / "X"
    testfile.write_text(u"a")
    hg_out("add")

    # Write out our commit message as if the program had already run and appended
    # a Differential Revision keyword to the commit body for tracking.
    hg_out(
        "commit",
        "--message",
        """\
Bug 1 - A

Differential Revision: http://example.test/D123
""",
    )

    mozphab.main(["submit", "--yes", "--bug", "1", "-r", "alice"])

    assert arc_call_conduit.call_count == 2


def test_submit_update_no_new_reviewers(in_process, hg_repo_path):
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
        [{"userName": "alice", "phid": "PHID-USER-1"}],
        {"data": {}},  # set reviewers response
    )
    testfile = hg_repo_path / "X"
    testfile.write_text(u"a")
    hg_out("add")

    # Write out our commit message as if the program had already run and appended
    # a Differential Revision keyword to the commit body for tracking.
    hg_out(
        "commit",
        "--message",
        """\
Bug 1 - A

Differential Revision: http://example.test/D123
""",
    )

    mozphab.main(["submit", "--yes", "--bug", "1", "-r", "alice"])
    assert arc_call_conduit.call_count == 3


def test_submit_update_bug_id(in_process, hg_repo_path):
    arc_call_conduit.reset_mock()
    arc_call_conduit.side_effect = (
        {
            "data": [
                {
                    "id": 123,
                    "phid": "PHID-REV-1",
                    "fields": {"bugzilla.bug-id": "1"},
                    "attachments": {
                        "reviewers": {"reviewers": [{"reviewerPHID": "PHID-USER-1"}]}
                    },
                }
            ]
        },  # get reviewers for updated revision
        [{"userName": "alice", "phid": "PHID-USER-1"}],
        {"data": {}},  # response from setting the bug id
    )
    testfile = hg_repo_path / "X"
    testfile.write_text(u"a")
    hg_out("add")

    # Write out our commit message as if the program had already run and appended
    # a Differential Revision keyword to the commit body for tracking.
    hg_out(
        "commit",
        "--message",
        """\
Bug 1 - A

Differential Revision: http://example.test/D123
""",
    )

    mozphab.main(["submit", "--yes", "--bug", "2", "-r", "alice"])

    assert (
        mock.call(
            "differential.revision.edit",
            {
                "objectIdentifier": "D123",
                "transactions": [{"type": "bugzilla.bug-id", "value": "2"}],
            },
            mock.ANY,
        )
        == arc_call_conduit.call_args_list[2]
    )
    assert arc_call_conduit.call_count == 3
