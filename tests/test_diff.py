# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# coding=utf-8

from unittest import mock
import textwrap

from .conftest import assert_attributes

from mozphab import environment
from mozphab.diff import Diff


class Args:
    def __init__(self, less_context=False):
        self.lesscontext = less_context


@mock.patch("mozphab.git.Git._file_size")
@mock.patch("mozphab.git.Git._cat_file")
@mock.patch("mozphab.git.Git.git_out")
def test_create(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "000000 100644 0000000000000000000000000000000000000000 "
        "78981922613b2afb6025042ff6bd878ac1994e85 A\x00a"
    )
    diff = Diff()
    m_cat_file.side_effect = (b"a\n",)
    m_file_size.return_value = 5
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    m_git_out.assert_not_called()
    assert change.file_type.name == "TEXT"
    assert change.kind.name == "ADD"
    assert change.cur_mode == "100644"
    assert len(change.hunks) == 1
    assert_attributes(
        change.hunks[0],
        dict(
            old_off=0,
            old_len=0,
            new_off=1,
            new_len=1,
            old_eof_newline=True,
            new_eof_newline=True,
            added=1,
            deleted=0,
            corpus="+a\n",
        ),
    )


@mock.patch("mozphab.git.Git._file_size")
@mock.patch("mozphab.git.Git._cat_file")
@mock.patch("mozphab.git.Git.git_out")
def test_change_file(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "100644 100644 78981922613b2afb6025042ff6bd878ac1994e85 "
        "422c2b7ab3b3c668038da977e4e93a5fc623169c M\x00a"
    )
    diff = Diff()
    m_cat_file.side_effect = (b"a\n", b"a\nb\n")
    m_git_out.return_value = b"""\
diff --git a/78981922613b2afb6025042ff6bd878ac1994e85 \
b/422c2b7ab3b3c668038da977e4e93a5fc623169c
index 7898192..422c2b7 100644
--- a/78981922613b2afb6025042ff6bd878ac1994e85\n+++ \
b/422c2b7ab3b3c668038da977e4e93a5fc623169c
@@ -1 +1,2 @@
 a
+b"""
    m_file_size.return_value = 5
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    m_git_out.assert_called_once_with(
        [
            "diff",
            "--submodule=short",
            "--no-ext-diff",
            "--no-color",
            "--no-textconv",
            "-U%s" % environment.MAX_CONTEXT_SIZE,
            "78981922613b2afb6025042ff6bd878ac1994e85",
            "422c2b7ab3b3c668038da977e4e93a5fc623169c",
        ],
        expect_binary=True,
    )
    assert change.file_type.name == "TEXT"
    assert change.kind.name == "CHANGE"
    assert change.old_mode is None
    assert change.cur_mode is None
    assert change.old_path == "a"
    assert len(change.hunks) == 1
    assert_attributes(
        change.hunks[0],
        dict(
            old_off=1,
            old_len=1,
            new_off=1,
            new_len=2,
            old_eof_newline=True,
            new_eof_newline=True,
            added=1,
            deleted=0,
            corpus=" a\n+b",
        ),
    )


@mock.patch("mozphab.git.Git._file_size")
@mock.patch("mozphab.git.Git._cat_file")
@mock.patch("mozphab.git.Git.git_out")
def test_create_empty(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "000000 100644 0000000000000000000000000000000000000000 "
        "78981922613b2afb6025042ff6bd878ac1994e85 A\x00a"
    )
    diff = Diff()
    m_cat_file.side_effect = (b"",)
    m_file_size.return_value = 0
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    m_git_out.assert_not_called()
    assert change.file_type.name == "TEXT"
    assert change.hunks == []
    assert change.kind.name == "ADD"
    assert change.cur_mode == "100644"


@mock.patch("mozphab.git.Git._file_size")
@mock.patch("mozphab.git.Git._cat_file")
@mock.patch("mozphab.git.Git.git_out")
def test_change_empty(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "100644 100755 0000000000000000000000000000000000000000 "
        "78981922613b2afb6025042ff6bd878ac1994e85 M\x00a"
    )
    diff = Diff()
    m_cat_file.side_effect = (b"",)
    m_file_size.return_value = 0
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    m_git_out.assert_not_called()
    assert (
        change.file_type.name == "TEXT"
    ), "The file should be recognized as a TEXT file"
    assert (
        change.hunks == []
    ), "`change.hunks` should be an empty list when parsing empty files"
    assert (
        change.kind.name == "CHANGE"
    ), "File mode changes should be recognized as a CHANGE"
    assert (
        change.old_mode == "100644"
    ), "The file's original mode should be set to 100644"
    assert (
        change.cur_mode == "100755"
    ), "The file's current mode should be set to 100755"


