"""FastAPI server with SSE endpoint for live transcript updates."""

import asyncio
import inspect
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import watchfiles
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import os

from .backends import CodingToolBackend, get_backend, get_multi_backend
from .backends.thinking import detect_thinking_level
from .summarizer import Summarizer, LogWriter, IdleTracker
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
    remove_session,
    session_count,
    set_backend,
)

logger = logging.getLogger(__name__)

# Configuration
CATCHUP_TIMEOUT = (
    30  # seconds - max time for catchup before telling client to reinitialize
)
_send_enabled = False  # Enable with --enable-send CLI flag
_skip_permissions = False  # Enable with --dangerously-skip-permissions CLI flag
_fork_enabled = False  # Enable with --fork CLI flag
_default_send_backend: str | None = None  # Enable with --default-send-backend CLI flag
_include_subagents = False  # Enable with --include-subagents CLI flag
_enable_thinking = False  # Enable with --enable-thinking CLI flag
_thinking_budget: int | None = None  # Fixed budget with --thinking-budget CLI flag

# Global state for server (not session-related)
_clients: set[asyncio.Queue] = set()
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

# Backend and rendering
_backend: CodingToolBackend | None = None
_css: str | None = None

# Cached models per backend (backend_name -> list of model strings)
_cached_models: dict[str, list[str]] = {}


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


def set_default_send_backend(backend: str) -> None:
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
        await broadcast_session_summary_updated(session.session_id)
        # Mark session as summarized in idle tracker to cancel pending timer
        if _idle_tracker is not None:
            _idle_tracker.mark_session_summarized(session.session_id)

    return result.success


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


class SendMessageRequest(BaseModel):
    """Request body for sending a message to a session."""

    message: str


class FileResponse(BaseModel):
    """Response for file preview endpoint."""

    content: str
    path: str
    filename: str
    size: int
    language: str | None
    truncated: bool = False
    rendered_html: str | None = None  # For markdown files: pre-rendered HTML


# File extension to highlight.js language mapping
EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".json": "json",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".md": "markdown",
    ".markdown": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".r": "r",
    ".lua": "lua",
    ".pl": "perl",
    ".gitignore": "plaintext",
    ".env": "plaintext",
}

MAX_FILE_SIZE = 1024 * 1024  # 1MB

# Image file extensions and their MIME types
IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
}


class NewSessionRequest(BaseModel):
    """Request body for starting a new session."""

    message: str  # Initial message to send (required)
    cwd: str | None = None  # Working directory (optional)
    backend: str | None = None  # Backend to use (optional, for multi-backend mode)
    model_index: int | None = (
        None  # Model index from /backends/{name}/models (optional)
    )


async def broadcast_event(event_type: str, data: dict) -> None:
    """Broadcast an event to all connected clients."""
    dead_clients = []

    for queue in _clients:
        try:
            queue.put_nowait({"event": event_type, "data": data})
        except asyncio.QueueFull:
            dead_clients.append(queue)

    for queue in dead_clients:
        _clients.discard(queue)


async def broadcast_message(session_id: str, html: str) -> None:
    """Broadcast a message to all connected clients."""
    await broadcast_event(
        "message",
        {
            "type": "html",
            "content": html,
            "session_id": session_id,
        },
    )


async def broadcast_session_added(info: SessionInfo) -> None:
    """Broadcast that a new session was added."""
    await broadcast_event("session_added", info.to_dict())


async def broadcast_session_catchup(info: SessionInfo) -> None:
    """Broadcast existing messages for a newly added session.

    When a session is added while clients are already connected, those clients
    need to receive the existing messages (catchup) for that session.
    """
    renderer = get_renderer_for_session(info.path)

    existing = info.tailer.read_all()
    for entry in existing:
        html = renderer.render_message(entry)
        if html:
            await broadcast_message(info.session_id, html)


async def broadcast_session_removed(session_id: str) -> None:
    """Broadcast that a session was removed."""
    await broadcast_event("session_removed", {"id": session_id})


async def broadcast_session_summary_updated(session_id: str) -> None:
    """Broadcast that a session's summary data has been updated."""
    info = get_session(session_id)
    if info is None:
        logger.debug(f"Cannot broadcast summary update: session {session_id} not found")
        return
    logger.debug(
        f"Broadcasting summary update for {session_id}: "
        f"title={info.summary_title!r}, short={info.summary_short!r}"
    )
    await broadcast_event(
        "session_summary_updated",
        {
            "session_id": session_id,
            "summaryTitle": info.summary_title,
            "summaryShort": info.summary_short,
            "summaryExecutive": info.summary_executive,
        },
    )


