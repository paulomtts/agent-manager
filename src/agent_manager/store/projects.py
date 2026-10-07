"""The `projects` rows: one per repository root, keyed by its resolved path.
Every function takes an open connection and never commits."""

import sqlite3
from datetime import datetime
from pathlib import Path

from agent_manager.store import db as store_db


def _repo_key(repo_dir: Path) -> str:
    """The `repo_dir` text of a project: its resolved path.

    `Path.resolve()` is non-strict, so a directory that does not exist is
    still normalised and stored; nothing here checks it exists.
    """
    return str(repo_dir.resolve())


def resolve(conn: sqlite3.Connection, repo_dir: Path, *, now: datetime) -> int:
    """The `projects.id` of `repo_dir`, creating the row on first sight.

    The insert is `ON CONFLICT(repo_dir) DO NOTHING` followed by a select, so
    a row another connection inserted first is adopted rather than refused,
    and an existing row keeps its `created_at`. Spellings that resolve to one
    directory (a `.` or `..` segment, a symlink) share one id. Does not
    commit: the caller's transaction covers the insert.
    """
    key = _repo_key(repo_dir)
    conn.execute(
        "INSERT INTO projects (repo_dir, created_at) VALUES (?, ?)"
        " ON CONFLICT(repo_dir) DO NOTHING",
        (key, store_db.iso(now)),
    )
    return conn.execute(
        "SELECT id FROM projects WHERE repo_dir = ?", (key,)
    ).fetchone()[0]


def lookup(conn: sqlite3.Connection, repo_dir: Path) -> int | None:
    """The `projects.id` of `repo_dir`, or `None` if it has no row. Read-only."""
    row = conn.execute(
        "SELECT id FROM projects WHERE repo_dir = ?", (_repo_key(repo_dir),)
    ).fetchone()
    return None if row is None else row[0]
