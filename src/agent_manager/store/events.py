"""The append-only `events` rows, numbered by `seq`, and the journal lines
they are. Every function takes an open connection and never commits; the
table's DDL and the triggers that refuse an update or delete live in
`store.db`. From this package it imports only `store.journal`, a lower layer."""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_manager.store import journal as store_journal


@dataclass(frozen=True)
class EventRow:
    """One row of `events`.

    `ts` is the text it was stored with; `payload` is the decoded JSON object.
    """

    seq: int
    project_id: int
    run_id: str
    run_seq: int
    ts: str
    kind: str
    story_id: str | None
    card_id: str | None
    phase: str | None
    attempt: int | None
    schema: int
    payload: dict[str, Any]
    source: str


def _event_from_row(row: sqlite3.Row) -> EventRow:
    return EventRow(
        seq=row["seq"],
        project_id=row["project_id"],
        run_id=row["run_id"],
        run_seq=row["run_seq"],
        ts=row["ts"],
        kind=row["kind"],
        story_id=row["story_id"],
        card_id=row["card_id"],
        phase=row["phase"],
        attempt=row["attempt"],
        schema=row["schema"],
        payload=json.loads(row["payload"]),
        source=row["source"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    run_id: str,
    ts: str,
    kind: str,
    payload: Mapping[str, Any],
    source: str,
    story_id: str | None = None,
    card_id: str | None = None,
    phase: str | None = None,
    attempt: int | None = None,
    schema: int = 1,
    run_seq: int | None = None,
) -> EventRow:
    """Insert one event and return the row as stored, with its `seq` and `run_seq`.

    `ts` is stored verbatim. `payload` is stored as `json.dumps` with
    `sort_keys`; it is serialised before any SQL runs, so an unserialisable
    payload raises `TypeError` and inserts nothing. `run_seq=None` assigns one
    more than the run's largest `run_seq` (1 for its first event), read on
    `conn` before the insert; the caller's write transaction makes that read
    and the insert atomic. An explicit `run_seq` is stored as given.
    SQLite's `IntegrityError` (CHECK, UNIQUE, NOT NULL) propagates unchanged.
    Does not commit: the caller's transaction covers the insert.
    """
    text = json.dumps(dict(payload), sort_keys=True)
    if run_seq is None:
        run_seq = conn.execute(
            "SELECT COALESCE(MAX(run_seq), 0) + 1 FROM events WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    cursor = conn.execute(
        "INSERT INTO events (project_id, run_id, run_seq, ts, kind, story_id,"
        " card_id, phase, attempt, schema, payload, source)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            run_id,
            run_seq,
            ts,
            kind,
            story_id,
            card_id,
            phase,
            attempt,
            schema,
            text,
            source,
        ),
    )
    row = conn.execute(
        "SELECT * FROM events WHERE seq = ?", (cursor.lastrowid,)
    ).fetchone()
    return _event_from_row(row)


def read(
    conn: sqlite3.Connection,
    *,
    after_seq: int = 0,
    limit: int | None = None,
    run_id: str | None = None,
    project_id: int | None = None,
) -> list[EventRow]:
    """The rows with `seq > after_seq`, ascending by `seq`, at most `limit` of them.

    `run_id` and `project_id`, when given, narrow the result and are ANDed;
    with neither, every project's rows. `limit=None` means no limit; a
    `limit` below 1 raises `ValueError`. Read-only.
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")
    clauses = ["seq > ?"]
    params: list[object] = [after_seq]
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    sql = "SELECT * FROM events WHERE " + " AND ".join(clauses) + " ORDER BY seq"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [_event_from_row(row) for row in conn.execute(sql, params).fetchall()]


def read_last(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    before_seq: int | None = None,
    run_id: str | None = None,
    project_id: int | None = None,
) -> list[EventRow]:
    """The `limit` rows with the largest `seq` below `before_seq`, returned
    ascending by `seq`.

    `before_seq=None` means no bound; a `before_seq` of 1 or less yields `[]`.
    `run_id` and `project_id`, when given, narrow the rows before the limit
    counts them and are ANDed. `limit=None` means every row; a `limit` below
    1 raises `ValueError`. A caller pages backwards by passing the first
    `seq` of a page as the next `before_seq`. Read-only.
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")
    clauses: list[str] = []
    params: list[object] = []
    if before_seq is not None:
        clauses.append("seq < ?")
        params.append(before_seq)
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    sql = "SELECT * FROM events"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY seq DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_event_from_row(row) for row in reversed(rows)]


