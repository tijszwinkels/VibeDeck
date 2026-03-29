"""Pytest wrapper for terminal keyboard JavaScript tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_terminal_keyboard_node_suite():
    """Run terminal keyboard protocol tests through Node from pytest."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the terminal keyboard tests")

    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "--test", "tests/terminal_keyboard.test.mjs"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
