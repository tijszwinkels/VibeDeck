"""Session discovery for Pi Coding Agent.

Handles finding pi session files and extracting metadata from
~/.pi/agent/sessions/ directory structure.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SESSIONS_DIR = Path.home() / ".pi" / "agent" / "sessions"


def decode_project_path(dirname: str) -> str:
    """Decode a pi session directory name to a filesystem path.

    Pi encodes cwd by replacing `/` with `-` and wrapping in `--`:
    `/home/claude/tmp` -> `--home-claude-tmp--`

    Args:
        dirname: Encoded directory name (e.g., "--home-claude-tmp--")

    Returns:
        Decoded path string (e.g., "/home/claude/tmp")
    """
    # Strip surrounding --
    stripped = dirname.strip("-")
    # Replace - with /
    return "/" + stripped.replace("-", "/")


def get_project_name(session_path: Path) -> tuple[str, str]:
    """Extract project name and path from a session file path.

    Args:
        session_path: Path to the session JSONL file.

    Returns:
        Tuple of (project_name, project_path).
    """
    # Session path: .../sessions/--encoded-cwd--/timestamp_uuid.jsonl
    dirname = session_path.parent.name
    project_path = decode_project_path(dirname)
    project_name = project_path.rstrip("/").rsplit("/", 1)[-1]
    return project_name, project_path


def get_session_id(session_path: Path) -> str:
    """Extract UUID from session filename.

    Filename format: <timestamp>_<uuid>.jsonl
    """
    stem = session_path.stem
    # Split on underscore, UUID is the last part
    parts = stem.rsplit("_", 1)
    if len(parts) == 2:
        return parts[1]
    return stem


def get_session_header(session_path: Path) -> dict | None:
    """Read the session header (first line) from a JSONL file.

    Returns:
        Parsed header dict, or None if file cannot be read.
    """
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            line = f.readline().strip()
            if line:
                obj = json.loads(line)
                if obj.get("type") == "session":
                    return obj
    except (FileNotFoundError, IOError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to read session header from {session_path}: {e}")
    return None


def get_session_id_from_header(session_path: Path) -> str | None:
    """Extract session ID from the session header."""
    header = get_session_header(session_path)
    if header:
        return header.get("id")
    return None


def has_messages(session_path: Path) -> bool:
    """Check if a session file has any user or assistant messages."""
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "message":
                        role = entry.get("message", {}).get("role")
                        if role in ("user", "assistant"):
                            return True
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, IOError):
        pass
    return False


def get_first_user_message(session_path: Path, max_length: int = 200) -> str | None:
    """Read the first user message text from a session file."""
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
                    if msg.get("role") != "user":
                        continue

                    content = msg.get("content", "")
                    if isinstance(content, str):
                        text = content.strip()
                        if text:
                            return text[:max_length]
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "").strip()
                                if text:
                                    return text[:max_length]
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, IOError):
        pass
    return None


def find_recent_sessions(
    sessions_dir: Path | None = None,
    limit: int = 10,
) -> list[Path]:
    """Find the most recently active pi session files with messages.

    Args:
        sessions_dir: Base sessions directory (defaults to ~/.pi/agent/sessions)
        limit: Maximum number of sessions to return.

    Returns:
        List of paths to session files, sorted by mtime (newest first).
    """
    if sessions_dir is None:
        sessions_dir = DEFAULT_SESSIONS_DIR

    if not sessions_dir.exists():
        logger.debug(f"Pi sessions directory not found: {sessions_dir}")
        return []

    candidates = []
    for f in sessions_dir.glob("--*--/*.jsonl"):
        try:
            if f.stat().st_size == 0:
                continue
            mtime = f.stat().st_mtime
            candidates.append((f, mtime))
        except OSError:
            continue

    if not candidates:
        return []

    # Sort by mtime (newest first)
    candidates.sort(key=lambda x: x[1], reverse=True)

    # Filter to sessions with messages
    results = []
    for f, _ in candidates:
        if has_messages(f):
            results.append(f)
        if len(results) >= limit:
            break

    return results


def should_watch_file(path: Path) -> bool:
    """Check if a file should be watched for changes."""
    return path.suffix == ".jsonl"


def get_session_id_from_changed_file(path: Path) -> str | None:
    """Get session ID from a changed file path."""
    if path.suffix == ".jsonl":
        return get_session_id(path)
    return None
