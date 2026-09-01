# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
import logging
from typing import cast
from unittest import mock

import pytest

from mozphab import exceptions
from mozphab.commands import list as list_command
from mozphab.repository import Repository


def test_format_known_statuses():
    """Test formatting of known status values."""
    assert list_command.format_revision_status("needs-review") == "Needs Review"
    assert list_command.format_revision_status("needs-revision") == "Needs Revision"
    assert list_command.format_revision_status("accepted") == "Accepted"
    assert list_command.format_revision_status("changes-planned") == "Changes Planned"
    assert list_command.format_revision_status("abandoned") == "Abandoned"
    assert list_command.format_revision_status("published") == "Published"


def test_format_unknown_status():
    """Test formatting of unknown status values."""
    # Should title-case unknown statuses
    assert list_command.format_revision_status("some-status") == "Some-Status"


@pytest.fixture
def repo() -> Repository:
    """`list_revisions` never touches the repository, so a stub is enough."""
    return cast(Repository, mock.MagicMock(spec=Repository))


@pytest.fixture
def args():
    return argparse.Namespace(
        include_published=False,
        include_abandoned=False,
        status=None,
        verbose=False,
        format="text",
    )


@pytest.fixture
def mock_get_revisions():
    revisions: list[dict] = [
        {
            "id": 123,
            "phid": "PHID-DREV-123",
            "fields": {
                "title": "Test revision 123",
                "status": {"value": "needs-review", "closed": False},
                "uri": "https://phabricator.services.mozilla.com/D123",
                "dateCreated": 1234567890,
                "dateModified": 1234567900,
            },
            "attachments": {
                "reviewers": {
                    "reviewers": [
                        {
                            "reviewerPHID": "PHID-USER-1",
                            "status": "added",
                            "isBlocking": False,
                        }
                    ]
                }
            },
        },
        {
            "id": 124,
            "phid": "PHID-DREV-124",
            "fields": {
                "title": "Test revision 124",
                "status": {"value": "accepted", "closed": False},
                "uri": "https://phabricator.services.mozilla.com/D124",
                "dateCreated": 1234567891,
                "dateModified": 1234567901,
            },
            "attachments": {
                "reviewers": {
                    "reviewers": [
                        {
                            "reviewerPHID": "PHID-USER-2",
                            "status": "accepted",
                            "isBlocking": True,
                        }
                    ]
                }
            },
        },
        {
            "id": 125,
            "phid": "PHID-DREV-125",
            "fields": {
                "title": "Test revision 125",
                "status": {"value": "abandoned", "closed": False},
                "uri": "https://phabricator.services.mozilla.com/D125",
                "dateCreated": 1234567892,
                "dateModified": 1234567902,
            },
            "attachments": {"reviewers": {"reviewers": []}},
        },
        {
            "id": 126,
            "phid": "PHID-DREV-126",
            "fields": {
                "title": "Test revision 126 - closed",
                "status": {"value": "published", "closed": True},
                "uri": "https://phabricator.services.mozilla.com/D126",
                "dateCreated": 1234567893,
                "dateModified": 1234567903,
            },
            "attachments": {"reviewers": {"reviewers": []}},
        },
    ]

    def func(author, statuses=None, order=None):
        return [
            rev
            for rev in revisions
            if statuses is None or rev["fields"]["status"]["value"] in statuses
        ]

    return func


@pytest.fixture
def mock_conduit(mock_get_revisions):
    with mock.patch("mozphab.commands.list.conduit") as m:
        m.check.return_value = True
        m.whoami.return_value = {"phid": "PHID-USER-test"}
        m.get_revisions_for_author.side_effect = mock_get_revisions
        m.get_usernames_for_phids.return_value = {
            "PHID-USER-1": "user1",
            "PHID-USER-2": "user2",
        }
        yield m


