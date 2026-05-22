# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""End-to-end benchmark for the `moz-phab patch` workflow.

Drives the real CLI through `mozphab.main([...], is_development=True)`
against a real git repository (`git_repo_path` from `tests/conftest.py`)
and the integration tests' `in_process` fixture. As in
`test_bench_submit.py`, only the HTTP boundary is mocked, via a
function-based module-level `call_conduit`.

The benchmark uses `--raw` mode because it exercises the full
`get_revisions` / `get_diffs` / parallel `differential.getrawdiff`
sequence without needing a real apply-able patch on disk. Future work
could add a `--no-commit` variant to also cover the `git apply` path.
"""

from pathlib import Path
from typing import Any, Callable, Dict
from unittest import mock

import pytest

from mozphab import mozphab

call_conduit = mock.Mock()


REV_1: Dict[str, Any] = {
    "id": 1,
    "phid": "PHID-REV-1",
    "fields": {
        "title": "Bug 1 - bench patch",
        "summary": "",
        "diffPHID": "PHID-DIFF-1",
        "stackGraph": {"PHID-REV-1": []},
        "status": {"value": "needs-review"},
    },
}


DIFF_1: Dict[str, Any] = {
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


PATCH_1_DIFF = (
    "diff --git a/X b/X\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/X\n"
    "@@ -0,0 +1 @@\n"
    "+a\n"
    "\n"
)


def make_patch_raw_dispatcher() -> Callable[..., Any]:
    """Return a `call_conduit.side_effect` for the `patch --raw` flow.

    Dispatching by method name (rather than a positional response tuple)
    keeps the function reusable across codspeed iterations and tolerant
    of the parallel `ping` / `repo.check_vcs` race that runs at the
    start of `patch`.
    """

    def dispatch(method: str, args: Dict[str, Any], **kwargs: Any) -> Any:
        if method == "conduit.ping":
            return {}
        if method == "diffusion.repository.search":
            return {"data": [{"phid": "PHID-REPO-1", "fields": {"vcs": "git"}}]}
        if method == "differential.revision.search":
            return {"data": [REV_1]}
        if method == "differential.diff.search":
            return {"data": [DIFF_1]}
        if method == "differential.getrawdiff":
            return PATCH_1_DIFF
        raise AssertionError(f"Unexpected Conduit method in patch benchmark: {method}")

    return dispatch


def reset_repo_to_init(git_repo_path: Path, init_sha: str) -> None:
    """Reset HEAD and the working tree to `init_sha` for a fresh iteration."""
    from tests.conftest import git_out

    git_out("reset", "--hard", init_sha)
    git_out("clean", "-fdx", "--", ".")


@pytest.mark.benchmark
def test_bench_patch_raw(in_process: None, git_repo_path: Path, init_sha: str) -> None:
    """Benchmark `moz-phab patch D1 --raw` for a single revision."""
    reset_repo_to_init(git_repo_path, init_sha)

    call_conduit.reset_mock()
    call_conduit.side_effect = make_patch_raw_dispatcher()

    mozphab.main(["patch", "D1", "--raw"], is_development=True)
