"""Summarizer module for generating session summaries.

This module provides functionality to automatically generate summaries
for Claude Code sessions using the Claude CLI with --no-session-persistence.
"""

from .config import (
    DEFAULT_OUTPUT_KEYS,
    DEFAULT_PROMPT_TEMPLATE,
    format_prompt,
    get_prompt_template,
)
from .generator import ParsedResponse, Summarizer, SummaryResult
from .output import LogWriter
from .tracker import IdleTracker, SummaryState, TrackedSession

__all__ = [
    # Config
    "DEFAULT_OUTPUT_KEYS",
    "DEFAULT_PROMPT_TEMPLATE",
    "format_prompt",
    "get_prompt_template",
    # Generator
    "ParsedResponse",
    "Summarizer",
    "SummaryResult",
    # Output
    "LogWriter",
    # Tracker
    "IdleTracker",
    "SummaryState",
    "TrackedSession",
]
