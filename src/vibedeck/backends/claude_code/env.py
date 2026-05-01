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

The scrub is gated on the spawning backend: only Claude Code subprocesses
have their Anthropic passthrough vars dropped. Other backends (OpenCode,
Codex, Pi) can legitimately use Anthropic providers via inherited env, so
this module leaves their env intact.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocol import CodingToolBackend

ANTHROPIC_PASSTHROUGH_VARS: tuple[str, ...] = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
)

CLAUDE_CODE_NORMALIZER_KEY = "claude_code"

CLAUDE_OAUTH_CREDENTIALS_PATH = Path("~/.claude/.credentials.json")


def claude_oauth_present() -> bool:
    """Return True if Claude Code OAuth credentials are configured."""
    return CLAUDE_OAUTH_CREDENTIALS_PATH.expanduser().is_file()


def scrub_anthropic_env(
    env: dict[str, str] | None = None,
    *,
    backend: "CodingToolBackend | None" = None,
) -> dict[str, str]:
    """Return a copy of ``env`` (default ``os.environ``) with Anthropic
    passthrough vars stripped when the OAuth handoff applies.

    The vars are dropped iff Claude Code OAuth credentials exist at
    ``~/.claude/.credentials.json`` AND either:

    * ``backend`` is ``None`` (caller is not spawning a backend CLI — e.g. the
      embedded terminal, where any ``claude`` invocation should pick up the
      user's OAuth credentials over inherited env); or
    * ``backend.normalizer_key`` is ``claude_code`` (the spawned CLI is the
      Claude Code one).

    For non-Claude-Code backends (OpenCode, Codex, Pi) the env is returned
    unchanged — those backends can legitimately rely on ``ANTHROPIC_*``
    inherited from the parent process.
    """
    base: dict[str, str] = dict(os.environ if env is None else env)
    if not claude_oauth_present():
        return base
    if backend is not None:
        normalizer_key = getattr(backend, "normalizer_key", None)
        if normalizer_key != CLAUDE_CODE_NORMALIZER_KEY:
            return base
    for var in ANTHROPIC_PASSTHROUGH_VARS:
        base.pop(var, None)
    return base
