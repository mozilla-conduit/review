# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
from contextlib import contextmanager
from unittest import mock

import pytest
from immutabledict import immutabledict

from mozphab import exceptions, mozphab, repository, simplecache
from mozphab.commits import Commit
from mozphab.conduit import ConduitAPIError, conduit
from mozphab.diff import Diff
from tests.conftest import search_rev


class Repo:
    api_url = "https://api_url"
    dot_path = "dot_path"
    phab_url = "phab_url"
    path = "path"
    cvs = "git"


def test_set_args_from_repo():
    repo = Repo()
    mozphab.conduit.set_repo(repo)
    assert mozphab.conduit.repo == repo


@pytest.mark.no_mock_token
@mock.patch("mozphab.conduit.read_json_field")
def test_load_api_token(m_read):
    m_read.return_value = False
    mozphab.conduit.set_repo(Repo())
    with pytest.raises(ConduitAPIError):
        mozphab.conduit.load_api_token()

    m_read.return_value = "x"
    assert mozphab.conduit.load_api_token() == "x"


@pytest.mark.no_mock_token
def test_load_api_token_from_env(monkeypatch):
    monkeypatch.setenv("MOZPHAB_PHABRICATOR_API_TOKEN", "api-token123")

    mozphab.conduit.set_repo(Repo())
    token = mozphab.conduit.load_api_token()

    assert token == "api-token123", "API token should be loaded from environment."


@mock.patch("mozphab.conduit.ConduitAPI.load_api_token")
def test_build_request(m_load_api_token):
    m_load_api_token.return_value = "saved-token"
    mozphab.conduit.set_repo(Repo())

    # default token
    assert mozphab.conduit._build_request(
        method="method",
        args={"call": "args"},
        token=None,
    ) == {
        "data": (
            b"params=%7B%22"
            b"call%22%3A%22args%22%2C%22"
            b"__conduit__%22%3A%7B%22"
            b"token%22%3A%22saved-token%22%7D%7D&"
            b"output=json&"
            b"__conduit__=True"
        ),
        "method": "POST",
        "url": "https://api_url/method",
        "headers": {"User-Agent": mock.ANY},
    }

    # provided token
    assert mozphab.conduit._build_request(
        method="method",
        args={"call": "args"},
        token="my-token",
    ) == {
        "data": (
            b"params=%7B%22"
            b"call%22%3A%22args%22%2C%22"
            b"__conduit__%22%3A%7B%22"
            b"token%22%3A%22my-token%22%7D%7D&"
            b"output=json&"
            b"__conduit__=True"
        ),
        "method": "POST",
        "url": "https://api_url/method",
        "headers": {"User-Agent": mock.ANY},
    }

    # unicode
    assert mozphab.conduit._build_request(
        method="method",
        args={"call": "ćwikła"},
        token=None,
    ) == {
        "data": (
            b"params=%7B%22"
            b"call%22%3A%22%5Cu0107wik%5Cu0142a%22%2C%22"
            b"__conduit__%22%3A%7B%22"
            b"token%22%3A%22saved-token%22%7D%7D&"
            b"output=json&"
            b"__conduit__=True"
        ),
        "method": "POST",
        "url": "https://api_url/method",
        "headers": {"User-Agent": mock.ANY},
    }

    # empty dict, empty list
    assert mozphab.conduit._build_request(
        method="method",
        args={"empty_dict": {}, "empty_list": []},
        token=None,
    ) == {
        "data": (
            b"params=%7B%22"
            b"empty_dict%22%3A%7B%7D%2C%22empty_list%22%3A%5B%5D%2C%22"
            b"__conduit__%22%3A%7B%22"
            b"token%22%3A%22saved-token%22%7D%7D&"
            b"output=json&"
            b"__conduit__=True"
        ),
        "method": "POST",
        "url": "https://api_url/method",
        "headers": {"User-Agent": mock.ANY},
    }


