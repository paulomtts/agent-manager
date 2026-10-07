"""The run lease, claim and control-request rows: their types, readers,
conflict checks and errors, and the SQL that writes them. Every function takes
an open connection and never commits."""

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime

from agent_manager.store import db as store_db


@dataclass(frozen=True)
class LeaseRow:
    """The running process's claim on a run: a row of `run_leases` (live control C2).

    Row-only and outside the journal. `accepting` is a real
    `bool`: once the control window closes it is `False` and a new request
    must be refused by the requester.
    """

    run_id: str
    token: str
    pid: int
    host: str
    acquired_at: datetime
    heartbeat_at: datetime
    accepting: bool


def _lease_from_row(row: sqlite3.Row) -> LeaseRow:
    return LeaseRow(
        run_id=row["run_id"],
        token=row["token"],
        pid=row["pid"],
        host=row["host"],
        acquired_at=datetime.fromisoformat(row["acquired_at"]),
        heartbeat_at=datetime.fromisoformat(row["heartbeat_at"]),
        accepting=bool(row["accepting"]),
    )


def read_lease(conn: sqlite3.Connection, run_id: str) -> LeaseRow | None:
    """The lease row of `run_id`, or `None` if no process holds one.

    A free function over a connection so a second process (`am pause`,
    `am status`) can read it without a `Store`, as with `load_run`.
    """
    row = conn.execute(
        "SELECT * FROM run_leases WHERE run_id = ?", (run_id,)
    ).fetchone()
    return None if row is None else _lease_from_row(row)


@dataclass(frozen=True)
class ClaimRow:
    """One key a run's lease owns: a row of `run_claims` (multi-process X5).

    Row-only and outside the journal, like `LeaseRow`. A claim counts only
    while the `run_leases` row of `run_id` still carries `token` and is live;
    otherwise the next `Store.take_lease` naming the key overwrites it.
    """

    key: str
    run_id: str
    token: str
    claimed_at: datetime


def _claim_from_row(row: sqlite3.Row) -> ClaimRow:
    return ClaimRow(
        key=row["key"],
        run_id=row["run_id"],
        token=row["token"],
        claimed_at=datetime.fromisoformat(row["claimed_at"]),
    )


@dataclass(frozen=True)
class LeaseTake:
    """What `Store.take_lease` took, and the earlier lease row it replaced, if any."""

    lease: LeaseRow
    displaced: LeaseRow | None


class LeaseHeldError(RuntimeError):
    """Another process holds this run's lease and it is live (multi-process X5)."""

    def __init__(self, holder: LeaseRow) -> None:
        super().__init__(
            f"run {holder.run_id!r} is held by a live lease"
            f" (pid {holder.pid} on {holder.host})"
        )
        self.holder = holder


class ClaimHeldError(RuntimeError):
    """A claim key belongs to another run whose lease is live (multi-process X5)."""

    def __init__(self, key: str, holder: LeaseRow) -> None:
        super().__init__(
            f"{key!r} is claimed by run {holder.run_id!r}, whose lease is live"
            f" (pid {holder.pid} on {holder.host})"
        )
        self.key = key
        self.holder = holder


class LeaseLostError(BaseException):
    """A bound store's lease was taken over or deleted: it must write nothing (X4).

    A `BaseException`, not an `Exception`, so no `except Exception` in the
    engine can swallow it and carry on writing a run this process no longer
    owns. `holder` is the lease row now in place, or `None` if there is none.
    """

    def __init__(self, run_id: str, holder: LeaseRow | None) -> None:
        who = (
            "no process holds it now"
            if holder is None
            else f"pid {holder.pid} on {holder.host} holds it now"
        )
        super().__init__(f"this process lost the lease of run {run_id!r}: {who}")
        self.run_id = run_id
        self.holder = holder


