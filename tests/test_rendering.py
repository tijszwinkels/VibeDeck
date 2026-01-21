"""Tests for the rendering module."""

import pytest

from vibedeck.rendering import (
    render_message,
    render_content_block,
    render_markdown_text,
    is_json_like,
    format_json,
    is_tool_result_message,
)


class TestRenderMarkdownText:
    """Tests for render_markdown_text."""

    def test_renders_basic_markdown(self):
        """Test that basic markdown is rendered."""
        result = render_markdown_text("**bold** and *italic*")
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result

    def test_renders_code_blocks(self):
        """Test that fenced code blocks are rendered."""
        result = render_markdown_text("```python\nprint('hello')\n```")
        assert "<code" in result  # May have class attribute
        assert "print" in result

    def test_empty_string_returns_empty(self):
        """Test that empty string returns empty."""
        assert render_markdown_text("") == ""
        assert render_markdown_text(None) == ""


class TestIsJsonLike:
    """Tests for is_json_like."""

    def test_detects_object(self):
        """Test that JSON objects are detected."""
        assert is_json_like('{"key": "value"}') is True

    def test_detects_array(self):
        """Test that JSON arrays are detected."""
        assert is_json_like("[1, 2, 3]") is True

    def test_rejects_plain_text(self):
        """Test that plain text is rejected."""
        assert is_json_like("hello world") is False

    def test_handles_empty(self):
        """Test that empty/None is rejected."""
        assert is_json_like("") is False
        assert is_json_like(None) is False


class TestFormatJson:
    """Tests for format_json."""

    def test_formats_dict(self):
        """Test that dicts are formatted as JSON."""
        result = format_json({"key": "value"})
        assert '<pre class="json">' in result
        # HTML escapes quotes
        assert "key" in result
        assert "value" in result

    def test_formats_json_string(self):
        """Test that JSON strings are parsed and formatted."""
        result = format_json('{"key": "value"}')
        assert '<pre class="json">' in result

    def test_handles_invalid_json(self):
        """Test that invalid JSON is escaped."""
        result = format_json("not json")
        assert "<pre>" in result
        assert "not json" in result


class TestIsToolResultMessage:
    """Tests for is_tool_result_message."""

    def test_detects_tool_result_only(self):
        """Test that pure tool result messages are detected."""
        message = {"content": [{"type": "tool_result", "content": "result"}]}
        assert is_tool_result_message(message) is True

    def test_rejects_mixed_content(self):
        """Test that mixed content is rejected."""
        message = {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_result", "content": "result"},
            ]
        }
        assert is_tool_result_message(message) is False

    def test_rejects_string_content(self):
        """Test that string content is rejected."""
        message = {"content": "just text"}
        assert is_tool_result_message(message) is False


class TestRenderContentBlock:
    """Tests for render_content_block."""

    def test_renders_text_block(self):
        """Test that text blocks are rendered."""
        block = {"type": "text", "text": "Hello world"}
        result = render_content_block(block)
        assert "Hello world" in result
        assert "assistant-text" in result

    def test_renders_thinking_block(self):
        """Test that thinking blocks are rendered."""
        block = {"type": "thinking", "thinking": "Let me think..."}
        result = render_content_block(block)
        assert "thinking" in result.lower()
        assert "Let me think" in result

    def test_renders_tool_use(self):
        """Test that generic tool use is rendered."""
        block = {
            "type": "tool_use",
            "id": "123",
            "name": "SomeTool",
            "input": {"param": "value"},
        }
        result = render_content_block(block)
        assert "SomeTool" in result
        assert "tool-use" in result

    def test_renders_write_tool(self):
        """Test that Write tool is rendered specially."""
        block = {
            "type": "tool_use",
            "id": "123",
            "name": "Write",
            "input": {"file_path": "/tmp/test.py", "content": "print('hi')"},
        }
        result = render_content_block(block)
        assert "write-tool" in result
        assert "test.py" in result

    def test_renders_edit_tool(self):
        """Test that Edit tool is rendered specially."""
        block = {
            "type": "tool_use",
            "id": "123",
            "name": "Edit",
            "input": {
                "file_path": "/tmp/test.py",
                "old_string": "old",
                "new_string": "new",
            },
        }
        result = render_content_block(block)
        assert "edit-tool" in result
        assert "old" in result
        assert "new" in result

    def test_renders_bash_tool(self):
        """Test that Bash tool is rendered specially."""
        block = {
            "type": "tool_use",
            "id": "123",
            "name": "Bash",
            "input": {"command": "ls -la", "description": "List files"},
        }
        result = render_content_block(block)
        assert "bash-tool" in result
        assert "ls -la" in result
        assert "List files" in result

    def test_renders_tool_result(self):
        """Test that tool results are rendered."""
        block = {"type": "tool_result", "content": "Operation successful"}
        result = render_content_block(block)
        assert "tool-result" in result
        assert "Operation successful" in result

    def test_renders_tool_error(self):
        """Test that tool errors are rendered with error styling."""
        block = {"type": "tool_result", "content": "Error occurred", "is_error": True}
        result = render_content_block(block)
        assert "tool-error" in result

    def test_renders_image_block(self):
        """Test that image blocks are rendered."""
        block = {
            "type": "image",
            "source": {"media_type": "image/png", "data": "base64data"},
        }
        result = render_content_block(block)
        assert "image-block" in result
        assert "data:image/png;base64" in result


class TestRenderMessage:
    """Tests for render_message."""

    def test_renders_user_message(self, sample_user_entry):
        """Test that user messages are rendered."""
        result = render_message(sample_user_entry)
        assert "message user" in result
        assert "User" in result
        assert "hello world" in result.lower()

    def test_renders_user_message_escapes_html(self):
        """Test that user messages with HTML-like content are escaped."""
        entry = {
            "type": "user",
            "timestamp": "2024-01-01T00:00:00Z",
            "message": {"content": "Create a YYYYMMDD-<title>.md file"},
        }
        result = render_message(entry)
        assert "user" in result.lower()
        # The <title> should be escaped, not interpreted as HTML
        assert "&lt;title&gt;" in result
        # Should NOT contain raw <title> tag
        assert "<title>" not in result

    def test_renders_assistant_message(self, sample_assistant_entry):
        """Test that assistant messages are rendered."""
        result = render_message(sample_assistant_entry)
        assert "message assistant" in result
        assert "Assistant" in result

    def test_renders_tool_reply(self, sample_tool_result_entry):
        """Test that tool result messages are rendered as tool-reply."""
        result = render_message(sample_tool_result_entry)
        assert "tool-reply" in result
        assert "Tool reply" in result

    def test_empty_message_returns_empty(self):
        """Test that empty message returns empty string."""
        entry = {"type": "user", "timestamp": "2024-01-01T00:00:00Z", "message": {}}
        result = render_message(entry)
        assert result == ""

    def test_unknown_type_returns_empty(self):
        """Test that unknown types return empty string."""
        entry = {
            "type": "system",
            "timestamp": "2024-01-01T00:00:00Z",
            "message": {"content": "test"},
        }
        result = render_message(entry)
        assert result == ""

    def test_includes_timestamp(self, sample_user_entry):
        """Test that timestamp is included."""
        result = render_message(sample_user_entry)
        assert "2024-12-30T10:00:00.000Z" in result

    def test_includes_message_id(self, sample_user_entry):
        """Test that message ID is included for linking."""
        result = render_message(sample_user_entry)
        assert 'id="msg-' in result
