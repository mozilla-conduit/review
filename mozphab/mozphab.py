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
import sys
import traceback

from mozphab import environment

from .args import parse_args
from .conduit import conduit
from .config import config
from .detect_repository import repo_from_args
from .exceptions import Error
from .logger import init_logging, logger
from .spinner import wait_message
from .sentry import init_sentry, report_to_sentry
from .telemetry import telemetry
from .updater import check_for_updates

# Known Issues
# - commits with a description already modified by arc (ie. the follow the arc commit
#   description template with 'test plan', subscribers, etc) are not handled by this
#   script.  commits in this format should be detected and result in the commit being
#   rejected.  ideally this should extract the title, body, reviewers, and bug-id
#   from the arc template and reformat to the standard mozilla format.


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
            check_for_updates()

        repo = None
        if args.needs_repo:
            with wait_message("Starting up.."):
                repo = repo_from_args(args)

            conduit.set_repo(repo)

        telemetry.set_metrics(args, is_development=is_development)

        if repo is not None:
            telemetry.set_vcs(repo)
            try:
                args.func(repo, args)
            finally:
                repo.cleanup()

        else:
            args.func(args)

        telemetry.metrics.mozphab.usage.command_time.stop()
        telemetry.submit()

    except KeyboardInterrupt:
        pass
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
    main(sys.argv[1:], is_development=False)


def run_dev():
    main(sys.argv[1:], is_development=True)


if __name__ == "__main__":
    run()
