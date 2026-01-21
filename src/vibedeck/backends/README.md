# Backend Protocol Documentation

This document describes how to implement a new backend for VibeDeck. A backend provides the interface between VibeDeck and a specific coding tool (e.g., Claude Code, OpenCode).

## Architecture Overview

```
backends/
├── __init__.py          # Exports protocols and registry
├── protocol.py          # Protocol definitions (interfaces)
├── registry.py          # Backend registration
├── base.py              # Shared base classes
├── README.md            # This file
└── claude_code/         # Reference implementation
    ├── __init__.py
    ├── backend.py       # Main backend class
    ├── tailer.py        # Session file reading
    ├── discovery.py     # Session discovery
    ├── pricing.py       # Token/cost calculation
    ├── cli.py           # CLI interaction
    └── renderer.py      # HTML rendering
```

## Quick Start

To add a new backend:

1. Create a directory: `backends/your_backend/`
2. Implement `YourBackend` class implementing `CodingToolBackend` protocol
3. Register in `backends/registry.py`
4. Use with: `vibedeck --backend your-backend`

## Protocol Reference

### CodingToolBackend

The main protocol that all backends must implement. Located in `protocol.py`.

```python
class CodingToolBackend(Protocol):
    """Protocol that all coding tool backends must implement."""

    # ===== Backend Identity =====

    @property
    def name(self) -> str:
        """Human-readable name (e.g., 'Claude Code', 'OpenCode')."""
        ...

    @property
    def cli_command(self) -> str | None:
        """CLI command name (e.g., 'claude', 'opencode'), or None if no CLI."""
        ...

    # ===== Session Discovery =====

    def find_recent_sessions(self, limit: int = 10) -> list[Path]:
        """Find recently modified sessions.

        Args:
            limit: Maximum number of sessions to return.

        Returns:
            List of session identifiers. For file-based backends, these are
            file paths. For database backends, these could be keys/IDs
            wrapped in Path objects.
        """
        ...

    def get_projects_dir(self) -> Path:
        """Get the base directory where sessions are stored.

        Returns:
            Path to the sessions directory (e.g., ~/.claude/projects).
        """
        ...

    # ===== Session Metadata =====

    def get_session_metadata(self, session_path: Path) -> SessionMetadata:
        """Extract metadata from a session.

        Args:
            session_path: Session identifier from find_recent_sessions().

        Returns:
            SessionMetadata with:
            - session_id: Unique identifier
            - project_name: Human-readable project name
            - project_path: Filesystem path to project (or None)
            - first_message: Preview of first user message (or None)
            - started_at: ISO timestamp (or None)
            - backend_data: Dict for backend-specific data
        """
        ...

    def get_session_id(self, session_path: Path) -> str:
        """Extract unique session ID from path.

        For file-based backends, typically the filename stem.
        For database backends, the record ID.
        """
        ...

    def has_messages(self, session_path: Path) -> bool:
        """Check if session has any user or assistant messages.

        Used to filter out empty/system-only sessions.
        """
        ...

    # ===== Session Reading =====

    def create_tailer(self, session_path: Path) -> SessionTailerProtocol:
        """Create a tailer for reading session messages.

        The tailer maintains position state for incremental reading.
        See SessionTailerProtocol below.
        """
        ...

    # ===== Token Usage & Pricing =====

    def get_session_token_usage(self, session_path: Path) -> TokenUsage:
        """Calculate total token usage and cost for a session.

        Returns:
            TokenUsage dataclass with:
            - input_tokens, output_tokens
            - cache_creation_tokens, cache_read_tokens
            - message_count
            - cost (USD)
            - models (list of model IDs used)
        """
        ...

    # ===== CLI Interaction (Optional) =====

    def supports_send_message(self) -> bool:
        """Whether backend supports sending messages via CLI."""
        ...

    def supports_fork_session(self) -> bool:
        """Whether backend supports forking/branching sessions."""
        ...

    def supports_permission_detection(self) -> bool:
        """Whether backend supports permission denial detection.

        Return True if the CLI supports --output-format stream-json and
        outputs permission denial events that can be parsed for interactive
        permission granting.
        """
        ...

    def is_cli_available(self) -> bool:
        """Check if CLI tool is installed (e.g., shutil.which())."""
        ...

    def get_cli_install_instructions(self) -> str:
        """Instructions for installing the CLI tool."""
        ...

    def build_send_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> list[str]:
        """Build CLI command to send a message to existing session.

        Args:
            session_id: Session to send to.
            message: Message text.
            skip_permissions: Skip permission prompts if supported.
            output_format: Output format (e.g., "stream-json" for permission detection).
            add_dirs: Additional directories to allow access to.

        Returns:
            Command arguments list, e.g.:
            ['claude', '-p', message, '--resume', session_id]
        """
        ...

    def build_fork_command(
        self,
        session_id: str,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> list[str]:
        """Build CLI command to fork a session with history.

        Args:
            session_id: Session to fork from.
            message: Initial message for forked session.
            skip_permissions: Skip permission prompts if supported.
            output_format: Output format (e.g., "stream-json" for permission detection).
            add_dirs: Additional directories to allow access to.
        """
        ...

    def build_new_session_command(
        self,
        message: str,
        skip_permissions: bool = False,
        output_format: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> list[str]:
        """Build CLI command to start a new session.

        Args:
            message: Initial message.
            skip_permissions: Skip permission prompts if supported.
            output_format: Output format (e.g., "stream-json" for permission detection).
            add_dirs: Additional directories to allow access to.
        """
        ...

    def ensure_session_indexed(self, session_id: str) -> None:
        """Ensure session is known to CLI tool.

        Some tools need sessions registered before --resume works.
        Claude Code creates ~/.claude/session-env/{session_id}/.
        """
        ...

    # ===== Rendering =====

    def get_message_renderer(self) -> MessageRendererProtocol:
        """Get renderer for converting messages to HTML.

        See MessageRendererProtocol below.
        """
        ...

    # ===== File Watching =====

    def should_watch_file(self, path: Path) -> bool:
        """Check if a file should be watched for changes.

        Used to filter file system events. Return True for session files,
        False for other files (logs, agent files, etc.).
        """
        ...
```

