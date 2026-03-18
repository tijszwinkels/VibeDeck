"""Codex CLI interaction helpers."""

from __future__ import annotations

import shutil

from ..protocol import CommandSpec


CLI_COMMAND = "codex"
CLI_INSTALL_INSTRUCTIONS = "Install Codex CLI from https://developers.openai.com/codex"


def is_cli_available() -> bool:
    """Check whether the Codex CLI is available."""
    return shutil.which(CLI_COMMAND) is not None


def ensure_session_indexed(session_id: str) -> None:
    """No-op for Codex.

    Codex sessions are resumed directly by session ID.
    """
    del session_id


def _add_common_flags(
    cmd: list[str],
    *,
    skip_permissions: bool = False,
    output_format: str | None = None,
    add_dirs: list[str] | None = None,
    cwd: str | None = None,
    model: str | None = None,
) -> list[str]:
    if output_format == "json":
        cmd.append("--json")
    if cwd:
        cmd.extend(["--cd", cwd])
    if add_dirs:
        for directory in add_dirs:
            cmd.extend(["--add-dir", directory])
    if model:
        cmd.extend(["--model", model])
    if skip_permissions:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    return cmd


def build_send_command(
    session_id: str,
    message: str,
    skip_permissions: bool = False,
    output_format: str | None = None,
    add_dirs: list[str] | None = None,
) -> CommandSpec:
    """Build a command for resuming an existing Codex session."""
    cmd = _add_common_flags(
        [CLI_COMMAND, "exec", "resume"],
        skip_permissions=skip_permissions,
        output_format=output_format,
        add_dirs=add_dirs,
    )
    cmd.extend([session_id, "-"])
    return CommandSpec(args=cmd, stdin=message)


def build_fork_command(
    session_id: str,
    message: str,
    skip_permissions: bool = False,
    output_format: str | None = None,
    add_dirs: list[str] | None = None,
) -> CommandSpec:
    """Build a command for forking a Codex session."""
    cmd = _add_common_flags(
        [CLI_COMMAND, "fork"],
        skip_permissions=skip_permissions,
        output_format=output_format,
        add_dirs=add_dirs,
    )
    cmd.extend([session_id, "-"])
    return CommandSpec(args=cmd, stdin=message)


def build_new_session_command(
    message: str,
    skip_permissions: bool = False,
    model: str | None = None,
    output_format: str | None = None,
    add_dirs: list[str] | None = None,
    cwd: str | None = None,
) -> CommandSpec:
    """Build a command for starting a new Codex session."""
    cmd = _add_common_flags(
        [CLI_COMMAND, "exec"],
        skip_permissions=skip_permissions,
        output_format=output_format,
        add_dirs=add_dirs,
        cwd=cwd,
        model=model,
    )
    cmd.append("-")
    return CommandSpec(args=cmd, stdin=message)
