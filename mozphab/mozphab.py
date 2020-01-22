#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# coding=utf-8

"""
CLI to support submission of a series of commits to Phabricator. .
"""

import argparse
import base64
import calendar
import configparser
import datetime
import io
import json
import logging
import logging.handlers
import mimetypes
import operator
import os
import re
import signal
import subprocess
import stat
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import urllib.error
import urllib.parse
import uuid
import __main__ as script_module
from collections import namedtuple
from contextlib import contextmanager, suppress
from distutils.dist import Distribution
from distutils.version import LooseVersion
from glob import glob
from pathlib import Path
from pkg_resources import get_distribution, parse_version
from shlex import quote
from shutil import which
from http.client import HTTPConnection, HTTPSConnection

from .exceptions import (
    CommandError,
    ConduitAPIError,
    Error,
    NonLinearException,
    NotFoundError,
)

from .reorganise import stack_transactions, walk_llist


# Known Issues
# - commits with a description already modified by arc (ie. the follow the arc commit
#   description template with 'test plan', subscribers, etc) are not handled by this
#   script.  commits in this format should be detected and result in the commit being
#   rejected.  ideally this should extract the title, body, reviewers, and bug-id
#   from the arc template and reformat to the standard mozilla format.


# Environment Vars

DEBUG = bool(os.getenv("DEBUG"))
HTTP_ALLOWED = bool(os.getenv("HTTP_ALLOWED"))
IS_WINDOWS = sys.platform == "win32"
HAS_ANSI = (
    not IS_WINDOWS
    and not os.getenv("NO_ANSI")
    and (
        (hasattr(sys.stdout, "isatty") and sys.stdout.isatty())
        or os.getenv("TERM", "") == "ANSI"
        or os.getenv("PYCHARM_HOSTED", "") == "1"
    )
)
SELF_FILE = os.getenv("UPDATE_FILE") if os.getenv("UPDATE_FILE") else __file__
# Switched off temporarily due to https://bugzilla.mozilla.org/show_bug.cgi?id=1565502
SHOW_SPINNER = False

# Constants and Globals

logger = logging.getLogger("moz-phab")
config = None
conduit = None

# Where to direct people when `arc` isn't installed.
GUIDE_URL = (
    "https://moz-conduit.readthedocs.io/en/latest/phabricator-user.html#quick-start"
)

GIT_COMMAND = ["git.exe" if IS_WINDOWS else "git"]
HOME_DIR = os.path.expanduser("~")

# ~/.mozbuild/moz-phab
MOZBUILD_PATH = os.path.join(
    os.environ.get("MOZBUILD_STATE_PATH", os.path.join(HOME_DIR, ".mozbuild")),
    "moz-phab",
)
LOG_FILE = os.path.join(MOZBUILD_PATH, "moz-phab.log")
LOG_MAX_SIZE = 1024 * 1024 * 50
LOG_BACKUPS = 5

# Arcanist
LIBPHUTIL_PATH = os.path.join(MOZBUILD_PATH, "libphutil")
ARC_PATH = os.path.join(MOZBUILD_PATH, "arcanist")
ARC_COMMAND = os.path.join(ARC_PATH, "bin", "arc.bat" if IS_WINDOWS else "arc")
ARC = [ARC_COMMAND]
INSTALL_CERT_MSG = (
    "You don't have credentials needed to access Phabricator.\n"
    "Please run the following command to configure moz-phab:\n\n"
    "   moz-phab install-certificate\n "
)
ARC_CONDUIT_ERROR = (
    {"matching": "install-certificate", "message": INSTALL_CERT_MSG},
    {"matching": "ERR-INVALID_AUTH", "message": "Server rejected your token."},
)
LIBPHUTIL_URL = "https://github.com/phacility/libphutil.git"
ARC_URL = "https://github.com/mozilla-conduit/arcanist.git"

# Auto-update
SELF_REPO = "mozilla-conduit/review"
SELF_UPDATE_FREQUENCY = 24 * 3  # hours
ARC_UPDATE_FREQUENCY = 24 * 7  # hours

# Environment names (display purposes only)
PHABRICATOR_URLS = {
    "https://phabricator.services.mozilla.com/": "Phabricator",
    "https://phabricator-dev.allizom.org/": "Phabricator-Dev",
}

DEFAULT_UPDATE_MESSAGE = "Revision updated."

# arc related consts.
ARC_COMMIT_DESC_TEMPLATE = """
{title}

Summary:
{body}

{depends_on}

Test Plan:

Reviewers: {reviewers}

Subscribers:

Bug #: {bug_id}
""".strip()
ARC_OUTPUT_REV_URL_RE = re.compile(r"^\s*Revision URI: (http.+)$", flags=re.MULTILINE)
ARC_DIFF_REV_RE = re.compile(
    r"^\s*Differential Revision:\s*https?://.+/D(\d+)\s*$", flags=re.MULTILINE
)

# If a commit body matches **all** of these, reject it.  This is to avoid the
# necessity to merge arc-style fields across an existing commit description
# and what we need to set.
ARC_REJECT_RE_LIST = [
    re.compile(r"^Summary:", flags=re.MULTILINE),
    re.compile(r"^Reviewers:", flags=re.MULTILINE),
]

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

MINIMUM_MERCURIAL_VERSION = LooseVersion("4.3.3")

MAX_TEXT_SIZE = 10 * 1024 * 1024
MAX_CONTEXT_SIZE = 4 * 1024 * 1024

NULL_SHA1 = "0" * 40

#
# Utilities
#


class SimpleCache:
    """Simple key/value store with all lowercase keys."""

    def __init__(self):
        self._cache = dict()

    def __contains__(self, key):
        return key.lower() in self._cache

    def get(self, key):
        return self._cache.get(key.lower())

    def set(self, key, value):
        self._cache[key.lower()] = value

    def delete(self, key):
        if key in self:
            del self._cache[key.lower()]

    def reset(self):
        self._cache = dict()


cache = SimpleCache()


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


def parse_zulu_time(timestamp):
    """Parse YYYY-MM-DDTHH:mm:SSZ date string, return as epoch seconds in local tz."""
    return calendar.timegm(time.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ"))


def check_call(command, **kwargs):
    # wrapper around subprocess.check_call with debug output
    logger.debug("$ %s", " ".join(quote(s) for s in command))
    try:
        subprocess.check_call(command, **kwargs)
    except subprocess.CalledProcessError as e:
        raise CommandError(
            "command '%s' failed to complete successfully" % command[0], e.returncode
        )


def check_call_by_line(command, cwd=None, never_log=False):
    # similar to check_call, yields for line-by-line processing
    logger.debug("$ %s", " ".join(quote(s) for s in command))

    # Connecting the STDIN to the PIPE will make arc throw an exception on reading
    # user input
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        cwd=cwd,
        universal_newlines=True,
    )
    try:
        for line in iter(process.stdout.readline, ""):
            line = line.rstrip()
            if not never_log:
                logger.debug("> %s", line)
            yield line
    finally:
        process.stdout.close()
        process.wait()

    if process.returncode:
        raise CommandError(
            "command '%s' failed to complete successfully" % command[0],
            process.returncode,
        )


def check_output(
    command,
    cwd=None,
    split=True,
    keep_ends=False,
    strip=True,
    never_log=False,
    stdin=None,
    stderr=None,
    env=None,
    search_error=None,
    expect_binary=False,
):
    # wrapper around subprocess.check_output with debug output and splitting
    logger.debug("$ %s", " ".join(quote(s) for s in command))
    kwargs = dict(cwd=cwd, stdin=stdin, stderr=stderr)
    if not expect_binary:
        kwargs["universal_newlines"] = True

    if env:
        kwargs["env"] = env

    try:
        output = subprocess.check_output(command, **kwargs)
    except subprocess.CalledProcessError as e:
        if search_error:
            for err in search_error:
                if err["matching"] in e.output:
                    logger.error(err["message"])

        if e.output and not never_log:
            logger.debug(e.output)

        if e.stderr and not never_log:
            logger.debug(e.stderr)

        raise CommandError(
            "command '%s' failed to complete successfully" % command[0], e.returncode
        )

    if expect_binary:
        logger.debug("%s bytes of data received", len(output))
        return output

    if strip:
        output = output.rstrip()
    if output and not never_log:
        logger.debug(output)
    return output.splitlines(keep_ends) if split else output


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


def prompt(question, options=None):
    if HAS_ANSI:
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
        # On Windows config file is likely to be cp1252 encoded, not UTF-8.
        if IS_WINDOWS:
            try:
                line = line
            except UnicodeDecodeError:
                pass

        try:
            name, value = line.split("=", 1)
        except ValueError:
            continue

        name = name.strip()
        value = value.strip()
        if filter_func is None or (callable(filter_func) and filter_func(name, value)):
            result[name] = value

    return result


def normalise_reviewer(reviewer, strip_group=True):
    """This provide a canonical form of the reviewer for comparison."""
    reviewer = reviewer.rstrip("!").lower()
    if strip_group:
        reviewer = reviewer.lstrip("#")
    return reviewer


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


def short_node(text):
    """Return a shortened version of a node name."""
    if len(text) == 40:
        try:
            int(text, 16)
            return text[:12]
        except ValueError:
            pass
    return text


# py2 doesn't handle SIGINT with background threads; hook up our own handler
# to allow background threads to check sig_int.triggered and stop.


class SigIntHandler(object):
    def __init__(self):
        self.triggered = False

    # noinspection PyUnusedLocal
    def signal_handler(self, sig, frame):
        self.triggered = True
        raise KeyboardInterrupt()


sig_int = SigIntHandler()
signal.signal(signal.SIGINT, sig_int.signal_handler)


class Spinner(threading.Thread):
    def __init__(self, message):
        super().__init__()
        self.message = message
        self.daemon = True
        self.running = False

    def run(self):
        self.running = True

        if not HAS_ANSI:
            sys.stdout.write("%s  " % self.message)

        spinner = ["-", "\\", "|", "/"]
        spin = 0
        try:
            while self.running:
                if HAS_ANSI:
                    sys.stdout.write("%s %s\r" % (self.message, spinner[spin]))
                else:
                    sys.stdout.write(chr(8) + spinner[spin])
                sys.stdout.flush()
                spin = (spin + 1) % len(spinner)
                time.sleep(0.2)
        finally:
            if HAS_ANSI:
                sys.stdout.write("\r\033[K")
            else:
                sys.stdout.write(chr(8) + " \n")
            sys.stdout.flush()


@contextmanager
def wait_message(message):
    if not SHOW_SPINNER:
        yield
        return

    spinner = Spinner(message)
    spinner.start()
    try:
        yield
    finally:
        spinner.running = False
        spinner.join()
        if sig_int.triggered:
            print("Cancelled")
            sys.exit(3)


#
# Configuration
#


class Config(object):
    def __init__(self, should_access_file=True):
        self._filename = os.path.join(HOME_DIR, ".moz-phab-config")
        self.name = "~/.moz-phab-config"  # human-readable name

        # Default values.
        defaults = """
            [ui]
            no_ansi = False

            [vcs]
            safe_mode = False

            [git]
            remote =

            [submit]
            auto_submit = False
            always_blocking = False
            warn_untracked = True

            [patch]
            apply_to = base
            create_bookmark = True
            always_full_stack = False

            [updater]
            self_last_check = 0
            arc_last_check = 0
            self_auto_update = True
            """

        self._config = configparser.ConfigParser()
        self._config.read_file(
            io.StringIO("\n".join([l.strip() for l in defaults.splitlines()]))
        )

        if self._config.has_section("arc"):
            self._config.remove_section("arc")

        if should_access_file:
            self._config.read([self._filename])

        self.no_ansi = self._config.getboolean("ui", "no_ansi")
        self.safe_mode = self._config.getboolean("vcs", "safe_mode")
        self.auto_submit = self._config.getboolean("submit", "auto_submit")
        self.always_blocking = self._config.getboolean("submit", "always_blocking")
        self.warn_untracked = self._config.getboolean("submit", "warn_untracked")
        self.apply_patch_to = self._config.get("patch", "apply_to")
        self.create_bookmark = self._config.getboolean("patch", "create_bookmark")
        self.always_full_stack = self._config.getboolean("patch", "always_full_stack")
        self.self_last_check = self._config.getint("updater", "self_last_check")
        self.self_auto_update = self._config.getboolean("updater", "self_auto_update")
        self.arc_last_check = self._config.getint("updater", "arc_last_check")
        git_remote = self._config.get("git", "remote")
        self.git_remote = git_remote.replace(" ", "").split(",") if git_remote else []

        if should_access_file and not os.path.exists(self._filename):
            self.write()

        self.arc = None

    def _set(self, section, option, value):
        if not self._config.has_section(section):
            self._config.add_section(section)
        self._config.set(section, option, str(value))

    def write(self):
        if os.path.exists(self._filename):
            logger.debug("updating %s", self._filename)
            self._set("submit", "auto_submit", self.auto_submit)
            self._set("patch", "always_full_stack", self.always_full_stack)
            self._set("updater", "self_last_check", self.self_last_check)
            self._set("updater", "arc_last_check", self.arc_last_check)
            self._set("updater", "self_auto_update", self.self_auto_update)

        else:
            logger.debug("creating %s", self._filename)
            self._set("ui", "no_ansi", self.no_ansi)
            self._set("vcs", "safe_mode", self.safe_mode)
            self._set("git", "remote", ", ".join(self.git_remote))
            self._set("submit", "auto_submit", self.auto_submit)
            self._set("submit", "always_blocking", self.always_blocking)
            self._set("submit", "warn_untracked", self.warn_untracked)
            self._set("patch", "apply_to", self.apply_patch_to)
            self._set("patch", "create_bookmark", self.create_bookmark)
            self._set("patch", "always_full_stack", self.always_full_stack)

        with open(self._filename, "w", encoding="utf-8") as f:
            self._config.write(f)


#
# Conduit
#


