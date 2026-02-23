"""FastAPI server with SSE endpoint for live transcript updates."""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import watchfiles
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, Response
from sse_starlette.sse import EventSourceResponse

from .backends import CodingToolBackend, get_backend, get_multi_backend
from .backends.thinking import detect_thinking_level
from .broadcasting import (
    add_client,
    broadcast_event,
    broadcast_message,
    broadcast_permission_denied,
    broadcast_session_added,
    broadcast_session_catchup,
    broadcast_session_removed,
    broadcast_session_status,
    broadcast_session_summary_updated,
    broadcast_session_token_usage_updated,
    get_clients,
    remove_client,
)
from .routes import archives_router, diff_router, files_router, sessions_router, statuses_router
from .routes.sessions import configure_session_routes
from .sessions import (
    MAX_SESSIONS,
    SessionInfo,
    add_session,
    get_current_backend,
    get_known_session_files,
    get_projects_dir,
    get_session,
    get_sessions,
    get_sessions_list,
    get_sessions_lock,
    session_count,
    set_backend,
)
from .summarizer import IdleTracker, LogWriter, Summarizer

logger = logging.getLogger(__name__)

# Configuration
CATCHUP_TIMEOUT = (
    30  # seconds - max time for catchup before telling client to reinitialize
)
_send_enabled = True  # Enabled by default, disable with --disable-send CLI flag
_skip_permissions = False  # Enable with --dangerously-skip-permissions CLI flag
_fork_enabled = False  # Enable with --fork CLI flag
_default_send_backend: str | None = None  # Enable with --default-send-backend CLI flag
_include_subagents = False  # Enable with --include-subagents CLI flag
_enable_thinking = False  # Enable with --enable-thinking CLI flag
_thinking_budget: int | None = None  # Fixed budget with --thinking-budget CLI flag
_terminal_enabled = True  # Enabled by default, disable with --disable-terminal CLI flag

# Global state for server (not session-related)
_watch_task: asyncio.Task | None = None

# Summarization state
_summarizer: "Summarizer | None" = None
_idle_tracker: "IdleTracker | None" = None
_summarize_after_idle_for: int | None = None
_idle_summary_model: str = "haiku"  # Model to use for idle summarization
_summary_after_long_running: int | None = None  # Summarize if CLI runs longer than N seconds

# Pending processes for new sessions (cwd -> process)
# When a new session is started, we store the process here until the session file appears
_pending_new_session_processes: dict[str, asyncio.subprocess.Process] = {}

# Allowed directories for sandbox access (dynamically added via API)
_allowed_directories: set[str] = set()

# Backend and rendering
_backend: CodingToolBackend | None = None
_css: str | None = None

# Cached models per backend (backend_name -> list of model strings)
_cached_models: dict[str, list[str]] = {}

# Session ownership callback for SSE filtering (set during lifespan if isolation backend)
_session_owner_callback = None


# Configuration setters/getters


def set_send_enabled(enabled: bool) -> None:
    """Set whether sending messages to Claude is enabled."""
    global _send_enabled
    _send_enabled = enabled


def set_skip_permissions(enabled: bool) -> None:
    """Set whether to skip permission prompts when running Claude."""
    global _skip_permissions
    _skip_permissions = enabled


def set_fork_enabled(enabled: bool) -> None:
    """Set whether the fork button is enabled."""
    global _fork_enabled
    _fork_enabled = enabled


def set_default_send_backend(backend: str | None) -> None:
    """Set the default backend for new sessions."""
    global _default_send_backend
    _default_send_backend = backend


def get_default_send_backend() -> str | None:
    """Get the default backend for new sessions."""
    return _default_send_backend


def set_include_subagents(enabled: bool) -> None:
    """Set whether to include subagent sessions in discovery."""
    global _include_subagents
    _include_subagents = enabled


def get_include_subagents() -> bool:
    """Get whether to include subagent sessions in discovery."""
    return _include_subagents


def set_enable_thinking(enabled: bool) -> None:
    """Set whether to enable thinking level detection."""
    global _enable_thinking
    _enable_thinking = enabled


def set_thinking_budget(budget: int | None) -> None:
    """Set a fixed thinking token budget (overrides keyword detection)."""
    global _thinking_budget
    _thinking_budget = budget


