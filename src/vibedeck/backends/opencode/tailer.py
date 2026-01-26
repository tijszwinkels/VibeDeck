"""Session tailer for OpenCode.

Unlike Claude Code's JSONL-based tailer, OpenCode stores messages and parts
as separate JSON files in a hierarchical directory structure. This tailer
aggregates those files to provide a unified view.

Storage layout:
    message/{sessionID}/{messageID}.json    # Messages
    part/{messageID}/{partID}.json          # Message parts
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class OpenCodeTailer:
    """Tailer that aggregates JSON files from message/ and part/ directories.

    This implements the SessionTailerProtocol for OpenCode's directory-based
    storage format.
    """

    def __init__(self, storage_dir: Path, session_id: str):
        """Initialize the tailer.

        Args:
            storage_dir: Base storage directory (~/.local/share/opencode/storage).
            session_id: Session ID to tail.
        """
        self._storage_dir = storage_dir
        self._session_id = session_id
        self._seen_messages: set[str] = set()
        self._seen_parts: dict[str, set[str]] = {}  # messageID -> set of partIDs
        self._message_mtimes: dict[str, float] = {}  # messageID -> last seen mtime
        self._part_mtimes: dict[
            str, dict[str, float]
        ] = {}  # messageID -> {partID -> mtime}
        self._waiting_for_input: bool = False
        self._first_timestamp: str | None = None

    @property
    def waiting_for_input(self) -> bool:
        """Whether the session is waiting for user input."""
        return self._waiting_for_input

    def _get_msg_dir(self) -> Path:
        """Get the message directory for this session."""
        return self._storage_dir / "message" / self._session_id

    def _read_parts(self, message_id: str) -> list[dict]:
        """Read all parts for a message.

        Args:
            message_id: Message ID to read parts for.

        Returns:
            List of part dictionaries, sorted by ID.
        """
        parts = []
        part_dir = self._storage_dir / "part" / message_id
        if part_dir.exists():
            for part_file in part_dir.glob("*.json"):
                try:
                    part_data = json.loads(part_file.read_text())
                    parts.append(part_data)
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Failed to read part file {part_file}: {e}")
        # Sort by ID (parts have IDs like "part_xxx")
        parts.sort(key=lambda p: p.get("id", ""))
        return parts

    def _read_new_parts(self, message_id: str) -> list[dict]:
        """Read only new or updated parts for a message.

        Args:
            message_id: Message ID to check for new parts.

        Returns:
            List of new part dictionaries.
        """
        new_parts = []
        part_dir = self._storage_dir / "part" / message_id
        seen = self._seen_parts.get(message_id, set())
        mtimes = self._part_mtimes.get(message_id, {})

        if part_dir.exists():
            for part_file in part_dir.glob("*.json"):
                part_id = part_file.stem
                try:
                    current_mtime = part_file.stat().st_mtime
                    # Check if part is new or updated
                    if part_id not in seen or mtimes.get(part_id, 0) < current_mtime:
                        part_data = json.loads(part_file.read_text())
                        new_parts.append(part_data)
                        seen.add(part_id)
                        mtimes[part_id] = current_mtime
                except (json.JSONDecodeError, IOError, OSError) as e:
                    logger.warning(f"Failed to read part file {part_file}: {e}")

        self._seen_parts[message_id] = seen
        self._part_mtimes[message_id] = mtimes
        new_parts.sort(key=lambda p: p.get("id", ""))
        return new_parts

    def seek_to_end(self) -> None:
        """Mark all existing messages as seen without reading their content.

        Use this for fast initialization when you don't need existing messages,
        only future changes. read_new_lines() will only return truly new messages.
        """
        msg_dir = self._get_msg_dir()
        if not msg_dir.exists():
            return

        # Mark all existing message IDs as seen without reading content
        for msg_file in msg_dir.glob("*.json"):
            msg_id = msg_file.stem
            self._seen_messages.add(msg_id)

    def read_all(self) -> list[dict]:
        """Read all messages with their parts, sorted by ID.

        Returns:
            List of message entries, each containing 'info' (message data)
            and 'parts' (list of part data).
        """
        messages = []
        msg_dir = self._get_msg_dir()
        if not msg_dir.exists():
            return []

        for msg_file in msg_dir.glob("*.json"):
            try:
                msg_data = json.loads(msg_file.read_text())
                message_id = msg_data.get("id")
                if message_id:
                    parts = self._read_parts(message_id)
                    messages.append({"info": msg_data, "parts": parts})
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to read message file {msg_file}: {e}")

        # Sort by message ID (ascending order)
        messages.sort(key=lambda m: m["info"].get("id", ""))

        # Update waiting state based on all messages
        self._update_waiting_state(messages)

        return messages

    def read_new_lines(self) -> list[dict]:
        """Read only new or updated messages since last call.

        Returns:
            List of new message entries. Each entry has 'info', 'parts',
            and optionally 'partial' (True if only parts were updated).
        """
        new_entries = []
        msg_dir = self._get_msg_dir()
        if not msg_dir.exists():
            return []

        for msg_file in msg_dir.glob("*.json"):
            msg_id = msg_file.stem
            try:
                msg_mtime = msg_file.stat().st_mtime

                # Check if message is new (not yet emitted)
                if msg_id not in self._seen_messages:
                    msg_data = json.loads(msg_file.read_text())
                    message_id = msg_data.get("id")
                    if message_id:
                        parts = self._read_parts(message_id)
                        role = msg_data.get("role")

                        # Determine if message is ready to emit:
                        # - User messages: emit when they have text content
                        # - Assistant messages: emit when they have a step-finish part
                        #   (indicates the streaming/tool execution is complete)
                        is_ready = False
                        if role == "user":
                            # User messages are ready when they have text
                            is_ready = any(p.get("type") == "text" for p in parts)
                        else:
                            # Assistant messages are ready when step-finish exists
                            # This signals the LLM turn is complete
                            is_ready = any(
                                p.get("type") == "step-finish" for p in parts
                            )

                        if is_ready:
                            new_entries.append({"info": msg_data, "parts": parts})
                            self._seen_messages.add(msg_id)
                            self._message_mtimes[msg_id] = msg_mtime
                            self._seen_parts[message_id] = {
                                p.get("id", "") for p in parts
                            }
                # OpenCode updates part files frequently during streaming, which would
                # cause messages to be re-rendered constantly. For now, we only emit
                # truly new messages. Streaming updates can be added later with proper
                # client-side handling for partial updates.

            except (json.JSONDecodeError, IOError, OSError) as e:
                logger.warning(f"Failed to read message file {msg_file}: {e}")

        # Sort by message ID
        new_entries.sort(key=lambda m: m["info"].get("id", ""))

        # Update waiting state
        self._update_waiting_state(new_entries)

        return new_entries

    def _update_waiting_state(self, entries: list[dict]) -> None:
        """Update waiting-for-input state based on messages.

        The session is waiting for input when:
        - The last message is an assistant message
        - The last part is a text part (not a tool call)

        Args:
            entries: List of message entries to check.
        """
        if not entries:
            return

        # Get the last message
        last = entries[-1]
        info = last.get("info", {})
        parts = last.get("parts", [])

        if info.get("role") == "assistant":
            if parts:
                last_part = parts[-1]
                part_type = last_part.get("type", "")
                # Waiting if last part is text (agent has responded)
                # Not waiting if last part is tool (agent is still working)
                if part_type == "text":
                    self._waiting_for_input = True
                elif part_type in ("tool", "step-start"):
                    self._waiting_for_input = False
                elif part_type == "step-finish":
                    # Step finished, likely waiting for input
                    self._waiting_for_input = True
            else:
                self._waiting_for_input = False
        elif info.get("role") == "user":
            # User sent a message, not waiting
            self._waiting_for_input = False

    def get_first_timestamp(self) -> str | None:
        """Get the timestamp of the first message in the session.

        Returns:
            ISO timestamp string, or None if no messages.
        """
        if self._first_timestamp is not None:
            return self._first_timestamp

        msg_dir = self._get_msg_dir()
        if not msg_dir.exists():
            return None

        # Get first message by ID
        msg_files = sorted(msg_dir.glob("*.json"))
        if not msg_files:
            return None

        try:
            msg_data = json.loads(msg_files[0].read_text())
            time_data = msg_data.get("time", {})
            created = time_data.get("created")
            if created:
                # OpenCode uses Unix milliseconds
                self._first_timestamp = self._format_timestamp(created)
                return self._first_timestamp
        except (json.JSONDecodeError, IOError, KeyError):
            pass

        return None

    def _format_timestamp(self, unix_ms: int | float) -> str:
        """Format Unix milliseconds as ISO timestamp.

        Args:
            unix_ms: Unix timestamp in milliseconds.

        Returns:
            ISO format timestamp string.
        """
        try:
            dt = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc)
            return dt.isoformat()
        except (ValueError, TypeError, OSError):
            return ""

    def get_last_message_timestamp(self) -> float | None:
        """Get the timestamp of the last message in the session.

        Returns:
            Unix timestamp (seconds since epoch) of the last message,
            or None if no messages found.
        """
        msg_dir = self._get_msg_dir()
        if not msg_dir.exists():
            return None

        # Get the last message by ID (messages are sorted by ID)
        msg_files = sorted(msg_dir.glob("*.json"), reverse=True)
        if not msg_files:
            return None

        try:
            msg_data = json.loads(msg_files[0].read_text())
            time_data = msg_data.get("time", {})
            # Use 'updated' if available, fall back to 'created'
            timestamp_ms = time_data.get("updated") or time_data.get("created")
            if timestamp_ms:
                # OpenCode uses Unix milliseconds, convert to seconds
                return timestamp_ms / 1000
        except (json.JSONDecodeError, IOError, KeyError):
            pass

        return None
