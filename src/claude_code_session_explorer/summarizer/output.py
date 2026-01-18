"""Output file writing for session summaries."""

from __future__ import annotations

import fcntl
import json
import logging
from pathlib import Path
from typing import Any

from .config import DEFAULT_OUTPUT_KEYS

logger = logging.getLogger(__name__)


class LogWriter:
    """Writes session summaries to a JSONL log file."""

    def __init__(
        self,
        log_path: Path | None = None,
        log_keys: list[str] | None = None,
    ):
        """Initialize the log writer.

        Args:
            log_path: Path to JSONL log file. If None, no log is written.
            log_keys: Keys to include in log entries. If None, uses defaults.
        """
        self.log_path = log_path
        self.log_keys = log_keys if log_keys is not None else DEFAULT_OUTPUT_KEYS

    def write_entry(self, summary: dict[str, Any]) -> bool:
        """Append a summary entry to the log as JSONL.

        Uses file locking for safe concurrent writes.

        Args:
            summary: The session summary dict.

        Returns:
            True if successful (or no log configured), False on error.
        """
        if not self.log_path:
            return True  # No log configured - not an error

        try:
            # Ensure parent directory exists
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

            # Filter keys if configured
            if self.log_keys:
                filtered = {k: v for k, v in summary.items() if k in self.log_keys}
            else:
                filtered = summary

            # Write as single-line JSON
            line = json.dumps(filtered, ensure_ascii=False)

            # Append with file locking for concurrent safety
            with open(self.log_path, "a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(line)
                    f.write("\n")
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            logger.debug(f"Wrote summary entry to {self.log_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to write log entry: {e}")
            return False
