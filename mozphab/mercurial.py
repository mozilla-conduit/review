# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import mimetypes
import os
import re
import time
import uuid

from contextlib import suppress
from distutils.version import LooseVersion

from mozphab import environment

from .config import config
from .diff import Diff
from .exceptions import CommandError, Error, NotFoundError
from .helpers import (
    parse_config,
    short_node,
    temporary_binary_file,
    temporary_file,
    which_path,
)
from .logger import logger
from .repository import Repository
from .spinner import wait_message
from .subprocess_wrapper import check_call, check_output

MINIMUM_MERCURIAL_VERSION = LooseVersion("4.3.3")


class Mercurial(Repository):
    def __init__(self, path):
        dot_path = os.path.join(path, ".hg")
        if not os.path.isdir(dot_path):
            raise ValueError("%s: not a hg repository" % path)
        logger.debug("found hg repo in %s", path)

        super().__init__(path, dot_path)
        self.vcs = "hg"

        self._hg = config.hg_command.copy()
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
            raise Error("Failed to find hg executable ({})".format(self._hg[0]))
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

            logger.debug(
                "hg extensions (safe mode): %s",
                ", ".join(self._get_extensions(from_args=options)),
            )

        else:
            logger.debug(
                "hg extensions: %s",
                ", ".join(self._get_extensions(from_config=hg_config)),
            )

        self._hg.extend(options)

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

    def commit_stack(self):
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
        changeset = ["# HG changeset patch"]
        if author:
            changeset.append("# User {}".format(author))

        if author_date:
            changeset.append("# Date {} 0".format(author_date))

        changeset.extend([body, diff])
        changeset_str = "\n".join(changeset)
        with temporary_binary_file(changeset_str.encode("utf8")) as changeset_file:
            self.hg(["import", changeset_file, "--quiet"])

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

        # build list of modified files
        # using a -T template here doesn't output all files (eg. source files from
        # a copy operation are skipped), so we have to parse the default output
        modified_files = [
            line[2:]  # strip leading status char and space
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
                if self.args.lesscontext or file_size > environment.MAX_CONTEXT_SIZE:
                    context_size = 100
                else:
                    context_size = environment.MAX_CONTEXT_SIZE

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

                if file_size > environment.MAX_CONTEXT_SIZE / 2:
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
        change.file_type = Diff.FileType("TEXT")
        if not lines:
            return

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
