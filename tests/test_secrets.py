"""Tests for the secret detection module."""

import json
from pathlib import Path

import pytest

from vibedeck.secrets import (
    scan_session_for_secrets,
    format_secret_matches,
    SecretMatch,
    _scan_text_for_secrets,
)


# --- Fixtures ---


@pytest.fixture
def session_with_env_read(tmp_path):
    """Create a session that reads a .env file."""
    session_file = tmp_path / "session.jsonl"
    messages = [
        {
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Read my .env file"},
        },
        {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "content": [
                    {"type": "text", "text": "I'll read that for you."},
                    {
                        "type": "tool_use",
                        "id": "tool_123",
                        "name": "Read",
                        "input": {"file_path": "/home/user/project/.env"},
                    },
                ]
            },
        },
    ]
    with open(session_file, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return session_file


@pytest.fixture
def session_with_api_key(tmp_path):
    """Create a session with API key in tool result."""
    session_file = tmp_path / "session.jsonl"
    messages = [
        {
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Show me the config"},
        },
        {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Reading the config."},
                    {
                        "type": "tool_use",
                        "id": "tool_123",
                        "name": "Read",
                        "input": {"file_path": "/home/user/config.py"},
                    },
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2024-12-30T10:00:02.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_123",
                        "content": "OPENAI_API_KEY = 'sk-abc123456789defghijk'\nDEBUG = True",
                    }
                ]
            },
        },
    ]
    with open(session_file, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return session_file


@pytest.fixture
def session_with_bearer_token(tmp_path):
    """Create a session with a Bearer token in text."""
    session_file = tmp_path / "session.jsonl"
    messages = [
        {
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Use this token: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"},
        },
    ]
    with open(session_file, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return session_file


@pytest.fixture
def session_with_write_secret(tmp_path):
    """Create a session that writes a file with secrets."""
    session_file = tmp_path / "session.jsonl"
    messages = [
        {
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Create a config file"},
        },
        {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Creating config."},
                    {
                        "type": "tool_use",
                        "id": "tool_123",
                        "name": "Write",
                        "input": {
                            "file_path": "/home/user/config.py",
                            "content": "MY_SECRET_KEY = 'super-secret-value-1234'\n",
                        },
                    },
                ]
            },
        },
    ]
    with open(session_file, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return session_file


@pytest.fixture
def session_clean(tmp_path):
    """Create a clean session without secrets."""
    session_file = tmp_path / "session.jsonl"
    messages = [
        {
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Hello!"},
        },
        {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Hello! How can I help you?"}
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2024-12-30T10:01:00.000Z",
            "message": {"content": "Write a hello world function"},
        },
        {
            "type": "assistant",
            "timestamp": "2024-12-30T10:01:01.000Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Creating the function."},
                    {
                        "type": "tool_use",
                        "id": "tool_456",
                        "name": "Write",
                        "input": {
                            "file_path": "/tmp/hello.py",
                            "content": "def hello():\n    print('Hello!')\n",
                        },
                    },
                ]
            },
        },
    ]
    with open(session_file, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return session_file


# --- Tests ---


class TestScanTextForSecrets:
    """Tests for the _scan_text_for_secrets helper function."""

    def test_detects_api_key_assignment(self):
        text = "API_KEY = 'sk-abc123456789'"
        matches = _scan_text_for_secrets(text)
        assert len(matches) >= 1
        assert any("API_KEY" in m.pattern_name for m in matches)

    def test_detects_prefixed_api_key(self):
        text = "OPENAI_API_KEY = 'sk-abc123456789'"
        matches = _scan_text_for_secrets(text)
        assert len(matches) >= 1
        assert any("API_KEY" in m.pattern_name for m in matches)

    def test_detects_secret_key_assignment(self):
        text = "SECRET_KEY = 'my-super-secret-value'"
        matches = _scan_text_for_secrets(text)
        assert len(matches) >= 1
        assert any("SECRET" in m.pattern_name for m in matches)

    def test_detects_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWI"
        matches = _scan_text_for_secrets(text)
        assert len(matches) >= 1
        assert any("Bearer" in m.pattern_name for m in matches)

    def test_detects_private_key_block(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOg..."
        matches = _scan_text_for_secrets(text)
        assert len(matches) >= 1
        assert any("Private key" in m.pattern_name for m in matches)

    def test_detects_env_file_read(self):
        text = "**File:** `/home/user/project/.env`"
        matches = _scan_text_for_secrets(text)
        assert len(matches) >= 1
        assert any("env file" in m.pattern_name for m in matches)

    def test_detects_env_local_file_read(self):
        text = "**File:** `/app/.env.local`"
        matches = _scan_text_for_secrets(text)
        assert len(matches) >= 1

    def test_no_false_positive_on_env_mention(self):
        text = "You should create a .env file for your secrets"
        matches = _scan_text_for_secrets(text)
        # Should not match just mentioning .env
        assert not any("env file" in m.pattern_name for m in matches)

    def test_detects_auth_secret(self):
        text = "AUTH_SECRET=my-super-secret-auth-value"
        matches = _scan_text_for_secrets(text)
        assert len(matches) >= 1
        assert any("_SECRET" in m.pattern_name for m in matches)

    def test_detects_generic_secret(self):
        text = "MY_APP_SECRET = 'abcdefghijk'"
        matches = _scan_text_for_secrets(text)
        assert len(matches) >= 1

    def test_detects_database_url(self):
        text = "DATABASE_URL = 'postgresql://user:password@localhost/db'"
        matches = _scan_text_for_secrets(text)
        assert len(matches) >= 1
        assert any("DATABASE_URL" in m.pattern_name for m in matches)

    def test_no_false_positive_on_normal_text(self):
        text = "This is a normal message about coding."
        matches = _scan_text_for_secrets(text)
        assert len(matches) == 0

    def test_no_false_positive_on_code_snippet(self):
        text = """
def hello():
    print("Hello, world!")
    return True
"""
        matches = _scan_text_for_secrets(text)
        assert len(matches) == 0


class TestScanSessionForSecrets:
    """Tests for scanning full session files."""

    def test_detects_env_file_read(self, session_with_env_read):
        matches = scan_session_for_secrets(session_with_env_read)
        assert len(matches) >= 1
        assert any("env file" in m.pattern_name for m in matches)

    def test_detects_api_key_in_tool_result(self, session_with_api_key):
        matches = scan_session_for_secrets(session_with_api_key)
        assert len(matches) >= 1
        assert any("API_KEY" in m.pattern_name for m in matches)

    def test_detects_bearer_token_in_user_message(self, session_with_bearer_token):
        matches = scan_session_for_secrets(session_with_bearer_token)
        assert len(matches) >= 1
        assert any("Bearer" in m.pattern_name for m in matches)

    def test_detects_secret_in_write_content(self, session_with_write_secret):
        matches = scan_session_for_secrets(session_with_write_secret)
        assert len(matches) >= 1
        assert any("SECRET" in m.pattern_name for m in matches)

    def test_clean_session_has_no_secrets(self, session_clean):
        matches = scan_session_for_secrets(session_clean)
        assert len(matches) == 0


class TestFormatSecretMatches:
    """Tests for formatting secret matches for display."""

    def test_empty_matches_returns_no_secrets_message(self):
        result = format_secret_matches([])
        assert "No secrets detected" in result

    def test_formats_single_match(self):
        matches = [
            SecretMatch(
                pattern_name="API_KEY",
                matched_text="API_KEY = 'abc123456789'",
                context="config: API_KEY = 'abc123456789' ...",
            )
        ]
        result = format_secret_matches(matches)
        assert "1 potential secret" in result
        assert "API_KEY" in result

    def test_formats_multiple_matches(self):
        matches = [
            SecretMatch(
                pattern_name="API_KEY",
                matched_text="API_KEY = 'abc123456789'",
                context="...",
            ),
            SecretMatch(
                pattern_name="env file",
                matched_text=".env ",
                context="...",
            ),
        ]
        result = format_secret_matches(matches)
        assert "2 potential secret" in result
        assert "API_KEY" in result
        assert "env file" in result

    def test_groups_by_pattern_name(self):
        matches = [
            SecretMatch("API_KEY", "API_KEY = 'x123456789'", "..."),
            SecretMatch("API_KEY", "API_KEY = 'y123456789'", "..."),
            SecretMatch("env file", ".env ", "..."),
        ]
        result = format_secret_matches(matches)
        assert "API_KEY (2 match" in result
        assert "env file (1 match" in result


class TestEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_handles_empty_session(self, tmp_path):
        session_file = tmp_path / "empty.jsonl"
        session_file.write_text("")
        matches = scan_session_for_secrets(session_file)
        assert len(matches) == 0

    def test_handles_session_with_only_user_messages(self, tmp_path):
        session_file = tmp_path / "session.jsonl"
        messages = [
            {
                "type": "user",
                "timestamp": "2024-12-30T10:00:00.000Z",
                "message": {"content": "Hello!"},
            },
        ]
        with open(session_file, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")
        matches = scan_session_for_secrets(session_file)
        assert len(matches) == 0

    def test_handles_string_content(self, tmp_path):
        """Test handling of string content instead of list content."""
        session_file = tmp_path / "session.jsonl"
        messages = [
            {
                "type": "user",
                "timestamp": "2024-12-30T10:00:00.000Z",
                "message": {"content": "MY_API_KEY=secret123456789"},
            },
        ]
        with open(session_file, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")
        matches = scan_session_for_secrets(session_file)
        assert len(matches) >= 1
