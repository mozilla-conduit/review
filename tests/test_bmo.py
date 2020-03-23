# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import mock
import pytest

from mozphab.bmo import BMOAPI, BMOAPIError
from mozphab.exceptions import CommandError


@mock.patch("mozphab.bmo.BMOAPI.call")
def test_get(m_call):
    bmo = BMOAPI()
    bmo.get("arg", kwarg="value")
    m_call.assert_called_once_with("arg", "GET", kwarg="value")


@mock.patch("mozphab.bmo.BMOAPI.get")
@mock.patch("mozphab.bmo.conduit")
def test_whoami(m_conduit, m_get):
    bmo = BMOAPI()
    bmo.whoami()
    m_get.assert_called_once_with("whoami", headers={"X-PHABRICATOR-TOKEN": mock.ANY})
    m_conduit.load_api_token.assert_called_once()


@mock.patch("mozphab.bmo.conduit")
@mock.patch("mozphab.bmo.HTTPConnection")
@mock.patch("mozphab.bmo.HTTPSConnection")
@mock.patch("mozphab.bmo.urllib.parse")
@mock.patch("mozphab.bmo.environment")
@mock.patch("mozphab.bmo.json")
def test_call(m_json, m_env, m_parse, m_https, m_http, m_conduit):
    bmo = BMOAPI()
    m_env.HTTP_ALLOWED = True

    m_conduit.repo.bmo_url = "http://bmo"
    url = mock.Mock()
    url.geturl.return_value = "http://bmo"
    url.scheme = "http"
    m_parse.urljoin.return_value = "http://bmo/rest/someapi"
    m_parse.urlparse.return_value = url
    m_json.loads.return_value = dict(success=True)

    url.get_url.reset_mock()
    assert dict(success=True) == bmo.call("someapi", "GET")
    m_parse.urljoin.assert_called_once_with("http://bmo", "rest/someapi")
    m_parse.urlparse.assert_called_once_with("http://bmo/rest/someapi")
    assert url.geturl.call_count == 3

    m_json.loads.return_value = dict(error=True)
    with pytest.raises(BMOAPIError):
        bmo.call("someapi", "GET")

    m_env.HTTP_ALLOWED = False
    with pytest.raises(CommandError):
        bmo.call("someapi", "GET")
