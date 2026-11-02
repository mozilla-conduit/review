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
from mozphab.repository import Repository

from .conftest import git_out, hg_out, jj_out, write_text


@dataclass
class ContractRepo:
    """A real repository of one VCS, with a way to commit files to it."""

    repo: Repository
    commit_files: Callable[[dict[str, str], str], str]


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


@pytest.fixture(params=["git", "hg", "jj"])
def contract_repo(request) -> ContractRepo:
    """Return a `ContractRepo` for each VCS backend."""
    if request.param == "git":
        path = request.getfixturevalue("git_repo_path")
        return ContractRepo(
            Git(str(path)),
            lambda files, message: git_commit_files(path, files, message),
        )

    if request.param == "hg":
        path = request.getfixturevalue("hg_repo_path")
        return ContractRepo(
            Mercurial(str(path)),
            lambda files, message: hg_commit_files(path, files, message),
        )

    path = request.getfixturevalue("jj_colocated_repo_path")
    return ContractRepo(
        Jujutsu(str(path)),
        lambda files, message: jj_commit_files(path, files, message),
    )


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
