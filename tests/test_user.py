# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import mock

from contextlib import contextmanager
from mozphab import user

from mozphab.conduit import ConduitAPIError


@mock.patch("mozphab.user.USER_INFO_FILE")
@mock.patch("mozphab.user.json")
def test_save_user_info(m_json, m_file, user_data):
    @contextmanager
    def with_open(*_, **__):
        yield None

    m_file.open = with_open
    # create file
    m_file.exists.return_value = False
    user_info = dict(is_employee=True, user_code="a", installation_id="b", last_check=1)
    user_data.save_user_info(**user_info)
    m_json.dump.assert_called_once_with(user_info, None, sort_keys=True, indent=2)

    # update file
    m_file.exists.return_value = True
    m_json.reset_mock()
    m_json.load.return_value = user_info
    user_data.save_user_info(user_code="c")
    m_json.dump.assert_called_once_with(
        dict(is_employee=True, user_code="c", installation_id="b", last_check=1),
        None,
        sort_keys=True,
        indent=2,
    )


def test_is_data_collected(user_data):
    user_data.update_from_dict(
        dict(is_employee=None, user_code="u", installation_id="i", last_check=1)
    )
    assert not user_data.is_data_collected
    user_data.update_from_dict(
        dict(is_employee=True, user_code=None, installation_id="i", last_check=1)
    )
    assert not user_data.is_data_collected
    user_data.update_from_dict(
        dict(is_employee=False, user_code="u", installation_id=None, last_check=1)
    )
    assert not user_data.is_data_collected
    user_data.update_from_dict(
        dict(is_employee=True, user_code="u", installation_id="i", last_check=None)
    )
    assert not user_data.is_data_collected
    user_data.update_from_dict(
        dict(is_employee=False, user_code="u", installation_id="i", last_check=1)
    )
    assert user_data.is_data_collected


@mock.patch("mozphab.user.hashlib")
@mock.patch("mozphab.user.UserData.whoami")
@mock.patch("mozphab.user.UserData.save_user_info")
@mock.patch("mozphab.user.USER_INFO_FILE")
@mock.patch("mozphab.user.time")
@mock.patch("mozphab.user.uuid")
def test_set_user_data(m_uuid, m_time, m_file, m_save, m_whoami, m_hashlib, user_data):
    m_save.return_value = False
    m_file.exists.return_value = True
    m_time.time.return_value = user.EMPLOYEE_CHECK_FREQUENCY - 1
    # all data saved in file, no need to update
    user_data.update_from_dict(
        dict(is_employee=False, user_code="u", installation_id="i", last_check=2)
    )
    assert not user_data.set_user_data()
    m_whoami.assert_not_called()

    # return None if `from_file_only` == True and one of the info is not not saved
    user_data.is_employee = None
    assert not user_data.set_user_data(from_file_only=True)
    user_data.is_employee = True
    user_data.user_code = None
    assert not user_data.set_user_data(from_file_only=True)
    user_data.user_code = "user_code00000000000000000000000"
    user_data.installation_id = None
    assert not user_data.set_user_data(from_file_only=True)
    user_data.installation_id = "installation11111111111111111111"

    # return None if whoami() failed
    user_data.is_employee = None
    m_whoami.return_value = None
    assert not user_data.set_user_data()

    # Update user_data, not employee
    m_whoami.side_effect = (dict(email="someemail", is_employee=False),)
    hexdigest = mock.Mock()
    hexdigest.hexdigest.return_value = "u" * 32
    m_hashlib.md5.return_value = hexdigest
    m_time.time.return_value = 123
    assert user_data.set_user_data()
    assert (
        dict(
            user_code="u" * 32,
            is_employee=False,
            installation_id="installation11111111111111111111",
            last_check=123,
        )
        == user_data.to_dict()
    )

    # Create user_data file, employee
    m_file.exists.return_value = False
    m_whoami.side_effect = (dict(email="someemail", is_employee=True),)
    hexdigest = mock.Mock()
    m_save.return_value = True
    uuid4 = mock.Mock()
    uuid4.hex = "i" * 32
    hexdigest.hexdigest.return_value = "#" * 32
    m_hashlib.md5.return_value = hexdigest
    m_uuid.uuid4.return_value = uuid4
    user_data.installation_id = None
    assert user_data.set_user_data()
    assert (
        dict(
            user_code="#" * 32,
            is_employee=True,
            installation_id="i" * 32,
            last_check=123,
        )
        == user_data.to_dict()
    )

    # No file, read_from_file_only
    assert not user_data.set_user_data(from_file_only=True)


@mock.patch("mozphab.user.bmo")
@mock.patch("mozphab.user.conduit")
def test_whoami(m_conduit, m_bmo, user_data):
    # return None if conduit.whoami() raises
    m_conduit.whoami.side_effect = ConduitAPIError
    assert user_data.whoami() is None

    # An employee based on @mozilla.com email
    m_conduit.whoami.side_effect = None
    m_conduit.whoami.return_value = dict(primaryEmail="someemail@mozilla.com")
    assert user_data.whoami() == dict(email="someemail@mozilla.com", is_employee=True)

    # Not employee as BMO.whoami failed
    m_conduit.whoami.return_value = dict(primaryEmail="some@email.com")
    m_bmo.whoami.return_value = None
    assert user_data.whoami() == dict(email="some@email.com", is_employee=False)

    # Not employee as not in mozilla-employee-confidential group
    m_conduit.whoami.return_value = dict(primaryEmail="some@email.com")
    m_bmo.whoami.return_value = dict(name="nvm@email.com", groups=["some-group"])
    assert user_data.whoami() == dict(email="some@email.com", is_employee=False)

    # An employee as in mozilla-employee-confidential group
    m_bmo.whoami.return_value = dict(
        name="someemail", groups=["mozilla-employee-confidential"]
    )
    assert user_data.whoami() == dict(email="some@email.com", is_employee=True)
