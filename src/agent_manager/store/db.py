"""The machine-wide SQLite projection `<data dir>/am.db`: its DDL, opening and
migrating it, the write-transaction helper and the busy-retry primitive.
"""

import functools
import random
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from agent_manager import paths

T = TypeVar("T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY,
    repo_dir   TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    project_id    INTEGER NOT NULL REFERENCES projects(id),
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
    project_id INTEGER NOT NULL REFERENCES projects(id),
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
    project_id    INTEGER NOT NULL REFERENCES projects(id),
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
    project_id INTEGER NOT NULL REFERENCES projects(id),
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
    project_id   INTEGER NOT NULL REFERENCES projects(id),
    run_id       TEXT NOT NULL,
    story_id     TEXT NOT NULL,
    card_id      TEXT NOT NULL,
    phase        TEXT NOT NULL,
    n            INTEGER NOT NULL,
    status       TEXT NOT NULL,
    exit_code    INTEGER,
    duration     REAL,
    prompt_path  TEXT,
    result_path  TEXT,
    stdout_path  TEXT,
    dispatch     TEXT NOT NULL,
    PRIMARY KEY (run_id, story_id, card_id, phase, n)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    run_id     TEXT NOT NULL,
    card_id    TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    workflow   TEXT NOT NULL,
    digest     TEXT NOT NULL,
    reason     TEXT NOT NULL CHECK (reason IN ('turn', 'parked', 'done', 'escalated')),
    agent      TEXT NOT NULL,
    saved_at   TEXT NOT NULL,
    PRIMARY KEY (run_id, card_id, seq)
);