### SessionTailerProtocol

Protocol for reading messages from a session incrementally.

```python
class SessionTailerProtocol(Protocol):
    """Protocol for reading messages from a session."""

    @property
    def waiting_for_input(self) -> bool:
        """Whether session is waiting for user input.

        True when last message is assistant text (not tool_use).
        Used to show "waiting" indicator in UI.
        """
        ...

    def read_new_lines(self) -> list[dict]:
        """Read new messages since last call.

        Maintains internal position state. Returns only messages
        added since the previous call.

        Returns:
            List of raw message entries in backend-specific format.
            For Claude Code, these are parsed JSONL objects.
        """
        ...

    def read_all(self) -> list[dict]:
        """Read all messages from the beginning.

        Does NOT modify internal position - creates fresh reader.
        Used for catchup when clients connect.

        Returns:
            List of all message entries.
        """
        ...

    def get_first_timestamp(self) -> str | None:
        """Get ISO timestamp of the first message."""
        ...
```

### MessageRendererProtocol

Protocol for rendering messages to HTML.

```python
class MessageRendererProtocol(Protocol):
    """Protocol for rendering messages to HTML."""

    def render_message(self, entry: dict) -> str:
        """Render a message entry to HTML.

        Args:
            entry: Raw message entry from tailer (backend-specific format).

        Returns:
            HTML string for the message, or empty string to skip.

        The HTML should include:
        - Role indicator (user/assistant)
        - Timestamp
        - Message content (text, tool calls, tool results, etc.)
        - Token usage info (for assistant messages)
        """
        ...
```

## Data Classes

### SessionMetadata

```python
@dataclass
class SessionMetadata:
    session_id: str           # Unique identifier
    project_name: str         # Human-readable name
    project_path: str | None  # Filesystem path to project
    first_message: str | None # Preview text (truncated)
    started_at: str | None    # ISO timestamp
    backend_data: dict        # Backend-specific metadata (default: {})
```

### TokenUsage

