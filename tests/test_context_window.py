"""Tests for context-window helpers."""

from __future__ import annotations

import json


def test_codex_context_limit_uses_effective_percent(tmp_path):
    """Codex should use the effective context limit from models_cache.json."""
    from vibedeck.backends.shared.context_window import (
        get_codex_context_limit_tokens,
    )

    cache_path = tmp_path / "models_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-5.4",
                        "context_window": 272000,
                        "effective_context_window_percent": 95,
                    }
                ]
            }
        )
    )

    assert get_codex_context_limit_tokens("gpt-5.4", cache_path=cache_path) == 258400


def test_claude_context_limit_detects_claude_models():
    """Claude-family models should report a 200K context window."""
    from vibedeck.backends.shared.context_window import (
        get_claude_context_limit_tokens,
    )

    assert (
        get_claude_context_limit_tokens("claude-sonnet-4-20250514") == 200000
    )
