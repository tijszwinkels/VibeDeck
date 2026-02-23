"""Session management routes."""

import asyncio
import inspect
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ..models import (
    AllowDirectoryRequest,
    GrantPermissionNewSessionRequest,
    GrantPermissionRequest,
    NewSessionRequest,
    SendMessageRequest,
)
from ..sessions import get_session, get_sessions, get_sessions_list, get_sessions_lock, _get_session_timestamp

logger = logging.getLogger(__name__)

router = APIRouter()


# These will be set by server.py during startup
_server_state: dict = {}


def configure_session_routes(
    *,
    get_server_backend,
    get_backend_for_session,
    is_send_enabled,
    is_fork_enabled,
    is_skip_permissions,
    get_default_send_backend,
    get_allowed_directories,
    add_allowed_directory,
    run_cli_for_session,
    broadcast_session_status,
    summarize_session_async,
    get_summarizer,
    get_idle_summary_model,
    cached_models,
    get_session_owner=None,
):
    """Configure the session routes with server state dependencies.

    This avoids circular imports by having server.py inject dependencies
    at startup.

    Args:
        get_session_owner: Optional callback (session_path: Path) -> str | None
            that returns the user_id owning a session. When set, enables
            user-scoped session filtering.
    """
    _server_state["get_server_backend"] = get_server_backend
    _server_state["get_backend_for_session"] = get_backend_for_session
    _server_state["is_send_enabled"] = is_send_enabled
    _server_state["is_fork_enabled"] = is_fork_enabled
    _server_state["is_skip_permissions"] = is_skip_permissions
    _server_state["get_default_send_backend"] = get_default_send_backend
    _server_state["get_allowed_directories"] = get_allowed_directories
    _server_state["add_allowed_directory"] = add_allowed_directory
    _server_state["run_cli_for_session"] = run_cli_for_session
    _server_state["broadcast_session_status"] = broadcast_session_status
    _server_state["summarize_session_async"] = summarize_session_async
    _server_state["get_summarizer"] = get_summarizer
    _server_state["get_idle_summary_model"] = get_idle_summary_model
    _server_state["cached_models"] = cached_models
    _server_state["get_session_owner"] = get_session_owner


def _get_request_user_id(request: Request) -> str | None:
    """Get the authenticated user's ID from the request session.

    Returns None if auth is not enabled or user is not authenticated.
    """
    session = request.scope.get("session")
    if session is None:
        return None
    user = session.get("user")
    return user["id"] if user else None


def _check_session_access(request: Request, info) -> None:
    """Check that the authenticated user owns this session.

    Raises HTTPException 403 if the user doesn't own the session.
    No-op if auth is not enabled (no user_id in session).
    """
    user_id = _get_request_user_id(request)
    if user_id is None:
        return  # Auth not enabled, allow all access

    get_owner = _server_state.get("get_session_owner")
    if get_owner is None:
        return  # No ownership tracking configured

    owner = get_owner(info.path)
    if owner is not None and owner != user_id:
        raise HTTPException(status_code=403, detail="Access denied: session belongs to another user")


def _normalize_backend_name(name: str) -> str:
    """Normalize backend name for cache keys."""
    return name.lower().replace(" ", "-")


def _get_target_backend(backend_name: str):
    """Get target backend by name, returns (backend, normalized_name) or raises 404."""
    backend = _server_state["get_server_backend"]()
    normalized = _normalize_backend_name(backend_name)

    # Check if multi-backend
    get_by_name = getattr(backend, "get_backend_by_name", None)
    if get_by_name is not None:
        target_backend = get_by_name(backend_name)
        if target_backend is not None:
            return target_backend, _normalize_backend_name(target_backend.name)
    elif _normalize_backend_name(backend.name) == normalized:
        # Single backend mode, check name matches
        return backend, normalized

    raise HTTPException(status_code=404, detail=f"Backend not found: {backend_name}")


@router.get("/sessions")
async def list_sessions(request: Request) -> dict:
    """List tracked sessions, filtered by authenticated user if auth is enabled."""
    user_id = _get_request_user_id(request)
    get_owner = _server_state.get("get_session_owner")

    async with get_sessions_lock():
        sessions = get_sessions_list()

        # Filter by user if auth is enabled and ownership tracking is configured
        if user_id is not None and get_owner is not None:
            sessions = [
                s for s in sessions
                if get_owner(Path(s["path"])) == user_id
            ]

        return {"sessions": sessions}


