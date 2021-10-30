# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from mozphab.commands.submit import update_commits_for_uplift


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

    assert (
        "#release-managers!" in reviewers["request"]
    ), "release-managers review group not present in reviewers"

    assert (
        len(reviewers["request"]) == 1
    ), "Non-release manager review requested in `request`"

    assert not reviewers["granted"], "Non-release manager review requested in `granted`"


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
