# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import subprocess
import sys
import time
import urllib.request
import __main__ as script_module

from distutils.dist import Distribution
from pathlib import Path
from pkg_resources import get_distribution, parse_version

from mozphab import environment

from .arcanist import update_arc
from .config import config
from .exceptions import Error
from .logger import logger, stop_logging
from .subprocess_wrapper import check_call

ARC_UPDATE_FREQUENCY = 24 * 7  # hours
SELF_UPDATE_FREQUENCY = 24 * 3  # hours


def get_installed_distribution():
    return get_distribution("MozPhab")


def get_name_and_version():
    dist = get_installed_distribution()
    return "{} ({})".format(dist.project_name, dist.version)


def get_pypi_info():
    url = "https://pypi.org/pypi/MozPhab/json"
    output = urllib.request.urlopen(urllib.request.Request(url), timeout=30).read()
    response = json.loads(output.decode("utf-8"))
    return response["info"]


def check_for_updates(with_arc=True):
    """Log a message if an update is required/available"""
    # Update arc.
    if (
        with_arc
        and config.arc_last_check >= 0
        and time.time() - config.arc_last_check > ARC_UPDATE_FREQUENCY * 60 * 60
    ):
        update_arc()

    # Update self.
    if (
        config.self_last_check >= 0
        and time.time() - config.self_last_check > SELF_UPDATE_FREQUENCY * 60 * 60
    ):
        config.self_last_check = int(time.time())
        current_version = get_installed_distribution().version
        pypi_info = get_pypi_info()
        logger.debug(
            "Versions - local: {}, PyPI: {}".format(
                current_version, pypi_info["version"]
            )
        )

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
                "MozPhab requires Python in version >= {}".format(
                    pypi_info["version"], ".".join(required_python_version)
                )
            )

        config.write()

        if parse_version(current_version) >= parse_version(pypi_info["version"]):
            logger.debug("update check not required")
            return

        if config.self_auto_update:
            logger.info("Upgrading to version %s", pypi_info["version"])
            self_upgrade()
            logger.info("Restarting...")

            # Explicitly close the log files to avoid issues with processes holding
            # exclusive logs on the files on Windows.
            stop_logging()

            # It's best to ignore errors here as they will be reported by the
            # new moz-phab process.
            p = subprocess.run(sys.argv)
            sys.exit(p.returncode)

        logger.warning(
            "Version %s of `moz-phab` is now available", pypi_info["version"]
        )


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

    # If moz-phab was installed with --user, we need to pass it to pip
    # Create "install" distutils command with --user to find the scripts_path
    d = Distribution()
    d.parse_config_files()
    i = d.get_command_obj("install", create=True)
    # Forcing the environment detected by Distribution to the --user one
    i.user = True
    i.prefix = i.exec_prefix = i.home = i.install_base = i.install_platbase = None
    i.finalize_options()
    # Checking if the moz-phab script is installed in user's scripts directory
    script_dir = Path(script_module.__file__).resolve().parent
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
