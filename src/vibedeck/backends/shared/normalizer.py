"""Normalization layer for backend-agnostic message representation.

Converts backend-specific raw dicts (Claude Code JSONL entries, OpenCode
message+parts) into a unified NormalizedMessage format. This is consumed by
the JSON SSE stream, JSON REST endpoints, and the markdown exporter.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..codex.response_items import get_function_call_output_state

logger = logging.getLogger(__name__)


@dataclass
class ContentBlock:
    """A single content block within a message."""

    type: str  # "text" | "thinking" | "tool_use" | "tool_result" | "image"

    # Text / thinking
    text: str | None = None

    # Tool use
    tool_name: str | None = None
    tool_id: str | None = None
    tool_input: dict | None = None

    # Tool result
    tool_use_id: str | None = None
    content: str | list | None = None  # string or list of content parts
    is_error: bool = False

    # Image
    media_type: str | None = None
    data: str | None = None  # base64

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict, omitting None and empty fields."""
        d = {"type": self.type}
        for field_name in [
            "text", "tool_name", "tool_id", "tool_input",
            "tool_use_id", "content", "is_error",
            "media_type", "data",
        ]:
            val = getattr(self, field_name)
            if val is not None and val is not False and val != "" and val != {}:
                d[field_name] = val
        # Always include is_error for tool_result
        if self.type == "tool_result":
            d["is_error"] = self.is_error
        return d


@dataclass
class NormalizedMessage:
    """Backend-agnostic message representation."""

    role: str  # "user" | "assistant"
    timestamp: str  # ISO 8601
    blocks: list[ContentBlock]
    model: str | None = None
    stop_reason: str | None = None  # "end_turn" | "tool_use" | None
    usage: dict | None = None  # {input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, cost}

    def to_dict(self) -> dict:
        d = {
            "role": self.role,
            "timestamp": self.timestamp,
            "blocks": [b.to_dict() for b in self.blocks],
        }
        if self.model is not None:
            d["model"] = self.model
        if self.stop_reason is not None:
            d["stop_reason"] = self.stop_reason
        if self.usage is not None:
            d["usage"] = self.usage
        return d


# ===== Claude Code normalization =====


def _is_no_content_placeholder(message_data: dict) -> bool:
    """Check if a message is a streaming placeholder with literal '(no content)'.

    Claude Code writes these as the initial chunk for Opus 4.5 responses
    before real content arrives.
    """
    if message_data.get("stop_reason") is not None:
        return False
    content = message_data.get("content", [])
    if not isinstance(content, list) or len(content) != 1:
        return False
    block = content[0]
    return (
        isinstance(block, dict)
        and block.get("type") == "text"
        and block.get("text", "").strip() == "(no content)"
    )


def _normalize_claude_code_content_block(block: dict) -> ContentBlock | None:
    """Normalize a single Claude Code content block."""
    if not isinstance(block, dict):
        return None

    block_type = block.get("type", "")

    if block_type == "text":
        return ContentBlock(type="text", text=block.get("text", ""))
    elif block_type == "thinking":
        return ContentBlock(type="thinking", text=block.get("thinking", ""))
    elif block_type == "tool_use":
        return ContentBlock(
            type="tool_use",
            tool_name=block.get("name", ""),
            tool_id=block.get("id", ""),
            tool_input=block.get("input", {}),
        )
    elif block_type == "tool_result":
        return ContentBlock(
            type="tool_result",
            tool_use_id=block.get("tool_use_id", ""),
            content=block.get("content"),
            is_error=block.get("is_error", False),
        )
    elif block_type == "image":
        source = block.get("source", {})
        return ContentBlock(
            type="image",
            media_type=source.get("media_type", "image/png"),
            data=source.get("data", ""),
        )
    else:
        # Unknown block type — skip
        return None


def normalize_claude_code_message(entry: dict) -> NormalizedMessage | None:
    """Normalize a Claude Code JSONL entry.

    Returns None for entries that should be skipped (wrong type, placeholders, etc).
    """
    log_type = entry.get("type")
    if log_type not in ("user", "assistant"):
        return None

    message_data = entry.get("message", {})
    if not message_data:
        return None

    # Skip "(no content)" placeholder entries
    if log_type == "assistant" and _is_no_content_placeholder(message_data):
        return None

    timestamp = entry.get("timestamp", "")
    content = message_data.get("content", "")
    model = message_data.get("model")
    stop_reason = message_data.get("stop_reason")

    # Build blocks
    blocks: list[ContentBlock] = []
    if isinstance(content, str):
        if content.strip():
            blocks.append(ContentBlock(type="text", text=content))
    elif isinstance(content, list):
        for raw_block in content:
            normalized = _normalize_claude_code_content_block(raw_block)
            if normalized is not None:
                blocks.append(normalized)

    if not blocks:
        return None

    # Extract usage for assistant messages
    usage = None
    if log_type == "assistant":
        raw_usage = message_data.get("usage")
        if raw_usage:
            usage = _normalize_claude_code_usage(raw_usage, model)

    return NormalizedMessage(
        role=log_type,
        timestamp=timestamp,
        blocks=blocks,
        model=model,
        stop_reason=stop_reason,
        usage=usage,
    )


