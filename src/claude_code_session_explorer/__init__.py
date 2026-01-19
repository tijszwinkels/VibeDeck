"""Claude Code Session Explorer - Live-updating transcript viewer for Claude Code sessions."""

import logging
import webbrowser
from pathlib import Path
from threading import Timer

import click
from click_default_group import DefaultGroup
import uvicorn

from .backends import list_backends, get_multi_backend

__version__ = "0.1.0"

logger = logging.getLogger(__name__)


@click.group(cls=DefaultGroup, default="serve", default_if_no_args=True)
@click.version_option(__version__, "-v", "--version")
def main() -> None:
    """Claude Code Session Explorer - Live transcript viewer and exporter.

    When run without a subcommand, starts the live-updating transcript viewer server.
    Use 'html' or 'md' subcommands to export transcripts to static files.
    """
    pass


@main.command()
@click.option(
    "--session",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    help="Watch a specific session file (in addition to auto-discovered sessions)",
)
@click.option(
    "--port",
    "-p",
    type=int,
    default=8765,
    help="Port to run the server on (default: 8765)",
)
@click.option(
    "--host",
    type=str,
    default="127.0.0.1",
    help="Host to bind to (default: 127.0.0.1)",
)
@click.option(
    "--no-open",
    is_flag=True,
    help="Don't open browser automatically",
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug logging",
)
@click.option(
    "--max-sessions",
    type=int,
    default=100,
    help="Maximum number of sessions to track (default: 100)",
)
@click.option(
    "--backend",
    type=click.Choice(["all"] + list_backends()),
    default="all",
    help="Backend to use: 'all' for all backends (default), or a specific backend name",
)
@click.option(
    "--experimental",
    is_flag=True,
    hidden=True,
    help="Enable experimental features (required for --enable-send)",
)
@click.option(
    "--enable-send",
    is_flag=True,
    hidden=True,
    help="Enable sending messages to Claude Code sessions (requires --experimental)",
)
@click.option(
    "--dangerously-skip-permissions",
    is_flag=True,
    hidden=True,
    help="Pass --dangerously-skip-permissions to Claude CLI (requires --experimental --enable-send)",
)
@click.option(
    "--fork",
    is_flag=True,
    hidden=True,
    help="Enable fork button to create new sessions from messages (requires --experimental --enable-send)",
)
@click.option(
    "--default-send-backend",
    type=click.Choice(list_backends()),
    default=None,
    hidden=True,
    help="Default backend for new sessions (requires --experimental --enable-send)",
)
@click.option(
    "--include-subagents",
    is_flag=True,
    help="Include subagent sessions in the session list",
)
@click.option(
    "--enable-thinking",
    is_flag=True,
    hidden=True,
    help="Enable thinking level detection (set MAX_THINKING_TOKENS based on keywords)",
)
@click.option(
    "--summary-log",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to JSONL file for appending session summaries",
)
@click.option(
    "--summarize-after-idle-for",
    type=int,
    default=None,
    help="Re-summarize sessions after N seconds of inactivity",
)
@click.option(
    "--idle-summary-model",
    type=str,
    default="haiku",
    help="Model to use for idle summarization (default: haiku)",
)
@click.option(
    "--summary-after-long-running",
    type=int,
    default=None,
    help="Summarize if CLI runs longer than N seconds (uses conversation's model for warm cache)",
)
@click.option(
    "--summary-prompt",
    type=str,
    default=None,
    hidden=True,
    help="Custom prompt template for summarization",
)
@click.option(
    "--summary-prompt-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    hidden=True,
    help="Path to file containing custom prompt template",
)
def serve(
    session: Path | None,
    port: int,
    host: str,
    no_open: bool,
    debug: bool,
    max_sessions: int,
    backend: str,
    experimental: bool,
    enable_send: bool,
    dangerously_skip_permissions: bool,
    fork: bool,
    default_send_backend: str | None,
    include_subagents: bool,
    enable_thinking: bool,
    summary_log: Path | None,
    summarize_after_idle_for: int | None,
    idle_summary_model: str,
    summary_after_long_running: int | None,
    summary_prompt: str | None,
    summary_prompt_file: Path | None,
) -> None:
    """Start the live-updating transcript viewer server.

    Watches Claude Code session files and serves a live-updating HTML view
    with tabs for each session. New messages appear automatically.

    By default, discovers and watches the most recent sessions in
    ~/.claude/projects/. Use --session to add a specific session file.
    """
    # Configure logging
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Validate experimental flag requirements
    if enable_send and not experimental:
        click.echo("Error: --enable-send requires --experimental flag", err=True)
        click.echo(
            "This feature is experimental and has known security limitations.", err=True
        )
        raise SystemExit(1)

    if dangerously_skip_permissions and not enable_send:
        click.echo(
            "Error: --dangerously-skip-permissions requires --enable-send", err=True
        )
        raise SystemExit(1)

    if fork and not enable_send:
        click.echo("Error: --fork requires --enable-send", err=True)
        raise SystemExit(1)

    if default_send_backend and not enable_send:
        click.echo("Error: --default-send-backend requires --enable-send", err=True)
        raise SystemExit(1)

    # Initialize backend
    from . import server
    from .sessions import MAX_SESSIONS as _

    # Update max sessions config
    from . import sessions

    sessions.MAX_SESSIONS = max_sessions

    # Initialize the backend before setting up the server
    if backend == "all":
        # Use multi-backend mode
        backend_instance = server.initialize_multi_backend()
        from .backends.multi import MultiBackend

        if isinstance(backend_instance, MultiBackend):
            backend_names = [b.name for b in backend_instance.get_backends()]
            click.echo(f"Using backends: {', '.join(backend_names)}")
        else:
            click.echo(f"Using backend: {backend_instance.name}")
    else:
        backend_instance = server.initialize_backend(backend)
        click.echo(f"Using backend: {backend_instance.name}")

    # Configure server features
    server.set_send_enabled(enable_send)
    server.set_skip_permissions(dangerously_skip_permissions)
    server.set_fork_enabled(fork)
    server.set_include_subagents(include_subagents)
    server.set_enable_thinking(enable_thinking)
    if default_send_backend:
        server.set_default_send_backend(default_send_backend)

    # Configure summarization
    server.configure_summarization(
        backend=backend_instance,
        summary_log=summary_log,
        summarize_after_idle_for=summarize_after_idle_for,
        idle_summary_model=idle_summary_model,
        summary_after_long_running=summary_after_long_running,
        summary_prompt=summary_prompt,
        summary_prompt_file=summary_prompt_file,
    )

    if summary_log:
        click.echo(f"Summary log: {summary_log}")
    if summarize_after_idle_for:
        click.echo(f"Summarize after idle: {summarize_after_idle_for}s")

    if enable_send:
        click.echo(
            "⚠️  EXPERIMENTAL: Send feature enabled - messages can be sent to Claude Code sessions"
        )
        click.echo("   This feature has known security limitations. Use with caution.")
    if dangerously_skip_permissions:
        click.echo(
            "⚠️  WARNING: --dangerously-skip-permissions enabled - Claude will skip permission prompts"
        )

    # If a specific session is provided, add it first
    if session is not None:
        click.echo(f"Watching: {session}")
        from .sessions import add_session

        add_session(session)

    # Check if any sessions were found
    recent = backend_instance.find_recent_sessions(
        limit=max_sessions, include_subagents=include_subagents
    )
    if not recent and session is None:
        projects_dir = backend_instance.get_projects_dir()
        click.echo(f"No session files found in {projects_dir}", err=True)
        click.echo("Specify a session file with --session", err=True)
        raise SystemExit(1)

    count = len(recent)
    if session:
        count += 1
    click.echo(f"Found {count} session(s) to watch")

    # Open browser after a short delay to let server start
    url = f"http://{host}:{port}"
    if not no_open:

        def open_browser():
            click.echo(f"Opening {url} in browser...")
            webbrowser.open(url)

        Timer(1.0, open_browser).start()
    else:
        click.echo(f"Server running at {url}")

    # Run server
    uvicorn.run(
        server.app,
        host=host,
        port=port,
        log_level="debug" if debug else "warning",
    )


