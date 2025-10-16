# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json

from mozphab.commits import Commit


def test_commit_to_dict_json_roundtrip():
    commit = Commit(
        name="commit-1",
        node="abcdef123456",
        orig_node="fedcba654321",
        submit=True,
        title="Add feature X",
        title_preview="Add feature X (preview)",
        body="Implements feature X and adds tests.",
        author_date_epoch=1727200000,
        author_name="Author Name",
        author_email="author@example.com",
        author_date="2024-09-25 12:34:56",
        parent="123456abcdef",
        bug_id="190001",
        bug_id_orig="190001",
        rev_id=12345,
        rev_phid="PHID-DREV-xyz",
        wip=False,
        tree_hash="deadbeefcafebabe",
        reviewers={"granted": ["r1", "r2"], "request": ["r3"]},
    )

    # Convert to dict, then JSON, then back to dict.
    commit_dict = commit.to_dict()
    json_blob = json.dumps(commit_dict)  # should not raise
    commit_dict_loaded = json.loads(json_blob)

    expected = {
        "name": "commit-1",
        "node": "abcdef123456",
        "orig_node": "fedcba654321",
        "submit": True,
        "title": "Add feature X",
        "title_preview": "Add feature X (preview)",
        "body": "Implements feature X and adds tests.",
        "author_date_epoch": 1727200000,
        "author_name": "Author Name",
        "author_email": "author@example.com",
        "author_date": "2024-09-25 12:34:56",
        "parent": "123456abcdef",
        "bug_id": "190001",
        "bug_id_orig": "190001",
        "rev_id": 12345,
        "rev_phid": "PHID-DREV-xyz",
        "wip": False,
        "tree_hash": "deadbeefcafebabe",
        "reviewers": {"granted": ["r1", "r2"], "request": ["r3"]},
    }

    assert commit_dict == expected
    assert commit_dict_loaded == expected

    # Check the `reviewers` key matches expected values.
    assert isinstance(commit_dict["reviewers"], dict)
    assert commit_dict["reviewers"]["granted"] == ["r1", "r2"]
    assert commit_dict["reviewers"]["request"] == ["r3"]


def test_commit_to_dict_defaults_are_serializable():
    # Create a commit with default values.
    commit = Commit()

    # Converting to `dict` and dumping to string should not raise.
    commit_dict = commit.to_dict()
    json.dumps(commit_dict)

    # Check a couple of defaults explicitly
    assert commit_dict["name"] == ""
    assert commit_dict["reviewers"] == {}
    assert commit_dict["rev_id"] is None
    assert commit_dict["submit"] is False
