# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import copy
import os
from unittest import mock
import time

from .conftest import hg_out, git_out

from mozphab import mozphab


@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
@mock.patch("mozphab.conduit.ConduitAPI.call")
@mock.patch("mozphab.conduit.ConduitAPI.get_successor_phids")
@mock.patch("mozphab.conduit.ConduitAPI.get_ancestor_phids")
@mock.patch("builtins.print")
def test_patch_raw(
    m_print,
    m_ancestor_phids,
    m_get_successor_phids,
    m_call_conduit,
    m_get_diffs,
    m_get_revs,
    in_process,
    hg_repo_path,
    git_repo_path,
):
    def init(path):
        os.chdir(path)
        m_get_successor_phids.return_value = []
        m_get_revs.return_value = [REV_1]
        m_ancestor_phids.return_value = []
        m_get_diffs.return_value = {"PHID-DIFF-1": DIFF_1}
        m_get_revs.side_effect = None
        m_ancestor_phids.side_effect = None
        m_call_conduit.side_effect = None

    # `patch --raw` generates different output for hg vs git

    # git tests
    init(git_repo_path)

    m_print.reset_mock()
    m_call_conduit.return_value = PATCH_1_DIFF
    mozphab.main(["patch", "D1", "--raw"], is_development=True)
    assert m_print.call_args_list == [mock.call(PATCH_1_DIFF)]

    m_print.reset_mock()
    m_call_conduit.return_value = BIN_PATCH_DIFF
    mozphab.main(["patch", "D1", "--raw"], is_development=True)
    assert m_print.call_args_list == [mock.call(BIN_PATCH_DIFF)]

    m_print.reset_mock()
    m_call_conduit.return_value = BIN_PATCH_DIFF
    mozphab.main(["patch", "D1", "--raw"], is_development=True)
    assert m_print.call_args_list == [mock.call(BIN_PATCH_DIFF)]

    m_print.reset_mock()
    m_get_revs.side_effect = ([REV_2], [REV_1])
    m_ancestor_phids.side_effect = [["PHID-REV-1"], []]
    m_get_diffs.return_value = {"PHID-DIFF-1": DIFF_1, "PHID-DIFF-2": DIFF_2}
    m_call_conduit.side_effect = [PATCH_1_DIFF, PATCH_2_DIFF]
    mozphab.main(["patch", "D2", "--raw"], is_development=True)
    assert m_print.call_args_list == [mock.call(PATCH_1_DIFF), mock.call(PATCH_2_DIFF)]

    # hg tests
    init(hg_repo_path)

    m_print.reset_mock()
    m_call_conduit.return_value = PATCH_1_DIFF
    mozphab.main(["patch", "D1", "--raw"], is_development=True)
    assert m_print.call_args_list == [mock.call(PATCH_1_META + PATCH_1_DIFF)]

    m_print.reset_mock()
    m_call_conduit.return_value = BIN_PATCH_DIFF
    mozphab.main(["patch", "D1", "--raw"], is_development=True)
    assert m_print.call_args_list == [mock.call(BIN_PATCH_META + BIN_PATCH_DIFF)]

    m_print.reset_mock()
    m_call_conduit.return_value = BIN_PATCH_DIFF
    mozphab.main(["patch", "D1", "--raw"], is_development=True)
    assert m_print.call_args_list == [mock.call(BIN_PATCH_META + BIN_PATCH_DIFF)]

    m_print.reset_mock()
    m_get_revs.side_effect = ([REV_2], [REV_1])
    m_ancestor_phids.side_effect = [["PHID-REV-1"], []]
    m_get_diffs.return_value = {"PHID-DIFF-1": DIFF_1, "PHID-DIFF-2": DIFF_2}
    m_call_conduit.side_effect = [PATCH_1_DIFF, PATCH_2_DIFF]
    mozphab.main(["patch", "D2", "--raw"], is_development=True)
    assert m_print.call_args_list == [
        mock.call(PATCH_1_META + PATCH_1_DIFF),
        mock.call(PATCH_2_META + PATCH_2_DIFF),
    ]