def read_escalations(
    conn: sqlite3.Connection,
    *,
    after_seq: int = 0,
    limit: int | None = None,
    project_id: int | None = None,
) -> list[EventRow]:
    """The escalation events with `seq > after_seq`, ascending by `seq`, at
    most `limit` of them.

    An escalation event is a `run_upsert` row whose payload's top-level
    `status` is exactly the string `"escalated"`. This is the one definition
    of it. A node of any other kind saying `"escalated"` is not one, matching
    is case-sensitive, and a payload that is not JSON is never a match (it is
    skipped, not an error). Every matching row is returned, so a run recorded
    twice while escalated yields two rows. `project_id`, when given, narrows
    the rows before the limit counts them; with none, every project's.
    `limit=None` means no limit; a `limit` below 1 raises `ValueError`.
    Read-only.
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")
    clauses = [
        "seq > ?",
        "kind = 'run_upsert'",
        "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.status') END"
        " = 'escalated'",
    ]
    params: list[object] = [after_seq]
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    sql = "SELECT * FROM events WHERE " + " AND ".join(clauses) + " ORDER BY seq"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [_event_from_row(row) for row in conn.execute(sql, params).fetchall()]


def head(conn: sqlite3.Connection) -> int:
    """The largest `seq` in `events`, or 0 when it has no rows. Read-only."""
    return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]


def run_ids(conn: sqlite3.Connection) -> list[str]:
    """Every distinct `run_id` in `events`, ascending, over every kind and
    every project; `[]` when the table has no rows. Read-only."""
    rows = conn.execute("SELECT DISTINCT run_id FROM events ORDER BY run_id").fetchall()
    return [row[0] for row in rows]


def has_run(conn: sqlite3.Connection, run_id: str) -> bool:
    """Whether `events` holds at least one row of `run_id`, of any kind and
    any project. Read-only."""
    row = conn.execute(
        "SELECT 1 FROM events WHERE run_id = ? LIMIT 1", (run_id,)
    ).fetchone()
    return row is not None


def journal_line(event: EventRow) -> store_journal.JournalLine:
    """The journal line `event` is: its `run_seq` is the line's `seq` and its
    `kind` the line's `event`; `ts`, coordinates and payload are the event's
    own, unchanged. A field `JournalLine` refuses raises pydantic's
    `ValidationError`."""
    return store_journal.JournalLine(
        seq=event.run_seq,
        ts=event.ts,
        run_id=event.run_id,
        event=event.kind,
        story=event.story_id,
        card=event.card_id,
        phase=event.phase,
        attempt=event.attempt,
        payload=event.payload,
    )


def run_lines(conn: sqlite3.Connection, run_id: str) -> list[store_journal.JournalLine]:
    """`run_id`'s events whose kind is in `store_journal.NODE_KINDS`, as
    `journal_line`s, ascending by `run_seq`.

    A row of any other kind is skipped; another run's rows are never read.
    `[]` when the run has no such row. A row whose payload is not JSON raises
    `JournalError` naming the run and the row's `run_seq`; a row
    `journal_line` refuses raises pydantic's `ValidationError`. Read-only.
    """
    kinds = sorted(store_journal.NODE_KINDS)
    rows = conn.execute(
        "SELECT * FROM events WHERE run_id = ? AND kind IN"
        f" ({', '.join('?' for _ in kinds)}) ORDER BY run_seq",
        (run_id, *kinds),
    ).fetchall()
    lines: list[store_journal.JournalLine] = []
    for row in rows:
        try:
            event = _event_from_row(row)
        except json.JSONDecodeError as error:
            raise store_journal.JournalError(
                f"event run_seq {row['run_seq']} of run {run_id!r} has a payload"
                f" that is not JSON: {error}"
            ) from error
        lines.append(journal_line(event))
    return lines
