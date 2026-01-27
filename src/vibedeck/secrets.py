"""Secret detection for session transcripts.

This module scans session transcripts for potential secrets before
allowing them to be uploaded to public gists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .export import export_markdown


@dataclass
class SecretMatch:
    """A detected secret pattern match."""

    pattern_name: str
    matched_text: str
    context: str  # A snippet of surrounding text


# Patterns that indicate potential secrets
# Each tuple is (name, compiled_regex)
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Environment files being read/written (match in **File:** context from markdown)
    ("env file read", re.compile(r"\*\*File:\*\*\s*`[^`]*\.env(?:\.local|\.production|\.development|\.staging|\.test)?`", re.IGNORECASE)),

    # API keys and tokens - generic patterns that catch prefixed variants
    ("API_KEY", re.compile(r"[A-Z_]*API_KEY\s*[=:]\s*['\"]?[a-zA-Z0-9_\-]{8,}", re.IGNORECASE)),
    ("SECRET_KEY", re.compile(r"[A-Z_]*SECRET_?KEY\s*[=:]\s*['\"]?[a-zA-Z0-9_\-]{8,}", re.IGNORECASE)),
    ("ACCESS_TOKEN", re.compile(r"[A-Z_]*ACCESS_?TOKEN\s*[=:]\s*['\"]?[a-zA-Z0-9_\-]{8,}", re.IGNORECASE)),
    ("AUTH_TOKEN", re.compile(r"[A-Z_]*AUTH_?TOKEN\s*[=:]\s*['\"]?[a-zA-Z0-9_\-]{8,}", re.IGNORECASE)),
    ("PRIVATE_KEY", re.compile(r"[A-Z_]*PRIVATE_?KEY\s*[=:]\s*['\"]?[a-zA-Z0-9_\-]{8,}", re.IGNORECASE)),
    ("PASSWORD", re.compile(r"[A-Z_]*PASSWORD\s*[=:]\s*['\"]?[^\s'\"]{4,}", re.IGNORECASE)),
    ("DATABASE_URL", re.compile(r"DATABASE_URL\s*[=:]\s*['\"]?[^\s'\"]+", re.IGNORECASE)),

    # Bearer tokens
    ("Bearer token", re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}")),

    # Private key blocks
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),

    # Generic _SECRET, _TOKEN, _CREDENTIAL patterns (catches AUTH_SECRET, etc.)
    ("_SECRET", re.compile(r"[A-Z_]*_SECRET\s*[=:]\s*['\"]?[^\s'\"]{8,}")),
    ("_TOKEN", re.compile(r"[A-Z_]+_TOKEN\s*[=:]\s*['\"]?[^\s'\"]{8,}")),
    ("_CREDENTIAL", re.compile(r"[A-Z_]+_CREDENTIAL\s*[=:]\s*['\"]?[^\s'\"]{8,}")),
]


def _extract_context(text: str, match: re.Match, context_chars: int = 50) -> str:
    """Extract surrounding context for a match."""
    start = max(0, match.start() - context_chars)
    end = min(len(text), match.end() + context_chars)

    context = text[start:end]

    # Add ellipsis if truncated
    if start > 0:
        context = "..." + context
    if end < len(text):
        context = context + "..."

    return context.replace("\n", " ")


def scan_session_for_secrets(session_path: Path) -> list[SecretMatch]:
    """Scan a session transcript for potential secrets.

    Converts the session to markdown and scans for secret patterns.

    Args:
        session_path: Path to session file

    Returns:
        List of SecretMatch objects describing found secrets
    """
    # Convert session to markdown - this handles both Claude Code and OpenCode
    markdown_text = export_markdown(session_path, output_path=None, hide_tools=False)

    return _scan_text_for_secrets(markdown_text)


def _scan_text_for_secrets(text: str) -> list[SecretMatch]:
    """Scan text for secret patterns."""
    matches = []

    for pattern_name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            matched = match.group(0)

            # Skip very long matches (probably false positives)
            if len(matched) > 500:
                continue

            matches.append(SecretMatch(
                pattern_name=pattern_name,
                matched_text=matched[:100] + "..." if len(matched) > 100 else matched,
                context=_extract_context(text, match),
            ))

    return matches


def format_secret_matches(matches: list[SecretMatch]) -> str:
    """Format secret matches for display to the user."""
    if not matches:
        return "No secrets detected."

    lines = [f"Found {len(matches)} potential secret(s) in the session:"]
    lines.append("")

    # Group by pattern name
    by_pattern: dict[str, list[SecretMatch]] = {}
    for match in matches:
        if match.pattern_name not in by_pattern:
            by_pattern[match.pattern_name] = []
        by_pattern[match.pattern_name].append(match)

    for pattern_name, pattern_matches in by_pattern.items():
        lines.append(f"  {pattern_name} ({len(pattern_matches)} match(es)):")
        for m in pattern_matches[:3]:  # Show at most 3 examples per pattern
            lines.append(f"    - {m.matched_text[:60]}...")
        if len(pattern_matches) > 3:
            lines.append(f"    ... and {len(pattern_matches) - 3} more")
        lines.append("")

    return "\n".join(lines)
