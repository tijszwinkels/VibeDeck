"""Idle session tracker for automatic summarization.

Tracks session activity and triggers summarization after idle thresholds.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Awaitable

if TYPE_CHECKING:
    from ..sessions import SessionInfo

logger = logging.getLogger(__name__)

# Timeout for summary generation (seconds)
SUMMARY_TIMEOUT = 300  # 5 minutes

# How often to check for stuck summarizations (seconds)
STUCK_CHECK_INTERVAL = 60


class SummaryState(Enum):
    """State machine states for summary tracking."""

    NONE = "none"  # No summary yet
    PENDING = "pending"  # Waiting for idle threshold
    SUMMARIZING = "summarizing"  # Summary in progress
    DONE = "done"  # Summary complete
    FAILED = "failed"  # Summary failed


@dataclass
class TrackedSession:
    """A session being tracked for idle-based summarization."""

    session_id: str
    state: SummaryState = SummaryState.NONE
    last_activity: datetime = field(default_factory=datetime.now)
    summary_started_at: datetime | None = None

    def mark_active(self) -> None:
        """Mark session as having new activity."""
        self.last_activity = datetime.now()
        # If already done or failed, reset to pending for re-summarization
        if self.state in (SummaryState.DONE, SummaryState.FAILED):
            self.state = SummaryState.PENDING
        elif self.state == SummaryState.NONE:
            self.state = SummaryState.PENDING

    def mark_summarizing(self) -> None:
        """Mark session as currently being summarized."""
        self.state = SummaryState.SUMMARIZING
        self.summary_started_at = datetime.now()

    def mark_done(self) -> None:
        """Mark session as summarized."""
        self.state = SummaryState.DONE
        self.summary_started_at = None

    def mark_failed(self) -> None:
        """Mark session as failed."""
        self.state = SummaryState.FAILED
        self.summary_started_at = None

    def seconds_since_activity(self) -> float:
        """Return seconds since last activity."""
        return (datetime.now() - self.last_activity).total_seconds()

    def seconds_since_summary_started(self) -> float | None:
        """Return seconds since summary started, or None if not summarizing."""
        if self.summary_started_at is None:
            return None
        return (datetime.now() - self.summary_started_at).total_seconds()


# Type alias for summarize callback
SummarizeCallback = Callable[["SessionInfo"], Awaitable[bool]]


class IdleTracker:
    """Tracks sessions and triggers summarization after idle threshold.

    Uses per-session asyncio timers for efficient idle detection.
    """

    def __init__(
        self,
        idle_threshold_seconds: int,
        summarize_callback: SummarizeCallback,
        get_session_callback: Callable[[str], "SessionInfo | None"],
    ):
        """Initialize the idle tracker.

        Args:
            idle_threshold_seconds: Seconds of inactivity before summarizing.
            summarize_callback: Async callback to run summarization.
            get_session_callback: Callback to get SessionInfo by ID.
        """
        self.idle_threshold_seconds = idle_threshold_seconds
        self.summarize_callback = summarize_callback
        self.get_session_callback = get_session_callback
        self.sessions: dict[str, TrackedSession] = {}
        self._timers: dict[str, asyncio.Task] = {}
        self._shutdown = False
        self._stuck_check_task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the stuck check background task."""
        if self._stuck_check_task is None:
            self._stuck_check_task = asyncio.create_task(self._stuck_check_loop())
            logger.info(f"Idle tracker started (threshold: {self.idle_threshold_seconds}s)")

    def mark_session_summarized(self, session_id: str) -> None:
        """Mark a session as summarized (from external immediate summarization).

        This cancels any pending idle timer and marks the session as DONE,
        preventing redundant re-summarization until new activity occurs.

        Args:
            session_id: The session that was summarized.
        """
        # Cancel pending timer if any
        if session_id in self._timers:
            self._timers[session_id].cancel()
            del self._timers[session_id]

        # Mark session as done
        if session_id in self.sessions:
            self.sessions[session_id].mark_done()
        else:
            # Create a new tracked session in DONE state
            tracked = TrackedSession(session_id=session_id, state=SummaryState.DONE)
            self.sessions[session_id] = tracked

        logger.debug(f"Session {session_id} marked as summarized (external)")

    def on_session_activity(self, session_id: str) -> None:
        """Handle activity on a session.

        Cancels any pending idle timer and schedules a new one.

        Args:
            session_id: The session that had activity.
        """
        if self._shutdown:
            return

        # Cancel existing timer if any
        if session_id in self._timers:
            self._timers[session_id].cancel()
            del self._timers[session_id]

        # Update or create tracked session
        if session_id in self.sessions:
            tracked = self.sessions[session_id]
            # Don't reset if currently summarizing
            if tracked.state != SummaryState.SUMMARIZING:
                tracked.mark_active()
        else:
            tracked = TrackedSession(session_id=session_id, state=SummaryState.PENDING)
            self.sessions[session_id] = tracked

        # Schedule new idle timer
        self._schedule_idle_timer(session_id)

    def _schedule_idle_timer(self, session_id: str) -> None:
        """Schedule a timer to trigger summarization after idle threshold."""

        async def idle_timer():
            try:
                await asyncio.sleep(self.idle_threshold_seconds)
                if self._shutdown:
                    return
                await self._on_idle_timeout(session_id)
            except asyncio.CancelledError:
                pass  # Timer was cancelled due to new activity

        task = asyncio.create_task(idle_timer())
        self._timers[session_id] = task
        logger.debug(
            f"Scheduled idle timer for {session_id} ({self.idle_threshold_seconds}s)"
        )

    async def _on_idle_timeout(self, session_id: str) -> None:
        """Called when a session's idle timer fires."""
        # Clean up timer reference
        if session_id in self._timers:
            del self._timers[session_id]

        tracked = self.sessions.get(session_id)
        if not tracked:
            return

        # Only summarize if in pending state
        if tracked.state != SummaryState.PENDING:
            logger.debug(
                f"Session {session_id} not pending (state={tracked.state}), skipping"
            )
            return

        # Get the actual SessionInfo
        session_info = self.get_session_callback(session_id)
        if not session_info:
            logger.warning(f"Session {session_id} not found for summarization")
            return

        logger.info(f"Session {session_id} idle, triggering summary")
        tracked.mark_summarizing()

        # Run summarization
        try:
            success = await self.summarize_callback(session_info)
            if success:
                tracked.mark_done()
            else:
                tracked.mark_failed()
        except Exception as e:
            logger.exception(f"Error summarizing session {session_id}: {e}")
            tracked.mark_failed()

    async def _stuck_check_loop(self) -> None:
        """Periodically check for stuck summarizations."""
        while not self._shutdown:
            await asyncio.sleep(STUCK_CHECK_INTERVAL)
            await self._check_stuck_summarizations()

    async def _check_stuck_summarizations(self) -> None:
        """Check for summarizations that have been running too long."""
        for session_id, tracked in list(self.sessions.items()):
            if self._shutdown:
                break

            if tracked.state == SummaryState.SUMMARIZING:
                elapsed = tracked.seconds_since_summary_started()
                if elapsed and elapsed > SUMMARY_TIMEOUT:
                    logger.warning(
                        f"Session {session_id} summary timed out after {elapsed:.0f}s"
                    )
                    tracked.mark_failed()

    def shutdown(self) -> None:
        """Signal shutdown - cancel all pending timers."""
        self._shutdown = True

        # Cancel all pending idle timers
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()

        # Cancel stuck check task
        if self._stuck_check_task:
            self._stuck_check_task.cancel()
