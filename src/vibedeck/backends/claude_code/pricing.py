"""Token pricing and usage calculation for Claude Code sessions."""

from __future__ import annotations

import json
import logging
from importlib.resources import files
from pathlib import Path

import yaml

from ..protocol import TokenUsage

logger = logging.getLogger(__name__)

# Characters per token estimate for output (English text averages ~3.5 chars/token)
CHARS_PER_TOKEN = 3.5


def estimate_output_tokens_from_content(content: list | str) -> int:
    """Estimate output tokens from message content.

    Claude Code's JSONL files contain incorrect output_tokens values (small
    streaming counters like 1-5 instead of actual token counts). This function
    estimates tokens from the actual content.

    Args:
        content: Message content - either a string or list of content blocks

    Returns:
        Estimated token count
    """
    if isinstance(content, str):
        return max(1, int(len(content) / CHARS_PER_TOKEN))

    if not isinstance(content, list):
        return 0

    total_chars = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type", "")
        if block_type == "text":
            total_chars += len(block.get("text", ""))
        elif block_type == "thinking":
            total_chars += len(block.get("thinking", ""))
        elif block_type == "tool_use":
            # Serialize tool call to JSON to estimate its token count
            total_chars += len(json.dumps(block, ensure_ascii=False))

    return max(1, int(total_chars / CHARS_PER_TOKEN)) if total_chars > 0 else 0

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
            pricing_file = files("vibedeck").joinpath("pricing.yaml")
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


def get_session_model(session_path: Path) -> str | None:
    """Get the first model used in a session.

    Reads the session file and returns the model from the first assistant message.

    Args:
        session_path: Path to the session JSONL file

    Returns:
        Model ID string (e.g., 'claude-opus-4-5-20251101') or None if not found.
    """
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
                        model = message.get("model")
                        if model:
                            return model
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, IOError):
        pass
    return None


def get_session_token_usage(session_path: Path) -> TokenUsage:
    """Calculate total token usage and cost from a session file.

    Reads all assistant messages and sums up their usage fields.
    Claude Code's JSONL files have unreliable output_tokens values (they appear
    to be internal streaming counters, not actual token counts). We estimate
    output tokens from the actual message content instead.

    Args:
        session_path: Path to the session JSONL file

    Returns:
        TokenUsage with totals for the session.
    """
    totals = TokenUsage()
    models_seen: set[str] = set()

    # Group entries by message.id to combine content and get accurate estimates
    # Structure: msg_id -> {usage, model, content_list}
    message_data: dict[str, dict] = {}

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
                        content = message.get("content", [])

                        if not usage:
                            continue

                        if msg_id:
                            if msg_id not in message_data:
                                # First entry for this message
                                message_data[msg_id] = {
                                    "usage": dict(usage),
                                    "model": model,
                                    "all_content": list(content) if content else [],
                                }
                            else:
                                # Additional chunk - merge content, update usage
                                if content:
                                    message_data[msg_id]["all_content"].extend(content)
                                message_data[msg_id]["usage"] = dict(usage)
                                if model:
                                    message_data[msg_id]["model"] = model
                        else:
                            # No message ID - treat as unique message
                            unique_key = f"no_id_{len(message_data)}"
                            message_data[unique_key] = {
                                "usage": dict(usage),
                                "model": model,
                                "all_content": list(content) if content else [],
                            }

                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, IOError):
        pass

    # Sum up all messages with estimated output tokens
    for data in message_data.values():
        usage = data["usage"]
        model = data["model"]
        all_content = data["all_content"]

        # Estimate output tokens from content
        estimated_output = estimate_output_tokens_from_content(all_content)

        if model and model not in models_seen:
            models_seen.add(model)
            totals.models.append(model)

        totals.input_tokens += usage.get("input_tokens", 0)
        totals.output_tokens += estimated_output
        totals.cache_creation_tokens += usage.get("cache_creation_input_tokens", 0)
        totals.cache_read_tokens += usage.get("cache_read_input_tokens", 0)
        totals.message_count += 1

        # Calculate cost with estimated output tokens
        usage_for_cost = dict(usage)
        usage_for_cost["output_tokens"] = estimated_output
        totals.cost += calculate_message_cost(usage_for_cost, model)

    return totals
