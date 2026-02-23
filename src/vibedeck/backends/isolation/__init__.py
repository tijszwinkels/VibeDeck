"""Isolation backend for multi-user Claude Code sessions in Docker/gVisor.

This backend discovers sessions across per-user directories and wraps
CLI interaction in docker exec commands. It reuses the claude-code backend's
tailer, renderer, and pricing since the JSONL format is identical.
"""

from .backend import IsolationBackend

__all__ = ["IsolationBackend"]