async def broadcast_session_status(session_id: str) -> None:
    """Broadcast session status (running state, queue size, waiting state)."""
    info = get_session(session_id)
    if info is None:
        return
    await broadcast_event(
        "session_status",
        {
            "session_id": session_id,
            "running": info.process is not None,
            "queued_messages": len(info.message_queue),
            "waiting_for_input": info.tailer.waiting_for_input,
        },
    )


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
        # Wait for process to complete
        await info.process.wait()
        duration = time.monotonic() - start_time
        logger.debug(f"Attached process completed for session {info.session_id} ({duration:.1f}s)")
    except Exception as e:
        logger.error(f"Error monitoring attached process for {info.session_id}: {e}")
    finally:
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
            session_backend = get_backend_for_session(info.path)
            session_model = session_backend.get_session_model(info.path)
            logger.info(f"Triggering summary for {summary_reason} session {info.session_id} with model {session_model}")
            asyncio.create_task(_summarize_session_async(info, model=session_model))

        await broadcast_session_status(info.session_id)


def _attach_pending_process(info: SessionInfo) -> bool:
    """Attach a pending process to a newly discovered session.

    When a new session is created via /sessions/new, we store the process
    in _pending_new_session_processes keyed by cwd. When the session file
    appears and we add it, we check if there's a matching pending process
    and attach it so the stop button works.

    Returns:
        True if a process was attached, False otherwise.
    """
    if not info.project_path:
        return False

    # Try to match by project path
    project_path_key = str(Path(info.project_path).resolve())
    proc = _pending_new_session_processes.pop(project_path_key, None)

    if proc is not None:
        # Check if process is still running
        if proc.returncode is None:
            info.process = proc
            logger.info(f"Attached pending process to session {info.session_id}")
            return True
        else:
            logger.debug(f"Pending process already exited for {info.session_id}")

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
    info = get_session(session_id)
    if info is None:
        logger.error(f"Session not found: {session_id}")
        return

    # Get the specific backend for this session
    backend = get_backend_for_session(info.path)

    try:
        # Ensure session is indexed (backend-specific)
        backend.ensure_session_indexed(session_id)

        # Build command using session-specific backend
        if fork:
            cmd_args = backend.build_fork_command(
                session_id, message, _skip_permissions
            )
        else:
            cmd_args = backend.build_send_command(
                session_id, message, _skip_permissions
            )

        # Determine working directory from session info
        cwd = (
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

        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        # Only track process on the original session if not forking
        # (forked session will create its own session file and be picked up by file watcher)
        if not fork:
            info.process = proc
            await broadcast_session_status(session_id)

        # Wait for completion
        _, stderr = await proc.communicate()
        duration = time.monotonic() - start_time

        if proc.returncode != 0:
            logger.error(f"CLI process failed for {session_id}: {stderr.decode()}")

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

            await broadcast_session_status(session_id)


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

    # Broadcast updated waiting state after processing messages
    if new_entries:
        await broadcast_session_status(session_id)


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
        await broadcast_session_summary_updated(session_id)
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
                        await broadcast_session_catchup(info)
                        # If we attached a process, broadcast status and start monitoring
                        if attached:
                            await broadcast_session_status(info.session_id)
                            # Monitor process completion in background
                            asyncio.create_task(_monitor_attached_process(info))
    except Exception as e:
        logger.warning(f"Failed to check for new sessions: {e}")


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage server lifecycle - start/stop file watcher."""
    global _watch_task

    backend = get_server_backend()

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


app = FastAPI(title="Claude Code Session Explorer", lifespan=lifespan)


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
            files("claude_code_session_explorer")
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


@app.get("/sessions")
async def list_sessions() -> dict:
    """List all tracked sessions."""
    async with get_sessions_lock():
        return {"sessions": get_sessions_list()}


async def event_generator(request: Request) -> AsyncGenerator[dict, None]:
    """Generate SSE events for a client."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _clients.add(queue)

    try:
        # Send sessions list
        async with get_sessions_lock():
            sessions_data = get_sessions_list()
        yield {
            "event": "sessions",
            "data": json.dumps({"sessions": sessions_data}),
        }

        # Send existing messages for each session (catchup)
        # Hold lock to prevent sessions modification during iteration
        catchup_start = time.monotonic()
        catchup_timed_out = False

        async with get_sessions_lock():
            for session_id, info in get_sessions().items():
                # Get the renderer for this specific session
                renderer = get_renderer_for_session(info.path)
                existing = info.tailer.read_all()
                for entry in existing:
                    # Check if catchup is taking too long (slow client)
                    if time.monotonic() - catchup_start > CATCHUP_TIMEOUT:
                        catchup_timed_out = True
                        break
                    html = renderer.render_message(entry)
                    if html:
                        yield {
                            "event": "message",
                            "data": json.dumps(
                                {
                                    "type": "html",
                                    "content": html,
                                    "session_id": session_id,
                                }
                            ),
                        }
                if catchup_timed_out:
                    break

        if catchup_timed_out:
            logger.warning("Catchup timeout - client too slow, requesting reinitialize")
            yield {
                "event": "reinitialize",
                "data": json.dumps({"reason": "catchup_timeout"}),
            }
            return

        # Signal catchup complete
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
                yield {
                    "event": event["event"],
                    "data": json.dumps(event["data"]),
                }
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                yield {"event": "ping", "data": "{}"}

    finally:
        _clients.discard(queue)


@app.get("/events")
async def events(request: Request) -> EventSourceResponse:
    """SSE endpoint for live transcript updates."""
    return EventSourceResponse(event_generator(request))


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {
        "status": "ok",
        "sessions": session_count(),
        "clients": len(_clients),
    }


@app.get("/send-enabled")
async def send_enabled() -> dict:
    """Check if sending messages is enabled."""
    return {"enabled": _send_enabled}


@app.get("/fork-enabled")
async def fork_enabled() -> dict:
    """Check if fork button is enabled."""
    return {"enabled": _fork_enabled}


@app.get("/default-send-backend")
async def default_send_backend() -> dict:
    """Get the default backend for new sessions."""
    return {"backend": _default_send_backend}


@app.get("/backends")
async def list_backends() -> dict:
    """List available backends for creating new sessions.

    Returns dict with:
        backends: List of backend info dicts with name, cli_available, supports_models
    """
    backend = get_server_backend()
    backends_info = []

    # Check if multi-backend
    get_backends = getattr(backend, "get_backends", None)
    if get_backends is not None:
        # Multi-backend mode - list all backends
        for b in get_backends():
            has_models = hasattr(b, "get_models") and callable(getattr(b, "get_models"))
            backends_info.append(
                {
                    "name": b.name,
                    "cli_available": b.is_cli_available(),
                    "supports_models": has_models,
                }
            )
    else:
        # Single backend mode
        has_models = hasattr(backend, "get_models") and callable(
            getattr(backend, "get_models")
        )
        backends_info.append(
            {
                "name": backend.name,
                "cli_available": backend.is_cli_available(),
                "supports_models": has_models,
            }
        )

    return {"backends": backends_info}


def _normalize_backend_name(name: str) -> str:
    """Normalize backend name for cache keys."""
    return name.lower().replace(" ", "-")


def _get_target_backend(backend_name: str):
    """Get target backend by name, returns (backend, normalized_name) or raises 404."""
    backend = get_server_backend()
    normalized = _normalize_backend_name(backend_name)

    # Check if multi-backend
    get_by_name = getattr(backend, "get_backend_by_name", None)
    if get_by_name is not None:
        target_backend = get_by_name(backend_name)
        if target_backend is not None:
            return target_backend, _normalize_backend_name(target_backend.name)
    elif _normalize_backend_name(backend.name) == normalized:
        # Single backend mode, check name matches
        return backend, normalized

    raise HTTPException(status_code=404, detail=f"Backend not found: {backend_name}")


@app.get("/backends/{backend_name}/models")
async def list_backend_models(backend_name: str) -> dict:
    """List available models for a specific backend.

    Args:
        backend_name: Name of the backend (case-insensitive)

    Returns dict with:
        models: List of model identifiers (indexed, use index for model_index param)
    """
    target_backend, normalized_name = _get_target_backend(backend_name)

    # Check if backend supports models
    get_models = getattr(target_backend, "get_models", None)
    if get_models is None or not callable(get_models):
        return {"models": []}

    # Fetch and cache models
    models = get_models()
    _cached_models[normalized_name] = models

    return {"models": models}


@app.get("/sessions/{session_id}/status")
async def session_status(session_id: str) -> dict:
    """Get the status of a session (running state, queue size)."""
    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "running": info.process is not None,
        "queued_messages": len(info.message_queue),
    }


