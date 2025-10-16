# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import re
from dataclasses import (
    asdict,
    dataclass,
    field,
)
from typing import (
    Dict,
    List,
    Optional,
)

from mozphab.logger import logger

ARC_COMMIT_DESC_TEMPLATE = """
{title}

Summary:
{body}

Test Plan:

Reviewers: {reviewers}

Subscribers:

Bug #: {bug_id}
""".strip()

WIP_RE = re.compile(r"^(?:WIP[: ]|WIP$)", flags=re.IGNORECASE)


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
    author_date_epoch: int = 0
    author_name: str = ""
    author_email: str = ""

    author_date: Optional[str] = None
    parent: Optional[str] = None
    bug_id: Optional[str] = None
    bug_id_orig: Optional[str] = None
    rev_id: Optional[int] = None
    rev_phid: Optional[str] = None
    wip: Optional[bool] = None
    tree_hash: Optional[str] = None
    reviewers: Dict[str, List[str]] = field(default_factory=dict)

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
        return f"{self.title}\n\n{self.body}"

    def to_dict(self) -> dict:
        """Convert the `Commit` to a `dict`."""
        return asdict(self)