@mock.patch("urllib.request.urlopen")
@mock.patch("mozphab.conduit.ConduitAPI.load_api_token")
def test_call(m_load_api_token, m_urlopen):
    m_load_api_token.return_value = "token"
    mozphab.conduit.set_repo(Repo())

    # build fake context-manager to mock urlopen
    cm = mock.MagicMock()
    cm.getcode.return_value = 200
    cm.__enter__.return_value = cm
    m_urlopen.return_value = cm

    # success
    cm.read.return_value = json.dumps({"result": "result", "error_code": False})
    assert mozphab.conduit.call("method", {"call": "args"}) == "result"

    # error
    cm.read.return_value = json.dumps({"error_info": "aieee", "error_code": 1})
    with pytest.raises(ConduitAPIError) as conduit_error:
        mozphab.conduit.call("method", {"call": "args"})
    assert conduit_error.value.args[0].startswith("Phabricator Error: ")


@mock.patch("mozphab.conduit.ConduitAPI.call")
def test_ping(m_call):
    m_call.return_value = {}
    assert mozphab.conduit.ping()

    m_call.side_effect = ConduitAPIError
    assert not mozphab.conduit.ping()

    m_call.side_effect = exceptions.CommandError
    assert not mozphab.conduit.ping()


@mock.patch("mozphab.conduit.ConduitAPI.call")
@mock.patch("mozphab.conduit.ConduitAPI.ping")
@mock.patch("mozphab.conduit.os")
@mock.patch("builtins.open")
def test_check(m_open, m_os, m_ping, m_call):
    check = mozphab.conduit.check

    m_os.path.join.return_value = "x"
    m_os.path.isfile.return_value = True
    assert check()
    m_os.utimie.assert_not_called()

    m_os.path.isfile.return_value = False
    m_ping.return_value = True
    assert check()
    m_open.assert_called_once_with("x", "a")
    m_os.utime.assert_called_once_with("x", None)

    m_ping.return_value = False
    assert not check()


@pytest.fixture
def get_revs():
    mozphab.conduit.set_repo(repository.Repository("", "", "dummy"))
    return mozphab.conduit.get_revisions


@pytest.fixture
def m_call(request):
    request.addfinalizer(simplecache.cache.reset)
    with mock.patch("mozphab.conduit.ConduitAPI.call") as xmock:
        yield xmock


def test_get_revisions_both_ids_and_phids_fails(get_revs, m_call):
    with pytest.raises(ValueError):
        get_revs(ids=[1], phids=["PHID-1"])


def test_get_revisions_none_ids_fails(get_revs, m_call):
    with pytest.raises(ValueError):
        get_revs(ids=None)


def test_get_revisions_none_phids_fails(get_revs, m_call):
    with pytest.raises(ValueError):
        get_revs(phids=None)


basic_phab_result = immutabledict({"data": [{"id": 1, "phid": "PHID-1"}]})


def test_get_revisions_search_by_revid(get_revs, m_call):
    """differential.revision.search by revision-id"""
    m_call.return_value = basic_phab_result

    assert len(get_revs(ids=[1])) == 1
    m_call.assert_called_with(
        "differential.revision.search",
        {"constraints": {"ids": [1]}, "attachments": {"reviewers": True}},
    )


def test_get_revisions_search_by_phid(get_revs, m_call):
    """differential.revision.search by phid"""
    m_call.return_value = basic_phab_result

    assert len(get_revs(phids=["PHID-1"])) == 1
    m_call.assert_called_with(
        "differential.revision.search",
        {"constraints": {"phids": ["PHID-1"]}, "attachments": {"reviewers": True}},
    )


def test_get_revisions_search_by_revid_with_dups(get_revs, m_call):
    """differential.revision.search by revision-id with duplicates"""
    m_call.return_value = basic_phab_result

    assert len(get_revs(ids=[1, 1])) == 2
    m_call.assert_called_with(
        "differential.revision.search",
        {"constraints": {"ids": [1]}, "attachments": {"reviewers": True}},
    )


