# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import difflib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from mozphab import mozphab
from mozphab.commits import Commit
from mozphab.config import Config
from mozphab.git import Git
from mozphab.jujutsu import Jujutsu
from mozphab.mercurial import Mercurial
from mozphab.repository import (
    PatchHunk,
    PatchRefs,
    Repository,
    RepositoryOptions,
    StackSelection,
    apply_hunks,
    get_lando_url_for_phabricator,
    is_mozilla_phabricator,
    same_changes,
)

from .conftest import git_out, hg_out, jj_out, write_text


def test_phabricator_target_from_environment(monkeypatch, tmp_path):
    (tmp_path / ".arcconfig").write_text(
        json.dumps(
            {
                "phabricator.uri": "https://phabricator.services.mozilla.com/",
                "repository.callsign": "FIREFOXAUTOLAND",
            }
        )
    )
    monkeypatch.setenv(
        "MOZPHAB_PHABRICATOR_URL", "https://phabricator-dev.allizom.org/"
    )
    monkeypatch.setenv("MOZPHAB_REPOSITORY_CALLSIGN", "FFTESTAUTOLANDDEV")
    monkeypatch.setenv("MOZPHAB_BUGZILLA_URL", "https://bugzilla-dev.allizom.org/")

    repo = Repository(str(tmp_path), str(tmp_path / ".git"))

    assert repo.phab_url == "https://phabricator-dev.allizom.org"
    assert repo.api_url == "https://phabricator-dev.allizom.org/api/"
    assert repo.call_sign == "FFTESTAUTOLANDDEV"
    assert repo.bmo_url == "https://bugzilla-dev.allizom.org/"


def test_phabricator_target_from_arcconfig_without_environment(monkeypatch, tmp_path):
    (tmp_path / ".arcconfig").write_text(
        json.dumps(
            {
                "phabricator.uri": "https://phabricator.services.mozilla.com/",
                "repository.callsign": "FIREFOXAUTOLAND",
            }
        )
    )
    monkeypatch.delenv("MOZPHAB_PHABRICATOR_URL", raising=False)
    monkeypatch.delenv("MOZPHAB_REPOSITORY_CALLSIGN", raising=False)
    monkeypatch.delenv("MOZPHAB_BUGZILLA_URL", raising=False)

    repo = Repository(str(tmp_path), str(tmp_path / ".git"))

    assert repo.phab_url == "https://phabricator.services.mozilla.com"
    assert repo.call_sign == "FIREFOXAUTOLAND"
    assert repo.bmo_url == "https://bugzilla.mozilla.org"


def test_is_mozilla_phabricator():
    assert is_mozilla_phabricator("https://phabricator.services.mozilla.com/")
    assert is_mozilla_phabricator("https://phabricator-dev.allizom.org/")
    assert not is_mozilla_phabricator("https://reviews.llvm.org/")


def test_get_lando_url_for_phabricator():
    assert (
        get_lando_url_for_phabricator("https://phabricator.services.mozilla.com/")
        == "https://lando.moz.tools"
    ), "Prod Phabricator with trailing slash should return prod Lando URL."
    assert (
        get_lando_url_for_phabricator("https://phabricator.services.mozilla.com")
        == "https://lando.moz.tools"
    ), "Prod Phabricator without trailing slash should return prod Lando URL."
    assert (
        get_lando_url_for_phabricator("https://phabricator-dev.allizom.org/")
        == "https://lando-dev.allizom.org"
    ), "Dev Phabricator should return dev Lando URL."

    with pytest.raises(KeyError):
        get_lando_url_for_phabricator("https://reviews.llvm.org/")


def test_repository_options_from_args():
    config = Config(should_access_file=False)
    args = mozphab.parse_args(["submit", "--path", "repo", "-u", "upstream"])

    assert RepositoryOptions.from_args(args, config) == RepositoryOptions(
        path="repo", upstream=("upstream",)
    ), "`--path` and `--upstream` should be kept."

    config.safe_mode = True
    assert RepositoryOptions.from_args(
        args, config
    ).safe_mode, "`safe_mode` in the config should enable safe mode."

    config.safe_mode = False
    args.safe_mode = True
    assert RepositoryOptions.from_args(
        args, config
    ).safe_mode, "`--safe-mode` should enable safe mode."


# The tests below check every VCS backend behaves the same, using real repositories.


@dataclass
class VcsRepo:
    """A real repository of one VCS, with ways to commit to it and list its refs."""

    repo: Repository
    commit_files: Callable[[dict[str, str], str], str]
    # The names of the branches or bookmarks in the repository.
    named_refs: Callable[[], set[str]]


def git_commit_files(path: Path, files: dict[str, str], message: str) -> str:
    """Commit `files` to the Git repository at `path`, returning the new node."""
    for name, content in files.items():
        write_text(path / name, content)
        git_out("add", name)
    git_out("commit", "--message", message)
    return git_out("rev-parse", "HEAD").strip()