def _normalize_claude_code_usage(raw_usage: dict, model: str | None) -> dict:
    """Normalize Claude Code usage dict to standard format with cost."""
    from ..claude_code.pricing import calculate_message_cost

    usage = {
        "input_tokens": raw_usage.get("input_tokens", 0),
        "output_tokens": raw_usage.get("output_tokens", 0),
        "cache_creation_tokens": raw_usage.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": raw_usage.get("cache_read_input_tokens", 0),
    }
    usage["cost"] = calculate_message_cost(raw_usage, model)
    return usage


# ===== OpenCode normalization =====


def _format_timestamp_ms(unix_ms: int | float) -> str:
    """Format Unix milliseconds as ISO 8601 timestamp."""
    try:
        dt = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError, OSError):
        return ""


def _normalize_opencode_tool_name(part: dict) -> str:
    """Extract and capitalize tool name from an OpenCode tool part."""
    tool_field = part.get("tool", "")
    if isinstance(tool_field, str):
        name = tool_field or part.get("name", "Unknown")
    elif isinstance(tool_field, dict):
        name = tool_field.get("name", "Unknown")
    else:
        name = part.get("name", "Unknown")
    return name.capitalize() if name else "Unknown"


def _normalize_opencode_part(part: dict) -> list[ContentBlock]:
    """Normalize a single OpenCode part.

    Returns a list because tool parts can expand into two blocks (tool_use + tool_result).
    """
    part_type = part.get("type", "")

    if part_type == "text":
        text = part.get("text", "")
        return [ContentBlock(type="text", text=text)] if text else []

    elif part_type == "reasoning":
        reasoning = part.get("reasoning", "")
        return [ContentBlock(type="thinking", text=reasoning)] if reasoning else []

    elif part_type == "tool":
        tool_name = _normalize_opencode_tool_name(part)
        call_id = part.get("callID", "")
        state = part.get("state", {})
        status = state.get("status", "pending")
        tool_input = state.get("input", {})

        blocks = [
            ContentBlock(
                type="tool_use",
                tool_name=tool_name,
                tool_id=call_id,
                tool_input=tool_input,
            )
        ]

        # Add tool_result if completed or errored
        if status == "completed" and state.get("output") is not None:
            output = state["output"]
            if not isinstance(output, str):
                import json
                output = json.dumps(output, indent=2)
            blocks.append(ContentBlock(
                type="tool_result",
                tool_use_id=call_id,
                content=output,
                is_error=False,
            ))
        elif status == "error" and state.get("error"):
            blocks.append(ContentBlock(
                type="tool_result",
                tool_use_id=call_id,
                content=state["error"],
                is_error=True,
            ))

        return blocks

    elif part_type == "file":
        mime = part.get("mime", "")
        data = part.get("data", "")
        file_path = part.get("path") or part.get("file", "")
        file_name = file_path.split("/")[-1] if file_path else "file"

        if mime.startswith("image/") or any(
            file_name.lower().endswith(ext)
            for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]
        ):
            if data:
                return [ContentBlock(
                    type="image",
                    media_type=mime or "image/png",
                    data=data,
                )]
        # Non-image file
        return [ContentBlock(type="text", text=f"[File: {file_name}]")]

    elif part_type in ("step-finish", "step-start", "snapshot", "patch",
                       "compaction", "subtask", "retry", "agent"):
        # Internal parts — skip
        return []

    else:
        # Unknown part type — skip
        return []


def normalize_opencode_message(entry: dict) -> NormalizedMessage | None:
    """Normalize an OpenCode message entry.

    Returns None for entries that should be skipped.
    """
    info = entry.get("info", {})
    parts = entry.get("parts", [])
    role = info.get("role", "")

    if role not in ("user", "assistant"):
        return None

    # Timestamp
    time_data = info.get("time", {})
    created = time_data.get("created")
    timestamp = _format_timestamp_ms(created) if created else ""

    # Model
    model_id = info.get("modelID")
    provider_id = info.get("providerID")
    model = f"{provider_id}/{model_id}" if provider_id and model_id else model_id

    # Build blocks
    blocks: list[ContentBlock] = []
    for part in parts:
        blocks.extend(_normalize_opencode_part(part))

    if not blocks:
        return None

    # Extract usage for assistant messages
    usage = None
    if role == "assistant":
        usage = _extract_opencode_usage(info, parts, model_id)

    return NormalizedMessage(
        role=role,
        timestamp=timestamp,
        blocks=blocks,
        model=model,
        usage=usage,
    )


