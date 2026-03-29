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
        from vibedeck.backends.protocol import CommandSpec

        backend = MagicMock()
        backend.build_send_command.return_value = CommandSpec(
            args=["claude", "-p", "--resume", "test"],
            stdin="test message",
        )
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
    async def test_parse_response_handles_trailing_text_after_json(self, mock_backend):
        """_parse_response should parse first JSON object when text trails after it."""
        summarizer = Summarizer(backend=mock_backend)

        raw_response = (
            '{"type": "result", "result": "{\\"title\\": \\\"Test\\\"}\\nNote: trailing commentary"}'
        )

        result = summarizer._parse_response(raw_response)

        assert result is not None
        assert result.summary["title"] == "Test"

    @pytest.mark.asyncio
    async def test_parse_response_extracts_codex_format(self, mock_backend):
        """_parse_response extracts summary from Codex CLI item.completed output."""
        summarizer = Summarizer(backend=mock_backend)

        # Codex --json output: multiple JSONL lines, response in item.completed
        raw_response = (
            '{"type":"thread.started","thread_id":"abc123"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message",'
            '"text":"{\\"title\\": \\"Codex Test\\", \\"short_summary\\": \\"A test\\"}"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":50}}'
        )

        result = summarizer._parse_response(raw_response)

        assert result is not None
        assert result.summary["title"] == "Codex Test"
        assert result.summary["short_summary"] == "A test"

    @pytest.mark.asyncio
    async def test_parse_response_extracts_pi_json_mode(self, mock_backend):
        """_parse_response extracts summary from Pi JSON mode output."""
        mock_backend.cli_command = "pi"
        summarizer = Summarizer(backend=mock_backend)

        raw_response = (
            '{"type":"session","version":3,"id":"abc123","timestamp":"2026-01-15T12:00:00.000Z","cwd":"/tmp"}\n'
            '{"type":"agent_start"}\n'
            '{"type":"turn_end","message":{"role":"assistant","content":[{"type":"text","text":"{\\"title\\":\\"Pi Test\\",\\"short_summary\\":\\"A test\\"}"}]}}\n'
        )

        result = summarizer._parse_response(raw_response)

        assert result is not None
        assert result.summary["title"] == "Pi Test"
        assert result.summary["short_summary"] == "A test"

    @pytest.mark.asyncio
    async def test_parse_response_pi_ignores_thinking_signature_json(self, mock_backend):
        """Pi parser should ignore thinking blocks containing JSON-like signatures."""
        mock_backend.cli_command = "pi"
        summarizer = Summarizer(backend=mock_backend)

        raw_response = (
            '{"type":"session","version":3,"id":"abc123","timestamp":"2026-01-15T12:00:00.000Z","cwd":"/tmp"}\n'
            '{"type":"turn_end","message":{"role":"assistant","content":['
            '{"type":"thinking","thinking":"","thinkingSignature":"{\\"id\\":\\"sig\\",\\"summary\\":[]}"},'
            '{"type":"text","text":"{\\"title\\":\\"Pi Test\\",\\"short_summary\\":\\"A test\\"}"}]}}\n'
        )

        result = summarizer._parse_response(raw_response)

        assert result is not None
        assert result.summary["title"] == "Pi Test"
        assert result.summary["short_summary"] == "A test"

    @pytest.mark.asyncio
    async def test_parse_response_pi_skips_user_turn_end_messages(self, mock_backend):
        """Pi parser should not treat user turn_end payloads as the summary response."""
        mock_backend.cli_command = "pi"
        summarizer = Summarizer(backend=mock_backend)

        raw_response = (
            '{"type":"session","version":3,"id":"abc123","timestamp":"2026-01-15T12:00:00.000Z","cwd":"/tmp"}\n'
            '{"type":"turn_end","message":{"role":"user","content":[{"type":"text","text":"{\\"title\\":\\"short title here\\"}"}]}}\n'
            '{"type":"turn_end","message":{"role":"assistant","content":[{"type":"text","text":"{\\"title\\":\\"Real Title\\",\\"short_summary\\":\\"Real summary\\"}"}]}}\n'
        )

        result = summarizer._parse_response(raw_response)

        assert result is not None
        assert result.summary["title"] == "Real Title"
        assert result.summary["short_summary"] == "Real summary"

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