def is_send_enabled() -> bool:
    """Check if sending messages is enabled."""
    return _send_enabled


def is_fork_enabled() -> bool:
    """Check if fork is enabled."""
    return _fork_enabled


def is_skip_permissions() -> bool:
    """Check if skip permissions is enabled."""
    return _skip_permissions


def set_terminal_enabled(enabled: bool) -> None:
    """Set whether the terminal feature is enabled."""
    global _terminal_enabled
    _terminal_enabled = enabled


def is_terminal_enabled() -> bool:
    """Check if terminal feature is enabled."""
    return _terminal_enabled


# Allowed directories management


def _get_allowed_dirs_path() -> Path:
    """Get the path to the allowed directories config file."""
    config_dir = Path.home() / ".config" / "vibedeck"
    return config_dir / "allowed-dirs.json"


def _load_allowed_directories() -> set[str]:
    """Load allowed directories from config file."""
    config_path = _get_allowed_dirs_path()
    if not config_path.exists():
        return set()
    try:
        with open(config_path, "r") as f:
            data = json.load(f)
            return set(data.get("directories", []))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load allowed directories: {e}")
        return set()


def _save_allowed_directories(directories: set[str]) -> bool:
    """Save allowed directories to config file."""
    config_path = _get_allowed_dirs_path()
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump({"directories": sorted(directories)}, f, indent=2)
        return True
    except OSError as e:
        logger.error(f"Failed to save allowed directories: {e}")
        return False


def add_allowed_directory(directory: str) -> None:
    """Add a directory to the allowed directories for sandbox access.

    Persists to ~/.config/vibedeck/allowed-dirs.json
    """
    _allowed_directories.add(directory)
    _save_allowed_directories(_allowed_directories)


def remove_allowed_directory(directory: str) -> None:
    """Remove a directory from the allowed directories.

    Persists to ~/.config/vibedeck/allowed-dirs.json
    """
    _allowed_directories.discard(directory)
    _save_allowed_directories(_allowed_directories)


def get_allowed_directories() -> list[str]:
    """Get the list of allowed directories for sandbox access."""
    return list(_allowed_directories)


def load_allowed_directories_from_config() -> None:
    """Load allowed directories from config file into memory.

    Should be called on server startup.
    """
    global _allowed_directories
    _allowed_directories = _load_allowed_directories()
    if _allowed_directories:
        logger.info(f"Loaded {len(_allowed_directories)} allowed directories from config")


# Summarization configuration


def configure_summarization(
    backend: CodingToolBackend,
    summary_log: "Path | None" = None,
    summarize_after_idle_for: int | None = None,
    idle_summary_model: str = "haiku",
    summary_after_long_running: int | None = None,
    summary_prompt: str | None = None,
    summary_prompt_file: "Path | None" = None,
    summary_log_keys: list[str] | None = None,
) -> None:
    """Configure session summarization.

    Args:
        backend: The backend to use for CLI commands.
        summary_log: Path to JSONL log file for summaries.
        summarize_after_idle_for: Seconds of idle before re-summarizing.
        idle_summary_model: Model to use for idle summarization (default: haiku).
        summary_after_long_running: Summarize if CLI runs longer than N seconds.
        summary_prompt: Custom prompt template.
        summary_prompt_file: Path to prompt template file.
        summary_log_keys: Keys to include in JSONL log.
    """
    global _summarizer, _idle_tracker, _summarize_after_idle_for, _idle_summary_model, _summary_after_long_running

    # Create log writer
    log_writer = LogWriter(
        log_path=summary_log,
        log_keys=summary_log_keys,
    )

    # Create summarizer
    _summarizer = Summarizer(
        backend=backend,
        log_writer=log_writer,
        prompt=summary_prompt,
        prompt_file=summary_prompt_file,
        thinking_budget=_thinking_budget,
    )

    # Store settings for later use
    _summarize_after_idle_for = summarize_after_idle_for
    _idle_summary_model = idle_summary_model
    _summary_after_long_running = summary_after_long_running

    # Create idle tracker if threshold is set
    if summarize_after_idle_for is not None:
        _idle_tracker = IdleTracker(
            idle_threshold_seconds=summarize_after_idle_for,
            summarize_callback=_summarize_session_async,
            get_session_callback=get_session,
        )


