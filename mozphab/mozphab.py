#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# coding=utf-8

"""
CLI to support submission of a series of commits to Phabricator. .
"""

import logging
import os
import ssl
import subprocess
import sys
import traceback

from mozphab import environment

from .args import parse_args
from .conduit import conduit, ConduitAPIError
from .config import config
from .detect_repository import repo_from_args
from .exceptions import Error
from .logger import init_logging, logger, stop_logging
from .spinner import wait_message
from .sentry import init_sentry, report_to_sentry
from .telemetry import telemetry, configure_telemetry
from .updater import (
    check_for_updates,
    log_windows_update_message,
    self_upgrade,
)

from packaging.version import Version


def restart_mozphab():
    """Restart `moz-phab`, re-using the previous command line."""
    logger.info("Restarting...")

    # Explicitly close the log files to avoid issues with processes holding
    # exclusive logs on the files on Windows.
    stop_logging()

    # It's best to ignore errors here as they will be reported by the
    # new moz-phab process.
    p = subprocess.run(sys.argv)
    sys.exit(p.returncode)


def assert_api_token_is_present(repo, args):
    """Assert a local API token is present.

    Prompt for install by running `install-certificate` if missing,
    unless the user is already running `install-certificate` themselves.
    """
    if args.command == "install-certificate":
        return

    try:
        conduit.load_api_token()
    except ConduitAPIError:
        logger.info("No API token detected, running `install-certificate`...")
        install = parse_args(["install-certificate"])
        install.func(repo, install)
        logger.info("Token installed, resuming original command")


def main(argv, *, is_development):
    try:
        if not is_development and config.report_to_sentry:
            init_sentry()

        os.makedirs(environment.MOZBUILD_PATH, exist_ok=True)

        if config.no_ansi:
            environment.HAS_ANSI = False
        os.environ["MOZPHAB"] = "1"

        args = parse_args(argv)

        if args.trace:
            environment.DEBUG = True
        if environment.DEBUG:
            environment.SHOW_SPINNER = False

        init_logging()

        logger.debug("%s (%s)", environment.MOZPHAB_NAME, environment.MOZPHAB_VERSION)

        # Ensure that `patch --raw ..` only outputs the patch
        if args.command == "patch" and getattr(args, "raw", False):
            environment.SHOW_SPINNER = False
            logger.setLevel(logging.ERROR)

        elif args.command != "self-update":
            new_version = check_for_updates()

            if new_version and environment.IS_WINDOWS:
                log_windows_update_message()
            elif new_version and config.self_auto_update:
                with wait_message(f"Upgrading to version {new_version}"):
                    self_upgrade()
                restart_mozphab()

        repo = None
        if args.needs_repo:
            with wait_message("Starting up.."):
                repo = repo_from_args(args)

            conduit.set_repo(repo)

        if not is_development:
            configure_telemetry(args)

        if repo is not None:
            assert_api_token_is_present(repo, args)

            telemetry().set_vcs(repo)
            try:
                args.func(repo, args)
            finally:
                repo.cleanup()

        else:
            args.func(args)

        telemetry().usage.command_time.stop()
        telemetry().submit()

    except KeyboardInterrupt:
        pass
    except ssl.SSLCertVerificationError as e:
        logger.error(e)

        if e.reason == "CERTIFICATE_VERIFY_FAILED":
            # Leave a helpful error message if the local issuer certificate
            # can't be found.
            logger.error(
                "\nYour Python installation is incomplete as you lack a local issuer "
                "certificate.\n"
                "See the following link for steps to resolve your problem:\n"
                "https://stackoverflow.com/questions/52805115/"
                "certificate-verify-failed-unable-to-get-local-issuer-certificate\n"
                "\n"
                "If you still experience trouble please file a bug."
            )

        sys.exit(1)
    except Error as e:
        logger.error(e)
        sys.exit(1)
    except Exception as e:
        if environment.DEBUG:
            logger.error(traceback.format_exc())
        else:
            logger.error("%s: %s", e.__class__.__name__, e)
            logger.error("Run moz-phab again with '--trace' to show debugging output")
        report_to_sentry(e)
        sys.exit(1)


def run():
    is_development = Version(environment.MOZPHAB_VERSION).is_prerelease
    main(sys.argv[1:], is_development=is_development)


if __name__ == "__main__":
    run()
