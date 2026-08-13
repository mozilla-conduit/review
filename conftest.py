# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config):
    """Disable `pytest-xdist` while benchmarking.

    `pytest-codspeed` measures nothing when tests run in `xdist` workers, and
    reports "0 benchmarked" rather than failing, so the default `-n auto` from
    `pytest.ini` would silently produce an empty benchmark run.
    """
    codspeed_enabled = (
        config.getoption("--codspeed") or os.environ.get("CODSPEED_ENV") is not None
    )
    if not codspeed_enabled:
        return

    config.option.numprocesses = 0
    config.option.dist = "no"
    config.option.tx = []
