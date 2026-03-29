"""Pi Coding Agent CLI interaction.

Handles building commands for the Pi CLI tool (`pi`) for sending messages,
forking sessions, and starting new sessions.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from ..protocol import CommandSpec
from .discovery import DEFAULT_SESSIONS_DIR

logger = logging.getLogger(__name__)

CLI_COMMAND = "pi"
CLI_INSTALL_INSTRUCTIONS = "Install with: npm install -g @mariozechner/pi-coding-agent"
PREFERRED_SUMMARY_MODELS = (
    "openai-codex/gpt-5.4-mini",
    "openai/gpt-5.4-mini",
)

# Prefer stable providers when resolving bare model IDs (e.g., "gpt-5.4")
# to avoid matching providers that are configured but missing credentials.
PREFERRED_MODEL_PROVIDERS = (
    "openai",
    "google-gemini-cli",
    "anthropic",
)


def is_cli_available() -> bool:
    """Check if the Pi CLI is installed and available."""
    return shutil.which(CLI_COMMAND) is not None


def find_session_file(session_id: str, sessions_dir: Path | None = None) -> Path | None:
    """Find a session file by its UUID.

    Searches the Pi sessions directory for a JSONL file whose filename
    contains the given session UUID.

    Args:
        session_id: Session UUID (or partial UUID).
        sessions_dir: Base sessions directory (defaults to ~/.pi/agent/sessions).

    Returns:
        Path to the session file, or None if not found.
    """
    if sessions_dir is None:
        sessions_dir = DEFAULT_SESSIONS_DIR

    if not sessions_dir.exists():
        return None

    for f in sessions_dir.glob("--*--/*.jsonl"):
        if session_id in f.stem:
            return f
    return None


def build_send_command(
    session_id: str,
    message: str,
    skip_permissions: bool = False,
    model: str | None = None,
    output_format: str | None = None,
    add_dirs: list[str] | None = None,
    sessions_dir: Path | None = None,
) -> CommandSpec:
    """Build CLI command to send a message to an existing Pi session.

    Uses --session <path> to resume a specific session file.

    Args:
        session_id: Session UUID.
        message: Message text.
        skip_permissions: Unused (Pi has no permission skip flag).
        model: Model to use (e.g., "gemini-2.5-pro").
        output_format: Unused (Pi has no output format flag).
        add_dirs: Unused (Pi has no add-dirs flag).
        sessions_dir: Override sessions directory for file lookup.

    Returns:
        CommandSpec with args and message as stdin.

    Raises:
        FileNotFoundError: If the session file cannot be found.
    """
    session_file = find_session_file(session_id, sessions_dir)
    if session_file is None:
        raise FileNotFoundError(
            f"Cannot find Pi session file for session ID: {session_id}"
        )

    cmd = [CLI_COMMAND, "-p", "--session", str(session_file)]
    if model:
        cmd.extend(["--model", model])
    return CommandSpec(args=cmd, stdin=message)


def build_fork_command(
    session_id: str,
    message: str,
    skip_permissions: bool = False,
    model: str | None = None,
    output_format: str | None = None,
    add_dirs: list[str] | None = None,
) -> CommandSpec:
    """Build CLI command to fork a Pi session.

    Uses --fork <uuid> which supports partial UUID matching.

    Args:
        session_id: Session UUID (or partial UUID).
        message: Initial message for forked session.
        skip_permissions: Unused.
        model: Model to use.
        output_format: Unused.
        add_dirs: Unused.

    Returns:
        CommandSpec with args and message as stdin.
    """
    cmd = [CLI_COMMAND, "-p", "--fork", session_id]
    if model:
        cmd.extend(["--model", model])
    return CommandSpec(args=cmd, stdin=message)


def build_new_session_command(
    message: str,
    skip_permissions: bool = False,
    model: str | None = None,
    output_format: str | None = None,
    add_dirs: list[str] | None = None,
) -> CommandSpec:
    """Build CLI command to start a new Pi session.

    Args:
        message: Initial message.
        skip_permissions: Unused.
        model: Model to use (e.g., "gemini-2.5-pro", "gpt-5.4").
        output_format: Unused.
        add_dirs: Unused.

    Returns:
        CommandSpec with args and message as stdin.
    """
    cmd = [CLI_COMMAND, "-p"]
    if model:
        cmd.extend(["--model", model])
    return CommandSpec(args=cmd, stdin=message)


def get_available_models() -> list[str]:
    """Get available models by parsing `pi --list-models` output.

    Parses the tabular output and returns model identifiers in
    "provider/model" format (e.g., "google-gemini-cli/gemini-2.5-pro").

    Returns:
        List of model identifier strings, or empty list on failure.
    """
    try:
        result = subprocess.run(
            [CLI_COMMAND, "--list-models"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning(
                "pi --list-models failed (exit %d): %s",
                result.returncode,
                result.stderr.strip(),
            )
            return []

        # Pi outputs the model table to stderr
        output = result.stdout or result.stderr
        return _parse_list_models_output(output)

    except FileNotFoundError:
        logger.warning("Pi CLI not found, cannot list models")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("pi --list-models timed out")
        return []
    except Exception as exc:
        logger.warning("Failed to get Pi models: %s", exc)
        return []


def resolve_summary_model(
    requested_model: str | None, available_models: list[str] | None = None
) -> str | None:
    """Resolve a summary model alias to an exact Pi model ID.

    Pi accepts model patterns, but VibeDeck's summary defaults use Claude-style
    aliases like ``haiku``. For Pi we prefer an exact, known-small model to
    avoid ambiguous or unsupported matches.

    Priority:
    1. Keep the requested model if it is already an exact available model.
    2. For the ``haiku`` alias, prefer hard-coded small-model fallbacks.
    3. Otherwise return the original requested model unchanged.
    """
    if not requested_model:
        return requested_model

    base_model = requested_model.split(":", 1)[0]
    models = available_models if available_models is not None else get_available_models()
    if not models:
        return requested_model

    # For summarization, prefer known-good small models whenever the request is
    # a bare alias/model ID (e.g. "haiku", "gpt-5.4") or any Haiku variant.
    # This avoids provider-pattern ambiguity and deprecated Haiku IDs.
    is_qualified_model = "/" in base_model
    is_haiku_request = "haiku" in base_model.lower()
    should_prefer_small_model = (not is_qualified_model) or is_haiku_request

    if should_prefer_small_model:
        for model in PREFERRED_SUMMARY_MODELS:
            if model in models:
                return model

    if requested_model in models:
        return requested_model
    if base_model in models:
        return base_model

    # Resolve bare model IDs (e.g. "gpt-5.4") to an exact provider/model.
    # This avoids ambiguous pattern matching selecting a provider without creds.
    suffix_matches = [m for m in models if m.endswith(f"/{base_model}")]
    if suffix_matches:
        for provider in PREFERRED_MODEL_PROVIDERS:
            for match in suffix_matches:
                if match.startswith(f"{provider}/"):
                    return match
        return suffix_matches[0]

    if not is_haiku_request:
        return requested_model

    for model in PREFERRED_SUMMARY_MODELS:
        if model in models:
            return model

    for model in models:
        if "haiku" in model.lower():
            return model

    return requested_model


def _parse_list_models_output(output: str) -> list[str]:
    """Parse the tabular output of `pi --list-models`.

    Expected format:
        provider           model             context  max-out  thinking  images
        google-gemini-cli  gemini-2.5-flash  1.0M     65.5K    yes       yes

    Returns:
        List of "provider/model" strings.
    """
    lines = output.strip().splitlines()
    if len(lines) < 2:
        return []

    models = []
    for line in lines[1:]:  # Skip header
        parts = line.split()
        if len(parts) >= 2:
            provider = parts[0]
            model = parts[1]
            models.append(f"{provider}/{model}")

    return models
