# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import io
import json
from unittest import mock
from urllib.error import HTTPError

import pytest

from mozphab.commands.submit import (
    local_uplift_if_possible,
    update_commits_for_uplift,
)
from mozphab.commands.uplift import (
    attempt_link_assessment,
    build_assessment_linking_url,
    link_assessment,
)
from mozphab.commits import Commit
from mozphab.exceptions import Error
from mozphab.helpers import ORIGINAL_DIFF_REV_RE


class Repo:
    def __init__(self, unified_head="beta", is_descendant=True, phid="PHID-beta"):
        self.unified_head = unified_head
        self._is_descendant = is_descendant
        self.uplift_called = False
        self.phid = phid

    def get_repo_head_branch(self):
        return self.unified_head

    def is_descendant(self, *args, **kwargs):
        return self._is_descendant

    def uplift_commits(self, *args, **kwargs):
        self.uplift_called = True


def test_local_uplift_if_possible():
    class Args:
        def __init__(self, no_rebase=False, train="train"):
            self.no_rebase = no_rebase
            self.train = train

    commits = [
        Commit(
            title="A",
            reviewers={"granted": ["john"], "request": []},
            bug_id=None,
            body="",
            rev_id=1,
        ),
    ]

    repo = Repo()

    args = Args(no_rebase=True)
    assert (
        local_uplift_if_possible(args, repo, commits) is True
    ), "Should always do a one-off uplift when `--no-rebase` is set."

    args = Args()
    repo = Repo(unified_head=None)

    assert (
        local_uplift_if_possible(args, repo, commits) is True
    ), "Should avoid do a one-off when no unified head is found."

    repo = Repo(is_descendant=True)
    assert (
        local_uplift_if_possible(args, repo, commits) is False
    ), "Should avoid uplifting commits locally when destination is a descendant."

    # Rebase-uplift case.
    repo = Repo(
        is_descendant=False,
        unified_head="beta",
    )
    args = Args(
        no_rebase=False,
        train="beta",
    )
    assert (
        local_uplift_if_possible(args, repo, commits) is False
    ), "Uplifting commits locally should amend them as well."
    assert (
        repo.uplift_called
    ), "Should call `uplift_commits` when non-descendant unified head found."


def test_update_commits_for_uplift_sets_relman_review():
    commits = [
        Commit(
            title="A",
            reviewers={"granted": ["john"], "request": []},
            bug_id=None,
            body="",
            rev_id=None,
        ),
        Commit(
            title="B",
            reviewers={"granted": ["john"], "request": ["doe"]},
            bug_id=None,
            body="",
            rev_id=None,
        ),
    ]

    update_commits_for_uplift(commits, Repo())

    reviewers = commits[0].reviewers

    assert not reviewers[
        "request"
    ], "Uplifted patch should have no requested reviewers initially."
    assert not reviewers[
        "granted"
    ], "Uplifted patch should have no granted reviewers initially."

    reviewers = commits[1].reviewers

    assert not reviewers[
        "request"
    ], "Uplifted patch should have no requested reviewers initially."
    assert not reviewers[
        "granted"
    ], "Uplifted patch should have no granted reviewers initially."


def test_update_commits_for_uplift_sets_original_revision():
    commits = [
        # Check initial submission.
        Commit(
            title="bug 1: firstline r?reviewer",
            reviewers={"granted": ["john"], "request": []},
            bug_id="1",
            body=(
                "bug 1: firstline r?reviewer\n"
                "\n"
                "Differential Revision: https://phabricator.services.mozila.com/D1\n"
            ),
            rev_id=1,
        ),
        # Check update of existing uplift revision.
        Commit(
            title="bug 1: firstline r?reviewer",
            reviewers={"granted": ["john"], "request": []},
            bug_id="1",
            body=(
                "bug 1: firstline r?reviewer\n"
                "\n"
                "Original Revision: https://phabricator.services.mozila.com/D1\n"
                "\n"
                "Differential Revision: https://phabricator.services.mozila.com/D2\n"
            ),
            rev_id=2,
        ),
        # Check another initial submission.
        Commit(
            title="bug 3: commit message",
            reviewers={"granted": [], "request": []},
            bug_id="3",
            body=(
                "bug 3: commit message\n"
                "\n"
                "Differential Revision: https://phabricator.services.mozila.com/D3\n"
            ),
            rev_id=3,
        ),
    ]

    with mock.patch("mozphab.commands.submit.conduit.get_revisions") as m_get_revs:
        m_get_revs.return_value = [
            {"id": 1, "fields": {"repositoryPHID": "PHID-mc"}},
            {"id": 2, "fields": {"repositoryPHID": "PHID-beta"}},
            {"id": 3, "fields": {"repositoryPHID": "PHID-mc"}},
        ]
        update_commits_for_uplift(commits, Repo())

    # Initial submission.
    body = commits[0].body
    rev_id = commits[0].rev_id

    assert "Differential Revision:" not in body
    assert "Original Revision:" in body
    assert rev_id is None

    # Update of existing uplift.
    body = commits[1].body
    rev_id = commits[1].rev_id

    assert "Differential Revision:" in body
    assert "Original Revision:" in body
    assert rev_id == 2

    # Another initial submission.
    body = commits[2].body
    rev_id = commits[2].rev_id

    assert "Differential Revision:" not in body
    assert ORIGINAL_DIFF_REV_RE.search(body).group("rev") == "3"
    assert rev_id is None


