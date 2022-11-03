# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import hashlib
import uuid

import pytest

from contextlib import contextmanager
from unittest import mock

from mozphab import user

from mozphab.conduit import ConduitAPIError
from mozphab.exceptions import Error


@mock.patch("mozphab.user.USER_INFO_FILE")
@mock.patch("mozphab.user.json")
def test_save_user_info(m_json, m_file, user_data):
    @contextmanager
    def with_open(*_, **__):
        yield None

    m_file.open = with_open
    # create file
    m_file.exists.return_value = False
    installation_id = str(uuid.uuid4())
    user_info = dict(
        is_employee=True,
        user_code=str(uuid.uuid4()),
        installation_id=installation_id,
        last_check=1,
    )
    user_data.save_user_info(**user_info)
    m_json.dump.assert_called_once_with(user_info, None, sort_keys=True, indent=2)

    # update file
    m_file.exists.return_value = True
    m_json.reset_mock()
    m_json.load.return_value = user_info
    new_user_code = str(uuid.uuid4())
    user_data.save_user_info(user_code=new_user_code)
    m_json.dump.assert_called_once_with(
        dict(
            is_employee=True,
            user_code=new_user_code,
            installation_id=installation_id,
            last_check=1,
        ),
        None,
        sort_keys=True,
        indent=2,
    )


def test_is_data_collected(user_data):
    user_data.update_from_dict(
        dict(
            is_employee=None,
            user_code="ff455518-16ae-4a89-b600-f08e30c25ad2",
            installation_id="5e3c5706-09c2-41a4-8216-e5e463b032b9",
            last_check=1,
        )
    )
    assert not user_data.is_data_collected
    user_data.update_from_dict(
        dict(
            is_employee=True,
            user_code=None,
            installation_id="5e3c5706-09c2-41a4-8216-e5e463b032b9",
            last_check=1,
        )
    )
    assert not user_data.is_data_collected
    user_data.update_from_dict(
        dict(
            is_employee=False,
            user_code="ff455518-16ae-4a89-b600-f08e30c25ad2",
            installation_id=None,
            last_check=1,
        )
    )
    assert not user_data.is_data_collected
    user_data.update_from_dict(
        dict(
            is_employee=True,
            user_code="ff455518-16ae-4a89-b600-f08e30c25ad2",
            installation_id="5e3c5706-09c2-41a4-8216-e5e463b032b9",
            last_check=None,
        )
    )
    assert not user_data.is_data_collected
    user_data.update_from_dict(
        dict(
            is_employee=False,
            user_code="ff455518-16ae-4a89-b600-f08e30c25ad2",
            installation_id="5e3c5706-09c2-41a4-8216-e5e463b032b9",
            last_check=1,
        )
    )
    assert user_data.is_data_collected


