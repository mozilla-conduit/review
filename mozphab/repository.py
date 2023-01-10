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

from .conduit import conduit, normalise_reviewer
from .exceptions import Error
from .helpers import (
    get_arcrc_path,
    has_arc_rejections,
    read_json_field,
)
from .logger import logger
from .spinner import wait_message


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
        self.bmo_url = self._get_setting("bmo_url")

        if self.bmo_url and not (
            urllib.parse.urlparse(self.bmo_url).scheme == "https"
            or environment.HTTP_ALLOWED
        ):
            raise Error("Only https connections are allowed.")

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

    def untracked(self):
        """Return a list of untracked files."""

    def commit_stack(self, **kwargs):
        """Return list of commits.

        List of dicts with the following keys:
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

    def map_callsign_to_unified_head(self, callsign: str) -> Optional[str]:
        """Return the expected VCS identifier for the given callsign.

        Returns a VCS identifier that corresponds to the given Phabricator repository
        callsign. Confirms the identified head exists in the repository.
        """

    def uplift_commits(self, dest: str, commits: List[dict]) -> List[dict]:
        """Uplift the repo's revset onto `dest` and returns the refreshed `commits`."""

    def rebase_commit(self, source_commit, dest_commit):
        """Rebase source onto destination."""

    def before_patch(self, node, name):
        """Prepare repository to apply the patches."""

    def apply_patch(self, diff, body, author, author_date):
        """Apply the patch and commit the changes."""

    def format_patch(self, diff, body, author, author_date):
        """Format a patch appropriate for importing."""

    def check_commits_for_submit(self, commits, *, require_bug=True):
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

            error_msg = (
                "Phabricator revisions should be unique, but the following "
                "commits refer to the same one (D{}):\n".format(rev_id)
            )
            for name in names:
                error_msg += "* %s\n" % name
            errors.append(error_msg)

        # Flatten and deduplicate reviewer list, keeping track of the
        # associated commit
        for commit in commits:
            # We can ignore reviewers on WIP commits, as they won't be passed to Phab
            if commit["wip"]:
                continue

            for group in list(commit["reviewers"].keys()):
                for reviewer in commit["reviewers"][group]:
                    all_reviewers.setdefault(group, set())
                    all_reviewers[group].add(reviewer)

                    reviewer = normalise_reviewer(reviewer)
                    reviewer_commit_map.setdefault(reviewer, [])
                    reviewer_commit_map[reviewer].append(commit["node"])

        # Verify all reviewers in a single call
        for invalid_reviewer in conduit.check_for_invalid_reviewers(all_reviewers):
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
            path = os.path.join(self.dot_path, ".moz-phab_phid")

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

    def validate_email(self):
        """Validate a user's configured email address."""
