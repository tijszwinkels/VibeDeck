"""Isolation backend implementation.

Provides the main backend class that implements CodingToolBackend protocol
for multi-user isolated sessions. Delegates to claude-code backend for
format-related operations (tailer, renderer, pricing) and only overrides
discovery (per-user directories) and CLI interaction (docker exec).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..protocol import (
    CommandSpec,
    MessageRendererProtocol,
    SessionMetadata,
    SessionTailerProtocol,
    TokenUsage,
)
from ..claude_code.tailer import ClaudeCodeTailer, has_messages, get_first_user_message
from ..claude_code.discovery import (
    get_session_name,
    get_session_id,
    should_watch_file,
    is_subagent_session,
    get_parent_session_id,
    is_summary_file,
    get_session_id_from_summary_file,
)
from ..claude_code.pricing import get_session_token_usage, get_session_model
from ..claude_code.renderer import ClaudeCodeRenderer
from .containers import ContainerManager, load_env_file
from .discovery import (
    find_sessions_for_all_users,
    find_sessions_for_user,
    get_session_owner,
)

logger = logging.getLogger(__name__)


class IsolationBackend:
    """Backend for multi-user isolated Claude Code sessions in Docker/gVisor.

    Discovers sessions under {users_dir}/{user_id}/.claude/projects/ and
    wraps CLI commands in docker exec. Reuses ClaudeCodeTailer and
    ClaudeCodeRenderer since the JSONL format is identical.
    """

    def __init__(
        self,
        users_dir: str,
        docker_image: str = "claude-sandbox",
        docker_runtime: str = "runsc",
        memory: str = "2g",
        cpus: str = "1",
        env_file: str | None = None,
    ):
        self._users_dir = Path(users_dir)
        self._renderer = ClaudeCodeRenderer()

        env_vars = load_env_file(Path(env_file)) if env_file else {}
        self._container_manager = ContainerManager(
            image=docker_image,
            runtime=docker_runtime,
            memory=memory,
            cpus=cpus,
            users_dir=self._users_dir,
            env_vars=env_vars,
        )

    # ===== Backend Identity =====

    @property
    def name(self) -> str:
        return "Isolation"

    @property
    def cli_command(self) -> str | None:
        return "docker"

    # ===== Session Discovery =====

    def find_recent_sessions(
        self, limit: int = 10, include_subagents: bool = True
    ) -> list[Path]:
        return find_sessions_for_all_users(
            self._users_dir, limit=limit, include_subagents=include_subagents
        )

    def get_projects_dir(self) -> Path:
        """Returns the users_dir as the base directory for watching."""
        return self._users_dir

    # ===== Session Metadata =====

    def get_session_metadata(self, session_path: Path) -> SessionMetadata:
        project_name, project_path = get_session_name(session_path)
        session_id = get_session_id(session_path)
        first_message = get_first_user_message(session_path)
        tailer = ClaudeCodeTailer(session_path)
        started_at = tailer.get_first_timestamp()

        is_subagent = is_subagent_session(session_path)
        parent_session_id = get_parent_session_id(session_path) if is_subagent else None
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
        return get_session_id(session_path)

    def has_messages(self, session_path: Path) -> bool:
        return has_messages(session_path)

    # ===== Session Reading =====

    def create_tailer(self, session_path: Path) -> SessionTailerProtocol:
        return ClaudeCodeTailer(session_path)

    # ===== Token Usage & Pricing =====

    def get_session_token_usage(self, session_path: Path) -> TokenUsage:
        return get_session_token_usage(session_path)

    def get_session_model(self, session_path: Path) -> str | None:
        return get_session_model(session_path)

    # ===== CLI Interaction =====

    def supports_send_message(self) -> bool:
        return True

    def supports_fork_session(self) -> bool:
        return False  # Fork doesn't make sense across Docker boundaries

    def supports_permission_detection(self) -> bool:
        return False  # gVisor is the security boundary

    def is_cli_available(self) -> bool:
        return ContainerManager.is_docker_available()

    def get_cli_install_instructions(self) -> str:
        return "Install Docker: https://docs.docker.com/get-docker/"

    def build_send_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        """Build docker exec command to send a message.

        Requires knowing which user owns the session. The session_id is looked
        up against known sessions to find the owner. Falls back to searching
        all user directories.
        """
        # We need to figure out the user_id from the session_id.
        # This is called from routes that have the session info with path,
        # so we derive user_id from the session path stored in session info.
        # The caller (server) should pass additional context, but for now
        # we build a generic command that requires user_id to be set externally.
        #
        # For the isolation backend, the actual command building happens in
        # build_send_command_for_user() which the routes call directly.
        raise NotImplementedError(
            "Use build_send_command_for_user() with explicit user_id"
        )

    def build_send_command_for_user(
        self,
        user_id: str,
        session_id: str,
        message: str,
    ) -> CommandSpec:
        """Build docker exec command to send a message to a user's session.

        Args:
            user_id: Owner of the session.
            session_id: Session to send to.
            message: Message text.

        Returns:
            CommandSpec with docker exec args and message as stdin.
        """
        args = self._container_manager.build_exec_command(
            user_id,
            ["-p", "--resume", session_id],
            interactive=True,
        )
        return CommandSpec(args=args, stdin=message)

    def build_fork_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        raise NotImplementedError("Fork not supported for isolation backend")

    def build_new_session_command(
        self,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        """Build command for new session — requires user context.

        For isolation backend, new sessions must specify a user. Use
        build_new_session_command_for_user() instead.
        """
        raise NotImplementedError(
            "Use build_new_session_command_for_user() with explicit user_id"
        )

    def build_new_session_command_for_user(
        self,
        user_id: str,
        message: str,
    ) -> CommandSpec:
        """Build docker exec command to start a new session for a user.

        Args:
            user_id: User to create session for.
            message: Initial message.

        Returns:
            CommandSpec with docker exec args and message as stdin.
        """
        args = self._container_manager.build_exec_command(
            user_id,
            ["-p"],
            interactive=True,
        )
        return CommandSpec(args=args, stdin=message)

    def ensure_session_indexed(self, session_id: str) -> None:
        """No-op for isolation backend (sessions are inside containers)."""
        pass

    # ===== Rendering =====

    def get_message_renderer(self) -> MessageRendererProtocol:
        return self._renderer

    # ===== File Watching Helpers =====

    def should_watch_file(self, path: Path, include_subagents: bool = True) -> bool:
        return should_watch_file(path, include_subagents=include_subagents)

    def get_session_id_from_changed_file(self, path: Path) -> str | None:
        if is_summary_file(path):
            return get_session_id_from_summary_file(path)
        return get_session_id(path)

    def is_summary_file(self, path: Path) -> bool:
        return is_summary_file(path)

    # ===== Isolation-specific =====

    @property
    def users_dir(self) -> Path:
        return self._users_dir

    @property
    def container_manager(self) -> ContainerManager:
        return self._container_manager

    def get_session_owner(self, session_path: Path) -> str | None:
        """Get the user_id that owns a session."""
        return get_session_owner(session_path, self._users_dir)
