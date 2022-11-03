# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest
import uuid

from unittest import mock

from mozphab import telemetry
from mozphab.bmo import BMOAPIError


@pytest.fixture
def get_telemetry():
    return telemetry.Telemetry()


class Args:
    def __init__(self, needs_repo=False, command="submit", force=None, force_vcs=None):
        self.needs_repo = needs_repo
        self.command = command
        self.no_arc = True
        if force_vcs is not None:
            self.force_vcs = force_vcs
        if force is not None:
            self.force = force


def test_vcs(get_telemetry):
    class Repo:
        def __init__(self, vcs, vcs_version):
            self.vcs = vcs
            self.vcs_version = vcs_version

    t = get_telemetry
    t.set_vcs(Repo("hg", "5.2"))
    assert "hg" == t.environment.vcs.name.test_get_value()
    assert "5.2" == t.environment.vcs.version.test_get_value()


@mock.patch("mozphab.telemetry.platform")
@mock.patch("mozphab.telemetry.distro")
def test_set_os(m_distro, m_platform, get_telemetry):
    t = get_telemetry
    m_platform.uname.side_effect = (("Linux", "node", "release", None, None, None),)
    m_distro.linux_distribution.side_effect = (("debian", "2020.1", None),)
    t._set_os()
    assert "debian 2020.1" == t.environment.distribution_version.test_get_value()

    m_platform.uname.side_effect = (("Windows", "node", "release", None, None, None),)
    m_platform.win32_ver.side_effect = (("10", "10.0.18362", "", "multiprocessor"),)
    t._set_os()
    assert "10.0.18362" == t.environment.distribution_version.test_get_value()

    m_platform.uname.side_effect = (("Darwin", "node", "release", None, None, None),)
    m_platform.mac_ver.side_effect = (("10.15.3", ("", "", ""), "x86_64"),)
    t._set_os()
    assert "10.15.3" == t.environment.distribution_version.test_get_value()

    m_platform.uname.side_effect = (("Something", "node", "release", None, None, None),)
    t._set_os()
    assert "release" == t.environment.distribution_version.test_get_value()


@mock.patch("mozphab.telemetry.platform")
def test_set_python(m_platform, get_telemetry):
    m_platform.python_version.side_effect = ("3.7.6",)
    t = get_telemetry
    t._set_python()
    assert "3.7.6" == t.environment.python_version.test_get_value()


@mock.patch("mozphab.telemetry.config")
@mock.patch("mozphab.telemetry.user_data")
@mock.patch("mozphab.telemetry.prompt")
def test_update_user_data(m_prompt, m_user_data, m_config):
    m_config.configure_mock(telemetry_enabled=False)

    # user_data not updated
    m_user_data.set_user_data.return_value = False
    telemetry.update_user_data()
    assert not m_config.telemetry_enabled
    m_config.write.assert_not_called()

    # not instantiated, BMOAPIError
    m_user_data.set_user_data.side_effect = BMOAPIError
    with pytest.raises(BMOAPIError):
        telemetry.update_user_data()

    # switched off, not instantiated, user data retrieved from BMO, employee
    m_user_data.set_user_data.side_effect = None
    m_user_data.set_user_data.return_value = True
    m_user_data.configure_mock(
        is_employee=True,
        is_data_collected=True,
    )
    m_config.configure_mock(telemetry_enabled=True)
    telemetry.update_user_data()
    assert m_config.telemetry_enabled
    m_config.write.assert_called_once()

    # switched off, not instantiated, user data retrieved from BMO, not employee
    m_user_data.is_employee = False
    m_config.telemetry_enabled = False

    # not opt-in
    m_config.write.reset_mock()
    m_prompt.return_value = "No"
    telemetry.update_user_data()
    assert not m_config.telemetry_enabled
    m_config.write.assert_called_once()

    # opt-in
    m_config.write.reset_mock()
    m_prompt.return_value = "Yes"
    telemetry.update_user_data()
    assert m_config.telemetry_enabled
    m_config.write.assert_called_once()


@mock.patch("mozphab.telemetry.update_user_data")
@mock.patch("mozphab.telemetry.user_data")
@mock.patch("mozphab.telemetry.config")
def test_set_metrics(m_config, m_user_data, m_update_user_data, get_telemetry):
    t = get_telemetry
    t._set_os = mock.Mock()
    t._set_python = mock.Mock()
    m_config.configure_mock(telemetry_enabled=True)

    # switched on in `update_user_data`
    m_config.configure_mock(telemetry_enabled=False)
    m_update_user_data.side_effect = None
    m_update_user_data.return_value = True
    m_user_data.configure_mock(
        is_data_collected=True,
        installation_id="0000aabb-bbaa-0000-aabb-0000bbaa0000",
        user_code="1111ffee-eeff-1111-ffee-1111eeff1111",
    )
    # telemetry is enabled in `update_user_data`
    t.set_metrics(Args(needs_repo=True))
    t._set_os.assert_called_once()
    t._set_python.assert_called_once()
    assert "submit" == t.usage.command.test_get_value()
    assert t.usage.override_switch.test_get_value() is False
    # `command_time.start()` has been called, but not stop, it has no value yet
    assert t.usage.command_time.test_get_value() is None
    assert (
        uuid.UUID("0000aabb-bbaa-0000-aabb-0000bbaa0000")
        == t.user.installation.test_get_value()
    )
    assert (
        uuid.UUID("1111ffee-eeff-1111-ffee-1111eeff1111") == t.user.id.test_get_value()
    )


@mock.patch("mozphab.telemetry.Telemetry")
@mock.patch("mozphab.telemetry.update_user_data")
@mock.patch("mozphab.telemetry.user_data")
@mock.patch("mozphab.telemetry.config")
def test_configure_telemetry(
    m_config,
    m_user_data,
    m_update_user_data,
    m_telemetry,
):
    # install-certificate -> disabled
    telemetry.configure_telemetry(Args(command="install-certificate"))
    assert isinstance(telemetry.telemetry(), telemetry.TelemetryDisabled)

    # self-update -> disabled
    telemetry.configure_telemetry(Args(command="self-update"))
    assert isinstance(telemetry.telemetry(), telemetry.TelemetryDisabled)

    # off -> disabled
    m_config.configure_mock(telemetry_enabled=False)
    m_user_data.configure_mock(is_data_collected=True)
    telemetry.configure_telemetry(Args())
    assert isinstance(telemetry.telemetry(), telemetry.TelemetryDisabled)

    # on, no user data -> disabled
    m_config.configure_mock(telemetry_enabled=True)
    m_user_data.configure_mock(is_data_collected=False)
    telemetry.configure_telemetry(Args())
    assert isinstance(telemetry.telemetry(), telemetry.TelemetryDisabled)

    # on, bmo error -> disabled
    m_config.configure_mock(telemetry_enabled=True)
    m_update_user_data.side_effect = BMOAPIError
    telemetry.configure_telemetry(Args(needs_repo=True))
    assert isinstance(telemetry.telemetry(), telemetry.TelemetryDisabled)

    # on --> enabled
    m_config.configure_mock(telemetry_enabled=True)
    m_user_data.configure_mock(is_data_collected=True)
    telemetry.configure_telemetry(Args())
    m_telemetry.assert_called_once()
    assert not isinstance(telemetry.telemetry(), telemetry.TelemetryDisabled)
