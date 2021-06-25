# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from mozphab.conduit import conduit
from mozphab.helpers import prompt
from mozphab.logger import logger
from mozphab.spinner import wait_message


def install_certificate(repo, args):
    """Asks user to enter the Phabricator API Token.

    Named for parity with arc's corresponding command.

    The response is saved in the ~/.arcrc file; moz-phab itself doesn't do any
    verification/parsing of the provided string (verification happens by passing
    it to the Phabricator server)."""
    logger.info(
        "LOGIN TO PHABRICATOR\nOpen this page in your browser and login "
        "to Phabricator if necessary:\n\n%s/conduit/login/\n",
        conduit.repo.phab_url,
    )
    token = prompt("Paste API Token from that page: ")

    # Call a method that requires authentication to both verify the token and clear
    # the default one-hour expiration of newly created tokens.
    with wait_message("Verifying token"):
        who = conduit.whoami(api_token=token)
    conduit.save_api_token(token)
    logger.info("Configured moz-phab for %s", who["realName"])


def add_parser(parser):
    cert_parser = parser.add_parser(
        "install-certificate", help="Configure your Phabricator API Token"
    )
    cert_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions",
    )
    cert_parser.set_defaults(func=install_certificate, needs_repo=True)
