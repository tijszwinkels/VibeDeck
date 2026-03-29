"""Message rendering for Pi Coding Agent sessions.

Renders pi session messages to HTML using shared Jinja2 macros.
"""

from __future__ import annotations

import html
import json

from ..shared.rendering import (
    macros,
    render_markdown_text,
    render_user_text,
    make_msg_id,
    format_json,
)


def _render_content_block(block: dict) -> str:
    """Render a single pi content block to HTML."""
    if not isinstance(block, dict):
        return f"<p>{html.escape(str(block))}</p>"

    block_type = block.get("type", "")

    if block_type == "thinking":
        content_html = render_markdown_text(block.get("thinking", ""))
        return macros.thinking(content_html)

    elif block_type == "text":
        content_html = render_markdown_text(block.get("text", ""))
        return macros.assistant_text(content_html)

    elif block_type == "toolCall":
        tool_name = block.get("name", "Unknown")
        tool_id = block.get("id", "")
        arguments = block.get("arguments", {})

        # Special handling for bash tool
        if tool_name == "bash" and "command" in arguments:
            return macros.bash_tool(
                arguments["command"],
                arguments.get("description", ""),
                tool_id,
            )

        # Generic tool rendering
        input_json = json.dumps(arguments, indent=2, ensure_ascii=False)
        return macros.tool_use(tool_name, "", input_json, tool_id)

    elif block_type == "image":
        media_type = block.get("mimeType", "image/png")
        data = block.get("data", "")
        return macros.image_block(media_type, data)

    else:
        return format_json(block)


