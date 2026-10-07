"""The `board_comments` outbox rows: their type, readers and writes. Every
function takes an open connection and never commits."""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from agent_manager.store import db as store_db

COMMENT_ATTEMPTS = 3
"""Failed posts after which a `board_comments` row is `abandoned` (board-comments
B7). The warning that names an abandoned row belongs to `comments.py`."""


@dataclass(frozen=True)
class CommentRow:
    """One queued outcome comment: a row of `board_comments` (board-comments B6).

    Row-only and outside the journal. `key` is the
    idempotency key a replay or resume enqueues again (B9); `comment_id` is
    the board's id once posted, else `None`.
    """

    run_id: str
    card_id: str
    key: str
    body: str
    state: str
    comment_id: str | None
    failed_attempts: int


def _comment_from_row(row: sqlite3.Row) -> CommentRow:
    return CommentRow(
        run_id=row["run_id"],
        card_id=row["card_id"],
        key=row["key"],
        body=row["body"],
        state=row["state"],
        comment_id=row["comment_id"],
        failed_attempts=row["failed_attempts"],
    )


def enqueue_comment(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    run_id: str,
    card_id: str,
    key: str,
    body: str,
    now: datetime,
) -> bool:
    """Queue `body` for `card_id` under `(project_id, key)`, once.

    True when a `pending` row was inserted; False when that pair already had
    a row, which is left exactly as it was. The same `key` in another
    project is a separate row. Only the key collision is ignored: a NULL
    body or any other refused value raises `sqlite3.IntegrityError` for the
    caller to roll back.
    """
    cursor = conn.execute(
        "INSERT INTO board_comments (project_id, run_id, card_id, key, body, state,"
        " comment_id, failed_attempts, created_at, posted_at)"
        " VALUES (?, ?, ?, ?, ?, 'pending', NULL, 0, ?, NULL)"
        " ON CONFLICT(project_id, key) DO NOTHING",
        (project_id, run_id, card_id, key, body, store_db.iso(now)),
    )
    return cursor.rowcount == 1


def pending_comments(
    conn: sqlite3.Connection,
    run_id: str | None = None,
    card_ids: Iterable[str] | None = None,
) -> list[CommentRow]:
    """Every `pending` row, oldest `created_at` first, then insertion order.

    Each given filter narrows the result and they are ANDed; with neither,
    every pending row of every run is returned. An empty `card_ids` matches
    nothing and runs no query.
    """
    clauses = ["state = 'pending'"]
    params: list[str] = []
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if card_ids is not None:
        cards = list(card_ids)
        if not cards:
            return []
        clauses.append(f"card_id IN ({', '.join('?' for _ in cards)})")
        params.extend(cards)
    rows = conn.execute(
        "SELECT * FROM board_comments WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at, rowid",
        params,
    ).fetchall()
    return [_comment_from_row(row) for row in rows]


def mark_comment_posted(
    conn: sqlite3.Connection,
    key: str,
    comment_id: str,
    now: datetime,
    *,
    project_id: int,
) -> None:
    """Record that `(project_id, key)`'s body is on the board as `comment_id`; an
    unknown pair changes nothing, and the same key in another project is untouched."""
    conn.execute(
        "UPDATE board_comments SET state = 'posted', comment_id = ?,"
        " posted_at = ? WHERE project_id = ? AND key = ?",
        (comment_id, store_db.iso(now), project_id, key),
    )


def record_comment_failure(conn: sqlite3.Connection, key: str, *, project_id: int) -> int:
    """Count one failed post of `(project_id, key)` and return the new `failed_attempts`.

    A `pending` row reaching `COMMENT_ATTEMPTS` becomes `abandoned`; a row
    already `posted` keeps its state. An unknown pair changes nothing and
    gives 0; the same key in another project is untouched.
    """
    conn.execute(
        "UPDATE board_comments SET failed_attempts = failed_attempts + 1,"
        " state = CASE WHEN state = 'pending' AND failed_attempts + 1 >= ?"
        " THEN 'abandoned' ELSE state END WHERE project_id = ? AND key = ?",
        (COMMENT_ATTEMPTS, project_id, key),
    )
    row = conn.execute(
        "SELECT failed_attempts FROM board_comments WHERE project_id = ? AND key = ?",
        (project_id, key),
    ).fetchone()
    return 0 if row is None else row["failed_attempts"]
