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
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

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


class Store:
    """The two stores of D5, bound together by the write ordering of §9.

    Every `record_*` appends the journal line first and writes the row second.
    There is deliberately no public method that writes a row on its own.
    """

    def __init__(self, conn: sqlite3.Connection, journal: Journal) -> None:
        self._conn = conn
        self._journal = journal

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
        self._conn.close()

    # -- recording ---------------------------------------------------------

    def record_run(self, run: models.Run) -> JournalLine:
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
        line = self._journal.append(
            "story_upsert",
            story.model_dump(mode="json", exclude={"subtasks"}),
            story=story.card_id,
        )
        self._write_story_row(self.run_id, story)
        return line

    def record_subtask(self, story_id: str, subtask: models.SubtaskRun) -> JournalLine:
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
        line = self._journal.append(
            "attempt_upsert",
            attempt.model_dump(mode="json"),
            story=story_id,
            card=card_id,
            phase=phase_name,
            attempt=attempt.n,
        )
        self._write_attempt_row(self.run_id, story_id, card_id, phase_name, attempt)
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
                              status, started_at, config)
            VALUES (:id, :workflow, :repo_dir, :base_branch, :branch_prefix,
                    :status, :started_at, :config)
            ON CONFLICT(id) DO UPDATE SET
                workflow=excluded.workflow,
                repo_dir=excluded.repo_dir,
                base_branch=excluded.base_branch,
                branch_prefix=excluded.branch_prefix,
                status=excluded.status,
                started_at=excluded.started_at,
                config=excluded.config
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
            },
        )
        self._conn.commit()

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
        self._conn.commit()

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
        self._conn.commit()

    def _write_phase_row(
        self, run_id: str, story_id: str, card_id: str, phase: models.PhaseRun
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO phases (run_id, story_id, card_id, name, kind, status,
                                started_at, ended_at, position)
            VALUES (:run_id, :story_id, :card_id, :name, :kind, :status,
                    :started_at, :ended_at,
                    (SELECT COUNT(*) FROM phases
                      WHERE run_id = :run_id AND story_id = :story_id
                        AND card_id = :card_id))
            ON CONFLICT(run_id, story_id, card_id, name) DO UPDATE SET
                kind=excluded.kind,
                status=excluded.status,
                started_at=excluded.started_at,
                ended_at=excluded.ended_at
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
            },
        )
        self._conn.commit()

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
        self._conn.commit()

    # -- reading -------------------------------------------------------------

    def load_run(self, run_id: str) -> models.Run | None:
        """Assemble the projection back into the §9 tree, or `None` if absent.

        Every value goes back through the `models` validators, so a projection
        that drifted from the schema fails here rather than downstream.
        """
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
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
        )

        for story_row in self._conn.execute(
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

            for subtask_row in self._conn.execute(
                "SELECT * FROM subtasks WHERE run_id = ? AND story_id = ?"
                " ORDER BY position",
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

                for phase_row in self._conn.execute(
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
                    )
                    subtask.phases.append(phase)

                    for attempt_row in self._conn.execute(
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

    # -- rebuild -------------------------------------------------------------

    def rebuild_from_journal(self, run_id: str) -> models.Run:
        """Replace this run's projection with what its journal says (D5).

        The journal wins: every row for `run_id` is deleted and rewritten from
        the replayed tree, so the result is the same whether the projection was
        stale, truncated or already correct.
        """
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
                    self._write_phase_row(run_id, story.card_id, subtask.card_id, phase)
                    for attempt in phase.attempts:
                        self._write_attempt_row(
                            run_id, story.card_id, subtask.card_id, phase.name, attempt
                        )
        return run

    def _delete_run(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM attempts WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM phases WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM subtasks WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM stories WHERE run_id = ?", (run_id,))
        self._conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        self._conn.commit()