@mock.patch("mozphab.mercurial.Mercurial.hg_out")
@mock.patch("mozphab.mercurial.Mercurial._get_file_meta")
@mock.patch("mozphab.mercurial.Mercurial._get_parent")
@mock.patch("mozphab.mercurial.Mercurial._get_file_modes")
@mock.patch("uuid.uuid4")
def test_change_empty_hg(
    m_uuid4, m_get_file_modes, m_get_parent, m_get_file_meta, m_hg_out, hg
):
    commit = {
        "name": "78981922613b",
        "node": "78981922613b2afb6025042ff6bd878ac1994e85",
        "orig-node": "78981922613b2afb6025042ff6bd878ac1994e85",
        "parent": "422c2b7ab3b3",
        "title": "test",
        "body": "test",
    }
    m_get_parent.return_value = "422c2b7ab3b3c668038da977e4e93a5fc623169c"
    m_get_file_modes.return_value = {
        "fn": {
            "old_mode": "100644",
            "new_mode": "100755",
        },
    }
    m_uuid4.side_effect = [
        mock.Mock(hex="abc123"),
        mock.Mock(hex="def456"),
    ]
    m_hg_out.side_effect = [
        "--abc123----def456----abc123----def456--fn--abc123----def456----abc123--",
    ]
    m_get_file_meta.side_effect = [
        dict(
            binary=False,
            bin_body=b"",
            body="",
            file_size=0,
        ),
        dict(
            binary=False,
            bin_body=b"",
            body="",
            file_size=0,
        ),
    ]

    hg.args = Args()
    diff = hg.get_diff(commit)
    change = diff.changes.get("fn")

    assert (
        change.kind.name == "CHANGE"
    ), "File mode changes should be recognized as a CHANGE"
    assert (
        change.old_mode == "100644"
    ), "The file's original mode should be set to 100644"
    assert (
        change.cur_mode == "100755"
    ), "The file's current mode should be set to 100755"
    assert (
        change.hunks == []
    ), "`change.hunks` should be an empty list when parsing empty files"


@mock.patch("mozphab.git.Git._file_size")
@mock.patch("mozphab.git.Git._cat_file")
@mock.patch("mozphab.git.Git.git_out")
def test_delete_file(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "100644 000000 61780798228d17af2d34fce4cfbdf35556832472 "
        "0000000000000000000000000000000000000000 D\x00a"
    )
    diff = Diff()
    m_cat_file.side_effect = (b"a\nb\n",)
    m_file_size.return_value = 5
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    m_git_out.assert_not_called()
    assert change.file_type.name == "TEXT"
    assert_attributes(
        change.hunks[0],
        dict(
            old_off=1,
            old_len=2,
            new_off=0,
            new_len=0,
            old_eof_newline=True,
            new_eof_newline=True,
            added=0,
            deleted=2,
            corpus="-a\n-b\n",
        ),
    )


@mock.patch("mozphab.git.Git._file_size")
@mock.patch("mozphab.git.Git._cat_file")
@mock.patch("mozphab.git.Git.git_out")
def test_delete_empty_file(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "100644 000000 61780798228d17af2d34fce4cfbdf35556832472 "
        "0000000000000000000000000000000000000000 D\x00a"
    )
    diff = Diff()
    m_cat_file.side_effect = (b"",)
    m_file_size.return_value = 0
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    m_git_out.assert_not_called()
    assert change.file_type.name == "TEXT"
    assert change.hunks == []


@mock.patch("mozphab.git.Git._file_size")
@mock.patch("mozphab.git.Git._cat_file")
@mock.patch("mozphab.git.Git.git_out")
def test_recognize_binary(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "000000 100644 0000000000000000000000000000000000000000 "
        "21be03052ed0c8dc31dff33eeb9275430241a727 A\x00sample.bin"
    )
    diff = Diff()
    content = b"\x08\x00\x00\x10"
    m_cat_file.side_effect = (content,)
    m_file_size.return_value = 5
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    m_git_out.assert_not_called()
    assert change.file_type.name == "BINARY"
    assert change.uploads == [
        dict(type="old", value=b"", mime="application/octet-stream", phid=None),
        dict(type="new", value=content, mime="application/octet-stream", phid=None),
    ]
    assert not change.hunks


@mock.patch("mozphab.git.Git._file_size")
@mock.patch("mozphab.git.Git._cat_file")
@mock.patch("mozphab.git.Git.git_out")
def test_recognize_long_text_as_binary(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "000000 100644 0000000000000000000000000000000000000000 "
        "78981922613b2afb6025042ff6bd878ac1994e85 A\x00a"
    )
    diff = Diff()
    content = b"a\n"
    m_cat_file.side_effect = (content,)
    m_file_size.return_value = environment.MAX_TEXT_SIZE + 1
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    m_git_out.assert_not_called()
    assert change.file_type.name == "BINARY"
    assert change.uploads == [
        dict(type="old", value=b"", mime="", phid=None),
        dict(type="new", value=content, mime="", phid=None),
    ]
    assert not change.hunks


