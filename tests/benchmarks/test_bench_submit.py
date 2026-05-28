# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""End-to-end benchmark for the `moz-phab submit` workflow.

Drives the real CLI through `mozphab.main([...], is_development=True)`
against a real git repository (`git_repo_path` from `tests/conftest.py`)
and the integration tests' `in_process` fixture. Only the HTTP boundary
(`ConduitAPI.call`) is mocked, via a function-based module-level
`call_conduit` that the `in_process` fixture wires up. The dispatcher
returns plausible payloads keyed by Conduit method name, so codspeed
iterations don't exhaust a fixed response sequence.

Because the benchmark exercises the real `Repository` layer, the
measurement includes the Python-side cost of spawning git
subprocesses (`subprocess.Popen` setup, command construction, output
parsing). codspeed's instruction-count mode tracks exactly that --
when a future change removes a git invocation, the dropped Popen
machinery shows up as a measurable instruction-count delta. The
subprocess work itself runs outside the measured process and so does
not contribute to the count, which is the right behaviour for
isolating moz-phab's own perf signal.
"""

from pathlib import Path
from typing import Any, Callable, Dict
from unittest import mock

import pytest

from mozphab import mozphab

# `in_process` reads `call_conduit` from the test module and binds it as
# `ConduitAPI.call`. A Mock with a function-based `side_effect`
# dispatcher (rather than the tuple-of-responses pattern the
# integration tests use) keeps the dispatcher reusable across codspeed
# iterations.
call_conduit = mock.Mock()


def make_submit_create_dispatcher() -> Callable[..., Any]:
    """Return a `call_conduit.side_effect` for the create-revision flow."""

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
            # No existing revisions for the create flow.
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
        raise AssertionError(f"Unexpected Conduit method in submit benchmark: {method}")

    return dispatch


def reset_repo_to_init(git_repo_path: Path, init_sha: str) -> None:
    """Reset the working tree and HEAD to `init_sha` for a fresh iteration.

    Codspeed wall-time mode invokes the test body many times to collect
    statistics; each iteration needs to see the same starting repo
    state. Instrumentation mode runs once, so this is effectively a
    no-op there.
    """
    from tests.conftest import git_out

    git_out("reset", "--hard", init_sha)
    git_out("clean", "-fdx", "--", ".")


@pytest.mark.benchmark
def test_bench_submit_create(
    in_process: None, git_repo_path: Path, init_sha: str
) -> None:
    """Benchmark `moz-phab submit` for a single new commit."""
    from tests.conftest import git_out

    reset_repo_to_init(git_repo_path, init_sha)

    (git_repo_path / "X").write_text("hello\nworld\n")
    git_out("add", ".")
    git_out("commit", "-m", "Bug 1 - bench commit r?alice")

    call_conduit.reset_mock()
    call_conduit.side_effect = make_submit_create_dispatcher()

    mozphab.main(
        ["submit", "--yes", "--bug", "1", init_sha],
        is_development=True,
    )
