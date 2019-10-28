# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# coding=utf-8

import imp
import mock
import os

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)

mozphab.SHOW_SPINNER = False


class Args:
    def __init__(self, less_context=False):
        self.lesscontext = less_context


@mock.patch("mozphab.Git._file_size")
@mock.patch("mozphab.Git._cat_file")
@mock.patch("mozphab.Git.git_out")
def test_create(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "000000 100644 0000000000000000000000000000000000000000 "
        "78981922613b2afb6025042ff6bd878ac1994e85 A\x00a"
    )
    diff = mozphab.Diff()
    m_cat_file.side_effect = (b"a\n",)
    m_file_size.return_value = 5
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    assert change.file_type.name == "TEXT"
    m_git_out.assert_not_called()
    assert change.hunks[0] == diff.Hunk(
        old_off=0,
        old_len=0,
        new_off=1,
        new_len=1,
        old_eof_newline=True,
        new_eof_newline=True,
        added=1,
        deleted=0,
        corpus="+a\n",
    )
    assert change.kind.name == "ADD"
    assert change.cur_mode == "100644"


@mock.patch("mozphab.Git._file_size")
@mock.patch("mozphab.Git._cat_file")
@mock.patch("mozphab.Git.git_out")
def test_change_file(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "100644 100644 78981922613b2afb6025042ff6bd878ac1994e85 "
        "422c2b7ab3b3c668038da977e4e93a5fc623169c M\x00a"
    )
    diff = mozphab.Diff()
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
    assert change.file_type.name == "TEXT"
    m_git_out.assert_called_once_with(
        [
            "diff",
            "--submodule=short",
            "--no-ext-diff",
            "--no-color",
            "--no-textconv",
            "-U%s" % mozphab.MAX_CONTEXT_SIZE,
            "78981922613b2afb6025042ff6bd878ac1994e85",
            "422c2b7ab3b3c668038da977e4e93a5fc623169c",
        ],
        expect_binary=True,
    )
    assert change.hunks[0] == diff.Hunk(
        old_off=1,
        old_len=1,
        new_off=1,
        new_len=2,
        old_eof_newline=True,
        new_eof_newline=True,
        added=1,
        deleted=0,
        corpus=" a\n+b",
    )
    assert change.kind.name == "CHANGE"
    assert change.old_mode is None
    assert change.cur_mode is None
    assert change.old_path == "a"


@mock.patch("mozphab.Git._file_size")
@mock.patch("mozphab.Git._cat_file")
@mock.patch("mozphab.Git.git_out")
def test_delete_file(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "100644 000000 61780798228d17af2d34fce4cfbdf35556832472 "
        "0000000000000000000000000000000000000000 D\x00a"
    )
    diff = mozphab.Diff()
    m_cat_file.side_effect = (b"a\nb\n",)
    m_file_size.return_value = 5
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    assert change.file_type.name == "TEXT"
    m_git_out.assert_not_called()
    assert change.hunks[0] == diff.Hunk(
        old_off=1,
        old_len=2,
        new_off=0,
        new_len=0,
        old_eof_newline=True,
        new_eof_newline=True,
        added=0,
        deleted=2,
        corpus="-a\n-b\n",
    )


@mock.patch("mozphab.Git._file_size")
@mock.patch("mozphab.Git._cat_file")
@mock.patch("mozphab.Git.git_out")
def test_recognize_binary(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "000000 100644 0000000000000000000000000000000000000000 "
        "21be03052ed0c8dc31dff33eeb9275430241a727 A\x00sample.bin"
    )
    diff = mozphab.Diff()
    content = b"\x08\x00\x00\x10"
    m_cat_file.side_effect = (content,)
    m_file_size.return_value = 5
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    assert change.file_type.name == "BINARY"
    m_git_out.assert_not_called()
    assert change.uploads == [
        dict(type="old", value=b"", mime="application/octet-stream", phid=None),
        dict(type="new", value=content, mime="application/octet-stream", phid=None),
    ]
    assert not change.hunks


@mock.patch("mozphab.Git._file_size")
@mock.patch("mozphab.Git._cat_file")
@mock.patch("mozphab.Git.git_out")
def test_recognize_long_text_as_binary(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "000000 100644 0000000000000000000000000000000000000000 "
        "78981922613b2afb6025042ff6bd878ac1994e85 A\x00a"
    )
    diff = mozphab.Diff()
    content = b"a\n"
    m_cat_file.side_effect = (content,)
    m_file_size.return_value = mozphab.MAX_TEXT_SIZE + 1
    git.args = Args()

    change = git._parse_diff_change(raw, diff)
    assert change.file_type.name == "BINARY"
    m_git_out.assert_not_called()
    assert change.uploads == [
        dict(type="old", value=b"", mime="", phid=None),
        dict(type="new", value=content, mime="", phid=None),
    ]
    assert not change.hunks


@mock.patch("mozphab.Git._file_size")
@mock.patch("mozphab.Git._cat_file")
@mock.patch("mozphab.Git.git_out")
def test_less_context(m_git_out, m_cat_file, m_file_size, git):
    raw = (
        "100644 100644 78981922613b2afb6025042ff6bd878ac1994e85 "
        "422c2b7ab3b3c668038da977e4e93a5fc623169c M\x00a"
    )
    diff = mozphab.Diff()
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
    m_file_size.return_value = mozphab.MAX_CONTEXT_SIZE + 1
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
