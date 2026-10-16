# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import logging
import threading
import urllib.request
from unittest import mock
from urllib.parse import unquote

import pytest

from mozphab import environment, mozphab
from mozphab.commands import profile
from mozphab.exceptions import Error
from mozphab.profiler import profiler


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(environment, "MOZBUILD_PATH", str(tmp_path))
    monkeypatch.delenv("PROFILER_ORIGIN", raising=False)
    monkeypatch.setattr(profile.config, "profiler_origin", "https://profiler.test")
    directory = tmp_path / "profiles"
    directory.mkdir()
    profiler.start()
    return directory


def profile_args(path=None, list_profiles=False):
    return argparse.Namespace(path=path, list=list_profiles)


def fetch_profile(profiler_url, responses):
    """Fetch the profile from the URL given to the Firefox Profiler."""
    prefix = "https://profiler.test/from-url/"
    assert profiler_url.startswith(prefix)
    assert profiler_url.endswith("/marker-chart/")
    profile_url = unquote(profiler_url[len(prefix) : -len("/marker-chart/")])

    def fetch():
        with urllib.request.urlopen(profile_url) as response:
            responses.append((response.headers, response.read()))

    thread = threading.Thread(target=fetch)
    thread.start()
    return thread


def test_open_latest_profile(profiles_dir, monkeypatch):
    (profiles_dir / "profile-1.json").write_text('{"old": true}')
    (profiles_dir / "profile-2.json").write_text('{"new": true}')
    monkeypatch.setattr(profile.config, "open_browser", True)
    responses = []
    threads = []

    def open_new_tab(url):
        threads.append(fetch_profile(url, responses))
        return True

    with mock.patch("webbrowser.open_new_tab", side_effect=open_new_tab) as m_open:
        profile.open_profile(profile_args())

    m_open.assert_called_once()
    threads[0].join()
    ((headers, data),) = responses
    assert data == b'{"new": true}'
    assert headers["Access-Control-Allow-Origin"] == "https://profiler.test"
    # Opening a profile doesn't save a new one.
    assert not profiler.enabled


def test_open_profile_without_browser(profiles_dir, monkeypatch, caplog):
    path = profiles_dir / "profile-1.json"
    path.write_text("{}")
    monkeypatch.setattr(profile.config, "open_browser", False)
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        profile.ProfileServer,
        "handle_request",
        lambda server: setattr(server, "served", True),
    )

    with mock.patch("webbrowser.open_new_tab") as m_open:
        profile.open_profile(profile_args(str(path)))

    m_open.assert_not_called()
    assert caplog.messages[-1].endswith("in your browser to view the profile.")


def test_open_missing_profile(profiles_dir):
    with pytest.raises(Error, match="Profile not found"):
        profile.open_profile(profile_args(str(profiles_dir / "missing.json")))


def test_open_profile_without_profiles(profiles_dir):
    with pytest.raises(Error, match="No profile found"):
        profile.open_profile(profile_args())


def test_list_profiles(profiles_dir, caplog):
    caplog.set_level(logging.INFO)
    # Through `main`, to check the arguments it gives to the command.
    mozphab.main(["profile", "--list"], is_development=True)
    assert f"No profile found in {profiles_dir}" in caplog.messages

    for name in ("profile-1.json", "profile-2.json"):
        (profiles_dir / name).write_text("{}")
    caplog.clear()
    profile.open_profile(profile_args(list_profiles=True))

    assert [message.split()[-1] for message in caplog.messages] == [
        str(profiles_dir / "profile-2.json"),
        str(profiles_dir / "profile-1.json"),
    ]


@pytest.mark.parametrize("argv", [["help"], ["submit", "--help"], ["--no-such-arg"]])
def test_argument_parsing_exit_does_not_save_a_profile(profiles_dir, argv):
    with pytest.raises(SystemExit):
        mozphab.main(argv, is_development=True)

    assert profiler.save(20) is None
    assert list(profiles_dir.iterdir()) == []
