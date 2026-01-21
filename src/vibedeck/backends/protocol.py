"""Protocol definitions for coding tool backends.

This module defines the interfaces that all coding tool backends must implement.
Using Python's Protocol for structural subtyping allows backends to be swapped
without requiring explicit inheritance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Callable, Any, runtime_checkable


@dataclass
class SessionMetadata:
    """Metadata about a coding session."""

    session_id: str
    """Unique identifier for the session."""

    project_name: str
    """Human-readable project name."""

    project_path: str | None
    """Filesystem path to the project directory."""

    first_message: str | None
    """Preview text of first user message."""

    started_at: str | None
    """ISO timestamp of session start."""

    backend_data: dict = field(default_factory=dict)
    """Backend-specific metadata (opaque to the explorer)."""

    is_subagent: bool = False
    """Whether this is a subagent session."""

    parent_session_id: str | None = None
    """Parent session ID if this is a subagent session."""


@dataclass
class MessageEntry:
    """A single message in a session.

    This is a normalized representation of a message that backends produce.
    The actual message data structure may vary between backends.
    """

    entry_type: str
    """Message type: 'user', 'assistant', etc."""

    timestamp: str
    """ISO timestamp of the message."""

    message_data: dict
    """Message content and metadata (backend-specific structure)."""

    request_id: str | None = None
    """Request ID for deduplication (optional)."""


@dataclass
class TokenUsage:
    """Token usage and cost statistics for a session."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    message_count: int = 0
    cost: float = 0.0  # USD
    models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "message_count": self.message_count,
            "cost": self.cost,
            "models": self.models,
        }


@dataclass
class SendMessageResult:
    """Result of sending a message to a session."""

    status: str  # 'sent', 'queued', 'forking', 'started', 'error'
    session_id: str | None = None
    queue_position: int | None = None
    error_message: str | None = None
    cwd: str | None = None


@runtime_checkable
class SessionTailerProtocol(Protocol):
    """Protocol for reading messages from a session file/store.

    Tailers maintain position state for incremental reading and track
    whether the session is waiting for user input.
    """

    @property
    def waiting_for_input(self) -> bool:
        """Whether the session is waiting for user input."""
        ...

    def read_new_lines(self) -> list[dict]:
        """Read new messages since last call.

        Returns:
            List of raw message entries (backend-specific format).
        """
        ...

    def read_all(self) -> list[dict]:
        """Read all messages from the beginning.

        Does NOT modify the current position - reads a fresh copy.

        Returns:
            List of all raw message entries.
        """
        ...

    def get_first_timestamp(self) -> str | None:
        """Get the timestamp of the first message."""
        ...

    def get_last_message_timestamp(self) -> float | None:
        """Get the timestamp of the last actual message (excluding summary entries).

        Returns:
            Unix timestamp (seconds since epoch) of the last user/assistant message,
            or None if no messages found.
        """
        ...


@runtime_checkable
class MessageRendererProtocol(Protocol):
    """Protocol for rendering messages to HTML.

    Renderers convert backend-specific message formats to HTML for display.
    """

    def render_message(self, entry: dict) -> str:
        """Render a message entry to HTML.

        Args:
            entry: Raw message entry in backend-specific format.

        Returns:
            HTML string for the message.
        """
        ...


