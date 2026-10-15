# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest import mock

from mozphab import mozphab


def args_for_command(needs_user_data: bool) -> mock.MagicMock:
    args = mock.MagicMock()
    # Skip the filesystem operations a repository would need.
    args.needs_repo = False
    args.needs_user_data = needs_user_data
    return args


@mock.patch("mozphab.mozphab.check_for_updates", new=mock.Mock(return_value=None))
@mock.patch("mozphab.mozphab.parse_args")
def test_command_needing_user_data_is_given_it(m_parse_args, user_data):
    args = args_for_command(needs_user_data=True)
    m_parse_args.return_value = args

    mozphab.main([], is_development=True)

    assert args.func.call_args_list == [
        mock.call(args, user_data)
    ], "A command declaring `needs_user_data` should be handed the `UserData`."


@mock.patch("mozphab.mozphab.check_for_updates", new=mock.Mock(return_value=None))
@mock.patch("mozphab.mozphab.parse_args")
def test_command_not_needing_user_data_is_not_given_it(m_parse_args):
    args = args_for_command(needs_user_data=False)
    m_parse_args.return_value = args

    mozphab.main([], is_development=True)

    assert args.func.call_args_list == [
        mock.call(args)
    ], "A command that doesn't need user data should be called without it."
