"""Tailer for Codex rollout JSONL transcripts."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..base import JsonlTailer
from .discovery import is_bootstrap_user_message

logger = logging.getLogger(__name__)


class CodexTailer(JsonlTailer):
    """Tail append-only Codex rollout files."""

    def __init__(self, path: Path, show_bootstrap_messages: bool = False):
        super().__init__(path)
        self._show_bootstrap_messages = show_bootstrap_messages

    def _should_include_entry(self, entry: dict) -> bool:
        entry_type = entry.get("type")
        payload = entry.get("payload", {})
        payload_type = payload.get("type")

        if not self._show_bootstrap_messages and is_bootstrap_user_message(entry):
            return False

        if entry_type != "response_item":
            return False
        if payload_type in {"function_call", "function_call_output"}:
            return True
        return payload_type == "message" and payload.get("role") in {"user", "assistant"}

    def _update_waiting_state(self, entry: dict) -> None:
        entry_type = entry.get("type")
        payload = entry.get("payload", {})
        payload_type = payload.get("type")

        if entry_type == "response_item" and payload_type == "function_call":
            self._waiting_for_input = False
            return
        if entry_type == "response_item" and payload_type == "message":
            self._waiting_for_input = payload.get("role") == "assistant"

    def get_first_timestamp(self) -> str | None:
        if self._first_timestamp is not None:
            return self._first_timestamp

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") == "session_meta":
                        timestamp = entry.get("payload", {}).get("timestamp")
                        if timestamp:
                            self._first_timestamp = timestamp
                            return timestamp
                    if self._should_include_entry(entry):
                        timestamp = entry.get("timestamp")
                        if timestamp:
                            self._first_timestamp = timestamp
                            return timestamp
        except OSError:
            return None

        return None

    def get_last_message_timestamp(self) -> float | None:
        last_timestamp = None
        for entry in self.read_all():
            timestamp = entry.get("timestamp")
            if not timestamp:
                continue
            try:
                last_timestamp = datetime.fromisoformat(
                    timestamp.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                continue
        return last_timestamp

    def read_new_lines(self) -> list[dict]:
        start_position = self.position
        entries = super().read_new_lines()
        logger.debug(
            "Codex tail read %s entries from %s (pos %s -> %s, waiting=%s)",
            len(entries),
            self.path.name,
            start_position,
            self.position,
            self.waiting_for_input,
        )
        if entries:
            logger.debug(
                "Codex tail entry types for %s: %s",
                self.path.name,
                [
                    (
                        entry.get("payload", {}).get("type"),
                        entry.get("payload", {}).get("role"),
                    )
                    for entry in entries
                ],
            )
        return entries


def has_messages(session_path: Path) -> bool:
    """Check whether a session contains transcript entries."""
    return bool(CodexTailer(session_path).read_all())
