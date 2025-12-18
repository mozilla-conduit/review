# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
from pathlib import Path
from typing import Optional

from mozphab.conduit import (
    conduit,
)
from mozphab.exceptions import (
    Error,
)
from mozphab.logger import (
    logger,
)
from mozphab.repository import (
    Repository,
)

from .submit import (
    add_submit_arguments,
    submit,
)

UPLIFT_REPOSITORY_TAG_SLUG = "uplift"


def map_train_arg_to_repo(train_repo_shortname: str) -> dict:
    """Attempt to map the value of `--train` to a Phabricator repo."""
    repo = conduit.get_repository_by_shortname(train_repo_shortname)
    if not repo:
        raise Error(f"No repo named {train_repo_shortname} on Phabricator!")

    uplift_repos = conduit.get_repositories_with_tag(UPLIFT_REPOSITORY_TAG_SLUG)
    if not uplift_repos:
        raise Error("No uplift repos found on Phabricator! Please file a bug.")

    uplift_repo_shortnames = {uplift["fields"]["shortName"] for uplift in uplift_repos}

    if repo["fields"]["shortName"] not in uplift_repo_shortnames:
        raise Error(f"Repo {train_repo_shortname} is not an uplift repository.")

    return repo


def list_trains():
    """List all uplift repositories on Phabricator.

    Queries the Phabricator repository search endpoint for all repositories
    with the `uplift` tag set on them, and displays their shortnames as a list
    of valid values for `--train`.
    """
    repositories = conduit.get_repositories_with_tag(UPLIFT_REPOSITORY_TAG_SLUG)
    if not repositories:
        raise Error("Found no repos with `uplift` tag on Phabricator.")

    logger.info("Available trains for uplift:")
    for repository in repositories:
        logger.info(f"   - {repository['fields']['shortName']}")


def build_assessment_linking_url(
    lando_url: str, tip_commit_id: int, assessment_id: Optional[int] = None
) -> str:
    """Return the URL for linking revisions."""
    # Ensure `lando_url` doesn't have a trailing slash.
    base_url = lando_url.rstrip("/")

    # Build the URL with `revisions` parameter.
    url = f"{base_url}/uplift/request/?revisions={tip_commit_id}"

    # Add `assessment_id` if provided.
    if assessment_id is not None:
        url += f"&assessment_id={assessment_id}"

    return url


def uplift(repo: Repository, args: argparse.Namespace):
    if args.list_trains:
        return list_trains()

    if not args.train:
        raise Error("Missing `--train` argument!")

    phab_repo = map_train_arg_to_repo(train_repo_shortname=args.train)

    # When using `moz-phab uplift`, we perform a standard `submit` but with all
    # the in-repo configurations for submit destination overwritten to submit
    # to the uplift target repository instead.
    repo.call_sign = phab_repo["fields"]["callsign"]
    repo._phab_repo = phab_repo

    # Run the usual submit command with our patched arg values.
    commits = submit(repo, args)

    if commits:
        tip_commit = commits[-1]
        tip_commit_id = tip_commit.rev_id

        uplift_assessment_linking_url = build_assessment_linking_url(
            repo.lando_url, tip_commit_id, args.assessment_id
        )

        logger.warning(
            f"\nPlease navigate to {uplift_assessment_linking_url} and complete the uplift "
            "request form."
        )

    # Output machine-readable data to a file.
    if args.output_file:
        data = {"commits": [commit.to_dict() for commit in commits]}

        with args.output_file.open("w") as f:
            json.dump(data, f)


def add_parser(parser):
    uplift_parser = parser.add_parser(
        "uplift",
        help="Submit uplift request commit(s) to Phabricator.",
        description=(
            "MozPhab will create a new revision to request patches be uplifted "
            "to stable release trains.\n"
            "\n"
            "See https://wiki.mozilla.org/index.php?title="
            "Release_Management/Requesting_an_Uplift\n"
            "for more information."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Add all the same arguments as `moz-phab submit`.
    add_submit_arguments(uplift_parser)

    # Add uplift-specific arguments.
    uplift_parser.add_argument(
        "--train",
        help=(
            "Indicate Phabricator shortname of the release train this stack "
            "should be uplifted to (see `--list-trains`)."
        ),
    )
    uplift_parser.add_argument(
        "--list-trains",
        help="List all possible values for `--train`.",
        action="store_true",
    )
    uplift_parser.add_argument(
        "--no-rebase",
        help=(
            "Send the specified range of commits as-is for uplift - do not attempt "
            "to rebase."
        ),
        action="store_true",
    )
    uplift_parser.add_argument(
        "--assessment-id",
        help="Existing assessment ID to link the new uplift revision to.",
    )
    uplift_parser.add_argument(
        "--output-file",
        dest="output_file",
        type=Path,
        help=argparse.SUPPRESS,
    )
    uplift_parser.set_defaults(func=uplift, needs_repo=True)
