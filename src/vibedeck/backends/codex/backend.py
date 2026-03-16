"""Codex backend implementation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..protocol import (
    CommandSpec,
    MessageRendererProtocol,
    SessionMetadata,
    SessionTailerProtocol,
    TokenUsage,
)
from .cli import (
    CLI_COMMAND,
    CLI_INSTALL_INSTRUCTIONS,
    build_fork_command,
    build_new_session_command,
    build_send_command,
    ensure_session_indexed,
    is_cli_available,
)
from .discovery import (
    DEFAULT_HISTORY_PATH,
    DEFAULT_SESSIONS_DIR,
    find_recent_sessions,
    get_first_user_message,
    has_messages,
    get_session_id,
    get_session_name,
    should_watch_file,
)
from .pricing import get_session_model, get_session_token_usage
from .renderer import CodexRenderer
from .tailer import CodexTailer

logger = logging.getLogger(__name__)


class CodexBackend:
    """Backend implementation for Codex rollout sessions."""

    def __init__(
        self,
        sessions_dir: Path | None = None,
        history_path: Path | None = None,
        show_bootstrap_messages: bool = False,
    ):
        self._sessions_dir = sessions_dir or DEFAULT_SESSIONS_DIR
        self._history_path = history_path or DEFAULT_HISTORY_PATH
        self._show_bootstrap_messages = show_bootstrap_messages
        self._renderer = CodexRenderer()

    @property
    def name(self) -> str:
        return "Codex"

    @property
    def normalizer_key(self) -> str:
        return "codex"

    @property
    def cli_command(self) -> str | None:
        return CLI_COMMAND

    def find_recent_sessions(
        self, limit: int = 10, include_subagents: bool = True
    ) -> list[Path]:
        del include_subagents
        return find_recent_sessions(self._sessions_dir, limit=limit)

    def get_projects_dir(self) -> Path:
        return self._sessions_dir

    def get_session_metadata(self, session_path: Path) -> SessionMetadata:
        project_name, project_path = get_session_name(session_path)
        tailer = CodexTailer(
            session_path, show_bootstrap_messages=self._show_bootstrap_messages
        )
        return SessionMetadata(
            session_id=get_session_id(session_path),
            project_name=project_name,
            project_path=project_path,
            first_message=get_first_user_message(
                session_path,
                history_path=self._history_path,
                show_bootstrap_messages=self._show_bootstrap_messages,
            ),
            started_at=tailer.get_first_timestamp(),
            backend_data={"file_path": str(session_path)},
        )

    def get_session_id(self, session_path: Path) -> str:
        return get_session_id(session_path)

    def has_messages(self, session_path: Path) -> bool:
        return has_messages(
            session_path, show_bootstrap_messages=self._show_bootstrap_messages
        )

    def create_tailer(self, session_path: Path) -> SessionTailerProtocol:
        return CodexTailer(
            session_path, show_bootstrap_messages=self._show_bootstrap_messages
        )

    def get_session_token_usage(self, session_path: Path) -> TokenUsage:
        return get_session_token_usage(session_path)

    def get_session_model(self, session_path: Path) -> str | None:
        return get_session_model(session_path)

    def get_models(self) -> list[str]:
        """Get available Codex model identifiers.

        Prefer the locally cached Codex model catalog, filtered to models that
        Codex currently exposes in its picker.
        """
        cache_path = Path.home() / ".codex" / "models_cache.json"
        try:
            data = json.loads(cache_path.read_text())
        except FileNotFoundError:
            logger.warning("Codex models cache not found at %s", cache_path)
            return []
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read Codex models cache %s: %s", cache_path, exc)
            return []

        models = data.get("models", [])
        visible = [
            model for model in models
            if isinstance(model, dict) and model.get("visibility") == "list"
        ]
        visible.sort(key=lambda model: model.get("priority", 999))
        return [
            str(model.get("slug"))
            for model in visible
            if model.get("slug")
        ]

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
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        return build_send_command(
            session_id,
            message,
            skip_permissions=skip_permissions,
            output_format=output_format,
            add_dirs=add_dirs,
        )

    def build_fork_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> CommandSpec:
        return build_fork_command(
            session_id,
            message,
            skip_permissions=skip_permissions,
            output_format=output_format,
            add_dirs=add_dirs,
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
            message,
            skip_permissions=skip_permissions,
            model=model,
            output_format=output_format,
            add_dirs=add_dirs,
        )

    def ensure_session_indexed(self, session_id: str) -> None:
        ensure_session_indexed(session_id)

    def get_message_renderer(self) -> MessageRendererProtocol:
        return self._renderer

    def should_watch_file(self, path: Path) -> bool:
        return should_watch_file(path)

    def get_session_id_from_changed_file(self, path: Path) -> str | None:
        return get_session_id(path) if should_watch_file(path) else None
