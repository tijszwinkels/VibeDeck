"""Claude Code backend implementation.

This module provides the main backend class that implements the
CodingToolBackend protocol for Claude Code.
"""

from __future__ import annotations

from pathlib import Path

from ..protocol import (
    CodingToolBackend,
    CommandSpec,
    SessionMetadata,
    SessionTailerProtocol,
    MessageRendererProtocol,
    TokenUsage,
)
from .tailer import ClaudeCodeTailer, has_messages, get_first_user_message
from .discovery import (
    get_session_name,
    get_session_id,
    find_recent_sessions,
    should_watch_file,
    is_subagent_session,
    get_parent_session_id,
    is_summary_file,
    get_session_id_from_summary_file,
    DEFAULT_PROJECTS_DIR,
)
from .pricing import get_session_token_usage, get_session_model
from .cli import (
    CLI_COMMAND,
    CLI_INSTALL_INSTRUCTIONS,
    is_cli_available,
    ensure_session_indexed,
    build_send_command,
    build_fork_command,
    build_new_session_command,
)
from .renderer import ClaudeCodeRenderer


class ClaudeCodeBackend:
    """Backend implementation for Claude Code.

    Handles session discovery, file parsing, CLI interaction, and rendering
    for Claude Code sessions stored as JSONL files.
    """

    def __init__(self, projects_dir: Path | None = None):
        """Initialize the Claude Code backend.

        Args:
            projects_dir: Custom projects directory. Defaults to ~/.claude/projects.
        """
        self._projects_dir = projects_dir or DEFAULT_PROJECTS_DIR
        self._renderer = ClaudeCodeRenderer()

    # ===== Backend Identity =====

    @property
    def name(self) -> str:
        """Human-readable name of the backend."""
        return "Claude Code"

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
            include_subagents: Whether to include subagent sessions.

        Returns:
            List of paths to recent session files.
        """
        return find_recent_sessions(
            self._projects_dir, limit=limit, include_subagents=include_subagents
        )

    def get_projects_dir(self) -> Path:
        """Get the base directory where sessions are stored."""
        return self._projects_dir

    # ===== Session Metadata =====

    def get_session_metadata(self, session_path: Path) -> SessionMetadata:
        """Extract metadata from a session.

        Args:
            session_path: Path to the session file.

        Returns:
            Session metadata.
        """
        project_name, project_path = get_session_name(session_path)
        session_id = get_session_id(session_path)
        first_message = get_first_user_message(session_path)

        # Get first timestamp from tailer
        tailer = ClaudeCodeTailer(session_path)
        started_at = tailer.get_first_timestamp()

        # Check if this is a subagent session
        is_subagent = is_subagent_session(session_path)
        parent_session_id = get_parent_session_id(session_path) if is_subagent else None

        # Prefix project name for subagents
        if is_subagent:
            project_name = f"[subagent] {project_name}"

        return SessionMetadata(
            session_id=session_id,
            project_name=project_name,
            project_path=project_path,
            first_message=first_message,
            started_at=started_at,
            backend_data={"file_path": str(session_path)},
            is_subagent=is_subagent,
            parent_session_id=parent_session_id,
        )

    def get_session_id(self, session_path: Path) -> str:
        """Get the unique session ID from a path.

        Args:
            session_path: Path to the session file.

        Returns:
            Session ID (filename without extension).
        """
        return get_session_id(session_path)

    def has_messages(self, session_path: Path) -> bool:
        """Check if a session has any user or assistant messages.

        Args:
            session_path: Path to the session file.

        Returns:
            True if session has messages.
        """
        return has_messages(session_path)

    # ===== Session Reading =====

    def create_tailer(self, session_path: Path) -> SessionTailerProtocol:
        """Create a tailer for reading session messages.

        Args:
            session_path: Path to the session file.

        Returns:
            A ClaudeCodeTailer instance.
        """
        return ClaudeCodeTailer(session_path)

    # ===== Token Usage & Pricing =====

    def get_session_token_usage(self, session_path: Path) -> TokenUsage:
        """Calculate total token usage and cost.

        Args:
            session_path: Path to the session file.

        Returns:
            Token usage statistics.
        """
        return get_session_token_usage(session_path)

    def get_session_model(self, session_path: Path) -> str | None:
        """Get the primary model used in a session.

        Args:
            session_path: Path to the session file.

        Returns:
            Model ID string or None if not found.
        """
        return get_session_model(session_path)

    # ===== Model Selection =====

    def get_models(self) -> list[str]:
        """Get available models for Claude Code.

        Returns a list of model aliases that can be passed to the --model flag.
        Includes both latest aliases and pinned version aliases for fallback.

        Returns:
            List of model identifier strings.
        """
        return [
            "opus",                 # Latest Opus (currently 4.6)
            "sonnet",               # Latest Sonnet (currently 4.5)
            "haiku",                # Latest Haiku (currently 4.5)
            "claude-opus-4-5",      # Pinned Opus 4.5
            "claude-sonnet-4-5",    # Pinned Sonnet 4.5
            "claude-haiku-4-5",     # Pinned Haiku 4.5
        ]

    # ===== CLI Interaction =====

    def supports_send_message(self) -> bool:
        """Whether this backend supports sending messages."""
        return True

    def supports_fork_session(self) -> bool:
        """Whether this backend supports forking sessions."""
        return True

    def supports_permission_detection(self) -> bool:
        """Whether this backend supports permission denial detection."""
        return True

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
            skip_permissions: Skip permission prompts.
            output_format: Output format (e.g., "stream-json" for permission detection).
            add_dirs: Additional directories to allow access to.

        Returns:
            CommandSpec with args and stdin content.
        """
        return build_send_command(session_id, message, skip_permissions, output_format, add_dirs)

    def build_fork_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        """Build the CLI command to fork a session.

        Args:
            session_id: Session to fork from.
            message: Initial message for forked session.
            skip_permissions: Skip permission prompts.
            output_format: Output format (e.g., "stream-json" for permission detection).
            add_dirs: Additional directories to allow access to.

        Returns:
            CommandSpec with args and stdin content.
        """
        return build_fork_command(session_id, message, skip_permissions, output_format, add_dirs)

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
            skip_permissions: Skip permission prompts.
            model: Model to use (e.g., "opus", "sonnet", "haiku").
            output_format: Output format (e.g., "stream-json" for permission detection).
            add_dirs: Additional directories to allow access to.

        Returns:
            CommandSpec with args and stdin content.
        """
        return build_new_session_command(message, skip_permissions, model=model, output_format=output_format, add_dirs=add_dirs)

    def ensure_session_indexed(self, session_id: str) -> None:
        """Ensure a session is indexed/known to the CLI tool.

        Args:
            session_id: Session to index.
        """
        ensure_session_indexed(session_id)

    # ===== Rendering =====

    def get_message_renderer(self) -> MessageRendererProtocol:
        """Get the message renderer for this backend.

        Returns:
            A ClaudeCodeRenderer instance.
        """
        return self._renderer

    # ===== File Watching Helpers =====

    def should_watch_file(self, path: Path, include_subagents: bool = True) -> bool:
        """Check if a file should be watched for changes.

        Args:
            path: File path to check.
            include_subagents: Whether to watch subagent session files.

        Returns:
            True if the file should be watched.
        """
        return should_watch_file(path, include_subagents=include_subagents)

    def get_session_id_from_changed_file(self, path: Path) -> str | None:
        """Get the session ID from a changed file path.

        For Claude Code, the watched files are either:
        - Session JSONL files: session ID is filename without extension
        - Summary JSON files: session ID is extracted from <session_id>_summary.json

        Args:
            path: Path to the changed file.

        Returns:
            Session ID, or None if file type not recognized.
        """
        if is_summary_file(path):
            return get_session_id_from_summary_file(path)
        return get_session_id(path)

    def is_summary_file(self, path: Path) -> bool:
        """Check if a path is a summary file.

        Args:
            path: Path to check.

        Returns:
            True if this is a summary file.
        """
        return is_summary_file(path)