CREATE TABLE IF NOT EXISTS checkpoint_floors (
    project_id INTEGER NOT NULL REFERENCES projects(id),
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
    project_id   INTEGER NOT NULL REFERENCES projects(id),
    run_id       TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    lease        TEXT NOT NULL,
    command      TEXT NOT NULL CHECK (command IN ('pause', 'cancel')),
    requested_at TEXT NOT NULL,
    handled_at   TEXT,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS run_leases (
    project_id   INTEGER NOT NULL REFERENCES projects(id),
    run_id       TEXT PRIMARY KEY,
    token        TEXT NOT NULL,
    pid          INTEGER NOT NULL,
    host         TEXT NOT NULL,
    acquired_at  TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    accepting    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS run_claims (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    key        TEXT NOT NULL,
    run_id     TEXT NOT NULL,
    token      TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    PRIMARY KEY (project_id, key)
);

CREATE TABLE IF NOT EXISTS board_comments (
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    run_id          TEXT NOT NULL,
    card_id         TEXT NOT NULL,
    key             TEXT NOT NULL,
    body            TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('pending', 'posted', 'abandoned')),
    comment_id      TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    posted_at       TEXT,
    PRIMARY KEY (project_id, key)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    run_id     TEXT NOT NULL,
    run_seq    INTEGER NOT NULL,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    story_id   TEXT,
    card_id    TEXT,
    phase      TEXT,
    attempt    INTEGER,
    schema     INTEGER NOT NULL DEFAULT 1,
    payload    TEXT NOT NULL,
    source     TEXT NOT NULL CHECK (source IN ('live', 'imported')),
    UNIQUE (run_id, run_seq)
);

CREATE INDEX IF NOT EXISTS events_run_seq ON events (run_id, seq);

CREATE INDEX IF NOT EXISTS events_project_seq ON events (project_id, seq);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;
"""


BUSY_TIMEOUT_SECONDS = 30.0
"""How long a statement on the projection waits for a lock held by another
connection before raising `sqlite3.OperationalError: database is locked`.

It covers a reader in another process, such as `am status`, holding the
database briefly, and a second `am` process's short `BEGIN IMMEDIATE` write
transactions: a lease take-over, or one fenced journal line and row
(multi-process X4, X9)."""

SCHEMA_VERSION = 1
"""The `PRAGMA user_version` this build stamps `am.db` with and can open."""

MIGRATED_KEY = "migrated_at"
"""The `meta` key `am migrate` writes once its merge has committed; the value
is that commit's ISO time. Its presence is what lets the store open on a
machine that still has per-project databases."""

STORE_ID_KEY = "store_id"
"""The `meta` key holding this database's identity: a random 32-character
lowercase hex id that `open_db` inserts when the row is missing and never
changes afterwards. A replaced or restored-from-elsewhere `am.db` therefore
carries a different value."""

RETRY_ATTEMPTS = 5
"""The most times `run_with_retry` calls its job."""

RETRY_DEADLINE_SECONDS = 10.0
"""No retry starts once this many seconds have passed since `run_with_retry`'s
first attempt began. Checked before each retry, so the worst case is this plus
one attempt blocked in `BUSY_TIMEOUT_SECONDS`."""

RETRY_FIRST_PAUSE = 0.1
"""The backoff base, in seconds, before `run_with_retry`'s first retry; each
later base doubles."""

RETRY_PAUSE_CAP = 2.0
"""The largest backoff base `run_with_retry` uses, in seconds."""


class StoreSchemaError(RuntimeError):
    """`am.db`'s `PRAGMA user_version` is greater than `SCHEMA_VERSION`.

    Raised by `open_db` and `open_db_for_reading` before anything is written
    to the file: the database was written by a newer `am`. Never retried.
    """

    def __init__(self, path: Path, found: int) -> None:
        super().__init__(
            f"{path} has schema version {found}, but this am only knows schema"
            f" version {SCHEMA_VERSION}: this am is older than the database."
            " Upgrade am; nothing has been changed"
        )
        self.path = path
        self.found = found


class MigrationRequiredError(RuntimeError):
    """Per-project databases from an older `am` are present and `am.db` has no
    `MIGRATED_KEY` row in `meta` (or no `am.db`, or no `meta` table).

    Raised by `open_db` and `open_db_for_reading` before anything is created
    or written: not the data directory, not `am.db` or its sidecars, not any
    legacy file. `legacy` is what `paths.legacy_project_dbs` listed.
    """

    def __init__(self, legacy: Sequence[Path]) -> None:
        super().__init__(
            f"{len(legacy)} per-project database(s) from an older am are in"
            f" {paths.data_path() / 'projects'} and have not been migrated into"
            f" {paths.db_path()}; run `am migrate` first. Nothing has been changed"
        )
        self.legacy = tuple(legacy)


class StoreBusyError(RuntimeError):
    """A write stayed busy or locked through `run_with_retry`'s whole budget.

    Raised by `run_with_retry` once the job has been called `RETRY_ATTEMPTS`
    times or `RETRY_DEADLINE_SECONDS` have passed, chained (`__cause__`) to the
    last busy `sqlite3.OperationalError`. Never retried itself. `operation`
    names the write, `attempts` the calls made, `elapsed` the seconds since the
    first attempt began.
    """

    def __init__(self, operation: str, attempts: int, elapsed: float) -> None:
        super().__init__(
            f"{operation}: the database stayed busy or locked through {attempts}"
            f" attempt(s) over {elapsed:.1f} s (retry budget: {RETRY_ATTEMPTS}"
            f" attempts within {RETRY_DEADLINE_SECONDS:g} s)"
        )
        self.operation = operation
        self.attempts = attempts
        self.elapsed = elapsed


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
migrated database end up with the same column order. `project_id` is first in
every table for the same reason: it never moves an added column off the end."""


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


def _read_only_uri(location: Path) -> str:
    """A URI that opens the existing `location` without writing or creating anything.

    A `mode=ro` read of a WAL database creates its `-wal` and `-shm`
    sidecars. When neither exists, no connection holds the file open, so it is
    opened `immutable=1` instead: that read takes no lock and creates nothing.
    """
    settled = not any(
        location.with_name(location.name + suffix).exists() for suffix in ("-wal", "-shm")
    )
    return f"{location.absolute().as_uri()}?{'immutable=1' if settled else 'mode=ro'}"


def _refuse_unmigrated(location: Path) -> None:
    """Raise `MigrationRequiredError` when the machine still needs `am migrate`.

    No legacy database (`paths.legacy_project_dbs`): no refusal. Otherwise
    refused unless `location` exists and its `meta` table has a
    `MIGRATED_KEY` row. `location` is read through a read-only connection,
    closed before returning or raising, so the check takes no write lock and
    creates nothing.
    """
    legacy = paths.legacy_project_dbs()
    if not legacy:
        return
    if location.exists():
        conn = sqlite3.connect(
            _read_only_uri(location), uri=True, timeout=BUSY_TIMEOUT_SECONDS
        )
        try:
            has_meta = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
                ).fetchone()
                is not None
            )
            migrated = (
                has_meta
                and conn.execute(
                    "SELECT 1 FROM meta WHERE key = ?", (MIGRATED_KEY,)
                ).fetchone()
                is not None
            )
        finally:
            conn.close()
        if migrated:
            return
    raise MigrationRequiredError(legacy)


