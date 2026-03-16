"""Session discovery for OpenCode.

Handles finding sessions and extracting metadata from OpenCode's storage.

OpenCode v1.2+ uses SQLite:
    ~/.local/share/opencode/opencode.db
        session table - session metadata
        message table - messages
        part table - message parts

Legacy JSON storage (deprecated):
    ~/.local/share/opencode/storage/
        session/{projectID}/{sessionID}.json    # Session metadata
        message/{sessionID}/{messageID}.json    # Messages
        part/{messageID}/{partID}.json          # Message parts
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .db import OpenCodeDB, db_exists, get_session_metadata_from_db

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_DIR = Path.home() / ".local" / "share" / "opencode" / "storage"
DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


@dataclass
class SessionInfo:
    id: str
    project_name: str
    project_path: str | None
    timestamp: float
    source: str


def get_session_name(session_path: Path, storage_dir: Path) -> tuple[str, str | None]:
    """Extract project name and path from a legacy session JSON file."""
    try:
        session_data = json.loads(session_path.read_text())
        directory = session_data.get("directory", "")
        if directory and Path(directory).exists():
            return Path(directory).name, directory
        title = session_data.get("title", "")
        if title:
            return title, directory or None
    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"Failed to read session file {session_path}: {e}")

    project_id = session_path.parent.name
    return project_id, None


def get_session_id(session_path: Path) -> str:
    """Get the session ID (filename without extension)."""
    return session_path.stem


def get_last_message_timestamp_legacy(
    session_path: Path, storage_dir: Path
) -> float | None:
    """Get timestamp from legacy JSON files."""
    session_id = get_session_id(session_path)
    msg_dir = storage_dir / "message" / session_id
    if not msg_dir.exists():
        return None

    msg_files = sorted(msg_dir.glob("*.json"), reverse=True)
    if not msg_files:
        return None

    try:
        msg_data = json.loads(msg_files[0].read_text())
        time_data = msg_data.get("time", {})
        timestamp_ms = time_data.get("updated") or time_data.get("created")
        if timestamp_ms:
            return timestamp_ms / 1000
    except (json.JSONDecodeError, IOError, KeyError):
        pass

    return None


def get_last_message_timestamp(
    session_id: str, storage_dir: Path | None = None
) -> float | None:
    """Get the timestamp of the last message in a session.

    Uses SQLite database if available, falls back to JSON files.

    Args:
        session_id: Session ID to query.
        storage_dir: Legacy storage directory (for JSON files).

    Returns:
        Unix timestamp (seconds since epoch) of the last message,
        or None if no messages found.
    """
    if db_exists():
        try:
            with OpenCodeDB() as db:
                cursor = db._get_conn().cursor()
                cursor.execute(
                    "SELECT MAX(time_updated) FROM message WHERE session_id = ?",
                    (session_id,),
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return row[0] / 1000
        except Exception as e:
            logger.debug(f"Failed to get timestamp from database: {e}")

    if storage_dir:
        msg_dir = storage_dir / "message" / session_id
        if msg_dir.exists():
            msg_files = sorted(msg_dir.glob("*.json"), reverse=True)
            if msg_files:
                try:
                    msg_data = json.loads(msg_files[0].read_text())
                    time_data = msg_data.get("time", {})
                    timestamp_ms = time_data.get("updated") or time_data.get("created")
                    if timestamp_ms:
                        return timestamp_ms / 1000
                except (json.JSONDecodeError, IOError, KeyError):
                    pass

    return None


def has_messages(session_path: Path, storage_dir: Path) -> bool:
    """Check if a session has any messages (legacy JSON format)."""
    session_id = get_session_id(session_path)
    msg_dir = storage_dir / "message" / session_id
    if not msg_dir.exists():
        return False
    try:
        return any(msg_dir.glob("*.json"))
    except OSError:
        return False


def has_messages_db(session_id: str) -> bool:
    """Check if a session has any messages using SQLite database."""
    try:
        with OpenCodeDB() as db:
            return db.has_messages(session_id)
    except Exception as e:
        logger.debug(f"Failed to check messages in database: {e}")
        return False


def get_first_user_message_legacy(
    session_path: Path, storage_dir: Path, max_length: int = 200
) -> str | None:
    """Read first user message from legacy JSON files."""
    session_id = get_session_id(session_path)
    msg_dir = storage_dir / "message" / session_id

    if not msg_dir.exists():
        return None

    msg_files = sorted(msg_dir.glob("*.json"))

    for msg_file in msg_files:
        try:
            msg_data = json.loads(msg_file.read_text())
            if msg_data.get("role") == "user":
                message_id = msg_data.get("id")
                if not message_id:
                    continue

                part_dir = storage_dir / "part" / message_id
                if not part_dir.exists():
                    continue

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


def get_first_user_message(
    session_path: Path | None,
    storage_dir: Path,
    max_length: int = 200,
    session_id: str | None = None,
) -> str | None:
    """Read the first user message from a session.

    Uses SQLite database if available, falls back to JSON files.

    Args:
        session_path: Path to session JSON file (for legacy format).
        storage_dir: Legacy storage directory.
        max_length: Maximum length of message to return.
        session_id: Session ID (required for database lookup).

    Returns:
        The first user message text, truncated to max_length, or None if not found.
    """
    custom_storage = storage_dir != DEFAULT_STORAGE_DIR
    use_db = db_exists() and not custom_storage and session_id is not None

    if use_db and session_id is not None:
        try:
            with OpenCodeDB() as db:
                return db.get_first_user_message(session_id, max_length)
        except Exception as e:
            logger.debug(f"Failed to get first message from database: {e}")

    if session_path:
        return get_first_user_message_legacy(session_path, storage_dir, max_length)

    return None


def find_recent_sessions(
    storage_dir: Path | None = None, limit: int = 10
) -> list[Path]:
    """Find the most recently active session files.

    Returns:
        For SQLite: Returns list of synthetic Path objects containing session IDs.
        For JSON: Returns list of paths to session JSON files.

    Note: This function maintains backward compatibility by returning Paths.
    For SQLite sessions, the path is synthetic: "session:<session_id>"

    When a custom storage_dir is passed (not the default), uses legacy JSON mode
    for testing purposes.
    """
    if storage_dir is None:
        storage_dir = DEFAULT_STORAGE_DIR

    custom_storage = storage_dir != DEFAULT_STORAGE_DIR
    use_db = db_exists() and not custom_storage

    if use_db:
        return find_recent_sessions_db(limit=limit)

    return find_recent_sessions_legacy(storage_dir, limit=limit)


def find_recent_sessions_db(limit: int = 10) -> list[Path]:
    """Find recent sessions from SQLite database."""
    try:
        with OpenCodeDB() as db:
            sessions = db.get_recent_sessions(limit=limit)
            return [Path(f"session:{s.id}") for s in sessions]
    except Exception as e:
        logger.warning(f"Failed to query database: {e}")
        return []


def find_recent_sessions_legacy(storage_dir: Path, limit: int = 10) -> list[Path]:
    """Find recent sessions from legacy JSON files."""
    session_base = storage_dir / "session"
    if not session_base.exists():
        logger.warning(f"Session directory not found: {session_base}")
        return []

    candidates = []
    for f in session_base.glob("*/*.json"):
        try:
            if f.stat().st_size == 0:
                continue
            mtime = f.stat().st_mtime
            candidates.append((f, mtime))
        except OSError:
            continue

    if not candidates:
        logger.warning("No session files found")
        return []

    candidates.sort(key=lambda x: x[1], reverse=True)

    sessions_with_timestamps: list[tuple[Path, float]] = []
    for f, _ in candidates:
        if not has_messages(f, storage_dir):
            continue
        msg_timestamp = get_last_message_timestamp_legacy(f, storage_dir)
        if msg_timestamp is not None:
            sessions_with_timestamps.append((f, msg_timestamp))
        if len(sessions_with_timestamps) >= limit * 3:
            break

    sessions_with_timestamps.sort(key=lambda x: x[1], reverse=True)

    return [f for f, _ in sessions_with_timestamps[:limit]]


def find_most_recent_session(storage_dir: Path | None = None) -> Path | None:
    """Find the most recently modified session file."""
    sessions = find_recent_sessions(storage_dir, limit=1)
    return sessions[0] if sessions else None


def is_db_session(session_path: Path) -> bool:
    """Check if a session path is from the SQLite database."""
    return str(session_path).startswith("session:")


def get_session_id_from_path(session_path: Path) -> str:
    """Extract session ID from a session path.

    Works for both SQLite sessions (session:<id>) and JSON file paths.
    """
    path_str = str(session_path)
    if path_str.startswith("session:"):
        return path_str[8:]
    return session_path.stem


def get_session_metadata(
    session_path: Path, storage_dir: Path | None = None
) -> tuple[str, str | None]:
    """Get session name and path from either SQLite or JSON.

    Args:
        session_path: Session path (either "session:<id>" or file path).
        storage_dir: Legacy storage directory.

    Returns:
        Tuple of (project_name, project_path).
    """
    session_id = get_session_id_from_path(session_path)

    if is_db_session(session_path):
        result = get_session_metadata_from_db(session_id)
        if result:
            return result
        return session_id, None

    if storage_dir is None:
        storage_dir = DEFAULT_STORAGE_DIR
    return get_session_name(session_path, storage_dir)


def should_watch_file(path: Path) -> bool:
    """Check if a file should be watched for changes.

    For SQLite-based sessions, we watch the database file.
    For legacy JSON format, we watch message and part JSON files.
    """
    str_path = str(path)
    if str_path.endswith("opencode.db") or str_path.endswith("opencode.db-wal"):
        return True

    if path.suffix != ".json":
        return False

    parts = path.parts
    return any(p in parts for p in ("message", "part"))


def get_db_path() -> Path:
    """Get the path to the SQLite database."""
    return DEFAULT_DB_PATH


def get_updated_sessions_since(last_check_times: dict[str, int]) -> list[str]:
    """Get session IDs that have been updated since the last check.

    Args:
        last_check_times: Dict mapping session_id to last checked timestamp (ms).

    Returns:
        List of session IDs that have newer messages.
    """
    if not db_exists():
        return []

    try:
        with OpenCodeDB() as db:
            cursor = db._get_conn().cursor()
            session_ids = list(last_check_times.keys())
            if not session_ids:
                return []

            placeholders = ",".join("?" * len(session_ids))
            cursor.execute(
                f"""
                SELECT DISTINCT session_id FROM message
                WHERE session_id IN ({placeholders})
                GROUP BY session_id
                HAVING MAX(time_updated) > ?
                """,
                session_ids + [min(last_check_times.values())],
            )
            return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logger.debug(f"Failed to check for updated sessions: {e}")
        return []


def get_db_last_modified() -> float | None:
    """Get the last modified timestamp of the SQLite database.

    Returns:
        Unix timestamp (seconds) of the database file's mtime, or None if not found.
    """
    db_path = DEFAULT_DB_PATH
    if not db_path.exists():
        return None
    try:
        return db_path.stat().st_mtime
    except OSError:
        return None


def get_session_id_from_file_path(path: Path, storage_dir: Path) -> str | None:
    """Extract session ID from a message or part file path (legacy format)."""
    parts = path.parts
    try:
        if "message" in parts:
            msg_idx = parts.index("message")
            if len(parts) > msg_idx + 1:
                return parts[msg_idx + 1]
        elif "part" in parts:
            if path.exists():
                data = json.loads(path.read_text())
                return data.get("sessionID")
    except (ValueError, IndexError, json.JSONDecodeError, OSError):
        pass
    return None
