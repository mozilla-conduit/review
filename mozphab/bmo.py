# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import json
import urllib.parse

from http.client import HTTPException, HTTPConnection, HTTPSConnection

from mozphab import environment
from .conduit import conduit
from .exceptions import CommandError, Error
from .logger import logger

DEFAULT_BMO_HOST = "https://bugzilla.mozilla.org"


class BMOAPIError(Error):
    """Raised when the Phabricator Conduit API returns an error response."""


class BMOAPI:
    def get(self, method, **kwargs):
        return self.call(method, "GET", **kwargs)

    def call(self, method, conn_method, headers=None):
        headers = headers or {}
        bmo_url = conduit.repo.bmo_url or DEFAULT_BMO_HOST
        url = urllib.parse.urlparse(
            urllib.parse.urljoin(bmo_url, "rest/{}".format(method))
        )
        logger.debug("BMO API call: %s", url.geturl())
        if url.scheme == "https":
            conn = HTTPSConnection(url.netloc, timeout=5)
        elif environment.HTTP_ALLOWED:
            # Allow for an HTTP connection in suite.
            conn = HTTPConnection(url.netloc, timeout=5)
        else:
            raise CommandError("Only https connections are allowed.")

        sanitized_headers = headers.copy()
        if "X-PHABRICATOR-TOKEN" in headers:
            sanitized_headers["X-PHABRICATOR-TOKEN"] = "cli-XXXXXXXXXXXXXXXXXXXXXXXXXX"

        logger.debug("%s %s %s", conn_method, url.geturl(), sanitized_headers)
        conn.request(conn_method, url.geturl(), headers=headers)
        try:
            response = conn.getresponse()
        except HTTPException as err:
            logger.debug("BMO API HTTPException - %", err.strerror)
            raise BMOAPIError(str(err))
        except OSError as err:
            logger.debug("BMO API OSError - %", err.strerror)
            raise BMOAPIError(str(err))

        data = response.read().decode("utf-8")
        try:
            response = json.loads(data)
        except json.JSONDecodeError:
            logger.debug("BMO API responded with a non JSON string.")
            raise BMOAPIError("Wrong response")

        if "error" in response and response["error"]:
            msg = response.get("message", "unknown error")
            code = response.get("code", "unknown code")
            logger.debug("BMO API Error %s - %s", code, msg)
            raise BMOAPIError(msg)

        return response

    def whoami(self):
        return self.get(
            "whoami", headers={"X-PHABRICATOR-TOKEN": conduit.load_api_token()}
        )


bmo = BMOAPI()
