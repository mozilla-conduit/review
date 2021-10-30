# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from typing import (
    Optional,
)

from mozphab.conduit import (
    conduit,
)
from mozphab.exceptions import (
    NotFoundError,
)
from mozphab.logger import (
    logger,
)
from .submit import (
    add_submit_arguments,
    submit,
)

UPLIFT_REPOSITORY_TAG_SLUG = "uplift"


def map_train_arg_to_repo(train: str) -> Optional[dict]:
    """Attempt to map the value of `--train` to a Phabricator repo."""
    try:
        repo = conduit.get_repository(train)
        if not repo:
            return None

        uplift_repos = conduit.get_repositories_with_tag(UPLIFT_REPOSITORY_TAG_SLUG)
        if not uplift_repos:
            return None
        
        uplift_repo_callsigns = {
            repo["fields"]["callsign"]
            for repo in uplift_repos
        }

        if repo["fields"]["callsign"] not in uplift_repo_callsigns:
            return None

        return repo

    except NotFoundError as e:
        logger.warn(e)
        return None


def list_trains() -> int:
    """List all uplift repositories on Phabricator.

    Queries the Phabricator repository search endpoint for all repositories
    with the `uplift` tag set on them, and displays their callsigns as a list
    of valid values for `--train`.

    Returns:
        Boolean indicating success or failure, to be returned as a status code.
    """
    try:
        repositories = conduit.get_repositories_with_tag(UPLIFT_REPOSITORY_TAG_SLUG)
        if not repositories:
            return 1

        logger.warning("Available trains for uplift:")
        for repository in repositories:
            logger.warning(f"   - {repository['fields']['callsign']}")

        return 0

    except NotFoundError as e:
        logger.fatal(f"Error retreiving the list of uplift repositories: {e}")
        return 1


def uplift(repo, args):
    if args.list_trains:
        return list_trains()

    if not args.train:
        logger.fatal("Missing `--train` argument!")
        return 1

    phab_repo = map_train_arg_to_repo(args.train)
    if not phab_repo:
        logger.fatal(f"Didn't recognize train '{args.train}'!")
        list_trains()
        return 1

    # When using `moz-phab uplift`, we perform a standard `submit` but with all
    # the in-repo configurations for submit destination overwritten to submit
    # to the uplift target repository instead.
    repo.call_sign = args.train
    repo._phab_repo = phab_repo

    # Run the usual submit comment with our patched arg values.
    submit(repo, args)

    logger.warning(
        "\nPlease navigate to the tip-most commit and complete the uplift "
        "request form."
    )


def add_parser(parser):
    uplift_parser = parser.add_parser(
        "uplift",
        help="Create differential revisions requesting uplift",
        description=(
            "MozPhab will create a new revision to request patches be uplifted "
            "to stable release trains."
        ),
    )

    # Add all the same arguments as `moz-phab submit`
    add_submit_arguments(uplift_parser)

    # Add uplift-specific arguments
    uplift_parser.add_argument(
        "--train",
        help="Indicate Phabricator callsign of the release train this stack should be uplifted to.",
    )
    uplift_parser.add_argument(
        "--list-trains",
        help="List all possible values for `--train`.",
        action="store_true",
    )
    uplift_parser.add_argument(
        "--no-rebase",
        help="Send the specified range of commits as-is for uplift - do not attempt to rebase.",
        action="store_true",
    )
    uplift_parser.set_defaults(func=uplift, needs_repo=True)
