# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import mock
import shutil

from callee import Contains
from .conftest import hg_out

from mozphab import mozphab

mozphab.SHOW_SPINNER = False

# Fail if arc ping is called
arc_ping = mock.Mock()
arc_ping.return_value = False

call_conduit = mock.Mock()
call_conduit.side_effect = ({}, [{"userName": "alice", "phid": "PHID-USER-1"}])


def test_submit_create(in_process, hg_repo_path):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        [dict(userName="alice", phid="PHID-USER-1")],
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    test_a = hg_repo_path / "A to rename"
    test_a.write_text("rename me\nsecond line\n")
    test_b = hg_repo_path / "B to remove"
    test_b.write_text("remove me\n")
    test_c = hg_repo_path / "C to modify"
    test_c.write_text("modify me\n")
    test_d = hg_repo_path / "D to copy"
    test_d.write_text("copy me\n")
    hg_out("add")
    hg_out("commit", "-m", "first")
    subdir = hg_repo_path / "subdir"
    subdir.mkdir()
    hg_out("copy", "D to copy", "D copied")
    test_e = hg_repo_path / "subdir" / "E add"
    test_e.write_text("added\n")
    test_a.rename(hg_repo_path / "A renamed")
    test_b.unlink()
    test_c.write_text("modified\n")
    hg_out("addremove")
    msgfile = hg_repo_path / "msg"
    msgfile.write_text("Ą r?alice")
    hg_out("commit", "-l", "msg")
    mozphab.main(["submit", "--yes", "--bug", "1", "."], is_development=True)

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
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
            "differential.setdiffproperty",
            {"diff_id": "1", "name": "local:commits", "data": Contains('"rev": "')},
        )
        in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "differential.creatediff",
            {
                "sourceControlPath": "/",
                "sourceControlSystem": "hg",
                "lintStatus": "none",
                "sourcePath": mock.ANY,
                "unitStatus": "none",
                "sourceMachine": "http://example.test",
                "sourceControlBaseRevision": mock.ANY,
                "repositoryPHID": "PHID-REPO-1",
                "branch": "default",
                "changes": [
                    {
                        "currentPath": "A renamed",
                        "type": 6,  # MOVE_HERE
                        "hunks": [
                            {
                                "oldOffset": 1,
                                "oldLength": 2,
                                "newOffset": 1,
                                "newLength": 2,
                                "addLines": 0,
                                "delLines": 0,
                                "isMissingOldNewline": False,
                                "isMissingNewNewline": False,
                                "corpus": " rename me\n second line\n",
                            }
                        ],
                        "oldProperties": {},
                        "oldPath": "A to rename",
                        "commitHash": mock.ANY,
                        "awayPaths": [],
                        "metadata": {},
                        "newProperties": {},
                        "fileType": 1,
                    },
                    {
                        "currentPath": "A to rename",
                        "type": 4,  # MOVE_AWAY
                        "hunks": [],
                        "oldProperties": {},
                        "oldPath": None,
                        "commitHash": mock.ANY,
                        "awayPaths": ["A renamed"],
                        "metadata": {},
                        "newProperties": {},
                        "fileType": 1,
                    },
                    {
                        "currentPath": "B to remove",
                        "type": 3,  # DELETE
                        "hunks": [
                            {
                                "oldOffset": 1,
                                "oldLength": 1,
                                "newOffset": 0,
                                "newLength": 0,
                                "addLines": 0,
                                "delLines": 1,
                                "isMissingOldNewline": False,
                                "isMissingNewNewline": False,
                                "corpus": "-remove me\n",
                            }
                        ],
                        "awayPaths": [],
                        "fileType": 1,
                        "oldPath": "B to remove",
                        "newProperties": {},
                        "commitHash": mock.ANY,
                        "metadata": {},
                        "oldProperties": {"unix:filemode": "100644"},
                    },
                    {
                        "currentPath": "C to modify",
                        "type": 2,  # CHANGE
                        "hunks": [
                            {
                                "oldOffset": 1,
                                "oldLength": 1,
                                "newOffset": 1,
                                "newLength": 1,
                                "addLines": 1,
                                "delLines": 1,
                                "isMissingOldNewline": False,
                                "isMissingNewNewline": False,
                                "corpus": "-modify me\n+modified\n",
                            }
                        ],
                        "commitHash": mock.ANY,
                        "metadata": {},
                        "fileType": 1,
                        "oldPath": "C to modify",
                        "newProperties": {},
                        "awayPaths": [],
                        "oldProperties": {},
                    },
                    {
                        "metadata": {},
                        "oldPath": "D to copy",
                        "currentPath": "D copied",
                        "awayPaths": [],
                        "oldProperties": {},
                        "newProperties": {},
                        "commitHash": mock.ANY,
                        "type": 7,  # COPY HERE
                        "fileType": 1,
                        "hunks": [
                            {
                                "oldOffset": 1,
                                "oldLength": 1,
                                "newOffset": 1,
                                "newLength": 1,
                                "addLines": 0,
                                "delLines": 0,
                                "isMissingOldNewline": False,
                                "isMissingNewNewline": False,
                                "corpus": " copy me\n",
                            }
                        ],
                    },
                    {
                        "metadata": {},
                        "oldPath": None,
                        "currentPath": "D to copy",
                        "awayPaths": ["D copied"],
                        "oldProperties": {},
                        "newProperties": {},
                        "commitHash": mock.ANY,
                        "type": 5,  # COPY AWAY
                        "fileType": 1,
                        "hunks": [],
                    },
                    {
                        "currentPath": "subdir/E add",
                        "type": 1,  # ADD
                        "hunks": [
                            {
                                "corpus": "+added\n",
                                "addLines": 1,
                                "oldOffset": 0,
                                "newOffset": 1,
                                "newLength": 1,
                                "delLines": 0,
                                "isMissingOldNewline": False,
                                "oldLength": 0,
                                "isMissingNewNewline": False,
                            }
                        ],
                        "commitHash": mock.ANY,
                        "awayPaths": [],
                        "newProperties": {"unix:filemode": "100644"},
                        "oldPath": None,
                        "oldProperties": {},
                        "fileType": 1,
                        "metadata": {},
                    },
                ],
                "creationMethod": "moz-phab-hg",
            },
        )
        in call_conduit.call_args_list
    )


