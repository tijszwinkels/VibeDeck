"""Tests for the SessionTailer class."""

import json
import tempfile
from pathlib import Path

import pytest

from vibedeck.tailer import (
    SessionTailer,
    find_most_recent_session,
    find_recent_sessions,
    get_session_id,
    get_session_name,
)


class TestSessionTailer:
    """Tests for SessionTailer."""

    def test_read_all_returns_messages(self, temp_jsonl_file):
        """Test that read_all returns all messages from file."""
        tailer = SessionTailer(temp_jsonl_file)
        messages = tailer.read_all()

        assert len(messages) == 2
        assert messages[0]["type"] == "user"
        assert messages[1]["type"] == "assistant"

    def test_read_all_filters_non_messages(self):
        """Test that read_all filters out non-message entries."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            entries = [
                {"type": "system", "data": "ignored"},
                {"type": "user", "timestamp": "2024-12-30T10:00:00Z", "message": {"content": "Hello"}},
                {"type": "summary", "summary": "ignored"},
                {"type": "assistant", "timestamp": "2024-12-30T10:00:01Z", "message": {"content": [{"type": "text", "text": "Hi"}]}},
            ]
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)
            messages = tailer.read_all()

            assert len(messages) == 2
            assert messages[0]["type"] == "user"
            assert messages[1]["type"] == "assistant"
        finally:
            path.unlink()

    def test_read_new_lines_incremental(self):
        """Test that read_new_lines reads incrementally."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "timestamp": "1", "message": {"content": "First"}}) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)

            # First read
            messages = tailer.read_new_lines()
            assert len(messages) == 1
            assert messages[0]["message"]["content"] == "First"

            # Append more data
            with open(path, "a") as f:
                f.write(json.dumps({"type": "assistant", "timestamp": "2", "message": {"content": [{"type": "text", "text": "Second"}]}}) + "\n")

            # Second read should only get new message
            messages = tailer.read_new_lines()
            assert len(messages) == 1
            assert messages[0]["type"] == "assistant"

            # Third read should be empty
            messages = tailer.read_new_lines()
            assert len(messages) == 0
        finally:
            path.unlink()

    def test_handles_incomplete_lines(self):
        """Test that incomplete lines are buffered correctly."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "timestamp": "1", "message": {"content": "Complete"}}) + "\n")
            f.write('{"type": "assistant", "timestamp": "2", "message": {"content": [{"type": "text", "text": "Incomple')  # No newline
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)
            messages = tailer.read_new_lines()

            # Should only get the complete message
            assert len(messages) == 1
            assert messages[0]["message"]["content"] == "Complete"

            # Complete the line
            with open(path, "a") as f:
                f.write('te"}]}}\n')

            # Now should get the second message
            messages = tailer.read_new_lines()
            assert len(messages) == 1
            assert messages[0]["type"] == "assistant"
        finally:
            path.unlink()

    def test_handles_malformed_json(self):
        """Test that malformed JSON lines are skipped."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "timestamp": "1", "message": {"content": "Valid"}}) + "\n")
            f.write("this is not json\n")
            f.write(json.dumps({"type": "assistant", "timestamp": "2", "message": {"content": [{"type": "text", "text": "Also valid"}]}}) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)
            messages = tailer.read_new_lines()

            # Should get both valid messages, skip the invalid one
            assert len(messages) == 2
        finally:
            path.unlink()

    def test_handles_missing_file(self):
        """Test that missing file returns empty list."""
        tailer = SessionTailer(Path("/nonexistent/file.jsonl"))
        messages = tailer.read_new_lines()
        assert messages == []

    def test_seek_to_end_sets_position(self):
        """Test that seek_to_end() sets position to file size without reading."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "timestamp": "1", "message": {"content": "Hello"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "timestamp": "2", "message": {"content": [{"type": "text", "text": "Hi"}]}}) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)
            assert tailer.position == 0

            tailer.seek_to_end()

            # Position should be at end of file
            assert tailer.position == path.stat().st_size
            # Message index should still be 0 (no messages read)
            assert tailer.message_index == 0
        finally:
            path.unlink()

    def test_read_new_lines_after_seek_to_end(self):
        """Test that read_new_lines() only returns new content after seek_to_end()."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "timestamp": "1", "message": {"content": "Initial"}}) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)
            tailer.seek_to_end()

            # read_new_lines should return nothing (we're at end)
            messages = tailer.read_new_lines()
            assert len(messages) == 0

            # Append new content
            with open(path, "a") as f:
                f.write(json.dumps({"type": "assistant", "timestamp": "2", "message": {"content": [{"type": "text", "text": "New"}]}}) + "\n")

            # Now read_new_lines should return only the new message
            messages = tailer.read_new_lines()
            assert len(messages) == 1
            assert messages[0]["type"] == "assistant"
        finally:
            path.unlink()

    def test_read_all_independent_of_position(self):
        """Test that read_all() returns all messages regardless of seek position."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "timestamp": "1", "message": {"content": "First"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "timestamp": "2", "message": {"content": [{"type": "text", "text": "Second"}]}}) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)
            tailer.seek_to_end()

            # read_all should still return all messages
            messages = tailer.read_all()
            assert len(messages) == 2
            assert messages[0]["message"]["content"] == "First"
            assert messages[1]["type"] == "assistant"

            # Position should be unchanged after read_all
            assert tailer.position == path.stat().st_size
        finally:
            path.unlink()

    def test_seek_to_end_handles_missing_file(self):
        """Test that seek_to_end() handles missing file gracefully."""
        tailer = SessionTailer(Path("/nonexistent/file.jsonl"))
        # Should not raise, just set position to 0
        tailer.seek_to_end()
        assert tailer.position == 0

    def test_waiting_for_input_after_assistant_text(self):
        """Test that waiting_for_input is True after assistant text message."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # User sends a message
            f.write(json.dumps({
                "type": "user",
                "timestamp": "1",
                "message": {"content": "Hello"}
            }) + "\n")
            # Assistant responds with text
            f.write(json.dumps({
                "type": "assistant",
                "timestamp": "2",
                "message": {"content": [{"type": "text", "text": "Hi there!"}]}
            }) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)
            tailer.read_new_lines()
            assert tailer.waiting_for_input is True
        finally:
            path.unlink()

    def test_waiting_for_input_false_after_tool_use(self):
        """Test that waiting_for_input is False after assistant tool_use."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # User sends a message
            f.write(json.dumps({
                "type": "user",
                "timestamp": "1",
                "message": {"content": "Run a command"}
            }) + "\n")
            # Assistant calls a tool
            f.write(json.dumps({
                "type": "assistant",
                "timestamp": "2",
                "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}
            }) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)
            tailer.read_new_lines()
            assert tailer.waiting_for_input is False
        finally:
            path.unlink()

    def test_waiting_for_input_false_after_user_input(self):
        """Test that waiting_for_input is False after new user input."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Assistant was waiting
            f.write(json.dumps({
                "type": "assistant",
                "timestamp": "1",
                "message": {"content": [{"type": "text", "text": "What would you like?"}]}
            }) + "\n")
            # User sends new input
            f.write(json.dumps({
                "type": "user",
                "timestamp": "2",
                "message": {"content": "Do something else"}
            }) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)
            tailer.read_new_lines()
            assert tailer.waiting_for_input is False
        finally:
            path.unlink()

    def test_waiting_for_input_false_after_tool_result(self):
        """Test that waiting_for_input is False after tool_result (agent processing)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Assistant calls a tool
            f.write(json.dumps({
                "type": "assistant",
                "timestamp": "1",
                "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}
            }) + "\n")
            # Tool result comes back
            f.write(json.dumps({
                "type": "user",
                "timestamp": "2",
                "message": {"content": [{"type": "tool_result", "content": "file1.txt\nfile2.txt"}]}
            }) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            tailer = SessionTailer(path)
            tailer.read_new_lines()
            assert tailer.waiting_for_input is False
        finally:
            path.unlink()


class TestFindMostRecentSession:
    """Tests for find_most_recent_session."""

    def test_returns_none_for_nonexistent_dir(self):
        """Test that nonexistent directory returns None."""
        result = find_most_recent_session(Path("/nonexistent/path"))
        assert result is None

    def test_returns_none_for_empty_dir(self):
        """Test that empty directory returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_most_recent_session(Path(tmpdir))
            assert result is None

    def test_finds_most_recent(self):
        """Test that most recently modified file is returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create older file with older timestamp
            older = tmppath / "older.jsonl"
            older.write_text('{"type": "user", "timestamp": "2024-01-01T10:00:00Z"}\n')

            # Create newer file with newer timestamp
            newer = tmppath / "newer.jsonl"
            newer.write_text('{"type": "user", "timestamp": "2024-01-01T11:00:00Z"}\n')

            result = find_most_recent_session(tmppath)
            assert result == newer

    def test_includes_agent_files_by_default(self):
        """Test that agent-* files are included by default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create regular file with older timestamp
            regular = tmppath / "session.jsonl"
            regular.write_text('{"type": "user", "timestamp": "2024-01-01T10:00:00Z"}\n')

            # Create agent file with newer timestamp (should be returned as it's now included)
            agent = tmppath / "agent-123.jsonl"
            agent.write_text('{"type": "user", "timestamp": "2024-01-01T11:00:00Z"}\n')

            result = find_most_recent_session(tmppath)
            # Agent file is newer and should be returned (subagents included by default)
            assert result == agent


