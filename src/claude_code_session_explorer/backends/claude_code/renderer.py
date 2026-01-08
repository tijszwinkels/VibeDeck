"""Message rendering for Claude Code sessions.

Renders Claude Code session messages to HTML for display in the browser.
"""

from __future__ import annotations

import html
import json
import re

from jinja2 import Environment, PackageLoader
import markdown

from .pricing import calculate_message_cost

# Set up Jinja2 environment
_jinja_env = Environment(
    loader=PackageLoader("claude_code_session_explorer", "templates"),
    autoescape=True,
)

# Load macros template and expose macros
_macros_template = _jinja_env.get_template("macros.html")
_macros = _macros_template.module

# Regex to match git commit output: [branch hash] message
COMMIT_PATTERN = re.compile(r"\[[\w\-/]+ ([a-f0-9]{7,})\] (.+?)(?:\n|$)")

# Module-level variable for GitHub repo
_github_repo = None


def set_github_repo(repo: str | None) -> None:
    """Set the GitHub repo for commit links."""
    global _github_repo
    _github_repo = repo


def render_markdown_text(text: str) -> str:
    """Render markdown text to HTML."""
    if not text:
        return ""
    return markdown.markdown(text, extensions=["fenced_code", "tables"])


def render_user_text(text: str) -> str:
    """Render user text to HTML, escaping HTML entities for safety.

    User messages should not contain raw HTML, so we escape angle brackets
    before markdown processing to prevent HTML injection (e.g., <title> tags
    being interpreted by the browser).
    """
    if not text:
        return ""
    # Escape HTML entities before markdown processing
    # This prevents user-typed <tag> from being interpreted as HTML
    text = html.escape(text)
    return markdown.markdown(text, extensions=["fenced_code", "tables"])


def is_json_like(text: str) -> bool:
    """Check if text looks like JSON."""
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    return (text.startswith("{") and text.endswith("}")) or (
        text.startswith("[") and text.endswith("]")
    )


def format_json(obj) -> str:
    """Format object as pretty-printed JSON in a pre block."""
    try:
        if isinstance(obj, str):
            obj = json.loads(obj)
        formatted = json.dumps(obj, indent=2, ensure_ascii=False)
        return f'<pre class="json">{html.escape(formatted)}</pre>'
    except (json.JSONDecodeError, TypeError):
        return f"<pre>{html.escape(str(obj))}</pre>"


def render_todo_write(tool_input: dict, tool_id: str) -> str:
    """Render TodoWrite tool calls."""
    todos = tool_input.get("todos", [])
    if not todos:
        return ""
    return _macros.todo_list(todos, tool_id)


def render_write_tool(tool_input: dict, tool_id: str) -> str:
    """Render Write tool calls with file path header and content preview."""
    file_path = tool_input.get("file_path", "Unknown file")
    content = tool_input.get("content", "")
    return _macros.write_tool(file_path, content, tool_id)


def render_edit_tool(tool_input: dict, tool_id: str) -> str:
    """Render Edit tool calls with diff-like old/new display."""
    file_path = tool_input.get("file_path", "Unknown file")
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")
    replace_all = tool_input.get("replace_all", False)
    return _macros.edit_tool(file_path, old_string, new_string, replace_all, tool_id)


def render_bash_tool(tool_input: dict, tool_id: str) -> str:
    """Render Bash tool calls with command as plain text."""
    command = tool_input.get("command", "")
    description = tool_input.get("description", "")
    return _macros.bash_tool(command, description, tool_id)


def render_read_tool(tool_input: dict, tool_id: str) -> str:
    """Render Read tool calls with file path header and JSON details."""
    file_path = tool_input.get("file_path", "Unknown file")
    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    input_json = json.dumps(tool_input, indent=2, ensure_ascii=False)
    return _macros.read_tool(file_path, offset, limit, input_json, tool_id)


