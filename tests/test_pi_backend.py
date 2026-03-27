"""Tests for the Pi Coding Agent backend."""

import json
import os
import tempfile
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SIMPLE_SESSION = FIXTURES_DIR / "pi_session_simple.jsonl"
BRANCHING_SESSION = FIXTURES_DIR / "pi_session_branching.jsonl"
TOOLS_SESSION = FIXTURES_DIR / "pi_session_tools.jsonl"


# ===== Discovery Tests =====


class TestDiscovery:
    """Test session discovery and metadata extraction."""

    def test_decode_project_path_simple(self):
        from vibedeck.backends.pi.discovery import decode_project_path

        assert decode_project_path("--home-claude-tmp--") == "/home/claude/tmp"

    def test_decode_project_path_naive(self):
        """Naive decode replaces all dashes with slashes.
        For accurate paths, the backend uses the header's cwd field.
        """
        from vibedeck.backends.pi.discovery import decode_project_path

        # All dashes become slashes in naive decode
        assert decode_project_path("--home-user-my-project--") == "/home/user/my/project"

    def test_get_session_id_from_filename(self):
        from vibedeck.backends.pi.discovery import get_session_id

        path = Path(
            "2026-03-27T12-10-22-476Z_317fbeae-c6a7-4fd4-b284-a8bf9ba1c736.jsonl"
        )
        assert get_session_id(path) == "317fbeae-c6a7-4fd4-b284-a8bf9ba1c736"

    def test_get_session_id_from_header(self):
        from vibedeck.backends.pi.discovery import get_session_id_from_header

        assert (
            get_session_id_from_header(SIMPLE_SESSION)
            == "317fbeae-c6a7-4fd4-b284-a8bf9ba1c736"
        )

    def test_get_session_metadata_from_header(self):
        from vibedeck.backends.pi.discovery import get_session_header

        header = get_session_header(SIMPLE_SESSION)
        assert header is not None
        assert header["type"] == "session"
        assert header["cwd"] == "/home/claude/tmp"
        assert header["id"] == "317fbeae-c6a7-4fd4-b284-a8bf9ba1c736"

    def test_find_recent_sessions(self):
        from vibedeck.backends.pi.discovery import find_recent_sessions

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create pi-like directory structure
            sessions_dir = Path(tmpdir) / "sessions"
            project_dir = sessions_dir / "--home-claude-tmp--"
            project_dir.mkdir(parents=True)

            # Copy fixture to temp dir
            dest = project_dir / "2026-03-27T12-10-22-476Z_317fbeae.jsonl"
            dest.write_text(SIMPLE_SESSION.read_text())

            results = find_recent_sessions(sessions_dir, limit=10)
            assert len(results) == 1
            assert results[0].name == dest.name

    def test_find_recent_sessions_skips_empty(self):
        from vibedeck.backends.pi.discovery import find_recent_sessions

        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir) / "sessions"
            project_dir = sessions_dir / "--home-claude-tmp--"
            project_dir.mkdir(parents=True)

            # Create empty session
            empty = project_dir / "2026-03-27T00-00-00-000Z_empty.jsonl"
            empty.write_text(
                '{"type":"session","version":3,"id":"empty","timestamp":"2026-03-27T00:00:00.000Z","cwd":"/tmp"}\n'
            )

            results = find_recent_sessions(sessions_dir, limit=10)
            assert len(results) == 0

    def test_has_messages(self):
        from vibedeck.backends.pi.discovery import has_messages

        assert has_messages(SIMPLE_SESSION) is True

    def test_has_messages_empty(self):
        from vibedeck.backends.pi.discovery import has_messages

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            f.write(
                '{"type":"session","version":3,"id":"empty","timestamp":"2026-03-27T00:00:00.000Z","cwd":"/tmp"}\n'
            )
            f.write(
                '{"type":"model_change","id":"mc1","parentId":null,"timestamp":"2026-03-27T00:00:00.001Z","provider":"openai","modelId":"gpt-5.4"}\n'
            )
            f.flush()
            try:
                assert has_messages(Path(f.name)) is False
            finally:
                os.unlink(f.name)

    def test_get_first_user_message(self):
        from vibedeck.backends.pi.discovery import get_first_user_message

        assert get_first_user_message(SIMPLE_SESSION) == "Hi!"

    def test_get_project_name(self):
        from vibedeck.backends.pi.discovery import get_project_name

        path = Path("/home/user/.pi/agent/sessions/--home-claude-tmp--/session.jsonl")
        name, project_path = get_project_name(path)
        assert name == "tmp"
        assert project_path == "/home/claude/tmp"