@router.get("/sessions/{session_id}/status")
async def session_status(session_id: str, request: Request) -> dict:
    """Get the status of a session (running state, queue size)."""
    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(request, info)
    return {
        "session_id": session_id,
        "running": info.process is not None,
        "queued_messages": len(info.message_queue),
    }


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, request: Request) -> dict:
    """Fetch all messages for a session (lazy loading).

    Returns rendered HTML for all messages in the session,
    used for on-demand loading when a session is selected.
    """
    async with get_sessions_lock():
        info = get_session(session_id)
        if info is None:
            raise HTTPException(status_code=404, detail="Session not found")
        _check_session_access(request, info)

        # Get the renderer for this specific session
        backend = _server_state["get_backend_for_session"](info.path)
        renderer = backend.get_message_renderer()

        # Read all messages and render to HTML
        entries = info.tailer.read_all()
        html_parts = []
        for entry in entries:
            html = renderer.render_message(entry)
            if html:
                html_parts.append(html)

        return {
            "session_id": session_id,
            "html": "\n".join(html_parts),
            "message_count": len(html_parts),
            "first_timestamp": info.tailer.get_first_timestamp(),
            "last_timestamp": info.tailer.get_last_message_timestamp(),
        }


@router.post("/sessions/{session_id}/send")
async def send_message(session_id: str, request: SendMessageRequest) -> dict:
    """Send a message to a coding session."""
    if not _server_state["is_send_enabled"]():
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Remove --disable-send flag to enable.",
        )

    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate input first (before checking CLI availability)
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Get the specific backend for this session
    backend = _server_state["get_backend_for_session"](info.path)

    # Check if CLI is available for this backend
    if not backend.is_cli_available():
        raise HTTPException(
            status_code=503,
            detail=f"CLI not found. {backend.get_cli_install_instructions()}",
        )

    # If a process is already running, queue the message
    if info.process is not None:
        info.message_queue.append(message)
        await _server_state["broadcast_session_status"](session_id)
        return {
            "status": "queued",
            "session_id": session_id,
            "queue_position": len(info.message_queue),
        }

    # Start the CLI process
    asyncio.create_task(_server_state["run_cli_for_session"](session_id, message))

    return {"status": "sent", "session_id": session_id}


@router.post("/sessions/{session_id}/fork")
async def fork_session(session_id: str, request: SendMessageRequest) -> dict:
    """Fork a session: create a new session with conversation history and send a message."""
    if not _server_state["is_fork_enabled"]():
        raise HTTPException(
            status_code=403,
            detail="Fork feature is disabled. Start server with --fork to enable.",
        )

    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the specific backend for this session
    backend = _server_state["get_backend_for_session"](info.path)

    # Check if CLI is available for this backend
    if not backend.is_cli_available():
        raise HTTPException(
            status_code=503,
            detail=f"CLI not found. {backend.get_cli_install_instructions()}",
        )

    # Check if backend supports forking
    if not backend.supports_fork_session():
        raise HTTPException(
            status_code=501,
            detail="This backend does not support session forking.",
        )

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Start the CLI process with fork=True
    asyncio.create_task(_server_state["run_cli_for_session"](session_id, message, fork=True))

    return {"status": "forking", "session_id": session_id}


@router.post("/sessions/{session_id}/grant-permission")
async def grant_permission(
    session_id: str, request: GrantPermissionRequest
) -> dict:
    """Grant permissions and re-send the original message.

    This endpoint is called when the user grants permissions after a
    permission denial. It writes the permissions to the project's
    .claude/settings.json file and re-sends the original message.
    """
    from ..permissions import update_permissions_file

    if not _server_state["is_send_enabled"]():
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Remove --disable-send flag to enable.",
        )

    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the specific backend for this session
    backend = _server_state["get_backend_for_session"](info.path)

    # Check if backend supports permission detection (and thus settings.json)
    if not (
        hasattr(backend, "supports_permission_detection")
        and backend.supports_permission_detection()
    ):
        raise HTTPException(
            status_code=501,
            detail="This backend does not support permission management.",
        )

    if not request.original_message.strip():
        raise HTTPException(status_code=400, detail="Original message cannot be empty")

    # Write permissions to project's .claude/settings.json (if any)
    if request.permissions:
        if not info.project_path:
            raise HTTPException(
                status_code=400,
                detail="Cannot grant permissions: session has no project path",
            )

        settings_path = Path(info.project_path) / ".claude" / "settings.json"

        try:
            update_permissions_file(settings_path, request.permissions)
            logger.info(
                f"Granted permissions for {session_id}: {request.permissions} "
                f"(wrote to {settings_path})"
            )
        except Exception as e:
            logger.error(f"Failed to write permissions for {session_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to write permissions file: {e}",
            )

    # Re-send the original message (with any newly allowed directories via --add-dir)
    asyncio.create_task(_server_state["run_cli_for_session"](session_id, request.original_message.strip()))

    return {
        "status": "granted",
        "session_id": session_id,
        "permissions": request.permissions,
    }


