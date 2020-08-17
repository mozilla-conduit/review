# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from pathlib import Path
import subprocess

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


def test_flake8():
    subprocess.check_call(
        [find_script_path("flake8")]
        + ["--max-line-length=88"]
        + ["--ignore=E203,W503"]
        + ["--disable-noqa"]
        + PY_FILES
    )
