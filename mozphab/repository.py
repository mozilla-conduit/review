# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
import os
import urllib.parse
from dataclasses import dataclass

from mozphab import environment
from mozphab.profiler import profiler

from .commits import Commit
from .conduit import conduit
from .config import Config
from .diff import Diff
from .exceptions import Error
from .helpers import (
    get_arcrc_path,
    read_json_field,
)
from .logger import logger
from .models import PhabricatorRepository
from .spinner import wait_message
from .vcs import Vcs

MOZILLA_DOMAINS = {
    ".mozilla.org",
    ".mozilla.com",
    ".allizom.org",
}

# Mapping of known Phabricator URLs to Lando URLs.
LANDO_URL_MAPPING = {
    "https://phabricator.services.mozilla.com": "https://lando.moz.tools",
    "https://phabricator-dev.allizom.org": "https://lando-dev.allizom.org",
    "https://phabricator.allizom.org": "https://lando.allizom.org",
}


@dataclass(frozen=True)
class RepositoryOptions:
    """The command line flags and config knobs for finding and running the VCS."""

    # Where to look for the repository, rather than the working directory.
    path: str | None = None
    avoid_jj: bool = False
    safe_mode: bool = False
    # The Git remotes to look for published commits on, overriding `git.remote`.
    upstream: tuple[str, ...] = ()

    @classmethod
    def from_args(cls, args: argparse.Namespace, config: Config) -> "RepositoryOptions":
        """Build the options from the parsed command line and the user's config."""
        return cls(
            path=args.path,
            avoid_jj=args.avoid_jj_vcs,
            safe_mode=args.safe_mode or config.safe_mode,
            upstream=tuple(args.upstream or ()),
        )


DEFAULT_REPOSITORY_OPTIONS = RepositoryOptions()


@dataclass(frozen=True)
class StackSelection:
    """The commits a command works on, as given on its command line."""

    start_rev: str = environment.DEFAULT_START_REV
    end_rev: str = environment.DEFAULT_END_REV
    single: bool = False

    def __post_init__(self):
        if self.single and self.end_rev != environment.DEFAULT_END_REV:
            raise Error("Option --single can be used with only one identifier.")


@dataclass(frozen=True)
class PatchRefs:
    """The kinds of named ref `patch` asks `before_patch` to create.

    This is the input to `before_patch`, decided from the flags and config, while
    the name is passed separately once the revisions are known. What
    `before_patch` actually did is returned as a `PatchAttempt`.
    """

    # Nothing is created unless asked for, so each VCS only needs the flag it reads.
    # A Git branch, or a Jujutsu bookmark.
    create_branch: bool = False
    # A Mercurial bookmark.
    create_bookmark: bool = False
    # Only created when Mercurial has the topic extension enabled.
    create_topic: bool = False


@dataclass(frozen=True)
class PatchAttempt:
    """What `before_patch` changed in the repository, so it can be undone.

    This is the output of `before_patch`, given the `PatchRefs` it was asked for.
    It's passed back to `apply_patch` and `discard_patch_attempt`.
    """

    # The node `before_patch` checked out to apply the patches on.
    node: str
    # The branch or bookmark actually created, if any. Its name can differ from
    # the requested name, eg. `phab-D1_1` when `phab-D1` already exists.
    branch: str | None = None


def parse_patches(log: bytes) -> dict[str, bytes]:
    """Return the patches of a log, keyed by node.

    Each entry of the log starts with a NUL character followed by the commit's
    node on its own line, then the patch.
    """
    patches = {}
    for entry in log.split(b"\0")[1:]:
        node, _, patch = entry.partition(b"\n")
        patches[node.decode()] = patch
    return patches


@dataclass(frozen=True)
class PatchHunk:
    """A hunk of a patch, from its `@@` line to the next one."""

    # The line of the file the hunk starts at, before the patch.
    old_start: int
    # The lines after the `@@` line, starting with ` `, `-`, `+` or `\`.
    lines: tuple[bytes, ...]