def test_uplift_beta_commit_to_esr():
    commit = Commit(
        title="bug 2: commit message r=john",
        reviewers={"granted": ["john"], "request": []},
        bug_id="2",
        body=(
            "bug 2: commit message r=john\n"
            "\n"
            "Original Revision: https://phabricator.services.mozila.com/D1\n"
            "\n"
            "Differential Revision: https://phabricator.services.mozila.com/D2\n"
        ),
        rev_id=2,
    )

    with mock.patch("mozphab.commands.submit.conduit.get_revisions") as m_get_revs:
        m_get_revs.return_value = [{"id": 2, "fields": {"repositoryPHID": "PHID-beta"}}]
        update_commits_for_uplift([commit], Repo(phid="PHID-esr"))

    reviewers = commit.reviewers
    body = commit.body
    rev_id = commit.rev_id

    assert not reviewers["request"]
    assert not reviewers["granted"]
    assert "Differential Revision:" not in body
    assert ORIGINAL_DIFF_REV_RE.search(body).group("rev") == "1"
    assert rev_id is None


def test_update_commits_for_uplift_strips_dontbuild():
    commits = [
        Commit(
            title="bug 1: fix something r=reviewer DONTBUILD",
            reviewers={"granted": ["reviewer"], "request": []},
            bug_id="1",
            body=(
                "bug 1: fix something r=reviewer DONTBUILD\n"
                "\n"
                "Differential Revision: https://phabricator.services.mozila.com/D1\n"
            ),
            rev_id=1,
        ),
        Commit(
            title="bug 2: another fix r=reviewer DONTBUILD (NPOTB)",
            reviewers={"granted": ["reviewer"], "request": []},
            bug_id="2",
            body=(
                "bug 2: another fix r=reviewer DONTBUILD (NPOTB)\n"
                "\n"
                "Differential Revision: https://phabricator.services.mozila.com/D2\n"
            ),
            rev_id=2,
        ),
    ]

    with mock.patch("mozphab.commands.submit.conduit.get_revisions") as m_get_revs:
        m_get_revs.return_value = [
            {"id": 1, "fields": {"repositoryPHID": "PHID-mc"}},
            {"id": 2, "fields": {"repositoryPHID": "PHID-mc"}},
        ]
        update_commits_for_uplift(commits, Repo())

    assert (
        "DONTBUILD" not in commits[0].title
    ), "DONTBUILD should be stripped from title on uplift."
    assert (
        commits[0].title == "bug 1: fix something r=reviewer"
    ), "Title should have DONTBUILD removed."

    assert (
        "DONTBUILD" not in commits[1].title
    ), "DONTBUILD (NPOTB) should be stripped from title on uplift."
    assert (
        commits[1].title == "bug 2: another fix r=reviewer"
    ), "Title should have DONTBUILD (NPOTB) removed."


def test_build_assessment_linking_url():
    url = build_assessment_linking_url("https://lando.moz.tools", 123)
    assert (
        url == "https://lando.moz.tools/uplift/request/?revisions=123"
    ), "Revision ID only should produce expected URL."

    url = build_assessment_linking_url("https://lando.moz.tools", 123, 5)
    assert (
        url == "https://lando.moz.tools/uplift/request/?revisions=123&assessment_id=5"
    ), "Revision ID and assessment ID should produce expected URL."

    url = build_assessment_linking_url("https://lando.moz.tools/", 456)
    assert (
        url == "https://lando.moz.tools/uplift/request/?revisions=456"
    ), "Trailing slash in Lando URL should produce valid URL."

    url = build_assessment_linking_url("https://lando.moz.tools/", 456, 10)
    assert (
        url == "https://lando.moz.tools/uplift/request/?revisions=456&assessment_id=10"
    ), "Trailing slash in Lando URL with assessment ID should produce valid URL."


def make_urlopen_response(data: dict) -> mock.MagicMock:
    """Create a mock context manager matching `urllib.request.urlopen` usage."""
    response = mock.MagicMock()
    response.read.return_value = json.dumps(data).encode()
    response.__enter__ = mock.Mock(return_value=response)
    response.__exit__ = mock.Mock(return_value=False)
    return response


