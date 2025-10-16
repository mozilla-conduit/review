# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import urllib.parse
from typing import (
    List,
    Optional,
)

from mozphab import environment

from .commits import Commit
from .conduit import conduit
from .diff import Diff
from .exceptions import Error
from .helpers import (
    get_arcrc_path,
    read_json_field,
)
from .logger import logger
from .spinner import wait_message

MOZILLA_DOMAINS = {
    ".mozilla.org",
    ".mozilla.com",
    ".allizom.org",
}

# Mapping of known Phabricator URLs to Lando URLs.
LANDO_URL_MAPPING = {
    "https://phabricator.services.mozilla.com": "https://lando.moz.tools",
    "https://phabricator-dev.allizom.org": "https://dev.lando.nonprod.webservices.mozgcp.net",
    "https://phabricator.allizom.org": "https://stage.lando.nonprod.webservices.mozgcp.net",
}


def get_lando_url_for_phabricator(phab_url: str) -> str:
    """Return the Lando URL for the given Phabricator URL."""
    return LANDO_URL_MAPPING[phab_url.rstrip("/")]


def is_mozilla_phabricator(url: str) -> bool:
    """Return `True` if the `url` is a Mozilla owned domain."""
    phab_host = urllib.parse.urlparse(url).hostname
    if not phab_host:
        return False
    return any(phab_host.endswith(domain) for domain in MOZILLA_DOMAINS)


class Repository(object):
    def __init__(self, path: str, dot_path: str, phab_url: Optional[str] = None):
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
        self.bmo_url = self._get_setting("bmo_url")

        if self.bmo_url:
            if not (
                urllib.parse.urlparse(self.bmo_url).scheme == "https"
                or environment.HTTP_ALLOWED
            ):
                raise Error("Only https connections are allowed.")
        elif is_mozilla_phabricator(self.phab_url):
            self.bmo_url = "https://bugzilla.mozilla.org"

    def is_worktree_clean(self) -> bool:
        """Check if the working tree is clean."""

    def before_submit(self):
        """Executed before the submit commit."""

    def after_submit(self):
        """Executed after the submit commit."""

    def _get_setting(self, key):
        """Read settings from .arcconfig"""
        value = read_json_field(self._arcconfig_files, [key])
        return value

    def _phab_url(self) -> str:
        """Determine the phab/conduit URL."""

        # In order of priority as per arc
        # FIXME: This should also check {.hg|.git}/arc/config, which is where
        # `arc set-config --local` writes to.  See bug 1497786.
        defaults_files = [get_arcrc_path()]
        if environment.IS_WINDOWS:
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
        if (
            hasattr(args, "single")
            and args.single
            and args.end_rev != environment.DEFAULT_END_REV
        ):
            raise Error("Option --single can be used with only one identifier.")

        self.args = args

    def untracked(self) -> List[str]:
        """Return a list of untracked files."""

    def commit_stack(self, **kwargs) -> Optional[List[Commit]]:
        """Return list of commits.

        List of `Commit`s:
            name          human readable identifier of commit (eg. short sha)
            node          SHA1 in stack
            orig-node     an original SHA1 of the commit
            title         first line of commit description (unaltered)
            body          commit description, excluding first line
            title-preview title with bug-id and reviewer modifications
            bug-id        bmo bug-id
            bug-id-orig   original bug-id from commit desc
            reviewers     list of reviewers
            rev-id        phabricator revision id
            parent        SHA1 of the parent commit
            author-date   string representation of the commit creation time
            author-date-epoch
            author-name
            author-email
        """

    def get_diff(self, commit: Commit) -> Diff:
        """Create a Diff object with changes."""

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

    def is_descendant(self, node: str) -> bool:
        """Return `True` if the repository revset is descendant from `node`."""

    def get_repo_head_branch(self) -> Optional[str]:
        """Return the expected branch/head for the current Phabricator repo.

        Confirms the identified head exists in the repository.
        """

    def uplift_commits(self, dest: str, commits: List[Commit]) -> List[Commit]:
        """Uplift the repo's revset onto `dest` and returns the refreshed `commits`."""

    def rebase_commit(self, source_commit, dest_commit):
        """Rebase source onto destination."""

    def before_patch(self, node, name):
        """Prepare repository to apply the patches."""

    def apply_patch(
        self, diff: str, body: str, author: Optional[str], author_date: Optional[int]
    ):
        """Apply the patch and commit the changes."""

    def format_patch(
        self, diff: str, body: str, author: Optional[str], author_date: Optional[int]
    ):
        """Format a patch appropriate for importing."""

    def check_commits_for_submit(self, commits: List[Commit]):
        """Validate the list of commits are okay to submit."""

    def _api_url(self):
        """Return a base URL for conduit API call"""
        url = urllib.parse.urljoin(self.phab_url, "api/")

        if not (
            urllib.parse.urlparse(url).scheme == "https" or environment.HTTP_ALLOWED
        ):
            raise Error("Only https connections are allowed.")

        return url

    @property
    def phab_repo(self):
        """Representation of the Repository in Phabricator API."""
        if not self._phab_repo:
            with wait_message("Reading repository data"):
                self._phab_repo = conduit.get_repository_by_callsign(self.call_sign)

        return self._phab_repo

    @property
    def phid(self):
        """PHID of the repository.

        This value does not change over time.
        It is stored in a file to avoid calling the API on every run.
        """
        if not self._phid:
            path = os.path.join(self.dot_path, ".moz-phab_phid_cache")

            if os.path.isfile(path):
                with open(path) as f:
                    try:
                        repo_phids = json.load(f)
                    except json.decoder.JSONDecodeError:
                        # File is probably using the old format.
                        repo_phids = {}
                    repo_phid = repo_phids.get(self.call_sign, None)
            else:
                repo_phids = {}
                repo_phid = None

            if not repo_phid:
                repo_phid = self.phab_repo["phid"]
                repo_phids[self.call_sign] = repo_phid
                with open(path, "w") as f:
                    json.dump(repo_phids, f)
            self._phid = repo_phid

        return self._phid

    def check_vcs(self):
        """`Git.check_vcs` raises if cinnabar required and not installed."""
        if self.args.force_vcs:
            return True

        if self.vcs != self.phab_vcs:
            # This error is captured in Git and not raised if Cinnabar installed.
            logger.warning(
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
            path = os.path.join(self.dot_path, ".moz-phab_vcs_cache")
            if os.path.isfile(path):
                with open(path) as f:
                    self._phab_vcs = f.readline()
            else:
                self._phab_vcs = self.phab_repo["fields"]["vcs"]
                with open(path, "w") as f:
                    f.write(self._phab_vcs)

        return self._phab_vcs

    @property
    def lando_url(self) -> str:
        """Return the Lando URL for this repository."""
        return get_lando_url_for_phabricator(self.phab_url)

    def get_public_node(self, node):
        """Hashtag in a remote VCS."""
        return node

    def validate_email(self):
        """Validate a user's configured email address."""
