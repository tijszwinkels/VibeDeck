"""FastAPI route modules."""

from .sessions import router as sessions_router
from .files import router as files_router
from .archives import router as archives_router
from .diff import router as diff_router
from .statuses import router as statuses_router
from .titles import router as titles_router

__all__ = ["sessions_router", "files_router", "archives_router", "diff_router", "statuses_router", "titles_router"]
