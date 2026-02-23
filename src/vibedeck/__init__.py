"""VibeDeck - Live-updating transcript viewer and web frontend for Claude Code and OpenCode sessions."""

import logging
import webbrowser
from pathlib import Path
from threading import Timer

import click
from click_default_group import DefaultGroup
import uvicorn

from .backends import list_backends, get_multi_backend
from .config import load_config

__version__ = "0.1.0"

logger = logging.getLogger(__name__)

# Load config once at startup
_config = load_config()


def _get_serve_default(key: str, fallback):
    """Get a default value from config, with fallback."""
    value = getattr(_config.serve, key, None)
    return value if value is not None else fallback


@click.group(cls=DefaultGroup, default="serve", default_if_no_args=True)
@click.version_option(__version__, "-v", "--version")
def main() -> None:
    """VibeDeck - Live transcript viewer and exporter for Claude Code and OpenCode.

    When run without a subcommand, starts the live-updating transcript viewer server.
    Use 'html' or 'md' subcommands to export transcripts to static files.
    """
    pass


@main.command()
@click.option(
    "--config",
    "-c",
    "config_file",
    type=click.Path(exists=True, path_type=Path),
    help="Load configuration from a specific TOML file",
)
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
    default=None,
    help=f"Port to run the server on (default: {_config.serve.port})",
)
@click.option(
    "--host",
    type=str,
    default=None,
    help=f"Host to bind to (default: {_config.serve.host})",
)
@click.option(
    "--no-open",
    is_flag=True,
    default=None,
    help="Don't open browser automatically",
)
@click.option(
    "--debug",
    is_flag=True,
    default=None,
    help="Enable debug logging",
)
@click.option(
    "--max-sessions",
    type=int,
    default=None,
    help=f"Maximum number of sessions to track (default: {_config.serve.max_sessions})",
)
@click.option(
    "--backend",
    type=click.Choice(["all"] + list_backends()),
    default=None,
    help=f"Backend to use (default: {_config.serve.backend})",
)
@click.option(
    "--disable-send",
    is_flag=True,
    default=None,
    help="Disable sending messages to Claude Code sessions (enabled by default)",
)
@click.option(
    "--disable-terminal",
    is_flag=True,
    default=None,
    help="Disable embedded terminal feature (enabled by default)",
)
@click.option(
    "--dangerously-skip-permissions",
    is_flag=True,
    default=None,
    hidden=True,
    help="Pass --dangerously-skip-permissions to Claude CLI",
)
@click.option(
    "--fork",
    is_flag=True,
    default=None,
    help="Enable fork button to create new sessions from messages",
)
@click.option(
    "--default-send-backend",
    type=click.Choice(list_backends()),
    default=None,
    help="Default backend for new sessions",
)
@click.option(
    "--include-subagents",
    is_flag=True,
    default=None,
    help="Include subagent sessions in the session list",
)
@click.option(
    "--enable-thinking",
    is_flag=True,
    default=None,
    hidden=True,
    help="Enable thinking level detection (set MAX_THINKING_TOKENS based on keywords)",
)
@click.option(
    "--thinking-budget",
    type=int,
    default=None,
    hidden=True,
    help="Fixed thinking token budget (overrides keyword detection)",
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
    default=None,
    help=f"Model to use for idle summarization (default: {_config.serve.idle_summary_model})",
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
    config_file: Path | None,
    session: Path | None,
    port: int | None,
    host: str | None,
    no_open: bool | None,
    debug: bool | None,
    max_sessions: int | None,
    backend: str | None,
    disable_send: bool | None,
    disable_terminal: bool | None,
    dangerously_skip_permissions: bool | None,
    fork: bool | None,
    default_send_backend: str | None,
    include_subagents: bool | None,
    enable_thinking: bool | None,
    thinking_budget: int | None,
    summary_log: Path | None,
    summarize_after_idle_for: int | None,
    idle_summary_model: str | None,
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
    # Load config from explicit file or use auto-loaded config
    if config_file:
        config = load_config([config_file])
        cfg = config.serve
    else:
        cfg = _config.serve
    port = port if port is not None else cfg.port
    host = host if host is not None else cfg.host
    no_open = no_open if no_open is not None else cfg.no_open
    debug = debug if debug is not None else cfg.debug
    max_sessions = max_sessions if max_sessions is not None else cfg.max_sessions
    backend = backend if backend is not None else cfg.backend
    disable_send = disable_send if disable_send is not None else cfg.disable_send
    dangerously_skip_permissions = dangerously_skip_permissions if dangerously_skip_permissions is not None else cfg.dangerously_skip_permissions
    fork = fork if fork is not None else cfg.fork
    default_send_backend = default_send_backend if default_send_backend is not None else cfg.default_send_backend
    include_subagents = include_subagents if include_subagents is not None else cfg.include_subagents
    enable_thinking = enable_thinking if enable_thinking is not None else cfg.enable_thinking
    thinking_budget = thinking_budget if thinking_budget is not None else cfg.thinking_budget
    idle_summary_model = idle_summary_model if idle_summary_model is not None else cfg.idle_summary_model
    summarize_after_idle_for = summarize_after_idle_for if summarize_after_idle_for is not None else cfg.summarize_after_idle_for
    summary_after_long_running = summary_after_long_running if summary_after_long_running is not None else cfg.summary_after_long_running
    summary_prompt = summary_prompt if summary_prompt is not None else cfg.summary_prompt
    # Handle summary_log and summary_prompt_file (need Path conversion from config string)
    if summary_log is None and cfg.summary_log:
        summary_log = Path(cfg.summary_log).expanduser()
    if summary_prompt_file is None and cfg.summary_prompt_file:
        summary_prompt_file = Path(cfg.summary_prompt_file).expanduser()

    # Configure logging
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Send is enabled by default, disable_send turns it off
    enable_send = not disable_send

    # Terminal is enabled by default, disable_terminal turns it off
    enable_terminal = not disable_terminal

    # Validate flag requirements
    if dangerously_skip_permissions and not enable_send:
        click.echo(
            "Error: --dangerously-skip-permissions requires send to be enabled (don't use --disable-send)", err=True
        )
        raise SystemExit(1)

    if fork and not enable_send:
        click.echo("Error: --fork requires send to be enabled (don't use --disable-send)", err=True)
        raise SystemExit(1)

    if default_send_backend and not enable_send:
        click.echo("Error: --default-send-backend requires send to be enabled (don't use --disable-send)", err=True)
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
    elif backend == "isolation":
        # Isolation backend needs config from [isolation] section
        if not config.isolation.users_dir:
            click.echo("Error: [isolation] users_dir is required when backend = 'isolation'", err=True)
            raise SystemExit(1)
        backend_instance = server.initialize_backend(
            backend,
            users_dir=config.isolation.users_dir,
            docker_image=config.isolation.docker_image,
            docker_runtime=config.isolation.docker_runtime,
            memory=config.isolation.memory,
            cpus=config.isolation.cpus,
            env_file=config.isolation.env_file,
        )
        click.echo(f"Using backend: {backend_instance.name} (users_dir={config.isolation.users_dir})")
    else:
        backend_instance = server.initialize_backend(backend)
        click.echo(f"Using backend: {backend_instance.name}")

    # Configure server features
    server.set_send_enabled(enable_send)
    server.set_terminal_enabled(enable_terminal)
    server.set_skip_permissions(dangerously_skip_permissions)
    server.set_fork_enabled(fork)
    server.set_include_subagents(include_subagents)
    server.set_enable_thinking(enable_thinking)
    server.set_thinking_budget(thinking_budget)
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

    # Configure auth if [auth] section is present with client_id
    if config.auth.client_id:
        from .auth import setup_auth
        try:
            setup_auth(server.app, config.auth)
            click.echo(f"OAuth authentication enabled (id_claim={config.auth.id_claim})")
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)

    if disable_send:
        click.echo("Message sending is disabled (--disable-send)")
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
        # Isolation backend starts empty — users create sessions after login
        if backend == "isolation":
            click.echo("No sessions yet (isolation backend, waiting for users)")
        else:
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
@click.argument("session_file", type=str, required=False)
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
    "--force",
    is_flag=True,
    help="Force gist upload even if secrets are detected (use with caution)",
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
@click.option(
    "--hide-tools",
    is_flag=True,
    help="Hide tool calls and results, showing only user/assistant conversation",
)
@click.option(
    "--phrase",
    type=str,
    help="Find session by unique verification phrase (searches today and yesterday)",
)
def html(
    session_file: str | None,
    output: Path | None,
    output_auto: bool,
    repo: str | None,
    gist: bool,
    force: bool,
    open_browser: bool,
    include_json: bool,
    hide_tools: bool,
    phrase: str | None,
) -> None:
    """Export a session transcript to static HTML files.

    Generates paginated HTML files with an index page showing user prompts,
    commits, and statistics. Each page contains 5 conversations with full
    message content and tool usage.

    SESSION_FILE can be a Claude Code .jsonl file path or an OpenCode session ID.
    Alternatively, use --phrase to find a session by a unique verification phrase.

    Example:
        vibedeck html session.jsonl -o ./output
        vibedeck html ses_xxx -o ./output
        vibedeck html --phrase "unique phrase from conversation" --gist
    """
    from .export import (
        generate_html,
        auto_output_name,
        create_gist,
        inject_gist_preview_js,
    )
    import shutil
    import tempfile

    # Validate arguments: need either session_file or --phrase
    if session_file is None and phrase is None:
        raise click.UsageError("Either SESSION_FILE or --phrase is required")
    if session_file is not None and phrase is not None:
        raise click.UsageError("Cannot use both SESSION_FILE and --phrase")

    # Resolve session path
    if phrase is not None:
        from .search import find_session_by_phrase
        try:
            session_path = find_session_by_phrase(phrase)
            click.echo(f"Found session: {session_path}")
        except ValueError as e:
            raise click.ClickException(str(e))
    else:
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
        index_path = generate_html(session_path, output, github_repo=repo, hide_tools=hide_tools)
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
        # Scan for secrets before uploading
        from .secrets import scan_session_for_secrets, format_secret_matches

        secret_matches = scan_session_for_secrets(session_path)

        if secret_matches and not force:
            click.echo("", err=True)
            click.echo("⚠️  Refusing to create gist: potential secrets detected!", err=True)
            click.echo("", err=True)
            click.echo(format_secret_matches(secret_matches), err=True)
            click.echo("The HTML files have been generated locally but will NOT be uploaded.", err=True)
            click.echo(f"Local output: {output.resolve()}", err=True)
            raise SystemExit(1)

        if secret_matches and force:
            click.echo("", err=True)
            click.echo("⚠️  Warning: secrets detected but --force was specified", err=True)
            click.echo(format_secret_matches(secret_matches), err=True)

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
@click.argument("session_file", type=str, required=False)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output file path. Use trailing slash for auto-named file in directory. Omit for stdout.",
)
@click.option(
    "--hide-tools",
    is_flag=True,
    help="Hide tool calls and results, showing only user/assistant conversation",
)
@click.option(
    "--phrase",
    type=str,
    help="Find session by unique verification phrase (searches today and yesterday)",
)
def md(
    session_file: str | None,
    output: Path | None,
    hide_tools: bool,
    phrase: str | None,
) -> None:
    """Export a session transcript to Markdown.

    Generates a single Markdown file with all user messages, assistant responses,
    and tool usage formatted for readability.

    SESSION_FILE can be a Claude Code .jsonl file path or an OpenCode session ID.
    Alternatively, use --phrase to find a session by a unique verification phrase.

    Example:
        vibedeck md session.jsonl > transcript.md
        vibedeck md session.jsonl -o transcript.md
        vibedeck md ses_xxx -o transcript.md
        vibedeck md --phrase "unique phrase from conversation"
    """
    from .export import export_markdown

    # Validate arguments: need either session_file or --phrase
    if session_file is None and phrase is None:
        raise click.UsageError("Either SESSION_FILE or --phrase is required")
    if session_file is not None and phrase is not None:
        raise click.UsageError("Cannot use both SESSION_FILE and --phrase")

    # Resolve session path
    if phrase is not None:
        from .search import find_session_by_phrase
        try:
            session_path = find_session_by_phrase(phrase)
            # Output to stderr so it doesn't interfere with stdout piping
            click.echo(f"Found session: {session_path}", err=True)
        except ValueError as e:
            raise click.ClickException(str(e))
    else:
        # Resolve session file (can be path or OpenCode session ID)
        session_path = resolve_session_path(session_file)

    try:
        result = export_markdown(session_path, output, hide_tools=hide_tools)
        if output is None:
            # Output to stdout
            click.echo(result, nl=False)
        else:
            click.echo(f"Generated: {result}")
    except Exception as e:
        click.echo(f"Error generating Markdown: {e}", err=True)
        raise SystemExit(1)


