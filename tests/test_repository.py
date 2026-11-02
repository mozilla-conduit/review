# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json

import pytest

from mozphab import mozphab
from mozphab.config import Config
from mozphab.repository import (
    Repository,
    RepositoryOptions,
    get_lando_url_for_phabricator,
    is_mozilla_phabricator,
)


def test_phabricator_target_from_environment(monkeypatch, tmp_path):
    (tmp_path / ".arcconfig").write_text(
        json.dumps(
            {
                "phabricator.uri": "https://phabricator.services.mozilla.com/",
                "repository.callsign": "FIREFOXAUTOLAND",
            }
        )
    )
    monkeypatch.setenv(
        "MOZPHAB_PHABRICATOR_URL", "https://phabricator-dev.allizom.org/"
    )
    monkeypatch.setenv("MOZPHAB_REPOSITORY_CALLSIGN", "FFTESTAUTOLANDDEV")
    monkeypatch.setenv("MOZPHAB_BUGZILLA_URL", "https://bugzilla-dev.allizom.org/")

    repo = Repository(str(tmp_path), str(tmp_path / ".git"))

    assert repo.phab_url == "https://phabricator-dev.allizom.org"
    assert repo.api_url == "https://phabricator-dev.allizom.org/api/"
    assert repo.call_sign == "FFTESTAUTOLANDDEV"
    assert repo.bmo_url == "https://bugzilla-dev.allizom.org/"


def test_phabricator_target_from_arcconfig_without_environment(monkeypatch, tmp_path):
    (tmp_path / ".arcconfig").write_text(
        json.dumps(
            {
                "phabricator.uri": "https://phabricator.services.mozilla.com/",
                "repository.callsign": "FIREFOXAUTOLAND",
            }
        )
    )
    monkeypatch.delenv("MOZPHAB_PHABRICATOR_URL", raising=False)
    monkeypatch.delenv("MOZPHAB_REPOSITORY_CALLSIGN", raising=False)
    monkeypatch.delenv("MOZPHAB_BUGZILLA_URL", raising=False)

    repo = Repository(str(tmp_path), str(tmp_path / ".git"))

    assert repo.phab_url == "https://phabricator.services.mozilla.com"
    assert repo.call_sign == "FIREFOXAUTOLAND"
    assert repo.bmo_url == "https://bugzilla.mozilla.org"


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


def test_repository_options_from_args():
    config = Config(should_access_file=False)
    args = mozphab.parse_args(["submit", "--path", "repo", "-u", "upstream"])

    assert RepositoryOptions.from_args(args, config) == RepositoryOptions(
        path="repo", upstream=("upstream",)
    ), "`--path` and `--upstream` should be kept."

    config.safe_mode = True
    assert RepositoryOptions.from_args(
        args, config
    ).safe_mode, "`safe_mode` in the config should enable safe mode."

    config.safe_mode = False
    args.safe_mode = True
    assert RepositoryOptions.from_args(
        args, config
    ).safe_mode, "`--safe-mode` should enable safe mode."
