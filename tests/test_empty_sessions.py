"""Test that VibeDeck starts correctly when no sessions exist."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from vibedeck import main


class TestEmptySessionsStartup:
    """VibeDeck should not exit when no sessions exist yet."""

    def test_serve_does_not_exit_when_no_sessions_found(self):
        """When no sessions exist, serve should start the server, not exit(1)."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            # Don't create projects_dir — it doesn't exist yet

            # Mock the backend to return no sessions
            mock_backend = MagicMock()
            mock_backend.name = "claude-code"
            mock_backend.find_recent_sessions.return_value = []
            mock_backend.get_projects_dir.return_value = projects_dir

            with (
                patch("vibedeck.server.initialize_backend", return_value=mock_backend),
                patch("vibedeck.server.set_send_enabled"),
                patch("vibedeck.server.set_terminal_enabled"),
                patch("vibedeck.server.set_skip_permissions"),
                patch("vibedeck.server.set_fork_enabled"),
                patch("vibedeck.server.set_include_subagents"),
                patch("vibedeck.server.set_enable_thinking"),
                patch("vibedeck.server.set_thinking_budget"),
                patch("vibedeck.server.configure_summarization"),
                patch("uvicorn.run"),  # Don't actually start the server
            ):
                result = runner.invoke(main, ["serve", "--no-open", "--backend", "claude-code"])

            assert result.exit_code == 0, f"serve exited with code {result.exit_code}: {result.output}"
            assert "waiting for sessions to appear" in result.output.lower() or "found 0 session" in result.output.lower()

    def test_serve_creates_projects_dir_if_missing(self):
        """When projects_dir doesn't exist, serve should create it."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            assert not projects_dir.exists()

            mock_backend = MagicMock()
            mock_backend.name = "claude-code"
            mock_backend.find_recent_sessions.return_value = []
            mock_backend.get_projects_dir.return_value = projects_dir

            with (
                patch("vibedeck.server.initialize_backend", return_value=mock_backend),
                patch("vibedeck.server.set_send_enabled"),
                patch("vibedeck.server.set_terminal_enabled"),
                patch("vibedeck.server.set_skip_permissions"),
                patch("vibedeck.server.set_fork_enabled"),
                patch("vibedeck.server.set_include_subagents"),
                patch("vibedeck.server.set_enable_thinking"),
                patch("vibedeck.server.set_thinking_budget"),
                patch("vibedeck.server.configure_summarization"),
                patch("uvicorn.run"),
            ):
                result = runner.invoke(main, ["serve", "--no-open", "--backend", "claude-code"])

            assert result.exit_code == 0, f"serve exited with code {result.exit_code}: {result.output}"
            assert projects_dir.exists(), "projects_dir should be created"
