# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import mimetypes
import os
import re
import subprocess
import sys
import uuid

from datetime import datetime

from mozphab import environment

from .config import config
from .diff import Diff
from .exceptions import CommandError, Error, NotFoundError
from .gitcommand import GitCommand
from .helpers import (
    prompt,
    short_node,
    temporary_binary_file,
    temporary_file,
    create_hunk_lines,
)
from .logger import logger
from .repository import Repository
from .spinner import wait_message
from .telemetry import telemetry

NULL_SHA1 = "0" * 40


class Git(Repository):
    def __init__(self, path):
        dot_path = os.path.join(path, ".git")
        if not os.path.exists(dot_path):
            raise ValueError("%s: not a git repository" % path)

        logger.debug("found git repo in %s", path)

        self.git = GitCommand()

        if os.path.isfile(dot_path):
            # We're working from a worktree. Let's find the dot_path directory.
            dot_path = self.git_out(
                ["rev-parse", "--git-common-dir"], path=path, split=False
            )

        super().__init__(path, dot_path)

        self.vcs = "git"
        m = re.search(r"\d+\.\d+\.\d+", self.git.output(["--version"], split=False))
        if not m:
            raise Error("Failed to determine Git version.")

        self.vcs_version = m.group(0)
        self.revset = None
        self.branch = None

    @property
    def is_cinnabar_installed(self):
        """Check if Cinnabar extension is callable."""
        return self.git.is_cinnabar_installed

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

    def is_index_modified(self):
        """Are there any changes added to the staging area."""
        return bool(self.git.output(["diff-index", "HEAD"]))

    def is_worktree_clean(self):
        return all(
            [l.startswith("?? ") for l in self.git_out(["status", "--porcelain"])]
        )

    def before_submit(self):
        if self.is_index_modified():
            raise Error(
                "Uncommitted changes present. "
                "Please stash them or commit before submitting."
            )

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

    def git_call(self, command, **kwargs):
        """Call git from the repository path."""
        self.git.call(command, cwd=self.path, **kwargs)

    def git_out(self, command, path=None, extra_env=None, **kwargs):
        """Call git from the repository path and return the result."""
        return self.git.output(
            command, cwd=path or self.path, extra_env=extra_env, **kwargs
        )

    def cleanup(self):
        self.git_call(["gc", "--auto", "--quiet"])
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

        self.git.set_args(args)
        if hasattr(self.args, "start_rev"):
            is_single = hasattr(self.args, "single") and self.args.single
            if self.args.start_rev == environment.DEFAULT_START_REV:
                if is_single:
                    start_rev = "HEAD"
                else:
                    start_rev = self._get_first_unpublished_node()
            else:
                start_rev = self.args.start_rev

            if start_rev is None:
                return None

            # We want inclusive range of commits if start commit is detected
            if self.args.start_rev == environment.DEFAULT_START_REV or is_single:
                start = "%s^" % start_rev
            else:
                start = start_rev

            end = start_rev if is_single else self.args.end_rev
            self.revset = (start, end)

    def _git_get_children(self, node):
        """Get commits SHA1 with their children.

        Args:
            node: The SHA1 of a node to check for all children

        Returns: A list of "aaaa bbbb cccc"" strings, where bbbb and cccc are
            SHA1 of direct children of aaaa
        """

        # Logging is disabled for this command as it can generate a _lot_ of
        # results, especially on mozilla-central.
        return self.git_out(
            ["rev-list", "--all", "--children", "--not", "%s^@" % node], never_log=True
        )

    @staticmethod
    def _get_direct_children(node, rev_list):
        """Return direct children of the commit.

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

    def commit_stack(self, single=False):
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

            if not single:
                # Check if the commit is a child of the first one
                if rev_list is None:
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

            # Tue, 14 Apr 2020 12:02:20 +0000
            commit_epoch = datetime.strptime(
                author_date, "%a, %d %b %Y %H:%M:%S %z"
            ).timestamp()
            commits.append(
                {
                    "name": short_node(node),
                    "node": node,
                    "orig-node": node,
                    "submit": True,
                    "title": desc[0],
                    "title-preview": desc[0],
                    "body": "\n".join(desc[1:]).rstrip(),
                    "bug-id": None,
                    "reviewers": dict(request=[], granted=[]),
                    "rev-id": None,
                    "parent": parents[0],
                    "tree-hash": tree_hash,
                    "author-date": author_date,
                    "author-date-epoch": commit_epoch,
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
        self.git_call(["checkout", "--quiet", node])

    def commit(self, body, author=None, author_date=None):
        """Commit the changes in the working directory."""
        commands = ["commit", "-a"]
        if author:
            commands.append('--author="%s"' % author)

        if author_date:
            commands.append('--date="format:raw:%s 0"' % author_date)

        with temporary_file(body) as temp_f:
            commands += ["-F", temp_f]
            self.git_call(commands)

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

            self.git_call(["checkout", "-q", "-b", branch_name])
            logger.info("Created branch %s", branch_name)

    def apply_patch(self, diff, body, author, author_date):
        # apply the patch as a binary file to ensure the correct line endings
        # is used.
        with temporary_binary_file(diff.encode("utf8")) as patch_file:
            self.git_call(["apply", "--index", patch_file])

        self.commit(body, author, author_date)

    def format_patch(self, diff, body, author, author_date):
        return diff

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
        self.git_call(["rebase", "--quiet", "--onto", newbase, upstream])

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
        telemetry.metrics.mozphab.submission.files_size.accumulate(file_size)

        # Detect if we're binary, and generate a unified diff
        if b"\0" in a_body or b"\0" in b_body or file_size > environment.MAX_TEXT_SIZE:
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
            change.set_as_binary(
                a_body=a_body,
                a_mime=mimetypes.guess_type(a_path)[0] or "",
                b_body=b_body,
                b_mime=mimetypes.guess_type(b_path)[0] or "",
            )

        else:
            # We can only diff changed blobs.
            if a_blob == b_blob:
                # No changes in the file contents.
                lines = create_hunk_lines(a_body, " ", False)[0]
                if lines:
                    change.hunks.append(
                        Diff.Hunk(
                            old_off=1,
                            old_len=len(lines),
                            new_off=1,
                            new_len=len(lines),
                            lines=lines,
                        )
                    )
            elif a_blob is None:
                # The file is created.
                lines, eof_missing_newline = create_hunk_lines(b_body, "+")
                if lines:
                    new_len = len(lines)
                    if eof_missing_newline:
                        new_len -= 1
                    change.hunks.append(
                        Diff.Hunk(
                            old_off=0,
                            old_len=0,
                            new_off=1,
                            new_len=new_len,
                            lines=lines,
                        )
                    )

            elif b_blob is None:
                # The file is removed.
                lines, eof_missing_newline = create_hunk_lines(a_body, "-")
                if lines:
                    old_len = len(lines)
                    if eof_missing_newline:
                        old_len -= 1
                    change.hunks.append(
                        Diff.Hunk(
                            old_off=1,
                            old_len=old_len,
                            new_off=0,
                            new_len=0,
                            lines=lines,
                        )
                    )

            else:
                # There are changes in the file.
                if self.args.lesscontext or file_size > environment.MAX_CONTEXT_SIZE:
                    context_size = 100
                else:
                    context_size = environment.MAX_CONTEXT_SIZE

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
                git_diff = self.git_out(diff_args, expect_binary=True).decode("utf-8")
                change.from_git_diff(git_diff)

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
        if self.args.force_vcs:
            return True

        if self.is_cinnabar_required and not self.is_cinnabar_installed:
            raise Error(
                "Git Cinnabar extension is required to work on this repository."
            )

        return True
