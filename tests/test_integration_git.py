# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import shutil
import mock
import pytest

from callee import Contains
from .conftest import git_out

from mozphab import exceptions, mozphab

mozphab.SHOW_SPINNER = False


arc_call_conduit = mock.Mock()

call_conduit = mock.Mock()


def by_line_mock(*args, **_kwargs):
    # join args to catch unicode errors
    " ".join(*args)
    return ["Revision URI: http://example.test/D123"]


check_call_by_line = mock.Mock()
check_call_by_line.side_effect = by_line_mock

initial_sha = None


def test_submit_create_arc(in_process, git_repo_path, init_sha):
    call_conduit.side_effect = (
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        [{"userName": "alice", "phid": "PHID-USER-1"}],
    )
    testfile = git_repo_path / "X"
    testfile.write_text("a")
    git_out("add", ".")
    git_out("commit", "--message", "A r?alice")
    testfile = git_repo_path / "untracked"
    testfile.write_text("a")

    mozphab.main(
        ["submit", "--arc", "--yes", "--bug", "1", init_sha], is_development=True
    )

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """
Bug 1 - A r?alice

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()


def test_submit_create(in_process, git_repo_path, init_sha):
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        # user search
        [dict(userName="alice", phid="PHID-USER-1")],
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    (git_repo_path / "X").write_text("ą\r\nb\nc\n")
    (git_repo_path / "Y").write_text("no line ending")
    git_out("add", ".")
    (git_repo_path / "msg").write_text("Ą r?alice")
    git_out("commit", "--file", "msg")
    (git_repo_path / "untracked").write_text("a\n")

    mozphab.main(["submit", "--yes", "--bug", "1", init_sha], is_development=True)

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """
Bug 1 - Ą r?alice

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()
    assert mock.call("conduit.ping", {}) in call_conduit.call_args_list
    assert (
        mock.call("user.query", dict(usernames=["alice"]))
        in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "diffusion.repository.search",
            dict(limit=1, constraints=dict(callsigns=["TEST"])),
        )
        in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "differential.creatediff",
            {
                "sourceControlPath": "/",
                "sourceControlSystem": "git",
                "lintStatus": "none",
                "sourcePath": mock.ANY,
                "unitStatus": "none",
                "sourceMachine": "http://example.test",
                "sourceControlBaseRevision": mock.ANY,
                "repositoryPHID": "PHID-REPO-1",
                "branch": "HEAD",
                "changes": [
                    {
                        "commitHash": mock.ANY,
                        "awayPaths": [],
                        "newProperties": {"unix:filemode": "100644"},
                        "oldPath": None,
                        "hunks": [
                            {
                                "oldOffset": 0,
                                "oldLength": 0,
                                "newOffset": 1,
                                "newLength": 3,
                                "addLines": 3,
                                "delLines": 0,
                                "corpus": "+ą\r\n+b\n+c\n",
                                "isMissingOldNewline": False,
                                "isMissingNewNewline": False,
                            }
                        ],
                        "oldProperties": {},
                        "currentPath": "X",
                        "fileType": 1,
                        "type": 1,
                        "metadata": {},
                    },
                    {
                        "commitHash": mock.ANY,
                        "awayPaths": [],
                        "newProperties": {"unix:filemode": "100644"},
                        "oldPath": None,
                        "hunks": [
                            {
                                "oldOffset": 0,
                                "oldLength": 0,
                                "newOffset": 1,
                                "newLength": 1,
                                "addLines": 1,
                                "delLines": 0,
                                "corpus": (
                                    "+no line ending\n\\ No newline at end of file\n"
                                ),
                                "isMissingOldNewline": False,
                                "isMissingNewNewline": True,
                            }
                        ],
                        "oldProperties": {},
                        "currentPath": "Y",
                        "fileType": 1,
                        "type": 1,
                        "metadata": {},
                    },
                ],
                "creationMethod": "moz-phab-git",
            },
        )
        in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "differential.setdiffproperty",
            {"diff_id": "1", "name": "local:commits", "data": ~Contains('"rev":')},
        )
        in call_conduit.call_args_list
    )