class TestSummarizerCommandBuilding:
    """Tests for backend-specific summary CLI arguments."""

    @pytest.mark.asyncio
    async def test_codex_summary_uses_new_session_not_resume(self, tmp_path):
        """Codex summaries should use a fresh ephemeral session, not resume."""
        from vibedeck.backends.protocol import CommandSpec

        backend = MagicMock()
        backend.cli_command = "codex"
        backend.build_new_session_command.return_value = CommandSpec(
            args=["codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "-"],
            stdin="summary prompt with transcript",
        )

        # Create a Codex-format session file
        session_file = tmp_path / "rollout-2026-01-15T12-00-00-test-id.jsonl"
        entries = [
            {"type": "response_item", "timestamp": "2026-01-15T12:00:01Z",
             "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": "hello codex"}]}},
            {"type": "response_item", "timestamp": "2026-01-15T12:00:02Z",
             "payload": {"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": "hi there"}]}},
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        session = MagicMock()
        session.session_id = "test-id"
        session.project_path = str(tmp_path)
        session.path = session_file
        session.tailer.get_first_timestamp.return_value = "2026-01-15T12:00:00"

        captured = {}

        class _Proc:
            returncode = 0

            def __init__(self):
                self.stdin = MagicMock()
                self.stdin.write = MagicMock()
                self.stdin.drain = AsyncMock()
                self.stdin.close = MagicMock()
                self.stdin.wait_closed = AsyncMock()

            async def communicate(self):
                return (
                    b'{"type":"result","result":"{\\"title\\":\\"Test\\"}"}',
                    b"",
                )

        async def _fake_create_subprocess_exec(*args, **kwargs):
            captured["args"] = list(args)
            captured["kwargs"] = kwargs
            return _Proc()

        summarizer = Summarizer(backend=backend)

        with patch("asyncio.create_subprocess_exec", _fake_create_subprocess_exec):
            result = await summarizer.summarize(session, model="gpt-5.4")

        assert result.success is True
        # Should use build_new_session_command, NOT build_send_command
        backend.build_new_session_command.assert_called_once()
        backend.build_send_command.assert_not_called()
        # Should NOT have "resume" in command args
        assert "resume" not in captured["args"]
        assert "--ephemeral" in captured["args"]
        assert "--json" in captured["args"]
        assert "--no-session-persistence" not in captured["args"]
        # Model should always be overridden to gpt-5.4-mini for Codex,
        # regardless of what was passed (e.g. "haiku" from Claude config)
        assert "--model" in captured["args"]
        model_idx = captured["args"].index("--model")
        assert captured["args"][model_idx + 1] == "gpt-5.4-mini"

    @pytest.mark.asyncio
    async def test_codex_summary_includes_transcript_in_prompt(self, tmp_path):
        """Codex summaries should include the session transcript in the prompt."""
        from vibedeck.backends.protocol import CommandSpec

        backend = MagicMock()
        backend.cli_command = "codex"

        # Capture the message passed to build_new_session_command
        captured_message = {}

        def fake_build_new(message, **kwargs):
            captured_message["message"] = message
            return CommandSpec(
                args=["codex", "exec", "--json", "-"],
                stdin=message,
            )

        backend.build_new_session_command.side_effect = fake_build_new

        # Create a Codex-format session with recognizable content
        session_file = tmp_path / "rollout-2026-01-15T12-00-00-test-id.jsonl"
        entries = [
            {"type": "response_item", "timestamp": "2026-01-15T12:00:01Z",
             "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": "implement the frobnicator"}]}},
            {"type": "response_item", "timestamp": "2026-01-15T12:00:02Z",
             "payload": {"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": "I'll create the frobnicator now."}]}},
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        session = MagicMock()
        session.session_id = "test-id"
        session.project_path = str(tmp_path)
        session.path = session_file
        session.tailer.get_first_timestamp.return_value = "2026-01-15T12:00:00"

        class _Proc:
            returncode = 0

            def __init__(self):
                self.stdin = MagicMock()
                self.stdin.write = MagicMock()
                self.stdin.drain = AsyncMock()
                self.stdin.close = MagicMock()
                self.stdin.wait_closed = AsyncMock()

            async def communicate(self):
                return (
                    b'{"type":"result","result":"{\\"title\\":\\"Test\\"}"}',
                    b"",
                )

        async def _fake_create_subprocess_exec(*args, **kwargs):
            return _Proc()

        summarizer = Summarizer(backend=backend)

        with patch("asyncio.create_subprocess_exec", _fake_create_subprocess_exec):
            await summarizer.summarize(session)

        # The prompt should contain the transcript text
        msg = captured_message["message"]
        assert "implement the frobnicator" in msg
        assert "Codex Transcript" in msg
        # And still contain the summary instructions
        assert "Summarize this coding session" in msg

    @pytest.mark.asyncio
    async def test_pi_summary_uses_ephemeral_json_mode_with_transcript(self, tmp_path):
        """Pi summaries should use an ephemeral JSON session and embed the transcript."""
        from vibedeck.backends.protocol import CommandSpec

        backend = MagicMock()
        backend.cli_command = "pi"

        captured_message = {}

        def fake_build_new(message, **kwargs):
            captured_message["message"] = message
            captured_message["kwargs"] = kwargs
            return CommandSpec(args=["pi", "-p"], stdin=message)

        backend.build_new_session_command.side_effect = fake_build_new

        session_file = tmp_path / "2026-03-27T12-10-22-476Z_test-id.jsonl"
        entries = [
            {"type": "session", "version": 3, "id": "test-id", "timestamp": "2026-03-27T12:00:00.000Z", "cwd": "/tmp"},
            {"type": "message", "id": "m1", "parentId": None, "timestamp": "2026-03-27T12:00:01.000Z", "message": {"role": "user", "content": [{"type": "text", "text": "summarize me"}]}},
            {"type": "message", "id": "m2", "parentId": "m1", "timestamp": "2026-03-27T12:00:02.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "sure"}], "model": "openai/gpt-5.4-mini", "stopReason": "stop"}},
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        session = MagicMock()
        session.session_id = "test-id"
        session.project_path = str(tmp_path)
        session.path = session_file
        session.tailer.get_first_timestamp.return_value = "2026-03-27T12:00:00"

        class _Proc:
            returncode = 0

            def __init__(self):
                self.stdin = MagicMock()
                self.stdin.write = MagicMock()
                self.stdin.drain = AsyncMock()
                self.stdin.close = MagicMock()
                self.stdin.wait_closed = AsyncMock()

            async def communicate(self):
                return (
                    b'{"type":"session","version":3,"id":"abc","timestamp":"2026-03-27T12:00:00.000Z","cwd":"/tmp"}\n'
                    b'{"type":"agent_end","messages":[{"role":"assistant","content":[{"type":"text","text":"{\\"title\\":\\"Pi Summary\\",\\"short_summary\\":\\"ok\\"}"}]}]}\n',
                    b"",
                )

        async def _fake_create_subprocess_exec(*args, **kwargs):
            captured_message["args"] = list(args)
            captured_message["process_kwargs"] = kwargs
            return _Proc()

        summarizer = Summarizer(backend=backend)

        with patch("asyncio.create_subprocess_exec", _fake_create_subprocess_exec):
            result = await summarizer.summarize(session, model="openai/gpt-5.4-mini")

        assert result.success is True
        backend.build_new_session_command.assert_called_once()
        assert "--session" not in captured_message["args"]
        assert "--no-session" in captured_message["args"]
        assert "--mode" in captured_message["args"]
        mode_idx = captured_message["args"].index("--mode")
        assert captured_message["args"][mode_idx + 1] == "json"
        assert "--no-tools" in captured_message["args"]
        msg = captured_message["message"]
        assert "summarize me" in msg
        assert "Pi Transcript" in msg
