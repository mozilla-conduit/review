# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import distro
import logging
import platform
from pathlib import Path
from typing import (
    Union,
)

from mozphab import environment

from .bmo import BMOAPIError
from .config import config
from .environment import MOZPHAB_VERSION
from .helpers import prompt
from .logger import logger
from .user import user_data


class Telemetry:
    def __init__(self):
        """Initiate Glean, load pings and metrics."""
        import glean

        logging.getLogger("glean").setLevel(logging.DEBUG)
        logger.debug("Initializing Glean...")

        glean.Glean.initialize(
            application_id="MozPhab",
            application_version=MOZPHAB_VERSION,
            upload_enabled=True,
            configuration=glean.Configuration(),
            data_dir=Path(environment.MOZBUILD_PATH) / "telemetry-data",
        )

        self._pings = glean.load_pings(environment.MOZPHAB_MAIN_DIR / "pings.yaml")
        self._metrics = glean.load_metrics(
            environment.MOZPHAB_MAIN_DIR / "metrics.yaml"
        )

    @property
    def environment(self):
        return self._metrics.mozphab.environment

    @property
    def usage(self):
        return self._metrics.mozphab.usage

    @property
    def user(self):
        return self._metrics.mozphab.user

    @property
    def submission(self):
        return self._metrics.mozphab.submission

    def _set_os(self):
        """Collect human readable information about the OS version.

        For Linux it is setting a distribution name and version.
        """
        system, node, release, version, machine, processor = platform.uname()
        if system == "Linux":
            distribution_name, distribution_number, _ = distro.linux_distribution(
                full_distribution_name=False
            )
            distribution_version = " ".join([distribution_name, distribution_number])
        elif system == "Windows":
            _release, distribution_version, _csd, _ptype = platform.win32_ver()
        elif system == "Darwin":
            distribution_version, _versioninfo, _machine = platform.mac_ver()
        else:
            distribution_version = release

        self.environment.distribution_version.set(distribution_version)

    def _set_python(self):
        self.environment.python_version.set(platform.python_version())

    def set_vcs(self, repo):
        self.environment.vcs.name.set(repo.vcs)
        self.environment.vcs.version.set(repo.vcs_version)

    def submit(self):
        self._pings.usage.submit()
        logger.debug("Telemetry submit called.")

    def set_metrics(self, args):
        """Sets metrics common to all commands."""
        self.usage.command.set(args.command)
        self._set_os()
        self._set_python()
        self.usage.override_switch.set(
            getattr(args, "force_vcs", False) or getattr(args, "force", False)
        )
        self.usage.command_time.start()
        self.user.installation.set(user_data.installation_id)
        self.user.id.set(user_data.user_code)


class TelemetryDisabled:
    """Dummy class that does nothing."""

    def __init__(*args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return self

    def __getattr__(self, *args, **kwargs):
        return self


def update_user_data():
    """Update user_data to enable or disable Telemetry.

    If employment data has been changed Telemetry might be switched on
    automatically. The opt-in decision is taken for the new employee. Non employees
    will have an option to enable data collection.
    """
    is_employee_changed = user_data.set_user_data()
    if not is_employee_changed:
        return

    # Switch on Telemetry for employee or ask to opt-in for non-employee
    if user_data.is_employee:
        logger.warning(
            "Enabled collecting MozPhab usage data.\n"
            "See https://moz-conduit.readthedocs.io/en/latest"
            "/mozphab-data-collection.html"
        )
        config.telemetry_enabled = True
    else:
        # user is new or no longer employee
        opt_in = (
            prompt(
                "Would you like to allow MozPhab to collect usage data?",
                ["Yes", "No"],
            )
            == "Yes"
        )
        if opt_in:
            config.telemetry_enabled = True
        else:
            logger.info(
                "MozPhab usage data collection disabled.\n"
                "See https://moz-conduit.readthedocs.io/en/latest"
                "/mozphab-data-collection.html"
            )
            config.telemetry_enabled = False
    config.write()


def configure_telemetry(args):
    if args.command == "install-certificate":
        # Collecting data without a certificate is not possible.
        _Globals.telemetry = TelemetryDisabled()
        return

    if args.command == "self-update":
        # Avoid locking issues on Windows by not loading Glean when we're updating
        _Globals.telemetry = TelemetryDisabled()
        return

    # `user_data` file will remain empty until user calls MozPhab with a command
    # requiring existence of the Repository.
    if args.needs_repo:
        try:
            update_user_data()
        except BMOAPIError as err:
            # Error in retrieving user status.
            # We quietly allow to work without enabling Telemetry.
            logger.debug("BMOAPIError: %s", err)
            _Globals.telemetry = TelemetryDisabled()
            return

    # We can't call telemetry if user data was never collected.
    if not config.telemetry_enabled or not user_data.is_data_collected:
        _Globals.telemetry = TelemetryDisabled()
        return

    # Enable telemetry by swapping the telemetry global with a Glean backed object.
    _Globals.telemetry = Telemetry()
    telemetry().set_metrics(args)


def telemetry():
    return _Globals.telemetry


class _Globals:
    """Container for holding globals in a way that can be easily replaced."""

    telemetry: Union[Telemetry, TelemetryDisabled] = TelemetryDisabled()