# ===== Tailer Tests =====


class TestTailer:
    """Test session file reading and tree linearization."""

    def test_read_all_simple(self):
        from vibedeck.backends.pi.tailer import PiTailer

        tailer = PiTailer(SIMPLE_SESSION)
        entries = tailer.read_all()
        # Should only include message entries (not session header, model_change, etc.)
        assert len(entries) == 4  # 2 user + 2 assistant
        assert entries[0]["message"]["role"] == "user"
        assert entries[1]["message"]["role"] == "assistant"

    def test_read_all_branching_follows_last_child(self):
        from vibedeck.backends.pi.tailer import PiTailer

        tailer = PiTailer(BRANCHING_SESSION)
        entries = tailer.read_all()
        # Should follow last child at branch point (msg00002 has two children)
        # Last child is msg00005 (second branch), so should follow that path
        roles = [e["message"]["role"] for e in entries]
        texts = []
        for e in entries:
            msg = e["message"]
            content = msg.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block["text"])

        assert "Hello" in texts
        assert "Hi there!" in texts
        assert "Second branch question" in texts
        assert "Second branch answer" in texts
        # First branch should NOT be in the linearized output
        assert "First branch question" not in texts
        assert "First branch answer" not in texts

    def test_read_all_with_tool_messages(self):
        from vibedeck.backends.pi.tailer import PiTailer

        tailer = PiTailer(TOOLS_SESSION)
        entries = tailer.read_all()
        # Extract roles from message entries only
        msg_roles = [
            e["message"]["role"] for e in entries if e.get("type") == "message"
        ]
        assert "toolResult" in msg_roles
        assert "bashExecution" in msg_roles
        # compaction entry should also be included
        entry_types = [e["type"] for e in entries]
        assert "compaction" in entry_types

    def test_waiting_for_input_after_assistant_stop(self):
        from vibedeck.backends.pi.tailer import PiTailer

        tailer = PiTailer(SIMPLE_SESSION)
        tailer.read_all()
        assert tailer.waiting_for_input is True

    def test_waiting_for_input_after_tool_use(self):
        from vibedeck.backends.pi.tailer import PiTailer

        # After the first assistant message with stopReason=toolUse
        tailer = PiTailer(TOOLS_SESSION)
        entries = tailer.read_new_lines()
        # After full read, last message is compaction, but before that
        # the last assistant had stopReason=stop
        assert tailer.waiting_for_input is True

    def test_get_first_timestamp(self):
        from vibedeck.backends.pi.tailer import PiTailer

        tailer = PiTailer(SIMPLE_SESSION)
        ts = tailer.get_first_timestamp()
        assert ts == "2026-03-27T12:10:22.476Z"  # From session header

    def test_get_last_message_timestamp(self):
        from vibedeck.backends.pi.tailer import PiTailer

        tailer = PiTailer(SIMPLE_SESSION)
        ts = tailer.get_last_message_timestamp()
        assert ts is not None
        # Last message timestamp is 2026-03-27T12:10:47.809Z
        assert abs(ts - 1774613447.809) < 1

    def test_read_new_lines_incremental(self):
        from vibedeck.backends.pi.tailer import PiTailer

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            # Write header and first message
            f.write(
                '{"type":"session","version":3,"id":"inc-test","timestamp":"2026-03-27T12:00:00.000Z","cwd":"/tmp"}\n'
            )
            f.write(
                '{"type":"message","id":"m1","parentId":null,"timestamp":"2026-03-27T12:00:01.000Z","message":{"role":"user","content":"First","timestamp":1774612801000}}\n'
            )
            f.flush()

            try:
                tailer = PiTailer(Path(f.name))
                entries1 = tailer.read_new_lines()
                assert len(entries1) == 1

                # Append a new message
                with open(f.name, "a") as fa:
                    fa.write(
                        '{"type":"message","id":"m2","parentId":"m1","timestamp":"2026-03-27T12:00:02.000Z","message":{"role":"assistant","content":[{"type":"text","text":"Reply"}],"model":"gpt-5.4","usage":{"input":10,"output":5,"totalTokens":15,"cost":{"total":0.001}},"stopReason":"stop","timestamp":1774612802000}}\n'
                    )

                entries2 = tailer.read_new_lines()
                assert len(entries2) == 1
                assert entries2[0]["message"]["role"] == "assistant"
            finally:
                os.unlink(f.name)

    def test_read_all_does_not_modify_position(self):
        from vibedeck.backends.pi.tailer import PiTailer

        tailer = PiTailer(SIMPLE_SESSION)
        pos_before = tailer.position
        tailer.read_all()
        assert tailer.position == pos_before