class ConduitAPI:
    def __init__(self):
        self.repo = None

    def set_repo(self, repo):
        self.repo = repo

    @property
    def repo_phid(self):
        return self.repo.phid

    def load_api_token(self):
        """Return an API Token for the given repository.

        Returns:
            API Token string
        """
        token = read_json_field(
            [get_arcrc_path()], ["hosts", self.repo.api_url, "token"]
        )
        if not token:
            raise ConduitAPIError(INSTALL_CERT_MSG)
        return token

    def save_api_token(self, token):
        filename = get_arcrc_path()
        created = False
        try:
            with open(filename, "r", encoding="utf-8") as f:
                rc = json.load(f)
        except FileNotFoundError:
            rc = {}
            created = True

        rc.setdefault("hosts", {})
        rc["hosts"].setdefault(self.repo.api_url, {})
        rc["hosts"][self.repo.api_url]["token"] = token

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(rc, f, sort_keys=True, indent=2)

        if created:
            os.chmod(filename, 0o600)

    def call(self, api_method, api_call_args):
        """Call Conduit API and return the JSON API call result.

        Args:
            api_method: The API method name to call, like 'differential.revision.edit'.
            api_call_args: JSON dict of call args to send.

        Returns:
            JSON API call result object

        Raises:
            ConduitAPIError if the API threw an error back at us.
        """
        url = urllib.parse.urlparse(urllib.parse.urljoin(self.repo.api_url, api_method))
        logger.debug("%s %s", url.geturl(), api_call_args)

        api_call_args = api_call_args.copy()
        api_call_args["__conduit__"] = {"token": self.load_api_token()}
        body = urllib.parse.urlencode(
            {
                "params": json.dumps(api_call_args),
                "output": "json",
                "__conduit__": True,
            }
        )
        # Send the POST request
        if url.scheme == "https":
            conn = HTTPSConnection(url.netloc)
        elif HTTP_ALLOWED:
            # Allow for an HTTP connection in suite.
            conn = HTTPConnection(url.netloc)
        else:
            raise CommandError("Only https connections are allowed.")

        conn.request("POST", url.geturl(), body=body)

        # Read the response as JSON
        response = json.loads(conn.getresponse().read().decode("utf-8"))
        if response["error_code"]:
            raise ConduitAPIError(
                response.get("error_info", "Error %s" % response["error_code"])
            )

        return response["result"]

    def ping(self):
        """Sends a ping to the Phabricator server using `conduit.ping` API.

        Returns: `True` if no error, otherwise - `False`
        """
        try:
            self.call("conduit.ping", {})
        except ConduitAPIError as err:
            logger.error(err)
            return False
        except CommandError as err:
            logger.error(err)
            return False
        return True

    def check(self):
        """Check if raw Conduit API can be used."""
        # Check if the cache file exists
        path = os.path.join(self.repo.dot_path, ".moz-phab_conduit-configured")
        if os.path.isfile(path):
            return True

        if self.ping():
            # Create the cache file
            with open(path, "a"):
                os.utime(path, None)
            return True

        return False

    def ids_to_phids(self, rev_ids):
        """Convert revision ids to PHIDs.

        Parameters:
            rev_ids (list): A list of revision ids

        Returns:
            A list of PHIDs.
        """
        return [r["phid"] for r in self.get_revisions(ids=rev_ids)]

    def id_to_phid(self, rev_id):
        """Convert revision id to PHID."""
        phids = self.ids_to_phids([rev_id])
        if phids:
            return phids[0]

        raise NotFoundError("revision {} not found".format(rev_id))

    def phids_to_ids(self, phids):
        """Convert revision PHIDs to ids.

        Parameteres:
            phids (list): A list of PHIDs

        Returns:
            A list of ids.
        """
        return ["D{}".format(r["id"]) for r in self.get_revisions(phids=phids)]

    def phid_to_id(self, phid):
        """Convert revision PHID to id."""
        ids = self.phids_to_ids([phid])
        if ids:
            return ids[0]

        raise NotFoundError("revision {} not found".format(phid))

    def get_revisions(self, ids=None, phids=None):
        """Get revisions info from Phabricator.

        Args:
            ids - list of revision ids
            phids - list of revision phids

        Returns a list of revisions ordered by ids or phids
        """
        if (ids and phids) or (ids is None and phids is None):
            raise ValueError("Internal Error: Invalid args to get_revisions")

        # Initialise depending on if we're passed revision IDs or PHIDs.
        if ids:
            ids = [str(rev_id) for rev_id in ids]
            phids_by_id = dict(
                [
                    (rev_id, cache.get("rev-id-%s" % rev_id))
                    for rev_id in ids
                    if "rev-id-%s" % rev_id in cache
                ]
            )
            found_phids = list(phids_by_id.values())
            query_field = "ids"
            query_values = [
                int(rev_id) for rev_id in set(ids) - set(phids_by_id.keys())
            ]

        else:
            phids_by_id = {}
            found_phids = phids[:]
            query_field = "phids"
            query_values = set([phid for phid in phids if "rev-%s" % phid not in cache])

        # Revisions metadata keyed by PHID.
        revisions = dict(
            [
                (phid, cache.get("rev-%s" % phid))
                for phid in found_phids
                if "rev-%s" % phid in cache
            ]
        )

        # Query Phabricator if we don't have cached values for revisions.
        if query_values:
            api_call_args = {
                "constraints": {query_field: sorted(query_values)},
                "attachments": {"reviewers": True},
            }
            response = self.call("differential.revision.search", api_call_args)
            rev_list = response.get("data")

            for r in rev_list:
                phids_by_id[str(r["id"])] = r["phid"]
                revisions[r["phid"]] = r
                cache.set("rev-id-%s" % r["id"], r["phid"])
                cache.set("rev-%s" % r["phid"], r)

        # Return revisions in the same order requested.
        if ids:
            # Skip revisions for which we do not have a query result.
            return [
                revisions[phids_by_id[rev_id]]
                for rev_id in ids
                if rev_id in phids_by_id
            ]
        else:
            return [revisions[phid] for phid in phids]

    def get_diffs(self, phids):
        """Get diffs from Phabricator.

        Args:
            phids - a list of diff PHIDs to pull

        Returns a dict of diffs identified by their PHID
        """
        api_call_args = {
            "constraints": {"phids": phids},
            "attachments": {"commits": True},
        }
        response = self.call("differential.diff.search", api_call_args)
        diff_list = response.get("data", [])

        diff_dict = {}
        for d in diff_list:
            diff_dict[d["phid"]] = d

        return diff_dict

    def get_successor_phids(self, phid, include_abandoned=False):
        return self.get_related_phids(
            phid, relation="child", include_abandoned=include_abandoned
        )

    def get_ancestor_phids(self, phid, include_abandoned=False):
        return self.get_related_phids(
            phid, relation="parent", include_abandoned=include_abandoned
        )

    def get_related_phids(self, base_phid, relation="parent", include_abandoned=False):
        """Returns the list of PHIDs with direct dependency"""
        result = []

        def _get_related(phid):
            api_call_args = {"sourcePHIDs": [phid], "types": ["revision.%s" % relation]}
            edge = self.call("edge.search", api_call_args)
            if edge.get("data"):
                if len(edge["data"]) > 1:
                    raise NonLinearException()

                result.append(edge["data"][0]["destinationPHID"])
                _get_related(result[-1])

        _get_related(base_phid)

        if not result or include_abandoned:
            return result

        revisions = self.get_revisions(phids=result)
        return [
            r["phid"]
            for r in revisions
            if r["fields"]["status"]["value"] != "abandoned"
        ]

    def get_stack(self, rev_ids):
        """Returns a dict of PHIDs."""
        phids = set()
        if not rev_ids:
            return {}
        revisions = self.get_revisions(ids=rev_ids)
        new_phids = set([rev["phid"] for rev in revisions])
        stack = {}

        while new_phids:
            phids.update(new_phids)

            edges = self.call(
                "edge.search",
                dict(
                    sourcePHIDs=list(new_phids),
                    types=["revision.parent", "revision.child"],
                    limit=10000,
                ),
            )["data"]

            new_phids = set()
            for edge in edges:
                new_phids.add(edge["sourcePHID"])
                new_phids.add(edge["destinationPHID"])

                if edge["edgeType"] == "revision.child":
                    if edge["sourcePHID"] in stack:
                        source_id = next(
                            r["id"]
                            for r in revisions
                            if r["phid"] == edge["sourcePHID"]
                        )
                        raise Error("Revision D%s has multiple children." % source_id)

                    stack[edge["sourcePHID"]] = edge["destinationPHID"]

            new_phids = new_phids - phids

        for child in list(stack.values()):
            # set the last child (not a parent)
            stack.setdefault(child)

        return stack

    def get_users(self, usernames):
        """Get users using the user.query API.

        Caches the result in the process.
        Returns a list of existing Phabricator users data.
        """
        to_collect = []
        users = []
        for user in usernames:
            u = user.rstrip("!")
            key = "user-%s" % u
            if key in cache:
                users.append(cache.get(key))
            else:
                to_collect.append(u)

        if not to_collect:
            return users

        api_call_args = {"usernames": to_collect}
        # We're using the deprecated user.query API as the user.search does not
        # provide the user availability information.
        # See https://phabricator.services.mozilla.com/conduit/method/user.query/
        response = self.call("user.query", api_call_args)
        for user in response:
            users.append(user)
            key = "user-%s" % user["userName"]
            cache.set(key, user)
            cache.set(user["phid"], key)

        return users

    def get_groups(self, slugs):
        to_collect = []
        groups = []
        for slug in slugs:
            s = slug.rstrip("!")
            key = "group-%s" % s
            if key in cache:
                groups.append(cache.get(key))
            else:
                to_collect.append(s)

        if not to_collect:
            return groups

        # See https://phabricator.services.mozilla.com/conduit/method/project.search/
        api_call_args = {"queryKey": "active", "constraints": {"slugs": to_collect}}
        response = self.call("project.search", api_call_args)
        for data in response.get("data"):
            group = dict(name=data["fields"]["slug"], phid=data["phid"])
            groups.append(group)
            key = "group-%s" % group["name"]
            cache.set(key, group)

        # projects might be received by an alias.
        maps = response["maps"]["slugMap"]
        for alias in maps.keys():
            name = normalise_reviewer(alias)
            group = dict(name=name, phid=maps[alias]["projectPHID"])
            key = "group-%s" % alias
            if key not in cache:
                groups.append(group)
                cache.set(key, group)

        return groups

    def create_revision(
        self, commit, title, summary, diff_phid, has_commit_reviewers, wip=False
    ):
        """Create a new revision in Phabricator."""
        transactions = [
            dict(type="title", value=title),
            dict(type="summary", value=summary),
        ]
        if has_commit_reviewers and not wip:
            update_revision_reviewers(transactions, commit)

        if commit["bug-id"]:
            transactions.append(dict(type="bugzilla.bug-id", value=commit["bug-id"]))
        return self.edit_revision(
            transactions=transactions, diff_phid=diff_phid, wip=wip
        )

    def update_revision(
        self,
        commit,
        has_commit_reviewers,
        existing_reviewers,
        diff_phid=None,
        wip=False,
        comment=None,
    ):
        """Update an existing revision in Phabricator."""
        # Update the title and summary
        transactions = [
            dict(type="title", value=commit["title"]),
            dict(type="summary", value=strip_differential_revision(commit["body"])),
        ]

        # Add update comment
        if comment:
            transactions.append(dict(type="comment", value=comment))

        # Add reviewers only if revision lacks them
        if has_commit_reviewers and not wip:
            if not existing_reviewers:
                update_revision_reviewers(transactions, commit)

        # Update bug id if different
        if commit["bug-id"]:
            revision = conduit.get_revisions(ids=[int(commit["rev-id"])])[0]
            if revision["fields"]["bugzilla.bug-id"] != commit["bug-id"]:
                transactions.append(
                    dict(type="bugzilla.bug-id", value=commit["bug-id"])
                )

        return self.edit_revision(
            transactions=transactions,
            diff_phid=diff_phid,
            rev_id=commit["rev-id"],
            wip=wip,
        )

    def edit_revision(
        self, transactions=None, diff_phid=None, rev_id=None, wip=False, force_wip=False
    ):
        """Edit (create or update) a revision."""
        trans = list(transactions or [])
        # diff_phid is not present for changes in revision settings (like WIP)
        if diff_phid:
            trans.append(dict(type="update", value=diff_phid))

        set_wip_later = False
        if wip:
            if rev_id and not force_wip:
                # Set "changes planned" in a new request called after the update one.
                # Phab API validation would return with an error if "changes planned
                # would be set in the first API call.
                existing_revision = conduit.get_revisions(ids=[int(rev_id)])[0]
                set_wip_later = (
                    existing_revision["fields"]["status"]["value"] == "changes-planned"
                )

        if force_wip or wip and not set_wip_later:
            trans.append(dict(type="plan-changes", value=True))

        api_call_args = dict(transactions=trans)

        if rev_id:
            api_call_args["objectIdentifier"] = rev_id

        revision = self.call("differential.revision.edit", api_call_args)
        if not revision:
            raise ConduitAPIError("Can't edit the revision.")

        if wip and set_wip_later:
            return self.edit_revision(rev_id=rev_id, force_wip=True)

        return revision

    def get_repository(self, call_sign):
        """Get the repository info from Phabricator."""
        key = "repo-%s" % call_sign
        if key in cache:
            return cache.get(key)

        api_call_args = dict(constraints=dict(callsigns=[call_sign]), limit=1)
        data = self.call("diffusion.repository.search", api_call_args)
        if not data.get("data"):
            raise NotFoundError("Repository %s not found" % call_sign)

        repo = data["data"][0]
        cache.set(key, repo)
        return repo

    def create_diff(self, changes, base_revision):
        creation_method = ["moz-phab", conduit.repo.vcs]
        if conduit.repo.vcs == "git" and conduit.repo.is_cinnabar_required:
            creation_method.append("cinnabar")

        api_call_args = dict(
            changes=changes,
            sourceMachine=self.repo.phab_url,
            sourceControlSystem=self.repo.phab_vcs,
            sourceControlPath="/",
            sourceControlBaseRevision=base_revision,
            creationMethod="-".join(creation_method),
            lintStatus="none",
            unitStatus="none",
            repositoryPHID=self.repo.phid,
            sourcePath=self.repo.path,
            branch="HEAD" if self.repo.phab_vcs == "git" else "default",
        )
        return self.call("differential.creatediff", api_call_args)

    def set_diff_property(self, diff_id, commit, message):
        data = {
            commit["node"]: {
                "author": commit["author-name"],
                "authorEmail": commit["author-email"],
                "time": 0,
                "summary": commit["title-preview"],
                "message": message,
                "commit": conduit.repo.get_public_node(commit["node"]),
                "parents": [conduit.repo.get_public_node(commit["parent"])],
            }
        }
        if "tree-hash" in commit:
            data[commit["node"]]["tree"] = commit["tree-hash"]

        if self.repo.phab_vcs == "hg":
            data[commit["node"]]["rev"] = commit["node"]

        api_call_args = dict(
            diff_id=diff_id, name="local:commits", data=json.dumps(data)
        )
        self.call("differential.setdiffproperty", api_call_args)

    def file_upload(self, data):
        if not data:
            return
        data_base64 = base64.standard_b64encode(data)
        return self.call("file.upload", dict(data_base64=data_base64.decode()))

    def whoami(self):
        if "whoami" in cache:
            return cache.get("whoami")

        who = self.call("user.whoami", {})
        cache.set("whoami", who)
        return who


conduit = ConduitAPI()


#
# Diff
#


class Diff:
    """Representation of the Diff used to submit to the Phabricator."""

    Hunk = namedtuple(
        "Hunk",
        [
            "old_off",
            "old_len",
            "new_off",
            "new_len",
            "old_eof_newline",
            "new_eof_newline",
            "added",
            "deleted",
            "corpus",
        ],
    )

    class Change:
        def __init__(self, path):
            self.old_mode = None
            self.cur_mode = None
            self.old_path = None
            self.cur_path = path
            self.away_paths = []
            self.kind = Diff.Kind("CHANGE")
            self.binary = False
            self.file_type = Diff.FileType("TEXT")
            self.uploads = []
            self.hunks = []

        @property
        def added(self):
            return sum(hunk.added for hunk in self.hunks)

        @property
        def deleted(self):
            return sum(hunk.deleted for hunk in self.hunks)

        def to_conduit(self, node):
            # Record upload information
            metadata = {}
            for upload in self.uploads:
                metadata["%s:binary-phid" % upload["type"]] = upload["phid"]
                metadata["%s:file:size" % upload["type"]] = len(upload["value"])
                metadata["%s:file:mime-type" % upload["type"]] = upload["mime"]

            # Translate hunks
            hunks = [
                {
                    "oldOffset": hunk.old_off,
                    "oldLength": hunk.old_len,
                    "newOffset": hunk.new_off,
                    "newLength": hunk.new_len,
                    "addLines": hunk.added,
                    "delLines": hunk.deleted,
                    "isMissingOldNewline": not hunk.old_eof_newline,
                    "isMissingNewNewline": not hunk.new_eof_newline,
                    "corpus": hunk.corpus,
                }
                for hunk in self.hunks
            ]

            old_props = {"unix:filemode": self.old_mode} if self.old_mode else {}
            cur_props = {"unix:filemode": self.cur_mode} if self.cur_mode else {}

            return {
                "metadata": metadata,
                "oldPath": self.old_path,
                "currentPath": self.cur_path,
                "awayPaths": self.away_paths,
                "oldProperties": old_props,
                "newProperties": cur_props,
                "commitHash": node,
                "type": self.kind.value,
                "fileType": self.file_type.value,
                "hunks": hunks,
            }

    class Kind:
        values = dict(
            ADD=1,
            CHANGE=2,
            DELETE=3,
            MOVE_AWAY=4,
            COPY_AWAY=5,
            MOVE_HERE=6,
            COPY_HERE=7,
            MULTICOPY=8,
        )

        def __init__(self, name):
            self.value = self.values[name]
            self.name = name

        def short(self):
            if self.name == "ADD":
                return "A "
            elif self.name == "CHANGE":
                return "M "
            elif self.name == "DELETE":
                return "D "
            elif self.name == "MOVE_AWAY":
                return "R>"
            elif self.name == "MOVE_HERE":
                return ">R"
            elif self.name == "COPY_AWAY":
                return "C>"
            elif self.name == "COPY_HERE":
                return ">C"
            elif self.name == "MULTICOPY":
                return "C*"

    class FileType:
        values = dict(
            TEXT=1,
            IMAGE=2,
            BINARY=3,
            DIRECTORY=4,  # Should never show up...
            SYMLINK=5,  # Support symlinks (do we care?)
            DELETED=6,
            NORMAL=7,
        )

        def __init__(self, name):
            self.value = self.values[name]
            self.name = name

    def __init__(self):
        # self.commit = commit
        self.changes = {}
        self.phid = None

    def change_for(self, path):
        if path not in self.changes:
            self.changes[path] = self.Change(path)
        return self.changes[path]

    def set_change_kind(self, change, kind, a_mode, b_mode, a_path, b_path):
        """Determine the correct kind from the letter."""
        if kind == "A":
            change.kind = self.Kind("ADD")
            change.cur_mode = b_mode

        elif kind == "D":
            change.kind = self.Kind("DELETE")
            change.old_mode = a_mode
            change.old_path = a_path

        elif kind == "M":
            change.kind = self.Kind("CHANGE")
            if a_mode != b_mode:
                change.old_mode = a_mode
                change.cur_mode = b_mode
            change.old_path = a_path
            assert change.old_path == change.cur_path

        elif kind == "R":
            change.kind = self.Kind("MOVE_HERE")
            if a_mode != b_mode:
                change.old_mode = a_mode
                change.cur_mode = b_mode

            change.old_path = a_path
            old = self.change_for(change.old_path)
            if old.kind.name in ["MOVE_AWAY", "COPY_AWAY"]:
                old.kind = self.Kind("MULTICOPY")
            elif old.kind.name != "MULTICOPY":
                old.kind = self.Kind("MOVE_AWAY")

            old.away_paths.append(change.cur_path)

        elif kind == "C":
            change.kind = self.Kind("COPY_HERE")
            if a_mode != b_mode:
                change.old_mode = a_mode
                change.cur_mode = b_mode

            change.old_path = a_path
            old = self.change_for(change.old_path)
            if old.kind.name in ["MOVE_AWAY", "COPY_AWAY"]:
                old.kind = self.Kind("MULTICOPY")
            elif old.kind.name != "MULTICOPY":
                old.kind = self.Kind("COPY_AWAY")

            old.away_paths.append(change.cur_path)

        else:
            raise "unsupported change type %s" % kind

    def upload_files(self):
        for change in list(self.changes.values()):
            for upload in change.uploads:
                upload["phid"] = conduit.file_upload(upload["value"])

    def submit(self, commit, message):
        files_changed = sorted(
            self.changes.values(), key=operator.attrgetter("cur_path")
        )
        changes = [
            change.to_conduit(conduit.repo.get_public_node(commit["node"]))
            for change in files_changed
        ]
        diff = conduit.create_diff(
            changes, conduit.repo.get_public_node(commit["parent"])
        )

        # Add information about our local commit to the patch. This info is
        # needed by Lando.
        conduit.set_diff_property(diff["diffid"], commit, message)

        return diff["phid"]

    @staticmethod
    def parse_git_diff(hdr):
        m = re.match(
            r"@@ -(?P<old_off>\d+)(?:,(?P<old_len>\d+))? "
            r"\+(?P<new_off>\d+)(?:,(?P<new_len>\d+))? @@",
            hdr,
        )
        old_off = int(m.group("old_off"))
        old_len = int(m.group("old_len") or 1)
        new_off = int(m.group("new_off"))
        new_len = int(m.group("new_len") or 1)
        return old_off, new_off, old_len, new_len


#
# Repository
#


def find_repo_root(path):
    """Lightweight check for a repo in/under the specified path."""
    path = os.path.abspath(path)
    while os.path.split(path)[1]:
        if Mercurial.is_repo(path) or Git.is_repo(path):
            return path
        path = os.path.abspath(os.path.join(path, os.path.pardir))
    return None


def probe_repo(path):
    try:
        return Mercurial(path)
    except ValueError:
        pass

    try:
        return Git(path)
    except ValueError:
        pass

    return None


def repo_from_args(args):
    """Returns a Repository object from either args.path or the cwd"""

    repo = None

    # This allows users to override the below sanity checks.
    if hasattr(args, "path") and args.path:
        repo = probe_repo(args.path)
        if not repo:
            raise Error("%s: Not a repository: .hg / .git" % args.path)

    else:
        # Walk parents to find repository root.
        path = find_repo_root(os.getcwd())
        if path:
            repo = probe_repo(path)
        if not repo:
            raise Error(
                "Not a repository (or any of the parent directories): .hg / .git"
            )

    repo.set_args(args)
    return repo


