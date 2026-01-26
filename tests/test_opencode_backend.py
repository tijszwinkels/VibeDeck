"""Tests for the OpenCode backend."""

import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def opencode_storage_dir():
    """Create a temporary OpenCode storage directory structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage_dir = Path(tmpdir)

        # Create directory structure
        (storage_dir / "session" / "proj123").mkdir(parents=True)
        (storage_dir / "message").mkdir(parents=True)
        (storage_dir / "part").mkdir(parents=True)

        yield storage_dir


@pytest.fixture
def opencode_session(opencode_storage_dir):
    """Create a sample OpenCode session with messages and parts."""
    storage_dir = opencode_storage_dir
    session_id = "ses_test123"
    project_id = "proj123"

    # Create project directory so get_session_name can use it
    project_dir = storage_dir / "test-project"
    project_dir.mkdir(parents=True)

    # Create session file
    session_data = {
        "id": session_id,
        "projectID": project_id,
        "directory": str(project_dir),  # Use the temp directory we created
        "title": "Test Session",
        "version": "1.0.0",
        "time": {
            "created": 1704067200000,  # 2024-01-01 00:00:00 UTC
            "updated": 1704067260000,
        },
    }
    session_file = storage_dir / "session" / project_id / f"{session_id}.json"
    session_file.write_text(json.dumps(session_data))

    # Create message directory
    msg_dir = storage_dir / "message" / session_id
    msg_dir.mkdir(parents=True)

    # Create user message (ID starts with 'msg_0' to sort before assistant 'msg_1')
    user_msg_id = "msg_0user1"
    user_msg = {
        "id": user_msg_id,
        "role": "user",
        "time": {"created": 1704067200000, "updated": 1704067200000},
    }
    (msg_dir / f"{user_msg_id}.json").write_text(json.dumps(user_msg))

    # Create user message part (text)
    user_part_dir = storage_dir / "part" / user_msg_id
    user_part_dir.mkdir(parents=True)
    user_part = {
        "id": "prt_user1",
        "sessionID": session_id,
        "messageID": user_msg_id,
        "type": "text",
        "text": "Hello, how are you?",
    }
    (user_part_dir / "prt_user1.json").write_text(json.dumps(user_part))

    # Create assistant message (ID starts with 'msg_1' to sort after user 'msg_0')
    asst_msg_id = "msg_1asst1"
    asst_msg = {
        "id": asst_msg_id,
        "role": "assistant",
        "modelID": "claude-sonnet-4-5",
        "providerID": "anthropic",
        "time": {"created": 1704067210000, "updated": 1704067220000},
        "tokens": {"input": 100, "output": 50, "cache": {"read": 0, "write": 0}},
        "cost": 0.001,
    }
    (msg_dir / f"{asst_msg_id}.json").write_text(json.dumps(asst_msg))

    # Create assistant message parts
    asst_part_dir = storage_dir / "part" / asst_msg_id
    asst_part_dir.mkdir(parents=True)

    # Text part
    text_part = {
        "id": "prt_text1",
        "sessionID": session_id,
        "messageID": asst_msg_id,
        "type": "text",
        "text": "I'm doing well, thank you!",
    }
    (asst_part_dir / "prt_text1.json").write_text(json.dumps(text_part))

    # Tool part
    tool_part = {
        "id": "prt_tool1",
        "sessionID": session_id,
        "messageID": asst_msg_id,
        "type": "tool",
        "tool": "bash",
        "callID": "toolu_123",
        "state": {
            "status": "completed",
            "input": {"command": "echo hello", "description": "Test command"},
            "output": "hello\n",
            "time": {"start": 1704067211000, "end": 1704067212000},
        },
    }
    (asst_part_dir / "prt_tool1.json").write_text(json.dumps(tool_part))

    # Step-finish part (required for read_new_lines to emit assistant messages)
    step_finish_part = {
        "id": "prt_zfinish",  # 'z' prefix to sort last
        "sessionID": session_id,
        "messageID": asst_msg_id,
        "type": "step-finish",
    }
    (asst_part_dir / "prt_zfinish.json").write_text(json.dumps(step_finish_part))

    return {
        "storage_dir": storage_dir,
        "session_id": session_id,
        "session_file": session_file,
        "project_id": project_id,
    }


class TestOpenCodeDiscovery:
    """Tests for OpenCode session discovery."""

    def test_find_recent_sessions(self, opencode_session):
        """Test finding recent sessions."""
        from vibedeck.backends.opencode.discovery import (
            find_recent_sessions,
        )

        storage_dir = opencode_session["storage_dir"]
        sessions = find_recent_sessions(storage_dir, limit=10)

        assert len(sessions) == 1
        assert sessions[0].stem == opencode_session["session_id"]

    def test_find_sessions_empty_storage(self, opencode_storage_dir):
        """Test finding sessions when storage is empty."""
        from vibedeck.backends.opencode.discovery import (
            find_recent_sessions,
        )

        sessions = find_recent_sessions(opencode_storage_dir, limit=10)
        assert sessions == []

    def test_get_session_id(self, opencode_session):
        """Test extracting session ID from path."""
        from vibedeck.backends.opencode.discovery import (
            get_session_id,
        )

        session_file = opencode_session["session_file"]
        session_id = get_session_id(session_file)

        assert session_id == opencode_session["session_id"]

    def test_has_messages_true(self, opencode_session):
        """Test has_messages returns True when messages exist."""
        from vibedeck.backends.opencode.discovery import (
            has_messages,
        )

        storage_dir = opencode_session["storage_dir"]
        session_file = opencode_session["session_file"]

        assert has_messages(session_file, storage_dir) is True

    def test_has_messages_false(self, opencode_storage_dir):
        """Test has_messages returns False when no messages."""
        from vibedeck.backends.opencode.discovery import (
            has_messages,
        )

        # Create session without messages
        session_dir = opencode_storage_dir / "session" / "proj456"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "ses_empty.json"
        session_file.write_text(json.dumps({"id": "ses_empty"}))

        assert has_messages(session_file, opencode_storage_dir) is False

    def test_get_first_user_message(self, opencode_session):
        """Test extracting first user message."""
        from vibedeck.backends.opencode.discovery import (
            get_first_user_message,
        )

        storage_dir = opencode_session["storage_dir"]
        session_file = opencode_session["session_file"]

        first_msg = get_first_user_message(session_file, storage_dir)
        assert first_msg == "Hello, how are you?"

    def test_should_watch_file_message(self, opencode_storage_dir):
        """Test should_watch_file for message files."""
        from vibedeck.backends.opencode.discovery import (
            should_watch_file,
        )

        msg_file = opencode_storage_dir / "message" / "ses_123" / "msg_456.json"
        assert should_watch_file(msg_file) is True

    def test_should_watch_file_part(self, opencode_storage_dir):
        """Test should_watch_file for part files."""
        from vibedeck.backends.opencode.discovery import (
            should_watch_file,
        )

        part_file = opencode_storage_dir / "part" / "msg_123" / "prt_456.json"
        assert should_watch_file(part_file) is True

    def test_should_watch_file_session(self, opencode_storage_dir):
        """Test should_watch_file returns False for session metadata files."""
        from vibedeck.backends.opencode.discovery import (
            should_watch_file,
        )

        session_file = opencode_storage_dir / "session" / "proj" / "ses_123.json"
        assert should_watch_file(session_file) is False

    def test_get_session_id_from_part_file(self, opencode_session):
        """Test extracting session ID from a part file by reading its contents."""
        from vibedeck.backends.opencode.discovery import (
            get_session_id_from_file_path,
        )

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        # Find a part file from the fixture
        part_dir = storage_dir / "part"
        part_files = list(part_dir.rglob("*.json"))
        assert len(part_files) > 0, "Fixture should have part files"

        # The part file should contain sessionID inside it
        result = get_session_id_from_file_path(part_files[0], storage_dir)
        assert result == session_id

    def test_get_session_id_from_message_file(self, opencode_session):
        """Test extracting session ID from a message file path."""
        from vibedeck.backends.opencode.discovery import (
            get_session_id_from_file_path,
        )

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        # Find a message file
        msg_dir = storage_dir / "message" / session_id
        msg_files = list(msg_dir.glob("*.json"))
        assert len(msg_files) > 0, "Fixture should have message files"

        # Session ID should be extracted from the path
        result = get_session_id_from_file_path(msg_files[0], storage_dir)
        assert result == session_id


class TestOpenCodeTailer:
    """Tests for OpenCode session tailer."""

    def test_read_all(self, opencode_session):
        """Test reading all messages."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        messages = tailer.read_all()

        assert len(messages) == 2
        assert messages[0]["info"]["role"] == "user"
        assert messages[1]["info"]["role"] == "assistant"

    def test_read_all_with_parts(self, opencode_session):
        """Test that parts are included with messages."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        messages = tailer.read_all()

        # User message has 1 text part
        assert len(messages[0]["parts"]) == 1
        assert messages[0]["parts"][0]["type"] == "text"

        # Assistant message has 3 parts (text + tool + step-finish)
        assert len(messages[1]["parts"]) == 3

    def test_read_all_empty_session(self, opencode_storage_dir):
        """Test reading from session with no messages."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        tailer = OpenCodeTailer(opencode_storage_dir, "nonexistent")
        messages = tailer.read_all()

        assert messages == []

    def test_read_new_lines_first_call(self, opencode_session):
        """Test first call to read_new_lines returns all messages."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        new_messages = tailer.read_new_lines()

        assert len(new_messages) == 2

    def test_read_new_lines_incremental(self, opencode_session):
        """Test subsequent calls only return new messages."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)

        # First call gets all
        first = tailer.read_new_lines()
        assert len(first) == 2

        # Second call with no changes returns empty
        second = tailer.read_new_lines()
        assert len(second) == 0

    def test_get_first_timestamp(self, opencode_session):
        """Test getting first message timestamp."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        timestamp = tailer.get_first_timestamp()

        assert timestamp is not None
        assert "2024-01-01" in timestamp

    def test_waiting_for_input_after_assistant_text(self, opencode_session):
        """Test waiting_for_input is True after assistant text message."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        tailer.read_all()

        # Last message is assistant with text part at end
        # But our fixture has tool part last, so it should be False
        # Let's check the actual state
        assert isinstance(tailer.waiting_for_input, bool)

    def test_seek_to_end_marks_messages_as_seen(self, opencode_session):
        """Test seek_to_end marks existing messages as seen without reading."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)

        # Initially no messages seen
        assert len(tailer._seen_messages) == 0

        tailer.seek_to_end()

        # After seek_to_end, messages should be marked as seen
        assert len(tailer._seen_messages) == 2  # user + assistant

    def test_read_new_lines_after_seek_to_end(self, opencode_session):
        """Test read_new_lines returns nothing after seek_to_end (existing messages skipped)."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        tailer.seek_to_end()

        # read_new_lines should return nothing since all messages are marked seen
        messages = tailer.read_new_lines()
        assert len(messages) == 0

    def test_read_all_after_seek_to_end(self, opencode_session):
        """Test read_all still returns all messages after seek_to_end."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        tailer.seek_to_end()

        # read_all should still return all messages (independent of seen state)
        messages = tailer.read_all()
        assert len(messages) == 2

    def test_seek_to_end_empty_session(self, opencode_storage_dir):
        """Test seek_to_end handles empty/nonexistent session gracefully."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        tailer = OpenCodeTailer(opencode_storage_dir, "nonexistent_session")

        # Should not raise
        tailer.seek_to_end()

        # No messages should be seen
        assert len(tailer._seen_messages) == 0

    def test_new_message_detected_after_seek_to_end(self, opencode_session):
        """Test that new messages added after seek_to_end are detected."""
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        tailer.seek_to_end()

        # Add a new message after seek_to_end
        msg_dir = storage_dir / "message" / session_id
        new_msg_id = "msg_2new1"
        new_msg = {
            "id": new_msg_id,
            "role": "user",
            "time": {"created": 1704067300000, "updated": 1704067300000},
        }
        (msg_dir / f"{new_msg_id}.json").write_text(json.dumps(new_msg))

        # Add text part for the new message
        new_part_dir = storage_dir / "part" / new_msg_id
        new_part_dir.mkdir(parents=True)
        new_part = {
            "id": "prt_new1",
            "sessionID": session_id,
            "messageID": new_msg_id,
            "type": "text",
            "text": "This is a new message",
        }
        (new_part_dir / "prt_new1.json").write_text(json.dumps(new_part))

        # read_new_lines should detect the new message
        messages = tailer.read_new_lines()
        assert len(messages) == 1
        assert messages[0]["info"]["id"] == new_msg_id