def open_db(root: Path) -> sqlite3.Connection:
    """Open the machine-wide projection `paths.db_path()`, applying the schema idempotently.

    `root` does not choose the file: every root opens `paths.db_path()`. The
    data directory is created first; `<data dir>/projects` never is.

    WAL mode is set before the schema so a reader never blocks the writer. The
    WAL switch is retried until `BUSY_TIMEOUT_SECONDS`, because SQLite does not
    call the busy handler for that pragma when another connection holds a write
    lock. Every `CREATE` is `IF NOT EXISTS`, so reopening an existing database never
    destroys what is already there. The only migration is additive:
    `_add_missing_columns` appends each column in `_ADDED_COLUMNS` that an older
    table lacks, as a nullable column. Existing rows keep their data and read
    the new column as NULL. It is a no-op on a database that already has the
    column, so opening the same database any number of times is safe. Nothing
    is ever dropped: an `attempts` table created before 2026-10-03 keeps its
    `tokens_in`, `tokens_out` and `cost` columns, which nothing writes or reads
    any more, so they stay NULL.

    Before anything else, `_refuse_unmigrated` raises `MigrationRequiredError`
    on a machine with per-project databases and no completed migration; the
    refusal is checked before the schema version, and creates nothing.

    `PRAGMA user_version` is read before anything is written: a value above
    `SCHEMA_VERSION` closes the connection and raises `StoreSchemaError`,
    leaving the file as it was. A lower value (0 on a new or unstamped file)
    is set to `SCHEMA_VERSION` after the schema and columns are applied.

    A missing `STORE_ID_KEY` row in `meta` is inserted with a fresh random id
    and committed with the schema; an existing one is never changed.

    The connection is used from a thread other than the one that opened it,
    so `check_same_thread` is off; a `Store` uses it only from its writer
    thread. `BUSY_TIMEOUT_SECONDS` covers another process holding
    the database briefly; two processes never write one run, because every
    run write is fenced by the lease token (multi-process X4).
    """
    location = paths.db_path()
    _refuse_unmigrated(location)
    paths.data_dir()
    conn = sqlite3.connect(
        location,
        timeout=BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    try:
        found = conn.execute("PRAGMA user_version").fetchone()[0]
        if found > SCHEMA_VERSION:
            raise StoreSchemaError(location, found)
        conn.row_factory = sqlite3.Row
        _enable_wal(conn)
        conn.executescript(_SCHEMA)
        _add_missing_columns(conn)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
            (STORE_ID_KEY, uuid4().hex),
        )
        if found < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


def store_id(conn: sqlite3.Connection) -> str | None:
    """This database's `STORE_ID_KEY` value from `meta`, or `None` when there is
    no such row.

    `None` only on an `open_db_for_reading` connection no `open_db` has touched:
    the in-memory empty projection, or an existing current-schema file not yet
    back-filled. Never writes, commits or mints; any row factory works.
    """
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (STORE_ID_KEY,)
    ).fetchone()
    return None if row is None else row[0]


