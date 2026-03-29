"""Generate session summaries using Claude CLI.

Uses --no-session-persistence with --resume to read session context
and generate summaries without writing anything back to the session file.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
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

# Model to use for Codex summarization (cheap and fast)
CODEX_SUMMARY_MODEL = "gpt-5.4-mini"


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
        thinking_budget: int | None = None,
    ):
        """Initialize the summarizer.

        Args:
            backend: The backend to use for building CLI commands.
            log_writer: Optional log writer for JSONL output.
            prompt: Optional custom prompt template.
            prompt_file: Optional path to prompt template file.
            thinking_budget: Fixed thinking token budget (for cache consistency).
        """
        self.backend = backend
        self.log_writer = log_writer or LogWriter()
        self.prompt = prompt
        self.prompt_file = prompt_file
        self.thinking_budget = thinking_budget

    def _build_summary_command(self, session: "SessionInfo", prompt: str) -> tuple[list[str], str | None]:
        """Build a backend-appropriate non-persistent summary command."""
        cli_command = getattr(self.backend, "cli_command", None)

        if cli_command == "codex":
            return self._build_codex_summary_command(session, prompt)
        if cli_command == "pi":
            return self._build_pi_summary_command(session, prompt)

        build_send = self.backend.build_send_command
        kwargs: dict[str, Any] = {
            "session_id": session.session_id,
            "message": prompt,
            "skip_permissions": True,
        }

        try:
            signature = inspect.signature(build_send)
        except (TypeError, ValueError):
            signature = None

        if signature is not None and "output_format" in signature.parameters:
            kwargs["output_format"] = "json"

        cmd_spec = build_send(**kwargs)
        cmd_args = list(cmd_spec.args)

        if cli_command == "claude":
            cmd_args.append("--no-session-persistence")

        return cmd_args, cmd_spec.stdin

    def _build_codex_summary_command(
        self, session: "SessionInfo", prompt: str
    ) -> tuple[list[str], str | None]:
        """Build a transcript-fed summary command for Codex.

        Instead of resuming the session (which would pollute it), we:
        1. Generate a compact markdown transcript of the session
        2. Embed it in the prompt
        3. Run a fresh ephemeral Codex execution
        """
        transcript = self._generate_codex_transcript(session)

        full_prompt = (
            "Here is the session transcript to summarize:\n\n"
            f"{transcript}\n\n---\n\n{prompt}"
        )

        cmd_spec = self.backend.build_new_session_command(
            message=full_prompt,
            skip_permissions=True,
            output_format="json",
        )
        cmd_args = list(cmd_spec.args)
        cmd_args.append("--ephemeral")

        return cmd_args, cmd_spec.stdin

    def _generate_codex_transcript(self, session: "SessionInfo") -> str:
        """Generate a compact markdown transcript for a Codex session."""
        from ..export import format_session_as_markdown, parse_codex_entries

        entries = parse_codex_entries(session.path)
        return format_session_as_markdown(
            entries, session.path, backend="codex", hide_tools=True
        )

    def _build_pi_summary_command(
        self, session: "SessionInfo", prompt: str
    ) -> tuple[list[str], str | None]:
        """Build an ephemeral transcript-fed summary command for Pi."""
        transcript = self._generate_pi_transcript(session)

        full_prompt = (
            "Here is the session transcript to summarize:\n\n"
            f"{transcript}\n\n---\n\n{prompt}"
        )

        cmd_spec = self.backend.build_new_session_command(
            message=full_prompt,
            skip_permissions=True,
        )
        cmd_args = list(cmd_spec.args)
        cmd_args.extend(["--no-session", "--mode", "json", "--no-tools"])

        return cmd_args, cmd_spec.stdin

    def _generate_pi_transcript(self, session: "SessionInfo") -> str:
        """Generate a compact markdown transcript for a Pi session."""
        from ..backends.pi.tailer import PiTailer
        from ..export import format_session_as_markdown

        entries = PiTailer(session.path).read_all()
        return format_session_as_markdown(entries, session.path, backend="pi", hide_tools=True)

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
        # Get session start time from tailer
        try:
            session_started_at = session.tailer.get_first_timestamp() or "Unknown"
        except Exception:
            session_started_at = "Unknown"

        prompt = format_prompt(
            template=prompt_template,
            session_id=session.session_id,
            project_path=session.project_path or "Unknown",
            generated_at=generated_at,
            session_started_at=session_started_at,
        )

        cmd_args, cmd_stdin = self._build_summary_command(session, prompt)

        # Add model flag if specified
        # Codex uses OpenAI models — override Claude model names
        cli_command = getattr(self.backend, "cli_command", None)
        if cli_command == "codex":
            model = CODEX_SUMMARY_MODEL
        if model:
            cmd_args.extend(["--model", model])

        logger.debug(f"Running summary command: {' '.join(cmd_args)}")

        try:
            # Run from the project directory
            # Claude CLI requires being in the project directory to find sessions
            cwd = session.project_path if session.project_path else None

            # Set up environment with thinking budget if configured
            env = None
            if self.thinking_budget is not None:
                env = {**os.environ, "MAX_THINKING_TOKENS": str(self.thinking_budget)}
                logger.debug(f"Using thinking budget: {self.thinking_budget}")

            # Use PIPE for stdin if we need to pass message content
            stdin_pipe = asyncio.subprocess.PIPE if cmd_stdin else asyncio.subprocess.DEVNULL

            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdin=stdin_pipe,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            # Write message to stdin if provided
            if cmd_stdin:
                process.stdin.write(cmd_stdin.encode())
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()

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
                stdout_preview = stdout.decode()[:500] if stdout else "(empty)"
                logger.error(
                    f"Summary command failed for session {session.session_id}: "
                    f"exit code {process.returncode}, "
                    f"cmd: {' '.join(cmd_args)}, "
                    f"stderr: {error_msg}, stdout: {stdout_preview}"
                )
                return SummaryResult(success=False, error=error_msg)

            # Parse the JSON output
            raw_response = stdout.decode()
            stderr_output = stderr.decode() if stderr else ""
            parsed = self._parse_response(raw_response)

            if parsed is None:
                logger.error(
                    f"Failed to parse summary for session {session.session_id}. "
                    f"stderr: {stderr_output!r}"
                )
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
        """Parse CLI JSON output containing a summary response.

        The LLM outputs the full summary JSON directly, which we pass through.

        Args:
            raw_response: Raw stdout from the CLI.

        Returns:
            ParsedResponse with summary dict, or None if parsing failed.
        """
        try:
            lines = raw_response.strip().split("\n")
            if self._looks_like_pi_json_mode_output(lines):
                response_text = self._parse_pi_json_mode_response(lines, raw_response)
            else:
                response_text = self._parse_claude_or_codex_response(lines, raw_response)

            if not response_text:
                return None

            summary = self._parse_first_json_object(response_text)
            if summary is not None:
                return ParsedResponse(summary=summary)

            logger.warning("Could not find JSON in response")
            return None

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON from response: {e}")
            return None
        except Exception as e:
            logger.exception(f"Error parsing response: {e}")
            return None

    def _looks_like_pi_json_mode_output(self, lines: list[str]) -> bool:
        """Detect Pi JSON mode output."""
        for line in lines:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("type") in {
                "agent_start",
                "agent_end",
                "turn_start",
                "turn_end",
                "message_start",
                "message_update",
                "message_end",
                "tool_execution_start",
                "tool_execution_update",
                "tool_execution_end",
            }:
                return True
        return False

    def _parse_claude_or_codex_response(self, lines: list[str], raw_response: str) -> str | None:
        """Parse response text from Claude or Codex JSON output."""
        response_text = None

        for line in lines:
            try:
                data = json.loads(line)
                # Handle both formats: JSON Lines (dict per line) or array
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    # Claude Code format: {"type": "result", "result": "..."}
                    if item.get("type") == "result":
                        response_text = item.get("result", "")
                        break
                    # Codex format: {"type": "item.completed", "item": {"text": "..."}}
                    if item.get("type") == "item.completed":
                        response_text = item.get("item", {}).get("text", "")
                        break
                if response_text:
                    break
            except json.JSONDecodeError:
                continue

        if not response_text:
            logger.warning(
                f"No result found in response ({len(lines)} line(s)): {raw_response[:1000]!r}"
            )
            return None

        return response_text

    def _parse_pi_json_mode_response(self, lines: list[str], raw_response: str) -> str | None:
        """Parse response text from Pi JSON mode output."""
        response_text = None

        for line in lines:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue

            event_type = data.get("type")
            if event_type in {"turn_end", "message_end"}:
                message = data.get("message", {})
                if isinstance(message, dict) and message.get("role") == "assistant":
                    response_text = self._extract_pi_message_text(message)
            elif event_type == "agent_end":
                messages = data.get("messages", [])
                if isinstance(messages, list):
                    for message in reversed(messages):
                        if isinstance(message, dict) and message.get("role") == "assistant":
                            response_text = self._extract_pi_message_text(message)
                            if response_text:
                                break
            if response_text:
                break

        if not response_text:
            logger.warning(
                f"No result found in response ({len(lines)} line(s)): {raw_response[:1000]!r}"
            )
            return None

        return response_text

    def _extract_pi_message_text(self, message: dict) -> str | None:
        """Extract plain assistant text from a Pi message.

        Only include final text blocks. Thinking blocks may contain JSON-like
        signatures that are not part of the answer and break summary parsing.
        """
        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
            return text or None
        if not isinstance(content, list):
            return None

        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text") or ""
                if text:
                    parts.append(text)
        text = "\n".join(parts).strip()
        return text or None

    def _parse_first_json_object(self, response_text: str) -> dict[str, Any] | None:
        """Parse the first JSON object from model response text.

        Handles wrapped content (markdown fences or trailing commentary) by
        decoding from the first object start and ignoring trailing data.
        """
        json_start = response_text.find("{")
        if json_start < 0:
            return None

        decoder = json.JSONDecoder()
        try:
            summary, _ = decoder.raw_decode(response_text[json_start:])
            if isinstance(summary, dict):
                return summary
        except json.JSONDecodeError:
            return None
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
