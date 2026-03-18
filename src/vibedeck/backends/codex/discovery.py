"""Session discovery helpers for Codex rollout files."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
DEFAULT_HISTORY_PATH = Path.home() / ".codex" / "history.jsonl"
ROLLOUT_PATTERN = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(?P<session_id>[0-9a-f-]+)$"
)
AGENTS_HEADER_PREFIX = "# AGENTS.md instructions for "


def get_session_id(session_path: Path) -> str:
    """Extract the session ID from a rollout filename."""
    match = ROLLOUT_PATTERN.match(session_path.stem)
    if match:
        return match.group("session_id")
    return session_path.stem


def _iter_entries(session_path: Path):
    try:
        with open(session_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse Codex rollout line in %s", session_path)
    except OSError as exc:
        logger.warning("Failed to read Codex session %s: %s", session_path, exc)


def _is_transcript_entry(entry: dict) -> bool:
    entry_type = entry.get("type")
    payload = entry.get("payload", {})
    payload_type = payload.get("type")

    if entry_type != "response_item":
        return False
    if payload_type in {"function_call", "function_call_output"}:
        return True
    return payload_type == "message" and payload.get("role") in {"user", "assistant"}


def is_bootstrap_user_message(entry: dict) -> bool:
    """Check whether an entry is Codex's injected AGENTS bootstrap message."""
    if entry.get("type") != "response_item":
        return False

    payload = entry.get("payload", {})
    if payload.get("type") != "message" or payload.get("role") != "user":
        return False

    content = payload.get("content", [])
    if not isinstance(content, list) or not content:
        return False

    first = content[0]
    if not isinstance(first, dict):
        return False

    text = str(first.get("text", ""))
    return text.startswith(AGENTS_HEADER_PREFIX) and "<INSTRUCTIONS>" in text


def has_messages(session_path: Path, show_bootstrap_messages: bool = False) -> bool:
    """Check whether the rollout contains transcript content."""
    return any(
        _is_transcript_entry(entry)
        and (show_bootstrap_messages or not is_bootstrap_user_message(entry))
        for entry in _iter_entries(session_path)
    )


def _get_session_meta(session_path: Path) -> dict:
    for entry in _iter_entries(session_path):
        if entry.get("type") == "session_meta":
            return entry.get("payload", {})
    return {}


def get_session_name(session_path: Path) -> tuple[str, str | None]:
    """Get project name and cwd from session metadata."""
    meta = _get_session_meta(session_path)
    cwd = meta.get("cwd")
    if cwd:
        return Path(cwd).name or cwd, cwd
    return get_session_id(session_path), None


def get_first_user_message(
    session_path: Path,
    history_path: Path | None = None,
    max_length: int = 200,
    show_bootstrap_messages: bool = False,
) -> str | None:
    """Get the first user-visible prompt for a Codex session."""
    session_id = get_session_id(session_path)
    if history_path and history_path.exists():
        first_match: tuple[int, str] | None = None
        try:
            with open(history_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("session_id") != session_id:
                        continue
                    text = str(entry.get("text", "")).strip()
                    if not text:
                        continue
                    ts = int(entry.get("ts", 0))
                    if first_match is None or ts < first_match[0]:
                        first_match = (ts, text)
        except OSError as exc:
            logger.warning("Failed to read Codex history %s: %s", history_path, exc)
        if first_match:
            text = first_match[1]
            return text[:max_length] if len(text) > max_length else text

    for entry in _iter_entries(session_path):
        if entry.get("type") == "response_item":
            if not show_bootstrap_messages and is_bootstrap_user_message(entry):
                continue
            payload = entry.get("payload", {})
            if payload.get("type") != "message" or payload.get("role") != "user":
                continue
            content = payload.get("content", [])
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = str(block.get("text", "")).strip()
                if text:
                    return text[:max_length] if len(text) > max_length else text
    return None


def _parse_timestamp(timestamp: str | None) -> float:
    if not timestamp:
        return 0.0
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def get_last_message_timestamp(session_path: Path) -> float | None:
    """Return the timestamp of the last transcript entry."""
    last_timestamp = 0.0
    for entry in _iter_entries(session_path):
        if _is_transcript_entry(entry):
            last_timestamp = max(
                last_timestamp, _parse_timestamp(entry.get("timestamp"))
            )
    return last_timestamp or None


def find_recent_sessions(
    sessions_dir: Path | None = None,
    limit: int = 10,
) -> list[Path]:
    """Find recent Codex rollout files with transcript content."""
    sessions_dir = sessions_dir or DEFAULT_SESSIONS_DIR
    if not sessions_dir.exists():
        return []

    candidates: list[tuple[float, Path]] = []
    for path in sessions_dir.glob("**/*.jsonl"):
        if not has_messages(path):
            continue
        last_timestamp = get_last_message_timestamp(path) or path.stat().st_mtime
        candidates.append((last_timestamp, path))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in candidates[:limit]]


def should_watch_file(path: Path) -> bool:
    """Codex sessions are stored as JSONL rollout files."""
    should_watch = path.suffix == ".jsonl" and path.name.startswith("rollout-")
    if should_watch:
        logger.debug("Codex watch candidate accepted: %s", path)
    return should_watch
