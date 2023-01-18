# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import mimetypes
import os
import re
import sys
import time
import uuid

from contextlib import suppress
from packaging.version import Version
from functools import lru_cache
from typing import (
    List,
    Optional,
)

import hglib
from mozphab import environment

from .config import config
from .diff import Diff
from .exceptions import CommandError, Error, NotFoundError
from .helpers import (
    create_hunk_lines,
    parse_config,
    short_node,
    temporary_binary_file,
    temporary_file,
    which_path,
    is_valid_email,
)
from .logger import logger
from .repository import Repository
from .spinner import wait_message, clear_terminal_line
from .subprocess_wrapper import debug_log_command
from .telemetry import telemetry

MINIMUM_MERCURIAL_VERSION = Version("4.3.3")


class Mercurial(Repository):
    def __init__(self, path):
        dot_path = os.path.join(path, ".hg")
        if not os.path.isdir(dot_path):
            raise ValueError("%s: not a hg repository" % path)
        logger.debug("found hg repo in %s", path)

        super().__init__(path, dot_path)
        self.vcs = "hg"
        self._hg_binary = config.hg_command[0]
        self.revset = None
        self.strip_nodes = []
        self.status = None
        self.obsstore = None
        self.unlink_obsstore = False
        self.use_evolve = False
        self.use_topic = False
        self.has_mq = False
        self.has_shelve = False
        self.previous_bookmark = None
        self.has_temporary_bookmark = False
        self.username = ""

        # Check for `hg` presence
        if not which_path(self._hg_binary):
            raise Error(f"Failed to find hg executable ({self._hg_binary})")

        self._config_options = {}
        self._safe_config_options = {}
        self._extra_options = {}
        self._safe_mode = False
        self._repo_path = path
        hglib.HGPATH = self._hg_binary
        self._repo = None
        self._configs = []
        major, minor, micro, *_ = self.repository.version
        self.mercurial_version = Version(f"{major}.{minor}.{micro}")
        self.vcs_version = str(self.mercurial_version)
        if self.mercurial_version < MINIMUM_MERCURIAL_VERSION:
            raise Error(
                f"You are currently running Mercurial {self.mercurial_version}.  "
                f"Mercurial {MINIMUM_MERCURIAL_VERSION} or newer is required."
            )

        # Some Mercurial commands require the current working directory to be
        # set to the repository root (eg. `hg files`).
        os.chdir(self._repo_path)

    @property
    def repository(self):
        """Returns the hglib.hgclient instance.

        If the config has changed, recreate the instance.
        """
        configs = [f"{key}={value}" for key, value in self._get_config_options()]
        configs.sort()
        # returns the repo instance if the config has not changed.
        if self._repo is not None and self._configs == configs:
            return self._repo

        if self._repo:
            self._repo.close()

        self._configs = configs
        self._repo = hglib.open(self._repo_path, encoding="UTF-8", configs=configs)
        return self._repo

    def _get_config_options(self):
        """Returns the --config options for hg

        Updated with the safe ones when safe mode is used.
        """
        options = dict(self._config_options)
        if self._safe_mode:
            options.update(self._safe_config_options)
        return list(options.items())

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

    @staticmethod
    def _get_extensions(*, from_config=None, from_args=None):
        assert from_config or from_args

        extensions = []
        if from_config:
            for name in from_config:
                if name.startswith("extensions."):
                    extensions.append(re.sub(r"^extensions\.(?:hgext\.)?", "", name))

        else:
            args = from_args.copy()
            while len(args) >= 2:
                arg = args.pop(0)
                if arg != "--config":
                    continue
                arg = args.pop(0)
                if arg.startswith("extensions."):
                    name, value = arg.split("=", maxsplit=1)
                    extensions.append(re.sub(r"^extensions\.(?:hgext\.)?", "", name))

        return sorted(extensions)

    def is_worktree_clean(self):
        status = self._status()
        return not status["T"]

    def hg(self, command, **kwargs):
        self.hg_out(command, capture=False, **kwargs)

    def hg_out(
        self,
        command,
        capture=True,
        expect_binary=False,
        strip=True,
        keep_ends=False,
        split=True,
        never_log=False,
    ):
        def error_handler(exit_code, stdout, stderr):
            if not capture:
                clear_terminal_line()
                if stderr:
                    print(stderr.decode(), file=sys.stderr, end="")
                if stdout:
                    print(stdout.decode(), end="")

            if not never_log:
                if stderr:
                    logger.debug(stderr.decode().rstrip())
                if stdout:
                    logger.debug(stdout.decode().rstrip())

            raise CommandError(
                "command '%s' failed to complete successfully" % command[0].decode(),
                exit_code,
            )

        for arg, value in self._extra_options.items():
            command.extend([arg, value])

        debug_log_command(["hg"] + command)
        command = [c.encode() for c in command]
        out = self.repository.rawcommand(command, eh=error_handler)

        if expect_binary:
            logger.debug("%s bytes of data received", len(out))
            return out

        out = out.decode()
        if strip:
            out = out.rstrip()
        if out and not never_log:
            logger.debug(out)

        if capture:
            return out.splitlines(keep_ends) if split else out

        clear_terminal_line()
        print(out, end="")
        return None

    def hg_log(self, revset, split=True, select="node"):
        return self.hg_out(["log", "-T", "{%s}\n" % select, "-r", revset], split=split)

    def before_submit(self):
        self.validate_email()

        # Remember the currently checked out commit.  If a bookmark is active
        # just use that, otherwise create a randomly named bookmark which will
        # be deleted in cleanup(). Mercurial will automatically move the
        # bookmark to the successors as we update commits.
        for active, bookmark in [
            line.split(" ", 1)
            for line in self.hg_out(["bookmark", "-T", "{active} {bookmark}\n"])
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
        self.hg(["update", self.previous_bookmark, "--quiet"])

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

    def set_args(self, args):
        """Sets up the right environment for hg, prior to running it.

        Sets:
        - all the --config options (includes safe mode)
        - extra options
        - the `revset` attribute
        """
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

        self._safe_mode = self.args.safe_mode or config.safe_mode
        self._safe_config_options.clear()
        self._config_options.clear()
        self._extra_options.clear()

        # Need to use the correct username.
        if "ui.username" not in hg_config:
            raise Error("ui.username is not configured in your hgrc")
        self._safe_config_options["ui.username"] = hg_config["ui.username"]
        self.username = hg_config["ui.username"]

        # Always need rebase.
        self._config_options["extensions.rebase"] = ""

        # Mercurial should never paginate the response.
        self._extra_options["--pager"] = "never"

        # Perform rebases in-memory to improve performance (requires v4.5+).
        if not self._safe_mode and self.mercurial_version >= Version("4.5"):
            self._config_options["rebase.experimental.inmemory"] = "true"

        # Enable evolve if the user's currently using it.  evolve makes amending
        # commits with children trivial (amongst other things).
        ext_evolve = self._get_extension("evolve", hg_config)
        if ext_evolve is not None:
            self._safe_config_options["extensions.evolve"] = ext_evolve
            self.use_evolve = True

        # Otherwise just enable obsolescence markers, and when we're done remove
        # the obsstore we created.
        else:
            self._config_options["experimental.evolution.createmarkers"] = "true"
            self._config_options["extensions.strip"] = ""
            self.use_evolve = False
            self.obsstore = os.path.join(self.path, ".hg", "store", "obsstore")
            self.unlink_obsstore = not os.path.exists(self.obsstore)

            # Warn users that `evolve` is not enabled and direct
            # them to `mach vcs-setup`.
            logger.warning(
                "moz-phab detected that hg isn't configured with `evolve`. This "
                "usually causes severe performance issues. Run `./mach vcs-setup` "
                "to configure evolve."
            )

        # User may wish to use topics rather than bookmarks.
        ext_topic = self._get_extension("topic", hg_config)
        if ext_topic is not None:
            self._safe_config_options["extensions.topic"] = ext_topic
            self.use_topic = True

        # This script interacts poorly with mq.
        ext_mq = self._get_extension("mq", hg_config)
        self.has_mq = ext_mq is not None
        if self.has_mq:
            self._safe_config_options["extensions.mq"] = ext_mq

        # `shelve` is useful for dealing with uncommitted changes; track if it's
        # currently enabled so we can tailor our error accordingly.
        self.has_shelve = self._get_extension("shelve", hg_config) is not None

        # Disable the user's hgrc file, to ensure we run without rogue extensions.
        if self._safe_mode:
            os.environ["HGRCPATH"] = ""
            options = self._get_config_options()
            logger.debug(
                "hg extensions (safe mode): %s",
                ", ".join(self._get_extensions(from_args=options)),
            )
        else:
            logger.debug(
                "hg extensions: %s",
                ", ".join(self._get_extensions(from_config=hg_config)),
            )

        if hasattr(self.args, "start_rev"):
            is_single = hasattr(self.args, "single") and self.args.single
            # Set the default start revision.
            if self.args.start_rev == environment.DEFAULT_START_REV:
                if is_single:
                    start_rev = self.args.end_rev
                else:
                    start_rev = "ancestors(.) and not public() and not obsolete()"
            else:
                start_rev = self.args.start_rev

            # Resolve to nodes as that's nicer to read.
            try:
                start = self.hg_log(start_rev)[0]
            except IndexError:
                if self.args.start_rev == environment.DEFAULT_START_REV:
                    raise Error("Failed to find draft commits to submit")
                else:
                    raise Error(
                        "Failed to start of commit range: %s" % self.args.start_rev
                    )

            if is_single:
                self.revset = start
                return

            end_rev = self.args.end_rev

            try:
                end = self.hg_log(end_rev)[0]
            except IndexError:
                raise Error("Failed to end of commit range: %s" % end_rev)

            self.revset = "%s::%s" % (short_node(start), short_node(end))

    def commit_stack(self, **kwargs):
        # Grab all the info we need about the commits, using randomness as a delimiter.
        boundary = "--%s--\n" % uuid.uuid4().hex
        hg_log = self.hg_out(
            ["log"]
            + [
                "-T",
                "{rev}\n{node}\n{date|hgdate}\n{author|person}\n{author|email}\n"
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
                    "submit": True,
                    "public-node": node,
                    "orig-node": node,
                    "title": desc[0],
                    "title-preview": desc[0],
                    "body": "\n".join(desc[1:]).rstrip(),
                    "bug-id": None,
                    "reviewers": dict(request=[], granted=[]),
                    "rev-id": None,
                    "author-date-epoch": int(author_date.split(" ")[0]),
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
            name - name of the bookmark/topic to be created
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

        if name and self.use_topic and config.create_topic and not self.args.no_topic:
            topics = self.hg_out(["topics", "-T", "{topic}\n"])
            topic_name = name
            i = 0
            while topic_name in topics:
                i += 1
                topic_name = "%s_%s" % (name, i)

            self.hg(["topic", topic_name])

    def apply_patch(self, diff, body, author, author_date):
        changeset_str = self.format_patch(diff, body, author, author_date)
        with temporary_binary_file(changeset_str.encode("utf8")) as changeset_file:
            self.hg(["import", changeset_file, "--quiet"])

    def format_patch(self, diff, body, author, author_date):
        changeset = ["# HG changeset patch"]
        if author:
            changeset.append("# User {}".format(author))
        if author_date:
            changeset.append("# Date {} 0".format(author_date))
        changeset.extend([body, diff])
        return "\n".join(changeset)

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

    def is_descendant(self, node: str) -> bool:
        # Query the log for all commits that are both in the revset and descendants of
        # `node`. If we get any results, then our revset is already based off of `node`.
        out = self.hg_out(
            ["log", "-r", f"{node}:: and {self.revset}", "-T", "{node}\n"],
            split=False,
        )

        # If the command output is empty, our revset does not descend from `node`.
        if not out:
            return False

        return True

    def map_callsign_to_unified_head(self, callsign: str) -> Optional[str]:
        if not self.is_node(callsign):
            return None

        return callsign

    def rebase_commit(self, source_commit, dest_commit):
        self.hg(
            ["rebase"]
            + ["--source", source_commit["node"]]
            + ["--dest", dest_commit["node"]]
        )

    def uplift_commits(self, dest: str, commits: List[dict]) -> List[dict]:
        out = self.hg_out(
            [
                # Send messages to `stderr` so `stdout` is pure JSON.
                "--config",
                "ui.message-output=stderr",
                "rebase",
                "-r",
                self.revset,
                "-d",
                dest,
                # Don't obsolete the original changesets.
                "--keep",
                "-T",
                "json",
            ],
            split=False,
        )

        # Capture the JSON output from rebase and use it to refresh our commit stack.
        # We need to do this since using `--keep` with rebase causes the newly produced
        # changesets to not be marked as successors of the original revisions, which
        # means `refresh_commit_stack` won't find any updates to make.
        nodechanges = json.loads(out)[0]["nodechanges"]

        if len(nodechanges) != len(commits):
            raise ValueError(
                f"Rebase created {len(nodechanges)} new changesets; "
                f"{len(commits)} expected"
            )

        for commit in commits:
            # We should have a single new commit for each commit we want to uplift.
            # Error out if we don't as progressing with an inconsistent stack might
            # cause behaviour we can't handle.
            new = nodechanges.get(commit["node"])
            if len(new) != 1:
                raise ValueError(f"Should only have a single new item, got {new}")

            self._refresh_commit(commit, new[0])

        # Set revset to the new range of commits.
        self.revset = "%s::%s" % (commits[0]["node"], commits[-1]["node"])

        return commits

    def check_commits_for_submit(self, commits, require_bug=True):
        # 'Greatest Common Ancestor'/'Merge Base' should be included in the revset.
        ancestor = self.hg_log("ancestor(%s)" % self.revset, split=False)
        if not any(commit["node"] == ancestor for commit in commits):
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

        super().check_commits_for_submit(commits, require_bug=require_bug)

    def _get_file_modes(self, commit):
        """Get modes of the modified files."""

        # build list of modified files
        # using a -T template here doesn't output all files (eg. source files from
        # a copy operation are skipped), so we have to parse the default output
        modified_files = [
            line[2:].replace("\\", "/")  # strip leading status char and space
            for line in self.hg_out(["status", "--change", commit["node"], "--copies"])
        ]

        def _to_mode_dict(mode_list):
            mode_dict = {}
            for line in mode_list:
                flag, mode_path = line.split(":", 1)
                mode_dict[mode_path] = "100755" if flag == "x" else "100644"
            return mode_dict

        # get before/after file modes
        # .arcconfig is added to the file list to try to avoid the situation where
        # none of the files specified by -I are present in the commit.
        try:
            old_modes = _to_mode_dict(
                self.hg_out(
                    ["files"]
                    + ["--rev", commit["parent"]]
                    + ["-T", "{flags}:{path}\n"]
                    + ["-I%s" % f for f in modified_files]
                    + ["-I.arcconfig"]
                )
            )
        except CommandError:
            # Mercurial will return an error if none of the files on the -I list are
            # valid for the specified revision.  Treat any error here as an empty
            # result.
            old_modes = {}

        new_modes = _to_mode_dict(
            self.hg_out(
                ["files"]
                + ["--rev", commit["node"]]
                + ["-T", "{flags}:{path}\n"]
                + ["-I%s" % f for f in modified_files]
                + ["-I.arcconfig"]
            )
        )

        # build response
        file_modes = {}
        for path in modified_files:
            if path in old_modes:
                file_modes.setdefault(path, {})
                file_modes[path]["old_mode"] = old_modes[path]
            if path in new_modes:
                file_modes.setdefault(path, {})
                file_modes[path]["new_mode"] = new_modes[path]

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

    @lru_cache(maxsize=None)
    def hg_cat(self, fn, node):
        return self.hg_out(["cat", "-r", node, fn], split=False, expect_binary=True)

    @lru_cache(maxsize=None)
    def _file_size(self, fn, rev):
        """Get the file size of the file."""
        return int(
            self.hg_out(
                ["files", "-v", "-r", rev, os.path.join(self.path, fn), "-T", "{size}"],
                split=False,
            )
        )

    @lru_cache(maxsize=128)
    def _get_file_meta(self, fn, rev):
        """Collect information about the file."""
        binary = False
        body = self.hg_cat(fn, rev)
        meta = dict(mime="TEXT", bin_body=body)

        meta["file_size"] = self._file_size(fn, rev)
        if meta["file_size"] > environment.MAX_TEXT_SIZE:
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

    def _change_add(self, change, fn, _old_fn, _parent, node):
        """Create a change about adding a file to the commit."""
        meta = self._get_file_meta(fn, node)
        telemetry().submission.files_size.accumulate(meta["file_size"])

        if meta["binary"]:
            change.set_as_binary(
                a_body="",
                a_mime="",
                b_body=meta["bin_body"],
                b_mime=meta["mime"],
            )
            return

        # Empty files don't have any hunks.
        if meta["file_size"] == 0:
            return

        lines, eof_missing_newline = create_hunk_lines(meta["body"], "+")
        new_len = len(lines)
        if eof_missing_newline:
            new_len -= 1

        change.hunks.append(
            Diff.Hunk(
                old_off=0,
                new_off=1,
                old_len=0,
                new_len=new_len,
                lines=lines,
            )
        )

    def _change_del(self, change, fn, _old_fn, parent, _node):
        """Create a change about deleting a file from the commit."""
        meta = self._get_file_meta(fn, parent)
        telemetry().submission.files_size.accumulate(meta["file_size"])

        if meta["binary"]:
            change.set_as_binary(
                a_body=meta["bin_body"],
                a_mime=meta["mime"],
                b_body="",
                b_mime="",
            )
            return

        # Empty files don't have any hunks.
        if meta["file_size"] == 0:
            return

        lines = create_hunk_lines(meta["body"], "-")[0]
        old_len = len(lines)

        change.hunks.append(
            Diff.Hunk(
                old_off=1,
                new_off=0,
                old_len=old_len,
                new_len=0,
                lines=lines,
            )
        )

    def _change_mod(self, change, fn, old_fn, parent, node):
        """Create a change about modified file in the commit."""
        a_meta = self._get_file_meta(old_fn, parent)
        b_meta = self._get_file_meta(fn, node)
        file_size = max(a_meta["file_size"], b_meta["file_size"])
        telemetry().submission.files_size.accumulate(file_size)

        if a_meta["binary"] or b_meta["binary"]:
            # Binary file.
            change.set_as_binary(
                a_body=a_meta["bin_body"],
                a_mime=a_meta["mime"],
                b_body=b_meta["bin_body"],
                b_mime=b_meta["mime"],
            )
            return

        # Empty files don't have any hunks.
        if a_meta["file_size"] == 0 and b_meta["file_size"] == 0:
            return

        if a_meta["body"] == b_meta["body"]:
            # File contents unchanged.
            lines = create_hunk_lines(a_meta["body"], " ", check_eof=False)[0]
            change.hunks.append(
                Diff.Hunk(
                    old_off=1,
                    new_off=1,
                    old_len=len(lines),
                    new_len=len(lines),
                    lines=lines,
                )
            )
            return

        if self.args.lesscontext or file_size > environment.MAX_CONTEXT_SIZE:
            context_size = 100
        else:
            context_size = environment.MAX_CONTEXT_SIZE

        start = time.process_time()

        # Using binary here to ensure we avoid converting newlines
        git_diff = self.hg_out(
            ["diff", "--git", "--unified", str(context_size), "--rev", parent, fn],
            expect_binary=True,
        ).decode("utf-8")
        change.from_git_diff(git_diff)

        if file_size > environment.MAX_CONTEXT_SIZE / 2:
            logger.debug(
                "Splitting the diff (size: %s) took %ss",
                file_size,
                time.process_time() - start,
            )

    def validate_email(self):
        email = self.extract_email_from_username()
        if not is_valid_email(email):
            raise Error(
                f"Your username configured with Mercurial ({email}) "
                f"must contain a valid email.\n"
                f"Please see https://www.mercurial-scm.org/doc/hgrc.5.html "
                f"for more information on editing your Mercurial configuration."
                f"\n\nYou can also amend a commit via "
                f'`hg commit --amend --user "username <some@email.com>"'
            )

    def extract_email_from_username(self) -> str:
        """Extracts an email from a Mercurial username, if it exists.
        Not guaranteed to return a valid email, make sure to validate."""
        return self.username.split("<").pop().replace(">", "")
