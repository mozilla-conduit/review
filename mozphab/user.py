# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import hashlib
import json
import time
import uuid

from mozphab import environment
from pathlib import Path

from .bmo import bmo
from .conduit import conduit, ConduitAPIError
from .logger import logger


USER_INFO_FILE = Path(environment.MOZBUILD_PATH) / "user_info.json"
EMPLOYEE_CHECK_FREQUENCY = 24 * 7 * 60 * 60  # week


class UserData:
    is_employee = None
    user_code = None
    installation_id = None
    last_check = None
    keys = ["is_employee", "user_code", "installation_id", "last_check"]

    def __init__(self):
        self.set_from_file()

    @property
    def is_data_collected(self):
        """True if all user info data are collected."""
        # All values are set (not None)
        return None not in [getattr(self, k) for k in self.keys]

    def to_dict(self):
        return {k: getattr(self, k) for k in self.keys}

    def update_from_dict(self, dictionary):
        """Assign attributes from a dict."""
        for key in self.keys:
            if key in dictionary:
                setattr(self, key, dictionary[key])

    def set_from_file(self):
        """Read user info from file."""
        if USER_INFO_FILE.exists():
            with USER_INFO_FILE.open("r", encoding="utf-8") as f:
                user_info = json.load(f)
                self.update_from_dict(user_info)

    def save_user_info(self, **kwargs):
        """Save any fields provided as kwargs into the user_info file."""
        self.update_from_dict(kwargs)
        user_info = self.to_dict()
        with USER_INFO_FILE.open("w", encoding="utf-8") as f:
            json.dump(user_info, f, sort_keys=True, indent=2)

    def whoami(self):
        """Returns a dict with email and employee status."""
        # Check user in Phabricator.
        try:
            who = conduit.whoami()
        except ConduitAPIError as e:
            logger.error(str(e))
            return None

        response = dict(email=who["primaryEmail"])
        if response["email"].lower().endswith("@mozilla.com"):
            response["is_employee"] = True
            return response

        bmo_who = bmo.whoami()
        response["is_employee"] = (
            bmo_who is not None and "mozilla-employee-confidential" in bmo_who["groups"]
        )
        return response

    def set_user_data(self, from_file_only=False):
        """Sets user data if needed.

        Returns a bool value indicating if status is updated.
        """
        if USER_INFO_FILE.exists():
            update = (
                self.last_check is None
                or time.time() - self.last_check > EMPLOYEE_CHECK_FREQUENCY
            )
            if not update and self.is_data_collected:
                return False

        if from_file_only:
            return False

        whoami = self.whoami()
        if whoami is None:
            # `user.whoami` failed.
            return False

        is_employee = self.is_employee

        if self.installation_id is None:
            self.installation_id = uuid.uuid4().hex

        self.last_check = int(time.time())
        self.user_code = hashlib.md5(whoami["email"].encode("utf-8")).hexdigest()
        self.is_employee = whoami["is_employee"]
        self.save_user_info(
            is_employee=self.is_employee,
            user_code=self.user_code,
            installation_id=self.installation_id,
            last_check=self.last_check,
        )
        return is_employee != self.is_employee


user_data = UserData()