def test_list_revisions_success(repo, mock_conduit, args, caplog):
    """Test successful listing of revisions."""
    # Setup mocks
    caplog.set_level(logging.INFO, logger="moz-phab")

    # Call function
    list_command.list_revisions(repo, args)

    # Verify API calls
    mock_conduit.check.assert_called_once()
    mock_conduit.whoami.assert_called_once()
    mock_conduit.get_revisions_for_author.assert_called_once()

    # Verify the API call arguments
    call_args = mock_conduit.get_revisions_for_author.call_args
    assert call_args[0][0] == "PHID-USER-test"
    statuses = call_args[1]["statuses"]
    assert "published" not in statuses
    assert "abandoned" not in statuses

    assert "D123" in caplog.text, "In-flight revision D123 should be listed"
    assert "D124" in caplog.text, "In-flight revision D124 should be listed"
    assert "Total: 2 revision(s)" in caplog.text, "Total count should be shown"
    assert "D125" not in caplog.text, "Abandoned revision D125 should not be listed"
    assert "D126" not in caplog.text, "Published revision D126 should not be listed"


def test_list_revisions_include_published(repo, mock_conduit, args, caplog):
    """Test listing with --include-published flag includes closed revisions."""
    caplog.set_level(logging.INFO, logger="moz-phab")

    # Modify args to include published
    args.include_published = True

    # Call function
    list_command.list_revisions(repo, args)

    call_args = mock_conduit.get_revisions_for_author.call_args
    statuses = call_args[1]["statuses"]
    assert "published" in statuses, "Status filter should include 'published'"
    assert "D126" in caplog.text, "Published revision D126 should be listed"


def test_list_revisions_include_abandoned(repo, mock_conduit, args, caplog):
    """Test listing with --include-abandoned flag."""
    caplog.set_level(logging.INFO, logger="moz-phab")

    # Modify args to include abandoned
    args.include_abandoned = True

    # Call function
    list_command.list_revisions(repo, args)

    statuses = mock_conduit.get_revisions_for_author.call_args[1]["statuses"]
    assert "abandoned" in statuses, "Status filter should include 'abandoned'"
    assert "D125" in caplog.text, "Abandoned revision D125 should be listed"


def test_list_revisions_status_filter(repo, mock_conduit, args):
    """Test listing with status filter."""
    # Modify args to filter by status
    args.status = ["accepted"]

    # Call function
    list_command.list_revisions(repo, args)

    # Verify the API call includes status constraint
    assert mock_conduit.get_revisions_for_author.call_args[1]["statuses"] == [
        "accepted"
    ]


def test_list_revisions_no_results(repo, mock_conduit, args, caplog):
    """Test listing when no revisions are found."""
    caplog.set_level(logging.INFO, logger="moz-phab")

    args.status = ["not-a-real-status"]

    # Call function
    list_command.list_revisions(repo, args)

    assert mock_conduit.get_revisions_for_author.call_args[1]["statuses"] == args.status
    assert "No revisions found" in caplog.text, "Should report no revisions found"


def test_list_revisions_connection_failure(repo, mock_conduit, args):
    """Test handling of connection failures."""
    mock_conduit.check.return_value = False

    with pytest.raises(exceptions.Error, match="Failed to use Conduit API"):
        list_command.list_revisions(repo, args)


def test_list_revisions_no_user_phid(repo, mock_conduit, args):
    """Test handling when user PHID cannot be determined."""
    mock_conduit.whoami.return_value = {}  # No PHID

    with pytest.raises(exceptions.Error, match="Unable to determine current user"):
        list_command.list_revisions(repo, args)


def test_list_revisions_verbose(repo, mock_conduit, args, caplog):
    """Test listing with verbose flag shows reviewers."""
    caplog.set_level(logging.INFO, logger="moz-phab")

    args.verbose = True
    list_command.list_revisions(repo, args)

    assert (
        "Reviewers:" in caplog.text
    ), "Reviewer information should be shown in verbose mode"
    assert "user1" in caplog.text, "Reviewer username should be resolved from PHID"


@pytest.fixture
def json_args(args):
    args.format = "json"
    return args


