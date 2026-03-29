"""Test that VibeDeck starts correctly when no sessions exist."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from vibedeck import main, _config


@pytest.fixture(autouse=True)
def reset_summary_defaults(monkeypatch):
    """Keep CLI summary defaults deterministic for these startup tests."""
    monkeypatch.setattr(_config.serve, "summarize_after_idle_for", 180)
    monkeypatch.setattr(_config.serve, "summary_after_long_running", 120)


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

    def test_serve_disable_auto_summarization_flag_disables_all_auto_triggers(self):
        """CLI flag should keep summarizer available but disable auto triggers."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"

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
                patch("vibedeck.server.configure_summarization") as configure_summarization,
                patch("uvicorn.run"),
            ):
                result = runner.invoke(
                    main,
                    [
                        "serve",
                        "--no-open",
                        "--backend",
                        "claude-code",
                        "--disable-auto-summarization",
                    ],
                )

            assert result.exit_code == 0, f"serve exited with code {result.exit_code}: {result.output}"
            configure_summarization.assert_called_once()
            kwargs = configure_summarization.call_args.kwargs
            assert kwargs["summarize_new_sessions"] is False
            assert kwargs["summarize_after_idle_for"] is None
            assert kwargs["summary_after_long_running"] is None

    def test_serve_disable_auto_summarization_from_config_disables_all_auto_triggers(self):
        """Config should disable all automatic summary triggers."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            projects_dir = tmpdir_path / "projects"
            config_file = tmpdir_path / "config.toml"
            config_file.write_text("""
[serve]
disable_auto_summarization = true
""")

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
                patch("vibedeck.server.configure_summarization") as configure_summarization,
                patch("uvicorn.run"),
            ):
                result = runner.invoke(
                    main,
                    [
                        "serve",
                        "--no-open",
                        "--backend",
                        "claude-code",
                        "--config",
                        str(config_file),
                    ],
                )

            assert result.exit_code == 0, f"serve exited with code {result.exit_code}: {result.output}"
            configure_summarization.assert_called_once()
            kwargs = configure_summarization.call_args.kwargs
            assert kwargs["summarize_new_sessions"] is False
            assert kwargs["summarize_after_idle_for"] is None
            assert kwargs["summary_after_long_running"] is None

    def test_serve_no_summarize_new_sessions_flag_only_disables_new_session_trigger(self):
        """CLI flag should disable only new-session summarization."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"

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
                patch("vibedeck.server.configure_summarization") as configure_summarization,
                patch("uvicorn.run"),
            ):
                result = runner.invoke(
                    main,
                    [
                        "serve",
                        "--no-open",
                        "--backend",
                        "claude-code",
                        "--no-summarize-new-sessions",
                    ],
                )

            assert result.exit_code == 0, f"serve exited with code {result.exit_code}: {result.output}"
            configure_summarization.assert_called_once()
            kwargs = configure_summarization.call_args.kwargs
            assert kwargs["summarize_new_sessions"] is False
            assert kwargs["summarize_after_idle_for"] == 180
            assert kwargs["summary_after_long_running"] == 120

    def test_serve_disable_idle_summarization_flag_only_disables_idle_trigger(self):
        """CLI flag should disable only idle summarization."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"

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
                patch("vibedeck.server.configure_summarization") as configure_summarization,
                patch("uvicorn.run"),
            ):
                result = runner.invoke(
                    main,
                    [
                        "serve",
                        "--no-open",
                        "--backend",
                        "claude-code",
                        "--disable-idle-summarization",
                    ],
                )

            assert result.exit_code == 0, f"serve exited with code {result.exit_code}: {result.output}"
            configure_summarization.assert_called_once()
            kwargs = configure_summarization.call_args.kwargs
            assert kwargs["summarize_new_sessions"] is True
            assert kwargs["summarize_after_idle_for"] is None
            assert kwargs["summary_after_long_running"] == 120

    def test_serve_disable_long_running_summarization_flag_only_disables_long_running_trigger(self):
        """CLI flag should disable only long-running summarization."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"

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
                patch("vibedeck.server.configure_summarization") as configure_summarization,
                patch("uvicorn.run"),
            ):
                result = runner.invoke(
                    main,
                    [
                        "serve",
                        "--no-open",
                        "--backend",
                        "claude-code",
                        "--disable-long-running-summarization",
                    ],
                )

            assert result.exit_code == 0, f"serve exited with code {result.exit_code}: {result.output}"
            configure_summarization.assert_called_once()
            kwargs = configure_summarization.call_args.kwargs
            assert kwargs["summarize_new_sessions"] is True
            assert kwargs["summarize_after_idle_for"] == 180
            assert kwargs["summary_after_long_running"] is None
