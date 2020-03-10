# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse

from mozphab import arcanist, environment
from mozphab.exceptions import CommandError
from mozphab.subprocess_wrapper import check_call


def arc_pass(args):
    if environment.DEBUG:
        arcanist.ARC.append("--trace")

    try:
        check_call(arcanist.ARC + args.commands)
    except CommandError:
        pass


def add_parser(parser):
    arc_parser = parser.add_parser("arc", help="Call Arcanist")
    arc_parser.add_argument("commands", nargs=argparse.REMAINDER)
    arc_parser.set_defaults(func=arc_pass, needs_repo=False)