# ===== Renderer Tests =====


class TestRenderer:
    """Test HTML rendering of Pi messages."""

    def test_render_user_message(self):
        from vibedeck.backends.pi.renderer import PiRenderer

        renderer = PiRenderer()
        entry = {
            "type": "message",
            "id": "test1",
            "timestamp": "2026-03-27T12:00:00.000Z",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello world"}],
                "timestamp": 1774612800000,
            },
        }
        html = renderer.render_message(entry)
        assert "User" in html or "user" in html
        assert "Hello world" in html

    def test_render_assistant_message_with_thinking(self):
        from vibedeck.backends.pi.renderer import PiRenderer

        renderer = PiRenderer()
        entry = {
            "type": "message",
            "id": "test2",
            "timestamp": "2026-03-27T12:00:01.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Deep thought..."},
                    {"type": "text", "text": "The answer is 42."},
                ],
                "model": "gpt-5.4",
                "usage": {
                    "input": 100,
                    "output": 10,
                    "cost": {"total": 0.001},
                },
                "stopReason": "stop",
            },
        }
        html = renderer.render_message(entry)
        assert "thinking" in html.lower()
        assert "Deep thought" in html
        assert "The answer is 42" in html

    def test_render_tool_call(self):
        from vibedeck.backends.pi.renderer import PiRenderer

        renderer = PiRenderer()
        entry = {
            "type": "message",
            "id": "test3",
            "timestamp": "2026-03-27T12:00:02.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "call_1",
                        "name": "bash",
                        "arguments": {"command": "ls -la"},
                    }
                ],
                "model": "gpt-5.4",
                "usage": {"input": 50, "output": 5, "cost": {"total": 0.0005}},
                "stopReason": "toolUse",
            },
        }
        html = renderer.render_message(entry)
        assert "ls -la" in html

    def test_render_tool_result(self):
        from vibedeck.backends.pi.renderer import PiRenderer

        renderer = PiRenderer()
        entry = {
            "type": "message",
            "id": "test4",
            "timestamp": "2026-03-27T12:00:03.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "call_1",
                "toolName": "bash",
                "content": [{"type": "text", "text": "file1.txt\nfile2.txt"}],
                "isError": False,
                "timestamp": 1774612803000,
            },
        }
        html = renderer.render_message(entry)
        assert "file1.txt" in html

    def test_render_bash_execution(self):
        from vibedeck.backends.pi.renderer import PiRenderer

        renderer = PiRenderer()
        entry = {
            "type": "message",
            "id": "test5",
            "timestamp": "2026-03-27T12:00:04.000Z",
            "message": {
                "role": "bashExecution",
                "command": "echo hello",
                "output": "hello\n",
                "exitCode": 0,
                "cancelled": False,
                "truncated": False,
                "timestamp": 1774612804000,
            },
        }
        html = renderer.render_message(entry)
        assert "echo hello" in html
        assert "hello" in html

    def test_render_compaction(self):
        from vibedeck.backends.pi.renderer import PiRenderer

        renderer = PiRenderer()
        entry = {
            "type": "compaction",
            "id": "cmp1",
            "parentId": "prev",
            "timestamp": "2026-03-27T12:00:05.000Z",
            "summary": "User discussed file operations.",
            "firstKeptEntryId": "kept1",
            "tokensBefore": 50000,
        }
        html = renderer.render_message(entry)
        assert "User discussed file operations" in html

    def test_render_skips_non_displayable(self):
        from vibedeck.backends.pi.renderer import PiRenderer

        renderer = PiRenderer()
        entry = {
            "type": "model_change",
            "id": "mc1",
            "parentId": None,
            "timestamp": "2026-03-27T12:00:00.000Z",
            "provider": "openai",
            "modelId": "gpt-5.4",
        }
        html = renderer.render_message(entry)
        assert html == ""

    def test_render_custom_message_displayed(self):
        """custom_message entries with display=true should be rendered."""
        from vibedeck.backends.pi.renderer import PiRenderer

        renderer = PiRenderer()
        entry = {
            "type": "custom_message",
            "id": "cm1",
            "parentId": "prev",
            "timestamp": "2026-03-27T12:00:06.000Z",
            "customType": "my-extension",
            "content": "Injected context from extension",
            "display": True,
            "details": {},
        }
        html = renderer.render_message(entry)
        assert "Injected context from extension" in html
        assert "my-extension" in html

    def test_render_custom_message_hidden(self):
        """custom_message entries with display=false should be skipped."""
        from vibedeck.backends.pi.renderer import PiRenderer

        renderer = PiRenderer()
        entry = {
            "type": "custom_message",
            "id": "cm2",
            "parentId": "prev",
            "timestamp": "2026-03-27T12:00:07.000Z",
            "customType": "my-extension",
            "content": "Hidden context",
            "display": False,
        }
        html = renderer.render_message(entry)
        assert html == ""

    def test_render_user_string_content(self):
        """User content can be a plain string instead of array."""
        from vibedeck.backends.pi.renderer import PiRenderer

        renderer = PiRenderer()
        entry = {
            "type": "message",
            "id": "test6",
            "timestamp": "2026-03-27T12:00:00.000Z",
            "message": {
                "role": "user",
                "content": "Hello plain string",
                "timestamp": 1774612800000,
            },
        }
        html = renderer.render_message(entry)
        assert "Hello plain string" in html


