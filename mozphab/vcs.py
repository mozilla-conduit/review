# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from enum import Enum


class Vcs(str, Enum):
    """The version control systems moz-phab works with."""

    GIT = "git"
    HG = "hg"
    JJ = "jj"
