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
    r"^\s*Differential Revision:\s*https?://.+/D(\d+)\s*$", flags=re.MULTILINE
)

# Bug and review regexs (from vct's commitparser)
BUG_ID_RE = re.compile(r"(?:(?:bug|b=)(?:\s*)(\d+)(?=\b))", flags=re.IGNORECASE)
LIST = r"[;,\/\\]\s*"
LIST_RE = re.compile(LIST)
IRC_NICK_CHARS = r"a-zA-Z0-9\-\_!"  # includes !, which is different from commitparser
IRC_NICK = r"#?[" + IRC_NICK_CHARS + "]+"
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
BLOCKING_REVIEWERS_RE = re.compile(r"\b(r!)([" + IRC_NICK_CHARS + ",]+)")

DEPENDS_ON_RE = re.compile(r"^\s*Depends on\s*D(\d+)\s*$", flags=re.MULTILINE)


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


def read_json_field(files, field_path):
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


def get_arcrc_path():
    """Return a path to the user's Arcanist configuration file."""
    if "arcrc" in cache:
        return cache.get("arcrc")

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
    return m.group(1) if m else None


def strip_differential_revision(body):
    return ARC_DIFF_REV_RE.sub("", body).rstrip()


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


def augment_commits_from_body(commits):
    """Extract metadata from commit body as fields.

    Adds: rev-id, bug-id, reviewers
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

    update_commit_title_previews(commits)


def parse_bugs(title):
    return BUG_ID_RE.findall(title)


def parse_reviewers(title):
    """Extract reviewers information from first line of the commit message.

    Returns a dictionary containing reviewers divided by the type:
        "r?" reviewers under the "request" key
        "r=" reviewers under the "granted" key
    """
    request_reviewers = []
    for match in re.finditer(REQUEST_REVIEWERS_RE, title):
        if not match.group(3):
            continue
        request_reviewers.extend(re.split(LIST_RE, match.group(3)))
    granted_reviewers = []
    for match in re.finditer(GRANTED_REVIEWERS_RE, title):
        if not match.group(3):
            continue
        granted_reviewers.extend(re.split(LIST_RE, match.group(3)))
    return dict(request=request_reviewers, granted=granted_reviewers)


def strip_depends_on(body):
    return DEPENDS_ON_RE.sub("", body).rstrip()


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
