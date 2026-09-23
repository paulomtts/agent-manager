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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_manager import paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    workflow      TEXT NOT NULL,
    repo_dir      TEXT NOT NULL,
    base_branch   TEXT NOT NULL,
    branch_prefix TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TEXT,
    config        TEXT NOT NULL
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
"""


def open_db(root: Path) -> sqlite3.Connection:
    """Open the per-project projection, applying the schema idempotently.

    WAL mode is set before the schema so a reader never blocks the writer. Every
    `CREATE` is `IF NOT EXISTS`, so reopening an existing database neither
    destroys nor migrates what is already there.
    """
    conn = sqlite3.connect(paths.project_db_path(root))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
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


class Journal:
    """Append-only JSONL log for one run: the truth the projection is built from."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.path = paths.run_dir(run_id) / JOURNAL_NAME

    def last_seq(self) -> int:
        """Highest sequence number already on disk, or 0 for a fresh journal."""
        if not self.path.exists():
            return 0
        return max((line.seq for line in self.read()), default=0)

    def read(self) -> list[JournalLine]:
        """Every line, validated, in sequence order.

        Blank lines are skipped: a crash between the write and the flush can
        leave one. Anything else that is not JSON is an error naming the line.
        """
        if not self.path.exists():
            raise MissingJournalError(
                f"no journal for run {self.run_id!r} at {self.path}"
            )
        lines: list[JournalLine] = []
        with self.path.open(encoding="utf-8") as handle:
            for number, text in enumerate(handle, start=1):
                if not text.strip():
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError as error:
                    raise CorruptJournalError(
                        f"{self.path}:{number}: line is not JSON: {error}"
                    ) from error
                lines.append(JournalLine.model_validate(record))
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

        The sequence number is read from disk on every call rather than cached,
        so a second writer attached to the same run continues the sequence
        instead of reusing a number.
        """
        line = JournalLine(
            seq=self.last_seq() + 1,
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
            os.fsync(handle.fileno())
        return line
