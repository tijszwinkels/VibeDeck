"""Tests for JSON SSE broadcasting and endpoint setup."""

import asyncio
import json

import pytest

from vibedeck import broadcasting


@pytest.fixture(autouse=True)
def reset_broadcasting():
    """Reset broadcasting state before/after each test."""
    broadcasting.get_clients().clear()
    broadcasting.get_json_clients().clear()
    yield
    broadcasting.get_clients().clear()
    broadcasting.get_json_clients().clear()


class TestJsonClientManagement:
    """Tests for JSON client set management."""

    def test_add_json_client(self):
        queue = asyncio.Queue(maxsize=100)
        broadcasting.add_json_client(queue)
        assert queue in broadcasting.get_json_clients()

    def test_remove_json_client(self):
        queue = asyncio.Queue(maxsize=100)
        broadcasting.add_json_client(queue)
        broadcasting.remove_json_client(queue)
        assert queue not in broadcasting.get_json_clients()

    def test_remove_nonexistent_client(self):
        """Removing a client that wasn't added should not raise."""
        queue = asyncio.Queue(maxsize=100)
        broadcasting.remove_json_client(queue)  # Should not raise

    def test_has_json_clients_empty(self):
        assert broadcasting.has_json_clients() is False

    def test_has_json_clients_with_client(self):
        queue = asyncio.Queue(maxsize=100)
        broadcasting.add_json_client(queue)
        assert broadcasting.has_json_clients() is True

    def test_json_clients_independent_of_html_clients(self):
        """JSON and HTML client sets should be independent."""
        html_q = asyncio.Queue(maxsize=100)
        json_q = asyncio.Queue(maxsize=100)
        broadcasting.add_client(html_q)
        broadcasting.add_json_client(json_q)

        assert html_q in broadcasting.get_clients()
        assert html_q not in broadcasting.get_json_clients()
        assert json_q in broadcasting.get_json_clients()
        assert json_q not in broadcasting.get_clients()


class TestJsonBroadcasting:
    """Tests for JSON-specific broadcasting functions."""

    @pytest.mark.asyncio
    async def test_broadcast_json_event(self):
        """broadcast_json_event should send events to JSON clients only."""
        html_q = asyncio.Queue(maxsize=100)
        json_q = asyncio.Queue(maxsize=100)
        broadcasting.add_client(html_q)
        broadcasting.add_json_client(json_q)

        await broadcasting.broadcast_json_event("session_added", {"id": "ses_123"})

        # JSON client should receive it
        event = json_q.get_nowait()
        assert event["event"] == "session_added"
        assert event["data"]["id"] == "ses_123"

        # HTML client should NOT receive it
        assert html_q.empty()

    @pytest.mark.asyncio
    async def test_broadcast_json_message(self):
        """broadcast_json_message should deliver normalized message to JSON clients."""
        from vibedeck.backends.shared.normalizer import NormalizedMessage, ContentBlock

        json_q = asyncio.Queue(maxsize=100)
        broadcasting.add_json_client(json_q)

        msg = NormalizedMessage(
            role="assistant",
            timestamp="2024-12-30T10:00:01.000Z",
            blocks=[ContentBlock(type="text", text="Hello")],
            model="claude-opus-4-6",
        )
        await broadcasting.broadcast_json_message("session-1", msg)

        event = json_q.get_nowait()
        assert event["event"] == "message"
        data = event["data"]
        assert data["session_id"] == "session-1"
        assert data["message"]["role"] == "assistant"
        assert data["message"]["blocks"][0]["type"] == "text"
        assert data["message"]["blocks"][0]["text"] == "Hello"
        assert data["message"]["model"] == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_broadcast_json_event_discards_full_queue(self):
        """Should discard JSON clients whose queue is full."""
        json_q = asyncio.Queue(maxsize=1)
        broadcasting.add_json_client(json_q)

        # Fill the queue
        await broadcasting.broadcast_json_event("ping", {})
        # This should cause the client to be discarded
        await broadcasting.broadcast_json_event("ping", {})

        assert json_q not in broadcasting.get_json_clients()

    @pytest.mark.asyncio
    async def test_broadcast_event_only_goes_to_html(self):
        """broadcast_event should only send to HTML clients."""
        html_q = asyncio.Queue(maxsize=100)
        json_q = asyncio.Queue(maxsize=100)
        broadcasting.add_client(html_q)
        broadcasting.add_json_client(json_q)

        await broadcasting.broadcast_event("custom_event", {"key": "value"})

        # HTML client should receive it
        event = html_q.get_nowait()
        assert event["event"] == "custom_event"

        # JSON client should NOT receive it (broadcast_event is HTML-only)
        assert json_q.empty()