@mock.patch("mozphab.git.Git._file_size")
@mock.patch("mozphab.git.Git._cat_file")
@mock.patch("mozphab.git.Git.git_out")
def test_less_context(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "100644 100644 78981922613b2afb6025042ff6bd878ac1994e85 "
        "422c2b7ab3b3c668038da977e4e93a5fc623169c M\x00a"
    )
    diff = Diff()
    m_cat_file.side_effect = (b"a\n", b"a\nb\n")
    m_git_out.return_value = b"""\
diff --git a/78981922613b2afb6025042ff6bd878ac1994e85 \
b/422c2b7ab3b3c668038da977e4e93a5fc623169c
index 7898192..422c2b7 100644
--- a/78981922613b2afb6025042ff6bd878ac1994e85\n+++ \
b/422c2b7ab3b3c668038da977e4e93a5fc623169c
@@ -1 +1,2 @@
 a
+b"""
    m_file_size.return_value = 5
    git.args = Args(less_context=True)

    git._parse_diff_change(raw, diff)
    m_git_out.assert_called_once_with(
        [
            "diff",
            "--submodule=short",
            "--no-ext-diff",
            "--no-color",
            "--no-textconv",
            "-U100",
            "78981922613b2afb6025042ff6bd878ac1994e85",
            "422c2b7ab3b3c668038da977e4e93a5fc623169c",
        ],
        expect_binary=True,
    )

    git.args = Args(less_context=False)
    m_file_size.return_value = environment.MAX_CONTEXT_SIZE + 1
    m_cat_file.side_effect = (b"a\n", b"a\nb\n")
    m_git_out.reset_mock()

    git._parse_diff_change(raw, diff)
    m_git_out.assert_called_once_with(
        [
            "diff",
            "--submodule=short",
            "--no-ext-diff",
            "--no-color",
            "--no-textconv",
            "-U100",
            "78981922613b2afb6025042ff6bd878ac1994e85",
            "422c2b7ab3b3c668038da977e4e93a5fc623169c",
        ],
        expect_binary=True,
    )


def test_multiple_hunks():
    git_diff = textwrap.dedent(
        """
        diff --git a/fn b/fn
        --- a/fn
        +++ b/fn
        @@ -4,3 +4,2 @@ c
        d
        -e
        f
        @@ -11,3 +10,2 @@ j
        k
        -l
        m
        @@ -25,2 +21,1 @@ x
        y
        -z
        """
    )

    change = Diff.Change("x")
    change.from_git_diff(git_diff)

    assert len(change.hunks) == 3
    assert_attributes(
        change.hunks[0],
        dict(
            old_off=4,
            old_len=3,
            new_off=4,
            new_len=2,
            old_eof_newline=True,
            new_eof_newline=True,
            added=0,
            deleted=1,
            corpus="d\n-e\nf\n",
        ),
    )
    assert_attributes(
        change.hunks[1],
        dict(
            old_off=11,
            old_len=3,
            new_off=10,
            new_len=2,
            old_eof_newline=True,
            new_eof_newline=True,
            added=0,
            deleted=1,
            corpus="k\n-l\nm\n",
        ),
    )
    assert_attributes(
        change.hunks[2],
        dict(
            old_off=25,
            old_len=2,
            new_off=21,
            new_len=1,
            old_eof_newline=True,
            new_eof_newline=True,
            added=0,
            deleted=1,
            corpus="y\n-z\n",
        ),
    )


def test_set_as_binary():
    change = Diff.Change("x")
    change.set_as_binary(
        a_body=b"a",
        a_mime="pdf/",
        b_body=b"b",
        b_mime="pdf/",
    )
    assert change.binary
    assert change.uploads == [
        {"type": "old", "value": b"a", "mime": "pdf/", "phid": None},
        {"type": "new", "value": b"b", "mime": "pdf/", "phid": None},
    ]
    assert change.file_type.name == "BINARY"

    change = Diff.Change("x")
    change.set_as_binary(
        a_body=b"a",
        a_mime="image/jpeg",
        b_body=b"b",
        b_mime="pdf/",
    )
    assert change.file_type.name == "IMAGE"

    change = Diff.Change("x")
    change.set_as_binary(
        a_body=b"a",
        a_mime="image/jpeg",
        b_body=b"b",
        b_mime="pdf/",
    )
    assert change.file_type.name == "IMAGE"
