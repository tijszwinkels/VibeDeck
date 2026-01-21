"""Tests for the permissions module."""

import json
import pytest
from pathlib import Path

from vibedeck.permissions import (
    parse_permission_denials,
    update_permissions_file,
    generate_permission_options,
    is_sandbox_denial_message,
)


class TestParsePermissionDenials:
    """Tests for parse_permission_denials function."""

    def test_parses_denials_from_stream_json(self):
        """Test parsing permission denials from stream-json output."""
        stdout = '\n'.join([
            '{"type": "system", "message": "Starting..."}',
            '{"type": "assistant", "content": "I will run..."}',
            '{"type": "result", "subtype": "success", "permission_denials": [{"tool_name": "Bash", "tool_use_id": "toolu_123", "tool_input": {"command": "npm test"}}]}',
        ])
        denials = parse_permission_denials(stdout)
        assert len(denials) == 1
        assert denials[0]["tool_name"] == "Bash"
        assert denials[0]["tool_input"]["command"] == "npm test"

    def test_returns_empty_list_when_no_denials(self):
        """Test returns empty list when no permission denials."""
        stdout = '\n'.join([
            '{"type": "system", "message": "Starting..."}',
            '{"type": "result", "subtype": "success", "permission_denials": []}',
        ])
        denials = parse_permission_denials(stdout)
        assert denials == []

    def test_returns_empty_list_when_no_result(self):
        """Test returns empty list when no result message."""
        stdout = '\n'.join([
            '{"type": "system", "message": "Starting..."}',
            '{"type": "assistant", "content": "Hello!"}',
        ])
        denials = parse_permission_denials(stdout)
        assert denials == []

    def test_handles_empty_stdout(self):
        """Test handles empty stdout gracefully."""
        denials = parse_permission_denials("")
        assert denials == []

    def test_handles_invalid_json_lines(self):
        """Test ignores invalid JSON lines."""
        stdout = '\n'.join([
            'not valid json',
            '{"type": "result", "permission_denials": [{"tool_name": "Read"}]}',
        ])
        denials = parse_permission_denials(stdout)
        assert len(denials) == 1
        assert denials[0]["tool_name"] == "Read"

    def test_parses_multiple_denials(self):
        """Test parsing multiple permission denials."""
        stdout = json.dumps({
            "type": "result",
            "permission_denials": [
                {"tool_name": "Bash", "tool_use_id": "1", "tool_input": {"command": "rm -rf"}},
                {"tool_name": "Read", "tool_use_id": "2", "tool_input": {"file_path": "/etc/passwd"}},
            ]
        })
        denials = parse_permission_denials(stdout)
        assert len(denials) == 2
        assert denials[0]["tool_name"] == "Bash"
        assert denials[1]["tool_name"] == "Read"


class TestUpdatePermissionsFile:
    """Tests for update_permissions_file function."""

    def test_creates_new_file(self, tmp_path):
        """Test creates settings file if it doesn't exist."""
        settings_path = tmp_path / ".claude" / "settings.json"
        update_permissions_file(settings_path, ["Bash(npm test:*)"])

        assert settings_path.exists()
        with open(settings_path) as f:
            data = json.load(f)
        assert data["permissions"]["allow"] == ["Bash(npm test:*)"]

    def test_appends_to_existing_file(self, tmp_path):
        """Test appends permissions to existing file."""
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        with open(settings_path, "w") as f:
            json.dump({"permissions": {"allow": ["Read"]}}, f)

        update_permissions_file(settings_path, ["Write"])

        with open(settings_path) as f:
            data = json.load(f)
        assert "Read" in data["permissions"]["allow"]
        assert "Write" in data["permissions"]["allow"]

    def test_does_not_duplicate_permissions(self, tmp_path):
        """Test does not add duplicate permissions."""
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        with open(settings_path, "w") as f:
            json.dump({"permissions": {"allow": ["Read"]}}, f)

        update_permissions_file(settings_path, ["Read", "Write"])

        with open(settings_path) as f:
            data = json.load(f)
        # Read should only appear once
        assert data["permissions"]["allow"].count("Read") == 1
        assert "Write" in data["permissions"]["allow"]

    def test_preserves_other_settings(self, tmp_path):
        """Test preserves other settings in the file."""
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        with open(settings_path, "w") as f:
            json.dump({
                "model": "opus",
                "permissions": {"allow": [], "deny": ["dangerous"]},
            }, f)

        update_permissions_file(settings_path, ["Read"])

        with open(settings_path) as f:
            data = json.load(f)
        assert data["model"] == "opus"
        assert data["permissions"]["deny"] == ["dangerous"]