def get_summarizer() -> "Summarizer | None":
    """Get the current summarizer instance."""
    return _summarizer


def get_idle_summary_model() -> str:
    """Get the model used for idle summarization."""
    return _idle_summary_model


async def _summarize_session_async(session: SessionInfo, model: str | None = None) -> bool:
    """Async wrapper for summarization.

    Args:
        session: The session to summarize.
        model: Model to use for summarization. If None, uses _idle_summary_model
               (for idle-triggered summarization).

    Returns:
        True if successful, False otherwise.
    """
    if _summarizer is None:
        return False

    # Get the session-specific backend (MultiBackend can't build commands directly)
    session_backend = get_backend_for_session(session.path)

    # Create a summarizer with the correct backend for this session
    session_summarizer = Summarizer(
        backend=session_backend,
        log_writer=_summarizer.log_writer,
        prompt=_summarizer.prompt,
        prompt_file=_summarizer.prompt_file,
        thinking_budget=_summarizer.thinking_budget,
    )

    # Use idle model by default (for idle tracker callbacks)
    effective_model = model if model is not None else _idle_summary_model
    result = await session_summarizer.summarize(session, model=effective_model)

    # If summary was successful, broadcast the update and notify idle tracker
    if result.success:
        await _broadcast_session_summary_updated(session.session_id)
        # Mark session as summarized in idle tracker to cancel pending timer
        if _idle_tracker is not None:
            _idle_tracker.mark_session_summarized(session.session_id)

    return result.success


# Backend initialization


def initialize_backend(backend_name: str | None = None, **config) -> CodingToolBackend:
    """Initialize the backend for the server.

    Args:
        backend_name: Name of the backend to use. Defaults to 'claude-code'.
        **config: Backend-specific configuration.

    Returns:
        The initialized backend instance.
    """
    global _backend, _css
    _backend = get_backend(backend_name, **config)
    set_backend(_backend)

    # Load CSS (this is generic, not backend-specific)
    from .rendering import CSS

    _css = CSS

    return _backend


def initialize_multi_backend(**config) -> CodingToolBackend:
    """Initialize a multi-backend that aggregates all available backends.

    Args:
        **config: Backend-specific configuration.

    Returns:
        The initialized multi-backend instance.
    """
    global _backend, _css
    _backend = get_multi_backend(**config)
    set_backend(_backend)

    # Load CSS (this is generic, not backend-specific)
    from .rendering import CSS

    _css = CSS

    return _backend


def get_server_backend() -> CodingToolBackend:
    """Get the current server backend.

    Raises:
        RuntimeError: If backend not initialized.
    """
    if _backend is None:
        raise RuntimeError("Backend not initialized. Call initialize_backend() first.")
    return _backend


def get_renderer_for_session(session_path: Path):
    """Get the appropriate message renderer for a session.

    For MultiBackend, this returns the renderer from the backend that owns
    the session. For single backends, returns that backend's renderer.

    Args:
        session_path: Path to the session file.

    Returns:
        MessageRendererProtocol for the session.
    """
    backend = get_server_backend()

    # Check if this is a MultiBackend with get_renderer_for_session method
    get_renderer = getattr(backend, "get_renderer_for_session", None)
    if get_renderer is not None:
        renderer = get_renderer(session_path)
        if renderer is not None:
            return renderer

    # Fallback to default renderer
    return backend.get_message_renderer()


def get_backend_for_session(session_path: Path) -> CodingToolBackend:
    """Get the appropriate backend for a session.

    For MultiBackend, this returns the backend that owns the session.
    For single backends, returns that backend.

    Args:
        session_path: Path to the session file.

    Returns:
        CodingToolBackend for the session.
    """
    backend = get_server_backend()

    # Check if this is a MultiBackend with get_backend_for_session method
    get_specific = getattr(backend, "get_backend_for_session", None)
    if get_specific is not None:
        specific_backend = get_specific(session_path)
        if specific_backend is not None:
            return specific_backend

    # Fallback to main backend
    return backend


# Broadcasting wrappers (use dependency injection pattern from broadcasting module)


async def _broadcast_session_catchup(info: SessionInfo) -> None:
    """Broadcast session catchup using the renderer for this session."""
    await broadcast_session_catchup(info, get_renderer_for_session)