@router.post("/sessions/grant-permission-new")
async def grant_permission_new_session(request: GrantPermissionNewSessionRequest) -> dict:
    """Grant permissions and resume the session that was created.

    This endpoint is called when permission is denied during new session creation.
    It writes permissions to the project's .claude/settings.json and then sends
    the message to the session that was already created (not creating a new one).
    """
    from ..permissions import update_permissions_file

    if not _server_state["is_send_enabled"]():
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Remove --disable-send flag to enable.",
        )

    if not request.original_message.strip():
        raise HTTPException(status_code=400, detail="Original message cannot be empty")

    # Determine working directory
    cwd: Path | None = None
    if request.cwd:
        cwd = Path(request.cwd).expanduser()
        if not cwd.is_dir():
            raise HTTPException(status_code=400, detail="Invalid working directory")

    if not cwd:
        raise HTTPException(
            status_code=400,
            detail="Cannot grant permissions: no working directory specified",
        )

    # Write permissions to project's .claude/settings.json (if any)
    # For sandbox-only denials, permissions may be empty - that's OK,
    # we just retry with the newly allowed directories via --add-dir
    if request.permissions:
        settings_path = cwd / ".claude" / "settings.json"

        try:
            update_permissions_file(settings_path, request.permissions)
            logger.info(f"Granted permissions for new session: {request.permissions} (wrote to {settings_path})")
        except Exception as e:
            logger.error(f"Failed to write permissions for new session: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to write permissions file: {e}",
            )

    # Find the session that was just created for this cwd
    # The initial create_new_session call already started a session that hit permission denial
    # We need to find that session and send the message to it instead of creating a new one
    cwd_str = str(cwd.resolve())
    matching_session = None

    async with get_sessions_lock():
        # Find most recently modified session matching this project path
        sessions = get_sessions()
        for session_id, info in sessions.items():
            if info.project_path and str(Path(info.project_path).resolve()) == cwd_str:
                if matching_session is None or (
                    info.path.exists()
                    and (not matching_session.path.exists()
                         or _get_session_timestamp(info) > _get_session_timestamp(matching_session))
                ):
                    matching_session = info

    if matching_session:
        logger.info(f"Found existing session {matching_session.session_id} for cwd {cwd_str}, sending message there")
        # Send to the existing session instead of creating a new one
        send_request = SendMessageRequest(message=request.original_message.strip())
        return await send_message(matching_session.session_id, send_request)
    else:
        # No existing session found - this shouldn't happen normally, but fall back to creating new
        logger.warning(f"No existing session found for cwd {cwd_str}, creating new session")
        new_session_request = NewSessionRequest(
            message=request.original_message.strip(),
            cwd=str(cwd) if cwd else None,
            backend=request.backend,
            model_index=request.model_index,
        )
        return await create_new_session(new_session_request)


