"""Session management for VibeDeck.

Handles tracking of coding sessions, including adding, removing,
and querying session state. Works with any backend that implements
the CodingToolBackend protocol.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .backends import CodingToolBackend, get_backend, SessionTailerProtocol

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Configuration
MAX_SESSIONS = 100

# Global state
_sessions: dict[str, SessionInfo] = {}  # session_id -> SessionInfo
_sessions_lock: asyncio.Lock | None = None  # Protects _sessions during iteration
_backend: CodingToolBackend | None = None
_known_session_files: set[Path] = set()  # Track known files to detect new ones


@dataclass
class SessionInfo:
    """Information about a tracked session."""

    path: Path
    tailer: SessionTailerProtocol
    name: str = ""
    session_id: str = ""
    project_name: str = ""
    project_path: str = ""
    first_message: str | None = None
    backend_name: str = ""  # Name of the backend this session belongs to
    # Process management for sending messages
    process: asyncio.subprocess.Process | None = None
    message_queue: list[str] = field(default_factory=list)
    # Summary file data (populated when *_summary.json exists)
    summary_title: str | None = None
    summary_short: str | None = None
    summary_executive: str | None = None
    summary_branch: str | None = None

    def __post_init__(self):
        backend = get_current_backend()
        if backend is None:
            raise RuntimeError("Backend not initialized. Call set_backend() first.")

        if not self.session_id:
            self.session_id = backend.get_session_id(self.path)

        # Try to get backend name from MultiBackend if available
        if not self.backend_name:
            # Use getattr to check for MultiBackend's method (duck typing)
            get_backend_name = getattr(backend, "get_backend_name_for_session", None)
            if get_backend_name is not None:
                self.backend_name = get_backend_name(self.path) or ""
            if not self.backend_name:
                self.backend_name = backend.name

        if not self.name or not self.project_name:
            try:
                metadata = backend.get_session_metadata(self.path)
                if not self.name:
                    self.name = metadata.project_name
                if not self.project_name:
                    self.project_name = metadata.project_name
                if not self.project_path:
                    self.project_path = metadata.project_path or ""
                if self.first_message is None:
                    self.first_message = metadata.first_message
            except (OSError, IOError) as e:
                # File may have been deleted or become unreadable
                logger.warning(f"Failed to read session metadata for {self.path}: {e}")
                if not self.name:
                    self.name = self.session_id
                if not self.project_name:
                    self.project_name = self.session_id

        # Try to load summary data if it exists
        self.load_summary()

    def get_summary_path(self) -> Path:
        """Get the path to the summary file for this session.

        Summary files are stored alongside session files with format:
        <session_id>_summary.json
        """
        return self.path.parent / f"{self.session_id}_summary.json"

    def load_summary(self) -> bool:
        """Load summary data from the summary file if it exists.

        Returns:
            True if summary was loaded successfully, False otherwise.
        """
        summary_path = self.get_summary_path()
        if not summary_path.exists():
            return False

        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.summary_title = data.get("title")
            self.summary_short = data.get("short_summary")
            self.summary_executive = data.get("executive_summary")
            self.summary_branch = data.get("branch")
            logger.debug(f"Loaded summary for session {self.session_id}: {self.summary_title}")
            return True
        except (OSError, IOError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load summary for {self.session_id}: {e}")
            return False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization.

        Returns minimal data if backend is unavailable (e.g., during shutdown).
        """
        # Get timestamps
        try:
            started_at = self.tailer.get_first_timestamp()
        except Exception:
            started_at = None

        # Use the last message timestamp instead of file mtime
        # This avoids showing wrong "updated at" times when Claude Code
        # appends summary entries to old session files
        try:
            last_updated = self.tailer.get_last_message_timestamp()
            if last_updated is None:
                # Fall back to file mtime if no message timestamp available
                last_updated = self.path.stat().st_mtime
        except (OSError, AttributeError):
            last_updated = None

        # Get token usage stats if backend is available
        backend = get_current_backend()
        if backend is not None:
            try:
                usage = backend.get_session_token_usage(self.path)
                token_usage = usage.to_dict()
            except (OSError, IOError) as e:
                logger.warning(f"Failed to get token usage for {self.path}: {e}")
                token_usage = {}
        else:
            token_usage = {}

        return {
            "id": self.session_id,
            "name": self.name,
            "path": str(self.path),
            "projectName": self.project_name,
            "projectPath": self.project_path,
            "firstMessage": self.first_message,
            "startedAt": started_at,
            "lastUpdatedAt": last_updated,
            "tokenUsage": token_usage,
            "backend": self.backend_name,
            # Summary data (may be None if no summary file exists yet)
            "summaryTitle": self.summary_title,
            "summaryShort": self.summary_short,
            "summaryExecutive": self.summary_executive,
            "summaryBranch": self.summary_branch,
        }


def get_sessions_lock() -> asyncio.Lock:
    """Get or create the sessions lock (must be created in event loop context)."""
    global _sessions_lock
    if _sessions_lock is None:
        _sessions_lock = asyncio.Lock()
    return _sessions_lock


def get_current_backend() -> CodingToolBackend | None:
    """Get the current backend instance."""
    return _backend


def set_backend(backend: CodingToolBackend) -> None:
    """Set the backend to use for session operations.

    Args:
        backend: Backend instance implementing CodingToolBackend protocol.
    """
    global _backend
    _backend = backend


def get_projects_dir() -> Path:
    """Get the projects directory path from the current backend."""
    if _backend is None:
        # Fallback to default Claude Code location
        return Path.home() / ".claude" / "projects"
    return _backend.get_projects_dir()


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
    """Find the oldest session by last message timestamp."""
    if not _sessions:
        return None
    oldest = min(
        _sessions.items(),
        key=lambda x: _get_session_timestamp(x[1]) if _get_session_timestamp(x[1]) > 0 else float("inf"),
    )
    return oldest[0]


def add_session(
    path: Path, evict_oldest: bool = True
) -> tuple[SessionInfo | None, str | None]:
    """Add a session to track.

    Returns a tuple of (SessionInfo if added, evicted_session_id if one was removed).
    Returns (None, None) if already tracked, if file is empty, or if path is not a file.
    If at the session limit and evict_oldest=True, removes the oldest session to make room.
    """
    backend = get_current_backend()
    if backend is None:
        raise RuntimeError("Backend not initialized. Call set_backend() first.")

    # Validate path is actually a file (not a directory)
    if not path.is_file():
        logger.debug(f"Skipping non-file path: {path}")
        return None, None

    session_id = backend.get_session_id(path)

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
    if not backend.has_messages(path):
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

    tailer = backend.create_tailer(path)
    # Fast initialization: seek to end without parsing content.
    # Messages are loaded on-demand via REST API when user views session.
    # File watching still works - read_new_lines() detects new content.
    tailer.seek_to_end()
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


def _get_session_timestamp(info: SessionInfo) -> float:
    """Get the effective timestamp for a session (message timestamp or file mtime)."""
    try:
        ts = info.tailer.get_last_message_timestamp()
        if ts is not None:
            return ts
    except AttributeError:
        pass
    try:
        return info.path.stat().st_mtime if info.path.exists() else 0
    except OSError:
        return 0


def get_sessions_list() -> list[dict]:
    """Get list of all tracked sessions, sorted by last message time (newest first)."""
    # Sort by last message timestamp, newest first
    sorted_sessions = sorted(
        _sessions.values(),
        key=_get_session_timestamp,
        reverse=True,
    )
    return [info.to_dict() for info in sorted_sessions]


def session_count() -> int:
    """Get the number of tracked sessions."""
    return len(_sessions)
