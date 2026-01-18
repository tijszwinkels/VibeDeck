"""Generate session summaries using Claude CLI.

Uses --no-session-persistence with --resume to read session context
and generate summaries without writing anything back to the session file.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import format_prompt, get_prompt_template
from .output import LogWriter

if TYPE_CHECKING:
    from ..backends.protocol import CodingToolBackend
    from ..sessions import SessionInfo

logger = logging.getLogger(__name__)

# Timeout for the Claude CLI subprocess (seconds)
SUBPROCESS_TIMEOUT = 300  # 5 minutes


@dataclass
class ParsedResponse:
    """Result of parsing Claude CLI output."""

    summary: dict[str, Any]


@dataclass
class SummaryResult:
    """Result of a summarization attempt."""

    success: bool
    summary: dict[str, Any] | None = None
    error: str | None = None


class Summarizer:
    """Handles session summarization using --no-session-persistence.

    This approach reads the session for context but doesn't write anything
    back, avoiding the need for backup/restore or fork cleanup.
    """

    def __init__(
        self,
        backend: CodingToolBackend,
        log_writer: LogWriter | None = None,
        prompt: str | None = None,
        prompt_file: Path | None = None,
    ):
        """Initialize the summarizer.

        Args:
            backend: The backend to use for building CLI commands.
            log_writer: Optional log writer for JSONL output.
            prompt: Optional custom prompt template.
            prompt_file: Optional path to prompt template file.
        """
        self.backend = backend
        self.log_writer = log_writer or LogWriter()
        self.prompt = prompt
        self.prompt_file = prompt_file

    async def summarize(self, session: SessionInfo, model: str | None = None) -> SummaryResult:
        """Generate a summary for a session.

        Uses --no-session-persistence to read the session context without
        modifying the session file.

        Args:
            session: The session to summarize.
            model: Optional model to use for summarization (e.g., 'haiku', 'sonnet', 'opus').
                   If None, uses the CLI default.

        Returns:
            SummaryResult with success status and summary data.
        """
        generated_at = datetime.now().isoformat()

        # Format prompt with session metadata
        prompt_template = get_prompt_template(self.prompt, self.prompt_file)
        prompt = format_prompt(
            template=prompt_template,
            session_id=session.session_id,
            project_path=session.project_path or "Unknown",
            generated_at=generated_at,
            session_started_at=session.started_at or "Unknown",
        )

        # Build resume command with --no-session-persistence
        # This reads the session for context but doesn't write anything back
        cmd = self.backend.build_send_command(
            session_id=session.session_id,
            message=prompt,
            skip_permissions=True,
        )
        cmd.extend(["--no-session-persistence", "--output-format", "json"])

        # Add model flag if specified
        if model:
            cmd.extend(["--model", model])

        logger.debug(f"Running summary command: {' '.join(cmd)}")

        try:
            # Run from the project directory
            # Claude CLI requires being in the project directory to find sessions
            cwd = session.project_path if session.project_path else None

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=SUBPROCESS_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error(f"Summary command timed out for session {session.session_id}")
                process.kill()
                await process.wait()
                return SummaryResult(success=False, error="Command timed out")

            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(
                    f"Summary command failed for session {session.session_id}: "
                    f"exit code {process.returncode}, stderr: {error_msg}"
                )
                return SummaryResult(success=False, error=error_msg)

            # Parse the JSON output
            raw_response = stdout.decode()
            parsed = self._parse_response(raw_response)

            if parsed is None:
                logger.error(f"Failed to parse summary for session {session.session_id}")
                return SummaryResult(success=False, error="Failed to parse response")

            summary = parsed.summary

            # Write summary.json to session directory
            summary_path = self._write_summary_json(session, summary, raw_response)

            # Add summary_file to summary for the log
            if summary_path:
                summary["summary_file"] = str(summary_path)

            # Add session_last_updated_at
            summary["session_last_updated_at"] = datetime.now().isoformat()

            # Append to JSONL log if configured
            self.log_writer.write_entry(summary)

            logger.info(f"Session {session.session_id} summarized: {summary.get('title', 'No title')}")
            return SummaryResult(success=True, summary=summary)

        except FileNotFoundError:
            error_msg = "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
            logger.error(error_msg)
            return SummaryResult(success=False, error=error_msg)
        except Exception as e:
            logger.exception(f"Error running summary command: {e}")
            return SummaryResult(success=False, error=str(e))

    def _parse_response(self, raw_response: str) -> ParsedResponse | None:
        """Parse Claude's JSON response.

        The LLM outputs the full summary JSON directly, which we pass through.

        Args:
            raw_response: Raw stdout from Claude CLI.

        Returns:
            ParsedResponse with summary dict, or None if parsing failed.
        """
        try:
            # Claude CLI with --output-format json outputs JSON lines
            # Find the assistant's response
            lines = raw_response.strip().split("\n")
            response_text = None

            for line in lines:
                try:
                    data = json.loads(line)
                    # Look for the assistant's text response
                    if data.get("type") == "result":
                        response_text = data.get("result", "")
                        break
                except json.JSONDecodeError:
                    continue

            if not response_text:
                logger.warning(f"No result found in response: {raw_response[:500]}")
                return None

            # Parse the JSON from the response (might be wrapped in markdown code blocks)
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                summary = json.loads(json_str)
                return ParsedResponse(summary=summary)

            logger.warning("Could not find JSON in response")
            return None

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON from response: {e}")
            return None
        except Exception as e:
            logger.exception(f"Error parsing response: {e}")
            return None

    def _write_summary_json(
        self, session: SessionInfo, summary: dict[str, Any], raw_response: str
    ) -> Path | None:
        """Write summary.json to the session directory.

        Args:
            session: The session being summarized.
            summary: The parsed summary dict from Claude.
            raw_response: The raw CLI response for debugging.

        Returns:
            The path to the written summary file, or None if writing failed.
        """
        try:
            summary_path = session.path.parent / f"{session.session_id}_summary.json"
            # Add raw_response for debugging
            output = {**summary, "raw_response": raw_response}
            with open(summary_path, "w") as f:
                json.dump(output, f, indent=2)
            logger.debug(f"Wrote summary to {summary_path}")
            return summary_path
        except Exception as e:
            logger.warning(f"Failed to write summary.json: {e}")
            return None
