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
from typing import Literal

from agent_manager import paths
from agent_manager.store import db as store_db

BackupRefusal = Literal["no_database", "target_exists", "no_target_dir"]
"""Which check refused a backup."""

_SIDECARS = ("-wal", "-shm", "-journal")
"""What SQLite may leave beside the temporary copy."""


class BackupRefusedError(RuntimeError):
    """`backup` will not write the copy; nothing has been written.

    `reason` names the check that refused, `path` the file it is about: the
    `am.db` path for `no_database`, the target otherwise.
    """

    def __init__(self, reason: BackupRefusal, path: Path) -> None:
        super().__init__(
            f"am backup refused ({reason}): {path}; nothing has been written"
        )
        self.reason = reason
        self.path = path


@dataclass(frozen=True)
class BackupResult:
    """The copy `backup` wrote: its absolute path and its size in bytes."""

    path: Path
    size_bytes: int


def backup(out: Path | None, *, now: datetime) -> BackupResult:
    """Copy `am.db` to `out`, or to `paths.default_backup_path(now)` when `out` is None.

    Refused, in this order, as `BackupRefusedError`: `no_database` when
    `paths.db_path()` does not exist (nothing is created, not even the data
    directory); `target_exists` when the target exists, as anything, a
    dangling symlink included, or appears while the copy runs; `no_target_dir`
    when `out`'s parent is not a directory. A relative `out` is taken from
    the current directory. Any other failure propagates; in every case the
    temporary copy and its sidecars are removed.
    """
    source = paths.db_path()
    if not source.exists():
        raise BackupRefusedError("no_database", source)
    target = (out if out is not None else paths.default_backup_path(now)).absolute()
    if os.path.lexists(target):
        raise BackupRefusedError("target_exists", target)
    if not target.parent.is_dir():
        raise BackupRefusedError("no_target_dir", target)
    handle, name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    os.close(handle)
    temporary = Path(name)
    try:
        _copy(source, temporary)
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise BackupRefusedError("target_exists", target) from None
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