async def _broadcast_session_summary_updated(session_id: str) -> None:
    """Broadcast session summary update."""
    await broadcast_session_summary_updated(session_id, get_session)


async def _broadcast_session_status(session_id: str) -> None:
    """Broadcast session status update."""
    await broadcast_session_status(session_id, get_session)


async def _broadcast_session_token_usage_updated(session_id: str) -> None:
    """Broadcast session token usage update."""
    await broadcast_session_token_usage_updated(session_id, get_session, get_current_backend)


# Process management


async def _monitor_attached_process(info: SessionInfo) -> None:
    """Monitor an attached process and clean up when it exits.

    This is called as a background task after attaching a pending process
    to a new session. It waits for the process to complete, then clears
    the process reference and broadcasts the updated status.
    """
    if info.process is None:
        return

    start_time = time.monotonic()
    duration = 0.0

    try:
        await info.process.wait()
        duration = time.monotonic() - start_time
    except Exception as e:
        logger.error(f"Error monitoring process for {info.session_id}: {e}")
    finally:
        info.process = None

        # Check if we should summarize
        if _summarizer is not None:
            should_summarize = False
            summary_reason = ""

            if not info.get_summary_path().exists():
                should_summarize = True
                summary_reason = "new session"
            elif _summary_after_long_running is not None and duration >= _summary_after_long_running:
                should_summarize = True
                summary_reason = f"long-running ({duration:.1f}s >= {_summary_after_long_running}s)"

            if should_summarize:
                session_backend = get_backend_for_session(info.path)
                session_model = session_backend.get_session_model(info.path)
                logger.info(f"Triggering summary for {summary_reason} session {info.session_id} with model {session_model}")
                asyncio.create_task(_summarize_session_async(info, model=session_model))

        await _broadcast_session_status(info.session_id)


def _attach_pending_process(info: SessionInfo) -> bool:
    """Try to attach a pending process to a newly discovered session.

    When a new session is started via /sessions/new, the process is stored
    in _pending_new_session_processes until the session file appears. This
    function checks if there's a pending process for this session's project
    path and attaches it.

    Returns:
        True if a process was attached, False otherwise.
    """
    if not info.project_path:
        return False

    cwd_key = str(Path(info.project_path).resolve())
    proc = _pending_new_session_processes.pop(cwd_key, None)

    if proc is not None:
        info.process = proc
        logger.debug(f"Attached pending process to session {info.session_id}")
        return True

    return False


