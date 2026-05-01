"""Subprocess environment scrubbing for Claude Code OAuth handoff.

In sandboxed deployments, the parent process (e.g. ``vibedeck serve`` running
as PID 1 in a container) may have been started with ``ANTHROPIC_AUTH_TOKEN``,
``ANTHROPIC_API_KEY`` and/or ``ANTHROPIC_BASE_URL`` exported, pointing the
Claude CLI at a default proxy (e.g. OpenRouter).

When the user later authenticates Claude Code via ``/login``, OAuth
credentials are persisted to ``~/.claude/.credentials.json``. From that point
on the user wants their own account to be used — but the env vars inherited
from the parent process still take precedence in the spawned ``claude``
subprocess, so the handoff requires a container restart unless we drop those
vars when building the child env.

This module returns a child env with the relevant variables removed iff the
OAuth credentials file is present.
"""

from __future__ import annotations

import os
from pathlib import Path

ANTHROPIC_PASSTHROUGH_VARS: tuple[str, ...] = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
)

CLAUDE_OAUTH_CREDENTIALS_PATH = Path("~/.claude/.credentials.json")


def claude_oauth_present() -> bool:
    """Return True if Claude Code OAuth credentials are configured."""
    return CLAUDE_OAUTH_CREDENTIALS_PATH.expanduser().is_file()


def scrub_anthropic_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of ``env`` (default ``os.environ``) with Anthropic
    passthrough vars stripped when Claude Code OAuth credentials exist.

    When OAuth credentials are not present the env is returned unchanged
    (still as an independent copy, so callers can mutate freely).
    """
    base: dict[str, str] = dict(os.environ if env is None else env)
    if claude_oauth_present():
        for var in ANTHROPIC_PASSTHROUGH_VARS:
            base.pop(var, None)
    return base
