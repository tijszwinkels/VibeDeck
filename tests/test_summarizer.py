"""Tests for the summarizer module."""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibedeck.summarizer import (
    DEFAULT_OUTPUT_KEYS,
    DEFAULT_PROMPT_TEMPLATE,
    IdleTracker,
    LogWriter,
    Summarizer,
    SummaryResult,
    SummaryState,
    TrackedSession,
    format_prompt,
    get_prompt_template,
)


class TestPromptConfig:
    """Tests for prompt configuration."""

    def test_default_prompt_template_has_placeholders(self):
        """Default prompt template contains required placeholders."""
        assert "{session_id}" in DEFAULT_PROMPT_TEMPLATE
        assert "{project_path}" in DEFAULT_PROMPT_TEMPLATE
        assert "{generated_at}" in DEFAULT_PROMPT_TEMPLATE
        assert "{session_started_at}" in DEFAULT_PROMPT_TEMPLATE

    def test_format_prompt_replaces_placeholders(self):
        """format_prompt replaces all placeholders."""
        result = format_prompt(
            template="{session_id} - {project_path} - {generated_at} - {session_started_at}",
            session_id="test-id",
            project_path="/test/path",
            generated_at="2026-01-15T12:00:00",
            session_started_at="2026-01-15T11:00:00",
        )
        assert result == "test-id - /test/path - 2026-01-15T12:00:00 - 2026-01-15T11:00:00"

    def test_format_prompt_handles_none_project_path(self):
        """format_prompt handles None project_path."""
        result = format_prompt(
            template="{project_path}",
            session_id="test-id",
            project_path=None,
            generated_at="2026-01-15T12:00:00",
            session_started_at="2026-01-15T11:00:00",
        )
        assert result == "Unknown"

    def test_get_prompt_template_returns_default(self):
        """get_prompt_template returns default when no custom prompt."""
        result = get_prompt_template()
        assert result == DEFAULT_PROMPT_TEMPLATE

    def test_get_prompt_template_returns_custom_prompt(self):
        """get_prompt_template returns custom prompt when provided."""
        custom = "Custom prompt"
        result = get_prompt_template(prompt=custom)
        assert result == custom

    def test_get_prompt_template_reads_file(self, tmp_path):
        """get_prompt_template reads from file when provided."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("File prompt")
        result = get_prompt_template(prompt_file=prompt_file)
        assert result == "File prompt"

    def test_get_prompt_template_prompt_takes_precedence(self, tmp_path):
        """get_prompt_template prefers inline prompt over file."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("File prompt")
        result = get_prompt_template(prompt="Inline prompt", prompt_file=prompt_file)
        assert result == "Inline prompt"


class TestLogWriter:
    """Tests for LogWriter."""

    def test_write_entry_without_log_path(self):
        """write_entry returns True when no log path configured."""
        writer = LogWriter(log_path=None)
        result = writer.write_entry({"title": "Test"})
        assert result is True

    def test_write_entry_creates_file(self, tmp_path):
        """write_entry creates JSONL file."""
        log_path = tmp_path / "summaries.jsonl"
        writer = LogWriter(log_path=log_path)
        result = writer.write_entry({"title": "Test", "summary": "Test summary"})

        assert result is True
        assert log_path.exists()

        content = log_path.read_text()
        data = json.loads(content.strip())
        assert data["title"] == "Test"

    def test_write_entry_appends(self, tmp_path):
        """write_entry appends to existing file."""
        log_path = tmp_path / "summaries.jsonl"
        writer = LogWriter(log_path=log_path)

        writer.write_entry({"title": "First"})
        writer.write_entry({"title": "Second"})

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["title"] == "First"
        assert json.loads(lines[1])["title"] == "Second"

    def test_write_entry_filters_keys(self, tmp_path):
        """write_entry filters to specified keys."""
        log_path = tmp_path / "summaries.jsonl"
        writer = LogWriter(log_path=log_path, log_keys=["title"])

        writer.write_entry({"title": "Test", "extra": "Ignored"})

        content = log_path.read_text()
        data = json.loads(content.strip())
        assert "title" in data
        assert "extra" not in data

    def test_write_entry_uses_default_keys(self, tmp_path):
        """write_entry uses DEFAULT_OUTPUT_KEYS when log_keys is None."""
        log_path = tmp_path / "summaries.jsonl"
        writer = LogWriter(log_path=log_path, log_keys=None)

        # This will use DEFAULT_OUTPUT_KEYS which includes title but not executive_summary
        entry = {
            "title": "Test",
            "short_summary": "Summary",
            "executive_summary": "Long summary",
        }
        writer.write_entry(entry)

        content = log_path.read_text()
        data = json.loads(content.strip())
        assert "title" in data
        assert "short_summary" in data
        # executive_summary is not in DEFAULT_OUTPUT_KEYS
        assert "executive_summary" not in data

    def test_write_entry_creates_parent_dirs(self, tmp_path):
        """write_entry creates parent directories."""
        log_path = tmp_path / "nested" / "dir" / "summaries.jsonl"
        writer = LogWriter(log_path=log_path)

        result = writer.write_entry({"title": "Test"})

        assert result is True
        assert log_path.exists()