@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
@mock.patch("mozphab.conduit.ConduitAPI.call")
@mock.patch("mozphab.conduit.ConduitAPI.get_successor_phids")
@mock.patch("mozphab.conduit.ConduitAPI.get_ancestor_phids")
def test_patch_no_commit(
    m_ancestor_phids,
    m_get_successor_phids,
    m_call_conduit,
    m_get_diffs,
    m_get_revs,
    in_process,
    hg_repo_path,
):
    m_get_successor_phids.return_value = []
    m_get_revs.return_value = [REV_1]
    m_ancestor_phids.return_value = []
    m_get_diffs.return_value = {"PHID-DIFF-1": DIFF_1}
    m_call_conduit.side_effect = [
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        PATCH_1_DIFF,
    ]

    mozphab.main(["patch", "D1", "--no-commit"], is_development=True)
    assert [".arcconfig", ".hg", "X"] == sorted(os.listdir(str(hg_repo_path)))
    test_file = hg_repo_path / "X"
    assert "a\n" == test_file.read_text()
    result = hg_out("log", "-G")
    assert "@  changeset:   0:" in result
    test_file.unlink()

    m_call_conduit.side_effect = [BIN_PATCH_DIFF]
    mozphab.main(["patch", "D1", "--no-commit"], is_development=True)
    assert [".arcconfig", ".hg", "sample.bin"] == sorted(os.listdir(str(hg_repo_path)))
    test_file = hg_repo_path / "sample.bin"
    test_file.unlink()

    m_get_revs.side_effect = ([REV_2], [REV_1])
    m_ancestor_phids.side_effect = [["PHID-REV-1"], []]
    m_get_diffs.return_value = {"PHID-DIFF-1": DIFF_1, "PHID-DIFF-2": DIFF_2}
    m_call_conduit.side_effect = [PATCH_1_DIFF, PATCH_2_DIFF]
    mozphab.main(["patch", "D2", "--no-commit"], is_development=True)
    assert [".arcconfig", ".hg", "X"] == sorted(os.listdir(str(hg_repo_path)))
    test_file = hg_repo_path / "X"
    assert "b\n" == test_file.read_text()
    test_file.unlink()


@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
@mock.patch("mozphab.conduit.ConduitAPI.call")
@mock.patch("mozphab.conduit.ConduitAPI.get_successor_phids")
@mock.patch("mozphab.conduit.ConduitAPI.get_ancestor_phids")
def test_git_patch_with_commit(
    m_ancestor_phids,
    m_get_successor_phids,
    m_call_conduit,
    m_get_diffs,
    m_get_revs,
    in_process,
    git_repo_path,
):
    m_get_successor_phids.return_value = []
    sha = git_out("rev-parse", "HEAD").rstrip("\n")
    diff_1 = copy.deepcopy(DIFF_1)
    diff_1["fields"]["refs"][0]["identifier"] = sha
    m_get_revs.return_value = [REV_1]
    m_ancestor_phids.return_value = []
    m_get_diffs.return_value = {"PHID-DIFF-1": diff_1}
    m_call_conduit.side_effect = [
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="git"))]),
        PATCH_1_DIFF,
    ]

    mozphab.main(["patch", "D1", "--apply-to", "here"], is_development=True)
    assert [".arcconfig", ".git", "X"] == sorted(os.listdir(str(git_repo_path)))
    test_file = git_repo_path / "X"
    assert "a\n" == test_file.read_text()
    result = git_out("log", "--all", "--format=[%at] %an <%ae>%n%s %P%n%b")
    assert 1 == result.count("[1547806078] user <author@example.com>")
    assert 1 == result.count("title R1")
    assert 1 == result.count("Differential Revision: http://example.test/D1")
    result = git_out("branch")
    assert "* phab-D1" in result

    time.sleep(1)  # to ensure the patch is applied with a different timestamp
    m_call_conduit.side_effect = [PATCH_1_DIFF]
    mozphab.main(["patch", "D1"], is_development=True)
    assert [".arcconfig", ".git", "X"] == sorted(os.listdir(str(git_repo_path)))
    test_file = git_repo_path / "X"
    assert "a\n" == test_file.read_text()
    result = git_out("log", "--all", "--format=[%at] %an <%ae>%n%s %P%n%b")
    assert 2 == result.count("[1547806078] user <author@example.com>")
    assert 2 == result.count("title R1")
    assert 2 == result.count("Differential Revision: http://example.test/D1")
    result = git_out("branch")
    assert "* phab-D1_1" in result
    assert "  phab-D1" in result

    time.sleep(1)
    m_get_revs.return_value = [REV_BIN]
    m_get_diffs.return_value = {"PHID-DIFF-3": diff_1}
    m_call_conduit.side_effect = [BIN_PATCH_DIFF]
    mozphab.main(["patch", "D3"], is_development=True)
    assert [".arcconfig", ".git", "sample.bin"] == sorted(
        os.listdir(str(git_repo_path))
    )
    result = git_out("log", "--all", "--format=[%at] %an <%ae>%n%s %P%n%b")
    assert 3 == result.count("[1547806078] user <author@example.com>")
    assert 1 == result.count("title BIN")
    assert 1 == result.count("Differential Revision: http://example.test/D3")
    assert 2 == result.count("title R1")
    assert 2 == result.count("Differential Revision: http://example.test/D1")
    result = git_out("branch")
    assert "* phab-D3" in result
    assert "  phab-D1_1" in result
    assert "  phab-D1" in result

    time.sleep(1)
    m_get_revs.side_effect = ([REV_2], [REV_1])
    m_ancestor_phids.side_effect = [["PHID-REV-1"], []]
    m_get_diffs.return_value = {"PHID-DIFF-1": diff_1, "PHID-DIFF-2": DIFF_2}
    m_call_conduit.side_effect = [PATCH_1_DIFF, PATCH_2_DIFF]
    mozphab.main(["patch", "D2"], is_development=True)
    assert [".arcconfig", ".git", "X"] == sorted(os.listdir(str(git_repo_path)))
    test_file = git_repo_path / "X"
    assert "b\n" == test_file.read_text()
    result = git_out("log", "--all", "--format=[%at] %an <%ae>%n%s %P%n%b")
    assert 5 == result.count("[1547806078] user <author@example.com>")
    assert 3 == result.count("title R1")
    assert 3 == result.count("Differential Revision: http://example.test/D1")
    assert 1 == result.count("Differential Revision: http://example.test/D2")
    assert 1 == result.count("title BIN")
    assert 1 == result.count("Differential Revision: http://example.test/D3")
    result = git_out("branch")
    assert "* phab-D2" in result
    assert "  phab-D3" in result
    assert "  phab-D1_1" in result
    assert "  phab-D1" in result

    time.sleep(1)
    m_get_revs.side_effect = ([REV_BIN],)
    m_get_diffs.return_value = {"PHID-DIFF-3": diff_1}
    m_call_conduit.side_effect = (PATCH_UTF8,)
    mozphab.main(["patch", "D4"], is_development=True)
    path = git_repo_path / "X"
    with path.open(encoding="utf-8") as f:
        line = f.readline().rstrip()

    assert line == "\u0105"


