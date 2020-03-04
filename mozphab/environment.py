# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import sys

DEBUG = bool(os.getenv("DEBUG"))
HTTP_ALLOWED = bool(os.getenv("HTTP_ALLOWED"))
IS_WINDOWS = sys.platform == "win32"
HAS_ANSI = (
    not IS_WINDOWS
    and not os.getenv("NO_ANSI")
    and (
        (hasattr(sys.stdout, "isatty") and sys.stdout.isatty())
        or os.getenv("TERM", "") == "ANSI"
        or os.getenv("PYCHARM_HOSTED", "") == "1"
    )
)

GIT_COMMAND = ["git.exe" if IS_WINDOWS else "git"]
HG_COMMAND = ["hg.exe" if IS_WINDOWS else "hg"]

# Switched off temporarily due to https://bugzilla.mozilla.org/show_bug.cgi?id=1565502
SHOW_SPINNER = False

DEFAULT_START_REV = "(auto)"
DEFAULT_END_REV = "."

HOME_DIR = os.path.expanduser("~")

# ~/.mozbuild/moz-phab
MOZBUILD_PATH = os.path.join(
    os.environ.get("MOZBUILD_STATE_PATH", os.path.join(HOME_DIR, ".mozbuild")),
    "moz-phab",
)
