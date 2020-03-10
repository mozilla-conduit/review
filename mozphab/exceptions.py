# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.


class Error(Exception):
    """Errors thrown explicitly by this script; won't generate a stack trace."""


class NotFoundError(Exception):
    """Errors raised when node is not found."""


class NonLinearException(Exception):
    """Errors raised when multiple children or parents found."""


class CommandError(Exception):
    """Errors raised by external commands."""

    status = None

    def __init__(self, msg="", status=1):
        self.status = status
        super().__init__(msg)
