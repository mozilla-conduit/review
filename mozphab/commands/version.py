# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import platform

from mozphab.environment import MOZPHAB_NAME, MOZPHAB_VERSION
from mozphab.logger import logger


def log_current_version(_):
    py_version = platform.python_version()
    system = platform.system()

    logger.info(f"{MOZPHAB_NAME} {MOZPHAB_VERSION} (Python {py_version}, {system})")


def add_parser(parser):
    ver_parser = parser.add_parser("version", help="Get version number.")
    ver_parser.set_defaults(func=log_current_version, needs_repo=False)
