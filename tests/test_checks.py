# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from mozphab import checks
from mozphab.diff import Diff


@pytest.mark.parametrize(
    "lines,expected",
    [
        (["+<<<<<<< HEAD\n", "+a\n"], True),
        (["+>>>>>>> Conflict 1 of 1 ends\n"], True),
        (["+<<<<<<<\n"], True),
        (["-<<<<<<< HEAD\n"], False),
        (["+=======\n"], False),
        ([" >>>>>>> other\n"], False),
        (["+<<<<<<<<\n"], True),
    ],
)
def test_has_conflict_markers(lines, expected):
    change = Diff.Change("X")
    change.hunks.append(
        Diff.Hunk(old_off=1, old_len=1, new_off=1, new_len=1, lines=lines)
    )
    assert checks.has_conflict_markers(change) == expected


@pytest.mark.parametrize(
    "kind,a_mode,b_mode,expected",
    [
        ("A", "000000", "120000", True),
        ("M", "100644", "120000", True),
        ("M", "120000", "120000", False),
        ("A", "000000", "100644", False),
        ("D", "120000", "000000", False),
    ],
)
def test_is_symlink(kind, a_mode, b_mode, expected):
    diff = Diff()
    change = diff.change_for("X")
    diff.set_change_kind(change, kind, a_mode, b_mode, "X", "X")
    assert checks.is_symlink(change) == expected


def test_failed_checks():
    first = Diff()
    first.change_for("X").hunks.append(
        Diff.Hunk(old_off=1, old_len=1, new_off=1, new_len=1, lines=["+<<<<<<<\n"])
    )
    first.change_for("link").cur_mode = "120000"
    first.change_for("fine")
    second = Diff()
    second.change_for("Y").hunks.append(
        Diff.Hunk(old_off=1, old_len=1, new_off=1, new_len=1, lines=["+>>>>>>>\n"])
    )

    found = checks.failed_checks([("aaa", first), ("bbb", second)])

    assert list(found) == checks.DIFF_CHECKS
    assert {check.name: paths for check, paths in found.items() if paths} == {
        "conflict-markers": ["aaa X", "bbb Y"],
        "symlinks": ["aaa link"],
    }
