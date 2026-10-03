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


def _project_digest(root: Path) -> str:
    """The per-project file stem: sha256 of the resolved root path."""
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()


def _projects_dir() -> Path:
    result = data_dir() / "projects"
    result.mkdir(parents=True, exist_ok=True)
    return result


def project_db_path(root: Path) -> Path:
    return _projects_dir() / f"{_project_digest(root)}.db"


def project_lock_path(root: Path, name: str) -> Path:
    """The file a process-wide lock named `name` flocks for the project at `root`.

    Beside the project's database under the data directory, never inside the
    repository. Creates the `projects` directory; the lock file itself is created
    by whoever first opens it (`locks.ProcessLock`).
    """
    return _projects_dir() / f"{_project_digest(root)}.{name}.lock"


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


def highest_attempt(run_id: str, card: str, phase: str) -> int:
    """The highest attempt number `phase` of `card` has a directory for, or 0.

    Scans `{phase}.1`, `{phase}.2`, ... on disk and stops at the first absent
    one, so a resumed run sees the attempts a previous process made. Creates
    no card or attempt directory; `run_dir` still creates the run's own root.
    """
    card_dir = run_dir(run_id) / card
    attempt = 0
    while (card_dir / f"{phase}.{attempt + 1}").exists():
        attempt += 1
    return attempt


def list_run_ids() -> list[str]:
    """Every run id that has a directory under `data_dir()/runs`, sorted.

    Lists only. Unlike `run_dir`, it creates neither the `runs` directory nor
    any run directory, so a reader that walks every run (`am watch --all`)
    leaves the data directory as it found it. A missing `runs` directory is an
    empty list, not an error. Plain files beside the run directories are not
    runs and are skipped.
    """
    runs = data_dir() / "runs"
    if not runs.is_dir():
        return []
    return sorted(entry.name for entry in runs.iterdir() if entry.is_dir())
