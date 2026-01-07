"""Session discovery for Claude Code.

Handles finding session files and extracting metadata from Claude Code's
session storage format.
"""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

from .tailer import has_messages

logger = logging.getLogger(__name__)

# Default location for Claude Code projects
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def get_session_name(session_path: Path) -> tuple[str, str]:
    """Extract project name and path from a session path.

    Session paths look like:
    ~/.claude/projects/-Users-tijs-projects-claude-code-live/abc123.jsonl

    The folder name encodes the original path with slashes replaced by dashes.
    Additionally, underscores in directory names are also replaced with dashes.
    We check if decoded paths actually exist on the filesystem to find the
    correct project directory.

    Returns:
        Tuple of (project_name, project_path) where project_name is the
        directory name and project_path is the full path.
    """
    # Get the parent folder name (the project identifier)
    folder = session_path.parent.name

    # URL decode any percent-encoded chars first
    folder = urllib.parse.unquote(folder)

    # Remove leading dash
    folder = folder.lstrip("-")

    # Try to find the actual directory by testing different dash positions
    # Some dashes are path separators, some are part of directory names,
    # and some were originally underscores.
    # Strategy: try replacing each dash with / and see if the resulting path exists
    # Also try replacing remaining dashes with underscores

    # Find all dash positions
    dash_positions = [i for i, c in enumerate(folder) if c == "-"]

    # Try combinations of dashes that could be path separators
    # Start with trying each individual dash position from the end
    # (most likely the project name is at the end)
    for num_path_seps in range(len(dash_positions), 0, -1):
        # Try the last N dashes as path separators
        for i in range(len(dash_positions) - num_path_seps + 1):
            positions_to_replace = dash_positions[i : i + num_path_seps]
            candidate = list(folder)
            for pos in positions_to_replace:
                candidate[pos] = "/"
            candidate_path = "/" + "".join(candidate)
            if Path(candidate_path).is_dir():
                return Path(candidate_path).name, candidate_path

            # Also try with remaining dashes as underscores
            candidate_with_underscores = ["_" if c == "-" else c for c in candidate]
            candidate_path_underscore = "/" + "".join(candidate_with_underscores)
            if Path(candidate_path_underscore).is_dir():
                return Path(candidate_path_underscore).name, candidate_path_underscore

    # Fallback: return the folder name as-is
    return folder or session_path.parent.name, folder


def get_session_id(session_path: Path) -> str:
    """Get the session ID (filename without extension)."""
    return session_path.stem


def find_recent_sessions(
    projects_dir: Path | None = None, limit: int = 10
) -> list[Path]:
    """Find the most recently modified session files that have messages.

    Args:
        projects_dir: Base directory to search (defaults to ~/.claude/projects)
        limit: Maximum number of sessions to return

    Returns:
        List of paths to recent .jsonl files with messages, sorted by modification time (newest first)
    """
    if projects_dir is None:
        projects_dir = DEFAULT_PROJECTS_DIR

    if not projects_dir.exists():
        logger.warning(f"Projects directory not found: {projects_dir}")
        return []

    # Find all .jsonl files, excluding agent files
    sessions = []
    for f in projects_dir.glob("**/*.jsonl"):
        if f.name.startswith("agent-"):
            continue
        try:
            # Skip empty files
            if f.stat().st_size == 0:
                continue
            mtime = f.stat().st_mtime
            sessions.append((f, mtime))
        except OSError:
            continue

    if not sessions:
        logger.warning("No session files found")
        return []

    # Sort by modification time (newest first)
    sessions.sort(key=lambda x: x[1], reverse=True)

    # Filter to sessions with messages, up to the limit
    result = []
    for f, _ in sessions:
        if has_messages(f):
            result.append(f)
            if len(result) >= limit:
                break

    return result


def find_most_recent_session(projects_dir: Path | None = None) -> Path | None:
    """Find the most recently modified session file.

    Args:
        projects_dir: Base directory to search (defaults to ~/.claude/projects)

    Returns:
        Path to most recent .jsonl file, or None if not found
    """
    sessions = find_recent_sessions(projects_dir, limit=1)
    return sessions[0] if sessions else None


def should_watch_file(path: Path) -> bool:
    """Check if a file should be watched for changes.

    Args:
        path: File path to check.

    Returns:
        True if the file is a Claude Code session file that should be watched.
    """
    # Only watch .jsonl files
    if path.suffix != ".jsonl":
        return False
    # Skip agent files
    if path.name.startswith("agent-"):
        return False
    return True