def get_arcrc_path():
    """Return a path to the user's Arcanist configuration file."""
    if "arcrc" in cache:
        return cache.get("arcrc")

    if IS_WINDOWS:
        arcrc = os.path.join(os.getenv("APPDATA", ""), ".arcrc")
    else:
        arcrc = os.path.expanduser("~/.arcrc")
        if os.path.isfile(arcrc) and stat.S_IMODE(os.stat(arcrc).st_mode) != 0o600:
            logger.debug("Changed file permissions on the %s file.", arcrc)
            os.chmod(arcrc, 0o600)

    cache.set("arcrc", arcrc)
    return arcrc


class Repository(object):
    def __init__(self, path, dot_path, phab_url=None):
        self._phid = None
        self._phab_repo = None
        self._phab_vcs = None
        self.vcs = None
        self.path = path  # base repository directory
        self.dot_path = dot_path  # .hg/.git directory
        self._arcconfig_files = [
            os.path.join(self.dot_path, ".arcconfig"),
            os.path.join(self.path, ".arcconfig"),
        ]
        self.args = None
        self.phab_url = (phab_url or self._phab_url()).rstrip("/")
        self.api_url = self._api_url()
        self.call_sign = self._get_setting("repository.callsign")

    def is_worktree_clean(self):
        """Check if the working tree is clean."""

    def before_submit(self):
        """Executed before the submit commit."""

    def after_submit(self):
        """Executed after the submit commit."""

    def _get_setting(self, key):
        """Read settings from .arcconfig"""
        value = read_json_field(self._arcconfig_files, [key])
        return value

    def _phab_url(self):
        """Determine the phab/conduit URL."""

        # In order of priority as per arc
        # FIXME: This should also check {.hg|.git}/arc/config, which is where
        # `arc set-config --local` writes to.  See bug 1497786.
        defaults_files = [get_arcrc_path()]
        if IS_WINDOWS:
            defaults_files.append(
                os.path.join(
                    os.getenv("ProgramData", ""), "Phabricator", "Arcanist", "config"
                )
            )
        else:
            defaults_files.append("/etc/arcconfig")

        phab_url = (
            self._get_setting("phabricator.uri")
            or self._get_setting("conduit_uri")
            or read_json_field(defaults_files, ["config", "default"])
        )

        if not phab_url:
            raise Error("Failed to determine Phabricator URL (missing .arcconfig?)")
        return phab_url

    def cleanup(self):
        """Perform any repo-related cleanup tasks.

        May be called multiple times.
        If an exception is raised this is NOT called (to avoid dataloss)."""

    def finalize(self, commits):
        """Update the history after node changed."""

    def set_args(self, args):
        self.args = args

    def untracked(self):
        """Return a list of untracked files."""

    def commit_stack(self):
        """Return list of commits.

        List of dicts with the following keys:
            name          human readable identifier of commit (eg. short sha)
            node          sha/hash
            title         first line of commit description (unaltered)
            body          commit description, excluding first line
            title-preview title with bug-id and reviewer modifications
            bug-id        bmo bug-id
            bug-id-orig   original bug-id from commit desc
            reviewers     list of reviewers
            rev-id        phabricator revision id
        """

    def refresh_commit_stack(self, commits):
        """Update the stack following an altering change (eg rebase)."""

    def is_node(self, node):
        """Check if node exists.

        Returns a Boolean.
        """

    def check_node(self, node):
        """Check if node exists.

        Returns a node if found.

        Raises NotFoundError if node not found in the repository.
        """

    def checkout(self, node):
        """Checkout/Update to specified node."""

    def commit(self, body):
        """Commit the changes in the working directory."""

    def amend_commit(self, commit, commits):
        """Amend commit description from `title` and `desc` fields"""

    def rebase_commit(self, source_commit, dest_commit):
        """Rebase source onto destination."""

    def before_patch(self, node, name):
        """Prepare repository to apply the patches."""

    def apply_patch(self, diff, body, author, author_date):
        """Apply the patch and commit the changes."""

    def check_commits_for_submit(
        self, commits, *, validate_reviewers=True, require_bug=True
    ):
        """Validate the list of commits (from commit_stack) are ok to submit"""
        errors = []
        warnings = []

        # Extract a set of reviewers and verify first; they will be displayed
        # with other commit errors.
        all_reviewers = {}
        reviewer_commit_map = {}
        commit_invalid_reviewers = {}
        rev_ids_to_names = dict()
        for commit in commits:
            commit_invalid_reviewers[commit["node"]] = []

            if not commit["rev-id"]:
                continue
            names = rev_ids_to_names.setdefault(commit["rev-id"], [])
            names.append(commit["name"])

        for rev_id, names in rev_ids_to_names.items():
            if len(names) < 2:
                continue

            error_msg = "Phabricator revisions should be unique, but the following commits refer to the same one (D{}):\n".format(
                rev_id
            )
            for name in names:
                error_msg += "* %s\n" % name
            errors.append(error_msg)

        if validate_reviewers:
            # Flatten and deduplicate reviewer list, keeping track of the
            # associated commit
            for commit in commits:
                for group in list(commit["reviewers"].keys()):
                    for reviewer in commit["reviewers"][group]:
                        all_reviewers.setdefault(group, set())
                        all_reviewers[group].add(reviewer)

                        reviewer = normalise_reviewer(reviewer)
                        reviewer_commit_map.setdefault(reviewer, [])
                        reviewer_commit_map[reviewer].append(commit["node"])

        # Verify all reviewers in a single call
        for invalid_reviewer in check_for_invalid_reviewers(all_reviewers):
            for node in reviewer_commit_map[
                normalise_reviewer(invalid_reviewer["name"])
            ]:
                commit_invalid_reviewers[node].append(invalid_reviewer)

        unavailable_reviewers_warning = False
        for commit in commits:
            commit_errors = []
            commit_warnings = []

            if require_bug and not commit["bug-id"]:
                commit_errors.append("missing bug-id")
            if has_arc_rejections(commit["body"]):
                commit_errors.append("contains arc fields")

            if commit["rev-id"]:
                revisions = conduit.get_revisions(ids=[int(commit["rev-id"])])
                if len(revisions) == 0:
                    commit_errors.append(
                        "Phabricator did not return a query result for revision D%s"
                        " (it might be inaccessible or not exist at all)"
                        % commit["rev-id"]
                    )

            # commit_issues identified below this are commit_errors unless
            # self.args.force is True, which makes them commit_warnings
            commit_issues = (
                commit_warnings if self.args and self.args.force else commit_errors
            )

            for reviewer in commit_invalid_reviewers[commit["node"]]:
                if "disabled" in reviewer:
                    commit_errors.append("User %s is disabled" % reviewer["name"])
                elif "until" in reviewer:
                    unavailable_reviewers_warning = True
                    msg = "%s is not available until %s" % (
                        reviewer["name"],
                        reviewer["until"],
                    )
                    commit_issues.append(msg)
                else:
                    commit_errors.append(
                        "%s is not a valid reviewer's name" % reviewer["name"]
                    )

            if commit_errors:
                errors.append(
                    "%s %s\n- %s"
                    % (commit["name"], commit["title"], "\n- ".join(commit_errors))
                )

            if commit_warnings:
                warnings.append(
                    "%s %s\n- %s"
                    % (commit["name"], commit["title"], "\n- ".join(commit_warnings))
                )

        if errors:
            raise Error("\n\n".join(errors))

        if warnings:
            logger.warning("\n\n".join(warnings))

        if unavailable_reviewers_warning:
            logger.warning("Notice: reviewer availability overridden.")

    def check_arc(self):
        """Check if arc can communicate with Phabricator."""
        # Check if the cache file exists
        path = os.path.join(self.dot_path, ".moz-phab_arc-configured")
        if os.path.isfile(path):
            return True

        if arc_ping(self.path):
            # Create the cache file
            with open(path, "a"):
                os.utime(path, None)
            return True

        return False

    def _api_url(self):
        """Return a base URL for conduit API call
        """
        return urllib.parse.urljoin(self.phab_url, "api/")

    @property
    def phab_repo(self):
        """Representation of the Repository in Phabricator API."""
        if not self._phab_repo:
            with wait_message("Reading repository data"):
                self._phab_repo = conduit.get_repository(self.call_sign)

        return self._phab_repo

    @property
    def phid(self):
        """PHID of the repository.

        This value does not change over time.
        It is stored in a file to avoid calling the API on every run.
        """
        if not self._phid:
            # check file
            path = os.path.join(self.dot_path, ".moz-phab_phid")
            if os.path.isfile(path):
                with open(path) as f:
                    self._phid = f.readline()
            else:
                self._phid = self.phab_repo["phid"]
                with open(path, "w") as f:
                    f.write(self._phid)

        return self._phid

    def check_vcs(self):
        """`Git.check_vcs` raises if cinnabar required and not installed."""
        if self.args.force_vcs:
            return True

        if self.vcs != self.phab_vcs:
            raise Error(
                "Local VCS ({local}) is different from the one defined in the "
                "repository ({remote}).".format(local=self.vcs, remote=self.phab_vcs)
            )

        return True

    @property
    def phab_vcs(self):
        """Version Control System short name stored in Phabricator.

        This value does not change over time.
        It is stored in a file to avoid calling the API on every run.
        """
        if not self._phab_vcs:
            # check file
            path = os.path.join(self.dot_path, ".moz-phab_vcs")
            if os.path.isfile(path):
                with open(path) as f:
                    self._phab_vcs = f.readline()
            else:
                self._phab_vcs = self.phab_repo["fields"]["vcs"]
                with open(path, "w") as f:
                    f.write(self._phab_vcs)

        return self._phab_vcs

    def get_public_node(self, node):
        """Hashtag in a remote VCS."""
        return node


#
# Mercurial
#


