"""Tests for the normalization layer.

Tests that Claude Code and OpenCode messages are normalized to
a unified NormalizedMessage format with ContentBlock instances.
"""

import json

import pytest

from vibedeck.backends.shared.normalizer import (
    ContentBlock,
    NormalizedMessage,
    normalize_claude_code_message,
    normalize_opencode_message,
    normalize_message,
)


# ===== ContentBlock tests =====


class TestContentBlock:
    """Tests for ContentBlock dataclass and serialization."""

    def test_text_block_to_dict(self):
        block = ContentBlock(type="text", text="Hello world")
        d = block.to_dict()
        assert d == {"type": "text", "text": "Hello world"}

    def test_thinking_block_to_dict(self):
        block = ContentBlock(type="thinking", text="Let me think...")
        d = block.to_dict()
        assert d == {"type": "thinking", "text": "Let me think..."}

    def test_tool_use_block_to_dict(self):
        block = ContentBlock(
            type="tool_use",
            tool_name="Bash",
            tool_id="toolu_123",
            tool_input={"command": "ls -la"},
        )
        d = block.to_dict()
        assert d == {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_id": "toolu_123",
            "tool_input": {"command": "ls -la"},
        }

    def test_tool_result_block_to_dict(self):
        block = ContentBlock(
            type="tool_result",
            tool_use_id="toolu_123",
            content="file1.py\nfile2.py",
            is_error=False,
        )
        d = block.to_dict()
        assert d == {
            "type": "tool_result",
            "tool_use_id": "toolu_123",
            "content": "file1.py\nfile2.py",
            "is_error": False,
        }

    def test_tool_result_error_block_to_dict(self):
        block = ContentBlock(
            type="tool_result",
            tool_use_id="toolu_456",
            content="Permission denied",
            is_error=True,
        )
        d = block.to_dict()
        assert d["is_error"] is True

    def test_tool_result_always_includes_is_error(self):
        """is_error should always appear for tool_result, even when False."""
        block = ContentBlock(
            type="tool_result",
            tool_use_id="toolu_123",
            content="ok",
            is_error=False,
        )
        d = block.to_dict()
        assert "is_error" in d
        assert d["is_error"] is False

    def test_image_block_to_dict(self):
        block = ContentBlock(
            type="image",
            media_type="image/png",
            data="iVBOR...",
        )
        d = block.to_dict()
        assert d == {
            "type": "image",
            "media_type": "image/png",
            "data": "iVBOR...",
        }

    def test_omits_none_fields(self):
        """None fields should not appear in serialized dict."""
        block = ContentBlock(type="text", text="hello")
        d = block.to_dict()
        assert "tool_name" not in d
        assert "tool_id" not in d
        assert "tool_input" not in d
        assert "media_type" not in d


# ===== NormalizedMessage tests =====


class TestNormalizedMessage:
    """Tests for NormalizedMessage dataclass and serialization."""

    def test_to_dict_minimal(self):
        msg = NormalizedMessage(
            role="user",
            timestamp="2024-12-30T10:00:00.000Z",
            blocks=[ContentBlock(type="text", text="Hello")],
        )
        d = msg.to_dict()
        assert d["role"] == "user"
        assert d["timestamp"] == "2024-12-30T10:00:00.000Z"
        assert len(d["blocks"]) == 1
        assert "model" not in d
        assert "stop_reason" not in d
        assert "usage" not in d

    def test_to_dict_full(self):
        msg = NormalizedMessage(
            role="assistant",
            timestamp="2024-12-30T10:00:01.000Z",
            blocks=[ContentBlock(type="text", text="Hi")],
            model="claude-opus-4-6",
            stop_reason="end_turn",
            usage={"input_tokens": 100, "output_tokens": 50, "cost": 0.01},
        )
        d = msg.to_dict()
        assert d["model"] == "claude-opus-4-6"
        assert d["stop_reason"] == "end_turn"
        assert d["usage"]["cost"] == 0.01

    def test_to_dict_round_trip(self):
        """Serialize to dict, then to JSON and back — should round-trip."""
        msg = NormalizedMessage(
            role="assistant",
            timestamp="2024-12-30T10:00:01.000Z",
            blocks=[
                ContentBlock(type="text", text="Hello"),
                ContentBlock(type="tool_use", tool_name="Bash", tool_id="t1", tool_input={"command": "ls"}),
            ],
            model="claude-opus-4-6",
            stop_reason="tool_use",
        )
        d = msg.to_dict()
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed == d