def test_get_revisions_search_by_phid_with_dups(get_revs, m_call):
    """differential.revision.search by phid with duplicates"""
    m_call.return_value = basic_phab_result

    assert len(get_revs(phids=["PHID-1", "PHID-1"])) == 2
    m_call.assert_called_with(
        "differential.revision.search",
        {"constraints": {"phids": ["PHID-1"]}, "attachments": {"reviewers": True}},
    )


multiple_phab_result = immutabledict(
    {
        "data": [
            {"id": 1, "phid": "PHID-1"},
            {"id": 2, "phid": "PHID-2"},
            {"id": 3, "phid": "PHID-3"},
        ]
    }
)


def test_get_revisions_search_by_revids_ordering(get_revs, m_call):
    """ordering of results must match input when querying by revids"""
    m_call.return_value = multiple_phab_result
    assert get_revs(ids=[2, 1, 3]) == [
        {"id": 2, "phid": "PHID-2"},
        {"id": 1, "phid": "PHID-1"},
        {"id": 3, "phid": "PHID-3"},
    ]


def test_get_revisions_search_by_phids_ordering(get_revs, m_call):
    """ordering of results must match input when querying by phids"""
    m_call.return_value = multiple_phab_result
    assert get_revs(phids=["PHID-2", "PHID-1", "PHID-3"]) == [
        {"id": 2, "phid": "PHID-2"},
        {"id": 1, "phid": "PHID-1"},
        {"id": 3, "phid": "PHID-3"},
    ]


def test_get_revisions_search_by_revids_missing(get_revs, m_call):
    """phabricator does not return info on all rev ids"""
    m_call.return_value = multiple_phab_result
    assert get_revs(ids=[2, 4, 1, 3]) == [
        {"id": 2, "phid": "PHID-2"},
        {"id": 1, "phid": "PHID-1"},
        {"id": 3, "phid": "PHID-3"},
    ]


@pytest.fixture
def get_diffs():
    mozphab.conduit.set_repo(repository.Repository("", "", "dummy"))
    return mozphab.conduit.get_diffs


def test_get_diffs_both_ids_and_phids_fails(get_diffs, m_call):
    with pytest.raises(ValueError):
        get_diffs(ids=[1], phids=["PHID-1"])


def test_get_diffs_none_ids_fails(get_diffs, m_call):
    with pytest.raises(ValueError):
        get_diffs(ids=None)


def test_get_diffs_none_phids_fails(get_diffs, m_call):
    with pytest.raises(ValueError):
        get_diffs(phids=None)


def test_get_diffs_by_phid(get_diffs, m_call):
    m_call.return_value = basic_phab_result

    assert (
        len(get_diffs(phids=["PHID-1"])) == 1
    ), "Should be a length of 1 when given 1 PHID"
    m_call.assert_called_with(
        "differential.diff.search",
        {"constraints": {"phids": ["PHID-1"]}, "attachments": {"commits": True}},
    )


def test_get_diffs_by_id(get_diffs, m_call):
    m_call.return_value = basic_phab_result

    assert len(get_diffs(ids=[1])) == 1, "Should be a length of 1 when given 1 ID"
    m_call.assert_called_with(
        "differential.diff.search",
        {"constraints": {"ids": [1]}, "attachments": {"commits": True}},
    )


def test_get_diffs_search_by_phid_with_dups(get_diffs, m_call):
    """differential.diff.search by phid with duplicates"""
    m_call.return_value = basic_phab_result

    assert (
        len(get_diffs(phids=["PHID-1", "PHID-1"])) == 1
    ), "Should not include redundant/duplicate diffs via PHID"
    m_call.assert_called_with(
        "differential.diff.search",
        {"constraints": {"phids": ["PHID-1"]}, "attachments": {"commits": True}},
    )