def claim_conflicts(
    conn: sqlite3.Connection,
    keys: Iterable[str],
    *,
    project_id: int,
    is_live: Callable[[LeaseRow], bool],
    run_id: str | None = None,
) -> list[tuple[str, LeaseRow]]:
    """The keys of `keys`, in order, that another run's live lease holds in `project_id`.

    Read-only. A key conflicts when its `run_claims` row in `project_id`
    names a run other than `run_id`, that run's `run_leases` row still
    carries the claim's token, and `is_live` says that lease row is live. A
    claim on the same key in another project is never a conflict. `is_live`
    is injected so this module never imports `control`; it is asked only
    about a claim whose token still matches its run's lease.
    """
    conflicts: list[tuple[str, LeaseRow]] = []
    for key in keys:
        claim = conn.execute(
            "SELECT run_id, token FROM run_claims WHERE project_id = ? AND key = ?",
            (project_id, key),
        ).fetchone()
        if claim is None or claim["run_id"] == run_id:
            continue
        lease = read_lease(conn, claim["run_id"])
        if lease is None or lease.token != claim["token"] or not is_live(lease):
            continue
        conflicts.append((key, lease))
    return conflicts


def held_claims(conn: sqlite3.Connection, run_id: str, token: str) -> list[ClaimRow]:
    """Every claim `run_id` holds under `token`, in key order."""
    rows = conn.execute(
        "SELECT * FROM run_claims WHERE run_id = ? AND token = ? ORDER BY key",
        (run_id, token),
    ).fetchall()
    return [_claim_from_row(row) for row in rows]


@dataclass(frozen=True)
class ControlRow:
    """One `am pause`/`am cancel` request: a row of `run_controls` (live control C1).

    Row-only and outside the journal. `lease` is the token the request was
    addressed to, so a row under an old lease never reaches a resumed run.
    """

    run_id: str
    seq: int
    lease: str
    command: str
    requested_at: datetime
    handled_at: datetime | None


def _control_from_row(row: sqlite3.Row) -> ControlRow:
    handled = row["handled_at"]
    return ControlRow(
        run_id=row["run_id"],
        seq=row["seq"],
        lease=row["lease"],
        command=row["command"],
        requested_at=datetime.fromisoformat(row["requested_at"]),
        handled_at=None if handled is None else datetime.fromisoformat(handled),
    )


def control_requests(
    conn: sqlite3.Connection, run_id: str, *, lease: str | None = None
) -> list[ControlRow]:
    """Every control request of `run_id` in `seq` order, handled or not.

    With `lease=None` every lease's rows are returned; otherwise only the rows
    addressed to that token.
    """
    if lease is None:
        rows = conn.execute(
            "SELECT * FROM run_controls WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM run_controls WHERE run_id = ? AND lease = ? ORDER BY seq",
            (run_id, lease),
        ).fetchall()
    return [_control_from_row(row) for row in rows]