@app.post("/sessions/{session_id}/send")
async def send_message(session_id: str, request: SendMessageRequest) -> dict:
    """Send a message to a coding session."""
    if not _send_enabled:
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Start server with --enable-send to enable.",
        )

    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the specific backend for this session
    backend = get_backend_for_session(info.path)

    # Check if CLI is available for this backend
    if not backend.is_cli_available():
        raise HTTPException(
            status_code=503,
            detail=f"CLI not found. {backend.get_cli_install_instructions()}",
        )

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # If a process is already running, queue the message
    if info.process is not None:
        info.message_queue.append(message)
        await broadcast_session_status(session_id)
        return {
            "status": "queued",
            "session_id": session_id,
            "queue_position": len(info.message_queue),
        }

    # Start the CLI process
    asyncio.create_task(run_cli_for_session(session_id, message))

    return {"status": "sent", "session_id": session_id}


@app.post("/sessions/{session_id}/fork")
async def fork_session(session_id: str, request: SendMessageRequest) -> dict:
    """Fork a session: create a new session with conversation history and send a message."""
    if not _fork_enabled:
        raise HTTPException(
            status_code=403,
            detail="Fork feature is disabled. Start server with --fork to enable.",
        )

    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the specific backend for this session
    backend = get_backend_for_session(info.path)

    # Check if CLI is available for this backend
    if not backend.is_cli_available():
        raise HTTPException(
            status_code=503,
            detail=f"CLI not found. {backend.get_cli_install_instructions()}",
        )

    # Check if backend supports forking
    if not backend.supports_fork_session():
        raise HTTPException(
            status_code=501,
            detail="This backend does not support session forking.",
        )

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Start the CLI process with fork=True
    asyncio.create_task(run_cli_for_session(session_id, message, fork=True))

    return {"status": "forking", "session_id": session_id}


