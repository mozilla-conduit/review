# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest import mock

import pytest

from mozphab.jujutsu import Jujutsu


@pytest.fixture
def jj():
    """A bare `Jujutsu` instance, without touching a real repo.

    The colocated Git backend is replaced with a `Mock` so delegation to it
    can be asserted directly.
    """
    jj = object.__new__(Jujutsu)
    jj._Jujutsu__git_repo = mock.Mock()  # type: ignore
    return jj


@pytest.mark.parametrize(
    "method,args,kwargs,return_value",
    (
        ("get_public_node", ("git_sha",), {}, "hg_sha"),
        ("is_public", ("sha111",), {}, True),
        ("get_latest_landing_node", (), {"before": 1547806078}, "landing_sha"),
    ),
)
def test_delegates_to_git(jj, method, args, kwargs, return_value):
    git_method = getattr(jj._Jujutsu__git_repo, method)
    git_method.return_value = return_value

    assert getattr(jj, method)(*args, **kwargs) == return_value
    git_method.assert_called_once_with(*args, **kwargs)


@mock.patch.object(Jujutsu, "_Jujutsu__cli_log_text")
def test_get_current_node_queries_working_copy_commit(m_cli_log_text, jj):
    m_cli_log_text.return_value = "wc_commit_id"

    assert jj.get_current_node() == "wc_commit_id"
    m_cli_log_text.assert_called_once_with(template='commit_id ++ "\\n"', revset="@")
