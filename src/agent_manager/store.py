"""The SQLite projection of a run and the append-only journal that is its truth.

D5 keeps two independent stores: `paths.project_db_path(root)` holds a
queryable projection of the state tree, and `paths.run_dir(run_id)/journal.jsonl`
holds the append-only audit trail. The journal is appended *before* the row is
written, so if the two ever disagree the journal wins and the projection can be
thrown away and rebuilt (§9 lines 365-368).

This module owns only those two stores. Path derivation belongs to `paths`, the
state tree belongs to `models`, and the resume loop that acts on an in-flight
attempt belongs to the engine.
"""

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from agent_manager import models, paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    workflow      TEXT NOT NULL,
    repo_dir      TEXT NOT NULL,
    base_branch   TEXT NOT NULL,
    branch_prefix TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TEXT,
    config        TEXT NOT NULL,
    milestone_id  TEXT
);

CREATE TABLE IF NOT EXISTS stories (
    run_id     TEXT NOT NULL,
    card_id    TEXT NOT NULL,
    title      TEXT NOT NULL,
    level      INTEGER NOT NULL,
    status     TEXT NOT NULL,
    tip_branch TEXT,
    position   INTEGER NOT NULL,
    PRIMARY KEY (run_id, card_id)
);

CREATE TABLE IF NOT EXISTS subtasks (
    run_id        TEXT NOT NULL,
    story_id      TEXT NOT NULL,
    card_id       TEXT NOT NULL,
    branch        TEXT NOT NULL,
    base_branch   TEXT NOT NULL,
    status        TEXT NOT NULL,
    worktree_path TEXT,
    position      INTEGER NOT NULL,
    PRIMARY KEY (run_id, story_id, card_id)
);

CREATE TABLE IF NOT EXISTS phases (
    run_id     TEXT NOT NULL,
    story_id   TEXT NOT NULL,
    card_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    status     TEXT NOT NULL,
    started_at TEXT,
    ended_at   TEXT,
    position   INTEGER NOT NULL,
    detail     TEXT,
    PRIMARY KEY (run_id, story_id, card_id, name)
);

CREATE TABLE IF NOT EXISTS attempts (
    run_id       TEXT NOT NULL,
    story_id     TEXT NOT NULL,
    card_id      TEXT NOT NULL,
    phase        TEXT NOT NULL,
    n            INTEGER NOT NULL,
    status       TEXT NOT NULL,
    exit_code    INTEGER,
    duration     REAL,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    cost         REAL,
    prompt_path  TEXT,
    result_path  TEXT,
    stdout_path  TEXT,
    dispatch     TEXT NOT NULL,
    PRIMARY KEY (run_id, story_id, card_id, phase, n)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    run_id    TEXT NOT NULL,
    card_id   TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    workflow  TEXT NOT NULL,
    digest    TEXT NOT NULL,
    reason    TEXT NOT NULL CHECK (reason IN ('turn', 'parked', 'done', 'escalated')),
    agent     TEXT NOT NULL,
    saved_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, card_id, seq)
);

CREATE TABLE IF NOT EXISTS checkpoint_floors (
    run_id     TEXT NOT NULL,
    card_id    TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    phase      TEXT NOT NULL,
    loop       INTEGER NOT NULL,
    source_run TEXT NOT NULL,
    floor      INTEGER NOT NULL CHECK (floor >= 0),
    PRIMARY KEY (run_id, card_id, seq)
);

