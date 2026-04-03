"""Tests for custom session title routes."""

import json
from pathlib import Path
from unittest.mock import patch

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
