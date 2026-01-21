"""File API routes for preview, download, upload, watch, and delete."""

import json
import logging
from pathlib import Path
from typing import AsyncGenerator

import watchfiles
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse

from ..models import (
    DeleteFileRequest,
    DeleteFileResponse,
    EXTENSION_TO_LANGUAGE,
    FileResponse,
    IMAGE_EXTENSIONS,
    MAX_FILE_SIZE,
    PathTypeResponse,
    UploadFileResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/file")
async def get_file(path: str) -> FileResponse:
    """Fetch file contents for preview.

    Args:
        path: Absolute path to the file to preview.

    Returns:
        FileResponse with content, metadata, and language detection.

    Raises:
        HTTPException: 404 if file not found, 400 if binary or not a file,
                      403 if permission denied, 500 for other errors.
    """
    file_path = Path(path)

    # Security: Restrict to user's home directory to prevent path traversal
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        # Check if resolved path is within home directory
        resolved_path.relative_to(home_dir)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within home directory ({home_dir})",
        )

    # Validate path exists
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Ensure it's a file, not directory
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    # Check file size
    try:
        file_size = file_path.stat().st_size
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Cannot stat file: {e}")

    truncated = file_size > MAX_FILE_SIZE

    # Detect language from extension
    extension = file_path.suffix.lower()
    language = EXTENSION_TO_LANGUAGE.get(extension)

    # Special case: Makefile, Dockerfile without extension
    if file_path.name.lower() == "makefile":
        language = "makefile"
    elif file_path.name.lower() == "dockerfile":
        language = "dockerfile"

    try:
        # Read file content - only read up to MAX_FILE_SIZE bytes to prevent
        # memory exhaustion on large files
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(MAX_FILE_SIZE + 1)  # Read one extra to detect truncation

        # If we read more than MAX_FILE_SIZE, file is truncated
        if len(content) > MAX_FILE_SIZE:
            content = content[:MAX_FILE_SIZE]
            truncated = True

        # Check for binary content (null bytes indicate binary)
        if "\x00" in content[:8192]:
            raise HTTPException(
                status_code=400, detail="Binary file cannot be displayed"
            )

        # Render markdown to HTML if it's a markdown file
        # Use safe=True to escape raw HTML and prevent XSS attacks
        rendered_html = None
        if language == "markdown":
            from ..backends.shared.rendering import render_markdown_text

            rendered_html = render_markdown_text(content, safe=True)

        return FileResponse(
            content=content,
            path=str(file_path.absolute()),
            filename=file_path.name,
            size=file_size,
            language=language,
            truncated=truncated,
            rendered_html=rendered_html,
        )

    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Binary file cannot be displayed")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading file {path}: {e}")
        raise HTTPException(status_code=500, detail=f"Error reading file: {e}")


@router.get("/file/raw")
async def get_file_raw(path: str) -> Response:
    """Serve raw file bytes with appropriate Content-Type.

    Primarily used for serving images in the file preview pane.

    Args:
        path: Absolute path to the file to serve.

    Returns:
        Raw file bytes with Content-Type header.

    Raises:
        HTTPException: 404 if file not found, 403 if permission denied.
    """
    file_path = Path(path)

    # Security: Restrict to user's home directory to prevent path traversal
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(home_dir)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within home directory ({home_dir})",
        )

    # Validate path exists
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Ensure it's a file, not directory
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    # Determine content type from extension
    extension = file_path.suffix.lower()
    content_type = IMAGE_EXTENSIONS.get(extension, "application/octet-stream")

    try:
        with open(file_path, "rb") as f:
            content = f.read()

        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Cache-Control": "private, max-age=3600",  # Cache for 1 hour
            },
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    except Exception as e:
        logger.error(f"Error reading file {path}: {e}")
        raise HTTPException(status_code=500, detail=f"Error reading file: {e}")


@router.post("/file/delete")
async def delete_file(request: DeleteFileRequest) -> DeleteFileResponse:
    """Delete a file or empty directory.

    Args:
        request: DeleteFileRequest containing the path to delete.

    Returns:
        DeleteFileResponse indicating success or failure.
    """
    file_path = Path(request.path)

    # Security: Restrict to user's home directory to prevent path traversal
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(home_dir)
    except ValueError:
        return DeleteFileResponse(
            success=False,
            error=f"Access denied: path must be within home directory ({home_dir})",
        )

    # Validate path exists
    if not file_path.exists():
        return DeleteFileResponse(success=False, error=f"File not found: {request.path}")

    try:
        if file_path.is_file():
            file_path.unlink()
            logger.info(f"Deleted file: {request.path}")
        elif file_path.is_dir():
            # Only delete empty directories for safety
            if any(file_path.iterdir()):
                return DeleteFileResponse(
                    success=False, error="Directory is not empty"
                )
            file_path.rmdir()
            logger.info(f"Deleted directory: {request.path}")
        else:
            return DeleteFileResponse(
                success=False, error="Path is neither a file nor directory"
            )

        return DeleteFileResponse(success=True)
    except PermissionError:
        return DeleteFileResponse(
            success=False, error=f"Permission denied: {request.path}"
        )
    except Exception as e:
        logger.error(f"Error deleting {request.path}: {e}")
        return DeleteFileResponse(success=False, error=str(e))


