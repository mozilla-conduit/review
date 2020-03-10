# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
import os
import subprocess
import time

from mozphab import environment

from .config import config
from .exceptions import CommandError, Error
from .gitcommand import GitCommand
from .helpers import parse_api_error, prompt, temporary_file
from .logger import logger
from .subprocess_wrapper import check_output

LIBPHUTIL_PATH = os.path.join(environment.MOZBUILD_PATH, "libphutil")
ARC_PATH = os.path.join(environment.MOZBUILD_PATH, "arcanist")
ARC_COMMAND = os.path.join(
    ARC_PATH, "bin", "arc.bat" if environment.IS_WINDOWS else "arc"
)
ARC = [ARC_COMMAND]
ARC_CONDUIT_ERROR = (
    {"matching": "install-certificate", "message": environment.INSTALL_CERT_MSG},
    {"matching": "ERR-INVALID_AUTH", "message": "Server rejected your token."},
)
LIBPHUTIL_URL = "https://github.com/phacility/libphutil.git"
ARC_URL = "https://github.com/mozilla-conduit/arcanist.git"


class ArcConduitAPIError(Error):
    """Raised when the Phabricator Conduit API returns an error response."""


def arc_out(
    args, cwd, stdin=None, log_output_to_console=True, stderr=None, search_error=None
):
    """arc wrapper that logs output to the console.

    Args:
        args: A list of arguments for the arc command.
        cwd: The directory to run the arc command in.
        stdin: Optionally overrides the standard input pipe to use for the arc
            subprocess call.
        log_output_to_console: Defaults to True.  If set to False, don't log the arc
            standard output to the console (stderr prints to console as normal).
        stderr: Standard error output stream
        search_error: a list of dicts passed to the check_output to parse the
            errors in the error response

    Returns:
        The list of lines arc printed to the console.
    """
    arc_output = check_output(
        ARC + args,
        cwd=cwd,
        split=False,
        stdin=stdin,
        stderr=stderr,
        search_error=search_error,
    )
    if logger.level != logging.DEBUG and log_output_to_console:
        logger.info(arc_output)
    return arc_output


def call_conduit(api_method, api_call_args, cwd):
    """Run 'arc call-conduit' and return the JSON API call result.

    Args:
        api_method: The API method name to call, like 'differential.revision.edit'.
        api_call_args: JSON dict of call args to send.
        cwd: The directory to run the arc command in.

    Raises:
        ArcConduitAPIError if the API threw an error back at us.
    """
    arc_args = ["call-conduit", api_method]
    # 'arc call-conduit' only accepts its args from STDIN.
    with temporary_file(json.dumps(api_call_args)) as args_file:
        logger.debug("Arc stdin: %s", api_call_args)
        with open(args_file, "rb") as temp_f:
            output = arc_out(
                arc_args,
                cwd=cwd,
                stdin=temp_f,
                log_output_to_console=False,
                stderr=subprocess.STDOUT,
                search_error=ARC_CONDUIT_ERROR,
            )

    # We expect arc output to be a JSON. However, in DEBUG mode, a `--trace` is used and
    # the response becomes a multiline string with some messages in plain text.
    output = "\n".join([line for line in output.splitlines() if line.startswith("{")])
    maybe_error = parse_api_error(output)
    if maybe_error:
        raise ArcConduitAPIError(maybe_error)

    return json.loads(output)["response"]


def install_arc_if_required():
    if os.path.exists(ARC_COMMAND) and os.path.exists(LIBPHUTIL_PATH):
        return

    try:
        git = GitCommand()
    except Error:
        logger.error("Git is required to install Arcanist.")
        raise

    logger.info("Installing arc")
    logger.debug("arc command: %s", ARC_COMMAND)
    logger.debug("libphutil path: %s", LIBPHUTIL_PATH)

    git.call(["clone", "--depth", "1", ARC_URL, ARC_PATH])
    git.call(
        ["clone", "--depth", "1", "--branch", "stable", LIBPHUTIL_URL, LIBPHUTIL_PATH]
    )


def arc_ping(cwd):
    """Sends a ping to the Phabricator server using `conduit.ping` API.

    Returns: `True` if no error, otherwise - `False`
    """
    try:
        call_conduit("conduit.ping", {}, cwd)
    except ArcConduitAPIError as err:
        logger.error(err)
        return False
    except CommandError:
        return False
    return True


def update_arc():
    """Write the last check and update arc."""

    def update_repo(name, path):
        logger.info("Updating %s...", name)
        rev = git.output(["rev-parse", "HEAD"], split=False, cwd=path)
        git.call(["pull", "--quiet"], cwd=path)
        if rev != git.output(["rev-parse", "HEAD"], split=False, cwd=path):
            logger.info("%s updated", name)
        else:
            logger.info("Update of %s not required", name)

    if not os.path.exists(ARC_COMMAND) or not os.path.exists(LIBPHUTIL_PATH):
        # Nothing to update
        return

    try:
        git = GitCommand()
    except Error:
        logger.error("Git is required to install Arcanist")
        raise

    try:
        update_repo("libphutil", LIBPHUTIL_PATH)
        update_repo("arcanist", ARC_PATH)
    except CommandError:
        result = prompt(
            "Would you like to skip arc upgrades in the future?", ["Yes", "No"]
        )
        if result == "Yes":
            config.arc_last_check = -1
            config.write()
    else:
        config.arc_last_check = int(time.time())
        config.write()
