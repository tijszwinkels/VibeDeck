"""Tests for Claude Code session discovery, including subagent support."""

import json
import tempfile
from pathlib import Path

import pytest

from vibedeck.backends.claude_code.discovery import (
    find_recent_sessions,
    should_watch_file,
    is_subagent_session,
    get_parent_session_id,
    get_session_name,
    is_summary_file,
    get_session_id_from_summary_file,
    _decode_path_greedy,
)
from vibedeck.backends.claude_code.pricing import get_session_model
from vibedeck.backends.claude_code.tailer import is_warmup_session


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

    def test_watches_summary_files(self):
        """Should watch *_summary.json files."""
        assert should_watch_file(Path("/some/path/abc123_summary.json"))
        assert should_watch_file(Path("/some/path/uuid-uuid-uuid_summary.json"))

    def test_ignores_non_summary_json(self):
        """Should not watch regular .json files."""
        assert not should_watch_file(Path("/some/path/config.json"))
        assert not should_watch_file(Path("/some/path/session.json"))


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
                "timestamp": "2024-01-01T10:00:00Z",
                "message": {"content": "Warmup"}
            }) + "\n")

            # Create a regular session
            regular = project_dir / "session.jsonl"
            regular.write_text(json.dumps({
                "type": "user",
                "timestamp": "2024-01-01T10:00:00Z",
                "message": {"content": "Help me code"}
            }) + "\n")

            sessions = find_recent_sessions(projects_dir, limit=10)
            session_names = [s.name for s in sessions]

            # Should find regular session but not warmup
            assert "session.jsonl" in session_names
            assert "agent-warmup.jsonl" not in session_names


