"""Token pricing and usage calculation for OpenCode sessions.

OpenCode stores token usage in step-finish parts within each message.
This module aggregates that data to calculate total usage and cost.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..protocol import TokenUsage

logger = logging.getLogger(__name__)

# OpenCode uses the same pricing file as Claude Code since it uses Claude models
try:
    from ..claude_code.pricing import get_model_pricing, calculate_message_cost
except ImportError:

    def get_model_pricing(model: str) -> dict:
        return {
            "input": 3.0,
            "output": 15.0,
            "cache_write_5m": 3.75,
            "cache_write_1h": 3.75,
            "cache_read": 0.30,
        }

    def calculate_message_cost(usage: dict, model: str | None = None) -> float:
        if not usage:
            return 0.0
        pricing = get_model_pricing(model) if model else get_model_pricing("")
        cost = 0.0
        cost += (usage.get("input_tokens", 0) / 1_000_000) * pricing.get("input", 0)
        cost += (usage.get("output_tokens", 0) / 1_000_000) * pricing.get("output", 0)
        cost += (usage.get("cache_read_input_tokens", 0) / 1_000_000) * pricing.get(
            "cache_read", 0
        )
        cost += (usage.get("cache_creation_input_tokens", 0) / 1_000_000) * pricing.get(
            "cache_write_5m", 0
        )
        return cost


def get_session_token_usage(session_path: Path, storage_dir: Path) -> TokenUsage:
    """Calculate total token usage and cost from a session."""

    totals = TokenUsage()
    models_seen: set[str] = set()
    session_id = session_path.stem

    msg_dir = storage_dir / "message" / session_id
    if not msg_dir.exists():
        return totals

    for msg_file in msg_dir.glob("*.json"):
        try:
            msg_data = json.loads(msg_file.read_text())
        except (json.JSONDecodeError, IOError):
            continue

        message_id = msg_data.get("id")
        if not message_id:
            continue

        model_id = msg_data.get("modelID")
        provider_id = msg_data.get("providerID")
        if model_id and model_id not in models_seen:
            models_seen.add(model_id)
            totals.models.append(
                f"{provider_id}/{model_id}" if provider_id else model_id
            )

        # Assistant message-level tokens (authoritative if present)
        if msg_data.get("role") == "assistant" and msg_data.get("tokens"):
            tokens = msg_data["tokens"]
            cache = tokens.get("cache", {})
            totals.input_tokens += tokens.get("input", 0)
            totals.output_tokens += tokens.get("output", 0)
            totals.cache_read_tokens += cache.get("read", 0)
            totals.cache_creation_tokens += cache.get("write", 0)
            totals.message_count += 1

            if msg_data.get("cost"):
                totals.cost += msg_data["cost"]
            else:
                usage = {
                    "input_tokens": tokens.get("input", 0),
                    "output_tokens": tokens.get("output", 0),
                    "cache_read_input_tokens": cache.get("read", 0),
                    "cache_creation_input_tokens": cache.get("write", 0),
                }
                totals.cost += calculate_message_cost(usage, model_id)
            continue

        # Otherwise aggregate step-finish parts
        part_dir = storage_dir / "part" / message_id
        if not part_dir.exists():
            continue

        for part_file in part_dir.glob("*.json"):
            try:
                part_data = json.loads(part_file.read_text())
            except (json.JSONDecodeError, IOError):
                continue

            if part_data.get("type") != "step-finish":
                continue

            tokens = part_data.get("tokens", {})
            cache = tokens.get("cache", {})
            totals.input_tokens += tokens.get("input", 0)
            totals.output_tokens += tokens.get("output", 0)
            totals.cache_read_tokens += cache.get("read", 0)
            totals.cache_creation_tokens += cache.get("write", 0)
            totals.message_count += 1

            if part_data.get("cost"):
                totals.cost += part_data["cost"]
            else:
                usage = {
                    "input_tokens": tokens.get("input", 0),
                    "output_tokens": tokens.get("output", 0),
                    "cache_read_input_tokens": cache.get("read", 0),
                    "cache_creation_input_tokens": cache.get("write", 0),
                }
                totals.cost += calculate_message_cost(usage, model_id)

    return totals
