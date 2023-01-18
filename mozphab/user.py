# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import hashlib
import json
import time
import uuid

from typing import Optional

from mozphab import environment
from pathlib import Path

from .bmo import bmo
from .conduit import conduit, ConduitAPIError
from .logger import logger


USER_INFO_FILE = Path(environment.MOZBUILD_PATH) / "user_info.json"
EMPLOYEE_CHECK_FREQUENCY = 24 * 7 * 60 * 60  # week
MOZILLA_EMPLOYEE_EMAIL_ENDINGS = {
    "@getpocket.com",
    "@mozilla.com",
    "@mozillafoundation.org",
}


def is_bad_uuid(key: str, value: Optional[str]) -> bool:
    """Return `True` if the key/value pair corresponds to a faulty UUID."""
    return (
        key in {"installation_id", "user_code"}
        and value is not None
        and len(value) == 32
    )


def format_uuid(bad_uuid: str) -> str:
    """Turn a non-hyphenated UUID `str` into a hyphenated one.

    This function assumes the `bad_uuid` is a 32-character `str`,
    usually already having been passed through `is_bad_uuid`.

    See https://bugzilla.mozilla.org/show_bug.cgi?id=1788719 for more.
    """
    # Pass the 32-char string into a `UUID` object, then pass through
    # `str()` to get the hyphenated 36-character version.
    return str(uuid.UUID(bad_uuid))


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
        """True if all user info data is collected."""
        return all(getattr(self, k) is not None for k in self.keys)

    def to_dict(self):
        return {k: getattr(self, k) for k in self.keys}

    def update_from_dict(self, dictionary):
        """Assign attributes from a dict."""
        for key in self.keys:
            if key in dictionary:

                # See bug 1788719.
                if is_bad_uuid(key, dictionary[key]):
                    dictionary[key] = format_uuid(dictionary[key])

                setattr(self, key, dictionary[key])

    def set_from_file(self):
        """Read user info from file."""
        if not USER_INFO_FILE.exists():
            return

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

        if not response["email"]:
            # If `primaryEmail` is empty we log a warning and return.
            logger.warning(
                "You have not set a primary email address in Phabricator.\n"
                "Please set a primary email address in your Phabricator settings."
            )
            response["is_employee"] = False
            return response

        lower_email = response["email"].lower()
        if any(
            lower_email.endswith(domain) for domain in MOZILLA_EMPLOYEE_EMAIL_ENDINGS
        ):
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
        if whoami is None or not whoami["email"]:
            # `user.whoami` failed.
            return False

        is_employee = self.is_employee

        if self.installation_id is None:
            self.installation_id = str(uuid.uuid4())

        user_code = hashlib.md5(whoami["email"].encode("utf-8")).hexdigest()
        self.user_code = format_uuid(user_code)

        self.last_check = int(time.time())
        self.is_employee = whoami["is_employee"]
        self.save_user_info(
            is_employee=self.is_employee,
            user_code=self.user_code,
            installation_id=self.installation_id,
            last_check=self.last_check,
        )
        return is_employee != self.is_employee


user_data = UserData()
