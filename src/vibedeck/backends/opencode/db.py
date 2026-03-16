"""SQLite database access for OpenCode.

OpenCode v1.2+ stores sessions, messages, and parts in a SQLite database
at ~/.local/share/opencode/opencode.db

This module provides functions to read from this database.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


@dataclass
class SessionRow:
    id: str
    project_id: str
    parent_id: str | None
    slug: str
    directory: str
    title: str
    version: str
    time_created: int
    time_updated: int


@dataclass
class MessageRow:
    id: str
    session_id: str
    time_created: int
    time_updated: int
    data: dict


@dataclass
class PartRow:
    id: str
    message_id: str
    session_id: str
    time_created: int
    time_updated: int
    data: dict


class OpenCodeDB:
    """SQLite connection to the OpenCode database.

    This class provides read-only access to the OpenCode SQLite database.
    It's designed to be used as a context manager for safe connection handling.
    """

    def __init__(self, db_path: Path | None = None):
        """Initialize the database connection.

        Args:
            db_path: Path to the SQLite database. Defaults to
                ~/.local/share/opencode/opencode.db
        """
        self._db_path = db_path or DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "OpenCodeDB":
        self._conn = sqlite3.connect(str(self._db_path), uri=True)
        self._conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, *args) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Use as context manager.")
        return self._conn

    def is_available(self) -> bool:
        return self._db_path.exists()

    def get_recent_sessions(self, limit: int = 10) -> list[SessionRow]:
        cursor = self._get_conn().cursor()
        cursor.execute(
            """
            SELECT id, project_id, parent_id, slug, directory, title, version,
                   time_created, time_updated
            FROM session
            ORDER BY time_updated DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        return [
            SessionRow(
                id=row["id"],
                project_id=row["project_id"],
                parent_id=row["parent_id"],
                slug=row["slug"],
                directory=row["directory"],
                title=row["title"],
                version=row["version"],
                time_created=row["time_created"],
                time_updated=row["time_updated"],
            )
            for row in rows
        ]

    def get_session_by_id(self, session_id: str) -> SessionRow | None:
        cursor = self._get_conn().cursor()
        cursor.execute(
            """
            SELECT id, project_id, parent_id, slug, directory, title, version,
                   time_created, time_updated
            FROM session
            WHERE id = ?
            """,
            (session_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return SessionRow(
            id=row["id"],
            project_id=row["project_id"],
            parent_id=row["parent_id"],
            slug=row["slug"],
            directory=row["directory"],
            title=row["title"],
            version=row["version"],
            time_created=row["time_created"],
            time_updated=row["time_updated"],
        )

    def get_messages_for_session(self, session_id: str) -> list[MessageRow]:
        cursor = self._get_conn().cursor()
        cursor.execute(
            """
            SELECT id, session_id, time_created, time_updated, data
            FROM message
            WHERE session_id = ?
            ORDER BY time_created ASC
            """,
            (session_id,),
        )
        rows = cursor.fetchall()
        return [
            MessageRow(
                id=row["id"],
                session_id=row["session_id"],
                time_created=row["time_created"],
                time_updated=row["time_updated"],
                data=json.loads(row["data"]),
            )
            for row in rows
        ]

    def get_parts_for_message(self, message_id: str) -> list[PartRow]:
        cursor = self._get_conn().cursor()
        cursor.execute(
            """
            SELECT id, message_id, session_id, time_created, time_updated, data
            FROM part
            WHERE message_id = ?
            ORDER BY time_created ASC
            """,
            (message_id,),
        )
        rows = cursor.fetchall()
        return [
            PartRow(
                id=row["id"],
                message_id=row["message_id"],
                session_id=row["session_id"],
                time_created=row["time_created"],
                time_updated=row["time_updated"],
                data=json.loads(row["data"]),
            )
            for row in rows
        ]

    def get_first_user_message(
        self, session_id: str, max_length: int = 200
    ) -> str | None:
        messages = self.get_messages_for_session(session_id)
        for msg in messages:
            if msg.data.get("role") == "user":
                parts = self.get_parts_for_message(msg.id)
                for part in parts:
                    if part.data.get("type") == "text":
                        text = part.data.get("text", "").strip()
                        if text:
                            return text[:max_length] if len(text) > max_length else text
        return None

    def get_session_model(self, session_id: str) -> str | None:
        """Get the model ID from the first assistant message in a session."""
        messages = self.get_messages_for_session(session_id)
        for msg in messages:
            if msg.data.get("role") == "assistant":
                model_id = msg.data.get("modelID")
                provider_id = msg.data.get("providerID")
                if model_id:
                    # Format as provider/model (e.g., "anthropic/claude-sonnet-4-5")
                    if provider_id:
                        return f"{provider_id}/{model_id}"
                    return model_id
        return None

    def has_messages(self, session_id: str) -> bool:
        cursor = self._get_conn().cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM message WHERE session_id = ?",
            (session_id,),
        )
        count = cursor.fetchone()[0]
        return count > 0


def db_exists() -> bool:
    return DEFAULT_DB_PATH.exists()


def get_session_metadata_from_db(session_id: str) -> tuple[str, str | None] | None:
    if not db_exists():
        return None
    with OpenCodeDB() as db:
        session = db.get_session_by_id(session_id)
        if session is None:
            return None
        directory = session.directory
        if directory and Path(directory).exists():
            return Path(directory).name, directory
        title = session.title
        if title:
            return title, directory or None
        return session.slug, directory or None
