# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import re

from mozphab import arcanist, environment

from mozphab.conduit import conduit
from mozphab.config import config
from mozphab.exceptions import Error
from mozphab.helpers import (
    augment_commits_from_body,
    BLOCKING_REVIEWERS_RE,
    parse_arc_diff_rev,
    prompt,
    strip_differential_revision,
    temporary_file,
    update_commit_title_previews,
)
from mozphab.logger import logger
from mozphab.spinner import wait_message
from mozphab.subprocess_wrapper import check_call_by_line

DEFAULT_UPDATE_MESSAGE = "Revision updated."

REVISION_URL_RE = re.compile(r"^\s*Revision URI: (http.+)$", flags=re.MULTILINE)
PHABRICATOR_URLS = {
    "https://phabricator.services.mozilla.com/": "Phabricator",
    "https://phabricator-dev.allizom.org/": "Phabricator-Dev",
}

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


def extract_revision_url(line):
    m = REVISION_URL_RE.search(line)
    if m:
        return m.group(1)


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


def amend_revision_url(body, new_url):
    """Append or replace the Differential Revision URL in a commit body."""
    body = strip_differential_revision(body)
    if body:
        body += "\n"
    body += "\nDifferential Revision: %s" % new_url
    return body


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
        closed = False
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

                    # Check if revision is closed
                    closed = revision["fields"]["status"]["closed"]

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

            if closed:
                logger.warning(
                    "!! This revision is closed!\n"
                    "   It will be reopened if submission proceeds.\n"
                    "   You can stop now and refine the stack range."
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
            granted = reviewers.copy()
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


def submit(repo, args):
    if environment.DEBUG:
        arcanist.ARC.append("--trace")

    with wait_message("Checking connection to Phabricator."):
        # Check if raw Conduit API can be used
        if not conduit.check():
            raise Error("Failed to use Conduit API")

        # Check if local and remote VCS matches
        repo.check_vcs()

        # Check if arc is configured
        if not args.no_arc and not repo.check_arc():
            raise Error("Failed to run %s." % arcanist.ARC_COMMAND)

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
                    arcanist.ARC + arc_args, cwd=repo.path, never_log=True
                ):
                    print(line)
                    revision_url = extract_revision_url(line) or revision_url

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
                        conduit.update_revision_reviewers(transactions, commit)
                        if current_status != "needs-review":
                            transactions.append(dict(type="request-review"))

                    if transactions:
                        arcanist.call_conduit(
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


def add_parser(parser):
    submit_parser = parser.add_parser(
        "submit",
        aliases=["upload"],
        help="Submit commit(s) to Phabricator.",
        description=(
            "MozPhab will change the working directory and amend the commits during "
            "the submission process."
        ),
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
        "--single", "-s", action="store_true", help="Submit a single commit.",
    )
    submit_parser.add_argument(
        "start_rev",
        nargs="?",
        default=environment.DEFAULT_START_REV,
        help="Start revision of range to submit (default: detected)",
    )
    submit_parser.add_argument(
        "end_rev",
        nargs="?",
        default=environment.DEFAULT_END_REV,
        help="End revision of range to submit (default: current commit)",
    )

    submit_parser.set_defaults(func=submit, needs_repo=True)