class Mercurial(Repository):
    def __init__(self, path):
        dot_path = os.path.join(path, ".hg")
        if not os.path.isdir(dot_path):
            raise ValueError("%s: not a hg repository" % path)
        logger.debug("found hg repo in %s", path)

        super().__init__(path, dot_path)
        self.vcs = "hg"

        self._hg = ["hg.exe" if IS_WINDOWS else "hg"]
        self.revset = None
        self.strip_nodes = []
        self.status = None
        self.obsstore = None
        self.unlink_obsstore = False
        self.use_evolve = False
        self.has_mq = False
        self.has_shelve = False
        self.previous_bookmark = None
        self.has_temporary_bookmark = False

        # Normalise/standardise Mercurial's output.
        os.environ["HGPLAIN"] = "1"
        os.environ["HGENCODING"] = "UTF-8"

        # Check for `hg`, and mercurial version.
        if not which_path(self._hg[0]):
            raise Error("Failed to find 'hg' executable")
        m = re.search(
            r"\(version ([^)]+)\)", self.hg_out(["--version", "--quiet"], split=False)
        )
        if not m:
            raise Error("Failed to determine Mercurial version.")
        self.mercurial_version = LooseVersion(m.group(1))
        if self.mercurial_version < MINIMUM_MERCURIAL_VERSION:
            raise Error(
                "You are currently running Mercurial %s.  "
                "Mercurial %s or newer is required."
                % (self.mercurial_version, MINIMUM_MERCURIAL_VERSION)
            )

    @classmethod
    def is_repo(cls, path):
        """Quick check for repository at specified path."""
        return os.path.exists(os.path.join(path, ".hg"))

    @staticmethod
    def _get_extension(extension, hg_config):
        for prefix in ("extensions.%s", "extensions.hgext.%s"):
            field = prefix % extension
            if field in hg_config:
                return hg_config.get(field, "")

        return None

    def is_worktree_clean(self):
        status = self._status()
        return not status["T"]

    def hg(self, command, **kwargs):
        check_call(self._hg + command, cwd=self.path, **kwargs)

    def hg_out(self, command, **kwargs):
        return check_output(self._hg + command, cwd=self.path, **kwargs)

    def hg_log(self, revset, split=True, select="node"):
        return self.hg_out(["log", "-T", "{%s}\n" % select, "-r", revset], split=split)

    def before_submit(self):
        # Remember the currently checked out commit.  If a bookmark is active
        # just use that, otherwise create a randomly named bookmark which will
        # be deleted in cleanup(). Mercurial will automatically move the
        # bookmark to the successors as we update commits.
        for active, bookmark in [
            l.split(" ", 1)
            for l in self.hg_out(["bookmark", "-T", "{active} {bookmark}\n"])
        ]:
            if active == "True":
                self.previous_bookmark = bookmark
                break
        else:
            self.previous_bookmark = "moz-phab_%s" % self.hg_out(
                ["id", "-q"], split=False, strip=True
            )
            self.has_temporary_bookmark = True
            self.hg(["bookmark", self.previous_bookmark])

    def after_submit(self):
        # Restore the previously active commit.
        self.hg(["update", self.previous_bookmark])

    def cleanup(self):
        # Remove the store of obsolescence markers; if the user doesn't have evolve
        # installed mercurial will warn if this exists.
        if not self.use_evolve and self.unlink_obsstore:
            with suppress(FileNotFoundError):
                os.unlink(self.obsstore)

        if self.strip_nodes:
            # With the obsstore deleted the amended nodes are no longer hidden, so
            # we need to strip them completely from the repo.
            self.hg(["strip", "--hidden"] + self.strip_nodes)
            self.strip_nodes = []

        # Remove temporary bookmark
        if self.has_temporary_bookmark:
            bookmarks = self.hg_out(["bookmark", "-T", "{bookmark}\n"])
            if self.previous_bookmark in bookmarks:
                self.hg(["bookmark", "--delete", self.previous_bookmark])
            self.previous_bookmark = None
            self.has_temporary_bookmark = False

    def _status(self):
        # `hg status` is slow on large repos.  As we'll need both uncommitted changes
        # and untracked files separately, run it once and cache results.
        if self.status is None:
            self.status = dict(T=[], U=[])
            for line in self.hg_out(
                ["status", "--added", "--deleted", "--modified", "--unknown"],
                split=True,
            ):
                status, path = line.split(" ", 1)
                self.status["U" if status == "?" else "T"].append(path)
        return self.status

    def untracked(self):
        return self._status()["U"]

    def _refresh_commit(self, commit, node, rev=None):
        """Update commit's node and name from node and rev."""
        if not rev:
            rev = self.hg_log(node, select="rev", split=False)
        commit["node"] = node
        commit["name"] = "%s:%s" % (rev, short_node(node))

    def _get_successor(self, node):
        """Get the successor of the commit represented by its node.

        Returns: a tuple containing rev and node"""
        hg_log = self.hg_out(
            ["log"]
            + ["-T", "{rev} {node}\n"]
            + ["--hidden"]
            + ["-r", "successors(%s) and not obsolete()" % node]
        )
        if not hg_log:
            return None, None

        # Not sure the best way to handle multiple successors, so just bail out.
        if len(hg_log) > 1:
            raise Error("Multiple successors found for %s, unable to continue" % node)

        return hg_log[0].split(" ", 1)

    def refresh_commit_stack(self, commits):
        """Update all commits to point to their superseded commit."""
        for commit in commits:
            (rev, node) = self._get_successor(commit["node"])
            if rev and node:
                self._refresh_commit(commit, node, rev)

        self.revset = "%s::%s" % (commits[0]["node"], commits[-1]["node"])

        super().refresh_commit_stack(commits)

    def set_args(self, args):
        super().set_args(args)

        # Load hg config into hg_config.  We'll specify specific settings on
        # the command line when calling hg; all other user settings are ignored.
        # Do not parse shell alias extensions.
        hg_config = parse_config(
            self.hg_out(["config"], never_log=True),
            lambda name, value: not (
                name.startswith("extensions.") and value.startswith("!")
            ),
        )

        safe_mode = self.args.safe_mode or config.safe_mode
        safe_options = []
        options = []

        # Need to use the correct username.
        if "ui.username" not in hg_config:
            raise Error("ui.username is not configured in your hgrc")

        safe_options.extend(["--config", "ui.username=%s" % hg_config["ui.username"]])

        options.extend(
            # Always need rebase.
            ["--config", "extensions.rebase="]
            # Mercurial should never paginate the response.
            + ["--pager", "never"]
        )

        # Perform rebases in-memory to improve performance (requires v4.5+).
        if not safe_mode and self.mercurial_version >= LooseVersion("4.5"):
            options.extend(["--config", "rebase.experimental.inmemory=true"])

        # Enable evolve if the user's currently using it.  evolve makes amending
        # commits with children trivial (amongst other things).
        ext_evolve = self._get_extension("evolve", hg_config)
        if ext_evolve is not None:
            safe_options.extend(["--config", "extensions.evolve=%s" % ext_evolve])
            self.use_evolve = True

        # Otherwise just enable obsolescence markers, and when we're done remove
        # the obsstore we created.
        else:
            options.extend(["--config", "experimental.evolution.createmarkers=true"])
            options.extend(["--config", "extensions.strip="])
            self.use_evolve = False
            self.obsstore = os.path.join(self.path, ".hg", "store", "obsstore")
            self.unlink_obsstore = not os.path.exists(self.obsstore)

        # This script interacts poorly with mq.
        ext_mq = self._get_extension("mq", hg_config)
        self.has_mq = ext_mq is not None
        if self.has_mq:
            safe_options.extend(["--config", "extensions.mq=%s" % ext_mq])

        # `shelve` is useful for dealing with uncommitted changes; track if it's
        # currently enabled so we can tailor our error accordingly.
        self.has_shelve = self._get_extension("shelve", hg_config) is not None

        # Disable the user's hgrc file, to ensure we run without rogue extensions.
        if safe_mode:
            os.environ["HGRCPATH"] = ""
            options.extend(safe_options)

        self._hg.extend(options)

        if hasattr(self.args, "start_rev"):
            # Set the default start revision.
            if self.args.start_rev == "(auto)":
                start = "ancestors(.) and not public() and not obsolete()"
            else:
                start = self.args.start_rev

            # Resolve to nodes as that's nicer to read.
            try:
                start = self.hg_log(start)[0]
            except IndexError:
                if self.args.start_rev == "(auto)":
                    raise Error("Failed to find draft commits to submit")
                else:
                    raise Error(
                        "Failed to start of commit range: %s" % self.args.start_rev
                    )
            try:
                end = self.hg_log(self.args.end_rev)[0]
            except IndexError:
                raise Error("Failed to end of commit range: %s" % self.args.end_rev)

            self.revset = "%s::%s" % (short_node(start), short_node(end))

    def commit_stack(self):
        # Grab all the info we need about the commits, using randomness as a delimiter.
        boundary = "--%s--\n" % uuid.uuid4().hex
        hg_log = self.hg_out(
            ["log"]
            + [
                "-T",
                "{rev}\n{node}\n{date|rfc822date}\n{author|person}\n{author|email}\n"
                "{desc}%s" % boundary,
            ]
            + ["-r", self.revset],
            split=False,
            strip=False,
        )[: -len(boundary)]

        # Guard against conditions where the stack is empty (see bug 1547083).
        if not hg_log:
            return []

        commits = []
        nodes = []
        branching_children = []
        for log_line in hg_log.split(boundary):
            rev, node, author_date, author_name, author_email, desc = log_line.split(
                "\n", 5
            )
            desc = desc.splitlines()

            children = self.hg_log("children(%s)" % node)
            if len(children) > 1 and not self.use_evolve:
                branching_children.extend(children)

            commits.append(
                {
                    "name": "%s:%s" % (rev, short_node(node)),
                    "node": node,
                    "public-node": node,
                    "orig-node": node,
                    "title": desc[0],
                    "title-preview": desc[0],
                    "body": "\n".join(desc[1:]).rstrip(),
                    "bug-id": None,
                    "reviewers": dict(request=[], granted=[]),
                    "rev-id": None,
                    "author-date": author_date,
                    "author-name": author_name,
                    "author-email": author_email,
                }
            )
            nodes.append(node)

        if branching_children:
            will_be_deleted = [
                short_node(c) for c in branching_children if c not in nodes
            ]
            msg = "following commits will be DELETED:\n%s" % will_be_deleted
            if not self.args.force_delete:
                raise Error(
                    "DAG branch point detected. Please install the evolve extension.\n"
                    "(https://www.mercurial-scm.org/doc/evolution/)\n"
                    "If you continue with `--force-delete` the %s" % msg
                )
            else:
                logger.warning("`--force-delete` used. The %s", msg)

        return commits

    def is_node(self, node):
        try:
            self.hg_out(["identify", "-q", "-r", node])
        except CommandError:
            return False

        return True

    def check_node(self, node):
        if not self.is_node(node):
            raise NotFoundError()

        return node

    def checkout(self, node):
        self.hg(["update", "--quiet", node])

    def commit(self, body):
        """Commit the changes in the working directory."""
        with temporary_file(body) as temp_f:
            self.hg(["commit", "--logfile", temp_f])

    def before_patch(self, node, name):
        """Prepare repository to apply the patches.

        Args:
            node - SHA1 or revision of the base commit
            name - name of the bookmark to be created
        """
        # Checkout sha
        if node:
            with wait_message("Checking out %s.." % short_node(node)):
                self.checkout(node)
            if not self.args.raw:
                logger.info("Checked out %s", short_node(node))

        if name and config.create_bookmark and not self.args.no_bookmark:
            bookmarks = self.hg_out(["bookmarks", "-T", "{bookmark}\n"])
            bookmark_name = name
            i = 0
            while bookmark_name in bookmarks:
                i += 1
                bookmark_name = "%s_%s" % (name, i)

            self.hg(["bookmark", bookmark_name])
            if not self.args.raw:
                logger.info("Bookmark set to %s", bookmark_name)

    def apply_patch(self, diff, body, author, author_date):
        commands = []
        if author:
            commands.extend(["-u", author])

        if author_date:
            commands.extend(["-d", author_date])

        with temporary_binary_file(diff.encode("utf8")) as patch_file, temporary_file(
            body
        ) as body_file:
            self.hg(["import", patch_file, "--quiet", "-l", body_file] + commands)

    def _amend_commit_body(self, node, body):
        with temporary_file(body) as body_file:
            self.checkout(node)
            self.hg(["commit", "--amend", "--logfile", body_file])

    def _get_parent(self, node):
        return self.hg_out(
            ["log", "-T", "{node}", "-r", "parents(%s)" % node], split=False
        )

    def finalize(self, commits):
        """Rebase stack children commits if needed."""
        # Currently we do all rebases in `amend_commit` if the evolve extension
        # is not installed.

        if not self.use_evolve:
            return

        parent = None
        for commit in commits:
            commit_parent = self._get_parent(commit["node"])
            if parent and parent["node"] not in commit_parent:
                self.rebase_commit(commit, parent)
                (rev, node) = self._get_successor(commit["node"])
                if rev and node:
                    self._refresh_commit(commit, node, rev)

            parent = commit

    def amend_commit(self, commit, commits):
        updated_body = "%s\n%s" % (commit["title"], commit["body"])
        current_body = self.hg_out(
            ["log", "-T", "{desc}", "-r", commit["node"]], split=False
        )
        if current_body == updated_body:
            logger.debug("not amending commit %s, unchanged", commit["name"])
            return False

        # Find our position in the stack.
        parent_node = None
        first_child = None
        is_parent = True
        for c in commits:
            if c["node"] == commit["node"]:
                is_parent = False
            elif is_parent:
                parent_node = c["node"]
            elif not first_child:
                first_child = c
                break

        # Track children of this commit which aren't part of the stack.
        stack_nodes = [c["node"] for c in commits]
        non_stack_children = [
            n
            for n in self.hg_log("children(%s)" % commit["node"])
            if n not in stack_nodes
        ]

        if self.use_evolve:
            # If evolve is installed this is trivial.
            self._amend_commit_body(commit["node"], updated_body)

        elif not first_child and not non_stack_children:
            # Without evolve things are much more exciting.

            # If there's no children we can just amend.
            self._amend_commit_body(commit["node"], updated_body)

            # This should always result in an amended node, but we need to be
            # extra careful not to strip the original node.
            amended_node = self.hg_log(".", split=False)
            if amended_node != commit["node"]:
                self.strip_nodes.append(commit["node"])

        else:
            # Brace yourself.  We need to create a dummy commit with the same parent as
            # the commit, rebase a copy of the commit onto the dummy, amend, rebase the
            # amended commit back onto the original parent, rebase the children onto
            # that, then strip the original commit and dummy commits.

            # Find a parent for the first commit in the stack
            if not parent_node:
                parent_node = self.hg_log("parents(%s)" % commit["node"])[0]

            # Create the dummy commit.
            self.checkout(parent_node)
            self.hg(
                ["commit"]
                + ["--message", "dummy"]
                + ["--config", "ui.allowemptycommit=true"]
            )
            dummy_node = self.hg_log(".", split=False)

            # Rebase a copy of this commit onto the dummy.
            self.hg(["rebase", "--keep", "--rev", commit["node"], "--dest", dummy_node])
            rebased_node = self.hg_log("children(.)", split=False)

            # Amend.
            self._amend_commit_body(rebased_node, updated_body)
            amended_node = self.hg_log(".", split=False)

            # Rebase back onto parent
            self.hg(["rebase"] + ["--source", amended_node] + ["--dest", parent_node])
            rebased_amended_node = self.hg_log(".", split=False)

            # Update the commit object now.
            original_node = commit["node"]
            self._refresh_commit(commit, rebased_amended_node)

            # Note what nodes need to be stripped when we're all done.
            self.strip_nodes.extend([original_node, dummy_node])

            # And rebase children.
            if first_child:
                self.rebase_commit(first_child, commit)

        # Ensure our view of the stack is up to date.
        self.refresh_commit_stack(commits)

        # Commits that aren't part of the stack need to be re-parented.
        for node in non_stack_children:
            self.hg(["rebase", "--source", node, "--dest", commit["node"]])

    def rebase_commit(self, source_commit, dest_commit):
        self.hg(
            ["rebase"]
            + ["--source", source_commit["node"]]
            + ["--dest", dest_commit["node"]]
        )

    def check_commits_for_submit(
        self, commits, validate_reviewers=True, require_bug=True
    ):
        # 'Greatest Common Ancestor'/'Merge Base' should be included in the revset.
        ancestor = self.hg_log("ancestor(%s)" % self.revset, split=False)
        if ancestor not in [c["node"] for c in commits]:
            raise Error(
                "Non-linear commit stack (common ancestor %s missing from stack)"
                % short_node(ancestor)
            )

        # Merge base needs to have a public parent.
        parent_phases = self.hg_out(
            ["log", "-T", "{phase} {node}\n", "-r", "parents(%s)" % ancestor]
        )
        for parent in parent_phases:
            (phase, node) = parent.split(" ", 1)
            if phase != "public":
                logger.warning(
                    "%s is based off non-public commit %s",
                    short_node(ancestor),
                    short_node(node),
                )

        # Can't submit merge requests.
        if self.hg_log("%s and merge()" % self.revset):
            raise Error("Commit stack contains a merge commit")

        # mq isn't currently supported.
        if self.has_mq and self.hg_out(["qapplied"]):
            raise Error("Found patches applied with `mq`, unable to continue")

        # Uncommitted changes can interact poorly when we update to a different commit.
        status = self._status()
        if status["T"]:
            err = [
                "%s uncommitted change%s present"
                % (len(status["T"]), " is" if len(status["T"]) == 1 else "s are")
            ]
            err.extend(
                [
                    "Commit changes, or use `hg shelve` to store uncommitted changes,",
                    "restoring with `hg unshelve` after submission",
                ]
            )
            if not self.has_shelve:
                err.append("You can enable the shelve extension via `hg config --edit`")
            raise Error("\n".join(err))

        super().check_commits_for_submit(
            commits, validate_reviewers=validate_reviewers, require_bug=require_bug
        )

    def _get_file_modes(self, commit):
        """Get modes of the modified files."""
        old_modes = self.hg_out(
            ["manifest", "-T", "{mode} {path}\n", "-r", commit["parent"]],
            never_log=True,
        )
        new_modes = self.hg_out(
            ["manifest", "-T", "{mode} {path}\n", "-r", commit["node"]], never_log=True
        )
        file_modes = {}
        for s_info in old_modes:
            info = s_info.split(None, 1)
            old_mode = info[0]
            if len(old_mode) == 3:
                # Windows responds with a 3-digit number
                old_mode = "100{}".format(old_mode)
            file_modes[info[1]] = dict(old_mode=old_mode)

        for s_info in new_modes:
            info = s_info.split(None, 1)
            new_mode = info[0]
            if len(new_mode) == 3:
                # Windows responds with a 3-digit number
                new_mode = "100{}".format(new_mode)
            file_modes.setdefault(info[1], dict())
            file_modes[info[1]]["new_mode"] = new_mode

        return file_modes

    def get_diff(self, commit):
        """Create a Diff object containing all changes for this commit."""
        commit["parent"] = self._get_parent(commit["node"])
        file_modes = self._get_file_modes(commit)

        # Get changed files.
        file_divider = "--%s--" % uuid.uuid4().hex
        type_divider = "--%s--" % uuid.uuid4().hex
        all_files = self.hg_out(
            ["log"]
            + ["-r", commit["node"]]
            + [
                "-T",
                "{{join(file_adds, '{file_divider}')}}{type_divider}"
                "{{join(file_dels, '{file_divider}')}}{type_divider}"
                "{{join(file_mods, '{file_divider}')}}{type_divider}"
                "{{join(file_copies, '{file_divider}')}}".format(
                    file_divider=file_divider, type_divider=type_divider
                ),
            ],
            split=False,
        )
        fn_adds, fn_dels, fn_mods, fn_renames_str = [
            fn.split(file_divider) for fn in all_files.split(type_divider)
        ]
        changes = []

        fn_renames = []
        fn_renamed = []  # collection of `old_fn`.
        # fn_renames_str is returning "new filename (old filename)"
        for fn in fn_renames_str:
            m = re.match(r"(?P<filename>[^(]+) \((?P<old_filename>[^)]+)\)", fn)
            if m:
                old_fn = m.group("old_filename")
                new_fn = m.group("filename")
                # A file can be mved only once.
                is_move = old_fn in fn_dels and old_fn not in fn_renamed
                changes.append(
                    dict(
                        fn=new_fn,
                        old_fn=old_fn,
                        kind="R" if is_move else "C",
                        func=self._change_mod,
                    )
                )
                fn_renames.append((new_fn, old_fn))
                fn_renamed.append(old_fn)

        # remove renames from adds and dels
        fn_adds = [fn for fn in fn_adds if fn and fn not in [c[0] for c in fn_renames]]
        fn_dels = [fn for fn in fn_dels if fn and fn not in [c[1] for c in fn_renames]]

        # remove empty string from mods
        fn_mods = [fn for fn in fn_mods if fn]

        changes.extend([dict(fn=fn, kind="A", func=self._change_add) for fn in fn_adds])
        changes.extend([dict(fn=fn, kind="D", func=self._change_del) for fn in fn_dels])
        changes.extend([dict(fn=fn, kind="M", func=self._change_mod) for fn in fn_mods])

        # Create changes.
        diff = Diff()
        for c in changes:
            change = diff.change_for(c["fn"])
            old_fn = c["old_fn"] if "old_fn" in c else c["fn"]
            c["func"](change, c["fn"], old_fn, commit["parent"], commit["node"])
            a_mode = (
                file_modes[old_fn]["old_mode"]
                if old_fn in file_modes and "old_mode" in file_modes[old_fn]
                else "000000"
            )
            b_mode = (
                file_modes[c["fn"]]["new_mode"]
                if c["fn"] in file_modes and "new_mode" in file_modes[c["fn"]]
                else "000000"
            )
            diff.set_change_kind(change, c["kind"], a_mode, b_mode, old_fn, c["fn"])

        return diff

    def hg_cat(self, fn, node):
        return self.hg_out(["cat", "-r", node, fn], split=False, expect_binary=True)

    def _file_size(self, fn, rev):
        """Get the file size of the file."""
        return int(
            self.hg_out(
                ["files", "-v", "-r", rev, os.path.join(self.path, fn), "-T", "{size}"],
                split=False,
            )
        )

    def _get_file_meta(self, fn, rev):
        """Collect information about the file."""
        binary = False
        body = self.hg_cat(fn, rev)
        meta = dict(mime="TEXT", bin_body=body)

        meta["file_size"] = self._file_size(fn, rev)
        if meta["file_size"] > MAX_TEXT_SIZE:
            binary = True

        if not binary:
            if b"\0" in body:
                binary = True

        if not binary:
            try:
                body = str(body, "utf-8")
            except UnicodeDecodeError:
                binary = True

        meta["body"] = body
        meta["binary"] = binary

        if binary:
            meta["mime"] = mimetypes.guess_type(fn)[0] or ""

        return meta

    def _change_add(self, change, fn, _, parent, node):
        """Create a change about adding a file to the commit."""
        meta = self._get_file_meta(fn, node)
        if meta["binary"]:
            self._change_set_binary(change, "", meta["bin_body"], "", meta["mime"])
        else:
            lines = meta["body"].splitlines(keepends=True)
            lines = ["+%s" % l for l in lines]

            if lines and not lines[-1].endswith("\n"):
                lines[-1] = "{}\n".format(lines[-1])
                lines.append("\\ No newline at end of file\n")

            self._change_create_hunk(
                change, fn, lines, meta["file_size"], parent, node, 0, 1, 0, len(lines)
            )

    def _change_del(self, change, fn, _, parent, node):
        """Create a change about deleting a file from the commit."""
        meta = self._get_file_meta(fn, parent)
        if meta["binary"]:
            self._change_set_binary(change, meta["bin_body"], "", meta["mime"], "")
        else:
            lines = meta["body"].splitlines(keepends=True)
            lines = ["-%s" % l for l in lines]

            if lines and not lines[-1].endswith("\n"):
                lines[-1] = "{}\n".format(lines[-1])
                lines.append("\\ No newline at end of file\n")

            self._change_create_hunk(
                change, fn, lines, meta["file_size"], parent, node, 1, 0, len(lines), 0
            )

    def _change_mod(self, change, fn, old_fn, parent, node):
        """Create a change about modified file in the commit."""
        a_meta = self._get_file_meta(old_fn, parent)
        b_meta = self._get_file_meta(fn, node)
        if a_meta["binary"] or b_meta["binary"]:
            self._change_set_binary(
                change,
                a_meta["bin_body"],
                b_meta["bin_body"],
                a_meta["mime"],
                b_meta["mime"],
            )
        else:
            file_size = max(a_meta["file_size"], b_meta["file_size"])
            if a_meta["body"] == b_meta["body"]:
                lines = a_meta["body"].splitlines(True)
                lines = [" %s" % l for l in lines]
                old_off = new_off = 1
                old_len = new_len = len(lines)
            else:
                if self.args.lesscontext or file_size > MAX_CONTEXT_SIZE:
                    context_size = 100
                else:
                    context_size = MAX_CONTEXT_SIZE

                # Monitoring if parsing the diff is not slowing down the submit process.
                start = time.process_time()
                git_diff = self.hg_out(
                    ["diff", "--git", "-U%s" % context_size, "-r", parent, fn],
                    expect_binary=True,
                )
                git_diff = str(git_diff, "utf-8").splitlines(keepends=True)
                # Remove all info above the header
                lines = []
                found_hdr = False
                for line in git_diff:
                    if not found_hdr:
                        found_hdr = line.startswith("@@")

                    if found_hdr:
                        lines.append(line)

                if file_size > MAX_CONTEXT_SIZE / 2:
                    logger.debug(
                        "Splitting the diff (size: %s) took %ss",
                        file_size,
                        time.process_time() - start,
                    )

                old_off, new_off, old_len, new_len = Diff.parse_git_diff(lines.pop(0))

            self._change_create_hunk(
                change,
                fn,
                lines,
                file_size,
                parent,
                node,
                old_off,
                new_off,
                old_len,
                new_len,
            )

    def _change_set_binary(self, change, a_body, b_body, a_mime, b_mime):
        """Sets `Change` object as binary."""
        change.binary = True
        change.uploads = [
            {"type": "old", "value": a_body, "mime": a_mime, "phid": None},
            {"type": "new", "value": b_body, "mime": b_mime, "phid": None},
        ]
        if a_mime.startswith("image/") or b_mime.startswith("image/"):
            change.file_type = Diff.FileType("IMAGE")
        else:
            change.file_type = Diff.FileType("BINARY")

    def _change_create_hunk(
        self,
        change,
        fn,
        lines,
        file_size,
        parent,
        node,
        old_off,
        new_off,
        old_len,
        new_len,
    ):
        """Creates a hunk for the Change object.

        Collects some stats about the diff, and generates the corpus we
        want to send to the Phabricator.
        """
        old_eof_newline = True
        new_eof_newline = True
        old_line = " "
        corpus = "".join(lines)
        for line in lines:
            if line.endswith("No newline at end of file\n"):
                if old_line[0] != "+":
                    old_eof_newline = False
                if old_line[0] != "-":
                    new_eof_newline = False
            old_line = line

        change.hunks = [
            Diff.Hunk(
                old_off=old_off,
                old_len=old_len,
                new_off=new_off,
                new_len=new_len,
                old_eof_newline=old_eof_newline,
                new_eof_newline=new_eof_newline,
                added=sum(1 for l in lines if l[0] == "+"),
                deleted=sum(1 for l in lines if l[0] == "-"),
                corpus=corpus,
            )
        ]
        change.file_type = Diff.FileType("TEXT")