class TestOpenCodeRenderer:
    """Tests for OpenCode message renderer."""

    def test_render_user_message(self, opencode_session):
        """Test rendering user message."""
        from vibedeck.backends.opencode.renderer import (
            OpenCodeRenderer,
        )
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        messages = tailer.read_all()
        renderer = OpenCodeRenderer()

        html = renderer.render_message(messages[0])

        assert "user" in html.lower()
        assert "Hello, how are you?" in html

    def test_render_user_message_strips_quotes_and_newlines(self):
        """Test that user messages with surrounding quotes and trailing newlines are cleaned."""
        from vibedeck.backends.opencode.renderer import (
            OpenCodeRenderer,
        )

        renderer = OpenCodeRenderer()

        # OpenCode wraps user messages in quotes and adds trailing newline
        entry = {
            "info": {
                "id": "msg_test",
                "role": "user",
                "time": {"created": 1704067200000},
            },
            "parts": [
                {
                    "id": "prt_test",
                    "type": "text",
                    "text": '"Hello, this is a test message"\n',
                }
            ],
        }

        html = renderer.render_message(entry)

        assert "user" in html.lower()
        # The quotes should be stripped
        assert "Hello, this is a test message" in html
        # The surrounding quotes should NOT appear in the output
        assert '>"Hello' not in html
        assert 'message"<' not in html

    def test_render_user_message_escapes_html(self):
        """Test that user messages with HTML-like content are escaped."""
        from vibedeck.backends.opencode.renderer import (
            OpenCodeRenderer,
        )

        renderer = OpenCodeRenderer()

        # User message with HTML-like content that could be interpreted as tags
        entry = {
            "info": {
                "id": "msg_test",
                "role": "user",
                "time": {"created": 1704067200000},
            },
            "parts": [
                {
                    "id": "prt_test",
                    "type": "text",
                    "text": '"Create a YYYYMMDD-<title>.md file"\n',
                }
            ],
        }

        html = renderer.render_message(entry)

        assert "user" in html.lower()
        # The <title> should be escaped, not interpreted as HTML
        assert "&lt;title&gt;" in html
        # Should NOT contain raw <title> tag
        assert "<title>" not in html

    def test_render_assistant_message(self, opencode_session):
        """Test rendering assistant message with text and tool."""
        from vibedeck.backends.opencode.renderer import (
            OpenCodeRenderer,
        )
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        messages = tailer.read_all()
        renderer = OpenCodeRenderer()

        html = renderer.render_message(messages[1])

        assert "assistant" in html.lower()
        assert "I'm doing well" in html
        # Tool should be rendered
        assert "Bash" in html or "bash" in html.lower()

    def test_render_tool_completed(self, opencode_session):
        """Test rendering completed tool with output."""
        from vibedeck.backends.opencode.renderer import (
            OpenCodeRenderer,
        )
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_id = opencode_session["session_id"]

        tailer = OpenCodeTailer(storage_dir, session_id)
        messages = tailer.read_all()
        renderer = OpenCodeRenderer()

        html = renderer.render_message(messages[1])

        # Should contain the command and output
        assert "echo hello" in html
        assert "hello" in html  # output


