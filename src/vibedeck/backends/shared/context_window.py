"""Helpers for deriving effective context limits from model metadata."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CLAUDE_CONTEXT_LIMIT_TOKENS = 200_000


def get_claude_context_limit_tokens(model: str | None) -> int | None:
    """Return the Claude context window for known Claude-family models."""
    if not model:
        return None

    normalized = model.strip().lower()
    if normalized.startswith("claude-"):
        return CLAUDE_CONTEXT_LIMIT_TOKENS
    return None


def get_codex_context_limit_tokens(
    model: str | None,
    cache_path: Path | None = None,
) -> int | None:
    """Return the effective context limit for a Codex model from models_cache."""
    if not model:
        return None

    cache_path = cache_path or (Path.home() / ".codex" / "models_cache.json")
    try:
        data = json.loads(cache_path.read_text())
    except FileNotFoundError:
        logger.debug("Codex models cache not found at %s", cache_path)
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read Codex models cache %s: %s", cache_path, exc)
        return None

    for entry in data.get("models", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("slug") != model:
            continue

        context_window = entry.get("context_window")
        if not isinstance(context_window, int) or context_window <= 0:
            return None

        effective_percent = entry.get("effective_context_window_percent", 100)
        if not isinstance(effective_percent, int) or effective_percent <= 0:
            effective_percent = 100

        return context_window * effective_percent // 100

    return None