def _render_user_content(msg: dict) -> str:
    """Render user message content."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return macros.user_content(render_user_text(content))
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    parts.append(
                        macros.user_content(render_user_text(block.get("text", "")))
                    )
                elif block_type == "image":
                    media_type = block.get("mimeType", "image/png")
                    data = block.get("data", "")
                    parts.append(macros.image_block(media_type, data))
                else:
                    parts.append(format_json(block))
            else:
                parts.append(f"<p>{html.escape(str(block))}</p>")
        return "".join(parts)
    return f"<p>{html.escape(str(content))}</p>"


def _render_assistant_content(msg: dict) -> str:
    """Render assistant message content blocks."""
    content = msg.get("content", [])
    if not isinstance(content, list):
        return f"<p>{html.escape(str(content))}</p>"
    return "".join(_render_content_block(block) for block in content)


def _render_tool_result(msg: dict) -> str:
    """Render a toolResult message."""
    content = msg.get("content", [])
    is_error = msg.get("isError", False)

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(f"<pre>{html.escape(item.get('text', ''))}</pre>")
            else:
                parts.append(format_json(item))
        content_html = "".join(parts)
    elif isinstance(content, str):
        content_html = f"<pre>{html.escape(content)}</pre>"
    else:
        content_html = format_json(content)

    return macros.tool_result(content_html, is_error)


def _render_bash_execution(msg: dict) -> str:
    """Render a bashExecution message."""
    command = msg.get("command", "")
    output = msg.get("output", "")
    exit_code = msg.get("exitCode", 0)

    # Use bash_tool macro for the command
    tool_html = macros.bash_tool(command, "", f"bash-{id(msg)}")

    # Add output as tool result
    is_error = exit_code != 0
    exit_label = f" (exit code {exit_code})" if exit_code != 0 else ""
    output_html = f"<pre>{html.escape(output)}</pre>"
    if exit_label:
        output_html += f"<div class='exit-code'>{html.escape(exit_label)}</div>"

    result_html = macros.tool_result(output_html, is_error)
    return tool_html + result_html


def _render_compaction(entry: dict) -> str:
    """Render a compaction summary entry."""
    summary = entry.get("summary", "")
    tokens_before = entry.get("tokensBefore", 0)
    label = f"Context compacted ({tokens_before:,} tokens)"
    content_html = f'<div class="compaction-summary"><strong>{html.escape(label)}</strong><p>{html.escape(summary)}</p></div>'
    return content_html


def _render_branch_summary(entry: dict) -> str:
    """Render a branch_summary entry."""
    summary = entry.get("summary", "")
    content_html = f'<div class="branch-summary"><strong>Branch summary</strong><p>{html.escape(summary)}</p></div>'
    return content_html


class PiRenderer:
    """Message renderer for Pi Coding Agent sessions."""

    def render_message(self, entry: dict) -> str:
        """Render a pi session entry to HTML."""
        entry_type = entry.get("type", "")

        # Handle non-message entry types
        if entry_type == "compaction":
            content_html = _render_compaction(entry)
            timestamp = entry.get("timestamp", "")
            msg_id = make_msg_id(timestamp)
            return macros.message(
                "system", "System", msg_id, timestamp, content_html, None, None
            )

        if entry_type == "branch_summary":
            content_html = _render_branch_summary(entry)
            timestamp = entry.get("timestamp", "")
            msg_id = make_msg_id(timestamp)
            return macros.message(
                "system", "System", msg_id, timestamp, content_html, None, None
            )

        if entry_type == "custom_message":
            display = entry.get("display", False)
            if not display:
                return ""
            custom_type = entry.get("customType", "extension")
            content = entry.get("content", "")
            if isinstance(content, str):
                content_html = render_markdown_text(content)
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(render_markdown_text(block.get("text", "")))
                content_html = "".join(parts)
            else:
                content_html = format_json(content)
            timestamp = entry.get("timestamp", "")
            msg_id = make_msg_id(timestamp)
            return macros.message(
                "system", custom_type, msg_id, timestamp, content_html, None, None
            )

        if entry_type != "message":
            return ""

        msg = entry.get("message", {})
        if not msg:
            return ""

        role = msg.get("role", "")
        timestamp = entry.get("timestamp", "")
        msg_id = make_msg_id(timestamp)

        if role == "user":
            content_html = _render_user_content(msg)
            if not content_html.strip():
                return ""
            return macros.message(
                "user", "User", msg_id, timestamp, content_html, None, None
            )

        elif role == "assistant":
            content_html = _render_assistant_content(msg)
            if not content_html.strip():
                return ""
            usage = msg.get("usage")
            model = msg.get("model")
            if usage:
                # Normalize pi usage format for the macro
                usage = dict(usage)
                usage.setdefault("input_tokens", usage.get("input", 0))
                usage.setdefault("output_tokens", usage.get("output", 0))
                cost = usage.get("cost", {})
                if isinstance(cost, dict):
                    usage["cost"] = cost.get("total", 0)
            return macros.message(
                "assistant",
                "Assistant",
                msg_id,
                timestamp,
                content_html,
                usage,
                model,
            )

        elif role == "toolResult":
            content_html = _render_tool_result(msg)
            return macros.message(
                "tool-reply", "Tool reply", msg_id, timestamp, content_html, None, None
            )

        elif role == "bashExecution":
            content_html = _render_bash_execution(msg)
            return macros.message(
                "tool-reply",
                "Bash",
                msg_id,
                timestamp,
                content_html,
                None,
                None,
            )

        elif role == "custom" and msg.get("display"):
            custom_type = msg.get("customType", "custom")
            content = msg.get("content", "")
            if isinstance(content, str):
                content_html = render_markdown_text(content)
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(render_markdown_text(block.get("text", "")))
                content_html = "".join(parts)
            else:
                content_html = format_json(content)
            return macros.message(
                "system", custom_type, msg_id, timestamp, content_html, None, None
            )

        elif role == "compactionSummary":
            summary = msg.get("summary", "")
            tokens_before = msg.get("tokensBefore", 0)
            label = f"Context compacted ({tokens_before:,} tokens)"
            content_html = f'<div class="compaction-summary"><strong>{html.escape(label)}</strong><p>{html.escape(summary)}</p></div>'
            return macros.message(
                "system", "System", msg_id, timestamp, content_html, None, None
            )

        elif role == "branchSummary":
            summary = msg.get("summary", "")
            content_html = f'<div class="branch-summary"><strong>Branch summary</strong><p>{html.escape(summary)}</p></div>'
            return macros.message(
                "system", "System", msg_id, timestamp, content_html, None, None
            )

        return ""
