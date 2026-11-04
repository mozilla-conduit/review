# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import dataclasses

import pytest

from mozphab.models import Revision, revision_monogram

from .conftest import search_rev


def test_revision_from_conduit():
    data = search_rev(
        rev=12,
        phid="PHID-DREV-12",
        bug="34",
        status="abandoned",
        closed=True,
        reviewers=["PHID-USER-2"],
    )
    data["fields"]["summary"] = "Summary"
    data["fields"]["stackGraph"] = {"PHID-DREV-12": ["PHID-DREV-11"]}

    revision = Revision.from_conduit(data)

    assert revision == Revision(
        id=12,
        phid="PHID-DREV-12",
        title="Title",
        summary="Summary",
        status="abandoned",
        is_closed=True,
        author_phid="PHID-USER-1",
        diff_phid="PHID-DIFF-1",
        repository_phid="PHID-REPO-1",
        bug_id="34",
        stack_graph={"PHID-DREV-12": ("PHID-DREV-11",)},
        reviewer_phids=("PHID-USER-2",),
    ), "Every field should be parsed from the search result."
    assert revision.is_abandoned, "An `abandoned` revision should be abandoned."


def test_revision_from_conduit_without_optional_fields():
    revision = Revision.from_conduit({"id": "3", "phid": "PHID-DREV-3", "fields": {}})

    assert revision == Revision(
        id=3, phid="PHID-DREV-3"
    ), "Fields missing from the search result should get their defaults."


def test_revision_is_frozen():
    revision = Revision(id=1, phid="PHID-DREV-1")

    with pytest.raises(dataclasses.FrozenInstanceError):
        revision.status = "abandoned"  # type: ignore[misc]


def test_revision_monogram():
    assert (
        revision_monogram(123) == "D123"
    ), "The monogram should be the revision ID prefixed with `D`."
    assert (
        Revision(id=123, phid="PHID-DREV-123").monogram == "D123"
    ), "A revision's monogram should be built from its ID."
