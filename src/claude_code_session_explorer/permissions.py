"""Permission handling for Claude Code sessions.

Handles parsing permission denials from CLI output and updating
Claude Code settings files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)


class PermissionDenial(TypedDict, total=False):
    """A single permission denial from Claude CLI."""

    tool_name: str
    tool_use_id: str
    tool_input: dict
    # Added fields for distinguishing denial types
    is_sandbox_denial: bool
    error_message: str


# Patterns that indicate a sandbox/directory restriction vs a tool permission denial
SANDBOX_DENIAL_PATTERNS = [
    "was blocked. For security, Claude Code may only",
    "only list files in the allowed working directories",
    "only access files within",
]


def is_sandbox_denial_message(message: str) -> bool:
    """Check if an error message indicates a sandbox/directory denial.

    Sandbox denials are different from tool permission denials:
    - Sandbox: Directory access restricted, requires --add-dir flag
    - Permission: Tool not allowed, can be granted via settings.json

    Args:
        message: The error message from tool result

    Returns:
        True if this is a sandbox denial
    """
    return any(pattern in message for pattern in SANDBOX_DENIAL_PATTERNS)


def parse_permission_denials(stdout: str) -> list[PermissionDenial]:
    """Parse Claude CLI JSON stream output for permission denials.

    The stream contains newline-delimited JSON objects. The final object
    with type="result" contains the permission_denials array. We also
    look at tool_use_result messages to get error details and distinguish
    sandbox denials from permission denials.

    Args:
        stdout: Raw stdout from Claude CLI with --output-format stream-json

    Returns:
        List of permission denial dicts, empty if none found.
        Each denial includes is_sandbox_denial and error_message fields.
    """
    denials: list[PermissionDenial] = []
    tool_errors: dict[str, str] = {}  # tool_use_id -> error message

    # First pass: collect tool error messages
    for line in stdout.strip().split("\n"):
        if not line:
            continue
        try:
            obj = json.loads(line)
            # Collect error messages from tool results
            if obj.get("type") == "user":
                # Look for tool_use_result which contains error messages
                tool_use_result = obj.get("tool_use_result")
                error_msg = ""
                if isinstance(tool_use_result, str):
                    error_msg = tool_use_result
                elif isinstance(tool_use_result, dict):
                    error_msg = tool_use_result.get("content", "")

                if error_msg and isinstance(error_msg, str) and error_msg.startswith("Error: "):
                    # Extract tool_use_id from the message content
                    content = obj.get("message", {}).get("content", [])
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_result" and item.get("is_error"):
                            tool_id = item.get("tool_use_id")
                            if tool_id:
                                tool_errors[tool_id] = item.get("content", "")
        except json.JSONDecodeError:
            continue

    # Second pass: get permission denials and enrich with error info
    for line in stdout.strip().split("\n"):
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get("type") == "result":
                raw_denials = obj.get("permission_denials", [])
                for denial in raw_denials:
                    tool_id = denial.get("tool_use_id", "")
                    error_msg = tool_errors.get(tool_id, "")
                    denial["error_message"] = error_msg
                    denial["is_sandbox_denial"] = is_sandbox_denial_message(error_msg)
                denials = raw_denials
                break
        except json.JSONDecodeError:
            continue

    return denials


def update_permissions_file(settings_path: Path, new_permissions: list[str]) -> None:
    """Add permissions to a Claude settings.json file.

    Creates the file and parent directories if they don't exist.
    Preserves existing permissions and other settings.

    Args:
        settings_path: Path to the settings.json file
        new_permissions: List of permission strings to add (e.g., ["Bash(npm:*)", "Read"])

    Raises:
        IOError: If the file cannot be written
        json.JSONDecodeError: If existing file contains invalid JSON
    """
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing settings or create new
    settings: dict = {}
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in {settings_path}, creating new file")
            settings = {}

    # Ensure permissions.allow exists
    if "permissions" not in settings:
        settings["permissions"] = {}
    if "allow" not in settings["permissions"]:
        settings["permissions"]["allow"] = []

    # Add new permissions (avoid duplicates)
    existing = set(settings["permissions"]["allow"])
    added = []
    for perm in new_permissions:
        if perm not in existing:
            settings["permissions"]["allow"].append(perm)
            added.append(perm)

    if added:
        logger.info(f"Adding permissions to {settings_path}: {added}")

        # Write back with nice formatting
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")  # Trailing newline
    else:
        logger.debug(f"All permissions already exist in {settings_path}")


def generate_permission_options(
    tool_name: str, tool_input: dict
) -> list[dict[str, str]]:
    """Generate permission grant options for a tool denial.

    Returns a list of options from most specific to most broad,
    allowing the user to choose their preferred level of access.

    Args:
        tool_name: The tool that was denied (e.g., "Bash", "Read")
        tool_input: The tool's input parameters

    Returns:
        List of dicts with 'label', 'value', and 'example' keys
    """
    options = []

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        parts = command.split()
        first_word = parts[0] if parts else command
        first_two_words = " ".join(parts[:2]) if len(parts) >= 2 else command

        options = [
            {
                "label": "Allow this exact command",
                "value": f"Bash({command})",
                "example": command,
            },
        ]

        # Only add "with any arguments" if there are arguments
        if len(parts) >= 2:
            options.append(
                {
                    "label": "Allow with any arguments",
                    "value": f"Bash({first_two_words}:*)",
                    "example": f"{first_two_words} ...",
                }
            )

        # Add broad option for the base command
        options.append(
            {
                "label": f"Allow all {first_word} commands",
                "value": f"Bash({first_word}:*)",
                "example": f"{first_word} ...",
            }
        )

    elif tool_name in ("Read", "Write", "Edit"):
        file_path = tool_input.get("file_path") or tool_input.get("path", "")

        # Note: Directory glob patterns (/**) are reportedly buggy in Claude Code
        # so we offer exact file and tool-wide permissions as the main options
        options = [
            {
                "label": "Allow this exact file",
                "value": f"{tool_name}({file_path})",
                "example": file_path,
            },
            {
                "label": f"Allow all {tool_name} operations",
                "value": tool_name,
                "example": "Any file",
            },
        ]

    else:
        # Generic fallback for other tools
        options = [
            {
                "label": f"Allow {tool_name}",
                "value": tool_name,
                "example": "All operations",
            }
        ]

    return options