def test_get_diffs_search_by_diffid_with_dups(get_diffs, m_call):
    """differential.diff.search by diff-id with duplicates"""
    m_call.return_value = basic_phab_result

    assert (
        len(get_diffs(ids=[1, 1])) == 1
    ), "Should not include redundant/duplicate diffs via ID"
    m_call.assert_called_with(
        "differential.diff.search",
        {"constraints": {"ids": [1]}, "attachments": {"commits": True}},
    )


def test_get_diffs_search_by_diffids_missing(get_diffs, m_call):
    """phabricator does not return info on all diff ids"""
    m_call.return_value = multiple_phab_result

    diff_dict = get_diffs(ids=[2, 4, 1, 3])
    assert diff_dict.get("PHID-1") == {
        "id": 1,
        "phid": "PHID-1",
    }, "Should return dict of Diff 1"
    assert diff_dict.get("PHID-2") == {
        "id": 2,
        "phid": "PHID-2",
    }, "Should return dict of Diff 2"
    assert diff_dict.get("PHID-3") == {
        "id": 3,
        "phid": "PHID-3",
    }, "Should return dict of Diff 3"
    assert not diff_dict.get("PHID-4"), "Should not return dict of non-existent diff"


@mock.patch("mozphab.conduit.ConduitAPI.call")
def test_get_related_phids(m_call):
    get_related_phids = mozphab.conduit.get_related_phids

    m_call.return_value = {}
    assert [] == get_related_phids("aaa", include_abandoned=True)
    m_call.assert_called_once_with(
        "edge.search", {"sourcePHIDs": ["aaa"], "types": ["revision.parent"]}
    )

    m_call.side_effect = [
        {"data": [{"destinationPHID": "bbb"}]},
        {"data": [{"destinationPHID": "aaa"}]},
        {},
    ]
    assert ["bbb", "aaa"] == get_related_phids("ccc", include_abandoned=True)

    m_call.side_effect = [
        {"data": [{"destinationPHID": "bbb"}]},
        {"data": [{"destinationPHID": "aaa"}]},
        {},
        {
            "data": [
                {"id": 1, "phid": "aaa", "fields": {"status": {"value": "-"}}},
                {"id": 2, "phid": "bbb", "fields": {"status": {"value": "abandoned"}}},
            ]
        },
    ]
    assert ["aaa"] == get_related_phids("ccc", include_abandoned=False)


def test_has_revision_reviewers(m_call):
    commit = Commit(rev_id=None)
    assert not conduit.has_revision_reviewers(commit)

    m_call.return_value = {"data": [search_rev(rev=123, reviewers=[])]}
    commit = Commit(rev_id=123)
    assert not conduit.has_revision_reviewers(commit)

    m_call.return_value = {"data": [search_rev(rev=456, reviewers=["alice"])]}
    commit = Commit(rev_id=456)
    assert conduit.has_revision_reviewers(commit)


@mock.patch("builtins.open")
@mock.patch("mozphab.conduit.json")
@mock.patch("mozphab.conduit.get_arcrc_path")
@mock.patch("os.chmod")
def test_save_api_token(m_chmod, m_get_arcrc_path, m_json, m_open, git):
    save_api_token = conduit.save_api_token

    @contextmanager
    def with_open():
        yield None

    m_get_arcrc_path.return_value = ".arcrc"
    git.api_url = "http://test/api/"
    mozphab.conduit.set_repo(git)
    m_open.side_effect = PermissionError
    with pytest.raises(PermissionError):
        save_api_token("abc")

    m_chmod.reset_mock()
    m_open.side_effect = (FileNotFoundError, with_open())
    save_api_token("abc")
    m_chmod.assert_called_once_with(".arcrc", 0o600)

    m_json.dump.assert_called_once_with(
        {"hosts": {git.api_url: {"token": "abc"}}}, mock.ANY, sort_keys=True, indent=2
    )

    m_chmod.reset_mock()
    m_json.reset_mock()
    m_open.side_effect = None
    m_json.load.return_value = {"existing_key": "existing_value"}
    save_api_token("abc")
    m_json.dump.assert_called_once_with(
        {"hosts": {git.api_url: {"token": "abc"}}, "existing_key": "existing_value"},
        mock.ANY,
        sort_keys=True,
        indent=2,
    )
    m_chmod.assert_not_called()

    m_json.reset_mock()
    m_json.load.return_value = {
        "hosts": {git.api_url: {"token": "token1"}, "address2": {"token": "token2"}},
        "existing_key": "existing_value",
    }
    save_api_token("abc")
    m_json.dump.assert_called_once_with(
        {
            "hosts": {git.api_url: {"token": "abc"}, "address2": {"token": "token2"}},
            "existing_key": "existing_value",
        },
        mock.ANY,
        sort_keys=True,
        indent=2,
    )