def test_submit_create_no_trailing_newline(in_process, hg_repo_path):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        [dict(userName="alice", phid="PHID-USER-1")],
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    test_a = hg_repo_path / "A to rename"
    test_a.write_text("rename me\nsecond line")
    test_b = hg_repo_path / "B to remove"
    test_b.write_text("remove me")
    test_c = hg_repo_path / "C to modify"
    test_c.write_text("modify me")
    test_d = hg_repo_path / "D to copy"
    test_d.write_text("copy me")
    hg_out("add")
    hg_out("commit", "-m", "first")
    subdir = hg_repo_path / "subdir"
    subdir.mkdir()
    hg_out("copy", "D to copy", "D copied")
    test_e = hg_repo_path / "subdir" / "E add"
    test_e.write_text("added")
    test_a.rename(hg_repo_path / "A renamed")
    test_b.unlink()
    test_c.write_text("modified")
    hg_out("addremove")
    msgfile = hg_repo_path / "msg"
    msgfile.write_text("Ą r?alice")
    hg_out("commit", "-l", "msg")
    mozphab.main(["submit", "--yes", "--bug", "1", "."], is_development=True)

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
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
            "differential.setdiffproperty",
            {"diff_id": "1", "name": "local:commits", "data": Contains('"rev": "')},
        )
        in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "differential.creatediff",
            {
                "sourceControlPath": "/",
                "sourceControlSystem": "hg",
                "lintStatus": "none",
                "sourcePath": mock.ANY,
                "unitStatus": "none",
                "sourceMachine": "http://example.test",
                "sourceControlBaseRevision": mock.ANY,
                "repositoryPHID": "PHID-REPO-1",
                "branch": "default",
                "changes": [
                    {
                        "currentPath": "A renamed",
                        "type": 6,  # MOVE_HERE
                        "hunks": [
                            {
                                "oldOffset": 1,
                                "oldLength": 2,
                                "newOffset": 1,
                                "newLength": 2,
                                "addLines": 0,
                                "delLines": 0,
                                "isMissingOldNewline": False,
                                "isMissingNewNewline": False,
                                "corpus": " rename me\n second line",
                            }
                        ],
                        "oldProperties": {},
                        "oldPath": "A to rename",
                        "commitHash": mock.ANY,
                        "awayPaths": [],
                        "metadata": {},
                        "newProperties": {},
                        "fileType": 1,
                    },
                    {
                        "currentPath": "A to rename",
                        "type": 4,  # MOVE_AWAY
                        "hunks": [],
                        "oldProperties": {},
                        "oldPath": None,
                        "commitHash": mock.ANY,
                        "awayPaths": ["A renamed"],
                        "metadata": {},
                        "newProperties": {},
                        "fileType": 1,
                    },
                    {
                        "currentPath": "B to remove",
                        "type": 3,  # DELETE
                        "hunks": [
                            {
                                "oldOffset": 1,
                                "oldLength": 2,
                                "newOffset": 0,
                                "newLength": 0,
                                "addLines": 0,
                                "delLines": 1,
                                "isMissingOldNewline": True,
                                "isMissingNewNewline": False,
                                "corpus": "-remove me\n\\ No newline at end of file\n",
                            }
                        ],
                        "awayPaths": [],
                        "fileType": 1,
                        "oldPath": "B to remove",
                        "newProperties": {},
                        "commitHash": mock.ANY,
                        "metadata": {},
                        "oldProperties": {"unix:filemode": "100644"},
                    },
                    {
                        "currentPath": "C to modify",
                        "type": 2,  # CHANGE
                        "hunks": [
                            {
                                "oldOffset": 1,
                                "oldLength": 1,
                                "newOffset": 1,
                                "newLength": 1,
                                "addLines": 1,
                                "delLines": 1,
                                "isMissingOldNewline": True,
                                "isMissingNewNewline": True,
                                "corpus": (
                                    "-modify me\n"
                                    "\\ No newline at end of file\n"
                                    "+modified\n"
                                    "\\ No newline at end of file\n"
                                ),
                            }
                        ],
                        "commitHash": mock.ANY,
                        "metadata": {},
                        "fileType": 1,
                        "oldPath": "C to modify",
                        "newProperties": {},
                        "awayPaths": [],
                        "oldProperties": {},
                    },
                    {
                        "metadata": {},
                        "oldPath": "D to copy",
                        "currentPath": "D copied",
                        "awayPaths": [],
                        "oldProperties": {},
                        "newProperties": {},
                        "commitHash": mock.ANY,
                        "type": 7,  # COPY HERE
                        "fileType": 1,
                        "hunks": [
                            {
                                "oldOffset": 1,
                                "oldLength": 1,
                                "newOffset": 1,
                                "newLength": 1,
                                "addLines": 0,
                                "delLines": 0,
                                "isMissingOldNewline": False,
                                "isMissingNewNewline": False,
                                "corpus": " copy me",
                            }
                        ],
                    },
                    {
                        "metadata": {},
                        "oldPath": None,
                        "currentPath": "D to copy",
                        "awayPaths": ["D copied"],
                        "oldProperties": {},
                        "newProperties": {},
                        "commitHash": mock.ANY,
                        "type": 5,  # COPY AWAY
                        "fileType": 1,
                        "hunks": [],
                    },
                    {
                        "currentPath": "subdir/E add",
                        "type": 1,  # ADD
                        "hunks": [
                            {
                                "corpus": "+added\n\\ No newline at end of file\n",
                                "addLines": 1,
                                "oldOffset": 0,
                                "newOffset": 1,
                                "newLength": 2,
                                "delLines": 0,
                                "isMissingOldNewline": False,
                                "oldLength": 0,
                                "isMissingNewNewline": True,
                            }
                        ],
                        "commitHash": mock.ANY,
                        "awayPaths": [],
                        "newProperties": {"unix:filemode": "100644"},
                        "oldPath": None,
                        "oldProperties": {},
                        "fileType": 1,
                        "metadata": {},
                    },
                ],
                "creationMethod": "moz-phab-hg",
            },
        )
        in call_conduit.call_args_list
    )