@app.post("/sessions/{session_id}/interrupt")
async def interrupt_session(session_id: str) -> dict:
    """Interrupt a running CLI process and clear the message queue."""
    if not _send_enabled:
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Start server with --enable-send to enable.",
        )

    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if info.process is None:
        raise HTTPException(
            status_code=409, detail="No process running for this session"
        )

    # Clear the queue first
    info.message_queue.clear()

    # Terminate the process
    try:
        info.process.terminate()
        # Give it a moment to terminate gracefully
        try:
            await asyncio.wait_for(info.process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            # Force kill if it doesn't terminate
            info.process.kill()
    except ProcessLookupError:
        # Process already terminated
        pass

    info.process = None
    await broadcast_session_status(session_id)

    return {"status": "interrupted", "session_id": session_id}


@app.post("/sessions/{session_id}/summarize")
async def trigger_summary(session_id: str, background_tasks: BackgroundTasks) -> dict:
    """Manually trigger summarization for a session.

    This endpoint allows users to request an on-demand summary of a session,
    useful when automatic idle summarization is disabled or when a user wants
    an immediate summary.
    """
    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if _summarizer is None:
        raise HTTPException(
            status_code=503,
            detail="Summarization is not configured. Start server with summarization options.",
        )

    # Run summarization in background task to not block the response
    async def run_summary():
        try:
            success = await _summarize_session_async(info, model=_idle_summary_model)
            if success:
                logger.info(f"Manual summary triggered successfully for {session_id}")
            else:
                logger.warning(f"Manual summary failed for {session_id}")
        except Exception as e:
            logger.error(f"Error during manual summary for {session_id}: {e}")

    background_tasks.add_task(run_summary)

    return {"status": "summarizing", "session_id": session_id}


@app.post("/sessions/new")
async def create_new_session(request: NewSessionRequest) -> dict:
    """Start a new session with an initial message."""
    if not _send_enabled:
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Start server with --enable-send to enable.",
        )

    backend = get_server_backend()

    # For multi-backend mode, get the specific backend if requested
    # Fallback order: request.backend -> _default_send_backend -> first available
    target_backend = backend
    requested_backend = request.backend or _default_send_backend
    if requested_backend:
        # Check if this is a MultiBackend with get_backend_by_name method
        get_by_name = getattr(backend, "get_backend_by_name", None)
        if get_by_name is not None:
            specific_backend = get_by_name(requested_backend)
            if specific_backend is not None:
                target_backend = specific_backend
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown backend: {requested_backend}",
                )

    # Check if CLI is available
    if not target_backend.is_cli_available():
        raise HTTPException(
            status_code=503,
            detail=f"CLI not found. {target_backend.get_cli_install_instructions()}",
        )

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Determine working directory - create if it doesn't exist
    cwd: Path | None = None
    if request.cwd:
        potential_cwd = Path(request.cwd)
        if not potential_cwd.exists():
            try:
                potential_cwd.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created directory: {potential_cwd}")
            except OSError as e:
                raise HTTPException(
                    status_code=400, detail=f"Cannot create directory: {e}"
                )
        if potential_cwd.is_dir():
            cwd = potential_cwd

    # Build command using target backend
    # Check if backend supports model parameter (OpenCode does, Claude Code doesn't)
    build_cmd = target_backend.build_new_session_command
    sig = inspect.signature(build_cmd)

    model: str | None = None
    if "model" in sig.parameters and request.model_index is not None:
        # Look up model from cached list by index
        normalized_backend = _normalize_backend_name(target_backend.name)
        cached = _cached_models.get(normalized_backend, [])
        if 0 <= request.model_index < len(cached):
            model = cached[request.model_index]
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid model_index: {request.model_index}. "
                f"Fetch models from /backends/{target_backend.name}/models first.",
            )

    if model:
        cmd_args = build_cmd(message, _skip_permissions, model=model)
    else:
        cmd_args = build_cmd(message, _skip_permissions)

    try:
        # Start CLI in the working directory
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        # Don't wait for completion - the session will appear via file watcher
        # Just check it started okay
        await asyncio.sleep(0.5)
        if proc.returncode is not None and proc.returncode != 0:
            stderr = await proc.stderr.read() if proc.stderr else b""
            logger.error(f"CLI failed to start: {stderr.decode()}")
            raise HTTPException(status_code=500, detail="Failed to start session")

        # Store process so we can attach it to the session when it appears
        # Use resolved cwd path as key (or empty string for no cwd)
        cwd_key = str(cwd.resolve()) if cwd else ""
        _pending_new_session_processes[cwd_key] = proc
        logger.debug(f"Stored pending process for cwd: {cwd_key}")

        return {"status": "started", "cwd": str(cwd) if cwd else None}

    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="CLI not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting new session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _get_directory_structure(rootdir: Path, shallow: bool = False) -> dict:
    """
    Creates a nested dictionary that represents the folder structure of rootdir.
    """
    dir_name = rootdir.name

    # Base structure
    item = {
        "name": dir_name,
        "path": str(rootdir.resolve()),
        "type": "directory",
        "children": [],
    }

    try:
        # Sort directories first, then files
        # Use simple heuristics for sorting
        entries = list(os.scandir(rootdir))
        entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))

        for entry in entries:
            # Skip hidden files/dirs and common ignored dirs
            if entry.name.startswith("."):
                continue
            if entry.name in [
                "__pycache__",
                "node_modules",
                "venv",
                "env",
                "dist",
                "build",
                "coverage",
                "target",
                "egg-info",
            ]:
                continue

            if entry.is_dir(follow_symlinks=False):
                if shallow:
                    # For shallow listing, we just indicate it's a directory
                    # We don't recurse.
                    item["children"].append(
                        {
                            "name": entry.name,
                            "path": str(Path(entry.path).resolve()),
                            "type": "directory",
                            "has_children": True,
                        }
                    )
                else:
                    item["children"].append(
                        _get_directory_structure(Path(entry.path), shallow=False)
                    )
            else:
                item["children"].append(
                    {
                        "name": entry.name,
                        "path": str(Path(entry.path).resolve()),
                        "type": "file",
                    }
                )
    except (PermissionError, OSError):
        pass

    return item


