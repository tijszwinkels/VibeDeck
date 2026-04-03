"""Custom session title management routes."""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..models import SessionTitleRequest, SessionTitlesResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# User preferences config directory
CONFIG_DIR = Path.home() / ".config" / "vibedeck"


def _get_session_titles_path() -> Path:
    """Get the path to the custom session titles config file."""
    return CONFIG_DIR / "session-titles.json"


def _normalize_title(title: str | None) -> str | None:
    """Normalize a custom title, treating blank values as cleared."""
    if title is None:
        return None
    normalized = " ".join(title.split()).strip()
    return normalized or None


def _load_session_titles() -> dict[str, str]:
    """Load custom session titles from config file."""
    config_path = _get_session_titles_path()
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r") as f:
            data = json.load(f)
            titles = data.get("titles", {})
            if isinstance(titles, dict):
                return {
                    str(session_id): normalized
                    for session_id, title in titles.items()
                    if (normalized := _normalize_title(title)) is not None
                }
            logger.warning("Session titles file did not contain a titles object")
            return {}
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
    """Set a custom title for a session. Use title=null or blank to clear."""
    session_id = request.session_id
    normalized_title = _normalize_title(request.title)
    titles = _load_session_titles()

    if normalized_title is None:
        titles.pop(session_id, None)
    else:
        titles[session_id] = normalized_title

    if _save_session_titles(titles):
        logger.info(f"Set custom title for session {session_id}: {normalized_title!r}")
        return {"status": "updated", "session_id": session_id, "new_title": normalized_title}

    raise HTTPException(status_code=500, detail="Failed to save session titles")
