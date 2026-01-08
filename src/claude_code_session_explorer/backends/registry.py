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
    ensure_backends_registered()

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
    ensure_backends_registered()
    return list(_backends.keys())


def get_all_backends(**config) -> list["CodingToolBackend"]:
    """Get instances of all registered backends.

    Args:
        **config: Backend-specific configuration passed to constructors.

    Returns:
        List of all available backend instances.
    """
    ensure_backends_registered()
    return [cls(**config) for cls in _backends.values()]


def get_multi_backend(**config) -> "CodingToolBackend":
    """Get a multi-backend wrapper that aggregates all backends.

    Args:
        **config: Backend-specific configuration passed to constructors.

    Returns:
        MultiBackend instance wrapping all available backends.
    """
    from .multi import MultiBackend

    backends = get_all_backends(**config)
    return MultiBackend(backends)


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


_backends_initialized = False


def ensure_backends_registered() -> None:
    """Ensure built-in backends are registered.

    This function is idempotent - it only registers backends once.
    Call this explicitly before using get_backend() or list_backends()
    to avoid import-time side effects.
    """
    global _backends_initialized
    if _backends_initialized:
        return

    _backends_initialized = True

    # Register Claude Code backend
    try:
        from .claude_code import ClaudeCodeBackend

        register_backend("claude-code", ClaudeCodeBackend)
    except ImportError as e:
        import logging

        logging.getLogger(__name__).warning(f"Claude Code backend not available: {e}")

    # Register OpenCode backend
    try:
        from .opencode import OpenCodeBackend

        register_backend("opencode", OpenCodeBackend)
    except ImportError as e:
        import logging

        logging.getLogger(__name__).warning(f"OpenCode backend not available: {e}")
