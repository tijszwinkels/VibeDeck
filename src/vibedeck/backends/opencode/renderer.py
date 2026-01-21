"""Message rendering for OpenCode sessions.

Renders OpenCode session messages to HTML for display in the browser.
OpenCode uses a different message format with separate message info and parts.

Message format:
    entry = {
        "info": {
            "id": "msg_xxx",
            "role": "user" | "assistant",
            "time": {"created": unix_ms, "updated": unix_ms},
            "modelID": "claude-sonnet-4-5",
            "providerID": "anthropic",
            "tokens": {
                "input": 100,
                "output": 50,
                "cache": {"read": 1000, "write": 50}
            },
            ...
        },
        "parts": [
            {"type": "text", "text": "...", "id": "part_xxx"},
            {"type": "tool", "name": "...", "state": {...}, "id": "part_xxx"},
            {"type": "reasoning", "reasoning": "...", "id": "part_xxx"},
            {
                "type": "step-finish",
                "cost": 0.01,
                "tokens": {
                    "input": 100,
                    "output": 50,
                    "cache": {"read": 1000, "write": 50}
                },
                "id": "part_xxx"
            },
            ...
        ]
    }
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

from ..shared.rendering import (
    macros,
    render_markdown_text,
    render_user_text,
    is_json_like,
    format_json,
    make_msg_id,
    render_git_commits,
)
from .pricing import calculate_message_cost

# Re-export set_github_repo for backward compatibility
from ..shared.rendering import set_github_repo  # noqa: F401


def format_timestamp(unix_ms: int | float) -> str:
    """Format Unix milliseconds as ISO timestamp."""
    try:
        dt = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError, OSError):
        return ""


def render_text_part(part: dict) -> str:
    """Render a text part."""
    text = part.get("text", "")
    content_html = render_markdown_text(text)
    return macros.assistant_text(content_html)


def render_reasoning_part(part: dict) -> str:
    """Render a reasoning/thinking part (collapsible)."""
    reasoning = part.get("reasoning", "")
    content_html = render_markdown_text(reasoning)
    return macros.thinking(content_html)


def render_tool_part(part: dict) -> str:
    """Render a tool call part.

    OpenCode tool parts have a different structure:
    {
        "type": "tool",
        "id": "prt_xxx",
        "tool": "bash",  # tool name as string
        "callID": "toolu_xxx",
        "state": {
            "status": "pending" | "running" | "completed" | "error",
            "input": {...},
            "output": {...},
            "error": "...",
            "title": "...",  # Optional description
            "time": {...}
        }
    }
    """
    # Tool name can be in "tool" field (string) or "name" field
    tool_field = part.get("tool", "")
    if isinstance(tool_field, str):
        tool_name = tool_field or part.get("name", "Unknown tool")
    elif isinstance(tool_field, dict):
        tool_name = tool_field.get("name", "Unknown tool")
    else:
        tool_name = part.get("name", "Unknown tool")

    # Capitalize tool name for display
    tool_name = tool_name.capitalize() if tool_name else "Unknown tool"
    state = part.get("state", {})
    status = state.get("status", "pending")
    tool_input = state.get("input", {})
    tool_output = state.get("output")
    tool_error = state.get("error")
    tool_id = part.get("id", "")

    # Handle specific tool types
    if tool_name == "TodoWrite":
        todos = tool_input.get("todos", [])
        if todos:
            return macros.todo_list(todos, tool_id)

    if tool_name == "Write":
        file_path = tool_input.get("file_path") or tool_input.get(
            "filePath", "Unknown file"
        )
        content = tool_input.get("content", "")
        return macros.write_tool(file_path, content, tool_id)

    if tool_name == "Edit":
        file_path = tool_input.get("file_path") or tool_input.get(
            "filePath", "Unknown file"
        )
        old_string = tool_input.get("old_string") or tool_input.get("oldString", "")
        new_string = tool_input.get("new_string") or tool_input.get("newString", "")
        replace_all = tool_input.get("replace_all") or tool_input.get(
            "replaceAll", False
        )
        return macros.edit_tool(file_path, old_string, new_string, replace_all, tool_id)

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        description = tool_input.get("description", "")
        result_html = macros.bash_tool(command, description, tool_id)

        # Add output if available
        if status == "completed" and tool_output:
            output_str = (
                str(tool_output) if not isinstance(tool_output, str) else tool_output
            )
            result_html += macros.tool_result(
                f"<pre>{html.escape(output_str)}</pre>", False
            )
        elif status == "error" and tool_error:
            result_html += macros.tool_result(
                f"<pre>{html.escape(str(tool_error))}</pre>", True
            )

        return result_html

    # Generic tool rendering
    description = tool_input.get("description", "")
    display_input = {k: v for k, v in tool_input.items() if k != "description"}
    input_json = (
        json.dumps(display_input, indent=2, ensure_ascii=False)
        if display_input
        else "{}"
    )

    result_html = macros.tool_use(tool_name, description, input_json, tool_id)

    # Add tool result/output
    if status == "completed" and tool_output is not None:
        if isinstance(tool_output, str):
            # Check for git commits using shared utility
            commit_html = render_git_commits(tool_output)
            if commit_html:
                content_html = commit_html
            else:
                content_html = f"<pre>{html.escape(tool_output)}</pre>"
        else:
            content_html = format_json(tool_output)
        result_html += macros.tool_result(content_html, False)
    elif status == "error" and tool_error:
        result_html += macros.tool_result(
            f"<pre>{html.escape(str(tool_error))}</pre>", True
        )
    elif status in ("pending", "running"):
        # Show status indicator for in-progress tools
        status_text = "Running..." if status == "running" else "Pending..."
        result_html += f'<div class="tool-status">{status_text}</div>'

    return result_html


def render_step_finish_part(part: dict) -> str:
    """Render a step-finish part (shows token usage)."""
    # Usage is shown in message header, not as separate part
    return ""


def render_file_part(part: dict) -> str:
    """Render a file attachment part."""
    file_path = part.get("path") or part.get("file", "")
    file_name = file_path.split("/")[-1] if file_path else "file"

    # Check if it's an image
    mime = part.get("mime", "")
    if mime.startswith("image/") or any(
        file_name.lower().endswith(ext)
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]
    ):
        data = part.get("data", "")
        if data:
            return macros.image_block(mime or "image/png", data)

    return f'<div class="file-attachment"><span class="file-icon">📎</span> {html.escape(file_name)}</div>'


def render_part(part: dict) -> str:
    """Render a single part based on its type."""
    part_type = part.get("type", "")

    if part_type == "text":
        return render_text_part(part)
    elif part_type == "reasoning":
        return render_reasoning_part(part)
    elif part_type == "tool":
        return render_tool_part(part)
    elif part_type == "step-finish":
        return render_step_finish_part(part)
    elif part_type == "file":
        return render_file_part(part)
    elif part_type == "step-start":
        # step-start is just metadata, don't render
        return ""
    elif part_type == "agent":
        # Agent switch - show as info
        agent = part.get("agent", "")
        return (
            f'<div class="agent-info">Agent: {html.escape(agent)}</div>'
            if agent
            else ""
        )
    elif part_type in ("snapshot", "patch", "compaction", "subtask", "retry"):
        # Internal parts, don't render
        return ""
    else:
        # Unknown part type - render as JSON
        return format_json(part)


def render_user_message(info: dict, parts: list[dict]) -> str:
    """Render a user message."""
    content_parts = []

    for part in parts:
        part_type = part.get("type", "")
        if part_type == "text":
            text = part.get("text", "").strip()
            # Strip surrounding quotes if present (from session explorer send)
            if text.startswith('"') and text.endswith('"') and len(text) > 1:
                text = text[1:-1]
            if is_json_like(text):
                content_parts.append(macros.user_content(format_json(text)))
            else:
                content_parts.append(macros.user_content(render_user_text(text)))
        elif part_type == "file":
            content_parts.append(render_file_part(part))
        # Other part types for user messages are rare

    return "".join(content_parts) if content_parts else ""


def render_assistant_message(info: dict, parts: list[dict]) -> str:
    """Render an assistant message with all its parts."""
    content_parts = []

    for part in parts:
        rendered = render_part(part)
        if rendered:
            content_parts.append(rendered)

    return "".join(content_parts)


def render_message(entry: dict) -> str:
    """Render a single message entry to HTML.

    Args:
        entry: An OpenCode message entry with 'info' and 'parts' keys.

    Returns:
        HTML string for the message, or empty string if invalid.
    """
    info = entry.get("info", {})
    parts = entry.get("parts", [])
    role = info.get("role", "")

    if not role:
        return ""

    # Get timestamp
    time_data = info.get("time", {})
    created = time_data.get("created")
    timestamp = format_timestamp(created) if created else ""

    # Build content HTML
    if role == "user":
        content_html = render_user_message(info, parts)
        role_class, role_label = "user", "User"
    elif role == "assistant":
        content_html = render_assistant_message(info, parts)
        role_class, role_label = "assistant", "Assistant"
    else:
        return ""

    if not content_html.strip():
        return ""

    # Extract usage data for assistant messages
    usage = None
    model = None
    if role == "assistant":
        model_id = info.get("modelID")
        provider_id = info.get("providerID")
        model = f"{provider_id}/{model_id}" if provider_id and model_id else model_id

        # Get tokens from message or step-finish parts
        tokens = info.get("tokens", {})
        cost = info.get("cost")

        # Check step-finish parts if not on message
        if not tokens:
            for part in parts:
                if part.get("type") == "step-finish":
                    tokens = part.get("tokens", {})
                    cost = part.get("cost", cost)
                    break

        if tokens:
            cache = tokens.get("cache", {})
            usage = {
                "input_tokens": tokens.get("input", 0),
                "output_tokens": tokens.get("output", 0),
                "cache_read_input_tokens": cache.get("read", 0),
                "cache_creation_input_tokens": cache.get("write", 0),
            }
            # Calculate cost if not provided
            if cost:
                usage["cost"] = cost
            else:
                usage["cost"] = calculate_message_cost(usage, model_id)

    msg_id = make_msg_id(timestamp)
    return macros.message(
        role_class, role_label, msg_id, timestamp, content_html, usage, model
    )


class OpenCodeRenderer:
    """Message renderer for OpenCode sessions."""

    def render_message(self, entry: dict) -> str:
        """Render a single message entry to HTML.

        Args:
            entry: An OpenCode message entry with 'info' and 'parts' keys.

        Returns:
            HTML string for the message, or empty string if invalid.
        """
        return render_message(entry)