```python
@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    message_count: int = 0
    cost: float = 0.0         # USD
    models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        ...
```

### SendMessageResult

```python
@dataclass
class SendMessageResult:
    status: str               # 'sent', 'queued', 'forking', 'started', 'error'
    session_id: str | None = None
    queue_position: int | None = None
    error_message: str | None = None
    cwd: str | None = None
```

## Base Classes

The `base.py` module provides reusable base classes for common patterns.

### BaseTailer

Abstract base class for file-based tailers with position tracking.

```python
class BaseTailer(ABC):
    """Base class for session tailers with file reading logic."""

    def __init__(self, path: Path):
        self.path = path
        self.position = 0      # Byte offset
        self.buffer = ""       # Incomplete line buffer
        self.message_index = 0
        self._waiting_for_input = False

    # Implemented methods:
    def _read_raw_content(self) -> str: ...
    def _split_lines(self, content: str) -> list[str]: ...
    def read_new_lines(self) -> list[dict]: ...
    def read_all(self) -> list[dict]: ...

    # Abstract methods to implement:
    @abstractmethod
    def _parse_line(self, line: str) -> dict | None: ...

    @abstractmethod
    def _update_waiting_state(self, entry: dict) -> None: ...

    @abstractmethod
    def get_first_timestamp(self) -> str | None: ...
```

### JsonlTailer

Base class for JSONL (JSON Lines) formatted files.

```python
class JsonlTailer(BaseTailer):
    """Base tailer for JSONL session files."""

    def _parse_line(self, line: str) -> dict | None:
        # Parses JSON, calls _should_include_entry()
        ...

    def _should_include_entry(self, entry: dict) -> bool:
        # Override to filter entries (default: include all)
        return True
```

## Registration

Register your backend in `backends/registry.py`:

```python
def _auto_register_backends() -> None:
    """Auto-register built-in backends."""
    try:
        from .claude_code import ClaudeCodeBackend
        register_backend("claude-code", ClaudeCodeBackend)
    except ImportError:
        pass

    # Add your backend:
    try:
        from .opencode import OpenCodeBackend
        register_backend("opencode", OpenCodeBackend)
    except ImportError:
        pass
```

## Example Implementation

Here's a minimal skeleton for an OpenCode backend:

```python
# backends/opencode/__init__.py
from .backend import OpenCodeBackend
__all__ = ["OpenCodeBackend"]

# backends/opencode/backend.py
from pathlib import Path
from ..protocol import (
    CodingToolBackend,
    SessionMetadata,
    SessionTailerProtocol,
    MessageRendererProtocol,
    TokenUsage,
)

class OpenCodeBackend:
    """Backend for OpenCode sessions."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path.home() / ".opencode"

    @property
    def name(self) -> str:
        return "OpenCode"

    @property
    def cli_command(self) -> str | None:
        return "opencode"

    def find_recent_sessions(self, limit: int = 10) -> list[Path]:
        # OpenCode uses SQLite - query database for recent sessions
        # Return list of session identifiers as Path objects
        ...

    def get_projects_dir(self) -> Path:
        return self._data_dir

    def get_session_metadata(self, session_path: Path) -> SessionMetadata:
        # Query database for session metadata
        ...

    def get_session_id(self, session_path: Path) -> str:
        return session_path.stem

    def has_messages(self, session_path: Path) -> bool:
        # Check if session has messages
        ...

    def create_tailer(self, session_path: Path) -> SessionTailerProtocol:
        return OpenCodeTailer(session_path, self._data_dir)

    def get_session_token_usage(self, session_path: Path) -> TokenUsage:
        # Calculate from database
        ...

    def supports_send_message(self) -> bool:
        return True

    def supports_fork_session(self) -> bool:
        return False  # If not supported

    def supports_permission_detection(self) -> bool:
        return False  # If CLI doesn't support --output-format stream-json

    def is_cli_available(self) -> bool:
        import shutil
        return shutil.which("opencode") is not None

    def get_cli_install_instructions(self) -> str:
        return "Install with: go install github.com/opencode-ai/opencode@latest"

    def build_send_command(self, session_id: str, message: str,
                           skip_permissions: bool = False,
                           output_format: str | None = None,
                           add_dirs: list[str] | None = None) -> list[str]:
        return ["opencode", "--session", session_id, "-m", message]

    def build_fork_command(self, session_id: str, message: str,
                           skip_permissions: bool = False,
                           output_format: str | None = None,
                           add_dirs: list[str] | None = None) -> list[str]:
        raise NotImplementedError("OpenCode doesn't support forking")

    def build_new_session_command(self, message: str,
                                  skip_permissions: bool = False,
                                  output_format: str | None = None,
                                  add_dirs: list[str] | None = None) -> list[str]:
        return ["opencode", "-m", message]

    def ensure_session_indexed(self, session_id: str) -> None:
        pass  # Not needed for OpenCode

    def get_message_renderer(self) -> MessageRendererProtocol:
        return OpenCodeRenderer()

    def should_watch_file(self, path: Path) -> bool:
        # OpenCode uses SQLite, may need different approach
        return path.suffix == ".db"
```