# ===== Pricing Tests =====


class TestPricing:
    """Test token usage and cost extraction."""

    def test_get_session_token_usage(self):
        from vibedeck.backends.pi.pricing import get_session_token_usage

        usage = get_session_token_usage(SIMPLE_SESSION)
        assert usage.input_tokens == 1115 + 1140
        assert usage.output_tokens == 13 + 93
        assert usage.message_count == 2
        assert abs(usage.cost - (0.0029825 + 0.004245)) < 0.0001
        assert "gpt-5.4" in usage.models

    def test_get_session_token_usage_tools(self):
        from vibedeck.backends.pi.pricing import get_session_token_usage

        usage = get_session_token_usage(TOOLS_SESSION)
        # 3 assistant messages with usage
        assert usage.message_count == 3
        assert usage.input_tokens == 500 + 600 + 700
        assert usage.output_tokens == 50 + 20 + 10

    def test_get_session_model(self):
        from vibedeck.backends.pi.pricing import get_session_model

        model = get_session_model(SIMPLE_SESSION)
        assert model == "gpt-5.4"

    def test_get_session_model_tools(self):
        from vibedeck.backends.pi.pricing import get_session_model

        model = get_session_model(TOOLS_SESSION)
        assert model == "claude-sonnet-4-5"


# ===== Backend Integration Tests =====


