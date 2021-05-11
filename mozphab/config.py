# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import configparser
import io
import os

from mozphab import environment

from .logger import logger


class Config(object):
    def __init__(self, should_access_file=True, filename=None):
        """Sets default config and overrides it with values from the config file.

        Kwargs:
            should_access_file (bool) - should the file be read/write?
            filename (string) - file to read/write the config - used only for testing.
        """
        self._filename = filename or os.path.join(
            environment.HOME_DIR, ".moz-phab-config"
        )
        # human-readable name, will diverge from _filename if initialized with filename
        self.name = "~/.moz-phab-config"

        # Default values.
        defaults = """
            [ui]
            no_ansi = False

            [vcs]
            safe_mode = False

            [git]
            remote =
            command_path =

            [hg]
            command_path =

            [submit]
            auto_submit = False
            always_blocking = False
            warn_untracked = True

            [patch]
            apply_to = base
            create_bookmark = True
            always_full_stack = False

            [updater]
            self_last_check = 0
            arc_last_check = 0
            self_auto_update = True
            get_pre_releases = False

            [error_reporting]
            report_to_sentry = True

            [telemetry]
            enabled = False
            """

        self._config = configparser.ConfigParser()
        self._config.read_file(
            io.StringIO("\n".join([l.strip() for l in defaults.splitlines()]))
        )

        if self._config.has_section("arc"):
            self._config.remove_section("arc")

        if should_access_file:
            self._config.read([self._filename])

        self.no_ansi = self._config.getboolean("ui", "no_ansi")
        self.safe_mode = self._config.getboolean("vcs", "safe_mode")
        self.auto_submit = self._config.getboolean("submit", "auto_submit")
        self.always_blocking = self._config.getboolean("submit", "always_blocking")
        self.warn_untracked = self._config.getboolean("submit", "warn_untracked")
        self.apply_patch_to = self._config.get("patch", "apply_to")
        self.create_bookmark = self._config.getboolean("patch", "create_bookmark")
        self.always_full_stack = self._config.getboolean("patch", "always_full_stack")
        self.self_last_check = self._config.getint("updater", "self_last_check")
        self.self_auto_update = self._config.getboolean("updater", "self_auto_update")
        self.get_pre_releases = self._config.getboolean("updater", "get_pre_releases")
        self.arc_last_check = self._config.getint("updater", "arc_last_check")
        git_remote = self._config.get("git", "remote")
        self.git_remote = git_remote.replace(" ", "").split(",") if git_remote else []
        self.report_to_sentry = self._config.getboolean(
            "error_reporting", "report_to_sentry"
        )
        self.telemetry_enabled = self._config.getboolean("telemetry", "enabled")
        self._git_command = self._config.get("git", "command_path")
        self.git_command = (
            [self._git_command] if self._git_command else environment.GIT_COMMAND
        )
        self._hg_command = self._config.get("hg", "command_path")
        self.hg_command = (
            [self._hg_command] if self._hg_command else environment.HG_COMMAND
        )

        if should_access_file and not os.path.exists(self._filename):
            self.write()

        self.arc = None

    def _set(self, section, option, value):
        if not self._config.has_section(section):
            self._config.add_section(section)
        self._config.set(section, option, str(value))

    def write(self):
        if os.path.exists(self._filename):
            logger.debug("updating %s", self._filename)
            self._set("submit", "auto_submit", self.auto_submit)
            self._set("patch", "always_full_stack", self.always_full_stack)
            self._set("updater", "self_last_check", self.self_last_check)
            self._set("updater", "arc_last_check", self.arc_last_check)
            self._set("updater", "self_auto_update", self.self_auto_update)
            self._set("updater", "get_pre_releases", self.get_pre_releases)
            self._set("telemetry", "enabled", self.telemetry_enabled)

        else:
            logger.debug("creating %s", self._filename)
            self._set("ui", "no_ansi", self.no_ansi)
            self._set("vcs", "safe_mode", self.safe_mode)
            self._set("git", "remote", ", ".join(self.git_remote))
            self._set("git", "command_path", ", ".join(self._git_command))
            self._set("hg", "command_path", ", ".join(self.hg_command))
            self._set("submit", "auto_submit", self.auto_submit)
            self._set("submit", "always_blocking", self.always_blocking)
            self._set("submit", "warn_untracked", self.warn_untracked)
            self._set("patch", "apply_to", self.apply_patch_to)
            self._set("patch", "create_bookmark", self.create_bookmark)
            self._set("patch", "always_full_stack", self.always_full_stack)
            self._set("telemetry", "enabled", self.telemetry_enabled)

        with open(self._filename, "w", encoding="utf-8") as f:
            self._config.write(f)


config = Config()
