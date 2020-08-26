# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import calendar
import contextlib
import logging
import logging.handlers
import os
import re
import sys
import time

from glob import glob

from mozphab import environment

logger = logging.getLogger("moz-phab")


LOG_MAX_SIZE = 1024 * 1024 * 50
LOG_BACKUPS = 5


_handlers = list()


class ColourFormatter(logging.Formatter):
    def __init__(self):
        if environment.DEBUG:
            fmt = "%(levelname)-8s %(asctime)-13s %(message)s"
        else:
            fmt = "%(message)s"
        super().__init__(fmt)
        self.log_colours = {"WARNING": 34, "ERROR": 31}  # blue, red

    def format(self, record):
        result = super().format(record)
        if environment.HAS_ANSI and record.levelname in self.log_colours:
            result = "\033[%sm%s\033[0m" % (self.log_colours[record.levelname], result)
        return result


def init_logging():
    """Initialize logging."""
    log_file = os.path.join(environment.MOZBUILD_PATH, "moz-phab.log")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColourFormatter())
    handler.setLevel(logging.DEBUG if environment.DEBUG else logging.INFO)
    logger.addHandler(handler)
    _handlers.append(handler)

    handler = logging.handlers.RotatingFileHandler(
        filename=log_file,
        maxBytes=LOG_MAX_SIZE,
        backupCount=LOG_BACKUPS,
        encoding="utf8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)-13s %(levelname)-8s %(message)s")
    )
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    _handlers.append(handler)

    logger.setLevel(logging.DEBUG)

    # clean up old date-based logs
    now = time.time()
    for filename in sorted(glob("%s/*.log.*" % os.path.dirname(log_file))):
        m = re.search(r"\.(\d\d\d\d)-(\d\d)-(\d\d)$", filename)
        if not m:
            continue
        file_time = calendar.timegm(
            (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0, 0, 0)
        )
        if (now - file_time) / (60 * 60 * 24) > 8:
            logger.debug("deleting old log file: %s" % os.path.basename(filename))
            with contextlib.suppress(IOError):
                os.unlink(filename)


def stop_logging():
    """Remove our logging handlers and close files."""

    for handler in _handlers:
        logger.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()
