# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from mozphab.repository import get_lando_url_for_phabricator, is_mozilla_phabricator


def test_is_mozilla_phabricator():
    assert is_mozilla_phabricator("https://phabricator.services.mozilla.com/")
    assert is_mozilla_phabricator("https://phabricator-dev.allizom.org/")
    assert not is_mozilla_phabricator("https://reviews.llvm.org/")


def test_get_lando_url_for_phabricator():
    assert (
        get_lando_url_for_phabricator("https://phabricator.services.mozilla.com/")
        == "https://lando.moz.tools"
    ), "Prod Phabricator with trailing slash should return prod Lando URL."
    assert (
        get_lando_url_for_phabricator("https://phabricator.services.mozilla.com")
        == "https://lando.moz.tools"
    ), "Prod Phabricator without trailing slash should return prod Lando URL."
    assert (
        get_lando_url_for_phabricator("https://phabricator-dev.allizom.org/")
        == "https://dev.lando.nonprod.webservices.mozgcp.net"
    ), "Dev Phabricator should return dev Lando URL."

    with pytest.raises(KeyError):
        get_lando_url_for_phabricator("https://reviews.llvm.org/")
