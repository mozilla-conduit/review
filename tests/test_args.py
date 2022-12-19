# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest import mock

from mozphab.args import should_fallback_to_submit


@mock.patch("mozphab.args.find_repo_root")
def test_should_fallback_to_submit(m_repo_root):
    commands = {"submit", "install-certificate"}

    m_repo_root.return_value = "someroot"

    argv = []
    assert should_fallback_to_submit(
        argv, commands
    ), "Empty run of `moz-phab` should fallback."

    argv = ["-h"]
    assert not should_fallback_to_submit(
        argv, commands
    ), "Running `moz-phab` with `-h` should not fallback."

    argv = ["--help"]
    assert not should_fallback_to_submit(
        argv, commands
    ), "Running `moz-phab` with `--help` should not fallback."

    argv = ["blah", "-h"]
    assert not should_fallback_to_submit(
        argv, commands
    ), "Running `moz-phab` with `-h` should not fallback."

    argv = ["blah", "--help"]
    assert not should_fallback_to_submit(
        argv, commands
    ), "Running `moz-phab` with `--help` should not fallback."

    argv = ["bad-command", "argument"]
    assert should_fallback_to_submit(
        argv, commands
    ), "Running `moz-phab` with unknown command should result in fallback."

    m_repo_root.return_value = None
    assert not should_fallback_to_submit(argv, commands), (
        "Running `moz-phab` with unknown command outside of repo root should "
        "not result in fallback."
    )
