# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Benchmark for the `moz-phab submit` workflow.

Drives `mozphab.commands.submit._submit` end-to-end against a mocked
`ConduitAPI.call` so the real `mozphab.conduit` helpers run unchanged --
`SimpleCache` deduplicates lookups for real, `update_revision` walks the
real transaction-building code, and `request_ai_reviews` fans out
through the real `ThreadPoolExecutor`. Only the HTTP boundary
(`ConduitAPI.call`) and the `Repository` layer are mocked; everything
between them is the production code path that codspeed will measure.

The benchmark is parametrised over stack size to expose per-commit
scaling behaviour rather than just a single number.
"""

import argparse
from unittest import mock

import pytest

from mozphab.commands import submit
from mozphab.commits import Commit
from mozphab.conduit import conduit
from mozphab.simplecache import cache

PHAB_URL = "http://phab.test"
REPO_PHID = "PHID-REPO-1"
ME_PHID = "PHID-USER-me"


def build_commit(index):
    """Return a `Commit` shaped like an update to revision `100 + index`."""
    rev_id = 100 + index
    node = f"newcommit{index:032x}"
    title = f"Bug 1 - Commit {index}"
    body = f"{title}\n\n" f"Differential Revision: {PHAB_URL}/D{rev_id}\n"
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


def build_args():
    """Return an `argparse.Namespace` populated with every flag `_submit` reads."""
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


def build_revision(rev_id):
    """Return a revision payload shaped like `differential.revision.search`."""
    return {
        "id": rev_id,
        "phid": f"PHID-DREV-{rev_id}",
        "fields": {
            "bugzilla.bug-id": "1",
            "status": {"value": "needs-review", "closed": False},
            "authorPHID": ME_PHID,
            "diffPHID": f"PHID-DIFF-{rev_id}",
            "repositoryPHID": REPO_PHID,
            "isDraft": False,
            "testPlan": "",
            "stackGraph": {},
        },
        "attachments": {"reviewers": {"reviewers": []}},
    }


def build_diff(rev_id):
    """Return a diff payload shaped like `differential.diff.search`.

    The recorded commit SHA differs from the local commit `node`, so
    `validate_commit_stack` will see `sha1_changed` and keep the commit
    flagged for submission instead of short-circuiting.
    """
    return {
        "id": rev_id,
        "phid": f"PHID-DIFF-{rev_id}",
        "fields": {"revisionPHID": f"PHID-DREV-{rev_id}", "dateCreated": 0},
        "attachments": {
            "commits": {
                "commits": [
                    {
                        "identifier": f"oldcommit{rev_id:032x}",
                        "author": {"name": "me", "email": "me@example.com"},
                    }
                ]
            }
        },
    }


def build_fake_call(num_commits):
    """Return a function suitable for use as `ConduitAPI.call.side_effect`.

    Dispatches on the Conduit method name and returns plausible payloads
    so the real `mozphab.conduit` helpers can decode them without
    raising.
    """
    revisions = [build_revision(100 + index) for index in range(1, num_commits + 1)]
    diffs = [build_diff(100 + index) for index in range(1, num_commits + 1)]
    next_new_diff_id = [9000]

    def fake_call(_self, method, args, *, api_token=None):
        if method == "differential.revision.search":
            constraints = args.get("constraints", {})
            ids = constraints.get("ids")
            phids = constraints.get("phids")
            if ids is not None:
                wanted = {int(value) for value in ids}
                return {"data": [r for r in revisions if r["id"] in wanted]}
            if phids is not None:
                wanted = set(phids)
                return {"data": [r for r in revisions if r["phid"] in wanted]}
            return {"data": revisions}

        if method == "differential.diff.search":
            constraints = args.get("constraints", {})
            phids = constraints.get("phids")
            ids = constraints.get("ids")
            if phids is not None:
                wanted = set(phids)
                return {"data": [d for d in diffs if d["phid"] in wanted]}
            if ids is not None:
                wanted = {int(value) for value in ids}
                return {"data": [d for d in diffs if d["id"] in wanted]}
            return {"data": diffs}

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
                "object": {"id": rev_id, "phid": f"PHID-DREV-{rev_id}"},
                "transactions": [],
            }

        if method == "differential.setdiffproperty":
            return {}

        if method == "reviewhelper.request":
            return {}

        raise AssertionError(f"Unexpected Conduit method in benchmark: {method}")

    return fake_call


def build_fake_repo():
    """Return a `MagicMock` shaped like the moz-phab `Repository` layer."""
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
    repo.get_public_node = lambda node: node or ""

    diff = mock.MagicMock()
    diff.changes = {}
    diff.id = None
    diff.phid = None
    repo.get_diff.return_value = diff
    return repo


@pytest.fixture
def submit_workload():
    """Yield a callable that runs `_submit` for `num_commits` commits.

    Each invocation rebuilds the canned conduit responses for that stack
    size, resets `SimpleCache`, and tears the mock state down again so
    the benchmark can iterate without leaking between runs.
    """

    def run(num_commits):
        commits = [build_commit(index) for index in range(1, num_commits + 1)]
        repo = build_fake_repo()
        repo.commit_stack.return_value = commits
        args = build_args()

        with (
            mock.patch("mozphab.conduit.ConduitAPI.check", return_value=True),
            mock.patch(
                "mozphab.conduit.ConduitAPI.call",
                side_effect=build_fake_call(num_commits),
                autospec=True,
            ),
            mock.patch("mozphab.commands.submit.config") as fake_config,
        ):
            fake_config.warn_untracked = False
            fake_config.auto_submit = True
            fake_config.ai_review = False
            fake_config.always_blocking = False
            fake_config.always_full_stack = False
            fake_config.filename = "/fake/.moz-phab-config"

            previous_repo = conduit.repo
            conduit.set_repo(repo)
            cache.reset()
            try:
                return submit._submit(repo, args)
            finally:
                cache.reset()
                conduit.repo = previous_repo

    yield run


@pytest.mark.parametrize("num_commits", [1, 3, 5, 10])
@pytest.mark.benchmark
def test_submit_stack(submit_workload, num_commits):
    submitted = submit_workload(num_commits)
    assert (
        len(submitted) == num_commits
    ), "`_submit` should return one entry per commit in the stack."