@mock.patch("mozphab.conduit.ConduitAPI.get_revisions")
@mock.patch("mozphab.conduit.ConduitAPI.get_diffs")
@mock.patch("mozphab.conduit.ConduitAPI.call")
@mock.patch("mozphab.conduit.ConduitAPI.get_successor_phids")
@mock.patch("mozphab.conduit.ConduitAPI.get_ancestor_phids")
def test_hg_patch_with_commit(
    m_ancestor_phids,
    m_get_successor_phids,
    m_call_conduit,
    m_get_diffs,
    m_get_revs,
    in_process,
    hg_repo_path,
):
    m_get_successor_phids.return_value = []
    m_get_revs.return_value = [REV_1]
    m_ancestor_phids.return_value = []
    m_get_diffs.return_value = {"PHID-DIFF-1": DIFF_1}
    m_call_conduit.side_effect = [
        dict(),
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
        PATCH_1_DIFF,
    ]

    mozphab.main(["patch", "D1", "--apply-to", "here"], is_development=True)
    assert [".arcconfig", ".hg", "X"] == sorted(os.listdir(str(hg_repo_path)))
    test_file = hg_repo_path / "X"
    assert "a\n" == test_file.read_text()
    result = hg_out("log", "-G")
    assert "@  changeset:   1:" in result
    assert "|  bookmark:    phab-D1" in result
    assert "|  user:        user <author@example.com>" in result
    assert "|  date:        Fri Jan 18" in result
    assert "|  summary:     title R1" in result
    assert "o  changeset:   0:" in result

    m_call_conduit.side_effect = [BIN_PATCH_DIFF, 67]
    mozphab.main(["patch", "D1"], is_development=True)
    assert [".arcconfig", ".hg", "sample.bin"] == sorted(os.listdir(str(hg_repo_path)))
    result = hg_out("log", "-G")
    assert "@  changeset:   2" in result
    assert "|  bookmark:    phab-D1_1" in result
    assert "|  parent:      0" in result
    assert "| o  changeset:   1" in result
    assert "|/   bookmark:    phab-D1" in result

    testfile = hg_repo_path / "unknown"
    testfile.write_text("not added to repository")
    m_get_revs.side_effect = ([REV_2], [REV_1])
    m_ancestor_phids.side_effect = [["PHID-REV-1"], []]
    m_get_diffs.return_value = {"PHID-DIFF-1": DIFF_1, "PHID-DIFF-2": DIFF_2}
    m_call_conduit.side_effect = [PATCH_1_DIFF, PATCH_2_DIFF]
    mozphab.main(["patch", "D2"], is_development=True)
    assert [".arcconfig", ".hg", "X", "unknown"] == sorted(
        os.listdir(str(hg_repo_path))
    )
    test_file = hg_repo_path / "X"
    assert "b\n" == test_file.read_text()
    result = hg_out("log", "-G")
    assert "@  changeset:   3:" in result
    assert "|  bookmark:    phab-D2" in result
    assert "|  parent:      1" in result
    assert "|  summary:     title R2" in result
    assert "| o  changeset:   2" in result
    assert "| |  bookmark:    phab-D1_1" in result
    assert "| |  parent:      0" in result
    assert "o |  changeset:   1" in result
    assert "|/   bookmark:    phab-D1" in result

    # Same, but this time request a specific bookmark name.
    m_get_revs.side_effect = ([REV_2], [REV_1])
    m_ancestor_phids.side_effect = [["PHID-REV-1"], []]
    m_get_diffs.return_value = {"PHID-DIFF-1": DIFF_1, "PHID-DIFF-2": DIFF_2}
    m_call_conduit.side_effect = [PATCH_1_DIFF, PATCH_2_DIFF]
    mozphab.main(["patch", "--name=myfeature", "D2"], is_development=True)
    result = hg_out("log", "-G")
    assert "@  changeset:   3:" in result
    assert "|  bookmark:    myfeature" in result
    assert "|  parent:      1" in result
    assert "|  summary:     title R2" in result
    assert "| o  changeset:   2" in result
    assert "| |  bookmark:    phab-D1_1" in result
    assert "| |  parent:      0" in result
    assert "o |  changeset:   1" in result
    assert "|/   bookmark:    phab-D1" in result