@dataclass(frozen=True)
class FilePatch:
    """The part of a patch changing one file."""

    # The rest of the `diff --git` line, eg. `a/path b/path`.
    key: bytes
    # The lines between the `diff --git` line and the first hunk.
    header: tuple[bytes, ...]
    hunks: tuple[PatchHunk, ...]


def split_patch(patch: bytes) -> list[FilePatch]:
    """Split `patch` into the changes to each file, sorted by path."""
    files = []
    for section in (b"\n" + patch).split(b"\ndiff --git ")[1:]:
        key, *lines = section.rstrip(b"\n").split(b"\n")
        header: list[bytes] = []
        # The line each hunk starts at, with its lines.
        hunks: list[tuple[int, list[bytes]]] = []
        for line in lines:
            if line.startswith(b"@@"):
                # The `@@` line is ASCII, even if the context after it isn't.
                old_start = Diff.parse_git_diff(line.decode("ascii", "replace"))[0]
                hunks.append((old_start, []))
            elif hunks:
                hunks[-1][1].append(line)
            else:
                header.append(line)
        files.append(
            FilePatch(
                key,
                tuple(header),
                tuple(PatchHunk(start, tuple(lines)) for start, lines in hunks),
            )
        )
    # A file can have several sections, e.g. when replacing a symlink with it.
    return sorted(files, key=lambda file: file.key)


def normalize_header(header: tuple[bytes, ...]) -> list[bytes]:
    """Leave out of the header of a file what Phabricator's raw diffs don't have.

    Git and Phabricator encode binary data differently, so only the blob IDs of
    the `index` line are kept for binary files. Text files don't need them.
    """
    binary = b"GIT binary patch" in header
    if binary:
        header = header[: header.index(b"GIT binary patch")]
    return [
        line.split(b" ")[1] if line.startswith(b"index ") else line
        for line in header
        if not line.startswith((b"similarity index ", b"dissimilarity index "))
        and (binary or not line.startswith(b"index "))
    ]


def hunk_sides(hunk: PatchHunk) -> tuple[list[bytes], list[bytes]]:
    """Return the lines of a file a hunk covers, before and after the hunk."""
    old_lines: list[bytes] = []
    new_lines: list[bytes] = []
    sides = []
    for line in hunk.lines:
        if line.startswith(b"\\"):
            # "\ No newline at end of file" is about the previous line.
            for side in sides:
                side[-1] += b"\n" + line
            continue
        sides = []
        if line.startswith((b" ", b"-")):
            sides.append(old_lines)
        if line.startswith((b" ", b"+")):
            sides.append(new_lines)
        for side in sides:
            side.append(line[1:])
    return old_lines, new_lines


def apply_hunks(
    file_lines: list[bytes], hunks: tuple[PatchHunk, ...]
) -> list[bytes] | None:
    """Apply `hunks` to `file_lines` like `git apply`, or return `None` if they don't.

    Hunks apply where their context matches, and only have to apply at the
    line they start at if it matches several places.
    """
    positions: dict[bytes, list[int]] = {}
    for position, line in enumerate(file_lines):
        positions.setdefault(line, []).append(position)
    # The position each hunk applies at, with the lines it covers and replaces.
    replacements = []
    for hunk in hunks:
        hunk_old_lines, hunk_new_lines = hunk_sides(hunk)
        if not hunk_old_lines:
            # Only adding lines to an empty file is unambiguous.
            starts = [0] if not file_lines else []
        else:
            starts = [
                start
                for start in positions.get(hunk_old_lines[0], [])
                if file_lines[start : start + len(hunk_old_lines)] == hunk_old_lines
            ]
        # Hunks starting at the first line, or without trailing context, have
        # to match at the beginning or the end of the file.
        if hunk.old_start <= 1:
            starts = [start for start in starts if start == 0]
        last_line = next(
            line for line in reversed(hunk.lines) if not line.startswith(b"\\")
        )
        if not last_line.startswith(b" "):
            starts = [
                start
                for start in starts
                if start + len(hunk_old_lines) == len(file_lines)
            ]
        if len(starts) > 1 and hunk.old_start - 1 in starts:
            starts = [hunk.old_start - 1]
        if len(starts) != 1:
            return None
        replacements.append((starts[0], len(hunk_old_lines), hunk_new_lines))
    result = list(file_lines)
    end = len(file_lines)
    for start, length, replacement in sorted(replacements, reverse=True):
        if start + length > end:
            # The hunks overlap.
            return None
        result[start : start + length] = replacement
        end = start
    return result