#
# Git
#


class Git(Repository):
    def __init__(self, path):
        dot_path = os.path.join(path, ".git")
        if not os.path.exists(dot_path):
            raise ValueError("%s: not a git repository" % path)

        logger.debug("found git repo in %s", path)

        self._git = GIT_COMMAND[:]
        if not which_path(self._git[0]):
            raise Error("Failed to find 'git' executable")

        # `self._env` is a dict representing environment used in all git commands.
        self._env = os.environ.copy()

        if os.path.isfile(dot_path):
            # We're working from a worktree. Let's find the dot_path directory.
            dot_path = self.git_out(
                ["rev-parse", "--git-common-dir"], path=path, split=False
            )

        super().__init__(path, dot_path)

        self.vcs = "git"
        self._cinnabar_installed = None
        self.revset = None
        self.extensions = []
        self.branch = None

    @property
    def is_cinnabar_installed(self):
        """Check if Cinnabar extension is callable."""
        if self._cinnabar_installed is None:
            # Unfortunately we cannot use --list-cmds as it requires git v2.18+

            # Normally cinnabar will be listed in the 'External commands' section.
            for line in self.git_out(["help", "--all"]):
                if re.search(r"^\s+cinnabar\b", line):
                    self._cinnabar_installed = True
                    break

            # Cinnabar might be installed in git's exec-path, which won't be
            # included in the `git help --all` output, nor is it necessarily
            # on the path.
            if not self._cinnabar_installed:
                exec_path = Path(self.git_out(["--exec-path"], split=False))
                if (exec_path / "git-cinnabar").exists():
                    self._cinnabar_installed = True

            # Finally check on the system path.
            if not self._cinnabar_installed:
                self._cinnabar_installed = which("git-cinnabar") is not None

        return self._cinnabar_installed

    @property
    def is_cinnabar_required(self):
        """Check if local VCS is different than the remote one."""
        return self.vcs != self.phab_vcs

    def _hg_to_git(self, node):
        """Convert Mercurial hashtag to Git."""
        if not self.is_cinnabar_required:
            return None

        return self.git_out(["cinnabar", "hg2git", node], split=False)

    def _git_to_hg(self, node):
        """Convert Git hashtag to Mercurial."""
        if not self.is_cinnabar_required:
            return None

        hg_node = self.git_out(["cinnabar", "git2hg", node], split=False)
        return hg_node if hg_node != NULL_SHA1 else None

    def get_public_node(self, node):
        """Return a Mercurial node if Cinnabar is required."""
        public_node = node
        if self.is_cinnabar_required:
            hg_node = self._git_to_hg(node)
            if hg_node:
                public_node = hg_node

        return public_node

    def is_worktree_clean(self):
        return all(
            [l.startswith("?? ") for l in self.git_out(["status", "--porcelain"])]
        )

    def before_submit(self):
        # Store current branch (fails if HEAD in detached state)
        try:
            self.branch = self._get_current_head()
        except Exception:
            raise Error(
                "Git failed to read the branch name.\n"
                "The repository is in a detached HEAD state.\n"
                "You need to run the *git checkout <branch-name>* command."
            )

    @classmethod
    def is_repo(cls, path):
        """Quick check for repository at specified path."""
        return os.path.exists(os.path.join(path, ".git"))

    def git(self, command, **kwargs):
        """Call git from the repository path."""
        check_call(self._git + command, cwd=self.path, env=self._env, **kwargs)

    def git_out(self, command, path=None, extra_env=None, **kwargs):
        """Call git from the repository path and return the result."""
        env = dict(self._env)
        if extra_env:
            env.update(extra_env)
        return check_output(
            self._git + command, cwd=path or self.path, env=env, **kwargs
        )

    def cleanup(self):
        self.git(["gc", "--auto", "--quiet"])
        if self.branch:
            self.checkout(self.branch)

    def _find_branches_to_rebase(self, commits):
        """Create a list of branches to rebase."""
        branches_to_rebase = dict()
        for commit in commits:
            if commit["node"] == commit["orig-node"]:
                continue
            branches = self.git_out(["branch", "--contains", commit["orig-node"]])
            for branch in branches:
                if branch.startswith("* ("):
                    # Omit `* (detached from {SHA1})`
                    continue

                branch = branch.lstrip("* ")
                # Rebase the branch to the last commit from the stack .
                branches_to_rebase[branch] = [commit["node"], commit["orig-node"]]

        return branches_to_rebase

    def finalize(self, commits):
        """Rebase all branches based on changed commits from the stack."""
        branches_to_rebase = self._find_branches_to_rebase(commits)

        for branch, nodes in branches_to_rebase.items():
            self.checkout(branch)
            self._rebase(*nodes)

        self.checkout(self.branch)

    def refresh_commit_stack(self, commits):
        """Update revset and names of the commits."""
        for commit in commits:
            commit["name"] = short_node(commit["node"])
        self.revset = (commits[0]["node"], commits[-1]["node"])

        super().refresh_commit_stack(commits)

    def _cherry(self, command, remotes):
        """Run command and try all the remotes until success."""
        if not remotes:
            return self.git_out(command)

        for remote in remotes:
            logger.info('Determining the commit range using upstream "%s"', remote)

            try:
                response = self.git_out(command + [remote])
            except CommandError:
                continue

            return response

    def _get_first_unpublished_node(self):
        """Check which commits should be pushed and return the oldest one."""
        cherry = ["cherry", "--abbrev=12"]
        remotes = config.git_remote
        if self.args.upstream:
            remotes = self.args.upstream
        elif not remotes:
            remotes = self.git_out(["remote"])
            if len(remotes) > 1:
                logger.warning("!! Found multiple upstreams (%s).", ", ".join(remotes))

        unpublished = self._cherry(cherry, remotes)
        if unpublished is None:
            raise Error(
                "Unable to detect the start commit. Please provide its SHA-1 or\n"
                "specify the upstream branch with `--upstream <branch>`."
            )

        if not unpublished:
            return None

        if len(unpublished) > 100:
            raise Error(
                "Unable to create a stack with %s unpublished commits.\n\n"
                "This is usually the result of a failure to detect the correct "
                "remote repository.\nTry again with the `--upstream <upstream>` "
                "switch to specify the correct remote repository." % len(unpublished)
            )

        for line in unpublished:
            # `git cherry` is producing the output in reverse order - oldest
            # commit is the first one. That is the *opposite* of what we can find
            # in the documentation.
            if line.startswith("+"):
                return line.split("+ ")[1]
            else:
                logger.warning(
                    "!! Diff from commit %s found in upstream - omitting.",
                    line.split("- ")[1],
                )

    def set_args(self, args):
        """Store moz-phab command line args and set the revset."""
        super().set_args(args)

        git_config = parse_config(self.git_out(["config", "--list"], never_log=True))

        safe_options = []

        # Need to use the correct username.
        if "user.email" not in git_config:
            raise Error("user.email is not configured in your gitconfig")

        safe_options.extend(["-c", "user.email=%s" % git_config["user.email"]])

        if "user.name" in git_config:
            safe_options.extend(["-c", "user.name=%s" % git_config["user.name"]])

        if "cinnabar.helper" in git_config:
            self.extensions.append("cinnabar")
            safe_options.extend(
                ["-c", "cinnabar.helper=%s" % git_config["cinnabar.helper"]]
            )

        if self.args.safe_mode or config.safe_mode:
            # Ignore the user's Git config
            # To make Git not read the `~/.gitconfig` we need to temporarily change the
            # `$HOME` variable.
            self._env["HOME"] = ""
            self._env["XDG_CONFIG_HOME"] = ""
            self._git.extend(safe_options)

        if hasattr(self.args, "start_rev"):
            if self.args.start_rev == "(auto)":
                start = self._get_first_unpublished_node()
            else:
                start = self.args.start_rev

            if start is None:
                return None

            # We want inclusive range of commits if start commit is detected
            if self.args.start_rev == "(auto)":
                start = "%s^" % start

            self.revset = (start, self.args.end_rev)

    def _git_get_children(self, node):
        """Get commits SHA1 with their children.

        Args:
            node: The SHA1 of a node to check for all children

        Returns: A list of "aaaa bbbb cccc"" strings, where bbbb and cccc are
            SHA1 of direct children of aaaa
        """
        return self.git_out(["rev-list", "--all", "--children", "--not", "%s^@" % node])

    @staticmethod
    def _get_direct_children(node, rev_list):
        """ Return direct children of the commit.

        Args:
            node: The SHA1 of a node to check for direct children
            rev_list: A list of SHA1 strings - result of the _git_get_children method

        Returns: A list of SHA1 representing direct children of a commit
        """
        # Find the line containing the node to extract its commit's children
        for line in rev_list:
            if line.startswith(node):
                children = line.split(" ")
                children.remove(node)
                return children

        return []

    def _get_commits_info(self, start, end):
        """Log useful info about the commits within the desired range.

        Returns a list of strings
        An example of a list item:
            Tue, 22 Jan 2019 13:42:48 +0000
            Conduit User
            conduit@mozilla.bugs
            4912923
            b18312ffe929d3482f1d7b1e9716a1885c7a61b8
            5f161c70fef9e59d1966bab693a0a68a9336af80
            Update code references

            Fixes:
            $ moz-phab self-update
            > Failed to download update: HTTP Error 404: Not Found
        """
        boundary = "--%s--\n" % uuid.uuid4().hex
        log = self.git_out(
            [
                "log",
                "--reverse",
                "--ancestry-path",
                "--quiet",
                "--format=%aD%n%an%n%ae%n%p%n%T%n%H%n%s%n%n%b{}".format(boundary),
                "{}..{}".format(start, end),
            ],
            split=False,
            strip=False,
        )[: -len(boundary) - 1]
        return log.split("%s\n" % boundary)

    def _is_child(self, parent, node, rev_list):
        """Check if `node` is a direct or indirect child of the `parent`.

        Args:
            parent: The parent node whose children will be searched
            node: The string we check if it's in parent-child relation to the `parent`
            rev_list: A response from the git _git_get_children method - a list of
                "aaaa bbbb cccc"" strings, where "bbbb" and cccc" are SHA1 of direct
                children of "aaaa"

        Returns: a Boolean True if the `node` represents a child of the `parent`.
        """
        direct_children = self._get_direct_children(parent, rev_list)
        if node in direct_children:
            return True

        for child in direct_children:
            if self._is_child(child, node, rev_list):
                return True

        return False

    def commit_stack(self):
        """Collect all the info about commits."""
        if not self.revset:
            # No commits found to submit
            return None

        commits = []
        rev_list = None
        first_node = None
        for log_line in self._get_commits_info(*self.revset):
            if not log_line:
                continue

            (
                author_date,
                author_name,
                author_email,
                parents,
                tree_hash,
                node,
                desc,
            ) = log_line.split("\n", 6)
            desc = desc.splitlines()

            # Check if the commit is a child of the first one
            if not rev_list:
                rev_list = self._git_get_children(node)
                first_node = node
            elif not self._is_child(first_node, node, rev_list):
                raise Error(
                    "Commit %s is not a child of %s, unable to continue"
                    % (short_node(node), short_node(first_node))
                )

            # Check if commit has multiple parents, if so - raise an Error
            # We may push the merging commit if it's the first one
            parents = parents.split(" ")
            if node != first_node and len(parents) > 1:
                raise Error(
                    "Multiple parents found for commit %s, unable to continue"
                    % short_node(node)
                )

            commits.append(
                {
                    "name": short_node(node),
                    "node": node,
                    "orig-node": node,
                    "title": desc[0],
                    "title-preview": desc[0],
                    "body": "\n".join(desc[1:]).rstrip(),
                    "bug-id": None,
                    "reviewers": dict(request=[], granted=[]),
                    "rev-id": None,
                    "parent": parents[0],
                    "tree-hash": tree_hash,
                    "author-date": author_date,
                    "author-name": author_name,
                    "author-email": author_email,
                }
            )

        return commits

    def is_node(self, node):
        try:
            node_type = self.git_out(
                ["cat-file", "-t", node], split=False, stderr=subprocess.STDOUT
            )
        except CommandError:
            return False

        return node_type == "commit"

    def check_node(self, node):
        """Check if the node exists.

        Calls `hg2git` if node is not found and cinnabar extension is installed.

        Returns a node if found.

        Raises NotFoundError if not found.
        """
        hashtag = node
        if not self.is_node(hashtag):
            if self.is_cinnabar_required and self.is_cinnabar_installed:
                hashtag = self._hg_to_git(hashtag)
                if hashtag == "0" * 40:
                    # hashtag is not found via hg2git
                    raise NotFoundError(
                        "Mercurial SHA1 not found by the cinnabar extension."
                    )
                elif not self.is_node(hashtag):
                    # the found hashtag is not a valid node in the repository.
                    raise NotFoundError(
                        "Mercurial SHA1 detected, but commit not found in the "
                        "repository."
                    )
            else:
                raise NotFoundError("Cinnabar extension not enabled.")

        return hashtag

    def checkout(self, node):
        self.git(["checkout", "--quiet", node])

    def commit(self, body, author=None, author_date=None):
        """Commit the changes in the working directory."""
        commands = ["commit", "-a"]
        if author:
            commands.append('--author="%s"' % author)

        if author_date:
            commands.append('--date="%s"' % author_date)

        with temporary_file(body) as temp_f:
            commands += ["-F", temp_f]
            self.git(commands)

    def before_patch(self, node, name):
        """Prepare repository to apply the patches.

        Args:
            node - SHA1 of the base commit
            name - name of the branch to be created
        """
        is_detached_head = self.args.no_branch and node
        if is_detached_head and not self.args.yes:
            res = prompt(
                "Switching to the 'detached HEAD' state. Do you wish to continue?",
                ["Yes", "No"],
            )
            if res == "No":
                sys.exit(1)

        if is_detached_head and self.args.yes:
            logger.warning("Switching to the 'detached HEAD' state.")

        if is_detached_head:
            logger.warning(
                "If you want to create a new branch to retain created commits,\n"
                "you may do so by calling `git checkout -b <new-branch-name>`"
            )

        # Checkout sha
        if node:
            with wait_message("Checking out %s.." % short_node(node)):
                self.checkout(node)
            logger.info("Checked out %s", short_node(node))

        if name and not self.args.no_branch:
            branches = self.git_out(["branch", "--list", "%s*" % name])
            branches = [re.sub("[ *]", "", b) for b in branches]
            branch_name = name
            i = 0
            while branch_name in branches:
                i += 1
                branch_name = "%s_%s" % (name, i)

            self.git(["checkout", "-q", "-b", branch_name])
            logger.info("Created branch %s", branch_name)

    def apply_patch(self, diff, body, author, author_date):
        # apply the patch as a binary file to ensure the correct line endings
        # is used.
        with temporary_binary_file(diff.encode("utf8")) as patch_file:
            self.git(["apply", "--index", patch_file])
        self.commit(body, author, author_date)

    def _get_current_head(self):
        """Return current's HEAD symbolic link."""
        symbolic = self.git_out(["symbolic-ref", "HEAD"], split=False)
        return symbolic.split("refs/heads/")[1]

    def _get_current_hash(self):
        """Return the SHA1 of the current commit."""
        return self._revparse("HEAD")

    def _revparse(self, branch):
        """Return the SHA1 of given branch."""
        return self.git_out(["rev-parse", branch], split=False)

    def _commit_tree(
        self, parent, tree_hash, message, author_name, author_email, author_date
    ):
        """Prepare and run `commit-tree` command.

        Creates a new commit for the tree_hash.
        Args:
            parent: SHA1 of the parent commit
            tree_hash: SHA1 of the tree_hash to use for the commit
            message: commit message

        Returns:
            str: SHA1 of the new commit.
        """
        with temporary_file(message) as message_file:
            return self.git_out(
                ["commit-tree", "-p", parent, "-F", message_file, tree_hash],
                split=False,
                extra_env={
                    "GIT_AUTHOR_NAME": author_name,
                    "GIT_AUTHOR_EMAIL": author_email,
                    "GIT_AUTHOR_DATE": author_date,
                },
            )

    def amend_commit(self, commit, commits):
        """Amend the commit with an updated message.

        Changing commit's message changes also its SHA1.
        All the children within the stack and branches are then updated
        to keep the history.

        Args:
            commit: Information about the commit to be amended
            commits: List of commits within the stack
        """
        updated_body = "%s\n%s" % (commit["title"], commit["body"])

        current_body = self.git_out(
            ["show", "-s", "--format=%s%n%b", commit["node"]], split=False
        )
        if current_body == updated_body:
            logger.debug("not amending commit %s, unchanged", commit["name"])
            return

        # Create a new commit with the updated body.
        new_parent_sha = self._commit_tree(
            commit["parent"],
            commit["tree-hash"],
            updated_body,
            commit["author-name"],
            commit["author-email"],
            commit["author-date"],
        )

        # Update commit info
        commit["node"] = new_parent_sha
        # Update parent for all the children of the `commit` within the stack
        has_children = False
        for c in commits:
            if not has_children:
                # Find the amended commit info in the list of all commits in the stack.
                # Next commits are children of this one.
                has_children = c == commit
                continue

            # Update parent information and create a new commit
            c["parent"] = new_parent_sha
            new_parent_sha = self._commit_tree(
                new_parent_sha,
                c["tree-hash"],
                "%s\n%s" % (c["title"], c["body"]),
                c["author-name"],
                c["author-email"],
                c["author-date"],
            )
            c["node"] = new_parent_sha

    def rebase_commit(self, source_commit, dest_commit):
        self._rebase(dest_commit["node"], source_commit["node"])

    def _rebase(self, newbase, upstream):
        self.git(["rebase", "--quiet", "--onto", newbase, upstream])

    def _file_size(self, blob):
        return int(self.git_out(["cat-file", "-s", blob], split=False))

    def _cat_file(self, blob):
        return self.git_out(["cat-file", "blob", blob], split=False, expect_binary=True)

    def _parse_diff_change(self, raw, diff):
        """Parse the changes provided in raw `git` response.

        Returns a Diff.Change object.
        """
        # find changed path
        paths = raw.split("\0")
        fields = paths.pop(0)
        [a_mode, b_mode, a_blob, b_blob, kind_l] = fields.split(" ")

        # Figure out what paths to use
        if len(paths) == 2:
            [a_path, b_path] = paths
        else:
            a_path = b_path = paths[0]

        # create a Change object
        change = diff.change_for(b_path)

        # Extract the bodies of blobs to compare
        if a_blob == NULL_SHA1:
            a_blob, a_body, a_size = None, b"", 0
        else:
            a_body = self._cat_file(a_blob)
            a_size = self._file_size(a_blob)

        if b_blob == NULL_SHA1:
            b_blob, b_body, b_size = None, b"", 0
        else:
            b_body = self._cat_file(b_blob)
            b_size = self._file_size(b_blob)

        file_size = max(a_size, b_size)

        # Detect if we're binary, and generate a unified diff
        if b"\0" in a_body or b"\0" in b_body or file_size > MAX_TEXT_SIZE:
            change.binary = True

        if not change.binary and a_body:
            try:
                a_body = str(a_body, "utf-8")
            except UnicodeDecodeError:
                change.binary = True

        if not change.binary and b_body:
            try:
                b_body = str(b_body, "utf-8")
            except UnicodeDecodeError:
                change.binary = True

        if change.binary:
            a_mime = mimetypes.guess_type(a_path)[0] or ""
            b_mime = mimetypes.guess_type(b_path)[0] or ""
            change.uploads = [
                {"type": "old", "value": a_body, "mime": a_mime, "phid": None},
                {"type": "new", "value": b_body, "mime": b_mime, "phid": None},
            ]
            if a_mime.startswith("image/") or b_mime.startswith("image/"):
                change.file_type = Diff.FileType("IMAGE")
            else:
                change.file_type = Diff.FileType("BINARY")
        else:
            # We can only diff changed blobs.
            if a_blob == b_blob:
                # No changes in the file contents.
                lines = a_body.splitlines(True)
                lines = [" %s" % l for l in lines]
                old_off = new_off = 1
                old_len = new_len = len(lines)
            elif a_blob is None:
                # The file is created.
                lines = b_body.splitlines(True)
                lines = ["+%s" % l for l in lines]
                new_len = len(lines)
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] = "{}\n".format(lines[-1])
                    lines.append("\\ No newline at end of file\n")

                old_off = 0
                new_off = 1
                old_len = 0
            elif b_blob is None:
                # The file is removed.
                lines = a_body.splitlines(True)
                lines = ["-%s" % l for l in lines]
                old_len = len(lines)
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] = "{}\n".format(lines[-1])
                    lines.append("\\ No newline at end of file\n")

                old_off = 1
                new_off = 0
                new_len = 0
            else:
                # There are changes in the file.
                if self.args.lesscontext or file_size > MAX_CONTEXT_SIZE:
                    context_size = 100
                else:
                    context_size = MAX_CONTEXT_SIZE

                diff_args = [
                    "diff",
                    "--submodule=short",
                    "--no-ext-diff",
                    "--no-color",
                    "--no-textconv",
                    "-U%s" % context_size,
                    a_blob,
                    b_blob,
                ]
                git_diff = self.git_out(diff_args, expect_binary=True)
                git_diff = str(git_diff, "utf-8").splitlines(keepends=True)
                lines = git_diff[4:]
                old_off, new_off, old_len, new_len = Diff.parse_git_diff(lines.pop(0))

            # Collect some stats about the diff, and generate the corpus we
            # want to send to Phabricator.
            old_eof_newline = True
            new_eof_newline = True
            old_line = " "
            corpus = "".join(lines)
            for line in lines:
                if line.endswith("No newline at end of file\n"):
                    if old_line[0] != "+":
                        old_eof_newline = False
                    if old_line[0] != "-":
                        new_eof_newline = False
                old_line = line

            change.hunks = [
                diff.Hunk(
                    old_off=old_off,
                    old_len=old_len,
                    new_off=new_off,
                    new_len=new_len,
                    old_eof_newline=old_eof_newline,
                    new_eof_newline=new_eof_newline,
                    added=sum(1 for l in lines if l[0] == "+"),
                    deleted=sum(1 for l in lines if l[0] == "-"),
                    corpus=corpus,
                )
            ]
            change.file_type = diff.FileType("TEXT")

        diff.set_change_kind(change, kind_l[0], a_mode, b_mode, a_path, b_path)

        return change

    def get_diff(self, commit):
        """Create a Diff object with changes."""
        raw = self.git_out(
            [
                "diff-tree",
                "-r",
                "--raw",
                "-z",
                "-M",
                "-C",
                "--no-abbrev",
                commit["node"],
            ],
            split=False,
        )

        diff = Diff()
        for raw_change in raw[:-1].split("\0:")[1:]:
            self._parse_diff_change(raw_change, diff)

        return diff

    def check_vcs(self):
        try:
            return super().check_vcs()
        except Error:
            if not self.is_cinnabar_installed:
                logger.error(
                    "Git Cinnabar extension is required to work on this repository."
                )
                raise

        return True


