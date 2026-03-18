"""Tests for the Codex backend."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def codex_storage_dir():
    """Create a temporary Codex storage tree with sample sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        sessions_dir = root / "sessions" / "2026" / "03" / "16"
        sessions_dir.mkdir(parents=True)
        history_path = root / "history.jsonl"

        session_id = "019cf737-934f-70f0-9a03-d729c9d857da"
        session_path = (
            sessions_dir
            / "rollout-2026-03-16T16-15-40-019cf737-934f-70f0-9a03-d729c9d857da.jsonl"
        )

        entries = [
            {
                "timestamp": "2026-03-16T15:15:40.000Z",
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "timestamp": "2026-03-16T15:15:40.000Z",
                    "cwd": "/home/claude/projects/VibeDeck",
                    "model_provider": "openai",
                },
            },
            {
                "timestamp": "2026-03-16T15:15:40.050Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "# AGENTS.md instructions for /home/claude/projects/VibeDeck\n\n<INSTRUCTIONS>\n- bootstrap\n</INSTRUCTIONS>",
                        }
                    ],
                },
            },
            {
                "timestamp": "2026-03-16T15:15:40.100Z",
                "type": "turn_context",
                "payload": {
                    "turn_id": "turn-1",
                    "cwd": "/home/claude/projects/VibeDeck",
                    "model": "gpt-5.4",
                },
            },
            {
                "timestamp": "2026-03-16T15:15:40.200Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Read docs/20260316-codex-backend-handoff.md and implement",
                        }
                    ],
                },
            },
            {
                "timestamp": "2026-03-16T15:15:42.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": '{"cmd":"rg --files"}',
                    "call_id": "call_123",
                },
            },
            {
                "timestamp": "2026-03-16T15:15:43.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_123",
                    "output": "src/vibedeck/backends/protocol.py",
                },
            },
            {
                "timestamp": "2026-03-16T15:15:43.500Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Inspecting the backend notes."}
                    ],
                },
            },
            {
                "timestamp": "2026-03-16T15:15:44.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 1200,
                            "cached_input_tokens": 300,
                            "output_tokens": 450,
                            "reasoning_output_tokens": 100,
                            "total_tokens": 1650,
                        }
                    },
                },
            },
        ]
        session_path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))

        history_entries = [
            {
                "session_id": session_id,
                "ts": 1773674144,
                "text": "Read docs/20260316-codex-backend-handoff.md and implement",
            },
            {
                "session_id": session_id,
                "ts": 1773674200,
                "text": "Make sure tests cover token counts.",
            },
        ]
        history_path.write_text(
            "".join(json.dumps(entry) + "\n" for entry in history_entries)
        )

        yield {
            "root": root,
            "sessions_dir": root / "sessions",
            "history_path": history_path,
            "session_id": session_id,
            "session_path": session_path,
        }


class TestCodexDiscovery:
    """Tests for Codex session discovery helpers."""

    def test_find_recent_sessions(self, codex_storage_dir):
        """Find rollout files under the Codex sessions tree."""
        from vibedeck.backends.codex.discovery import find_recent_sessions

        sessions = find_recent_sessions(codex_storage_dir["sessions_dir"], limit=10)

        assert sessions == [codex_storage_dir["session_path"]]

    def test_get_session_metadata(self, codex_storage_dir):
        """Extract metadata using session_meta and history preview data."""
        from vibedeck.backends.codex import CodexBackend

        backend = CodexBackend(
            sessions_dir=codex_storage_dir["sessions_dir"],
            history_path=codex_storage_dir["history_path"],
        )
        metadata = backend.get_session_metadata(codex_storage_dir["session_path"])

        assert metadata.session_id == codex_storage_dir["session_id"]
        assert metadata.project_name == "VibeDeck"
        assert metadata.project_path == "/home/claude/projects/VibeDeck"
        assert metadata.first_message == (
            "Read docs/20260316-codex-backend-handoff.md and implement"
        )
        assert metadata.started_at == "2026-03-16T15:15:40.000Z"

    def test_get_session_metadata_can_show_bootstrap_message(self, codex_storage_dir):
        """Bootstrap messages can be re-enabled explicitly."""
        from vibedeck.backends.codex import CodexBackend

        backend = CodexBackend(
            sessions_dir=codex_storage_dir["sessions_dir"],
            history_path=codex_storage_dir["root"] / "missing-history.jsonl",
            show_bootstrap_messages=True,
        )
        metadata = backend.get_session_metadata(codex_storage_dir["session_path"])

        assert metadata.first_message.startswith("# AGENTS.md instructions for ")


class TestCodexTailer:
    """Tests for Codex transcript tailing."""

    def test_read_all_filters_to_transcript_entries(self, codex_storage_dir):
        """Only transcript-useful records should be surfaced."""
        from vibedeck.backends.codex.tailer import CodexTailer

        tailer = CodexTailer(codex_storage_dir["session_path"])
        entries = tailer.read_all()

        assert [entry["type"] for entry in entries] == [
            "response_item",
            "response_item",
            "response_item",
            "response_item",
        ]
        assert entries[0]["payload"]["role"] == "user"
        assert entries[-1]["payload"]["role"] == "assistant"

    def test_waiting_for_input_after_assistant_message(self, codex_storage_dir):
        """Assistant text should set waiting_for_input."""
        from vibedeck.backends.codex.tailer import CodexTailer

        tailer = CodexTailer(codex_storage_dir["session_path"])
        tailer.read_all()

        assert tailer.waiting_for_input is True


