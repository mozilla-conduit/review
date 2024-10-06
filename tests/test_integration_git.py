# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import os
import platform
import shutil
from unittest import mock

import pytest
from callee import Contains, Matching

from mozphab import exceptions, mozphab

from .conftest import git_out, search_diff, search_rev

call_conduit = mock.Mock()


def by_line_mock(*args, **_kwargs):
    # join args to catch unicode errors
    " ".join(*args)
    return ["Revision URI: http://example.test/D123"]


check_call_by_line = mock.Mock()
check_call_by_line.side_effect = by_line_mock

initial_sha = None


def test_submit_create(in_process, git_repo_path, init_sha):
    call_conduit.side_effect = (
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # user search
        [{"userName": "alice", "phid": "PHID-USER-1"}],
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
    )
    (git_repo_path / "X").write_text("ą\nb\nc\n", encoding="utf-8")
    (git_repo_path / "Y").write_text("no line ending")
    git_out("add", ".")
    (git_repo_path / "msg").write_text("Ą r?alice", encoding="utf-8")
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
        mock.call("user.query", {"usernames": ["alice"]}) in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "diffusion.repository.search",
            {"limit": 1, "constraints": {"callsigns": ["TEST"]}},
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
                                "corpus": "+ą\n+b\n+c\n",
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
    call_conduit.assert_any_call(
        "differential.revision.edit",
        {
            "transactions": [
                {"type": "title", "value": "Bug 1 - Ą r?alice"},
                {"type": "summary", "value": ""},
                {"type": "reviewers.set", "value": ["PHID-USER-1"]},
                {"type": "bugzilla.bug-id", "value": "1"},
                {"type": "update", "value": "PHID-DIFF-1"},
            ]
        },
    )


def test_submit_create_added_not_commited(in_process, git_repo_path, init_sha):
    call_conduit.side_effect = (
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # user search
        [{"userName": "alice", "phid": "PHID-USER-1"}],
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
    )
    (git_repo_path / "X").write_text("ą\r\nb\nc\n", encoding="utf-8")
    (git_repo_path / "Y").write_text("no line ending")
    git_out("add", ".")
    (git_repo_path / "msg").write_text("Ą r?alice", encoding="utf-8")
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
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # user search
        [{"userName": "alice", "phid": "PHID-USER-1"}],
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
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


def test_submit_create_binary(in_process, git_repo_path, init_sha, data_file):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # file.allocate
        {"filePHID": None, "upload": True},
        # file.upload
        {},
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
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
    assert (
        mock.call(
            "file.allocate",
            {"name": "img.png", "contentHash": mock.ANY, "contentLength": 182},
        )
        in call_conduit.call_args_list
    )
    assert (
        mock.call("file.upload", {"data_base64": mock.ANY, "name": "img.png"})
        in call_conduit.call_args_list
    )


def test_submit_create_binary_existing(in_process, git_repo_path, init_sha, data_file):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # file.allocate
        {"filePHID": "PHID-FILE-1", "upload": False},
        # no file.upload call
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
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
    assert (
        mock.call(
            "file.allocate",
            {"name": "img.png", "contentHash": mock.ANY, "contentLength": 182},
        )
        in call_conduit.call_args_list
    )
    assert mock.call("file.upload", mock.ANY) not in call_conduit.call_args_list


def test_submit_create_binary_chunked(in_process, git_repo_path, init_sha, data_file):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # file.allocate
        {"filePHID": "PHID-FILE-1", "upload": True},
        # file.querychunks
        [
            {"byteStart": "0", "byteEnd": "4194304", "complete": False},
            {"byteStart": "4194304", "byteEnd": "8388608", "complete": False},
            {"byteStart": "8388608", "byteEnd": "8425160", "complete": False},
        ],
        # file.uploadchunk
        {},
        # file.uploadchunk
        {},
        # file.uploadchunk
        {},
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
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
    assert (
        mock.call(
            "file.allocate",
            {"name": "img.png", "contentHash": mock.ANY, "contentLength": 182},
        )
        in call_conduit.call_args_list
    )
    assert (
        mock.call("file.querychunks", {"filePHID": "PHID-FILE-1"})
        in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "file.uploadchunk",
            {
                "filePHID": "PHID-FILE-1",
                "byteStart": 0,
                "data": mock.ANY,
                "dataEncoding": "base64",
            },
        )
        in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "file.uploadchunk",
            {
                "filePHID": "PHID-FILE-1",
                "byteStart": 4194304,
                "data": mock.ANY,
                "dataEncoding": "base64",
            },
        )
        in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "file.uploadchunk",
            {
                "filePHID": "PHID-FILE-1",
                "byteStart": 8388608,
                "data": mock.ANY,
                "dataEncoding": "base64",
            },
        )
        in call_conduit.call_args_list
    )


