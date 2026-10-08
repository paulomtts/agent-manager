"""`am export`: one run's `events` rows as journal-shaped JSON lines.

A line is the journal envelope plus the additive `gseq` (the row's global
`seq`), serialised as the journal writer serialises: `json.dumps` with
`sort_keys`. It is built straight from the row, never through `JournalLine`,
so `ts` stays the stored text and a kind outside `EventKind` is exported like
any other. For a journal line `am` wrote, the exported line minus `gseq` is
the same bytes. Read-only: `am.db` only through `store_db.open_db_for_reading`;
the only file it ever creates is `write_export`'s target, never a run's
journal.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent_manager import paths, runs
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal
from agent_manager.store import queries as store_queries

ExportRefusal = Literal["journal_path", "target_exists", "no_target_dir"]
"""Which check refused an export to `--out`."""


class ExportRefusedError(RuntimeError):
    """`write_export` will not write the file; nothing has been written.

    `reason` names the check that refused, `path` the target it is about.
    """

    def __init__(self, reason: ExportRefusal, path: Path) -> None:
        super().__init__(
            f"am export refused ({reason}): {path}; nothing has been written"
        )
        self.reason = reason
        self.path = path


@dataclass(frozen=True)
class ExportResult:
    """The file `write_export` wrote: the run, its absolute path, its line count."""

    run_id: str
    path: Path
    lines: int


def line(row: store_events.EventRow) -> str:
    """`row` as one exported line, without the trailing newline: every
    envelope key present (`story`, `card`, `phase`, `attempt` null when the
    row has none), `seq` the row's `run_seq`, `gseq` its global `seq`, `ts`
    the stored text. `schema`, `project_id` and `source` are not exported."""
    return json.dumps(
        {
            "attempt": row.attempt,
            "card": row.card_id,
            "event": row.kind,
            "gseq": row.seq,
            "payload": row.payload,
            "phase": row.phase,
            "run_id": row.run_id,
            "seq": row.run_seq,
            "story": row.story_id,
            "ts": row.ts,
        },
        sort_keys=True,
    )


def export_run(run_id: str) -> list[str]:
    """Every event of `run_id`, of every kind, as `line`s ascending by
    `run_seq`.

    The run check and the read run in one `store_db.read_snapshot`, so the
    export is one consistent cut. A run with neither an `events` row nor a
    `runs` row raises `runs.unknown_run(run_id)`; a `runs` row with no events
    is `[]`. Run ids are machine-unique, so no repository is resolved:
    `open_db_for_reading` gets `Path(".")` only because it takes a root. Its
    refusals (`MigrationRequiredError`, `StoreSchemaError`) propagate.
    Read-only; the connection is closed on every path.
    """
    conn = store_db.open_db_for_reading(Path("."))
    try:
        with store_db.read_snapshot(conn):
            if not store_queries.run_known(conn, run_id):
                raise runs.unknown_run(run_id)
            rows = store_events.read_run(conn, run_id)
    finally:
        conn.close()
    return [line(row) for row in rows]


def write_export(run_id: str, out: Path) -> ExportResult:
    """`export_run(run_id)` written to the new file `out`, one line and a
    newline per event (an empty file for none).

    `out` is taken after `expanduser`, from the current directory when
    relative. The run is checked and read first, so an unknown run creates
    nothing. Then refused, in this order, as `ExportRefusedError`:
    `journal_path` when `out`, resolved, is any run's journal file
    (`<data dir>/runs/<id>/journal.jsonl`), existing or not; `target_exists`
    when `out` exists as anything, a dangling symlink included, or appears
    before the exclusive open; `no_target_dir` when its parent is not a
    directory.
    """
    lines = export_run(run_id)
    target = out.expanduser().absolute()
    if _is_journal_path(target):
        raise ExportRefusedError("journal_path", target)
    if os.path.lexists(target):
        raise ExportRefusedError("target_exists", target)
    if not target.parent.is_dir():
        raise ExportRefusedError("no_target_dir", target)
    try:
        with open(target, "xb") as handle:
            handle.write("".join(f"{text}\n" for text in lines).encode("utf-8"))
    except FileExistsError:
        raise ExportRefusedError("target_exists", target) from None
    return ExportResult(run_id=run_id, path=target, lines=len(lines))


def _is_journal_path(target: Path) -> bool:
    """Whether `target`, resolved, is `<data dir>/runs/<id>/journal.jsonl` for
    some `<id>`. `paths.data_path()`, not `data_dir()`: nothing is created."""
    resolved = target.resolve()
    return (
        resolved.name == store_journal.JOURNAL_NAME
        and resolved.parent.parent == (paths.data_path() / "runs").resolve()
    )
