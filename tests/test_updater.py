# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from mozphab import updater


def test_should_self_update():
    assert (
        updater.should_self_update(-1, 0) is False
    ), "Setting last check to a negative value should disable self-update."

    assert (
        updater.should_self_update(1, 250000) is False
    ), "Last check within the 3 day frequency should not self-update."

    assert (
        updater.should_self_update(1, 3000000) is True
    ), "Last check greater than the 3 day frequency should cause a self-update."


def test_parse_latest_prerelease_version():
    # Test data from the `simple` api.
    data = {
        "files": [
            {
                "filename": "MozPhab-1.2.2rc0.tar.gz",
            },
            {
                "filename": "MozPhab-1.2.2rc1-py3-none-any.whl",
            },
            {
                "filename": "MozPhab-1.2.2rc1.tar.gz",
            },
            {
                "filename": "MozPhab-1.2.0.tar.gz",
            },
        ],
    }

    assert (
        updater.parse_latest_prerelease_version(data) == "1.2.2rc1"
    ), "`get_newest_pypi_version` should detect `1.2.2rc1` as the latest version."


def test_parse_latest_version_filename_case():
    # Test data from the `simple` api.
    data = {
        "files": [
            {
                "filename": "MozPhab-1.2.0.tar.gz",
            },
            {
                "filename": "mozphab-1.2.1.tar.gz",
            },
        ],
    }

    assert (
        updater.parse_latest_prerelease_version(data) == "1.2.1"
    ), "`get_newest_pypi_version` should detect `1.2.1` as the latest version."
