"""Merging every per-project database an older `am` left into the
machine-wide `am.db`: once, in one transaction, and idempotent."""

import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent_manager import control, paths
from agent_manager.store import db as store_db
from agent_manager.store import legacy as store_legacy


@dataclass(frozen=True)
class MigratedProject:
    """One legacy file as merged: `repo_dir` is the resolved key stored in
    `projects`, `project_id` that row's id in `am.db`, `rows` the rows
    copied per table (every copied table, 0 included), `ignored_tables` the
    file's tables nothing was copied from, sorted."""

    path: Path
    repo_dir: str
    project_id: int
    rows: dict[str, int]
    ignored_tables: tuple[str, ...]


@dataclass(frozen=True)
class MigrationReport:
    """What `migrate` did.

    `already_migrated`: the marker was already there and nothing was done.
    `migrated_at`: the marker's value, new or existing; `None` when there was
    nothing to migrate. `projects`: one per merged file, in
    `paths.legacy_project_dbs` order. `skipped`: the files with no `runs`
    rows, in that order.
    """

    already_migrated: bool
    migrated_at: str | None
    projects: tuple[MigratedProject, ...]
    skipped: tuple[Path, ...]


def migrate(
    *,
    now: datetime,
    host: str = socket.gethostname(),
    alive: Callable[[int], bool] = control.pid_alive,
) -> MigrationReport:
    """Merge every `paths.legacy_project_dbs()` file into `paths.db_path()`.

    A marker already in `am.db` (read read-only) returns it as
    `already_migrated` without opening any legacy file. No legacy file
    returns `migrated_at=None` and creates nothing. Otherwise every file is
    read through `store_legacy.read_legacy`; files with no runs are skipped;
    the rest are merged by `store_legacy.merge` in one transaction that also
    writes the marker, even when every file was skipped. `host` and `alive`
    decide lease liveness through `control.lease_is_live`.
    """
    location = paths.db_path()
    marker = store_db.migration_marker(location)
    if marker is not None:
        return MigrationReport(
            already_migrated=True, migrated_at=marker, projects=(), skipped=()
        )
    legacy = paths.legacy_project_dbs()
    if not legacy:
        return MigrationReport(
            already_migrated=False, migrated_at=None, projects=(), skipped=()
        )
    files = [store_legacy.read_legacy(path) for path in legacy]
    merging = [legacy_file for legacy_file in files if legacy_file.run_ids]
    skipped = tuple(legacy_file.path for legacy_file in files if not legacy_file.run_ids)
    conn = store_db.open_db_for_migration()
    try:
        outcome = store_legacy.merge(conn, merging, now=now)
    finally:
        conn.close()
    if outcome.already_migrated:
        return MigrationReport(
            already_migrated=True, migrated_at=outcome.migrated_at, projects=(), skipped=()
        )
    return MigrationReport(
        already_migrated=False,
        migrated_at=outcome.migrated_at,
        projects=tuple(
            MigratedProject(
                path=merged.path,
                repo_dir=merged.repo_dir,
                project_id=merged.project_id,
                rows=merged.rows,
                ignored_tables=merged.ignored_tables,
            )
            for merged in outcome.merged
        ),
        skipped=skipped,
    )
