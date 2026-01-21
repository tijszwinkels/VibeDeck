"""Tests for session management, including summary file support."""

import json
import tempfile
from pathlib import Path

import pytest

from vibedeck.sessions import (
    SessionInfo,
    add_session,
    get_session,
    remove_session,
    get_sessions,
)


@pytest.fixture
def temp_session_with_summary():
    """Create a temporary session file with a summary file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Create the session file
        session_path = project_dir / "abc123.jsonl"
        session_path.write_text(json.dumps({
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Hello, Claude!"}
        }) + "\n")

        # Create the summary file
        summary_path = project_dir / "abc123_summary.json"
        summary_data = {
            "title": "Test Session Title",
            "short_summary": "This is a short summary.",
            "executive_summary": "This is a longer executive summary with more details.",
            "session_id": "abc123",
        }
        summary_path.write_text(json.dumps(summary_data))

        yield session_path, summary_path


@pytest.fixture
def temp_session_without_summary():
    """Create a temporary session file without a summary file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Create the session file
        session_path = project_dir / "def456.jsonl"
        session_path.write_text(json.dumps({
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Hello, Claude!"}
        }) + "\n")

        yield session_path


class TestSessionInfoSummary:
    """Tests for SessionInfo summary functionality."""

    def test_loads_summary_on_init(self, temp_session_with_summary):
        """Should load summary data when creating SessionInfo."""
        session_path, summary_path = temp_session_with_summary

        info, _ = add_session(session_path)
        assert info is not None
        assert info.summary_title == "Test Session Title"
        assert info.summary_short == "This is a short summary."
        assert info.summary_executive == "This is a longer executive summary with more details."

        # Cleanup
        remove_session(info.session_id)

    def test_summary_fields_none_without_file(self, temp_session_without_summary):
        """Should have None summary fields when no summary file exists."""
        session_path = temp_session_without_summary

        info, _ = add_session(session_path)
        assert info is not None
        assert info.summary_title is None
        assert info.summary_short is None
        assert info.summary_executive is None

        # Cleanup
        remove_session(info.session_id)

    def test_get_summary_path(self, temp_session_with_summary):
        """Should return the correct summary file path."""
        session_path, summary_path = temp_session_with_summary

        info, _ = add_session(session_path)
        assert info is not None
        assert info.get_summary_path() == summary_path

        # Cleanup
        remove_session(info.session_id)

    def test_load_summary_reloads_data(self, temp_session_with_summary):
        """Should reload summary data when load_summary is called."""
        session_path, summary_path = temp_session_with_summary

        info, _ = add_session(session_path)
        assert info is not None
        assert info.summary_title == "Test Session Title"

        # Update the summary file
        new_summary = {
            "title": "Updated Title",
            "short_summary": "Updated short summary.",
            "executive_summary": "Updated executive summary.",
        }
        summary_path.write_text(json.dumps(new_summary))

        # Reload
        result = info.load_summary()
        assert result is True
        assert info.summary_title == "Updated Title"
        assert info.summary_short == "Updated short summary."
        assert info.summary_executive == "Updated executive summary."

        # Cleanup
        remove_session(info.session_id)

    def test_load_summary_returns_false_without_file(self, temp_session_without_summary):
        """Should return False when summary file doesn't exist."""
        session_path = temp_session_without_summary

        info, _ = add_session(session_path)
        assert info is not None

        # load_summary should return False for non-existent file
        result = info.load_summary()
        assert result is False

        # Cleanup
        remove_session(info.session_id)

    def test_to_dict_includes_summary(self, temp_session_with_summary):
        """Should include summary fields in to_dict output."""
        session_path, summary_path = temp_session_with_summary

        info, _ = add_session(session_path)
        assert info is not None

        data = info.to_dict()
        assert data["summaryTitle"] == "Test Session Title"
        assert data["summaryShort"] == "This is a short summary."
        assert data["summaryExecutive"] == "This is a longer executive summary with more details."

        # Cleanup
        remove_session(info.session_id)

    def test_to_dict_summary_fields_null_without_file(self, temp_session_without_summary):
        """Should have null summary fields in to_dict when no summary exists."""
        session_path = temp_session_without_summary

        info, _ = add_session(session_path)
        assert info is not None

        data = info.to_dict()
        assert data["summaryTitle"] is None
        assert data["summaryShort"] is None
        assert data["summaryExecutive"] is None

        # Cleanup
        remove_session(info.session_id)


class TestSessionInfoSummaryWithMalformedFiles:
    """Tests for handling malformed summary files."""

    def test_handles_malformed_json(self):
        """Should handle malformed JSON in summary file gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create session file
            session_path = project_dir / "bad123.jsonl"
            session_path.write_text(json.dumps({
                "type": "user",
                "timestamp": "2024-12-30T10:00:00.000Z",
                "message": {"content": "Hello"}
            }) + "\n")

            # Create malformed summary file
            summary_path = project_dir / "bad123_summary.json"
            summary_path.write_text("{ invalid json }")

            # Should not crash, just have None summary fields
            info, _ = add_session(session_path)
            assert info is not None
            assert info.summary_title is None
            assert info.summary_short is None
            assert info.summary_executive is None

            # Cleanup
            remove_session(info.session_id)

    def test_handles_missing_fields(self):
        """Should handle summary file with missing fields gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create session file
            session_path = project_dir / "partial123.jsonl"
            session_path.write_text(json.dumps({
                "type": "user",
                "timestamp": "2024-12-30T10:00:00.000Z",
                "message": {"content": "Hello"}
            }) + "\n")

            # Create summary file with only some fields
            summary_path = project_dir / "partial123_summary.json"
            summary_path.write_text(json.dumps({
                "title": "Only Title"
                # Missing short_summary and executive_summary
            }))

            info, _ = add_session(session_path)
            assert info is not None
            assert info.summary_title == "Only Title"
            assert info.summary_short is None
            assert info.summary_executive is None

            # Cleanup
            remove_session(info.session_id)
