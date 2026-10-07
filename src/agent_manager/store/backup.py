"""`am backup`: a consistent copy of `am.db`, taken through SQLite's online-backup API.

The source is opened read-only (`store_db.open_reader`), so a backup holds no
write lock and live writers keep committing; the whole copy is one
`backup` step, read under one read transaction, so it is a snapshot that
includes what is committed but not yet checkpointed out of the `-wal`. The
copy is built in a temporary file beside the target and put in place with
`os.link`, which never overwrites. Never opened through `open_db`: a backup
is a page copy, so an unmigrated or newer-schema `am.db` is copied as it is.
"""

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent_manager import paths
from agent_manager.store import db as store_db

_SIDECARS = ("-wal", "-shm", "-journal")
"""What SQLite may leave beside the temporary copy."""


@dataclass(frozen=True)
class BackupResult:
    """The copy `backup` wrote: its absolute path and its size in bytes."""

    path: Path
    size_bytes: int


def backup(out: Path | None, *, now: datetime) -> BackupResult:
    """Copy `am.db` to `out`, or to `paths.default_backup_path(now)` when `out` is None.

    A relative `out` is taken from the current directory. Any failure
    propagates; in every case the temporary copy and its sidecars are removed.
    """
    source = paths.db_path()
    target = (out if out is not None else paths.default_backup_path(now)).absolute()
    handle, name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    os.close(handle)
    temporary = Path(name)
    try:
        _copy(source, temporary)
        os.link(temporary, target)
    finally:
        for leftover in (temporary, *_sidecars(temporary)):
            leftover.unlink(missing_ok=True)
    return BackupResult(path=target, size_bytes=target.stat().st_size)


def _copy(source: Path, destination: Path) -> None:
    """Copy `source` into the empty database file `destination` in one backup step."""
    reader = store_db.open_reader(source)
    try:
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()


def _sidecars(database: Path) -> list[Path]:
    return [database.with_name(database.name + suffix) for suffix in _SIDECARS]
