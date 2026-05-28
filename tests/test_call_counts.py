# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Regression test pinning the Conduit API call shape of submit/patch.

The benchmark suite under `tests/benchmarks/` measures how long each
workflow takes; this test asserts what each workflow *does*. It mocks
`ConduitAPI.call` with a `Counter` and runs `submit._submit` and
`patch.patch` end-to-end. Any change that adds a redundant network
trip, breaks a `SimpleCache` invariant, or rearranges the batched
fetch pattern will move at least one counter and fail the assertion.

Concretely it catches things like:

- A new helper added to the submit loop that calls `get_revisions`
  outside the validation preload.
- A regression where the per-commit `has_revision_reviewers()` check
  stops hitting the cache.
- A patch refactor that splits the batched ancestor fetch back into
  per-revision calls.

Unlike the benchmarks, this runs in the normal pytest job (no
codspeed dependency), so a regression fails the PR / commit gate
loudly rather than as a silent drift in a dashboard number.
"""

import argparse
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from unittest import mock

import pytest

from mozphab.commands import patch as patch_cmd
from mozphab.commands import submit
from mozphab.commits import Commit
from mozphab.conduit import ConduitAPI, conduit
from mozphab.simplecache import cache

PHAB_URL = "http://phab.test"
REPO_PHID = "PHID-REPO-1"
ME_PHID = "PHID-USER-me"


@dataclass
class FakeState:
    """In-memory store for the canned Conduit responses.

    Two lookup tables keyed by `id` and `phid` keep the dispatcher
    simple -- `differential.revision.search` accepts either, and the
    patch flow uses phid-batched fetches while the submit flow uses
    id-batched ones.
    """

    revisions_by_id: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    revisions_by_phid: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    diffs_by_phid: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    diffs_by_id: Dict[int, Dict[str, Any]] = field(default_factory=dict)


def revision_phid(rev_id: int) -> str:
    return f"PHID-DREV-{rev_id}"


def diff_phid(rev_id: int) -> str:
    return f"PHID-DIFF-{rev_id}"


def build_submit_commit(index: int) -> Commit:
    """Return a `Commit` representing an update to revision `100 + index`."""
    rev_id = 100 + index
    node = f"newcommit{index:032x}"
    title = f"Bug 1 - Commit {index}"
    body = f"{title}\n\nDifferential Revision: {PHAB_URL}/D{rev_id}\n"
    return Commit(
        name=f"c{index}",
        node=node,
        orig_node=node,
        title=title,
        title_preview=title,
        body=body,
        bug_id="1",
        bug_id_orig="1",
        rev_id=rev_id,
        reviewers={"granted": [], "request": []},
        submit=True,
    )


def build_submit_args() -> argparse.Namespace:
    args = argparse.Namespace()
    args.command = "submit"
    args.message = None
    args.yes = True
    args.lesscontext = False
    args.no_stack = False
    args.force = False
    args.force_vcs = False
    args.safe_mode = False
    args.no_bug = False
    args.wip = False
    args.no_wip = False
    args.interactive = False
    args.single = False
    args.ai = True
    args.reviewer = None
    args.blocker = None
    args.bug = None
    args.test_plan = None
    return args


def build_patch_args(target_rev_id: int) -> argparse.Namespace:
    args = argparse.Namespace()
    args.revision_id = target_rev_id
    args.no_commit = False
    args.raw = False
    args.apply_to = "base"
    args.yes = True
    args.skip_dependencies = False
    args.include_abandoned = False
    args.force_vcs = False
    args.name = None
    args.diff_id = None
    return args


def populate_submit_state(state: FakeState, num_commits: int) -> None:
    """Seed `state` with revisions and diffs the submit flow will look up.

    The diff's recorded commit SHA differs from the local commit `node`
    so `validate_commit_stack` sees `sha1_changed` and keeps every
    commit flagged for submission.
    """
    for index in range(1, num_commits + 1):
        rev_id = 100 + index
        rev = {
            "id": rev_id,
            "phid": revision_phid(rev_id),
            "fields": {
                "bugzilla.bug-id": "1",
                "status": {"value": "needs-review", "closed": False},
                "authorPHID": ME_PHID,
                "diffPHID": diff_phid(rev_id),
                "repositoryPHID": REPO_PHID,
                "isDraft": False,
                "testPlan": "",
                "stackGraph": {},
            },
            "attachments": {"reviewers": {"reviewers": []}},
        }
        diff = {
            "id": rev_id,
            "phid": diff_phid(rev_id),
            "fields": {"revisionPHID": revision_phid(rev_id), "dateCreated": 0},
            "attachments": {
                "commits": {
                    "commits": [
                        {
                            "identifier": f"oldcommit{rev_id:032x}",
                            "author": {
                                "name": "me",
                                "email": "me@example.com",
                            },
                        }
                    ]
                }
            },
        }
        state.revisions_by_id[rev_id] = rev
        state.revisions_by_phid[rev["phid"]] = rev
        state.diffs_by_id[diff["id"]] = diff
        state.diffs_by_phid[diff["phid"]] = diff


def populate_patch_state(state: FakeState, num_revisions: int) -> None:
    """Seed `state` for a linear `num_revisions`-revision stack.

    Every revision carries the full stack graph in its `fields.stackGraph`
    -- the production `patch` code reads the graph from the target
    revision only, but seeding all of them keeps the dispatcher simple.
    """
    rev_ids = [100 + index for index in range(1, num_revisions + 1)]
    stack_graph: Dict[str, List[str]] = {}
    for position, rev_id in enumerate(rev_ids):
        if position == 0:
            stack_graph[revision_phid(rev_id)] = []
        else:
            stack_graph[revision_phid(rev_id)] = [revision_phid(rev_ids[position - 1])]

    base_node = "basesha000000000000000000000000000000000"
    for rev_id in rev_ids:
        rev = {
            "id": rev_id,
            "phid": revision_phid(rev_id),
            "fields": {
                "title": f"Bug 1 - Commit {rev_id}",
                "summary": f"Summary for D{rev_id}",
                "diffPHID": diff_phid(rev_id),
                "repositoryPHID": REPO_PHID,
                "status": {"value": "needs-review", "closed": False},
                "stackGraph": stack_graph,
                "isDraft": False,
                "bugzilla.bug-id": "1",
                "authorPHID": "PHID-USER-author",
            },
            "attachments": {"reviewers": {"reviewers": []}},
        }
        diff = {
            "id": rev_id + 5000,
            "phid": diff_phid(rev_id),
            "fields": {
                "revisionPHID": revision_phid(rev_id),
                "dateCreated": 0,
                "refs": [{"identifier": base_node, "type": "base"}],
            },
            "attachments": {
                "commits": {
                    "commits": [
                        {
                            "identifier": f"sha{rev_id:037x}",
                            "author": {
                                "name": "me",
                                "email": "me@example.com",
                            },
                        }
                    ]
                }
            },
        }
        state.revisions_by_id[rev_id] = rev
        state.revisions_by_phid[rev["phid"]] = rev
        state.diffs_by_id[diff["id"]] = diff
        state.diffs_by_phid[diff["phid"]] = diff


def build_fake_repo() -> mock.MagicMock:
    """Return a `Repository` stand-in covering everything submit/patch touch."""
    repo = mock.MagicMock()
    repo.phab_url = PHAB_URL
    repo.phab_vcs = "git"
    repo.vcs = "git"
    repo.phid = REPO_PHID
    repo.path = "/fake"
    repo.dot_path = "/fake/.git"
    repo.api_url = "https://phab.test/api/"
    repo.is_cinnabar_required = False
    repo.untracked.return_value = []
    repo.check_vcs.return_value = None
    repo.before_submit.return_value = None
    repo.after_submit.return_value = None
    repo.cleanup.return_value = None
    repo.finalize.return_value = None
    repo.refresh_commit_stack.return_value = None
    repo.amend_commit.return_value = None
    repo.check_commits_for_submit.return_value = None
    repo.is_worktree_clean.return_value = True
    repo.check_node.side_effect = lambda node: node
    repo.before_patch.return_value = None
    repo.apply_patch.return_value = None
    repo.format_patch.return_value = ""
    repo.get_public_node = lambda node: node or ""

    diff = mock.MagicMock()
    diff.changes = {}
    diff.id = None
    diff.phid = None
    repo.get_diff.return_value = diff
    return repo


def make_fake_call(state: FakeState, calls: Counter) -> Callable[..., Any]:
    """Build the `ConduitAPI.call` side-effect that records and dispatches."""

    next_new_diff_id = [9000]

    def fake_call(
        _self: Any,
        method: str,
        args: Dict[str, Any],
        *,
        api_token: Optional[str] = None,
    ) -> Any:
        calls[method] += 1

        if method == "differential.revision.search":
            constraints = args.get("constraints", {})
            ids = constraints.get("ids")
            phids = constraints.get("phids")
            if ids is not None:
                wanted = {int(value) for value in ids}
                return {
                    "data": [
                        state.revisions_by_id[i]
                        for i in wanted
                        if i in state.revisions_by_id
                    ]
                }
            if phids is not None:
                wanted = set(phids)
                return {
                    "data": [
                        state.revisions_by_phid[p]
                        for p in wanted
                        if p in state.revisions_by_phid
                    ]
                }
            return {"data": list(state.revisions_by_id.values())}

        if method == "differential.diff.search":
            constraints = args.get("constraints", {})
            phids = constraints.get("phids")
            ids = constraints.get("ids")
            if phids is not None:
                wanted = set(phids)
                return {
                    "data": [
                        state.diffs_by_phid[p]
                        for p in wanted
                        if p in state.diffs_by_phid
                    ]
                }
            if ids is not None:
                wanted = {int(value) for value in ids}
                return {
                    "data": [
                        state.diffs_by_id[i] for i in wanted if i in state.diffs_by_id
                    ]
                }
            return {"data": list(state.diffs_by_phid.values())}

        if method == "user.whoami":
            return {"phid": ME_PHID, "userName": "me"}

        if method == "differential.creatediff":
            next_new_diff_id[0] += 1
            new_id = next_new_diff_id[0]
            return {"diffid": str(new_id), "phid": f"PHID-DIFF-new-{new_id}"}

        if method == "differential.revision.edit":
            identifier = args.get("objectIdentifier")
            rev_id = int(identifier) if identifier is not None else 999
            return {
                "object": {"id": rev_id, "phid": revision_phid(rev_id)},
                "transactions": [],
            }

        if method == "differential.setdiffproperty":
            return {}

        if method == "reviewhelper.request":
            return {}

        if method == "differential.getrawdiff":
            return ""

        if method == "conduit.ping":
            return {}

        raise AssertionError(f"Unexpected Conduit method in call-count test: {method}")

    return fake_call


@pytest.fixture
def call_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Tuple[FakeState, Counter, mock.MagicMock]]:
    """Yield `(state, calls, repo)` with `ConduitAPI.call` mocked.

    The repo is wired up as `conduit.repo` so `submit_diff` and
    `set_diff_property` can read `phab_url` / `phab_vcs` / `path` /
    `get_public_node` without further patching. `ConduitAPI.check` is
    also short-circuited because its real implementation touches the
    filesystem for a cache file.
    """
    state = FakeState()
    calls: Counter = Counter()
    repo = build_fake_repo()

    monkeypatch.setattr(ConduitAPI, "call", make_fake_call(state, calls))
    monkeypatch.setattr(ConduitAPI, "check", lambda self: True)

    previous_repo = conduit.repo
    conduit.set_repo(repo)
    cache.reset()
    try:
        yield state, calls, repo
    finally:
        cache.reset()
        conduit.repo = previous_repo


# Expected ConduitAPI.call shape for `_submit` of an N-commit stack of
# updates with no reviewers, AI review enabled, and per-commit SHA1
# changes. Re-derive these from a fresh run if the workflow is
# refactored intentionally.
SUBMIT_EXPECTED: Dict[int, Dict[str, int]] = {
    1: {
        "differential.revision.search": 1,
        "differential.diff.search": 1,
        "user.whoami": 1,
        "differential.creatediff": 1,
        "differential.revision.edit": 1,
        "differential.setdiffproperty": 1,
        "reviewhelper.request": 1,
    },
    3: {
        "differential.revision.search": 3,
        "differential.diff.search": 1,
        "user.whoami": 1,
        "differential.creatediff": 3,
        "differential.revision.edit": 3,
        "differential.setdiffproperty": 3,
        "reviewhelper.request": 3,
    },
    5: {
        "differential.revision.search": 5,
        "differential.diff.search": 1,
        "user.whoami": 1,
        "differential.creatediff": 5,
        "differential.revision.edit": 5,
        "differential.setdiffproperty": 5,
        "reviewhelper.request": 5,
    },
    10: {
        "differential.revision.search": 10,
        "differential.diff.search": 1,
        "user.whoami": 1,
        "differential.creatediff": 10,
        "differential.revision.edit": 10,
        "differential.setdiffproperty": 10,
        "reviewhelper.request": 10,
    },
}


@pytest.mark.parametrize("num_commits", sorted(SUBMIT_EXPECTED))
def test_submit_call_count(
    call_harness: Tuple[FakeState, Counter, mock.MagicMock],
    num_commits: int,
) -> None:
    state, calls, repo = call_harness
    populate_submit_state(state, num_commits)
    commits = [build_submit_commit(index) for index in range(1, num_commits + 1)]
    repo.commit_stack.return_value = commits

    with mock.patch("mozphab.commands.submit.config") as fake_config:
        fake_config.warn_untracked = False
        fake_config.auto_submit = True
        fake_config.ai_review = False
        fake_config.always_blocking = False
        fake_config.always_full_stack = False
        fake_config.filename = "/fake/.moz-phab-config"

        submit._submit(repo, build_submit_args())

    expected = SUBMIT_EXPECTED[num_commits]
    assert dict(calls) == expected, (
        f"Submit of {num_commits} commit(s) made an unexpected set of "
        f"Conduit calls. Expected {expected}, got {dict(calls)}. "
        f"Re-derive `SUBMIT_EXPECTED` if this change is intentional."
    )


# Expected ConduitAPI.call shape for `patch.patch` against the top of
# an N-revision linear stack with `--apply-to=base` and the default
# create-commit flow. Re-derive from a fresh run if the workflow is
# refactored intentionally.
PATCH_EXPECTED: Dict[int, Dict[str, int]] = {
    1: {
        "differential.revision.search": 1,
        "differential.diff.search": 1,
        "differential.getrawdiff": 1,
    },
    3: {
        "differential.revision.search": 2,
        "differential.diff.search": 1,
        "differential.getrawdiff": 3,
    },
    5: {
        "differential.revision.search": 2,
        "differential.diff.search": 1,
        "differential.getrawdiff": 5,
    },
    10: {
        "differential.revision.search": 2,
        "differential.diff.search": 1,
        "differential.getrawdiff": 10,
    },
}


@pytest.mark.parametrize("num_revisions", sorted(PATCH_EXPECTED))
def test_patch_call_count(
    call_harness: Tuple[FakeState, Counter, mock.MagicMock],
    num_revisions: int,
) -> None:
    state, calls, repo = call_harness
    populate_patch_state(state, num_revisions)
    target_rev_id = 100 + num_revisions

    with mock.patch("mozphab.commands.patch.config") as fake_config:
        fake_config.create_commit = True
        fake_config.always_full_stack = True
        fake_config.apply_patch_to = "base"
        fake_config.branch_name_template = "phab-D{rev_id}"

        patch_cmd.patch(repo, build_patch_args(target_rev_id))

    expected = PATCH_EXPECTED[num_revisions]
    assert dict(calls) == expected, (
        f"Patch of {num_revisions} revision(s) made an unexpected set "
        f"of Conduit calls. Expected {expected}, got {dict(calls)}. "
        f"Re-derive `PATCH_EXPECTED` if this change is intentional."
    )
