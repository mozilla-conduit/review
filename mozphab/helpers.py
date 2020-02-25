# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import tempfile

from contextlib import contextmanager
from shutil import which

from .logger import logger


def which_path(path):
    """Check if an executable is provided. Fall back to which if not.

    Args:
        path: (str) filename or path to check for an executable command

    Returns:
        The full path of a command or None.
    """
    if (
        os.path.exists(path)
        and os.access(path, os.F_OK | os.X_OK)
        and not os.path.isdir(path)
    ):
        logger.debug("Path found: %s", path)
        return path

    return which(path)


def parse_config(config_list, filter_func=None):
    """Parses list with "name=value" strings.

    Args:
        config_list: A list of "name=value" strings
        filter_func: A function taking the parsing config name and value for each line.
            If the function returns True the config value will be included in the final
            dict.

    Returns: A dict containing parsed data.
    """
    result = dict()
    for line in config_list:
        try:
            name, value = line.split("=", 1)
        except ValueError:
            continue

        name = name.strip()
        value = value.strip()
        if filter_func is None or (callable(filter_func) and filter_func(name, value)):
            result[name] = value

    return result


def read_json_field(files, field_path):
    """Parses json files in turn returning value as per field_path, or None."""
    for filename in files:
        try:
            with open(filename, encoding="utf-8") as f:
                rc = json.load(f)
                for field_name in field_path:
                    if field_name not in rc:
                        rc = None
                        break
                    rc = rc[field_name]
                if not rc:
                    continue
                return rc
        except FileNotFoundError:
            continue
        except ValueError:
            continue
    return None


@contextmanager
def temporary_file(content, encoding="utf-8"):
    f = tempfile.NamedTemporaryFile(delete=False, mode="w+", encoding=encoding)
    try:
        f.write(content)
        f.flush()
        f.close()
        yield f.name
    finally:
        os.remove(f.name)


@contextmanager
def temporary_binary_file(content):
    f = tempfile.NamedTemporaryFile(delete=False, mode="wb+")
    try:
        f.write(content)
        f.flush()
        f.close()
        yield f.name
    finally:
        os.remove(f.name)
