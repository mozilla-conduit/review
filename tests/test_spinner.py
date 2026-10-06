# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import time

import pytest

from mozphab import environment
from mozphab.spinner import WAIT_INTERVAL, Spinner, wait_message


def test_signal_stop_before_start_does_not_hang():
    spinner = Spinner("test")
    spinner.signal_stop()
    spinner.start()
    spinner.join(timeout=1)
    assert not spinner.is_alive()


def test_wait_message_returns_without_waiting_for_spin_interval(monkeypatch):
    monkeypatch.setattr(environment, "SHOW_SPINNER", True)
    start = time.monotonic()
    with wait_message("test"):
        # Give the spinner time to enter its loop, otherwise it can exit
        # before ever waiting on the spin interval.
        time.sleep(WAIT_INTERVAL / 4)
    assert time.monotonic() - start < WAIT_INTERVAL


@pytest.mark.parametrize(
    "has_ansi,expected_end", [(True, "\r\033[K"), (False, "\x08 \n")]
)
def test_wait_message_cleans_up_line(monkeypatch, capsys, has_ansi, expected_end):
    monkeypatch.setattr(environment, "SHOW_SPINNER", True)
    monkeypatch.setattr(environment, "HAS_ANSI", has_ansi)
    with wait_message("test"):
        pass
    assert capsys.readouterr().out.endswith(expected_end)