def test_parse_git_diff():
    parse = Diff.parse_git_diff
    assert parse("@@ -40,9 +50,3 @@ packaging==19.1 \\") == (40, 50, 9, 3)


@mock.patch("mozphab.repository.conduit.call")
def test_diff_property(m_call, git, hg):
    git.get_public_node = lambda x: x
    git._phab_vcs = "git"
    conduit.set_repo(git)
    commit = Commit(
        name="abc-name",
        author_name="Author Name",
        author_email="auth@or.email",
        author_date_epoch=1234567,
        title_preview="Title Preview",
        node="abc",
        parent="def",
        wip=False,
    )
    mozphab.conduit.set_diff_property("1", commit, "message")
    m_call.assert_called_once_with(
        "differential.setdiffproperty",
        {
            "diff_id": "1",
            "name": "local:commits",
            "data": json.dumps(
                {
                    "abc": {
                        "author": "Author Name",
                        "authorEmail": "auth@or.email",
                        "time": 1234567,
                        "summary": "Title Preview",
                        "message": "message",
                        "commit": "abc",
                        "parents": ["def"],
                    }
                }
            ),
        },
    )

    m_call.reset_mock()
    git._phab_vcs = "hg"
    git._cinnabar_installed = True
    mozphab.conduit.set_diff_property("1", commit, "message")
    m_call.assert_called_once_with(
        "differential.setdiffproperty",
        {
            "diff_id": "1",
            "name": "local:commits",
            "data": json.dumps(
                {
                    "abc": {
                        "author": "Author Name",
                        "authorEmail": "auth@or.email",
                        "time": 1234567,
                        "summary": "Title Preview",
                        "message": "message",
                        "commit": "abc",
                        "parents": ["def"],
                        "rev": "abc",
                    }
                }
            ),
        },
    )

    m_call.reset_mock()
    hg._phab_vcs = "hg"
    mozphab.conduit.set_repo(hg)
    mozphab.conduit.set_diff_property("1", commit, "message")
    m_call.assert_called_once_with(
        "differential.setdiffproperty",
        {
            "diff_id": "1",
            "name": "local:commits",
            "data": json.dumps(
                {
                    "abc": {
                        "author": "Author Name",
                        "authorEmail": "auth@or.email",
                        "time": 1234567,
                        "summary": "Title Preview",
                        "message": "message",
                        "commit": "abc",
                        "parents": ["def"],
                        "rev": "abc",
                    }
                }
            ),
        },
    )


@mock.patch("mozphab.repository.conduit.call")
def test_get_projects(m_call):
    expected_projects = [
        {
            "id": 1,
            "type": "PROJ",
            "phid": "PHID-PROJ-1",
            "attachments": {},
            "fields": {"name": "A", "slug": "a"},
        },
        {
            "id": 2,
            "type": "PROJ",
            "phid": "PHID-PROJ-2",
            "attachments": {},
            "fields": {"name": "B", "slug": "b"},
        },
    ]
    m_call.side_effect = (
        {
            "data": expected_projects,
            "maps": {},
            "query": {"queryKey": None},
            "cursor": {"limit": 100, "after": None, "before": None, "order": None},
        },
    )
    projects = mozphab.conduit.get_projects(["a", "b"])
    m_call.assert_called_once_with(
        "project.search", {"constraints": {"slugs": ["a", "b"]}}
    )
    assert projects == expected_projects


