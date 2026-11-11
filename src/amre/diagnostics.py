"""Local workspace and tool checks for doctor and status."""

import shutil
import subprocess
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from amre.config import Settings


@dataclass(frozen=True)
class WorkspaceStatus:
    root: Path
    is_directory: bool
    git: str | None
    in_worktree: bool


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str
    required: bool = True


def inspect_workspace(root: Path) -> WorkspaceStatus:
    try:
        is_directory = root.is_dir()
    except OSError:
        is_directory = False
    git = shutil.which("git")
    in_worktree = False
    if is_directory and git:
        try:
            result = subprocess.run(
                [git, "rev-parse", "--is-inside-work-tree"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        else:
            in_worktree = result.returncode == 0 and result.stdout.strip() == b"true"
    return WorkspaceStatus(root, is_directory, git, in_worktree)


def _sqlite_check() -> Check:
    try:
        import sqlite3
    except ImportError:
        return Check("SQLite", False, "Python's sqlite3 module is unavailable")

    try:
        with closing(sqlite3.connect(":memory:")) as connection:
            (version,) = connection.execute("SELECT sqlite_version()").fetchone()
    except sqlite3.Error:
        return Check("SQLite", False, "In-memory query failed")
    return Check("SQLite", True, version)


def run_checks(settings: Settings) -> tuple[Check, ...]:
    workspace = inspect_workspace(settings.workspace_root)
    codex = shutil.which(settings.codex_executable)
    return (
        Check("Python", sys.version_info >= (3, 11), sys.version.split()[0]),
        _sqlite_check(),
        Check("Workspace", workspace.is_directory, str(workspace.root)),
        Check("Git", workspace.git is not None, workspace.git or "Executable unavailable"),
        Check(
            "Git worktree",
            workspace.in_worktree,
            "Detected" if workspace.in_worktree else "Could not confirm a Git worktree",
        ),
        Check("Codex CLI", codex is not None, codex or "Executable unavailable", required=False),
    )
