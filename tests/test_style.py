# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
from pathlib import Path

from .conftest import find_script_path

ROOT = Path(__file__).resolve().parent.parent
PY_FILES = sorted(
    str(f)
    for f in list(ROOT.glob("*.py"))
    + list((ROOT / "mozphab").glob("**/*.py"))
    + list((ROOT / "tests").glob("**/*.py"))
)


def test_black():
    subprocess.check_call([find_script_path("black"), "--check"] + PY_FILES)


def test_ruff():
    """Run ruff on the codebase.

    Use the project root as the directory to lint, and define appropriate lint
    paths in the `ruff.toml` file.
    """
    subprocess.check_call(
        (
            find_script_path("ruff"),
            "check",
            "--target-version",
            "py38",
            ROOT,
        )
    )