@router.post("/allow-directory")
async def allow_directory(request: AllowDirectoryRequest) -> dict:
    """Allow a directory for sandbox access and optionally retry a message.

    This endpoint adds a directory to the allowed list for --add-dir flag.
    If session_id and original_message are provided, it retries the message.
    """
    if not _server_state["is_send_enabled"]():
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Remove --disable-send flag to enable.",
        )

    if not request.directory:
        raise HTTPException(status_code=400, detail="Directory cannot be empty")

    # Normalize and validate directory path
    directory = str(Path(request.directory).expanduser().resolve())

    # Add to allowed directories
    _server_state["add_allowed_directory"](directory)
    logger.info(f"Added allowed directory: {directory}")

    # Also add any additional directories
    if request.add_dirs:
        for add_dir in request.add_dirs:
            normalized_dir = str(Path(add_dir).expanduser().resolve())
            _server_state["add_allowed_directory"](normalized_dir)
            logger.info(f"Added additional allowed directory: {normalized_dir}")

    return {"status": "allowed", "directory": directory}


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(session_id: str) -> dict:
    """Interrupt a running CLI process and clear the message queue."""
    if not _server_state["is_send_enabled"]():
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Remove --disable-send flag to enable.",
        )

    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if info.process is None:
        raise HTTPException(
            status_code=409, detail="No process running for this session"
        )

    # Clear the queue first
    info.message_queue.clear()

    # Terminate the process
    try:
        info.process.terminate()
        # Give it a moment to terminate gracefully
        try:
            await asyncio.wait_for(info.process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            # Force kill if it doesn't terminate
            info.process.kill()
    except ProcessLookupError:
        # Process already terminated
        pass

    info.process = None
    await _server_state["broadcast_session_status"](session_id)

    return {"status": "interrupted", "session_id": session_id}


@router.post("/sessions/{session_id}/summarize")
async def trigger_summary(session_id: str, background_tasks: BackgroundTasks) -> dict:
    """Manually trigger summarization for a session.

    This endpoint allows users to request an on-demand summary of a session,
    useful when automatic idle summarization is disabled or when a user wants
    an immediate summary.
    """
    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if _server_state["get_summarizer"]() is None:
        raise HTTPException(
            status_code=503,
            detail="Summarization is not configured. Start server with summarization options.",
        )

    # Run summarization in background task to not block the response
    async def run_summary():
        try:
            idle_model = _server_state["get_idle_summary_model"]()
            success = await _server_state["summarize_session_async"](info, model=idle_model)
            if success:
                logger.info(f"Manual summary triggered successfully for {session_id}")
            else:
                logger.warning(f"Manual summary failed for {session_id}")
        except Exception as e:
            logger.error(f"Error during manual summary for {session_id}: {e}")

    background_tasks.add_task(run_summary)

    return {"status": "summarizing", "session_id": session_id}


@router.post("/sessions/new")
async def create_new_session(request: NewSessionRequest) -> dict:
    """Start a new session with an initial message."""
    import os

    from ..permissions import parse_permission_denials

    if not _server_state["is_send_enabled"]():
        raise HTTPException(
            status_code=403,
            detail="Send feature is disabled. Remove --disable-send flag to enable.",
        )

    backend = _server_state["get_server_backend"]()

    # For multi-backend mode, get the specific backend if requested
    # Fallback order: request.backend -> _default_send_backend -> first available
    target_backend = backend
    requested_backend = request.backend or _server_state["get_default_send_backend"]()
    if requested_backend:
        # Check if this is a MultiBackend with get_backend_by_name method
        get_by_name = getattr(backend, "get_backend_by_name", None)
        if get_by_name is not None:
            specific_backend = get_by_name(requested_backend)
            if specific_backend is not None:
                target_backend = specific_backend
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown backend: {requested_backend}",
                )

    # Validate input first (before checking CLI availability)
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Validate model_index if provided (doesn't require CLI)
    build_cmd = target_backend.build_new_session_command
    sig = inspect.signature(build_cmd)

    model: str | None = None
    cached_models = _server_state["cached_models"]
    if "model" in sig.parameters and request.model_index is not None:
        # Look up model from cached list by index
        normalized_backend = _normalize_backend_name(target_backend.name)
        cached = cached_models.get(normalized_backend, [])
        if 0 <= request.model_index < len(cached):
            model = cached[request.model_index]
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid model_index: {request.model_index}. "
                f"Fetch models from /backends/{target_backend.name}/models first.",
            )

    # Check if CLI is available
    if not target_backend.is_cli_available():
        raise HTTPException(
            status_code=503,
            detail=f"CLI not found. {target_backend.get_cli_install_instructions()}",
        )

    # Determine working directory - must be an absolute path (~ is expanded)
    cwd: Path | None = None
    if request.cwd:
        potential_cwd = Path(request.cwd).expanduser()
        if not potential_cwd.is_absolute():
            raise HTTPException(
                status_code=400, detail="Directory path must be absolute (e.g., /home/user/project or ~/project)"
            )
        if not potential_cwd.exists():
            try:
                potential_cwd.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created directory: {potential_cwd}")
            except OSError as e:
                raise HTTPException(
                    status_code=400, detail=f"Cannot create directory: {e}"
                )
        if potential_cwd.is_dir():
            cwd = potential_cwd

    # Determine if we should use permission detection
    skip_permissions = _server_state["is_skip_permissions"]()
    use_permission_detection = (
        not skip_permissions
        and hasattr(target_backend, "supports_permission_detection")
        and target_backend.supports_permission_detection()
    )
    output_format = "stream-json" if use_permission_detection else None
    add_dirs = _server_state["get_allowed_directories"]() or None

    if model:
        cmd_spec = build_cmd(message, skip_permissions, model=model, output_format=output_format, add_dirs=add_dirs)
    else:
        cmd_spec = build_cmd(message, skip_permissions, output_format=output_format, add_dirs=add_dirs)

    # Import here to access pending processes dict
    from ..server import _pending_new_session_processes

    try:
        # Capture stdout if using permission detection
        stdout_pipe = asyncio.subprocess.PIPE if use_permission_detection else asyncio.subprocess.DEVNULL
        # Use PIPE for stdin if we need to pass message content
        stdin_pipe = asyncio.subprocess.PIPE if cmd_spec.stdin else asyncio.subprocess.DEVNULL

        # Start CLI in the working directory
        proc = await asyncio.create_subprocess_exec(
            *cmd_spec.args,
            cwd=cwd,
            stdin=stdin_pipe,
            stdout=stdout_pipe,
            stderr=asyncio.subprocess.PIPE,
        )

        # Write message to stdin if provided
        if cmd_spec.stdin:
            proc.stdin.write(cmd_spec.stdin.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            await proc.stdin.wait_closed()

        # Store process reference and cwd for permission handling
        cwd_key = str(cwd.resolve()) if cwd else ""

        if use_permission_detection:
            # For new sessions with permission detection, we wait for CLI completion
            # to check for permission denials. This can take a while if the LLM takes
            # time to respond, but we need to catch denials to show the permission modal.
            #
            # Note: The frontend sets pendingSession.starting = true BEFORE this fetch,
            # so session_added SSE events will be properly merged even if this takes time.

            # Store process so we can attach it to the session when it appears
            _pending_new_session_processes[cwd_key] = proc
            logger.debug(f"Stored pending process for cwd: {cwd_key}")

            # Wait for completion and capture output
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.warning(f"CLI process exited with code {proc.returncode}")

            # Check for permission denials
            denials = parse_permission_denials(stdout.decode()) if stdout else []

            if denials:
                logger.info(f"Permission denials in new session: {[d['tool_name'] for d in denials]}")
                return {
                    "status": "permission_denied",
                    "cwd": str(cwd) if cwd else None,
                    "denials": denials,
                    "original_message": message,
                    "backend": target_backend.name,
                    "model_index": request.model_index,
                }

            return {"status": "started", "cwd": str(cwd) if cwd else None}
        else:
            # Original behavior: don't wait for completion
            await asyncio.sleep(0.5)
            if proc.returncode is not None and proc.returncode != 0:
                stderr = await proc.stderr.read() if proc.stderr else b""
                logger.error(f"CLI failed to start: {stderr.decode()}")
                raise HTTPException(status_code=500, detail="Failed to start session")

            # Store process so we can attach it to the session when it appears
            _pending_new_session_processes[cwd_key] = proc
            logger.debug(f"Stored pending process for cwd: {cwd_key}")

            return {"status": "started", "cwd": str(cwd) if cwd else None}

    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="CLI not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting new session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/tree")
