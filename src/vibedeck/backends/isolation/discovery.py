"""Per-user session discovery for the isolation backend.

Scans {users_dir}/{user_id}/.claude/projects/**/*.jsonl to find sessions
across all users or for a specific user. Reuses claude_code discovery helpers
for file filtering and timestamp extraction.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..claude_code.discovery import (
    get_last_message_timestamp,
    is_subagent_session,
    should_watch_file,
)
from ..claude_code.tailer import has_messages, is_warmup_session

logger = logging.getLogger(__name__)


def get_user_projects_dir(users_dir: Path, user_id: str) -> Path:
    """Get the .claude/projects directory for a specific user.

    Args:
        users_dir: Base directory containing user directories.
        user_id: User identifier (directory name).

    Returns:
        Path to {users_dir}/{user_id}/.claude/projects/
    """
    return users_dir / user_id / ".claude" / "projects"


def get_session_owner(session_path: Path, users_dir: Path) -> str | None:
    """Extract the user_id that owns a session from its path.

    Args:
        session_path: Path to the session JSONL file.
        users_dir: Base directory containing user directories.

    Returns:
        User ID string, or None if path is not under users_dir.
    """
    try:
        rel = session_path.relative_to(users_dir)
        return rel.parts[0] if rel.parts else None
    except (ValueError, IndexError):
        return None


def _find_session_candidates(
    projects_dir: Path,
    include_subagents: bool = True,
) -> list[tuple[Path, float]]:
    """Find session files in a projects directory and get their timestamps.

    Args:
        projects_dir: Directory to glob for *.jsonl files.
        include_subagents: Whether to include subagent sessions.

    Returns:
        List of (path, mtime) tuples for valid session files.
    """
    if not projects_dir.exists():
        return []

    candidates = []
    for f in projects_dir.glob("**/*.jsonl"):
        if not include_subagents and is_subagent_session(f):
            continue
        try:
            if f.stat().st_size == 0:
                continue
            mtime = f.stat().st_mtime
            candidates.append((f, mtime))
        except OSError:
            continue

    return candidates


def find_sessions_for_user(
    users_dir: Path,
    user_id: str,
    limit: int = 10,
    include_subagents: bool = True,
) -> list[Path]:
    """Find recent sessions for a specific user.

    Args:
        users_dir: Base directory containing user directories.
        user_id: User identifier.
        limit: Maximum sessions to return.
        include_subagents: Whether to include subagent sessions.

    Returns:
        List of session paths sorted by last message timestamp (newest first).
    """
    projects_dir = get_user_projects_dir(users_dir, user_id)
    return _find_and_sort_sessions(projects_dir, limit, include_subagents)


def find_sessions_for_all_users(
    users_dir: Path,
    limit: int = 10,
    include_subagents: bool = True,
) -> list[Path]:
    """Find recent sessions across all users.

    Args:
        users_dir: Base directory containing user directories.
        limit: Maximum sessions to return.
        include_subagents: Whether to include subagent sessions.

    Returns:
        List of session paths sorted by last message timestamp (newest first).
    """
    if not users_dir.exists():
        logger.warning(f"Users directory not found: {users_dir}")
        return []

    all_candidates: list[tuple[Path, float]] = []
    try:
        for user_dir in users_dir.iterdir():
            if not user_dir.is_dir() or user_dir.name.startswith("."):
                continue
            projects_dir = user_dir / ".claude" / "projects"
            all_candidates.extend(
                _find_session_candidates(projects_dir, include_subagents)
            )
    except OSError as e:
        logger.error(f"Error scanning users directory {users_dir}: {e}")
        return []

    return _sort_and_filter(all_candidates, limit)


def _find_and_sort_sessions(
    projects_dir: Path,
    limit: int,
    include_subagents: bool,
) -> list[Path]:
    """Find, filter, and sort sessions by timestamp."""
    candidates = _find_session_candidates(projects_dir, include_subagents)
    return _sort_and_filter(candidates, limit)


def _sort_and_filter(
    candidates: list[tuple[Path, float]],
    limit: int,
) -> list[Path]:
    """Sort candidates by actual message timestamp and filter invalid sessions.

    Args:
        candidates: List of (path, mtime) tuples.
        limit: Maximum results.

    Returns:
        Sorted list of paths.
    """
    # Sort by mtime first for rough ordering
    candidates.sort(key=lambda x: x[1], reverse=True)

    sessions_with_timestamps: list[tuple[Path, float]] = []
    for f, _ in candidates:
        if not has_messages(f) or is_warmup_session(f):
            continue
        msg_timestamp = get_last_message_timestamp(f)
        if msg_timestamp is not None:
            sessions_with_timestamps.append((f, msg_timestamp))
        if len(sessions_with_timestamps) >= limit * 3:
            break

    sessions_with_timestamps.sort(key=lambda x: x[1], reverse=True)
    return [f for f, _ in sessions_with_timestamps[:limit]]