def render_content_block(block: dict) -> str:
    """Render a single content block to HTML."""
    if not isinstance(block, dict):
        return f"<p>{html.escape(str(block))}</p>"

    block_type = block.get("type", "")

    if block_type == "image":
        source = block.get("source", {})
        media_type = source.get("media_type", "image/png")
        data = source.get("data", "")
        return _macros.image_block(media_type, data)

    elif block_type == "thinking":
        content_html = render_markdown_text(block.get("thinking", ""))
        return _macros.thinking(content_html)

    elif block_type == "text":
        content_html = render_markdown_text(block.get("text", ""))
        return _macros.assistant_text(content_html)

    elif block_type == "tool_use":
        tool_name = block.get("name", "Unknown tool")
        tool_input = block.get("input", {})
        tool_id = block.get("id", "")

        if tool_name == "TodoWrite":
            return render_todo_write(tool_input, tool_id)
        if tool_name == "Write":
            return render_write_tool(tool_input, tool_id)
        if tool_name == "Edit":
            return render_edit_tool(tool_input, tool_id)
        if tool_name == "Bash":
            return render_bash_tool(tool_input, tool_id)
        if tool_name == "Read":
            return render_read_tool(tool_input, tool_id)

        # Generic tool rendering
        description = tool_input.get("description", "")
        display_input = {k: v for k, v in tool_input.items() if k != "description"}
        input_json = json.dumps(display_input, indent=2, ensure_ascii=False)
        return _macros.tool_use(tool_name, description, input_json, tool_id)

    elif block_type == "tool_result":
        content = block.get("content", "")
        is_error = block.get("is_error", False)

        # Check for git commits and render with styled cards
        if isinstance(content, str):
            commits_found = list(COMMIT_PATTERN.finditer(content))
            if commits_found:
                # Build commit cards + remaining content
                parts = []
                last_end = 0
                for match in commits_found:
                    # Add any content before this commit
                    before = content[last_end : match.start()].strip()
                    if before:
                        parts.append(f"<pre>{html.escape(before)}</pre>")

                    commit_hash = match.group(1)
                    commit_msg = match.group(2)
                    parts.append(
                        _macros.commit_card(commit_hash, commit_msg, _github_repo)
                    )
                    last_end = match.end()

                # Add any remaining content after last commit
                after = content[last_end:].strip()
                if after:
                    parts.append(f"<pre>{html.escape(after)}</pre>")

                content_html = "".join(parts)
            else:
                content_html = f"<pre>{html.escape(content)}</pre>"
        elif isinstance(content, list) or is_json_like(content):
            content_html = format_json(content)
        else:
            content_html = format_json(content)

        return _macros.tool_result(content_html, is_error)

    else:
        return format_json(block)


def is_tool_result_message(message_data: dict) -> bool:
    """Check if a user message contains only tool results."""
    content = message_data.get("content", [])
    if not isinstance(content, list):
        return False
    return all(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def render_user_message_content(message_data: dict) -> str:
    """Render user message content to HTML."""
    content = message_data.get("content", "")
    if isinstance(content, str):
        if is_json_like(content):
            return _macros.user_content(format_json(content))
        return _macros.user_content(render_user_text(content))
    elif isinstance(content, list):
        return "".join(render_content_block(block) for block in content)
    return f"<p>{html.escape(str(content))}</p>"


def render_assistant_message(message_data: dict) -> str:
    """Render assistant message content to HTML."""
    content = message_data.get("content", [])
    if not isinstance(content, list):
        return f"<p>{html.escape(str(content))}</p>"
    return "".join(render_content_block(block) for block in content)


def make_msg_id(timestamp: str) -> str:
    """Create a DOM-safe message ID from timestamp."""
    return f"msg-{timestamp.replace(':', '-').replace('.', '-')}"


def render_message(entry: dict) -> str:
    """Render a single message entry to HTML.

    Standalone function for backward compatibility.

    Args:
        entry: A parsed JSONL entry with type, timestamp, and message keys

    Returns:
        HTML string for the message, or empty string if invalid
    """
    log_type = entry.get("type")
    message_data = entry.get("message", {})
    timestamp = entry.get("timestamp", "")

    if not message_data:
        return ""

    usage = None
    model = None
    if log_type == "user":
        content_html = render_user_message_content(message_data)
        # Check if this is a tool result message
        if is_tool_result_message(message_data):
            role_class, role_label = "tool-reply", "Tool reply"
        else:
            role_class, role_label = "user", "User"
    elif log_type == "assistant":
        content_html = render_assistant_message(message_data)
        role_class, role_label = "assistant", "Assistant"
        # Extract usage data for assistant messages and calculate cost
        usage = message_data.get("usage", {})
        model = message_data.get("model")
        if usage:
            usage = dict(usage)  # Make a copy to avoid mutating the original
            usage["cost"] = calculate_message_cost(usage, model)
    else:
        return ""

    if not content_html.strip():
        return ""

    msg_id = make_msg_id(timestamp)
    return _macros.message(
        role_class, role_label, msg_id, timestamp, content_html, usage, model
    )


class ClaudeCodeRenderer:
    """Message renderer for Claude Code sessions."""

    def render_message(self, entry: dict) -> str:
        """Render a single message entry to HTML.

        Args:
            entry: A parsed JSONL entry with type, timestamp, and message keys

        Returns:
            HTML string for the message, or empty string if invalid
        """
        # Delegate to the standalone function
        return render_message(entry)
