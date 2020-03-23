from unittest import mock
from unittest.mock import MagicMock

from mozphab import mozphab


@mock.patch("mozphab.mozphab.parse_args")
@mock.patch("mozphab.config.Config")
@mock.patch("mozphab.mozphab.init_sentry")
@mock.patch("mozphab.updater.check_for_updates")
def test_sentry_not_enabled_if_development(
    _, mock_init_sentry, mock_config_class, mock_parse_args
):
    config = MagicMock()
    config.report_to_sentry = True
    mock_config_class.return_value = config

    args = MagicMock()
    args.needs_repo = False  # skip FS operations
    mock_parse_args.return_value = args
    mozphab.main([], is_development=True)

    mock_init_sentry.assert_not_called()


@mock.patch("mozphab.mozphab.parse_args")
@mock.patch("mozphab.config.Config")
@mock.patch("mozphab.mozphab.init_sentry")
@mock.patch("mozphab.updater.check_for_updates")
def test_sentry_not_enabled_if_config_disabled(
    _, mock_init_sentry, mock_config_class, mock_parse_args
):
    config = MagicMock()
    config.report_to_sentry = False
    mock_config_class.return_value = config

    args = MagicMock()
    args.needs_repo = False  # skip FS operations
    mock_parse_args.return_value = args
    mozphab.main([], is_development=True)

    mock_init_sentry.assert_not_called()


@mock.patch("mozphab.mozphab.parse_args")
@mock.patch("mozphab.config.Config")
@mock.patch("mozphab.mozphab.init_sentry")
@mock.patch("mozphab.updater.check_for_updates")
@mock.patch("mozphab.mozphab.telemetry")
def test_sentry_enabled(
    _telemetry, _check, mock_init_sentry, mock_config_class, mock_parse_args
):
    config = MagicMock()
    config.report_to_sentry = True
    mock_config_class.return_value = config

    args = MagicMock()
    args.needs_repo = False  # skip FS operations
    mock_parse_args.return_value = args
    mozphab.main([], is_development=False)

    mock_init_sentry.assert_called_once()
