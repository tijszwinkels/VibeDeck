"""Export functionality for session transcripts.

This module provides functions to export Claude Code and OpenCode sessions to
static HTML or Markdown files. The HTML export produces paginated pages with
an index, similar to the claude-code-transcripts package.

Supports both:
- Claude Code JSONL files (~/.claude/projects/*/SESSION_ID.jsonl)
- OpenCode sessions (~/.local/share/opencode/storage/ with session ID)
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Literal

import click
from jinja2 import Environment, PackageLoader

from .backends.shared.rendering import (
    jinja_env,
    macros,
    render_markdown_text,
    render_user_text,
    make_msg_id,
    set_github_repo,
    get_github_repo,
    COMMIT_PATTERN,
)
from .backends.claude_code.renderer import render_message as claude_render_message
from .backends.claude_code.tailer import ClaudeCodeTailer
from .backends.opencode.renderer import render_message as opencode_render_message
from .backends.opencode.tailer import OpenCodeTailer

# Type alias for session backends
SessionBackend = Literal["claude_code", "opencode"]

# Constants
PROMPTS_PER_PAGE = 5
LONG_TEXT_THRESHOLD = (
    300  # Characters - text blocks longer than this are shown in index
)

# Regex to detect GitHub repo from git push output
GITHUB_REPO_PATTERN = re.compile(
    r"github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)/pull/new/"
)

# Load export-specific templates
_export_env = Environment(
    loader=PackageLoader("claude_code_session_explorer", "templates/export"),
    autoescape=True,
)


def get_export_template(name: str):
    """Get an export template by name."""
    return _export_env.get_template(name)


def copy_css_to_output(output_dir: Path) -> None:
    """Copy the styles.css file to the output directory.

    Uses importlib.resources to locate the CSS file in the package.
    """
    css_source = files("claude_code_session_explorer").joinpath(
        "templates/export/styles.css"
    )
    css_dest = output_dir / "styles.css"
    css_dest.write_text(css_source.read_text(encoding="utf-8"), encoding="utf-8")


# JavaScript for static export
JS = """
document.querySelectorAll('time[data-timestamp]').forEach(function(el) {
    const timestamp = el.getAttribute('data-timestamp');
    const date = new Date(timestamp);
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    const timeStr = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    if (isToday) { el.textContent = timeStr; }
    else { el.textContent = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + timeStr; }
});
document.querySelectorAll('pre.json').forEach(function(el) {
    let text = el.textContent;
    text = text.replace(/"([^"]+)":/g, '<span style="color: #ce93d8">"$1"</span>:');
    text = text.replace(/: "([^"]*)"/g, ': <span style="color: #81d4fa">"$1"</span>');
    text = text.replace(/: (\\d+)/g, ': <span style="color: #ffcc80">$1</span>');
    text = text.replace(/: (true|false|null)/g, ': <span style="color: #f48fb1">$1</span>');
    el.innerHTML = text;
});
document.querySelectorAll('.truncatable').forEach(function(wrapper) {
    const content = wrapper.querySelector('.truncatable-content');
    const btn = wrapper.querySelector('.expand-btn');
    if (content.scrollHeight > 250) {
        wrapper.classList.add('truncated');
        btn.addEventListener('click', function() {
            if (wrapper.classList.contains('truncated')) { wrapper.classList.remove('truncated'); wrapper.classList.add('expanded'); btn.textContent = 'Show less'; }
            else { wrapper.classList.remove('expanded'); wrapper.classList.add('truncated'); btn.textContent = 'Show more'; }
        });
    }
});
"""

# JavaScript to fix relative URLs when served via gisthost.github.io
# Based on simonw/claude-code-transcripts approach
GIST_PREVIEW_JS = r"""
(function() {
    var hostname = window.location.hostname;
    if (hostname !== 'gisthost.github.io' && hostname !== 'gistpreview.github.io') return;

    // URL format: https://gisthost.github.io/?GIST_ID/filename.html
    var match = window.location.search.match(/^\?([^/]+)/);
    if (!match) return;
    var gistId = match[1];

    function rewriteLinks(root) {
        (root || document).querySelectorAll('a[href]').forEach(function(link) {
            var href = link.getAttribute('href');
            // Skip already-rewritten links
            if (href.startsWith('?')) return;
            // Skip external links and anchors
            if (href.startsWith('http') || href.startsWith('#') || href.startsWith('//')) return;
            // Handle anchor in relative URL (e.g., page-001.html#msg-123)
            var parts = href.split('#');
            var filename = parts[0];
            var anchor = parts.length > 1 ? '#' + parts[1] : '';
            link.setAttribute('href', '?' + gistId + '/' + filename + anchor);
        });
    }

    // Run immediately
    rewriteLinks();

    // Also run on DOMContentLoaded in case DOM isn't ready yet
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { rewriteLinks(); });
    }

    // Use MutationObserver to catch dynamically added content
    var observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            mutation.addedNodes.forEach(function(node) {
                if (node.nodeType === 1) {
                    rewriteLinks(node);
                    if (node.tagName === 'A' && node.getAttribute('href')) {
                        var href = node.getAttribute('href');
                        if (!href.startsWith('?') && !href.startsWith('http') &&
                            !href.startsWith('#') && !href.startsWith('//')) {
                            var parts = href.split('#');
                            var filename = parts[0];
                            var anchor = parts.length > 1 ? '#' + parts[1] : '';
                            node.setAttribute('href', '?' + gistId + '/' + filename + anchor);
                        }
                    }
                }
            });
        });
    });

    function startObserving() {
        if (document.body) {
            observer.observe(document.body, { childList: true, subtree: true });
        } else {
            setTimeout(startObserving, 10);
        }
    }
    startObserving();

    // Handle fragment navigation after dynamic content loads
    function scrollToFragment() {
        var hash = window.location.hash;
        if (!hash) return false;
        var targetId = hash.substring(1);
        var target = document.getElementById(targetId);
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return true;
        }
        return false;
    }

    if (!scrollToFragment()) {
        var delays = [100, 300, 500, 1000, 2000];
        delays.forEach(function(delay) {
            setTimeout(scrollToFragment, delay);
        });
    }
})();
"""


def extract_text_from_content(content) -> str:
    """Extract plain text from message content.

    Handles both string content and array content (list of blocks).
    """
    if isinstance(content, str):
        return content.strip()
    elif isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    texts.append(text)
        return " ".join(texts).strip()
    return ""


def detect_github_repo(entries: list[dict]) -> str | None:
    """Detect GitHub repo from git push output in tool results.

    Looks for patterns like:
    - github.com/owner/repo/pull/new/branch (from git push messages)
    """
    for entry in entries:
        message = entry.get("message", {})
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, str):
                    match = GITHUB_REPO_PATTERN.search(result_content)
                    if match:
                        return match.group(1)
    return None


def auto_output_name(session_path: Path) -> str:
    """Generate output directory name from session file path.

    Extracts meaningful name from the session path, handling encoded
    project folder names like '-home-user-projects-myproject'.
    """
    # Try to use the session file stem
    name = session_path.stem

    # If the parent folder looks like a Claude Code project folder,
    # try to extract a meaningful project name
    parent_name = session_path.parent.name
    if parent_name.startswith("-"):
        # Encoded path like '-home-user-projects-myproject'
        parts = parent_name.split("-")
        # Skip common path components
        skip_dirs = {
            "home",
            "users",
            "projects",
            "code",
            "repos",
            "src",
            "dev",
            "work",
            "mnt",
            "c",
        }
        meaningful_parts = []
        for part in parts:
            if not part:
                continue
            if part.lower() in skip_dirs:
                continue
            meaningful_parts.append(part)
        if meaningful_parts:
            name = "-".join(meaningful_parts[-2:])  # Take last 2 meaningful parts

    return name


def analyze_conversation(
    messages: list[dict], backend: SessionBackend = "claude_code"
) -> dict:
    """Analyze messages in a conversation to extract stats and long texts.

    Args:
        messages: List of message entries
        backend: The backend type for these messages

    Returns:
        Dictionary with tool_counts, long_texts, and commits
    """
    tool_counts: dict[str, int] = {}
    long_texts: list[str] = []
    commits: list[tuple[str, str, str]] = []  # (hash, message, timestamp)

    for entry in messages:
        if backend == "claude_code":
            message_data = entry.get("message", {})
            timestamp = entry.get("timestamp", "")
            content = message_data.get("content", [])

            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")

                if block_type == "tool_use":
                    tool_name = block.get("name", "Unknown")
                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

                elif block_type == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, str):
                        for match in COMMIT_PATTERN.finditer(result_content):
                            commits.append((match.group(1), match.group(2), timestamp))

                elif block_type == "text":
                    text = block.get("text", "")
                    if len(text) >= LONG_TEXT_THRESHOLD:
                        long_texts.append(text)

        else:  # opencode
            timestamp = get_entry_timestamp(entry, backend)
            parts = entry.get("parts", [])

            for part in parts:
                part_type = part.get("type", "")

                if part_type == "tool":
                    tool_name = part.get("tool", "") or part.get("name", "Unknown")
                    if isinstance(tool_name, str):
                        tool_name = tool_name.capitalize()
                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

                    # Check tool output for commits
                    state = part.get("state", {})
                    tool_output = state.get("output")
                    if isinstance(tool_output, str):
                        for match in COMMIT_PATTERN.finditer(tool_output):
                            commits.append((match.group(1), match.group(2), timestamp))

                elif part_type == "text":
                    text = part.get("text", "")
                    if len(text) >= LONG_TEXT_THRESHOLD:
                        long_texts.append(text)

    return {
        "tool_counts": tool_counts,
        "long_texts": long_texts,
        "commits": commits,
    }


def format_tool_stats(tool_counts: dict[str, int]) -> str:
    """Format tool counts into a concise summary string."""
    if not tool_counts:
        return ""

    abbrev = {
        "Bash": "bash",
        "Read": "read",
        "Write": "write",
        "Edit": "edit",
        "Glob": "glob",
        "Grep": "grep",
        "Task": "task",
        "TodoWrite": "todo",
        "WebFetch": "fetch",
        "WebSearch": "search",
    }

    parts = []
    for name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        short_name = abbrev.get(name, name.lower())
        parts.append(f"{count} {short_name}")

    return " · ".join(parts)


def generate_pagination_html(current_page: int, total_pages: int) -> str:
    """Generate pagination HTML for a page."""
    return macros.pagination(current_page, total_pages)


def generate_index_pagination_html(total_pages: int) -> str:
    """Generate pagination HTML for the index page."""
    return macros.index_pagination(total_pages)


def detect_session_backend(session_path: Path) -> SessionBackend:
    """Detect the backend type for a session.

    Args:
        session_path: Path to session file or OpenCode session ID

    Returns:
        'claude_code' or 'opencode'

    Raises:
        ValueError if session type cannot be determined
    """
    # Check if it's a JSONL file (Claude Code)
    if session_path.suffix == ".jsonl" and session_path.is_file():
        return "claude_code"

    # Check if it's an OpenCode session ID (directory-based storage)
    opencode_storage = get_opencode_storage_dir()
    session_msg_dir = opencode_storage / "message" / session_path.name

    if session_msg_dir.exists():
        return "opencode"

    # Also check if session_path itself points to the opencode storage
    if "opencode" in str(session_path) and (session_path / "message").exists():
        return "opencode"

    # Check if session_path is just a session ID string
    if not session_path.suffix and session_msg_dir.exists():
        return "opencode"

    raise ValueError(
        f"Cannot determine session type for: {session_path}\n"
        "Expected a .jsonl file (Claude Code) or OpenCode session ID."
    )


def get_opencode_storage_dir() -> Path:
    """Get the OpenCode storage directory."""
    return Path.home() / ".local" / "share" / "opencode" / "storage"


def parse_claude_code_entries(session_path: Path) -> list[dict]:
    """Parse a Claude Code JSONL file and return list of entries.

    Only includes user and assistant message types.
    """
    entries = []
    with open(session_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("type") in ("user", "assistant"):
                    entries.append(entry)
            except json.JSONDecodeError:
                continue
    return entries


def parse_opencode_entries(session_id: str) -> list[dict]:
    """Parse OpenCode session and return list of entries.

    Returns entries in the OpenCode format with 'info' and 'parts' keys.
    """
    storage_dir = get_opencode_storage_dir()
    tailer = OpenCodeTailer(storage_dir, session_id)
    return tailer.read_all()


def parse_session_entries(
    session_path: Path, backend: SessionBackend | None = None
) -> tuple[list[dict], SessionBackend]:
    """Parse a session file and return list of entries with the detected backend.

    Args:
        session_path: Path to session file or OpenCode session ID
        backend: Optional backend override. If None, auto-detects.

    Returns:
        Tuple of (entries, backend_type)
    """
    if backend is None:
        backend = detect_session_backend(session_path)

    if backend == "claude_code":
        entries = parse_claude_code_entries(session_path)
    else:  # opencode
        # session_path might be a full path or just session ID
        session_id = (
            session_path.name if session_path.suffix == "" else session_path.stem
        )
        # For opencode, we need to figure out the session ID
        # If path contains 'message', extract session ID from parent
        if session_path.name == "message" or "message" in str(session_path):
            # Path like ~/.local/share/opencode/storage/message/SESSION_ID/
            parts = session_path.parts
            for i, part in enumerate(parts):
                if part == "message" and i + 1 < len(parts):
                    session_id = parts[i + 1]
                    break
        entries = parse_opencode_entries(session_id)

    return entries, backend


def get_entry_user_text(entry: dict, backend: SessionBackend) -> str | None:
    """Extract user text from an entry based on backend type."""
    if backend == "claude_code":
        message_data = entry.get("message", {})
        content = message_data.get("content", "")
        # Check if this is actual user text (not tool result)
        if isinstance(content, list) and content:
            if isinstance(content[0], dict) and content[0].get("type") == "tool_result":
                return None
        return extract_text_from_content(content) or None
    else:  # opencode
        info = entry.get("info", {})
        parts = entry.get("parts", [])
        if info.get("role") != "user":
            return None
        for part in parts:
            if part.get("type") == "text":
                text = part.get("text", "").strip()
                if text:
                    return text
        return None


def get_entry_timestamp(entry: dict, backend: SessionBackend) -> str:
    """Get timestamp from an entry based on backend type."""
    if backend == "claude_code":
        return entry.get("timestamp", "")
    else:  # opencode
        info = entry.get("info", {})
        time_data = info.get("time", {})
        created = time_data.get("created")
        if created:
            from datetime import datetime, timezone

            try:
                dt = datetime.fromtimestamp(created / 1000, tz=timezone.utc)
                return dt.isoformat()
            except (ValueError, TypeError, OSError):
                pass
        return ""


def get_entry_role(entry: dict, backend: SessionBackend) -> str:
    """Get role (user/assistant) from an entry based on backend type."""
    if backend == "claude_code":
        return entry.get("type", "")
    else:  # opencode
        info = entry.get("info", {})
        return info.get("role", "")


def render_entry(entry: dict, backend: SessionBackend) -> str:
    """Render an entry to HTML based on backend type."""
    if backend == "claude_code":
        return claude_render_message(entry)
    else:  # opencode
        return opencode_render_message(entry)


def generate_html(
    session_path: Path,
    output_dir: Path,
    github_repo: str | None = None,
) -> Path:
    """Generate static HTML transcript from session file.

    Args:
        session_path: Path to session file (JSONL for Claude Code, or session ID for OpenCode)
        output_dir: Directory to write HTML files
        github_repo: Optional GitHub repo for commit links (owner/repo)

    Returns:
        Path to generated index.html
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy CSS file to output directory
    copy_css_to_output(output_dir)

    # Read all entries and detect backend
    entries, backend = parse_session_entries(session_path)

    # Auto-detect GitHub repo if not provided (only works for Claude Code format)
    if github_repo is None and backend == "claude_code":
        github_repo = detect_github_repo(entries)
        if github_repo:
            click.echo(f"Auto-detected GitHub repo: {github_repo}")

    # Set GitHub repo for commit card rendering
    set_github_repo(github_repo)

    # Group entries into conversations (split on user messages)
    conversations = []
    current_conv = None

    for entry in entries:
        role = get_entry_role(entry, backend)
        timestamp = get_entry_timestamp(entry, backend)
        is_compact_summary = (
            entry.get("isCompactSummary", False) if backend == "claude_code" else False
        )

        is_user_prompt = False
        user_text = None

        if role == "user":
            user_text = get_entry_user_text(entry, backend)
            if user_text:
                is_user_prompt = True

        if is_user_prompt:
            if current_conv:
                conversations.append(current_conv)
            current_conv = {
                "user_text": user_text,
                "timestamp": timestamp,
                "messages": [entry],
                "is_continuation": bool(is_compact_summary),
                "backend": backend,
            }
        elif current_conv:
            current_conv["messages"].append(entry)

    if current_conv:
        conversations.append(current_conv)

    total_convs = len(conversations)
    total_pages = max(1, (total_convs + PROMPTS_PER_PAGE - 1) // PROMPTS_PER_PAGE)

    # Generate paginated pages
    for page_num in range(1, total_pages + 1):
        start_idx = (page_num - 1) * PROMPTS_PER_PAGE
        end_idx = min(start_idx + PROMPTS_PER_PAGE, total_convs)
        page_convs = conversations[start_idx:end_idx]

        messages_html = []
        for conv in page_convs:
            is_first = True
            conv_backend = conv.get("backend", backend)
            for entry in conv["messages"]:
                msg_html = render_entry(entry, conv_backend)
                if msg_html:
                    # Wrap continuation summaries in collapsed details
                    if is_first and conv.get("is_continuation"):
                        msg_html = f'<details class="continuation"><summary>Session continuation summary</summary>{msg_html}</details>'
                    messages_html.append(msg_html)
                is_first = False

        pagination_html = generate_pagination_html(page_num, total_pages)
        page_template = get_export_template("page.html")
        page_content = page_template.render(
            js=JS,
            page_num=page_num,
            total_pages=total_pages,
            pagination_html=pagination_html,
            messages_html="".join(messages_html),
            backend=backend,
        )

        page_path = output_dir / f"page-{page_num:03d}.html"
        page_path.write_text(page_content, encoding="utf-8")
        click.echo(f"Generated {page_path.name}")

    # Calculate overall stats and collect commits for timeline
    total_tool_counts: dict[str, int] = {}
    total_messages = 0
    all_commits: list[
        tuple[str, str, str, int, int]
    ] = []  # (timestamp, hash, msg, page_num, conv_idx)

    for i, conv in enumerate(conversations):
        total_messages += len(conv["messages"])
        conv_backend = conv.get("backend", backend)
        stats = analyze_conversation(conv["messages"], conv_backend)
        for tool, count in stats["tool_counts"].items():
            total_tool_counts[tool] = total_tool_counts.get(tool, 0) + count
        page_num = (i // PROMPTS_PER_PAGE) + 1
        for commit_hash, commit_msg, commit_ts in stats["commits"]:
            all_commits.append((commit_ts, commit_hash, commit_msg, page_num, i))

    total_tool_calls = sum(total_tool_counts.values())
    total_commits = len(all_commits)

    # Build timeline items: prompts and commits merged by timestamp
    timeline_items = []

    # Add prompts
    prompt_num = 0
    for i, conv in enumerate(conversations):
        if conv.get("is_continuation"):
            continue
        user_text = conv.get("user_text", "") or ""
        if user_text.startswith("Stop hook feedback:"):
            continue

        prompt_num += 1
        page_num = (i // PROMPTS_PER_PAGE) + 1
        msg_id = make_msg_id(conv["timestamp"])
        link = f"page-{page_num:03d}.html#{msg_id}"
        rendered_content = render_markdown_text(user_text)

        # Collect all messages including from subsequent continuation conversations
        all_messages = list(conv["messages"])
        for j in range(i + 1, len(conversations)):
            if not conversations[j].get("is_continuation"):
                break
            all_messages.extend(conversations[j]["messages"])

        # Analyze conversation for stats
        conv_backend = conv.get("backend", backend)
        stats = analyze_conversation(all_messages, conv_backend)
        tool_stats_str = format_tool_stats(stats["tool_counts"])

        long_texts_html = ""
        for lt in stats["long_texts"]:
            rendered_lt = render_markdown_text(lt)
            long_texts_html += macros.index_long_text(rendered_lt)

        stats_html = macros.index_stats(tool_stats_str, long_texts_html)

        item_html = macros.index_item(
            prompt_num, link, conv["timestamp"], rendered_content, stats_html
        )
        timeline_items.append((conv["timestamp"], "prompt", item_html))

    # Add commits as separate timeline items
    for commit_ts, commit_hash, commit_msg, page_num, conv_idx in all_commits:
        item_html = macros.index_commit(commit_hash, commit_msg, commit_ts, github_repo)
        timeline_items.append((commit_ts, "commit", item_html))

    # Sort by timestamp
    timeline_items.sort(key=lambda x: x[0])
    index_items = [item[2] for item in timeline_items]

    # Generate index page
    index_pagination = generate_index_pagination_html(total_pages)
    index_template = get_export_template("index.html")
    index_content = index_template.render(
        js=JS,
        pagination_html=index_pagination,
        prompt_num=prompt_num,
        total_messages=total_messages,
        total_tool_calls=total_tool_calls,
        total_commits=total_commits,
        total_pages=total_pages,
        index_items_html="".join(index_items),
        backend=backend,
    )

    index_path = output_dir / "index.html"
    index_path.write_text(index_content, encoding="utf-8")
    click.echo(
        f"Generated {index_path.name} ({total_convs} prompts, {total_pages} pages)"
    )

    return index_path


def inject_gist_preview_js(output_dir: Path) -> None:
    """Inject gist preview JavaScript into all HTML files in the output directory."""
    for html_file in output_dir.glob("*.html"):
        content = html_file.read_text(encoding="utf-8")
        if "</body>" in content:
            content = content.replace(
                "</body>", f"<script>{GIST_PREVIEW_JS}</script>\n</body>"
            )
            html_file.write_text(content, encoding="utf-8")


def create_gist(output_dir: Path, public: bool = False) -> tuple[str, str]:
    """Create a GitHub gist from the HTML files in output_dir.

    Returns:
        Tuple of (gist_id, gist_url)

    Raises:
        click.ClickException on failure
    """
    html_files = list(output_dir.glob("*.html"))
    if not html_files:
        raise click.ClickException("No HTML files found to upload to gist.")

    cmd = ["gh", "gist", "create"]
    cmd.extend(str(f) for f in sorted(html_files))
    if public:
        cmd.append("--public")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        gist_url = result.stdout.strip()
        gist_id = gist_url.rstrip("/").split("/")[-1]
        return gist_id, gist_url
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise click.ClickException(f"Failed to create gist: {error_msg}")
    except FileNotFoundError:
        raise click.ClickException(
            "gh CLI not found. Install it from https://cli.github.com/ and run 'gh auth login'."
        )


def export_markdown(
    session_path: Path,
    output_path: Path | None = None,
) -> str:
    """Export session to Markdown.

    Args:
        session_path: Path to session file (JSONL for Claude Code, or session ID for OpenCode)
        output_path: Optional output file path. If None, returns markdown string.
                    If path ends with '/', creates file with auto-generated name.

    Returns:
        If output_path is None: the markdown string
        If output_path is provided: the path to the written file
    """
    entries, backend = parse_session_entries(session_path)
    markdown_content = format_session_as_markdown(entries, session_path, backend)

    if output_path is None:
        return markdown_content

    # Handle trailing slash (directory)
    if str(output_path).endswith("/") or output_path.is_dir():
        output_path = output_path / f"{auto_output_name(session_path)}.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_content, encoding="utf-8")
    return str(output_path)


def format_session_as_markdown(
    entries: list[dict], session_path: Path, backend: SessionBackend = "claude_code"
) -> str:
    """Convert parsed session messages to Markdown format.

    Args:
        entries: List of parsed session entries
        session_path: Path to the session file (for metadata)
        backend: The backend type for these entries

    Returns:
        Formatted markdown string
    """
    lines = []

    # Header
    title = (
        "Claude Code Transcript" if backend == "claude_code" else "OpenCode Transcript"
    )
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"**Session:** {session_path.stem}")
    lines.append(f"**Project:** {session_path.parent.name}")

    # Get first timestamp if available
    if entries:
        first_ts = get_entry_timestamp(entries[0], backend)
        if first_ts:
            lines.append(f"**Date:** {first_ts}")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Process messages
    prompt_num = 0
    for entry in entries:
        role = get_entry_role(entry, backend)
        timestamp = get_entry_timestamp(entry, backend)

        if backend == "claude_code":
            message_data = entry.get("message", {})
            content = message_data.get("content", "")
        else:  # opencode
            content = entry.get("parts", [])

        if role == "user":
            # Check if this is a tool result or actual user prompt
            is_tool_result = (
                isinstance(content, list)
                and content
                and isinstance(content[0], dict)
                and content[0].get("type") == "tool_result"
            )

            if is_tool_result:
                # Format tool results
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        result = block.get("content", "")
                        is_error = block.get("is_error", False)
                        if is_error:
                            lines.append("**Tool Error:**")
                        else:
                            lines.append("**Tool Output:**")
                        lines.append("```")
                        # Truncate very long output
                        if isinstance(result, str):
                            if len(result) > 2000:
                                result = result[:2000] + "\n... (truncated)"
                            lines.append(result)
                        else:
                            lines.append(json.dumps(result, indent=2))
                        lines.append("```")
                        lines.append("")
            else:
                # User prompt
                prompt_num += 1
                lines.append(f"## Prompt {prompt_num}")
                lines.append("")
                lines.append(f"**User** ({timestamp}):")
                lines.append("")
                text = extract_text_from_content(content)
                lines.append(text)
                lines.append("")

        elif role == "assistant":
            lines.append(f"**Assistant** ({timestamp}):")
            lines.append("")

            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue

                    block_type = block.get("type", "")

                    if block_type == "text":
                        lines.append(block.get("text", ""))
                        lines.append("")

                    elif block_type == "thinking":
                        # Claude Code thinking
                        lines.append("*Thinking:*")
                        lines.append("")
                        lines.append(f"> {block.get('thinking', '')}")
                        lines.append("")

                    elif block_type == "reasoning":
                        # OpenCode reasoning (similar to thinking)
                        reasoning = block.get("reasoning", "")
                        lines.append("*Reasoning:*")
                        lines.append("")
                        for line in reasoning.split("\n"):
                            lines.append(f"> {line}")
                        lines.append("")

                    elif block_type == "tool_use":
                        # Claude Code tool use
                        tool_name = block.get("name", "Unknown")
                        tool_input = block.get("input", {})
                        _format_tool_md(lines, tool_name, tool_input)

                    elif block_type == "tool":
                        # OpenCode tool part
                        tool_field = block.get("tool", "")
                        if isinstance(tool_field, str):
                            tool_name = tool_field or block.get("name", "Unknown")
                        elif isinstance(tool_field, dict):
                            tool_name = tool_field.get("name", "Unknown")
                        else:
                            tool_name = block.get("name", "Unknown")

                        # Capitalize for consistency
                        tool_name = tool_name.capitalize() if tool_name else "Unknown"

                        state = block.get("state", {})
                        tool_input = state.get("input", {})
                        tool_output = state.get("output", {})
                        tool_error = state.get("error", "")

                        _format_tool_md(lines, tool_name, tool_input)

                        # Include output/error if present
                        if tool_error:
                            lines.append("**Error:**")
                            lines.append("```")
                            lines.append(tool_error)
                            lines.append("```")
                            lines.append("")
                        elif tool_output:
                            output_str = (
                                tool_output
                                if isinstance(tool_output, str)
                                else json.dumps(tool_output, indent=2)
                            )
                            if len(output_str) > 2000:
                                output_str = output_str[:2000] + "\n... (truncated)"
                            lines.append("**Output:**")
                            lines.append("```")
                            lines.append(output_str)
                            lines.append("```")
                            lines.append("")

                    elif block_type == "step-finish":
                        # OpenCode step finish - skip or add cost info
                        pass

            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def _format_tool_md(lines: list, tool_name: str, tool_input: dict) -> None:
    """Format a tool call for markdown output."""
    lines.append(f"### Tool: {tool_name}")

    tool_name_lower = tool_name.lower()

    if tool_name_lower == "bash":
        command = tool_input.get("command", "")
        desc = tool_input.get("description", "")
        if desc:
            lines.append(f"*{desc}*")
        lines.append("```bash")
        lines.append(command)
        lines.append("```")

    elif tool_name_lower == "edit":
        file_path = tool_input.get("file_path", tool_input.get("filePath", ""))
        old_string = tool_input.get("old_string", tool_input.get("oldString", ""))
        new_string = tool_input.get("new_string", tool_input.get("newString", ""))
        lines.append(f"**File:** `{file_path}`")
        lines.append("")
        lines.append("**Old:**")
        lines.append("```")
        lines.append(old_string)
        lines.append("```")
        lines.append("")
        lines.append("**New:**")
        lines.append("```")
        lines.append(new_string)
        lines.append("```")

    elif tool_name_lower == "write":
        file_path = tool_input.get("file_path", tool_input.get("filePath", ""))
        content_str = tool_input.get("content", "")
        lines.append(f"**File:** `{file_path}`")
        lines.append("")
        lines.append("```")
        # Truncate very long content
        if len(content_str) > 2000:
            content_str = content_str[:2000] + "\n... (truncated)"
        lines.append(content_str)
        lines.append("```")

    elif tool_name_lower == "read":
        file_path = tool_input.get("file_path", tool_input.get("filePath", ""))
        lines.append(f"**File:** `{file_path}`")

    elif tool_name_lower == "glob":
        pattern = tool_input.get("pattern", "")
        path = tool_input.get("path", "")
        lines.append(f"**Pattern:** `{pattern}`")
        if path:
            lines.append(f"**Path:** `{path}`")

    elif tool_name_lower == "grep":
        pattern = tool_input.get("pattern", "")
        include = tool_input.get("include", "")
        path = tool_input.get("path", "")
        lines.append(f"**Pattern:** `{pattern}`")
        if include:
            lines.append(f"**Include:** `{include}`")
        if path:
            lines.append(f"**Path:** `{path}`")

    else:
        # Generic tool
        lines.append("```json")
        lines.append(json.dumps(tool_input, indent=2))
        lines.append("```")

    lines.append("")