def add_control(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    project_id: int,
    lease: str,
    command: str,
    requested_at: datetime,
) -> ControlRow:
    """Insert the next control request of `run_id`, addressed to `lease`.

    `seq` is 0 for the run's first request and one past the highest after
    that; the row carries `project_id`. Does not commit: run it inside
    `immediate` so the `MAX(seq)` read and the insert are one locked write.
    An unknown `command` is refused by the table's `CHECK` as
    `sqlite3.IntegrityError`; that is the only guard.
    """
    highest = conn.execute(
        "SELECT MAX(seq) FROM run_controls WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    seq = 0 if highest is None else highest + 1
    conn.execute(
        "INSERT INTO run_controls (project_id, run_id, seq, lease, command,"
        " requested_at, handled_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
        (project_id, run_id, seq, lease, command, store_db.iso(requested_at)),
    )
    return ControlRow(
        run_id=run_id,
        seq=seq,
        lease=lease,
        command=command,
        requested_at=requested_at,
        handled_at=None,
    )


def take_lease(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    project_id: int,
    token: str,
    pid: int,
    host: str,
    now: datetime,
    is_live: Callable[[LeaseRow], bool],
    claims: Iterable[str] = (),
) -> LeaseTake:
    """Take `run_id`'s lease under `token`, with every key of `claims` in `project_id`.

    Does not commit: run it inside `store_db.immediate` so the checks and the
    upserts are one locked write. A live lease under another token raises
    `LeaseHeldError`; otherwise that row, or `None`, is the `displaced` one.
    Then the first key another run of `project_id` holds under a live lease
    raises `ClaimHeldError`. Only then are the lease (window open) and every
    claim upserted, each row carrying `project_id`; a claim is keyed
    `(project_id, key)`, so the same key in another project is untouched.
    """
    keys = list(claims)
    current = read_lease(conn, run_id)
    if current is not None and current.token != token and is_live(current):
        raise LeaseHeldError(current)
    conflicts = claim_conflicts(
        conn, keys, project_id=project_id, is_live=is_live, run_id=run_id
    )
    if conflicts:
        key, holder = conflicts[0]
        raise ClaimHeldError(key, holder)
    conn.execute(
        "INSERT INTO run_leases (project_id, run_id, token, pid, host, acquired_at,"
        " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, ?, 1)"
        " ON CONFLICT(run_id) DO UPDATE SET"
        " token=excluded.token, pid=excluded.pid, host=excluded.host,"
        " acquired_at=excluded.acquired_at,"
        " heartbeat_at=excluded.heartbeat_at, accepting=1",
        (project_id, run_id, token, pid, host, store_db.iso(now), store_db.iso(now)),
    )
    for key in keys:
        conn.execute(
            "INSERT INTO run_claims (project_id, key, run_id, token, claimed_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(project_id, key) DO UPDATE SET"
            " run_id=excluded.run_id, token=excluded.token,"
            " claimed_at=excluded.claimed_at",
            (project_id, key, run_id, token, store_db.iso(now)),
        )
    return LeaseTake(
        lease=LeaseRow(
            run_id=run_id,
            token=token,
            pid=pid,
            host=host,
            acquired_at=now,
            heartbeat_at=now,
            accepting=True,
        ),
        displaced=current,
    )


def release_claims(conn: sqlite3.Connection, run_id: str, token: str) -> None:
    """Delete `run_id`'s claims held under `token`; any other row is untouched."""
    conn.execute(
        "DELETE FROM run_claims WHERE run_id = ? AND token = ?",
        (run_id, token),
    )


def beat(conn: sqlite3.Connection, run_id: str, token: str, now: datetime) -> None:
    """Move the heartbeat of `run_id`'s lease to `now`, if `token` still holds it."""
    conn.execute(
        "UPDATE run_leases SET heartbeat_at = ? WHERE run_id = ? AND token = ?",
        (store_db.iso(now), run_id, token),
    )


def close_window(conn: sqlite3.Connection, run_id: str, token: str) -> None:
    """Stop `run_id`'s lease under `token` accepting control requests (`accepting = 0`)."""
    conn.execute(
        "UPDATE run_leases SET accepting = 0 WHERE run_id = ? AND token = ?",
        (run_id, token),
    )


def release_lease(conn: sqlite3.Connection, run_id: str, token: str) -> None:
    """Delete `run_id`'s lease, if `token` still holds it."""
    conn.execute(
        "DELETE FROM run_leases WHERE run_id = ? AND token = ?",
        (run_id, token),
    )


def set_lease_holder(
    conn: sqlite3.Connection, run_id: str, token: str, *, pid: int, host: str
) -> None:
    """Name `pid` on `host` as `run_id`'s lease holder, if `token` still holds it."""
    conn.execute(
        "UPDATE run_leases SET pid = ?, host = ? WHERE run_id = ? AND token = ?",
        (pid, host, run_id, token),
    )


def pending_controls(conn: sqlite3.Connection, run_id: str, token: str) -> list[ControlRow]:
    """`run_id`'s unhandled requests addressed to `token`, in `seq` order."""
    rows = conn.execute(
        "SELECT * FROM run_controls WHERE run_id = ? AND lease = ?"
        " AND handled_at IS NULL ORDER BY seq",
        (run_id, token),
    ).fetchall()
    return [_control_from_row(row) for row in rows]


def mark_control_handled(
    conn: sqlite3.Connection, run_id: str, seq: int, now: datetime
) -> ControlRow | None:
    """Record that `run_id`'s pending request `seq` was applied at `now`.

    Returns the row as it now stands. An unknown `seq`, or one already
    handled, is left exactly as it is and gives `None`: the first handling's
    `handled_at` stands.
    """
    row = conn.execute(
        "SELECT * FROM run_controls WHERE run_id = ? AND seq = ?", (run_id, seq)
    ).fetchone()
    if row is None or row["handled_at"] is not None:
        return None
    conn.execute(
        "UPDATE run_controls SET handled_at = ? WHERE run_id = ? AND seq = ?",
        (store_db.iso(now), run_id, seq),
    )
    return replace(_control_from_row(row), handled_at=now)
