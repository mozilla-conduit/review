# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import sentry_sdk
from pkg_resources import get_distribution
from sentry_sdk.integrations.logging import LoggingIntegration


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
        dsn="https://e8a2a97b86c7472f9308186547aebfa2@sentry.prod.mozaws.net/502",
        integrations=[sentry_logging],
        release=distribution.version,
    )


def report_to_sentry(e):
    sentry_sdk.capture_exception(e)
