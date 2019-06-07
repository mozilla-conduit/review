# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import imp
import json
import mock
import os
import pytest

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
    [dict(a=u"Ą"), [("a", u"Ą")]],
    [dict(a="A", B="b"), [("a", "A"), ("B", "b")]],
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


@mock.patch("mozphab.urllib2.Request")
@mock.patch("mozphab.urllib2.urlopen")
@mock.patch("mozphab.ConduitAPI.load_api_token")
def test_call(m_token, m_urlopen, m_Request):
    req = mock.Mock()
    response = mock.Mock()
    m_Request.return_value = req
    m_urlopen.return_value = response
    response.read.return_value = json.dumps(dict(result="x", error_code=False))
    m_token.return_value = "token"
    mozphab.conduit.set_repo(Repo())

    assert mozphab.conduit.call("method", dict(call="args")) == "x"
    m_Request.assert_called_once_with(
        "http://api_url/method", data="api.token=token&call=args"
    )
    m_urlopen.assert_called_once()

    assert mozphab.conduit.call("method", dict(call=u"ćwikła")) == "x"
    m_Request.assert_called_with(
        "http://api_url/method", data="api.token=token&call=%C4%87wik%C5%82a"
    )

    response.read.return_value = json.dumps(dict(error_code=1, error_info="x"))

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
@mock.patch("__builtin__.open")
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


@mock.patch("mozphab.ConduitAPI.call")
def test_get_revisions(m_call):
    repo = mozphab.Repository("", "", "dummy")
    mozphab.conduit.set_repo(repo)
    get_revs = mozphab.conduit.get_revisions

    # sanity checks
    with pytest.raises(ValueError):
        get_revs(ids=[1], phids=["PHID-1"])
    with pytest.raises(ValueError):
        get_revs(ids=None)
    with pytest.raises(ValueError):
        get_revs(phids=None)

    m_call.return_value = {"data": [dict(id=1, phid="PHID-1")]}

    # differential.revision.search by revision-id
    assert len(get_revs(ids=[1])) == 1
    m_call.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(ids=[1]), attachments=dict(reviewers=True)),
    )

    # differential.revision.search by phid
    m_call.reset_mock()
    mozphab.cache.reset()
    assert len(get_revs(phids=["PHID-1"])) == 1
    m_call.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(phids=["PHID-1"]), attachments=dict(reviewers=True)),
    )

    # differential.revision.search by revision-id with duplicates
    m_call.reset_mock()
    mozphab.cache.reset()
    assert len(get_revs(ids=[1, 1])) == 2
    m_call.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(ids=[1]), attachments=dict(reviewers=True)),
    )

    # differential.revision.search by phid with duplicates
    m_call.reset_mock()
    mozphab.cache.reset()
    assert len(get_revs(phids=["PHID-1", "PHID-1"])) == 2
    m_call.assert_called_with(
        "differential.revision.search",
        dict(constraints=dict(phids=["PHID-1"]), attachments=dict(reviewers=True)),
    )

    # ordering of results must match input
    m_call.reset_mock()
    mozphab.cache.reset()
    m_call.return_value = {
        "data": [
            dict(id=1, phid="PHID-1"),
            dict(id=2, phid="PHID-2"),
            dict(id=3, phid="PHID-3"),
        ]
    }
    assert get_revs(ids=[2, 1, 3]) == [
        dict(id=2, phid="PHID-2"),
        dict(id=1, phid="PHID-1"),
        dict(id=3, phid="PHID-3"),
    ]

    assert get_revs(phids=["PHID-2", "PHID-1", "PHID-3"]) == [
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
