# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import platform
from unittest import mock

import pytest

from mozphab import updater
from mozphab.exceptions import Error


def test_should_self_update():
    assert (
        updater.should_self_update(-1, 0) is False
    ), "Setting last check to a negative value should disable self-update."

    assert (
        updater.should_self_update(1, 250000) is False
    ), "Last check within the 3 day frequency should not self-update."

    assert (
        updater.should_self_update(1, 3000000) is True
    ), "Last check greater than the 3 day frequency should cause a self-update."


def test_parse_latest_prerelease_version():
    # Test data from the `simple` api.
    data = {
        "files": [
            {
                "filename": "MozPhab-1.2.2rc0.tar.gz",
            },
            {
                "filename": "MozPhab-1.2.2rc1-py3-none-any.whl",
            },
            {
                "filename": "MozPhab-1.2.2rc1.tar.gz",
            },
            {
                "filename": "MozPhab-1.2.0.tar.gz",
            },
        ],
    }

    assert (
        updater.parse_latest_prerelease_version(data) == "1.2.2rc1"
    ), "`get_newest_pypi_version` should detect `1.2.2rc1` as the latest version."


def test_parse_latest_version_filename_case():
    # Test data from the `simple` api.
    data = {
        "files": [
            {
                "filename": "MozPhab-1.2.0.tar.gz",
            },
            {
                "filename": "mozphab-1.2.1.tar.gz",
            },
        ],
    }

    assert (
        updater.parse_latest_prerelease_version(data) == "1.2.1"
    ), "`get_newest_pypi_version` should detect `1.2.1` as the latest version."


def make_venv_layout(root, base_executable=None):
    """Build a fake venv at `root` and return its interpreter path.

    When `base_executable` is given, the interpreter is created as a symlink
    pointing at it, mirroring how a `uv` venv links the base Python.
    """
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    executable = bin_dir / "python"

    if base_executable:
        executable.symlink_to(base_executable)
    else:
        executable.write_text("")

    return executable


def test_find_uv_receipt_detects_uv_install(monkeypatch, tmp_path):
    prefix = tmp_path / "tools" / "mozphab"
    make_venv_layout(prefix)
    (prefix / "uv-receipt.toml").write_text("[tool]\n")
    monkeypatch.setattr(updater.sys, "prefix", str(prefix))

    assert (
        updater.find_uv_receipt() == prefix / "uv-receipt.toml"
    ), "`find_uv_receipt` should locate the receipt at the environment root."
    assert (
        updater.is_uv_tool_install() is True
    ), "`is_uv_tool_install` should be `True` for a `uv` tool layout."


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Skipped because Windows requires privileges to create symlinks",
)
def test_find_uv_receipt_detects_uv_install_with_symlinked_interpreter(
    monkeypatch, tmp_path
):
    """Bug 2050987: a symlinked interpreter should not defeat `uv` detection.

    A `uv` venv symlinks `bin/python` at the base Python, so deriving the
    environment root by resolving the interpreter lands outside the tool.
    """
    base_executable = make_venv_layout(tmp_path / "base")
    prefix = tmp_path / "tools" / "mozphab"
    executable = make_venv_layout(prefix, base_executable=base_executable)
    (prefix / "uv-receipt.toml").write_text("[tool]\n")
    monkeypatch.setattr(updater.sys, "prefix", str(prefix))
    monkeypatch.setattr(updater.sys, "executable", str(executable))

    assert not executable.resolve().is_relative_to(
        prefix
    ), "The fake interpreter should resolve outside the environment root."
    assert (
        updater.is_uv_tool_install() is True
    ), "`is_uv_tool_install` should be `True` despite the symlinked interpreter."


def test_find_uv_receipt_returns_none_for_non_uv_install(monkeypatch, tmp_path):
    make_venv_layout(tmp_path)
    monkeypatch.setattr(updater.sys, "prefix", str(tmp_path))

    assert (
        updater.find_uv_receipt() is None
    ), "`find_uv_receipt` should return `None` without a `uv-receipt.toml`."
    assert (
        updater.is_uv_tool_install() is False
    ), "`is_uv_tool_install` should be `False` for a non-`uv` install."


def test_self_upgrade_dispatches_to_uv(monkeypatch):
    monkeypatch.setattr(updater, "is_uv_tool_install", lambda: True)
    uv_upgrade = mock.Mock()
    pip_upgrade = mock.Mock()
    monkeypatch.setattr(updater, "uv_upgrade", uv_upgrade)
    monkeypatch.setattr(updater, "pip_upgrade", pip_upgrade)

    updater.self_upgrade()

    assert (
        uv_upgrade.called
    ), "`self_upgrade` should use `uv_upgrade` for `uv` installs."
    assert (
        not pip_upgrade.called
    ), "`self_upgrade` should not use `pip_upgrade` for `uv` installs."


