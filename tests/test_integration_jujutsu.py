# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import argparse

from mozphab.jujutsu import Jujutsu

from .conftest import jj_out, write_text


def test_apply_patch_tracks_new_files(in_process, jj_colocated_repo_path):
    """Verify `apply_patch` commits new files when `snapshot.auto-track` doesn't match
    them, even if they're ignored locally."""
    jj_out("config", "set", "--repo", "snapshot.auto-track", "none()")
    write_text(jj_colocated_repo_path / "oldfile.txt", "old content\n")
    write_text(jj_colocated_repo_path / ".gitignore", "ignored.txt\n")
    jj_out("file", "track", "oldfile.txt", ".gitignore")
    jj_out("commit", "--message", "add file to rename and .gitignore")

    repo = Jujutsu(str(jj_colocated_repo_path))
    repo.args = argparse.Namespace(no_branch=True)
    diff = (
        "diff --git a/newfile.txt b/newfile.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/newfile.txt\n"
        "@@ -0,0 +1 @@\n"
        "+new content\n"
        "diff --git a/ignored.txt b/ignored.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/ignored.txt\n"
        "@@ -0,0 +1 @@\n"
        "+ignored content\n"
        "diff --git a/oldfile.txt b/renamed.txt\n"
        "similarity index 100%\n"
        "rename from oldfile.txt\n"
        "rename to renamed.txt\n"
    )
    repo.apply_patch(diff, "Bug 1 - Add files", None, None)

    files = jj_out(
        "log", "--no-graph", "--revisions", "@-", "--template", "diff.summary()"
    )
    assert sorted(files.splitlines()) == [
        "A ignored.txt",
        "A newfile.txt",
        "R {oldfile.txt => renamed.txt}",
    ], "The new, ignored and renamed files should be committed."
