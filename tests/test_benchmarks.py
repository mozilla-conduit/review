# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Performance benchmarks for mozphab core functions."""

import textwrap

import pytest

from mozphab import helpers
from mozphab.commits import Commit
from mozphab.diff import Diff

# --- helpers.parse_bugs ---


@pytest.mark.benchmark
def test_bench_parse_bugs_single():
    helpers.parse_bugs("Bug 1094764 - Implement AudioContext.suspend and friends")


@pytest.mark.benchmark
def test_bench_parse_bugs_multiple():
    helpers.parse_bugs("Bug 1 and bug 2 and also bug 345678")


@pytest.mark.benchmark
def test_bench_parse_bugs_no_match():
    helpers.parse_bugs("No bug reference here, just a normal commit message")


# --- helpers.parse_reviewers ---


@pytest.mark.benchmark
def test_bench_parse_reviewers_granted():
    helpers.parse_reviewers(
        "Bug 1094764 - Implement AudioContext.suspend and friends. r=roc,ehsan"
    )


@pytest.mark.benchmark
def test_bench_parse_reviewers_request():
    helpers.parse_reviewers("Bug 1094764 - Implement AudioContext. r?romulus,remus")


@pytest.mark.benchmark
def test_bench_parse_reviewers_mixed():
    helpers.parse_reviewers("Bug 1094764 - Big change; r?romulus,gps r=remus,ehsan")


# --- helpers.replace_reviewers ---


@pytest.mark.benchmark
def test_bench_replace_reviewers():
    helpers.replace_reviewers(
        "Bug 1094764 - Implement AudioContext.suspend r=old_reviewer",
        {"request": ["romulus"], "granted": ["remus", "gps"]},
    )


# --- helpers.split_lines ---


_SMALL_BODY = "line1\nline2\nline3\n"
_MEDIUM_BODY = "\n".join(f"line {i}: some content here" for i in range(100)) + "\n"
_MIXED_BODY = "line1\r\nline2\nline3\r\nline4\n"


@pytest.mark.benchmark
def test_bench_split_lines_small():
    helpers.split_lines(_SMALL_BODY)


@pytest.mark.benchmark
def test_bench_split_lines_medium():
    helpers.split_lines(_MEDIUM_BODY)


@pytest.mark.benchmark
def test_bench_split_lines_mixed_endings():
    helpers.split_lines(_MIXED_BODY)


# --- helpers.join_lineseps ---


_SPLIT_LINES = ["line1", "\n", "line2", "\n", "line3", "\n", ""]


@pytest.mark.benchmark
def test_bench_join_lineseps():
    helpers.join_lineseps(_SPLIT_LINES)


_LARGE_SPLIT = []
for i in range(100):
    _LARGE_SPLIT.extend([f"line {i}: content", "\n"])
_LARGE_SPLIT.append("")


@pytest.mark.benchmark
def test_bench_join_lineseps_large():
    helpers.join_lineseps(_LARGE_SPLIT)


# --- helpers.create_hunk_lines ---


@pytest.mark.benchmark
def test_bench_create_hunk_lines_small():
    helpers.create_hunk_lines("hello\nworld\n", "+", check_eof=True)


@pytest.mark.benchmark
def test_bench_create_hunk_lines_medium():
    body = "\n".join(f"line {i}" for i in range(50)) + "\n"
    helpers.create_hunk_lines(body, "+", check_eof=True)


@pytest.mark.benchmark
def test_bench_create_hunk_lines_no_eof():
    helpers.create_hunk_lines("hello\nworld", "-", check_eof=True)


# --- helpers.augment_commits_from_body ---


def _make_commits():
    return [
        Commit(
            title="Bug 1 - test r?reviewer",
            body="Differential Revision: https://example.com/D101",
        ),
        Commit(
            title="WIP: Bug 2 - blah r=blocker!",
            body="Differential Revision: https://example.com/D102",
        ),
        Commit(
            title="Bug 345678 - Large feature r?alice,bob,charlie",
            body="Summary\n\nDifferential Revision: https://example.com/D103",
        ),
    ]


@pytest.mark.benchmark
def test_bench_augment_commits_from_body():
    commits = _make_commits()
    helpers.augment_commits_from_body(commits)


@pytest.mark.benchmark
def test_bench_update_commit_title_previews():
    commits = _make_commits()
    helpers.augment_commits_from_body(commits)
    helpers.update_commit_title_previews(commits)


# --- helpers.prepare_body ---


@pytest.mark.benchmark
def test_bench_prepare_body():
    helpers.prepare_body(
        title="Bug 1094764 - Implement AudioContext.suspend and friends",
        summary="This patch implements the suspend method.\n\n"
        "Depends on D100\n\n"
        "Differential Revision: https://example.com/D200",
        rev_id=201,
        phab_url="https://phabricator.services.mozilla.com",
    )


