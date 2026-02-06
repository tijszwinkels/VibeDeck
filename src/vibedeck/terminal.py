"""Terminal WebSocket handler with PTY support.

Provides an embedded terminal in VibeDeck using xterm.js on the frontend
and ptyprocess for PTY management on the backend.
"""

import asyncio
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Try to import ptyprocess - it's optional
try:
    import ptyprocess

    PTYPROCESS_AVAILABLE = True
except ImportError:
    PTYPROCESS_AVAILABLE = False
    logger.warning("ptyprocess not installed - terminal feature disabled")


@dataclass
class TerminalSession:
    """Represents an active terminal session."""

    websocket: WebSocket
    process: "ptyprocess.PtyProcess | None" = None
    read_task: asyncio.Task | None = None
    cwd: str | None = None
    session_id: str | None = None  # VibeDeck session ID for logging

    # Track if we're shutting down to avoid sending after close
    closing: bool = False


class TerminalManager:
    """Manages PTY processes for WebSocket terminal connections."""

    def __init__(self) -> None:
        self.sessions: dict[int, TerminalSession] = {}

    def _get_shell(self) -> str:
        """Get the user's shell or fall back to /bin/bash."""
        shell = os.environ.get("SHELL", "/bin/bash")
        # Verify the shell exists
        if shutil.which(shell):
            return shell
        # Fall back to common shells
        for fallback in ["/bin/bash", "/bin/sh"]:
            if shutil.which(fallback):
                return fallback
        return "/bin/sh"

    def _get_available_shells(self) -> list[str]:
        """Get list of available shells on the system."""
        shells = []
        # Check /etc/shells if available
        try:
            with open("/etc/shells") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and shutil.which(line):
                        shells.append(line)
        except FileNotFoundError:
            pass

        # Always include common shells if they exist
        for shell in ["/bin/bash", "/bin/zsh", "/bin/fish", "/bin/sh"]:
            if shell not in shells and shutil.which(shell):
                shells.append(shell)

        return shells

    async def spawn_pty(
        self, session: TerminalSession, rows: int = 24, cols: int = 80
    ) -> bool:
        """Spawn a PTY process for a terminal session.

        Returns True if successful, False otherwise.
        """
        if not PTYPROCESS_AVAILABLE:
            logger.error("Cannot spawn PTY: ptyprocess not installed")
            return False

        shell = self._get_shell()
        cwd = session.cwd

        # Validate cwd exists
        if cwd and not Path(cwd).is_dir():
            logger.warning(f"Terminal cwd does not exist: {cwd}, using home")
            cwd = str(Path.home())

        try:
            logger.info(f"Spawning shell: {shell} in {cwd or 'home'}")
            proc = ptyprocess.PtyProcess.spawn(
                [shell, "-l"],  # Login shell for proper environment
                cwd=cwd,
                dimensions=(rows, cols),
                env={
                    **os.environ,
                    "TERM": "xterm-256color",
                    "COLORTERM": "truecolor",
                },
            )
            session.process = proc
            return True
        except Exception as e:
            logger.error(f"Failed to spawn PTY: {e}")
            return False

    async def _read_pty_output(self, session: TerminalSession) -> None:
        """Read from PTY and send to WebSocket.

        Uses a dedicated thread for blocking PTY reads, bridged to async
        via a queue. This avoids the race condition of orphaned executor
        futures that occurs with asyncio.wait_for + run_in_executor.
        """
        proc = session.process
        ws = session.websocket
        loop = asyncio.get_event_loop()

        if not proc:
            return

        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        def reader_thread() -> None:
            """Blocking reader in a dedicated thread."""
            try:
                while proc.isalive() and not session.closing:
                    try:
                        data = proc.read(4096)
                        if data:
                            loop.call_soon_threadsafe(queue.put_nowait, data)
                    except EOFError:
                        break
                    except Exception as e:
                        if not session.closing:
                            logger.error(f"PTY read error: {e}")
                        break
            finally:
                # Sentinel to signal reader is done
                loop.call_soon_threadsafe(queue.put_nowait, None)

        thread = threading.Thread(target=reader_thread, daemon=True)
        thread.start()

        try:
            while not session.closing:
                data = await queue.get()
                if data is None:
                    break
                await ws.send_json(
                    {
                        "type": "output",
                        "data": data.decode("utf-8", errors="replace"),
                    }
                )
        except Exception as e:
            if not session.closing:
                logger.error(f"PTY send error: {e}")
        finally:
            if not session.closing:
                try:
                    await ws.send_json(
                        {"type": "exit", "code": proc.exitstatus or 0}
                    )
                except Exception:
                    pass
            logger.debug(f"PTY read loop ended, exit status: {proc.exitstatus}")

    async def handle_websocket(
        self, websocket: WebSocket, cwd: str | None = None, session_id: str | None = None
    ) -> None:
        """Handle a WebSocket connection for terminal I/O.

        Protocol:
        - Client sends: {"type": "input", "data": "..."} for keyboard input
        - Client sends: {"type": "resize", "rows": N, "cols": M} for terminal resize
        - Server sends: {"type": "output", "data": "..."} for terminal output
        - Server sends: {"type": "exit", "code": N} when shell exits
        - Server sends: {"type": "error", "message": "..."} on errors
        """
        await websocket.accept()
        ws_id = id(websocket)

        session = TerminalSession(websocket=websocket, cwd=cwd, session_id=session_id)
        self.sessions[ws_id] = session

        logger.info(f"Terminal WebSocket connected: ws={ws_id}, session={session_id}")

        # Spawn PTY
        if not await self.spawn_pty(session):
            await websocket.send_json(
                {"type": "error", "message": "Failed to spawn terminal"}
            )
            await websocket.close()
            del self.sessions[ws_id]
            return

        # Start output reader task
        session.read_task = asyncio.create_task(self._read_pty_output(session))

        try:
            async for message in websocket.iter_json():
                msg_type = message.get("type")

                if msg_type == "input":
                    data = message.get("data", "")
                    if session.process and session.process.isalive():
                        try:
                            session.process.write(data.encode("utf-8") if isinstance(data, str) else data)
                        except Exception as e:
                            logger.error(f"Failed to write to PTY: {e}")

                elif msg_type == "resize":
                    rows = message.get("rows", 24)
                    cols = message.get("cols", 80)
                    if session.process and session.process.isalive():
                        try:
                            session.process.setwinsize(rows, cols)
                            logger.debug(f"Terminal resized: {rows}x{cols}")
                        except Exception as e:
                            logger.error(f"Failed to resize PTY: {e}")

                else:
                    logger.warning(f"Unknown terminal message type: {msg_type}")

        except WebSocketDisconnect:
            logger.info(f"Terminal WebSocket disconnected: {ws_id}")
        except Exception as e:
            logger.error(f"Terminal WebSocket error: {e}")
        finally:
            await self._cleanup_session(ws_id)

    async def _cleanup_session(self, ws_id: int) -> None:
        """Clean up a terminal session."""
        session = self.sessions.get(ws_id)
        if not session:
            return

        session.closing = True

        # Cancel read task
        if session.read_task:
            session.read_task.cancel()
            try:
                await session.read_task
            except asyncio.CancelledError:
                pass

        # Terminate PTY
        if session.process and session.process.isalive():
            logger.info(f"Terminating PTY for session {ws_id}")
            try:
                session.process.terminate(force=True)
            except Exception as e:
                logger.error(f"Failed to terminate PTY: {e}")

        del self.sessions[ws_id]
        logger.info(f"Terminal session cleaned up: {ws_id}")

    def get_shells(self) -> dict:
        """Get available shells and the default shell."""
        return {
            "shells": self._get_available_shells(),
            "default": self._get_shell(),
        }


# Global terminal manager instance
terminal_manager = TerminalManager()


def is_terminal_available() -> bool:
    """Check if terminal feature is available (ptyprocess installed)."""
    return PTYPROCESS_AVAILABLE