@main.command()
@click.argument("search_phrase", type=str)
@click.option(
    "-n",
    "--limit",
    type=int,
    default=5,
    help="Maximum number of results to return (default: 5)",
)
@click.option(
    "--include-subagents",
    is_flag=True,
    help="Include subagent sessions in search",
)
@click.option(
    "--case-sensitive",
    is_flag=True,
    help="Make search case-sensitive (default: case-insensitive)",
)
@click.option(
    "--show-tools",
    is_flag=True,
    help="Show tool calls and results (default: hide tools)",
)
@click.option(
    "-c",
    "--context",
    type=int,
    default=2,
    help="Number of messages to show before and after each match (default: 2)",
)
def search(
    search_phrase: str,
    limit: int,
    include_subagents: bool,
    case_sensitive: bool,
    show_tools: bool,
    context: int,
) -> None:
    """Search session transcripts for a phrase.

    Searches through all Claude Code sessions for the given phrase and outputs
    matching sessions in markdown format with metadata headers.

    Output format for each match:
        ## /path/to/session.jsonl
        created_at: YYYY-MM-DD HH:MM:SS
        updated_at: YYYY-MM-DD HH:MM:SS
        last_msg_at: YYYY-MM-DD HH:MM:SS
        matches: N

        <markdown content similar to 'md --hide-tools'>

    Results are sorted by last message timestamp (most recent first).

    Example:
        vibedeck search "error handling"
        vibedeck search "API endpoint" --limit 20
        vibedeck search "TODO" --case-sensitive --show-tools
    """
    from .search import search_sessions

    try:
        result = search_sessions(
            search_phrase,
            limit=limit,
            include_subagents=include_subagents,
            case_insensitive=not case_sensitive,
            hide_tools=not show_tools,
            context_before=context,
            context_after=context,
        )
        click.echo(result, nl=False)
    except Exception as e:
        click.echo(f"Error searching sessions: {e}", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
