"""Token pricing and usage calculation for Claude Code sessions."""

from __future__ import annotations

import json
import logging
from importlib.resources import files
from pathlib import Path

import yaml

from ..protocol import TokenUsage

logger = logging.getLogger(__name__)

# Cache for pricing data
_pricing_data: dict | None = None

# Default pricing fallback if file cannot be loaded
_DEFAULT_PRICING = {
    "default": {
        "input": 3.0,
        "output": 15.0,
        "cache_write_5m": 3.75,
        "cache_write_1h": 3.75,
        "cache_read": 0.30,
    }
}


def _get_pricing_data() -> dict:
    """Load and cache pricing data from YAML file.

    Uses importlib.resources for robust path resolution that works
    in packaged distributions (zip imports, frozen executables).
    Falls back to default pricing if file cannot be loaded.
    """
    global _pricing_data
    if _pricing_data is None:
        try:
            # Use importlib.resources for robust package resource access
            pricing_file = files("claude_code_session_explorer").joinpath("pricing.yaml")
            _pricing_data = yaml.safe_load(pricing_file.read_text())
        except Exception as e:
            logger.warning(f"Failed to load pricing.yaml, using defaults: {e}")
            _pricing_data = _DEFAULT_PRICING
    return _pricing_data


def get_model_pricing(model: str) -> dict:
    """Get pricing for a model, falling back to default if not found.

    Args:
        model: Model ID (e.g., 'claude-opus-4-5-20251101')

    Returns:
        Dictionary with pricing fields: input, output, cache_write_5m,
        cache_write_1h, cache_read (all per million tokens)
    """
    pricing_data = _get_pricing_data()
    models = pricing_data.get("models", {})
    return models.get(model, pricing_data.get("default", {}))


def calculate_message_cost(usage: dict, model: str | None = None) -> float:
    """Calculate the cost in USD for a message's token usage.

    Args:
        usage: Token usage dictionary from message.usage
        model: Optional model ID for model-specific pricing

    Returns:
        Cost in USD
    """
    if not usage:
        return 0.0

    pricing = get_model_pricing(model) if model else _get_pricing_data().get("default", {})

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read_tokens = usage.get("cache_read_input_tokens", 0)

    # Get detailed cache write breakdown if available
    cache_creation = usage.get("cache_creation", {})
    cache_5m_tokens = cache_creation.get("ephemeral_5m_input_tokens", 0)
    cache_1h_tokens = cache_creation.get("ephemeral_1h_input_tokens", 0)

    # Fall back to total cache creation tokens if no breakdown
    total_cache_create = usage.get("cache_creation_input_tokens", 0)
    if cache_5m_tokens == 0 and cache_1h_tokens == 0 and total_cache_create > 0:
        # Assume 5m cache if no breakdown available
        cache_5m_tokens = total_cache_create

    # Calculate cost (prices are per million tokens)
    cost = 0.0
    cost += (input_tokens / 1_000_000) * pricing.get("input", 0)
    cost += (output_tokens / 1_000_000) * pricing.get("output", 0)
    cost += (cache_5m_tokens / 1_000_000) * pricing.get("cache_write_5m", 0)
    cost += (cache_1h_tokens / 1_000_000) * pricing.get("cache_write_1h", 0)
    cost += (cache_read_tokens / 1_000_000) * pricing.get("cache_read", 0)

    return cost


def get_session_token_usage(session_path: Path) -> TokenUsage:
    """Calculate total token usage and cost from a session file.

    Reads all assistant messages and sums up their usage fields.
    Deduplicates streaming messages by keeping only the final version
    of each API request (identified by message_id:request_id).

    Args:
        session_path: Path to the session JSONL file

    Returns:
        TokenUsage with totals for the session.
    """
    totals = TokenUsage()
    models_seen: set[str] = set()

    # Store deduplicated messages: dedup_hash -> (usage, model, output_tokens)
    # Keep the entry with highest output_tokens (the complete response)
    dedup_messages: dict[str, tuple[dict, str | None, int]] = {}

    try:
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "assistant":
                        message = entry.get("message", {})
                        usage = message.get("usage", {})
                        model = message.get("model")
                        msg_id = message.get("id")
                        request_id = entry.get("requestId")

                        if not usage:
                            continue

                        output_tokens = usage.get("output_tokens", 0)

                        # Deduplicate by message_id:request_id
                        if msg_id and request_id:
                            dedup_hash = f"{msg_id}:{request_id}"
                            existing = dedup_messages.get(dedup_hash)
                            # Keep the entry with highest output_tokens (complete response)
                            if existing is None or output_tokens > existing[2]:
                                dedup_messages[dedup_hash] = (usage, model, output_tokens)
                        else:
                            # No dedup info available, use a unique key
                            unique_key = f"no_dedup_{len(dedup_messages)}"
                            dedup_messages[unique_key] = (usage, model, output_tokens)

                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, IOError):
        pass

    # Sum up deduplicated messages
    for usage, model, _ in dedup_messages.values():
        if model and model not in models_seen:
            models_seen.add(model)
            totals.models.append(model)

        totals.input_tokens += usage.get("input_tokens", 0)
        totals.output_tokens += usage.get("output_tokens", 0)
        totals.cache_creation_tokens += usage.get("cache_creation_input_tokens", 0)
        totals.cache_read_tokens += usage.get("cache_read_input_tokens", 0)
        totals.message_count += 1
        totals.cost += calculate_message_cost(usage, model)

    return totals