class TestCodexUsage:
    """Tests for Codex token usage extraction."""

    def test_get_session_token_usage(self, codex_storage_dir):
        """Use the latest token_count totals and discovered models."""
        from vibedeck.backends.codex.pricing import get_session_token_usage

        usage = get_session_token_usage(codex_storage_dir["session_path"])

        assert usage.input_tokens == 1200
        assert usage.output_tokens == 450
        assert usage.cache_read_tokens == 300
        assert usage.cache_creation_tokens == 0
        assert usage.message_count == 4
        assert usage.cost == 0.0
        assert usage.models == ["gpt-5.4"]


class TestCodexRenderer:
    """Tests for Codex transcript rendering."""

    def test_render_function_call_output(self, codex_storage_dir):
        """Tool calls and outputs should be rendered into useful HTML."""
        from vibedeck.backends.codex.renderer import CodexRenderer
        from vibedeck.backends.codex.tailer import CodexTailer

        renderer = CodexRenderer()
        entries = CodexTailer(codex_storage_dir["session_path"]).read_all()
        html = renderer.render_message(entries[1]) + renderer.render_message(entries[2])

        assert "exec_command" in html
        assert "src/vibedeck/backends/protocol.py" in html

    def test_render_function_call_output_error(self):
        """Errored tool outputs should render with error styling."""
        from vibedeck.backends.codex.renderer import CodexRenderer

        renderer = CodexRenderer()
        html = renderer.render_message(
            {
                "timestamp": "2026-03-16T15:15:43.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_123",
                    "error": "Permission denied",
                    "status": "error",
                },
            }
        )

        assert "tool-error" in html
        assert "Permission denied" in html


class TestCodexBackend:
    """Tests for the Codex backend wrapper."""

    def test_backend_properties(self, codex_storage_dir):
        """Backend should expose the Codex identity and CLI support."""
        from vibedeck.backends.codex import CodexBackend

        backend = CodexBackend(
            sessions_dir=codex_storage_dir["sessions_dir"],
            history_path=codex_storage_dir["history_path"],
        )

        assert backend.name == "Codex"
        assert backend.normalizer_key == "codex"
        assert backend.cli_command == "codex"
        assert backend.supports_send_message() is True
        assert backend.supports_fork_session() is True
        assert backend.supports_permission_detection() is False

    def test_get_models_reads_codex_cache(self, codex_storage_dir):
        """Model list should come from the local Codex cache."""
        from vibedeck.backends.codex import CodexBackend

        cache_dir = codex_storage_dir["root"] / ".codex"
        cache_dir.mkdir()
        cache_path = cache_dir / "models_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "models": [
                        {"slug": "gpt-5.4", "visibility": "list", "priority": 0},
                        {"slug": "gpt-5.3-codex", "visibility": "list", "priority": 3},
                        {"slug": "gpt-5", "visibility": "hide", "priority": 9},
                    ]
                }
            )
        )

        backend = CodexBackend(
            sessions_dir=codex_storage_dir["sessions_dir"],
            history_path=codex_storage_dir["history_path"],
        )

        with patch("pathlib.Path.home", return_value=codex_storage_dir["root"]):
            assert backend.get_models() == ["gpt-5.4", "gpt-5.3-codex"]

    def test_backend_registered(self):
        """Registry should include the Codex backend."""
        from vibedeck.backends.registry import list_backends

        assert "codex" in list_backends()


class TestCodexCLI:
    """Tests for Codex CLI command building."""

    def test_build_send_command(self):
        """Resume commands should use codex exec resume."""
        from vibedeck.backends.codex.cli import build_send_command

        cmd_spec = build_send_command(
            "session-123",
            "continue",
            skip_permissions=True,
            output_format="json",
            add_dirs=["/tmp/extra"],
        )

        assert cmd_spec.args == [
            "codex",
            "exec",
            "resume",
            "--json",
            "--add-dir",
            "/tmp/extra",
            "--dangerously-bypass-approvals-and-sandbox",
            "session-123",
            "-",
        ]
        assert cmd_spec.stdin == "continue"

    def test_build_new_session_command(self):
        """New sessions should use codex exec with cwd/model support."""
        from vibedeck.backends.codex.cli import build_new_session_command

        cmd_spec = build_new_session_command(
            "start",
            skip_permissions=True,
            model="gpt-5.4",
            output_format="json",
            add_dirs=["/tmp/extra"],
            cwd="/home/claude/projects/VibeDeck",
        )

        assert cmd_spec.args == [
            "codex",
            "exec",
            "--json",
            "--cd",
            "/home/claude/projects/VibeDeck",
            "--add-dir",
            "/tmp/extra",
            "--model",
            "gpt-5.4",
            "--dangerously-bypass-approvals-and-sandbox",
            "-",
        ]
        assert cmd_spec.stdin == "start"
