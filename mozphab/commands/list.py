# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
from dataclasses import dataclass
from enum import Enum

from mozphab.conduit import RevisionStatus, conduit
from mozphab.exceptions import Error
from mozphab.logger import logger
from mozphab.repository import Repository
from mozphab.spinner import wait_message

# The statuses listed when `--status` isn't passed.
DEFAULT_STATUSES = frozenset(
    {
        RevisionStatus.NEEDS_REVIEW,
        RevisionStatus.NEEDS_REVISION,
        RevisionStatus.ACCEPTED,
        RevisionStatus.CHANGES_PLANNED,
    }
)

# The statuses `--status` accepts.
STATUS_CHOICES = (
    RevisionStatus.NEEDS_REVIEW,
    RevisionStatus.NEEDS_REVISION,
    RevisionStatus.ACCEPTED,
    RevisionStatus.CHANGES_PLANNED,
    RevisionStatus.ABANDONED,
    RevisionStatus.PUBLISHED,
)


class OutputFormat(str, Enum):
    """The formats `list` can print the revisions in."""

    TEXT = "text"
    JSON = "json"


@dataclass(frozen=True)
class ListOptions:
    """The command line flags that control `list`, resolved once."""

    statuses: frozenset[RevisionStatus] = DEFAULT_STATUSES
    verbose: bool = False
    output_format: OutputFormat = OutputFormat.TEXT

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ListOptions":
        """Build the options from the parsed command line."""
        statuses = {
            RevisionStatus(status) for status in args.status or DEFAULT_STATUSES
        }
        if args.include_abandoned:
            statuses.add(RevisionStatus.ABANDONED)
        if args.include_published:
            statuses.add(RevisionStatus.PUBLISHED)

        return cls(
            statuses=frozenset(statuses),
            verbose=args.verbose,
            output_format=OutputFormat(args.format),
        )


def format_revision_status(status: str) -> str:
    """Format status string for display."""
    status_label_mapping = {
        "needs-review": "Needs Review",
        "needs-revision": "Needs Revision",
        "accepted": "Accepted",
        "changes-planned": "Changes Planned",
        "abandoned": "Abandoned",
        "published": "Published",
    }
    return status_label_mapping.get(status, status.title())


def list_revisions(repo: Repository, args: argparse.Namespace):
    """List in-flight patches for the current user."""
    options = ListOptions.from_args(args)

    # Check connection to Phabricator
    with wait_message("Checking connection to Phabricator..."):
        if not conduit.check():
            raise Error("Failed to use Conduit API")

    # Get current user's PHID
    with wait_message("Fetching user information..."):
        whoami = conduit.whoami()
        user_phid = whoami.get("phid")
        if not user_phid:
            raise Error("Unable to determine current user")

    with wait_message("Fetching revisions..."):
        revisions = conduit.get_revisions_for_author(
            user_phid, statuses=sorted(status.value for status in options.statuses)
        )

    if not revisions:
        if options.output_format is OutputFormat.JSON:
            print(json.dumps([], indent=2))
        else:
            logger.info("No revisions found.")
        return

    # Format as JSON if requested
    if options.output_format is OutputFormat.JSON:
        output_data = []
        for revision in revisions:
            rev_id = revision["id"]
            fields = revision["fields"]

            # Build JSON object
            rev_data = {
                "id": rev_id,
                "title": fields["title"],
                "status": fields["status"]["value"],
                "uri": fields["uri"],
                "dateCreated": fields.get("dateCreated"),
                "dateModified": fields.get("dateModified"),
            }

            # Add reviewers if verbose
            if options.verbose:
                reviewers_data = revision["attachments"]["reviewers"]["reviewers"]
                rev_data["reviewers"] = [
                    {
                        "phid": r.get("reviewerPHID"),
                        "status": r.get("status"),
                        "isBlocking": r.get("isBlocking", False),
                    }
                    for r in reviewers_data
                ]

            output_data.append(rev_data)

        print(json.dumps(output_data, indent=2))
        return

    # Display results in human-readable format
    logger.info("Revisions:\n")

    # Resolve reviewer PHIDs to usernames up front if verbose
    phid_to_username: dict[str, str] = {}
    if options.verbose:
        all_phids = set()
        for revision in revisions:
            for reviewer in revision["attachments"]["reviewers"]["reviewers"]:
                phid = reviewer.get("reviewerPHID")
                if phid:
                    all_phids.add(phid)
        if all_phids:
            with wait_message("Fetching reviewer information..."):
                phid_to_username = conduit.get_usernames_for_phids(list(all_phids))

    for revision in revisions:
        rev_id = revision["id"]
        fields = revision["fields"]
        title = fields["title"]
        status = fields["status"]["value"]
        status_display = format_revision_status(status)

        # Get reviewer info
        reviewers_data = revision["attachments"]["reviewers"]["reviewers"]
        reviewers = []
        for reviewer in reviewers_data:
            reviewer_status = reviewer.get("status", "")
            phid = reviewer.get("reviewerPHID", "")
            username = phid_to_username.get(phid, phid)
            if reviewer_status == "accepted":
                reviewers.append(f"✓ {username}")
            elif reviewer_status == "rejected":
                reviewers.append(f"✗ {username}")
            else:
                reviewers.append(username)

        # Format output
        logger.info(f"D{rev_id}: {title}")
        logger.info(f"  Status: {status_display}")
        if reviewers and options.verbose:
            reviewer_display = ", ".join(reviewers[:3])
            if len(reviewers) > 3:
                reviewer_display += f", ... and {len(reviewers) - 3} more"
            logger.info(f"  Reviewers: {reviewer_display}")
        logger.info("")

    logger.info(f"Total: {len(revisions)} revision(s)")


def add_parser(parser):
    """Add the list command parser."""
    list_parser = parser.add_parser(
        "list",
        help="List in-flight patches for the current user.",
    )
    list_parser.add_argument(
        "--include-published",
        action="store_true",
        help="Include published (landed) revisions (default: False).",
    )
    list_parser.add_argument(
        "--include-abandoned",
        action="store_true",
        help="Include abandoned revisions (default: False).",
    )
    list_parser.add_argument(
        "--status",
        nargs="+",
        # Strings rather than `type=RevisionStatus`, so a bad value lists the choices.
        choices=[status.value for status in STATUS_CHOICES],
        help="Filter by specific status(es).",
    )
    list_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show additional information like reviewers (default: False).",
    )
    list_parser.add_argument(
        "--format",
        choices=[output_format.value for output_format in OutputFormat],
        default=OutputFormat.TEXT.value,
        help="Output format (default: text).",
    )
    list_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions.",
    )
    list_parser.set_defaults(
        func=list_revisions, needs_repo=True, needs_user_data=False
    )