@router.get("/file/download")
async def download_file(path: str) -> Response:
    """Download a file with proper Content-Disposition header.

    Args:
        path: Absolute path to the file to download.

    Returns:
        File bytes with Content-Disposition attachment header.

    Raises:
        HTTPException: 404 if file not found, 403 if permission denied.
    """
    file_path = Path(path)

    # Security: Restrict to user's home directory to prevent path traversal
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(home_dir)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within home directory ({home_dir})",
        )

    # Validate path exists
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Ensure it's a file, not directory
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    try:
        with open(file_path, "rb") as f:
            content = f.read()

        # Determine content type from extension
        extension = file_path.suffix.lower()
        content_type = IMAGE_EXTENSIONS.get(extension, "application/octet-stream")

        # Use filename for Content-Disposition
        filename = file_path.name

        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    except Exception as e:
        logger.error(f"Error downloading file {path}: {e}")
        raise HTTPException(status_code=500, detail=f"Error downloading file: {e}")


@router.post("/file/upload")
async def upload_file(
    request: Request, directory: str, filename: str
) -> UploadFileResponse:
    """Upload a file to a directory.

    Args:
        request: The request with file content in body.
        directory: Target directory path.
        filename: Name for the uploaded file.

    Returns:
        UploadFileResponse indicating success or failure.
    """
    dir_path = Path(directory)

    # Security: Restrict to user's home directory
    home_dir = Path.home()
    try:
        resolved_dir = dir_path.resolve()
        resolved_dir.relative_to(home_dir)
    except ValueError:
        return UploadFileResponse(
            success=False,
            error=f"Access denied: directory must be within home directory ({home_dir})",
        )

    # Validate directory exists
    if not dir_path.exists():
        return UploadFileResponse(
            success=False, error=f"Directory not found: {directory}"
        )

    if not dir_path.is_dir():
        return UploadFileResponse(
            success=False, error=f"Not a directory: {directory}"
        )

    # Sanitize filename (prevent path traversal in filename)
    safe_filename = Path(filename).name
    if not safe_filename or safe_filename in (".", ".."):
        return UploadFileResponse(success=False, error="Invalid filename")

    target_path = dir_path / safe_filename

    try:
        # Read file content from request body
        content = await request.body()

        # Write file
        with open(target_path, "wb") as f:
            f.write(content)

        logger.info(f"Uploaded file: {target_path}")
        return UploadFileResponse(success=True, path=str(target_path))

    except PermissionError:
        return UploadFileResponse(
            success=False, error=f"Permission denied: {target_path}"
        )
    except Exception as e:
        logger.error(f"Error uploading file to {target_path}: {e}")
        return UploadFileResponse(success=False, error=str(e))


@router.get("/path/type")
async def check_path_type(path: str) -> PathTypeResponse:
    """Check if a path exists and return its type (lightweight, no content fetching).

    Args:
        path: Absolute path to check. Supports ~ for home directory.

    Returns:
        PathTypeResponse with type "file" or "directory".

    Raises:
        HTTPException: 404 if path not found or outside home directory.
    """
    # Expand ~ to home directory
    if path.startswith("~"):
        path = str(Path.home() / path[2:])

    file_path = Path(path)

    # Security: Restrict to user's home directory
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(home_dir)
    except ValueError:
        # Outside home directory - return not found
        raise HTTPException(status_code=404)

    try:
        if file_path.exists():
            if file_path.is_file():
                return PathTypeResponse(type="file")
            elif file_path.is_dir():
                return PathTypeResponse(type="directory")
        raise HTTPException(status_code=404)
    except HTTPException:
        raise
    except (PermissionError, OSError):
        raise HTTPException(status_code=404)


