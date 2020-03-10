# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from .exceptions import Error
from .gitcommand import GitCommand
from .helpers import temporary_binary_file
from .logger import logger


def apply_patch(diff, cwd):
    """Apply a patch provided in the `diff`."""
    try:
        git = GitCommand()
    except Error:
        logger.error("Git is required to apply patches.")
        raise

    with temporary_binary_file(diff.encode("utf8")) as temp_f:
        git.call(["apply", temp_f], cwd=cwd)
