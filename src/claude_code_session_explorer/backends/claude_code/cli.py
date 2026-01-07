"""Claude Code CLI interaction.

Handles building commands for the Claude Code CLI tool and managing
session indexing.
"""

from __future__ import annotations

import shutil
from pathlib import Path


CLI_COMMAND = "claude"
CLI_INSTALL_INSTRUCTIONS = "Install with: npm install -g @anthropic-ai/claude-code"


def is_cli_available() -> bool:
    """Check if the Claude CLI is installed and available.

    Returns:
        True if the 'claude' command is found in PATH.
    """
    return shutil.which(CLI_COMMAND) is not None


def ensure_session_indexed(session_id: str) -> None:
    """Ensure a session is in Claude's session index so --resume works.

    Claude uses ~/.claude/session-env/<session-id>/ directories as its index.
    Sessions created externally (via -p mode) don't get indexed automatically.

    Args:
        session_id: Session ID to ensure is indexed.
    """
    session_env_dir = Path.home() / ".claude" / "session-env" / session_id
    session_env_dir.mkdir(parents=True, exist_ok=True)


def build_send_command(
    session_id: str,
    message: str,
    skip_permissions: bool = False,
) -> list[str]:
    """Build the CLI command to send a message to an existing session.

    Args:
        session_id: Session to send to.
        message: Message text.
        skip_permissions: Skip permission prompts.

    Returns:
        Command arguments list.
    """
    cmd = [CLI_COMMAND, "-p", message, "--resume", session_id]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    return cmd


def build_fork_command(
    session_id: str,
    message: str,
    skip_permissions: bool = False,
) -> list[str]:
    """Build the CLI command to fork a session with conversation history.

    Args:
        session_id: Session to fork from.
        message: Initial message for forked session.
        skip_permissions: Skip permission prompts.

    Returns:
        Command arguments list.
    """
    cmd = [CLI_COMMAND, "-p", message, "--resume", session_id, "--fork-session"]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    return cmd


def build_new_session_command(
    message: str,
    skip_permissions: bool = False,
) -> list[str]:
    """Build the CLI command to start a new session.

    Args:
        message: Initial message.
        skip_permissions: Skip permission prompts.

    Returns:
        Command arguments list.
    """
    cmd = [CLI_COMMAND, "-p", message]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    return cmd
