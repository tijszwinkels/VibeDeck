"""FastAPI server with SSE endpoint for live transcript updates."""

import asyncio
import json
import logging
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import watchfiles
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .rendering import CSS, render_message, get_template
from .sessions import (
    MAX_SESSIONS,
    SessionInfo,
    add_session,
    get_known_session_files,
    get_projects_dir,
    get_session,
    get_sessions,
    get_sessions_list,
    get_sessions_lock,
    remove_session,
    session_count,
)
from .tailer import find_recent_sessions, get_session_id

logger = logging.getLogger(__name__)

# Configuration
CATCHUP_TIMEOUT = 30  # seconds - max time for catchup before telling client to reinitialize
_send_enabled = False  # Enable with --enable-send CLI flag
_skip_permissions = False  # Enable with --dangerously-skip-permissions CLI flag

# Global state for server (not session-related)
_clients: set[asyncio.Queue] = set()
_watch_task: asyncio.Task | None = None


def set_send_enabled(enabled: bool) -> None:
    """Set whether sending messages to Claude is enabled."""
    global _send_enabled
    _send_enabled = enabled


def set_skip_permissions(enabled: bool) -> None:
    """Set whether to skip permission prompts when running Claude."""
    global _skip_permissions
    _skip_permissions = enabled


def is_send_enabled() -> bool:
    """Check if sending messages is enabled."""
    return _send_enabled


class SendMessageRequest(BaseModel):
    """Request body for sending a message to a session."""
    message: str


class NewSessionRequest(BaseModel):
    """Request body for starting a new session."""
    message: str  # Initial message to send (required)
    cwd: str | None = None  # Working directory (optional)


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
    await broadcast_event("message", {
        "type": "html",
        "content": html,
        "session_id": session_id,
    })


async def broadcast_session_added(info: SessionInfo) -> None:
    """Broadcast that a new session was added."""
    await broadcast_event("session_added", info.to_dict())


async def broadcast_session_catchup(info: SessionInfo) -> None:
    """Broadcast existing messages for a newly added session.

    When a session is added while clients are already connected, those clients
    need to receive the existing messages (catchup) for that session.
    """
    existing = info.tailer.read_all()
    for entry in existing:
        html = render_message(entry)
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
    await broadcast_event("session_status", {
        "session_id": session_id,
        "running": info.process is not None,
        "queued_messages": len(info.message_queue),
        "waiting_for_input": info.tailer.waiting_for_input,
    })


async def run_claude_for_session(session_id: str, message: str) -> None:
    """Send a message to a Claude Code session and track the process."""
    info = get_session(session_id)
    if info is None:
        logger.error(f"Session not found: {session_id}")
        return

    try:
        # Ensure session is in Claude's session index so --resume works.
        # Claude uses ~/.claude/session-env/<session-id>/ directories as its index.
        # Sessions created externally (via -p mode) don't get indexed automatically.
        session_env_dir = Path.home() / ".claude" / "session-env" / session_id
        session_env_dir.mkdir(parents=True, exist_ok=True)

        # Build command arguments
        cmd_args = ["claude", "-p", message, "--resume", session_id]
        if _skip_permissions:
            cmd_args.append("--dangerously-skip-permissions")

        # Determine working directory from session info
        cwd = info.project_path if info.project_path and Path(info.project_path).is_dir() else None

        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        info.process = proc
        await broadcast_session_status(session_id)

        # Wait for completion
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.error(f"Claude process failed for {session_id}: {stderr.decode()}")

    except Exception as e:
        logger.error(f"Error running Claude for {session_id}: {e}")

    finally:
        info.process = None

        # Process queue if messages waiting
        if info.message_queue:
            next_message = info.message_queue.pop(0)
            asyncio.create_task(run_claude_for_session(session_id, next_message))

        await broadcast_session_status(session_id)


async def process_session_messages(session_id: str) -> None:
    """Read new messages from a session and broadcast to clients."""
    info = get_session(session_id)
    if info is None:
        return

    new_entries = info.tailer.read_new_lines()
    for entry in new_entries:
        html = render_message(entry)
        if html:
            await broadcast_message(session_id, html)

    # Broadcast updated waiting state after processing messages
    if new_entries:
        await broadcast_session_status(session_id)


