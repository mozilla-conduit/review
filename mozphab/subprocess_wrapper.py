# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
from shlex import quote
from typing import (
    Any,
    List,
    Optional,
)

from .exceptions import CommandError
from .logger import logger


def debug_log_command(command: List[str]):
    logger.debug("$ %s", " ".join(quote(s.replace("\n", r"\n")) for s in command))


def check_call(command: List[str], **kwargs):
    # wrapper around subprocess.check_call with debug output
    debug_log_command(command)
    kwargs["encoding"] = "UTF-8"
    try:
        subprocess.check_call(command, **kwargs)
    except subprocess.CalledProcessError as e:
        raise CommandError(
            "command '%s' failed to complete successfully" % command[0], e.returncode
        )


def check_call_by_line(
    command: List[str], cwd: Optional[str] = None, never_log: bool = False
):
    # similar to check_call, yields for line-by-line processing
    debug_log_command(command)

    # Connecting the STDIN to the PIPE will make arc throw an exception on reading
    # user input
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        cwd=cwd,
        universal_newlines=True,
    )
    try:
        for line in iter(process.stdout.readline, ""):
            line = line.rstrip()
            if not never_log:
                logger.debug("> %s", line)
            yield line
    finally:
        process.stdout.close()
        process.wait()

    if process.returncode:
        raise CommandError(
            "command '%s' failed to complete successfully" % command[0],
            process.returncode,
        )


def command_output(
    command: List[str],
    cwd: Optional[str] = None,
    strip: bool = True,
    never_log: bool = False,
    stdin=None,
    stderr=None,
    env: Optional[dict] = None,
    search_error=None,
    expect_binary: bool = False,
) -> Any:
    """Wrapper around subprocess.check_output with debug output.

    Returns the raw bytes when `expect_binary` is set, and the decoded output
    otherwise. Call one of the `check_output*` functions below, which name the
    type they return.
    """
    debug_log_command(command)
    kwargs = {"cwd": cwd, "stdin": stdin, "stderr": stderr}
    if not expect_binary:
        kwargs["universal_newlines"] = True
        kwargs["encoding"] = "UTF-8"

    if env:
        kwargs["env"] = env

    try:
        output = subprocess.check_output(command, **kwargs)
    except subprocess.CalledProcessError as e:
        if search_error:
            for err in search_error:
                if err["matching"] in e.output:
                    logger.error(err["message"])

        if e.output and not never_log:
            logger.debug(e.output)

        if e.stderr and not never_log:
            logger.debug(e.stderr)

        raise CommandError(
            "command '%s' failed to complete successfully" % command[0],
            e.returncode,
            stderr=e.stderr or "",
        )

    if expect_binary:
        logger.debug("%s bytes of data received", len(output))
        return output

    if strip:
        output = output.rstrip()
    if output and not never_log:
        logger.debug(output)
    return output


def check_output_binary(command: List[str], **kwargs) -> bytes:
    """Run `command` and return its raw output. See `command_output`."""
    return command_output(command, expect_binary=True, **kwargs)


def check_output_text(command: List[str], **kwargs) -> str:
    """Run `command` and return its output as a single string. See `command_output`."""
    return command_output(command, **kwargs)


def check_output(command: List[str], keep_ends: bool = False, **kwargs) -> List[str]:
    """Run `command` and return its output split into lines. See `command_output`."""
    return check_output_text(command, **kwargs).splitlines(keep_ends)