def test_submit_update(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = [
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # diffusion.revision.search
        {"data": [search_rev(rev=123, reviewers=["alice"])]},
        # diffusion.diff.search
        {"data": [search_diff()]},
        # whoami
        {"phid": "PHID-USER-1"},
        # user.query
        [{"phid": "PHID-USER-2", "userName": "alice"}],
        # differential.creatediff
        {"phid": "PHID-DIFF-2", "diffid": "2"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
    ]
    testfile = git_repo_path / "X"
    testfile.write_text("ą", encoding="utf-8")
    git_out("add", "X")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        "Bug 1 - Ą r?alice\n\nDifferential Revision: http://example.test/D123",
        encoding="utf-8",
    )
    git_out("commit", "--file", "msg")

    mozphab.main(
        ["submit", "--yes", "--message", "update message ćwikła", init_sha],
        is_development=True,
    )

    log = git_out("log", "--format=%s%n%n%b", "-1").strip()
    expected = "Bug 1 - Ą r?alice\n\nDifferential Revision: http://example.test/D123"
    assert log == expected
    call_conduit.assert_any_call(
        "differential.creatediff",
        Matching(lambda x: x.get("repositoryPHID") == "PHID-REPO-1"),
    )
    call_conduit.assert_any_call(
        "differential.revision.edit",
        {
            "objectIdentifier": 123,
            "transactions": [
                {"type": "title", "value": "Bug 1 - Ą r?alice"},
                {"type": "summary", "value": ""},
                {"type": "comment", "value": "update message ćwikła"},
                {"type": "update", "value": "PHID-DIFF-2"},
            ],
        },
    )


def test_submit_update_uplift(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = [
        # conduit.ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # differential.revision.search
        {"data": [search_rev(repo="PHID-BETA-2")]},
        # differential.diff.search
        {"data": [search_diff()]},
        # user.whoami
        {"phid": "PHID-USER-1"},
        # differential.creatediff
        {"phid": "PHID-DIFF-2", "diffid": "2"},
        # differential.revision.edit
        {"object": {"id": "1", "phid": "PHID-DREV-1"}},
        # differential.setdiffproperty
        {},
    ]
    testfile = git_repo_path / "X"
    testfile.write_text("ą", encoding="utf-8")
    git_out("add", "X")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        "Bug 1 - Ą\n\n"
        "Original Revision: http://example.test/D999\n\n"
        "Differential Revision: http://example.test/D1",
        encoding="utf-8",
    )
    git_out("commit", "--file", "msg")

    mozphab.main(["submit", "-y", init_sha], is_development=True)

    log = git_out("log", "--format=%s%n%n%b", "-1").strip()
    expected = (
        "Bug 1 - Ą\n\n"
        "Original Revision: http://example.test/D999\n\n"
        "Differential Revision: http://example.test/D1"
    )
    assert log == expected
    call_conduit.assert_any_call(
        "differential.creatediff",
        Matching(lambda x: x.get("repositoryPHID") == "PHID-BETA-2"),
    )


@mock.patch("mozphab.commands.submit.logger.warning")
def test_submit_update_no_change(
    m_logger_warning, in_process, git_repo_path, init_sha, git_sha
):
    testfile = git_repo_path / "X"
    testfile.write_text("a")
    git_out("add", ".")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1 - A, r=test

Differential Revision: http://example.test/D123
"""
    )
    git_out("commit", "--file", "msg")
    sha = git_sha()
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # diffusion.revision.search
        {"data": [search_rev(rev=123, reviewers=("test",))]},
        # diffusion.diff.search
        {"data": [search_diff(node=sha)]},
        # whoami
        {"phid": "PHID-USER-1"},
        # user.query
        [{"phid": "PHID-USER-2", "userName": "test"}],
    )

    mozphab.main(
        ["submit", "--yes"] + [init_sha],
        is_development=True,
    )
    assert call_conduit.call_count == 6
    m_logger_warning.assert_called_with("No changes to submit.")


def test_submit_remove_cr(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = [
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # user.search
        [{"userName": "alice", "phid": "PHID-USER-1"}],
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
    ]
    test_a = git_repo_path / "X"
    with open(test_a, "w", newline="") as f:
        f.write("a\r\nb\n")
    git_out("-c", "core.autocrlf=false", "add", "X")
    git_out("-c", "core.autocrlf=false", "commit", "-m", "A r?alice")
    # removing CR, leaving LF
    with open(test_a, "w", newline="") as f:
        f.write("a\nb\n")
    git_out("-c", "core.autocrlf=false", "add", "X")
    git_out("-c", "core.autocrlf=false", "commit", "-m", "B r?alice")

    mozphab.main(["submit", "--yes", "--bug", "1", "-s"], is_development=True)

    call_conduit.assert_any_call(
        "differential.creatediff",
        Matching(lambda x: x["changes"][0]["hunks"][0]["corpus"] == "-a\r\n+a\n b\n"),
    )


def test_submit_remove_form_feed(in_process, git_repo_path, init_sha):
    """Test deleting a file with a form feed character will not corrupt the diff."""
    call_conduit.side_effect = (
        # CREATE
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # user.search
        [{"userName": "alice", "phid": "PHID-USER-1"}],
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
    )

    test_a = git_repo_path / "X"
    test_a.write_text("some line\fcontaining form feed\na second line\n")
    git_out("add", "X")
    git_out("commit", "-am", "A r?alice")

    # delete file
    test_a.unlink()
    git_out("commit", "-am", "B r?alice")

    call_conduit.reset_mock()
    mozphab.main(["submit", "--yes", "--bug", "1", "HEAD~"], is_development=True)

    failing = True
    for arg in call_conduit.call_args_list:
        if arg[0][0] == "differential.creatediff":
            assert arg[0][1]["changes"][0]["hunks"][0]["corpus"] == (
                "-some line\fcontaining form feed\n-a second line\n"
            )
            failing = False

    assert not failing


def test_submit_single_last(in_process, git_repo_path, init_sha):
    call_conduit.side_effect = (
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
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
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
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
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        {"data": [search_rev(rev=123)]},
        {"data": [search_diff()]},
        {"phid": "PHID-USER-1"},
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
    )
    (git_repo_path / "X").write_text("ą", encoding="utf-8")
    git_out("add", ".")
    (git_repo_path / "msg").write_text(
        """\
Bug 1 - Ą

Differential Revision: http://example.test/D123
""",
        encoding="utf-8",
    )
    git_out("commit", "--file", "msg")

    mozphab.main(["submit", "--yes", "--bug", "1", init_sha], is_development=True)

    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """\
Bug 1 - Ą

Differential Revision: http://example.test/D123

"""
    assert log == expected


def test_submit_update_revision_not_found(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = [
        # conduit.ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # differential.revision.search
        {"data": []},
        # moz-phab asks again for D124
        {"data": []},
    ]
    testfile = git_repo_path / "X"
    testfile.write_text("ą", encoding="utf-8")
    git_out("add", "X")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        "missing revision\n\nDifferential Revision: http://example.test/D124",
        encoding="utf-8",
    )
    git_out("commit", "--file", "msg")

    with pytest.raises(exceptions.Error) as excinfo:
        mozphab.main(
            ["submit", "--yes"]
            + ["--bug", "1"]
            + ["--no-wip"]
            + ["--message", "update message ćwikła"]
            + [init_sha],
            is_development=True,
        )

    assert "query result for revision D124" in str(excinfo.value)


def test_uplift(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = [
        # diffusion.repository.search
        {
            "data": [
                {
                    "phid": "PHID-BETA-2",
                    "fields": {"shortName": "beta", "callsign": "BETA", "vcs": "git"},
                }
            ]
        },
        # diffusion.repository.search
        {"data": [{"fields": {"shortName": "beta"}}]},
        # conduit.ping
        {},
        # differential.revision.search
        {"data": [search_rev()]},
        # differential.creatediff
        {"phid": "PHID-DIFF-2", "diffid": "2"},
        # differential.revision.edit
        {"object": {"id": "2", "phid": "PHID-DREV-2"}},
        # differential.setdiffproperty
        {},
    ]
    testfile = git_repo_path / "X"
    testfile.write_text("ą", encoding="utf-8")
    git_out("add", "X")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        "Bug 1 - Ą r=alice\n\nDifferential Revision: http://example.test/D1",
        encoding="utf-8",
    )
    git_out("commit", "--file", "msg")

    mozphab.main(["uplift", "-y", "--train", "beta", init_sha], is_development=True)

    call_conduit.assert_any_call(
        "differential.creatediff",
        Matching(lambda x: x.get("repositoryPHID") == "PHID-BETA-2"),
    )
    call_conduit.assert_any_call(
        "differential.revision.edit",
        {
            "transactions": [
                {"type": "title", "value": "Bug 1 - Ą"},
                {
                    "type": "summary",
                    "value": "\nOriginal Revision: http://example.test/D1",
                },
                {"type": "bugzilla.bug-id", "value": "1"},
                {"type": "update", "value": "PHID-DIFF-2"},
            ]
        },
    )
    call_conduit.assert_any_call(
        "differential.setdiffproperty",
        {
            "diff_id": "2",
            "name": "local:commits",
            "data": Contains('"summary": "Bug 1 - \\u0104"')
            & Contains(
                "Summary:\\n\\n"
                "Original Revision: http://example.test/D1\\n\\n"
                "Differential Revision: http://example.test/D2"
            ),
        },
    )


def test_empty_file(in_process, git_repo_path, init_sha):
    # Add an empty file
    call_conduit.side_effect = (
        # ping
        {},
        # diffusion.repository.search
        {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]},
        # differential.creatediff
        {"phid": "PHID-DIFF-1", "diffid": "1"},
        # differential.revision.edit
        {"object": {"id": "123", "phid": "PHID-DREV-123"}},
        # differential.setdiffproperty
        {},
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
    assert (
        mock.call(
            "differential.creatediff",
            {
                "changes": [
                    {
                        "metadata": {},
                        "oldPath": None,
                        "currentPath": "X",
                        "awayPaths": [],
                        "oldProperties": {},
                        "newProperties": {"unix:filemode": "100644"},
                        "commitHash": mock.ANY,
                        "type": 1,
                        "fileType": 1,
                        "hunks": [],
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
    ), (
        "The diff should have populated fields and "
        "`change.hunks` should be an empty list"
    )

    # Modify a file's mode
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # differential.creatediff
        {"phid": "PHID-DIFF-2", "diffid": "2"},
        # differential.revision.edit
        {"object": {"id": "124", "phid": "PHID-DREV-124"}},
        # differential.setdiffproperty
        {},
    )
    # Windows cannot update UMASK bits in the same way Unix
    # systems can, so use Git's update-index command to
    # directly modify the index for the file.
    if platform.system() == "Windows":
        git_out("update-index", "--chmod=+x", str(testfile))
    else:
        os.chmod(testfile, 0o0755)
    git_out("commit", "-a", "--message", "B")
    mozphab.main(["submit", "--yes", "--bug", "1", "HEAD~"], is_development=True)
    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """
Bug 1 - B

Differential Revision: http://example.test/D124
"""
    assert log.strip() == expected.strip()
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
                        "oldProperties": {"unix:filemode": "100644"},
                        "newProperties": {"unix:filemode": "100755"},
                        "commitHash": mock.ANY,
                        "type": 2,
                        "fileType": 1,
                        "hunks": [],
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
    ), (
        "The diff should contain filemode changes and "
        "`change.hunks` should be an empty list"
    )

    # Remove an empty file
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # differential.creatediff
        {"phid": "PHID-DIFF-3", "diffid": "3"},
        # differential.revision.edit
        {"object": {"id": "125", "phid": "PHID-DREV-125"}},
        # differential.setdiffproperty
        {},
    )
    testfile.unlink()
    git_out("commit", "-a", "--message", "C")
    mozphab.main(["submit", "--yes", "--bug", "1", "HEAD~"], is_development=True)
    log = git_out("log", "--format=%s%n%n%b", "-1")
    expected = """
Bug 1 - C

Differential Revision: http://example.test/D125
"""
    assert log.strip() == expected.strip()
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
                        "oldProperties": {"unix:filemode": "100755"},
                        "newProperties": {},
                        "commitHash": mock.ANY,
                        "type": 3,
                        "fileType": 1,
                        "hunks": [],
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
    ), (
        "The diff should have empty `newProperties` and "
        "`change.hunks` should be an empty list"
    )