#
# Commit helpers
#


def parse_arc_diff_rev(body):
    m = ARC_DIFF_REV_RE.search(body)
    return m.group(1) if m else None


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


def strip_differential_revision(body):
    return ARC_DIFF_REV_RE.sub("", body).rstrip()


def strip_depends_on(body):
    return DEPENDS_ON_RE.sub("", body).rstrip()


def amend_revision_url(body, new_url):
    """Append or replace the Differential Revision URL in a commit body."""
    body = strip_differential_revision(body)
    if body:
        body += "\n"
    body += "\nDifferential Revision: %s" % new_url
    return body


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


def has_arc_rejections(body):
    return all(r.search(body) for r in ARC_REJECT_RE_LIST)


def morph_blocking_reviewers(commits):
    """Automatically fix common typo by replacing r!user with r=user!"""

    def morph_reviewer(matchobj):
        if matchobj.group(1) == "r!":
            nick = matchobj.group(2)

            # strip trailing , so we can put it back later
            if nick.endswith(","):
                suffix = ","
                nick = nick[:-1]
            else:
                suffix = ""

            # split on comma to support r!a,b -> r=a!,b!
            return "r=%s%s" % (
                ",".join(["%s!" % n.rstrip("!") for n in nick.split(",")]),
                suffix,
            )
        else:
            return matchobj.group(0)

    for commit in commits:
        commit["title"] = BLOCKING_REVIEWERS_RE.sub(morph_reviewer, commit["title"])


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


def update_commit_title_previews(commits):
    """Update title-preview from commit metadata for all commits in stack"""
    for commit in commits:
        commit["title-preview"] = build_commit_title(commit)


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


def show_commit_stack(
    commits, validate=True, ignore_reviewers=False, show_rev_urls=False
):
    """Log the commit stack in a human readable form."""

    # keep output in columns by sizing the action column to the longest rev + 1 ("D")
    max_len = (
        max(len(c.get("rev-id", "") or "") for c in commits) + 1 if commits else ""
    )
    action_template = "(%" + str(max_len) + "s)"

    if validate:
        # preload all revisions
        ids = [int(c["rev-id"]) for c in commits if c.get("rev-id")]
        if ids:
            with wait_message("Loading existing revisions..."):
                conduit.get_revisions(ids=ids)

    for commit in reversed(commits):
        change_bug_id = False
        is_author = True
        revision = None

        if commit.get("rev-id"):
            action = action_template % ("D" + commit["rev-id"])
            if validate:
                revisions = conduit.get_revisions(ids=[int(commit["rev-id"])])
                if len(revisions) > 0:
                    revision = revisions[0]

                    # Check if target bug ID is the same as in the Phabricator revision
                    change_bug_id = (
                        "bugzilla.bug-id" in revision["fields"]
                        and revision["fields"]["bugzilla.bug-id"]
                        and (commit["bug-id"] != revision["fields"]["bugzilla.bug-id"])
                    )

                    # Check if comandeering is required
                    whoami = conduit.whoami()
                    if "authorPHID" in revision["fields"] and (
                        revision["fields"]["authorPHID"] != whoami["phid"]
                    ):
                        is_author = False
        else:
            action = action_template % "New"

        logger.info("%s %s %s", action, commit["name"], commit["title-preview"])
        if validate:
            if change_bug_id:
                logger.warning(
                    "!! Bug ID in Phabricator revision will be changed from %s to %s",
                    revision["fields"]["bugzilla.bug-id"],
                    commit["bug-id"],
                )

            if not is_author:
                logger.warning(
                    "!! You don't own this revision. Normally, you should only\n"
                    '   update revisions you own. You can "Commandeer" this\n'
                    "   revision from the web interface if you want to become\n"
                    "   the owner."
                )

            if not commit["bug-id"]:
                logger.warning("!! Missing Bug ID")

            if commit["bug-id-orig"] and commit["bug-id"] != commit["bug-id-orig"]:
                logger.warning(
                    "!! Bug ID changed from %s to %s",
                    commit["bug-id-orig"],
                    commit["bug-id"],
                )

            if (
                not ignore_reviewers
                and not commit["reviewers"]["granted"] + commit["reviewers"]["request"]
            ):
                logger.warning("!! Missing reviewers")

        if show_rev_urls and commit["rev-id"]:
            logger.warning("-> %s/D%s", conduit.repo.phab_url, commit["rev-id"])


def check_for_invalid_reviewers(reviewers):
    """Return a list of invalid reviewer names.

    Args:
        reviewers: A commit reviewers dict of granted and requested reviewers.
    """

    # Combine the lists of requested reviewers and granted reviewers.
    all_reviewers = []
    found_names = []
    for sublist in list(reviewers.values()):
        all_reviewers.extend(
            [
                normalise_reviewer(r, strip_group=False)
                for r in sublist
                if not r.startswith("#")
            ]
        )

    users = []
    if all_reviewers:
        users = conduit.get_users(all_reviewers)
        found_names = [
            normalise_reviewer(data["userName"], strip_group=False) for data in users
        ]

    # Group reviewers are represented by a "#" prefix
    all_groups = []
    found_groups = []
    for sublist in list(reviewers.values()):
        all_groups.extend(
            [
                normalise_reviewer(r, strip_group=False)
                for r in sublist
                if r.startswith("#")
            ]
        )

    if all_groups:
        groups = conduit.get_groups(all_groups)
        found_groups = ["#%s" % normalise_reviewer(group["name"]) for group in groups]

    all_reviewers.extend(all_groups)
    found_names.extend(found_groups)
    invalid = list(set(all_reviewers) - set(found_names))

    # Find users availability:
    unavailable = [
        dict(
            name=r["userName"],
            until=datetime.datetime.fromtimestamp(r["currentStatusUntil"]).strftime(
                "%Y-%m-%d %H:%M"
            ),
        )
        for r in users
        if r.get("currentStatus") == "away"
    ]

    # Find disabled users:
    disabled = [
        dict(name=r["userName"], disabled=True)
        for r in users
        if "disabled" in r.get("roles", [])
    ]
    return disabled + unavailable + [dict(name=r) for r in invalid]


#
# Arc helpers
#


def arc_out(
    args, cwd, stdin=None, log_output_to_console=True, stderr=None, search_error=None
):
    """arc wrapper that logs output to the console.

    Args:
        args: A list of arguments for the arc command.
        cwd: The directory to run the arc command in.
        stdin: Optionally overrides the standard input pipe to use for the arc
            subprocess call.
        log_output_to_console: Defaults to True.  If set to False, don't log the arc
            standard output to the console (stderr prints to console as normal).
        stderr: Standard error output stream
        search_error: a list of dicts passed to the check_output to parse the
            errors in the error response

    Returns:
        The list of lines arc printed to the console.
    """
    arc_output = check_output(
        ARC + args,
        cwd=cwd,
        split=False,
        stdin=stdin,
        stderr=stderr,
        search_error=search_error,
    )
    if logger.level != logging.DEBUG and log_output_to_console:
        logger.info(arc_output)
    return arc_output


def arc_call_conduit(api_method, api_call_args, cwd):
    """Run 'arc call-conduit' and return the JSON API call result.

    Args:
        api_method: The API method name to call, like 'differential.revision.edit'.
        api_call_args: JSON dict of call args to send.
        cwd: The directory to run the arc command in.

    Raises:
        ConduitAPIError if the API threw an error back at us.
    """
    arc_args = ["call-conduit", api_method]
    # 'arc call-conduit' only accepts its args from STDIN.
    with temporary_file(json.dumps(api_call_args)) as args_file:
        logger.debug("Arc stdin: %s", api_call_args)
        with open(args_file, "rb") as temp_f:
            output = arc_out(
                arc_args,
                cwd=cwd,
                stdin=temp_f,
                log_output_to_console=False,
                stderr=subprocess.STDOUT,
                search_error=ARC_CONDUIT_ERROR,
            )

    # We expect arc output to be a JSON. However, in DEBUG mode, a `--trace` is used and
    # the response becomes a multiline string with some messages in plain text.
    output = "\n".join([line for line in output.splitlines() if line.startswith("{")])
    maybe_error = parse_api_error(output)
    if maybe_error:
        raise ConduitAPIError(maybe_error)

    return json.loads(output)["response"]


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


def install_arc_if_required():
    if os.path.exists(ARC_COMMAND) and os.path.exists(LIBPHUTIL_PATH):
        return

    logger.info("Installing arc")
    logger.debug("arc command: %s", ARC_COMMAND)
    logger.debug("libphutil path: %s", LIBPHUTIL_PATH)

    check_call(["git", "clone", "--depth", "1", ARC_URL, ARC_PATH])
    check_call(["git", "clone", "--depth", "1", LIBPHUTIL_URL, LIBPHUTIL_PATH])


def arc_ping(cwd):
    """Sends a ping to the Phabricator server using `conduit.ping` API.

    Returns: `True` if no error, otherwise - `False`
    """
    try:
        arc_call_conduit("conduit.ping", {}, cwd)
    except ConduitAPIError as err:
        logger.error(err)
        return False
    except CommandError:
        return False
    return True


#
# "install-certificate" command
#


def install_certificate(repo, args):
    """Asks user to enter the Phabricator's certificate.

    The response is saved in the ~/.arcrc file."""
    logger.info(
        "LOGIN TO PHABRICATOR\nOpen this page in your browser and login "
        "to Phabricator if necessary:\n\n%s/conduit/login/\n",
        conduit.repo.phab_url,
    )
    token = prompt("Paste API Token from that page: ")
    conduit.save_api_token(token)


#
# "submit" Command
#


def arc_message(template_vars):
    """Build arc commit desc message from the template"""

    # Map `None` to an empty string.
    for name in list(template_vars.keys()):
        if template_vars[name] is None:
            template_vars[name] = ""

    # `depends_on` is optional.
    if "depends_on" not in template_vars:
        template_vars["depends_on"] = ""

    message = ARC_COMMIT_DESC_TEMPLATE.format(**template_vars)
    logger.debug("--- arc message\n%s\n---" % message)
    return message


def make_blocking(reviewers):
    return ["%s!" % r.rstrip("!") for r in reviewers]


def remove_duplicates(reviewers):
    """Remove all duplicate items from the list.

    Args:
        reviewers: list of strings representing IRC nicks of reviewers.

    Returns: list of unique reviewers.

    Duplicates with excalamation mark are preferred.
    """
    unique = []
    nicks = []
    for reviewer in reviewers:
        nick = reviewer.lower().strip("!")
        if reviewer.endswith("!") and nick in nicks:
            nicks.remove(nick)
            unique = [r for r in unique if r.lower().strip("!") != nick]

        if nick not in nicks:
            nicks.append(nick)
            unique.append(reviewer)

    return unique


def update_commits_from_args(commits, args):
    """Modify commit description based on args and configuration.

    Args:
        commits: list of dicts representing the commits in commit stack
        args: argparse.ArgumentParser object. In this function we're using
            following attributes:
            * reviewer - list of strings representing reviewers
            * blocker - list of string representing blocking reviewers
            * bug - an integer representing bug id in Bugzilla

    Command args always overwrite commit desc.
    Overwriting reviewers rules
    (The "-r" are loaded into args.reviewer and "-R" into args.blocker):
        a) Recognize `r=` and `r?` from commit message title.
        b) Modify reviewers only if `-r` or `-R` is used in call arguments.
        c) Add new reviewers using `r=`.
        d) Do not modify reviewers already provided in the title (`r?` or `r=`).
        e) Delete reviewer if `-r` or `-R` is used and the reviewer is not mentioned.

    If no reviewers are provided by args and an `always_blocking` flag is set
    change all reviewers in title to blocking.
    """
    # Build list of reviewers from args.
    reviewers = list(set(args.reviewer)) if args.reviewer else []
    # Order might be changed here `list(set(["one", "two"])) == ['two', 'one']`
    reviewers.sort()
    blockers = [r.rstrip("!") for r in args.blocker] if args.blocker else []
    # User might use "-r <nick>!" to provide a blocking reviewer.
    # Add all such blocking reviewers to blockers list.
    blockers += [r.rstrip("!") for r in reviewers if r.endswith("!")]
    blockers = list(set(blockers))
    blockers.sort()
    # Remove blocking reviewers from reviewers list
    reviewers_no_flag = [r.strip("!") for r in reviewers]
    reviewers = [r for r in reviewers_no_flag if r.lower() not in blockers]
    # Add all blockers to reviewers list. Reviewers list will contain a list
    # of all reviewers where blocking ones are marked with the "!" suffix.
    reviewers += make_blocking(blockers)
    reviewers = remove_duplicates(reviewers)
    lowercase_reviewers = [r.lower() for r in reviewers]
    lowercase_blockers = [r.lower() for r in blockers]

    for commit in commits:
        if reviewers:
            # Only the reviewers mentioned in command line args will remain.
            # New reviewers will be marked as "granted" (r=).
            granted = reviewers[:]
            requested = []

            # commit["reviewers"]["request|"] is a list containing reviewers
            # provided in commit's title with an r? mark.
            for reviewer in commit["reviewers"]["request"]:
                r = reviewer.strip("!")
                # check which request reviewers are provided also via -r
                if r.lower() in lowercase_reviewers:
                    requested.append(r)
                    granted.remove(r)
                # check which request reviewers are provided also via -R
                elif r.lower() in lowercase_blockers:
                    requested.append("%s!" % r)
                    granted.remove("%s!" % r)
        else:
            granted = commit["reviewers"].get("granted", [])
            requested = commit["reviewers"].get("request", [])

        commit["reviewers"] = dict(granted=granted, request=requested)
        if args.bug:
            # Bug ID command arg used.
            commit["bug-id"] = args.bug

    # Otherwise honour config setting to always use blockers
    if not reviewers and config.always_blocking:
        for commit in commits:
            commit["reviewers"] = dict(
                request=make_blocking(commit["reviewers"]["request"]),
                granted=make_blocking(commit["reviewers"]["granted"]),
            )

    update_commit_title_previews(commits)


