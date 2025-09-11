# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import unittest
from unittest import mock

from callee import Contains

from mozphab import exceptions
from mozphab.commands import abandon


class TestCheckRevisionId(unittest.TestCase):
    """Test the revision ID validation functionality."""

    def test_single_ids(self):
        """Test parsing single revision IDs."""
        # Basic integer
        result = abandon.check_revision_id("123")
        self.assertEqual(result, 123)

        # D-prefixed
        result = abandon.check_revision_id("D456")
        self.assertEqual(result, 456)

    def test_urls(self):
        """Test parsing Phabricator URLs."""
        # HTTPS URL
        result = abandon.check_revision_id(
            "https://phabricator.services.mozilla.com/D12345"
        )
        self.assertEqual(result, 12345)

        # HTTP URL
        result = abandon.check_revision_id("http://phabricator.example.com/D678")
        self.assertEqual(result, 678)

    def test_invalid_format(self):
        """Test that invalid formats raise errors."""
        with self.assertRaises(argparse.ArgumentTypeError) as cm:
            abandon.check_revision_id("invalid")
        self.assertIn("Invalid Revision ID", str(cm.exception))

        with self.assertRaises(argparse.ArgumentTypeError) as cm:
            abandon.check_revision_id("D")
        self.assertIn("Invalid Revision ID", str(cm.exception))

        with self.assertRaises(argparse.ArgumentTypeError) as cm:
            abandon.check_revision_id("123-125")
        self.assertIn("Invalid Revision ID", str(cm.exception))


