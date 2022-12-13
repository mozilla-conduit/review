# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from pathlib import Path

# Find all command modules

__all__ = sorted(
    [
        f.stem
        for f in Path(__file__).parent.glob("*.py")
        if f.is_file() and f.stem != "__init__"
    ]
)