@mock.patch("mozphab.user.hashlib")
@mock.patch("mozphab.user.UserData.whoami")
@mock.patch("mozphab.user.UserData.save_user_info")
@mock.patch("mozphab.user.USER_INFO_FILE")
@mock.patch("mozphab.user.time")
def test_set_user_data(m_time, m_file, m_save, m_whoami, m_hashlib, user_data):
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

    user_data.is_employee = None
    m_whoami.return_value = {"email": None}
    assert (
        not user_data.set_user_data()
    ), "Status should not be updated without a valid email."

    # Update user_data, not employee
    m_whoami.side_effect = (dict(email="someemail", is_employee=False),)
    hexdigest = mock.Mock()
    hashed_email = hashlib.md5("someemail".encode("utf-8")).hexdigest()
    user_code = str(uuid.UUID(hashed_email))
    hexdigest.hexdigest.return_value = hashed_email
    m_hashlib.md5.return_value = hexdigest
    m_time.time.return_value = 123
    assert user_data.set_user_data()
    assert (
        dict(
            user_code=user_code,
            is_employee=False,
            installation_id="installation11111111111111111111",
            last_check=123,
        )
        == user_data.to_dict()
    )

    # Create user_data file, employee
    m_file.exists.return_value = False
    m_whoami.side_effect = (dict(email="someemail", is_employee=True),)
    m_save.return_value = True
    user_data.installation_id = None
    assert user_data.set_user_data()
    user_data_dict = user_data.to_dict()
    assert (
        user_data_dict["is_employee"] is True
    ), "`whoami` showing user is employee should be reflected in user data."

    assert isinstance(
        user_data_dict["user_code"], str
    ), "`user_code` should be a `str`."
    assert (
        len(user_data_dict["user_code"]) == 36
    ), "`user_code` should be a 36-character `str`."
    assert "-" in user_data_dict["user_code"], "`user_code` should include hyphens."

    assert isinstance(
        user_data_dict["installation_id"], str
    ), "`installation_id` should be a `str`."
    assert (
        len(user_data_dict["installation_id"]) == 36
    ), "`installation_id` should be a 36-character `str`."
    assert (
        "-" in user_data_dict["installation_id"]
    ), "`installation_id` should include hyphens."

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

    # An employee based on @getpocket.com email
    m_conduit.whoami.side_effect = None
    m_conduit.whoami.return_value = dict(primaryEmail="someemail@getpocket.com")
    assert user_data.whoami() == dict(email="someemail@getpocket.com", is_employee=True)

    # An employee based on @mozillafoundation.com email
    m_conduit.whoami.side_effect = None
    m_conduit.whoami.return_value = dict(primaryEmail="someemail@mozillafoundation.org")
    assert user_data.whoami() == dict(
        email="someemail@mozillafoundation.org", is_employee=True
    )

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

    m_conduit.whoami.return_value = dict(primaryEmail=None)
    assert user_data.whoami() == dict(
        email=None, is_employee=False
    ), "When `primaryEmail` is empty, `is_employee` is False and doesn't fail."

    # whoami raises an error if BMO.whoami raises Error
    m_conduit.whoami.return_value = dict(primaryEmail="some@email.com")
    m_bmo.whoami.side_effect = Error
    with pytest.raises(Error):
        user_data.whoami()


def test_is_bad_uuid():
    assert not user.is_bad_uuid(
        "randomkey", None
    ), "Irrelevant key should return False."
    assert not user.is_bad_uuid(
        "randomkey", "asdf"
    ), "Irrelevant key should return False."

    assert not user.is_bad_uuid(
        "installation_id", None
    ), "Empty value is not a bad UUID."
    assert not user.is_bad_uuid("user_code", None), "Empty value is not a bad UUID."

    assert not user.is_bad_uuid(
        "installation_id", "blah"
    ), "Bad UUID can only be 32-chars."
    assert not user.is_bad_uuid("user_code", "blah"), "Bad UUID can only be 32-chars."

    assert user.is_bad_uuid(
        "user_code", "a" * 32
    ), "32-char `str` with appropriate key is bad UUID."
    assert user.is_bad_uuid(
        "installation_id", "a" * 32
    ), "32-char `str` with appropriate key is bad UUID."


def test_format_uuid():
    assert (
        user.format_uuid("83a4fbef668c4b168dc7f62427c1b855")
        == "83a4fbef-668c-4b16-8dc7-f62427c1b855"
    ), "Bad UUID should be converted to a valid one."


def test_bad_uuid_rewritten(user_data):
    user_data.update_from_dict(
        {
            "installation_id": "83a4fbef668c4b168dc7f62427c1b855",
            "user_code": "83a4fbef668c4b168dc7f62427c1b855",
        }
    )
    assert (
        user_data.installation_id == "83a4fbef-668c-4b16-8dc7-f62427c1b855"
    ), "`installation_id` should be rewritten to good UUID on update."
    assert (
        user_data.user_code == "83a4fbef-668c-4b16-8dc7-f62427c1b855"
    ), "`user_code` should be rewritten to good UUID on update."


@mock.patch("mozphab.user.USER_INFO_FILE")
@mock.patch("mozphab.user.UserData.whoami")
def test_new_telemetry_ids_are_valid(m_whoami, m_file, user_data):
    m_whoami.side_effect = ({"email": "someemail", "is_employee": False},)
    m_file.exists.return_value = False
    assert (
        user_data.installation_id is None
    ), "Sanity check `installation_id` starts as `None`."

    user_data.set_user_data()

    assert (
        len(user_data.installation_id) == 36
    ), "`installation_id` should have len of 36."
    assert "-" in user_data.installation_id, "`installation_id` should contain hyphens."

    assert len(user_data.user_code) == 36, "`user_code` should have len of 36."
    assert "-" in user_data.user_code, "`user_code` should contain hyphens."