def test_submit_create_added_not_commited(in_process, git_repo_path, init_sha):
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        # user search
        [dict(userName="alice", phid="PHID-USER-1")],
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    (git_repo_path / "X").write_text("ą\r\nb\nc\n")
    (git_repo_path / "Y").write_text("no line ending")
    git_out("add", ".")
    (git_repo_path / "msg").write_text("Ą r?alice")
    git_out("commit", "--file", "msg")
    (git_repo_path / "untracked").write_text("a\n")
    git_out("add", "untracked")

    with pytest.raises(exceptions.Error) as excinfo:
        mozphab.main(["submit", "--yes", "--bug", "1", init_sha], is_development=True)

    assert "Uncommitted changes present." in str(excinfo.value)


def test_submit_create_no_bug(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        # user search
        [dict(userName="alice", phid="PHID-USER-1")],
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    testfile = git_repo_path / "X"
    testfile.write_text("a\n")
    git_out("add", ".")
    msgfile = git_repo_path / "msg"
    msgfile.write_text("A r?alice")
    git_out("commit", "--file", "msg")

    mozphab.main(["submit", "--yes", "--no-bug", init_sha], is_development=True)

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """
A r?alice

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()


def test_submit_create_binary_arc(in_process, git_repo_path, init_sha, data_file):
    call_conduit.side_effect = (
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        [{"userName": "alice", "phid": "PHID-USER-1"}],
    )
    shutil.copyfile(str(data_file), str(git_repo_path / "img.png"))
    git_out("add", ".")
    git_out("commit", "--message", "IMG")

    mozphab.main(
        ["submit", "--arc", "--yes", "--bug", "1", init_sha], is_development=True
    )
    expected = """
Bug 1 - IMG

Differential Revision: http://example.test/D123
"""
    log = git_out("log", "--format=%s%n%n%b", "-1")
    assert log.strip() == expected.strip()


def test_submit_create_binary(in_process, git_repo_path, init_sha, data_file):
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        # file upload
        dict(),
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    shutil.copyfile(str(data_file), str(git_repo_path / "img.png"))
    git_out("add", ".")
    git_out("commit", "-m", "IMG")

    mozphab.main(["submit", "--yes", "--bug", "1", init_sha], is_development=True)

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """
Bug 1 - IMG

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()


def test_submit_update(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        dict(
            data=[
                {
                    "fields": {
                        "bugzilla.bug-id": "1",
                        "status": {"value": "needs-review"},
                        "authorPHID": "PHID-USER-1",
                    },
                    "phid": "PHID-DREV-y7x5hvdpe2gyerctdqqz",
                    "id": 123,
                    "attachments": {"reviewers": {"reviewers": []}},
                }
            ]
        ),
        # whoami
        dict(phid="PHID-USER-1"),
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    testfile = git_repo_path / "X"
    testfile.write_text("ą")
    git_out("add", ".")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1 - Ą

Differential Revision: http://example.test/D123
"""
    )
    git_out("commit", "--file", "msg")

    mozphab.main(
        ["submit", "--yes"]
        + ["--bug", "1"]
        + ["--message", "update message ćwikła"]
        + [init_sha],
        is_development=True,
    )

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """\
Bug 1 - Ą

Differential Revision: http://example.test/D123

"""
    assert log == expected


def test_submit_remove_cr(in_process, git_repo_path, init_sha):
    call_conduit.side_effect = (
        # CREATE
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        [dict(userName="alice", phid="PHID-USER-1")],
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        dict(),
        dict(object=dict(id="123")),
        # UPDATE
        # no need to ping (checked)
        # no need to check reviewer
        # no need to search for repository repository data is saved in .hg
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-2", diffid="2")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="124")),
    )
    test_a = git_repo_path / "X"
    test_a.write_text("a\r\nb\n")
    git_out("add", "X")
    git_out("commit", "-am", "A r?alice")
    mozphab.main(["submit", "--yes", "--bug", "1", init_sha], is_development=True)
    call_conduit.reset_mock()
    # removing CR, leaving LF
    test_a.write_text("a\nb\n")
    git_out("commit", "-am", "B r?alice")
    mozphab.main(["submit", "--yes", "--bug", "1", "HEAD~"], is_development=True)

    assert (
        mock.call(
            "differential.creatediff",
            {
                "changes": [
                    {
                        "metadata": {},
                        "oldPath": "X",
                        "currentPath": "X",
                        "awayPaths": [],
                        "oldProperties": {},
                        "newProperties": {},
                        "commitHash": mock.ANY,
                        "type": 2,
                        "fileType": 1,
                        "hunks": [
                            {
                                "oldOffset": 1,
                                "oldLength": 2,
                                "newOffset": 1,
                                "newLength": 2,
                                "addLines": 1,
                                "delLines": 1,
                                "isMissingOldNewline": False,
                                "isMissingNewNewline": False,
                                "corpus": "-a\r\n+a\n b\n",
                            }
                        ],
                    }
                ],
                "sourceMachine": "http://example.test",
                "sourceControlSystem": "git",
                "sourceControlPath": "/",
                "sourceControlBaseRevision": mock.ANY,
                "creationMethod": "moz-phab-git",
                "lintStatus": "none",
                "unitStatus": "none",
                "repositoryPHID": "PHID-REPO-1",
                "sourcePath": mock.ANY,
                "branch": "HEAD",
            },
        )
        in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "differential.setdiffproperty",
            {
                "diff_id": "2",
                "name": "local:commits",
                "data": Contains('"summary": "Bug 1 - B r?alice"')
                & Contains(
                    '"message": "'
                    "Bug 1 - B r?alice\\n\\n"
                    "Summary:\\n\\n\\n\\n\\n"
                    "Test Plan:\\n\\n"
                    "Reviewers: alice\\n\\n"
                    "Subscribers:\\n\\n"
                    'Bug #: 1"'
                ),
            },
        )
        in call_conduit.call_args_list
    )


def test_submit_single_last(in_process, git_repo_path, init_sha):
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    (git_repo_path / "X").write_text("a\n")
    git_out("add", "X")
    git_out("commit", "-am", "A")
    (git_repo_path / "X").write_text("b\n")
    git_out("commit", "-am", "B")

    mozphab.main(["submit", "--yes", "--bug", "1", "--single"], is_development=True)

    log = git_out("log", "--format=%s%n%n%b", "-2")
    expected = """\
Bug 1 - B

Differential Revision: http://example.test/D123
A


"""
    assert log == expected


def test_submit_single_first(in_process, git_repo_path, init_sha, git_sha):
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    (git_repo_path / "X").write_text("a\n")
    git_out("add", "X")
    git_out("commit", "-am", "A")
    sha = git_sha()
    (git_repo_path / "X").write_text("b\n")
    git_out("commit", "-am", "B")

    mozphab.main(
        ["submit", "--yes", "--bug", "1", "--single", sha], is_development=True
    )

    log = git_out("log", "--format=%s%n%n%b", "-2")
    expected = """\
B


Bug 1 - A

Differential Revision: http://example.test/D123
"""
    assert log == expected


def test_submit_update_no_message(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        dict(
            data=[
                {
                    "fields": {
                        "bugzilla.bug-id": "1",
                        "status": {"value": "needs-review"},
                        "authorPHID": "PHID-USER-1",
                    },
                    "phid": "PHID-DREV-y7x5hvdpe2gyerctdqqz",
                    "id": 123,
                    "attachments": {"reviewers": {"reviewers": []}},
                }
            ]
        ),
        dict(phid="PHID-USER-1"),
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    testfile = git_repo_path / "X"
    testfile.write_text(u"ą")
    git_out("add", ".")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        u"""\
Bug 1 - Ą

Differential Revision: http://example.test/D123
"""
    )
    git_out("commit", "--file", "msg")

    mozphab.main(["submit", "--yes", "--bug", "1", init_sha], is_development=True)

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """\
Bug 1 - Ą

Differential Revision: http://example.test/D123

"""
    assert log == expected


def test_submit_different_author_arc(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        [{"userName": "alice", "phid": "PHID-USER-1"}],
    )
    testfile = git_repo_path / "X"
    testfile.write_text("a")
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
    testfile.write_text("b")
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

    mozphab.main(
        ["submit", "--arc", "--yes", "--bug", "1", init_sha], is_development=True
    )

    log = git_out("log", "--format=%aD+++%an+++%ae", "-2")
    expected = """\
Tue, 22 Jan 2019 13:43:48 +0000+++bar+++bar@foo.com
Tue, 22 Jan 2019 13:42:48 +0000+++foo+++foo@bar.com
"""
    assert log == expected


def test_submit_utf8_author_arc(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        [{"userName": "alice", "phid": "PHID-USER-1"}],
    )
    testfile = git_repo_path / "X"
    testfile.write_text("a")
    git_out("add", ".")
    git_out(
        "commit",
        "--date",
        "Tue, 22 Jan 2019 13:42:48 +0000",
        "--author",
        "ćwikła <ćwikła@bar.com>",
        "--message",
        "A r?alice",
    )

    mozphab.main(
        ["submit", "--arc", "--yes", "--bug", "1", init_sha], is_development=True
    )

    log = git_out("log", "--format=%aD+++%an+++%ae", "-1")
    expected = "Tue, 22 Jan 2019 13:42:48 +0000+++ćwikła+++ćwikła@bar.com\n"
    assert log == expected


def test_submit_update_arc(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        {},  # ping
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        {  # differential.revision.search
            "data": [
                {
                    "fields": {
                        "bugzilla.bug-id": "1",
                        "status": {"value": "needs-review"},
                        "authorPHID": "PHID-USER-1",
                    },
                    "phid": "PHID-DREV-y7x5hvdpe2gyerctdqqz",
                    "id": 123,
                    "attachments": {"reviewers": {"reviewers": []}},
                }
            ]
        },
        dict(phid="PHID-USER-1"),
    )
    testfile = git_repo_path / "X"
    testfile.write_text("a")
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

    mozphab.main(
        ["submit", "--arc", "--yes"]
        + ["--bug", "1"]
        + ["--message", "update message ćwikła"]
        + [init_sha],
        is_development=True,
    )

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """\
Bug 1 - A

Differential Revision: http://example.test/D123

"""
    assert log == expected


def test_submit_update_bug_id_arc(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        {
            "data": [
                {
                    "id": 123,
                    "phid": "PHID-REV-1",
                    "fields": {
                        "bugzilla.bug-id": "1",
                        "status": {"value": "needs-review"},
                        "authorPHID": "PHID-USER-1",
                    },
                    "attachments": {"reviewers": {"reviewers": []}},
                }
            ]
        },  # get reviewers for updated revision
        dict(phid="PHID-USER-1"),
    )
    arc_call_conduit.reset_mock()
    arc_call_conduit.side_effect = (
        {},
        {"data": {}},
    )
    testfile = git_repo_path / "X"
    testfile.write_text("a")
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

    mozphab.main(
        ["submit", "--arc", "--yes", "--bug", "2", init_sha], is_development=True
    )

    arc_call_conduit.assert_called_with(
        "differential.revision.edit",
        {
            "objectIdentifier": "D123",
            "transactions": [{"type": "bugzilla.bug-id", "value": "2"}],
        },
        mock.ANY,
    )


def test_submit_update_revision_not_found(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        # response for searching D123 and D124
        dict(
            data=[
                {
                    "fields": {
                        "bugzilla.bug-id": "1",
                        "status": {"value": "needs-review"},
                    },
                    "phid": "PHID-DREV-y7x5hvdpe2gyerctdqqz",
                    "id": 123,
                    "attachments": {"reviewers": {"reviewers": []}},
                }
            ]
        ),
        # moz-phab asks again for D124
        dict(data=[]),
        # moz-phab asks again for D124
        dict(data=[]),
        # moz-phab asks again for D124
        dict(data=[]),
    )
    testfile = git_repo_path / "X"
    testfile.write_text(u"ą")
    git_out("add", ".")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        u"""\
Bug 1 - Ą

Differential Revision: http://example.test/D123
"""
    )
    git_out("commit", "--file", "msg")
    testfile.write_text(u"missing repo")
    msgfile.write_text(
        u"""\
Bug 1 - missing revision

Differential Revision: http://example.test/D124
"""
    )
    git_out("commit", "--all", "--file", "./msg")

    with pytest.raises(exceptions.Error) as excinfo:
        mozphab.main(
            ["submit", "--yes"]
            + ["--bug", "1"]
            + ["--message", "update message ćwikła"]
            + [init_sha],
            is_development=True,
        )
    assert "query result for revision D124" in str(excinfo.value)


def test_empty_file(in_process, git_repo_path, init_sha):
    # Add an empty file
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    testfile = git_repo_path / "X"
    testfile.touch()
    git_out("add", ".")
    git_out("commit", "--message", "A")

    mozphab.main(["submit", "--yes", "--bug", "1", init_sha], is_development=True)

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """
Bug 1 - A

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()

    # Rempve an empty file
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-2", diffid="2")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="124")),
    )
    testfile.unlink()
    git_out("commit", "-a", "--message", "B")
    mozphab.main(["submit", "--yes", "--bug", "1", "HEAD~"], is_development=True)
    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """
Bug 1 - B

Differential Revision: http://example.test/D124
"""
    assert log.strip() == expected.strip()
