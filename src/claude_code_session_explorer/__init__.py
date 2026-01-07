"""Claude Code Session Explorer - Live-updating transcript viewer for Claude Code sessions."""

import logging
import webbrowser
from pathlib import Path
from threading import Timer

import click
import uvicorn

from .backends import list_backends

__version__ = "0.1.0"

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--session", "-s",
    type=click.Path(exists=True, path_type=Path),
    help="Watch a specific session file (in addition to auto-discovered sessions)",
)
@click.option(
    "--port", "-p",
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
    type=click.Choice(list_backends()),
    default="claude-code",
    help="Backend to use (default: claude-code)",
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
def main(
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
) -> None:
    """Start a live-updating transcript viewer for Claude Code sessions.

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
        click.echo("This feature is experimental and has known security limitations.", err=True)
        raise SystemExit(1)

    if dangerously_skip_permissions and not enable_send:
        click.echo("Error: --dangerously-skip-permissions requires --enable-send", err=True)
        raise SystemExit(1)

    if fork and not enable_send:
        click.echo("Error: --fork requires --enable-send", err=True)
        raise SystemExit(1)

    # Initialize backend
    from . import server
    from .sessions import MAX_SESSIONS as _

    # Update max sessions config
    from . import sessions
    sessions.MAX_SESSIONS = max_sessions

    # Initialize the backend before setting up the server
    backend_instance = server.initialize_backend(backend)
    click.echo(f"Using backend: {backend_instance.name}")

    # Configure server features
    server.set_send_enabled(enable_send)
    server.set_skip_permissions(dangerously_skip_permissions)
    server.set_fork_enabled(fork)

    if enable_send:
        click.echo("⚠️  EXPERIMENTAL: Send feature enabled - messages can be sent to Claude Code sessions")
        click.echo("   This feature has known security limitations. Use with caution.")
    if dangerously_skip_permissions:
        click.echo("⚠️  WARNING: --dangerously-skip-permissions enabled - Claude will skip permission prompts")

    # If a specific session is provided, add it first
    if session is not None:
        click.echo(f"Watching: {session}")
        from .sessions import add_session
        add_session(session)

    # Check if any sessions were found
    recent = backend_instance.find_recent_sessions(limit=max_sessions)
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


if __name__ == "__main__":
    main()
