"""Pytest wrapper for the date-category tooltip JavaScript tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_date_category_tooltip_node_suite():
    """Run the browser-helper aggregation tests through Node from pytest."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the date category tooltip tests")

    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "--test", "tests/date_category_tooltip.test.mjs"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
