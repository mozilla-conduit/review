import argparse
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Union

from packaging.version import Version

from mozphab import environment

from .commits import Commit
from .config import config
from .diff import Diff
from .exceptions import CommandError, Error
from .git import Git
from .helpers import (
    is_valid_email,
    short_node,
    temporary_binary_file,
    temporary_file,
)
from .logger import logger
from .repository import Repository
from .spinner import wait_message
from .subprocess_wrapper import check_call, check_output, subprocess


class Jujutsu(Repository):
    MIN_VERSION = Version("0.33.0")

    @classmethod
    def is_repo(cls, path: str) -> bool:
        """Quick check for repository at specified path."""
        return os.path.exists(os.path.join(path, ".jj"))

    # ----
    # Methods expected from callers of the `Repository` interface:
    # ----

    def __init__(self, path: str):
        dot_path = os.path.join(path, ".jj")
        if not os.path.exists(dot_path):
            raise ValueError(f"{path}: not a Jujutsu repository")

        logger.debug("found Jujutsu repo in %s", path)

        version_str, version = self.__check_and_get_version()
        self.vcs_version = version_str
        self.__version = version

        resolved_path = Path(path).resolve(strict=True)
        logger.debug(f"resolved_path: {resolved_path}")

        try:
            self.git_path = Path(
                self.__check_output(["jj", "git", "root"], split=False)
            )
        except Exception:
            raise ValueError(
                f"{path}: failed to run `jj git root`, likely not a Jujutsu repository"
            )
        logger.debug(f"git_path: {self.git_path}")

        try:
            is_colocated = (
                resolved_path == self.git_path.parent and self.git_path.name == ".git"
            )
            logger.debug(f"is_colocated: {is_colocated}")
            bare_path = None if is_colocated else str(self.git_path)
            self.__git_repo = Git(path, bare_path=bare_path)
        except Exception:
            raise ValueError(
                f"internal error: failed to initialize Git repo from {self.git_path}"
            )

        # Populate common fields expected from a `Repository`
        super().__init__(path, dot_path)

        self.vcs = "jj"

        self.revset = None
        self.branch = None

        self.__email = self.__check_output(
            ["jj", "config", "get", "user.email"], split=False
        ).rstrip()

    def __check_and_get_version(self) -> Tuple[str, Version]:
        min_version = Jujutsu.MIN_VERSION

        version_re = re.compile(r"jj (\d+\.\d+\.\d+)(?:-[a-fA-F0-9]{40})?")
        try:
            jj_version_output = self.__check_output(["jj", "version"], split=False)
        except FileNotFoundError as exc:
            if exc.filename == "jj":
                raise Error("`jj` executable was not found.")
            raise exc

        m = version_re.fullmatch(jj_version_output)
        if not m:
            raise Error("Failed to determine Jujutsu version.")
        version = Version(m.group(1))

        if version < min_version:
            raise Error(f"`moz-phab` requires Jujutsu {min_version} or higher.")
        return m.group(0), version

    def check_vcs(self) -> bool:
        if self.args.force_vcs:
            return True

        if self.is_cinnabar_required and not self.is_cinnabar_installed:
            raise Error(
                "Git Cinnabar extension is required to work on this repository."
            )

        return True

    def before_submit(self):
        self.__validate_email()

    def set_args(self, args: argparse.Namespace):
        """Store moz-phab command line args and set the revset."""
        super().set_args(args)

        is_single = hasattr(self.args, "single") and self.args.single

        if hasattr(self.args, "start_rev"):
            start_rev = None
            if self.args.start_rev != environment.DEFAULT_START_REV:
                start_rev = self.args.start_rev
            else:
                start_rev = (
                    self.__get_last_stack_change()
                    if is_single
                    else self.__get_first_stack_change()
                )
            if not start_rev:
                return None

            end_rev = None
            if is_single:
                end_rev = start_rev
            elif (
                hasattr(self.args, "end_rev")
                and self.args.end_rev != environment.DEFAULT_END_REV
            ):
                end_rev = self.args.end_rev
            else:
                end_rev = self.__get_last_stack_change()
            if not end_rev:
                return None

            self.revset = (start_rev, end_rev)

    def commit_stack(self, single: bool = False) -> Optional[List[Commit]]:
        """Collect all the info about commits."""
        logger.debug(f"searching with start and end at {self.revset}")
        if not self.revset:
            # No commits found to submit
            return None

        boundary = "--%s--\n" % uuid.uuid4().hex
        log = self.__cli_log(
            use_reversed=True,
            template="".join(
                [
                    "separate('\n', ",
                    "author.timestamp().utc().format('%+'), ",
                    "author.name(), ",
                    "author.email(), ",
                    "self.parents().map(|c| c.commit_id().short()).join(' '), ",
                    "change_id.short(), ",
                    "commit_id, ",
                    "conflict, ",
                    "description, ",
                    f'"{boundary}"',
                    ")",
                ]
            ),
            revset="({})::({})".format(self.revset[0], self.revset[1]),
            split=False,
            strip=False,
        )[: -len(boundary)]
        changes = log.split(boundary)

        commits = []
        first_commit_id = None
        for log_line in changes:
            if not log_line:
                continue  # TODO: Why would we need this? This seems suspicious.

            (
                author_date,
                author_name,
                author_email,
                parents,
                change_id,
                commit_id,
                is_conflicted,
                desc,
            ) = log_line.split("\n", 7)
            desc = desc.splitlines()

            tree_hash = self.__git_repo._revparse(commit_id + ":./")

            parents = parents.split(" ")
            if first_commit_id != commit_id and len(parents) > 1:
                raise Error(
                    f"Multiple parents found for change {change_id}, unable to continue"
                )
            first_commit_id = first_commit_id or commit_id

            is_conflicted = Jujutsu.__parse_log_bool(
                "`conflict` template segment", is_conflicted
            )
            if is_conflicted:
                raise Error(f"Change {change_id} is conflicted, unable to continue")

            commit_epoch = datetime.fromisoformat(author_date).timestamp()
            commits.append(
                Commit(
                    name=change_id,
                    node=commit_id,
                    orig_node=commit_id,
                    submit=True,
                    title=desc[0],
                    title_preview=desc[0],
                    body="\n".join(desc[1:]).rstrip(),
                    bug_id=None,
                    reviewers={"request": [], "granted": []},
                    rev_id=None,
                    parent=parents[0],
                    tree_hash=tree_hash,
                    author_date=author_date,
                    author_date_epoch=commit_epoch,
                    author_name=author_name,
                    author_email=author_email,
                )
            )

        return commits

    def after_submit(self):
        # `moz-phab submit` operations on the Jujutsu backend don't change the change [sic] that the
        # current workspace is working with—including the Git commands we issue, which should be
        # read-only. So, we don't have to do anything. Yay!
        pass

    def untracked(self) -> List[str]:
        is_working_copy_descriptionless_but_changed = self.__cli_log(
            revset="@",
            template="self.description().len() == 0 && !self.empty()",
            split=False,
        )
        is_working_copy_descriptionless_but_changed = Jujutsu.__parse_log_bool(
            "check for descriptionless-but-changed working copy",
            is_working_copy_descriptionless_but_changed,
        )
        if is_working_copy_descriptionless_but_changed:
            return self.__cli_log(
                revset="@",
                template='self.diff().files().map(|f| f.path()).join("\n")',
            )
        return []

    def get_diff(self, commit: Commit) -> Diff:
        """Create a Diff object with changes."""
        # NOTE: If we don't do this, then we break on a `lesscontext` member missing from `args`.
        self.__git_repo.args = self.args
        return self.__git_repo.get_diff(commit)

    def amend_commit(self, commit: Commit, commits: List[Commit]):
        """Amend the commit with an updated message.

        Changing commit's message changes also its SHA1.
        All the children within the stack and bookmarks are then updated
        to keep the history.

        Args:
            commit: Information about the commit to be amended
            commits: List of commits within the stack
        """
        updated_message = f"{commit.title}\n{commit.body}"

        # # TODO: apply this optimization
        # current_body = self.git_out(
        #     ["show", "-s", "--format=%s%n%b", commit.node], split=False
        # )
        # if current_body == updated_body:
        #     logger.debug("not amending commit %s, unchanged", commit.name)
        #     return

        change_id = commit.name
        # TODO: It would be nice to open an issue to drive amending in bulk, rather than
        # `describe`-ing and rebasing all descendants with each change (potentially); `jj describe`
        # on multiple commits can edit the message for _all_ changes in the stack in one go, if we
        # need it.
        with temporary_file(updated_message) as message_path:
            with open(message_path) as message_file:
                check_call(["jj", "describe", change_id, "--stdin"], stdin=message_file)

    def finalize(self, commits: List[Commit]):
        pass

    def cleanup(self):
        # NOTE: This backend only `jj describe`s changes, and doesn't change them, so we haven't
        # changed any state that we might need to restore.
        pass

    def refresh_commit_stack(self, commits: List[Commit]):
        # TODO: update `commit[n].node` to match current `commit_id`s
        pass

    @property
    def is_cinnabar_installed(self) -> bool:
        """Check if Cinnabar extension is callable."""
        return self.__git_repo.is_cinnabar_installed

    @property
    def is_cinnabar_required(self) -> bool:
        """Check if local VCS is different than the remote one."""
        return self.__git_repo.is_cinnabar_required

    def get_public_node(self, node: str) -> str:
        """Return a Mercurial node if Cinnabar is required."""
        return self.__git_repo.get_public_node(node)

    # TODO: Functionality to make `local_uplift_if_possible` work?

    def is_worktree_clean(self) -> bool:
        """Check if the working tree is clean."""
        check_call(["jj", "git", "export"])
        return True

    def check_node(self, node: str) -> str:
        """Check if the node exists.

        Consults `jj log --revisions $node` first, and:

        1. If multiple commits are found, return an error.
        2. If a single commit is found, return it as a node.
        3. If the CLI errors (which happens when there is no match,
           among other things), then fall back to the Git backend's
           behavior.

        Raises NotFoundError if none of the above yield a single commit.
        """
        try:
            commits = self.__cli_log(template='commit_id ++ "\\n"', revset=node)
            if len(commits) > 1:
                raise Error(
                    f"Multiple commits match revset `{node}`, unable to continue"
                )
            return commits[0]
        except CommandError:
            # Call the git backend as well, as it will try to resolve any
            # cinnabar hashes.
            return self.__git_repo.check_node(node)

    def before_patch(self, node: str, name: str):
        """Prepare repository to apply the patches.

        Args:
            node - SHA1 of the base commit
            name - name of the bookmark to be created
        """

        if node:
            with wait_message("Checking out %s.." % short_node(node)):
                check_call(["jj", "new", node])
            logger.info("Checked out %s", short_node(node))

        if name and not self.args.no_branch and config.create_branch:
            branches = set(
                self.__check_output(
                    ["jj", "bookmark", "list", "--template", 'name ++ "\n"'],
                    strip=False,
                )
            )
            branches = [re.sub("[ *]", "", b) for b in branches]
            branch_name = name
            i = 0
            while branch_name in branches:
                i += 1
                branch_name = "%s_%s" % (name, i)

            check_call(["jj", "bookmark", "create", "--revision=@", branch_name])
            logger.info("Created bookmark %s", branch_name)
            self.__patch_branch_name = branch_name

    def apply_patch(
        self, diff: str, body: str, author: Optional[str], author_date: Optional[int]
    ):
        # NOTE: `before_patch` ensures that we are editing a new, empty commit on the base we want.

        # apply the patch as a binary file to ensure the correct line endings
        # is used.
        # TODO: Use `jj`'s built-in patching facilities when it exists (see
        # <https://github.com/martinvonz/jj/issues/2702>).
        with temporary_binary_file(diff.encode("utf8")) as patch_file:
            # NOTE: We avoid `self.__git_repo.git_call` because it changes the CWD.
            self.__git_repo.git.call(["apply", patch_file], cwd=self.path)

        # TODO: author date: <https://bugzilla.mozilla.org/show_bug.cgi?id=1976915>
        # TODO: dedupe with other `describe` usage
        with temporary_file(body) as message_path:
            with open(message_path) as message_file:
                check_call(["jj", "describe", "--stdin"], stdin=message_file)
        check_call(["jj", "metaedit", "--author", author])

        check_call(["jj", "new"])

        if not self.args.no_branch and config.create_branch:
            # # Advance the bookmark we created for this patch. Because we know we're creating
            # entirely new commits, and we have the branch at the parent commit, we can let `--from`
            # compensate for the fact that we otherwise don't remember the name of the branch.

            check_call(["jj", "bookmark", "move", self.__patch_branch_name, "--to=@-"])

    def format_patch(
        self, diff: str, body: str, author: Optional[str], author_date: Optional[int]
    ) -> str:
        return diff

    # ----
    # Methods private to this abstraction.
    # ----

    def __cli_log(
        self, *, revset: str, template: str, use_reversed: bool = False, **kwargs
    ) -> Union[List[str], str]:
        options = []
        if use_reversed:
            options.append("--reversed")
        return self.__check_output(
            [
                "jj",
                "log",
                "--no-pager",
                "--no-graph",
                "--revisions",
                revset,
                "--template",
                template,
            ]
            + options
            + ["--"],
            **kwargs,
        )

    def __validate_email(self):
        """Validate the user's configured email (user.email)."""

        if not is_valid_email(self.__email):
            raise Error(
                f"Your email configured with Jujutsu ({self.git.email}) is not a valid "
                f"format.\n"
                f"Please run `jj config set user.email someone@example.com …` to set "
                f"the correct value.\n"
                # NOTE: Unlike with a Git repository, it's not easy to specify a custom author from
                # the CLI. There is upstream work to track a better CLI for this:
                # <https://github.com/martinvonz/jj/issues/4170>
            )

    def __get_last_stack_change(self) -> Optional[str]:
        """Gets the last of the current stack of mutable changes, but _only_ if there's one."""
        # TODO: Should we do something different when `config.git_remote` or `self.args.upstream`
        # are specified? Compare with `remotes` checks in Git impl.

        revset = 'heads(immutable()..@- | present(@ ~ description(exact:"")))'
        mutable_roots = self.__cli_log(template='change_id ++ "\\n"', revset=revset)
        if not mutable_roots:
            return None
        elif len(mutable_roots) > 1:
            raise Error(
                f"Multiple mutable heads found (revset `{revset}`), unable to continue"
            )
        return mutable_roots[0]

    def __get_first_stack_change(self) -> Optional[str]:
        """Gets the first of the current stack of mutable changes, but _only_ if there's one."""
        # TODO: Should we do something different when `config.git_remote` or `self.args.upstream`
        # are specified? Compare with `remotes` checks in Git impl.

        revset = 'roots(immutable()..@- | present(@ ~ description(exact:"")))'
        mutable_roots = self.__cli_log(template='change_id ++ "\\n"', revset=revset)
        if not mutable_roots:
            return None
        elif len(mutable_roots) > 1:
            raise Error(
                f"Multiple mutable roots found (revset `{revset}`), unable to continue"
            )
        return mutable_roots[0]

    @staticmethod
    def __parse_log_bool(name: str, s: str) -> bool:
        if s not in ["true", "false"]:
            raise Error(f"internal error: {name} was not `true` or `false`")
        return s == "true"

    def __check_output(self, *args, **kwargs) -> Union[List[str], str]:
        return check_output(*args, stderr=subprocess.PIPE, **kwargs)