@mock.patch("mozphab.repository.conduit.get_projects")
def test_get_project_phid(m_get_projects):
    m_get_projects.side_effect = (
        [
            {
                "id": 99,
                "type": "PROJ",
                "phid": "PHID-PROJ-1",
                "fields": {
                    "name": "Release managers",
                    "slug": "release-managers",
                    "subtype": "default",
                    "milestone": None,
                    "depth": 0,
                    "parent": None,
                    "icon": {"key": "tag", "name": "Tag", "icon": "fa-tags"},
                    "color": {"key": "blue", "name": "Blue"},
                    "spacePHID": None,
                    "dateCreated": 1552593979,
                    "dateModified": 1558110482,
                    "policy": {"view": "public", "edit": "admin", "join": "no-one"},
                    "description": "description",
                },
                "attachments": {},
            }
        ],
    )
    phid = mozphab.conduit.get_project_phid("release-managers")
    m_get_projects.assert_called_once_with(["release-managers"])
    assert phid == "PHID-PROJ-1"


@mock.patch("mozphab.repository.conduit.call")
def test_submit_diff(m_call, git):
    mozphab.conduit.set_repo(git)
    diff = Diff()
    commit = Commit(
        name="abc-name",
        author_name="Author Name",
        author_email="auth@example.com",
        author_date_epoch=1234567,
        title_preview="Title Preview",
        body="Additional summary.",
        bug_id="777",
        rev_id=456,
        node="abc",
        parent="def",
    )
    m_call.side_effect = [
        # differential.revision.search
        {"data": [search_rev(rev=456, repo="PHID-BETA")]},
        # differential.creatediff
        {},
    ]
    mozphab.conduit.submit_diff(diff, commit)
    m_call.assert_any_call(
        "differential.creatediff",
        {
            "changes": mock.ANY,
            "sourceMachine": mock.ANY,
            "sourceControlSystem": "git",
            "sourceControlPath": "/",
            "sourceControlBaseRevision": "def",
            "creationMethod": "moz-phab-git",
            "lintStatus": "none",
            "unitStatus": "none",
            "repositoryPHID": "PHID-BETA",
            "sourcePath": mock.ANY,
            "branch": "HEAD",
        },
    )


@mock.patch("mozphab.repository.conduit.call")
def test_create_revision(m_call):
    commit = Commit(
        name="abc-name",
        author_name="Author Name",
        author_email="auth@example.com",
        author_date_epoch=1234567,
        title_preview="Title Preview",
        body="Additional summary.",
        bug_id="777",
        node="abc",
        parent="def",
        wip=False,
    )
    mozphab.conduit.create_revision(commit, "PHID-DIFF-7")
    m_call.assert_called_once_with(
        "differential.revision.edit",
        {
            "transactions": [
                {"type": "title", "value": "Title Preview"},
                {"type": "summary", "value": "Additional summary."},
                {"type": "bugzilla.bug-id", "value": "777"},
                {"type": "update", "value": "PHID-DIFF-7"},
            ]
        },
    )


