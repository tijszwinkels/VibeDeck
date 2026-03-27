"""Session file tailer for Pi Coding Agent JSONL files.

Handles tree linearization: pi sessions form a tree via id/parentId.
For display, we follow the last child at each branch point.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..base import JsonlTailer

logger = logging.getLogger(__name__)

# Entry types to include in display output
_DISPLAYABLE_ENTRY_TYPES = {"message", "compaction", "branch_summary"}

# Non-displayable entry types (filtered out)
_SKIP_ENTRY_TYPES = {
    "session",
    "custom",
    "label",
    "session_info",
    "model_change",
    "thinking_level_change",
    "custom_message",
}


def _linearize_tree(entries: list[dict]) -> list[dict]:
    """Linearize a tree of entries by following the last child at each branch.

    Args:
        entries: All entries (must have 'id' and 'parentId' fields).

    Returns:
        Entries in linear order following the last child path.
    """
    if not entries:
        return []

    # Build parent -> children mapping (preserving insertion order)
    children: dict[str | None, list[dict]] = {}
    entry_by_id: dict[str, dict] = {}

    for entry in entries:
        entry_id = entry.get("id")
        parent_id = entry.get("parentId")
        if entry_id:
            entry_by_id[entry_id] = entry
        children.setdefault(parent_id, []).append(entry)

    # Find root(s) - entries with parentId=None or parentId not in entry_by_id
    # Also consider entries whose parentId points to non-displayable entries
    # that we filtered out (like model_change, thinking_level_change)
    all_ids = set(entry_by_id.keys())

    # Walk from root following last child
    result = []

    # Find starting entries: those with no parent or parent not in our set
    roots = []
    for entry in entries:
        parent_id = entry.get("parentId")
        if parent_id is None or parent_id not in all_ids:
            roots.append(entry)

    if not roots:
        # Fallback: return entries as-is
        return entries

    # Start from the first root and walk
    current = roots[0]
    while current:
        result.append(current)
        current_id = current.get("id")
        kids = children.get(current_id, [])
        if kids:
            # Follow the LAST child (most recent branch)
            current = kids[-1]
        else:
            current = None

    return result


class PiTailer(JsonlTailer):
    """Tailer for Pi Coding Agent JSONL session files.

    Pi stores sessions as JSONL with tree structure (id/parentId).
    For incremental reading (read_new_lines), entries are appended as-is.
    For full reading (read_all), tree linearization is applied.
    """

    def _should_include_entry(self, entry: dict) -> bool:
        """Include message entries and structural markers."""
        entry_type = entry.get("type", "")
        if entry_type in _DISPLAYABLE_ENTRY_TYPES:
            return True
        return False

    def _update_waiting_state(self, entry: dict) -> None:
        """Update waiting-for-input state based on entry."""
        entry_type = entry.get("type", "")

        if entry_type != "message":
            return

        msg = entry.get("message", {})
        role = msg.get("role", "")

        if role == "assistant":
            stop_reason = msg.get("stopReason", "")
            if stop_reason == "stop":
                self._waiting_for_input = True
            else:
                # toolUse, error, etc - not waiting
                self._waiting_for_input = False
        elif role == "user":
            self._waiting_for_input = False
        elif role in ("toolResult", "bashExecution"):
            self._waiting_for_input = False

    def get_first_timestamp(self) -> str | None:
        """Get the session start timestamp from the header."""
        if self._first_timestamp is not None:
            return self._first_timestamp

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                line = f.readline().strip()
                if line:
                    obj = json.loads(line)
                    if obj.get("type") == "session":
                        self._first_timestamp = obj.get("timestamp")
                        return self._first_timestamp
        except (FileNotFoundError, IOError, json.JSONDecodeError):
            pass
        return None

    def get_last_message_timestamp(self) -> float | None:
        """Get the timestamp of the last user/assistant message.

        Returns:
            Unix timestamp (seconds since epoch), or None.
        """
        try:
            return self._find_last_message_timestamp()
        except (FileNotFoundError, IOError):
            return None

    def _find_last_message_timestamp(self) -> float | None:
        """Scan from end of file for last message timestamp."""
        with open(self.path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return None

            chunk_size = 65536
            bytes_read = 0

            while bytes_read < file_size:
                read_size = min(chunk_size, file_size)
                f.seek(file_size - read_size)
                chunk = f.read(read_size).decode("utf-8", errors="ignore")
                bytes_read = read_size

                lines = chunk.split("\n")
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("type") == "message":
                            role = obj.get("message", {}).get("role")
                            if role in ("user", "assistant"):
                                ts = obj.get("timestamp")
                                if ts:
                                    dt = datetime.fromisoformat(
                                        ts.replace("Z", "+00:00")
                                    )
                                    return dt.timestamp()
                    except json.JSONDecodeError:
                        continue

                chunk_size *= 2

            return None

    def read_all(self) -> list[dict]:
        """Read all messages with tree linearization.

        Overrides base class to apply tree linearization after reading.
        """
        # Read all entries using a fresh tailer
        fresh = self.__class__(self.path)
        raw_entries = fresh.read_new_lines()
        # Copy waiting state
        self._waiting_for_input = fresh._waiting_for_input

        # Apply tree linearization
        return _linearize_tree(raw_entries)
