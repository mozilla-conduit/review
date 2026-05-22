# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Regression test pinning the number of git subprocess invocations.

`tests/test_call_counts.py` does the same job for `ConduitAPI.call`
(the network boundary). This module does it for `subprocess`-bound git
work, which is moz-phab's other hot path -- spawning git is expensive
relative to in-process Python, and a refactor that adds an extra
`git log` or `git rev-parse` to the submit / patch loop is exactly the
kind of perf regression worth catching at PR time.

The fixture wraps `mozphab.gitcommand.check_call` and
`mozphab.gitcommand.check_output` (the chokepoints all `GitCommand`
invocations go through) and records the git subcommand for every
call. The wrappers still delegate to the real implementations so the
underlying workflow runs against a real `git_repo_path`.
"""

from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List
from unittest import mock

import pytest

from mozphab import gitcommand, mozphab

# Module-level `call_conduit` so `in_process` (from `tests/conftest.py`)
# wires it up as `ConduitAPI.call`. Each test re-primes
# `call_conduit.side_effect` so the dispatcher is scoped to that test.
call_conduit = mock.Mock()


def extract_git_subcommand(command: List[str]) -> str:
    """Pull the git subcommand out of a fully-qualified git invocation.

    moz-phab prepends `-c key=value` flags to every git call (for UTF-8
    encoding and, in safe-mode, user identity); the subcommand is the
    first positional argument after those.
    """
    index = 1
    while index < len(command):
        if command[index] == "-c":
            index += 2
            continue
        return command[index]
    return "<unknown>"


@pytest.fixture
def git_call_counter(monkeypatch: pytest.MonkeyPatch) -> Iterator[Counter]:
    """Yield a `Counter` keyed by git subcommand for the duration of the test.

    Both `check_call` (used for side-effectful git like `commit`) and
    `check_output` (used for read-only queries like `log` and
    `rev-parse`) are wrapped. The real implementations still run, so
    the git repo is exercised exactly as in production.
    """
    calls: Counter = Counter()
    original_check_call = gitcommand.check_call
    original_check_output = gitcommand.check_output

    def counting_check_call(command: List[str], **kwargs: Any) -> Any:
        if command and Path(command[0]).name == "git":
            calls[extract_git_subcommand(command)] += 1
        return original_check_call(command, **kwargs)

    def counting_check_output(command: List[str], **kwargs: Any) -> Any:
        if command and Path(command[0]).name == "git":
            calls[extract_git_subcommand(command)] += 1
        return original_check_output(command, **kwargs)

    monkeypatch.setattr(gitcommand, "check_call", counting_check_call)
    monkeypatch.setattr(gitcommand, "check_output", counting_check_output)
    yield calls


def make_submit_create_dispatcher() -> Callable[..., Any]:
    """Side-effect for `call_conduit` covering the create-revision flow."""

    def dispatch(method: str, args: Dict[str, Any], **kwargs: Any) -> Any:
        if method == "conduit.ping":
            return {}
        if method == "diffusion.repository.search":
            return {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]}
        if method == "user.query":
            return [{"userName": "alice", "phid": "PHID-USER-1"}]
        if method == "user.whoami":
            return {"phid": "PHID-USER-1", "userName": "alice"}
        if method == "differential.revision.search":
            return {"data": []}
        if method == "differential.diff.search":
            return {"data": []}
        if method == "differential.creatediff":
            return {"phid": "PHID-DIFF-1", "diffid": "1"}
        if method == "differential.revision.edit":
            return {
                "object": {"id": "123", "phid": "PHID-DREV-123"},
                "transactions": [],
            }
        if method == "differential.setdiffproperty":
            return {}
        if method == "reviewhelper.request":
            return {}
        raise AssertionError(
            f"Unexpected Conduit method in git call-count test: {method}"
        )

    return dispatch


def make_patch_raw_dispatcher() -> Callable[..., Any]:
    """Side-effect for `call_conduit` covering the `patch --raw` flow."""

    rev_1 = {
        "id": 1,
        "phid": "PHID-REV-1",
        "fields": {
            "title": "Bug 1 - count test",
            "summary": "",
            "diffPHID": "PHID-DIFF-1",
            "stackGraph": {"PHID-REV-1": []},
            "status": {"value": "needs-review"},
        },
    }
    diff_1 = {
        "id": 1,
        "phid": "PHID-DIFF-1",
        "fields": {
            "dateCreated": 0,
            "refs": [{"identifier": "0", "type": "base"}],
        },
        "attachments": {
            "commits": {
                "commits": [
                    {
                        "author": {
                            "name": "user",
                            "email": "author@example.com",
                            "epoch": None,
                        }
                    }
                ]
            }
        },
    }
    patch_diff = (
        "diff --git a/X b/X\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/X\n"
        "@@ -0,0 +1 @@\n"
        "+a\n"
        "\n"
    )

    def dispatch(method: str, args: Dict[str, Any], **kwargs: Any) -> Any:
        if method == "conduit.ping":
            return {}
        if method == "diffusion.repository.search":
            return {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]}
        if method == "differential.revision.search":
            return {"data": [rev_1]}
        if method == "differential.diff.search":
            return {"data": [diff_1]}
        if method == "differential.getrawdiff":
            return patch_diff
        raise AssertionError(
            f"Unexpected Conduit method in git call-count test: {method}"
        )

    return dispatch


# Expected git invocations for `moz-phab submit` of a single new commit
# against a fresh `git_repo_path`. 14 distinct subcommands, 18 calls
# total: a lot for a one-commit submit, and exactly the kind of cost
# this test exists to pin so future reductions surface explicitly.
# Re-derive if the workflow is refactored intentionally.
SUBMIT_CREATE_EXPECTED: Dict[str, int] = {
    "--version": 1,
    "branch": 1,
    "cat-file": 2,
    "checkout": 3,
    "commit-tree": 1,
    "config": 1,
    "diff-index": 1,
    "diff-tree": 1,
    "gc": 2,
    "log": 2,
    "rev-list": 1,
    "show": 1,
    "symbolic-ref": 1,
    "update-ref": 1,
}


def test_submit_create_git_call_count(
    in_process: None,
    git_repo_path: Path,
    init_sha: str,
    git_call_counter: Counter,
) -> None:
    from tests.conftest import git_out

    (git_repo_path / "X").write_text("hello\nworld\n")
    git_out("add", ".")
    git_out("commit", "-m", "Bug 1 - count test r?alice")

    # `git_out` calls subprocess directly (it's the test harness, not
    # moz-phab), so its invocations don't pass through the counted
    # wrappers. Reset the counter here so only moz-phab's own git
    # subprocesses are counted by `mozphab.main` below.
    git_call_counter.clear()

    call_conduit.reset_mock()
    call_conduit.side_effect = make_submit_create_dispatcher()

    mozphab.main(
        ["submit", "--yes", "--bug", "1", init_sha],
        is_development=True,
    )

    assert dict(git_call_counter) == SUBMIT_CREATE_EXPECTED, (
        "`moz-phab submit` of a single new commit invoked an unexpected "
        f"set of git subcommands. Expected {SUBMIT_CREATE_EXPECTED}, got "
        f"{dict(git_call_counter)}. Re-derive `SUBMIT_CREATE_EXPECTED` if "
        "this change is intentional."
    )


# Expected git invocations for `moz-phab patch D1 --raw` against a
# fresh `git_repo_path`. Far smaller than submit -- `--raw` prints the
# diff to stdout rather than applying it, so most of moz-phab's git
# work falls away. Re-derive if the workflow is refactored
# intentionally.
PATCH_RAW_EXPECTED: Dict[str, int] = {
    "--version": 1,
    "config": 1,
    "gc": 1,
}


def test_patch_raw_git_call_count(
    in_process: None,
    git_repo_path: Path,
    init_sha: str,
    git_call_counter: Counter,
) -> None:
    git_call_counter.clear()

    call_conduit.reset_mock()
    call_conduit.side_effect = make_patch_raw_dispatcher()

    mozphab.main(["patch", "D1", "--raw"], is_development=True)

    assert dict(git_call_counter) == PATCH_RAW_EXPECTED, (
        "`moz-phab patch D1 --raw` invoked an unexpected set of git "
        f"subcommands. Expected {PATCH_RAW_EXPECTED}, got "
        f"{dict(git_call_counter)}. Re-derive `PATCH_RAW_EXPECTED` if "
        "this change is intentional."
    )
