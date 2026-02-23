"""Tests for isolation backend session discovery."""

import json
import tempfile
from pathlib import Path

import pytest


def _write_session(path: Path, user_msg: str = "Hello", timestamp: str = "2026-01-15T10:00:00.000Z") -> None:
    """Write a minimal valid session JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "type": "user",
            "timestamp": timestamp,
            "message": {"content": user_msg},
        })
        + "\n"
        + json.dumps({
            "type": "assistant",
            "timestamp": timestamp,
            "message": {"content": "Hi there"},
        })
        + "\n"
    )


@pytest.fixture
def users_dir():
    """Create a temp users directory with sessions for two users."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # User A has two sessions in two projects
        _write_session(
            base / "user_a" / ".claude" / "projects" / "-home-alice-project1" / "sess-a1.jsonl",
            user_msg="Alice project1",
            timestamp="2026-01-15T10:00:00.000Z",
        )
        _write_session(
            base / "user_a" / ".claude" / "projects" / "-home-alice-project2" / "sess-a2.jsonl",
            user_msg="Alice project2",
            timestamp="2026-01-15T11:00:00.000Z",
        )

        # User B has one session
        _write_session(
            base / "user_b" / ".claude" / "projects" / "-home-bob-project1" / "sess-b1.jsonl",
            user_msg="Bob project1",
            timestamp="2026-01-15T12:00:00.000Z",
        )

        yield base


class TestIsolationDiscovery:
    """Test per-user session discovery."""

    def test_finds_sessions_across_all_users(self, users_dir):
        """Discovery should find sessions from all user directories."""
        from vibedeck.backends.isolation.discovery import find_sessions_for_all_users

        sessions = find_sessions_for_all_users(users_dir, limit=10)
        assert len(sessions) == 3

    def test_finds_sessions_for_specific_user(self, users_dir):
        """Discovery should find only sessions for a specific user."""
        from vibedeck.backends.isolation.discovery import find_sessions_for_user

        sessions = find_sessions_for_user(users_dir, "user_a", limit=10)
        assert len(sessions) == 2

        sessions_b = find_sessions_for_user(users_dir, "user_b", limit=10)
        assert len(sessions_b) == 1

    def test_returns_empty_for_unknown_user(self, users_dir):
        """Discovery should return empty list for user with no sessions."""
        from vibedeck.backends.isolation.discovery import find_sessions_for_user

        sessions = find_sessions_for_user(users_dir, "nonexistent", limit=10)
        assert sessions == []

    def test_extracts_user_id_from_path(self, users_dir):
        """Should extract user ID from session file path."""
        from vibedeck.backends.isolation.discovery import get_session_owner

        path_a = users_dir / "user_a" / ".claude" / "projects" / "-home-alice-project1" / "sess-a1.jsonl"
        assert get_session_owner(path_a, users_dir) == "user_a"

        path_b = users_dir / "user_b" / ".claude" / "projects" / "-home-bob-project1" / "sess-b1.jsonl"
        assert get_session_owner(path_b, users_dir) == "user_b"

    def test_returns_none_for_path_outside_users_dir(self, users_dir):
        """Should return None if path is not under users_dir."""
        from vibedeck.backends.isolation.discovery import get_session_owner

        outside_path = Path("/tmp/random/session.jsonl")
        assert get_session_owner(outside_path, users_dir) is None

    def test_respects_limit(self, users_dir):
        """Discovery should respect the limit parameter."""
        from vibedeck.backends.isolation.discovery import find_sessions_for_all_users

        sessions = find_sessions_for_all_users(users_dir, limit=2)
        assert len(sessions) == 2

    def test_sorted_by_timestamp_newest_first(self, users_dir):
        """Sessions should be sorted by last message timestamp, newest first."""
        from vibedeck.backends.isolation.discovery import find_sessions_for_all_users

        sessions = find_sessions_for_all_users(users_dir, limit=10)
        # sess-b1 is newest (12:00), then sess-a2 (11:00), then sess-a1 (10:00)
        assert sessions[0].name == "sess-b1.jsonl"
        assert sessions[1].name == "sess-a2.jsonl"
        assert sessions[2].name == "sess-a1.jsonl"

    def test_skips_empty_session_files(self, users_dir):
        """Should skip empty session files."""
        from vibedeck.backends.isolation.discovery import find_sessions_for_all_users

        # Create an empty session file
        empty = users_dir / "user_a" / ".claude" / "projects" / "-home-alice-project1" / "empty.jsonl"
        empty.write_text("")

        sessions = find_sessions_for_all_users(users_dir, limit=10)
        session_names = [s.name for s in sessions]
        assert "empty.jsonl" not in session_names

    def test_handles_missing_users_dir(self):
        """Should return empty list if users_dir doesn't exist."""
        from vibedeck.backends.isolation.discovery import find_sessions_for_all_users

        sessions = find_sessions_for_all_users(Path("/nonexistent"), limit=10)
        assert sessions == []

    def test_get_user_projects_dir(self, users_dir):
        """Should return the .claude/projects dir for a user."""
        from vibedeck.backends.isolation.discovery import get_user_projects_dir

        projects = get_user_projects_dir(users_dir, "user_a")
        assert projects == users_dir / "user_a" / ".claude" / "projects"
