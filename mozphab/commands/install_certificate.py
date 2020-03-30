# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from mozphab.conduit import conduit
from mozphab.helpers import prompt
from mozphab.logger import logger


def install_certificate(repo, args):
    """Asks user to enter the Phabricator's certificate.

    The response is saved in the ~/.arcrc file."""
    logger.info(
        "LOGIN TO PHABRICATOR\nOpen this page in your browser and login "
        "to Phabricator if necessary:\n\n%s/conduit/login/\n",
        conduit.repo.phab_url,
    )
    token = prompt("Paste API Token from that page: ")
    conduit.save_api_token(token)


def add_parser(parser):
    cert_parser = parser.add_parser(
        "install-certificate", help="Install Phabricator certificate locally"
    )
    cert_parser.add_argument(
        "--safe-mode",
        dest="safe_mode",
        action="store_true",
        help="Run VCS with only necessary extensions.",
    )
    cert_parser.set_defaults(func=install_certificate, needs_repo=True, no_arc=True)
