# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from mozphab.commands.submit import (
    update_commits_for_uplift,
    local_uplift_if_possible,
)


def test_local_uplift_if_possible():
    class Args:
        def __init__(self, no_rebase=False, train="train"):
            self.no_rebase = no_rebase
            self.train = train

    class Repo:
        def __init__(self, unified_head="beta", is_descendant=True):
            self.unified_head = unified_head
            self._is_descendant = is_descendant
            self.uplift_called = False

        def map_callsign_to_unified_head(self, *args, **kwargs):
            return self.unified_head

        def is_descendant(self, *args, **kwargs):
            return self._is_descendant

        def uplift_commits(self, *args, **kwargs):
            self.uplift_called = True

    commits = [
        {
            "title": "A",
            "reviewers": dict(granted=["john"], request=[]),
            "bug-id": None,
            "body": "",
            "rev-id": 1,
        },
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
        {
            "title": "A",
            "reviewers": dict(granted=["john"], request=[]),
            "bug-id": None,
            "body": "",
            "rev-id": 1,
        },
    ]

    update_commits_for_uplift(commits)

    reviewers = commits[0]["reviewers"]

    assert not reviewers[
        "request"
    ], "Uplifted patch should have no requested reviewers initially."

    assert not reviewers[
        "granted"
    ], "Uplifted patch should have no granted reviewers initially."


def test_update_commits_for_uplift_sets_original_revision():
    commits = [
        # Check initial submission.
        {
            "title": "bug 1: firstline r?reviewer",
            "reviewers": dict(granted=["john"], request=[]),
            "bug-id": 1,
            "body": (
                "bug 1: firstline r?reviewer\n"
                "\n"
                "Differential Revision: https://phabricator.services.mozila.com/D1\n"
            ),
            "rev-id": 1,
        },
        # Check update of existing uplift revision.
        {
            "title": "bug 1: firstline r?reviewer",
            "reviewers": dict(granted=["john"], request=[]),
            "bug-id": 1,
            "body": (
                "bug 1: firstline r?reviewer\n"
                "\n"
                "Original Revision: https://phabricator.services.mozila.com/D1\n"
                "\n"
                "Differential Revision: https://phabricator.services.mozila.com/D2\n"
            ),
            "rev-id": 2,
        },
    ]

    update_commits_for_uplift(commits)

    # Initial submission.
    body = commits[0]["body"]
    rev_id = commits[0]["rev-id"]

    assert "Differential Revision:" not in body
    assert "Original Revision:" in body
    assert rev_id is None

    # Update of existing uplift.
    body = commits[1]["body"]
    rev_id = commits[1]["rev-id"]

    assert "Differential Revision:" in body
    assert "Original Revision:" in body
    assert rev_id == 2