@mock.patch("mozphab.commands.uplift.conduit")
@mock.patch("mozphab.commands.uplift.url_request.urlopen")
def test_link_assessment(m_urlopen, m_conduit):
    m_conduit.load_api_token.return_value = "test-token"

    response_data = {"revision_id": 123, "assessment_id": 5, "created": True}
    m_urlopen.return_value = make_urlopen_response(response_data)

    result = link_assessment("https://lando.moz.tools", 123, 5)

    m_urlopen.assert_called_once()
    request = m_urlopen.call_args[0][0]

    assert (
        request.full_url == "https://lando.moz.tools/api/uplift/assessments/link"
    ), "Request URL should point to the Lando assessment linking endpoint."
    assert (
        request.get_header("X-phabricator-api-key") == "test-token"
    ), "Request should include the Phabricator API token header."
    assert (
        request.get_header("Content-type") == "application/json"
    ), "Request should use JSON content type."
    assert request.get_method() == "POST", "Request should use POST method."

    body = json.loads(request.data)
    assert body == {
        "revision_id": 123,
        "assessment_id": 5,
    }, "Request body should contain `revision_id` and `assessment_id`."

    assert result == response_data, "`link_assessment` should return the API response."


@mock.patch("mozphab.commands.uplift.conduit")
@mock.patch("mozphab.commands.uplift.url_request.urlopen")
def test_link_assessment_strips_trailing_slash(m_urlopen, m_conduit):
    m_conduit.load_api_token.return_value = "test-token"

    response_data = {"revision_id": 456, "assessment_id": 10, "created": True}
    m_urlopen.return_value = make_urlopen_response(response_data)

    link_assessment("https://lando.moz.tools/", 456, 10)

    request = m_urlopen.call_args[0][0]
    assert (
        request.full_url == "https://lando.moz.tools/api/uplift/assessments/link"
    ), "Trailing slash in Lando URL should be stripped."


@mock.patch("mozphab.commands.uplift.conduit")
@mock.patch("mozphab.commands.uplift.url_request.urlopen")
def test_link_assessment_http_error_rfc7807(m_urlopen, m_conduit):
    """Error response with RFC 7807 Problem Details JSON."""
    m_conduit.load_api_token.return_value = "test-token"

    problem_body = json.dumps(
        {
            "type": "about:blank",
            "title": "Bad Request",
            "status": 400,
            "detail": "Revision D123 is not an uplift revision.",
        }
    ).encode()

    m_urlopen.side_effect = HTTPError(
        url="https://lando.moz.tools/api/uplift/assessments/link",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=io.BytesIO(problem_body),
    )

    with pytest.raises(Error, match="Failed to link assessment") as exc_info:
        link_assessment("https://lando.moz.tools", 123, 5)

    error_message = str(exc_info.value)
    assert "HTTP 400" in error_message, "Error should include the HTTP status code."
    assert (
        "Revision D123 is not an uplift revision." in error_message
    ), "Error should include the RFC 7807 `detail`."


@mock.patch("mozphab.commands.uplift.conduit")
@mock.patch("mozphab.commands.uplift.url_request.urlopen")
def test_link_assessment_http_error_non_json(m_urlopen, m_conduit):
    """Error response with a non-JSON body falls back to raw text."""
    m_conduit.load_api_token.return_value = "test-token"

    m_urlopen.side_effect = HTTPError(
        url="https://lando.moz.tools/api/uplift/assessments/link",
        code=500,
        msg="Internal Server Error",
        hdrs={},
        fp=io.BytesIO(b"Internal Server Error"),
    )

    with pytest.raises(Error, match="Failed to link assessment") as exc_info:
        link_assessment("https://lando.moz.tools", 123, 5)

    error_message = str(exc_info.value)
    assert "HTTP 500" in error_message, "Error should include the HTTP status code."
    assert (
        "Internal Server Error" in error_message
    ), "Error should fall back to the raw response body."


@pytest.mark.parametrize(
    "side_effect, expected",
    [
        (None, True),
        (Error("something went wrong"), False),
    ],
    ids=["success", "failure"],
)
@mock.patch("mozphab.commands.uplift.link_assessment")
def test_try_link_assessment(m_link_assessment, side_effect, expected):
    m_link_assessment.side_effect = side_effect

    result = attempt_link_assessment("https://lando.moz.tools", 123, 5)

    assert result is expected, (
        f"`try_link_assessment` should return `{expected}` "
        f"when `link_assessment` {'succeeds' if expected else 'raises `Error`'}."
    )
    m_link_assessment.assert_called_once_with("https://lando.moz.tools", 123, 5)
