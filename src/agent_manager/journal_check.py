"""`am journal-check`: the dual-write checker. Each run's `events` rows of the
node kinds (`store_journal.NODE_KINDS`) against its `journal.jsonl`, keyed by
`run_seq`, every difference reported.

Read-only: `am.db` only through `store_db.open_db_for_reading`, each journal
only through `store_journal.read_verbatim`; nothing under the data directory is
created, changed or removed. The table and the file are read one after the
other, so a run live during the check can show its in-flight tail as a
transient difference."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agent_manager import paths, runs
from agent_manager.store import db as store_db
from agent_manager.store import events as store_events
from agent_manager.store import journal as store_journal

DifferenceKind = Literal["missing_line", "extra_line", "mismatch"]

FIELDS: tuple[str, ...] = ("event", "ts", "story", "card", "phase", "attempt", "payload")
"""The journal fields compared, in the order one line's mismatches are reported."""


@dataclass(frozen=True)
class Difference:
    """One difference at `run_seq`. `missing_line`: `table` is the row in
    journal shape, `file` None. `extra_line`: `file` is the line in journal
    shape, `table` None. `mismatch`: `field` names the field, `table` and
    `file` are its two values."""

    run_seq: int
    kind: DifferenceKind
    field: str | None
    table: Any
    file: Any


@dataclass(frozen=True)
class Unreadable:
    """The journal line `read_verbatim` refused (1-based) and its reason."""

    line: int
    why: str


@dataclass(frozen=True)
class RunCheck:
    """One run's result. `clean` is no difference and nothing unreadable;
    `differences` ascend by `run_seq`, then by `FIELDS`."""

    run_id: str
    journal: str
    journal_present: bool
    table_lines: int
    file_lines: int
    torn_line: int | None
    unreadable: Unreadable | None
    clean: bool
    differences: tuple[Difference, ...]


@dataclass(frozen=True)
class JournalCheckReport:
    """Every run checked, ascending by `run_id`; `clean` is true when all are."""

    clean: bool
    runs: tuple[RunCheck, ...]


def _journal_path(run_id: str) -> Path:
    return paths.data_path() / "runs" / run_id / store_journal.JOURNAL_NAME


def _table_shape(run_id: str, row: store_events.EventRow) -> dict[str, Any]:
    return {
        "seq": row.run_seq,
        "ts": row.ts,
        "run_id": run_id,
        "event": row.kind,
        "story": row.story_id,
        "card": row.card_id,
        "phase": row.phase,
        "attempt": row.attempt,
        "payload": row.payload,
    }


def _file_shape(run_id: str, line: store_journal.VerbatimLine) -> dict[str, Any]:
    return {
        "seq": line.run_seq,
        "ts": line.ts,
        "run_id": run_id,
        "event": line.kind,
        "story": line.story_id,
        "card": line.card_id,
        "phase": line.phase,
        "attempt": line.attempt,
        "payload": line.payload,
    }


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def compare(
    run_id: str,
    table: Sequence[store_events.EventRow],
    journal: store_journal.VerbatimJournal | None,
    journal_path: Path,
) -> RunCheck:
    """Pure: `table` are the run's rows of any kind, of which only the
    `NODE_KINDS` count; `journal` None means no file. Every field is compared
    as canonical JSON, so `ts` is exact text and `payload` ignores key order
    but not type. Never yields `unreadable`."""
    rows = {
        row.run_seq: _table_shape(run_id, row)
        for row in table
        if row.kind in store_journal.NODE_KINDS
    }
    lines = (
        {line.run_seq: _file_shape(run_id, line) for line in journal.lines}
        if journal is not None
        else {}
    )
    differences: list[Difference] = []
    for run_seq in sorted(rows.keys() | lines.keys()):
        row, line = rows.get(run_seq), lines.get(run_seq)
        if line is None:
            differences.append(Difference(run_seq, "missing_line", None, row, None))
        elif row is None:
            differences.append(Difference(run_seq, "extra_line", None, None, line))
        else:
            differences.extend(
                Difference(run_seq, "mismatch", field, row[field], line[field])
                for field in FIELDS
                if _canonical(row[field]) != _canonical(line[field])
            )
    return RunCheck(
        run_id=run_id,
        journal=str(journal_path),
        journal_present=journal is not None,
        table_lines=len(rows),
        file_lines=len(lines),
        torn_line=journal.torn_line if journal is not None else None,
        unreadable=None,
        clean=not differences,
        differences=tuple(differences),
    )


def _check_run(run_id: str, table: Sequence[store_events.EventRow]) -> RunCheck:
    path = _journal_path(run_id)
    try:
        journal = store_journal.read_verbatim(path, run_id)
    except store_journal.MissingJournalError:
        journal = None
    except store_journal.UnimportableLineError as error:
        return RunCheck(
            run_id=run_id,
            journal=str(path),
            journal_present=True,
            table_lines=sum(row.kind in store_journal.NODE_KINDS for row in table),
            file_lines=0,
            torn_line=None,
            unreadable=Unreadable(line=error.line, why=error.why),
            clean=False,
            differences=(),
        )
    return compare(run_id, table, journal, path)


def check(run_id: str | None) -> JournalCheckReport:
    """`run_id` None checks every run: each `run_id` in `events`, of any kind,
    and each run directory holding a journal file. `am.db` is opened with
    `open_db_for_reading`, read and closed before any journal is read. An
    explicit run with no row and no journal file raises `runs.UnknownRunError`."""
    conn = store_db.open_db_for_reading(paths.data_path())
    try:
        if run_id is None:
            ids = sorted(
                set(store_events.run_ids(conn))
                | {found for found in paths.list_run_ids() if _journal_path(found).is_file()}
            )
        else:
            ids = [run_id]
        tables = {found: store_events.read(conn, run_id=found) for found in ids}
    finally:
        conn.close()
    if run_id is not None and not tables[run_id] and not _journal_path(run_id).is_file():
        raise runs.UnknownRunError(
            f"run {run_id!r} has no events and no journal at {_journal_path(run_id)}"
        )
    checked = tuple(_check_run(found, tables[found]) for found in ids)
    return JournalCheckReport(clean=all(run.clean for run in checked), runs=checked)
