# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import mock

from .conftest import hg_out, search_diff, search_rev

from mozphab import mozphab

mozphab.SHOW_SPINNER = False


arc_call_conduit = mock.Mock()
arc_call_conduit.return_value = [{"userName": "alice", "phid": "PHID-USER-1"}]

call_conduit = mock.Mock()

check_call_by_line = mock.Mock()
check_call_by_line.return_value = ["Revision URI: http://example.test/D123"]


def test_submit_create(in_process, hg_repo_path):
    call_conduit.side_effect = (
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        [{"userName": "alice", "phid": "PHID-USER-1"}],
    )
    testfile = hg_repo_path / "X"
    testfile.write_text("a")
    hg_out("add")
    hg_out("commit", "--message", "A r?alice")

    mozphab.main(["submit", "--arc", "--yes", "--bug", "1", "."], is_development=True)

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """
Bug 1 - A r?alice

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()

    assert hg_out("bookmark").strip() == "no bookmarks set"


def test_submit_create_with_user_bookmark(in_process, hg_repo_path):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        [{"userName": "alice", "phid": "PHID-USER-1"}],
    )

    testfile = hg_repo_path / "X"
    testfile.write_text("a")
    hg_out("add")
    hg_out("commit", "--message", "A r?alice")

    user_bookmark_name = "user bookmark"
    hg_out("bookmark", user_bookmark_name)

    mozphab.main(["submit", "--arc", "--yes", "--bug", "1", "."], is_development=True)

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """
Bug 1 - A r?alice

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()

    assert hg_out("bookmark").startswith(" * " + user_bookmark_name)


def test_submit_update(in_process, hg_repo_path):
    call_conduit.reset_mock()
    arc_call_conduit.reset_mock()
    call_conduit.side_effect = (
        {},
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        dict(data=[search_rev(rev=123)]),
        dict(data=[search_diff()]),
        # get reviewers for updated revision
        dict(phid="PHID-USER-1"),
        dict(data=[search_rev(rev=123, reviewers=["PHID-USER-1"])]),
    )
    check_call_by_line.reset_mock()
    testfile = hg_repo_path / "X"
    testfile.write_text("a")
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

    mozphab.main(["submit", "--arc", "--yes", "--bug", "1", "."], is_development=True)

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """\
Bug 1 - A

Differential Revision: http://example.test/D123
"""
    assert log == expected
    assert call_conduit.call_count == 5
    arc_call_conduit.assert_called_once_with("conduit.ping", {}, mock.ANY)
    check_call_by_line.assert_called_once()  # update


def test_submit_update_reviewers_not_updated(in_process, hg_repo_path):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        {},
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        dict(data=[search_rev(rev=123, reviewers=["PHID-USER-1"])]),
        dict(data=[search_diff()]),
        dict(phid="PHID-USER-1"),
        [{"userName": "alice", "phid": "PHID-USER-1"}],
    )
    arc_call_conduit.reset_mock()
    check_call_by_line.reset_mock()
    testfile = hg_repo_path / "X"
    testfile.write_text("a")
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

    mozphab.main(
        ["submit", "--arc", "--yes", "--bug", "1", "-r", "alice", "."],
        is_development=True,
    )

    arc_call_conduit.assert_called_once_with("conduit.ping", {}, mock.ANY)
    check_call_by_line.assert_called_once()


def test_submit_update_no_new_reviewers(in_process, hg_repo_path):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        {},
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        dict(data=[search_rev(rev=123, status="changes-planned")]),
        dict(data=[search_diff()]),
        dict(phid="PHID-USER-1"),
        [{"userName": "alice", "phid": "PHID-USER-1"}],
    )
    arc_call_conduit.reset_mock()
    arc_call_conduit.side_effect = (
        {},
        {"data": {}},
    )  # set reviewers response
    check_call_by_line.reset_mock()
    testfile = hg_repo_path / "X"
    testfile.write_text("a")
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

    mozphab.main(
        ["submit", "--arc", "--yes", "--bug", "1", "-r", "alice", "."],
        is_development=True,
    )
    arc_call_conduit.assert_called_with(
        "differential.revision.edit",
        {
            "objectIdentifier": "D123",
            "transactions": [
                {"type": "reviewers.set", "value": ["PHID-USER-1"]},
                {"type": "request-review"},
            ],
        },
        mock.ANY,
    )
    check_call_by_line.assert_called_once()


def test_submit_update_bug_id(in_process, hg_repo_path):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        {},
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        dict(data=[search_rev(rev=123, reviewers=["PHID-USER-1"])]),
        dict(data=[search_diff()]),
        dict(phid="PHID-USER-1"),
        [{"userName": "alice", "phid": "PHID-USER-1"}],
    )
    arc_call_conduit.reset_mock()
    # response from setting the bug id
    arc_call_conduit.side_effect = (
        {},
        {"data": {}},
    )
    testfile = hg_repo_path / "X"
    testfile.write_text("a")
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

    mozphab.main(
        ["submit", "--arc", "--yes", "--bug", "2", "-r", "alice"], is_development=True
    )

    arc_call_conduit.assert_called_with(
        "differential.revision.edit",
        {
            "objectIdentifier": "D123",
            "transactions": [{"type": "bugzilla.bug-id", "value": "2"}],
        },
        mock.ANY,
    )
    assert call_conduit.call_count == 6
