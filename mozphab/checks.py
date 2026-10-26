# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Checks run against the changes being submitted."""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from mozphab.diff import Diff

# Added lines opening or closing a conflict.
CONFLICT_MARKER_RE = re.compile(r"^\+(<{7,}|>{7,})( |$)", re.MULTILINE)


def has_conflict_markers(change: Diff.Change) -> bool:
    """Return `True` if `change` adds conflict markers."""
    return any(CONFLICT_MARKER_RE.search(hunk.corpus) for hunk in change.hunks)


def is_symlink(change: Diff.Change) -> bool:
    """Return `True` if `change` adds a symlink or turns a file into one."""
    return change.cur_mode == "120000"


@dataclass(frozen=True)
class DiffCheck:
    """Finds changes that shouldn't be submitted."""

    # The name to pass to `--bypass-check` to submit anyway.
    name: str
    description: str
    matches: Callable[[Diff.Change], bool]


DIFF_CHECKS = [
    DiffCheck("conflict-markers", "Conflict markers found in", has_conflict_markers),
    DiffCheck("symlinks", "Symlinks found in", is_symlink),
    DiffCheck(
        "try-task-config",
        "Try task config found in",
        lambda change: change.cur_path == "try_task_config.json",
    ),
    DiffCheck(
        "submodules",
        "Git submodules found in",
        lambda change: change.cur_path == ".gitmodules",
    ),
]


def failed_checks(
    named_diffs: Iterable[tuple[str, Diff]]
) -> dict[DiffCheck, list[str]]:
    """Run every check against every change, in a single pass.

    Takes `(name, diff)` pairs and returns the `"<name> <path>"` entries each
    check matched, keyed in `DIFF_CHECKS` order.
    """
    found: dict[DiffCheck, list[str]] = {check: [] for check in DIFF_CHECKS}
    for name, diff in named_diffs:
        for change in diff.changes.values():
            for check in DIFF_CHECKS:
                if check.matches(change):
                    found[check].append(f"{name} {change.cur_path}")
    return found


@dataclass(frozen=True)
class OverrideHint:
    """A hint Lando needs in the revision title to land changes to some paths."""

    pattern: re.Pattern[str]
    hint: str
    # Whether to offer to add the hint to the commit title.
    offer_to_add: bool = False

    def missing_paths(self, title: str, diff: Diff) -> list[str]:
        """Return the paths in `diff` needing the hint, unless `title` has it."""
        if self.hint in title:
            return []
        return [path for path in diff.changes if self.pattern.match(path)]


OVERRIDE_HINTS = [
    OverrideHint(
        re.compile(r"^\.github/workflows"), "DOT_GITHUB_OVERRIDE", offer_to_add=True
    ),
]
