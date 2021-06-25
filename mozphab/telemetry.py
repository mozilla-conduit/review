# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import distro
import logging
import platform

from glean import Glean, Configuration, load_metrics, load_pings
from pathlib import Path

from mozphab import environment

from .bmo import BMOAPIError
from .config import config
from .environment import MOZPHAB_VERSION
from .helpers import prompt
from .logger import logger
from .user import user_data

logging.getLogger("glean").setLevel(logging.DEBUG)


def if_telemetry_enabled(func):
    def wrapper(*args, **kwargs):
        if config.telemetry_enabled:
            func(*args, **kwargs)

    return wrapper


class Telemetry:
    def __init__(self):
        """Initiate Glean, load pings and metrics."""

        logger.debug("Initializing Glean...")
        Glean.initialize(
            application_id="MozPhab",
            application_version=MOZPHAB_VERSION,
            upload_enabled=config.telemetry_enabled,
            configuration=Configuration(),
            data_dir=Path(environment.MOZBUILD_PATH) / "telemetry-data",
        )

        self.pings = load_pings(environment.MOZPHAB_MAIN_DIR / "pings.yaml")
        self.metrics = load_metrics(environment.MOZPHAB_MAIN_DIR / "metrics.yaml")

    @if_telemetry_enabled
    def set_os(self):
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

        self.metrics.mozphab.environment.distribution_version.set(distribution_version)

    @if_telemetry_enabled
    def set_python(self):
        self.metrics.mozphab.environment.python_version.set(platform.python_version())

    @if_telemetry_enabled
    def set_vcs(self, repo):
        self.metrics.mozphab.environment.vcs.name.set(repo.vcs)
        self.metrics.mozphab.environment.vcs.version.set(repo.vcs_version)

    @if_telemetry_enabled
    def submit(self):
        self.pings.usage.submit()
        logger.debug("Telemetry submit called.")

    def enable(self):
        Glean.set_upload_enabled(True)
        config.telemetry_enabled = True
        config.write()

    def disable(self, write=True):
        Glean.set_upload_enabled(False)
        config.telemetry_enabled = False
        if write:
            config.write()

    def update_user_data(self):
        """Update user_data and enable or disable Telemetry.

        If employment data has been changed Telemetry might be switched on
        automatically. The opt-in decision is taken for the new employee. Non employees
        will have an option to enable data collection.
        """
        is_employee_changed = user_data.set_user_data()

        # Switch on Telemetry for employee or ask to opt-in for non-employee
        if is_employee_changed:
            if user_data.is_employee:
                logger.warning(
                    "Enabled collecting MozPhab usage data.\n"
                    "See https://moz-conduit.readthedocs.io/en/latest"
                    "/mozphab-data-collection.html"
                )
                self.enable()
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
                    self.enable()
                else:
                    logger.info(
                        "MozPhab usage data collection disabled.\n"
                        "See https://moz-conduit.readthedocs.io/en/latest"
                        "/mozphab-data-collection.html"
                    )
                    self.disable()

    def set_metrics(self, args, is_development=False):
        """Set the generic metrics.

        Updates user_data and sets metrics common to all commands.
        """
        if is_development:
            self.disable(write=False)
            return

        if args.command == "install-certificate":
            # Collecting data without a certificate is not possible.
            self.disable(write=False)
            return

        # `user_data` file will remain empty until user calls MozPhab with a command
        # requiring existence of the Repository.
        if args.needs_repo:
            try:
                self.update_user_data()
            except BMOAPIError as err:
                # Error in retrieving user status.
                # We quietly allow to work without Telemetry.
                self.disable(write=False)
                logger.debug("BMOAPIErrori: %s", str(err))
                return

        # We can't call Telemetry if user data was never collected.
        if not config.telemetry_enabled or not user_data.is_data_collected:
            return

        self.metrics.mozphab.usage.command.set(args.command)
        self.set_os()
        self.set_python()
        self.metrics.mozphab.usage.override_switch.set(
            getattr(args, "force_vcs", False) or getattr(args, "force", False)
        )
        self.metrics.mozphab.usage.command_time.start()
        self.metrics.mozphab.user.installation.set(user_data.installation_id)
        self.metrics.mozphab.user.id.set(user_data.user_code)


telemetry = Telemetry()
