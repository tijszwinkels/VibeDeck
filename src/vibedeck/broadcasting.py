"""SSE broadcasting utilities for sending events to connected clients."""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .backends.shared.normalizer import NormalizedMessage
    from .sessions import SessionInfo

logger = logging.getLogger(__name__)

# Global state for connected clients
_clients: set[asyncio.Queue] = set()

# Separate client set for JSON SSE
_json_clients: set[asyncio.Queue] = set()


def get_clients() -> set[asyncio.Queue]:
    """Get the set of connected HTML client queues."""
    return _clients


def add_client(queue: asyncio.Queue) -> None:
    """Add an HTML client queue to the set."""
    _clients.add(queue)


def remove_client(queue: asyncio.Queue) -> None:
    """Remove an HTML client queue from the set."""
    _clients.discard(queue)


def get_json_clients() -> set[asyncio.Queue]:
    """Get the set of connected JSON client queues."""
    return _json_clients


def add_json_client(queue: asyncio.Queue) -> None:
    """Add a JSON client queue to the set."""
    _json_clients.add(queue)


def remove_json_client(queue: asyncio.Queue) -> None:
    """Remove a JSON client queue from the set."""
    _json_clients.discard(queue)


def has_json_clients() -> bool:
    """Check if any JSON SSE clients are connected."""
    return len(_json_clients) > 0


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


async def broadcast_json_event(event_type: str, data: dict) -> None:
    """Broadcast an event to all connected JSON SSE clients."""
    dead_clients = []

    for queue in _json_clients:
        try:
            queue.put_nowait({"event": event_type, "data": data})
        except asyncio.QueueFull:
            dead_clients.append(queue)

    for queue in dead_clients:
        _json_clients.discard(queue)


async def broadcast_json_message(session_id: str, message: "NormalizedMessage") -> None:
    """Broadcast a normalized message to JSON SSE clients."""
    await broadcast_json_event(
        "message",
        {"session_id": session_id, "message": message.to_dict()},
    )


async def broadcast_session_added(info: "SessionInfo") -> None:
    """Broadcast that a new session was added (to both HTML and JSON clients)."""
    data = info.to_dict()
    await broadcast_event("session_added", data)
    await broadcast_json_event("session_added", data)


async def broadcast_session_catchup(
    info: "SessionInfo", get_renderer_for_session, get_normalizer_for_session=None
) -> None:
    """Broadcast existing messages for a newly added session.

    When a session is added while clients are already connected, those clients
    need to receive the existing messages (catchup) for that session.

    Args:
        info: The session info.
        get_renderer_for_session: Function to get renderer for session path.
        get_normalizer_for_session: Optional function to get normalizer for session path.
    """
    renderer = get_renderer_for_session(info.path)
    send_json = has_json_clients() and get_normalizer_for_session is not None

    existing = info.tailer.read_all()

    # HTML catchup: send individual messages
    for entry in existing:
        html = renderer.render_message(entry)
        if html:
            await broadcast_message(info.session_id, html)

    # JSON catchup: send all normalized messages as a batch
    if send_json:
        normalizer = get_normalizer_for_session(info.path)
        normalized_messages = []
        for entry in existing:
            try:
                msg = normalizer(entry)
                if msg is not None:
                    normalized_messages.append(msg.to_dict())
            except Exception as e:
                logger.warning(f"Failed to normalize entry for JSON catchup: {e}")
        if normalized_messages:
            await broadcast_json_event(
                "session_catchup",
                {
                    "session_id": info.session_id,
                    "messages": normalized_messages,
                },
            )


async def broadcast_session_removed(session_id: str) -> None:
    """Broadcast that a session was removed (to both HTML and JSON clients)."""
    data = {"id": session_id}
    await broadcast_event("session_removed", data)
    await broadcast_json_event("session_removed", data)


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
    data = {
        "session_id": session_id,
        "summaryTitle": info.summary_title,
        "summaryShort": info.summary_short,
        "summaryExecutive": info.summary_executive,
    }
    await broadcast_event("session_summary_updated", data)
    await broadcast_json_event("session_summary_updated", data)


async def broadcast_session_status(session_id: str, get_session) -> None:
    """Broadcast session status (running state, queue size, waiting state).

    Args:
        session_id: The session ID.
        get_session: Function to get session by ID.
    """
    info = get_session(session_id)
    if info is None:
        return
    data = {
        "session_id": session_id,
        "running": info.process is not None,
        "queued_messages": len(info.message_queue),
        "waiting_for_input": info.tailer.waiting_for_input,
    }
    await broadcast_event("session_status", data)
    await broadcast_json_event("session_status", data)


async def broadcast_session_token_usage_updated(
    session_id: str, get_session, get_backend
) -> None:
    """Broadcast that a session's token usage has been updated.

    Args:
        session_id: The session ID.
        get_session: Function to get session by ID.
        get_backend: Function to get the current backend.
    """
    info = get_session(session_id)
    if info is None:
        return

    backend = get_backend()
    if backend is None:
        return

    try:
        usage = backend.get_session_token_usage(info.path)
        data = {
            "session_id": session_id,
            "tokenUsage": usage.to_dict(),
        }
        await broadcast_event("session_token_usage_updated", data)
        await broadcast_json_event("session_token_usage_updated", data)
    except (OSError, IOError) as e:
        logger.warning(f"Failed to broadcast token usage for {session_id}: {e}")


async def broadcast_permission_denied(
    session_id: str, denials: list[dict], original_message: str
) -> None:
    """Broadcast permission denial event to clients.

    Args:
        session_id: The session where permission was denied
        denials: List of permission denial dicts from CLI output
        original_message: The original message that triggered the denial
    """
    data = {
        "session_id": session_id,
        "denials": denials,
        "original_message": original_message,
    }
    await broadcast_event("permission_denied", data)
    await broadcast_json_event("permission_denied", data)
