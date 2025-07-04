# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import re
from typing import List

from mozphab.conduit import conduit
from mozphab.exceptions import Error, NotFoundError
from mozphab.helpers import PHABRICATOR_URL_REVISION_PATTERN, prompt
from mozphab.logger import logger
from mozphab.spinner import wait_message


def check_revision_id(value: str) -> int:
    """Parse the revision ID from `value`.

    `value` is a `str` which is either `<id>`, `D<id>`,
    or a Phabricator revision URL.
    """
    # D123 or 123
    m = re.search(r"^D?(\d+)$", value)
    if m:
        return int(m.group(1))

    # Full URL
    m = re.search(r"^" + PHABRICATOR_URL_REVISION_PATTERN, value)
    if m:
        return int(m.group("rev"))

    # Invalid
    raise argparse.ArgumentTypeError(
        "Invalid Revision ID (expected number or URL): %s\n" % value
    )


def abandon_revisions(revision_ids: List[int], args: argparse.Namespace):
    """Abandon the specified revisions in Phabricator."""

    # Check connection to Phabricator
    with wait_message("Checking connection to Phabricator..."):
        if not conduit.check():
            raise Error("Failed to use Conduit API")

    # Get revision information
    with wait_message("Fetching revision information..."):
        try:
            revisions = conduit.get_revisions(ids=revision_ids)
        except NotFoundError as e:
            raise Error(f"Failed to fetch revisions: {e}")

    if not revisions:
        raise Error("No revisions found")

    # Filter out revisions that are already abandoned
    revisions_to_abandon = []
    already_abandoned = []

    for revision in revisions:
        status = revision["fields"]["status"]["value"]
        if status == "abandoned":
            already_abandoned.append(revision["id"])
        else:
            revisions_to_abandon.append(revision)

    # Show what will be abandoned
    if already_abandoned:
        logger.info(
            "Already abandoned: %s",
            ", ".join(f"D{rev_id}" for rev_id in already_abandoned),
        )

    if not revisions_to_abandon:
        logger.warning("All specified revisions are already abandoned.")
        return

    logger.warning("The following revisions will be abandoned:")
    for revision in revisions_to_abandon:
        logger.info(" * D%s: %s", revision["id"], revision["fields"]["title"])

    # Confirmation prompt
    if not args.yes:
        res = prompt("Abandon these revisions?", ["Yes", "No"])
        if res == "No":
            return

    # Abandon the revisions
    with wait_message("Abandoning revisions..."):
        for revision in revisions_to_abandon:
            transactions = [{"type": "abandon", "value": True}]
            conduit.apply_transactions_to_revision(
                rev_id=revision["phid"], transactions=transactions
            )
            logger.info("D%s abandoned", revision["id"])

    logger.warning("Completed: %d revision(s) abandoned", len(revisions_to_abandon))


def abandon(repo, args: argparse.Namespace):
    """Main function for the abandon command."""
    abandon_revisions(args.revisions, args)


def add_parser(parser):
    """Add the abandon command parser."""
    abandon_parser = parser.add_parser(
        "abandon", help="Abandon revisions in Phabricator."
    )
    abandon_parser.add_argument(
        "revisions",
        nargs="+",
        type=check_revision_id,
        help="Revision(s) to abandon. Supports IDs (123, D123) and URLs.",
    )
    abandon_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Abandon without confirmation (default: False).",
    )
    abandon_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions.",
    )
    abandon_parser.set_defaults(func=abandon, needs_repo=True)