def _table_columns(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Each table of `conn`'s main database, with its column names."""
    tables = [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ]
    return {
        table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for table in tables
    }


@functools.cache
def _current_columns() -> dict[str, set[str]]:
    """The tables and columns `_SCHEMA` creates."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_SCHEMA)
        return _table_columns(conn)
    finally:
        conn.close()


def _has_current_schema(conn: sqlite3.Connection) -> bool:
    found = _table_columns(conn)
    return all(
        columns <= found.get(table, set())
        for table, columns in _current_columns().items()
    )


def open_db_for_reading(root: Path) -> sqlite3.Connection:
    """A connection that reads the machine-wide projection and never writes it.

    `root` does not choose the file, as for `open_db`. No `am.db`: an
    in-memory, empty projection with the current schema, and nothing is
    created on disk. An existing database with the current schema: opened
    read-only through `_read_only_uri`, so it can never be written or
    created; `mode=ro` while a writer has it open (its rows are read live in
    WAL mode), `immutable=1` when no `-wal`/`-shm` exists (the rows as they
    were at open). An existing database with an older schema: `open_db`,
    which migrates it as before. A
    `user_version` above `SCHEMA_VERSION` raises `StoreSchemaError` with
    nothing created; below it, or missing tables or columns, is "an older
    schema".

    First, as `open_db` does, `_refuse_unmigrated` may raise
    `MigrationRequiredError`. The connection is opened by `_read_only_uri`
    and is for one short read, never held across writes.
    """
    location = paths.db_path()
    _refuse_unmigrated(location)
    if not location.exists():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn
    conn = sqlite3.connect(
        _read_only_uri(location),
        uri=True,
        timeout=BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        found = conn.execute("PRAGMA user_version").fetchone()[0]
        if found > SCHEMA_VERSION:
            raise StoreSchemaError(location, found)
        current = found == SCHEMA_VERSION and _has_current_schema(conn)
    except BaseException:
        conn.close()
        raise
    if current:
        return conn
    conn.close()
    return open_db(root)


def open_reader(location: Path) -> sqlite3.Connection:
    """A read-only connection on the existing database file `location`.

    Opened `mode=ro`, never `immutable=1`: another connection of this process
    holds the file open and writes it, and each statement here sees what was
    committed when it began, never an open transaction's rows. Any write
    through it raises `sqlite3.OperationalError`. Usable from any thread
    (`check_same_thread` off); the caller serialises its use. Rows are
    `sqlite3.Row`, and a lock held elsewhere is waited out for
    `BUSY_TIMEOUT_SECONDS`.
    """
    conn = sqlite3.connect(
        f"{location.absolute().as_uri()}?mode=ro",
        uri=True,
        timeout=BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


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


def _is_busy(error: sqlite3.OperationalError) -> bool:
    """Whether `error` is SQLite reporting busy or locked, extended codes included.

    Only the primary code counts (`sqlite_errorcode & 0xFF`), so
    `SQLITE_BUSY_SNAPSHOT`, `SQLITE_BUSY_TIMEOUT` and `SQLITE_LOCKED_SHAREDCACHE`
    are busy too. An error without an integer code (one built by hand) is not:
    the message is never read.
    """
    code = getattr(error, "sqlite_errorcode", None)
    return isinstance(code, int) and code & 0xFF in (
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    )


def run_with_retry(
    job: Callable[[], T],
    *,
    operation: str,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], object] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> T:
    """Call `job` and return its value, re-running the whole job while SQLite is busy.

    Only a `sqlite3.OperationalError` whose primary code is `SQLITE_BUSY` or
    `SQLITE_LOCKED` is retried. Before each retry the budget is checked: once
    the job has been called `RETRY_ATTEMPTS` times, or `RETRY_DEADLINE_SECONDS`
    have passed since the first attempt began, `StoreBusyError` is raised from
    the last busy error, without sleeping. Otherwise the k-th retry waits a
    jittered `min(RETRY_FIRST_PAUSE * 2**(k-1), RETRY_PAUSE_CAP)` (between half
    and all of it), clamped to the time left. All four constants are read now,
    so tests can patch them. Anything else, `LeaseLostError` and every other
    `BaseException` included, propagates on that attempt as the same object.

    This opens and rolls back no transaction: the job does (under `immediate`,
    a raise rolls back). So the job must be re-runnable: no hidden state, and
    no side effect outside the database before its commit.
    """
    start = clock()
    attempts = 0
    while True:
        attempts += 1
        try:
            return job()
        except sqlite3.OperationalError as error:
            if not _is_busy(error):
                raise
            elapsed = clock() - start
            if attempts >= RETRY_ATTEMPTS or elapsed >= RETRY_DEADLINE_SECONDS:
                raise StoreBusyError(operation, attempts, elapsed) from error
            base = min(RETRY_FIRST_PAUSE * 2 ** (attempts - 1), RETRY_PAUSE_CAP)
            pause = base * (0.5 + 0.5 * rng())
            sleep(max(0.0, min(pause, RETRY_DEADLINE_SECONDS - elapsed)))


def iso(value: datetime | None) -> str | None:
    """The column encoding of a timestamp: ISO-8601 text; `None` stays `None`."""
    return None if value is None else value.isoformat()
