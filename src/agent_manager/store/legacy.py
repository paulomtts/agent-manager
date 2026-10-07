"""Per-project databases from an older `am`: each read through a private copy
that leaves the file and its sidecars untouched."""

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agent_manager.store import leases as store_leases


@dataclass(frozen=True)
class LegacyTable:
    """One table of a legacy file: its column names and every row, in the
    file's column order."""

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class LegacyFile:
    """Everything read from one legacy file at `path`.

    `tables` holds every table but SQLite's own (`sqlite_*`), keyed by name.
    `repo_dirs` is the distinct `runs.repo_dir` values, sorted, and `run_ids`
    every `runs.id`, sorted; both are empty without a `runs` table. `leases`
    is every `run_leases` row, empty without that table.
    """

    path: Path
    tables: dict[str, LegacyTable]
    repo_dirs: tuple[str, ...]
    run_ids: tuple[str, ...]
    leases: tuple[store_leases.LeaseRow, ...]


class LegacyUnreadableError(RuntimeError):
    """SQLite cannot read the legacy file at `path` (not a database, or corrupt)."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"{path} is not a readable SQLite database")
        self.path = path


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sidecar(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def _table_names(conn: sqlite3.Connection) -> list[str]:
    """`conn`'s tables but SQLite's own, in name order."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _read(conn: sqlite3.Connection, path: Path) -> LegacyFile:
    tables: dict[str, LegacyTable] = {}
    for name in _table_names(conn):
        cursor = conn.execute(f"SELECT * FROM {_quoted(name)}")
        tables[name] = LegacyTable(
            columns=tuple(column[0] for column in cursor.description),
            rows=tuple(tuple(row) for row in cursor.fetchall()),
        )
    repo_dirs: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    if "runs" in tables:
        repo_dirs = tuple(
            row[0]
            for row in conn.execute("SELECT DISTINCT repo_dir FROM runs ORDER BY repo_dir")
        )
        run_ids = tuple(row[0] for row in conn.execute("SELECT id FROM runs ORDER BY id"))
    leases = tuple(store_leases.read_leases(conn)) if "run_leases" in tables else ()
    return LegacyFile(
        path=path, tables=tables, repo_dirs=repo_dirs, run_ids=run_ids, leases=leases
    )


def read_legacy(path: Path) -> LegacyFile:
    """Read the legacy database `path` without opening or changing it.

    `path`, and its `-wal` when there is one, are byte-copied into a fresh
    `tempfile.mkdtemp` directory, and only the copy is opened. So rows
    committed only in the `-wal` are read, `path`, its `-wal` and its `-shm`
    keep their bytes, and no sidecar is created beside them. The directory is
    removed before returning or raising. A 0-byte file reads as one with no
    tables. A file SQLite cannot read raises `LegacyUnreadableError`.
    """
    scratch = Path(tempfile.mkdtemp(prefix="am-migrate-"))
    try:
        copy = scratch / path.name
        shutil.copyfile(path, copy)
        if _sidecar(path, "-wal").exists():
            shutil.copyfile(_sidecar(path, "-wal"), _sidecar(copy, "-wal"))
        try:
            conn = sqlite3.connect(copy)
            try:
                conn.row_factory = sqlite3.Row
                return _read(conn, path)
            finally:
                conn.close()
        except sqlite3.DatabaseError as error:
            raise LegacyUnreadableError(path) from error
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
