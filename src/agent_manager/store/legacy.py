"""Per-project databases from an older `am`: each read through a private copy
that leaves the file and its sidecars untouched, and all of them merged into
`am.db` in one write transaction."""

import shutil
import sqlite3
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent_manager.store import db as store_db
from agent_manager.store import leases as store_leases
from agent_manager.store import projects as store_projects

_NOT_COPIED = frozenset({"projects", "meta", "events"})
"""`am.db` tables no legacy row is copied into: `merge` writes the project row
and the marker itself, and no legacy row becomes an event."""


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


@dataclass(frozen=True)
class MergedFile:
    """One legacy file as merged: the `projects` row it went under (`repo_dir`
    as stored), the rows copied into each copied table (every one present, 0
    included), and its tables nothing was copied from, sorted."""

    path: Path
    repo_dir: str
    project_id: int
    rows: dict[str, int]
    ignored_tables: tuple[str, ...]


@dataclass(frozen=True)
class MergeOutcome:
    """What `merge` did. `migrated_at` is the marker's value. With
    `already_migrated`, the marker was already there, so nothing was written
    and `merged` is empty."""

    migrated_at: str
    already_migrated: bool
    merged: tuple[MergedFile, ...]


class LegacyRunClashError(RuntimeError):
    """`run_id`, a run of the legacy file at `path`, is already a run of `am.db`."""

    def __init__(self, run_id: str, path: Path) -> None:
        super().__init__(f"run id {run_id} from {path} is already in am.db")
        self.run_id = run_id
        self.path = path


class LegacyRowClashError(RuntimeError):
    """`am.db` refused a `table` row of the legacy file at `path` with
    `sqlite3.IntegrityError`: a key clash, a `NOT NULL` or a `CHECK`."""

    def __init__(self, table: str, path: Path, cause: str) -> None:
        super().__init__(f"a {table} row from {path} was refused: {cause}")
        self.table = table
        self.path = path


def _copied_tables(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    """`conn`'s tables that legacy rows are copied into, in name order, with their columns."""
    return {
        name: tuple(row[1] for row in conn.execute(f"PRAGMA table_info({_quoted(name)})"))
        for name in _table_names(conn)
        if name not in _NOT_COPIED
    }


def _copy(
    conn: sqlite3.Connection,
    legacy: LegacyFile,
    table: str,
    columns: tuple[str, ...],
    project_id: int,
) -> int:
    """Insert every `table` row of `legacy` under `project_id`; the count inserted.

    Columns are matched by name: legacy columns `table` lacks (and a legacy
    `project_id`) are dropped, and `table` columns the legacy table lacks take
    their default.
    """
    source = legacy.tables.get(table)
    if source is None or not source.rows:
        return 0
    shared = [c for c in columns if c != "project_id" and c in source.columns]
    positions = [source.columns.index(c) for c in shared]
    names = ", ".join(_quoted(c) for c in ("project_id", *shared))
    marks = ", ".join("?" for _ in range(len(shared) + 1))
    try:
        conn.executemany(
            f"INSERT INTO {_quoted(table)} ({names}) VALUES ({marks})",
            [(project_id, *(row[i] for i in positions)) for row in source.rows],
        )
    except sqlite3.IntegrityError as error:
        raise LegacyRowClashError(table, legacy.path, str(error)) from error
    return len(source.rows)


def merge(
    conn: sqlite3.Connection, files: Sequence[LegacyFile], *, now: datetime
) -> MergeOutcome:
    """Merge `files` into `am.db` through `conn` in one `immediate` transaction.

    Each file must have exactly one `repo_dirs` value. Inside the transaction:
    a `MIGRATED_KEY` row already in `meta` returns it as `already_migrated`
    with nothing written; a run id of a file already in `runs` raises
    `LegacyRunClashError`; then each file, in order, gets its project through
    `store_projects.resolve` (an existing row is adopted) and its rows copied
    into every `am.db` table but `projects`, `meta`, `events` and `sqlite_*`,
    values verbatim; an `IntegrityError` there raises `LegacyRowClashError`;
    last, `MIGRATED_KEY` is written as `store_db.iso(now)` and everything is
    committed. Any raise rolls the whole transaction back.
    """
    with store_db.immediate(conn):
        found = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (store_db.MIGRATED_KEY,)
        ).fetchone()
        if found is not None:
            return MergeOutcome(migrated_at=found[0], already_migrated=True, merged=())
        for legacy in files:
            for run_id in legacy.run_ids:
                if conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
                    raise LegacyRunClashError(run_id, legacy.path)
        targets = _copied_tables(conn)
        merged: list[MergedFile] = []
        for legacy in files:
            (repo_dir,) = legacy.repo_dirs
            project_id = store_projects.resolve(conn, Path(repo_dir), now=now)
            key = conn.execute(
                "SELECT repo_dir FROM projects WHERE id = ?", (project_id,)
            ).fetchone()[0]
            rows = {
                table: _copy(conn, legacy, table, columns, project_id)
                for table, columns in targets.items()
            }
            merged.append(
                MergedFile(
                    path=legacy.path,
                    repo_dir=key,
                    project_id=project_id,
                    rows=rows,
                    ignored_tables=tuple(sorted(set(legacy.tables) - set(targets))),
                )
            )
        migrated_at = store_db.iso(now)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            (store_db.MIGRATED_KEY, migrated_at),
        )
    return MergeOutcome(migrated_at=migrated_at, already_migrated=False, merged=tuple(merged))