# File watch SSE endpoint for live file updates
async def _file_watch_generator(
    file_path: Path, request: Request, follow: bool = True
) -> AsyncGenerator[dict, None]:
    """Generate SSE events for file changes using tail-style heuristic.

    Args:
        file_path: Path to the file to watch.
        request: The request object to check for disconnection.
        follow: If True, detect appends and send only new bytes (for tailing logs).
                If False, always send full file content on any change.

    Events:
    - initial: Full file content on connect
    - append: New content appended to file (size increased) - only when follow=True
    - replace: Full file content (truncation, rewrite, or in-place edit)
    - error: File deleted, permission denied, etc.
    """
    try:
        # Get initial file state
        stat = file_path.stat()
        last_size = stat.st_size
        last_inode = stat.st_ino

        # Send initial content
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(MAX_FILE_SIZE)

        # Check for binary content
        if "\x00" in content[:8192]:
            yield {
                "event": "error",
                "data": json.dumps({"message": "Binary file cannot be displayed"}),
            }
            return

        yield {
            "event": "initial",
            "data": json.dumps(
                {
                    "content": content,
                    "size": last_size,
                    "inode": last_inode,
                    "truncated": last_size > MAX_FILE_SIZE,
                }
            ),
        }

        # Watch for changes with debouncing to batch rapid updates
        # 100ms debounce prevents overwhelming the client with rapid file changes
        async for changes in watchfiles.awatch(file_path, debounce=100):
            # Check if client disconnected
            if await request.is_disconnected():
                logger.debug(f"Client disconnected, stopping file watch for {file_path}")
                return

            try:
                stat = file_path.stat()
                new_size = stat.st_size
                new_inode = stat.st_ino

                # When follow=false, just notify of change - frontend will refetch via /api/file
                # This allows the existing endpoint to handle markdown rendering, etc.
                if not follow:
                    yield {
                        "event": "changed",
                        "data": json.dumps({"size": new_size, "inode": new_inode}),
                    }
                # When follow=true, use tail-style heuristic for efficient append detection
                elif new_inode != last_inode:
                    # File replaced (different inode) - send full content
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(MAX_FILE_SIZE)
                    yield {
                        "event": "replace",
                        "data": json.dumps(
                            {
                                "content": content,
                                "size": new_size,
                                "inode": new_inode,
                                "truncated": new_size > MAX_FILE_SIZE,
                            }
                        ),
                    }
                elif new_size > last_size:
                    # File grew - likely append, read only new bytes
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_size)
                        new_content = f.read(MAX_FILE_SIZE)
                    yield {
                        "event": "append",
                        "data": json.dumps(
                            {
                                "content": new_content,
                                "offset": last_size,
                            }
                        ),
                    }
                elif new_size < last_size:
                    # File truncated - send full content
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(MAX_FILE_SIZE)
                    yield {
                        "event": "replace",
                        "data": json.dumps(
                            {
                                "content": content,
                                "size": new_size,
                                "inode": new_inode,
                                "truncated": new_size > MAX_FILE_SIZE,
                            }
                        ),
                    }
                else:
                    # Same size but modified - in-place edit, send full content
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(MAX_FILE_SIZE)
                    yield {
                        "event": "replace",
                        "data": json.dumps(
                            {
                                "content": content,
                                "size": new_size,
                                "inode": new_inode,
                                "truncated": new_size > MAX_FILE_SIZE,
                            }
                        ),
                    }

                last_size = new_size
                last_inode = new_inode

            except FileNotFoundError:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "File was deleted"}),
                }
                return
            except PermissionError:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "Permission denied"}),
                }
                return

    except FileNotFoundError:
        yield {
            "event": "error",
            "data": json.dumps({"message": "File not found"}),
        }
    except PermissionError:
        yield {
            "event": "error",
            "data": json.dumps({"message": "Permission denied"}),
        }
    except Exception as e:
        logger.error(f"Error in file watch for {file_path}: {e}")
        yield {
            "event": "error",
            "data": json.dumps({"message": f"Error: {e}"}),
        }


@router.get("/file/watch")
async def watch_file(path: str, request: Request, follow: bool = True) -> EventSourceResponse:
    """SSE endpoint for live file updates.

    Uses tail-style heuristic to efficiently detect appends vs full rewrites:
    - Tracks file size and inode
    - If size increases and follow=True: assume append, send only new bytes
    - If size decreases or inode changes: send full content
    - If follow=False: always send full content on any change

    Args:
        path: Absolute path to the file to watch.
        follow: If True (default), detect appends and send only new bytes.
                If False, always send full file content on any change.

    Returns:
        EventSourceResponse streaming file changes.

    Events:
        - initial: {content, size, inode, truncated} - Full file on connect
        - append: {content, offset} - New content (file grew, only when follow=True)
        - replace: {content, size, inode, truncated} - Full content (truncation/rewrite)
        - error: {message} - File deleted, permission denied, etc.
    """
    file_path = Path(path)

    # Security: Restrict to user's home directory
    home_dir = Path.home()
    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(home_dir)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within home directory ({home_dir})",
        )

    # Validate path exists and is a file
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    return EventSourceResponse(_file_watch_generator(file_path, request, follow=follow))
