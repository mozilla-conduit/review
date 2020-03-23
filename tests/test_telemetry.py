# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import mock
import pytest
import uuid

from mozphab import telemetry
from mozphab.bmo import BMOAPIError


@pytest.fixture
@mock.patch("mozphab.telemetry.config")
def get_telemetry(m_config):
    m_config.telemetry_enabled = True
    return telemetry.Telemetry()


@mock.patch("mozphab.telemetry.config")
def test_telemetry_enabled(m_config):
    m_config.telemetry_enabled = True
    passed = mock.Mock()

    @telemetry.if_telemetry_enabled
    def telemetry_pass():
        passed()

    telemetry_pass()
    passed.assert_called_once()

    passed.reset_mock()
    m_config.telemetry_enabled = False
    telemetry_pass()
    passed.assert_not_called()


@mock.patch("mozphab.telemetry.config")
def test_vcs(m_config, get_telemetry):
    class Repo:
        def __init__(self, vcs, vcs_version):
            self.vcs = vcs
            self.vcs_version = vcs_version

    m_config.telemetry_enabled = True
    t = get_telemetry
    t.set_vcs(Repo("hg", "5.2"))
    assert "hg" == t.metrics.mozphab.environment.vcs.name.test_get_value()
    assert "5.2" == t.metrics.mozphab.environment.vcs.version.test_get_value()


@mock.patch("mozphab.telemetry.platform")
@mock.patch("mozphab.telemetry.distro")
@mock.patch("mozphab.telemetry.config")
def test_set_os(m_config, m_distro, m_platform, get_telemetry):
    m_config.telemetry_enabled = True
    t = get_telemetry
    m_platform.uname.side_effect = (("Linux", "node", "release", None, None, None),)
    m_distro.linux_distribution.side_effect = (("debian", "2020.1", None),)
    t.set_os()
    assert (
        "debian 2020.1"
        == t.metrics.mozphab.environment.distribution_version.test_get_value()
    )

    m_platform.uname.side_effect = (("Windows", "node", "release", None, None, None),)
    m_platform.win32_ver.side_effect = (("10", "10.0.18362", "", "multiprocessor"),)
    t.set_os()
    assert (
        "10.0.18362"
        == t.metrics.mozphab.environment.distribution_version.test_get_value()
    )

    m_platform.uname.side_effect = (("Darwin", "node", "release", None, None, None),)
    m_platform.mac_ver.side_effect = (("10.15.3", ("", "", ""), "x86_64"),)
    t.set_os()
    assert (
        "10.15.3" == t.metrics.mozphab.environment.distribution_version.test_get_value()
    )

    m_platform.uname.side_effect = (("Something", "node", "release", None, None, None),)
    t.set_os()
    assert (
        "release" == t.metrics.mozphab.environment.distribution_version.test_get_value()
    )


@mock.patch("mozphab.telemetry.platform")
@mock.patch("mozphab.telemetry.config")
def test_set_python(m_config, m_platform, get_telemetry):
    m_config.telemetry_enabled = True
    m_platform.python_version.side_effect = ("3.7.6",)
    t = get_telemetry
    t.set_python()
    assert "3.7.6" == t.metrics.mozphab.environment.python_version.test_get_value()


@mock.patch("mozphab.telemetry.config")
def test_disable(m_config, get_telemetry):
    get_telemetry.disable(write=False)
    assert not m_config.telemetry_enabled
    m_config.write.assert_not_called()

    get_telemetry.disable()
    assert not m_config.telemetry_enabled
    m_config.write.assert_called_once()


@mock.patch("mozphab.telemetry.config")
def test_enable(m_config, get_telemetry):
    get_telemetry.enable()
    assert m_config.telemetry_enabled
    m_config.write.assert_called_once()


