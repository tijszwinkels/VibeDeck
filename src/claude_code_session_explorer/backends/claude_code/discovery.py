"""Session discovery for Claude Code.

Handles finding session files and extracting metadata from Claude Code's
session storage format, including subagent sessions.
"""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

from .tailer import has_messages, is_warmup_session

logger = logging.getLogger(__name__)

# Default location for Claude Code projects
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def is_subagent_session(path: Path) -> bool:
    """Check if a session file is a subagent session.

    Subagent sessions are identified by the 'agent-' prefix in the filename.

    Args:
        path: Path to the session file.

    Returns:
        True if this is a subagent session file.
    """
    return path.name.startswith("agent-")


def get_parent_session_id(path: Path) -> str | None:
    """Get the parent session ID for a subagent session.

    Subagent sessions are stored in:
    ~/.claude/projects/<project>/<parent-session-uuid>/subagents/agent-<id>.jsonl

    Args:
        path: Path to the subagent session file.

    Returns:
        Parent session UUID, or None if not a subagent or path doesn't match expected structure.
    """
    if not is_subagent_session(path):
        return None

    # Path should be: .../subagents/agent-xxx.jsonl
    # Parent should be: .../<parent-uuid>/subagents/
    if path.parent.name == "subagents":
        return path.parent.parent.name

    return None


def get_session_name(session_path: Path) -> tuple[str, str]:
    """Extract project name and path from a session path.

    Session paths look like:
    ~/.claude/projects/-Users-tijs-projects-claude-code-live/abc123.jsonl

    For subagent sessions in nested structure:
    ~/.claude/projects/-Users-tijs-projects-claude-code-live/SESSION-UUID/subagents/agent-xxx.jsonl

    The folder name encodes the original path with slashes replaced by dashes.
    Additionally, underscores in directory names are also replaced with dashes.
    We check if decoded paths actually exist on the filesystem to find the
    correct project directory.

    Returns:
        Tuple of (project_name, project_path) where project_name is the
        directory name and project_path is the full path.
    """
    # For subagent files in nested structure, navigate up to find the project folder
    # Path: .../projects/-project-path/SESSION-UUID/subagents/agent-xxx.jsonl
    # We need to get to: -project-path
    parent = session_path.parent
    if parent.name == "subagents":
        # Go up two levels: subagents -> SESSION-UUID -> project-folder
        parent = parent.parent.parent

    # Get the parent folder name (the project identifier)
    folder = parent.name

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
    projects_dir: Path | None = None,
    limit: int = 10,
    include_subagents: bool = True,
) -> list[Path]:
    """Find the most recently modified session files that have messages.

    Args:
        projects_dir: Base directory to search (defaults to ~/.claude/projects)
        limit: Maximum number of sessions to return
        include_subagents: Whether to include subagent sessions (default True)

    Returns:
        List of paths to recent .jsonl files with messages, sorted by modification time (newest first)
    """
    if projects_dir is None:
        projects_dir = DEFAULT_PROJECTS_DIR

    if not projects_dir.exists():
        logger.warning(f"Projects directory not found: {projects_dir}")
        return []

    # Find all .jsonl files
    sessions = []
    for f in projects_dir.glob("**/*.jsonl"):
        # Filter out subagents if requested
        if not include_subagents and is_subagent_session(f):
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

    # Filter to sessions with messages (excluding warmup sessions), up to the limit
    result = []
    for f, _ in sessions:
        if has_messages(f) and not is_warmup_session(f):
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


def should_watch_file(path: Path, include_subagents: bool = True) -> bool:
    """Check if a file should be watched for changes.

    Args:
        path: File path to check.
        include_subagents: Whether to watch subagent session files (default True).

    Returns:
        True if the file is a Claude Code session file that should be watched.
    """
    # Only watch .jsonl files
    if path.suffix != ".jsonl":
        return False
    # Filter out subagents if requested
    if not include_subagents and is_subagent_session(path):
        return False
    return True
