"""SSE broadcasting utilities for sending events to connected clients."""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sessions import SessionInfo

logger = logging.getLogger(__name__)

# Global state for connected clients
_clients: set[asyncio.Queue] = set()


def get_clients() -> set[asyncio.Queue]:
    """Get the set of connected client queues."""
    return _clients


def add_client(queue: asyncio.Queue) -> None:
    """Add a client queue to the set."""
    _clients.add(queue)


def remove_client(queue: asyncio.Queue) -> None:
    """Remove a client queue from the set."""
    _clients.discard(queue)


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


async def broadcast_session_added(info: "SessionInfo") -> None:
    """Broadcast that a new session was added."""
    await broadcast_event("session_added", info.to_dict())


async def broadcast_session_catchup(
    info: "SessionInfo", get_renderer_for_session
) -> None:
    """Broadcast existing messages for a newly added session.

    When a session is added while clients are already connected, those clients
    need to receive the existing messages (catchup) for that session.

    Args:
        info: The session info.
        get_renderer_for_session: Function to get renderer for session path.
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


async def broadcast_session_summary_updated(
    session_id: str, get_session
) -> None:
    """Broadcast that a session's summary data has been updated.

    Args:
        session_id: The session ID.
        get_session: Function to get session by ID.
    """
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


async def broadcast_session_status(session_id: str, get_session) -> None:
    """Broadcast session status (running state, queue size, waiting state).

    Args:
        session_id: The session ID.
        get_session: Function to get session by ID.
    """
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


async def broadcast_permission_denied(
    session_id: str, denials: list[dict], original_message: str
) -> None:
    """Broadcast permission denial event to clients.

    Args:
        session_id: The session where permission was denied
        denials: List of permission denial dicts from CLI output
        original_message: The original message that triggered the denial
    """
    await broadcast_event(
        "permission_denied",
        {
            "session_id": session_id,
            "denials": denials,
            "original_message": original_message,
        },
    )