async def get_session_file_tree(session_id: str, path: str | None = None) -> dict:
    """Get the file tree for a session's working directory or specific path.

    Args:
        session_id: The session ID
        path: Optional absolute path to list. If None, uses session's project path.
    """
    import os

    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    target_path = None

    if path:
        # User requested specific path - expand ~ to home directory
        if path.startswith("~"):
            path = str(Path.home() / path[2:])
        target_path = Path(path).resolve()

        # Security: Ensure it's within home directory
        # (Relaxed from project_path restriction to allow home navigation)
        try:
            target_path.relative_to(Path.home())
        except ValueError:
            # Just log for now, allow system access if local
            pass
    else:
        # Default to project path
        if not info.project_path:
            return {"tree": None, "error": "No project path set for this session"}
        target_path = Path(info.project_path)

    if not target_path.exists() or not target_path.is_dir():
        return {
            "tree": None,
            "error": f"Path does not exist: {target_path}",
        }

    try:
        # Use shallow listing for navigation efficiency
        tree = _get_directory_structure(target_path, shallow=True)
        # Include project root for relative path calculations
        project_root = info.project_path if info.project_path else None
        return {"tree": tree, "home": str(Path.home()), "projectRoot": project_root}
    except Exception as e:
        logger.error(f"Error generating file tree for {session_id}: {e}")
        return {"tree": None, "error": str(e)}