def resolve_session_path(session_arg: str) -> Path:
    """Resolve a session argument to a Path.

    Accepts either:
    - A file path (e.g., /path/to/session.jsonl)
    - An OpenCode session ID (e.g., ses_xxx) which is looked up in storage

    Returns:
        Path to the session file/directory

    Raises:
        click.BadParameter if the session cannot be found
    """
    # First, try as a direct file path
    path = Path(session_arg)
    if path.exists():
        return path

    # Try as an OpenCode session ID
    opencode_storage = Path.home() / ".local" / "share" / "opencode" / "storage"
    session_msg_dir = opencode_storage / "message" / session_arg

    if session_msg_dir.exists():
        # Return the session ID as a Path object for the export functions to handle
        return Path(session_arg)

    raise click.BadParameter(
        f"Session not found: {session_arg}\n"
        f"Tried: {path.absolute()}\n"
        f"Tried: {session_msg_dir}"
    )


@main.command()
@click.argument("session_file", type=str)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output directory for HTML files",
)
@click.option(
    "-a",
    "--output-auto",
    is_flag=True,
    help="Auto-name output subdirectory based on session file",
)
@click.option(
    "--repo",
    type=str,
    help="GitHub repo (owner/repo) for commit links. Auto-detected if not specified.",
)
@click.option(
    "--gist",
    is_flag=True,
    help="Upload to GitHub Gist and output a gisthost.github.io URL",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open in browser after export",
)
@click.option(
    "--json",
    "include_json",
    is_flag=True,
    help="Include the original session JSON in the output directory",
)
def html(
    session_file: str,
    output: Path | None,
    output_auto: bool,
    repo: str | None,
    gist: bool,
    open_browser: bool,
    include_json: bool,
) -> None:
    """Export a session transcript to static HTML files.

    Generates paginated HTML files with an index page showing user prompts,
    commits, and statistics. Each page contains 5 conversations with full
    message content and tool usage.

    SESSION_FILE can be a Claude Code .jsonl file path or an OpenCode session ID.

    Example:
        claude-code-session-explorer html session.jsonl -o ./output
        claude-code-session-explorer html ses_xxx -o ./output
    """
    from .export import (
        generate_html,
        auto_output_name,
        create_gist,
        inject_gist_preview_js,
    )
    import shutil
    import tempfile

    # Resolve session file (can be path or OpenCode session ID)
    session_path = resolve_session_path(session_file)

    # Determine output directory
    if output_auto:
        parent_dir = output if output else Path(".")
        output = parent_dir / auto_output_name(session_path)
    elif output is None:
        # Use session name (stem for files, name for session IDs)
        session_name = session_path.stem if session_path.suffix else session_path.name
        output = Path(tempfile.gettempdir()) / f"claude-session-{session_name}"

    # At this point output is guaranteed to be a Path
    assert output is not None

    # Generate HTML
    try:
        index_path = generate_html(session_path, output, github_repo=repo)
        click.echo(f"Generated: {output.resolve()}")
    except Exception as e:
        click.echo(f"Error generating HTML: {e}", err=True)
        raise SystemExit(1)

    # Copy JSON file if requested (only for JSONL files, not session IDs)
    if include_json and session_path.suffix == ".jsonl":
        json_dest = output / session_path.name
        shutil.copy(session_path, json_dest)
        click.echo(f"Copied: {json_dest}")

    # Upload to gist if requested
    if gist:
        try:
            inject_gist_preview_js(output)
            click.echo("Creating GitHub gist...")
            gist_id, gist_url = create_gist(output)
            preview_url = f"https://gisthost.github.io/?{gist_id}/index.html"
            click.echo(f"Gist: {gist_url}")
            click.echo(f"Preview: {preview_url}")
        except click.ClickException as e:
            click.echo(f"Gist upload failed: {e}", err=True)

    # Open in browser if requested
    if open_browser:
        index_url = (output / "index.html").resolve().as_uri()
        webbrowser.open(index_url)


@main.command()
@click.argument("session_file", type=str)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output file path. Use trailing slash for auto-named file in directory. Omit for stdout.",
)
def md(
    session_file: str,
    output: Path | None,
) -> None:
    """Export a session transcript to Markdown.

    Generates a single Markdown file with all user messages, assistant responses,
    and tool usage formatted for readability.

    SESSION_FILE can be a Claude Code .jsonl file path or an OpenCode session ID.

    Example:
        claude-code-session-explorer md session.jsonl > transcript.md
        claude-code-session-explorer md session.jsonl -o transcript.md
        claude-code-session-explorer md ses_xxx -o transcript.md
    """
    from .export import export_markdown

    # Resolve session file (can be path or OpenCode session ID)
    session_path = resolve_session_path(session_file)

    try:
        result = export_markdown(session_path, output)
        if output is None:
            # Output to stdout
            click.echo(result, nl=False)
        else:
            click.echo(f"Generated: {result}")
    except Exception as e:
        click.echo(f"Error generating Markdown: {e}", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
