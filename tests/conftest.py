"""Pytest configuration and fixtures."""

import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def initialize_backend():
    """Initialize the backend before each test."""
    from vibedeck import server, sessions

    # Use the proper initialization function which also sets up CSS, etc.
    server.initialize_backend("claude-code")

    yield

    # Clean up
    sessions._backend = None
    server._backend = None
    server._css = None


@pytest.fixture
def temp_jsonl_file():
    """Create a temporary JSONL file with sample session data."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        # Write some sample messages
        messages = [
            {
                "type": "user",
                "timestamp": "2024-12-30T10:00:00.000Z",
                "message": {
                    "content": "Hello, Claude!"
                }
            },
            {
                "type": "assistant",
                "timestamp": "2024-12-30T10:00:01.000Z",
                "message": {
                    "content": [
                        {"type": "text", "text": "Hello! How can I help you today?"}
                    ]
                }
            },
        ]
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
        f.flush()
        yield Path(f.name)

    # Cleanup
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def sample_user_entry():
    """A sample user message entry."""
    return {
        "type": "user",
        "timestamp": "2024-12-30T10:00:00.000Z",
        "message": {
            "content": "Write a hello world function in Python"
        }
    }


@pytest.fixture
def sample_assistant_entry():
    """A sample assistant message entry with tool use."""
    return {
        "type": "assistant",
        "timestamp": "2024-12-30T10:00:05.000Z",
        "message": {
            "content": [
                {"type": "text", "text": "I'll create a hello world function for you."},
                {
                    "type": "tool_use",
                    "id": "tool_123",
                    "name": "Write",
                    "input": {
                        "file_path": "/tmp/hello.py",
                        "content": "def hello():\n    print('Hello, World!')\n"
                    }
                }
            ]
        }
    }


@pytest.fixture
def sample_tool_result_entry():
    """A sample user message with tool result."""
    return {
        "type": "user",
        "timestamp": "2024-12-30T10:00:06.000Z",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool_123",
                    "content": "File created successfully"
                }
            ]
        }
    }
