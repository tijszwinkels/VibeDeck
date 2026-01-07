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
# Import the shared pricing utilities
try:
    from ..claude_code.pricing import get_model_pricing, calculate_message_cost
except ImportError:
    # Fallback if Claude Code backend not available
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
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)

        cost = 0.0
        cost += (input_tokens / 1_000_000) * pricing.get("input", 0)
        cost += (output_tokens / 1_000_000) * pricing.get("output", 0)
        cost += (cache_read / 1_000_000) * pricing.get("cache_read", 0)
        cost += (cache_create / 1_000_000) * pricing.get("cache_write_5m", 0)
        return cost


def get_session_token_usage(session_path: Path, storage_dir: Path) -> TokenUsage:
    """Calculate total token usage and cost from a session.

    OpenCode stores token data in step-finish parts. We aggregate all
    step-finish parts across all messages in the session.

    Args:
        session_path: Path to the session JSON file.
        storage_dir: Base storage directory.

    Returns:
        TokenUsage with totals for the session.
    """
    totals = TokenUsage()
    models_seen: set[str] = set()

    # Get session ID from path
    session_id = session_path.stem

    # Read all messages for this session
    msg_dir = storage_dir / "message" / session_id
    if not msg_dir.exists():
        return totals

    for msg_file in msg_dir.glob("*.json"):
        try:
            msg_data = json.loads(msg_file.read_text())
            message_id = msg_data.get("id")
            if not message_id:
                continue

            # Get model from message
            model_id = msg_data.get("modelID")
            provider_id = msg_data.get("providerID")
            if model_id and model_id not in models_seen:
                models_seen.add(model_id)
                # Store as provider/model format if available
                if provider_id:
                    totals.models.append(f"{provider_id}/{model_id}")
                else:
                    totals.models.append(model_id)

            # Check for cost/tokens directly on message (assistant messages)
            if msg_data.get("role") == "assistant":
                msg_cost = msg_data.get("cost")
                msg_tokens = msg_data.get("tokens")
                if msg_tokens:
                    totals.input_tokens += msg_tokens.get("input", 0)
                    totals.output_tokens += msg_tokens.get("output", 0)
                    totals.cache_read_tokens += msg_tokens.get("cacheRead", 0)
                    totals.cache_creation_tokens += msg_tokens.get("cacheWrite", 0)
                    totals.message_count += 1

                    # Calculate cost if not provided
                    if msg_cost:
                        totals.cost += msg_cost
                    else:
                        # Build usage dict for cost calculation
                        usage = {
                            "input_tokens": msg_tokens.get("input", 0),
                            "output_tokens": msg_tokens.get("output", 0),
                            "cache_read_input_tokens": msg_tokens.get("cacheRead", 0),
                            "cache_creation_input_tokens": msg_tokens.get(
                                "cacheWrite", 0
                            ),
                        }
                        totals.cost += calculate_message_cost(usage, model_id)
                    continue  # Already got tokens from message, skip parts

            # Read parts to find step-finish data
            part_dir = storage_dir / "part" / message_id
            if not part_dir.exists():
                continue

            for part_file in part_dir.glob("*.json"):
                try:
                    part_data = json.loads(part_file.read_text())
                    if part_data.get("type") == "step-finish":
                        # step-finish contains token usage
                        tokens = part_data.get("tokens", {})
                        cost = part_data.get("cost", 0)

                        totals.input_tokens += tokens.get("input", 0)
                        totals.output_tokens += tokens.get("output", 0)
                        totals.cache_read_tokens += tokens.get("cacheRead", 0)
                        totals.cache_creation_tokens += tokens.get("cacheWrite", 0)

                        if cost:
                            totals.cost += cost
                        else:
                            # Calculate cost from tokens
                            usage = {
                                "input_tokens": tokens.get("input", 0),
                                "output_tokens": tokens.get("output", 0),
                                "cache_read_input_tokens": tokens.get("cacheRead", 0),
                                "cache_creation_input_tokens": tokens.get(
                                    "cacheWrite", 0
                                ),
                            }
                            totals.cost += calculate_message_cost(usage, model_id)

                        totals.message_count += 1

                except (json.JSONDecodeError, IOError) as e:
                    logger.debug(f"Failed to read part file {part_file}: {e}")

        except (json.JSONDecodeError, IOError) as e:
            logger.debug(f"Failed to read message file {msg_file}: {e}")

    return totals
