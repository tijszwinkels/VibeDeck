"""OpenCode CLI interaction.

Handles building commands for the OpenCode CLI tool.

Key differences from Claude Code:
- OpenCode uses `opencode run "message"` for non-interactive mode
- Session resume: `opencode run -s {session_id} "message"`
- No --fork CLI flag (forking requires SDK/server)
- No --dangerously-skip-permissions (permissions configured in opencode.json)
"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


CLI_COMMAND = "opencode"
CLI_INSTALL_INSTRUCTIONS = (
    "Install OpenCode: npm install -g opencode or see https://opencode.ai/docs/"
)


def is_cli_available() -> bool:
    """Check if the OpenCode CLI is installed and available.

    Returns:
        True if the 'opencode' command is found in PATH.
    """
    return shutil.which(CLI_COMMAND) is not None


def ensure_session_indexed(session_id: str) -> None:
    """Ensure a session is indexed/known to the CLI tool.

    OpenCode doesn't require separate indexing - sessions are stored
    in a database-like structure and are always accessible by ID.

    Args:
        session_id: Session ID (no-op for OpenCode).
    """
    # OpenCode sessions are always accessible by ID
    pass


def build_send_command(
    session_id: str,
    message: str,
    skip_permissions: bool = False,
) -> list[str]:
    """Build the CLI command to send a message to an existing session.

    Args:
        session_id: Session to send to.
        message: Message text.
        skip_permissions: Ignored for OpenCode (uses config file).

    Returns:
        Command arguments list.
    """
    # opencode run -s {session_id} "{message}"
    cmd = [CLI_COMMAND, "run", "-s", session_id, message]
    return cmd


def build_fork_command(
    session_id: str,
    message: str,
    skip_permissions: bool = False,
) -> list[str]:
    """Build the CLI command to fork a session.

    OpenCode does not support forking via CLI - it requires the SDK/server.

    Args:
        session_id: Session to fork from.
        message: Initial message for forked session.
        skip_permissions: Ignored.

    Raises:
        NotImplementedError: Always, as fork is not supported via CLI.
    """
    raise NotImplementedError(
        "OpenCode does not support session forking via CLI. "
        "Use `opencode serve` and the SDK to fork sessions."
    )


def build_new_session_command(
    message: str,
    skip_permissions: bool = False,
    model: str | None = None,
) -> list[str]:
    """Build the CLI command to start a new session.

    Args:
        message: Initial message.
        skip_permissions: Ignored for OpenCode (uses config file).
        model: Model to use (e.g., "anthropic/claude-sonnet-4-5"). Optional.

    Returns:
        Command arguments list.
    """
    # opencode run [-m model] "{message}"
    cmd = [CLI_COMMAND, "run"]
    if model:
        cmd.extend(["-m", model])
    cmd.append(message)
    return cmd


def get_available_models() -> list[str]:
    """Get list of available models from OpenCode CLI.

    Returns:
        List of model identifiers (e.g., ["anthropic/claude-sonnet-4-5", ...]).
        Returns empty list if CLI is not available or command fails.
    """
    if not is_cli_available():
        return []

    try:
        result = subprocess.run(
            [CLI_COMMAND, "models"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"opencode models command failed: {result.stderr}")
            return []

        # Parse output - one model per line
        models = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line and "/" in line:  # Model format is "provider/model"
                models.append(line)
        return models
    except subprocess.TimeoutExpired:
        logger.warning("opencode models command timed out")
        return []
    except Exception as e:
        logger.warning(f"Failed to get OpenCode models: {e}")
        return []