class TestAbandonRevisions(unittest.TestCase):
    """Test the main abandon functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.args = argparse.Namespace(yes=True)  # Skip confirmation for tests
        self.mock_revisions = [
            {
                "id": 123,
                "phid": "PHID-DREV-123",
                "fields": {
                    "title": "Test revision 123",
                    "status": {"value": "needs-review"},
                },
            },
            {
                "id": 124,
                "phid": "PHID-DREV-124",
                "fields": {
                    "title": "Test revision 124",
                    "status": {"value": "abandoned"},
                },
            },
            {
                "id": 125,
                "phid": "PHID-DREV-125",
                "fields": {
                    "title": "Test revision 125",
                    "status": {"value": "accepted"},
                },
            },
        ]

    @mock.patch("mozphab.commands.abandon.conduit")
    def test_abandon_revisions_success(self, mock_conduit):
        """Test successful abandonment of revisions."""
        # Setup mocks
        mock_conduit.check.return_value = True
        mock_conduit.get_revisions.return_value = [
            self.mock_revisions[0],
            self.mock_revisions[2],
        ]

        # Call function
        abandon.abandon_revisions([123, 125], self.args)

        # Verify API calls
        mock_conduit.check.assert_called_once()
        mock_conduit.get_revisions.assert_called_once_with(ids=[123, 125])

        # Verify abandonment calls
        expected_calls = [
            mock.call(
                rev_id="PHID-DREV-123",
                transactions=[{"type": "abandon", "value": True}],
            ),
            mock.call(
                rev_id="PHID-DREV-125",
                transactions=[{"type": "abandon", "value": True}],
            ),
        ]
        mock_conduit.apply_transactions_to_revision.assert_has_calls(expected_calls)

    @mock.patch("mozphab.commands.abandon.conduit")
    def test_abandon_already_abandoned(self, mock_conduit):
        """Test handling of already abandoned revisions."""
        # Setup mocks
        mock_conduit.check.return_value = True
        mock_conduit.get_revisions.return_value = [
            self.mock_revisions[1]
        ]  # Already abandoned

        # Call function
        with self.assertLogs() as logging_watcher:
            abandon.abandon_revisions([124], self.args)

        # Verify no abandonment calls were made
        mock_conduit.apply_transactions_to_revision.assert_not_called()

        # Verify warning was logged
        self.assertIn(
            Contains("All specified revisions are already abandoned."),
            logging_watcher.output,
        )

    @mock.patch("mozphab.commands.abandon.conduit")
    def test_abandon_connection_failure(self, mock_conduit):
        """Test handling of connection failures."""
        mock_conduit.check.return_value = False

        with self.assertRaises(exceptions.Error) as cm:
            abandon.abandon_revisions([123], self.args)

        self.assertIn("Failed to use Conduit API", str(cm.exception))

    @mock.patch("mozphab.commands.abandon.conduit")
    def test_abandon_revisions_not_found(self, mock_conduit):
        """Test handling of revisions not found."""
        mock_conduit.check.return_value = True
        mock_conduit.get_revisions.return_value = []

        with self.assertRaises(exceptions.Error) as cm:
            abandon.abandon_revisions([999], self.args)

        self.assertIn("No revisions found", str(cm.exception))

    @mock.patch("mozphab.commands.abandon.conduit")
    @mock.patch("mozphab.commands.abandon.prompt")
    def test_abandon_with_confirmation_no(self, mock_prompt, mock_conduit):
        """Test abandonment with confirmation prompt answering No."""
        # Setup mocks
        mock_conduit.check.return_value = True
        mock_conduit.get_revisions.return_value = [self.mock_revisions[0]]
        mock_prompt.return_value = "No"

        # Create args without --yes flag
        args = argparse.Namespace(yes=False)

        # Call function
        abandon.abandon_revisions([123], args)

        # Verify prompt was called
        mock_prompt.assert_called_once_with("Abandon these revisions?", ["Yes", "No"])

        # Verify no abandonment calls were made
        mock_conduit.apply_transactions_to_revision.assert_not_called()

    @mock.patch("mozphab.commands.abandon.conduit")
    @mock.patch("mozphab.commands.abandon.prompt")
    def test_abandon_with_confirmation_yes(self, mock_prompt, mock_conduit):
        """Test abandonment with confirmation prompt answering Yes."""
        # Setup mocks
        mock_conduit.check.return_value = True
        mock_conduit.get_revisions.return_value = [self.mock_revisions[0]]
        mock_prompt.return_value = "Yes"

        # Create args without --yes flag
        args = argparse.Namespace(yes=False)

        # Call function
        abandon.abandon_revisions([123], args)

        # Verify prompt was called
        mock_prompt.assert_called_once_with("Abandon these revisions?", ["Yes", "No"])

        # Verify abandonment call was made
        mock_conduit.apply_transactions_to_revision.assert_called_once_with(
            rev_id="PHID-DREV-123", transactions=[{"type": "abandon", "value": True}]
        )

    @mock.patch("mozphab.commands.abandon.conduit")
    def test_abandon_mixed_statuses(self, mock_conduit):
        """Test abandonment with mixed revision statuses."""
        # Setup mocks
        mock_conduit.check.return_value = True
        mock_conduit.get_revisions.return_value = self.mock_revisions  # Mix of statuses

        # Call function
        with self.assertLogs() as logging_watcher:
            abandon.abandon_revisions([123, 124, 125], self.args)

        # Verify only non-abandoned revisions were processed
        expected_calls = [
            mock.call(
                rev_id="PHID-DREV-123",
                transactions=[{"type": "abandon", "value": True}],
            ),
            mock.call(
                rev_id="PHID-DREV-125",
                transactions=[{"type": "abandon", "value": True}],
            ),
        ]
        mock_conduit.apply_transactions_to_revision.assert_has_calls(expected_calls)

        # Verify already abandoned message was logged
        self.assertIn(Contains("Already abandoned: D124"), logging_watcher.output)


class TestAbandonMainFunction(unittest.TestCase):
    """Test the main abandon function."""

    @mock.patch("mozphab.commands.abandon.abandon_revisions")
    def test_abandon_main_success(self, mock_abandon_revisions):
        """Test successful execution of main abandon function."""
        # Setup args with revision IDs (parsed by argparse)
        args = argparse.Namespace(revisions=[123, 124])

        # Call function
        abandon.abandon(None, args)

        # Verify calls
        mock_abandon_revisions.assert_called_once_with([123, 124], args)

    @mock.patch("mozphab.commands.abandon.abandon_revisions")
    def test_abandon_main_error_propagation(self, mock_abandon_revisions):
        """Test that errors are properly propagated."""
        # Setup mock to raise error
        mock_abandon_revisions.side_effect = exceptions.Error("Test error")
        args = argparse.Namespace(revisions=[123])

        # Verify error is propagated
        with self.assertRaises(exceptions.Error) as cm:
            abandon.abandon(None, args)

        self.assertEqual(str(cm.exception), "Test error")


class TestAbandonParser(unittest.TestCase):
    """Test the argument parser setup."""

    def test_add_parser(self):
        """Test that the parser is configured correctly."""
        import argparse

        # Create a parent parser to add our command to
        parent_parser = argparse.ArgumentParser()
        subparsers = parent_parser.add_subparsers()

        # Add our abandon parser
        abandon.add_parser(subparsers)

        # Test parsing valid arguments (check_revision_id converts to int)
        args = parent_parser.parse_args(["abandon", "D123", "D124"])
        self.assertEqual(args.revisions, [123, 124])
        self.assertFalse(args.yes)

        # Test with --yes flag
        args = parent_parser.parse_args(["abandon", "--yes", "D123"])
        self.assertEqual(args.revisions, [123])
        self.assertTrue(args.yes)

        # Test with -y flag
        args = parent_parser.parse_args(["abandon", "-y", "D123"])
        self.assertEqual(args.revisions, [123])
        self.assertTrue(args.yes)