async def run_cli_for_session(
    session_id: str, message: str, fork: bool = False
) -> None:
    """Send a message to a coding session and track the process.

    Args:
        session_id: The session to send the message to
        message: The message to send
        fork: If True, fork the session to create a new one with conversation history
    """
    from .permissions import parse_permission_denials

    info = get_session(session_id)
    if info is None:
        logger.error(f"Session not found: {session_id}")
        return

    # Get the specific backend for this session
    backend = get_backend_for_session(info.path)

    # Determine if we should use permission detection
    # Only for backends that support it and when not using skip_permissions
    use_permission_detection = (
        not _skip_permissions
        and hasattr(backend, "supports_permission_detection")
        and backend.supports_permission_detection()
    )

    try:
        # Ensure session is indexed (backend-specific)
        backend.ensure_session_indexed(session_id)

        # Build command using session-specific backend
        output_format = "stream-json" if use_permission_detection else None
        add_dirs = get_allowed_directories() or None

        # Check if this is a user-aware backend (isolation backend)
        has_user_aware_send = hasattr(backend, "build_send_command_for_user")

        if fork:
            cmd_spec = backend.build_fork_command(
                session_id, message, _skip_permissions, output_format, add_dirs
            )
        elif has_user_aware_send:
            # Isolation backend: derive user_id from session path, ensure container
            user_id = backend.get_session_owner(Path(info.path))
            if user_id is None:
                logger.error(f"Cannot determine owner for session {session_id}")
                return
            await backend.container_manager.ensure_container(user_id)
            cmd_spec = backend.build_send_command_for_user(
                user_id, session_id, message
            )
        else:
            cmd_spec = backend.build_send_command(
                session_id, message, _skip_permissions, output_format, add_dirs
            )

        # Determine working directory from session info
        # For isolation backends, cwd is irrelevant (docker exec handles it)
        cwd = None if has_user_aware_send else (
            info.project_path
            if info.project_path and Path(info.project_path).is_dir()
            else None
        )

        # Get thinking token budget: fixed budget > keyword detection > disabled
        if _thinking_budget is not None:
            # Fixed budget takes precedence
            thinking_env = {"MAX_THINKING_TOKENS": str(_thinking_budget)}
            env = {**os.environ, **thinking_env}
            logger.info(
                f"Sending message to {session_id} with fixed thinking budget "
                f"({_thinking_budget} tokens)"
            )
        elif _enable_thinking:
            # Keyword-based detection
            thinking_level = detect_thinking_level(message)
            thinking_env = {"MAX_THINKING_TOKENS": str(thinking_level.budget_tokens)}
            env = {**os.environ, **thinking_env}
            logger.info(
                f"Sending message to {session_id} with thinking level "
                f"'{thinking_level.name}' ({thinking_level.budget_tokens} tokens)"
            )
        else:
            env = os.environ.copy()
            logger.info(f"Sending message to {session_id} (thinking disabled by default)")

        # Track start time for long-running session detection
        start_time = time.monotonic()
        duration = 0.0  # Initialize in case of early exception

        # Capture stdout if using permission detection
        stdout_pipe = asyncio.subprocess.PIPE if use_permission_detection else asyncio.subprocess.DEVNULL
        # Use PIPE for stdin if we need to pass message content
        stdin_pipe = asyncio.subprocess.PIPE if cmd_spec.stdin else asyncio.subprocess.DEVNULL

        proc = await asyncio.create_subprocess_exec(
            *cmd_spec.args,
            cwd=cwd,
            env=env,
            stdin=stdin_pipe,
            stdout=stdout_pipe,
            stderr=asyncio.subprocess.PIPE,
        )

        # Write message to stdin if provided
        if cmd_spec.stdin:
            proc.stdin.write(cmd_spec.stdin.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            await proc.stdin.wait_closed()

        # Only track process on the original session if not forking
        # (forked session will create its own session file and be picked up by file watcher)
        if not fork:
            info.process = proc
            await _broadcast_session_status(session_id)

        # Wait for completion
        stdout, stderr = await proc.communicate()
        duration = time.monotonic() - start_time

        if proc.returncode != 0:
            logger.error(f"CLI process failed for {session_id}: {stderr.decode()}")

        # Check for permission denials if using permission detection
        if use_permission_detection and stdout:
            denials = parse_permission_denials(stdout.decode())
            if denials:
                logger.info(
                    f"Permission denials detected for {session_id}: "
                    f"{[d['tool_name'] for d in denials]}"
                )
                await broadcast_permission_denied(session_id, denials, message)
                # Don't process queue or summarize - wait for user decision
                if not fork:
                    info.process = None
                    await _broadcast_session_status(session_id)
                return

    except Exception as e:
        logger.error(f"Error running CLI for {session_id}: {e}")

    finally:
        if not fork:
            info.process = None

            # Determine if we should summarize:
            # 1. New session (no summary yet) - always summarize immediately
            # 2. Long-running session (duration > threshold) - summarize to use warm cache
            should_summarize = False
            summary_reason = ""

            if _summarizer is not None:
                if not info.get_summary_path().exists():
                    should_summarize = True
                    summary_reason = "new session"
                elif _summary_after_long_running is not None and duration >= _summary_after_long_running:
                    should_summarize = True
                    summary_reason = f"long-running ({duration:.1f}s >= {_summary_after_long_running}s)"

            if should_summarize:
                # Use the same model that was used for the conversation
                session_backend = get_backend_for_session(info.path)
                session_model = session_backend.get_session_model(info.path)
                logger.info(f"Triggering summary for {summary_reason} session {session_id} with model {session_model}")
                asyncio.create_task(_summarize_session_async(info, model=session_model))

            # Process queue if messages waiting
            if info.message_queue:
                next_message = info.message_queue.pop(0)
                asyncio.create_task(run_cli_for_session(session_id, next_message))

            await _broadcast_session_status(session_id)


# Session message processing


async def process_session_messages(session_id: str) -> None:
    """Read new messages from a session and broadcast to clients."""
    info = get_session(session_id)
    if info is None:
        return

    # Get the renderer for this specific session
    renderer = get_renderer_for_session(info.path)

    new_entries = info.tailer.read_new_lines()
    logger.debug(f"read_new_lines returned {len(new_entries)} entries for {session_id}")
    for entry in new_entries:
        html = renderer.render_message(entry)
        if html:
            await broadcast_message(session_id, html)

    # Broadcast updated waiting state and token usage after processing messages
    if new_entries:
        await _broadcast_session_status(session_id)
        await _broadcast_session_token_usage_updated(session_id)


async def process_session_summary_update(session_id: str) -> None:
    """Reload summary data for a session and broadcast to clients.

    Called when a session's summary file is created or modified.
    """
    logger.debug(f"Processing summary update for session {session_id}")
    info = get_session(session_id)
    if info is None:
        logger.debug(f"Session {session_id} not found, skipping summary update")
        return

    # Reload summary data from file
    summary_path = info.get_summary_path()
    logger.debug(f"Looking for summary at: {summary_path}")
    if info.load_summary():
        logger.info(f"Summary updated for session {session_id}: {info.summary_title}")
        await _broadcast_session_summary_updated(session_id)
    else:
        logger.debug(f"Failed to load summary for session {session_id}")


async def check_for_new_sessions() -> None:
    """Check for new session files and add them.

    Uses the backend's find_recent_sessions to discover new sessions,
    which handles backend-specific file patterns (JSONL for Claude Code,
    JSON for OpenCode).
    """
    backend = get_server_backend()

    # Use backend to find recent sessions (handles pattern differences)
    try:
        recent = backend.find_recent_sessions(
            limit=MAX_SESSIONS, include_subagents=get_include_subagents()
        )
        for f in recent:
            if f not in get_known_session_files():
                async with get_sessions_lock():
                    info, evicted_id = add_session(f)
                    if evicted_id:
                        await broadcast_session_removed(evicted_id)
                    if info:
                        # Check if there's a pending process for this session's project path
                        attached = _attach_pending_process(info)
                        await broadcast_session_added(info)
                        await _broadcast_session_catchup(info)
                        # If we attached a process, broadcast status and start monitoring
                        if attached:
                            await _broadcast_session_status(info.session_id)
                            # Monitor process completion in background
                            asyncio.create_task(_monitor_attached_process(info))
    except Exception as e:
        logger.warning(f"Failed to check for new sessions: {e}")


# Watch loop


def _get_watch_directories() -> list[Path]:
    """Get all directories to watch based on the current backend.

    For MultiBackend, returns directories from all backends.
    For single backend, returns just that backend's directory.
    """
    backend = get_server_backend()

    # Check if this is a MultiBackend with get_all_project_dirs method
    if hasattr(backend, "get_all_project_dirs"):
        return backend.get_all_project_dirs()

    # Single backend
    projects_dir = get_projects_dir()
    return [projects_dir] if projects_dir.exists() else []


async def watch_loop() -> None:
    """Background task that watches for file changes.

    Watches the backend's projects directory for file changes. The backend
    determines which files should trigger updates via should_watch_file()
    and how to map changed files to session IDs via get_session_id_from_changed_file().

    For MultiBackend, watches all directories from all backends.
    """
    backend = get_server_backend()
    watch_dirs = _get_watch_directories()

    if not watch_dirs:
        logger.warning("No project directories found to watch")
        return

    # Filter to existing directories only
    watch_dirs = [d for d in watch_dirs if d.exists()]
    if not watch_dirs:
        logger.warning("No existing project directories to watch")
        return

    logger.info(f"Starting watch loop for {len(watch_dirs)} directories: {watch_dirs}")

    try:
        async for changes in watchfiles.awatch(*watch_dirs):
            # Collect sessions to process and whether to check for new sessions
            # This minimizes lock contention by batching operations
            sessions_to_process: set[str] = set()
            sessions_with_summary_updates: set[str] = set()
            need_new_session_check = False

            for change_type, changed_path in changes:
                changed_path = Path(changed_path)

                # Use backend to check if file should be watched
                if not backend.should_watch_file(changed_path):
                    continue

                # Get session ID from the changed file path
                # For Claude Code: session ID is the filename (or from summary file)
                # For OpenCode: session ID is extracted from message/part path
                session_id = backend.get_session_id_from_changed_file(changed_path)
                logger.debug(
                    f"File change: {change_type.name} {changed_path.name} -> session {session_id}"
                )

                if session_id and get_session(session_id) is not None:
                    # Known session
                    # Check if this is a summary file change
                    is_summary = getattr(backend, "is_summary_file", None)
                    logger.debug(
                        f"is_summary_file method: {is_summary}, "
                        f"file: {changed_path.name}, "
                        f"result: {is_summary(changed_path) if is_summary else 'N/A'}"
                    )
                    if is_summary and is_summary(changed_path):
                        logger.debug(f"Summary file detected for session {session_id}")
                        sessions_with_summary_updates.add(session_id)
                    else:
                        # Regular session file - queue for message processing
                        sessions_to_process.add(session_id)
                else:
                    # Unknown session - might be a new session file
                    need_new_session_check = True

            # Check for new sessions (needs lock, but only once per batch)
            if need_new_session_check:
                await check_for_new_sessions()

            # Process messages for known sessions (doesn't need lock)
            for session_id in sessions_to_process:
                await process_session_messages(session_id)
                # Notify idle tracker of activity (for re-summarization after idle)
                if _idle_tracker is not None:
                    _idle_tracker.on_session_activity(session_id)

            # Process summary updates for known sessions
            for session_id in sessions_with_summary_updates:
                await process_session_summary_update(session_id)

    except asyncio.CancelledError:
        logger.info("Watch loop cancelled")
        raise
    except Exception as e:
        logger.error(f"Watch loop error: {e}")


# FastAPI app setup


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage server lifecycle - start/stop file watcher."""
    global _watch_task

    backend = get_server_backend()

    # Load allowed directories from config file
    load_allowed_directories_from_config()

    # Determine session owner callback for user-scoped access
    global _session_owner_callback
    _get_session_owner = None
    if hasattr(backend, "get_session_owner"):
        _get_session_owner = backend.get_session_owner
        _session_owner_callback = _get_session_owner

    # Configure session routes with server dependencies
    configure_session_routes(
        get_server_backend=get_server_backend,
        get_backend_for_session=get_backend_for_session,
        is_send_enabled=is_send_enabled,
        is_fork_enabled=is_fork_enabled,
        is_skip_permissions=is_skip_permissions,
        get_default_send_backend=get_default_send_backend,
        get_allowed_directories=get_allowed_directories,
        add_allowed_directory=add_allowed_directory,
        run_cli_for_session=run_cli_for_session,
        broadcast_session_status=_broadcast_session_status,
        summarize_session_async=_summarize_session_async,
        get_summarizer=get_summarizer,
        get_idle_summary_model=get_idle_summary_model,
        cached_models=_cached_models,
        get_session_owner=_get_session_owner,
    )

    # Startup: find recent sessions
    recent = backend.find_recent_sessions(
        limit=MAX_SESSIONS, include_subagents=get_include_subagents()
    )
    async with get_sessions_lock():
        for path in recent:
            add_session(path, evict_oldest=False)  # No eviction needed at startup

    if session_count() == 0:
        logger.warning("No session files found")

    # Start watching for changes
    _watch_task = asyncio.create_task(watch_loop())

    # Start idle tracker if configured
    if _idle_tracker is not None:
        _idle_tracker.start()
        logger.info("Idle summarization tracker started")

    yield

    # Shutdown
    if _idle_tracker is not None:
        _idle_tracker.shutdown()

    if _watch_task:
        _watch_task.cancel()
        try:
            await _watch_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="VibeDeck", lifespan=lifespan)

# Include routers
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(archives_router)
app.include_router(diff_router)
app.include_router(statuses_router)


# Core routes (index, static, SSE events, health)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Serve the main live transcript page."""
    from .rendering import get_template

    template = get_template("live.html")
    html = template.render(css=_css)
    return HTMLResponse(content=html)


@app.get("/static/js/{filename}")
async def serve_js(filename: str) -> Response:
    """Serve JavaScript modules from the static/js directory."""
    from importlib.resources import files

    # Validate filename (only allow .js files, no path traversal)
    if not filename.endswith(".js") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        content = (
            files("vibedeck")
            .joinpath("templates", "static", "js", filename)
            .read_text()
        )
        return Response(
            content=content,
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not found")


def _get_sse_user_id(request: Request) -> str | None:
    """Extract user_id from request for SSE filtering.

    Returns None if auth is not enabled.
    """
    session = request.scope.get("session")
    if session is None:
        return None
    user = session.get("user")
    return user["id"] if user else None


def _event_belongs_to_user(event: dict, user_id: str, get_owner) -> bool:
    """Check if an SSE event belongs to the given user.

    Events without a session_id pass through (global events).
    Events with a session_id are checked against the session owner.
    """
    data = event.get("data", {})

    # Extract session_id from event data
    session_id = data.get("session_id") or data.get("id")
    if session_id is None:
        # Global events (no session_id) pass through
        return True

    # Look up the session to get its path, then check owner
    info = get_session(session_id)
    if info is None:
        # Session not found — could be a removal event with "id" field
        # For removal events, let them through (client handles unknown IDs gracefully)
        return True

    owner = get_owner(info.path)
    if owner is None:
        return True  # Can't determine owner, allow

    return owner == user_id


async def event_generator(request: Request) -> AsyncGenerator[dict, None]:
    """Generate SSE events for a client.

    When auth is enabled, filters events to only include the authenticated
    user's sessions.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    add_client(queue)

    # Determine user filtering
    user_id = _get_sse_user_id(request)
    get_owner = _session_owner_callback
    filter_by_user = user_id is not None and get_owner is not None

    try:
        # Send sessions list (lazy loading: no messages sent here)
        async with get_sessions_lock():
            sessions_data = get_sessions_list()

        # Filter sessions by user if auth is enabled
        if filter_by_user:
            sessions_data = [
                s for s in sessions_data
                if get_owner(Path(s["path"])) == user_id
            ]

        yield {
            "event": "sessions",
            "data": json.dumps({"sessions": sessions_data, "maxSessions": MAX_SESSIONS}),
        }

        # Signal catchup complete immediately (messages loaded on-demand via REST API)
        yield {"event": "catchup_complete", "data": "{}"}

        # Stream new events
        ping_interval = 30  # seconds

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                # Wait for new event with timeout for ping
                event = await asyncio.wait_for(queue.get(), timeout=ping_interval)

                # Filter event by user ownership
                if filter_by_user and not _event_belongs_to_user(event, user_id, get_owner):
                    continue

                yield {
                    "event": event["event"],
                    "data": json.dumps(event["data"]),
                }
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                yield {"event": "ping", "data": "{}"}

    finally:
        remove_client(queue)


@app.get("/events")
async def events(request: Request) -> EventSourceResponse:
    """SSE endpoint for live transcript updates."""
    return EventSourceResponse(event_generator(request))


# Auth status (set to True when setup_auth() is called)
_auth_enabled = False


@app.get("/auth/user")
async def auth_user(request: Request) -> dict:
    """Get the currently authenticated user.

    Returns auth status and user info. When auth is disabled,
    returns auth_enabled=false. When auth is enabled but user not
    logged in, returns user=null with auth_enabled=true.
    """
    if not _auth_enabled:
        return {"user": None, "auth_enabled": False}

    session = request.scope.get("session")
    user = session.get("user") if session else None
    return {"user": user, "auth_enabled": True}


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {
        "status": "ok",
        "sessions": session_count(),
        "clients": len(get_clients()),
    }


# Terminal routes


@app.get("/api/terminal/enabled")
async def terminal_enabled() -> dict:
    """Check if terminal feature is enabled."""
    from .terminal import is_terminal_available

    return {
        "enabled": _terminal_enabled and is_terminal_available(),
    }


@app.get("/api/terminal/shells")
async def terminal_shells() -> dict:
    """Get available shells and default shell."""
    from .terminal import terminal_manager

    if not _terminal_enabled:
        raise HTTPException(status_code=403, detail="Terminal feature is disabled")

    return terminal_manager.get_shells()


@app.websocket("/ws/terminal")
async def terminal_websocket(websocket: WebSocket, cwd: str | None = None) -> None:
    """WebSocket endpoint for terminal I/O.

    Query params:
        cwd: Working directory for the terminal (optional)
    """
    from .terminal import is_terminal_available, terminal_manager

    if not _terminal_enabled:
        await websocket.close(code=4003, reason="Terminal feature disabled")
        return

    if not is_terminal_available():
        await websocket.close(code=4003, reason="ptyprocess not installed")
        return

    await terminal_manager.handle_websocket(websocket, cwd)