@app.get("/sessions/{session_id}/tree")
async def get_session_file_tree(session_id: str, path: str | None = None) -> dict:
    """Get the file tree for a session's working directory or specific path.

    Args:
        session_id: The session ID
        path: Optional absolute path to list. If None, uses session's project path.
    """
    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    target_path = None

    if path:
        # User requested specific path - expand ~ to home directory
        if path.startswith("~"):
            path = str(Path.home() / path[2:])
        target_path = Path(path).resolve()

        # Security: Ensure it's within home directory
        # (Relaxed from project_path restriction to allow home navigation)
        try:
            target_path.relative_to(Path.home())
        except ValueError:
            # Just log for now, allow system access if local
            pass
    else:
        # Default to project path
        if not info.project_path:
            return {"tree": None, "error": "No project path set for this session"}
        target_path = Path(info.project_path)

    if not target_path.exists() or not target_path.is_dir():
        return {
            "tree": None,
            "error": f"Path does not exist: {target_path}",
        }

    try:
        # Use shallow listing for navigation efficiency
        tree = _get_directory_structure(target_path, shallow=True)
        # Include project root for relative path calculations
        project_root = info.project_path if info.project_path else None
        return {"tree": tree, "home": str(Path.home()), "projectRoot": project_root}
    except Exception as e:
        logger.error(f"Error generating file tree for {session_id}: {e}")
        return {"tree": None, "error": str(e)}


