"""Filesystem locations for agent-manager.

Every path this tool writes to is derived from :func:`data_dir`. Nothing here
takes a repository worktree as a write base: that is what keeps run artifacts
out of the worktree, where they would break the verify step's clean-tree check
or be swept into a commit.
"""

import hashlib
import os
from pathlib import Path


def data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path(os.environ["HOME"]) / ".local" / "share"
    result = base / "agent-manager"
    result.mkdir(parents=True, exist_ok=True)
    return result


def project_db_path(root: Path) -> Path:
    projects_dir = data_dir() / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()
    return projects_dir / f"{digest}.db"


def run_dir(run_id: str) -> Path:
    """Root of one run's artifact tree, always outside any repository worktree."""
    result = data_dir() / "runs" / run_id
    result.mkdir(parents=True, exist_ok=True)
    return result


def attempt_dir(run_id: str, card: str, phase: str, attempt: int) -> Path:
    """Directory holding one attempt's prompt.txt, result.json and stdout.log."""
    result = run_dir(run_id) / card / f"{phase}.{attempt}"
    result.mkdir(parents=True, exist_ok=True)
    return result