REV_1 = dict(
    id=1,
    phid="PHID-REV-1",
    fields=dict(title="title R1", summary="\u0105", diffPHID="PHID-DIFF-1"),
)

REV_2 = dict(
    id=2,
    phid="PHID-REV-2",
    fields=dict(title="title R2", summary="\u0105", diffPHID="PHID-DIFF-2"),
)

REV_BIN = dict(
    id=3,
    phid="PHID-REV-3",
    fields=dict(title="title BIN", summary="\u0105", diffPHID="PHID-DIFF-3"),
)
ATTACHMENTS = dict(
    commits=dict(
        commits=[dict(author=dict(name="user", email="author@example.com", epoch=None))]
    )
)

DIFF_1 = dict(
    fields=dict(dateCreated=1547806078, refs=[dict(identifier="0", type="base")]),
    id=1,
    attachments=ATTACHMENTS,
)

DIFF_2 = dict(fields=dict(dateCreated=1547806078), id=2, attachments=ATTACHMENTS)

PATCH_1_META = """\
# HG changeset patch
# User user <author@example.com>
# Date 1547806078 0
title R1

ą

Differential Revision: http://example.test/D1
"""
PATCH_1_DIFF = """\
diff --git a/X b/X
new file mode 100644
--- /dev/null
+++ b/X
@@ -0,0 +1 @@
+a

"""

PATCH_2_META = """\
# HG changeset patch
# User user <author@example.com>
# Date 1547806078 0
title R2

ą

Differential Revision: http://example.test/D2

Depends on D1
"""
PATCH_2_DIFF = """\
diff --git a/X b/X
--- a/X
+++ b/X
@@ -1 +1 @@
-a
+b

"""

PATCH_UTF8 = """\
diff --git a/X b/X
new file mode 100644
--- /dev/null
+++ b/X
@@ -0,0 +1 @@
+\u0105

"""

BIN_PATCH_META = """\
# HG changeset patch
# User user <author@example.com>
# Date 1547806078 0
title R1

ą

Differential Revision: http://example.test/D1
"""
BIN_PATCH_DIFF = """\
diff --git a/sample.bin b/sample.bin
new file mode 100644
index 0000000000000000000000000000000000000000..ea9bc1d9ecfd0d99001e388c9b70dd588b1ba265
GIT binary patch
literal 3072
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
O!n%`!v-e$`d$Yhe6Zzr*

literal 0
Hc$@<O00001


"""
