"""Tests for the terminal module."""

import asyncio
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vibedeck import server
from vibedeck.server import app
from vibedeck.terminal import TerminalManager, TerminalSession, is_terminal_available


@pytest.fixture(autouse=True)
def reset_terminal_state():
    """Reset terminal state before each test."""
    server.set_terminal_enabled(True)
    yield
    server.set_terminal_enabled(True)


# ---------------------------------------------------------------------------
# TerminalManager._get_shell
# ---------------------------------------------------------------------------


class TestGetShell:
    def test_returns_shell_env_var(self):
        mgr = TerminalManager()
        with patch.dict(os.environ, {"SHELL": "/bin/bash"}):
            with patch.object(shutil, "which", return_value="/bin/bash"):
                assert mgr._get_shell() == "/bin/bash"

    def test_falls_back_to_bash_when_env_missing(self):
        mgr = TerminalManager()
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                shutil, "which", side_effect=lambda s: s if s == "/bin/bash" else None
            ):
                assert mgr._get_shell() == "/bin/bash"

    def test_falls_back_to_sh_when_bash_missing(self):
        mgr = TerminalManager()
        with patch.dict(os.environ, {"SHELL": "/nonexistent/shell"}):
            with patch.object(
                shutil, "which", side_effect=lambda s: s if s == "/bin/sh" else None
            ):
                assert mgr._get_shell() == "/bin/sh"

    def test_ultimate_fallback_is_sh(self):
        mgr = TerminalManager()
        with patch.dict(os.environ, {"SHELL": "/nonexistent"}):
            with patch.object(shutil, "which", return_value=None):
                assert mgr._get_shell() == "/bin/sh"


# ---------------------------------------------------------------------------
# TerminalManager._get_available_shells
# ---------------------------------------------------------------------------


class TestGetAvailableShells:
    def test_includes_common_shells(self, tmp_path):
        mgr = TerminalManager()
        # Mock /etc/shells as nonexistent, but common shells exist
        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch.object(
                shutil,
                "which",
                side_effect=lambda s: s if s in ["/bin/bash", "/bin/sh"] else None,
            ):
                shells = mgr._get_available_shells()
                assert "/bin/bash" in shells
                assert "/bin/sh" in shells
                assert "/bin/zsh" not in shells

    def test_reads_etc_shells(self, tmp_path):
        mgr = TerminalManager()
        etc_shells = "/bin/bash\n/bin/zsh\n# comment\n"
        mock_open = MagicMock()
        mock_open.return_value.__enter__ = MagicMock(
            return_value=etc_shells.splitlines(keepends=True)
        )
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        with patch("builtins.open", mock_open):
            with patch.object(shutil, "which", side_effect=lambda s: s):
                shells = mgr._get_available_shells()
                assert "/bin/bash" in shells
                assert "/bin/zsh" in shells


# ---------------------------------------------------------------------------
# TerminalManager.spawn_pty
# ---------------------------------------------------------------------------


