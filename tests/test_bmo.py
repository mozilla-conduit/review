# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import pytest

from unittest import mock

from mozphab.bmo import bmo, BMOAPIError
from mozphab.exceptions import Error


@mock.patch("mozphab.bmo.BMOAPI._req_with_retries")
@mock.patch("mozphab.bmo.conduit")
def test_whoami(m_conduit, m_req_with_retries):
    bmo.whoami()
    m_req_with_retries.assert_called_once_with(
        "whoami", headers={"X-PHABRICATOR-TOKEN": mock.ANY}
    )
    m_conduit.load_api_token.assert_called_once()


@mock.patch("mozphab.bmo.conduit")
def test_build_request(m_conduit):
    m_conduit.repo.bmo_url = "https://bmo.test"

    assert bmo._build_request(method="test_method") == {
        "url": "https://bmo.test/rest/test_method",
        "method": "GET",
        "headers": {"User-Agent": mock.ANY},
    }

    assert bmo._build_request(method="test_method", headers={"X-Test": "true"}) == {
        "url": "https://bmo.test/rest/test_method",
        "method": "GET",
        "headers": {"User-Agent": mock.ANY, "X-Test": "true"},
    }


def test_sanitised_req():
    assert bmo._sanitise_req(
        {
            "url": "https://bmo.test/rest/test_method",
            "method": "GET",
            "headers": {"X-PHABRICATOR-TOKEN": "cli-secret"},
        }
    ) == {
        "url": "https://bmo.test/rest/test_method",
        "method": "GET",
        "headers": {"X-PHABRICATOR-TOKEN": "cli-XXXX"},
    }


@mock.patch("urllib.request.urlopen")
@mock.patch("mozphab.bmo.conduit")
def test_get(m_conduit, m_urlopen):
    m_conduit.repo.bmo_url = "https://bmo.test"

    # build fake context-manager to mock urlopen
    cm = mock.MagicMock()
    cm.getcode.return_value = 200
    cm.__enter__.return_value = cm
    m_urlopen.return_value = cm

    # success
    cm.read.return_value = json.dumps({"result": "result"})
    assert bmo.get("method") == {"result": "result"}

    # error
    cm.read.return_value = json.dumps({"error": "aieee"})
    with pytest.raises(BMOAPIError) as bmo_error:
        bmo.get("method")
    assert bmo_error.value.args[0].startswith("Bugzilla Error: ")


@mock.patch("mozphab.bmo.BMOAPI.get")
def test_req_with_retries(m_get):
    # raises Error after 3 retries
    m_get.side_effect = (BMOAPIError, BMOAPIError, BMOAPIError)
    with pytest.raises(Error):
        bmo._req_with_retries("test")

    # raises error with custom retry amount
    m_get.side_effect = (BMOAPIError, BMOAPIError)
    with pytest.raises(Error):
        bmo._req_with_retries("test", retries=2)

    # returns result if successful
    m_get.side_effect = None
    m_get.return_value = {"message": "test"}
    assert bmo._req_with_retries("test")["message"] == "test"