class TestPiBackend:
    """Integration tests for the full PiBackend."""

    def test_backend_identity(self):
        from vibedeck.backends.pi.backend import PiBackend

        backend = PiBackend()
        assert backend.name == "Pi"
        assert backend.normalizer_key == "pi"
        assert backend.cli_command == "pi"

    def test_backend_cli_flags(self):
        from vibedeck.backends.pi.backend import PiBackend

        backend = PiBackend()
        assert backend.supports_send_message() is True
        assert backend.supports_fork_session() is True
        assert backend.supports_permission_detection() is False

    def test_backend_session_operations(self):
        from vibedeck.backends.pi.backend import PiBackend

        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            project_dir = sessions_dir / "--home-claude-tmp--"
            project_dir.mkdir(parents=True)

            dest = project_dir / "2026-03-27T12-10-22-476Z_317fbeae.jsonl"
            dest.write_text(SIMPLE_SESSION.read_text())

            backend = PiBackend(sessions_dir=sessions_dir)

            # Discovery
            sessions = backend.find_recent_sessions(limit=5)
            assert len(sessions) == 1

            # Metadata
            meta = backend.get_session_metadata(sessions[0])
            assert meta.project_path == "/home/claude/tmp"
            assert meta.first_message == "Hi!"
            assert meta.started_at is not None

            # Has messages
            assert backend.has_messages(sessions[0]) is True

            # Tailer
            tailer = backend.create_tailer(sessions[0])
            entries = tailer.read_all()
            assert len(entries) == 4

            # Renderer
            renderer = backend.get_message_renderer()
            html = renderer.render_message(entries[0])
            assert "Hi!" in html

            # Token usage
            usage = backend.get_session_token_usage(sessions[0])
            assert usage.message_count == 2
            assert usage.cost > 0

    def test_backend_registered(self):
        """Verify Pi backend is registered in the registry."""
        from vibedeck.backends.registry import (
            ensure_backends_registered,
            list_backends,
        )

        ensure_backends_registered()
        backends = list_backends()
        assert "pi" in backends

    def test_should_watch_file(self):
        from vibedeck.backends.pi.backend import PiBackend

        backend = PiBackend()
        assert backend.should_watch_file(Path("session.jsonl")) is True
        assert backend.should_watch_file(Path("data.json")) is False
        assert backend.should_watch_file(Path("notes.txt")) is False


# ===== Normalizer Tests =====


