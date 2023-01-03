# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import re
import stat
import sys
import tempfile
from typing import (
    List,
    Optional,
    Tuple,
)
from itertools import zip_longest

from contextlib import contextmanager
from shutil import which

from mozphab import environment

from .logger import logger
from .simplecache import cache


# If a commit body matches **all** of these, reject it.  This is to avoid the
# necessity to merge arc-style fields across an existing commit description
# and what we need to set.
ARC_REJECT_RE_LIST = [
    re.compile(r"^Summary:", flags=re.MULTILINE),
    re.compile(r"^Reviewers:", flags=re.MULTILINE),
]


ARC_DIFF_REV_RE = re.compile(
    r"^\s*Differential Revision:\s*(?P<phab_url>https?://.+)/D(?P<rev>\d+)\s*$",
    flags=re.MULTILINE,
)
ORIGINAL_DIFF_REV_RE = re.compile(
    r"^\s*Original Revision:\s*(?P<phab_url>https?://.+)/D(?P<rev>\d+)\s*$",
    flags=re.MULTILINE,
)

# Bug and review regexs (from vct's commitparser)
BUG_ID_RE = re.compile(r"(?:(?:bug|b=)(?:\s*)(\d+)(?=\b))", flags=re.IGNORECASE)
LIST = r"[;,\/\\]\s*"
LIST_RE = re.compile(LIST)
IRC_NICK_CHARS = r"a-zA-Z0-9\-\_!"  # includes !, which is different from commitparser
IRC_NICK_CHARS_WITH_PERIOD = IRC_NICK_CHARS + r"."
IRC_NICK = r"#?[" + IRC_NICK_CHARS_WITH_PERIOD + r"]*[" + IRC_NICK_CHARS + r"]+"
REVIEWERS_RE = (
    r"([\s(.\[;,])(r%s)("
    + IRC_NICK
    + r"(?:"
    + LIST
    + r"(?![a-z0-9.\-]+[=?])"
    + IRC_NICK
    + r")*)?"
)
ALL_REVIEWERS_RE = re.compile(REVIEWERS_RE % r"[=?]")
REQUEST_REVIEWERS_RE = re.compile(REVIEWERS_RE % r"[?]")
GRANTED_REVIEWERS_RE = re.compile(REVIEWERS_RE % r"=")
R_SPECIFIER_RE = re.compile(r"\br[=?]")
BLOCKING_REVIEWERS_RE = re.compile(r"\b(r!)([" + IRC_NICK_CHARS_WITH_PERIOD + ",]+)")

DEPENDS_ON_RE = re.compile(r"^\s*Depends on\s*D(\d+)\s*$", flags=re.MULTILINE)

WIP_RE = re.compile(r"^(?:WIP[: ]|WIP$)", flags=re.IGNORECASE)

VALID_EMAIL_RE = re.compile(r"[^@ \t\r\n]+@[^@ \t\r\n]+\.[^@ \t\r\n]+")


def which_path(path):
    """Check if an executable is provided. Fall back to which if not.

    Args:
        path: (str) filename or path to check for an executable command

    Returns:
        The full path of a command or None.
    """
    if (
        os.path.exists(path)
        and os.access(path, os.F_OK | os.X_OK)
        and not os.path.isdir(path)
    ):
        logger.debug("Path found: %s", path)
        return path

    return which(path)


def parse_config(config_list, filter_func=None):
    """Parses list with "name=value" strings.

    Args:
        config_list: A list of "name=value" strings
        filter_func: A function taking the parsing config name and value for each line.
            If the function returns True the config value will be included in the final
            dict.

    Returns: A dict containing parsed data.
    """
    result = dict()
    for line in config_list:
        try:
            name, value = line.split("=", 1)
        except ValueError:
            continue

        name = name.strip()
        value = value.strip()
        if filter_func is None or (callable(filter_func) and filter_func(name, value)):
            result[name] = value

    return result


def read_json_field(files: List[str], field_path: List[str]) -> Optional[str]:
    """Parses json files in turn returning value as per field_path, or None."""
    for filename in files:
        try:
            with open(filename, encoding="utf-8") as f:
                rc = json.load(f)
                for field_name in field_path:
                    if field_name not in rc:
                        rc = None
                        break
                    rc = rc[field_name]
                if not rc:
                    continue
                return rc
        except FileNotFoundError:
            continue
        except ValueError:
            continue
    return None


