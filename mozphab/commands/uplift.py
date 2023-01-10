# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse

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

    # Run the usual submit comment with our patched arg values.
    submit(repo, args)

    logger.warning(
        "\nPlease navigate to the tip-most commit and complete the uplift "
        "request form."
    )


def add_parser(parser):
    uplift_parser = parser.add_parser(
        "uplift",
        help="Submit uplift request commit(s) to Phabricator.",
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
    uplift_parser.set_defaults(func=uplift, needs_repo=True)
