"""FastAPI server with SSE endpoint for live transcript updates."""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import watchfiles
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .backends import CodingToolBackend, get_backend, get_multi_backend
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

# Global state for server (not session-related)
_clients: set[asyncio.Queue] = set()
_watch_task: asyncio.Task | None = None

# Backend and rendering
_backend: CodingToolBackend | None = None
_css: str | None = None


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


def is_send_enabled() -> bool:
    """Check if sending messages is enabled."""
    return _send_enabled


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


class NewSessionRequest(BaseModel):
    """Request body for starting a new session."""

    message: str  # Initial message to send (required)
    cwd: str | None = None  # Working directory (optional)
    backend: str | None = None  # Backend to use (optional, for multi-backend mode)


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

        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=cwd,
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

        if proc.returncode != 0:
            logger.error(f"CLI process failed for {session_id}: {stderr.decode()}")

    except Exception as e:
        logger.error(f"Error running CLI for {session_id}: {e}")

    finally:
        if not fork:
            info.process = None

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
                        await broadcast_session_added(info)
                        await broadcast_session_catchup(info)
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
            need_new_session_check = False

            for change_type, changed_path in changes:
                changed_path = Path(changed_path)

                # Use backend to check if file should be watched
                if not backend.should_watch_file(changed_path):
                    continue

                # Get session ID from the changed file path
                # For Claude Code: session ID is the filename
                # For OpenCode: session ID is extracted from message/part path
                session_id = backend.get_session_id_from_changed_file(changed_path)
                logger.debug(
                    f"File change: {change_type.name} {changed_path.name} -> session {session_id}"
                )

                if session_id and get_session(session_id) is not None:
                    # Known session - queue for message processing
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

    yield

    # Shutdown
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
    cmd_args = target_backend.build_new_session_command(message, _skip_permissions)

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

        return {"status": "started", "cwd": str(cwd) if cwd else None}

    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="CLI not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting new session: {e}")
        raise HTTPException(status_code=500, detail=str(e))
