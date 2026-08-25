# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
import sys
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
    subprocess.check_call((find_script_path("ruff"), "check", ROOT))


def test_pyrefly():
    """Fail on type errors that are not already recorded in the baseline.

    Regenerate the baseline after fixing or knowingly introducing errors with
    `uv run pyrefly check --baseline pyrefly-baseline.json --update-baseline`.
    """
    command = (
        find_script_path("pyrefly"),
        "check",
        "--config",
        str(ROOT / "pyrefly.toml"),
        "--baseline",
        str(ROOT / "pyrefly-baseline.json"),
        # Resolve imports against the environment running the tests, rather
        # than letting pyrefly auto-detect a virtualenv.
        "--python-interpreter-path",
        sys.executable,
        "--summary=none",
    )
    # The baseline records paths relative to the repository root, so pyrefly
    # only matches them when run from there.
    assert (
        subprocess.call(command, cwd=ROOT) == 0
    ), "pyrefly should report no type errors missing from `pyrefly-baseline.json`."
