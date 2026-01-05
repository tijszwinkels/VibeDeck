"""File tailer for watching Claude Code session files."""

import json
import logging
import urllib.parse
from pathlib import Path
from typing import AsyncGenerator, Callable

import watchfiles

logger = logging.getLogger(__name__)


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
            candidate_with_underscores = [
                "_" if c == "-" else c for c in candidate
            ]
            candidate_path_underscore = "/" + "".join(candidate_with_underscores)
            if Path(candidate_path_underscore).is_dir():
                return Path(candidate_path_underscore).name, candidate_path_underscore

    # Fallback: return the folder name as-is
    return folder or session_path.parent.name, folder


def get_session_id(session_path: Path) -> str:
    """Get the session ID (filename without extension)."""
    return session_path.stem


def has_messages(session_path: Path) -> bool:
    """Check if a session file has any user or assistant messages.

    Args:
        session_path: Path to the session JSONL file

    Returns:
        True if the session has at least one user or assistant message.
    """
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") in ("user", "assistant"):
                        return True
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, IOError):
        pass
    return False


def get_first_user_message(session_path: Path, max_length: int = 200) -> str | None:
    """Read the first user message from a session file.

    Args:
        session_path: Path to the session JSONL file
        max_length: Maximum length of message to return

    Returns:
        The first user message text, truncated to max_length, or None if not found.
    """
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "user":
                        content = entry.get("message", {}).get("content", [])

                        # Content can be a string or a list of blocks
                        if isinstance(content, str):
                            # Skip command messages (start with <command-)
                            if content.startswith("<command-"):
                                continue
                            text = content.strip()
                            if text:
                                return text[:max_length] if len(text) > max_length else text
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text = block.get("text", "").strip()
                                    if text:
                                        return (
                                            text[:max_length]
                                            if len(text) > max_length
                                            else text
                                        )
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, IOError):
        pass
    return None


class SessionTailer:
    """Tail a JSONL session file, yielding new complete lines."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.position = 0  # Byte position in file
        self.buffer = ""  # Incomplete line buffer
        self.message_index = 0  # Count of messages yielded
        self._first_timestamp: str | None = None  # Cached first message timestamp
        self._last_message_type: str | None = None  # 'assistant_text', 'assistant_tool', 'user', etc.
        self._waiting_for_input: bool = False  # True when agent finished and waiting

    def get_first_timestamp(self) -> str | None:
        """Get the timestamp of the first message in the session."""
        if self._first_timestamp is not None:
            return self._first_timestamp

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("type") in ("user", "assistant"):
                            self._first_timestamp = obj.get("timestamp", "")
                            return self._first_timestamp
                    except json.JSONDecodeError:
                        continue
        except (FileNotFoundError, IOError):
            pass
        return None

    def _update_waiting_state(self, entry: dict) -> None:
        """Update the waiting-for-input state based on a message entry.

        Agent is waiting for input when the last message is an assistant
        message with text content (not tool_use).
        """
        entry_type = entry.get("type")
        message_data = entry.get("message", {})
        content = message_data.get("content", [])

        if entry_type == "assistant":
            # Check the content type
            if isinstance(content, list) and content:
                last_content = content[-1] if content else {}
                content_type = last_content.get("type", "") if isinstance(last_content, dict) else ""

                if content_type == "tool_use":
                    self._last_message_type = "assistant_tool"
                    self._waiting_for_input = False
                elif content_type == "text":
                    self._last_message_type = "assistant_text"
                    self._waiting_for_input = True
                else:
                    # thinking, etc - keep previous state
                    pass
            else:
                self._last_message_type = "assistant_other"
                self._waiting_for_input = False

        elif entry_type == "user":
            # Check if it's a tool result or actual user input
            if isinstance(content, list) and content:
                first_content = content[0] if content else {}
                content_type = first_content.get("type", "") if isinstance(first_content, dict) else ""
                if content_type == "tool_result":
                    self._last_message_type = "user_tool_result"
                    self._waiting_for_input = False
                else:
                    self._last_message_type = "user_input"
                    self._waiting_for_input = False
            elif isinstance(content, str):
                self._last_message_type = "user_input"
                self._waiting_for_input = False

    @property
    def waiting_for_input(self) -> bool:
        """Check if the session is waiting for user input."""
        return self._waiting_for_input

    def read_new_lines(self) -> list[dict]:
        """Read and parse new complete lines from the file.

        Returns a list of parsed JSON objects for each complete new line.
        Handles incomplete lines by buffering them for the next read.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                f.seek(self.position)
                content = f.read()
                self.position = f.tell()
        except FileNotFoundError:
            logger.warning(f"File not found: {self.path}")
            return []
        except IOError as e:
            logger.error(f"Error reading file: {e}")
            return []

        if not content:
            return []

        self.buffer += content
        lines = self.buffer.split("\n")
        # Keep the last (potentially incomplete) line in buffer
        self.buffer = lines[-1]

        results = []
        for line in lines[:-1]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                # Only process user and assistant messages
                entry_type = obj.get("type")
                if entry_type in ("user", "assistant"):
                    results.append(obj)
                    self.message_index += 1
                    self._update_waiting_state(obj)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON line: {e}")
                continue

        return results

    def read_all(self) -> list[dict]:
        """Read all messages from the file from the beginning.

        Does NOT modify the current position - reads a fresh copy.
        Returns list of all parsed message objects.
        Also updates the waiting_for_input state based on all messages.
        """
        # Create a fresh tailer to read from start without affecting our position
        fresh_tailer = SessionTailer(self.path)
        results = fresh_tailer.read_new_lines()
        # Copy the waiting state from the fresh tailer
        self._waiting_for_input = fresh_tailer._waiting_for_input
        self._last_message_type = fresh_tailer._last_message_type
        return results


async def watch_file(path: Path, callback: Callable[[], None]) -> AsyncGenerator[None, None]:
    """Watch a file for modifications and call callback on each change.

    Args:
        path: Path to the file to watch
        callback: Async function to call when file changes

    Yields:
        Nothing, runs indefinitely until cancelled
    """
    logger.info(f"Starting file watch on {path}")
    try:
        async for changes in watchfiles.awatch(path):
            for change_type, changed_path in changes:
                if change_type == watchfiles.Change.modified:
                    logger.debug(f"File modified: {changed_path}")
                    await callback()
    except Exception as e:
        logger.error(f"Error watching file: {e}")
        raise


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
        projects_dir = Path.home() / ".claude" / "projects"

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
