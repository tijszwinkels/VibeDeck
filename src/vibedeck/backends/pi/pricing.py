"""Token pricing and usage calculation for Pi Coding Agent sessions.

Pi stores usage.cost.total on each assistant message, so no external
pricing module is needed - just sum the inline values.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..protocol import TokenUsage

logger = logging.getLogger(__name__)


def get_session_token_usage(session_path: Path) -> TokenUsage:
    """Calculate total token usage and cost from a pi session file.

    Reads all assistant messages and sums their usage fields.
    Pi provides cost inline, so no external pricing needed.

    Args:
        session_path: Path to the session JSONL file.

    Returns:
        TokenUsage with totals.
    """
    totals = TokenUsage()
    models_seen: set[str] = set()

    try:
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") != "message":
                        continue
                    msg = entry.get("message", {})
                    if msg.get("role") != "assistant":
                        continue
                    usage = msg.get("usage")
                    if not usage:
                        continue

                    totals.input_tokens += usage.get("input", 0)
                    totals.output_tokens += usage.get("output", 0)
                    totals.cache_read_tokens += usage.get("cacheRead", 0)
                    totals.cache_creation_tokens += usage.get("cacheWrite", 0)
                    totals.message_count += 1

                    cost = usage.get("cost", {})
                    if isinstance(cost, dict):
                        totals.cost += cost.get("total", 0)
                    elif isinstance(cost, (int, float)):
                        totals.cost += cost

                    model = msg.get("model")
                    if model and model not in models_seen:
                        models_seen.add(model)
                        totals.models.append(model)

                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, IOError):
        pass

    return totals


def get_session_model(session_path: Path) -> str | None:
    """Get the first model used in a session.

    Returns:
        Model ID string or None.
    """
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") != "message":
                        continue
                    msg = entry.get("message", {})
                    if msg.get("role") == "assistant":
                        model = msg.get("model")
                        if model:
                            return model
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, IOError):
        pass
    return None
