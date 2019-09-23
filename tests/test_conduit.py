# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import imp
import mock
import os
import pytest
from contextlib import contextmanager
from frozendict import frozendict

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


class Repo:
    api_url = "http://api_url"
    dot_path = "dot_path"
    phab_url = "phab_url"
    path = "path"
    cvs = "git"


def test_set_args_from_repo():
    repo = Repo()
    mozphab.conduit.set_repo(repo)
    assert mozphab.conduit.repo == repo


args_query_testdata = [
    [dict(a="Ą"), [("a", "Ą")]],
    [dict(a="A", B="b"), [("B", "b"), ("a", "A")]],
    [dict(a=1), [("a", "1")]],
    [dict(arr=["a", 1, 2]), [("arr[0]", "a"), ("arr[1]", "1"), ("arr[2]", "2")]],
    [dict(a=dict(b=[1])), [("a[b][0]", "1")]],
]


@pytest.mark.parametrize("args,params", args_query_testdata)
def test_json_args_to_query_params(args, params):
    assert mozphab.json_args_to_query_params(args) == params


@mock.patch("mozphab.read_json_field")
def test_load_api_token(m_read):
    m_read.return_value = False
    with pytest.raises(mozphab.ConduitAPIError):
        mozphab.conduit.load_api_token()

    m_read.return_value = "x"
    assert mozphab.conduit.load_api_token() == "x"


@mock.patch("mozphab.urllib.request.Request")
@mock.patch("mozphab.urllib.request.urlopen")
@mock.patch("mozphab.ConduitAPI.load_api_token")
def test_call(m_token, m_urlopen, m_Request):
    req = mock.Mock()
    response = mock.Mock()
    m_Request.return_value = req
    m_urlopen.return_value = response
    response.read.return_value = b'{"result": "x", "error_code": false}'
    m_token.return_value = "token"
    mozphab.conduit.set_repo(Repo())

    assert mozphab.conduit.call("method", dict(call="args")) == "x"
    m_Request.assert_called_once_with(
        "http://api_url/method", data=b"api.token=token&call=args"
    )
    m_urlopen.assert_called_once()

    assert mozphab.conduit.call("method", dict(call="ćwikła")) == "x"
    m_Request.assert_called_with(
        "http://api_url/method", data=b"api.token=token&call=%C4%87wik%C5%82a"
    )

    response.read.return_value = b'{"error_info": "x", "error_code": 1}'

    with pytest.raises(mozphab.ConduitAPIError):
        mozphab.conduit.call("method", dict(call="args"))


@mock.patch("mozphab.ConduitAPI.call")
def test_ping(m_call):
    m_call.return_value = {}
    assert mozphab.conduit.ping()

    m_call.side_effect = mozphab.ConduitAPIError
    assert not mozphab.conduit.ping()

    m_call.side_effect = mozphab.CommandError
    assert not mozphab.conduit.ping()


@mock.patch("mozphab.ConduitAPI.call")
@mock.patch("mozphab.ConduitAPI.ping")
@mock.patch("mozphab.os")
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
    mozphab.conduit.set_repo(mozphab.Repository("", "", "dummy"))
    return mozphab.conduit.get_revisions


@pytest.fixture
def m_call(request):
    request.addfinalizer(mozphab.cache.reset)
    with mock.patch("mozphab.ConduitAPI.call") as xmock:
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


basic_phab_result = frozendict({"data": [dict(id=1, phid="PHID-1")]})


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


multiple_phab_result = frozendict(
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


@mock.patch("mozphab.ConduitAPI.call")
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


@mock.patch("mozphab.ConduitAPI.call")
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
@mock.patch("mozphab.json")
@mock.patch("mozphab.get_arcrc_path")
def test_save_api_token(m_get_arcrc_path, m_json, m_open, git):
    save_api_token = mozphab.conduit.save_api_token

    @contextmanager
    def with_open():
        yield None

    mozphab.get_arcrc_path.return_value = ".arcrc"
    git.api_url = "http://test/api/"
    mozphab.conduit.set_repo(git)
    m_open.side_effect = PermissionError
    with pytest.raises(PermissionError):
        save_api_token("abc")

    m_open.side_effect = (FileNotFoundError, with_open())
    save_api_token("abc")

    m_json.dump.assert_called_once_with(
        {"hosts": {git.api_url: {"token": "abc"}}}, mock.ANY, sort_keys=True, indent=2
    )

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
    parse = mozphab.Diff.parse_git_diff
    assert parse("@@ -40,9 +50,3 @@ packaging==19.1 \\") == (40, 50, 9, 3)