def hg_commit_files(path: Path, files: dict[str, str], message: str) -> str:
    """Commit `files` to the Mercurial repository at `path`, returning the node."""
    for name, content in files.items():
        write_text(path / name, content)
    hg_out("addremove")
    hg_out("commit", "--message", message)
    return hg_out("log", "--rev", ".", "--template", "{node}")


def jj_commit_files(path: Path, files: dict[str, str], message: str) -> str:
    """Commit `files` to the Jujutsu repository at `path`, returning the commit id."""
    for name, content in files.items():
        write_text(path / name, content)
    jj_out("commit", "--message", message)
    return jj_out(
        "log", "--no-graph", "--revisions", "@-", "--template", "commit_id"
    ).strip()


def git_named_refs() -> set[str]:
    """Return the branches in the Git repository in the working directory."""
    return set(git_out("branch", "--format=%(refname:short)").split())


def hg_named_refs() -> set[str]:
    """Return the bookmarks in the Mercurial repository in the working directory."""
    return set(hg_out("bookmarks", "--template", "{bookmark}\n").split())


def jj_named_refs() -> set[str]:
    """Return the bookmarks in the Jujutsu repository in the working directory."""
    return set(jj_out("bookmark", "list", "--template", 'name ++ "\n"').split())


@pytest.fixture(params=["git", "hg", "jj"])
def vcs_repo(request) -> VcsRepo:
    """Return a `VcsRepo` for each VCS backend."""
    if request.param == "git":
        path = request.getfixturevalue("git_repo_path")
        return VcsRepo(
            Git(str(path)),
            lambda files, message: git_commit_files(path, files, message),
            git_named_refs,
        )

    if request.param == "hg":
        path = request.getfixturevalue("hg_repo_path")
        return VcsRepo(
            Mercurial(str(path)),
            lambda files, message: hg_commit_files(path, files, message),
            hg_named_refs,
        )

    path = request.getfixturevalue("jj_colocated_repo_path")
    # `commit_stack` can't parse commits without an author.
    jj_out("config", "set", "--repo", "user.name", "Developer")
    jj_out("config", "set", "--repo", "user.email", "developer@mozilla.com")
    return VcsRepo(
        Jujutsu(str(path)),
        lambda files, message: jj_commit_files(path, files, message),
        jj_named_refs,
    )


# A branch or bookmark, in whichever form each VCS creates it.
ALL_REFS = PatchRefs(create_branch=True, create_bookmark=True)


def numbered_lines(count: int, changed: int | None = None) -> str:
    """Return `count` numbered lines, with the line numbered `changed` changed."""
    return "".join(
        f"changed {number}\n" if number == changed else f"line {number}\n"
        for number in range(count)
    )


def test_get_diff_less_context(vcs_repo: VcsRepo):
    vcs_repo.commit_files({"file.txt": numbered_lines(500)}, "Add a file")
    node = vcs_repo.commit_files(
        {"file.txt": numbered_lines(500, changed=250)}, "Change a line"
    )
    commit = Commit(node=node)

    full_hunk = vcs_repo.repo.get_diff(commit).changes["file.txt"].hunks[0]
    assert (
        full_hunk.old_len == 500
    ), "A modified file should be diffed with the whole file as context."

    short_hunk = (
        vcs_repo.repo.get_diff(commit, less_context=True).changes["file.txt"].hunks[0]
    )
    assert (
        short_hunk.old_len == 201
    ), "`less_context` should only keep 100 lines of context around a change."


def test_select_single_commit(vcs_repo: VcsRepo):
    vcs_repo.commit_files({"first.txt": "first\n"}, "First commit")
    vcs_repo.commit_files({"second.txt": "second\n"}, "Second commit")

    vcs_repo.repo.select_stack(StackSelection(single=True))
    commits = vcs_repo.repo.commit_stack(single=True)

    assert [commit.title for commit in commits or []] == [
        "Second commit"
    ], "`--single` should select only the most recent commit."


def test_before_patch_creates_the_named_ref(vcs_repo: VcsRepo):
    node = vcs_repo.repo.get_current_node()

    vcs_repo.repo.before_patch(node, ALL_REFS, "phab-D1")

    assert (
        "phab-D1" in vcs_repo.named_refs()
    ), "`before_patch` should create a branch or bookmark with the given name."


def test_before_patch_without_named_refs(vcs_repo: VcsRepo):
    node = vcs_repo.repo.get_current_node()
    vcs_repo.repo.before_patch(node, PatchRefs(), name="phab-D1", assume_yes=True)

    assert (
        "phab-D1" not in vcs_repo.named_refs()
    ), "`before_patch` shouldn't create a branch or bookmark it wasn't asked for."


@pytest.mark.parametrize(
    "vcs_repo",
    [
        "git",
        pytest.param(
            "hg",
            marks=pytest.mark.xfail(
                reason="Mercurial doesn't implement `discard_patch_attempt` yet.",
                strict=True,
            ),
        ),
        "jj",
    ],
    indirect=True,
)
def test_discard_patch_attempt_removes_the_named_ref(vcs_repo: VcsRepo):
    node = vcs_repo.repo.get_current_node()
    attempt = vcs_repo.repo.before_patch(node, ALL_REFS, "phab-D1")
    assert (
        attempt.branch == "phab-D1"
    ), "The attempt should record the branch or bookmark it created."

    vcs_repo.repo.discard_patch_attempt(attempt)

    assert (
        "phab-D1" not in vcs_repo.named_refs()
    ), "Discarding the attempt should remove the branch or bookmark it created."


