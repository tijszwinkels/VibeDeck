"""File tailer for watching Claude Code session files.

This module provides backward-compatible functions for session file handling.
The actual implementation has been moved to the backend modules.

For new code, use the backend protocol instead.
"""

# Re-export from backend for backward compatibility
from .backends.claude_code.tailer import (
    ClaudeCodeTailer as SessionTailer,
    has_messages,
    get_first_user_message,
)
from .backends.claude_code.discovery import (
    get_session_name,
    get_session_id,
    find_recent_sessions,
    find_most_recent_session,
)
from .backends.claude_code.pricing import (
    get_model_pricing,
    calculate_message_cost,
    get_session_token_usage,
)

# For backward compatibility, convert TokenUsage to dict
_original_get_session_token_usage = get_session_token_usage


def get_session_token_usage(session_path):
    """Calculate total token usage and cost from a session file.

    Returns a dictionary for backward compatibility.
    """
    usage = _original_get_session_token_usage(session_path)
    return usage.to_dict()


__all__ = [
    "SessionTailer",
    "has_messages",
    "get_first_user_message",
    "get_session_name",
    "get_session_id",
    "find_recent_sessions",
    "find_most_recent_session",
    "get_model_pricing",
    "calculate_message_cost",
    "get_session_token_usage",
]
