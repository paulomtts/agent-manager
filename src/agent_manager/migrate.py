"""Merging every per-project database an older `am` left into the
machine-wide `am.db`: once, in one transaction, idempotent, and a typed
refusal that commits nothing."""

import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from agent_manager import control, paths
from agent_manager.store import db as store_db
from agent_manager.store import legacy as store_legacy

RefusalReason = Literal[
    "unreadable",
    "live_run",
    "repo_dir_disagrees",
    "digest_mismatch",
    "duplicate_run_id",
    "row_clash",
]
"""Which check refused a migration."""


class MigrationRefusedError(RuntimeError):
    """`migrate` cannot merge safely; nothing has been committed.

    `reason` names the check that refused, `paths` the legacy files involved,
    and `run_ids` the run ids involved (empty when none is).
    """

    def __init__(
        self,
        reason: RefusalReason,
        detail: str,
        *,
        files: Sequence[Path],
        run_ids: Sequence[str] = (),
    ) -> None:
        super().__init__(
            f"am migrate refused ({reason}): {detail}; nothing has been migrated"
        )
        self.reason = reason
        self.paths = tuple(files)
        self.run_ids = tuple(run_ids)


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


def _read(path: Path) -> store_legacy.LegacyFile:
    try:
        return store_legacy.read_legacy(path)
    except store_legacy.LegacyUnreadableError as error:
        raise MigrationRefusedError("unreadable", str(error), files=(path,)) from error


def _refuse_live(
    files: Sequence[store_legacy.LegacyFile],
    *,
    now: datetime,
    host: str,
    alive: Callable[[int], bool],
) -> None:
    live = [
        (legacy.path, lease.run_id)
        for legacy in files
        for lease in legacy.leases
        if control.lease_is_live(lease, now=now, host=host, alive=alive)
    ]
    if not live:
        return
    run_ids = sorted({run_id for _, run_id in live})
    involved = list(dict.fromkeys(path for path, _ in live))
    raise MigrationRefusedError(
        "live_run",
        f"run(s) {', '.join(run_ids)} in {', '.join(map(str, involved))}"
        " hold a live lease; wait for them to finish or stop them",
        files=involved,
        run_ids=run_ids,
    )


def _refuse_foreign(legacy: store_legacy.LegacyFile) -> None:
    if len(legacy.repo_dirs) != 1:
        raise MigrationRefusedError(
            "repo_dir_disagrees",
            f"{legacy.path} has runs of {len(legacy.repo_dirs)} repo_dirs:"
            f" {', '.join(legacy.repo_dirs)}",
            files=(legacy.path,),
        )
    (repo_dir,) = legacy.repo_dirs
    expected = paths.project_digest(Path(repo_dir))
    if legacy.path.stem != expected:
        raise MigrationRefusedError(
            "digest_mismatch",
            f"{legacy.path} is named {legacy.path.stem}, but its repo_dir"
            f" {repo_dir} has digest {expected}",
            files=(legacy.path,),
        )


def _refuse_duplicates(files: Sequence[store_legacy.LegacyFile]) -> None:
    first_seen: dict[str, Path] = {}
    clashes: dict[str, set[Path]] = {}
    for legacy in files:
        for run_id in legacy.run_ids:
            if run_id in first_seen:
                clashes.setdefault(run_id, {first_seen[run_id]}).add(legacy.path)
            else:
                first_seen[run_id] = legacy.path
    if not clashes:
        return
    involved = [
        legacy.path
        for legacy in files
        if any(legacy.path in where for where in clashes.values())
    ]
    run_ids = sorted(clashes)
    raise MigrationRefusedError(
        "duplicate_run_id",
        f"run id(s) {', '.join(run_ids)} appear in more than one of"
        f" {', '.join(map(str, involved))}",
        files=involved,
        run_ids=run_ids,
    )


def _merge(
    files: Sequence[store_legacy.LegacyFile], *, now: datetime
) -> store_legacy.MergeOutcome:
    conn = store_db.open_db_for_migration()
    try:
        return store_legacy.merge(conn, files, now=now)
    except store_legacy.LegacyRunClashError as error:
        raise MigrationRefusedError(
            "duplicate_run_id", str(error), files=(error.path,), run_ids=(error.run_id,)
        ) from error
    except store_legacy.LegacyRowClashError as error:
        raise MigrationRefusedError("row_clash", str(error), files=(error.path,)) from error
    finally:
        conn.close()


def migrate(
    *,
    now: datetime,
    host: str = socket.gethostname(),
    alive: Callable[[int], bool] = control.pid_alive,
) -> MigrationReport:
    """Merge every `paths.legacy_project_dbs()` file into `paths.db_path()`.

    In order: a marker already in `am.db` (read read-only) returns it as
    `already_migrated` without opening any legacy file. No legacy file
    returns `migrated_at=None` and creates nothing. Otherwise every file is
    read through `store_legacy.read_legacy` (`unreadable`). Any lease that
    `control.lease_is_live` accepts with `now`, `host` and `alive` refuses
    (`live_run`). Each file with runs must have one `repo_dir`
    (`repo_dir_disagrees`) whose digest is its stem (`digest_mismatch`), and
    no run id may be in two files (`duplicate_run_id`); files with no runs are
    skipped. These checks create nothing. Then `store_legacy.merge` runs one
    transaction that re-checks the marker, refuses a run id already in
    `am.db` (`duplicate_run_id`) or a refused row (`row_clash`), and writes
    the marker, even when every file was skipped.

    Every refusal is `MigrationRefusedError`, and nothing is committed.
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
    files = [_read(path) for path in legacy]
    _refuse_live(files, now=now, host=host, alive=alive)
    merging = [legacy_file for legacy_file in files if legacy_file.run_ids]
    skipped = tuple(legacy_file.path for legacy_file in files if not legacy_file.run_ids)
    for legacy_file in merging:
        _refuse_foreign(legacy_file)
    _refuse_duplicates(merging)
    outcome = _merge(merging, now=now)
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