class TestSpawnPty:
    @pytest.mark.asyncio
    async def test_returns_false_when_ptyprocess_unavailable(self):
        mgr = TerminalManager()
        session = TerminalSession(websocket=AsyncMock())
        with patch("vibedeck.terminal.PTYPROCESS_AVAILABLE", False):
            result = await mgr.spawn_pty(session)
            assert result is False

    @pytest.mark.asyncio
    async def test_falls_back_to_home_when_cwd_missing(self):
        mgr = TerminalManager()
        ws = AsyncMock()
        session = TerminalSession(websocket=ws, cwd="/nonexistent/path")

        mock_proc = MagicMock()
        with patch("vibedeck.terminal.PTYPROCESS_AVAILABLE", True):
            with patch("vibedeck.terminal.ptyprocess") as mock_pty:
                mock_pty.PtyProcess.spawn.return_value = mock_proc
                result = await mgr.spawn_pty(session)
                assert result is True
                # Verify cwd was changed to home
                call_kwargs = mock_pty.PtyProcess.spawn.call_args
                assert call_kwargs.kwargs["cwd"] == str(Path.home())

    @pytest.mark.asyncio
    async def test_spawn_success(self, tmp_path):
        mgr = TerminalManager()
        ws = AsyncMock()
        session = TerminalSession(websocket=ws, cwd=str(tmp_path))

        mock_proc = MagicMock()
        with patch("vibedeck.terminal.PTYPROCESS_AVAILABLE", True):
            with patch("vibedeck.terminal.ptyprocess") as mock_pty:
                mock_pty.PtyProcess.spawn.return_value = mock_proc
                result = await mgr.spawn_pty(session)
                assert result is True
                assert session.process is mock_proc

    @pytest.mark.asyncio
    async def test_spawn_uses_non_login_shell(self, tmp_path):
        mgr = TerminalManager()
        ws = AsyncMock()
        session = TerminalSession(websocket=ws, cwd=str(tmp_path))

        mock_proc = MagicMock()
        with patch("vibedeck.terminal.PTYPROCESS_AVAILABLE", True):
            with patch.object(mgr, "_get_shell", return_value="/bin/bash"):
                with patch("vibedeck.terminal.ptyprocess") as mock_pty:
                    mock_pty.PtyProcess.spawn.return_value = mock_proc
                    result = await mgr.spawn_pty(session)
                    assert result is True
                    call_args = mock_pty.PtyProcess.spawn.call_args.args[0]
                    assert call_args == ["/bin/bash"]

    @pytest.mark.asyncio
    async def test_spawn_sanitizes_tmux_environment(self, tmp_path):
        mgr = TerminalManager()
        ws = AsyncMock()
        session = TerminalSession(websocket=ws, cwd=str(tmp_path))

        mock_proc = MagicMock()
        with patch("vibedeck.terminal.PTYPROCESS_AVAILABLE", True):
            with patch.object(mgr, "_get_shell", return_value="/bin/bash"):
                with patch.dict(
                    os.environ,
                    {
                        "TMUX": "/tmp/tmux.sock,123,0",
                        "TMUX_PANE": "%1",
                        "TERM_PROGRAM": "tmux",
                    },
                    clear=False,
                ):
                    with patch("vibedeck.terminal.ptyprocess") as mock_pty:
                        mock_pty.PtyProcess.spawn.return_value = mock_proc
                        result = await mgr.spawn_pty(session)
                        assert result is True
                        env = mock_pty.PtyProcess.spawn.call_args.kwargs["env"]
                        assert env["VIBEDECK_EMBEDDED_TERMINAL"] == "1"
                        assert env["TERM"] == "xterm-256color"
                        assert env["COLORTERM"] == "truecolor"
                        assert "TMUX" not in env
                        assert "TMUX_PANE" not in env
                        assert "TERM_PROGRAM" not in env

    @pytest.mark.asyncio
    async def test_spawn_exception(self, tmp_path):
        mgr = TerminalManager()
        ws = AsyncMock()
        session = TerminalSession(websocket=ws, cwd=str(tmp_path))

        with patch("vibedeck.terminal.PTYPROCESS_AVAILABLE", True):
            with patch("vibedeck.terminal.ptyprocess") as mock_pty:
                mock_pty.PtyProcess.spawn.side_effect = OSError("spawn failed")
                result = await mgr.spawn_pty(session)
                assert result is False


# ---------------------------------------------------------------------------
# TerminalManager._cleanup_session
# ---------------------------------------------------------------------------


class TestCleanupSession:
    @pytest.mark.asyncio
    async def test_cleanup_terminates_process(self):
        mgr = TerminalManager()
        mock_proc = MagicMock()
        mock_proc.isalive.return_value = True

        # Create a real async task that we can cancel
        async def noop():
            await asyncio.sleep(999)

        task = asyncio.create_task(noop())

        session = TerminalSession(
            websocket=AsyncMock(), process=mock_proc, read_task=task
        )
        ws_id = 42
        mgr.sessions[ws_id] = session

        await mgr._cleanup_session(ws_id)

        assert session.closing is True
        mock_proc.terminate.assert_called_once_with(force=True)
        assert ws_id not in mgr.sessions

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_session(self):
        mgr = TerminalManager()
        # Should not raise
        await mgr._cleanup_session(999)

    @pytest.mark.asyncio
    async def test_cleanup_dead_process(self):
        mgr = TerminalManager()
        mock_proc = MagicMock()
        mock_proc.isalive.return_value = False

        session = TerminalSession(websocket=AsyncMock(), process=mock_proc)
        ws_id = 42
        mgr.sessions[ws_id] = session

        await mgr._cleanup_session(ws_id)

        mock_proc.terminate.assert_not_called()
        assert ws_id not in mgr.sessions


# ---------------------------------------------------------------------------
# TerminalManager.get_shells
# ---------------------------------------------------------------------------


class TestGetShells:
    def test_returns_dict_with_shells_and_default(self):
        mgr = TerminalManager()
        result = mgr.get_shells()
        assert "shells" in result
        assert "default" in result
        assert isinstance(result["shells"], list)
        assert isinstance(result["default"], str)


# ---------------------------------------------------------------------------
# is_terminal_available
# ---------------------------------------------------------------------------


