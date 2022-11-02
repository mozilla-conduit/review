# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import sys
import time
import urllib.request

from typing import Optional

from setuptools import Distribution
from pathlib import Path
from pkg_resources import parse_version

from mozphab import environment

from .config import config
from .environment import MOZPHAB_VERSION
from .exceptions import Error
from .logger import logger
from .subprocess_wrapper import check_call

SELF_UPDATE_FREQUENCY = 24 * 3  # hours


def get_pypi_info():
    url = "https://pypi.org/pypi/MozPhab/json"
    output = urllib.request.urlopen(urllib.request.Request(url), timeout=30).read()
    response = json.loads(output.decode("utf-8"))
    return response["info"]


def check_for_updates(force_check: bool = False) -> Optional[str]:
    """Check if an update is available for `moz-phab`.

    Log a message about the new version, return the version as a `str` if it is
    found or return `None`. Use `force_check` to check for updates even when the
    usual conditions aren't met.
    """
    self_update_disabled = config.self_last_check < 0
    last_check_before_frequency = (
        time.time() - config.self_last_check <= SELF_UPDATE_FREQUENCY * 60 * 60
    )

    # Return if our check conditions aren't met.
    if not force_check and (self_update_disabled or not last_check_before_frequency):
        return

    config.self_last_check = int(time.time())
    current_version = MOZPHAB_VERSION
    pypi_info = get_pypi_info()
    logger.debug(f"Versions - local: {current_version}, PyPI: {pypi_info['version']}")

    # convert ">=3.6" to (3, 6)
    try:
        required_python_version = tuple(
            [int(i) for i in pypi_info["requires_python"][2:].split(".")]
        )
    except ValueError:
        required_python_version = ()

    if sys.version_info < required_python_version:
        raise Error(
            "Unable to upgrade to version {}.\n"
            "MozPhab requires Python in version {}".format(
                pypi_info["version"], pypi_info["requires_python"]
            )
        )

    config.write()

    if parse_version(current_version) >= parse_version(pypi_info["version"]):
        logger.debug("update check not required")
        return

    logger.warning(f"Version {pypi_info['version']} of `moz-phab` is now available")

    return pypi_info["version"]


def self_upgrade():
    """Upgrade ourselves with pip."""

    # Run pip using the current python executable to accommodate for virtualenvs
    command = (
        [sys.executable]
        + ["-m", "pip"]
        + ["install", "MozPhab"]
        + ["--upgrade"]
        + ["--no-cache-dir"]
        + ["--disable-pip-version-check"]
    )

    if config.get_pre_releases:
        command += ["--pre"]

    if not environment.DEBUG:
        command += ["--quiet"]

    # sys.path[0] is the directory containing the script that was used to
    # start python. This will be something like:
    # "<python environment>/bin" or "<python environment>\Scripts" (Windows)
    script_dir = Path(sys.path[0])

    # If moz-phab was installed with --user, we need to pass it to pip
    # Create "install" setuptools command with --user to find the scripts_path
    d = Distribution()
    d.parse_config_files()
    i = d.get_command_obj("install", create=True)
    # Forcing the environment detected by Distribution to the --user one
    i.user = True
    i.prefix = i.exec_prefix = i.home = i.install_base = i.install_platbase = None
    i.finalize_options()
    # Checking if the moz-phab script is installed in user's scripts directory
    user_dir = Path(i.install_scripts).resolve()
    if script_dir == user_dir:
        command.append("--user")

    if environment.IS_WINDOWS:
        # Windows does not allow to remove the exe file of the running process.
        # Renaming the `moz-phab.exe` file to allow pip to install a new version.
        temp_exe = script_dir / "moz-phab-temp.exe"
        try:
            temp_exe.unlink()
        except FileNotFoundError:
            pass

        exe = script_dir / "moz-phab.exe"
        exe.rename(temp_exe)

        try:
            check_call(command)
        except Exception:
            temp_exe.rename(exe)
            raise

        if not exe.is_file():
            # moz-phab.exe is not created - install wasn't needed.
            temp_exe.rename(exe)

    else:
        check_call(command)