class TestNormalizer:
    """Test message normalization for export."""

    def test_normalize_user_message(self):
        from vibedeck.backends.shared.normalizer import normalize_message

        entry = {
            "type": "message",
            "id": "test1",
            "timestamp": "2026-03-27T12:00:00.000Z",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello"}],
                "timestamp": 1774612800000,
            },
        }
        msg = normalize_message(entry, "pi")
        assert msg is not None
        assert msg.role == "user"
        assert len(msg.blocks) == 1
        assert msg.blocks[0].text == "Hello"

    def test_normalize_assistant_message(self):
        from vibedeck.backends.shared.normalizer import normalize_message

        entry = {
            "type": "message",
            "id": "test2",
            "timestamp": "2026-03-27T12:00:01.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Hmm..."},
                    {"type": "text", "text": "Answer"},
                ],
                "model": "gpt-5.4",
                "usage": {
                    "input": 100,
                    "output": 10,
                    "cost": {"total": 0.001},
                },
                "stopReason": "stop",
            },
        }
        msg = normalize_message(entry, "pi")
        assert msg is not None
        assert msg.role == "assistant"
        assert msg.model == "gpt-5.4"
        assert len(msg.blocks) == 2
        assert msg.blocks[0].type == "thinking"
        assert msg.blocks[1].type == "text"
        assert msg.usage is not None
        assert msg.usage["cost"] == 0.001

    def test_normalize_tool_call(self):
        from vibedeck.backends.shared.normalizer import normalize_message

        entry = {
            "type": "message",
            "id": "test3",
            "timestamp": "2026-03-27T12:00:02.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "call_1",
                        "name": "bash",
                        "arguments": {"command": "ls"},
                    }
                ],
                "model": "gpt-5.4",
                "usage": {"input": 50, "output": 5, "cost": {"total": 0.0005}},
                "stopReason": "toolUse",
            },
        }
        msg = normalize_message(entry, "pi")
        assert msg is not None
        assert msg.blocks[0].type == "tool_use"
        assert msg.blocks[0].tool_name == "bash"
        assert msg.blocks[0].tool_input == {"command": "ls"}

    def test_normalize_tool_result(self):
        from vibedeck.backends.shared.normalizer import normalize_message

        entry = {
            "type": "message",
            "id": "test4",
            "timestamp": "2026-03-27T12:00:03.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "call_1",
                "toolName": "bash",
                "content": [{"type": "text", "text": "output"}],
                "isError": False,
                "timestamp": 1774612803000,
            },
        }
        msg = normalize_message(entry, "pi")
        assert msg is not None
        assert msg.role == "assistant"  # toolResult normalized as assistant
        assert msg.blocks[0].type == "tool_result"

    def test_normalize_bash_execution(self):
        from vibedeck.backends.shared.normalizer import normalize_message

        entry = {
            "type": "message",
            "id": "test5",
            "timestamp": "2026-03-27T12:00:04.000Z",
            "message": {
                "role": "bashExecution",
                "command": "ls",
                "output": "file.txt",
                "exitCode": 0,
            },
        }
        msg = normalize_message(entry, "pi")
        assert msg is not None
        assert msg.role == "assistant"
        # Should have tool_use + tool_result blocks
        assert any(b.type == "tool_use" for b in msg.blocks)
        assert any(b.type == "tool_result" for b in msg.blocks)

    def test_normalize_skips_non_message(self):
        from vibedeck.backends.shared.normalizer import normalize_message

        entry = {
            "type": "model_change",
            "id": "mc1",
            "parentId": None,
            "timestamp": "2026-03-27T12:00:00.000Z",
        }
        msg = normalize_message(entry, "pi")
        assert msg is None

    def test_normalize_custom_message_displayed(self):
        from vibedeck.backends.shared.normalizer import normalize_message

        entry = {
            "type": "custom_message",
            "id": "cm1",
            "parentId": "prev",
            "timestamp": "2026-03-27T12:00:06.000Z",
            "customType": "my-extension",
            "content": "Extension context",
            "display": True,
        }
        msg = normalize_message(entry, "pi")
        assert msg is not None
        assert msg.role == "system"
        assert msg.blocks[0].text == "Extension context"

    def test_normalize_custom_message_hidden(self):
        from vibedeck.backends.shared.normalizer import normalize_message

        entry = {
            "type": "custom_message",
            "id": "cm2",
            "parentId": "prev",
            "timestamp": "2026-03-27T12:00:07.000Z",
            "customType": "my-extension",
            "content": "Hidden",
            "display": False,
        }
        msg = normalize_message(entry, "pi")
        assert msg is None

    def test_normalize_compaction(self):
        from vibedeck.backends.shared.normalizer import normalize_message

        entry = {
            "type": "compaction",
            "id": "cmp1",
            "parentId": "prev",
            "timestamp": "2026-03-27T12:00:05.000Z",
            "summary": "Summary of conversation.",
            "tokensBefore": 50000,
        }
        msg = normalize_message(entry, "pi")
        assert msg is not None
        assert msg.role == "system"
        assert "Summary of conversation" in msg.blocks[0].text


# ===== CLI Tests =====