# --- helpers.short_node ---


@pytest.mark.benchmark
def test_bench_short_node_sha():
    helpers.short_node("b016b6080ff9fa6d9ac459950e24bdcdaa939be0")


@pytest.mark.benchmark
def test_bench_short_node_name():
    helpers.short_node("mozilla-central")


# --- helpers.parse_arc_diff_rev ---


@pytest.mark.benchmark
def test_bench_parse_arc_diff_rev():
    helpers.parse_arc_diff_rev(
        "Summary of changes\n\n"
        "Differential Revision: https://phabricator.services.mozilla.com/D12345"
    )


# --- helpers.strip_differential_revision ---


@pytest.mark.benchmark
def test_bench_strip_differential_revision():
    helpers.strip_differential_revision(
        "title\n\nsummary\n\n"
        "Differential Revision: https://phabricator.services.mozilla.com/D123"
    )


# --- helpers.strip_depends_on ---


@pytest.mark.benchmark
def test_bench_strip_depends_on():
    helpers.strip_depends_on("title\n\nsummary\n\nDepends on D100\n\nDepends on D200")


# --- Diff.parse_git_diff ---


@pytest.mark.benchmark
def test_bench_parse_git_diff_header():
    Diff.parse_git_diff("@@ -23,6 +23,7 @@ jobs:")


@pytest.mark.benchmark
def test_bench_parse_git_diff_no_len():
    Diff.parse_git_diff("@@ -1 +1,2 @@")


# --- Diff.Hunk ---


_HUNK_LINES = [" context\n", "+added\n", "-removed\n", " context\n"]


@pytest.mark.benchmark
def test_bench_hunk_creation():
    Diff.Hunk(old_off=1, old_len=3, new_off=1, new_len=3, lines=_HUNK_LINES)


_LARGE_HUNK_LINES = []
for _i in range(200):
    _LARGE_HUNK_LINES.append(f" line {_i}\n")
    if _i % 5 == 0:
        _LARGE_HUNK_LINES.append(f"+added {_i}\n")
    if _i % 7 == 0:
        _LARGE_HUNK_LINES.append(f"-removed {_i}\n")


@pytest.mark.benchmark
def test_bench_hunk_creation_large():
    Diff.Hunk(old_off=1, old_len=200, new_off=1, new_len=220, lines=_LARGE_HUNK_LINES)


# --- Diff.Change.from_git_diff ---

_GIT_DIFF_MULTI_HUNK = textwrap.dedent("""\
    diff --git a/fn b/fn
    --- a/fn
    +++ b/fn
    @@ -4,3 +4,2 @@ c
     d
    -e
     f
    @@ -11,3 +10,2 @@ j
     k
    -l
     m
    @@ -25,2 +21,1 @@ x
     y
    -z
    """)


@pytest.mark.benchmark
def test_bench_change_from_git_diff():
    change = Diff.Change("test_file")
    change.from_git_diff(_GIT_DIFF_MULTI_HUNK)


# --- Commit operations ---


@pytest.mark.benchmark
def test_bench_commit_wip_check():
    commit = Commit(title="WIP: Bug 123 - Work in progress")
    commit.wip_in_commit_title()


@pytest.mark.benchmark
def test_bench_commit_to_dict():
    commit = Commit(
        name="abc123",
        node="b016b6080ff9fa6d9ac459950e24bdcdaa939be0",
        title="Bug 1 - test r?reviewer",
        body="Summary\n\nDifferential Revision: https://example.com/D101",
        bug_id="1",
        rev_id=101,
        reviewers={"request": ["reviewer"], "granted": []},
    )
    commit.to_dict()


@pytest.mark.benchmark
def test_bench_commit_build_arc_message():
    commit = Commit(
        title="Bug 1094764 - Implement AudioContext.suspend",
        title_preview="Bug 1094764 - Implement AudioContext.suspend r=roc,ehsan",
        body="This implements the suspend method.",
        bug_id="1094764",
        reviewers={"request": [], "granted": ["roc", "ehsan"]},
    )
    commit.build_arc_commit_message()


# --- helpers.parse_config ---


@pytest.mark.benchmark
def test_bench_parse_config():
    helpers.parse_config(
        [
            "key=value 1",
            "key2 = value2 ",
            "key3=",
            "key4=one=two=three",
            "no_equals_here",
            "remote.origin.url=https://github.com/example/repo.git",
            "branch.main.remote=origin",
            "branch.main.merge=refs/heads/main",
        ]
    )


# --- helpers.is_valid_email ---


@pytest.mark.benchmark
def test_bench_is_valid_email():
    helpers.is_valid_email("developer@mozilla.com")
    helpers.is_valid_email("Test User")
    helpers.is_valid_email("complex+tag@sub.domain.org")