def update_revision_description(transactions, commit, revision):
    # Appends differential.revision.edit transaction(s) to `transactions` if
    # updating the commit title and/or summary is required.

    if commit["title"] != revision["fields"]["title"]:
        transactions.append(dict(type="title", value=commit["title"]))

    # The Phabricator API will refuse the new summary value if we include the
    # "Differential Revision:" keyword in the summary body.
    local_body = strip_differential_revision(commit["body"]).strip()
    remote_body = strip_differential_revision(revision["fields"]["summary"]).strip()
    if local_body != remote_body:
        transactions.append(dict(type="summary", value=local_body))


def update_revision_bug_id(transactions, commit, revision):
    # Appends differential.revision.edit transaction(s) to `transactions` if
    # updating the commit bug-id is required.
    if commit["bug-id"] and commit["bug-id"] != revision["fields"]["bugzilla.bug-id"]:
        transactions.append(dict(type="bugzilla.bug-id", value=commit["bug-id"]))


def update_revision_reviewers(transactions, commit):
    # Appends differential.revision.edit transaction(s) to `transactions` to
    # set the reviewers.

    all_reviewing = commit["reviewers"]["request"] + commit["reviewers"]["granted"]

    # Find reviewers PHIDs
    all_reviewers = [r for r in all_reviewing if not r.startswith("#")]
    # preload all reviewers
    conduit.get_users(all_reviewers)
    reviewers = [r for r in all_reviewers if not r.endswith("!")]
    blocking_reviewers = [r.rstrip("!") for r in all_reviewers if r.endswith("!")]
    reviewers_phid = [user["phid"] for user in conduit.get_users(reviewers)]
    blocking_phid = [
        "blocking(%s)" % user["phid"] for user in conduit.get_users(blocking_reviewers)
    ]

    # Find groups PHIDs
    all_groups = [g for g in all_reviewing if g.startswith("#")]
    groups = [g for g in all_groups if not g.endswith("!")]
    blocking_groups = [g.rstrip("!") for g in all_groups if g.endswith("!")]
    # preload all groups
    conduit.get_groups(all_groups)
    groups_phid = [group["phid"] for group in conduit.get_groups(groups)]
    bl_groups_phid = [
        "blocking(%s)" % group["phid"] for group in conduit.get_groups(blocking_groups)
    ]

    all_reviewing_phid = reviewers_phid + blocking_phid + groups_phid + bl_groups_phid
    transactions.extend([dict(type="reviewers.set", value=all_reviewing_phid)])


def submit(repo, args):
    if DEBUG:
        ARC.append("--trace")

    with wait_message("Checking connection to Phabricator."):
        # Check if raw Conduit API can be used
        if not conduit.check():
            raise Error("Failed to use Conduit API")

        # Check if local and remote VCS matches
        repo.check_vcs()

        # Check if arc is configured
        if not args.no_arc and not repo.check_arc():
            raise Error("Failed to run %s." % ARC_COMMAND)

    repo.before_submit()

    # Find and preview commits to submits.
    with wait_message("Looking for commits.."):
        commits = repo.commit_stack()
    if not commits:
        raise Error("Failed to find any commits to submit")
    logger.warning(
        "Submitting %s commit%s %s:",
        len(commits),
        "" if len(commits) == 1 else "s",
        "as Work In Progress" if args.wip else "for review",
    )

    with wait_message("Loading commits.."):
        # Pre-process to load metadata.
        morph_blocking_reviewers(commits)
        augment_commits_from_body(commits)
        update_commits_from_args(commits, args)

    # Validate commit stack is suitable for review.
    show_commit_stack(commits, validate=True, ignore_reviewers=args.wip)
    try:
        with wait_message("Checking commits.."):
            repo.check_commits_for_submit(
                commits, validate_reviewers=not args.wip, require_bug=not args.no_bug,
            )
    except Error as e:
        if not args.force:
            raise Error("Unable to submit commits:\n\n%s" % e)
        logger.error("Ignoring issues found with commits:\n\n%s", e)

    # Show a warning if there are untracked files.
    if config.warn_untracked:
        untracked = repo.untracked()
        if untracked:
            logger.warning(
                "Warning: found %s untracked file%s (will not be submitted):",
                len(untracked),
                "" if len(untracked) == 1 else "s",
            )
            if len(untracked) <= 5:
                for filename in untracked:
                    logger.info("  %s", filename)

    # Show a warning if -m is used and there are new commits.
    if args.message and any([c for c in commits if not c["rev-id"]]):
        logger.warning(
            "Warning: --message works with updates only, and will not\n"
            "be result in a comment on new revisions."
        )

    # Confirmation prompt.
    if args.yes:
        pass
    elif config.auto_submit and not args.interactive:
        logger.info(
            "Automatically submitting (as per submit.auto_submit in %s)", config.name
        )
    else:
        res = prompt(
            "Submit to %s" % PHABRICATOR_URLS.get(repo.phab_url, repo.phab_url),
            ["Yes", "No", "Always"],
        )
        if res == "No":
            return
        if res == "Always":
            config.auto_submit = True
            config.write()

    # Process.
    previous_commit = None
    # Collect all existing revisions to get reviewers info.
    rev_ids_to_update = [int(c["rev-id"]) for c in commits if c.get("rev-id")]
    revisions_to_update = None
    if rev_ids_to_update:
        with wait_message("Loading revision data..."):
            list_to_update = conduit.get_revisions(ids=rev_ids_to_update)

        revisions_to_update = {str(r["id"]): r for r in list_to_update}

    for commit in commits:
        # Only revisions being updated have an ID.  Newly created ones don't.
        is_update = bool(commit["rev-id"])
        revision_to_update = (
            revisions_to_update[commit["rev-id"]] if is_update else None
        )
        existing_reviewers = (
            revision_to_update["attachments"]["reviewers"]["reviewers"]
            if revision_to_update
            else None
        )
        has_commit_reviewers = bool(
            commit["reviewers"]["granted"] + commit["reviewers"]["request"]
        )

        # Let the user know something's happening.
        if is_update:
            logger.info("\nUpdating revision D%s:", commit["rev-id"])
        else:
            logger.info("\nCreating new revision:")

        logger.info("%s %s", commit["name"], commit["title-preview"])
        repo.checkout(commit["node"])

        # WIP submissions shouldn't set reviewers on phabricator.
        if args.wip:
            reviewers = ""
        else:
            reviewers = ", ".join(
                commit["reviewers"]["granted"] + commit["reviewers"]["request"]
            )

        # Create arc-annotated commit description.
        template_vars = dict(
            title=commit["title-preview"],
            body=commit["body"],
            reviewers=reviewers,
            bug_id=commit["bug-id"],
        )
        summary = commit["body"]
        if previous_commit and not args.no_stack:
            template_vars["depends_on"] = "Depends on D%s" % previous_commit["rev-id"]
            summary = "%s\n\n%s" % (summary, template_vars["depends_on"])

        message = arc_message(template_vars)

        if args.no_arc:
            # Create a diff if needed
            with wait_message("Creating local diff..."):
                diff = repo.get_diff(commit)

            if diff:
                with wait_message("Uploading binary file(s)..."):
                    diff.upload_files()

                with wait_message("Submitting the diff..."):
                    diff_phid = diff.submit(commit, message)
            else:
                diff_phid = None

            if is_update:
                with wait_message("Updating revision..."):
                    rev = conduit.update_revision(
                        commit,
                        has_commit_reviewers,
                        existing_reviewers,
                        diff_phid=diff_phid,
                        wip=args.wip,
                        comment=args.message,
                    )
            else:
                with wait_message("Creating a new revision..."):
                    rev = conduit.create_revision(
                        commit,
                        commit["title-preview"],
                        summary,
                        diff_phid,
                        has_commit_reviewers,
                        wip=args.wip,
                    )

            revision_url = "%s/D%s" % (repo.phab_url, rev["object"]["id"])

        else:
            # Run arc.
            with temporary_file(message) as message_file:
                arc_args = (
                    ["diff"]
                    + ["--base", "arc:this"]
                    + ["--allow-untracked", "--no-amend", "--no-ansi"]
                    + ["--message-file", message_file]
                )
                if args.nolint:
                    arc_args.append("--nolint")
                if args.wip:
                    arc_args.append("--plan-changes")
                if args.lesscontext:
                    arc_args.append("--less-context")
                if is_update:
                    message = args.message if args.message else DEFAULT_UPDATE_MESSAGE
                    arc_args.extend(
                        ["--message", message] + ["--update", commit["rev-id"]]
                    )
                else:
                    arc_args.append("--create")

                revision_url = None
                for line in check_call_by_line(
                    ARC + arc_args, cwd=repo.path, never_log=True
                ):
                    print(line)

                    # Extract Revision URL.
                    m = ARC_OUTPUT_REV_URL_RE.search(line)
                    if m:
                        revision_url = m.group(1)

            if not revision_url:
                raise Error("Failed to find 'Revision URL' in arc output")

            if is_update:
                current_status = revision_to_update["fields"]["status"]["value"]
                with wait_message("Updating D%s.." % commit["rev-id"]):
                    transactions = []
                    revision = conduit.get_revisions(ids=[int(commit["rev-id"])])[0]

                    update_revision_description(transactions, commit, revision)
                    update_revision_bug_id(transactions, commit, revision)

                    # Add reviewers only if revision lacks them
                    if not args.wip and has_commit_reviewers and not existing_reviewers:
                        update_revision_reviewers(transactions, commit)
                        if current_status != "needs-review":
                            transactions.append(dict(type="request-review"))

                    if transactions:
                        arc_call_conduit(
                            "differential.revision.edit",
                            {
                                "objectIdentifier": "D%s" % commit["rev-id"],
                                "transactions": transactions,
                            },
                            repo.path,
                        )

        # Append/replace div rev url to/in commit description.
        body = amend_revision_url(commit["body"], revision_url)

        # Amend the commit if required.
        if commit["title-preview"] != commit["title"] or body != commit["body"]:
            commit["title"] = commit["title-preview"]
            commit["body"] = body
            commit["rev-id"] = parse_arc_diff_rev(commit["body"])
            with wait_message("Updating commit.."):
                repo.amend_commit(commit, commits)

        previous_commit = commit

    # Cleanup (eg. strip nodes) and refresh to ensure the stack is right for the
    # final showing.
    with wait_message("Cleaning up.."):
        repo.finalize(commits)
        repo.after_submit()
        repo.cleanup()
        repo.refresh_commit_stack(commits)

    logger.warning("\nCompleted")
    show_commit_stack(commits, validate=False, show_rev_urls=True)


#
# Self-Updater
#


def update_arc():
    """Write the last check and update arc."""

    def update_repo(name, path):
        logger.info("Updating %s...", name)
        rev = check_output(GIT_COMMAND + ["rev-parse", "HEAD"], split=False, cwd=path)
        check_call(GIT_COMMAND + ["pull", "--quiet"], cwd=path)
        if rev != check_output(
            GIT_COMMAND + ["rev-parse", "HEAD"], split=False, cwd=path
        ):
            logger.info("%s updated", name)
        else:
            logger.info("Update of %s not required", name)

    if not which_path(GIT_COMMAND[0]):
        raise Error(
            "Failed to find 'git' executable, which is required to install "
            "MozPhab's dependencies."
        )

    try:
        update_repo("libphutil", LIBPHUTIL_PATH)
        update_repo("arcanist", ARC_PATH)
    except CommandError:
        result = prompt(
            "Would you like to skip arc upgrades in the future?", ["Yes", "No"]
        )
        if result == "Yes":
            config.arc_last_check = -1
            config.write()
    else:
        config.arc_last_check = int(time.time())
        config.write()


def get_installed_distribution():
    return get_distribution("MozPhab")


def get_name_and_version():
    dist = get_installed_distribution()
    return "{} ({})".format(dist.project_name, dist.version)


def get_pypi_version():
    url = "https://pypi.org/pypi/MozPhab/json"
    output = urllib.request.urlopen(urllib.request.Request(url), timeout=30).read()
    response = json.loads(output.decode("utf-8"))
    return response["info"]["version"]


def log_current_version(_):
    logger.info(get_name_and_version())


def check_for_updates(with_arc=True):
    """Log a message if an update is required/available"""
    # Update arc.
    if (
        with_arc
        and config.arc_last_check >= 0
        and time.time() - config.arc_last_check > ARC_UPDATE_FREQUENCY * 60 * 60
    ):
        update_arc()

    # Update self.
    if (
        config.self_last_check >= 0
        and time.time() - config.self_last_check > SELF_UPDATE_FREQUENCY * 60 * 60
    ):
        config.self_last_check = int(time.time())
        config.write()

        current_version = get_installed_distribution().version
        pypi_version = get_pypi_version()
        logger.debug(
            "Versions - local: {}, PyPI: {}".format(current_version, pypi_version)
        )

        if parse_version(current_version) >= parse_version(pypi_version):
            logger.debug("update check not required")
            return

        if config.self_auto_update:
            logger.info("Upgrading to version %s", pypi_version)
            self_upgrade()
            logger.info("Restarting...")
            check_call([sys.executable] + sys.argv)
            sys.exit()

        logger.warning("Version %s of `moz-phab` is now available", pypi_version)


def self_upgrade():
    """Upgrade ourselves with pip."""

    # Run pip using the current python executable to accommodate for virtualenvs
    command = (
        [sys.executable]
        + ["-m", "pip"]
        + ["install", "MozPhab"]
        + ["--upgrade"]
        + ["--no-cache-dir"]
        + ["--disable-pip-version-check"]
    )

    # If moz-phab was installed with --user, we need to pass it to pip
    # Create "install" distutils command with --user to find the scripts_path
    d = Distribution()
    d.parse_config_files()
    i = d.get_command_obj("install", create=True)
    # Forcing the environment detected by Distribution to the --user one
    i.user = True
    i.prefix = i.exec_prefix = i.home = i.install_base = i.install_platbase = None
    i.finalize_options()
    # Checking if the moz-phab script is installed in user's scripts directory
    script_dir = Path(script_module.__file__).resolve().parent
    user_dir = Path(i.install_scripts).resolve()
    if script_dir == user_dir:
        command.append("--user")

    check_call(command)


def self_update(_):
    """`self-update` command, updates arc and this script"""
    # Update arc.
    if config.arc_last_check >= 0:
        update_arc()

    # Upgrade self
    self_upgrade()


def apply_patch(diff, cwd):
    """Apply a patch provided in the `diff`."""
    with temporary_binary_file(diff.encode("utf8")) as temp_f:
        check_call([GIT_COMMAND[0], "apply", temp_f], cwd=cwd)


def get_base_ref(diff):
    for ref in diff["fields"].get("refs", []):
        if ref["type"] == "base":
            return ref["identifier"]


def check_revision_id(value):
    # D123 or 123
    m = re.search(r"^D?(\d+)$", value)
    if m:
        return int(m.group(1))

    # Full URL
    m = re.search(r"^https?://[^/]+/D(\d+)", value)
    if m:
        return int(m.group(1))

    # Invalid
    raise argparse.ArgumentTypeError(
        "Invalid Revision ID (expected number or URL): %s\n" % value
    )


