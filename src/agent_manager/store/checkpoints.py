"""The `checkpoints` and `checkpoint_floors` rows: their types, readers and
the insert. Every function takes an open connection and never commits."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from agent_manager import models
from agent_manager.store import db as store_db


@dataclass(frozen=True)
class TurnFloor:
    """The turn identity saved beside an agent-phase checkpoint (exactly-once 1.1).

    One row of `checkpoint_floors`, keyed like its `checkpoints` row. Row-only
    and outside the journal: nothing journals it and `rebuild_from_events`
    leaves it alone. Computing it is the runtime's job, not the store's.
    """

    phase: str
    loop: int
    source_run: str
    floor: int


@dataclass(frozen=True)
class Checkpoint:
    """One saved turn of a subtask's agent: a row of `checkpoints` (pygents spec §6).

    Internal state, so a plain dataclass rather than a pydantic model. It is not
    part of the §9 tree: no journal line records it and `rebuild_from_events`
    neither writes nor deletes it. `agent` is the decoded JSON of the stored
    text, never the dict the caller handed in.
    """

    run_id: str
    card_id: str
    seq: int
    workflow: str
    digest: str
    reason: str
    agent: dict
    saved_at: datetime
    floor: TurnFloor | None = None


def _checkpoint_from_row(row: sqlite3.Row) -> Checkpoint:
    """A `Checkpoint` from a `checkpoints` row, joined with its floor if selected.

    A `sqlite3.Row` raises `IndexError` for a key it lacks, so a row selected
    without the `floor_*` columns is checked for the key first and gives
    `floor=None`, as does a joined row with no `checkpoint_floors` match.
    """
    floor = None
    if "floor_phase" in row.keys() and row["floor_phase"] is not None:
        floor = TurnFloor(
            phase=row["floor_phase"],
            loop=row["floor_loop"],
            source_run=row["floor_source_run"],
            floor=row["floor_floor"],
        )
    return Checkpoint(
        run_id=row["run_id"],
        card_id=row["card_id"],
        seq=row["seq"],
        workflow=row["workflow"],
        digest=row["digest"],
        reason=row["reason"],
        agent=json.loads(row["agent"]),
        saved_at=datetime.fromisoformat(row["saved_at"]),
        floor=floor,
    )


_CHECKPOINT_SELECT = (
    "SELECT c.*, f.phase AS floor_phase, f.loop AS floor_loop,"
    " f.source_run AS floor_source_run, f.floor AS floor_floor"
    " FROM checkpoints c LEFT JOIN checkpoint_floors f"
    " ON f.run_id = c.run_id AND f.card_id = c.card_id AND f.seq = c.seq"
)
"""Every checkpoint reader's select: the row plus its floor, if it has one."""


def insert_checkpoint(
    conn: sqlite3.Connection,
    run_id: str,
    card_id: str,
    *,
    project_id: int,
    workflow: str,
    digest: str,
    reason: str,
    agent: dict,
    saved_at: datetime,
    floor: TurnFloor | None = None,
) -> Checkpoint:
    """Insert the next checkpoint of `card_id` under `run_id`, and its floor if given.

    `seq` is 0 for the card's first row in the run and one past the highest
    after that. Both rows carry `project_id`. Does not commit or roll back:
    an unknown `reason` or a negative floor raises `sqlite3.IntegrityError`
    from the table's `CHECK`, and the caller discards the transaction.
    `agent` in the result is the decoded JSON of the stored text.
    """
    text = json.dumps(agent, sort_keys=True)
    highest = conn.execute(
        "SELECT MAX(seq) FROM checkpoints WHERE run_id = ? AND card_id = ?",
        (run_id, card_id),
    ).fetchone()[0]
    seq = 0 if highest is None else highest + 1
    conn.execute(
        "INSERT INTO checkpoints (project_id, run_id, card_id, seq, workflow,"
        " digest, reason, agent, saved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            run_id,
            card_id,
            seq,
            workflow,
            digest,
            reason,
            text,
            store_db.iso(saved_at),
        ),
    )
    if floor is not None:
        conn.execute(
            "INSERT INTO checkpoint_floors (project_id, run_id, card_id, seq,"
            " phase, loop, source_run, floor) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                run_id,
                card_id,
                seq,
                floor.phase,
                floor.loop,
                floor.source_run,
                floor.floor,
            ),
        )
    return Checkpoint(
        run_id=run_id,
        card_id=card_id,
        seq=seq,
        workflow=workflow,
        digest=digest,
        reason=reason,
        agent=json.loads(text),
        saved_at=saved_at,
        floor=floor,
    )


