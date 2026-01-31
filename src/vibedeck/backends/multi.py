"""Multi-backend wrapper for aggregating sessions from multiple backends.

This module provides a wrapper that combines multiple coding tool backends,
allowing the session explorer to show sessions from all backends simultaneously.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import (
        CodingToolBackend,
        CommandSpec,
        MessageRendererProtocol,
        SessionMetadata,
        SessionTailerProtocol,
        TokenUsage,
    )

logger = logging.getLogger(__name__)


class MultiBackend:
    """Wrapper that aggregates sessions from multiple backends.

    This class implements the CodingToolBackend protocol by delegating to
    multiple underlying backends. Session discovery returns sessions from
    all backends, and operations on specific sessions are delegated to the
    appropriate backend based on which backend owns the session.

    For CLI operations (send, fork, new session), a specific backend must be
    specified since these are backend-specific operations.
    """

    def __init__(self, backends: list["CodingToolBackend"]):
        """Initialize the multi-backend wrapper.

        Args:
            backends: List of backend instances to aggregate.

        Raises:
            ValueError: If no backends are provided.
        """
        if not backends:
            raise ValueError("At least one backend is required")

        self._backends = backends
        self._backend_by_name: dict[str, "CodingToolBackend"] = {}

        for backend in backends:
            # Use a normalized name as key
            name = self._normalize_name(backend.name)
            self._backend_by_name[name] = backend

        # Map session paths to their owning backend
        self._session_backend: dict[Path, "CodingToolBackend"] = {}

    def _normalize_name(self, name: str) -> str:
        """Normalize backend name for consistent lookup."""
        return name.lower().replace(" ", "-")

    # ===== Backend Identity =====

    @property
    def name(self) -> str:
        """Human-readable name of the backend."""
        return "Multi-Backend"

    @property
    def cli_command(self) -> str | None:
        """CLI command name - not applicable for multi-backend."""
        return None

    # ===== Backend Access =====

    def get_backends(self) -> list["CodingToolBackend"]:
        """Get all underlying backends."""
        return list(self._backends)

    def get_backend_by_name(self, name: str) -> "CodingToolBackend | None":
        """Get a specific backend by name.

        Args:
            name: Backend name (case-insensitive, spaces converted to dashes).

        Returns:
            The backend instance, or None if not found.
        """
        return self._backend_by_name.get(self._normalize_name(name))

    def get_backend_for_session(self, session_path: Path) -> "CodingToolBackend | None":
        """Get the backend that owns a specific session.

        Args:
            session_path: Path to the session file.

        Returns:
            The owning backend, or None if not tracked.
        """
        return self._session_backend.get(session_path)

    def get_backend_name_for_session(self, session_path: Path) -> str | None:
        """Get the backend name for a session.

        Args:
            session_path: Path to the session file.

        Returns:
            Backend name, or None if not tracked.
        """
        backend = self._session_backend.get(session_path)
        return backend.name if backend else None

    # ===== Session Discovery =====

    def find_recent_sessions(
        self, limit: int = 10, include_subagents: bool = True
    ) -> list[Path]:
        """Find recently active sessions from all backends.

        Args:
            limit: Maximum number of sessions to return per backend.
            include_subagents: Whether to include subagent sessions.

        Returns:
            List of paths to session files, sorted by last message timestamp.
        """
        all_sessions: list[tuple[Path, float]] = []

        for backend in self._backends:
            try:
                sessions = backend.find_recent_sessions(
                    limit=limit, include_subagents=include_subagents
                )
                for path in sessions:
                    self._session_backend[path] = backend
                    # Get actual message timestamp from tailer
                    try:
                        tailer = backend.create_tailer(path)
                        timestamp = tailer.get_last_message_timestamp()
                        if timestamp is None:
                            timestamp = 0
                    except (OSError, AttributeError):
                        timestamp = 0
                    all_sessions.append((path, timestamp))
            except Exception as e:
                logger.warning(f"Failed to get sessions from {backend.name}: {e}")

        # Sort by message timestamp, newest first
        all_sessions.sort(key=lambda x: x[1], reverse=True)

        # Return limited list
        return [path for path, _ in all_sessions[:limit]]

    def get_projects_dir(self) -> Path:
        """Get base directory - returns first backend's directory.

        For multi-backend, this is only used for logging. The actual watching
        is done per-backend.
        """
        if self._backends:
            return self._backends[0].get_projects_dir()
        return Path.home()

    def get_all_project_dirs(self) -> list[Path]:
        """Get all project directories to watch.

        Returns:
            List of directories from all backends.
        """
        dirs = []
        for backend in self._backends:
            d = backend.get_projects_dir()
            if d not in dirs:
                dirs.append(d)
        return dirs

    # ===== Session Metadata =====

    def get_session_metadata(self, session_path: Path) -> "SessionMetadata":
        """Extract metadata from a session.

        Delegates to the appropriate backend.
        """
        backend = self._session_backend.get(session_path)
        if backend is None:
            # Try to find the backend
            for b in self._backends:
                try:
                    # Check if this backend can read the session
                    metadata = b.get_session_metadata(session_path)
                    self._session_backend[session_path] = b
                    return metadata
                except Exception:
                    continue
            raise ValueError(f"No backend found for session: {session_path}")
        return backend.get_session_metadata(session_path)

    def get_session_id(self, session_path: Path) -> str:
        """Get the unique session ID from a path."""
        backend = self._session_backend.get(session_path)
        if backend is None:
            for b in self._backends:
                try:
                    return b.get_session_id(session_path)
                except Exception:
                    continue
            # Fallback to filename
            return session_path.stem
        return backend.get_session_id(session_path)

    def has_messages(self, session_path: Path) -> bool:
        """Check if a session has any messages."""
        backend = self._session_backend.get(session_path)
        if backend is None:
            for b in self._backends:
                try:
                    return b.has_messages(session_path)
                except Exception:
                    continue
            return False
        return backend.has_messages(session_path)

    # ===== Session Reading =====

    def create_tailer(self, session_path: Path) -> "SessionTailerProtocol":
        """Create a tailer for reading session messages."""
        backend = self._session_backend.get(session_path)
        if backend is None:
            raise ValueError(f"No backend found for session: {session_path}")
        return backend.create_tailer(session_path)

    # ===== Token Usage & Pricing =====

    def get_session_token_usage(self, session_path: Path) -> "TokenUsage":
        """Calculate total token usage and cost."""
        backend = self._session_backend.get(session_path)
        if backend is None:
            from .protocol import TokenUsage

            return TokenUsage()
        return backend.get_session_token_usage(session_path)

    def get_session_model(self, session_path: Path) -> str | None:
        """Get the primary model used in a session.

        Delegates to the appropriate backend.
        """
        backend = self._session_backend.get(session_path)
        if backend is None:
            return None
        return backend.get_session_model(session_path)

    # ===== CLI Interaction =====

    def supports_send_message(self) -> bool:
        """Whether any backend supports sending messages."""
        return any(b.supports_send_message() for b in self._backends)

    def supports_fork_session(self) -> bool:
        """Whether any backend supports forking sessions."""
        return any(b.supports_fork_session() for b in self._backends)

    def is_cli_available(self) -> bool:
        """Check if any CLI tool is installed."""
        return any(b.is_cli_available() for b in self._backends)

    def get_cli_install_instructions(self) -> str:
        """Get instructions for installing CLI tools."""
        instructions = []
        for b in self._backends:
            if not b.is_cli_available():
                instructions.append(f"{b.name}: {b.get_cli_install_instructions()}")
        return "\n".join(instructions) if instructions else "All CLI tools installed."

    def build_send_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
    ) -> "CommandSpec":
        """Build the CLI command to send a message.

        Raises:
            ValueError: Multi-backend cannot build send commands directly.
                       Use the specific backend's method.
        """
        raise NotImplementedError(
            "MultiBackend cannot build send commands. "
            "Use get_backend_for_session() to get the specific backend."
        )

    def build_fork_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
    ) -> "CommandSpec":
        """Build the CLI command to fork a session."""
        raise NotImplementedError(
            "MultiBackend cannot build fork commands. "
            "Use get_backend_for_session() to get the specific backend."
        )

    def build_new_session_command(
        self,
        message: str,
        skip_permissions: bool = False,
        model: str | None = None,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> "CommandSpec":
        """Build the CLI command to start a new session.

        For multi-backend, defaults to first backend with CLI available.

        Args:
            message: Initial message.
            skip_permissions: Whether to skip permission prompts.
            model: Model to use (e.g., "claude-sonnet-4"). Optional.
            output_format: Output format for the CLI (e.g., "stream-json").
            add_dirs: Additional directories to allow. Optional.

        Returns:
            CommandSpec with args and optional stdin.
        """
        for backend in self._backends:
            if backend.is_cli_available():
                return backend.build_new_session_command(
                    message,
                    skip_permissions,
                    model=model,
                    output_format=output_format,
                    add_dirs=add_dirs,
                )
        raise RuntimeError("No backend CLI available")

    def ensure_session_indexed(self, session_id: str) -> None:
        """Ensure a session is indexed.

        Delegates to the appropriate backend.
        """
        # Find session by ID to get its path
        for path, backend in self._session_backend.items():
            if backend.get_session_id(path) == session_id:
                backend.ensure_session_indexed(session_id)
                return

    # ===== Rendering =====

    def get_message_renderer(self) -> "MessageRendererProtocol":
        """Get message renderer.

        Returns the first backend's renderer. For multi-backend mode,
        the server should use get_renderer_for_session() instead.
        """
        if self._backends:
            return self._backends[0].get_message_renderer()
        raise RuntimeError("No backends available")

    def get_renderer_for_session(
        self, session_path: Path
    ) -> "MessageRendererProtocol | None":
        """Get the appropriate renderer for a session.

        Args:
            session_path: Path to the session file.

        Returns:
            The renderer for the session's backend, or None if not found.
        """
        backend = self._session_backend.get(session_path)
        if backend:
            return backend.get_message_renderer()
        return None

    # ===== File Watching Helpers =====

    def should_watch_file(self, path: Path) -> bool:
        """Check if a file should be watched for changes.

        Returns True if any backend wants to watch this file.
        """
        return any(b.should_watch_file(path) for b in self._backends)

    def is_summary_file(self, path: Path) -> bool:
        """Check if a file is a summary file.

        Returns True if any backend identifies this as a summary file.
        This is needed for the watch loop to distinguish summary file
        updates from regular session message updates.
        """
        for backend in self._backends:
            is_summary = getattr(backend, "is_summary_file", None)
            if is_summary and is_summary(path):
                return True
        return False

    def get_session_id_from_changed_file(self, path: Path) -> str | None:
        """Get the session ID from a changed file path.

        Tries each backend until one returns a session ID.
        """
        for backend in self._backends:
            if backend.should_watch_file(path):
                session_id = backend.get_session_id_from_changed_file(path)
                if session_id:
                    return session_id
        return None

    def get_backend_for_changed_file(self, path: Path) -> "CodingToolBackend | None":
        """Get the backend that should handle a changed file.

        Args:
            path: Path to the changed file.

        Returns:
            The backend that claims this file, or None.
        """
        for backend in self._backends:
            if backend.should_watch_file(path):
                return backend
        return None

    def register_session(self, path: Path, backend: "CodingToolBackend") -> None:
        """Register a session path with its owning backend.

        Call this when a new session is discovered from a file change event.
        """
        self._session_backend[path] = backend
