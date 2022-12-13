# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest import mock
from unittest.mock import MagicMock

from mozphab import mozphab
from mozphab.config import config


@mock.patch("mozphab.mozphab.parse_args")
@mock.patch("mozphab.mozphab.init_sentry")
@mock.patch("mozphab.mozphab.check_for_updates", new=mock.Mock(return_value=None))
def test_sentry_not_enabled_if_development(mock_init_sentry, mock_parse_args):
    config.report_to_sentry = True
    args = MagicMock()
    args.needs_repo = False  # skip FS operations
    mock_parse_args.return_value = args

    mozphab.main([], is_development=True)

    mock_init_sentry.assert_not_called()


@mock.patch("mozphab.mozphab.parse_args")
@mock.patch("mozphab.mozphab.init_sentry")
@mock.patch("mozphab.mozphab.check_for_updates", new=mock.Mock(return_value=None))
def test_sentry_not_enabled_if_config_disabled(mock_init_sentry, mock_parse_args):
    config.report_to_sentry = False
    args = MagicMock()
    args.needs_repo = False  # skip FS operations
    mock_parse_args.return_value = args

    mozphab.main([], is_development=True)

    mock_init_sentry.assert_not_called()


@mock.patch("mozphab.mozphab.parse_args")
@mock.patch("mozphab.mozphab.init_sentry")
@mock.patch("mozphab.mozphab.telemetry")
@mock.patch("mozphab.mozphab.check_for_updates", new=mock.Mock(return_value=None))
def test_sentry_enabled(_telemetry, mock_init_sentry, mock_parse_args):
    config.report_to_sentry = True
    args = MagicMock()
    args.needs_repo = False  # skip FS operations
    mock_parse_args.return_value = args

    mozphab.main([], is_development=False)

    mock_init_sentry.assert_called_once()