def test_submit_create_no_bug(in_process, hg_repo_path):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        [dict(userName="alice", phid="PHID-USER-1")],
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    test_a = hg_repo_path / "x"
    test_a.write_text("a")
    hg_out("add")
    hg_out("commit", "--message", "A r?alice")
    mozphab.main(["submit", "--yes", "--no-bug", "."], is_development=True)

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """
A r?alice

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()


def test_submit_create_binary(in_process, hg_repo_path, data_file):
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        # file upload
        dict(),
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    shutil.copyfile(str(data_file), str(hg_repo_path / "img.png"))
    hg_out("add")
    hg_out("commit", "-m", "IMG")

    mozphab.main(["submit", "--yes", "--bug", "1", "."], is_development=True)

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """
Bug 1 - IMG

Differential Revision: http://example.test/D123
"""
    assert log.strip() == expected.strip()


def test_submit_remove_cr(in_process, hg_repo_path):
    call_conduit.side_effect = (
        # CREATE
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
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
    test_a = hg_repo_path / "X"
    test_a.write_text("a\r\nb\n")
    hg_out("add")
    hg_out("commit", "--message", "A r?alice")
    mozphab.main(["submit", "--yes", "--bug", "1", "."], is_development=True)
    call_conduit.reset_mock()
    # removing CR, leaving LF
    test_a.write_text("a\nb\n")
    hg_out("commit", "--message", "B r?alice")
    mozphab.main(["submit", "--yes", "--bug", "1", "."], is_development=True)

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
                "sourceControlSystem": "hg",
                "sourceControlPath": "/",
                "sourceControlBaseRevision": mock.ANY,
                "creationMethod": "moz-phab-hg",
                "lintStatus": "none",
                "unitStatus": "none",
                "repositoryPHID": "PHID-REPO-1",
                "sourcePath": mock.ANY,
                "branch": "default",
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


def test_submit_single_first(in_process, hg_repo_path, hg_sha):
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    (hg_repo_path / "X").write_text("a\n")
    hg_out("add", "X")
    hg_out("commit", "-m", "A")
    sha = hg_sha()
    (hg_repo_path / "X").write_text("b\n")
    hg_out("commit", "-m", "B")

    mozphab.main(
        ["submit", "--yes", "--bug", "1", "--single", sha], is_development=True
    )

    log = hg_out("log", "--template", r"{desc}\n---\n", "--limit", "2")
    expected = """\
B
---
Bug 1 - A

Differential Revision: http://example.test/D123
---
"""
    assert log == expected


def test_submit_single_last(in_process, hg_repo_path):
    call_conduit.side_effect = (
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        # differential.creatediff
        dict(dict(phid="PHID-DIFF-1", diffid="1")),
        # differential.setdiffproperty
        dict(),
        # differential.revision.edit
        dict(object=dict(id="123")),
    )
    (hg_repo_path / "X").write_text("a\n")
    hg_out("add", "X")
    hg_out("commit", "-m", "A")
    (hg_repo_path / "X").write_text("b\n")
    hg_out("commit", "-m", "B")

    mozphab.main(["submit", "--yes", "--bug", "1", "--single"], is_development=True)

    log = hg_out("log", "--template", r"{desc}\n---\n", "--limit", "2")
    expected = """\
Bug 1 - B

Differential Revision: http://example.test/D123
---
A
---
"""
    assert log == expected
