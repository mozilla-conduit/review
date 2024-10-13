# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

from packaging.version import Version
from pkg_resources import parse_version
from setuptools import Distribution

from mozphab import environment

from .config import config
from .environment import MOZPHAB_VERSION
from .exceptions import Error
from .logger import logger
from .subprocess_wrapper import check_call

# Update every three days.
SELF_UPDATE_FREQUENCY_SECONDS = 24 * 3 * 60 * 60


def get_pypi_json() -> dict:
    """Get data about `MozPhab` from the JSON API endpoint."""
    url = "https://pypi.org/pypi/MozPhab/json"
    output = urllib.request.urlopen(urllib.request.Request(url), timeout=30).read()
    response = json.loads(output.decode("utf-8"))
    return response


def get_simple_json() -> dict:
    """Get data about `MozPhab` from the `simple` API endpoint."""
    url = "https://pypi.org/simple/MozPhab"
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.pypi.simple.v1+json"}
    )
    output = urllib.request.urlopen(request, timeout=30).read()
    return json.loads(output.decode("utf-8"))


def parse_latest_prerelease_version(simple_json: dict) -> str:
    """Parse PyPI's API response for `moz-phab` to determine the latest version."""
    # Get all the returned `.tar.gz` file entries.
    filenames = (entry["filename"].lower() for entry in simple_json["files"])

    # The format is `MozPhab-<version>.tar.gz`, so remove the prefix and
    # suffix to get the version strings.
    versions = (
        filename[len("mozphab-") :][: -len(".tar.gz")]
        for filename in filenames
        if filename.startswith("mozphab-") and filename.endswith(".tar.gz")
    )

    # Turn each version string into a `Version`, get the max, then return as `str`.
    return str(max(Version(version) for version in versions))


def log_windows_update_message():
    """Log a message telling users to update `moz-phab` to the latest version."""
    logger.warning(
        "Please run `pip install --upgrade MozPhab` to ugprade to the latest version."
    )


def should_self_update(self_last_check: int, current_time: int) -> bool:
    """Return `True` if a self-update should be performed."""
    # Setting `updater.self_last_check` to a negative value disables self-update.
    if self_last_check < 0:
        logger.debug("Self-update is disabled, skipping check for new versions.")
        return False

    # Avoid self-update if our last check was before the frequency interval.
    time_since_last_check = current_time - self_last_check
    if time_since_last_check <= SELF_UPDATE_FREQUENCY_SECONDS:
        logger.debug(
            f"Time since last check is {round(time_since_last_check)} seconds, "
            "skipping check for new versions."
        )
        return False

    return True


def check_for_updates(force_check: bool = False) -> Optional[str]:
    """Check if an update is available for `moz-phab`.

    Log a message about the new version, return the version as a `str` if it is
    found or return `None`. Use `force_check` to check for updates even when the
    usual conditions aren't met.
    """
    logger.debug(f"Forced update check: {force_check}.")

    # Return if our check conditions aren't met.
    current_time_s = int(time.time())
    if not force_check and not should_self_update(
        config.self_last_check, current_time_s
    ):
        logger.debug("Not checking for updates.")
        return

    config.self_last_check = current_time_s
    current_version = MOZPHAB_VERSION
    pypi_json = get_pypi_json()
    pypi_info = pypi_json["info"]

    if not config.get_pre_releases:
        # Use the latest full release.
        pypi_version = pypi_info["version"]
    else:
        # Find the latest pre-release version manually since the "version" key
        # only contains the latest full release on PyPI.
        simple_json = get_simple_json()
        pypi_version = parse_latest_prerelease_version(simple_json)

    logger.debug(f"Versions - local: {current_version}, PyPI: {pypi_version}")

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
                pypi_version, pypi_info["requires_python"]
            )
        )

    config.write()

    if parse_version(current_version) >= parse_version(pypi_version):
        logger.debug("update check not required")
        return

    logger.warning(f"Version {pypi_version} of `moz-phab` is now available")

    return pypi_version


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

    # `sys.argv[0]` is the path to the script that was used to start python.
    # For example: `/home/connor/.virtualenvs/moz-phab-dev/bin/moz-phab`. Run
    # `.resolve()` to make sure we have a full path, and then the `parent` is
    # the directory for the script.
    script_dir = Path(sys.argv[0]).resolve().parent

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

    # Prevent self-update from failing when installing over OS-managed
    # installs; see bug 1876182.
    command_env = os.environ.copy()
    command_env["PIP_BREAK_SYSTEM_PACKAGES"] = "1"

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
        check_call(command, env=command_env)
