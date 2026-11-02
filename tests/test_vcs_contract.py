# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Behaviour every VCS backend should share, checked against real repositories."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from mozphab.commits import Commit
from mozphab.git import Git
from mozphab.jujutsu import Jujutsu
from mozphab.mercurial import Mercurial
from mozphab.repository import PatchRefs, Repository

from .conftest import git_out, hg_out, jj_out, write_text


@dataclass
class ContractRepo:
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
def contract_repo(request) -> ContractRepo:
    """Return a `ContractRepo` for each VCS backend."""
    if request.param == "git":
        path = request.getfixturevalue("git_repo_path")
        return ContractRepo(
            Git(str(path)),
            lambda files, message: git_commit_files(path, files, message),
            git_named_refs,
        )

    if request.param == "hg":
        path = request.getfixturevalue("hg_repo_path")
        return ContractRepo(
            Mercurial(str(path)),
            lambda files, message: hg_commit_files(path, files, message),
            hg_named_refs,
        )

    path = request.getfixturevalue("jj_colocated_repo_path")
    return ContractRepo(
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


def test_get_diff_less_context(contract_repo: ContractRepo):
    contract_repo.commit_files({"file.txt": numbered_lines(500)}, "Add a file")
    node = contract_repo.commit_files(
        {"file.txt": numbered_lines(500, changed=250)}, "Change a line"
    )
    commit = Commit(node=node)

    full_hunk = contract_repo.repo.get_diff(commit).changes["file.txt"].hunks[0]
    assert (
        full_hunk.old_len == 500
    ), "A modified file should be diffed with the whole file as context."

    short_hunk = (
        contract_repo.repo.get_diff(commit, less_context=True)
        .changes["file.txt"]
        .hunks[0]
    )
    assert (
        short_hunk.old_len == 201
    ), "`less_context` should only keep 100 lines of context around a change."


def test_before_patch_creates_the_named_ref(contract_repo: ContractRepo):
    node = contract_repo.repo.get_current_node()

    contract_repo.repo.before_patch(node, ALL_REFS, "phab-D1")

    assert (
        "phab-D1" in contract_repo.named_refs()
    ), "`before_patch` should create a branch or bookmark with the given name."


def test_before_patch_without_named_refs(contract_repo: ContractRepo):
    node = contract_repo.repo.get_current_node()
    contract_repo.repo.before_patch(node, PatchRefs(), name="phab-D1", assume_yes=True)

    assert (
        "phab-D1" not in contract_repo.named_refs()
    ), "`before_patch` shouldn't create a branch or bookmark it wasn't asked for."


@pytest.mark.parametrize(
    "contract_repo",
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
def test_discard_patch_attempt_removes_the_named_ref(contract_repo: ContractRepo):
    node = contract_repo.repo.get_current_node()
    attempt = contract_repo.repo.before_patch(node, ALL_REFS, "phab-D1")
    assert (
        attempt.branch == "phab-D1"
    ), "The attempt should record the branch or bookmark it created."

    contract_repo.repo.discard_patch_attempt(attempt)

    assert (
        "phab-D1" not in contract_repo.named_refs()
    ), "Discarding the attempt should remove the branch or bookmark it created."
