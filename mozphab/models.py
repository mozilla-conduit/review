# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Typed, immutable views of the data returned by the Phabricator Conduit API."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType


class RevisionStatus(str, Enum):
    """The statuses of a Phabricator revision."""

    NEEDS_REVIEW = "needs-review"
    NEEDS_REVISION = "needs-revision"
    CHANGES_PLANNED = "changes-planned"
    ACCEPTED = "accepted"
    PUBLISHED = "published"
    ABANDONED = "abandoned"
    DRAFT = "draft"

    @property
    def label(self) -> str:
        """Return the status as Phabricator shows it, eg. `Needs Review`."""
        return self.value.replace("-", " ").title()


@dataclass(frozen=True)
class Revision:
    """A revision, parsed from a `differential.revision.search` result."""

    id: int
    phid: str
    title: str = ""
    summary: str = ""
    status: RevisionStatus | None = None
    is_closed: bool = False
    is_draft: bool = False
    author_phid: str = ""
    diff_phid: str = ""
    repository_phid: str | None = None
    bug_id: str | None = None
    test_plan: str = ""
    # Each revision in the stack's PHID, mapped to the PHIDs of its parents.
    stack_graph: Mapping[str, Sequence[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    reviewer_phids: tuple[str, ...] = ()

    @property
    def is_abandoned(self) -> bool:
        """Return `True` if the revision has been abandoned."""
        return self.status == RevisionStatus.ABANDONED

    @property
    def monogram(self) -> str:
        """Return the revision's monogram, eg. `D123`."""
        return revision_monogram(self.id)

    @classmethod
    def from_conduit(cls, data: dict) -> "Revision":
        """Parse a revision from its `differential.revision.search` result.

        Only the ID and PHID are required, as Phabricator instances differ in the
        fields they return, eg. Mozilla's `bugzilla.bug-id`.
        """
        fields = data.get("fields", {})
        status = fields.get("status", {})
        reviewers = (
            data.get("attachments", {}).get("reviewers", {}).get("reviewers", [])
        )
        return cls(
            id=int(data["id"]),
            phid=data["phid"],
            title=fields.get("title", ""),
            summary=fields.get("summary", ""),
            status=RevisionStatus(status["value"]) if "value" in status else None,
            is_closed=bool(status.get("closed", False)),
            is_draft=bool(fields.get("isDraft", False)),
            author_phid=fields.get("authorPHID", ""),
            diff_phid=fields.get("diffPHID", ""),
            repository_phid=fields.get("repositoryPHID"),
            bug_id=fields.get("bugzilla.bug-id") or None,
            test_plan=fields.get("testPlan", ""),
            stack_graph=MappingProxyType(
                {
                    phid: tuple(parents)
                    for phid, parents in fields.get("stackGraph", {}).items()
                }
            ),
            reviewer_phids=tuple(reviewer["reviewerPHID"] for reviewer in reviewers),
        )


def revision_monogram(revision_id: int) -> str:
    """Return the monogram Phabricator shows for the revision `revision_id`, eg. `D123`.

    Use this when only the ID is at hand; otherwise use `Revision.monogram`.
    """
    return f"D{revision_id}"


@dataclass(frozen=True)
class DiffCommit:
    """A local commit recorded on a diff, from the diff's `commits` attachment."""

    identifier: str = ""
    message: str = ""
    author_name: str | None = None
    author_email: str | None = None

    @classmethod
    def from_conduit(cls, data: dict) -> "DiffCommit":
        """Parse a commit from a diff's `commits` attachment."""
        author = data.get("author", {})
        return cls(
            identifier=data.get("identifier", ""),
            message=data.get("message", ""),
            author_name=author.get("name"),
            author_email=author.get("email"),
        )


@dataclass(frozen=True)
class RemoteDiff:
    """A diff on Phabricator, parsed from a `differential.diff.search` result.

    Named to tell it apart from `mozphab.diff.Diff`, the local diff to submit.
    """

    id: int
    phid: str
    revision_phid: str | None = None
    # The node the diff was based on, if the diff recorded one.
    base_ref: str | None = None
    # Unlike `dateModified`, this doesn't change for comments or reviewer edits.
    date_created: int | None = None
    commits: tuple[DiffCommit, ...] = ()

    @property
    def commit_title(self) -> str:
        """Return the first line of the recorded commit message, if any."""
        if not self.commits or not self.commits[0].message:
            return ""
        return self.commits[0].message.splitlines()[0]

    @classmethod
    def from_conduit(cls, data: dict) -> "RemoteDiff":
        """Parse a diff from its `differential.diff.search` result."""
        fields = data.get("fields", {})
        commits = data.get("attachments", {}).get("commits", {}).get("commits", [])
        base_refs = [
            ref["identifier"] for ref in fields.get("refs", []) if ref["type"] == "base"
        ]
        return cls(
            id=int(data["id"]),
            phid=data["phid"],
            revision_phid=fields.get("revisionPHID"),
            base_ref=base_refs[0] if base_refs else None,
            date_created=fields.get("dateCreated"),
            commits=tuple(DiffCommit.from_conduit(commit) for commit in commits),
        )
