"""Session custom title management routes."""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from vibedeck.broadcasting import broadcast_event, broadcast_json_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# User preferences config directory
CONFIG_DIR = Path.home() / ".config" / "vibedeck"


class SessionTitlesResponse(BaseModel):
    """Response for session custom titles."""

    titles: dict[str, str]


class SessionTitleRequest(BaseModel):
    """Request body for setting a custom session title."""

    session_id: str
    title: str | None  # Custom title string, or None to clear


def _get_session_titles_path() -> Path:
    """Get the path to the session titles config file."""
    return CONFIG_DIR / "session-titles.json"


def _load_session_titles() -> dict[str, str]:
    """Load custom session titles from config file."""
    config_path = _get_session_titles_path()
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r") as f:
            data = json.load(f)
            return data.get("titles", {})
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load session titles: {e}")
        return {}


def _save_session_titles(titles: dict[str, str]) -> bool:
    """Save custom session titles to config file."""
    config_path = _get_session_titles_path()
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump({"titles": titles}, f, indent=2)
        return True
    except OSError as e:
        logger.error(f"Failed to save session titles: {e}")
        return False


@router.get("/session-titles")
async def get_session_titles() -> SessionTitlesResponse:
    """Get all custom session titles."""
    return SessionTitlesResponse(titles=_load_session_titles())


@router.post("/session-titles/set")
async def set_session_title(request: SessionTitleRequest) -> dict:
    """Set a custom title for a session. Use title=null to clear."""
    session_id = request.session_id
    title = request.title

    if title is not None and len(title.strip()) == 0:
        title = None  # Treat empty/whitespace-only as clearing

    if title is not None and len(title) > 200:
        raise HTTPException(status_code=400, detail="Title too long (max 200 chars)")

    titles = _load_session_titles()

    if title is None:
        titles.pop(session_id, None)
    else:
        titles[session_id] = title.strip()

    if _save_session_titles(titles):
        logger.info(f"Set custom title for session {session_id}: {title}")
        payload = {"session_id": session_id, "title": title}
        await broadcast_event("session_title_updated", payload)
        await broadcast_json_event("session_title_updated", payload)
        return {"status": "updated", "session_id": session_id, "title": title}
    else:
        raise HTTPException(status_code=500, detail="Failed to save session title")