class TestEditRevision:
    @staticmethod
    def _get_revisions(status):
        return [{"fields": {"status": {"value": status}}}]

    @mock.patch("mozphab.repository.conduit.call")
    def test_new_wip(self, m_call):
        conduit.edit_revision(wip=True)
        m_call.assert_called_once_with(
            "differential.revision.edit",
            {
                "transactions": [
                    {"type": "plan-changes", "value": True},
                ],
            },
        )

    @mock.patch("mozphab.repository.conduit.call")
    def test_new_no_wip(self, m_call):
        conduit.edit_revision(wip=False)
        m_call.assert_called_once_with(
            "differential.revision.edit",
            {
                "transactions": [],
            },
        )

    @mock.patch("mozphab.repository.conduit.get_revisions")
    @mock.patch("mozphab.repository.conduit.call")
    def test_update_wip_changes_planned(self, m_call, m_get_revisions):
        # wip + changes-planned -> plan-changes
        m_get_revisions.return_value = self._get_revisions("changes-planned")
        conduit.edit_revision(rev_id=1, wip=True)
        call_args = m_call.call_args_list
        assert len(call_args) == 2
        assert call_args[0] == mock.call(
            "differential.revision.edit",
            {
                "transactions": [],
                "objectIdentifier": 1,
            },
        )
        assert call_args[1] == mock.call(
            "differential.revision.edit",
            {
                "transactions": [
                    {"type": "plan-changes", "value": True},
                ],
                "objectIdentifier": 1,
            },
        )

    @mock.patch("mozphab.repository.conduit.get_revisions")
    @mock.patch("mozphab.repository.conduit.call")
    def test_update_wip_needs_review(self, m_call, m_get_revisions):
        # wip + needs-review -> plan-changes
        m_get_revisions.return_value = self._get_revisions("needs-review")
        conduit.edit_revision(rev_id=1, wip=True)
        m_call.assert_called_once_with(
            "differential.revision.edit",
            {
                "transactions": [
                    {"type": "plan-changes", "value": True},
                ],
                "objectIdentifier": 1,
            },
        )

    @mock.patch("mozphab.repository.conduit.get_revisions")
    @mock.patch("mozphab.repository.conduit.call")
    def test_update_wip_accepted(self, m_call, m_get_revisions):
        # wip + accepted -> plan-changes
        m_get_revisions.return_value = self._get_revisions("accepted")
        conduit.edit_revision(rev_id=1, wip=True)
        m_call.assert_called_once_with(
            "differential.revision.edit",
            {
                "transactions": [
                    {"type": "plan-changes", "value": True},
                ],
                "objectIdentifier": 1,
            },
        )

    @mock.patch("mozphab.repository.conduit.get_revisions")
    @mock.patch("mozphab.repository.conduit.call")
    def test_update_no_wip_changes_planned(self, m_call, m_get_revisions):
        # no-wip + changes-planned -> request-review
        m_get_revisions.return_value = self._get_revisions("changes-planned")
        conduit.edit_revision(rev_id=1, wip=False)
        m_call.assert_called_once_with(
            "differential.revision.edit",
            {
                "transactions": [
                    {"type": "request-review", "value": True},
                ],
                "objectIdentifier": 1,
            },
        )

    @mock.patch("mozphab.repository.conduit.get_revisions")
    @mock.patch("mozphab.repository.conduit.call")
    def test_update_no_wip_needs_review(self, m_call, m_get_revisions):
        # no-wip + needs-review -> no-op
        m_get_revisions.return_value = self._get_revisions("needs-review")
        conduit.edit_revision(rev_id=1, wip=False)
        m_call.assert_called_once_with(
            "differential.revision.edit",
            {
                "transactions": [],
                "objectIdentifier": 1,
            },
        )

    @mock.patch("mozphab.repository.conduit.get_revisions")
    @mock.patch("mozphab.repository.conduit.call")
    def test_update_no_wip_accepted(self, m_call, m_get_revisions):
        # no-wip + accepted -> no-op
        m_get_revisions.return_value = self._get_revisions("accepted")
        conduit.edit_revision(rev_id=1, wip=False)
        m_call.assert_called_once_with(
            "differential.revision.edit",
            {
                "transactions": [],
                "objectIdentifier": 1,
            },
        )

    @mock.patch("mozphab.repository.conduit.get_revisions")
    @mock.patch("mozphab.repository.conduit.call")
    def test_edit_revision_sets_parent(self, m_call, m_get_revisions):
        m_get_revisions.return_value = self._get_revisions("accepted")
        conduit.edit_revision(rev_id=1, wip=False, parent_rev_phid="PHID-DREV-123")
        m_call.assert_called_once_with(
            "differential.revision.edit",
            {
                "transactions": [{"type": "parents.add", "value": ["PHID-DREV-123"]}],
                "objectIdentifier": 1,
            },
        )