# ===== Claude Code normalization tests =====


class TestNormalizeClaudeCodeMessage:
    """Tests for normalizing Claude Code JSONL entries."""

    def test_user_text_message(self):
        entry = {
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Hello, Claude!"},
        }
        msg = normalize_claude_code_message(entry)
        assert msg is not None
        assert msg.role == "user"
        assert msg.timestamp == "2024-12-30T10:00:00.000Z"
        assert len(msg.blocks) == 1
        assert msg.blocks[0].type == "text"
        assert msg.blocks[0].text == "Hello, Claude!"

    def test_user_list_content(self):
        entry = {
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Look at this"},
                    {"type": "image", "source": {"media_type": "image/png", "data": "abc123"}},
                ]
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg is not None
        assert len(msg.blocks) == 2
        assert msg.blocks[0].type == "text"
        assert msg.blocks[1].type == "image"
        assert msg.blocks[1].media_type == "image/png"
        assert msg.blocks[1].data == "abc123"

    def test_assistant_text(self):
        entry = {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "model": "claude-opus-4-6",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": "I'll help with that."},
                ],
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg is not None
        assert msg.role == "assistant"
        assert msg.model == "claude-opus-4-6"
        assert msg.stop_reason == "end_turn"
        assert msg.blocks[0].type == "text"
        assert msg.blocks[0].text == "I'll help with that."

    def test_assistant_thinking(self):
        entry = {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "Let me analyze..."},
                    {"type": "text", "text": "Here's what I found."},
                ],
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg is not None
        assert len(msg.blocks) == 2
        assert msg.blocks[0].type == "thinking"
        assert msg.blocks[0].text == "Let me analyze..."
        assert msg.blocks[1].type == "text"

    def test_assistant_tool_use(self):
        entry = {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "Bash",
                        "input": {"command": "ls -la", "description": "List files"},
                    },
                ],
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg is not None
        assert msg.blocks[0].type == "tool_use"
        assert msg.blocks[0].tool_name == "Bash"
        assert msg.blocks[0].tool_id == "toolu_123"
        assert msg.blocks[0].tool_input == {"command": "ls -la", "description": "List files"}

    def test_user_tool_result(self):
        entry = {
            "type": "user",
            "timestamp": "2024-12-30T10:00:02.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "file1.py\nfile2.py",
                        "is_error": False,
                    },
                ],
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg is not None
        assert msg.role == "user"
        assert msg.blocks[0].type == "tool_result"
        assert msg.blocks[0].tool_use_id == "toolu_123"
        assert msg.blocks[0].content == "file1.py\nfile2.py"
        assert msg.blocks[0].is_error is False

    def test_tool_result_with_error(self):
        entry = {
            "type": "user",
            "timestamp": "2024-12-30T10:00:02.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_456",
                        "content": "Permission denied",
                        "is_error": True,
                    },
                ],
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg.blocks[0].is_error is True

    def test_image_block(self):
        entry = {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "content": [
                    {
                        "type": "image",
                        "source": {"media_type": "image/jpeg", "data": "base64data"},
                    },
                ],
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg is not None
        assert msg.blocks[0].type == "image"
        assert msg.blocks[0].media_type == "image/jpeg"
        assert msg.blocks[0].data == "base64data"

    def test_usage_extraction(self):
        entry = {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "model": "claude-opus-4-6",
                "content": [{"type": "text", "text": "Hi"}],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_creation_input_tokens": 50,
                    "cache_read_input_tokens": 2000,
                },
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg is not None
        assert msg.usage is not None
        assert msg.usage["input_tokens"] == 1000
        assert msg.usage["output_tokens"] == 500
        assert msg.usage["cache_creation_tokens"] == 50
        assert msg.usage["cache_read_tokens"] == 2000
        assert "cost" in msg.usage

    def test_no_content_placeholder_skipped(self):
        """Claude Code '(no content)' placeholders should return None."""
        entry = {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "content": [{"type": "text", "text": "(no content)"}],
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg is None

    def test_no_content_placeholder_with_stop_reason_not_skipped(self):
        """Messages with stop_reason set should NOT be skipped even if text is (no content)."""
        entry = {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "(no content)"}],
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg is not None

    def test_skips_non_user_assistant_types(self):
        """Entries with types other than 'user' or 'assistant' should be skipped."""
        entry = {
            "type": "system",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "system message"},
        }
        msg = normalize_claude_code_message(entry)
        assert msg is None

    def test_skips_empty_message(self):
        """Entries with no message data should return None."""
        entry = {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {},
        }
        msg = normalize_claude_code_message(entry)
        assert msg is None

    def test_tool_result_with_list_content(self):
        """Tool results can contain a list of content parts."""
        entry = {
            "type": "user",
            "timestamp": "2024-12-30T10:00:02.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_789",
                        "content": [
                            {"type": "text", "text": "output line 1"},
                            {"type": "image", "source": {"media_type": "image/png", "data": "imgdata"}},
                        ],
                    },
                ],
            },
        }
        msg = normalize_claude_code_message(entry)
        assert msg is not None
        assert msg.blocks[0].type == "tool_result"
        assert isinstance(msg.blocks[0].content, list)


