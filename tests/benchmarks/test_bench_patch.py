# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Benchmark for the `moz-phab patch` workflow.

Drives `mozphab.commands.patch.patch` end-to-end against a mocked
`ConduitAPI.call` so the real conduit helpers, the `SimpleCache`, and
the parallel `differential.getrawdiff` fan-out all run. Only the HTTP
boundary and the local-VCS / filesystem touchpoints (`repo.apply_patch`
et al.) are mocked.

Parametrised over revision count so codspeed can detect regressions in
per-revision scaling -- the parallel raw-diff fetch is the workflow's
main optimisation knob and is exactly what we want to measure.
"""

import argparse
from unittest import mock

import pytest

from mozphab.commands import patch as patch_cmd
from mozphab.conduit import conduit
from mozphab.simplecache import cache

PHAB_URL = "http://phab.test"
REPO_PHID = "PHID-REPO-1"
BASE_NODE = "basesha000000000000000000000000000000000"


def revision_phid(rev_id):
    return f"PHID-DREV-{rev_id}"


def diff_phid(rev_id):
    return f"PHID-DIFF-{rev_id}"


def build_revision(rev_id, parent_rev_ids, stack_graph):
    """Return a `differential.revision.search` payload.

    `stack_graph` must already contain the linear ancestor chain for
    every revision in the stack -- the real Phabricator copies the
    graph into each revision's payload, and the patch code reads it
    from the target revision only.
    """
    return {
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


def build_diff(rev_id):
    return {
        "id": rev_id + 5000,
        "phid": diff_phid(rev_id),
        "fields": {
            "revisionPHID": revision_phid(rev_id),
            "dateCreated": 0,
            "refs": [{"identifier": BASE_NODE, "type": "base"}],
        },
        "attachments": {
            "commits": {
                "commits": [
                    {
                        "identifier": f"sha{rev_id:037x}",
                        "author": {"name": "me", "email": "me@example.com"},
                    }
                ]
            }
        },
    }


def build_stack_graph(num_revisions):
    """Build a linear-chain `stackGraph` for `num_revisions` revisions.

    The root (rev_id 101) has no parents; each subsequent revision has
    exactly one parent -- the revision below it. This matches the shape
    `patch._get_ancestors_from_stack_graph` walks.
    """
    rev_ids = [100 + index for index in range(1, num_revisions + 1)]
    graph = {}
    for position, rev_id in enumerate(rev_ids):
        phid = revision_phid(rev_id)
        if position == 0:
            graph[phid] = []
        else:
            graph[phid] = [revision_phid(rev_ids[position - 1])]
    return graph


def build_fake_call(num_revisions):
    """Return a `ConduitAPI.call` side-effect for a patch workload.

    All revisions share the same `stackGraph` so the helper functions
    can walk the ancestor chain from the target downward without extra
    API trips.
    """
    stack_graph = build_stack_graph(num_revisions)
    rev_ids = [100 + index for index in range(1, num_revisions + 1)]
    revisions = [build_revision(rev_id, [], stack_graph) for rev_id in rev_ids]
    diffs = [build_diff(rev_id) for rev_id in rev_ids]
    revisions_by_id = {r["id"]: r for r in revisions}
    revisions_by_phid = {r["phid"]: r for r in revisions}
    diffs_by_phid = {d["phid"]: d for d in diffs}

    def fake_call(_self, method, args, *, api_token=None):
        if method == "differential.revision.search":
            constraints = args.get("constraints", {})
            ids = constraints.get("ids")
            phids = constraints.get("phids")
            if ids is not None:
                wanted = {int(value) for value in ids}
                return {
                    "data": [revisions_by_id[i] for i in wanted if i in revisions_by_id]
                }
            if phids is not None:
                wanted = set(phids)
                return {
                    "data": [
                        revisions_by_phid[p] for p in wanted if p in revisions_by_phid
                    ]
                }
            return {"data": revisions}

        if method == "differential.diff.search":
            constraints = args.get("constraints", {})
            phids = constraints.get("phids")
            ids = constraints.get("ids")
            if phids is not None:
                wanted = set(phids)
                return {
                    "data": [diffs_by_phid[p] for p in wanted if p in diffs_by_phid]
                }
            if ids is not None:
                wanted = {int(value) for value in ids}
                return {"data": [d for d in diffs if d["id"] in wanted]}
            return {"data": diffs}

        if method == "differential.getrawdiff":
            return ""

        if method == "conduit.ping":
            return {}

        raise AssertionError(f"Unexpected Conduit method in benchmark: {method}")

    return fake_call


def build_args(target_rev_id):
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


def build_fake_repo():
    repo = mock.MagicMock()
    repo.phab_url = PHAB_URL
    repo.phab_vcs = "git"
    repo.vcs = "git"
    repo.phid = REPO_PHID
    repo.path = "/fake"
    repo.dot_path = "/fake/.git"
    repo.api_url = "https://phab.test/api/"
    repo.is_worktree_clean.return_value = True
    repo.check_vcs.return_value = None
    repo.check_node.side_effect = lambda node: node
    repo.before_patch.return_value = None
    repo.apply_patch.return_value = None
    repo.format_patch.return_value = ""
    return repo


@pytest.fixture
def patch_workload():
    """Yield a callable that runs `patch.patch` for `num_revisions` revisions."""

    def run(num_revisions):
        target_rev_id = 100 + num_revisions
        repo = build_fake_repo()
        args = build_args(target_rev_id)

        with (
            mock.patch("mozphab.conduit.ConduitAPI.check", return_value=True),
            mock.patch(
                "mozphab.conduit.ConduitAPI.call",
                side_effect=build_fake_call(num_revisions),
                autospec=True,
            ),
            mock.patch("mozphab.commands.patch.config") as fake_config,
        ):
            fake_config.create_commit = True
            fake_config.always_full_stack = True
            fake_config.apply_patch_to = "base"
            fake_config.branch_name_template = "phab-D{rev_id}"

            previous_repo = conduit.repo
            conduit.set_repo(repo)
            cache.reset()
            try:
                patch_cmd.patch(repo, args)
            finally:
                cache.reset()
                conduit.repo = previous_repo

    yield run


@pytest.mark.parametrize("num_revisions", [1, 3, 5, 10])
@pytest.mark.benchmark
def test_patch_stack(patch_workload, num_revisions):
    patch_workload(num_revisions)