class TestTrackedSession:
    """Tests for TrackedSession state machine."""

    def test_initial_state_is_none(self):
        """TrackedSession starts with NONE state."""
        session = TrackedSession(session_id="test")
        assert session.state == SummaryState.NONE

    def test_mark_active_from_none(self):
        """mark_active transitions from NONE to PENDING."""
        session = TrackedSession(session_id="test")
        session.mark_active()
        assert session.state == SummaryState.PENDING

    def test_mark_active_updates_timestamp(self):
        """mark_active updates last_activity timestamp."""
        session = TrackedSession(session_id="test")
        before = datetime.now()
        session.mark_active()
        after = datetime.now()

        assert before <= session.last_activity <= after

    def test_mark_active_resets_from_done(self):
        """mark_active transitions from DONE back to PENDING."""
        session = TrackedSession(session_id="test")
        session.mark_done()
        session.mark_active()
        assert session.state == SummaryState.PENDING

    def test_mark_summarizing_sets_timestamp(self):
        """mark_summarizing sets summary_started_at."""
        session = TrackedSession(session_id="test")
        session.mark_summarizing()
        assert session.state == SummaryState.SUMMARIZING
        assert session.summary_started_at is not None

    def test_mark_done_clears_timestamp(self):
        """mark_done clears summary_started_at."""
        session = TrackedSession(session_id="test")
        session.mark_summarizing()
        session.mark_done()
        assert session.state == SummaryState.DONE
        assert session.summary_started_at is None

    def test_seconds_since_activity(self):
        """seconds_since_activity returns correct value."""
        session = TrackedSession(session_id="test")
        session.mark_active()
        # Small sleep to ensure some time passes
        import time
        time.sleep(0.01)
        elapsed = session.seconds_since_activity()
        assert elapsed >= 0.01

    def test_seconds_since_summary_started_none_when_not_summarizing(self):
        """seconds_since_summary_started returns None when not summarizing."""
        session = TrackedSession(session_id="test")
        assert session.seconds_since_summary_started() is None


class TestIdleTracker:
    """Tests for IdleTracker."""

    @pytest.fixture
    def mock_summarize_callback(self):
        """Create a mock summarize callback."""
        return AsyncMock(return_value=True)

    @pytest.fixture
    def mock_get_session(self):
        """Create a mock get_session callback."""
        mock_session = MagicMock()
        mock_session.session_id = "test-id"
        return MagicMock(return_value=mock_session)

    @pytest.mark.asyncio
    async def test_on_session_activity_creates_tracked_session(
        self, mock_summarize_callback, mock_get_session
    ):
        """on_session_activity creates a new TrackedSession."""
        tracker = IdleTracker(
            idle_threshold_seconds=60,
            summarize_callback=mock_summarize_callback,
            get_session_callback=mock_get_session,
        )

        tracker.on_session_activity("test-id")

        assert "test-id" in tracker.sessions
        assert tracker.sessions["test-id"].state == SummaryState.PENDING

        # Clean up
        tracker.shutdown()

    @pytest.mark.asyncio
    async def test_on_session_activity_resets_timer(
        self, mock_summarize_callback, mock_get_session
    ):
        """on_session_activity resets the idle timer."""
        tracker = IdleTracker(
            idle_threshold_seconds=60,
            summarize_callback=mock_summarize_callback,
            get_session_callback=mock_get_session,
        )

        tracker.on_session_activity("test-id")
        first_timer = tracker._timers.get("test-id")

        tracker.on_session_activity("test-id")
        second_timer = tracker._timers.get("test-id")

        # Timer should be replaced (first one cancelled)
        assert first_timer is not second_timer

        # Clean up
        tracker.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_timers(self, mock_summarize_callback, mock_get_session):
        """shutdown cancels all pending timers."""
        tracker = IdleTracker(
            idle_threshold_seconds=60,
            summarize_callback=mock_summarize_callback,
            get_session_callback=mock_get_session,
        )

        tracker.on_session_activity("test-1")
        tracker.on_session_activity("test-2")

        tracker.shutdown()

        assert len(tracker._timers) == 0
        assert tracker._shutdown is True


