# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import mock
import pytest
from contextlib import contextmanager
from immutabledict import immutabledict

from mozphab import diff, exceptions, mozphab, repository, simplecache

from mozphab.conduit import conduit, ConduitAPIError


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


@mock.patch("mozphab.conduit.read_json_field")
def test_load_api_token(m_read):
    m_read.return_value = False
    mozphab.conduit.set_repo(Repo())
    with pytest.raises(ConduitAPIError):
        mozphab.conduit.load_api_token()

    m_read.return_value = "x"
    assert mozphab.conduit.load_api_token() == "x"


@mock.patch("mozphab.conduit.HTTPSConnection")
@mock.patch("mozphab.conduit.ConduitAPI.load_api_token")
def test_call(m_token, m_Connect):
    conn = mock.Mock()
    m_Connect.return_value = conn
    from io import BytesIO

    conn.getresponse.return_value = BytesIO(b'{"result": "x", "error_code": false}')
    m_token.return_value = "token"
    mozphab.conduit.set_repo(Repo())

    assert mozphab.conduit.call("method", dict(call="args")) == "x"
    conn.request.assert_called_once_with(
        "POST",
        "https://api_url/method",
        body="params=%7B%22call%22%3A+%22args%22%2C+"
        "%22__conduit__%22%3A+%7B"
        "%22token%22%3A+%22token%22%7D%7D&"
        "output=json"
        "&__conduit__=True",
    )

    conn.getresponse.return_value = BytesIO(b'{"result": "x", "error_code": false}')
    assert mozphab.conduit.call("method", dict(call="ćwikła")) == "x"
    conn.request.assert_called_with(
        "POST",
        "https://api_url/method",
        body="params=%7B%22call%22%3A+%22%5Cu0107wik%5Cu0142a%22%2C+"
        "%22__conduit__%22%3A+%7B%22token%22%3A+%22token%22%7D%7D"
        "&output=json&__conduit__=True",
    )

    m_Connect.reset_mock()
    conn.reset_mock()
    conn.getresponse.return_value = BytesIO(b'{"result": "x", "error_code": false}')
    assert mozphab.conduit.call("method", dict(empty_dict={}, empty_list=[])) == "x"
    conn.request.assert_called_once_with(
        "POST",
        "https://api_url/method",
        body="params=%7B%22empty_dict%22%3A+%7B%7D%2C+%22empty_list%22%3A+"
        "%5B%5D%2C+%22__conduit__%22%3A+%7B%22token%22%3A+%22token%22%7D%7D"
        "&output=json&__conduit__=True",
    )

    conn.getresponse.return_value = BytesIO(b'{"error_info": "x", "error_code": 1}')

    with pytest.raises(ConduitAPIError):
        mozphab.conduit.call("method", dict(call="args"))


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


basic_phab_result = immutabledict({"data": [dict(id=1, phid="PHID-1")]})


def test_get_revisions_search_by_revid(get_revs, m_call):
    """differential.revision.search by revision-id"""
    m_call.return_value = basic_phab_result

    assert len(get_revs(ids=[1])) == 1
    m_call.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(ids=[1]), attachments=dict(reviewers=True)),
    )


def test_get_revisions_search_by_phid(get_revs, m_call):
    """differential.revision.search by phid"""
    m_call.return_value = basic_phab_result

    assert len(get_revs(phids=["PHID-1"])) == 1
    m_call.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(phids=["PHID-1"]), attachments=dict(reviewers=True)),
    )


def test_get_revisions_search_by_revid_with_dups(get_revs, m_call):
    """differential.revision.search by revision-id with duplicates"""
    m_call.return_value = basic_phab_result

    assert len(get_revs(ids=[1, 1])) == 2
    m_call.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(ids=[1]), attachments=dict(reviewers=True)),
    )


def test_get_revisions_search_by_phid_with_dups(get_revs, m_call):
    """differential.revision.search by phid with duplicates"""
    m_call.return_value = basic_phab_result

    assert len(get_revs(phids=["PHID-1", "PHID-1"])) == 2
    m_call.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(phids=["PHID-1"]), attachments=dict(reviewers=True)),
    )