def same_changes(patch: bytes, raw_diff: bytes) -> bool:
    """Return whether `patch` and Phabricator's `raw_diff` make the same changes.

    `patch` has the whole files as context, so the files before and after the
    changes are rebuilt from it, and `raw_diff` is applied to the former. The
    line numbers of the raw diff only matter for hunks whose context matches
    several places, so that a patch that rebases cleanly is the same.
    """
    files, raw_files = split_patch(patch), split_patch(raw_diff)
    if len(files) != len(raw_files):
        return False
    for file, raw_file in zip(files, raw_files, strict=True):
        if file.key != raw_file.key:
            return False
        if normalize_header(file.header) != normalize_header(raw_file.header):
            return False
        file_old_lines: list[bytes] = []
        file_new_lines: list[bytes] = []
        for hunk in file.hunks:
            hunk_old_lines, hunk_new_lines = hunk_sides(hunk)
            file_old_lines += hunk_old_lines
            file_new_lines += hunk_new_lines
        if apply_hunks(file_old_lines, raw_file.hunks) != file_new_lines:
            return False
    return True


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
    # The local VCS, set by each backend.
    vcs: Vcs

    def __init__(
        self,
        path: str,
        dot_path: str,
        phab_url: str | None = None,
        options: RepositoryOptions = DEFAULT_REPOSITORY_OPTIONS,
    ):
        self.options = options
        self._phid = None
        self._phab_repo = None
        self._phab_vcs: str | None = None
        self.path = path  # base repository directory
        self.dot_path = dot_path  # .hg/.git directory
        self._arcconfig_files = [
            os.path.join(self.dot_path, ".arcconfig"),
            os.path.join(self.path, ".arcconfig"),
        ]
        self.phab_url = (phab_url or self._phab_url()).rstrip("/")
        self.api_url = self._api_url()
        call_sign_override = os.getenv("MOZPHAB_REPOSITORY_CALLSIGN")
        if call_sign_override:
            logger.warning(
                "Using repository callsign from `MOZPHAB_REPOSITORY_CALLSIGN`: %s",
                call_sign_override,
            )
        self.call_sign = call_sign_override or self._get_setting("repository.callsign")
        self.bmo_url = os.getenv("MOZPHAB_BUGZILLA_URL") or self._get_setting("bmo_url")

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
        raise NotImplementedError()

    def before_submit(self):
        """Executed before the submit commit."""

    def after_submit(self):
        """Executed after the submit commit."""

    def _get_setting(self, key: str):
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

        phab_url_override = os.getenv("MOZPHAB_PHABRICATOR_URL")
        if phab_url_override:
            logger.warning(
                "Using Phabricator URL from `MOZPHAB_PHABRICATOR_URL`: %s",
                phab_url_override,
            )

        phab_url = (
            phab_url_override
            or self._get_setting("phabricator.uri")
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

    def finalize(self, commits: list[Commit]):
        """Update the history after node changed."""

    def configure(self):
        """Read the user's VCS configuration, before the repository is used.

        Honours `options.safe_mode`, which ignores most of the configuration.
        """

    def select_stack(self, selection: StackSelection):
        """Select the commits `commit_stack` and the other stack methods work on."""
        raise NotImplementedError()

    def untracked(self) -> list[str]:
        """Return a list of untracked files.

        Backends that can't report untracked files inherit this empty default.
        """
        return []

    def commit_stack(
        self, single: bool = False, force_delete: bool = False
    ) -> list[Commit] | None:
        """Return list of commits.

        `force_delete` lets Mercurial without evolve continue past a branch point,
        deleting the commits on the other branches.

        List of `Commit`s:
            name          human readable identifier of commit (eg. short sha)
            node          SHA1 in stack
            orig-node     an original SHA1 of the commit
            title         first line of commit description (unaltered)
            body          commit description, excluding the first line and the
                          blank line after it (no leading or trailing blank lines)
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

    def get_diff(self, commit: Commit, less_context: bool = False) -> Diff:
        """Create a Diff object with changes.

        With `less_context`, modified files are diffed with only 100 lines of
        context, rather than the whole file.
        """
        raise NotImplementedError()

    def refresh_commit_stack(self, commits: list[Commit]):
        """Update the stack following an altering change (eg rebase)."""

    def is_node(self, node: str) -> bool:
        """Check if node exists."""
        raise NotImplementedError()

    def check_node(self, node: str) -> str:
        """Return the node if it exists.

        Raises NotFoundError if node not found in the repository.
        """
        raise NotImplementedError()

    def is_public(self, node: str) -> bool:
        """Return `True` if `node` has landed on an official remote/branch.

        A node that only exists locally (eg. part of another, unlanded
        patch stack) is not public, even if it's present in the repository.
        """
        raise NotImplementedError()

    def get_latest_landing_node(self, before: int | None = None) -> str | None:
        """Return the most recent node known to have landed on mozilla-central.

        Used as a rebase target when a patch's original base commit isn't a
        public node, eg. because it's part of a different, unlanded stack.
        If `before` (a Unix timestamp) is given, only consider nodes that
        landed at or before that time. Returns `None` if no such node can be
        determined.
        """
        raise NotImplementedError()

    def checkout(self, node: str):
        """Checkout/Update to specified node."""

    def commit(self, body: str):
        """Commit the changes in the working directory."""

    def amend_commit(self, commit: Commit, commits: list[Commit]):
        """Amend commit description from `title` and `desc` fields"""

    def is_descendant(self, node: str) -> bool:
        """Return `True` if the repository revset is descendant from `node`."""
        raise NotImplementedError()

    def get_current_node(self) -> str:
        """Return the node currently checked out in the working directory."""
        raise NotImplementedError()

    def get_current_branch(self) -> str | None:
        """Return the active branch/bookmark, or None when there is none."""
        return None

    def resolve_node(self, ref: str) -> str:
        """Return the node `ref` (a branch, bookmark, or other revision) resolves to."""
        raise NotImplementedError()

    def get_repo_head_branch(self, repository: PhabricatorRepository) -> str | None:
        """Return the local branch/head of `repository`'s default branch.

        Confirms the identified head exists in the repository.
        """

    def uplift_commits(self, dest: str, commits: list[Commit]) -> list[Commit]:
        """Uplift the repo's revset onto `dest` and returns the refreshed `commits`."""
        raise NotImplementedError()

    def rebase_node(self, source_node: str, dest_node: str):
        """Rebase the commits after `source_node`, up to the current tip, onto `dest_node`."""

    def abort_rebase(self):
        """Abort an interrupted rebase, if the VCS leaves one behind on failure."""

    def before_patch(
        self,
        node: str,
        refs: PatchRefs,
        name: str | None = None,
        assume_yes: bool = False,
    ) -> PatchAttempt:
        """Prepare repository to apply the patches.

        Checks out `node` and creates the `refs` this VCS supports, called `name`,
        or none without a `name`. With `assume_yes`, don't ask before detaching
        Git's HEAD.
        """
        return PatchAttempt(node)

    def discard_patch_attempt(
        self,
        attempt: PatchAttempt,
        *,
        keep_branch: bool = False,
        original_branch: str | None = None,
    ):
        """Undo a failed patch attempt, returning the repository to its node.

        With `keep_branch`, restore the target without detaching the current
        branch, where supported.

        Drops the branch/bookmark `before_patch` created, and the commits
        applied since, so applying at another base starts from a clean state.
        VCSs that don't implement this leave the failed attempt in place: the
        next attempt gets a fresh branch/bookmark name, and the commits are
        collected by the VCS as usual.
        """
        if keep_branch:
            self.checkout(attempt.node)

    def apply_patch(
        self,
        diff: str,
        body: str,
        author: str | None,
        author_date: int | None,
        attempt: PatchAttempt | None = None,
    ):
        """Apply the patch and commit the changes, as part of `attempt` if given."""

    def format_patch(
        self,
        diff: str,
        body: str,
        author: str | None,
        author_date: int | None,
        include_preamble: bool = False,
    ) -> str:
        """Format a patch appropriate for importing.

        With `include_preamble`, the patch also carries the author, date and full
        commit message in the form the VCS imports commits from.
        """
        raise NotImplementedError()

    def fetch_from_upstream(self):
        """Fetch latest changes from upstream remote without merging."""

    def check_commits_for_submit(self, commits: list[Commit]):
        """Validate the list of commits are okay to submit."""

    def _api_url(self) -> str:
        """Return a base URL for conduit API call"""
        url = urllib.parse.urljoin(self.phab_url, "api/")

        if not (
            urllib.parse.urlparse(url).scheme == "https" or environment.HTTP_ALLOWED
        ):
            raise Error("Only https connections are allowed.")

        return url

    @property
    def phab_repo(self) -> dict:
        """Representation of the Repository in Phabricator API."""
        if not self._phab_repo:
            if not self.call_sign:
                raise Error(
                    "Failed to determine the Phabricator callsign for this repository "
                    "(missing .arcconfig?)"
                )

            with wait_message("Reading repository data"):
                self._phab_repo = conduit.get_repository_by_callsign(self.call_sign)

        return self._phab_repo

    @property
    def phabricator_repository(self) -> PhabricatorRepository:
        """This repository on Phabricator."""
        return PhabricatorRepository.from_conduit(self.phab_repo)

    @property
    def phid(self):
        """PHID of the repository.

        This value does not change over time.
        It is stored in a file to avoid calling the API on every run.
        """
        if not self._phid:
            path = os.path.join(self.dot_path, ".moz-phab_phid_cache")

            with profiler.file_io("stat", path):
                is_file = os.path.isfile(path)
            if is_file:
                with profiler.file_io("read", path), open(path) as f:
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
                with profiler.file_io("write", path), open(path, "w") as f:
                    json.dump(repo_phids, f)
            self._phid = repo_phid

        return self._phid

    def check_vcs(self) -> bool:
        """Check the local VCS can work with the repository on Phabricator.

        `--force-vcs` skips the check, so commands don't call this when it's given.
        """
        if self.vcs != self.phab_vcs:
            # This error is captured in Git and not raised if Cinnabar installed.
            logger.warning(
                "Local VCS ({local}) is different from the one defined in the "
                "repository ({remote}).".format(
                    local=self.vcs.value, remote=self.phab_vcs
                )
            )

        return True

    @property
    def phab_vcs(self) -> str:
        """Version Control System short name stored in Phabricator.

        This value does not change over time.
        It is stored in a file to avoid calling the API on every run.
        """
        if self._phab_vcs:
            return self._phab_vcs

        # check file
        path = os.path.join(self.dot_path, ".moz-phab_vcs_cache")
        with profiler.file_io("stat", path):
            is_file = os.path.isfile(path)
        if is_file:
            with profiler.file_io("read", path), open(path) as f:
                phab_vcs = f.readline()
        else:
            phab_vcs = self.phab_repo["fields"]["vcs"]
            with profiler.file_io("write", path), open(path, "w") as f:
                f.write(phab_vcs)

        self._phab_vcs = phab_vcs
        return phab_vcs

    @property
    def lando_url(self) -> str:
        """Return the Lando URL for this repository."""
        return get_lando_url_for_phabricator(self.phab_url)

    def get_public_node(self, node: str) -> str:
        """Hashtag in a remote VCS."""
        return node

    def get_patches(self, nodes: list[str]) -> dict[str, bytes]:
        """Return the patch of each commit, keyed by node.

        The context of each hunk is large enough to cover the entire file, so
        the file's content before and after the commit can be rebuilt from it.
        """
        raise NotImplementedError()

    def get_public_base_node(self, node: str) -> str | None:
        """Return the public ancestor automation should use as the diff base."""
        raise NotImplementedError()

    @property
    def is_cinnabar_required(self) -> bool:
        """Only the Git based backends can need Cinnabar to talk to a hg repo."""
        return False

    def validate_email(self):
        """Validate a user's configured email address."""
