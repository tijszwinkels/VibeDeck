"""Tests for multi-backend changed-file routing."""

from pathlib import Path
from unittest.mock import MagicMock

from vibedeck.backends.multi import MultiBackend


class TestMultiBackendChangedFileRouting:
    """Tests for backend selection when multiple backends watch similar files."""

    def test_codex_rollout_prefers_codex_backend_over_claude_backend(self):
        """Codex rollout files should not be claimed by Claude Code in all mode."""
        codex_root = Path("/home/test/.codex/sessions")
        changed = codex_root / "2026/03/16/rollout-2026-03-16T21-49-48-019cf869-7a02-70b3-ba7e-cb16463f966f.jsonl"

        claude = MagicMock()
        claude.name = "Claude Code"
        claude.get_projects_dir.return_value = Path("/home/test/.claude/projects")
        claude.should_watch_file.return_value = True
        claude.get_session_id_from_changed_file.return_value = changed.stem

        codex = MagicMock()
        codex.name = "Codex"
        codex.get_projects_dir.return_value = codex_root
        codex.should_watch_file.return_value = True
        codex.get_session_id_from_changed_file.return_value = (
            "019cf869-7a02-70b3-ba7e-cb16463f966f"
        )

        backend = MultiBackend([claude, codex])

        assert backend.get_session_id_from_changed_file(changed) == (
            "019cf869-7a02-70b3-ba7e-cb16463f966f"
        )
        assert backend.get_backend_for_changed_file(changed) is codex

    def test_get_all_project_dirs_excludes_opencode_db_parent(self):
        claude = MagicMock()
        claude.name = "Claude Code"
        claude.get_projects_dir.return_value = Path("/home/test/.claude/projects")

        opencode = MagicMock()
        opencode.name = "OpenCode"
        opencode.get_projects_dir.return_value = Path("/home/test/.local/share/opencode/storage")
        opencode.get_db_path.return_value = Path("/home/test/.local/share/opencode/opencode.db")

        backend = MultiBackend([claude, opencode])

        assert backend.get_all_project_dirs() == [
            Path("/home/test/.claude/projects"),
            Path("/home/test/.local/share/opencode/storage"),
        ]