CREATE TABLE IF NOT EXISTS run_controls (
    run_id       TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    lease        TEXT NOT NULL,
    command      TEXT NOT NULL CHECK (command IN ('pause', 'cancel')),
    requested_at TEXT NOT NULL,
    handled_at   TEXT,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS run_leases (
    run_id       TEXT PRIMARY KEY,
    token        TEXT NOT NULL,
    pid          INTEGER NOT NULL,
    host         TEXT NOT NULL,
    acquired_at  TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    accepting    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS run_claims (
    key        TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    token      TEXT NOT NULL,
    claimed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_comments (
    run_id          TEXT NOT NULL,
    card_id         TEXT NOT NULL,
    key             TEXT PRIMARY KEY,
    body            TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('pending', 'posted', 'abandoned')),
    comment_id      TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    posted_at       TEXT
);
"""


BUSY_TIMEOUT_SECONDS = 30.0
"""How long a statement on the projection waits for a lock held by another
connection before raising `sqlite3.OperationalError: database is locked`.

It covers a reader in another process, such as `am status`, holding the
database briefly, and a second `am` process's short `BEGIN IMMEDIATE` write
transactions: a lease take-over, or one fenced journal line and row
(multi-process X4, X9)."""


_WAL_RETRY_FIRST_PAUSE = 0.05
"""Seconds `_enable_wal` waits after the first locked attempt; each later pause doubles."""

_WAL_RETRY_PAUSE_CAP = 0.5
"""The longest single pause `_enable_wal` takes between attempts."""


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switch `conn` to WAL mode, retrying while the database is locked.

    SQLite does not call the busy handler when this pragma meets another
    connection's RESERVED lock on a database still in rollback-journal mode; it
    fails at once with `database is locked`. So the pragma alone is retried,
    pausing 0.05 s and doubling up to 0.5 s, each pause clamped to the time left,
    until `BUSY_TIMEOUT_SECONDS` (read now, so tests can patch it) has passed
    since the first attempt. Then the last error is re-raised unchanged. Any
    other error propagates on the first attempt.
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_SECONDS
    pause = _WAL_RETRY_FIRST_PAUSE
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as error:
            if "database is locked" not in str(error):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(pause, remaining))
            pause = min(pause * 2, _WAL_RETRY_PAUSE_CAP)


_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("phases", "detail", "TEXT"),
    ("runs", "milestone_id", "TEXT"),
)
"""Columns added to a table after it first shipped, as (table, column, type).

`CREATE TABLE IF NOT EXISTS` leaves an existing table as it was, so a database
created before one of these columns existed would never get it. Each column
must also appear, last, in that table's `CREATE` in `_SCHEMA`, so a fresh and a
migrated database end up with the same column order."""


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Add each `_ADDED_COLUMNS` entry its table lacks, and touch nothing else.

    SQLite has no `ADD COLUMN IF NOT EXISTS`, so the column list is read first
    and `ALTER TABLE ... ADD COLUMN` runs only for a missing column. Nothing is
    caught: any SQLite error propagates unchanged.
    """
    for table, column, sql_type in _ADDED_COLUMNS:
        present = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


def open_db(root: Path) -> sqlite3.Connection:
    """Open the per-project projection, applying the schema idempotently.

    WAL mode is set before the schema so a reader never blocks the writer. The
    WAL switch is retried until `BUSY_TIMEOUT_SECONDS`, because SQLite does not
    call the busy handler for that pragma when another connection holds a write
    lock. Every `CREATE` is `IF NOT EXISTS`, so reopening an existing database never
    destroys what is already there. The only migration is additive:
    `_add_missing_columns` appends each column in `_ADDED_COLUMNS` that an older
    table lacks, as a nullable column. Existing rows keep their data and read
    the new column as NULL. It is a no-op on a database that already has the
    column, so opening the same database any number of times is safe.

    The connection may be used from any thread of the process that holds the
    run's lease, so `check_same_thread` is off; `Store` serialises that use
    behind its own lock. `BUSY_TIMEOUT_SECONDS` covers another process holding
    the database briefly; two processes never write one run, because every
    run write is fenced by the lease token (multi-process X4).
    """
    conn = sqlite3.connect(
        paths.project_db_path(root),
        timeout=BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    _enable_wal(conn)
    conn.executescript(_SCHEMA)
    _add_missing_columns(conn)
    conn.commit()
    return conn


JOURNAL_NAME = "journal.jsonl"

EventKind = Literal[
    "run_upsert", "story_upsert", "subtask_upsert", "phase_upsert", "attempt_upsert"
]
"""Every event is an upsert of one node of the §9 tree: a status transition is
the same node recorded again with a new status."""


class JournalError(RuntimeError):
    """The journal could not be read as an append-only log of this run."""


class MissingJournalError(JournalError):
    """There is no journal file for this run: a missing run, not an empty one."""


class CorruptJournalError(JournalError):
    """A journal line is not JSON. Names the file and the 1-based line number."""


class JournalLine(BaseModel):
    """The envelope around one journalled event.

    `extra="forbid"` for the same reason `models._Model` uses it: an envelope
    from an older schema must fail loudly rather than lose a coordinate.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(gt=0)
    ts: datetime
    run_id: str = Field(min_length=1)
    event: EventKind
    story: str | None = None
    card: str | None = None
    phase: str | None = None
    attempt: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


_EVENT_KINDS: frozenset[str] = frozenset(get_args(EventKind))


class _UnknownEventLine(BaseModel):
    """What a line with an unrecognised `event` must still carry: its `seq`.

    A newer `am` may journal an event kind this version's `EventKind` does not
    list. `Journal.read` skips such a line, but `last_seq` still counts it, so
    a later `append` never reuses a number already on disk. Every other field
    belongs to a schema this version does not know and is ignored.
    """

    model_config = ConfigDict(extra="ignore")

    seq: int = Field(gt=0)


class Journal:
    """Append-only JSONL log for one run: the truth the projection is built from.

    The threads of the process that holds a run's lease share one `Journal`.
    The highest sequence number on disk is read when the journal is opened and
    cached; a lock serialises appends from those threads. A process that takes
    the lease over calls `reseek`, because the previous owner may have appended
    after this journal was opened (multi-process X4).
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.path = paths.run_dir(run_id) / JOURNAL_NAME
        self._lock = threading.Lock()
        self._seq = self.last_seq()

    @classmethod
    def _for_reading(cls, run_id: str) -> "Journal":
        """Another run's journal, opened only to be read.

        `__init__` scans the file for its highest `seq` with the default
        `read()`, which would raise on the very torn tail
        `read(ignore_torn_tail=True)` exists to tolerate, and `paths.run_dir`
        would create a directory for a run that never existed. This instance
        is never appended to, so it needs neither: `_seq` stays 0.
        """
        journal = cls.__new__(cls)
        journal.run_id = run_id
        journal.path = paths.data_dir() / "runs" / run_id / JOURNAL_NAME
        journal._lock = threading.Lock()
        journal._seq = 0
        return journal

    def last_seq(self) -> int:
        """Highest sequence number already on disk, or 0 for a fresh journal.

        Counts lines `read` skips for an unrecognised `event` too: they are on
        disk, so `append` must never number a line with one of their `seq`s.
        """
        if not self.path.exists():
            return 0
        return max((seq for seq, _ in self._scan()), default=0)

    def reseek(self) -> None:
        """Re-read the highest `seq` on disk into the cache, under the append lock.

        Called by `Store.take_lease` once the lease is this process's: a stuck
        previous owner may have appended lines after `__init__` cached `_seq`,
        and the new owner must number its first line after them.
        """
        with self._lock:
            self._seq = self.last_seq()

    def _scan(
        self, *, ignore_torn_tail: bool = False
    ) -> list[tuple[int, JournalLine | None]]:
        """Every non-blank line's `seq`, in file order, with its `JournalLine`.

        The `JournalLine` is `None` for a line whose `event` is a string this
        version's `EventKind` does not list: `read` skips it, but `last_seq`
        still counts its `seq`. Such a line must still carry a positive `seq`;
        everything else on it is ignored. Any other line is validated strictly
        as a `JournalLine`, so a missing or non-string `event`, a non-object
        line, or an unknown envelope key on a known event still raises.

        Blank lines are skipped: a crash between the write and the flush can
        leave one. Anything else that is not JSON is an error naming the line.

        `ignore_torn_tail` is for reading *another* run's journal, which a
        process elsewhere may be appending to right now: a final line that is
        not JSON and has no trailing newline is that append in flight, and is
        skipped. A non-JSON line that is newline-terminated, or that is not the
        last, is still `CorruptJournalError`. Lines are ASCII (`json.dumps`
        escapes), so a cut can never split a character.
        """
        if not self.path.exists():
            raise MissingJournalError(
                f"no journal for run {self.run_id!r} at {self.path}"
            )
        with self.path.open(encoding="utf-8") as handle:
            texts = handle.readlines()
        scanned: list[tuple[int, JournalLine | None]] = []
        for number, text in enumerate(texts, start=1):
            if not text.strip():
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as error:
                torn = number == len(texts) and not text.endswith("\n")
                if ignore_torn_tail and torn:
                    continue
                raise CorruptJournalError(
                    f"{self.path}:{number}: line is not JSON: {error}"
                ) from error
            if (
                isinstance(record, dict)
                and isinstance(record.get("event"), str)
                and record["event"] not in _EVENT_KINDS
            ):
                skipped = _UnknownEventLine.model_validate(record)
                scanned.append((skipped.seq, None))
                continue
            line = JournalLine.model_validate(record)
            scanned.append((line.seq, line))
        return scanned

    def read(self, *, ignore_torn_tail: bool = False) -> list[JournalLine]:
        """Every line whose event this version knows, validated, in `seq` order.

        A line whose `event` is a string outside `EventKind` (written by a
        newer `am`) is skipped rather than raising; see `_scan` for what is
        still an error and for `ignore_torn_tail`. Unknown keys inside a known
        line's `payload` pass through untouched: `replay` judges payloads.
        """
        lines = [
            line
            for _, line in self._scan(ignore_torn_tail=ignore_torn_tail)
            if line is not None
        ]
        lines.sort(key=lambda line: line.seq)
        return lines

    def append(
        self,
        event: EventKind,
        payload: dict[str, Any],
        *,
        story: str | None = None,
        card: str | None = None,
        phase: str | None = None,
        attempt: int | None = None,
    ) -> JournalLine:
        """Append one line, flushed and fsynced before returning.

        Only the process holding the run's lease writes it: after a take-over
        the new owner writes, and the old owner's writes are fenced out by the
        lease token (multi-process X4). The sequence number is cached when the
        journal is opened (and re-read by `reseek` on a take-over), not re-read
        from disk on each append, and the lock is held from numbering the line
        until it is fsynced, so the threads of that process never share a
        number or interleave their bytes. The cached number
        advances once the line has been written and flushed to the file; if
        validation, the open or the write raises, the next append retries the
        same number, and if only the fsync raises the number stays spent, so
        no seq is ever repeated on disk. The lock is released either way.
        """
        with self._lock:
            seq = self._seq + 1
            line = JournalLine(
                seq=seq,
                ts=datetime.now(timezone.utc),
                run_id=self.run_id,
                event=event,
                story=story,
                card=card,
                phase=phase,
                attempt=attempt,
                payload=payload,
            )
            text = json.dumps(line.model_dump(mode="json"), sort_keys=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
                handle.flush()
                # The line is in the file now, fsynced or not: spend its number
                # so a retry after a failed fsync cannot repeat it on disk.
                self._seq = seq
                os.fsync(handle.fileno())
            return line


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _text(value: Path | None) -> str | None:
    return None if value is None else str(value)


def _upsert(
    items: list[Any], key: str, node: Any, children: str | None
) -> Any:
    """Replace the sibling with the same key, keeping its children, or append."""
    for index, existing in enumerate(items):
        if getattr(existing, key) == getattr(node, key):
            if children is not None:
                node = node.model_copy(update={children: getattr(existing, children)})
            items[index] = node
            return node
    items.append(node)
    return node


def _find(items: list[Any], key: str, value: str | None, what: str, seq: int) -> Any:
    for existing in items:
        if getattr(existing, key) == value:
            return existing
    raise JournalError(
        f"journal line {seq} names {what} {value!r}, which no earlier line created"
    )


def replay(lines: Iterable[JournalLine]) -> models.Run:
    """Fold journal lines, in sequence order, back into the §9 tree.

    Nothing here is defensive: a line that fails `models` validation raises the
    `pydantic.ValidationError` straight out, because an old-schema line has to
    fail loudly rather than quietly drop a field from the projection.
    """
    run: models.Run | None = None

    for line in sorted(lines, key=lambda item: item.seq):
        if line.event == "run_upsert":
            fresh = models.Run.model_validate(line.payload)
            run = fresh if run is None else fresh.model_copy(
                update={"stories": run.stories}
            )
            continue

        if run is None:
            raise JournalError(
                f"journal line {line.seq} is a {line.event} but no run_upsert"
                " preceded it: the head of the journal is missing"
            )

        if line.event == "story_upsert":
            _upsert(
                run.stories,
                "card_id",
                models.StoryRun.model_validate(line.payload),
                "subtasks",
            )
            continue

        story = _find(run.stories, "card_id", line.story, "story", line.seq)

        if line.event == "subtask_upsert":
            _upsert(
                story.subtasks,
                "card_id",
                models.SubtaskRun.model_validate(line.payload),
                "phases",
            )
            continue

        subtask = _find(story.subtasks, "card_id", line.card, "subtask", line.seq)

        if line.event == "phase_upsert":
            _upsert(
                subtask.phases,
                "name",
                models.PhaseRun.model_validate(line.payload),
                "attempts",
            )
            continue

        phase = _find(subtask.phases, "name", line.phase, "phase", line.seq)
        _upsert(phase.attempts, "n", models.Attempt.model_validate(line.payload), None)

    if run is None:
        raise JournalError("journal contains no run_upsert line")
    return run


class RunSummary(BaseModel):
    """One row of the shared `runs` table, without the tree hanging off it.

    A `models.Run` would be a lie here: its `stories` list would always be empty
    because `runs` is the only table read for it. The fields are the run's
    identity and nothing else, and they go through pydantic for the same reason
    `load_run` does -- a projection that drifted from `models` must fail loudly
    rather than print half a history.

    `milestone_id` is the `runs` column: null on a `--card` run and on a row
    written before the column existed. `card_id` is not stored anywhere on the
    run row: it is the single subtask a `task` (`--card`) run records, and null
    for any other workflow or before that subtask row is written. Both default
    to `None`, so they are additive: no older key changed.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    workflow: str
    repo_dir: Path
    base_branch: str
    branch_prefix: str
    status: models.Status
    started_at: datetime | None = None
    milestone_id: str | None = None
    card_id: str | None = None


def list_runs(conn: sqlite3.Connection) -> list[RunSummary]:
    """Every run recorded in this project's projection, newest first.

    Takes a connection rather than a root so one caller can list the history and
    then load a run's tree over the same connection, and close it once. The
    connection comes from `open_db(root)`; there is no second database.

    `started_at DESC` puts a NULL start time last (SQLite orders NULL below every
    value, so descending sends it to the end) and the id breaks a tie, which run
    ids minted at second resolution really do produce.

    `card_id` is derived, not stored: a `task` run (`am run --card`) records one
    story and one subtask, and that subtask's card is the run's card. Any other
    workflow -- a milestone run records many subtasks -- gets NULL. Should a
    `task` run ever hold several subtask rows, the lowest `position`, then the
    lowest card id, wins, so the answer is stable rather than an error.
    """
    rows = conn.execute(
        "SELECT runs.id, runs.workflow, runs.repo_dir, runs.base_branch,"
        " runs.branch_prefix, runs.status, runs.started_at, runs.milestone_id,"
        " CASE WHEN runs.workflow = 'task' THEN ("
        "   SELECT subtasks.card_id FROM subtasks"
        "    WHERE subtasks.run_id = runs.id"
        "    ORDER BY subtasks.position, subtasks.card_id LIMIT 1"
        " ) END AS card_id"
        " FROM runs ORDER BY runs.started_at DESC, runs.id DESC"
    ).fetchall()
    return [RunSummary.model_validate(dict(row)) for row in rows]


def latest_run_id(conn: sqlite3.Connection) -> str | None:
    """The most recent run of this project, or `None` if it has never been run.

    Derived from `list_runs` rather than from a second `ORDER BY`, so "most
    recent" can never mean two different things in two commands.
    """
    summaries = list_runs(conn)
    return summaries[0].id if summaries else None


def load_run(conn: sqlite3.Connection, run_id: str) -> models.Run | None:
    """Assemble one run's projection back into the §9 tree, or `None` if absent.

    A free function over a connection, because the reader that needs it -- the
    `status` command -- has no `Journal` and must not create one: `Journal`
    derives its path from `paths.run_dir`, which creates the directory, so
    looking up a run that does not exist through `Store.open` would leave an
    artifact directory behind for a run nobody ever started.

    Every value goes back through the `models` validators, so a projection that
    drifted from the schema fails here rather than downstream.
    """
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None

    run = models.Run(
        id=row["id"],
        workflow=row["workflow"],
        repo_dir=row["repo_dir"],
        base_branch=row["base_branch"],
        branch_prefix=row["branch_prefix"],
        status=row["status"],
        started_at=row["started_at"],
        config=json.loads(row["config"]),
        milestone_id=row["milestone_id"],
    )

    for story_row in conn.execute(
        "SELECT * FROM stories WHERE run_id = ? ORDER BY position", (run_id,)
    ).fetchall():
        story = models.StoryRun(
            card_id=story_row["card_id"],
            title=story_row["title"],
            level=story_row["level"],
            status=story_row["status"],
            tip_branch=story_row["tip_branch"],
        )
        run.stories.append(story)

        for subtask_row in conn.execute(
            "SELECT * FROM subtasks WHERE run_id = ? AND story_id = ? ORDER BY position",
            (run_id, story.card_id),
        ).fetchall():
            subtask = models.SubtaskRun(
                card_id=subtask_row["card_id"],
                branch=subtask_row["branch"],
                base_branch=subtask_row["base_branch"],
                status=subtask_row["status"],
                worktree_path=subtask_row["worktree_path"],
            )
            story.subtasks.append(subtask)

            for phase_row in conn.execute(
                "SELECT * FROM phases WHERE run_id = ? AND story_id = ?"
                " AND card_id = ? ORDER BY position",
                (run_id, story.card_id, subtask.card_id),
            ).fetchall():
                phase = models.PhaseRun(
                    name=phase_row["name"],
                    kind=phase_row["kind"],
                    status=phase_row["status"],
                    started_at=phase_row["started_at"],
                    ended_at=phase_row["ended_at"],
                    detail=phase_row["detail"],
                )
                subtask.phases.append(phase)

                for attempt_row in conn.execute(
                    "SELECT * FROM attempts WHERE run_id = ? AND story_id = ?"
                    " AND card_id = ? AND phase = ? ORDER BY n",
                    (run_id, story.card_id, subtask.card_id, phase.name),
                ).fetchall():
                    phase.attempts.append(
                        models.Attempt(
                            n=attempt_row["n"],
                            dispatch=json.loads(attempt_row["dispatch"]),
                            status=attempt_row["status"],
                            exit_code=attempt_row["exit_code"],
                            duration=attempt_row["duration"],
                            tokens_in=attempt_row["tokens_in"],
                            tokens_out=attempt_row["tokens_out"],
                            cost=attempt_row["cost"],
                            prompt_path=attempt_row["prompt_path"],
                            result_path=attempt_row["result_path"],
                            stdout_path=attempt_row["stdout_path"],
                        )
                    )

    return run


def run_status(conn: sqlite3.Connection, run_id: str) -> str | None:
    """`runs.status` of `run_id`, or `None` if the run was never recorded.

    A free function over a connection, like `load_run`, for a reader in
    another process that needs the status alone (`am pause`, `am resume`).
    """
    row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    return None if row is None else row["status"]


@dataclass(frozen=True)
class TurnFloor:
    """The turn identity saved beside an agent-phase checkpoint (exactly-once 1.1).

    One row of `checkpoint_floors`, keyed like its `checkpoints` row. Row-only
    and outside the journal: nothing journals it and `rebuild_from_journal`
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
    part of the §9 tree: no journal line records it and `rebuild_from_journal`
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


@dataclass(frozen=True)
class LeaseRow:
    """The running process's claim on a run: a row of `run_leases` (live control C2).

    Row-only and outside the journal, like `Checkpoint`. `accepting` is a real
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
    is_live: Callable[[LeaseRow], bool],
    run_id: str | None = None,
) -> list[tuple[str, LeaseRow]]:
    """The keys of `keys`, in order, that another run's live lease holds.

    Read-only. A key conflicts when its `run_claims` row names a run other
    than `run_id`, that run's `run_leases` row still carries the claim's
    token, and `is_live` says that lease row is live. `is_live` is injected so
    this module never imports `control`; it is asked only about a claim whose
    token still matches its run's lease.
    """
    conflicts: list[tuple[str, LeaseRow]] = []
    for key in keys:
        claim = conn.execute(
            "SELECT run_id, token FROM run_claims WHERE key = ?", (key,)
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


COMMENT_ATTEMPTS = 3
"""Failed posts after which a `board_comments` row is `abandoned` (board-comments
B7). The warning that names an abandoned row belongs to `comments.py`."""


@dataclass(frozen=True)
class CommentRow:
    """One queued outcome comment: a row of `board_comments` (board-comments B6).

    Row-only and outside the journal, like `Checkpoint`. `key` is the
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


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One write transaction that holds the database write lock from `BEGIN`.

    Python's `sqlite3` in legacy transaction mode opens an implicit
    transaction on the first DML statement, and `BEGIN` inside one raises; so
    any open implicit transaction is committed first. The body then runs under
    `BEGIN IMMEDIATE` and is committed on a normal exit, or rolled back and
    the exception re-raised on any error, leaving no partial rows.
    """
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def add_control(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    lease: str,
    command: str,
    requested_at: datetime,
) -> ControlRow:
    """Insert the next control request of `run_id`, addressed to `lease`.

    `seq` is 0 for the run's first request and one past the highest after
    that. Does not commit: run it inside `immediate` so the `MAX(seq)` read
    and the insert are one locked write. An unknown `command` is refused by
    the table's `CHECK` as `sqlite3.IntegrityError`; that is the only guard.
    """
    highest = conn.execute(
        "SELECT MAX(seq) FROM run_controls WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    seq = 0 if highest is None else highest + 1
    conn.execute(
        "INSERT INTO run_controls (run_id, seq, lease, command, requested_at,"
        " handled_at) VALUES (?, ?, ?, ?, ?, NULL)",
        (run_id, seq, lease, command, _iso(requested_at)),
    )
    return ControlRow(
        run_id=run_id,
        seq=seq,
        lease=lease,
        command=command,
        requested_at=requested_at,
        handled_at=None,
    )


class Store:
    """The two stores of D5, bound together by the write ordering of §9.

    Every `record_*` appends the journal line first and writes the row second.
    There is deliberately no public method that writes a tree row on its own.
    The exceptions are `checkpoints` (pygents spec §6), `run_controls` and
    `run_leases` (live control C1/C2) and `board_comments` (board-comments
    B6): row-only tables outside the journal.
    Their methods write rows and never touch the journal, and
    `rebuild_from_journal` leaves those rows alone.

    The threads of the process holding a run's lease share one `Store`. A
    single re-entrant lock serialises every use of the shared connection. Each
    `record_*` holds it across the journal append and the row write, so the two
    are one critical section and journal order equals row order; `close`,
    `load_run` and `rebuild_from_journal` hold it too. Once `take_lease` has
    bound a token, every run write also runs inside `_fenced()`, one
    `BEGIN IMMEDIATE` transaction that first checks the token still holds the
    lease (multi-process X4). The lock never covers the caller's own work,
    only the append and the row write.
    """

    def __init__(self, conn: sqlite3.Connection, journal: Journal) -> None:
        self._conn = conn
        self._journal = journal
        self._lock = threading.RLock()
        self._token: str | None = None
        self._in_fence = False

    @classmethod
    def open(cls, root: Path, run_id: str) -> "Store":
        return cls(open_db(root), Journal(run_id))

    @property
    def run_id(self) -> str:
        return self._journal.run_id

    @property
    def journal(self) -> Journal:
        return self._journal

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _fenced(self) -> Iterator[None]:
        """Run one write as a single transaction fenced by the bound token (X4, X9).

        With no token bound this is a no-op and the write commits as it always
        has. Otherwise it opens `immediate`, and if this run's lease row is gone
        or carries another token it raises `LeaseLostError` before the body
        runs, so nothing is appended or written. While the body runs,
        `_in_fence` makes `_commit` a no-op: the journal append and the row
        write commit together when `immediate` exits, or roll back on a raise.
        Callers already hold `self._lock`.
        """
        if self._token is None:
            yield
            return
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT * FROM run_leases WHERE run_id = ?", (self.run_id,)
            ).fetchone()
            if row is None or row["token"] != self._token:
                raise LeaseLostError(
                    self.run_id, None if row is None else _lease_from_row(row)
                )
            self._in_fence = True
            try:
                yield
            finally:
                self._in_fence = False

    def _commit(self) -> None:
        """Commit a row write, unless a fence will commit it with its journal line."""
        if not self._in_fence:
            self._conn.commit()

    # -- recording ---------------------------------------------------------
    #
    # Each method holds the store lock, and the fence of the bound lease token,
    # across its whole body: the journal line is appended first and the row
    # written second (§9), with no other record able to land in between. A
    # store whose lease was lost raises `LeaseLostError` before appending. If
    # the row write raises, the line stays on disk, the exception propagates
    # unchanged and the `with` block releases the lock.

    def record_run(self, run: models.Run) -> JournalLine:
        with self._lock, self._fenced():
            if run.id != self.run_id:
                raise ValueError(
                    f"store is bound to run {self.run_id!r} but was handed run"
                    f" {run.id!r}: the row is keyed by the store's id while the"
                    " journal payload keeps the model's, so the two stores would"
                    " disagree about which run this is"
                )
            line = self._journal.append(
                "run_upsert", run.model_dump(mode="json", exclude={"stories"})
            )
            self._write_run_row(self.run_id, run)
            return line

    def record_story(self, story: models.StoryRun) -> JournalLine:
        with self._lock, self._fenced():
            line = self._journal.append(
                "story_upsert",
                story.model_dump(mode="json", exclude={"subtasks"}),
                story=story.card_id,
            )
            self._write_story_row(self.run_id, story)
            return line

    def record_subtask(self, story_id: str, subtask: models.SubtaskRun) -> JournalLine:
        with self._lock, self._fenced():
            line = self._journal.append(
                "subtask_upsert",
                subtask.model_dump(mode="json", exclude={"phases"}),
                story=story_id,
                card=subtask.card_id,
            )
            self._write_subtask_row(self.run_id, story_id, subtask)
            return line

    def record_phase(
        self, story_id: str, card_id: str, phase: models.PhaseRun
    ) -> JournalLine:
        with self._lock, self._fenced():
            line = self._journal.append(
                "phase_upsert",
                phase.model_dump(mode="json", exclude={"attempts"}),
                story=story_id,
                card=card_id,
                phase=phase.name,
            )
            self._write_phase_row(self.run_id, story_id, card_id, phase)
            return line

    def record_attempt(
        self, story_id: str, card_id: str, phase_name: str, attempt: models.Attempt
    ) -> JournalLine:
        with self._lock, self._fenced():
            line = self._journal.append(
                "attempt_upsert",
                attempt.model_dump(mode="json"),
                story=story_id,
                card=card_id,
                phase=phase_name,
                attempt=attempt.n,
            )
            self._write_attempt_row(
                self.run_id, story_id, card_id, phase_name, attempt
            )
            return line

    # -- row writers -------------------------------------------------------
    #
    # `position` is assigned from the sibling count at insert time and is never
    # touched by the conflict clause, so recording a node twice updates it in
    # place and leaves the order it was first seen in.

    def _write_run_row(self, run_id: str, run: models.Run) -> None:
        self._conn.execute(
            """
            INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,
                              status, started_at, config, milestone_id)
            VALUES (:id, :workflow, :repo_dir, :base_branch, :branch_prefix,
                    :status, :started_at, :config, :milestone_id)
            ON CONFLICT(id) DO UPDATE SET
                workflow=excluded.workflow,
                repo_dir=excluded.repo_dir,
                base_branch=excluded.base_branch,
                branch_prefix=excluded.branch_prefix,
                status=excluded.status,
                started_at=excluded.started_at,
                config=excluded.config,
                milestone_id=excluded.milestone_id
            """,
            {
                "id": run_id,
                "workflow": run.workflow,
                "repo_dir": str(run.repo_dir),
                "base_branch": run.base_branch,
                "branch_prefix": run.branch_prefix,
                "status": run.status,
                "started_at": _iso(run.started_at),
                "config": json.dumps(run.config.model_dump(mode="json"), sort_keys=True),
                "milestone_id": run.milestone_id,
            },
        )
        self._commit()

    def _write_story_row(self, run_id: str, story: models.StoryRun) -> None:
        self._conn.execute(
            """
            INSERT INTO stories (run_id, card_id, title, level, status, tip_branch, position)
            VALUES (:run_id, :card_id, :title, :level, :status, :tip_branch,
                    (SELECT COUNT(*) FROM stories WHERE run_id = :run_id))
            ON CONFLICT(run_id, card_id) DO UPDATE SET
                title=excluded.title,
                level=excluded.level,
                status=excluded.status,
                tip_branch=excluded.tip_branch
            """,
            {
                "run_id": run_id,
                "card_id": story.card_id,
                "title": story.title,
                "level": story.level,
                "status": story.status,
                "tip_branch": story.tip_branch,
            },
        )
        self._commit()

    def _write_subtask_row(
        self, run_id: str, story_id: str, subtask: models.SubtaskRun
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO subtasks (run_id, story_id, card_id, branch, base_branch,
                                  status, worktree_path, position)
            VALUES (:run_id, :story_id, :card_id, :branch, :base_branch,
                    :status, :worktree_path,
                    (SELECT COUNT(*) FROM subtasks
                      WHERE run_id = :run_id AND story_id = :story_id))
            ON CONFLICT(run_id, story_id, card_id) DO UPDATE SET
                branch=excluded.branch,
                base_branch=excluded.base_branch,
                status=excluded.status,
                worktree_path=excluded.worktree_path
            """,
            {
                "run_id": run_id,
                "story_id": story_id,
                "card_id": subtask.card_id,
                "branch": subtask.branch,
                "base_branch": subtask.base_branch,
                "status": subtask.status,
                "worktree_path": _text(subtask.worktree_path),
            },
        )
        self._commit()

    def _write_phase_row(
        self, run_id: str, story_id: str, card_id: str, phase: models.PhaseRun
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO phases (run_id, story_id, card_id, name, kind, status,
                                started_at, ended_at, detail, position)
            VALUES (:run_id, :story_id, :card_id, :name, :kind, :status,
                    :started_at, :ended_at, :detail,
                    (SELECT COUNT(*) FROM phases
                      WHERE run_id = :run_id AND story_id = :story_id
                        AND card_id = :card_id))
            ON CONFLICT(run_id, story_id, card_id, name) DO UPDATE SET
                kind=excluded.kind,
                status=excluded.status,
                started_at=excluded.started_at,
                ended_at=excluded.ended_at,
                detail=excluded.detail
            """,
            {
                "run_id": run_id,
                "story_id": story_id,
                "card_id": card_id,
                "name": phase.name,
                "kind": phase.kind,
                "status": phase.status,
                "started_at": _iso(phase.started_at),
                "ended_at": _iso(phase.ended_at),
                "detail": phase.detail,
            },
        )
        self._commit()

    def _write_attempt_row(
        self,
        run_id: str,
        story_id: str,
        card_id: str,
        phase_name: str,
        attempt: models.Attempt,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO attempts (run_id, story_id, card_id, phase, n, status,
                                  exit_code, duration, tokens_in, tokens_out, cost,
                                  prompt_path, result_path, stdout_path, dispatch)
            VALUES (:run_id, :story_id, :card_id, :phase, :n, :status,
                    :exit_code, :duration, :tokens_in, :tokens_out, :cost,
                    :prompt_path, :result_path, :stdout_path, :dispatch)
            ON CONFLICT(run_id, story_id, card_id, phase, n) DO UPDATE SET
                status=excluded.status,
                exit_code=excluded.exit_code,
                duration=excluded.duration,
                tokens_in=excluded.tokens_in,
                tokens_out=excluded.tokens_out,
                cost=excluded.cost,
                prompt_path=excluded.prompt_path,
                result_path=excluded.result_path,
                stdout_path=excluded.stdout_path,
                dispatch=excluded.dispatch
            """,
            {
                "run_id": run_id,
                "story_id": story_id,
                "card_id": card_id,
                "phase": phase_name,
                "n": attempt.n,
                "status": attempt.status,
                "exit_code": attempt.exit_code,
                "duration": attempt.duration,
                "tokens_in": attempt.tokens_in,
                "tokens_out": attempt.tokens_out,
                "cost": attempt.cost,
                "prompt_path": _text(attempt.prompt_path),
                "result_path": _text(attempt.result_path),
                "stdout_path": _text(attempt.stdout_path),
                "dispatch": json.dumps(
                    attempt.dispatch.model_dump(mode="json"), sort_keys=True
                ),
            },
        )
        self._commit()

    # -- reading -------------------------------------------------------------

    def load_run(self, run_id: str) -> models.Run | None:
        """The module-level `load_run` over this store's own connection.

        Kept as a method because `rebuild_from_journal` and every existing caller
        already hold a `Store`; the free function is what a reader without a run
        id uses. Holds the store lock so a read on the shared connection never
        interleaves with a write's execute or commit.
        """
        with self._lock:
            return load_run(self._conn, run_id)

    # -- checkpoints ---------------------------------------------------------
    #
    # A row-only table outside the journal (pygents spec §6, G10): nothing here
    # calls `self._journal`. Each method holds the store lock across its whole
    # body, so `seq` is read and the row written with no other write between.

    def save_checkpoint(
        self,
        card_id: str,
        *,
        workflow: str,
        digest: str,
        reason: str,
        agent: dict,
        saved_at: datetime,
        floor: TurnFloor | None = None,
    ) -> Checkpoint:
        """Write the next checkpoint of `card_id` under this store's run.

        `seq` is 0 for the card's first row in this run and one past the
        highest after that. With `floor`, a `checkpoint_floors` row keyed by
        the same `(run_id, card_id, seq)` is written in the same transaction,
        under the same fence, with one commit. Any `sqlite3.Error` from either
        insert -- an unknown `reason` refused by the `checkpoints` CHECK, a
        negative floor refused by the `checkpoint_floors` CHECK -- rolls back
        both rows and propagates unchanged, and no `seq` is spent.
        """
        with self._lock, self._fenced():
            text = json.dumps(agent, sort_keys=True)
            highest = self._conn.execute(
                "SELECT MAX(seq) FROM checkpoints WHERE run_id = ? AND card_id = ?",
                (self.run_id, card_id),
            ).fetchone()[0]
            seq = 0 if highest is None else highest + 1
            try:
                self._conn.execute(
                    "INSERT INTO checkpoints (run_id, card_id, seq, workflow, digest,"
                    " reason, agent, saved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.run_id,
                        card_id,
                        seq,
                        workflow,
                        digest,
                        reason,
                        text,
                        _iso(saved_at),
                    ),
                )
                if floor is not None:
                    self._conn.execute(
                        "INSERT INTO checkpoint_floors (run_id, card_id, seq, phase,"
                        " loop, source_run, floor) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            self.run_id,
                            card_id,
                            seq,
                            floor.phase,
                            floor.loop,
                            floor.source_run,
                            floor.floor,
                        ),
                    )
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
            return Checkpoint(
                run_id=self.run_id,
                card_id=card_id,
                seq=seq,
                workflow=workflow,
                digest=digest,
                reason=reason,
                agent=json.loads(text),
                saved_at=saved_at,
                floor=floor,
            )

    def latest_checkpoint(self, card_id: str) -> Checkpoint | None:
        """The highest-`seq` checkpoint of `card_id` in this store's run, any reason."""
        with self._lock:
            row = self._conn.execute(
                _CHECKPOINT_SELECT
                + " WHERE c.run_id = ? AND c.card_id = ?"
                " ORDER BY c.seq DESC LIMIT 1",
                (self.run_id, card_id),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)

    def latest_turn_checkpoint(self, card_id: str) -> Checkpoint | None:
        """The highest-`seq` `turn` checkpoint of `card_id` in this store's run.

        A phase escalation's closing `escalated` row holds no turn
        (`runtime_engine.pending_phase`); the turn the failing phase ran in
        is the newest `turn` row, saved by `BEFORE_TURN` before it ran. A
        milestone resume rewinds to it (card 54e4ec29).
        """
        with self._lock:
            row = self._conn.execute(
                _CHECKPOINT_SELECT
                + " WHERE c.run_id = ? AND c.card_id = ?"
                " AND c.reason = 'turn' ORDER BY c.seq DESC LIMIT 1",
                (self.run_id, card_id),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)

    def latest_open_checkpoint(self, card_id: str, workflow: str) -> Checkpoint | None:
        """The newest open checkpoint of `card_id` for `workflow`, across every run.

        The card's newest row in any run and any workflow decides first: if it
        is `done`, or it belongs to a run whose status is `cancelled` (live
        control C9), the card is closed and this returns `None`. Otherwise it
        is the newest `turn`/`parked`/`escalated` row of `workflow` that does
        not belong to a cancelled run, or `None`. A checkpoint whose run has no
        `runs` row counts as not cancelled. "Newest" is `saved_at` descending,
        then `seq` descending.
        """
        with self._lock:
            newest = self._conn.execute(
                "SELECT c.reason, r.status FROM checkpoints c"
                " LEFT JOIN runs r ON r.id = c.run_id"
                " WHERE c.card_id = ?"
                " ORDER BY c.saved_at DESC, c.seq DESC LIMIT 1",
                (card_id,),
            ).fetchone()
            if (
                newest is None
                or newest["reason"] == "done"
                or newest["status"] == "cancelled"
            ):
                return None
            row = self._conn.execute(
                _CHECKPOINT_SELECT
                + " WHERE c.card_id = ? AND c.workflow = ?"
                " AND c.reason IN ('turn', 'parked', 'escalated')"
                " AND c.run_id NOT IN (SELECT id FROM runs WHERE status = 'cancelled')"
                " ORDER BY c.saved_at DESC, c.seq DESC LIMIT 1",
                (card_id, workflow),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)

    # -- board comment outbox ------------------------------------------------
    #
    # A row-only table outside the journal (board-comments B6, B9): nothing
    # here calls `self._journal`, and `rebuild_from_journal` leaves the rows
    # alone. Every writer holds the store lock and the fence of the bound
    # lease token, like `save_checkpoint`. Posting to the board is not this
    # module's job: `comments.py` drains the outbox through `board.py`.

    def enqueue_comment(
        self,
        *,
        run_id: str,
        card_id: str,
        key: str,
        body: str,
        now: datetime,
    ) -> bool:
        """Queue `body` for `card_id` under `key`, once (B9).

        True when a `pending` row was inserted; False when `key` already had a
        row, which is left exactly as it was, whatever its state. Only the key
        collision is ignored (`ON CONFLICT(key) DO NOTHING`, not `OR IGNORE`):
        a NULL body or any other refused value raises `sqlite3.IntegrityError`
        and rolls back.
        """
        with self._lock, self._fenced():
            try:
                cursor = self._conn.execute(
                    "INSERT INTO board_comments (run_id, card_id, key, body, state,"
                    " comment_id, failed_attempts, created_at, posted_at)"
                    " VALUES (?, ?, ?, ?, 'pending', NULL, 0, ?, NULL)"
                    " ON CONFLICT(key) DO NOTHING",
                    (run_id, card_id, key, body, _iso(now)),
                )
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
            return cursor.rowcount == 1

    def pending_comments(
        self,
        run_id: str | None = None,
        card_ids: Iterable[str] | None = None,
    ) -> list[CommentRow]:
        """Every `pending` row, oldest `created_at` first, then insertion order.

        Each given filter narrows the result and they are ANDed; with neither,
        every pending row of every run is returned. `card_ids` matches across
        runs, which is what a relaunch needs; an empty `card_ids` matches
        nothing.
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
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM board_comments WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at, rowid",
                params,
            ).fetchall()
            return [_comment_from_row(row) for row in rows]

    def mark_comment_posted(self, key: str, comment_id: str, now: datetime) -> None:
        """Record that `key`'s body is on the board as `comment_id`.

        The row leaves `pending_comments`. An unknown `key` changes nothing.
        """
        with self._lock, self._fenced():
            try:
                self._conn.execute(
                    "UPDATE board_comments SET state = 'posted', comment_id = ?,"
                    " posted_at = ? WHERE key = ?",
                    (comment_id, _iso(now), key),
                )
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise

    def record_comment_failure(self, key: str) -> int:
        """Count one failed post of `key` and return the new `failed_attempts`.

        A `pending` row reaching `COMMENT_ATTEMPTS` becomes `abandoned` and
        leaves `pending_comments`; a row already `posted` keeps its state. No
        warning is emitted here. An unknown `key` changes nothing and gives 0.
        """
        with self._lock, self._fenced():
            try:
                self._conn.execute(
                    "UPDATE board_comments SET failed_attempts = failed_attempts + 1,"
                    " state = CASE WHEN state = 'pending' AND failed_attempts + 1 >= ?"
                    " THEN 'abandoned' ELSE state END WHERE key = ?",
                    (COMMENT_ATTEMPTS, key),
                )
                row = self._conn.execute(
                    "SELECT failed_attempts FROM board_comments WHERE key = ?", (key,)
                ).fetchone()
                self._commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
            return 0 if row is None else row["failed_attempts"]

    # -- leases, claims and control requests -----------------------------------
    #
    # Row-only tables outside the journal (live control C2, multi-process X5):
    # nothing here calls `self._journal`, and `rebuild_from_journal` leaves the
    # rows alone. `take_lease` is the only check-and-set; every other method
    # touches only the rows whose token matches, and any other token is a
    # silent no-op.

    def take_lease(
        self,
        *,
        token: str,
        pid: int,
        host: str,
        now: datetime,
        is_live: Callable[[LeaseRow], bool],
        claims: Iterable[str] = (),
    ) -> LeaseTake:
        """Take this run's lease under `token`, with every key of `claims`, atomically.

        One `BEGIN IMMEDIATE` transaction (X5, X9): a live lease under another
        token raises `LeaseHeldError`; otherwise that row, or `None`, is the
        `displaced` one. Then the first key another run holds under a live
        lease raises `ClaimHeldError`. Only then are the lease (window open)
        and every claim upserted and committed. Any raise rolls all of it
        back and leaves the bound token as it was. On success the store is
        bound to `token` and the journal re-reads its highest `seq`.
        """
        keys = list(claims)
        with self._lock:
            with immediate(self._conn):
                current = read_lease(self._conn, self.run_id)
                if current is not None and current.token != token and is_live(current):
                    raise LeaseHeldError(current)
                conflicts = claim_conflicts(
                    self._conn, keys, is_live=is_live, run_id=self.run_id
                )
                if conflicts:
                    key, holder = conflicts[0]
                    raise ClaimHeldError(key, holder)
                self._conn.execute(
                    "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                    " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, 1)"
                    " ON CONFLICT(run_id) DO UPDATE SET"
                    " token=excluded.token, pid=excluded.pid, host=excluded.host,"
                    " acquired_at=excluded.acquired_at,"
                    " heartbeat_at=excluded.heartbeat_at, accepting=1",
                    (self.run_id, token, pid, host, _iso(now), _iso(now)),
                )
                for key in keys:
                    self._conn.execute(
                        "INSERT INTO run_claims (key, run_id, token, claimed_at)"
                        " VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                        " run_id=excluded.run_id, token=excluded.token,"
                        " claimed_at=excluded.claimed_at",
                        (key, self.run_id, token, _iso(now)),
                    )
            self.bind_lease(token)
            self._journal.reseek()
            return LeaseTake(
                lease=LeaseRow(
                    run_id=self.run_id,
                    token=token,
                    pid=pid,
                    host=host,
                    acquired_at=now,
                    heartbeat_at=now,
                    accepting=True,
                ),
                displaced=current,
            )

    def bind_lease(self, token: str | None) -> None:
        """Fence this store's run writes to `token`, or stop fencing with `None`."""
        with self._lock:
            self._token = token

    def release_claims(self, token: str) -> None:
        """Delete this run's claims held under `token`; any other row is untouched."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM run_claims WHERE run_id = ? AND token = ?",
                (self.run_id, token),
            )
            self._conn.commit()

    def beat(self, token: str, now: datetime) -> None:
        """Move the heartbeat of this run's lease, if `token` still holds it."""
        with self._lock:
            self._conn.execute(
                "UPDATE run_leases SET heartbeat_at = ? WHERE run_id = ? AND token = ?",
                (_iso(now), self.run_id, token),
            )
            self._conn.commit()

    def close_window(self, token: str) -> None:
        """Stop accepting control requests under `token` (`accepting = 0`)."""
        with self._lock:
            self._conn.execute(
                "UPDATE run_leases SET accepting = 0 WHERE run_id = ? AND token = ?",
                (self.run_id, token),
            )
            self._conn.commit()

    def release_lease(self, token: str) -> None:
        """Delete this run's lease, if `token` still holds it."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM run_leases WHERE run_id = ? AND token = ?",
                (self.run_id, token),
            )
            self._conn.commit()

    def pending_controls(self, token: str) -> list[ControlRow]:
        """This run's unhandled requests addressed to `token`, in `seq` order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM run_controls WHERE run_id = ? AND lease = ?"
                " AND handled_at IS NULL ORDER BY seq",
                (self.run_id, token),
            ).fetchall()
            return [_control_from_row(row) for row in rows]

    def mark_control_handled(self, seq: int, now: datetime) -> None:
        """Record that this run's request `seq` has been applied."""
        with self._lock:
            self._conn.execute(
                "UPDATE run_controls SET handled_at = ? WHERE run_id = ? AND seq = ?",
                (_iso(now), self.run_id, seq),
            )
            self._conn.commit()

    # -- rebuild -------------------------------------------------------------

    def rebuild_from_journal(self, run_id: str) -> models.Run:
        """Replace this run's projection with what its journal says (D5).

        The journal wins: every row for `run_id` is deleted and rewritten from
        the replayed tree, so the result is the same whether the projection was
        stale, truncated or already correct.

        The store lock is held from reading the journal through the delete and
        every rewrite, so no `record_*` lands between the delete and the
        rewrite. `_delete_run` is only called from here and takes no lock of
        its own. With a lease token bound, the delete and every rewrite are
        one fenced transaction: a store that lost its lease touches no row.
        """
        with self._lock, self._fenced():
            journal = (
                self._journal if self._journal.run_id == run_id else Journal(run_id)
            )
            run = replay(journal.read())
            if run.id != run_id:
                raise JournalError(
                    f"journal of run {run_id!r} has a run_upsert naming run"
                    f" {run.id!r}: refusing to key its projection under two ids"
                )
            self._delete_run(run_id)
            self._write_run_row(run_id, run)
            for story in run.stories:
                self._write_story_row(run_id, story)
                for subtask in story.subtasks:
                    self._write_subtask_row(run_id, story.card_id, subtask)
                    for phase in subtask.phases:
                        self._write_phase_row(
                            run_id, story.card_id, subtask.card_id, phase
                        )
                        for attempt in phase.attempts:
                            self._write_attempt_row(
                                run_id,
                                story.card_id,
                                subtask.card_id,
                                phase.name,
                                attempt,
                            )
            return run

    def replay_journal(self, run_id: str) -> models.Run:
        """The §9 tree `run_id`'s journal records, without touching any row.

        Adoption reads attempts here and never from the `attempts` projection.
        The store lock is held across the read: `Journal.read` takes no lock,
        and other lanes of a milestone resume append to this run's journal
        through this store, so an unlocked read could meet half a line. Nothing
        is written, so there is no `_fenced()`. Another run's journal may be
        live in another process, so only there is a torn final line ignored.
        """
        with self._lock:
            if run_id == self.run_id:
                lines = self._journal.read()
            else:
                lines = Journal._for_reading(run_id).read(ignore_torn_tail=True)
            return replay(lines)

    def _delete_run(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM attempts WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM phases WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM subtasks WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM stories WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        self._commit()
