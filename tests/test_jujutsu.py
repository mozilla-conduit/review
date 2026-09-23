# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest import mock

import pytest

from mozphab.exceptions import Error
from mozphab.jujutsu import Jujutsu


@pytest.fixture
def jj():
    """A bare `Jujutsu` instance, without touching a real repo.

    The colocated Git backend is replaced with a `Mock` so delegation to it
    can be asserted directly.
    """
    jj = object.__new__(Jujutsu)
    jj._Jujutsu__git_repo = mock.Mock()  # type: ignore
    jj._Jujutsu__patch_branch_name = ""  # type: ignore
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


@mock.patch("mozphab.jujutsu.check_call")
def test_discard_patch_attempt_abandons_bookmark_and_commits(m_check_call, jj):
    jj._Jujutsu__patch_branch_name = "phab-D1"

    jj.discard_patch_attempt("sha111")
    assert m_check_call.call_args_list == [
        mock.call(["jj", "bookmark", "delete", "--quiet", "phab-D1"]),
        mock.call(["jj", "abandon", "--quiet", "sha111..@"]),
    ]
    assert jj._Jujutsu__patch_branch_name == ""


@mock.patch("mozphab.jujutsu.check_call")
def test_discard_patch_attempt_without_a_bookmark(m_check_call, jj):
    jj.discard_patch_attempt("sha111")
    m_check_call.assert_called_once_with(["jj", "abandon", "--quiet", "sha111..@"])


@mock.patch.object(Jujutsu, "_Jujutsu__cli_log_text")
def test_get_current_node_queries_working_copy_commit(m_cli_log_text, jj):
    m_cli_log_text.return_value = "wc_commit_id"

    assert jj.get_current_node() == "wc_commit_id"
    m_cli_log_text.assert_called_once_with(template='commit_id ++ "\\n"', revset="@")


@mock.patch.object(Jujutsu, "_Jujutsu__cli_log_text")
def test_commit_stack_raises_on_change_with_no_description(m_cli_log_text, jj):
    jj.revset = ("start_id", "end_id")

    log_line = "\n".join(
        [
            "2024-01-01T00:00:00+00:00",
            "Author Name",
            "author@example.com",
            "parent_commit_id",
            "change_id",
            "commit_id",
            "false",
            "",
        ]
    )

    # "0" * 37 required because in commit_stack, the log is trimmed with
    # log = self.__cli_log_text(...)[: -len(boundary)]. Without it we would
    # consume the log line.
    #
    # boundary = "--%s--\n" % uuid.uuid4().hex which adds up to:
    # 2 (--)  + 32 (hex uuid) + 2 (--) + 1 (\n) = 37 characters.
    m_cli_log_text.return_value = log_line + "0" * 37

    with pytest.raises(Error) as excinfo:
        jj.commit_stack()

    assert str(excinfo.value) == (
        "Change change_id has no description set, unable to continue. "
        "Run `jj describe -r change_id` and provide a commit message."
    )
