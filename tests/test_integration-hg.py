# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import imp
import mock
import os
import shutil

from callee import Contains
from .conftest import hg_out

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)
mozphab.SHOW_SPINNER = False

# Fail if arc ping is called
arc_ping = mock.Mock()
arc_ping.return_value = False

call_conduit = mock.Mock()
call_conduit.side_effect = ({}, [{"userName": "alice", "phid": "PHID-USER-1"}])

check_call_by_line = mozphab.check_call_by_line


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
    # test_a = hg_repo_path / "X"
    # test_a.write_text("a")
    # hg_out("add")
    # hg_out("commit", "--message", "Ą r?alice")
    # mozphab.main(["submit", "--no-arc", "--yes", "--bug", "1", "."])

    test_a = hg_repo_path / "A to rename"
    test_a.write_text("rename me\nsecond line")
    test_b = hg_repo_path / "B to remove"
    test_b.write_text("remove me")
    test_c = hg_repo_path / "C to modify"
    test_c.write_text("modify me")
    hg_out("add")
    hg_out("commit", "-m", "first")
    subdir = hg_repo_path / "subdir"
    subdir.mkdir()
    test_d = hg_repo_path / "subdir" / "D add"
    test_d.write_text("added")
    test_a.rename(hg_repo_path / "A renamed")
    test_b.unlink()
    test_c.write_text("modified")
    hg_out("addremove")
    msgfile = hg_repo_path / "msg"
    msgfile.write_text("Ą r?alice")
    hg_out("commit", "-l", "msg")
    mozphab.main(["submit", "--no-arc", "--yes", "--bug", "1", "."])

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
                                "oldLength": 1,
                                "newOffset": 0,
                                "newLength": 0,
                                "addLines": 0,
                                "delLines": 1,
                                "isMissingOldNewline": False,
                                "isMissingNewNewline": False,
                                "corpus": "-remove me",
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
                                    "-modify me\n\\ No newline at end of file\n"
                                    "+modified\n\\ No newline at end of file\n"
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
                        "currentPath": "subdir/D add",
                        "type": 1,  # ADD
                        "hunks": [
                            {
                                "corpus": "+added",
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

    mozphab.main(["submit", "--no-arc", "--yes", "--bug", "1", "."])

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
    mozphab.main(["submit", "--no-arc", "--yes", "--bug", "1", "."])
    call_conduit.reset_mock()
    # removing CR, leaving LF
    test_a.write_text("a\nb\n")
    hg_out("commit", "--message", "B r?alice")
    mozphab.main(["submit", "--no-arc", "--yes", "--bug", "1", "."])

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