# ===== OpenCode normalization tests =====


class TestNormalizeOpenCodeMessage:
    """Tests for normalizing OpenCode message entries."""

    def test_user_text_message(self):
        entry = {
            "info": {
                "role": "user",
                "time": {"created": 1704020400000},
            },
            "parts": [{"type": "text", "text": "Hello from OpenCode!"}],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert msg.role == "user"
        assert msg.timestamp  # should be ISO 8601
        assert len(msg.blocks) == 1
        assert msg.blocks[0].type == "text"
        assert msg.blocks[0].text == "Hello from OpenCode!"

    def test_assistant_text_message(self):
        entry = {
            "info": {
                "role": "assistant",
                "time": {"created": 1704020401000},
                "modelID": "claude-sonnet-4-5",
                "providerID": "anthropic",
            },
            "parts": [{"type": "text", "text": "I can help with that."}],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert msg.role == "assistant"
        assert msg.model == "anthropic/claude-sonnet-4-5"
        assert msg.blocks[0].text == "I can help with that."

    def test_reasoning_part(self):
        entry = {
            "info": {
                "role": "assistant",
                "time": {"created": 1704020401000},
            },
            "parts": [
                {"type": "reasoning", "reasoning": "Let me think about this..."},
                {"type": "text", "text": "Here's what I found."},
            ],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert len(msg.blocks) == 2
        assert msg.blocks[0].type == "thinking"
        assert msg.blocks[0].text == "Let me think about this..."
        assert msg.blocks[1].type == "text"

    def test_tool_part_completed(self):
        """A completed OpenCode tool part should produce tool_use + tool_result blocks."""
        entry = {
            "info": {
                "role": "assistant",
                "time": {"created": 1704020401000},
            },
            "parts": [
                {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "toolu_abc",
                    "id": "prt_001",
                    "state": {
                        "status": "completed",
                        "input": {"command": "ls"},
                        "output": "file1.py\nfile2.py",
                    },
                },
            ],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert len(msg.blocks) == 2
        # First block: tool_use
        assert msg.blocks[0].type == "tool_use"
        assert msg.blocks[0].tool_name == "Bash"
        assert msg.blocks[0].tool_id == "toolu_abc"
        assert msg.blocks[0].tool_input == {"command": "ls"}
        # Second block: tool_result
        assert msg.blocks[1].type == "tool_result"
        assert msg.blocks[1].tool_use_id == "toolu_abc"
        assert msg.blocks[1].content == "file1.py\nfile2.py"
        assert msg.blocks[1].is_error is False

    def test_tool_part_error(self):
        """An errored OpenCode tool part should produce tool_use + tool_result(is_error=True)."""
        entry = {
            "info": {
                "role": "assistant",
                "time": {"created": 1704020401000},
            },
            "parts": [
                {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "toolu_def",
                    "id": "prt_002",
                    "state": {
                        "status": "error",
                        "input": {"command": "rm -rf /"},
                        "error": "Permission denied",
                    },
                },
            ],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert len(msg.blocks) == 2
        assert msg.blocks[1].type == "tool_result"
        assert msg.blocks[1].is_error is True
        assert msg.blocks[1].content == "Permission denied"

    def test_tool_part_pending(self):
        """A pending OpenCode tool part should produce only tool_use (no result yet)."""
        entry = {
            "info": {
                "role": "assistant",
                "time": {"created": 1704020401000},
            },
            "parts": [
                {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "toolu_ghi",
                    "id": "prt_003",
                    "state": {
                        "status": "pending",
                        "input": {"command": "sleep 10"},
                    },
                },
            ],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert len(msg.blocks) == 1
        assert msg.blocks[0].type == "tool_use"

    def test_file_part_image(self):
        entry = {
            "info": {
                "role": "user",
                "time": {"created": 1704020400000},
            },
            "parts": [
                {
                    "type": "file",
                    "path": "/tmp/screenshot.png",
                    "mime": "image/png",
                    "data": "iVBOR...",
                },
            ],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert msg.blocks[0].type == "image"
        assert msg.blocks[0].media_type == "image/png"
        assert msg.blocks[0].data == "iVBOR..."

    def test_file_part_non_image(self):
        entry = {
            "info": {
                "role": "user",
                "time": {"created": 1704020400000},
            },
            "parts": [
                {
                    "type": "file",
                    "path": "/tmp/data.csv",
                    "mime": "text/csv",
                },
            ],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert msg.blocks[0].type == "text"
        assert "data.csv" in msg.blocks[0].text

    def test_step_finish_skipped(self):
        """step-finish parts should be skipped (not included in blocks)."""
        entry = {
            "info": {
                "role": "assistant",
                "time": {"created": 1704020401000},
            },
            "parts": [
                {"type": "text", "text": "Done."},
                {"type": "step-finish", "tokens": {"input": 100, "output": 50}},
            ],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert len(msg.blocks) == 1
        assert msg.blocks[0].type == "text"

    def test_step_start_skipped(self):
        entry = {
            "info": {
                "role": "assistant",
                "time": {"created": 1704020401000},
            },
            "parts": [
                {"type": "step-start"},
                {"type": "text", "text": "Working on it."},
            ],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert len(msg.blocks) == 1

    def test_snapshot_skipped(self):
        entry = {
            "info": {
                "role": "assistant",
                "time": {"created": 1704020401000},
            },
            "parts": [
                {"type": "snapshot"},
                {"type": "text", "text": "Result."},
            ],
        }
        msg = normalize_opencode_message(entry)
        assert len(msg.blocks) == 1

    def test_usage_from_message_info_tokens(self):
        entry = {
            "info": {
                "role": "assistant",
                "time": {"created": 1704020401000},
                "modelID": "claude-sonnet-4-5",
                "tokens": {
                    "input": 500,
                    "output": 200,
                    "cache": {"read": 1000, "write": 50},
                },
                "cost": 0.005,
            },
            "parts": [{"type": "text", "text": "Done."}],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert msg.usage is not None
        assert msg.usage["input_tokens"] == 500
        assert msg.usage["output_tokens"] == 200
        assert msg.usage["cache_read_tokens"] == 1000
        assert msg.usage["cache_creation_tokens"] == 50
        assert msg.usage["cost"] == 0.005

    def test_usage_from_step_finish_parts(self):
        """When info has no tokens, should extract from step-finish parts."""
        entry = {
            "info": {
                "role": "assistant",
                "time": {"created": 1704020401000},
                "modelID": "claude-sonnet-4-5",
            },
            "parts": [
                {"type": "text", "text": "Done."},
                {
                    "type": "step-finish",
                    "tokens": {
                        "input": 300,
                        "output": 100,
                        "cache": {"read": 500, "write": 25},
                    },
                    "cost": 0.003,
                },
            ],
        }
        msg = normalize_opencode_message(entry)
        assert msg is not None
        assert msg.usage is not None
        assert msg.usage["input_tokens"] == 300
        assert msg.usage["cost"] == 0.003

    def test_skips_no_role(self):
        entry = {
            "info": {"time": {"created": 1704020400000}},
            "parts": [{"type": "text", "text": "orphan"}],
        }
        msg = normalize_opencode_message(entry)
        assert msg is None


# ===== Dispatch function tests =====


class TestNormalizeMessage:
    """Tests for the dispatch function."""

    def test_dispatches_claude_code(self):
        entry = {
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Hi"},
        }
        msg = normalize_message(entry, "claude_code")
        assert msg is not None
        assert msg.role == "user"

    def test_dispatches_opencode(self):
        entry = {
            "info": {"role": "user", "time": {"created": 1704020400000}},
            "parts": [{"type": "text", "text": "Hi"}],
        }
        msg = normalize_message(entry, "opencode")
        assert msg is not None
        assert msg.role == "user"

    def test_dispatches_codex_user_message(self):
        entry = {
            "type": "response_item",
            "timestamp": "2026-03-16T15:15:40.200Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Hello from Codex"}],
            },
        }
        msg = normalize_message(entry, "codex")
        assert msg is not None
        assert msg.role == "user"
        assert msg.blocks[0].type == "text"
        assert msg.blocks[0].text == "Hello from Codex"

    def test_dispatches_codex_assistant_message(self):
        entry = {
            "type": "response_item",
            "timestamp": "2026-03-16T15:15:43.500Z",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Inspecting the backend notes."}
                ],
            },
        }
        msg = normalize_message(entry, "codex")
        assert msg is not None
        assert msg.role == "assistant"
        assert msg.blocks[0].type == "text"
        assert msg.blocks[0].text == "Inspecting the backend notes."

    def test_dispatches_codex_function_call(self):
        entry = {
            "type": "response_item",
            "timestamp": "2026-03-16T15:15:42.000Z",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": '{"cmd":"rg --files"}',
                "call_id": "call_123",
            },
        }
        msg = normalize_message(entry, "codex")
        assert msg is not None
        assert msg.role == "assistant"
        assert len(msg.blocks) == 1
        assert msg.blocks[0].type == "tool_use"
        assert msg.blocks[0].tool_name == "exec_command"
        assert msg.blocks[0].tool_id == "call_123"
        assert msg.blocks[0].tool_input == {"cmd": "rg --files"}

    def test_dispatches_codex_function_call_output(self):
        entry = {
            "type": "response_item",
            "timestamp": "2026-03-16T15:15:43.000Z",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_123",
                "output": "src/vibedeck/backends/protocol.py",
            },
        }
        msg = normalize_message(entry, "codex")
        assert msg is not None
        assert msg.role == "assistant"
        assert len(msg.blocks) == 1
        assert msg.blocks[0].type == "tool_result"
        assert msg.blocks[0].tool_use_id == "call_123"
        assert msg.blocks[0].content == "src/vibedeck/backends/protocol.py"
        assert msg.blocks[0].is_error is False

    def test_unknown_backend_returns_none(self):
        entry = {"type": "user", "message": {"content": "Hi"}}
        msg = normalize_message(entry, "unknown_backend")
        assert msg is None
