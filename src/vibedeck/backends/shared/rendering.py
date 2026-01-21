"""Shared rendering utilities for all backends.

This module provides common rendering functions used by both Claude Code
and OpenCode backend renderers, eliminating code duplication.
"""

from __future__ import annotations

import html
import json
import re

from jinja2 import Environment, PackageLoader
import markdown

# Shared Jinja2 environment
jinja_env = Environment(
    loader=PackageLoader("vibedeck", "templates"),
    autoescape=True,
)

# Load macros template and expose macros
_macros_template = jinja_env.get_template("macros.html")
macros = _macros_template.module

# Regex to match git commit output: [branch hash] message
COMMIT_PATTERN = re.compile(r"\[[\w\-/]+ ([a-f0-9]{7,})\] (.+?)(?:\n|$)")

# Module-level variable for GitHub repo
_github_repo: str | None = None


def set_github_repo(repo: str | None) -> None:
    """Set the GitHub repo for commit links."""
    global _github_repo
    _github_repo = repo


def get_github_repo() -> str | None:
    """Get the current GitHub repo setting."""
    return _github_repo


def render_markdown_text(text: str, safe: bool = False) -> str:
    """Render markdown text to HTML.

    Args:
        text: The markdown text to render.
        safe: If True, disable raw HTML to prevent XSS attacks.
              Raw HTML will be escaped and displayed as text.
    """
    if not text:
        return ""
    if safe:
        # Escape HTML before markdown processing to prevent XSS
        text = html.escape(text)
    return markdown.markdown(text, extensions=["fenced_code", "tables"])


def render_user_text(text: str) -> str:
    """Render user text to HTML, escaping HTML entities for safety.

    User messages should not contain raw HTML, so we escape angle brackets
    before markdown processing to prevent HTML injection (e.g., <title> tags
    being interpreted by the browser).
    """
    if not text:
        return ""
    # Escape HTML entities before markdown processing
    # This prevents user-typed <tag> from being interpreted as HTML
    text = html.escape(text)
    return markdown.markdown(text, extensions=["fenced_code", "tables"])


def is_json_like(text: str) -> bool:
    """Check if text looks like JSON."""
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    return (text.startswith("{") and text.endswith("}")) or (
        text.startswith("[") and text.endswith("]")
    )


def format_json(obj) -> str:
    """Format object as pretty-printed JSON in a pre block."""
    try:
        if isinstance(obj, str):
            obj = json.loads(obj)
        formatted = json.dumps(obj, indent=2, ensure_ascii=False)
        return f'<pre class="json">{html.escape(formatted)}</pre>'
    except (json.JSONDecodeError, TypeError):
        return f"<pre>{html.escape(str(obj))}</pre>"


def make_msg_id(timestamp: str) -> str:
    """Create a DOM-safe message ID from timestamp."""
    return f"msg-{timestamp.replace(':', '-').replace('.', '-')}"


def render_git_commits(content: str) -> str | None:
    """Render git commit output with styled cards.

    Looks for git commit patterns in the content and renders them
    as styled commit cards with optional GitHub links.

    Args:
        content: String content that may contain git commit output.

    Returns:
        HTML string with commit cards if commits found, None otherwise.
    """
    commits_found = list(COMMIT_PATTERN.finditer(content))
    if not commits_found:
        return None

    parts = []
    last_end = 0
    for match in commits_found:
        # Add any content before this commit
        before = content[last_end : match.start()].strip()
        if before:
            parts.append(f"<pre>{html.escape(before)}</pre>")

        commit_hash = match.group(1)
        commit_msg = match.group(2)
        parts.append(macros.commit_card(commit_hash, commit_msg, _github_repo))
        last_end = match.end()

    # Add any remaining content after last commit
    after = content[last_end:].strip()
    if after:
        parts.append(f"<pre>{html.escape(after)}</pre>")

    return "".join(parts)
