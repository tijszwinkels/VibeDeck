"""Base classes and shared utilities for backends.

This module provides abstract base classes and utilities that can be shared
across multiple backend implementations.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class BaseTailer(ABC):
    """Abstract base class for session tailers with common file reading logic.

    Provides incremental file reading with position tracking and line buffering.
    Subclasses implement the parsing logic for their specific format.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.position = 0  # Byte position in file
        self.buffer = ""  # Incomplete line buffer
        self.message_index = 0  # Count of messages yielded
        self._first_timestamp: str | None = None
        self._waiting_for_input: bool = False

    @property
    def waiting_for_input(self) -> bool:
        """Whether the session is waiting for user input."""
        return self._waiting_for_input

    def _read_raw_content(self) -> str:
        """Read new content from file since last position.

        Returns:
            New content as string, or empty string if no new content.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                f.seek(self.position)
                content = f.read()
                self.position = f.tell()
                return content
        except FileNotFoundError:
            logger.warning(f"File not found: {self.path}")
            return ""
        except IOError as e:
            logger.error(f"Error reading file: {e}")
            return ""

    def _split_lines(self, content: str) -> list[str]:
        """Split content into complete lines, keeping incomplete line in buffer.

        Args:
            content: New content read from file.

        Returns:
            List of complete lines (without newlines).
        """
        if not content:
            return []

        self.buffer += content
        lines = self.buffer.split("\n")
        # Keep the last (potentially incomplete) line in buffer
        self.buffer = lines[-1]

        # Return complete lines (excluding the buffered incomplete one)
        return [line.strip() for line in lines[:-1] if line.strip()]

    @abstractmethod
    def _parse_line(self, line: str) -> dict | None:
        """Parse a single line into a message entry.

        Args:
            line: Complete line to parse.

        Returns:
            Parsed message entry, or None if line should be skipped.
        """
        ...

    @abstractmethod
    def _update_waiting_state(self, entry: dict) -> None:
        """Update waiting-for-input state based on a message entry.

        Args:
            entry: Parsed message entry.
        """
        ...

    def seek_to_end(self) -> None:
        """Set position to end of file without reading content.

        Use this for fast initialization when you don't need the existing
        messages, only future changes. File watching still works because
        read_new_lines() will detect new content appended after this position.
        """
        try:
            with open(self.path, "rb") as f:
                f.seek(0, 2)  # SEEK_END
                self.position = f.tell()
        except (FileNotFoundError, IOError) as e:
            logger.warning(f"Failed to seek to end of {self.path}: {e}")
            self.position = 0

    def read_new_lines(self) -> list[dict]:
        """Read and parse new complete lines from the file.

        Returns:
            List of parsed message entries.
        """
        content = self._read_raw_content()
        lines = self._split_lines(content)

        results = []
        for line in lines:
            entry = self._parse_line(line)
            if entry is not None:
                results.append(entry)
                self.message_index += 1
                self._update_waiting_state(entry)

        return results

    def read_all(self) -> list[dict]:
        """Read all messages from the file from the beginning.

        Does NOT modify the current file position - creates a fresh reader.
        However, updates the waiting_for_input state to reflect the current
        file contents (this is intentional, as we want the latest state).

        Returns:
            List of all parsed message entries.
        """
        # Create a fresh tailer to read from start without affecting our position
        fresh = self.__class__(self.path)
        results = fresh.read_new_lines()
        # Copy the waiting state from the fresh tailer
        self._waiting_for_input = fresh._waiting_for_input
        return results

    @abstractmethod
    def get_first_timestamp(self) -> str | None:
        """Get the timestamp of the first message."""
        ...

    @abstractmethod
    def get_last_message_timestamp(self) -> float | None:
        """Get the timestamp of the last actual message.

        Returns:
            Unix timestamp (seconds since epoch) of the last message,
            or None if no messages found.
        """
        ...


class JsonlTailer(BaseTailer):
    """Base tailer for JSONL (JSON Lines) formatted session files.

    Parses each line as JSON. Subclasses can override _should_include_entry
    to filter which entries to include.
    """

    def _parse_line(self, line: str) -> dict | None:
        """Parse a JSON line.

        Args:
            line: JSON string to parse.

        Returns:
            Parsed JSON object if valid and should be included, None otherwise.
        """
        try:
            obj = json.loads(line)
            if self._should_include_entry(obj):
                return obj
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON line: {e}")
            return None

    def _should_include_entry(self, entry: dict) -> bool:
        """Check if an entry should be included in results.

        Override in subclasses to filter entries.

        Args:
            entry: Parsed JSON entry.

        Returns:
            True if entry should be included.
        """
        return True