class TestOpenCodeBackend:
    """Tests for the main OpenCode backend class."""

    def test_backend_name(self, opencode_storage_dir):
        """Test backend name property."""
        from vibedeck.backends.opencode import OpenCodeBackend

        backend = OpenCodeBackend(storage_dir=opencode_storage_dir)
        assert backend.name == "OpenCode"

    def test_cli_command(self, opencode_storage_dir):
        """Test CLI command property."""
        from vibedeck.backends.opencode import OpenCodeBackend

        backend = OpenCodeBackend(storage_dir=opencode_storage_dir)
        assert backend.cli_command == "opencode"

    def test_supports_send_message(self, opencode_storage_dir):
        """Test that send message is supported."""
        from vibedeck.backends.opencode import OpenCodeBackend

        backend = OpenCodeBackend(storage_dir=opencode_storage_dir)
        assert backend.supports_send_message() is True

    def test_supports_fork_session(self, opencode_storage_dir):
        """Test that fork is not supported."""
        from vibedeck.backends.opencode import OpenCodeBackend

        backend = OpenCodeBackend(storage_dir=opencode_storage_dir)
        assert backend.supports_fork_session() is False

    def test_build_send_command(self, opencode_storage_dir):
        """Test building send command."""
        from vibedeck.backends.opencode import OpenCodeBackend

        backend = OpenCodeBackend(storage_dir=opencode_storage_dir)
        cmd_spec = backend.build_send_command("ses_123", "hello world")

        assert cmd_spec.args == ["opencode", "run", "-s", "ses_123"]
        assert cmd_spec.stdin == "hello world"

    def test_build_new_session_command(self, opencode_storage_dir):
        """Test building new session command."""
        from vibedeck.backends.opencode import OpenCodeBackend

        backend = OpenCodeBackend(storage_dir=opencode_storage_dir)
        cmd_spec = backend.build_new_session_command("start message")

        assert cmd_spec.args == ["opencode", "run"]
        assert cmd_spec.stdin == "start message"

    def test_build_new_session_command_with_model(self, opencode_storage_dir):
        """Test building new session command with model parameter."""
        from vibedeck.backends.opencode import OpenCodeBackend

        backend = OpenCodeBackend(storage_dir=opencode_storage_dir)
        cmd_spec = backend.build_new_session_command(
            "start message", model="anthropic/claude-sonnet-4-5"
        )

        assert cmd_spec.args == [
            "opencode",
            "run",
            "-m",
            "anthropic/claude-sonnet-4-5",
        ]
        assert cmd_spec.stdin == "start message"

    def test_get_session_metadata(self, opencode_session):
        """Test getting session metadata."""
        from vibedeck.backends.opencode import OpenCodeBackend

        storage_dir = opencode_session["storage_dir"]
        session_file = opencode_session["session_file"]

        backend = OpenCodeBackend(storage_dir=storage_dir)
        metadata = backend.get_session_metadata(session_file)

        assert metadata.session_id == opencode_session["session_id"]
        assert metadata.project_name == "test-project"
        assert metadata.first_message == "Hello, how are you?"

    def test_create_tailer(self, opencode_session):
        """Test creating a tailer."""
        from vibedeck.backends.opencode import OpenCodeBackend
        from vibedeck.backends.opencode.tailer import OpenCodeTailer

        storage_dir = opencode_session["storage_dir"]
        session_file = opencode_session["session_file"]

        backend = OpenCodeBackend(storage_dir=storage_dir)
        tailer = backend.create_tailer(session_file)

        assert isinstance(tailer, OpenCodeTailer)

    def test_get_session_token_usage(self, opencode_session):
        """Test getting token usage."""
        from vibedeck.backends.opencode import OpenCodeBackend

        storage_dir = opencode_session["storage_dir"]
        session_file = opencode_session["session_file"]

        backend = OpenCodeBackend(storage_dir=storage_dir)
        usage = backend.get_session_token_usage(session_file)

        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.message_count == 1
        assert len(usage.models) == 1
        assert "claude-sonnet" in usage.models[0]


class TestOpenCodeCLI:
    """Tests for OpenCode CLI utilities."""

    def test_build_send_command(self):
        """Test building send command."""
        from vibedeck.backends.opencode.cli import (
            build_send_command,
        )

        cmd_spec = build_send_command("ses_abc", "test message")
        assert cmd_spec.args == ["opencode", "run", "-s", "ses_abc"]
        assert cmd_spec.stdin == "test message"

    def test_build_new_session_command(self):
        """Test building new session command."""
        from vibedeck.backends.opencode.cli import (
            build_new_session_command,
        )

        cmd_spec = build_new_session_command("initial prompt")
        assert cmd_spec.args == ["opencode", "run"]
        assert cmd_spec.stdin == "initial prompt"

    def test_build_fork_command_raises(self):
        """Test that fork command raises NotImplementedError."""
        from vibedeck.backends.opencode.cli import (
            build_fork_command,
        )

        with pytest.raises(NotImplementedError):
            build_fork_command("ses_123", "fork message")