def test_same_changes():
    # A patch with the whole files as context.
    patch = b"""\
diff --git a/A b/A
index 1111111..2222222 100644
--- a/A
+++ b/A
@@ -1,5 +1,5 @@
 a
 b
-c
+C
 d
 e
diff --git a/B b/B
similarity index 90%
copy from A
copy to B
--- a/B
+++ b/B
@@ -1,2 +1,2 @@
-x
+y
\\ No newline at end of file
"""
    # Phabricator's raw diffs list the files in another order, without the
    # blob IDs and similarity, and the line numbers can differ after a rebase.
    raw_diff = b"""\
diff --git a/B b/B
copy from A
copy to B
--- a/B
+++ b/B
@@ -1,2 +1,2 @@
-x
+y
\\ No newline at end of file
diff --git a/A b/A
--- a/A
+++ b/A
@@ -20,3 +20,3 @@
 b
-c
+C
 d
"""
    assert same_changes(patch, raw_diff)
    for changed in (
        raw_diff.replace(b"+C", b"+C "),
        raw_diff.replace(b" d\n", b" D\n"),
        raw_diff.replace(b"copy from A", b"copy from Z"),
        raw_diff.replace(b"\\ No newline at end of file\n", b""),
    ):
        assert not same_changes(patch, changed)

    # Line numbers matter when the context matches several places.
    repeated = (
        b"diff --git a/A b/A\n@@ -1,8 +1,8 @@\n 0\n a\n-b\n+c\n x\n a\n b\n x\n 0\n"
    )
    hunk = b"diff --git a/A b/A\n@@ -%d,3 +%d,3 @@\n a\n-b\n+c\n x\n"
    assert same_changes(repeated, hunk % (2, 2))
    assert not same_changes(repeated, hunk % (5, 5))

    # Like `git apply`, hunks starting at the first line, or without trailing
    # context, only apply at the beginning or the end of the file.
    def full_patch(before, after):
        """A patch with the whole file as context, changing `before` to `after`."""
        diff = difflib.diff_bytes(
            difflib.unified_diff, before, after, n=len(before), lineterm=b""
        )
        return b"diff --git a/A b/A\n" + b"\n".join(list(diff)[2:])

    numbers = [b"%d" % i for i in range(1, 21)]
    start = b"diff --git a/A b/A\n@@ -1,3 +1,3 @@\n 1\n-2\n+two\n 3\n"
    rebased = [b"0", *numbers]
    assert same_changes(full_patch(numbers, [b"1", b"two", *numbers[2:]]), start)
    assert not same_changes(
        full_patch(rebased, [b"0", b"1", b"two", *numbers[2:]]), start
    )
    end = b"diff --git a/A b/A\n@@ -19,2 +19,2 @@\n 19\n-20\n+twenty\n"
    assert same_changes(full_patch(numbers, [*numbers[:-1], b"twenty"]), end)
    assert not same_changes(
        full_patch([*numbers, b"21"], [*numbers[:-1], b"twenty", b"21"]), end
    )
    # Elsewhere, they apply wherever their context matches.
    middle = b"diff --git a/A b/A\n@@ -9,3 +9,3 @@\n 9\n-10\n+ten\n 11\n"
    assert same_changes(
        full_patch(rebased, [b"0", *numbers[:9], b"ten", *numbers[10:]]), middle
    )

    # Binary files are compared with their blob IDs, as Git and Phabricator
    # encode their data differently.
    binary = b"diff --git a/C b/C\nindex %s 100644\nGIT binary patch\nliteral 1\n%s\n"
    raw_binary = b"diff --git a/C b/C\nindex %s\nGIT binary patch\nliteral 1\n%s\n"
    assert same_changes(binary % (b"1..2", b"Data"), raw_binary % (b"1..2", b"Other"))
    assert not same_changes(
        binary % (b"1..2", b"Data"), raw_binary % (b"1..3", b"Data")
    )


def test_apply_hunks():
    lines = [b"a", b"b", b"c", b"d"]
    first = PatchHunk(1, (b" a", b"-b", b"+B", b" c"))
    second = PatchHunk(2, (b" b", b"-c", b"+C", b" d"))
    assert apply_hunks(lines, (first,)) == [b"a", b"B", b"c", b"d"]
    assert apply_hunks(lines, (second,)) == [b"a", b"b", b"C", b"d"]
    assert (
        apply_hunks(lines, (first, second)) is None
    ), "Hunks covering the same lines shouldn't apply together."
    assert (
        apply_hunks(lines, (PatchHunk(1, (b" x", b"-b", b"+B")),)) is None
    ), "A hunk whose context doesn't match shouldn't apply."
