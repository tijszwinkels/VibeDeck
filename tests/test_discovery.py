"""Tests for Claude Code session discovery, including subagent support."""

import json
import tempfile
from pathlib import Path

import pytest

from claude_code_session_explorer.backends.claude_code.discovery import (
    find_recent_sessions,
    should_watch_file,
    is_subagent_session,
    get_parent_session_id,
)
from claude_code_session_explorer.backends.claude_code.tailer import is_warmup_session


@pytest.fixture
def temp_projects_dir():
    """Create a temporary projects directory with sample sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)

        # Create a project directory
        project_dir = projects_dir / "-home-user-myproject"
        project_dir.mkdir(parents=True)

        # Create a regular session
        regular_session = project_dir / "abc123.jsonl"
        regular_session.write_text(json.dumps({
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Hello"}
        }) + "\n")

        # Create a session directory with subagents
        session_dir = project_dir / "def456"
        subagents_dir = session_dir / "subagents"
        subagents_dir.mkdir(parents=True)

        # Create a subagent session
        subagent_session = subagents_dir / "agent-xyz789.jsonl"
        subagent_session.write_text(json.dumps({
            "type": "user",
            "timestamp": "2024-12-30T11:00:00.000Z",
            "isSidechain": True,
            "agentId": "xyz789",
            "sessionId": "def456",
            "message": {"content": "Subagent task"}
        }) + "\n")

        # Create another regular session (the parent of the subagent)
        parent_session = project_dir / "def456.jsonl"
        parent_session.write_text(json.dumps({
            "type": "user",
            "timestamp": "2024-12-30T10:30:00.000Z",
            "message": {"content": "Parent session"}
        }) + "\n")

        yield projects_dir


class TestFindRecentSessions:
    """Tests for find_recent_sessions function."""

    def test_finds_regular_sessions(self, temp_projects_dir):
        """Should find regular session files."""
        sessions = find_recent_sessions(temp_projects_dir, limit=10)

        # Should find at least the regular sessions
        session_names = [s.name for s in sessions]
        assert "abc123.jsonl" in session_names
        assert "def456.jsonl" in session_names

    def test_includes_subagents_by_default(self, temp_projects_dir):
        """Should include subagent sessions by default."""
        sessions = find_recent_sessions(temp_projects_dir, limit=10)

        # Should find the subagent
        session_names = [s.name for s in sessions]
        assert "agent-xyz789.jsonl" in session_names

    def test_excludes_subagents_when_requested(self, temp_projects_dir):
        """Should exclude subagent sessions when include_subagents=False."""
        sessions = find_recent_sessions(
            temp_projects_dir, limit=10, include_subagents=False
        )

        # Should not find subagents
        session_names = [s.name for s in sessions]
        assert "agent-xyz789.jsonl" not in session_names

        # But should still find regular sessions
        assert "abc123.jsonl" in session_names
        assert "def456.jsonl" in session_names

    def test_respects_limit(self, temp_projects_dir):
        """Should respect the limit parameter."""
        sessions = find_recent_sessions(temp_projects_dir, limit=1)
        assert len(sessions) == 1


class TestShouldWatchFile:
    """Tests for should_watch_file function."""

    def test_watches_regular_jsonl(self):
        """Should watch regular .jsonl files."""
        assert should_watch_file(Path("/some/path/session.jsonl"))

    def test_watches_subagent_files_by_default(self):
        """Should watch subagent files by default."""
        assert should_watch_file(Path("/some/path/subagents/agent-abc123.jsonl"))

    def test_excludes_subagent_files_when_requested(self):
        """Should exclude subagent files when include_subagents=False."""
        assert not should_watch_file(
            Path("/some/path/subagents/agent-abc123.jsonl"),
            include_subagents=False
        )

    def test_ignores_non_jsonl(self):
        """Should not watch non-.jsonl files."""
        assert not should_watch_file(Path("/some/path/file.txt"))
        assert not should_watch_file(Path("/some/path/file.json"))


class TestIsSubagentSession:
    """Tests for is_subagent_session function."""

    def test_identifies_subagent_by_filename(self):
        """Should identify subagent sessions by filename pattern."""
        assert is_subagent_session(Path("/path/subagents/agent-abc123.jsonl"))
        assert is_subagent_session(Path("/path/agent-xyz.jsonl"))

    def test_regular_session_is_not_subagent(self):
        """Should return False for regular session files."""
        assert not is_subagent_session(Path("/path/abc123.jsonl"))
        assert not is_subagent_session(Path("/path/session.jsonl"))


class TestGetParentSessionId:
    """Tests for get_parent_session_id function."""

    def test_gets_parent_from_subagent_path(self):
        """Should extract parent session ID from subagent path."""
        path = Path("/home/user/.claude/projects/-myproject/def456/subagents/agent-xyz.jsonl")
        assert get_parent_session_id(path) == "def456"

    def test_returns_none_for_regular_session(self):
        """Should return None for non-subagent sessions."""
        path = Path("/home/user/.claude/projects/-myproject/abc123.jsonl")
        assert get_parent_session_id(path) is None


class TestIsWarmupSession:
    """Tests for is_warmup_session function."""

    def test_detects_warmup_session(self):
        """Should detect sessions with 'Warmup' as first message."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"content": "Warmup"}
            }) + "\n")
            f.flush()
            assert is_warmup_session(Path(f.name))

    def test_regular_session_is_not_warmup(self):
        """Should return False for regular sessions."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"content": "Hello, help me with code"}
            }) + "\n")
            f.flush()
            assert not is_warmup_session(Path(f.name))


class TestFindRecentSessionsExcludesWarmup:
    """Tests for warmup session filtering in find_recent_sessions."""

    def test_excludes_warmup_sessions(self):
        """Should exclude warmup sessions from results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir)
            project_dir = projects_dir / "-myproject"
            project_dir.mkdir(parents=True)

            # Create a warmup session
            warmup = project_dir / "agent-warmup.jsonl"
            warmup.write_text(json.dumps({
                "type": "user",
                "message": {"content": "Warmup"}
            }) + "\n")

            # Create a regular session
            regular = project_dir / "session.jsonl"
            regular.write_text(json.dumps({
                "type": "user",
                "message": {"content": "Help me code"}
            }) + "\n")

            sessions = find_recent_sessions(projects_dir, limit=10)
            session_names = [s.name for s in sessions]

            # Should find regular session but not warmup
            assert "session.jsonl" in session_names
            assert "agent-warmup.jsonl" not in session_names
