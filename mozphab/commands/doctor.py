# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse

from mozphab.conduit import conduit, ConduitAPIError
from mozphab.environment import INSTALL_CERT_MSG
from mozphab.helpers import get_arcrc_path, read_json_field
from mozphab.logger import logger
from mozphab.repository import Repository


def doctor(repo: Repository, args: argparse.Namespace):
    """Validates the user's installation of moz-phab.

    Currently, this only retrieves the user's Phabricator
    API key and validates it against the API.

    Raises ConduitAPIError if token is invalid in any way.
    """
    token = read_json_field([get_arcrc_path()], ["hosts", repo.api_url, "token"])
    if not token:
        raise ConduitAPIError(INSTALL_CERT_MSG)

    try:
        who = conduit.whoami(api_token=token)
        logger.info(f"Phabricator API token for {who['userName']} is valid.")
    except ConduitAPIError as e:
        if "not valid" in str(e):
            logger.error(e)
            raise ConduitAPIError(INSTALL_CERT_MSG) from e

        # Re-raise the exception if it is N/A to the above conditions.
        raise e

    repo.validate_email()

    logger.info("No critical issues detected with moz-phab installation.")


def add_parser(parser):
    doctor_parser = parser.add_parser(
        "doctor", help="Ensure your moz-phab installation is valid."
    )
    doctor_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions.",
    )
    doctor_parser.set_defaults(func=doctor, needs_repo=True)
