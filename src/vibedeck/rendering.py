"""HTML rendering for Claude Code messages.

This module provides backward-compatible rendering functions.
The actual rendering logic has been moved to the backend modules.

For new code, use backend.get_message_renderer() instead.
"""

from importlib.resources import files
from jinja2 import Environment, PackageLoader

# Re-export from backend for backward compatibility
from .backends.claude_code.renderer import (
    render_message,
    render_content_block,
    render_markdown_text,
    set_github_repo,
    is_json_like,
    format_json,
    is_tool_result_message,
)
from .backends.claude_code.pricing import calculate_message_cost

# Set up Jinja2 environment (shared across backends)
_jinja_env = Environment(
    loader=PackageLoader("vibedeck", "templates"),
    autoescape=True,
)


def get_template(name: str):
    """Get a Jinja2 template by name."""
    return _jinja_env.get_template(name)


def _load_static_file(filename: str) -> str:
    """Load a static file from the package."""
    return files("vibedeck").joinpath("static", filename).read_text()


# Load CSS and JS from static files
CSS = _load_static_file("style.css")
JS = _load_static_file("script.js")

__all__ = [
    "render_message",
    "render_content_block",
    "render_markdown_text",
    "set_github_repo",
    "is_json_like",
    "format_json",
    "is_tool_result_message",
    "calculate_message_cost",
    "get_template",
    "CSS",
    "JS",
]