def test_self_upgrade_dispatches_to_pip(monkeypatch):
    monkeypatch.setattr(updater, "is_uv_tool_install", lambda: False)
    uv_upgrade = mock.Mock()
    pip_upgrade = mock.Mock()
    monkeypatch.setattr(updater, "uv_upgrade", uv_upgrade)
    monkeypatch.setattr(updater, "pip_upgrade", pip_upgrade)

    updater.self_upgrade()

    assert (
        pip_upgrade.called
    ), "`self_upgrade` should use `pip_upgrade` for non-`uv` installs."
    assert (
        not uv_upgrade.called
    ), "`self_upgrade` should not use `uv_upgrade` for non-`uv` installs."


def mock_successful_upgrade(monkeypatch):
    """Make `uv_upgrade` see a version change, as a real upgrade would."""
    monkeypatch.setattr(updater, "MOZPHAB_VERSION", "1.0.0")
    monkeypatch.setattr(updater, "get_mozphab_version", lambda: "1.0.1")


def test_uv_upgrade_command(monkeypatch):
    check_call = mock.Mock()
    monkeypatch.setattr(updater, "check_call", check_call)
    monkeypatch.setattr(updater.config, "get_pre_releases", False)
    monkeypatch.setattr(updater.environment, "DEBUG", False)
    mock_successful_upgrade(monkeypatch)

    updater.uv_upgrade()

    assert check_call.call_args[0][0] == [
        "uv",
        "tool",
        "upgrade",
        "MozPhab",
        "--quiet",
    ], "`uv_upgrade` should run a quiet `uv tool upgrade` for stable releases."


def test_uv_upgrade_command_pre_releases(monkeypatch):
    check_call = mock.Mock()
    monkeypatch.setattr(updater, "check_call", check_call)
    monkeypatch.setattr(updater.config, "get_pre_releases", True)
    monkeypatch.setattr(updater.environment, "DEBUG", True)
    mock_successful_upgrade(monkeypatch)

    updater.uv_upgrade()

    assert check_call.call_args[0][0] == [
        "uv",
        "tool",
        "upgrade",
        "MozPhab",
        "--prerelease",
        "allow",
    ], "`uv_upgrade` should allow pre-releases and stay verbose under `DEBUG`."


@pytest.mark.parametrize(
    "installed_version",
    (
        pytest.param("1.0.0", id="unchanged"),
        pytest.param("0.9.0", id="downgraded"),
    ),
)
def test_uv_upgrade_raises_unless_version_increases(monkeypatch, installed_version):
    """`uv tool upgrade` exits zero when it declines to upgrade a pinned tool."""
    monkeypatch.setattr(updater, "check_call", mock.Mock())
    monkeypatch.setattr(updater.config, "get_pre_releases", False)
    monkeypatch.setattr(updater.environment, "DEBUG", False)
    monkeypatch.setattr(updater, "MOZPHAB_VERSION", "1.0.0")
    monkeypatch.setattr(updater, "get_mozphab_version", lambda: installed_version)

    with pytest.raises(Error, match="did not move `moz-phab` past version 1.0.0"):
        updater.uv_upgrade()


def test_uv_upgrade_accepts_pre_release_upgrade(monkeypatch):
    """A pre-release newer than the installed version should count as an upgrade."""
    monkeypatch.setattr(updater, "check_call", mock.Mock())
    monkeypatch.setattr(updater.config, "get_pre_releases", True)
    monkeypatch.setattr(updater.environment, "DEBUG", False)
    monkeypatch.setattr(updater, "MOZPHAB_VERSION", "1.0.0")
    monkeypatch.setattr(updater, "get_mozphab_version", lambda: "1.0.1rc1")

    updater.uv_upgrade()


def test_uv_upgrade_missing_uv_raises_error(monkeypatch):
    def raise_not_found(_command):
        raise FileNotFoundError("uv")

    monkeypatch.setattr(updater, "check_call", raise_not_found)
    monkeypatch.setattr(updater.config, "get_pre_releases", False)
    monkeypatch.setattr(updater.environment, "DEBUG", False)

    with pytest.raises(Error, match="not found on your PATH"):
        updater.uv_upgrade()
