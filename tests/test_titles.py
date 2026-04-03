"""Tests for custom session title routes and live session name updates."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from vibedeck.routes.titles import (
    _get_session_titles_path,
    _load_session_titles,
    _save_session_titles,
)


@pytest.fixture
def titles_config(tmp_path, monkeypatch):
    """Patch CONFIG_DIR to use a temp directory."""
    monkeypatch.setattr("vibedeck.routes.titles.CONFIG_DIR", tmp_path)
    return tmp_path


class TestSessionTitlesPersistence:
    def test_load_empty(self, titles_config):
        """Loading when no file exists returns empty dict."""
        assert _load_session_titles() == {}

    def test_save_and_load(self, titles_config):
        """Saving and loading round-trips correctly."""
        titles = {"session-1": "My Custom Title", "session-2": "Another Title"}
        assert _save_session_titles(titles)
        loaded = _load_session_titles()
        assert loaded == titles

    def test_save_creates_dirs(self, titles_config):
        """Save creates parent directories if needed."""
        # titles_config is already a tmp dir, so this should work
        assert _save_session_titles({"s1": "title1"})
        assert (titles_config / "session-titles.json").exists()

    def test_load_corrupt_file(self, titles_config):
        """Loading a corrupt JSON file returns empty dict."""
        path = titles_config / "session-titles.json"
        path.write_text("not json{{{")
        assert _load_session_titles() == {}

    def test_clear_title(self, titles_config):
        """Removing a title key works."""
        titles = {"s1": "Title 1", "s2": "Title 2"}
        _save_session_titles(titles)
        loaded = _load_session_titles()
        del loaded["s1"]
        _save_session_titles(loaded)
        assert _load_session_titles() == {"s2": "Title 2"}


class TestSessionTitleRoutes:
    """Test the FastAPI route handlers."""

    @pytest.fixture
    def client(self, titles_config):
        """Create a test client with the titles router."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from vibedeck.routes.titles import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_get_titles_empty(self, client):
        resp = client.get("/api/session-titles")
        assert resp.status_code == 200
        assert resp.json() == {"titles": {}}

    def test_set_and_get_title(self, client):
        resp = client.post(
            "/api/session-titles/set",
            json={"session_id": "s1", "title": "My Title"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

        resp = client.get("/api/session-titles")
        assert resp.json()["titles"] == {"s1": "My Title"}

    def test_clear_title(self, client):
        client.post(
            "/api/session-titles/set",
            json={"session_id": "s1", "title": "My Title"},
        )
        resp = client.post(
            "/api/session-titles/set",
            json={"session_id": "s1", "title": None},
        )
        assert resp.status_code == 200
        assert client.get("/api/session-titles").json()["titles"] == {}

    def test_empty_title_clears(self, client):
        """Empty/whitespace title is treated as clearing."""
        client.post(
            "/api/session-titles/set",
            json={"session_id": "s1", "title": "My Title"},
        )
        resp = client.post(
            "/api/session-titles/set",
            json={"session_id": "s1", "title": "   "},
        )
        assert resp.status_code == 200
        assert client.get("/api/session-titles").json()["titles"] == {}

    def test_title_too_long(self, client):
        resp = client.post(
            "/api/session-titles/set",
            json={"session_id": "s1", "title": "x" * 201},
        )
        assert resp.status_code == 400

    def test_title_stripped(self, client):
        """Titles are trimmed of whitespace."""
        client.post(
            "/api/session-titles/set",
            json={"session_id": "s1", "title": "  My Title  "},
        )
        assert client.get("/api/session-titles").json()["titles"] == {
            "s1": "My Title"
        }


class TestCheckSessionNameUpdate:
    """Tests for _check_session_name_update in the server."""

    @pytest.fixture
    def pi_session_file(self, tmp_path):
        """Create a Pi session JSONL file with a session_info entry."""
        f = tmp_path / "test.jsonl"
        f.write_text(
            json.dumps({"type": "session", "id": "root", "timestamp": "2026-01-01T00:00:00Z", "cwd": str(tmp_path)}) + "\n"
            + json.dumps({"type": "message", "id": "m1", "parentId": "root", "timestamp": "2026-01-01T00:00:01Z", "message": {"role": "user", "content": "hello"}}) + "\n"
            + json.dumps({"type": "session_info", "id": "i1", "parentId": "m1", "name": "Original Name"}) + "\n"
        )
        return f

    @pytest.fixture
    def mock_session_info(self, pi_session_file):
        """Create a mock SessionInfo-like object."""
        class FakeInfo:
            path = pi_session_file
            session_id = "test-session-1"
            session_name = "Original Name"
        return FakeInfo()

    @pytest.fixture
    def mock_backend_with_name(self):
        """Create a mock backend that supports get_session_name_from_file."""
        from vibedeck.backends.pi.discovery import get_session_name

        class FakeBackend:
            def get_session_name_from_file(self, path):
                return get_session_name(path)
        return FakeBackend()

    @pytest.fixture
    def mock_backend_without_name(self):
        """Create a mock backend without get_session_name_from_file."""
        class FakeBackend:
            pass
        return FakeBackend()

    @pytest.mark.asyncio
    async def test_broadcasts_on_name_change(self, mock_session_info, mock_backend_with_name, pi_session_file, monkeypatch):
        """Should broadcast session_name_updated when name changes."""
        from vibedeck import server

        monkeypatch.setattr(server, "get_backend_for_session", lambda path: mock_backend_with_name)
        mock_broadcast = AsyncMock()
        mock_json_broadcast = AsyncMock()
        monkeypatch.setattr(server, "broadcast_event", mock_broadcast)
        monkeypatch.setattr(server, "broadcast_json_event", mock_json_broadcast)

        # Change the name in the file
        with open(pi_session_file, "a") as f:
            f.write(json.dumps({"type": "session_info", "id": "i2", "parentId": "i1", "name": "New Name"}) + "\n")

        await server._check_session_name_update(mock_session_info)

        expected = {"session_id": "test-session-1", "sessionName": "New Name"}
        mock_broadcast.assert_called_once_with("session_name_updated", expected)
        mock_json_broadcast.assert_called_once_with("session_name_updated", expected)
        assert mock_session_info.session_name == "New Name"

    @pytest.mark.asyncio
    async def test_no_broadcast_when_name_unchanged(self, mock_session_info, mock_backend_with_name, monkeypatch):
        """Should not broadcast when name hasn't changed."""
        from vibedeck import server

        monkeypatch.setattr(server, "get_backend_for_session", lambda path: mock_backend_with_name)
        mock_broadcast = AsyncMock()
        mock_json_broadcast = AsyncMock()
        monkeypatch.setattr(server, "broadcast_event", mock_broadcast)
        monkeypatch.setattr(server, "broadcast_json_event", mock_json_broadcast)

        await server._check_session_name_update(mock_session_info)

        mock_broadcast.assert_not_called()
        mock_json_broadcast.assert_not_called()
        assert mock_session_info.session_name == "Original Name"

    @pytest.mark.asyncio
    async def test_skips_backend_without_name_support(self, mock_session_info, mock_backend_without_name, monkeypatch):
        """Should skip backends that don't support get_session_name_from_file."""
        from vibedeck import server

        monkeypatch.setattr(server, "get_backend_for_session", lambda path: mock_backend_without_name)
        mock_broadcast = AsyncMock()
        mock_json_broadcast = AsyncMock()
        monkeypatch.setattr(server, "broadcast_event", mock_broadcast)
        monkeypatch.setattr(server, "broadcast_json_event", mock_json_broadcast)

        await server._check_session_name_update(mock_session_info)

        mock_broadcast.assert_not_called()
        mock_json_broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcasts_on_name_cleared(self, mock_session_info, mock_backend_with_name, pi_session_file, monkeypatch):
        """Should broadcast with None when name is cleared."""
        from vibedeck import server

        monkeypatch.setattr(server, "get_backend_for_session", lambda path: mock_backend_with_name)
        mock_broadcast = AsyncMock()
        mock_json_broadcast = AsyncMock()
        monkeypatch.setattr(server, "broadcast_event", mock_broadcast)
        monkeypatch.setattr(server, "broadcast_json_event", mock_json_broadcast)

        # Clear the name
        with open(pi_session_file, "a") as f:
            f.write(json.dumps({"type": "session_info", "id": "i2", "parentId": "i1", "name": ""}) + "\n")

        await server._check_session_name_update(mock_session_info)

        expected = {"session_id": "test-session-1", "sessionName": None}
        mock_broadcast.assert_called_once_with("session_name_updated", expected)
        mock_json_broadcast.assert_called_once_with("session_name_updated", expected)
        assert mock_session_info.session_name is None

    @pytest.mark.asyncio
    async def test_name_check_runs_without_displayable_entries(self, mock_session_info, mock_backend_with_name, pi_session_file, monkeypatch):
        """Name check should run even when process_session_messages finds no displayable entries.

        This is the key bug: session_info entries are filtered by the Pi tailer,
        so a name_session-only write produces new_entries == []. The name check
        must still fire.
        """
        from vibedeck import server
        from vibedeck.backends.pi.tailer import PiTailer

        # Create a tailer and seek to current end
        tailer = PiTailer(pi_session_file)
        tailer.seek_to_end()

        # Append only a session_info entry (no displayable message)
        with open(pi_session_file, "a") as f:
            f.write(json.dumps({"type": "session_info", "id": "i2", "parentId": "i1", "name": "New Name"}) + "\n")

        # Tailer returns no displayable entries
        new_entries = tailer.read_new_lines()
        assert new_entries == [], "session_info should be filtered by PiTailer"

        # But _check_session_name_update should still detect the change
        monkeypatch.setattr(server, "get_backend_for_session", lambda path: mock_backend_with_name)
        mock_broadcast = AsyncMock()
        mock_json_broadcast = AsyncMock()
        monkeypatch.setattr(server, "broadcast_event", mock_broadcast)
        monkeypatch.setattr(server, "broadcast_json_event", mock_json_broadcast)

        await server._check_session_name_update(mock_session_info)

        expected = {"session_id": "test-session-1", "sessionName": "New Name"}
        mock_broadcast.assert_called_once_with("session_name_updated", expected)
        mock_json_broadcast.assert_called_once_with("session_name_updated", expected)