def _extract_opencode_usage(info: dict, parts: list[dict], model_id: str | None) -> dict | None:
    """Extract usage info from OpenCode message info or step-finish parts."""
    from ..opencode.pricing import calculate_message_cost

    # Try message-level tokens first (authoritative)
    tokens = info.get("tokens")
    cost = info.get("cost")

    # Fall back to step-finish parts
    if not tokens:
        for part in parts:
            if part.get("type") == "step-finish":
                tokens = part.get("tokens")
                cost = part.get("cost", cost)
                break

    if not tokens:
        return None

    cache = tokens.get("cache", {})
    usage = {
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0),
        "cache_read_tokens": cache.get("read", 0),
        "cache_creation_tokens": cache.get("write", 0),
    }

    if cost is not None:
        usage["cost"] = cost
    else:
        raw_usage = {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cache_read_input_tokens": usage["cache_read_tokens"],
            "cache_creation_input_tokens": usage["cache_creation_tokens"],
        }
        usage["cost"] = calculate_message_cost(raw_usage, model_id)

    return usage


# ===== Pi normalization =====


def _normalize_pi_content_block(block: dict) -> ContentBlock | None:
    """Normalize a single Pi content block."""
    if not isinstance(block, dict):
        return None

    block_type = block.get("type", "")

    if block_type == "text":
        return ContentBlock(type="text", text=block.get("text", ""))
    elif block_type == "thinking":
        return ContentBlock(type="thinking", text=block.get("thinking", ""))
    elif block_type == "toolCall":
        return ContentBlock(
            type="tool_use",
            tool_name=block.get("name", ""),
            tool_id=block.get("id", ""),
            tool_input=block.get("arguments", {}),
        )
    elif block_type == "image":
        return ContentBlock(
            type="image",
            media_type=block.get("mimeType", "image/png"),
            data=block.get("data", ""),
        )
    return None


def normalize_pi_message(entry: dict) -> NormalizedMessage | None:
    """Normalize a Pi JSONL entry.

    Returns None for entries that should be skipped.
    """
    entry_type = entry.get("type")

    # Handle compaction entries
    if entry_type == "compaction":
        summary = entry.get("summary", "")
        tokens_before = entry.get("tokensBefore", 0)
        text = f"Context compacted ({tokens_before:,} tokens): {summary}"
        return NormalizedMessage(
            role="system",
            timestamp=entry.get("timestamp", ""),
            blocks=[ContentBlock(type="text", text=text)],
        )

    # Handle branch_summary entries
    if entry_type == "branch_summary":
        summary = entry.get("summary", "")
        text = f"Branch summary: {summary}"
        return NormalizedMessage(
            role="system",
            timestamp=entry.get("timestamp", ""),
            blocks=[ContentBlock(type="text", text=text)],
        )

    if entry_type != "message":
        return None

    msg = entry.get("message", {})
    if not msg:
        return None

    role = msg.get("role", "")
    timestamp = entry.get("timestamp", "")

    if role == "user":
        content = msg.get("content", "")
        blocks: list[ContentBlock] = []
        if isinstance(content, str):
            if content.strip():
                blocks.append(ContentBlock(type="text", text=content))
        elif isinstance(content, list):
            for raw_block in content:
                normalized = _normalize_pi_content_block(raw_block)
                if normalized:
                    blocks.append(normalized)
        if not blocks:
            return None
        return NormalizedMessage(role="user", timestamp=timestamp, blocks=blocks)

    elif role == "assistant":
        content = msg.get("content", [])
        blocks = []
        if isinstance(content, list):
            for raw_block in content:
                normalized = _normalize_pi_content_block(raw_block)
                if normalized:
                    blocks.append(normalized)
        if not blocks:
            return None

        # Extract usage
        usage = None
        raw_usage = msg.get("usage")
        if raw_usage:
            cost = raw_usage.get("cost", {})
            cost_total = cost.get("total", 0) if isinstance(cost, dict) else cost
            usage = {
                "input_tokens": raw_usage.get("input", 0),
                "output_tokens": raw_usage.get("output", 0),
                "cache_read_tokens": raw_usage.get("cacheRead", 0),
                "cache_creation_tokens": raw_usage.get("cacheWrite", 0),
                "cost": cost_total,
            }

        return NormalizedMessage(
            role="assistant",
            timestamp=timestamp,
            blocks=blocks,
            model=msg.get("model"),
            stop_reason=msg.get("stopReason"),
            usage=usage,
        )

    elif role == "toolResult":
        content = msg.get("content", [])
        tool_content = ""
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            tool_content = "\n".join(parts)
        elif isinstance(content, str):
            tool_content = content

        return NormalizedMessage(
            role="assistant",
            timestamp=timestamp,
            blocks=[
                ContentBlock(
                    type="tool_result",
                    tool_use_id=msg.get("toolCallId", ""),
                    content=tool_content,
                    is_error=msg.get("isError", False),
                )
            ],
        )

    elif role == "bashExecution":
        command = msg.get("command", "")
        output = msg.get("output", "")
        exit_code = msg.get("exitCode", 0)
        return NormalizedMessage(
            role="assistant",
            timestamp=timestamp,
            blocks=[
                ContentBlock(
                    type="tool_use",
                    tool_name="bash",
                    tool_id=f"bash-{entry.get('id', '')}",
                    tool_input={"command": command},
                ),
                ContentBlock(
                    type="tool_result",
                    tool_use_id=f"bash-{entry.get('id', '')}",
                    content=output,
                    is_error=exit_code != 0,
                ),
            ],
        )

    return None


