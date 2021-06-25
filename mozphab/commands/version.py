# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from mozphab.logger import logger
from mozphab.environment import MOZPHAB_NAME, MOZPHAB_VERSION


def log_current_version(_):
    logger.info("%s (%s)", MOZPHAB_NAME, MOZPHAB_VERSION)


def add_parser(parser):
    ver_parser = parser.add_parser("version", help="Get version number")
    ver_parser.set_defaults(func=log_current_version, needs_repo=False)