@mock.patch("builtins.print")
def test_list_json_format(mock_print, repo, mock_conduit, json_args):
    """Test JSON output format."""
    # Call function
    list_command.list_revisions(repo, json_args)

    # Verify print was called with JSON
    mock_print.assert_called_once()
    output = mock_print.call_args[0][0]

    # Verify it's valid JSON
    parsed = json.loads(output)
    assert len(parsed) == 2
    assert parsed[0]["id"] == 123
    assert parsed[0]["title"] == "Test revision 123"
    assert parsed[0]["status"] == "needs-review"
    assert parsed[0]["uri"] == "https://phabricator.services.mozilla.com/D123"


@mock.patch("builtins.print")
def test_list_json_format_verbose(mock_print, repo, mock_conduit, json_args):
    """Test JSON output format with verbose flag."""
    # Modify args to be verbose
    json_args.verbose = True

    # Call function
    list_command.list_revisions(repo, json_args)

    # Verify print was called with JSON
    mock_print.assert_called_once()
    output = mock_print.call_args[0][0]

    # Verify it's valid JSON with reviewers
    parsed = json.loads(output)
    assert "reviewers" in parsed[0]
    assert len(parsed[0]["reviewers"]) == 1
    assert parsed[0]["reviewers"][0]["phid"] == "PHID-USER-1"
    assert parsed[0]["reviewers"][0]["status"] == "added"
    assert parsed[0]["reviewers"][0]["isBlocking"] is False


@mock.patch("builtins.print")
def test_list_json_empty_results(mock_print, repo, mock_conduit, json_args):
    """Test JSON output with no results."""
    json_args.status = ["not-a-real-status"]

    # Call function
    list_command.list_revisions(repo, json_args)

    # Verify print was called with empty JSON array
    mock_print.assert_called_once()
    output = mock_print.call_args[0][0]
    parsed = json.loads(output)
    assert parsed == []


@mock.patch("builtins.print")
def test_list_json_no_spinner(mock_print, repo, mock_conduit, json_args, caplog):
    """Test that JSON format doesn't produce spinner output."""
    caplog.set_level(logging.DEBUG, logger="moz-phab")

    # Call function - should not produce logs
    list_command.list_revisions(repo, json_args)

    assert caplog.text == ""

    # Only print should be called (for JSON output)
    mock_print.assert_called_once()


def test_add_parser():
    """Test that the parser is configured correctly."""
    # Create a parent parser to add our command to
    parent_parser = argparse.ArgumentParser()
    subparsers = parent_parser.add_subparsers()

    # Add our list parser
    list_command.add_parser(subparsers)

    # Test parsing with defaults
    args = parent_parser.parse_args(["list"])
    assert args.include_published is False
    assert args.include_abandoned is False
    assert args.status is None
    assert args.verbose is False
    assert args.format == "text"

    # Test with --include-published flag
    args = parent_parser.parse_args(["list", "--include-published"])
    assert args.include_published is True

    # Test with --include-abandoned flag
    args = parent_parser.parse_args(["list", "--include-abandoned"])
    assert args.include_abandoned is True

    # Test with --status flag
    args = parent_parser.parse_args(["list", "--status", "accepted"])
    assert args.status == ["accepted"]

    # Test with multiple statuses
    args = parent_parser.parse_args(["list", "--status", "accepted", "needs-review"])
    assert args.status == ["accepted", "needs-review"]

    # Test with --verbose flag
    args = parent_parser.parse_args(["list", "--verbose"])
    assert args.verbose is True

    # Test with -v flag
    args = parent_parser.parse_args(["list", "-v"])
    assert args.verbose is True

    # Test with --format flag
    args = parent_parser.parse_args(["list", "--format", "json"])
    assert args.format == "json"

    # Test combined flags
    args = parent_parser.parse_args(
        [
            "list",
            "--include-published",
            "--verbose",
            "--format",
            "json",
            "--status",
            "accepted",
        ]
    )
    assert args.include_published is True
    assert args.verbose is True
    assert args.format == "json"
    assert args.status == ["accepted"]