@app.get("/api/file")
async def get_file(path: str) -> FileResponse:
    """Fetch file contents for preview.

    Args:
        path: Absolute path to the file to preview.

    Returns:
        FileResponse with content, metadata, and language detection.

    Raises:
        HTTPException: 404 if file not found, 400 if binary or not a file,
                      403 if permission denied, 500 for other errors.
    """
    file_path = Path(path)

    # Security: Restrict to user's home directory to prevent path traversal
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        # Check if resolved path is within home directory
        resolved_path.relative_to(home_dir)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within home directory ({home_dir})",
        )

    # Validate path exists
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Ensure it's a file, not directory
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    # Check file size
    try:
        file_size = file_path.stat().st_size
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Cannot stat file: {e}")

    truncated = file_size > MAX_FILE_SIZE

    # Detect language from extension
    extension = file_path.suffix.lower()
    language = EXTENSION_TO_LANGUAGE.get(extension)

    # Special case: Makefile, Dockerfile without extension
    if file_path.name.lower() == "makefile":
        language = "makefile"
    elif file_path.name.lower() == "dockerfile":
        language = "dockerfile"

    try:
        # Read file content - only read up to MAX_FILE_SIZE bytes to prevent
        # memory exhaustion on large files
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(MAX_FILE_SIZE + 1)  # Read one extra to detect truncation

        # If we read more than MAX_FILE_SIZE, file is truncated
        if len(content) > MAX_FILE_SIZE:
            content = content[:MAX_FILE_SIZE]
            truncated = True

        # Check for binary content (null bytes indicate binary)
        if "\x00" in content[:8192]:
            raise HTTPException(
                status_code=400, detail="Binary file cannot be displayed"
            )

        # Render markdown to HTML if it's a markdown file
        # Use safe=True to escape raw HTML and prevent XSS attacks
        rendered_html = None
        if language == "markdown":
            from .backends.shared.rendering import render_markdown_text

            rendered_html = render_markdown_text(content, safe=True)

        return FileResponse(
            content=content,
            path=str(file_path.absolute()),
            filename=file_path.name,
            size=file_size,
            language=language,
            truncated=truncated,
            rendered_html=rendered_html,
        )

    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Binary file cannot be displayed")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading file {path}: {e}")
        raise HTTPException(status_code=500, detail=f"Error reading file: {e}")


@app.get("/api/file/raw")
async def get_file_raw(path: str) -> Response:
    """Serve raw file bytes with appropriate Content-Type.

    Primarily used for serving images in the file preview pane.

    Args:
        path: Absolute path to the file to serve.

    Returns:
        Raw file bytes with Content-Type header.

    Raises:
        HTTPException: 404 if file not found, 403 if permission denied.
    """
    file_path = Path(path)

    # Security: Restrict to user's home directory to prevent path traversal
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(home_dir)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within home directory ({home_dir})",
        )

    # Validate path exists
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Ensure it's a file, not directory
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    # Determine content type from extension
    extension = file_path.suffix.lower()
    content_type = IMAGE_EXTENSIONS.get(extension, "application/octet-stream")

    try:
        with open(file_path, "rb") as f:
            content = f.read()

        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Cache-Control": "private, max-age=3600",  # Cache for 1 hour
            },
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    except Exception as e:
        logger.error(f"Error reading file {path}: {e}")
        raise HTTPException(status_code=500, detail=f"Error reading file: {e}")


class DeleteFileRequest(BaseModel):
    """Request body for file deletion."""

    path: str


class DeleteFileResponse(BaseModel):
    """Response for file deletion."""

    success: bool
    error: str | None = None


@app.post("/api/file/delete")
async def delete_file(request: DeleteFileRequest) -> DeleteFileResponse:
    """Delete a file or empty directory.

    Args:
        request: DeleteFileRequest containing the path to delete.

    Returns:
        DeleteFileResponse indicating success or failure.
    """
    file_path = Path(request.path)

    # Security: Restrict to user's home directory to prevent path traversal
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(home_dir)
    except ValueError:
        return DeleteFileResponse(
            success=False,
            error=f"Access denied: path must be within home directory ({home_dir})",
        )

    # Validate path exists
    if not file_path.exists():
        return DeleteFileResponse(success=False, error=f"File not found: {request.path}")

    try:
        if file_path.is_file():
            file_path.unlink()
            logger.info(f"Deleted file: {request.path}")
        elif file_path.is_dir():
            # Only delete empty directories for safety
            if any(file_path.iterdir()):
                return DeleteFileResponse(
                    success=False, error="Directory is not empty"
                )
            file_path.rmdir()
            logger.info(f"Deleted directory: {request.path}")
        else:
            return DeleteFileResponse(
                success=False, error="Path is neither a file nor directory"
            )

        return DeleteFileResponse(success=True)
    except PermissionError:
        return DeleteFileResponse(
            success=False, error=f"Permission denied: {request.path}"
        )
    except Exception as e:
        logger.error(f"Error deleting {request.path}: {e}")
        return DeleteFileResponse(success=False, error=str(e))


