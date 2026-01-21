"""Pydantic models for API request/response schemas."""

from pydantic import BaseModel


class SendMessageRequest(BaseModel):
    """Request body for sending a message to a session."""

    message: str


class GrantPermissionRequest(BaseModel):
    """Request body for granting permissions and re-sending a message."""

    permissions: list[str]  # e.g., ["Bash(npm test:*)", "Read"]
    original_message: str  # Message to re-send after granting


class GrantPermissionNewSessionRequest(BaseModel):
    """Request body for granting permissions when starting a new session."""

    permissions: list[str]  # e.g., ["Bash(npm test:*)", "Read"]
    original_message: str  # Message to re-send after granting
    cwd: str  # Working directory for the new session
    backend: str | None = None  # Backend to use (optional)
    model_index: int | None = None  # Model index (optional)


class NewSessionRequest(BaseModel):
    """Request body for starting a new session."""

    message: str  # Initial message to send (required)
    cwd: str | None = None  # Working directory (optional)
    backend: str | None = None  # Backend to use (optional, for multi-backend mode)
    model_index: int | None = (
        None  # Model index from /backends/{name}/models (optional)
    )


class AllowDirectoryRequest(BaseModel):
    """Request body for adding a directory to the allowed list."""

    directory: str
    add_dirs: list[str] | None = None  # Additional directories to allow


class FileResponse(BaseModel):
    """Response for file preview endpoint."""

    content: str
    path: str
    filename: str
    size: int
    language: str | None
    truncated: bool = False
    rendered_html: str | None = None  # For markdown files: pre-rendered HTML


class DeleteFileRequest(BaseModel):
    """Request body for deleting a file."""

    path: str
    confirm: bool = False  # Safety flag


class DeleteFileResponse(BaseModel):
    """Response for file deletion."""

    success: bool
    error: str | None = None


class UploadFileResponse(BaseModel):
    """Response for file upload."""

    success: bool
    path: str | None = None
    error: str | None = None


class PathTypeResponse(BaseModel):
    """Response for path type check."""

    type: str  # "file" or "directory"


class ArchivedSessionsResponse(BaseModel):
    """Response for archived sessions list."""

    archived: list[str]


class ArchiveSessionRequest(BaseModel):
    """Request body for archiving/unarchiving a session."""

    session_id: str


# File extension to highlight.js language mapping
EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".json": "json",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".md": "markdown",
    ".markdown": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".r": "r",
    ".lua": "lua",
    ".pl": "perl",
    ".gitignore": "plaintext",
    ".env": "plaintext",
}

MAX_FILE_SIZE = 1024 * 1024  # 1MB

# Image file extensions and their MIME types
IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
}
