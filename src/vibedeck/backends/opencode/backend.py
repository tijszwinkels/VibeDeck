"""OpenCode backend implementation.

This module provides the main backend class that implements the
CodingToolBackend protocol for OpenCode sessions.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from ..protocol import (
    CommandSpec,
    SessionMetadata,
    SessionTailerProtocol,
    MessageRendererProtocol,
    TokenUsage,
)
from .tailer import OpenCodeTailer
from .db import OpenCodeDB
from .discovery import (
    get_session_metadata,
    get_session_id_from_path,
    is_db_session,
    find_recent_sessions,
    should_watch_file,
    has_messages_db,
    has_messages,
    get_first_user_message,
    get_session_id_from_file_path,
    get_db_path,
    DEFAULT_STORAGE_DIR,
    DEFAULT_DB_PATH,
)
from .pricing import get_session_token_usage
from .cli import (
    CLI_COMMAND,
    CLI_INSTALL_INSTRUCTIONS,
    is_cli_available,
    ensure_session_indexed,
    build_send_command,
    build_fork_command,
    build_new_session_command,
    get_available_models,
)
from .renderer import OpenCodeRenderer


class OpenCodeBackend:
    """Backend implementation for OpenCode.

    Handles session discovery, file parsing, CLI interaction, and rendering
    for OpenCode sessions stored as hierarchical JSON files.
    """

    def __init__(self, storage_dir: Path | None = None):
        """Initialize the OpenCode backend.

        Args:
            storage_dir: Custom storage directory.
                Defaults to ~/.local/share/opencode/storage.
        """
        self._storage_dir = storage_dir or DEFAULT_STORAGE_DIR
        self._renderer = OpenCodeRenderer()

    # ===== Backend Identity =====

    @property
    def name(self) -> str:
        """Human-readable name of the backend."""
        return "OpenCode"

    @property
    def normalizer_key(self) -> str:
        return "opencode"

    @property
    def cli_command(self) -> str | None:
        """CLI command name."""
        return CLI_COMMAND

    # ===== Session Discovery =====

    def find_recent_sessions(
        self, limit: int = 10, include_subagents: bool = True
    ) -> list[Path]:
        """Find recently modified sessions.

        Args:
            limit: Maximum number of sessions to return.
            include_subagents: Not used for OpenCode (no subagent concept).

        Returns:
            List of paths to recent session files.
        """
        # OpenCode doesn't have subagents, so ignore include_subagents
        return find_recent_sessions(self._storage_dir, limit=limit)

    def get_projects_dir(self) -> Path:
        """Get the base directory where sessions are stored."""
        return self._storage_dir

    def get_db_path(self) -> Path | None:
        """Get the path to the SQLite database if using SQLite storage.

        Returns:
            Path to opencode.db if it exists, None otherwise.
        """
        if DEFAULT_DB_PATH.exists():
            return DEFAULT_DB_PATH
        return None

    # ===== Session Metadata =====

    def get_session_metadata(self, session_path: Path) -> SessionMetadata:
        project_name, project_path = get_session_metadata(
            session_path, self._storage_dir
        )
        session_id = get_session_id_from_path(session_path)
        first_message = get_first_user_message(
            session_path, self._storage_dir, session_id=session_id
        )

        tailer = OpenCodeTailer(self._storage_dir, session_id)
        started_at = tailer.get_first_timestamp()

        return SessionMetadata(
            session_id=session_id,
            project_name=project_name,
            project_path=project_path,
            first_message=first_message,
            started_at=started_at,
            backend_data={
                "file_path": str(session_path),
                "storage_dir": str(self._storage_dir),
            },
        )

    def get_session_id(self, session_path: Path) -> str:
        return get_session_id_from_path(session_path)

    def has_messages(self, session_path: Path) -> bool:
        session_id = get_session_id_from_path(session_path)
        if is_db_session(session_path):
            return has_messages_db(session_id)
        return has_messages(session_path, self._storage_dir)

    # ===== Session Reading =====

    def create_tailer(self, session_path: Path) -> SessionTailerProtocol:
        session_id = get_session_id_from_path(session_path)
        return OpenCodeTailer(self._storage_dir, session_id)

    # ===== Token Usage & Pricing =====

    def get_session_token_usage(self, session_path: Path) -> TokenUsage:
        """Calculate total token usage and cost.

        Args:
            session_path: Path to the session file.

        Returns:
            Token usage statistics.
        """
        return get_session_token_usage(session_path, self._storage_dir)

    def get_session_model(self, session_path: Path) -> str | None:
        """Get the primary model used in a session.

        Returns the model from the first assistant message, which is used for
        continuing the session with the same model.

        Args:
            session_path: Path to the session file (or session:xxx for SQLite).

        Returns:
            Model identifier (e.g., "anthropic/claude-sonnet-4-5") or None.
        """
        session_id = get_session_id_from_path(session_path)

        if is_db_session(session_path):
            try:
                with OpenCodeDB() as db:
                    return db.get_session_model(session_id)
            except Exception as e:
                logger.debug(f"Failed to get model from database: {e}")
                return None

        # Legacy JSON format - not implemented
        return None

    # ===== CLI Interaction =====

    def supports_send_message(self) -> bool:
        """Whether this backend supports sending messages."""
        return True

    def supports_fork_session(self) -> bool:
        """Whether this backend supports forking sessions.

        OpenCode does not support forking via CLI - it requires the SDK/server.
        """
        return False

    def supports_permission_detection(self) -> bool:
        """Whether this backend supports permission denial detection.

        OpenCode does not support permission detection - permissions are
        configured via the OpenCode config file.
        """
        return False

    def supports_summarization(self) -> bool:
        """Whether this backend supports session summarization.

        OpenCode sessions should not be summarized as it clutters the session.
        """
        return False

    def is_cli_available(self) -> bool:
        """Check if the CLI tool is installed and available."""
        return is_cli_available()

    def get_cli_install_instructions(self) -> str:
        """Get instructions for installing the CLI tool."""
        return CLI_INSTALL_INSTRUCTIONS

    def build_send_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        """Build the CLI command to send a message.

        Args:
            session_id: Session to send to.
            message: Message text.
            skip_permissions: Ignored for OpenCode.
            output_format: Ignored for OpenCode.
            add_dirs: Ignored for OpenCode.

        Returns:
            CommandSpec with args and stdin content.
        """
        return build_send_command(session_id, message, skip_permissions)

    def build_fork_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        """Build the CLI command to fork a session.

        OpenCode does not support forking via CLI.

        Raises:
            NotImplementedError: Always.
        """
        return build_fork_command(session_id, message, skip_permissions)

    def build_new_session_command(
        self,
        message: str,
        skip_permissions: bool = False,
        model: str | None = None,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        """Build the CLI command to start a new session.

        Args:
            message: Initial message.
            skip_permissions: Ignored for OpenCode.
            model: Model to use (e.g., "anthropic/claude-sonnet-4-5"). Optional.
            output_format: Ignored for OpenCode.
            add_dirs: Ignored for OpenCode.

        Returns:
            CommandSpec with args and stdin content.
        """
        return build_new_session_command(message, skip_permissions, model)

    def get_models(self) -> list[str]:
        """Get available models for this backend.

        Returns:
            List of model identifiers.
        """
        return get_available_models()

    def ensure_session_indexed(self, session_id: str) -> None:
        """Ensure a session is indexed/known to the CLI tool.

        OpenCode doesn't require separate indexing.

        Args:
            session_id: Session to index (no-op).
        """
        ensure_session_indexed(session_id)

    # ===== Rendering =====

    def get_message_renderer(self) -> MessageRendererProtocol:
        """Get the message renderer for this backend.

        Returns:
            An OpenCodeRenderer instance.
        """
        return self._renderer

    # ===== File Watching Helpers =====

    def should_watch_file(self, path: Path) -> bool:
        """Check if a file should be watched for changes.

        For OpenCode, we watch message and part JSON files (legacy) and the SQLite database.

        Args:
            path: File path to check.

        Returns:
            True if the file should be watched.
        """
        return should_watch_file(path)

    def is_db_file(self, path: Path) -> bool:
        """Check if this is the SQLite database file.

        Args:
            path: File path to check.

        Returns:
            True if this is the opencode.db file.
        """
        str_path = str(path)
        return str_path.endswith("opencode.db") or str_path.endswith("opencode.db-wal")

    def get_updated_sessions(
        self, tracked_session_ids: list[str], last_check_time: float
    ) -> list[str]:
        """Get session IDs that have been updated since the last check.

        This is used for SQLite-based sessions where we can't easily determine
        which session changed from a file watch event.

        Args:
            tracked_session_ids: List of session IDs currently being tracked.
            last_check_time: Unix timestamp (seconds) of the last check.

        Returns:
            List of session IDs that have new messages since last_check_time.
        """
        if not DEFAULT_DB_PATH.exists():
            return []

        try:
            with OpenCodeDB() as db:
                cursor = db._get_conn().cursor()
                last_check_ms = int(last_check_time * 1000)
                placeholders = ",".join("?" * len(tracked_session_ids))
                cursor.execute(
                    f"""
                    SELECT DISTINCT session_id FROM message
                    WHERE session_id IN ({placeholders})
                    AND time_updated > ?
                    """,
                    tracked_session_ids + [last_check_ms],
                )
                result = [row[0] for row in cursor.fetchall()]
                logger.debug(
                    f"get_updated_sessions: {len(result)} updated sessions since {last_check_time}"
                )
                return result
        except Exception as e:
            logger.warning(f"Failed to check for updated sessions: {e}")
            return []

    def get_session_id_from_changed_file(self, path: Path) -> str | None:
        """Get session ID from a changed message or part file.

        This is used to determine which session a file change belongs to.

        Args:
            path: Path to the changed file.

        Returns:
            Session ID, or None if it cannot be determined.
        """
        return get_session_id_from_file_path(path, self._storage_dir)
