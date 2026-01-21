"""Session discovery for OpenCode.

Handles finding session files and extracting metadata from OpenCode's
hierarchical JSON storage format.

OpenCode stores sessions in:
~/.local/share/opencode/storage/
    session/{projectID}/{sessionID}.json    # Session metadata
    message/{sessionID}/{messageID}.json    # Messages
    part/{messageID}/{partID}.json          # Message parts
    project/{projectID}.json                # Project metadata
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default location for OpenCode storage (XDG Base Directories)
DEFAULT_STORAGE_DIR = Path.home() / ".local" / "share" / "opencode" / "storage"


def get_session_name(session_path: Path, storage_dir: Path) -> tuple[str, str | None]:
    """Extract project name and path from a session file.

    Session paths look like:
    ~/.local/share/opencode/storage/session/{projectID}/{sessionID}.json

    We read the session JSON to get the working directory, then use that
    to derive the project name.

    Args:
        session_path: Path to the session JSON file.
        storage_dir: Base storage directory.

    Returns:
        Tuple of (project_name, project_path) where project_path may be None.
    """
    try:
        session_data = json.loads(session_path.read_text())
        directory = session_data.get("directory", "")
        if directory and Path(directory).exists():
            return Path(directory).name, directory
        # Fallback to title if available
        title = session_data.get("title", "")
        if title:
            return title, directory or None
    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"Failed to read session file {session_path}: {e}")

    # Final fallback: use projectID from path
    project_id = session_path.parent.name
    return project_id, None


def get_session_id(session_path: Path) -> str:
    """Get the session ID (filename without extension)."""
    return session_path.stem


def get_last_message_timestamp(session_path: Path, storage_dir: Path) -> float | None:
    """Get the timestamp of the last message in a session.

    Args:
        session_path: Path to the session JSON file.
        storage_dir: Base storage directory.

    Returns:
        Unix timestamp (seconds since epoch) of the last message,
        or None if no messages found.
    """
    session_id = get_session_id(session_path)
    msg_dir = storage_dir / "message" / session_id
    if not msg_dir.exists():
        return None

    # Get the last message by ID (messages are sorted by ID)
    msg_files = sorted(msg_dir.glob("*.json"), reverse=True)
    if not msg_files:
        return None

    try:
        msg_data = json.loads(msg_files[0].read_text())
        time_data = msg_data.get("time", {})
        # Use 'updated' if available, fall back to 'created'
        timestamp_ms = time_data.get("updated") or time_data.get("created")
        if timestamp_ms:
            # OpenCode uses Unix milliseconds, convert to seconds
            return timestamp_ms / 1000
    except (json.JSONDecodeError, IOError, KeyError):
        pass

    return None


def has_messages(session_path: Path, storage_dir: Path) -> bool:
    """Check if a session has any messages.

    Args:
        session_path: Path to the session JSON file.
        storage_dir: Base storage directory.

    Returns:
        True if the session has at least one message file.
    """
    session_id = get_session_id(session_path)
    msg_dir = storage_dir / "message" / session_id
    if not msg_dir.exists():
        return False
    # Check if there's at least one JSON file
    try:
        return any(msg_dir.glob("*.json"))
    except OSError:
        return False


def get_first_user_message(
    session_path: Path, storage_dir: Path, max_length: int = 200
) -> str | None:
    """Read the first user message from a session.

    Args:
        session_path: Path to the session JSON file.
        storage_dir: Base storage directory.
        max_length: Maximum length of message to return.

    Returns:
        The first user message text, truncated to max_length, or None if not found.
    """
    session_id = get_session_id(session_path)
    msg_dir = storage_dir / "message" / session_id

    if not msg_dir.exists():
        return None

    # Get all message files and sort by ID (they're sorted alphabetically)
    msg_files = sorted(msg_dir.glob("*.json"))

    for msg_file in msg_files:
        try:
            msg_data = json.loads(msg_file.read_text())
            if msg_data.get("role") == "user":
                # Read parts for this message to get text content
                message_id = msg_data.get("id")
                if not message_id:
                    continue

                part_dir = storage_dir / "part" / message_id
                if not part_dir.exists():
                    continue

                # Find text parts
                for part_file in sorted(part_dir.glob("*.json")):
                    try:
                        part_data = json.loads(part_file.read_text())
                        if part_data.get("type") == "text":
                            text = part_data.get("text", "").strip()
                            if text:
                                return (
                                    text[:max_length]
                                    if len(text) > max_length
                                    else text
                                )
                    except (json.JSONDecodeError, IOError):
                        continue
        except (json.JSONDecodeError, IOError):
            continue

    return None


def find_recent_sessions(
    storage_dir: Path | None = None, limit: int = 10
) -> list[Path]:
    """Find the most recently active session files that have messages.

    Args:
        storage_dir: Base storage directory (defaults to ~/.local/share/opencode/storage)
        limit: Maximum number of sessions to return

    Returns:
        List of paths to recent session JSON files with messages, sorted by
        last message timestamp (newest first).
    """
    if storage_dir is None:
        storage_dir = DEFAULT_STORAGE_DIR

    session_base = storage_dir / "session"
    if not session_base.exists():
        logger.warning(f"Session directory not found: {session_base}")
        return []

    # Find all session JSON files, use mtime for initial rough ordering
    candidates = []
    for f in session_base.glob("*/*.json"):
        try:
            # Skip empty files
            if f.stat().st_size == 0:
                continue
            mtime = f.stat().st_mtime
            candidates.append((f, mtime))
        except OSError:
            continue

    if not candidates:
        logger.warning("No session files found")
        return []

    # Sort by mtime first (rough order, fast) to prioritize likely-recent files
    candidates.sort(key=lambda x: x[1], reverse=True)

    # Get actual message timestamps for valid sessions
    sessions_with_timestamps: list[tuple[Path, float]] = []
    for f, _ in candidates:
        if not has_messages(f, storage_dir):
            continue
        msg_timestamp = get_last_message_timestamp(f, storage_dir)
        if msg_timestamp is not None:
            sessions_with_timestamps.append((f, msg_timestamp))
        # Stop early if we have enough candidates
        if len(sessions_with_timestamps) >= limit * 3:
            break

    # Sort by actual message timestamp (accurate order)
    sessions_with_timestamps.sort(key=lambda x: x[1], reverse=True)

    return [f for f, _ in sessions_with_timestamps[:limit]]


def find_most_recent_session(storage_dir: Path | None = None) -> Path | None:
    """Find the most recently modified session file.

    Args:
        storage_dir: Base storage directory (defaults to ~/.local/share/opencode/storage)

    Returns:
        Path to most recent session file, or None if not found.
    """
    sessions = find_recent_sessions(storage_dir, limit=1)
    return sessions[0] if sessions else None


def should_watch_file(path: Path) -> bool:
    """Check if a file should be watched for changes.

    For OpenCode, we watch message and part JSON files for live updates.
    Session files are only metadata and don't need live watching.

    Args:
        path: File path to check.

    Returns:
        True if the file should be watched (message or part JSON files).
    """
    if path.suffix != ".json":
        return False

    # Watch message and part files
    parts = path.parts
    return any(p in parts for p in ("message", "part"))


def get_session_id_from_file_path(path: Path, storage_dir: Path) -> str | None:
    """Extract session ID from a message or part file path.

    Message files: storage/message/{sessionID}/{messageID}.json
    Part files: storage/part/{messageID}/{partID}.json

    For part files, the sessionID is stored inside the file itself,
    so we read it directly (O(1)) rather than scanning all sessions.

    Args:
        path: Path to the changed file.
        storage_dir: Base storage directory (unused for part files but kept for API).

    Returns:
        Session ID, or None if it cannot be determined.
    """
    parts = path.parts
    try:
        if "message" in parts:
            # message/{sessionID}/{messageID}.json
            msg_idx = parts.index("message")
            if len(parts) > msg_idx + 1:
                return parts[msg_idx + 1]
        elif "part" in parts:
            # part/{messageID}/{partID}.json
            # Part files contain sessionID directly - just read it
            if path.exists():
                data = json.loads(path.read_text())
                return data.get("sessionID")
    except (ValueError, IndexError, json.JSONDecodeError, OSError):
        pass
    return None
