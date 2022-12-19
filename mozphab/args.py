# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import os
import textwrap

from importlib import import_module
from typing import List, Set

from mozphab import commands

from .config import config
from .detect_repository import find_repo_root
from .logger import logger


def should_fallback_to_submit(argv: List[str], commands: Set[str]) -> bool:
    """Return `True` if `moz-phab` should fallback to `submit` command.

    If `moz-phab` is called without a command and from within a repository,
    default to submit.
    """
    # Just running `moz-phab` with no arguments means fallback.
    if not argv:
        return True

    # Don't fallback if help args are passed.
    if any(help_arg in set(argv) for help_arg in {"-h", "--help"}):
        return False

    # Don't fallback if a known command is passed.
    if argv[0] in commands:
        return False

    # Don't fallback when we aren't in a repo.
    if not find_repo_root(os.getcwd()):
        return False

    return True


def parse_args(argv):
    main_parser = argparse.ArgumentParser(add_help=False)
    main_parser.add_argument("--version", action="store_true", help=argparse.SUPPRESS)
    main_parser.add_argument(
        "--trace", "--debug", action="store_true", help=argparse.SUPPRESS
    )
    parser = argparse.ArgumentParser(
        parents=[main_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """
            If moz-phab is executed without specifying a command, the 'submit' command
            will be executed.

            For more help on 'submit' and its options run 'moz-phab submit --help'.
            """
        ),
        epilog=textwrap.dedent(
            f"""\
                configuration:
                    {config.name}

                documentation:
                    https://github.com/mozilla-conduit/review/blob/master/README.md
            """
        ),
    )

    commands_parser = parser.add_subparsers(
        dest="command",
        metavar="COMMAND",
        description="For full command description: moz-phab COMMAND -h",
    )
    commands_parser.required = True

    for command in commands.__all__:
        module = import_module("mozphab.commands.{}".format(command))
        add_parser = getattr(module, "add_parser", None)
        if callable(add_parser):
            add_parser(commands_parser)
            logger.debug("Command added - %s", command)

    help_parser = commands_parser.add_parser("help")
    help_parser.add_argument("command", nargs=argparse.OPTIONAL)
    help_parser.set_defaults(print_help=True)

    fallback = should_fallback_to_submit(
        argv, {command for command in commands_parser.choices}
    )
    if fallback:
        argv.insert(0, "submit")

    main_args, unknown = main_parser.parse_known_args(argv)

    # map --version to the 'version' command
    if main_args.version:
        unknown = ["version"]

    args = parser.parse_args(unknown)

    # copy across parsed main_args; they are defined in `args`, but not set
    for name, value in vars(main_args).items():
        args.__setattr__(name, value)

    args.fallback = fallback

    # handle the help command here as printing help needs access to the parser
    if hasattr(args, "print_help"):
        help_argv = ["--help"]
        if args.command:
            help_argv.insert(0, args.command)
        # parse_args calls parser.exit() when passed --help
        parser.parse_args(help_argv)

    return args
