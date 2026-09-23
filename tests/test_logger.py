# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import pytest

from mozphab import environment
from mozphab import logger as logger_module


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    """Run init_logging against a temporary log dir with isolated handler state."""
    monkeypatch.setattr(environment, "MOZBUILD_PATH", str(tmp_path))
    monkeypatch.setattr(environment, "DEBUG", False)
    # Give init_logging a fresh handler list so we don't touch global state; the
    # autouse `restore_logging` fixture restores `logger.handlers` afterwards.
    monkeypatch.setattr(logger_module, "_handlers", [])
    try:
        logger_module.init_logging()
        yield
    finally:
        logger_module.stop_logging()


def _find_handler(predicate):
    return next(h for h in logger_module._handlers if predicate(h))


def test_disable_stdout_logging_keeps_file_handler(isolated_logging):
    """disable_stdout_logging silences stdout but leaves the log file intact."""
    stdout_handler = _find_handler(
        lambda h: h.name == logger_module._STDOUT_HANDLER_NAME
    )
    file_handler = _find_handler(lambda h: isinstance(h, logging.FileHandler))

    # Baseline: stdout at INFO, file at DEBUG.
    assert stdout_handler.level == logging.INFO
    assert file_handler.level == logging.DEBUG

    logger_module.disable_stdout_logging()

    # Only the stdout handler is raised to ERROR; the file handler is untouched.
    assert stdout_handler.level == logging.ERROR
    assert file_handler.level == logging.DEBUG


def test_disable_stdout_logging_leaves_logger_level(isolated_logging):
    """The logger's own level is unchanged, so records still reach the file."""
    original_level = logger_module.logger.level

    logger_module.disable_stdout_logging()

    assert logger_module.logger.level == original_level