@runtime_checkable
class CodingToolBackend(Protocol):
    """Protocol that all coding tool backends must implement.

    This is the main interface for interacting with a coding tool's session
    storage and CLI. Backends implement this protocol to integrate with
    the session explorer.
    """

    # ===== Backend Identity =====

    @property
    def name(self) -> str:
        """Human-readable name of the backend (e.g., 'Claude Code')."""
        ...

    @property
    def cli_command(self) -> str | None:
        """CLI command name if available (e.g., 'claude'), or None."""
        ...

    # ===== Session Discovery =====

    def find_recent_sessions(
        self, limit: int = 10, include_subagents: bool = True
    ) -> list[Path]:
        """Find recently modified sessions.

        Args:
            limit: Maximum number of sessions to return.
            include_subagents: Whether to include subagent sessions (Claude Code only).

        Returns:
            List of paths to session files, sorted by modification time (newest first).
        """
        ...

    def get_projects_dir(self) -> Path:
        """Get the base directory where sessions are stored."""
        ...

    # ===== Session Metadata =====

    def get_session_metadata(self, session_path: Path) -> SessionMetadata:
        """Extract metadata from a session.

        Args:
            session_path: Session identifier.

        Returns:
            Session metadata.
        """
        ...

    def get_session_id(self, session_path: Path) -> str:
        """Get the unique session ID from a path.

        Args:
            session_path: Session identifier.

        Returns:
            Session ID string.
        """
        ...

    def has_messages(self, session_path: Path) -> bool:
        """Check if a session has any user or assistant messages.

        Args:
            session_path: Session identifier.

        Returns:
            True if session has messages.
        """
        ...

    # ===== Session Reading =====

    def create_tailer(self, session_path: Path) -> SessionTailerProtocol:
        """Create a tailer for reading session messages.

        Args:
            session_path: Session identifier.

        Returns:
            A tailer that implements SessionTailerProtocol.
        """
        ...

    # ===== Token Usage & Pricing =====

    def get_session_token_usage(self, session_path: Path) -> TokenUsage:
        """Calculate total token usage and cost.

        Args:
            session_path: Session identifier.

        Returns:
            Token usage statistics.
        """
        ...

    def get_session_model(self, session_path: Path) -> str | None:
        """Get the primary model used in a session.

        Returns the model from the first assistant message, which is used for
        determining warm cache optimization when summarizing.

        Args:
            session_path: Session identifier.

        Returns:
            Model ID string (e.g., 'claude-opus-4-5-20251101') or None if
            not found or not implemented for this backend.
        """
        ...

    # ===== CLI Interaction (Optional) =====

    def supports_send_message(self) -> bool:
        """Whether this backend supports sending messages."""
        ...

    def supports_fork_session(self) -> bool:
        """Whether this backend supports forking sessions."""
        ...

    def supports_permission_detection(self) -> bool:
        """Whether this backend supports permission denial detection.

        When True, the backend can capture CLI output in JSON format and
        parse permission denials for interactive handling.
        """
        ...

    def is_cli_available(self) -> bool:
        """Check if the CLI tool is installed and available."""
        ...

    def get_cli_install_instructions(self) -> str:
        """Get instructions for installing the CLI tool."""
        ...

    def build_send_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> list[str]:
        """Build the CLI command to send a message.

        Args:
            session_id: Session to send to.
            message: Message text.
            skip_permissions: Skip permission prompts if supported.
            output_format: Output format (e.g., "stream-json" for permission detection).
            add_dirs: Additional directories to allow access to.

        Returns:
            Command arguments list.
        """
        ...

    def build_fork_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> list[str]:
        """Build the CLI command to fork a session.

        Args:
            session_id: Session to fork from.
            message: Initial message for forked session.
            skip_permissions: Skip permission prompts if supported.
            output_format: Output format (e.g., "stream-json" for permission detection).
            add_dirs: Additional directories to allow access to.

        Returns:
            Command arguments list.
        """
        ...

    def build_new_session_command(
        self,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> list[str]:
        """Build the CLI command to start a new session.

        Args:
            message: Initial message.
            skip_permissions: Skip permission prompts if supported.
            output_format: Output format (e.g., "stream-json" for permission detection).
            add_dirs: Additional directories to allow access to.

        Returns:
            Command arguments list.
        """
        ...

    def ensure_session_indexed(self, session_id: str) -> None:
        """Ensure a session is indexed/known to the CLI tool.

        Some backends need sessions to be registered before --resume works.

        Args:
            session_id: Session to index.
        """
        ...

    # ===== Rendering =====

    def get_message_renderer(self) -> MessageRendererProtocol:
        """Get the message renderer for this backend.

        Returns:
            A renderer that can convert backend-specific messages to HTML.
        """
        ...

    # ===== File Watching Helpers =====

    def should_watch_file(self, path: Path) -> bool:
        """Check if a file should be watched for changes.

        Args:
            path: File path to check.

        Returns:
            True if the file should be watched.
        """
        ...

    def get_session_id_from_changed_file(self, path: Path) -> str | None:
        """Get the session ID from a changed file path.

        For some backends (like OpenCode), the changed file may be a message
        or part file, not the session file itself. This method extracts the
        session ID from such paths.

        For backends where watched files are session files (like Claude Code),
        this is equivalent to get_session_id().

        Args:
            path: Path to the changed file.

        Returns:
            Session ID, or None if it cannot be determined.
        """
        ...
