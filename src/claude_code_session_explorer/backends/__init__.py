"""Coding tool backend abstractions.

This module provides a protocol-based abstraction for different coding tools
(Claude Code, OpenCode, etc.) allowing the session explorer to work with
multiple backends through a common interface.
"""

from .protocol import (
    CodingToolBackend,
    SessionTailerProtocol,
    MessageRendererProtocol,
    SessionMetadata,
    MessageEntry,
    TokenUsage,
    SendMessageResult,
)
from .registry import (
    register_backend,
    get_backend,
    list_backends,
    get_all_backends,
    get_multi_backend,
)

__all__ = [
    # Protocols
    "CodingToolBackend",
    "SessionTailerProtocol",
    "MessageRendererProtocol",
    # Data classes
    "SessionMetadata",
    "MessageEntry",
    "TokenUsage",
    "SendMessageResult",
    # Registry
    "register_backend",
    "get_backend",
    "list_backends",
    "get_all_backends",
    "get_multi_backend",
]
