import argparse
from datetime import datetime
from functools import lru_cache
import os
import re
from typing import (
    List,
    Optional,
)
import uuid

from mozphab import environment

from .commits import Commit
from .diff import Diff
from .exceptions import Error
from .git import Git
from .gitcommand import GitCommand
from .helpers import (
    create_hunk_lines,
    is_valid_email,
    temporary_file,
)
from .logger import logger
from .repository import Repository
from .subprocess_wrapper import check_call, check_output


class Jujutsu(Repository):
    # ----
    # Methods expected from callers of the `Repository` interface:
    # ----

    def __init__(self, path: str):
        # NOTE: We expect a co-located Git repository alongside the Jujutsu repository, so let's
        # start up a Git backend to handle the pieces we need.
        self.__git_repo = Git(path)

        # Populate common fields expected from a `Repository`

        dot_path = os.path.join(path, ".jj")
        if not os.path.exists(dot_path):
            raise ValueError("%s: not a Jujutsu repository" % path)
        logger.debug("found Jujutsu repo in %s", path)
        super().__init__(path, dot_path)

        self.vcs = "jj"

        version_output = check_output(["jj", "--version"], split=False)
        m = re.search(r"jj (\d+\.\d+\.\d+.*)", version_output)
        if not m:
            raise Error("Failed to determine Jujutsu version.")
        self.vcs_version = m.group(0)

        self.revset = None
        self.branch = None

        # Populate fields unique to this implementation
        # TODO?

        self.__username = check_output(
            ["jj", "config", "get", "user.name"], split=False
        ).rstrip()
        self.__email = check_output(
            ["jj", "config", "get", "user.email"], split=False
        ).rstrip()
        self.__previous_change_ids = None
        self.__change_id_at_start_was_empty = None

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

        current_revision_is_empty = None
        try:
            current_revision_is_empty = self.__cli_log(
                revset="@", template="empty", split=False
            ).rstrip()
        except:
            pass
        if current_revision_is_empty not in ["false", "true"]:
            raise Error(
                "Failed to parse current revision for unknown reasons."
            )  # TODO: casing, etc. consistent?
        self.__change_id_at_start_was_empty = current_revision_is_empty == "true"

        try:
            revset = "@-" if self.__change_id_at_start_was_empty else "@"
            self.__previous_change_ids = self.__cli_log(
                revset="@", template="change_id"
            )
        except Exception:
            raise Error("Jujutsu failed to read the branch name for unknown reasons.")

    def set_args(self, args: argparse.Namespace):
        """Store moz-phab command line args and set the revset."""
        super().set_args(args)

        # TODO: Did we accidentally change the behavior of `--single` with no `start_rev` specified?
        is_single = hasattr(self.args, "single") and self.args.single

        # TODO: What if we try to submit something that's immutable?
        start_rev = None
        if (
            hasattr(self.args, "start_rev")
            and self.args.start_rev != environment.DEFAULT_START_REV
        ):
            start_rev = self.args.start_rev
        else:
            start_rev = "@-" if is_single else self.__get_first_mutable_change()

        end_rev = None
        if is_single:
            end_rev = start_rev
        elif (
            hasattr(self.args, "end_rev")
            and self.args.end_rev != environment.DEFAULT_END_REV
        ):
            end_rev = self.args.end_rev
        else:
            end_rev = start_rev

        self.revset = (start_rev, end_rev)

    def commit_stack(self, single: bool = False) -> Optional[List[Commit]]:
        """Collect all the info about commits."""
        logger.debug(f"searching with start and end at {self.revset}")
        if not self.revset:
            # No commits found to submit
            return None

        boundary = '"--%s--\n"' % uuid.uuid4().hex
        log = self.__cli_log(
            # XXX: Some duplication with `self.__cli_log`. Dedupe?
            template="".join(
                [
                    "separate('\n', ",
                    "author.timestamp().utc().format('%+'), ",
                    "author.name(), " "author.email(), ",
                    "self.parents().map(|c| c.commit_id().short()).join(' '), ",
                    "change_id.short(), ",
                    "commit_id, ",
                    "conflict, ",
                    "description, ",
                    boundary,
                    ")",
                ]
            ),
            revset="{}::{}".format(self.revset[0], self.revset[1]),
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

            tree_hash = check_output(
                ["git", "rev-parse", commit_id + ":./"], split=False, strip=False
            ).rstrip()

            parents = parents.split(" ")
            if first_commit_id != commit_id and len(parents) > 1:
                raise Error(
                    "Multiple parents found for change %s, unable to continue"
                    % change_id
                )
            first_commit_id = first_commit_id or commit_id

            if is_conflicted not in ["true", "false"]:
                raise Error(
                    "internal error: `conflict` template segment not `true` or `false`"
                )
            if is_conflicted == "true":
                raise Error("Change %s is conflicted, unable to continue" % change_id)

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
        # Restore the previously active commit.
        verb = "new" if self.__change_id_at_start_was_empty else "new"
        # NOTE: `jj` upstream currently doesn't have a way to make this be `--quiet`-ish, but if it
        # does add it, it'd be nice to use here.
        check_call(["jj", verb] + self.self.__previous_change_ids, strip=False)

    def untracked(self) -> List[str]:
        # Jujutsu snapshots files automatically, so there's no risk of losing anything. Report
        # nothing untracked.
        #
        # TODO: Think about a concrete bug model to disclaim here.
        return []

    def checkout(self, node: str):
        # The purpose of this method (in general) is to change local state so that a new message can
        # be applied to a change/commit/revision. Because Jujutsu can update _any_ change's message
        # without changing what's checked out, we don't need to do anything here.
        pass

    def get_diff(self, commit: Commit) -> Diff:
        """Create a Diff object with changes."""
        return self.__git_repo.get_diff(commit)

    def amend_commit(self, commit: Commit, commits: List[Commit]):
        """Amend the commit with an updated message.

        Changing commit's message changes also its SHA1.
        All the children within the stack and branches are then updated
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

    def after_submit(self):
        # NOTE: This backend only `jj describe`s changes, and doesn't change them, so we haven't
        # changed any state that we might need to restore.
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

    def _hg_to_git(self, node: str) -> Optional[str]:
        return self.__git_repo(node)

    @lru_cache(maxsize=None)  # noqa: B019
    def _git_to_hg(self, node: str) -> Optional[str]:
        """Convert Git hashtag to Mercurial."""
        return self.__git_repo._git_to_hg(node)

    # TODO: Functionality to make `local_uplift_if_possible` work?

    # ----
    # Methods private to this abstraction.
    # ----

    def __cli_log(self, *, revset: str, template: str, **kwargs):
        return check_output(
            [
                "jj",
                "log",
                "--no-pager",
                "--no-graph",
                "--revisions",
                revset,
                "--template",
                template,
            ],
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

    def __get_first_mutable_change(self):
        """Check which commits should be pushed and return the oldest one."""
        # TODO: Should we do something different when `config.git_remote` or `self.args.upstream`
        # are specified? Compare with `remotes` checks in Git impl.

        revset = "roots(immutable()..@-)"
        mutable_roots = self.__cli_log(template='change_id ++ "\\n"', revset=revset)
        if not mutable_roots:
            raise Error(
                f"No mutable roots found (revset `{revset}`), unable to continue"
            )
        elif len(mutable_roots) > 1:
            raise Error(
                f"Multiple mutable roots found (revset `{revset}`), unable to continue"
            )
        return mutable_roots[0]
