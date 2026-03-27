"""Pi Coding Agent backend implementation.

Backend for pi-coding-agent sessions. Supports session discovery, reading,
and CLI interaction (send, fork, new session) with model selection.
"""

from __future__ import annotations

from pathlib import Path

from ..protocol import (
    CommandSpec,
    SessionMetadata,
    SessionTailerProtocol,
    MessageRendererProtocol,
    TokenUsage,
)
from .discovery import (
    DEFAULT_SESSIONS_DIR,
    find_recent_sessions,
    get_project_name,
    get_session_id,
    get_session_header,
    get_session_id_from_header,
    has_messages,
    get_first_user_message,
    should_watch_file,
    get_session_id_from_changed_file,
)
from .cli import (
    is_cli_available,
    build_send_command,
    build_fork_command,
    build_new_session_command,
    get_available_models,
    CLI_INSTALL_INSTRUCTIONS,
)
from .tailer import PiTailer
from .pricing import get_session_token_usage, get_session_model
from .renderer import PiRenderer


class PiBackend:
    """Backend implementation for Pi Coding Agent.

    Discovers and displays pi sessions stored as JSONL files in
    ~/.pi/agent/sessions/. Supports creating new sessions, sending
    messages, and forking via the Pi CLI.
    """

    def __init__(self, sessions_dir: Path | None = None):
        self._sessions_dir = sessions_dir or DEFAULT_SESSIONS_DIR
        self._renderer = PiRenderer()

    # ===== Backend Identity =====

    @property
    def name(self) -> str:
        return "Pi"

    @property
    def normalizer_key(self) -> str:
        return "pi"

    @property
    def cli_command(self) -> str | None:
        return "pi"

    # ===== Session Discovery =====

    def find_recent_sessions(
        self, limit: int = 10, include_subagents: bool = True
    ) -> list[Path]:
        return find_recent_sessions(self._sessions_dir, limit=limit)

    def get_projects_dir(self) -> Path:
        return self._sessions_dir

    # ===== Session Metadata =====

    def get_session_metadata(self, session_path: Path) -> SessionMetadata:
        project_name, project_path = get_project_name(session_path)
        session_id = get_session_id(session_path)
        first_message = get_first_user_message(session_path)

        # Try header for better metadata
        header = get_session_header(session_path)
        started_at = None
        if header:
            started_at = header.get("timestamp")
            # Prefer cwd from header over decoded dirname
            if header.get("cwd"):
                project_path = header["cwd"]
                project_name = project_path.rstrip("/").rsplit("/", 1)[-1]

        tailer = PiTailer(session_path)
        if not started_at:
            started_at = tailer.get_first_timestamp()

        return SessionMetadata(
            session_id=session_id,
            project_name=project_name,
            project_path=project_path,
            first_message=first_message,
            started_at=started_at,
            backend_data={"file_path": str(session_path)},
        )

    def get_session_id(self, session_path: Path) -> str:
        return get_session_id(session_path)

    def has_messages(self, session_path: Path) -> bool:
        return has_messages(session_path)

    # ===== Session Reading =====

    def create_tailer(self, session_path: Path) -> SessionTailerProtocol:
        return PiTailer(session_path)

    # ===== Token Usage & Pricing =====

    def get_session_token_usage(self, session_path: Path) -> TokenUsage:
        return get_session_token_usage(session_path)

    def get_session_model(self, session_path: Path) -> str | None:
        return get_session_model(session_path)

    def get_context_limit_tokens(self, session_path: Path) -> int | None:
        return None  # Pi doesn't expose context limits

    # ===== Model Selection =====

    def get_models(self) -> list[str]:
        """Get available models by querying the Pi CLI.

        Returns models in "provider/model" format (e.g.,
        "google-gemini-cli/gemini-2.5-pro", "openai/gpt-5.4").
        """
        return get_available_models()

    # ===== CLI Interaction =====

    def supports_send_message(self) -> bool:
        return True

    def supports_fork_session(self) -> bool:
        return True

    def supports_permission_detection(self) -> bool:
        return False

    def is_cli_available(self) -> bool:
        return is_cli_available()

    def get_cli_install_instructions(self) -> str:
        return CLI_INSTALL_INSTRUCTIONS

    def build_send_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        model: str | None = None,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        return build_send_command(
            session_id, message, skip_permissions,
            model=model, sessions_dir=self._sessions_dir,
        )

    def build_fork_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        model: str | None = None,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        return build_fork_command(
            session_id, message, skip_permissions, model=model,
        )

    def build_new_session_command(
        self,
        message: str,
        skip_permissions: bool = False,
        model: str | None = None,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        return build_new_session_command(
            message, skip_permissions, model=model,
        )

    def ensure_session_indexed(self, session_id: str) -> None:
        pass  # Not needed for Pi

    # ===== Rendering =====

    def get_message_renderer(self) -> MessageRendererProtocol:
        return self._renderer

    # ===== File Watching =====

    def should_watch_file(self, path: Path) -> bool:
        return should_watch_file(path)

    def get_session_id_from_changed_file(self, path: Path) -> str | None:
        return get_session_id_from_changed_file(path)