# ===== Dispatch =====


# ===== Codex normalization =====


def _normalize_codex_text_blocks(content: list[dict]) -> list[ContentBlock]:
    """Normalize Codex message content blocks to text blocks."""
    blocks: list[ContentBlock] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in {"input_text", "output_text"}:
            continue
        text = block.get("text", "")
        if text:
            blocks.append(ContentBlock(type="text", text=text))
    return blocks


def _parse_codex_tool_input(arguments) -> dict:
    """Best-effort parsing of Codex function_call arguments."""
    if isinstance(arguments, dict):
        return arguments
    if not arguments:
        return {}
    if not isinstance(arguments, str):
        return {"raw": str(arguments)}

    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {"raw": arguments}

    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _normalize_codex_function_call(payload: dict, timestamp: str) -> NormalizedMessage:
    """Normalize Codex function_call into an assistant tool_use message."""
    return NormalizedMessage(
        role="assistant",
        timestamp=timestamp,
        blocks=[
            ContentBlock(
                type="tool_use",
                tool_name=str(payload.get("name") or "tool"),
                tool_id=str(payload.get("call_id") or payload.get("id") or ""),
                tool_input=_parse_codex_tool_input(payload.get("arguments")),
            )
        ],
    )


def _normalize_codex_function_call_output(payload: dict, timestamp: str) -> NormalizedMessage:
    """Normalize Codex function_call_output into an assistant tool_result message."""
    content, is_error = get_function_call_output_state(payload)

    return NormalizedMessage(
        role="assistant",
        timestamp=timestamp,
        blocks=[
            ContentBlock(
                type="tool_result",
                tool_use_id=str(payload.get("call_id") or ""),
                content=content,
                is_error=is_error,
            )
        ],
    )


def normalize_codex_message(entry: dict) -> NormalizedMessage | None:
    """Normalize a Codex transcript entry."""
    if entry.get("type") != "response_item":
        return None

    payload = entry.get("payload", {})
    if not isinstance(payload, dict):
        return None

    payload_type = payload.get("type")
    timestamp = entry.get("timestamp", "")

    if payload_type == "message":
        role = payload.get("role")
        if role not in {"user", "assistant"}:
            return None

        content = payload.get("content", [])
        if not isinstance(content, list):
            return None

        blocks = _normalize_codex_text_blocks(content)
        if not blocks:
            return None

        return NormalizedMessage(role=role, timestamp=timestamp, blocks=blocks)

    if payload_type == "function_call":
        return _normalize_codex_function_call(payload, timestamp)

    if payload_type == "function_call_output":
        return _normalize_codex_function_call_output(payload, timestamp)

    return None


def normalize_message(entry: dict, backend: str) -> NormalizedMessage | None:
    """Dispatch to backend-specific normalizer.

    Args:
        entry: Raw message entry (backend-specific format).
        backend: Backend name ("claude_code", "opencode", "codex", or "pi").

    Returns:
        NormalizedMessage or None if entry should be skipped.
    """
    if backend == "claude_code":
        return normalize_claude_code_message(entry)
    elif backend == "opencode":
        return normalize_opencode_message(entry)
    elif backend == "codex":
        return normalize_codex_message(entry)
    elif backend == "pi":
        return normalize_pi_message(entry)
    return None
