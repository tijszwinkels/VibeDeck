"""Render Codex transcript entries to HTML."""

from __future__ import annotations

import html

from ..shared.rendering import (
    macros,
    format_json,
    is_json_like,
    make_msg_id,
    render_markdown_text,
    render_user_text,
)
from .response_items import get_function_call_output_state


def _render_message_blocks(content: list[dict], role: str) -> str:
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        text = block.get("text", "")
        if block_type in {"input_text", "output_text"}:
            rendered = (
                render_user_text(text) if role == "user" else render_markdown_text(text)
            )
            if role == "user":
                parts.append(macros.user_content(rendered))
            else:
                parts.append(macros.assistant_text(rendered))
    return "".join(parts)


def render_message(entry: dict) -> str:
    """Render a single Codex transcript entry."""
    timestamp = entry.get("timestamp", "")
    msg_id = make_msg_id(timestamp or "codex")
    payload = entry.get("payload", {})
    entry_type = entry.get("type")
    payload_type = payload.get("type")

    if entry_type != "response_item":
        return ""

    if payload_type == "message":
        role = payload.get("role", "")
        content = payload.get("content", [])
        if not isinstance(content, list):
            return ""
        content_html = _render_message_blocks(content, role)
        if not content_html:
            return ""
        role_class = "assistant" if role == "assistant" else "user"
        role_label = "Assistant" if role == "assistant" else "User"
        return macros.message(
            role_class, role_label, msg_id, timestamp, content_html, None, None
        )

    if payload_type == "function_call":
        arguments = payload.get("arguments", "")
        tool_input = format_json(arguments) if is_json_like(arguments) else f"<pre>{html.escape(str(arguments))}</pre>"
        content_html = macros.tool_use(payload.get("name", "tool"), "", tool_input, payload.get("call_id", ""))
        return macros.message(
            "assistant", "Assistant", msg_id, timestamp, content_html, None, None
        )

    if payload_type == "function_call_output":
        output, is_error = get_function_call_output_state(payload)
        if is_json_like(output):
            content_html = macros.tool_result(format_json(output), is_error)
        else:
            content_html = macros.tool_result(
                f"<pre>{html.escape(str(output))}</pre>", is_error
            )
        return macros.message(
            "assistant", "Assistant", msg_id, timestamp, content_html, None, None
        )

    return ""


class CodexRenderer:
    """Renderer wrapper for the Codex backend."""

    def render_message(self, entry: dict) -> str:
        return render_message(entry)
