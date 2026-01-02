"""FastAPI server with SSE endpoint for live transcript updates."""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator

import watchfiles
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from .rendering import CSS, render_message, get_template
from .tailer import (
    SessionTailer,
    find_recent_sessions,
    get_session_id,
    get_session_name,
)

logger = logging.getLogger(__name__)

# Configuration
MAX_SESSIONS = 10
CATCHUP_TIMEOUT = 30  # seconds - max time for catchup before telling client to reinitialize


@dataclass
class SessionInfo:
    """Information about a tracked session."""

    path: Path
    tailer: SessionTailer
    name: str = ""
    session_id: str = ""

    def __post_init__(self):
        if not self.name:
            self.name = get_session_name(self.path)
        if not self.session_id:
            self.session_id = get_session_id(self.path)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        # Get timestamps
        started_at = self.tailer.get_first_timestamp()
        try:
            last_updated = self.path.stat().st_mtime
        except OSError:
            last_updated = None

        return {
            "id": self.session_id,
            "name": self.name,
            "path": str(self.path),
            "startedAt": started_at,
            "lastUpdatedAt": last_updated,
        }


# Global state
_sessions: dict[str, SessionInfo] = {}  # session_id -> SessionInfo
_sessions_lock: asyncio.Lock | None = None  # Protects _sessions during iteration
_clients: set[asyncio.Queue] = set()
_watch_task: asyncio.Task | None = None
_projects_dir: Path | None = None
_known_session_files: set[Path] = set()  # Track known files to detect new ones


def _get_sessions_lock() -> asyncio.Lock:
    """Get or create the sessions lock (must be created in event loop context)."""
    global _sessions_lock
    if _sessions_lock is None:
        _sessions_lock = asyncio.Lock()
    return _sessions_lock


def get_projects_dir() -> Path:
    """Get the projects directory path."""
    global _projects_dir
    if _projects_dir is None:
        _projects_dir = Path.home() / ".claude" / "projects"
    return _projects_dir


def set_projects_dir(path: Path) -> None:
    """Set the projects directory path (for testing)."""
    global _projects_dir
    _projects_dir = path


def get_oldest_session_id() -> str | None:
    """Find the oldest session by modification time."""
    if not _sessions:
        return None
    oldest = min(
        _sessions.items(),
        key=lambda x: x[1].path.stat().st_mtime if x[1].path.exists() else float("inf"),
    )
    return oldest[0]


def add_session(path: Path, evict_oldest: bool = True) -> tuple[SessionInfo | None, str | None]:
    """Add a session to track.

    Returns a tuple of (SessionInfo if added, evicted_session_id if one was removed).
    Returns (None, None) if already tracked.
    If at the session limit and evict_oldest=True, removes the oldest session to make room.
    """
    session_id = get_session_id(path)

    if session_id in _sessions:
        return None, None

    evicted_id = None
    # If at limit, remove the oldest session to make room
    if len(_sessions) >= MAX_SESSIONS:
        if evict_oldest:
            oldest_id = get_oldest_session_id()
            if oldest_id:
                logger.info(f"Session limit reached, removing oldest: {oldest_id}")
                remove_session(oldest_id)
                evicted_id = oldest_id
        else:
            logger.debug(f"Session limit reached, not adding {path}")
            return None, None

    tailer = SessionTailer(path)
    # Advance tailer position to end of file so process_session_messages
    # only picks up truly new messages (catchup uses read_all with fresh tailer)
    tailer.read_new_lines()
    info = SessionInfo(path=path, tailer=tailer)
    _sessions[session_id] = info
    _known_session_files.add(path)
    logger.info(f"Added session: {info.name} ({session_id})")
    return info, evicted_id


def remove_session(session_id: str) -> bool:
    """Remove a session from tracking."""
    if session_id in _sessions:
        info = _sessions.pop(session_id)
        _known_session_files.discard(info.path)
        logger.info(f"Removed session: {info.name} ({session_id})")
        return True
    return False


def get_sessions_list() -> list[dict]:
    """Get list of all tracked sessions, sorted by modification time (newest first)."""
    # Sort by file modification time, newest first
    sorted_sessions = sorted(
        _sessions.values(),
        key=lambda info: info.path.stat().st_mtime if info.path.exists() else 0,
        reverse=True,
    )
    return [info.to_dict() for info in sorted_sessions]


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


async def broadcast_session_removed(session_id: str) -> None:
    """Broadcast that a session was removed."""
    await broadcast_event("session_removed", {"id": session_id})


async def process_session_messages(session_id: str) -> None:
    """Read new messages from a session and broadcast to clients."""
    info = _sessions.get(session_id)
    if info is None:
        return

    new_entries = info.tailer.read_new_lines()
    for entry in new_entries:
        html = render_message(entry)
        if html:
            await broadcast_message(session_id, html)


async def check_for_new_sessions() -> None:
    """Check for new session files and add them."""
    projects_dir = get_projects_dir()
    if not projects_dir.exists():
        return

    # Find all session files
    for f in projects_dir.glob("**/*.jsonl"):
        if f.name.startswith("agent-"):
            continue
        if f not in _known_session_files:
            async with _get_sessions_lock():
                info, evicted_id = add_session(f)
                if evicted_id:
                    await broadcast_session_removed(evicted_id)
                if info:
                    await broadcast_session_added(info)


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

                async with _get_sessions_lock():
                    if change_type == watchfiles.Change.added:
                        # New session file
                        info, evicted_id = add_session(changed_path)
                        if evicted_id:
                            await broadcast_session_removed(evicted_id)
                        if info:
                            await broadcast_session_added(info)

                    elif change_type == watchfiles.Change.modified:
                        # Existing file modified
                        session_id = get_session_id(changed_path)
                        if session_id in _sessions:
                            await process_session_messages(session_id)
                        elif changed_path not in _known_session_files:
                            # File we haven't seen - might be new
                            info, evicted_id = add_session(changed_path)
                            if evicted_id:
                                await broadcast_session_removed(evicted_id)
                            if info:
                                await broadcast_session_added(info)

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
    for path in recent:
        add_session(path, evict_oldest=False)  # No eviction needed at startup

    if not _sessions:
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


app = FastAPI(title="Claude Code Live", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Serve the main live transcript page."""
    template = get_template("live.html")
    html = template.render(css=CSS)
    return HTMLResponse(content=html)


@app.get("/sessions")
async def list_sessions() -> dict:
    """List all tracked sessions."""
    return {"sessions": get_sessions_list()}


async def event_generator(request: Request) -> AsyncGenerator[dict, None]:
    """Generate SSE events for a client."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _clients.add(queue)

    try:
        # Send sessions list
        yield {
            "event": "sessions",
            "data": json.dumps({"sessions": get_sessions_list()}),
        }

        # Send existing messages for each session (catchup)
        # Hold lock to prevent _sessions modification during iteration
        catchup_start = time.monotonic()
        catchup_timed_out = False

        async with _get_sessions_lock():
            for session_id, info in _sessions.items():
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
        "sessions": len(_sessions),
        "clients": len(_clients),
    }


# Legacy single-session API for backwards compatibility
def set_session_path(path: Path) -> None:
    """Set a single session file to watch (legacy API)."""
    add_session(path, evict_oldest=False)
