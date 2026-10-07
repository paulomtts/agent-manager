"""Per-project databases as an older `am` left them, for the migration tests.

Importable as `legacyhelpers` because `pyproject.toml` puts `tests/` on
`pythonpath`. Everything here is stdlib `sqlite3` and file copies under the
test's data directory or `tmp_path`; nothing spawns a process, so callers
stay unit tier.
"""

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from agent_manager import paths

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
"""The `now` every migration test passes."""

STAMP = "2026-10-07T11:00:00+00:00"
"""An hour before `NOW`: a lease beating at this time is stale."""

Rows = Mapping[str, Sequence[Mapping[str, object]]]

LEGACY_SCHEMA = """
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
"""`master`'s `store.py` `_SCHEMA`, verbatim: no `projects`, no `project_id`."""

LEGACY_TABLES = (
    "attempts",
    "board_comments",
    "checkpoint_floors",
    "checkpoints",
    "phases",
    "run_claims",
    "run_controls",
    "run_leases",
    "runs",
    "stories",
    "subtasks",
)
"""The tables `LEGACY_SCHEMA` creates, sorted: also every table `am.db` copies into."""


def _swap(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new)


OLD_SCHEMA = _swap(
    _swap(
        _swap(
            LEGACY_SCHEMA,
            "    position   INTEGER NOT NULL,\n    detail     TEXT,\n",
            "    position   INTEGER NOT NULL,\n",
        ),
        "    config        TEXT NOT NULL,\n    milestone_id  TEXT\n",
        "    config        TEXT NOT NULL\n",
    ),
    "    dispatch     TEXT NOT NULL,\n",
    "    dispatch     TEXT NOT NULL,\n    tokens_in    INTEGER,\n"
    "    tokens_out   INTEGER,\n    cost         REAL,\n",
)
"""An older file still: no `phases.detail`, no `runs.milestone_id`, and
`attempts` still carrying `tokens_in`, `tokens_out` and `cost`."""


def run_row(run_id: str, repo: Path, *, status: str = "done") -> dict[str, object]:
    """A `runs` row of `repo`, with `repo_dir` its resolved path as `am` stored it."""
    return {
        "id": run_id,
        "workflow": "task",
        "repo_dir": str(repo.resolve()),
        "base_branch": "main",
        "branch_prefix": "am/",
        "status": status,
        "started_at": STAMP,
        "config": "{}",
    }


def lease_row(
    run_id: str, *, heartbeat_at: datetime, pid: int = 4242, host: str = "here"
) -> dict[str, object]:
    """A `run_leases` row of `run_id` that last beat at `heartbeat_at`."""
    return {
        "run_id": run_id,
        "token": f"tok-{run_id}",
        "pid": pid,
        "host": host,
        "acquired_at": heartbeat_at.isoformat(),
        "heartbeat_at": heartbeat_at.isoformat(),
        "accepting": 1,
    }


def full_rows(run_id: str, repo: Path) -> dict[str, list[dict[str, object]]]:
    """One row in each of the eleven legacy tables, all of run `run_id`.

    Its lease beat at `STAMP`, so it is stale at `NOW`. Fits `OLD_SCHEMA`
    too: no `detail`, no `milestone_id`, no `tokens_*`.
    """
    token = f"tok-{run_id}"
    return {
        "runs": [run_row(run_id, repo)],
        "stories": [
            {"run_id": run_id, "card_id": "s1", "title": "t", "level": 0,
             "status": "done", "position": 0},
        ],
        "subtasks": [
            {"run_id": run_id, "story_id": "s1", "card_id": "c1", "branch": "b",
             "base_branch": "main", "status": "done", "position": 0},
        ],
        "phases": [
            {"run_id": run_id, "story_id": "s1", "card_id": "c1", "name": "spec",
             "kind": "agent", "status": "done", "position": 0},
        ],
        "attempts": [
            {"run_id": run_id, "story_id": "s1", "card_id": "c1", "phase": "spec",
             "n": 1, "status": "done", "dispatch": "{}"},
        ],
        "checkpoints": [
            {"run_id": run_id, "card_id": "c1", "seq": 0, "workflow": "task",
             "digest": "d", "reason": "turn", "agent": "{}", "saved_at": STAMP},
        ],
        "checkpoint_floors": [
            {"run_id": run_id, "card_id": "c1", "seq": 0, "phase": "spec", "loop": 0,
             "source_run": run_id, "floor": 0},
        ],
        "run_controls": [
            {"run_id": run_id, "seq": 0, "lease": token, "command": "pause",
             "requested_at": STAMP},
        ],
        "run_leases": [
            lease_row(run_id, heartbeat_at=datetime.fromisoformat(STAMP)),
        ],
        "run_claims": [
            {"key": "card:c1", "run_id": run_id, "token": token, "claimed_at": STAMP},
        ],
        "board_comments": [
            {"run_id": run_id, "card_id": "c1", "key": "k1", "body": "b",
             "state": "pending", "created_at": STAMP},
        ],
    }


