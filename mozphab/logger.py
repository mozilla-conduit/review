# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import calendar
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


class ColourFormatter(logging.Formatter):
    def __init__(self, debug, has_ansi):
        if debug:
            fmt = "%(levelname)-8s %(asctime)-13s %(message)s"
        else:
            fmt = "%(message)s"
        super().__init__(fmt)
        self.log_colours = {"WARNING": 34, "ERROR": 31}  # blue, red
        self.has_ansi = has_ansi

    def format(self, record):
        result = super().format(record)
        if self.has_ansi and record.levelname in self.log_colours:
            result = "\033[%sm%s\033[0m" % (self.log_colours[record.levelname], result)
        return result


def init_logging():
    """Initialize logging."""
    log_file = os.path.join(environment.MOZBUILD_PATH, "moz-phab.log")
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(
        ColourFormatter(environment.DEBUG, environment.HAS_ANSI)
    )
    stdout_handler.setLevel(logging.DEBUG if environment.DEBUG else logging.INFO)
    logger.addHandler(stdout_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_file,
        maxBytes=LOG_MAX_SIZE,
        backupCount=LOG_BACKUPS,
        encoding="utf8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)-13s %(levelname)-8s %(message)s")
    )
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

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
            os.unlink(filename)
