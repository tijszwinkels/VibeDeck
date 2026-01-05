"""Tests for the FastAPI server."""

import json
import pytest
from fastapi.testclient import TestClient

from claude_code_session_explorer import server, sessions
from claude_code_session_explorer.server import app
from claude_code_session_explorer.sessions import add_session


@pytest.fixture(autouse=True)
def reset_server_state():
    """Reset server state before each test."""
    sessions.get_sessions().clear()
    server._clients.clear()
    sessions.get_known_session_files().clear()
    server.set_send_enabled(False)  # Reset send feature state
    yield
    sessions.get_sessions().clear()
    server._clients.clear()
    sessions.get_known_session_files().clear()
    server.set_send_enabled(False)


class TestServerEndpoints:
    """Tests for server endpoints."""

    def test_index_returns_html(self, temp_jsonl_file):
        """Test that index returns HTML page."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Claude Code Session Explorer" in response.text

    def test_index_includes_css(self, temp_jsonl_file):
        """Test that index includes CSS."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/")
        assert ":root" in response.text
        assert "--bg-color" in response.text

    def test_index_includes_sse_script(self, temp_jsonl_file):
        """Test that index includes SSE client script."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/")
        assert "EventSource" in response.text
        assert "/events" in response.text

    def test_index_includes_tab_bar(self, temp_jsonl_file):
        """Test that index includes tab bar elements."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/")
        assert "tab-bar" in response.text
        assert "auto-follow" in response.text

    def test_health_check(self, temp_jsonl_file):
        """Test health check endpoint."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "sessions" in data
        assert "clients" in data

    def test_sessions_endpoint(self, temp_jsonl_file):
        """Test sessions list endpoint."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert len(data["sessions"]) == 1


class TestSessionManagement:
    """Tests for session management functions."""

    def test_add_session(self, temp_jsonl_file):
        """Test adding a session."""
        info, evicted_id = add_session(temp_jsonl_file)

        assert info is not None
        assert evicted_id is None
        assert info.session_id == temp_jsonl_file.stem
        assert info.path == temp_jsonl_file

    def test_add_duplicate_session(self, temp_jsonl_file):
        """Test that adding duplicate session returns None."""
        info1, _ = add_session(temp_jsonl_file)
        info2, evicted_id = add_session(temp_jsonl_file)

        assert info1 is not None
        assert info2 is None
        assert evicted_id is None

    def test_add_empty_session_skipped(self, tmp_path):
        """Test that empty session files are skipped."""
        empty_file = tmp_path / "empty.jsonl"
        empty_file.write_text("")  # 0 bytes

        info, evicted_id = add_session(empty_file)

        assert info is None
        assert evicted_id is None
        assert "empty" not in sessions.get_sessions()

    def test_session_limit_with_eviction(self, tmp_path):
        """Test that session limit evicts oldest sessions."""
        import time

        # Create more sessions than the limit, with slight time delays
        for i in range(sessions.MAX_SESSIONS + 2):
            session_file = tmp_path / f"session_{i}.jsonl"
            session_file.write_text('{"type": "user"}\n')
            add_session(session_file)
            time.sleep(0.01)  # Ensure different mtime

        # Should still have MAX_SESSIONS (oldest got evicted)
        assert len(sessions.get_sessions()) == sessions.MAX_SESSIONS
        # First session should have been evicted
        assert "session_0" not in sessions.get_sessions()

    def test_session_limit_without_eviction(self, tmp_path):
        """Test that session limit is respected when eviction is disabled."""
        # Create more sessions than the limit without eviction
        for i in range(sessions.MAX_SESSIONS + 2):
            session_file = tmp_path / f"session_{i}.jsonl"
            session_file.write_text('{"type": "user"}\n')
            add_session(session_file, evict_oldest=False)

        # Should stop at MAX_SESSIONS
        assert len(sessions.get_sessions()) == sessions.MAX_SESSIONS

    def test_remove_session(self, temp_jsonl_file):
        """Test removing a session."""
        info, _ = add_session(temp_jsonl_file)
        session_id = info.session_id

        assert sessions.remove_session(session_id) is True
        assert session_id not in sessions.get_sessions()

    def test_remove_nonexistent_session(self):
        """Test removing a session that doesn't exist."""
        assert sessions.remove_session("nonexistent") is False

    def test_get_sessions_list(self, temp_jsonl_file):
        """Test getting the sessions list."""
        add_session(temp_jsonl_file)
        sessions_list = sessions.get_sessions_list()

        assert len(sessions_list) == 1
        assert sessions_list[0]["id"] == temp_jsonl_file.stem


