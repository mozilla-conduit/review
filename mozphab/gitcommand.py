# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import re
from pathlib import Path
from shutil import which

from .config import config
from .exceptions import Error
from .helpers import parse_config, which_path
from .subprocess_wrapper import check_call, check_output


class GitCommand:
    def __init__(self):
        """Check if Git is available, set initial values."""
        self.command = config.git_command.copy()
        if not which_path(self.command[0]):
            raise Error("Failed to find Git executable ({})".format(self.command[0]))

        # `self._env` is a dict representing environment used in all git commands.
        self._env = os.environ.copy()

        self.extensions = []
        self._cinnabar_installed = None
        self.safe_mode = config.safe_mode
        self.email = ""

    def call(self, git_args, **kwargs):
        unicode_args = [
            "-c",
            "i18n.logOutputEncoding=UTF-8",
            "-c",
            "i18n.commitEncoding=UTF-8",
        ]
        check_call(self.command + unicode_args + git_args, env=self._env, **kwargs)

    def output(self, git_args, extra_env=None, **kwargs):
        env = dict(self._env)
        if extra_env:
            env.update(extra_env)

        unicode_args = [
            "-c",
            "i18n.logOutputEncoding=UTF-8",
            "-c",
            "i18n.commitEncoding=UTF-8",
        ]
        return check_output(self.command + unicode_args + git_args, env=env, **kwargs)

    def set_args(self, args):
        """Read and set the configuration."""
        git_config = parse_config(self.output(["config", "--list"], never_log=True))

        safe_options = []

        # Need to use the correct username.
        if "user.email" not in git_config:
            raise Error("user.email is not configured in your gitconfig")

        self.email = git_config["user.email"]
        safe_options.extend(["-c", "user.email=%s" % git_config["user.email"]])

        if "user.name" in git_config:
            safe_options.extend(["-c", "user.name=%s" % git_config["user.name"]])

        if "cinnabar.helper" in git_config:
            self.extensions.append("cinnabar")
            safe_options.extend(
                ["-c", "cinnabar.helper=%s" % git_config["cinnabar.helper"]]
            )

        if args.safe_mode or self.safe_mode:
            # Ignore the user's Git config
            # To make Git not read the `~/.gitconfig` we need to temporarily change the
            # `$HOME` variable.
            self._env["HOME"] = ""
            self._env["XDG_CONFIG_HOME"] = ""
            self.command.extend(safe_options)

    @property
    def is_cinnabar_installed(self):
        """Check if Cinnabar extension is callable."""
        if self._cinnabar_installed is None:
            # Unfortunately we cannot use --list-cmds as it requires git v2.18+

            # Normally cinnabar will be listed in the 'External commands' section.
            for line in self.output(["help", "--all"]):
                if re.search(r"^\s+cinnabar\b", line):
                    self._cinnabar_installed = True
                    break

            # Cinnabar might be installed in git's exec-path, which won't be
            # included in the `git help --all` output, nor is it necessarily
            # on the path.
            if not self._cinnabar_installed:
                exec_path = Path(self.output(["--exec-path"], split=False))
                if (exec_path / "git-cinnabar").exists():
                    self._cinnabar_installed = True

            # Finally check on the system path.
            if not self._cinnabar_installed:
                self._cinnabar_installed = which("git-cinnabar") is not None

        return self._cinnabar_installed