@app.get("/api/file/download")
async def download_file(path: str) -> Response:
    """Download a file with proper Content-Disposition header.

    Args:
        path: Absolute path to the file to download.

    Returns:
        File bytes with Content-Disposition attachment header.

    Raises:
        HTTPException: 404 if file not found, 403 if permission denied.
    """
    file_path = Path(path)

    # Security: Restrict to user's home directory to prevent path traversal
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(home_dir)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within home directory ({home_dir})",
        )

    # Validate path exists
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Ensure it's a file, not directory
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    try:
        with open(file_path, "rb") as f:
            content = f.read()

        # Determine content type from extension
        extension = file_path.suffix.lower()
        content_type = IMAGE_EXTENSIONS.get(extension, "application/octet-stream")

        # Use filename for Content-Disposition
        filename = file_path.name

        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    except Exception as e:
        logger.error(f"Error downloading file {path}: {e}")
        raise HTTPException(status_code=500, detail=f"Error downloading file: {e}")


class UploadFileResponse(BaseModel):
    """Response for file upload."""

    success: bool
    path: str | None = None
    error: str | None = None


@app.post("/api/file/upload")
async def upload_file(
    request: Request, directory: str, filename: str
) -> UploadFileResponse:
    """Upload a file to a directory.

    Args:
        request: The request with file content in body.
        directory: Target directory path.
        filename: Name for the uploaded file.

    Returns:
        UploadFileResponse indicating success or failure.
    """
    dir_path = Path(directory)

    # Security: Restrict to user's home directory
    home_dir = Path.home()
    try:
        resolved_dir = dir_path.resolve()
        resolved_dir.relative_to(home_dir)
    except ValueError:
        return UploadFileResponse(
            success=False,
            error=f"Access denied: directory must be within home directory ({home_dir})",
        )

    # Validate directory exists
    if not dir_path.exists():
        return UploadFileResponse(
            success=False, error=f"Directory not found: {directory}"
        )

    if not dir_path.is_dir():
        return UploadFileResponse(
            success=False, error=f"Not a directory: {directory}"
        )

    # Sanitize filename (prevent path traversal in filename)
    safe_filename = Path(filename).name
    if not safe_filename or safe_filename in (".", ".."):
        return UploadFileResponse(success=False, error="Invalid filename")

    target_path = dir_path / safe_filename

    try:
        # Read file content from request body
        content = await request.body()

        # Write file
        with open(target_path, "wb") as f:
            f.write(content)

        logger.info(f"Uploaded file: {target_path}")
        return UploadFileResponse(success=True, path=str(target_path))

    except PermissionError:
        return UploadFileResponse(
            success=False, error=f"Permission denied: {target_path}"
        )
    except Exception as e:
        logger.error(f"Error uploading file to {target_path}: {e}")
        return UploadFileResponse(success=False, error=str(e))


class PathTypeResponse(BaseModel):
    """Response for path type check."""

    type: str  # "file" or "directory"


@app.get("/api/path/type")
async def check_path_type(path: str) -> PathTypeResponse:
    """Check if a path exists and return its type (lightweight, no content fetching).

    Args:
        path: Absolute path to check. Supports ~ for home directory.

    Returns:
        PathTypeResponse with type "file" or "directory".

    Raises:
        HTTPException: 404 if path not found or outside home directory.
    """
    # Expand ~ to home directory
    if path.startswith("~"):
        path = str(Path.home() / path[2:])

    file_path = Path(path)

    # Security: Restrict to user's home directory
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(home_dir)
    except ValueError:
        # Outside home directory - return not found
        raise HTTPException(status_code=404)

    try:
        if file_path.exists():
            if file_path.is_file():
                return PathTypeResponse(type="file")
            elif file_path.is_dir():
                return PathTypeResponse(type="directory")
        raise HTTPException(status_code=404)
    except HTTPException:
        raise
    except (PermissionError, OSError):
        raise HTTPException(status_code=404)


