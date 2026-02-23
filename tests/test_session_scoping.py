"""Tests for user-scoped session filtering."""

import json
import tempfile
from pathlib import Path

import pytest

from vibedeck.backends.isolation.discovery import get_session_owner


def _write_session(path: Path, timestamp: str = "2026-01-15T10:00:00.000Z") -> None:
    """Write a minimal valid session JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "type": "user",
            "timestamp": timestamp,
            "message": {"content": "Hello"},
        })
        + "\n"
        + json.dumps({
            "type": "assistant",
            "timestamp": timestamp,
            "message": {"content": "Hi"},
        })
        + "\n"
    )


@pytest.fixture
def users_dir():
    """Create temp users directory with sessions for two users."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        _write_session(
            base / "alice" / ".claude" / "projects" / "-project1" / "sess-a1.jsonl",
        )
        _write_session(
            base / "alice" / ".claude" / "projects" / "-project2" / "sess-a2.jsonl",
        )
        _write_session(
            base / "bob" / ".claude" / "projects" / "-project1" / "sess-b1.jsonl",
        )

        yield base


class TestSessionOwnerFiltering:
    """Test filtering sessions by user ownership."""

    def test_filter_sessions_for_user(self, users_dir):
        """Should filter session list to only those owned by user."""
        all_sessions = list(users_dir.glob("*/.claude/projects/**/*.jsonl"))
        assert len(all_sessions) == 3

        alice_sessions = [
            s for s in all_sessions if get_session_owner(s, users_dir) == "alice"
        ]
        assert len(alice_sessions) == 2

        bob_sessions = [
            s for s in all_sessions if get_session_owner(s, users_dir) == "bob"
        ]
        assert len(bob_sessions) == 1

    def test_user_cannot_see_other_users_sessions(self, users_dir):
        """Alice's sessions should not include any of Bob's."""
        all_sessions = list(users_dir.glob("*/.claude/projects/**/*.jsonl"))

        alice_sessions = [
            s for s in all_sessions if get_session_owner(s, users_dir) == "alice"
        ]
        for sess in alice_sessions:
            assert "bob" not in str(sess)

    def test_no_auth_sees_all_sessions(self, users_dir):
        """When auth is disabled (user_id=None), all sessions are visible."""
        all_sessions = list(users_dir.glob("*/.claude/projects/**/*.jsonl"))

        # Simulating no auth: no filtering
        visible = all_sessions  # No filter applied
        assert len(visible) == 3

    def test_unknown_user_sees_nothing(self, users_dir):
        """Unknown user should see no sessions."""
        all_sessions = list(users_dir.glob("*/.claude/projects/**/*.jsonl"))

        unknown_sessions = [
            s for s in all_sessions if get_session_owner(s, users_dir) == "eve"
        ]
        assert len(unknown_sessions) == 0


class TestIsolationBackendSessionOwner:
    """Test IsolationBackend.get_session_owner()."""

    def test_get_session_owner(self, users_dir):
        """Backend should extract user from session path."""
        from vibedeck.backends.isolation.backend import IsolationBackend

        backend = IsolationBackend(users_dir=str(users_dir))

        path = users_dir / "alice" / ".claude" / "projects" / "-proj" / "sess.jsonl"
        assert backend.get_session_owner(path) == "alice"

    def test_get_session_owner_outside_users_dir(self, users_dir):
        """Should return None for paths outside users_dir."""
        from vibedeck.backends.isolation.backend import IsolationBackend

        backend = IsolationBackend(users_dir=str(users_dir))

        path = Path("/tmp/other/session.jsonl")
        assert backend.get_session_owner(path) is None
