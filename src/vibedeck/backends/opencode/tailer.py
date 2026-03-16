"""Session tailer for OpenCode.

Supports both SQLite database (v1.2+) and legacy JSON file storage.

SQLite storage (v1.2+):
    ~/.local/share/opencode/opencode.db
        session table - session metadata
        message table - messages (with JSON data column)
        part table - message parts (with JSON data column)

Legacy JSON storage (deprecated):
    message/{sessionID}/{messageID}.json    # Messages
    part/{messageID}/{partID}.json          # Message parts
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .db import OpenCodeDB, db_exists

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
DEFAULT_STORAGE_DIR = Path.home() / ".local" / "share" / "opencode" / "storage"


class OpenCodeTailer:
    """Tailer that reads from SQLite database or JSON files.

    This implements the SessionTailerProtocol for OpenCode's storage format.
    It automatically detects whether to use SQLite or JSON based on availability.
    """

    def __init__(self, storage_dir: Path | None, session_id: str):
        """Initialize the tailer.

        Args:
            storage_dir: Legacy storage directory (~/.local/share/opencode/storage).
                Can be None for SQLite-only mode.
            session_id: Session ID to tail.
        """
        self._storage_dir = storage_dir or DEFAULT_STORAGE_DIR
        self._session_id = session_id
        self._seen_messages: set[str] = set()
        self._seen_parts: dict[str, set[str]] = {}
        self._waiting_for_input: bool = False
        self._first_timestamp: str | None = None
        custom_storage = self._storage_dir != DEFAULT_STORAGE_DIR
        self._use_db = db_exists() and not custom_storage

    @property
    def waiting_for_input(self) -> bool:
        return self._waiting_for_input

    def _get_msg_dir(self) -> Path:
        return self._storage_dir / "message" / self._session_id

    def _read_parts_from_db(self, message_id: str) -> list[dict]:
        with OpenCodeDB() as db:
            parts = db.get_parts_for_message(message_id)
            return [p.data for p in parts]

    def _read_parts_from_json(self, message_id: str) -> list[dict]:
        parts = []
        part_dir = self._storage_dir / "part" / message_id
        if part_dir.exists():
            for part_file in sorted(part_dir.glob("*.json")):
                try:
                    part_data = json.loads(part_file.read_text())
                    parts.append(part_data)
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Failed to read part file {part_file}: {e}")
        return parts

    def _read_parts(self, message_id: str) -> list[dict]:
        if self._use_db:
            return self._read_parts_from_db(message_id)
        return self._read_parts_from_json(message_id)

    def seek_to_end(self) -> None:
        if self._use_db:
            with OpenCodeDB() as db:
                for msg in db.get_messages_for_session(self._session_id):
                    # Only mark ready messages as seen
                    # Not-ready messages (e.g., assistant without step-finish)
                    # should be returned once they become ready
                    parts = self._read_parts_from_db(msg.id)
                    role = msg.data.get("role")
                    is_ready = False
                    if role == "user":
                        is_ready = any(p.get("type") == "text" for p in parts)
                    else:
                        is_ready = any(p.get("type") == "step-finish" for p in parts)

                    if is_ready:
                        self._seen_messages.add(msg.id)
        else:
            msg_dir = self._get_msg_dir()
            if msg_dir.exists():
                for msg_file in msg_dir.glob("*.json"):
                    # For legacy JSON, also check if message is ready
                    try:
                        msg_data = json.loads(msg_file.read_text())
                        msg_id = msg_file.stem
                        role = msg_data.get("role")

                        is_ready = False
                        if role == "user":
                            # User messages are ready if they have a text part
                            part_dir = self._storage_dir / "part" / msg_id
                            if part_dir.exists():
                                for part_file in part_dir.glob("*.json"):
                                    try:
                                        part_data = json.loads(part_file.read_text())
                                        if part_data.get("type") == "text":
                                            is_ready = True
                                            break
                                    except (json.JSONDecodeError, IOError):
                                        continue
                        else:
                            # Assistant messages are ready if they have step-finish
                            part_dir = self._storage_dir / "part" / msg_id
                            if part_dir.exists():
                                for part_file in part_dir.glob("*.json"):
                                    try:
                                        part_data = json.loads(part_file.read_text())
                                        if part_data.get("type") == "step-finish":
                                            is_ready = True
                                            break
                                    except (json.JSONDecodeError, IOError):
                                        continue

                        if is_ready:
                            self._seen_messages.add(msg_id)
                    except (json.JSONDecodeError, IOError):
                        # If we can't read it, mark it as seen to skip it
                        self._seen_messages.add(msg_file.stem)

    def _read_all_from_db(self) -> list[dict]:
        messages = []
        with OpenCodeDB() as db:
            for msg in db.get_messages_for_session(self._session_id):
                parts = self._read_parts_from_db(msg.id)
                info = msg.data.copy()
                info["id"] = msg.id
                info["sessionID"] = msg.session_id
                messages.append({"info": info, "parts": parts})

        messages.sort(key=lambda m: m["info"].get("id", ""))
        self._update_waiting_state(messages)
        return messages

    def _read_all_from_json(self) -> list[dict]:
        messages = []
        msg_dir = self._get_msg_dir()
        if not msg_dir.exists():
            return []

        for msg_file in msg_dir.glob("*.json"):
            try:
                msg_data = json.loads(msg_file.read_text())
                message_id = msg_data.get("id")
                if message_id:
                    parts = self._read_parts_from_json(message_id)
                    messages.append({"info": msg_data, "parts": parts})
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to read message file {msg_file}: {e}")

        messages.sort(key=lambda m: m["info"].get("id", ""))
        self._update_waiting_state(messages)
        return messages

    def read_all(self) -> list[dict]:
        if self._use_db:
            return self._read_all_from_db()
        return self._read_all_from_json()

    def _read_new_from_db(self) -> list[dict]:
        new_entries = []
        with OpenCodeDB() as db:
            messages = db.get_messages_for_session(self._session_id)
            ready_count = 0
            not_ready_count = 0
            seen_count = 0
            for msg in messages:
                if msg.id in self._seen_messages:
                    seen_count += 1
                    continue

                parts = self._read_parts_from_db(msg.id)
                info = msg.data.copy()
                info["id"] = msg.id
                info["sessionID"] = msg.session_id

                role = info.get("role")
                is_ready = False
                if role == "user":
                    is_ready = any(p.get("type") == "text" for p in parts)
                else:
                    is_ready = any(p.get("type") == "step-finish" for p in parts)

                if is_ready:
                    new_entries.append({"info": info, "parts": parts})
                    self._seen_messages.add(msg.id)
                    ready_count += 1
                else:
                    not_ready_count += 1

        logger.debug(
            f"_read_new_from_db: {len(new_entries)} new entries, "
            f"{seen_count} seen, {not_ready_count} not ready for {self._session_id}"
        )
        new_entries.sort(key=lambda m: m["info"].get("id", ""))
        self._update_waiting_state(new_entries)
        return new_entries

    def _read_new_from_json(self) -> list[dict]:
        new_entries = []
        msg_dir = self._get_msg_dir()
        if not msg_dir.exists():
            return []

        for msg_file in msg_dir.glob("*.json"):
            msg_id = msg_file.stem
            try:
                if msg_id not in self._seen_messages:
                    msg_data = json.loads(msg_file.read_text())
                    message_id = msg_data.get("id")
                    if message_id:
                        parts = self._read_parts_from_json(message_id)
                        role = msg_data.get("role")

                        is_ready = False
                        if role == "user":
                            is_ready = any(p.get("type") == "text" for p in parts)
                        else:
                            is_ready = any(
                                p.get("type") == "step-finish" for p in parts
                            )

                        if is_ready:
                            new_entries.append({"info": msg_data, "parts": parts})
                            self._seen_messages.add(msg_id)
            except (json.JSONDecodeError, IOError, OSError) as e:
                logger.warning(f"Failed to read message file {msg_file}: {e}")

        new_entries.sort(key=lambda m: m["info"].get("id", ""))
        self._update_waiting_state(new_entries)
        return new_entries

    def read_new_lines(self) -> list[dict]:
        if self._use_db:
            return self._read_new_from_db()
        return self._read_new_from_json()

    def _update_waiting_state(self, entries: list[dict]) -> None:
        if not entries:
            return

        last = entries[-1]
        info = last.get("info", {})
        parts = last.get("parts", [])

        if info.get("role") == "assistant":
            if parts:
                last_part = parts[-1]
                part_type = last_part.get("type", "")
                if part_type == "text":
                    self._waiting_for_input = True
                elif part_type in ("tool", "step-start"):
                    self._waiting_for_input = False
                elif part_type == "step-finish":
                    self._waiting_for_input = True
            else:
                self._waiting_for_input = False
        elif info.get("role") == "user":
            self._waiting_for_input = False

    def _get_first_timestamp_from_db(self) -> str | None:
        with OpenCodeDB() as db:
            messages = db.get_messages_for_session(self._session_id)
            if not messages:
                return None
            first_msg = messages[0]
            time_data = first_msg.data.get("time", {})
            created = time_data.get("created")
            if created:
                return self._format_timestamp(created)
        return None

    def _get_first_timestamp_from_json(self) -> str | None:
        msg_dir = self._get_msg_dir()
        if not msg_dir.exists():
            return None

        msg_files = sorted(msg_dir.glob("*.json"))
        if not msg_files:
            return None

        try:
            msg_data = json.loads(msg_files[0].read_text())
            time_data = msg_data.get("time", {})
            created = time_data.get("created")
            if created:
                return self._format_timestamp(created)
        except (json.JSONDecodeError, IOError, KeyError):
            pass

        return None

    def get_first_timestamp(self) -> str | None:
        if self._first_timestamp is not None:
            return self._first_timestamp

        if self._use_db:
            self._first_timestamp = self._get_first_timestamp_from_db()
        else:
            self._first_timestamp = self._get_first_timestamp_from_json()
        return self._first_timestamp

    def _format_timestamp(self, unix_ms: int | float) -> str:
        try:
            dt = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc)
            return dt.isoformat()
        except (ValueError, TypeError, OSError):
            return ""

    def _get_last_timestamp_from_db(self) -> float | None:
        with OpenCodeDB() as db:
            cursor = db._get_conn().cursor()
            cursor.execute(
                "SELECT MAX(time_updated) FROM message WHERE session_id = ?",
                (self._session_id,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0] / 1000
        return None

    def _get_last_timestamp_from_json(self) -> float | None:
        msg_dir = self._get_msg_dir()
        if not msg_dir.exists():
            return None

        msg_files = sorted(msg_dir.glob("*.json"), reverse=True)
        if not msg_files:
            return None

        try:
            msg_data = json.loads(msg_files[0].read_text())
            time_data = msg_data.get("time", {})
            timestamp_ms = time_data.get("updated") or time_data.get("created")
            if timestamp_ms:
                return timestamp_ms / 1000
        except (json.JSONDecodeError, IOError, KeyError):
            pass

        return None

    def get_last_message_timestamp(self) -> float | None:
        if self._use_db:
            return self._get_last_timestamp_from_db()
        return self._get_last_timestamp_from_json()