@contextmanager
def temporary_file(content, encoding="utf-8"):
    f = tempfile.NamedTemporaryFile(delete=False, mode="w+", encoding=encoding)
    try:
        f.write(content)
        f.flush()
        f.close()
        yield f.name
    finally:
        os.remove(f.name)


@contextmanager
def temporary_binary_file(content):
    f = tempfile.NamedTemporaryFile(delete=False, mode="wb+")
    try:
        f.write(content)
        f.flush()
        f.close()
        yield f.name
    finally:
        os.remove(f.name)


def get_arcrc_path() -> str:
    """Return a path to the user's Arcanist configuration file."""
    if "arcrc" in cache:
        return str(cache.get("arcrc"))

    if environment.IS_WINDOWS:
        arcrc = os.path.join(os.getenv("APPDATA", ""), ".arcrc")
    else:
        arcrc = os.path.expanduser("~/.arcrc")
        if os.path.isfile(arcrc) and stat.S_IMODE(os.stat(arcrc).st_mode) != 0o600:
            logger.debug("Changed file permissions on the %s file.", arcrc)
            os.chmod(arcrc, 0o600)

    cache.set("arcrc", arcrc)
    return arcrc


def parse_arc_diff_rev(body):
    m = ARC_DIFF_REV_RE.search(body)
    return m.group("rev") if m else None


def strip_differential_revision(body):
    return ARC_DIFF_REV_RE.sub("", body).rstrip()


def move_drev_to_original(
    body: str, rev_id: Optional[int]
) -> Tuple[str, Optional[int]]:
    """Handle moving the `Differential Revision` line.

    Moves the `Differential Revision` line to `Original Revision`, if a link
    to the original revision does not already exist. If the `Original Revision`
    line does exist, scrub the `Differential Revision` line.

    Args:
        body: `str` text of the commit message.
        rev_id: `int` parsed integer representing the drev number for the revision.

    Returns:
        tuple of:
            New commit message body text as `str`,
            Revision id as `int`, or `None` if a new revision should be created.
    """
    # Previous logic did not find a revision id, so this function won't either.
    if not rev_id:
        return body, rev_id

    differential_revision = ARC_DIFF_REV_RE.search(body)
    original_revision = ORIGINAL_DIFF_REV_RE.search(body)

    # If both match, this is an update to an uplift.
    if differential_revision and original_revision:
        return body, rev_id

    def repl(match):
        phab_url = match.group("phab_url")
        rev = match.group("rev")
        return f"\nOriginal Revision: {phab_url}/D{rev}"

    # Update the commit message and set the `rev-id` to `None`.
    return ARC_DIFF_REV_RE.sub(repl, body), None


def parse_api_error(api_response):
    """Parse the string output from 'arc call-conduit' and return any errors found.

    Args:
        api_response: stdout string captured from 'arc call-conduit'.  It should
            contain only valid JSON.

    Returns:
        A string error description if an error occurred or None if no error occurred.
    """
    # Example error response from running
    # $ echo '{}' | arc call-conduit differential.revision.edit | jq .
    #
    # {
    #   "error": "ERR-CONDUIT-CORE",
    #   "errorMessage": "ERR-CONDUIT-CORE: Parameter \"transactions\" is not a list "\
    #       "of transactions.",
    #   "response": null
    # }
    response = json.loads(api_response)
    if response["error"] and response["errorMessage"]:
        return response["errorMessage"]


def prompt(question, options=None):
    if environment.HAS_ANSI:
        question = "\033[33m%s\033[0m" % question
    prompt_str = question
    if options:
        prompt_options = list(options)
        prompt_options[0] = prompt_options[0].upper()
        prompt_str = "%s (%s)? " % (question, "/".join(prompt_options))

        options_map = {o[0].lower(): o for o in options}
        options_map[""] = options[0]

    while True:
        res = input(prompt_str)

        if res == chr(27):  # escape
            sys.exit(1)

        if not options:
            return res

        if len(res) > 1:
            res = res[0].lower()

        if res in options_map:
            return options_map[res]


def has_arc_rejections(body):
    return all(r.search(body) for r in ARC_REJECT_RE_LIST)


