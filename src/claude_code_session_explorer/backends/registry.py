"""Backend registry for discovering and configuring backends."""

from __future__ import annotations

from typing import Type, TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import CodingToolBackend

_backends: dict[str, Type["CodingToolBackend"]] = {}
_default_backend: str = "claude-code"


def register_backend(name: str, backend_class: Type["CodingToolBackend"]) -> None:
    """Register a backend implementation.

    Args:
        name: Unique name for the backend (e.g., 'claude-code', 'opencode').
        backend_class: Class that implements CodingToolBackend protocol.
    """
    _backends[name] = backend_class


def get_backend(name: str | None = None, **config) -> "CodingToolBackend":
    """Get a backend instance by name.

    Args:
        name: Backend name. If None, uses the default backend.
        **config: Backend-specific configuration passed to constructor.

    Returns:
        Configured backend instance.

    Raises:
        ValueError: If the backend name is not registered.
    """
    if name is None:
        name = _default_backend

    if name not in _backends:
        available = ", ".join(_backends.keys()) if _backends else "none"
        raise ValueError(f"Unknown backend: {name}. Available: {available}")

    return _backends[name](**config)


def list_backends() -> list[str]:
    """List available backend names.

    Returns:
        List of registered backend names.
    """
    return list(_backends.keys())


def set_default_backend(name: str) -> None:
    """Set the default backend name.

    Args:
        name: Backend name to use as default.

    Raises:
        ValueError: If the backend name is not registered.
    """
    global _default_backend
    if name not in _backends:
        raise ValueError(f"Unknown backend: {name}")
    _default_backend = name


def get_default_backend() -> str:
    """Get the default backend name.

    Returns:
        The default backend name.
    """
    return _default_backend


# Auto-register built-in backends when the module is imported
def _auto_register_backends() -> None:
    """Auto-register built-in backends."""
    try:
        from .claude_code import ClaudeCodeBackend
        register_backend("claude-code", ClaudeCodeBackend)
    except ImportError:
        pass  # Claude Code backend not available


_auto_register_backends()