def patch(repo, args):
    """Patch repository from Phabricator's revisions.

    By default:
    * perform sanity checks
    * find the base commit
    * create a new branch/bookmark
    * apply the patches and commit the changes

    args.no_commit is True - no commit will be created after applying diffs
    args.apply_to - <head|tip|branch> (default: branch)
        branch - find base commit and apply on top of it
        head/tip - apply changes to current commit
    args.raw is True - only print out the diffs (--force doesn't change anything)

    Raises:
    * Error if uncommitted changes are present in the working tree
    * Error if Phabricator revision is not found
    * Error if `--apply-to base` and no base commit found in the first diff
    * Error if base commit not found in repository
    """
    # Check if raw Conduit API can be used
    with wait_message("Checking connection to Phabricator."):
        # Check if raw Conduit API can be used
        if not conduit.check():
            raise Error("Failed to use Conduit API")

    if not args.raw:
        # Check if local and remote VCS matches
        with wait_message("Checking VCS"):
            repo.check_vcs()

        # Look for any uncommitted changes
        with wait_message("Checking repository.."):
            clean = repo.is_worktree_clean()

        if not clean:
            raise Error(
                "Uncommitted changes present. Please %s them or commit before patching."
                % ("shelve" if isinstance(repo, Mercurial) else "stash")
            )

    # Get the target revision
    with wait_message("Fetching D%s.." % args.revision_id):
        revs = conduit.get_revisions(ids=[args.revision_id])

    if not revs:
        raise Error("Revision not found")

    revision = revs[0]

    if not args.skip_dependencies:
        with wait_message("Fetching D%s children.." % args.revision_id):
            try:
                children = conduit.get_successor_phids(
                    revision["phid"], include_abandoned=args.include_abandoned
                )
                non_linear = False
            except NonLinearException:
                children = []
                non_linear = True

        patch_children = True
        if children:
            if args.yes or config.always_full_stack:
                patch_children = True

            else:
                children_msg = (
                    "a child commit" if len(children) == 1 else "child commits"
                )
                res = prompt(
                    "Revision D%s has %s.  Would you like to patch the "
                    "full stack?." % (args.revision_id, children_msg),
                    ["Yes", "No", "Always"],
                )
                if res == "Always":
                    config.always_full_stack = True
                    config.write()

                patch_children = res == "Yes" or res == "Always"

            if patch_children:
                if non_linear and not args.yes:
                    logger.warning(
                        "Revision D%s has a non-linear successor graph.\n"
                        "Unable to apply the full stack.",
                        args.revision_id,
                    )
                    res = prompt("Continue with only part of the stack?", ["Yes", "No"])
                    if res == "No":
                        return

        # Get list of PHIDs in the stack
        try:
            with wait_message("Fetching D%s parents.." % args.revision_id):
                phids = conduit.get_ancestor_phids(revision["phid"])
        except NonLinearException:
            raise Error("Non linear dependency detected. Unable to patch the stack.")

        # Pull revisions data
        if phids:
            with wait_message("Fetching related revisions.."):
                revs.extend(conduit.get_revisions(phids=phids))
            revs.reverse()

        if children and patch_children:
            with wait_message("Fetching related revisions.."):
                revs.extend(conduit.get_revisions(phids=children))

    # Set the target id
    rev_id = revs[-1]["id"]

    if not args.raw:
        logger.info(
            "Patching revision%s: %s",
            "s" if len(revs) > 1 else "",
            " ".join(["D%s" % r["id"] for r in revs]),
        )

    # Pull diffs
    with wait_message("Downloading patch information.."):
        diffs = conduit.get_diffs([r["fields"]["diffPHID"] for r in revs])

    if not args.no_commit and not args.raw:
        for rev in revs:
            diff = diffs[rev["fields"]["diffPHID"]]
            if not diff["attachments"]["commits"]["commits"]:
                raise Error(
                    "A diff without commit information detected in revision D%s.\n"
                    "Use `--no-commit` to patch the working tree." % rev["id"]
                )

    base_node = None
    if not args.raw:
        if args.apply_to == "base":
            base_node = get_base_ref(diffs[revs[0]["fields"]["diffPHID"]])

            if not base_node:
                raise Error(
                    "Base commit not found in diff. "
                    "Use `--apply-to here` to patch current commit."
                )
        elif args.apply_to != "here":
            base_node = args.apply_to

        if args.apply_to != "here":
            try:
                with wait_message("Checking %s.." % short_node(base_node)):
                    base_node = repo.check_node(base_node)
            except NotFoundError as e:
                msg = "Unknown revision: %s" % short_node(base_node)
                if str(e):
                    msg += "\n%s" % str(e)

                if args.apply_to == "base":
                    msg += "\nUse --apply-to to set the base commit."

                raise Error(msg)

        branch_name = None if args.no_commit else "D%s" % rev_id
        repo.before_patch(base_node, branch_name)

    parent = None
    for rev in revs:
        # Prepare the body using just the data from Phabricator
        body = prepare_body(
            rev["fields"]["title"],
            rev["fields"]["summary"],
            rev["id"],
            repo.phab_url,
            depends_on=parent,
        )
        parent = rev["id"]
        diff = diffs[rev["fields"]["diffPHID"]]
        with wait_message("Downloading D%s.." % rev["id"]):
            raw = conduit.call("differential.getrawdiff", {"diffID": diff["id"]})

        if args.no_commit:
            if repo.vcs == "hg" and not which_path(GIT_COMMAND[0]):
                raise Error(
                    "Failed to find 'git' executable.\n"
                    "Git is required to apply patches."
                )
            with wait_message("Applying D%s.." % rev["id"]):
                apply_patch(raw, repo.path)

        elif args.raw:
            logger.info(raw)

        else:
            diff_commits = diff["attachments"]["commits"]["commits"]
            author = "%s <%s>" % (
                diff_commits[0]["author"]["name"],
                diff_commits[0]["author"]["email"],
            )
            author_date = datetime.datetime.fromtimestamp(
                diff["fields"]["dateCreated"]
            ).isoformat()

            try:
                with wait_message("Applying D%s.." % rev["id"]):
                    repo.apply_patch(raw, body, author, author_date)
            except subprocess.CalledProcessError:
                raise Error("Patch failed to apply")

        if not args.raw and rev["id"] != revs[-1]["id"]:
            logger.info("D%s applied", rev["id"])

    if not args.raw:
        logger.warning("D%s applied", rev_id)


def arc_pass(args):
    if DEBUG:
        ARC.append("--trace")

    try:
        check_call(ARC + args.commands)
    except CommandError:
        pass


#
# Reorganise
#


def reorganise(repo, args):
    with wait_message("Checking connection to Phabricator."):
        # Check if raw Conduit API can be used
        if not conduit.check():
            raise Error("Failed to use Conduit API")

    # Find and preview commits to submits.
    with wait_message("Looking for commits.."):
        commits = repo.commit_stack()

    if not commits:
        raise Error("Failed to find any commits to reorganise.")

    with wait_message("Loading commits.."):
        augment_commits_from_body(commits)

    localstack_ids = [c["rev-id"] for c in commits]
    if None in localstack_ids:
        names = [c["name"] for c in commits if c["rev-id"] is None]
        plural = len(names) > 1
        raise Error(
            "Found new commit{plural} in the local stack: {names}.\n"
            "Please submit {them} first.".format(
                plural="s" if plural else "",
                them="them" if plural else "it",
                names=", ".join(names),
            )
        )

    logger.warning(
        "Reorganisation based on {} commit{}:".format(
            len(commits), "" if len(commits) == 1 else "s",
        )
    )

    # Get PhabricatorStack
    # Errors will be raised later in the `walk_llist` method
    with wait_message("Detecting the remote stack..."):
        try:
            phabstack = conduit.get_stack(localstack_ids)
        except Error as e:
            logger.error("Remote stack is not linear.")
            raise

    # Preload the phabricator stack
    with wait_message("Preloading Phabricator stack revisions..."):
        conduit.get_revisions(phids=list(phabstack.keys()))

    if phabstack:
        try:
            phabstack_phids = walk_llist(phabstack)
        except Error as e:
            logger.error(
                "Remote stack is not linear.\n"
                "Detected stack:\n{}".format(
                    " <- ".join(conduit.phids_to_ids(list(phabstack.keys())))
                )
            )
            raise
    else:
        phabstack_phids = []

    localstack_phids = conduit.ids_to_phids(localstack_ids)
    try:
        transactions = stack_transactions(phabstack_phids, localstack_phids)
    except Error:
        logger.error("Unable to prepare stack transactions.")
        raise

    if not transactions:
        raise Error("Reorganisation is not needed.")

    logger.warning("Stack will be reorganised:")
    for phid, rev_transactions in transactions.items():
        node_id = conduit.phid_to_id(phid)
        if "abandon" in [t["type"] for t in rev_transactions]:
            logger.info(" * {} will be abandoned".format(node_id))
        else:
            for t in rev_transactions:
                if t["type"] == "children.set":
                    logger.info(
                        " * {child} will depend on {parent}".format(
                            child=conduit.phid_to_id(t["value"][0]), parent=node_id,
                        )
                    )
                if t["type"] == "children.remove":
                    logger.info(
                        " * {child} will no longer depend on {parent}".format(
                            child=conduit.phid_to_id(t["value"][0]), parent=node_id,
                        )
                    )

    if args.yes:
        pass
    else:
        res = prompt("Perform reorganisation", ["Yes", "No"])
        if res == "No":
            sys.exit(1)

    with wait_message("Applying transactions..."):
        for phid, rev_transactions in transactions.items():
            conduit.edit_revision(rev_id=phid, transactions=transactions[phid])

    logger.info("Stack has been reorganised.")


#
# Main
#


class ColourFormatter(logging.Formatter):
    def __init__(self):
        if DEBUG:
            fmt = "%(levelname)-8s %(asctime)-13s %(message)s"
        else:
            fmt = "%(message)s"
        super().__init__(fmt)
        self.log_colours = {"WARNING": 34, "ERROR": 31}  # blue, red

    def format(self, record):
        result = super().format(record)
        if HAS_ANSI and record.levelname in self.log_colours:
            result = "\033[%sm%s\033[0m" % (self.log_colours[record.levelname], result)
        return result


def init_logging():
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(ColourFormatter())
    stdout_handler.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    logger.addHandler(stdout_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=LOG_FILE, maxBytes=LOG_MAX_SIZE, backupCount=LOG_BACKUPS
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)-13s %(levelname)-8s %(message)s")
    )
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    logger.setLevel(logging.DEBUG)

    # clean up old date-based logs
    now = time.time()
    for filename in sorted(glob("%s/*.log.*" % os.path.dirname(LOG_FILE))):
        m = re.search(r"\.(\d\d\d\d)-(\d\d)-(\d\d)$", filename)
        if not m:
            continue
        file_time = calendar.timegm(
            (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0, 0, 0)
        )
        if (now - file_time) / (60 * 60 * 24) > 8:
            logger.debug("deleting old log file: %s" % os.path.basename(filename))
            os.unlink(filename)


def parse_args(argv):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(
        dest="command",
        metavar="COMMAND",
        description="For full command description: moz-phab COMMAND -h",
    )
    commands.required = True

    # submit

    submit_parser = commands.add_parser(
        "submit", help="Submit commits(s) to Phabricator"
    )
    submit_parser.add_argument(
        "--path", "-p", help="Set path to repository (default: detected)"
    )
    submit_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Submit without confirmation (default: %s)" % config.auto_submit,
    )
    submit_parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Submit with confirmation (default: %s)" % (not config.auto_submit),
    )
    submit_parser.add_argument(
        "--message",
        "-m",
        help="Provide a custom update message (default: %s)" % DEFAULT_UPDATE_MESSAGE,
    )
    submit_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Override sanity checks and force submission; a tool of last resort",
    )
    submit_parser.add_argument(
        "--force-delete",
        action="store_true",
        help="Mercurial only. Override the fail if a DAG branch point detected "
        "and no evolve installed",
    )
    submit_parser.add_argument(
        "--bug", "-b", help="Set Bug ID for all commits (default: from commit)"
    )
    submit_parser.add_argument(
        "--no-bug",
        action="store_true",
        help="Continue if a bug number is not provided.",
    )
    submit_parser.add_argument(
        "--reviewer",
        "--reviewers",
        "-r",
        action="append",
        help="Set review(s) for all commits (default: from commit)",
    )
    submit_parser.add_argument(
        "--blocker",
        "--blockers",
        "-R",
        action="append",
        help="Set blocking review(s) for all commits (default: from commit)",
    )
    submit_parser.add_argument(
        "--nolint",
        "--no-lint",
        action="store_true",
        help="Do not run lint (default: lint changed files if configured)",
    )
    submit_parser.add_argument(
        "--wip",
        "--plan-changes",
        action="store_true",
        help="Create or update a revision without requesting a code review",
    )
    submit_parser.add_argument(
        "--less-context",
        "--lesscontext",
        action="store_true",
        dest="lesscontext",
        help=(
            "Normally, files are diffed with full context: the entire file "
            "is sent to Phabricator so reviewers can 'show more' and see "
            "it. If you are making changes to very large files with tens of "
            "thousands of lines, this may not work well. With this flag, a "
            "revision will be created that has only a few lines of context."
        ),
    )
    submit_parser.add_argument(
        "--no-stack",
        action="store_true",
        help="Submit multiple commits, but do not mark them as dependent",
    )
    submit_parser.add_argument(
        "--upstream",
        "--remote",
        "-u",
        action="append",
        help='Set upstream branch to detect the starting commit. (default: "")',
    )
    submit_parser.add_argument(
        "--arc", dest="no_arc", action="store_false", help="Submits with Arcanist.",
    )
    submit_parser.add_argument(
        "--force-vcs",
        action="store_true",
        help="EXPERIMENTAL: Override VCS compatibility check.",
    )
    submit_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions.",
    )
    submit_parser.add_argument(
        "start_rev",
        nargs="?",
        default="(auto)",
        help="Start revision of range to submit (default: detected)",
    )
    submit_parser.add_argument(
        "end_rev",
        nargs="?",
        default=".",
        help="End revision of range to submit (default: current commit)",
    )

    # arc informs users to pass --trace for more output, so we need to accept it.
    submit_parser.add_argument("--trace", action="store_true", help=argparse.SUPPRESS)

    submit_parser.set_defaults(func=submit, needs_repo=True)

    # self-update

    update_parser = commands.add_parser("self-update", help="Update review script")
    update_parser.add_argument(
        "--force", "-f", action="store_true", help="Force update even if not necessary"
    )

    # We suppress exception stack traces unless --trace is provided
    update_parser.add_argument("--trace", action="store_true", help=argparse.SUPPRESS)

    update_parser.set_defaults(func=self_update, needs_repo=False)

    # patch

    patch_parser = commands.add_parser("patch", help="Patch from Phabricator revision")
    patch_parser.add_argument(
        "revision_id", type=check_revision_id, help="Revision number"
    )
    patch_group = patch_parser.add_mutually_exclusive_group()
    patch_group.add_argument(
        "--apply-to",
        "--applyto",
        "-a",
        metavar="TARGET",
        dest="apply_to",
        help="Where to apply the patch? <{NODE}|here|base> (default: %s)"
        % config.apply_patch_to,
    )
    patch_group.add_argument(
        "--raw", action="store_true", help="Prints out the raw diff to the STDOUT"
    )
    patch_parser.add_argument(
        "--no-commit",
        "--nocommit",
        action="store_true",
        dest="no_commit",
        help="Do not commit. Applies the changes with the `patch` command",
    )
    patch_parser.add_argument(
        "--no-bookmark",
        "--nobookmark",
        action="store_true",
        dest="no_bookmark",
        help="(Mercurial only) Do not create the bookmark",
    )
    patch_parser.add_argument(
        "--no-branch",
        "--nobranch",
        action="store_true",
        dest="no_branch",
        help="(Git only) Do not create the branch",
    )
    patch_parser.add_argument(
        "--skip-dependencies",
        action="store_true",
        help="Do not search for dependencies; patch only one revision",
    )
    patch_parser.add_argument(
        "--include-abandoned", action="store_true", help="Apply abandoned revisions"
    )
    patch_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Patch without confirmation (default: False)",
    )
    patch_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions.",
    )
    patch_parser.add_argument(
        "--force-vcs",
        action="store_true",
        help="EXPERIMENTAL: Override VCS compatibility check.",
    )
    # We suppress exception stack traces unless --trace is provided
    patch_parser.add_argument("--trace", action="store_true", help=argparse.SUPPRESS)
    patch_parser.set_defaults(func=patch, needs_repo=True, no_arc=True)

    # reorganise

    reorg_parser = commands.add_parser(
        "reorg", help="Reorganise commits in Phabricator"
    )
    reorg_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Reorganise without confirmation (default: False)",
    )
    reorg_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions.",
    )
    reorg_parser.add_argument(
        "--upstream",
        "--remote",
        "-u",
        action="append",
        help='Set upstream branch to detect the starting commit. (default: "")',
    )
    reorg_parser.add_argument(
        "start_rev",
        nargs="?",
        default="(auto)",
        help="Start revision of range to reorganise (default: detected)",
    )
    reorg_parser.add_argument(
        "end_rev",
        nargs="?",
        default=".",
        help="End revision of range to reorganise (default: current commit)",
    )
    reorg_parser.add_argument("--trace", action="store_true", help=argparse.SUPPRESS)
    reorg_parser.set_defaults(func=reorganise, needs_repo=True, no_arc=True)

    # install-certificate

    cert_parser = commands.add_parser(
        "install-certificate", help="Install Phabricator certificate locally"
    )
    cert_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions.",
    )
    cert_parser.add_argument("--trace", action="store_true", help=argparse.SUPPRESS)
    cert_parser.set_defaults(func=install_certificate, needs_repo=True, no_arc=True)

    # arc

    arc_parser = commands.add_parser("arc", help="Call Arcanist")
    arc_parser.add_argument("commands", nargs=argparse.REMAINDER)
    arc_parser.set_defaults(func=arc_pass, needs_repo=False)

    # version

    ver_parser = commands.add_parser("version", help="Get version number")
    ver_parser.set_defaults(func=log_current_version, needs_repo=False, no_arc=True)

    # if we're called without a command and from within a repository, default to submit.
    if not argv or (
        not (set(argv) & {"-h", "--help"})
        and argv[0] not in [choice for choice in commands.choices]
        and find_repo_root(os.getcwd())
    ):
        logger.debug("defaulting to `submit`")
        argv.insert(0, "submit")

    return parser.parse_args(argv)


def main(argv):
    global config, HAS_ANSI, DEBUG, SHOW_SPINNER
    try:
        if not os.path.exists(MOZBUILD_PATH):
            os.makedirs(MOZBUILD_PATH)

        init_logging()
        config = Config()
        os.environ["MOZPHAB"] = "1"

        logger.debug(get_name_and_version())

        if config.no_ansi:
            HAS_ANSI = False

        args = parse_args(argv)

        with_arc = not hasattr(args, "no_arc") or not args.no_arc
        if with_arc:
            install_arc_if_required()

        if hasattr(args, "trace") and args.trace:
            DEBUG = True
        if DEBUG:
            SHOW_SPINNER = False

        if args.command != "self-update":
            check_for_updates(with_arc=with_arc)

        if args.command == "patch" and not args.apply_to:
            args.apply_to = config.apply_patch_to

        if args.needs_repo:
            with wait_message("Starting up.."):
                repo = repo_from_args(args)

            conduit.set_repo(repo)
            try:
                args.func(repo, args)
            finally:
                repo.cleanup()

        else:
            args.func(args)

    except KeyboardInterrupt:
        pass
    except Error as e:
        logger.error(e)
        sys.exit(1)
    except Exception as e:
        if DEBUG:
            logger.error(traceback.format_exc())
        else:
            logger.error("%s: %s", e.__class__.__name__, e)
            logger.error("Run moz-phab again with '--trace' to show the stack trace")
        sys.exit(1)


def run():
    main(sys.argv[1:])


if __name__ == "__main__":
    run()