## Message Format

The renderer receives raw entries from the tailer. The format is backend-specific.
For Claude Code, entries look like:

```json
{
  "type": "user" | "assistant",
  "timestamp": "2024-01-06T12:34:56.789Z",
  "message": {
    "id": "msg_abc123",
    "content": [
      {"type": "text", "text": "Hello"},
      {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
      {"type": "tool_result", "content": "file1.txt\nfile2.txt"}
    ],
    "usage": {
      "input_tokens": 1000,
      "output_tokens": 500
    },
    "model": "claude-opus-4-5-20251101"
  },
  "requestId": "req_def456"
}
```

Your renderer should handle the content block types relevant to your tool.

## HTML Rendering

The session explorer uses Jinja2 templates in `templates/`. Your renderer can:

1. **Use existing macros** from `templates/macros.html`:
   - `message()` - Wrapper for any message
   - `tool_use()` - Generic tool call display
   - `tool_result()` - Tool output display
   - `thinking()` - Collapsible thinking blocks

2. **Create custom rendering** if your tool has unique message types.

Example using macros:

```python
from jinja2 import Environment, PackageLoader

_jinja_env = Environment(
    loader=PackageLoader("vibedeck", "templates"),
    autoescape=True,
)
_macros = _jinja_env.get_template("macros.html").module

class OpenCodeRenderer:
    def render_message(self, entry: dict) -> str:
        # Use the shared macros
        return _macros.message(
            role_class="assistant",
            role_label="Assistant",
            msg_id=f"msg-{entry['id']}",
            timestamp=entry["timestamp"],
            content_html=self._render_content(entry),
            usage=entry.get("usage"),
            model=entry.get("model"),
        )
```

## Testing Your Backend

```python
# Test backend implementation
from vibedeck.backends import get_backend

backend = get_backend("your-backend")
print(f"Name: {backend.name}")
print(f"CLI: {backend.cli_command}")
print(f"Available: {backend.is_cli_available()}")

# Test session discovery
sessions = backend.find_recent_sessions(limit=5)
print(f"Found {len(sessions)} sessions")

if sessions:
    # Test metadata
    meta = backend.get_session_metadata(sessions[0])
    print(f"Session: {meta.project_name}")

    # Test tailer
    tailer = backend.create_tailer(sessions[0])
    messages = tailer.read_all()
    print(f"Messages: {len(messages)}")

    # Test renderer
    renderer = backend.get_message_renderer()
    if messages:
        html = renderer.render_message(messages[0])
        print(f"Rendered: {len(html)} chars")
```

## Checklist

Before submitting a new backend:

- [ ] Implements all required `CodingToolBackend` methods
- [ ] `SessionTailerProtocol` correctly tracks position for incremental reads
- [ ] `read_all()` doesn't modify tailer position
- [ ] `waiting_for_input` correctly reflects session state
- [ ] `MessageRendererProtocol` produces valid HTML
- [ ] Registered in `registry.py`
- [ ] Works with `--backend your-backend` CLI option
- [ ] Handles missing/deleted session files gracefully
- [ ] Token usage calculation handles streaming deduplication (if applicable)