class TestFindRecentSessions:
    """Tests for find_recent_sessions."""

    def test_returns_empty_for_nonexistent_dir(self):
        """Test that nonexistent directory returns empty list."""
        result = find_recent_sessions(Path("/nonexistent/path"))
        assert result == []

    def test_returns_empty_for_empty_dir(self):
        """Test that empty directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_recent_sessions(Path(tmpdir))
            assert result == []

    def test_respects_limit(self):
        """Test that limit parameter is respected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create multiple files with different timestamps
            for i in range(5):
                f = tmppath / f"session_{i}.jsonl"
                f.write_text(f'{{"type": "user", "timestamp": "2024-01-01T1{i}:00:00Z"}}\n')

            result = find_recent_sessions(tmppath, limit=3)
            assert len(result) == 3

    def test_sorted_by_timestamp(self):
        """Test that results are sorted by message timestamp, newest first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create files with known timestamp order
            older = tmppath / "older.jsonl"
            older.write_text('{"type": "user", "timestamp": "2024-01-01T10:00:00Z"}\n')

            newer = tmppath / "newer.jsonl"
            newer.write_text('{"type": "user", "timestamp": "2024-01-01T11:00:00Z"}\n')

            result = find_recent_sessions(tmppath)
            assert result[0] == newer
            assert result[1] == older


class TestGetSessionId:
    """Tests for get_session_id."""

    def test_returns_stem(self):
        """Test that session ID is the filename without extension."""
        path = Path("/home/user/.claude/projects/foo/abc123.jsonl")
        assert get_session_id(path) == "abc123"

    def test_handles_uuid(self):
        """Test with UUID-style filename."""
        path = Path("/tmp/0f984efa-f0bd-4219-9fa2-4235c879e487.jsonl")
        assert get_session_id(path) == "0f984efa-f0bd-4219-9fa2-4235c879e487"


class TestGetSessionName:
    """Tests for get_session_name."""

    def test_extracts_project_name_with_dashes(self):
        """Test extraction of project name that contains dashes."""
        path = Path("/home/user/.claude/projects/-Users-tijs-projects-claude-code-live/abc123.jsonl")
        # Returns tuple (name, path) - fallback since path doesn't exist
        name, project_path = get_session_name(path)
        assert name == "Users-tijs-projects-claude-code-live"

    def test_handles_simple_path(self):
        """Test with simple project path."""
        path = Path("/home/user/.claude/projects/-Users-tijs-projects-myproject/session.jsonl")
        name, project_path = get_session_name(path)
        # Fallback since path doesn't exist
        assert name == "Users-tijs-projects-myproject"

    def test_handles_tmp_path(self):
        """Test with tmp directory path."""
        path = Path("/home/user/.claude/projects/-Users-tijs-tmp-llm-council/session.jsonl")
        name, project_path = get_session_name(path)
        # Fallback since path doesn't exist
        assert name == "Users-tijs-tmp-llm-council"

    def test_handles_nested_code_path(self):
        """Test with code directory path."""
        path = Path("/home/user/.claude/projects/-home-user-code-python-webapp/session.jsonl")
        name, project_path = get_session_name(path)
        # Fallback since path doesn't exist
        assert name == "home-user-code-python-webapp"

    def test_fallback_to_folder_name(self):
        """Test fallback when no markers found."""
        path = Path("/tmp/some-folder/session.jsonl")
        # Should handle gracefully
        name, project_path = get_session_name(path)
        assert name is not None
        assert len(name) > 0

    def test_resolves_existing_path(self):
        """Test that existing paths are resolved correctly."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test directory structure
            tmppath = Path(tmpdir)
            test_project = tmppath / "test_project"
            test_project.mkdir()

            # Simulate a Claude projects path pointing to the test_project
            # The encoded path would be like -tmp-xxx-test_project
            encoded_name = str(tmppath).replace("/", "-").lstrip("-") + "-test_project"
            claude_projects = tmppath / ".claude" / "projects" / f"-{encoded_name}"
            claude_projects.mkdir(parents=True)

            session_file = claude_projects / "session.jsonl"
            session_file.write_text('{"type": "user"}\n')

            name, project_path = get_session_name(session_file)
            # Should resolve to the actual directory
            assert name == "test_project"
            assert project_path == str(test_project)

    def test_handles_underscores_in_path(self):
        """Test that paths with underscores (encoded as dashes) are resolved."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Create a directory with underscores
            test_project = tmppath / "my_cool_project"
            test_project.mkdir()

            # Simulate the Claude-encoded path (underscores become dashes)
            encoded_name = str(tmppath).replace("/", "-").lstrip("-") + "-my-cool-project"
            claude_projects = tmppath / ".claude" / "projects" / f"-{encoded_name}"
            claude_projects.mkdir(parents=True)

            session_file = claude_projects / "session.jsonl"
            session_file.write_text('{"type": "user"}\n')

            name, project_path = get_session_name(session_file)
            # Should resolve to the actual directory with underscores
            assert name == "my_cool_project"
            assert project_path == str(test_project)
