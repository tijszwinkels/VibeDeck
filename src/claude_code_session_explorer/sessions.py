"""Session management for Claude Code Session Explorer.

Handles tracking of Claude Code sessions, including adding, removing,
and querying session state.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .tailer import SessionTailer, get_first_user_message, get_session_id, get_session_name, has_messages

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Configuration
MAX_SESSIONS = 100

# Global state
_sessions: dict[str, SessionInfo] = {}  # session_id -> SessionInfo
_sessions_lock: asyncio.Lock | None = None  # Protects _sessions during iteration
_projects_dir: Path | None = None
_known_session_files: set[Path] = set()  # Track known files to detect new ones


@dataclass
class SessionInfo:
    """Information about a tracked session."""

    path: Path
    tailer: SessionTailer
    name: str = ""
    session_id: str = ""
    project_name: str = ""
    project_path: str = ""
    first_message: str | None = None
    # Process management for sending messages
    process: asyncio.subprocess.Process | None = None
    message_queue: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.name or not self.project_name:
            name, path = get_session_name(self.path)
            if not self.name:
                self.name = name
            if not self.project_name:
                self.project_name = name
            if not self.project_path:
                self.project_path = path
        if not self.session_id:
            self.session_id = get_session_id(self.path)
        if self.first_message is None:
            self.first_message = get_first_user_message(self.path)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        # Get timestamps
        started_at = self.tailer.get_first_timestamp()
        try:
            last_updated = self.path.stat().st_mtime
        except OSError:
            last_updated = None

        return {
            "id": self.session_id,
            "name": self.name,
            "path": str(self.path),
            "projectName": self.project_name,
            "projectPath": self.project_path,
            "firstMessage": self.first_message,
            "startedAt": started_at,
            "lastUpdatedAt": last_updated,
        }


def get_sessions_lock() -> asyncio.Lock:
    """Get or create the sessions lock (must be created in event loop context)."""
    global _sessions_lock
    if _sessions_lock is None:
        _sessions_lock = asyncio.Lock()
    return _sessions_lock


def get_projects_dir() -> Path:
    """Get the projects directory path."""
    global _projects_dir
    if _projects_dir is None:
        _projects_dir = Path.home() / ".claude" / "projects"
    return _projects_dir


def set_projects_dir(path: Path) -> None:
    """Set the projects directory path (for testing)."""
    global _projects_dir
    _projects_dir = path


def get_sessions() -> dict[str, SessionInfo]:
    """Get the sessions dictionary (for internal use)."""
    return _sessions


def get_known_session_files() -> set[Path]:
    """Get the set of known session files."""
    return _known_session_files


def get_session(session_id: str) -> SessionInfo | None:
    """Get a session by ID."""
    return _sessions.get(session_id)


def get_oldest_session_id() -> str | None:
    """Find the oldest session by modification time."""
    if not _sessions:
        return None
    oldest = min(
        _sessions.items(),
        key=lambda x: x[1].path.stat().st_mtime if x[1].path.exists() else float("inf"),
    )
    return oldest[0]


def add_session(path: Path, evict_oldest: bool = True) -> tuple[SessionInfo | None, str | None]:
    """Add a session to track.

    Returns a tuple of (SessionInfo if added, evicted_session_id if one was removed).
    Returns (None, None) if already tracked or if file is empty.
    If at the session limit and evict_oldest=True, removes the oldest session to make room.
    """
    session_id = get_session_id(path)

    if session_id in _sessions:
        return None, None

    # Skip empty files (claude --resume creates empty files before connecting)
    try:
        if path.stat().st_size == 0:
            logger.debug(f"Skipping empty session file: {path}")
            return None, None
    except OSError:
        return None, None

    # Skip sessions without any user/assistant messages
    if not has_messages(path):
        logger.debug(f"Skipping session without messages: {path}")
        return None, None

    evicted_id = None
    # If at limit, remove the oldest session to make room
    if len(_sessions) >= MAX_SESSIONS:
        if evict_oldest:
            oldest_id = get_oldest_session_id()
            if oldest_id:
                logger.info(f"Session limit reached, removing oldest: {oldest_id}")
                remove_session(oldest_id)
                evicted_id = oldest_id
        else:
            logger.debug(f"Session limit reached, not adding {path}")
            return None, None

    tailer = SessionTailer(path)
    # Advance tailer position to end of file so process_session_messages
    # only picks up truly new messages (catchup uses read_all with fresh tailer)
    tailer.read_new_lines()
    info = SessionInfo(path=path, tailer=tailer)
    _sessions[session_id] = info
    _known_session_files.add(path)
    logger.info(f"Added session: {info.name} ({session_id})")
    return info, evicted_id


def remove_session(session_id: str) -> bool:
    """Remove a session from tracking."""
    if session_id in _sessions:
        info = _sessions.pop(session_id)
        _known_session_files.discard(info.path)
        logger.info(f"Removed session: {info.name} ({session_id})")
        return True
    return False


def get_sessions_list() -> list[dict]:
    """Get list of all tracked sessions, sorted by modification time (newest first)."""
    # Sort by file modification time, newest first
    sorted_sessions = sorted(
        _sessions.values(),
        key=lambda info: info.path.stat().st_mtime if info.path.exists() else 0,
        reverse=True,
    )
    return [info.to_dict() for info in sorted_sessions]


def session_count() -> int:
    """Get the number of tracked sessions."""
    return len(_sessions)