async def check_for_new_sessions() -> None:
    """Check for new session files and add them."""
    projects_dir = get_projects_dir()
    if not projects_dir.exists():
        return

    # Find all session files
    for f in projects_dir.glob("**/*.jsonl"):
        if f.name.startswith("agent-"):
            continue
        if f not in get_known_session_files():
            async with get_sessions_lock():
                info, evicted_id = add_session(f)
                if evicted_id:
                    await broadcast_session_removed(evicted_id)
                if info:
                    await broadcast_session_added(info)
                    await broadcast_session_catchup(info)


async def watch_loop() -> None:
    """Background task that watches for file changes."""
    projects_dir = get_projects_dir()

    if not projects_dir.exists():
        logger.warning(f"Projects directory not found: {projects_dir}")
        return

    logger.info(f"Starting watch loop for {projects_dir}")

    try:
        async for changes in watchfiles.awatch(projects_dir):
            for change_type, changed_path in changes:
                changed_path = Path(changed_path)

                # Skip non-jsonl files and agent files
                if not changed_path.suffix == ".jsonl":
                    continue
                if changed_path.name.startswith("agent-"):
                    continue

                async with get_sessions_lock():
                    if change_type == watchfiles.Change.added:
                        # New session file
                        info, evicted_id = add_session(changed_path)
                        if evicted_id:
                            await broadcast_session_removed(evicted_id)
                        if info:
                            await broadcast_session_added(info)
                            await broadcast_session_catchup(info)

                    elif change_type == watchfiles.Change.modified:
                        # Existing file modified
                        session_id = get_session_id(changed_path)
                        if get_session(session_id) is not None:
                            await process_session_messages(session_id)
                        elif changed_path not in get_known_session_files():
                            # File we haven't seen - might be new
                            info, evicted_id = add_session(changed_path)
                            if evicted_id:
                                await broadcast_session_removed(evicted_id)
                            if info:
                                await broadcast_session_added(info)
                                await broadcast_session_catchup(info)

    except asyncio.CancelledError:
        logger.info("Watch loop cancelled")
        raise
    except Exception as e:
        logger.error(f"Watch loop error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage server lifecycle - start/stop file watcher."""
    global _watch_task

    # Startup: find recent sessions
    recent = find_recent_sessions(get_projects_dir(), limit=MAX_SESSIONS)
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
    template = get_template("live.html")
    html = template.render(css=CSS)
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
                existing = info.tailer.read_all()
                for entry in existing:
                    # Check if catchup is taking too long (slow client)
                    if time.monotonic() - catchup_start > CATCHUP_TIMEOUT:
                        catchup_timed_out = True
                        break
                    html = render_message(entry)
                    if html:
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "type": "html",
                                "content": html,
                                "session_id": session_id,
                            }),
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
    """Send a message to a Claude Code session."""
    if not _send_enabled:
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Start server with --enable-send to enable.",
        )

    # Check if Claude CLI is available
    if shutil.which("claude") is None:
        raise HTTPException(
            status_code=503,
            detail="Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code",
        )

    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

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

    # Start the Claude process
    asyncio.create_task(run_claude_for_session(session_id, message))

    return {"status": "sent", "session_id": session_id}


@app.post("/sessions/{session_id}/interrupt")
async def interrupt_session(session_id: str) -> dict:
    """Interrupt a running Claude process and clear the message queue."""
    if not _send_enabled:
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Start server with --enable-send to enable.",
        )

    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if info.process is None:
        raise HTTPException(status_code=409, detail="No process running for this session")

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
    """Start a new Claude session with an initial message."""
    if not _send_enabled:
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Start server with --enable-send to enable.",
        )

    # Check if Claude CLI is available
    if shutil.which("claude") is None:
        raise HTTPException(
            status_code=503,
            detail="Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code",
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
                raise HTTPException(status_code=400, detail=f"Cannot create directory: {e}")
        if potential_cwd.is_dir():
            cwd = potential_cwd

    # Build command with the initial message
    cmd_args = ["claude", "-p", message]
    if _skip_permissions:
        cmd_args.append("--dangerously-skip-permissions")

    try:
        # Start Claude in the working directory
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        # Don't wait for completion - the session will appear via file watcher
        # Just check it started okay
        await asyncio.sleep(0.5)
        if proc.returncode is not None and proc.returncode != 0:
            stderr = await proc.stderr.read() if proc.stderr else b""
            logger.error(f"Claude failed to start: {stderr.decode()}")
            raise HTTPException(status_code=500, detail="Failed to start Claude session")

        return {"status": "started", "cwd": str(cwd) if cwd else None}

    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Claude CLI not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting new session: {e}")
        raise HTTPException(status_code=500, detail=str(e))
