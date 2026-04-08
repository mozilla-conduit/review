# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
import urllib.error as url_error
import urllib.request as url_request
from pathlib import Path
from typing import Optional

from mozphab.conduit import (
    conduit,
)
from mozphab.environment import (
    USER_AGENT,
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
from mozphab.spinner import (
    wait_message,
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


def link_assessment(lando_url: str, revision_id: int, assessment_id: int):
    """Link an uplift revision to an existing assessment via the Lando API."""
    api_url = f"{lando_url.rstrip('/')}/api/uplift/assessments/link"
    api_token = conduit.load_api_token()

    payload = json.dumps(
        {"revision_id": revision_id, "assessment_id": assessment_id}
    ).encode()

    request = url_request.Request(
        url=api_url,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "X-Phabricator-API-Key": api_token,
        },
        data=payload,
    )

    logger.debug("Linking revision D%s to assessment %s.", revision_id, assessment_id)

    try:
        with url_request.urlopen(request) as response:
            return json.load(response)
    except url_error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")

        # Parse RFC 7807 Problem Details `detail` from Lando.
        try:
            detail = json.loads(body).get("detail", body)
        except (json.JSONDecodeError, TypeError):
            detail = body

        raise Error(f"Failed to link assessment (HTTP {err.code}): {detail}")
    except url_error.URLError as err:
        raise Error(f"Failed to connect to Lando: {err.reason}")


def attempt_link_assessment(
    lando_url: str, revision_id: int, assessment_id: int
) -> bool:
    """Attempt to link an uplift revision to an assessment, returning success status."""
    try:
        with wait_message(f"Linking assessment {assessment_id} to D{revision_id}"):
            link_assessment(lando_url, revision_id, assessment_id)
    except Error as err:
        logger.warning("Failed to automatically link assessment: %s", err)
        return False
    else:
        return True


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

        if args.assessment_id and attempt_link_assessment(
            repo.lando_url, tip_commit_id, args.assessment_id
        ):
            logger.warning(
                "\nUplift submitted and linked to assessment. "
                "No further action required."
            )
        else:
            uplift_assessment_linking_url = build_assessment_linking_url(
                repo.lando_url, tip_commit_id, args.assessment_id
            )

            logger.warning(
                f"\nPlease navigate to {uplift_assessment_linking_url} and save "
                "the uplift request form."
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
        type=int,
        help="Existing assessment ID to link the new uplift revision to.",
    )
    uplift_parser.add_argument(
        "--output-file",
        dest="output_file",
        type=Path,
        help=argparse.SUPPRESS,
    )
    uplift_parser.set_defaults(func=uplift, needs_repo=True)
