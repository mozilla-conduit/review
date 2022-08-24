# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import platform
import pytest

from mozphab.config import Config


def test_defaults(config):
    git_cmd = ["git.exe"] if platform.system() == "Windows" else ["git"]
    hg_cmd = ["hg.exe"] if platform.system() == "Windows" else ["hg"]

    assert config.no_ansi is False
    assert config.safe_mode is False
    assert config.git_remote == []
    assert config.git_command == git_cmd
    assert config.hg_command == hg_cmd
    assert config.auto_submit is False
    assert config.always_blocking is False
    assert config.warn_untracked is True
    assert config.apply_patch_to == "base"
    assert config.create_bookmark is True
    assert config.create_topic is False
    assert config.always_full_stack is False
    assert config.self_last_check == 0
    assert config.self_auto_update is True
    assert config.get_pre_releases is False
    assert config.report_to_sentry is True
    assert config.telemetry_enabled is False


def test_write(config):
    # Note: some of these values will NOT be overridden
    # based on how `config.write` is set up
    config.no_ansi = True
    config.safe_mode = True
    config.git_remote = ["test"]
    config.git_command = ["test"]
    config.hg_command = ["test"]
    config.auto_submit = True
    config.always_blocking = True
    config.warn_untracked = False
    config.apply_patch_to = "here"
    config.create_bookmark = False
    config.create_topic = True
    config.always_full_stack = True
    config.self_last_check = 12
    config.self_auto_update = False
    config.get_pre_releases = True
    config.report_to_sentry = False
    config.telemetry_enabled = True
    config.write()

    new_config = Config(filename=config._filename)
    git_cmd = ["git.exe"] if platform.system() == "Windows" else ["git"]

    assert new_config.no_ansi is True
    assert new_config.safe_mode is True
    assert new_config.git_remote[0] == "test"
    assert new_config.git_command[0] == git_cmd[0]
    assert new_config.hg_command[0] == "test"
    assert new_config.auto_submit is True
    assert new_config.always_blocking is True
    assert new_config.warn_untracked is False
    assert new_config.apply_patch_to == "here"
    assert new_config.create_bookmark is False
    assert new_config.create_topic is True
    assert new_config.always_full_stack is True
    assert new_config.self_last_check == 0
    assert new_config.self_auto_update is True
    assert new_config.get_pre_releases is False
    assert new_config.report_to_sentry is True
    assert new_config.telemetry_enabled is True


def test_invalid_int_field(config):
    config._set("updater", "self_last_check", "")
    config.write()

    with pytest.raises(ValueError) as e:
        Config(filename=config._filename)

    assert str(e.value) == (
        "could not convert updater.self_last_check to an integer: "
        "invalid literal for int() with base 10: ''"
    )


def test_invalid_bool_field(config):
    config.no_ansi = "test"
    config.write()

    with pytest.raises(ValueError) as e:
        Config(filename=config._filename)

    assert (
        str(e.value) == "could not convert ui.no_ansi to a boolean: Not a boolean: test"
    )
