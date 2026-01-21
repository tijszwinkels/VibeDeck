"""Summarizer configuration and prompt templates."""

from __future__ import annotations

from pathlib import Path


DEFAULT_PROMPT_TEMPLATE = """Summarize this coding session.

Session started: {session_started_at}

For the title (~5 words): Be clear about what the user is trying to do and the status. If the session mentions what the title should be, use that.
Format: "Category: Task - Status"
For Status, use one of: "In Progress", "Done". For Coding: "Waiting for Input", "Waiting for Testing", "Waiting for Commit", "Waiting for Merge", "Merged". "Done" if it was tested and directly committed on the main branch.
Examples: "Coding: Add dark mode - Done", "Research: Auth libraries - In progress"
Don't mark a task as done unless it clearly was a simple one-off query, or unless the user has indicates that it has been completed.

For the short summary (2-3 lines): Include the task status and what was last discussed.

For the executive summary: Comprehensive overview of what the user was trying to do,
steps taken, and current status.

For the branch: If the session mentions or shows a worktree-name or branch-name, use that here. For the main or master branch, use 'main' or 'master'. Leave empty if no git used or unclear.

Output ONLY valid JSON in this exact format, no other text:
{{"title": "short title here",
"short_summary": "2-3 line summary here",
"executive_summary": "comprehensive overview here",
"summary_generated_at": "{generated_at}",
"session_started_at": "{session_started_at}",
"session_id": "{session_id}",
"path": "{project_path}",
"branch": "worktree-or-branch-name-here"}}
"""


# Default keys to include in JSONL output (excludes executive_summary by default)
DEFAULT_OUTPUT_KEYS = [
    "title",
    "short_summary",
    "summary_generated_at",
    "session_started_at",
    "session_last_updated_at",
    "session_id",
    "path",
    "summary_file",
    "branch",
]


def format_prompt(
    template: str,
    session_id: str,
    project_path: str,
    generated_at: str,
    session_started_at: str,
) -> str:
    """Format the prompt template with session metadata.

    Args:
        template: Prompt template with placeholders.
        session_id: The session ID.
        project_path: The project path.
        generated_at: ISO timestamp.
        session_started_at: ISO timestamp of session start.

    Returns:
        Formatted prompt string.
    """
    return template.format(
        session_id=session_id,
        project_path=project_path or "Unknown",
        generated_at=generated_at,
        session_started_at=session_started_at or "Unknown",
    )


def get_prompt_template(
    prompt: str | None = None,
    prompt_file: Path | None = None,
) -> str:
    """Get the prompt template from config or use default.

    Args:
        prompt: Inline prompt string.
        prompt_file: Path to prompt file.

    Returns:
        The prompt template string.
    """
    if prompt:
        return prompt
    if prompt_file and prompt_file.exists():
        return prompt_file.read_text()
    return DEFAULT_PROMPT_TEMPLATE
