# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from mozphab.environment import (
    IS_WINDOWS,
    MOZPHAB_VERSION,
)
from mozphab.logger import logger
from mozphab.spinner import wait_message
from mozphab.updater import (
    check_for_updates,
    log_windows_update_message,
    self_upgrade,
)


def self_update(_):
    """`self-update` command, updates this package"""
    new_version = check_for_updates(force_check=True)

    if not new_version:
        logger.info(
            f"You are running the latest version of `moz-phab`, {MOZPHAB_VERSION}."
        )
        return

    if IS_WINDOWS:
        log_windows_update_message()
        return

    with wait_message(f"Upgrading to version {new_version}"):
        self_upgrade()

    logger.info(f"Upgraded to version {new_version}")


def add_parser(parser):
    update_parser = parser.add_parser("self-update", help="Update review script.")
    update_parser.set_defaults(func=self_update, needs_repo=False)