multiple_phab_result = immutabledict(
    {
        "data": [
            dict(id=1, phid="PHID-1"),
            dict(id=2, phid="PHID-2"),
            dict(id=3, phid="PHID-3"),
        ]
    }
)


def test_get_revisions_search_by_revids_ordering(get_revs, m_call):
    """ordering of results must match input when querying by revids"""
    m_call.return_value = multiple_phab_result
    assert get_revs(ids=[2, 1, 3]) == [
        dict(id=2, phid="PHID-2"),
        dict(id=1, phid="PHID-1"),
        dict(id=3, phid="PHID-3"),
    ]


def test_get_revisions_search_by_phids_ordering(get_revs, m_call):
    """ordering of results must match input when querying by phids"""
    m_call.return_value = multiple_phab_result
    assert get_revs(phids=["PHID-2", "PHID-1", "PHID-3"]) == [
        dict(id=2, phid="PHID-2"),
        dict(id=1, phid="PHID-1"),
        dict(id=3, phid="PHID-3"),
    ]


def test_get_revisions_search_by_revids_missing(get_revs, m_call):
    """phabricator does not return info on all rev ids"""
    m_call.return_value = multiple_phab_result
    assert get_revs(ids=[2, 4, 1, 3]) == [
        dict(id=2, phid="PHID-2"),
        dict(id=1, phid="PHID-1"),
        dict(id=3, phid="PHID-3"),
    ]


@mock.patch("mozphab.conduit.ConduitAPI.call")
def test_get_diffs(m_call):
    conduit = mozphab.conduit
    get_diffs = conduit.get_diffs

    m_call.return_value = {}
    m_call.return_value = dict(
        data=[dict(phid="PHID-1"), dict(phid="PHID-2"), dict(phid="PHID-3")]
    )
    assert get_diffs(["PHID-2", "PHID-1", "PHID-3"]) == {
        "PHID-1": dict(phid="PHID-1"),
        "PHID-2": dict(phid="PHID-2"),
        "PHID-3": dict(phid="PHID-3"),
    }


@mock.patch("mozphab.conduit.ConduitAPI.call")
def test_get_related_phids(m_call):
    get_related_phids = mozphab.conduit.get_related_phids

    m_call.return_value = {}
    assert [] == get_related_phids("aaa", include_abandoned=True)
    m_call.assert_called_once_with(
        "edge.search", {"sourcePHIDs": ["aaa"], "types": ["revision.parent"]}
    )

    m_call.side_effect = [
        dict(data=[dict(destinationPHID="bbb")]),
        dict(data=[dict(destinationPHID="aaa")]),
        dict(),
    ]
    assert ["bbb", "aaa"] == get_related_phids("ccc", include_abandoned=True)

    m_call.side_effect = [
        dict(data=[dict(destinationPHID="bbb")]),
        dict(data=[dict(destinationPHID="aaa")]),
        dict(),
        dict(
            data=[
                dict(id=1, phid="aaa", fields=dict(status=dict(value="-"))),
                dict(id=2, phid="bbb", fields=dict(status=dict(value="abandoned"))),
            ]
        ),
    ]
    assert ["aaa"] == get_related_phids("ccc", include_abandoned=False)


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
    parse = diff.Diff.parse_git_diff
    assert parse("@@ -40,9 +50,3 @@ packaging==19.1 \\") == (40, 50, 9, 3)


@mock.patch("mozphab.repository.conduit.call")
def test_diff_property(m_call, git, hg):
    git.get_public_node = lambda x: x
    git._phab_vcs = "git"
    conduit.set_repo(git)
    commit = {
        "name": "abc-name",
        "author-name": "Author Name",
        "author-email": "auth@or.email",
        "author-date-epoch": 1234567,
        "title-preview": "Title Preview",
        "node": "abc",
        "parent": "def",
    }
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