def wip_in_commit_title(title):
    return WIP_RE.search(title) is not None


def augment_commits_from_body(commits):
    """Extract metadata from commit body as fields.

    Adds: rev-id, bug-id, reviewers, wip
    """
    for commit in commits:
        commit["rev-id"] = parse_arc_diff_rev(commit["body"])

        # bug-id
        bug_ids = parse_bugs(commit["title"])
        if bug_ids:
            if len(bug_ids) > 1:
                logger.warning("Multiple bug-IDs found, using %s", bug_ids[0])
            commit["bug-id"] = bug_ids[0]
        else:
            commit["bug-id"] = None
        if "bug-id-orig" not in commit:
            commit["bug-id-orig"] = commit["bug-id"]

        # reviewers
        commit["reviewers"] = parse_reviewers(commit["title"])

        # mark commit as WIP if commit desc starts with "WIP:"
        commit["wip"] = wip_in_commit_title(commit["title"])

    update_commit_title_previews(commits)


def parse_bugs(title):
    return BUG_ID_RE.findall(title)


def parse_reviewers(title):
    """Extract reviewers information from first line of the commit message.

    Returns a dictionary containing reviewers divided by the type:
        "r?" reviewers under the "request" key
        "r=" reviewers under the "granted" key
    """

    def extend_matches(match_re, matches):
        """Extends `matches` with any matches found using `match_re`.
        Args:
            match_re (str): a regular expression string to search with
            matches (list of str): a list of strings of reviewers captured
        Returns:
            dict (str, list of str): a dictionary of requested and granted reviewers
        """
        for match in re.finditer(match_re, title):
            if match.group(3):
                matches.extend(re.split(LIST_RE, match.group(3)))

    reviewers = {"request": [], "granted": []}
    extend_matches(REQUEST_REVIEWERS_RE, reviewers["request"])
    extend_matches(GRANTED_REVIEWERS_RE, reviewers["granted"])
    return reviewers


def strip_depends_on(body):
    return DEPENDS_ON_RE.sub("", body).rstrip()


def revision_title_from_commit(commit: dict) -> str:
    """Returns a string suitable for a Revision title for the given commit."""
    title = WIP_RE.sub("", commit["title-preview"]).lstrip()
    if commit["wip"]:
        title = "WIP: %s" % title
    return title


def update_commit_title_previews(commits):
    """Update title-preview from commit metadata for all commits in stack"""
    for commit in commits:
        commit["title-preview"] = build_commit_title(commit)


def build_commit_title(commit):
    """Build/update title from commit metadata"""
    # Reviewers.
    title = replace_reviewers(commit["title"], commit["reviewers"])

    # Bug-ID.
    if commit["bug-id"]:
        if BUG_ID_RE.search(title):
            title = BUG_ID_RE.sub("Bug %s" % commit["bug-id"], title, count=1)
        else:
            title = "Bug %s - %s" % (commit["bug-id"], title)
    else:
        # This is likely to result in unappealing results.
        title = BUG_ID_RE.sub("", title)

    return title


def replace_reviewers(commit_description, reviewers):
    """From vct's commitparser"""
    reviewers_lst = []
    if reviewers["request"]:
        reviewers_lst.append("r?" + ",".join(reviewers["request"]))

    if reviewers["granted"]:
        reviewers_lst.append("r=" + ",".join(reviewers["granted"]))

    reviewers_str = " ".join(reviewers_lst)

    if commit_description == "":
        return reviewers_str

    commit_description = commit_description.splitlines()
    commit_title = commit_description.pop(0)
    commit_description = "\n".join(commit_description)

    if not R_SPECIFIER_RE.search(commit_title):
        commit_title += " " + reviewers_str
    else:
        # replace the first r? with the reviewer list, and all subsequent
        # occurrences with a marker to mark the blocks we need to remove
        # later.
        d = {"first": True}

        def replace_first_reviewer(matchobj):
            if R_SPECIFIER_RE.match(matchobj.group(2)):
                if d["first"]:
                    d["first"] = False
                    return matchobj.group(1) + reviewers_str
                else:
                    return "\0"
            else:
                return matchobj.group(0)

        commit_title = re.sub(ALL_REVIEWERS_RE, replace_first_reviewer, commit_title)

        # remove marker values as well as leading separators.  this allows us
        # to remove runs of multiple reviewers and retain the trailing
        # separator.
        commit_title = re.sub(LIST + "\0", "", commit_title)
        commit_title = re.sub("\0", "", commit_title)

    if commit_description == "":
        return commit_title.strip()
    else:
        return commit_title.strip() + "\n" + commit_description