class TestSendFeature:
    """Tests for the send message feature."""

    def test_send_enabled_endpoint_disabled(self):
        """Test /send-enabled returns false when disabled."""
        client = TestClient(app)
        response = client.get("/send-enabled")
        assert response.status_code == 200
        assert response.json() == {"enabled": False}

    def test_send_enabled_endpoint_enabled(self):
        """Test /send-enabled returns true when enabled."""
        server.set_send_enabled(True)
        client = TestClient(app)
        response = client.get("/send-enabled")
        assert response.status_code == 200
        assert response.json() == {"enabled": True}

    def test_send_returns_403_when_disabled(self, temp_jsonl_file):
        """Test that send endpoint returns 403 when feature is disabled."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.post(
            f"/sessions/{temp_jsonl_file.stem}/send",
            json={"message": "test message"}
        )
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()

    def test_send_returns_404_for_unknown_session(self, temp_jsonl_file):
        """Test that send returns 404 for unknown session."""
        server.set_send_enabled(True)
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.post(
            "/sessions/nonexistent/send",
            json={"message": "test message"}
        )
        assert response.status_code == 404

    def test_send_returns_400_for_empty_message(self, temp_jsonl_file):
        """Test that send returns 400 for empty message."""
        server.set_send_enabled(True)
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.post(
            f"/sessions/{temp_jsonl_file.stem}/send",
            json={"message": "   "}
        )
        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()

    def test_session_status_endpoint(self, temp_jsonl_file):
        """Test session status endpoint."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get(f"/sessions/{temp_jsonl_file.stem}/status")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == temp_jsonl_file.stem
        assert data["running"] is False
        assert data["queued_messages"] == 0

    def test_session_status_404_for_unknown(self):
        """Test session status returns 404 for unknown session."""
        client = TestClient(app)
        response = client.get("/sessions/nonexistent/status")
        assert response.status_code == 404

    def test_interrupt_returns_403_when_disabled(self, temp_jsonl_file):
        """Test that interrupt returns 403 when feature is disabled."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.post(f"/sessions/{temp_jsonl_file.stem}/interrupt")
        assert response.status_code == 403

    def test_interrupt_returns_404_for_unknown_session(self):
        """Test that interrupt returns 404 for unknown session."""
        server.set_send_enabled(True)
        client = TestClient(app)

        response = client.post("/sessions/nonexistent/interrupt")
        assert response.status_code == 404

    def test_interrupt_returns_409_when_not_running(self, temp_jsonl_file):
        """Test that interrupt returns 409 when no process is running."""
        server.set_send_enabled(True)
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.post(f"/sessions/{temp_jsonl_file.stem}/interrupt")
        assert response.status_code == 409
        assert "no process running" in response.json()["detail"].lower()

    def test_new_session_returns_403_when_disabled(self):
        """Test that new session returns 403 when feature is disabled."""
        client = TestClient(app)

        response = client.post(
            "/sessions/new",
            json={"message": "Hello"}
        )
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()

    def test_new_session_returns_400_for_empty_message(self):
        """Test that new session returns 400 for empty message."""
        server.set_send_enabled(True)
        client = TestClient(app)

        response = client.post(
            "/sessions/new",
            json={"message": "   "}
        )
        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()


# Note: SSE endpoint streaming tests are skipped because TestClient
# doesn't handle SSE event generators well. The endpoint is tested
# manually and through integration tests.