def latest_checkpoint(
    conn: sqlite3.Connection, run_id: str, card_id: str
) -> Checkpoint | None:
    """The highest-`seq` checkpoint of `card_id` in `run_id`, any reason."""
    row = conn.execute(
        _CHECKPOINT_SELECT
        + " WHERE c.run_id = ? AND c.card_id = ?"
        " ORDER BY c.seq DESC LIMIT 1",
        (run_id, card_id),
    ).fetchone()
    return None if row is None else _checkpoint_from_row(row)


def latest_turn_checkpoint(
    conn: sqlite3.Connection, run_id: str, card_id: str
) -> Checkpoint | None:
    """The highest-`seq` `turn` checkpoint of `card_id` in `run_id`."""
    row = conn.execute(
        _CHECKPOINT_SELECT
        + " WHERE c.run_id = ? AND c.card_id = ?"
        " AND c.reason = 'turn' ORDER BY c.seq DESC LIMIT 1",
        (run_id, card_id),
    ).fetchone()
    return None if row is None else _checkpoint_from_row(row)


def latest_open_checkpoint(
    conn: sqlite3.Connection, card_id: str, workflow: str
) -> Checkpoint | None:
    """The newest open checkpoint of `card_id` for `workflow`, across every run.

    The card's newest row in any run and any workflow decides first: if it
    is `done`, or it belongs to a run canceled in either spelling, the card
    is closed and this returns `None`. Otherwise it is the newest
    `turn`/`parked`/`escalated` row of `workflow` that does not belong to a
    run canceled in either spelling, or `None`.
    """
    newest = conn.execute(
        "SELECT c.reason, r.status FROM checkpoints c"
        " LEFT JOIN runs r ON r.id = c.run_id"
        " WHERE c.card_id = ?"
        " ORDER BY c.saved_at DESC, c.seq DESC LIMIT 1",
        (card_id,),
    ).fetchone()
    if (
        newest is None
        or newest["reason"] == "done"
        or models.is_canceled(newest["status"])
    ):
        return None
    row = conn.execute(
        _CHECKPOINT_SELECT
        + " WHERE c.card_id = ? AND c.workflow = ?"
        " AND c.reason IN ('turn', 'parked', 'escalated')"
        " AND c.run_id NOT IN (SELECT id FROM runs WHERE status IN (?, ?))"
        " ORDER BY c.saved_at DESC, c.seq DESC LIMIT 1",
        (card_id, workflow, models.CANCELED, models.LEGACY_CANCELED),
    ).fetchone()
    return None if row is None else _checkpoint_from_row(row)


def checkpoint_cards(conn: sqlite3.Connection, run_id: str) -> list[tuple[str, str]]:
    """Every distinct `(card_id, workflow)` with a checkpoint row under `run_id`.

    Any `reason` counts, `done` included. Ordered by `card_id`, then
    `workflow`.
    """
    rows = conn.execute(
        "SELECT DISTINCT card_id, workflow FROM checkpoints"
        " WHERE run_id = ? ORDER BY card_id, workflow",
        (run_id,),
    ).fetchall()
    return [(row["card_id"], row["workflow"]) for row in rows]