def projects_dir() -> Path:
    """`<data dir>/projects`, created."""
    directory = paths.data_path() / "projects"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def project_file(repo: Path) -> Path:
    """Where an older `am` kept `repo`'s database: `projects/<digest>.db`."""
    return projects_dir() / f"{paths.project_digest(repo)}.db"


def _insert(conn: sqlite3.Connection, table: str, row: Mapping[str, object]) -> None:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values()))


def _fill(conn: sqlite3.Connection, rows: Rows) -> None:
    for table, table_rows in rows.items():
        for row in table_rows:
            _insert(conn, table, row)


def write_db(path: Path, rows: Rows | None = None, *, schema: str = LEGACY_SCHEMA) -> Path:
    """A settled database at `path`: `schema`, then `rows`, committed and closed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(schema)
        _fill(conn, rows or {})
        conn.commit()
    finally:
        conn.close()
    return path


def write_wal_db(
    path: Path,
    scratch: Path,
    rows_in_wal: Rows,
    *,
    rows: Rows | None = None,
    schema: str = LEGACY_SCHEMA,
) -> Path:
    """`path` as a crashed `am` leaves it: `rows` checkpointed into the `.db`,
    `rows_in_wal` committed only in a `-wal`, and a `-shm` beside it.

    Built under `scratch` with the writing connection still open, so nothing
    is checkpointed, then byte-copied to `path`.
    """
    scratch.mkdir(parents=True, exist_ok=True)
    source = scratch / path.name
    conn = sqlite3.connect(source)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(schema)
        _fill(conn, rows or {})
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        _fill(conn, rows_in_wal)
        conn.commit()
        path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            shutil.copyfile(
                source.with_name(source.name + suffix), path.with_name(path.name + suffix)
            )
    finally:
        conn.close()
    return path


def tree(directory: Path) -> dict[str, str]:
    """sha256 of every file under `directory`, keyed by its relative path."""
    return {
        str(entry.relative_to(directory)): hashlib.sha256(entry.read_bytes()).hexdigest()
        for entry in sorted(directory.rglob("*"))
        if entry.is_file()
    }


_COORDS = ("story", "card", "phase", "attempt")


def journal_line(
    run_id: str,
    seq: int,
    ts: str,
    event: str = "run_upsert",
    payload: Mapping[str, object] | None = None,
    **coords: object,
) -> dict[str, object]:
    """One journal line as `am` wrote it: every envelope key, with `story`,
    `card`, `phase` and `attempt` taken from `coords` and null otherwise."""
    unknown = set(coords) - set(_COORDS)
    assert not unknown, unknown
    return {
        "seq": seq,
        "ts": ts,
        "run_id": run_id,
        "event": event,
        **{key: coords.get(key) for key in _COORDS},
        "payload": dict(payload or {}),
    }


def write_journal(
    run_id: str, lines: Sequence[Mapping[str, object]], *, tail: str | None = None
) -> Path:
    """`<data dir>/runs/<run_id>/journal.jsonl`, overwritten with one
    `json.dumps(line, sort_keys=True)` and a newline per line, then `tail`
    verbatim when given."""
    path = paths.data_path() / "runs" / run_id / "journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines)
    path.write_bytes((text + (tail or "")).encode("utf-8"))
    return path