class TestIsTerminalAvailable:
    def test_reflects_ptyprocess_availability(self):
        with patch("vibedeck.terminal.PTYPROCESS_AVAILABLE", True):
            assert is_terminal_available() is True
        with patch("vibedeck.terminal.PTYPROCESS_AVAILABLE", False):
            assert is_terminal_available() is False


# ---------------------------------------------------------------------------
# Server API endpoints
# ---------------------------------------------------------------------------


class TestTerminalEndpoints:
    def test_terminal_enabled_endpoint(self):
        client = TestClient(app)
        server.set_terminal_enabled(True)
        resp = client.get("/api/terminal/enabled")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data

    def test_terminal_disabled_endpoint(self):
        client = TestClient(app)
        server.set_terminal_enabled(False)
        resp = client.get("/api/terminal/enabled")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_terminal_shells_endpoint(self):
        client = TestClient(app)
        server.set_terminal_enabled(True)
        resp = client.get("/api/terminal/shells")
        assert resp.status_code == 200
        data = resp.json()
        assert "shells" in data
        assert "default" in data

    def test_terminal_shells_disabled(self):
        client = TestClient(app)
        server.set_terminal_enabled(False)
        resp = client.get("/api/terminal/shells")
        assert resp.status_code == 403

    def test_supports_terminal_no_session(self):
        """Supports-terminal endpoint returns supported=False for nonexistent session."""
        client = TestClient(app)
        resp = client.get("/api/terminal/session/nonexistent-session/supports-terminal")
        assert resp.status_code == 200
        data = resp.json()
        assert data["supported"] is False
        assert "not found" in data["reason"].lower()

    def test_kill_session_terminal_no_active(self):
        """Kill endpoint returns killed=False when no terminal is active."""
        client = TestClient(app)
        resp = client.post("/api/terminal/session/nonexistent-session/kill")
        assert resp.status_code == 200
        assert resp.json()["killed"] is False

    def test_session_terminal_status_no_active(self):
        """Status endpoint returns active=False when no terminal is active."""
        client = TestClient(app)
        resp = client.get("/api/terminal/session/nonexistent-session/status")
        assert resp.status_code == 200
        assert resp.json()["active"] is False


# ---------------------------------------------------------------------------
# TerminalManager.kill_session_terminal / has_session_terminal
# ---------------------------------------------------------------------------


class TestSessionTerminal:
    def test_has_session_terminal_false_when_empty(self):
        mgr = TerminalManager()
        assert mgr.has_session_terminal("some-session") is False

    def test_has_session_terminal_true_when_active(self):
        mgr = TerminalManager()
        session = TerminalSession(
            websocket=AsyncMock(), session_id="ses-123"
        )
        mgr.sessions[42] = session
        assert mgr.has_session_terminal("ses-123") is True
        assert mgr.has_session_terminal("other") is False

    def test_has_session_terminal_false_when_closing(self):
        mgr = TerminalManager()
        session = TerminalSession(
            websocket=AsyncMock(), session_id="ses-123", closing=True
        )
        mgr.sessions[42] = session
        assert mgr.has_session_terminal("ses-123") is False

    @pytest.mark.asyncio
    async def test_kill_session_terminal(self):
        mgr = TerminalManager()
        mock_ws = AsyncMock()
        mock_proc = MagicMock()
        mock_proc.isalive.return_value = True

        session = TerminalSession(
            websocket=mock_ws, session_id="ses-456", process=mock_proc
        )
        ws_id = id(mock_ws)
        mgr.sessions[ws_id] = session

        killed = await mgr.kill_session_terminal("ses-456")
        assert killed is True
        assert ws_id not in mgr.sessions

    @pytest.mark.asyncio
    async def test_kill_session_terminal_no_match(self):
        mgr = TerminalManager()
        killed = await mgr.kill_session_terminal("no-such-session")
        assert killed is False

    @pytest.mark.asyncio
    async def test_spawn_with_custom_command(self, tmp_path):
        """spawn_pty uses explicit command when provided."""
        mgr = TerminalManager()
        session = TerminalSession(websocket=AsyncMock(), cwd=str(tmp_path))

        mock_proc = MagicMock()
        with patch("vibedeck.terminal.PTYPROCESS_AVAILABLE", True):
            with patch("vibedeck.terminal.ptyprocess") as mock_pty:
                mock_pty.PtyProcess.spawn.return_value = mock_proc
                result = await mgr.spawn_pty(
                    session, command=["claude", "--resume", "ses-789"]
                )
                assert result is True
                call_args = mock_pty.PtyProcess.spawn.call_args.args[0]
                assert call_args == ["claude", "--resume", "ses-789"]
