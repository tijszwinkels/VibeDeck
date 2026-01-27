"""Git diff API routes for diff view functionality."""

import logging
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ..sessions import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diff")

# Maximum file size to read for untracked files (1MB)
MAX_FILE_SIZE = 1024 * 1024

# Binary file extensions to skip
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".pyc", ".pyo", ".class", ".o", ".a",
}


def _run_git_command(
    cwd: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a git command in the specified directory."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, ["git", *args], result.stdout, result.stderr
            )
        return result
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Git command timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Git not found")


def _get_main_branch(cwd: Path) -> str | None:
    """Get the name of the main/master branch."""
    # Check for common main branch names
    for branch in ["main", "master"]:
        result = _run_git_command(
            cwd, "rev-parse", "--verify", f"refs/heads/{branch}", check=False
        )
        if result.returncode == 0:
            return branch
    return None


def _get_changed_files_uncommitted(cwd: Path) -> list[dict[str, Any]]:
    """Get list of uncommitted changes (staged + unstaged + untracked)."""
    files = []

    # Get staged changes
    result = _run_git_command(
        cwd, "diff", "--cached", "--numstat", "--no-renames", check=False
    )
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                added, deleted, path = parts[0], parts[1], parts[2]
                files.append(
                    {
                        "path": path,
                        "additions": int(added) if added != "-" else 0,
                        "deletions": int(deleted) if deleted != "-" else 0,
                        "status": "staged",
                    }
                )

    # Get unstaged changes (modified tracked files)
    result = _run_git_command(cwd, "diff", "--numstat", "--no-renames", check=False)
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                added, deleted, path = parts[0], parts[1], parts[2]
                # Check if already in staged list
                existing = next((f for f in files if f["path"] == path), None)
                if existing:
                    existing["status"] = "modified"
                    existing["additions"] += int(added) if added != "-" else 0
                    existing["deletions"] += int(deleted) if deleted != "-" else 0
                else:
                    files.append(
                        {
                            "path": path,
                            "additions": int(added) if added != "-" else 0,
                            "deletions": int(deleted) if deleted != "-" else 0,
                            "status": "modified",
                        }
                    )

    # Get untracked files (use -- to prevent path injection)
    result = _run_git_command(
        cwd, "ls-files", "--others", "--exclude-standard", "--", check=False
    )
    if result.returncode == 0 and result.stdout.strip():
        for path in result.stdout.strip().split("\n"):
            if not path:
                continue
            file_path = cwd / path

            # Skip binary files
            if file_path.suffix.lower() in BINARY_EXTENSIONS:
                files.append(
                    {
                        "path": path,
                        "additions": 0,
                        "deletions": 0,
                        "status": "untracked",
                        "binary": True,
                    }
                )
                continue

            # Count lines in untracked file (with size limit)
            if file_path.is_file():
                try:
                    file_size = file_path.stat().st_size
                    if file_size > MAX_FILE_SIZE:
                        # Large file - estimate lines
                        lines = file_size // 50  # rough estimate
                    else:
                        with open(
                            file_path, "r", encoding="utf-8", errors="replace"
                        ) as f:
                            lines = len(f.readlines())
                except Exception:
                    lines = 0
                files.append(
                    {
                        "path": path,
                        "additions": lines,
                        "deletions": 0,
                        "status": "untracked",
                    }
                )

    return files


def _get_changed_files_vs_main(cwd: Path, main_branch: str) -> list[dict[str, Any]]:
    """Get list of changes between current HEAD and main branch."""
    files = []

    # Get diff between main and HEAD
    result = _run_git_command(
        cwd, "diff", f"{main_branch}...HEAD", "--numstat", "--no-renames", check=False
    )
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                added, deleted, path = parts[0], parts[1], parts[2]
                files.append(
                    {
                        "path": path,
                        "additions": int(added) if added != "-" else 0,
                        "deletions": int(deleted) if deleted != "-" else 0,
                        "status": "committed",
                    }
                )

    return files