class TestSummarizer:
    """Tests for Summarizer."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock backend."""
        backend = MagicMock()
        backend.build_send_command.return_value = ["claude", "-p", "test"]
        return backend

    @pytest.fixture
    def mock_session(self, tmp_path):
        """Create a mock session."""
        session_file = tmp_path / "test-session.jsonl"
        session_file.write_text('{"type": "user", "message": "hello"}')

        session = MagicMock()
        session.session_id = "test-id"
        session.project_path = str(tmp_path)
        session.started_at = "2026-01-15T12:00:00"
        session.path = session_file
        return session

    def test_summarizer_init(self, mock_backend):
        """Summarizer initializes correctly."""
        summarizer = Summarizer(backend=mock_backend)
        assert summarizer.backend == mock_backend
        assert summarizer.log_writer is not None

    def test_summarizer_with_custom_log_writer(self, mock_backend, tmp_path):
        """Summarizer accepts custom log writer."""
        log_writer = LogWriter(log_path=tmp_path / "custom.jsonl")
        summarizer = Summarizer(backend=mock_backend, log_writer=log_writer)
        assert summarizer.log_writer == log_writer

    @pytest.mark.asyncio
    async def test_parse_response_extracts_summary(self, mock_backend):
        """_parse_response extracts summary from Claude CLI output."""
        summarizer = Summarizer(backend=mock_backend)

        # Simulate Claude CLI JSON output
        raw_response = '{"type": "result", "result": "{\\"title\\": \\"Test\\", \\"summary\\": \\"Test summary\\"}"}'

        result = summarizer._parse_response(raw_response)

        assert result is not None
        assert result.summary["title"] == "Test"

    @pytest.mark.asyncio
    async def test_parse_response_handles_markdown_wrapped_json(self, mock_backend):
        """_parse_response handles JSON wrapped in markdown code blocks."""
        summarizer = Summarizer(backend=mock_backend)

        raw_response = '{"type": "result", "result": "```json\\n{\\"title\\": \\"Test\\"}\\n```"}'

        result = summarizer._parse_response(raw_response)

        assert result is not None
        assert result.summary["title"] == "Test"

    @pytest.mark.asyncio
    async def test_parse_response_returns_none_for_invalid(self, mock_backend):
        """_parse_response returns None for invalid response."""
        summarizer = Summarizer(backend=mock_backend)

        result = summarizer._parse_response("invalid response")

        assert result is None

    def test_write_summary_json(self, mock_backend, mock_session, tmp_path):
        """_write_summary_json writes summary to file."""
        summarizer = Summarizer(backend=mock_backend)

        summary = {"title": "Test", "summary": "Test summary"}
        raw_response = '{"type": "result"}'

        path = summarizer._write_summary_json(mock_session, summary, raw_response)

        assert path is not None
        assert path.exists()

        content = json.loads(path.read_text())
        assert content["title"] == "Test"
        assert content["raw_response"] == raw_response


class TestDefaultOutputKeys:
    """Tests for DEFAULT_OUTPUT_KEYS."""

    def test_includes_title(self):
        """DEFAULT_OUTPUT_KEYS includes title."""
        assert "title" in DEFAULT_OUTPUT_KEYS

    def test_includes_short_summary(self):
        """DEFAULT_OUTPUT_KEYS includes short_summary."""
        assert "short_summary" in DEFAULT_OUTPUT_KEYS

    def test_excludes_executive_summary(self):
        """DEFAULT_OUTPUT_KEYS excludes executive_summary by default."""
        assert "executive_summary" not in DEFAULT_OUTPUT_KEYS

    def test_includes_timestamps(self):
        """DEFAULT_OUTPUT_KEYS includes timestamp fields."""
        assert "summary_generated_at" in DEFAULT_OUTPUT_KEYS
        assert "session_started_at" in DEFAULT_OUTPUT_KEYS
        assert "session_last_updated_at" in DEFAULT_OUTPUT_KEYS
