# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from shlex import quote
from typing import (
    Any,
)

from .exceptions import CommandError
from .logger import logger
from .profiler import Category, MarkerType, profiler

# Global options of git, hg and jj taking a separate value, skipped when looking
# for the subcommand.
OPTIONS_WITH_VALUE = {"-c", "-C", "-R", "--config", "--cwd", "--repository"}


VCS_EXECUTABLES = {"git", "hg", "jj"}


def format_command(command: list[str]) -> str:
    return " ".join(quote(s.replace("\n", r"\n")) for s in command)


def debug_log_command(command: list[str]):
    logger.debug("$ %s", format_command(command))


def find_subcommand_index(command: list[str]) -> int:
    """Return the index of the subcommand, skipping global options and their values."""
    arguments = enumerate(command[1:], start=1)
    for index, argument in arguments:
        if argument in OPTIONS_WITH_VALUE:
            next(arguments, None)
        elif not argument.startswith("-"):
            return index
    # Fall back to the last argument for commands like `git --version`.
    return len(command) - 1


@contextmanager
def profiler_marker(command: list[str]) -> Generator[dict]:
    """Record a profiler marker named after the executable.

    The exit code is taken from `subprocess.CalledProcessError`, or from
    `CommandError` for the Mercurial commands.
    """
    executable = os.path.basename(command[0])
    subcommand_index = find_subcommand_index(command)

    with profiler.marker(
        executable,
        (
            Category.VCS
            if executable.removesuffix(".exe") in VCS_EXECUTABLES
            else Category.OTHER
        ),
        MarkerType.COMMAND,
        subcommand=command[subcommand_index] if len(command) > 1 else "",
        # Without the global options, which are the same for every command.
        # Commit messages can be passed on the command line.
        shortCommand=format_command([executable, *command[subcommand_index:]])[:1000],
        command=format_command(command)[:1000],
    ) as marker_data:
        try:
            yield marker_data
        except subprocess.CalledProcessError as e:
            marker_data["exitCode"] = e.returncode
            raise
        except CommandError as e:
            marker_data["exitCode"] = e.status
            raise
        marker_data["exitCode"] = 0


def check_call(command: list[str], **kwargs):
    # wrapper around subprocess.check_call with debug output
    debug_log_command(command)
    kwargs["encoding"] = "UTF-8"
    try:
        with profiler_marker(command):
            subprocess.check_call(command, **kwargs)
    except subprocess.CalledProcessError as e:
        raise CommandError(
            "command '%s' failed to complete successfully" % command[0], e.returncode
        )


def check_call_by_line(
    command: list[str], cwd: str | None = None, never_log: bool = False
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
    # `stdout` is only `None` when the pipe above was not requested.
    assert process.stdout is not None, "`stdout` should be a pipe."

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
    command: list[str],
    cwd: str | None = None,
    strip: bool = True,
    never_log: bool = False,
    stdin=None,
    stderr=None,
    env: dict | None = None,
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
        with profiler_marker(command):
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


def check_output_binary(command: list[str], **kwargs) -> bytes:
    """Run `command` and return its raw output. See `command_output`."""
    return command_output(command, expect_binary=True, **kwargs)


def check_output_text(command: list[str], **kwargs) -> str:
    """Run `command` and return its output as a single string. See `command_output`."""
    return command_output(command, **kwargs)


def check_output(command: list[str], keep_ends: bool = False, **kwargs) -> list[str]:
    """Run `command` and return its output split into lines. See `command_output`."""
    return check_output_text(command, **kwargs).splitlines(keep_ends)
