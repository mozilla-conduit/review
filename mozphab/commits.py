# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import re
from dataclasses import (
    asdict,
    dataclass,
    field,
    fields,
)
from enum import Enum

from mozphab.logger import logger
from mozphab.models import CreateRawDiffResult

ARC_COMMIT_DESC_TEMPLATE = """
{title}

Summary:

{body}

Test Plan:
{test_plan}

Reviewers: {reviewers}

Subscribers:

Bug #: {bug_id}
""".strip()

WIP_RE = re.compile(r"^(?:WIP[: ]|WIP$)", flags=re.IGNORECASE)


# Subclassing `str` lets `json.dump` write a state as its value, eg. in `to_dict`.
class AiReviewState(str, Enum):
    NOT_REQUESTED = "not requested"
    REQUESTED = "requested"
    FAILED = "failed"


# The `metadata` of a `Commit` field that `Commit.to_dict` leaves out.
NOT_IN_DICT = {"in_dict": False}


@dataclass
class MergeConflictDiffs:
    """The diffs showing an uplift's merge conflict on a revision, then its resolution."""

    # The diff showing the merge conflict, uploaded before the resolution.
    conflict: CreateRawDiffResult

    # The ID of the diff resolving the conflict, once it's attached.
    resolution_id: int | None = None


@dataclass
class Commit:
    """`moz-phab`'s representation of a commit."""

    name: str = ""
    node: str = ""
    orig_node: str = ""
    submit: bool = False
    title: str = ""
    title_preview: str = ""
    body: str = ""
    test_plan: str = ""
    author_date_epoch: int = 0
    author_name: str = ""
    author_email: str = ""

    # Set by the backends that can provide them; empty otherwise.
    author_date: str = ""
    parent: str = ""
    tree_hash: str = ""
    bug_id: str | None = None
    bug_id_orig: str | None = None
    rev_id: int | None = None
    rev_phid: str | None = None
    wip: bool | None = None
    ai_review_state: AiReviewState = AiReviewState.NOT_REQUESTED
    reviewers: dict[str, list[str]] = field(default_factory=dict)

    # The diffs showing the merge conflict an uplift of this commit hit, if any.
    # `uplift` reports them once in its own output, so they aren't in `to_dict`.
    merge_conflict: MergeConflictDiffs | None = field(
        default=None, metadata=NOT_IN_DICT
    )

    @property
    def has_reviewers(self) -> bool:
        """Return `True` if the commit has reviewers."""
        if not self.reviewers:
            return False

        return bool(self.reviewers.get("granted") or self.reviewers.get("request"))

    def build_arc_commit_message(self) -> str:
        """Contruct a commit message for this `Commit` object."""
        # WIP submissions shouldn't set reviewers.
        if self.wip:
            reviewers = ""
        else:
            reviewers = ", ".join(self.reviewers["granted"] + self.reviewers["request"])

        # Create arc-annotated commit description.
        template_vars = {
            "title": self.revision_title(),
            "body": self.body,
            "test_plan": self.test_plan,
            "reviewers": reviewers,
            # Change `None` to an empty string.
            "bug_id": self.bug_id or "",
        }

        message = ARC_COMMIT_DESC_TEMPLATE.format(**template_vars)
        logger.debug("--- arc message\n%s\n---" % message)
        return message

    def revision_title(self) -> str:
        """Returns a string suitable for a Revision title for the given commit."""
        title = WIP_RE.sub("", self.title_preview).lstrip()
        if self.wip:
            title = f"WIP: {title}"
        return title

    def wip_in_commit_title(self) -> bool:
        """Return `True` if the commit title indicates the revision is a WIP."""
        return WIP_RE.search(self.title) is not None

    @property
    def message(self) -> str:
        """Return the full commit message: the title and body separated by a blank line."""
        if not self.body:
            return self.title
        return f"{self.title}\n\n{self.body}"

    def to_dict(self) -> dict:
        """Convert the `Commit` to a `dict`, leaving out fields marked `NOT_IN_DICT`."""
        left_out = {
            commit_field.name
            for commit_field in fields(self)
            if not commit_field.metadata.get("in_dict", True)
        }
        return asdict(
            self,
            dict_factory=lambda items: {
                name: value for name, value in items if name not in left_out
            },
        )
