"""Tests for SSE event filtering by authenticated user."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibedeck.server import _event_belongs_to_user


@pytest.fixture
def users_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def _make_get_owner(users_dir: Path):
    """Create a get_owner callback that derives user from path."""
    from vibedeck.backends.isolation.discovery import get_session_owner
    return lambda path: get_session_owner(path, users_dir)


class TestEventBelongsToUser:
    """Test SSE event ownership checking."""

    def test_event_with_matching_session_id(self, users_dir):
        """Events for user's own sessions should pass."""
        from vibedeck.sessions import SessionInfo

        session_path = users_dir / "alice" / ".claude" / "projects" / "-proj" / "sess-1.jsonl"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text('{"type":"user","timestamp":"2026-01-01T00:00:00Z","message":{"content":"hi"}}\n')

        get_owner = _make_get_owner(users_dir)

        # Mock get_session to return a session info with the right path
        info = MagicMock()
        info.path = session_path

        import vibedeck.server as server_mod
        original = server_mod.get_session
        server_mod.get_session = lambda sid: info if sid == "sess-1" else None

        try:
            event = {"data": {"session_id": "sess-1", "content": "hello"}}
            assert _event_belongs_to_user(event, "alice", get_owner) is True
            assert _event_belongs_to_user(event, "bob", get_owner) is False
        finally:
            server_mod.get_session = original

    def test_event_without_session_id_passes(self, users_dir):
        """Global events (no session_id) should always pass through."""
        get_owner = _make_get_owner(users_dir)

        event = {"data": {"key": "value"}}
        assert _event_belongs_to_user(event, "alice", get_owner) is True

    def test_event_for_unknown_session_passes(self, users_dir):
        """Events for sessions not found in state should pass through."""
        get_owner = _make_get_owner(users_dir)

        import vibedeck.server as server_mod
        original = server_mod.get_session
        server_mod.get_session = lambda sid: None

        try:
            event = {"data": {"session_id": "unknown-sess"}}
            assert _event_belongs_to_user(event, "alice", get_owner) is True
        finally:
            server_mod.get_session = original

    def test_session_removed_event_uses_id_field(self, users_dir):
        """session_removed events use 'id' instead of 'session_id'."""
        get_owner = _make_get_owner(users_dir)

        session_path = users_dir / "alice" / ".claude" / "projects" / "-proj" / "sess-2.jsonl"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text('{"type":"user","timestamp":"2026-01-01T00:00:00Z","message":{"content":"hi"}}\n')

        info = MagicMock()
        info.path = session_path

        import vibedeck.server as server_mod
        original = server_mod.get_session
        server_mod.get_session = lambda sid: info if sid == "sess-2" else None

        try:
            event = {"data": {"id": "sess-2"}}
            assert _event_belongs_to_user(event, "alice", get_owner) is True
            assert _event_belongs_to_user(event, "bob", get_owner) is False
        finally:
            server_mod.get_session = original

    def test_session_added_event_has_path(self, users_dir):
        """session_added events carry full session data including id."""
        get_owner = _make_get_owner(users_dir)

        session_path = users_dir / "bob" / ".claude" / "projects" / "-proj" / "sess-3.jsonl"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text('{"type":"user","timestamp":"2026-01-01T00:00:00Z","message":{"content":"hi"}}\n')

        info = MagicMock()
        info.path = session_path

        import vibedeck.server as server_mod
        original = server_mod.get_session
        server_mod.get_session = lambda sid: info if sid == "sess-3" else None

        try:
            event = {"data": {"id": "sess-3", "name": "Test Session"}}
            assert _event_belongs_to_user(event, "bob", get_owner) is True
            assert _event_belongs_to_user(event, "alice", get_owner) is False
        finally:
            server_mod.get_session = original