class TestDualBroadcastEvents:
    """Tests that non-message events are sent to both HTML and JSON clients."""

    @pytest.mark.asyncio
    async def test_session_removed_dual_broadcast(self):
        """broadcast_session_removed should send to both HTML and JSON."""
        html_q = asyncio.Queue(maxsize=100)
        json_q = asyncio.Queue(maxsize=100)
        broadcasting.add_client(html_q)
        broadcasting.add_json_client(json_q)

        await broadcasting.broadcast_session_removed("ses_456")

        html_event = html_q.get_nowait()
        json_event = json_q.get_nowait()
        assert html_event["event"] == "session_removed"
        assert json_event["event"] == "session_removed"
        assert html_event["data"]["id"] == "ses_456"
        assert json_event["data"]["id"] == "ses_456"


class TestJsonMessagesEndpoint:
    """Tests for GET /sessions/{id}/messages/json endpoint."""

    def test_messages_json_returns_normalized(self, temp_jsonl_file):
        """Should return normalized messages for a session."""
        from vibedeck.server import app
        from vibedeck.sessions import add_session
        from vibedeck.routes.sessions import configure_session_routes
        from vibedeck import server, sessions

        sessions.get_sessions().clear()
        sessions.get_known_session_files().clear()
        configure_session_routes(
            get_server_backend=server.get_server_backend,
            get_backend_for_session=server.get_backend_for_session,
            is_send_enabled=server.is_send_enabled,
            is_fork_enabled=server.is_fork_enabled,
            is_skip_permissions=server.is_skip_permissions,
            get_default_send_backend=server.get_default_send_backend,
            get_allowed_directories=server.get_allowed_directories,
            add_allowed_directory=server.add_allowed_directory,
            run_cli_for_session=server.run_cli_for_session,
            broadcast_session_status=server._broadcast_session_status,
            summarize_session_async=server._summarize_session_async,
            get_summarizer=server.get_summarizer,
            get_idle_summary_model=server.get_idle_summary_model,
            cached_models=server._cached_models,
        )

        from fastapi.testclient import TestClient

        info, _ = add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get(f"/sessions/{info.session_id}/messages/json")
        assert response.status_code == 200

        data = response.json()
        assert data["session_id"] == info.session_id
        assert "messages" in data
        assert isinstance(data["messages"], list)
        assert data["message_count"] == len(data["messages"])
        assert len(data["messages"]) > 0

        # Verify structure of first message
        msg = data["messages"][0]
        assert "role" in msg
        assert "timestamp" in msg
        assert "blocks" in msg

    def test_messages_json_404_for_unknown_session(self):
        """Should return 404 for unknown session."""
        from vibedeck.server import app
        from vibedeck.routes.sessions import configure_session_routes
        from vibedeck import server, sessions

        sessions.get_sessions().clear()
        configure_session_routes(
            get_server_backend=server.get_server_backend,
            get_backend_for_session=server.get_backend_for_session,
            is_send_enabled=server.is_send_enabled,
            is_fork_enabled=server.is_fork_enabled,
            is_skip_permissions=server.is_skip_permissions,
            get_default_send_backend=server.get_default_send_backend,
            get_allowed_directories=server.get_allowed_directories,
            add_allowed_directory=server.add_allowed_directory,
            run_cli_for_session=server.run_cli_for_session,
            broadcast_session_status=server._broadcast_session_status,
            summarize_session_async=server._summarize_session_async,
            get_summarizer=server.get_summarizer,
            get_idle_summary_model=server.get_idle_summary_model,
            cached_models=server._cached_models,
        )

        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/sessions/nonexistent-id/messages/json")
        assert response.status_code == 404


class TestJsonEventEndpointExists:
    """Tests that the /events/json endpoint is registered."""

    def test_events_json_route_registered(self):
        """The /events/json route should be registered on the app."""
        from vibedeck.server import app

        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/events/json" in routes

    def test_events_route_still_exists(self):
        """The original /events route should still exist."""
        from vibedeck.server import app

        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/events" in routes
