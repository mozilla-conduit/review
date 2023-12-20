# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from dataclasses import (
    dataclass,
    field,
)
from typing import (
    Dict,
    List,
    Optional,
)


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