def _get_git_root(path: Path) -> Path | None:
    """Get the git repository root from any path within the repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path if path.is_dir() else path.parent,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def _resolve_cwd(cwd_param: str | None, info) -> Path:
    """Resolve the working directory from parameter or session info.

    Always returns the git root directory, not a subdirectory.
    """
    if cwd_param:
        target = Path(cwd_param)
        # If target is a file, use its parent directory
        if target.is_file():
            target = target.parent
        # Security: ensure within home directory
        try:
            target.resolve().relative_to(Path.home())
        except ValueError:
            raise HTTPException(status_code=403, detail="Path must be within home directory")
        # Find the git root from this path
        git_root = _get_git_root(target)
        if git_root:
            # Security: also validate git root is within home directory
            try:
                git_root.resolve().relative_to(Path.home())
            except ValueError:
                raise HTTPException(
                    status_code=403, detail="Git root must be within home directory"
                )
            return git_root
        return target
    elif info.project_path:
        return Path(info.project_path)
    else:
        raise HTTPException(status_code=400, detail="No project path for session")


@router.get("/session/{session_id}/files")
async def get_diff_files(session_id: str, cwd: str | None = None) -> dict:
    """Get list of changed files for a session's project.

    Returns both uncommitted changes AND branch changes vs main when available.
    The primary diff_type indicates which has priority for the main file list,
    but both are always returned when present.

    Args:
        session_id: The session ID
        cwd: Optional working directory override (e.g., for worktrees).
             If provided, uses this directory instead of the session's project path.

    Returns:
        dict with:
            - files: List of changed files with path, additions, deletions, status
            - diff_type: "uncommitted", "vs_main", or "no_git" (primary type)
            - main_branch: Name of main branch
            - current_branch: Current branch name
            - cwd: The working directory used
            - uncommitted_files: List of uncommitted changes (always present)
            - branch_files: List of branch changes vs main (always present)
    """
    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    work_dir = _resolve_cwd(cwd, info)

    if not work_dir.exists():
        raise HTTPException(status_code=404, detail="Project path does not exist")

    # Check if it's a git repository
    result = _run_git_command(work_dir, "rev-parse", "--is-inside-work-tree", check=False)
    if result.returncode != 0:
        # Not a git repository - return a response that lets the frontend
        # show the file contents instead of an error
        return {
            "files": [],
            "diff_type": "no_git",
            "main_branch": None,
            "current_branch": None,
            "cwd": str(work_dir),
            "requested_file": cwd,  # Original file path that was clicked
            "uncommitted_files": [],
            "branch_files": [],
        }

    # Get current branch
    branch_result = _run_git_command(
        work_dir, "rev-parse", "--abbrev-ref", "HEAD", check=False
    )
    current_branch = (
        branch_result.stdout.strip() if branch_result.returncode == 0 else None
    )

    # Get uncommitted changes
    uncommitted = _get_changed_files_uncommitted(work_dir)

    # Get main branch and branch changes
    main_branch = _get_main_branch(work_dir)
    branch_files = []

    if main_branch and current_branch and current_branch != main_branch:
        branch_files = _get_changed_files_vs_main(work_dir, main_branch)

    # Determine primary diff type (for backward compatibility with "files" field)
    if uncommitted:
        diff_type = "uncommitted"
        files = uncommitted
    elif branch_files:
        diff_type = "vs_main"
        files = branch_files
    else:
        diff_type = "vs_main"
        files = []

    return {
        "files": files,
        "diff_type": diff_type,
        "main_branch": main_branch,
        "current_branch": current_branch,
        "cwd": str(work_dir),
        "uncommitted_files": uncommitted,
        "branch_files": branch_files,
    }


@router.get("/session/{session_id}/file")
async def get_file_diff(session_id: str, path: str, cwd: str | None = None) -> dict:
    """Get the diff content for a specific file.

    Args:
        session_id: The session ID
        path: Relative path to the file within the project
        cwd: Optional working directory override (e.g., for worktrees)

    Returns:
        dict with:
            - diff: Unified diff string
            - file_path: Full path to the file
            - status: File status (staged, modified, untracked, committed)
    """
    info = get_session(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    work_dir = _resolve_cwd(cwd, info)

    if not work_dir.exists():
        raise HTTPException(status_code=404, detail="Project path does not exist")

    full_path = work_dir / path

    # Check if it's a git repository
    result = _run_git_command(work_dir, "rev-parse", "--is-inside-work-tree", check=False)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail="Not a git repository")

    # Determine file status and get appropriate diff
    diff_content = ""
    status = "unknown"

    # Check if file is untracked
    untracked_result = _run_git_command(
        work_dir, "ls-files", "--others", "--exclude-standard", path, check=False
    )
    if untracked_result.returncode == 0 and untracked_result.stdout.strip():
        status = "untracked"
        # For untracked files, show the entire file content as additions
        if full_path.is_file():
            try:
                with open(
                    full_path, "r", encoding="utf-8", errors="replace"
                ) as f:
                    content = f.read()
                lines = content.split("\n")
                # Create a pseudo-diff showing all lines as additions
                diff_lines = [
                    f"diff --git a/{path} b/{path}",
                    "new file mode 100644",
                    "--- /dev/null",
                    f"+++ b/{path}",
                    f"@@ -0,0 +1,{len(lines)} @@",
                ]
                for line in lines:
                    diff_lines.append(f"+{line}")
                diff_content = "\n".join(diff_lines)
            except Exception as e:
                logger.error(f"Error reading untracked file {path}: {e}")
                diff_content = f"Error reading file: {e}"
        return {
            "diff": diff_content,
            "file_path": str(full_path),
            "status": status,
        }

    # Check for staged changes
    staged_result = _run_git_command(work_dir, "diff", "--cached", "--", path, check=False)
    staged_diff = staged_result.stdout.strip() if staged_result.returncode == 0 else ""

    # Check for unstaged changes
    unstaged_result = _run_git_command(work_dir, "diff", "--", path, check=False)
    unstaged_diff = (
        unstaged_result.stdout.strip() if unstaged_result.returncode == 0 else ""
    )

    if staged_diff and unstaged_diff:
        # Both staged and unstaged changes - combine them
        status = "modified"
        diff_content = f"=== Staged changes ===\n{staged_diff}\n\n=== Unstaged changes ===\n{unstaged_diff}"
    elif staged_diff:
        status = "staged"
        diff_content = staged_diff
    elif unstaged_diff:
        status = "modified"
        diff_content = unstaged_diff
    else:
        # No uncommitted changes - check diff vs main
        main_branch = _get_main_branch(work_dir)
        if main_branch:
            branch_result = _run_git_command(
                work_dir, "rev-parse", "--abbrev-ref", "HEAD", check=False
            )
            current_branch = (
                branch_result.stdout.strip()
                if branch_result.returncode == 0
                else None
            )

            if current_branch and current_branch != main_branch:
                vs_main_result = _run_git_command(
                    work_dir, "diff", f"{main_branch}...HEAD", "--", path, check=False
                )
                if vs_main_result.returncode == 0 and vs_main_result.stdout.strip():
                    status = "committed"
                    diff_content = vs_main_result.stdout.strip()

    if not diff_content:
        return {
            "diff": "",
            "file_path": str(full_path),
            "status": "unchanged",
        }

    return {
        "diff": diff_content,
        "file_path": str(full_path),
        "status": status,
    }
