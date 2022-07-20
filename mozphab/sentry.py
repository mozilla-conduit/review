# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import errno
import logging
import socket
import ssl
import urllib.error

import hglib.error
import sentry_sdk
from pkg_resources import get_distribution
from sentry_sdk.integrations.logging import LoggingIntegration

from .exceptions import CommandError


def init_sentry():
    distribution = get_distribution("MozPhab")

    sentry_logging = LoggingIntegration(
        level=logging.INFO,
        event_level=None,
        # The default event_level is logging.ERROR, which will report any
        # "logging.error(...)" call to Sentry.  However, we respond to
        # incorrect usage with "logging.error(...)" messages, which we don't
        # want to report to Sentry.
    )
    sentry_sdk.init(
        dsn=(
            "https://f2dcfa028ddb4540b5d64a855d480909@o1069899.ingest.sentry.io/6250015"
        ),
        integrations=[sentry_logging],
        release=distribution.version,
    )


def report_to_sentry(e):
    if (
        # As we don't capture stdout/stderr, failures when running hg/git are not
        # actionable.  Some cases of CommandError never need reporting (eg. failure
        # to apply a patch).  Mercurial's command server can throw these as ServerErrors
        isinstance(e, CommandError)
        or isinstance(e, hglib.error.ServerError)
        # SSLCertVerification errors are caused by a misconfigured Python install.
        or isinstance(e, ssl.SSLCertVerificationError)
        # Network unreachable
        or (isinstance(e, OSError) and e.errno == errno.ENETUNREACH)
        # Network timeout
        or isinstance(e, TimeoutError)
        # DNS resolution failures
        or isinstance(e, socket.gaierror)
        # urllib throws URLError on SSL verification, network unreachable, etc
        or isinstance(e, urllib.error.URLError)
        # Connection resets are transient
        or isinstance(e, ConnectionResetError)
        # Ctrl-C can manifest in a number of ways
        or isinstance(e, KeyboardInterrupt)
        or isinstance(e, BrokenPipeError)
    ):
        return

    sentry_sdk.capture_exception(e)
