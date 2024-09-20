# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from mozphab.commands.submit import (
    local_uplift_if_possible,
    update_commits_for_uplift,
)
from mozphab.commits import Commit
from mozphab.helpers import ORIGINAL_DIFF_REV_RE


class Repo:
    def __init__(self, unified_head="beta", is_descendant=True, phid="PHID-beta"):
        self.unified_head = unified_head
        self._is_descendant = is_descendant
        self.uplift_called = False
        self.phid = phid

    def map_callsign_to_unified_head(self, *args, **kwargs):
        return self.unified_head

    def is_descendant(self, *args, **kwargs):
        return self._is_descendant

    def uplift_commits(self, *args, **kwargs):
        self.uplift_called = True


def test_local_uplift_if_possible():
    class Args:
        def __init__(self, no_rebase=False, train="train"):
            self.no_rebase = no_rebase
            self.train = train

    commits = [
        Commit(
            title="A",
            reviewers={"granted": ["john"], "request": []},
            bug_id=None,
            body="",
            rev_id=1,
        ),
    ]

    repo = Repo()

    args = Args(no_rebase=True)
    assert (
        local_uplift_if_possible(args, repo, commits) is True
    ), "Should always do a one-off uplift when `--no-rebase` is set."

    args = Args()
    repo = Repo(unified_head=None)

    assert (
        local_uplift_if_possible(args, repo, commits) is True
    ), "Should avoid do a one-off when no unified head is found."

    repo = Repo(is_descendant=True)
    assert (
        local_uplift_if_possible(args, repo, commits) is False
    ), "Should avoid uplifting commits locally when destination is a descendant."

    # Rebase-uplift case.
    repo = Repo(
        is_descendant=False,
        unified_head="beta",
    )
    args = Args(
        no_rebase=False,
        train="beta",
    )
    assert (
        local_uplift_if_possible(args, repo, commits) is False
    ), "Uplifting commits locally should amend them as well."
    assert (
        repo.uplift_called
    ), "Should call `uplift_commits` when non-descendant unified head found."


def test_update_commits_for_uplift_sets_relman_review():
    commits = [
        Commit(
            title="A",
            reviewers={"granted": ["john"], "request": []},
            bug_id=None,
            body="",
            rev_id=None,
        ),
        Commit(
            title="B",
            reviewers={"granted": ["john"], "request": ["doe"]},
            bug_id=None,
            body="",
            rev_id=None,
        ),
    ]
    repo = Repo()

    update_commits_for_uplift(commits, {}, repo)

    reviewers = commits[0].reviewers

    assert not reviewers[
        "request"
    ], "Uplifted patch should have no requested reviewers initially."
    assert not reviewers[
        "granted"
    ], "Uplifted patch should have no granted reviewers initially."

    reviewers = commits[1].reviewers

    assert not reviewers[
        "request"
    ], "Uplifted patch should have no requested reviewers initially."
    assert not reviewers[
        "granted"
    ], "Uplifted patch should have no granted reviewers initially."


def test_update_commits_for_uplift_sets_original_revision():
    commits = [
        # Check initial submission.
        Commit(
            title="bug 1: firstline r?reviewer",
            reviewers={"granted": ["john"], "request": []},
            bug_id="1",
            body=(
                "bug 1: firstline r?reviewer\n"
                "\n"
                "Differential Revision: https://phabricator.services.mozila.com/D1\n"
            ),
            rev_id=1,
        ),
        # Check update of existing uplift revision.
        Commit(
            title="bug 1: firstline r?reviewer",
            reviewers={"granted": ["john"], "request": []},
            bug_id="1",
            body=(
                "bug 1: firstline r?reviewer\n"
                "\n"
                "Original Revision: https://phabricator.services.mozila.com/D1\n"
                "\n"
                "Differential Revision: https://phabricator.services.mozila.com/D2\n"
            ),
            rev_id=2,
        ),
        # Check another initial submission.
        Commit(
            title="bug 3: commit message",
            reviewers={"granted": [], "request": []},
            bug_id="3",
            body=(
                "bug 3: commit message\n"
                "\n"
                "Differential Revision: https://phabricator.services.mozila.com/D3\n"
            ),
            rev_id=3,
        ),
    ]
    revisions = {
        1: {"id": 1, "fields": {"repositoryPHID": "PHID-mc"}},
        2: {"id": 2, "fields": {"repositoryPHID": "PHID-beta"}},
        3: {"id": 3, "fields": {"repositoryPHID": "PHID-mc"}},
    }
    repo = Repo()

    update_commits_for_uplift(commits, revisions, repo)

    # Initial submission.
    body = commits[0].body
    rev_id = commits[0].rev_id

    assert "Differential Revision:" not in body
    assert "Original Revision:" in body
    assert rev_id is None

    # Update of existing uplift.
    body = commits[1].body
    rev_id = commits[1].rev_id

    assert "Differential Revision:" in body
    assert "Original Revision:" in body
    assert rev_id == 2

    # Another initial submission.
    body = commits[2].body
    rev_id = commits[2].rev_id

    assert "Differential Revision:" not in body
    assert ORIGINAL_DIFF_REV_RE.search(body).group("rev") == "3"
    assert rev_id is None


def test_uplift_beta_commit_to_esr():
    commit = Commit(
        title="bug 2: commit message r=john",
        reviewers={"granted": ["john"], "request": []},
        bug_id="2",
        body=(
            "bug 2: commit message r=john\n"
            "\n"
            "Original Revision: https://phabricator.services.mozila.com/D1\n"
            "\n"
            "Differential Revision: https://phabricator.services.mozila.com/D2\n"
        ),
        rev_id=2,
    )
    revisions = {2: {"id": 2, "fields": {"repositoryPHID": "PHID-beta"}}}
    repo = Repo(phid="PHID-esr")

    update_commits_for_uplift([commit], revisions, repo)

    reviewers = commit.reviewers
    body = commit.body
    rev_id = commit.rev_id

    assert not reviewers["request"]
    assert not reviewers["granted"]
    assert "Differential Revision:" not in body
    assert ORIGINAL_DIFF_REV_RE.search(body).group("rev") == "1"
    assert rev_id is None