@mock.patch("mozphab.telemetry.config")
@mock.patch("mozphab.telemetry.user_data")
@mock.patch("mozphab.telemetry.prompt")
def test_update_user_data(m_prompt, m_user_data, m_config, get_telemetry):
    m_config.configure_mock(telemetry_enabled=False)
    t = get_telemetry

    # user_data not updated
    m_user_data.set_user_data.return_value = False
    t.update_user_data()
    assert not m_config.telemetry_enabled
    m_config.write.assert_not_called()

    # not instantiated, BMOAPIError
    m_user_data.set_user_data.side_effect = BMOAPIError
    with pytest.raises(BMOAPIError):
        t.update_user_data()

    # switched off, not instantiated, user data retrieved from BMO, employee
    m_user_data.set_user_data.side_effect = None
    m_user_data.set_user_data.return_value = True
    m_user_data.configure_mock(
        is_employee=True, is_data_collected=True,
    )
    m_config.configure_mock(telemetry_enabled=True)
    t.update_user_data()
    assert m_config.telemetry_enabled
    m_config.write.assert_called_once()

    # switched off, not instantiated, user data retrieved from BMO, not employee
    m_user_data.is_employee = False
    m_config.telemetry_enabled = False

    # not opt-in
    m_config.write.reset_mock()
    m_prompt.return_value = "No"
    t.update_user_data()
    assert not m_config.telemetry_enabled
    m_config.write.assert_called_once()

    # opt-in
    m_config.write.reset_mock()
    m_prompt.return_value = "Yes"
    t.update_user_data()
    assert m_config.telemetry_enabled
    m_config.write.assert_called_once()


@mock.patch("mozphab.telemetry.user_data")
@mock.patch("mozphab.telemetry.config")
def test_set_metrics(m_config, m_user_data, get_telemetry):
    t = get_telemetry
    t.update_user_data = mock.Mock()
    t.set_os = mock.Mock()
    t.set_python = mock.Mock()
    m_config.configure_mock(telemetry_enabled=True)

    class Args:
        def __init__(
            self, needs_repo=False, command="submit", force=None, force_vcs=None
        ):
            self.needs_repo = needs_repo
            self.command = command
            self.no_arc = True
            if force_vcs is not None:
                self.force_vcs = force_vcs

            if force is not None:
                self.force = force

    # is_development
    t.set_metrics(None, is_development=True)
    assert not m_config.telemetry_enabled
    t.update_user_data.assert_not_called()
    t.set_os.assert_not_called()

    # install-certificate
    t.set_metrics(Args(command="install-certificate"))
    assert not m_config.telemetry_enabled
    t.update_user_data.assert_not_called()
    t.set_os.assert_not_called()

    # switched off, no repo
    m_user_data.configure_mock(is_data_collected=False,)
    m_config.configure_mock(telemetry_enabled=True)
    t.set_metrics(Args())
    t.update_user_data.assert_not_called()
    assert not t.metrics.mozphab.usage.command.test_has_value()
    t.set_os.assert_not_called()

    # not instantiated, repo, BMOAPIError
    t.update_user_data.side_effect = BMOAPIError
    t.set_metrics(Args(needs_repo=True))
    m_config.write.assert_not_called()
    assert not m_config.telemetry_enabled
    t.set_os.assert_not_called()

    # switched on in `update_user_data`
    m_config.configure_mock(telemetry_enabled=False)
    t.update_user_data.side_effect = None
    t.update_user_data.return_value = True
    m_user_data.configure_mock(
        is_data_collected=True,
        installation_id="0000aabbbbaa0000aabb0000bbaa0000",
        user_code="1111ffeeeeff1111ffee1111eeff1111",
    )
    # telemetry is enabled in `update_user_data`
    t.enable()
    t.set_metrics(Args(needs_repo=True))
    t.set_os.assert_called_once()
    t.set_python.assert_called_once()
    assert "submit" == t.metrics.mozphab.usage.command.test_get_value()
    assert t.metrics.mozphab.usage.override_switch.test_get_value() is False
    # `command_time.start()` has been called, but not stop, it has no value yet
    assert not t.metrics.mozphab.usage.command_time.test_has_value()
    assert (
        uuid.UUID("0000aabb-bbaa-0000-aabb-0000bbaa0000")
        == t.metrics.mozphab.user.installation.test_get_value()
    )
    assert (
        uuid.UUID("1111ffee-eeff-1111-ffee-1111eeff1111")
        == t.metrics.mozphab.user.id.test_get_value()
    )

    # switched off in `update_user_data`
    t.set_os.reset_mock()
    m_config.configure_mock(telemetry_enabled=True)
    t.update_user_data.return_value = True
    m_user_data.configure_mock(is_data_collected=True,)
    # telemetry is disabled in `update_user_data`
    t.disable()
    t.set_metrics(Args(needs_repo=True))
    t.set_os.assert_not_called()
