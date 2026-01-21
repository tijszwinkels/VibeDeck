"""Session file tailer for Claude Code JSONL files."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..base import JsonlTailer

logger = logging.getLogger(__name__)


class ClaudeCodeTailer(JsonlTailer):
    """Tailer for Claude Code JSONL session files.

    Claude Code stores sessions as JSONL files with entries like:
    {"type": "user"|"assistant", "timestamp": "...", "message": {...}, "requestId": "..."}

    Only user and assistant message types are included in results.
    """

    def __init__(self, path: Path):
        super().__init__(path)
        self._last_message_type: str | None = None

    def _should_include_entry(self, entry: dict) -> bool:
        """Only include user and assistant messages."""
        return entry.get("type") in ("user", "assistant")

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
                content_type = (
                    last_content.get("type", "") if isinstance(last_content, dict) else ""
                )

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
                content_type = (
                    first_content.get("type", "") if isinstance(first_content, dict) else ""
                )
                if content_type == "tool_result":
                    self._last_message_type = "user_tool_result"
                    self._waiting_for_input = False
                else:
                    self._last_message_type = "user_input"
                    self._waiting_for_input = False
            elif isinstance(content, str):
                self._last_message_type = "user_input"
                self._waiting_for_input = False

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

    def get_last_message_timestamp(self) -> float | None:
        """Get the timestamp of the last user/assistant message.

        Efficiently reads from the end of the file to find the last actual message,
        ignoring summary entries added by Claude Code's summarization feature.

        Returns:
            Unix timestamp (seconds since epoch) of the last message,
            or None if no messages found.
        """
        try:
            return self._find_last_message_timestamp_from_tail()
        except (FileNotFoundError, IOError):
            return None

    def _find_last_message_timestamp_from_tail(
        self, initial_chunk_size: int = 65536
    ) -> float | None:
        """Read from end of file to find last message timestamp.

        Starts with initial_chunk_size bytes, doubles if needed until found
        or entire file is read.
        """
        with open(self.path, "rb") as f:
            f.seek(0, 2)  # Seek to end
            file_size = f.tell()

            if file_size == 0:
                return None

            chunk_size = initial_chunk_size
            bytes_read = 0

            while bytes_read < file_size:
                read_size = min(chunk_size, file_size)
                f.seek(file_size - read_size)
                chunk = f.read(read_size).decode("utf-8", errors="ignore")
                bytes_read = read_size

                result = self._parse_last_timestamp_from_chunk(chunk)
                if result is not None:
                    return result

                # Double chunk size and retry
                chunk_size *= 2

            return None

    def _parse_last_timestamp_from_chunk(self, chunk: str) -> float | None:
        """Parse the last user/assistant message timestamp from a text chunk."""
        lines = chunk.split("\n")
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                # Only consider user/assistant messages, skip summary entries
                if obj.get("type") in ("user", "assistant"):
                    timestamp_str = obj.get("timestamp")
                    if timestamp_str:
                        dt = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        )
                        return dt.timestamp()
            except json.JSONDecodeError:
                # Could be partial line at start of chunk, continue
                continue
        return None


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


def is_warmup_session(session_path: Path) -> bool:
    """Check if a session is a warmup session (first message is 'Warmup').

    Warmup sessions are created by Claude Code to pre-warm subagents.
    They're not meaningful user sessions.

    Args:
        session_path: Path to the session JSONL file

    Returns:
        True if the first user message is exactly 'Warmup'.
    """
    first_message = get_first_user_message(session_path, max_length=50)
    return first_message == "Warmup"


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