class TestCli:
    """Test Pi CLI command building."""

    def test_build_new_session_command_basic(self):
        from vibedeck.backends.pi.cli import build_new_session_command

        spec = build_new_session_command("Hello world")
        assert spec.args == ["pi", "-p"]
        assert spec.stdin == "Hello world"

    def test_build_new_session_command_with_model(self):
        from vibedeck.backends.pi.cli import build_new_session_command

        spec = build_new_session_command("Hello", model="google-gemini-cli/gemini-2.5-pro")
        assert spec.args == ["pi", "-p", "--model", "google-gemini-cli/gemini-2.5-pro"]
        assert spec.stdin == "Hello"

    def test_build_send_command(self):
        from vibedeck.backends.pi.cli import build_send_command

        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            project_dir = sessions_dir / "--home-claude-tmp--"
            project_dir.mkdir(parents=True)
            session_file = project_dir / "2026-03-27T12-10-22-476Z_317fbeae.jsonl"
            session_file.write_text(SIMPLE_SESSION.read_text())

            spec = build_send_command(
                "317fbeae", "Continue please", sessions_dir=sessions_dir
            )
            assert spec.args[0] == "pi"
            assert "-p" in spec.args
            assert "--session" in spec.args
            assert str(session_file) in spec.args
            assert spec.stdin == "Continue please"

    def test_build_send_command_with_model(self):
        from vibedeck.backends.pi.cli import build_send_command

        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            project_dir = sessions_dir / "--home-claude-tmp--"
            project_dir.mkdir(parents=True)
            session_file = project_dir / "2026-03-27T12-10-22-476Z_317fbeae.jsonl"
            session_file.write_text(SIMPLE_SESSION.read_text())

            spec = build_send_command(
                "317fbeae", "Hello", model="openai/gpt-5.4", sessions_dir=sessions_dir
            )
            assert "--model" in spec.args
            assert "openai/gpt-5.4" in spec.args

    def test_build_send_command_not_found(self):
        from vibedeck.backends.pi.cli import build_send_command

        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError, match="Cannot find Pi session"):
                build_send_command(
                    "nonexistent-id", "Hello", sessions_dir=Path(tmpdir)
                )

    def test_build_fork_command(self):
        from vibedeck.backends.pi.cli import build_fork_command

        spec = build_fork_command("317fbeae", "Fork message")
        assert spec.args == ["pi", "-p", "--fork", "317fbeae"]
        assert spec.stdin == "Fork message"

    def test_build_fork_command_with_model(self):
        from vibedeck.backends.pi.cli import build_fork_command

        spec = build_fork_command("317fbeae", "Fork", model="openai/gpt-5.4")
        assert spec.args == ["pi", "-p", "--fork", "317fbeae", "--model", "openai/gpt-5.4"]
        assert spec.stdin == "Fork"

    def test_find_session_file(self):
        from vibedeck.backends.pi.cli import find_session_file

        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            project_dir = sessions_dir / "--home-claude-tmp--"
            project_dir.mkdir(parents=True)
            session_file = project_dir / "2026-03-27T12-10-22-476Z_317fbeae.jsonl"
            session_file.write_text("test")

            result = find_session_file("317fbeae", sessions_dir)
            assert result == session_file

    def test_find_session_file_not_found(self):
        from vibedeck.backends.pi.cli import find_session_file

        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_session_file("nonexistent", Path(tmpdir))
            assert result is None

    def test_parse_list_models_output(self):
        from vibedeck.backends.pi.cli import _parse_list_models_output

        output = """provider           model             context  max-out  thinking  images
google-gemini-cli  gemini-2.5-flash  1.0M     65.5K    yes       yes
google-gemini-cli  gemini-2.5-pro    1.0M     65.5K    yes       yes
openai             gpt-5.4           272K     128K     yes       yes
"""
        models = _parse_list_models_output(output)
        assert models == [
            "google-gemini-cli/gemini-2.5-flash",
            "google-gemini-cli/gemini-2.5-pro",
            "openai/gpt-5.4",
        ]

    def test_parse_list_models_empty(self):
        from vibedeck.backends.pi.cli import _parse_list_models_output

        assert _parse_list_models_output("") == []
        assert _parse_list_models_output("header only\n") == []

    def test_backend_get_models(self, monkeypatch):
        """Test that PiBackend.get_models() delegates to get_available_models."""
        from vibedeck.backends.pi import backend as backend_mod
        from vibedeck.backends.pi.backend import PiBackend

        monkeypatch.setattr(
            backend_mod,
            "get_available_models",
            lambda: ["google-gemini-cli/gemini-2.5-pro", "openai/gpt-5.4"],
        )
        backend = PiBackend()
        models = backend.get_models()
        assert "google-gemini-cli/gemini-2.5-pro" in models
        assert "openai/gpt-5.4" in models

    def test_backend_build_new_session_with_model(self):
        from vibedeck.backends.pi.backend import PiBackend

        backend = PiBackend()
        spec = backend.build_new_session_command(
            "Hello", model="openai/gpt-5.4"
        )
        assert "--model" in spec.args
        assert "openai/gpt-5.4" in spec.args
        assert spec.stdin == "Hello"