class TestGetSessionName:
    """Tests for get_session_name function."""

    def test_decodes_dotfile_path(self):
        """Should correctly decode paths with dotfiles (e.g., .mycel)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the actual dotfile directory structure
            dotfile_dir = Path(tmpdir) / ".mycel" / "agents" / "tool"
            dotfile_dir.mkdir(parents=True)

            # Create the encoded project directory name (as Claude Code would)
            # /tmp/xxx/.mycel/agents/tool -> -tmp-xxx--mycel-agents-tool
            encoded_name = tmpdir.replace("/", "-").lstrip("-") + "--mycel-agents-tool"
            projects_dir = Path(tmpdir) / "projects"
            project_dir = projects_dir / f"-{encoded_name}"
            project_dir.mkdir(parents=True)

            session_path = project_dir / "abc123.jsonl"
            session_path.touch()

            name, path = get_session_name(session_path)

            assert path == str(dotfile_dir)
            assert name == "tool"

    def test_decodes_regular_path(self):
        """Should correctly decode paths without dotfiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the actual directory structure
            regular_dir = Path(tmpdir) / "myproject"
            regular_dir.mkdir(parents=True)

            # Create the encoded project directory name
            encoded_name = tmpdir.replace("/", "-").lstrip("-") + "-myproject"
            projects_dir = Path(tmpdir) / "projects"
            project_dir = projects_dir / f"-{encoded_name}"
            project_dir.mkdir(parents=True)

            session_path = project_dir / "abc123.jsonl"
            session_path.touch()

            name, path = get_session_name(session_path)

            assert path == str(regular_dir)
            assert name == "myproject"

    def test_decodes_literal_double_dash_in_dirname(self):
        """Should correctly decode paths with literal -- in directory name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a directory with a literal double dash in the name
            double_dash_dir = Path(tmpdir) / "foo--bar"
            double_dash_dir.mkdir(parents=True)

            # Create the encoded project directory name
            # /tmp/xxx/foo--bar -> -tmp-xxx-foo--bar
            encoded_name = tmpdir.replace("/", "-").lstrip("-") + "-foo--bar"
            projects_dir = Path(tmpdir) / "projects"
            project_dir = projects_dir / f"-{encoded_name}"
            project_dir.mkdir(parents=True)

            session_path = project_dir / "abc123.jsonl"
            session_path.touch()

            name, path = get_session_name(session_path)

            assert path == str(double_dash_dir)
            assert name == "foo--bar"

    def test_decodes_tilde_dotfile_path(self):
        """Should correctly decode paths with ~/. (tilde + dotfile)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a structure like: /tmp/xxx/project/~/.mycel/agents/tool
            # This simulates someone with a literal ~ directory inside a project
            tilde_dotfile_dir = Path(tmpdir) / "project" / "~" / ".mycel" / "agents" / "tool"
            tilde_dotfile_dir.mkdir(parents=True)

            # The encoding: /tmp/xxx/project/~/.mycel/agents/tool
            # becomes: -tmp-xxx-project----mycel-agents-tool
            # (/ -> -, ~ -> -, / -> -, . -> - = four dashes before mycel)
            encoded_name = (
                tmpdir.replace("/", "-").lstrip("-") + "-project----mycel-agents-tool"
            )
            projects_dir = Path(tmpdir) / "projects"
            project_dir = projects_dir / f"-{encoded_name}"
            project_dir.mkdir(parents=True)

            session_path = project_dir / "abc123.jsonl"
            session_path.touch()

            name, path = get_session_name(session_path)

            assert path == str(tilde_dotfile_dir)
            assert name == "tool"

    def test_decodes_path_with_dashes_in_dirname(self):
        """Should correctly decode paths where directory names contain dashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/my-cool-project/src
            dir_with_dashes = Path(tmpdir) / "my-cool-project" / "src"
            dir_with_dashes.mkdir(parents=True)

            # Encoding: /tmp/xxx/my-cool-project/src -> -tmp-xxx-my-cool-project-src
            encoded_name = tmpdir.replace("/", "-").lstrip("-") + "-my-cool-project-src"
            projects_dir = Path(tmpdir) / "projects"
            project_dir = projects_dir / f"-{encoded_name}"
            project_dir.mkdir(parents=True)

            session_path = project_dir / "abc123.jsonl"
            session_path.touch()

            name, path = get_session_name(session_path)

            assert path == str(dir_with_dashes)
            assert name == "src"


class TestDecodePathGreedy:
    """Tests for _decode_path_greedy function."""

    def test_decodes_simple_path(self):
        """Should decode a simple path with no special characters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/home/user/projects
            target = Path(tmpdir) / "home" / "user" / "projects"
            target.mkdir(parents=True)

            # Encoded: home-user-projects (relative to tmpdir)
            # We need to test from root, so create full structure
            result = _decode_path_greedy(str(target).lstrip("/").replace("/", "-"))

            assert result == str(target)

    def test_decodes_path_with_dashes_in_dirname(self):
        """Should handle directory names containing dashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/my-cool-project/src
            target = Path(tmpdir) / "my-cool-project" / "src"
            target.mkdir(parents=True)

            encoded = str(target).lstrip("/").replace("/", "-")
            result = _decode_path_greedy(encoded)

            assert result == str(target)

    def test_decodes_path_with_dotfile(self):
        """Should handle dotfile directories when variant is provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/.mycel/agents
            target = Path(tmpdir) / ".mycel" / "agents"
            target.mkdir(parents=True)

            # The variant has already converted -- to -.
            # So input would be: tmp-xxx-.mycel-agents
            encoded = str(target).lstrip("/").replace("/", "-")
            result = _decode_path_greedy(encoded)

            assert result == str(target)

    def test_decodes_tilde_dotfile_path(self):
        """Should handle ~/. pattern (tilde + dotfile)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/project/~/.mycel/agents
            target = Path(tmpdir) / "project" / "~" / ".mycel" / "agents"
            target.mkdir(parents=True)

            # The variant has already converted ---- to -~-.
            # So input would be: tmp-xxx-project-~-.mycel-agents
            encoded = str(target).lstrip("/").replace("/", "-")
            result = _decode_path_greedy(encoded)

            assert result == str(target)

    def test_returns_none_for_nonexistent_path(self):
        """Should return None when path doesn't exist."""
        result = _decode_path_greedy("nonexistent-path-that-does-not-exist")
        assert result is None

    def test_handles_underscore_directories(self):
        """Should try underscore variants for directory names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/my_project/src (with underscore)
            target = Path(tmpdir) / "my_project" / "src"
            target.mkdir(parents=True)

            # Encoded as dashes: tmp-xxx-my-project-src
            # The algorithm should try my_project when my-project fails
            encoded = str(target).lstrip("/").replace("/", "-").replace("_", "-")
            result = _decode_path_greedy(encoded)

            assert result == str(target)

    def test_prefers_shorter_segments(self):
        """Should prefer shorter valid path segments (greedy behavior)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/a/b/c
            target = Path(tmpdir) / "a" / "b" / "c"
            target.mkdir(parents=True)

            encoded = str(target).lstrip("/").replace("/", "-")
            result = _decode_path_greedy(encoded)

            assert result == str(target)

    def test_handles_deep_nesting(self):
        """Should handle deeply nested paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a deeply nested path
            target = Path(tmpdir) / "a" / "b" / "c" / "d" / "e" / "f"
            target.mkdir(parents=True)

            encoded = str(target).lstrip("/").replace("/", "-")
            result = _decode_path_greedy(encoded)

            assert result == str(target)

    def test_handles_single_segment(self):
        """Should handle path with no dashes (single segment)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/project
            target = Path(tmpdir) / "project"
            target.mkdir(parents=True)

            encoded = str(target).lstrip("/").replace("/", "-")
            result = _decode_path_greedy(encoded)

            assert result == str(target)

    def test_handles_multiple_dashes_in_name(self):
        """Should handle directory names with multiple consecutive dashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/foo--bar/src (literal double dash in name)
            target = Path(tmpdir) / "foo--bar" / "src"
            target.mkdir(parents=True)

            encoded = str(target).lstrip("/").replace("/", "-")
            result = _decode_path_greedy(encoded)

            assert result == str(target)

    def test_greedy_chooses_correct_path_when_ambiguous(self):
        """Should find correct path even when intermediate segments could match multiple ways."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create only: /tmp/xxx/home/user/projects
            # NOT: /tmp/xxx/home-user/projects
            # The greedy algorithm should find the correct segmentation
            target = Path(tmpdir) / "home" / "user" / "projects"
            target.mkdir(parents=True)

            encoded = str(target).lstrip("/").replace("/", "-")
            result = _decode_path_greedy(encoded)

            assert result == str(target)

    def test_handles_collision_prefers_first_valid(self):
        """When multiple paths could match, greedy prefers shortest first segment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create both: /tmp/xxx/a/bc and /tmp/xxx/a-bc
            # Encoded "a-bc" is ambiguous
            path1 = Path(tmpdir) / "a" / "bc"
            path1.mkdir(parents=True)
            path2 = Path(tmpdir) / "a-bc"
            path2.mkdir(parents=True)

            # The greedy algorithm will find /a/bc first (shorter first segment)
            encoded = str(path1).lstrip("/").replace("/", "-")
            result = _decode_path_greedy(encoded)

            # Greedy finds the one with shorter segments first
            assert result == str(path1)


class TestGetSessionNameEdgeCases:
    """Edge case tests for get_session_name with special encodings."""

    def test_combined_dotfile_and_dashes(self):
        """Should handle paths with both dotfiles and dashes in names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/my-project/.config/settings
            target = Path(tmpdir) / "my-project" / ".config" / "settings"
            target.mkdir(parents=True)

            # Encoding: tmp-xxx-my-project--config-settings
            # The -- represents /.
            encoded_name = (
                str(tmpdir).replace("/", "-").lstrip("-")
                + "-my-project--config-settings"
            )
            projects_dir = Path(tmpdir) / "projects"
            project_dir = projects_dir / f"-{encoded_name}"
            project_dir.mkdir(parents=True)

            session_path = project_dir / "abc123.jsonl"
            session_path.touch()

            name, path = get_session_name(session_path)

            assert path == str(target)
            assert name == "settings"

    def test_multiple_dotfiles_in_path(self):
        """Should handle paths with multiple dotfile directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create: /tmp/xxx/.config/.local/data
            target = Path(tmpdir) / ".config" / ".local" / "data"
            target.mkdir(parents=True)

            # Encoding: tmp-xxx--config--local-data
            # Each -- represents /.
            encoded_name = (
                str(tmpdir).replace("/", "-").lstrip("-") + "--config--local-data"
            )
            projects_dir = Path(tmpdir) / "projects"
            project_dir = projects_dir / f"-{encoded_name}"
            project_dir.mkdir(parents=True)

            session_path = project_dir / "abc123.jsonl"
            session_path.touch()

            name, path = get_session_name(session_path)

            assert path == str(target)
            assert name == "data"


class TestIsSummaryFile:
    """Tests for is_summary_file function."""

    def test_identifies_summary_file(self):
        """Should identify summary files by filename pattern."""
        assert is_summary_file(Path("/path/abc123_summary.json"))
        assert is_summary_file(Path("/path/uuid-uuid-uuid_summary.json"))

    def test_regular_json_is_not_summary(self):
        """Should return False for regular JSON files."""
        assert not is_summary_file(Path("/path/config.json"))
        assert not is_summary_file(Path("/path/session.json"))
        assert not is_summary_file(Path("/path/abc123.json"))

    def test_jsonl_is_not_summary(self):
        """Should return False for JSONL files."""
        assert not is_summary_file(Path("/path/abc123.jsonl"))


class TestGetSessionIdFromSummaryFile:
    """Tests for get_session_id_from_summary_file function."""

    def test_extracts_session_id(self):
        """Should extract session ID from summary filename."""
        assert get_session_id_from_summary_file(
            Path("/path/abc123_summary.json")
        ) == "abc123"
        assert get_session_id_from_summary_file(
            Path("/path/uuid-uuid-uuid_summary.json")
        ) == "uuid-uuid-uuid"

    def test_returns_none_for_non_summary(self):
        """Should return None for non-summary files."""
        assert get_session_id_from_summary_file(Path("/path/config.json")) is None
        assert get_session_id_from_summary_file(Path("/path/abc123.jsonl")) is None


class TestGetSessionModel:
    """Tests for get_session_model function."""

    def test_gets_model_from_first_assistant_message(self):
        """Should extract model from first assistant message."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"content": "Hello"}
            }) + "\n")
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Hi"}],
                    "model": "claude-opus-4-5-20251101"
                }
            }) + "\n")
            f.flush()
            path = Path(f.name)

        model = get_session_model(path)
        assert model == "claude-opus-4-5-20251101"

    def test_returns_none_for_session_without_model(self):
        """Should return None if no model field in assistant messages."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"content": "Hello"}
            }) + "\n")
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hi"}]}
            }) + "\n")
            f.flush()
            path = Path(f.name)

        model = get_session_model(path)
        assert model is None

    def test_returns_none_for_nonexistent_file(self):
        """Should return None for nonexistent files."""
        model = get_session_model(Path("/nonexistent/session.jsonl"))
        assert model is None

    def test_returns_none_for_user_only_session(self):
        """Should return None if session has no assistant messages."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"content": "Hello"}
            }) + "\n")
            f.flush()
            path = Path(f.name)

        model = get_session_model(path)
        assert model is None
