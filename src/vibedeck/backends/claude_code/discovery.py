"""Session discovery for Claude Code.

Handles finding session files and extracting metadata from Claude Code's
session storage format, including subagent sessions.
"""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

from .tailer import ClaudeCodeTailer, has_messages, is_warmup_session

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


def _decode_path_greedy(encoded: str) -> str | None:
    """Decode an encoded path by greedily building segments.

    Uses filesystem lookups to determine which dashes are path separators
    vs. literal characters in directory names.

    Args:
        encoded: The encoded folder name (e.g., "home-claude-projects-my-project")

    Returns:
        The decoded path if found, None otherwise.
    """
    # Start with root
    current_path = Path("/")
    remaining = encoded

    while remaining:
        # Find all dash positions in remaining string
        dash_positions = [i for i, c in enumerate(remaining) if c == "-"]

        if not dash_positions:
            # No more dashes - remaining is the final segment
            candidate = current_path / remaining
            if candidate.is_dir():
                return str(candidate)
            # Also try underscore variant
            candidate = current_path / remaining.replace("-", "_")
            if candidate.is_dir():
                return str(candidate)
            return None

        # Try progressively longer segments (greedy: prefer shorter valid paths first)
        found_segment = False
        for dash_pos in dash_positions:
            segment = remaining[:dash_pos]
            if not segment:
                continue

            candidate = current_path / segment
            if candidate.is_dir():
                current_path = candidate
                remaining = remaining[dash_pos + 1 :]  # Skip the dash
                found_segment = True
                break

            # Also try underscore variant for the segment
            segment_with_underscore = segment.replace("-", "_")
            candidate = current_path / segment_with_underscore
            if candidate.is_dir():
                current_path = candidate
                remaining = remaining[dash_pos + 1 :]
                found_segment = True
                break

        if not found_segment:
            # No valid segment found at any dash position
            # Try the entire remaining string as the final segment
            candidate = current_path / remaining
            if candidate.is_dir():
                return str(candidate)
            # Try with underscores
            candidate = current_path / remaining.replace("-", "_")
            if candidate.is_dir():
                return str(candidate)
            return None

    return str(current_path) if current_path != Path("/") else None


def get_session_name(session_path: Path) -> tuple[str, str]:
    """Extract project name and path from a session path.

    Session paths look like:
    ~/.claude/projects/-Users-tijs-projects-claude-code-live/abc123.jsonl

    For subagent sessions in nested structure:
    ~/.claude/projects/-Users-tijs-projects-claude-code-live/SESSION-UUID/subagents/agent-xxx.jsonl

    The folder name encodes the original path with slashes replaced by dashes.
    Additionally, special characters are encoded: . -> -, ~ -> -, _ -> -.
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

    # Generate variants to handle special character encoding:
    # - -- could be /. (dotfile: /.mycel -> --mycel)
    # - --- could be /~/ (tilde dir: /~/ -> ---)
    # - ---- could be /~/. (tilde + dotfile: /~/.mycel -> ----mycel)
    # We restore special chars but keep dashes that will later be tested as path separators.
    folder_variants = [folder]
    if "----" in folder:
        # /~/. pattern (tilde directory + dotfile) - restore ~/.
        folder_variants.append(folder.replace("----", "-~-."))
    if "---" in folder:
        # /~/ pattern (tilde directory) - restore ~/
        folder_variants.append(folder.replace("---", "-~-"))
    if "--" in folder:
        # /. pattern (dotfile) - restore the dot
        folder_variants.append(folder.replace("--", "-."))

    # Try greedy path decoding first (handles complex paths with dashes in names)
    for folder_variant in folder_variants:
        decoded = _decode_path_greedy(folder_variant)
        if decoded:
            return Path(decoded).name, decoded

    # Fallback to contiguous dash replacement (original algorithm)
    for folder_variant in folder_variants:
        # Find all dash positions
        dash_positions = [i for i, c in enumerate(folder_variant) if c == "-"]

        # Try combinations of dashes that could be path separators
        # Start with trying each individual dash position from the end
        # (most likely the project name is at the end)
        for num_path_seps in range(len(dash_positions), 0, -1):
            # Try the last N dashes as path separators
            for i in range(len(dash_positions) - num_path_seps + 1):
                positions_to_replace = dash_positions[i : i + num_path_seps]
                candidate = list(folder_variant)
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


def get_last_message_timestamp(path: Path) -> float | None:
    """Get the timestamp of the last user/assistant message in a session file.

    Args:
        path: Path to the session JSONL file.

    Returns:
        Unix timestamp (seconds since epoch) of the last message,
        or None if no messages found.
    """
    tailer = ClaudeCodeTailer(path)
    return tailer.get_last_message_timestamp()


def find_recent_sessions(
    projects_dir: Path | None = None,
    limit: int = 10,
    include_subagents: bool = True,
) -> list[Path]:
    """Find the most recently active session files that have messages.

    Args:
        projects_dir: Base directory to search (defaults to ~/.claude/projects)
        limit: Maximum number of sessions to return
        include_subagents: Whether to include subagent sessions (default True)

    Returns:
        List of paths to recent .jsonl files with messages, sorted by last message timestamp (newest first)
    """
    if projects_dir is None:
        projects_dir = DEFAULT_PROJECTS_DIR

    if not projects_dir.exists():
        logger.warning(f"Projects directory not found: {projects_dir}")
        return []

    # Find all .jsonl files, use mtime for initial rough ordering
    candidates = []
    for f in projects_dir.glob("**/*.jsonl"):
        # Filter out subagents if requested
        if not include_subagents and is_subagent_session(f):
            continue
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
    # We check more candidates than needed in case some have no messages
    sessions_with_timestamps: list[tuple[Path, float]] = []
    for f, _ in candidates:
        if not has_messages(f) or is_warmup_session(f):
            continue
        msg_timestamp = get_last_message_timestamp(f)
        if msg_timestamp is not None:
            sessions_with_timestamps.append((f, msg_timestamp))
        # Stop early if we have enough candidates with recent timestamps
        # Check 3x limit to handle files with touched mtimes but old messages
        if len(sessions_with_timestamps) >= limit * 3:
            break

    # Sort by actual message timestamp (accurate order)
    sessions_with_timestamps.sort(key=lambda x: x[1], reverse=True)

    return [f for f, _ in sessions_with_timestamps[:limit]]


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
        True if the file is a Claude Code session file or summary file that should be watched.
    """
    # Watch .jsonl session files
    if path.suffix == ".jsonl":
        # Filter out subagents if requested
        if not include_subagents and is_subagent_session(path):
            return False
        return True

    # Watch *_summary.json files
    if path.name.endswith("_summary.json"):
        return True

    return False


def is_summary_file(path: Path) -> bool:
    """Check if a file is a summary file.

    Args:
        path: File path to check.

    Returns:
        True if the file is a summary file.
    """
    return path.name.endswith("_summary.json")


def get_session_id_from_summary_file(path: Path) -> str | None:
    """Extract session ID from a summary file path.

    Summary files are named: <session_id>_summary.json

    Args:
        path: Path to the summary file.

    Returns:
        Session ID, or None if not a valid summary file.
    """
    if not is_summary_file(path):
        return None
    # Remove _summary.json suffix to get session ID
    return path.stem.replace("_summary", "")
