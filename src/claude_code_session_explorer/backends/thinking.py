"""Thinking level detection from user message keywords.

Detects thinking keywords in user messages to set appropriate thinking token budgets.
Mirrors Claude Code CLI's keyword-based thinking levels.

Levels:
- Default (2048): No keywords detected
- Low (4000): "think"
- Medium (10000): "think hard", "megathink", etc.
- Max (31999): "ultrathink", "think harder", etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ThinkingLevel:
    """Thinking level configuration."""

    name: str
    budget_tokens: int


# Thinking levels in order of precedence (most specific first)
LEVELS = {
    "max": ThinkingLevel("max", 31999),
    "medium": ThinkingLevel("medium", 10000),
    "low": ThinkingLevel("low", 4000),
    "default": ThinkingLevel("default", 2048),
}

# Patterns for each level (checked in order: max -> medium -> low)
# Max level: 31,999 tokens
MAX_PATTERNS = [
    r"\bultrathink\b",
    r"\bthink\s+harder\b",
    r"\bthink\s+intensely\b",
    r"\bthink\s+longer\b",
    r"\bthink\s+really\s+hard\b",
    r"\bthink\s+super\s+hard\b",
    r"\bthink\s+very\s+hard\b",
]

# Medium level: 10,000 tokens
MEDIUM_PATTERNS = [
    r"\bmegathink\b",
    r"\bthink\s+hard\b",
    r"\bthink\s+deeply\b",
    r"\bthink\s+more\b",
    r"\bthink\s+about\s+it\b",
    r"\bthink\s+a\s+lot\b",
]

# Low level: 4,000 tokens
LOW_PATTERNS = [
    r"\bthink\b",
]


def detect_thinking_level(message: str) -> ThinkingLevel:
    """Detect thinking level from message keywords.

    Scans the message for trigger keywords and returns the appropriate
    thinking level. Checks in order of specificity (max first) to ensure
    "think harder" isn't matched as just "think".

    Args:
        message: User message text to scan.

    Returns:
        ThinkingLevel with name and budget_tokens.
    """
    message_lower = message.lower()

    # Check max level patterns first (most specific)
    for pattern in MAX_PATTERNS:
        if re.search(pattern, message_lower):
            return LEVELS["max"]

    # Check medium level patterns
    for pattern in MEDIUM_PATTERNS:
        if re.search(pattern, message_lower):
            return LEVELS["medium"]

    # Check low level patterns
    for pattern in LOW_PATTERNS:
        if re.search(pattern, message_lower):
            return LEVELS["low"]

    # Default level
    return LEVELS["default"]


def get_thinking_env(message: str) -> dict[str, str]:
    """Get environment variables for thinking level.

    Args:
        message: User message text to scan for keywords.

    Returns:
        Dict with MAX_THINKING_TOKENS set to the appropriate budget.
    """
    level = detect_thinking_level(message)
    return {"MAX_THINKING_TOKENS": str(level.budget_tokens)}