class TestGeneratePermissionOptions:
    """Tests for generate_permission_options function."""

    def test_bash_command_options(self):
        """Test generates options for Bash commands."""
        options = generate_permission_options("Bash", {"command": "npm test"})

        assert len(options) == 3
        assert options[0]["value"] == "Bash(npm test)"
        assert options[1]["value"] == "Bash(npm test:*)"
        assert options[2]["value"] == "Bash(npm:*)"

    def test_bash_single_word_command(self):
        """Test generates options for single-word Bash command."""
        options = generate_permission_options("Bash", {"command": "ls"})

        # Should only have 2 options (no "with arguments" option)
        assert len(options) == 2
        assert options[0]["value"] == "Bash(ls)"
        assert options[1]["value"] == "Bash(ls:*)"

    def test_read_file_options(self):
        """Test generates options for Read tool."""
        options = generate_permission_options("Read", {"file_path": "/home/user/project/file.txt"})

        # Directory glob patterns (/**) are buggy in Claude Code, so we only offer
        # exact file and tool-wide permissions
        assert len(options) == 2
        assert options[0]["value"] == "Read(/home/user/project/file.txt)"
        assert options[1]["value"] == "Read"

    def test_write_file_options(self):
        """Test generates options for Write tool."""
        options = generate_permission_options("Write", {"file_path": "/tmp/output.json"})

        assert len(options) == 2
        assert options[0]["value"] == "Write(/tmp/output.json)"
        assert options[1]["value"] == "Write"

    def test_edit_file_options(self):
        """Test generates options for Edit tool."""
        options = generate_permission_options("Edit", {"file_path": "/src/main.py"})

        assert len(options) == 2
        assert options[0]["value"] == "Edit(/src/main.py)"
        assert options[1]["value"] == "Edit"

    def test_unknown_tool_options(self):
        """Test generates generic options for unknown tools."""
        options = generate_permission_options("WebSearch", {"query": "test"})

        assert len(options) == 1
        assert options[0]["value"] == "WebSearch"
        assert "All operations" in options[0]["example"]

    def test_file_path_fallback(self):
        """Test uses 'path' key if 'file_path' not present."""
        options = generate_permission_options("Read", {"path": "/etc/config"})

        assert options[0]["value"] == "Read(/etc/config)"


class TestSandboxDenialDetection:
    """Tests for sandbox denial detection."""

    def test_detects_blocked_directory_message(self):
        """Test detects 'was blocked' sandbox denial pattern."""
        msg = "Error: /home/other/file was blocked. For security, Claude Code may only access files within allowed directories."
        assert is_sandbox_denial_message(msg) is True

    def test_detects_list_files_restriction(self):
        """Test detects 'only list files' sandbox denial pattern."""
        msg = "Error: Claude Code may only list files in the allowed working directories."
        assert is_sandbox_denial_message(msg) is True

    def test_detects_access_files_restriction(self):
        """Test detects 'only access files within' sandbox denial pattern."""
        msg = "Error: Claude Code may only access files within the project directory."
        assert is_sandbox_denial_message(msg) is True

    def test_does_not_match_permission_denial(self):
        """Test does not flag regular permission denials as sandbox."""
        msg = "Error: Permission denied for Bash tool"
        assert is_sandbox_denial_message(msg) is False

    def test_does_not_match_empty_message(self):
        """Test handles empty messages."""
        assert is_sandbox_denial_message("") is False

    def test_parse_denials_enriches_with_sandbox_flag(self):
        """Test parse_permission_denials enriches denials with sandbox info."""
        # Simulate output with a permission denial (not sandbox)
        stdout = json.dumps({
            "type": "result",
            "permission_denials": [
                {"tool_name": "Bash", "tool_use_id": "1", "tool_input": {"command": "npm test"}}
            ]
        })
        denials = parse_permission_denials(stdout)
        assert len(denials) == 1
        # Should have is_sandbox_denial field (False since no sandbox error message)
        assert denials[0].get("is_sandbox_denial") is False
        assert denials[0].get("error_message") == ""