def prepare_body(title, summary, rev_id, phab_url, depends_on=None):
    """Prepare the body using title and summary."""
    summary = strip_differential_revision(summary)
    summary = strip_depends_on(summary)

    if summary:
        summary += "\n\n"

    summary += "Differential Revision: %s/D%s" % (phab_url, rev_id)
    if depends_on:
        summary += "\n\nDepends on D%s" % depends_on

    body = "%s\n\n%s" % (title, summary)

    return body


def short_node(text):
    """Return a shortened version of a node name."""
    if len(text) == 40:
        try:
            int(text, 16)
            return text[:12]
        except ValueError:
            pass
    return text


def create_hunk_lines(
    body: str, prefix: str, check_eof: bool = True
) -> Tuple[List[str], Optional[bool]]:
    """Parse a text body into a list of lines to be used in hunks.

    Args:
        body: A string containing the raw content of the file.
        prefix: A character (e.g. "+") indicating whether the lines are to be
            added or removed or are unchanged.
        check_eof: Whether to check or not to check the end of the file for the
            newline character. If set to True, the check is performed and if a
            newline character is missing, it will be added to the last entry in
            `lines` along with a message.

    Returns:
        A tuple containing a list of strings to be used when generating the corpus
            of the hunks, as well as boolean representing whether the file
            terminated with a new line or not if applicable, `None` if not.

    Note:
        When the body is missing a newline at the end, the appended lines will use a
        POSIX line terminator regardless of what line separators are already used in the
        body.
    """
    allowed_prefixes = ("+", "-", " ")
    if prefix not in allowed_prefixes:
        raise ValueError(f"Prefix should be one of {allowed_prefixes}")

    empty_file = body in ("", b"")

    if empty_file:
        # `body` has absolutely nothing in it, return values accordingly.
        if check_eof and prefix != "+":
            return ["\\ No newline at end of file\n"], True
        else:
            return [], None

    # Split lines on line separators
    lines = split_lines(body)
    eof_missing_newline = lines[-1] != ""

    # Re-add line separators for all lines except the last line.
    last_line = lines.pop()
    lines = join_lineseps(lines)
    lines = [f"{prefix}{line}" for line in lines]
    if eof_missing_newline:
        if check_eof:
            # Re-add the last line with the line separator, and another line
            # indicating that no new line was at the EOF.
            lines.append(f"{prefix}{last_line}\n")
            lines.append("\\ No newline at end of file\n")
        else:
            # Re-add the last line as is since we weren't supposed to touch it.
            lines.append(f"{prefix}{last_line}")

    return lines, eof_missing_newline if check_eof else None


def split_lines(body):
    """Split a string on line separators, and keep line endings.

    This method behaves the same as `str.splitlines(True)`, but only splits on POSIX
    and DOS style line endings.

    Args:
        body: An arbitrary string.

    Returns:
        A list of strings consisting of lines and line separators.

    Example:
        >>> test = "line1\nline2\r\nline3"
        >>> split_lines(test)
        >>> ["line1", "\n", "line2", "\r\n", "line3"]
    """
    binary = isinstance(body, bytes)
    pattern = b"(\n|\r\n)"
    if not binary:
        pattern = pattern.decode("utf-8")

    return re.split(pattern, body)


def join_lineseps(lines):
    """Given a list of strings, join every two entries together where possible.

    NOTE: If the list length is odd, then the last entry is joined with an empty string.

    Args:
        lines: A list of strings representing lines to join.

    Returns:
        A list of strings that contains a joined list of strings.

    Example:
        >>> test = ["line1", "\n", "line2", "\r\n", "line3"]
        >>> join_lineseps(test)
        >>> ["line1\n", "line2\r\n", "line3"]
    """
    return [
        "".join(line) for line in zip_longest(lines[0::2], lines[1::2], fillvalue="")
    ]


def is_valid_email(email: str) -> bool:
    """Given a string, determines if it is a valid email."""
    return VALID_EMAIL_RE.match(email) is not None