def _get_directory_structure(rootdir: Path, shallow: bool = False) -> dict:
    """Creates a nested dictionary that represents the folder structure of rootdir."""
    import os

    dir_name = rootdir.name

    # Base structure
    item = {
        "name": dir_name,
        "path": str(rootdir.resolve()),
        "type": "directory",
        "children": [],
    }

    try:
        # Sort directories first, then files
        # Use simple heuristics for sorting
        entries = list(os.scandir(rootdir))
        entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))

        for entry in entries:
            # Skip hidden files/dirs and common ignored dirs
            if entry.name.startswith("."):
                continue
            if entry.name in [
                "__pycache__",
                "node_modules",
                "venv",
                "env",
                "dist",
                "build",
                "coverage",
                "target",
                "egg-info",
            ]:
                continue

            if entry.is_dir(follow_symlinks=False):
                if shallow:
                    # For shallow listing, we just indicate it's a directory
                    # We don't recurse.
                    item["children"].append(
                        {
                            "name": entry.name,
                            "path": str(Path(entry.path).resolve()),
                            "type": "directory",
                            "has_children": True,
                        }
                    )
                else:
                    item["children"].append(
                        _get_directory_structure(Path(entry.path), shallow=False)
                    )
            else:
                item["children"].append(
                    {
                        "name": entry.name,
                        "path": str(Path(entry.path).resolve()),
                        "type": "file",
                    }
                )
    except (PermissionError, OSError):
        pass

    return item


# Backend-related routes

@router.get("/send-enabled")
async def send_enabled() -> dict:
    """Check if sending messages is enabled."""
    return {"enabled": _server_state["is_send_enabled"]()}


@router.get("/fork-enabled")
async def fork_enabled() -> dict:
    """Check if fork button is enabled."""
    return {"enabled": _server_state["is_fork_enabled"]()}


@router.get("/default-send-backend")
async def default_send_backend() -> dict:
    """Get the default backend for new sessions."""
    return {"backend": _server_state["get_default_send_backend"]()}


@router.get("/backends")
async def list_backends() -> dict:
    """List available backends for creating new sessions.

    Returns dict with:
        backends: List of backend info dicts with name, cli_available, supports_models
    """
    backend = _server_state["get_server_backend"]()
    backends_info = []

    # Check if multi-backend
    get_backends = getattr(backend, "get_backends", None)
    if get_backends is not None:
        # Multi-backend mode - list all backends
        for b in get_backends():
            has_models = hasattr(b, "get_models") and callable(getattr(b, "get_models"))
            backends_info.append(
                {
                    "name": b.name,
                    "cli_available": b.is_cli_available(),
                    "supports_models": has_models,
                }
            )
    else:
        # Single backend mode
        has_models = hasattr(backend, "get_models") and callable(
            getattr(backend, "get_models")
        )
        backends_info.append(
            {
                "name": backend.name,
                "cli_available": backend.is_cli_available(),
                "supports_models": has_models,
            }
        )

    return {"backends": backends_info}


@router.get("/backends/{backend_name}/models")
async def list_backend_models(backend_name: str) -> dict:
    """List available models for a specific backend.

    Args:
        backend_name: Name of the backend (case-insensitive)

    Returns dict with:
        models: List of model identifiers (indexed, use index for model_index param)
    """
    target_backend, normalized_name = _get_target_backend(backend_name)

    # Check if backend supports models
    get_models = getattr(target_backend, "get_models", None)
    if get_models is None or not callable(get_models):
        return {"models": []}

    # Fetch and cache models
    cached_models = _server_state["cached_models"]
    models = get_models()
    cached_models[normalized_name] = models

    return {"models": models}