# File watch SSE endpoint for live file updates
async def _file_watch_generator(
    file_path: Path, request: Request, follow: bool = True
) -> AsyncGenerator[dict, None]:
    """Generate SSE events for file changes using tail-style heuristic.

    Args:
        file_path: Path to the file to watch.
        request: The request object to check for disconnection.
        follow: If True, detect appends and send only new bytes (for tailing logs).
                If False, always send full file content on any change.

    Events:
    - initial: Full file content on connect
    - append: New content appended to file (size increased) - only when follow=True
    - replace: Full file content (truncation, rewrite, or in-place edit)
    - error: File deleted, permission denied, etc.
    """
    try:
        # Get initial file state
        stat = file_path.stat()
        last_size = stat.st_size
        last_inode = stat.st_ino

        # Send initial content
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(MAX_FILE_SIZE)

        # Check for binary content
        if "\x00" in content[:8192]:
            yield {
                "event": "error",
                "data": json.dumps({"message": "Binary file cannot be displayed"}),
            }
            return

        yield {
            "event": "initial",
            "data": json.dumps(
                {
                    "content": content,
                    "size": last_size,
                    "inode": last_inode,
                    "truncated": last_size > MAX_FILE_SIZE,
                }
            ),
        }

        # Watch for changes with debouncing to batch rapid updates
        # 100ms debounce prevents overwhelming the client with rapid file changes
        async for changes in watchfiles.awatch(file_path, debounce=100):
            # Check if client disconnected
            if await request.is_disconnected():
                logger.debug(f"Client disconnected, stopping file watch for {file_path}")
                return

            try:
                stat = file_path.stat()
                new_size = stat.st_size
                new_inode = stat.st_ino

                # When follow=false, just notify of change - frontend will refetch via /api/file
                # This allows the existing endpoint to handle markdown rendering, etc.
                if not follow:
                    yield {
                        "event": "changed",
                        "data": json.dumps({"size": new_size, "inode": new_inode}),
                    }
                # When follow=true, use tail-style heuristic for efficient append detection
                elif new_inode != last_inode:
                    # File replaced (different inode) - send full content
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(MAX_FILE_SIZE)
                    yield {
                        "event": "replace",
                        "data": json.dumps(
                            {
                                "content": content,
                                "size": new_size,
                                "inode": new_inode,
                                "truncated": new_size > MAX_FILE_SIZE,
                            }
                        ),
                    }
                elif new_size > last_size:
                    # File grew - likely append, read only new bytes
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_size)
                        new_content = f.read(MAX_FILE_SIZE)
                    yield {
                        "event": "append",
                        "data": json.dumps(
                            {
                                "content": new_content,
                                "offset": last_size,
                            }
                        ),
                    }
                elif new_size < last_size:
                    # File truncated - send full content
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(MAX_FILE_SIZE)
                    yield {
                        "event": "replace",
                        "data": json.dumps(
                            {
                                "content": content,
                                "size": new_size,
                                "inode": new_inode,
                                "truncated": new_size > MAX_FILE_SIZE,
                            }
                        ),
                    }
                else:
                    # Same size but modified - in-place edit, send full content
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(MAX_FILE_SIZE)
                    yield {
                        "event": "replace",
                        "data": json.dumps(
                            {
                                "content": content,
                                "size": new_size,
                                "inode": new_inode,
                                "truncated": new_size > MAX_FILE_SIZE,
                            }
                        ),
                    }

                last_size = new_size
                last_inode = new_inode

            except FileNotFoundError:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "File was deleted"}),
                }
                return
            except PermissionError:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "Permission denied"}),
                }
                return

    except FileNotFoundError:
        yield {
            "event": "error",
            "data": json.dumps({"message": "File not found"}),
        }
    except PermissionError:
        yield {
            "event": "error",
            "data": json.dumps({"message": "Permission denied"}),
        }
    except Exception as e:
        logger.error(f"Error in file watch for {file_path}: {e}")
        yield {
            "event": "error",
            "data": json.dumps({"message": f"Error: {e}"}),
        }


@app.get("/api/file/watch")
async def watch_file(path: str, request: Request, follow: bool = True) -> EventSourceResponse:
    """SSE endpoint for live file updates.

    Uses tail-style heuristic to efficiently detect appends vs full rewrites:
    - Tracks file size and inode
    - If size increases and follow=True: assume append, send only new bytes
    - If size decreases or inode changes: send full content
    - If follow=False: always send full content on any change

    Args:
        path: Absolute path to the file to watch.
        follow: If True (default), detect appends and send only new bytes.
                If False, always send full file content on any change.

    Returns:
        EventSourceResponse streaming file changes.

    Events:
        - initial: {content, size, inode, truncated} - Full file on connect
        - append: {content, offset} - New content (file grew, only when follow=True)
        - replace: {content, size, inode, truncated} - Full content (truncation/rewrite)
        - error: {message} - File deleted, permission denied, etc.
    """
    file_path = Path(path)

    # Security: Restrict to user's home directory
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(home_dir)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within home directory ({home_dir})",
        )

    # Validate path exists and is a file
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    return EventSourceResponse(_file_watch_generator(file_path, request, follow=follow))
