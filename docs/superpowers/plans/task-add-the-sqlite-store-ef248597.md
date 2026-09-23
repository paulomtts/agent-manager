<!-- task-pipeline: validated -->
# Subtask ef248597 — Add the SQLite store and the append-only journal

Parent story: 8831189b "Foundations: paths, run store and journal". Blocked by 1535b285 (state models, done); sits on top of fdebc746 (paths, done).

Narrowing of the agreed design in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` — D5 (line 70), §9 (lines 346-387), §14 (lines 477-492). No new design decisions are taken here.

## Scope

One new module, `src/agent_manager/store.py`, plus its mirror test module `tests/test_store.py`. It owns exactly three things: the SQLite projection (schema + connection in WAL mode), the append-only journal writer, and `rebuild_from_journal`.

Out of scope, owned elsewhere or by a later story: path derivation (`paths.py` — call `paths.project_db_path(root)` and `paths.run_dir(run_id)`, never re-derive the data dir, the sha256 digest or the run directory); the state model tree (`models.py` — serialize and validate with `Run`/`StoryRun`/`SubtaskRun`/`PhaseRun`/`Attempt`/`Dispatch`/`RunConfig`/`HarnessAssignment` and the `Status`/`AttemptStatus`/`PhaseKind`/`Launcher` literals, never redefine them); phase result models (`ExploreResult`, `CriticResult`, …) which belong to the engine story; the engine's resume loop itself, milestone orchestration (census, levels, parallel stories, integrate), the CLI, and non-Claude harnesses.

The store is also independent of `brd`: it writes no board state and reads none, so `brd forget`/`purge` and agent-manager cleanup cannot affect each other (D5). Nothing in this module may take a repository worktree as a write base.

## Observable behaviour

**Two stores, one truth.** The journal at `paths.run_dir(run_id) / "journal.jsonl"` is the append-only truth. The SQLite file at `paths.project_db_path(root)` is a queryable projection of it, holding rows for runs, stories, subtasks, phases and attempts. Where they disagree the journal wins, and the projection is discardable at any time.

**Connection.** Opening the project DB creates its parent directories via `paths`, applies the schema idempotently (safe to call against an existing DB), and puts the connection in WAL journal mode. Reopening an existing DB must not destroy or migrate data — schema creation is `IF NOT EXISTS`-shaped.

**Journal lines.** One JSON object per line, newline-terminated, appended and flushed so a crash immediately after the call cannot lose the line. Every line carries at least: the monotonic sequence number, the run id, the card id, the phase name, the attempt number and the event payload. The sequence number is monotonically increasing within a run and never reused; it is derived from what is already on disk when the journal is opened, so an appender attached to a partially written journal continues the sequence rather than restarting it. Card, phase and attempt are null for events that are above that level (e.g. run-level status transitions).

**Write ordering is load-bearing.** Every state-changing operation appends the journal line *first* and writes/updates the SQLite row *second*. If the SQLite write fails or the process dies between the two, the journal is still correct and the projection is rebuildable. There is no code path that writes a row without a preceding journal line.

**`rebuild_from_journal(run_id)`** reads the journal for that run in sequence order, replays events into the model tree, and writes the resulting projection into the DB, replacing whatever rows that run had. A DB that was truncated, deleted or corrupted mid-run yields, after rebuild, the same query results the journal describes. Rebuild is idempotent: running it twice produces the same projection and does not duplicate rows.

**Attempts in flight.** An attempt whose journal has a `started` line but no terminal line (`ok`, `schema_invalid`, `gate_failed`, `harness_error`) is reconstructed with `status="started"` and no exit code, duration, tokens or cost. The store preserves that state rather than deleting or normalising it — the engine's resume logic is what discards it (§9 lines 370-373). Distinguishing in-flight from terminal attempts after a rebuild is the store's contract.

## Error paths

- A journal line that fails `models` validation — unknown key, bad status literal, missing required field — propagates the `pydantic.ValidationError` out of `rebuild_from_journal`. The `extra="forbid"` base is deliberate: an old-schema line must fail loudly, never be skipped, and never silently drop a field. No `try/except` that swallows it.
- A malformed (non-JSON) line likewise raises, naming the file and the line number so the operator can see which line is bad.
- `rebuild_from_journal` for a run with no journal file raises a clear error rather than silently producing an empty projection — an empty run and a missing run are different failures.
- SQLite write failures propagate; they do not corrupt the journal, which was already appended.
- Appending to a journal directory that cannot be created propagates the OS error from `paths`.

## Test list

All tests live in `tests/test_store.py`, mirroring `src/agent_manager/store.py`. Per the placement rule in spec §14, this module touches real files (SQLite DB, journal), so nothing here is in the "pure functions" tier; these are deterministic, no-network, temp-directory tests in the **"steps"-style I/O tier** — real temp DB files and real temp journals rather than mocks — and they stay in the default `uv run pytest` suite. None of them dispatches a harness, so none belongs in the single opt-in "end to end" test. The module docstring states this tier explicitly, following the sibling `tests/test_models.py` convention. `HOME`/`XDG_DATA_HOME` are pointed at a tmp path so `paths` writes nowhere real.

1. **Schema and WAL** ("steps" I/O tier) — opening the project DB creates the file under `paths.project_db_path(root)` and `PRAGMA journal_mode` reports `wal`; the expected tables for runs, stories, subtasks, phases and attempts exist.
2. **Re-open is non-destructive** ("steps" I/O tier) — opening an existing DB that already holds a run leaves its rows intact.
3. **Journal line shape** ("steps" I/O tier) — an appended event is one JSON line in `run_dir(run_id)/journal.jsonl` carrying run id, card, phase, attempt and a sequence number.
4. **Monotonic sequence across reopen** ("steps" I/O tier) — sequence numbers increase by one across appends, and a writer reopened against an existing journal continues from the last number rather than restarting at zero.
5. **Journal precedes the row** ("steps" I/O tier) — with the SQLite write forced to fail, the journal line is still present on disk and the projection is missing the row; a subsequent rebuild produces it. This is the write-ordering guarantee of §9 line 365.
6. **Truncated DB rebuilt from journal** ("steps" I/O tier) — the card's required test. Drive a multi-story/multi-subtask/multi-phase run through the store, truncate (or delete) the DB mid-run, call `rebuild_from_journal(run_id)`, and assert the reconstructed projection equals the pre-truncation state.
7. **Rebuild is idempotent** ("steps" I/O tier) — calling `rebuild_from_journal` twice leaves identical rows, with no duplicates.
8. **In-flight attempt survives rebuild** ("steps" I/O tier) — an attempt journalled as `started` with no terminal line rebuilds as `status="started"` with exit code, duration, tokens and cost all unset, and is distinguishable from a terminal attempt.
9. **Old-schema line fails loudly** ("steps" I/O tier) — a journal line with an extra/unknown key, and one with an invalid `Status` value, each raise `ValidationError` out of `rebuild_from_journal` instead of being skipped.
10. **Corrupt line fails loudly** ("steps" I/O tier) — a non-JSON line raises an error identifying the journal and the line.
11. **Missing journal** ("steps" I/O tier) — `rebuild_from_journal` on an unknown run id raises rather than returning an empty projection.
12. **Store writes nothing into the repo worktree** ("steps" I/O tier) — after a full run is recorded against a temp git-less repo dir, that directory is unchanged; all artifacts live under the temp data dir (D5 independence, and the §4 constraint that run artifacts never enter the worktree).

## Verification

`uv run pytest`. No separate lint or typecheck command in this project.

---

# SQLite Store and Append-Only Journal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/store.py`: a per-project SQLite projection of the run state tree, an append-only JSONL journal that is written before every row, and `rebuild_from_journal(run_id)` that reconstructs the projection from the journal.

**Architecture:** Three layers in one module. `open_db(root)` opens the per-project SQLite file at `paths.project_db_path(root)` in WAL mode and applies an `IF NOT EXISTS` schema. `Journal` owns `paths.run_dir(run_id)/journal.jsonl`: it appends one fsynced JSON line per event, each line validated through a `JournalLine` pydantic envelope carrying a monotonic sequence number plus the run/story/card/phase/attempt coordinates, and reads lines back in sequence order. `Store` composes the two: every `record_*` method appends the journal line *first* and writes the SQLite row *second*, so a crash or SQLite failure between the two leaves the journal correct and the projection rebuildable. `replay()` folds journal lines back into a `models.Run` tree and `Store.rebuild_from_journal(run_id)` deletes that run's rows and rewrites them from the replayed tree.

**Tech Stack:** Python 3.12+, stdlib `sqlite3` and `json`, pydantic v2, pytest. No new dependencies — `pyproject.toml` is untouched.

**Spec:** `docs/superpowers/specs/task-add-the-sqlite-store-ef248597-design.md` (reproduced verbatim above), narrowing `docs/superpowers/specs/2026-09-23-agent-manager-design.md` D5 (line 70), §9 (lines 346-387) and §14 (lines 477-492).

## Global Constraints

- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (repo `CLAUDE.md`).
- Verification is exactly `uv run pytest`. There is no lint or typecheck command in this project.
- Never re-derive paths: call `paths.project_db_path(root)`, `paths.run_dir(run_id)`. Never compute the data dir or the sha256 digest in `store.py`.
- Never redefine the state models: import and use `models.Run`, `models.StoryRun`, `models.SubtaskRun`, `models.PhaseRun`, `models.Attempt`, `models.Dispatch`, `models.RunConfig`, `models.HarnessAssignment` and the `Status` / `AttemptStatus` / `PhaseKind` / `Launcher` literals from `agent_manager.models`.
- `models._Model` sets `extra="forbid"`. Validation errors raised while replaying a journal line must propagate — no `try`/`except` that swallows `pydantic.ValidationError`.
- Write ordering is load-bearing: no code path writes a SQLite row without a preceding journal append.
- Nothing in `store.py` may take a repository worktree as a write base.
- Do not add phase result models, engine resume, milestone orchestration, or CLI wiring — other cards own those.
- Every test module docstring names the spec §14 tier it belongs to, following `tests/test_models.py:1-10`.

## Review Focus

1. **Journal with a missing head** — a `story_upsert` line replayed before any `run_upsert` (a journal whose first lines were lost) must raise `JournalError` naming the coordinate, not an `AttributeError` on `None`. Covered in Task 5, Step 9.
2. **Crash mid-append leaves a partial last line** — a blank/whitespace-only line is skipped; a truncated JSON line raises `CorruptJournalError` naming the file and the 1-based line number. Covered in Task 2, Step 5.
3. **Two writers on the same run** — two `Journal` objects open on one run must never hand out the same sequence number. Covered in Task 2, Step 1.
4. **Rebuild scoped to one run** — the per-project DB holds many runs; `rebuild_from_journal("run-a")` must not delete or alter run-b's rows. Covered in Task 5, Step 11.
5. **Repeated records for one node** — a status transition (`pending` → `started` → `done`) records the same node three times; the projection must update in place, never duplicate a row and never reorder siblings. Covered in Task 3, Step 7.

---

### Task 1: Project DB connection, schema and WAL

**Files:**
- Create: `src/agent_manager/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `agent_manager.paths.project_db_path(root: Path) -> Path` (`src/agent_manager/paths.py:22-26`).
- Produces: `store.open_db(root: Path) -> sqlite3.Connection` — a connection with `row_factory = sqlite3.Row`, `journal_mode=wal`, and the five tables `runs`, `stories`, `subtasks`, `phases`, `attempts` applied idempotently.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store.py` with the module docstring, shared fixtures and the first two tests:

```python
"""Behaviour of the SQLite projection and the append-only journal (spec §9, D5).

This module touches real files — a SQLite database and a JSONL journal — so it
is not in the "pure functions" tier of spec §14. These are the deterministic,
no-network, temporary-directory tests of the "steps" I/O tier: real temp DB
files and real temp journals rather than mocks. None of them dispatches a
harness, so none belongs in the single opt-in "end to end" test, and they all
run in the default `uv run pytest` suite.

`XDG_DATA_HOME` and `HOME` are redirected into pytest's tmp_path so `paths`
writes nowhere real.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_manager import models, paths, store

RUN_ID = "run-2026-09-23-01"


@pytest.fixture
def repo(monkeypatch, tmp_path) -> Path:
    """A redirected data dir plus a stand-in for the project worktree."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "repo"
    project.mkdir()
    return project


def _dispatch(card: str = "ef248597", phase: str = "implement", n: int = 1) -> models.Dispatch:
    return models.Dispatch(
        harness="claude",
        model="sonnet",
        role="coder",
        cwd=Path(f"/repo/.claude/worktrees/m1/{card}"),
        prompt_path=Path(f"/runs/{RUN_ID}/{card}/{phase}.{n}/prompt.txt"),
        result_path=Path(f"/runs/{RUN_ID}/{card}/{phase}.{n}/result.json"),
    )


def _run(repo: Path, run_id: str = RUN_ID) -> models.Run:
    return models.Run(
        id=run_id,
        workflow="milestone",
        repo_dir=repo,
        base_branch="main",
        branch_prefix="m1/",
        status="started",
        started_at=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
        config=models.RunConfig(
            max_concurrent_stories=2,
            harness_map={"coder": models.HarnessAssignment(harness="claude", model="sonnet")},
        ),
    )


def test_open_db_creates_the_project_file_in_wal_mode(repo):
    conn = store.open_db(repo)
    try:
        assert paths.project_db_path(repo).exists()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_open_db_creates_every_projection_table(repo):
    conn = store.open_db(repo)
    try:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert {"runs", "stories", "subtasks", "phases", "attempts"} <= names


def test_reopening_an_existing_db_keeps_its_rows(repo):
    first = store.open_db(repo)
    first.execute(
        "INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,"
        " status, started_at, config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (RUN_ID, "milestone", str(repo), "main", "m1/", "started", None, "{}"),
    )
    first.commit()
    first.close()

    second = store.open_db(repo)
    try:
        rows = second.execute("SELECT id, workflow FROM runs").fetchall()
    finally:
        second.close()
    assert [(row["id"], row["workflow"]) for row in rows] == [(RUN_ID, "milestone")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v`
Expected: collection error — `ImportError: cannot import name 'store' from 'agent_manager'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/store.py`:

```python
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

import sqlite3
from pathlib import Path

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all green, including the pre-existing `tests/test_models.py`, `tests/test_paths.py` and `tests/test_package.py`.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): open the per-project SQLite projection in WAL mode"
```

---

### Task 2: The append-only journal writer

**Files:**
- Modify: `src/agent_manager/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `agent_manager.paths.run_dir(run_id: str) -> Path` (`src/agent_manager/paths.py:29-33`).
- Produces:
  - `store.JOURNAL_NAME: str = "journal.jsonl"`
  - `store.EventKind = Literal["run_upsert", "story_upsert", "subtask_upsert", "phase_upsert", "attempt_upsert"]`
  - `store.JournalError(RuntimeError)`, `store.MissingJournalError(JournalError)`, `store.CorruptJournalError(JournalError)`
  - `store.JournalLine` — pydantic model, `extra="forbid"`, fields `seq: int`, `ts: datetime`, `run_id: str`, `event: EventKind`, `story: str | None`, `card: str | None`, `phase: str | None`, `attempt: int | None`, `payload: dict[str, Any]`
  - `store.Journal(run_id: str)` with `.run_id: str`, `.path: Path`, `.last_seq() -> int`, `.read() -> list[JournalLine]`, `.append(event, payload, *, story=None, card=None, phase=None, attempt=None) -> JournalLine`

- [ ] **Step 1: Write the failing tests for line shape and sequencing**

Append to `tests/test_store.py`:

```python
def test_append_writes_one_json_line_with_every_coordinate(repo):
    journal = store.Journal(RUN_ID)
    line = journal.append(
        "attempt_upsert",
        {"n": 1, "status": "started"},
        story="8831189b",
        card="ef248597",
        phase="implement",
        attempt=1,
    )

    assert journal.path == paths.run_dir(RUN_ID) / "journal.jsonl"
    text = journal.path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert len(text.splitlines()) == 1

    record = json.loads(text)
    assert record["seq"] == 1
    assert record["run_id"] == RUN_ID
    assert record["event"] == "attempt_upsert"
    assert record["story"] == "8831189b"
    assert record["card"] == "ef248597"
    assert record["phase"] == "implement"
    assert record["attempt"] == 1
    assert record["payload"] == {"n": 1, "status": "started"}
    assert line.seq == 1


def test_run_level_lines_leave_the_lower_coordinates_null(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"status": "started"})

    record = json.loads(journal.path.read_text(encoding="utf-8"))
    assert record["story"] is None
    assert record["card"] is None
    assert record["phase"] is None
    assert record["attempt"] is None


def test_sequence_numbers_increase_by_one(repo):
    journal = store.Journal(RUN_ID)
    seqs = [journal.append("run_upsert", {"i": i}).seq for i in range(3)]
    assert seqs == [1, 2, 3]
    assert [line.seq for line in journal.read()] == [1, 2, 3]


def test_a_reopened_journal_continues_the_sequence(repo):
    first = store.Journal(RUN_ID)
    first.append("run_upsert", {"i": 0})
    first.append("run_upsert", {"i": 1})

    second = store.Journal(RUN_ID)
    assert second.last_seq() == 2
    assert second.append("run_upsert", {"i": 2}).seq == 3
    assert [line.payload["i"] for line in second.read()] == [0, 1, 2]


def test_two_writers_on_one_run_never_reuse_a_sequence_number(repo):
    # Review Focus 3: the sequence is derived from what is on disk at append
    # time, not cached at construction, so a second writer cannot collide.
    first = store.Journal(RUN_ID)
    second = store.Journal(RUN_ID)
    seqs = [
        first.append("run_upsert", {"w": "a"}).seq,
        second.append("run_upsert", {"w": "b"}).seq,
        first.append("run_upsert", {"w": "a"}).seq,
    ]
    assert seqs == [1, 2, 3]
    assert len({line.seq for line in first.read()}) == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "journal or sequence or coordinate or writers" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.store' has no attribute 'Journal'`.

- [ ] **Step 3: Write the journal writer**

Add to `src/agent_manager/store.py`. Extend the import block at the top of the file to:

```python
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_manager import paths
```

and add below `_SCHEMA` / `open_db`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 8 passed.

- [ ] **Step 5: Write the failing tests for the journal error paths**

Append to `tests/test_store.py`:

```python
def test_reading_a_journal_that_does_not_exist_raises(repo):
    journal = store.Journal("run-never-started")
    with pytest.raises(store.MissingJournalError) as excinfo:
        journal.read()
    assert "run-never-started" in str(excinfo.value)
    assert str(journal.path) in str(excinfo.value)


def test_a_non_json_line_names_the_file_and_the_line_number(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")

    with pytest.raises(store.CorruptJournalError) as excinfo:
        journal.read()
    message = str(excinfo.value)
    assert str(journal.path) in message
    assert ":2:" in message


def test_a_truncated_final_line_is_an_error_but_a_blank_one_is_not(repo):
    # Review Focus 2: a crash mid-append leaves either nothing, a blank line, or
    # half a line. The blank one is noise; the half line is data loss and says so.
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert [line.payload["i"] for line in journal.read()] == [0]

    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "run_id": "run-2026\n')
    with pytest.raises(store.CorruptJournalError) as excinfo:
        journal.read()
    assert ":3:" in str(excinfo.value)


def test_an_envelope_with_an_unknown_key_is_rejected(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", {"i": 0})
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "seq": 2,
                    "ts": "2026-09-23T10:00:00+00:00",
                    "run_id": RUN_ID,
                    "event": "run_upsert",
                    "payload": {},
                    "operator": "someone",
                }
            )
            + "\n"
        )

    with pytest.raises(ValidationError) as excinfo:
        journal.read()
    assert "operator" in str(excinfo.value)


def test_a_run_directory_that_cannot_be_created_propagates_the_os_error(repo, tmp_path):
    # The journal defers directory creation to `paths.run_dir`, so the OS error
    # comes through untouched -- the store adds no fallback of its own.
    runs = tmp_path / "data" / "agent-manager" / "runs"
    runs.mkdir(parents=True)
    (runs / "run-blocked").write_text("not a directory")

    with pytest.raises(OSError):
        store.Journal("run-blocked")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 13 passed. These five error paths are already satisfied by the Step 3 implementation (`MissingJournalError`, the `json.JSONDecodeError` wrap, the blank-line skip, `JournalLine`'s `extra="forbid"`, and `paths.run_dir` raising straight out of `Journal.__init__`); this step confirms that rather than adding code. If any of them fails, fix `Journal` before moving on.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): add the append-only journal writer with monotonic sequencing"
```

---

### Task 3: Recording state — journal line first, SQLite row second

**Files:**
- Modify: `src/agent_manager/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `store.open_db`, `store.Journal`, `store.JournalLine` from Tasks 1-2; `models.Run`, `models.StoryRun`, `models.SubtaskRun`, `models.PhaseRun`, `models.Attempt` from `src/agent_manager/models.py`.
- Produces:
  - `store.Store(conn: sqlite3.Connection, journal: Journal)` and `store.Store.open(root: Path, run_id: str) -> Store`
  - `store.Store.run_id: str`, `store.Store.journal: Journal`, `store.Store.connection: sqlite3.Connection`, `store.Store.close() -> None`
  - `store.Store.record_run(run: models.Run) -> JournalLine`
  - `store.Store.record_story(story: models.StoryRun) -> JournalLine`
  - `store.Store.record_subtask(story_id: str, subtask: models.SubtaskRun) -> JournalLine`
  - `store.Store.record_phase(story_id: str, card_id: str, phase: models.PhaseRun) -> JournalLine`
  - `store.Store.record_attempt(story_id: str, card_id: str, phase_name: str, attempt: models.Attempt) -> JournalLine`

- [ ] **Step 1: Write the failing tests for recording**

Append to `tests/test_store.py`:

```python
def _story() -> models.StoryRun:
    return models.StoryRun(
        card_id="8831189b",
        title="Foundations: paths, run store and journal",
        level=0,
        status="started",
        tip_branch="m1/task-add-the-run-state-models-1535b285",
    )


def _subtask(card_id: str = "ef248597", base: str = "main") -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card_id,
        branch=f"m1/task-{card_id}",
        base_branch=base,
        status="started",
        worktree_path=Path(f"/repo/.claude/worktrees/m1/task-{card_id}"),
    )


def test_record_run_writes_the_journal_line_and_the_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        line = st.record_run(_run(repo))
        assert line.event == "run_upsert"
        assert line.card is None

        row = st.connection.execute("SELECT * FROM runs WHERE id = ?", (RUN_ID,)).fetchone()
        assert row["workflow"] == "milestone"
        assert row["status"] == "started"
        assert row["repo_dir"] == str(repo)
        assert json.loads(row["config"])["max_concurrent_stories"] == 2
    finally:
        st.close()

    lines = store.Journal(RUN_ID).read()
    assert [line.event for line in lines] == ["run_upsert"]
    assert lines[0].payload["workflow"] == "milestone"
    assert "stories" not in lines[0].payload


def test_record_story_subtask_phase_and_attempt_write_their_rows(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_subtask("8831189b", _subtask())
        st.record_phase(
            "8831189b",
            "ef248597",
            models.PhaseRun(
                name="implement",
                kind="agent",
                status="started",
                started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
            ),
        )
        attempt_line = st.record_attempt(
            "8831189b",
            "ef248597",
            "implement",
            models.Attempt(n=1, dispatch=_dispatch(), status="ok", exit_code=0, cost=0.42),
        )

        assert attempt_line.story == "8831189b"
        assert attempt_line.card == "ef248597"
        assert attempt_line.phase == "implement"
        assert attempt_line.attempt == 1

        assert st.connection.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1
        assert st.connection.execute("SELECT COUNT(*) FROM subtasks").fetchone()[0] == 1
        phase_row = st.connection.execute("SELECT * FROM phases").fetchone()
        assert phase_row["kind"] == "agent"
        assert phase_row["ended_at"] is None
        attempt_row = st.connection.execute("SELECT * FROM attempts").fetchone()
        assert attempt_row["status"] == "ok"
        assert attempt_row["exit_code"] == 0
        assert attempt_row["cost"] == pytest.approx(0.42)
        assert json.loads(attempt_row["dispatch"])["role"] == "coder"
    finally:
        st.close()

    assert [line.event for line in store.Journal(RUN_ID).read()] == [
        "run_upsert",
        "story_upsert",
        "subtask_upsert",
        "phase_upsert",
        "attempt_upsert",
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k record -v`
Expected: FAIL with `AttributeError: module 'agent_manager.store' has no attribute 'Store'`.

- [ ] **Step 3: Write the Store and its row writers**

Add to the bottom of `src/agent_manager/store.py`. Extend the import block with `from agent_manager import models, paths` (replacing `from agent_manager import paths`), then:

```python
def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _text(value: Path | None) -> str | None:
    return None if value is None else str(value)


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 15 passed.

- [ ] **Step 5: Write the failing test for the write-ordering guarantee**

Append to `tests/test_store.py`:

```python
def test_a_failed_sqlite_write_still_leaves_the_journal_line(repo):
    # §9 line 365: the journal is appended first. Closing the connection is a
    # real SQLite failure -- no mock -- and the line must survive it.
    st = store.Store.open(repo, RUN_ID)
    st.record_run(_run(repo))
    st.close()

    with pytest.raises(sqlite3.Error):
        st.record_story(_story())

    lines = store.Journal(RUN_ID).read()
    assert [line.event for line in lines] == ["run_upsert", "story_upsert"]
    assert lines[1].story == "8831189b"

    reopened = store.Store.open(repo, RUN_ID)
    try:
        assert reopened.connection.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 0
    finally:
        reopened.close()
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_store.py::test_a_failed_sqlite_write_still_leaves_the_journal_line -v`
Expected: PASS. This is a characterisation of the ordering already written in Step 3. If it fails because the journal line is absent, the append is on the wrong side of the row write — fix `record_story`.

- [ ] **Step 7: Write the failing test for repeated records (Review Focus 5)**

Append to `tests/test_store.py`:

```python
def test_recording_a_node_again_updates_it_without_duplicating_or_reordering(repo):
    # Review Focus 5: pending -> started -> done is the same node three times.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        first = _subtask("fdebc746")
        second = _subtask("1535b285", base="m1/task-fdebc746")
        st.record_subtask("8831189b", first)
        st.record_subtask("8831189b", second)

        st.record_subtask("8831189b", first.model_copy(update={"status": "done"}))
        st.record_subtask("8831189b", second.model_copy(update={"status": "failed"}))

        rows = st.connection.execute(
            "SELECT card_id, status FROM subtasks WHERE run_id = ? AND story_id = ?"
            " ORDER BY position",
            (RUN_ID, "8831189b"),
        ).fetchall()
    finally:
        st.close()

    assert [(row["card_id"], row["status"]) for row in rows] == [
        ("fdebc746", "done"),
        ("1535b285", "failed"),
    ]


def test_run_status_transitions_replace_the_single_run_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        run = _run(repo)
        st.record_run(run)
        st.record_run(run.model_copy(update={"status": "done"}))
        rows = st.connection.execute("SELECT id, status FROM runs").fetchall()
    finally:
        st.close()

    assert [(row["id"], row["status"]) for row in rows] == [(RUN_ID, "done")]
    assert len(store.Journal(RUN_ID).read()) == 2
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 18 passed. If `position` drifts, the conflict clause is updating it — it must not.

- [ ] **Step 9: Write the failing test for worktree independence**

Append to `tests/test_store.py`:

```python
def test_recording_a_run_writes_nothing_into_the_repo_directory(repo, tmp_path):
    # D5 and §4: the two stores are independent of the board and of the
    # worktree. Nothing agent-manager writes may land in the repo.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_subtask("8831189b", _subtask())
        st.record_phase("8831189b", "ef248597", models.PhaseRun(name="implement", kind="agent"))
        st.record_attempt(
            "8831189b", "ef248597", "implement", models.Attempt(n=1, dispatch=_dispatch())
        )
    finally:
        st.close()

    assert list(repo.iterdir()) == []
    assert paths.project_db_path(repo).is_relative_to(tmp_path / "data")
    assert store.Journal(RUN_ID).path.is_relative_to(tmp_path / "data")
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 19 passed.

- [ ] **Step 11: Run the full suite and commit**

Run: `uv run pytest`
Expected: all green.

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): record state with the journal line ahead of the row"
```

---

### Task 4: Reading the projection back as a model tree

**Files:**
- Modify: `src/agent_manager/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `store.Store` and its row writers from Task 3.
- Produces: `store.Store.load_run(run_id: str) -> models.Run | None` — the projection assembled back into the §9 tree, stories/subtasks/phases in `position` order and attempts in `n` order, or `None` when no run row exists.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_store.py`:

```python
def _record_full_run(st: store.Store, repo: Path) -> None:
    """One run, two subtasks, three phases, a finished and an in-flight attempt."""
    st.record_run(_run(repo))
    st.record_story(_story())

    st.record_subtask("8831189b", _subtask("fdebc746").model_copy(update={"status": "done"}))
    st.record_phase(
        "8831189b",
        "fdebc746",
        models.PhaseRun(
            name="verify",
            kind="deterministic",
            status="done",
            started_at=datetime(2026, 9, 23, 10, 5, tzinfo=timezone.utc),
            ended_at=datetime(2026, 9, 23, 10, 6, tzinfo=timezone.utc),
        ),
    )

    st.record_subtask("8831189b", _subtask("ef248597", base="m1/task-fdebc746"))
    st.record_phase(
        "8831189b",
        "ef248597",
        models.PhaseRun(
            name="explore",
            kind="agent",
            status="done",
            started_at=datetime(2026, 9, 23, 10, 10, tzinfo=timezone.utc),
            ended_at=datetime(2026, 9, 23, 10, 12, tzinfo=timezone.utc),
        ),
    )
    st.record_attempt(
        "8831189b",
        "ef248597",
        "explore",
        models.Attempt(
            n=1,
            dispatch=_dispatch(card="ef248597", phase="explore"),
            status="ok",
            exit_code=0,
            duration=31.25,
            tokens_in=8000,
            tokens_out=1500,
            cost=0.31,
            prompt_path=Path(f"/runs/{RUN_ID}/ef248597/explore.1/prompt.txt"),
            result_path=Path(f"/runs/{RUN_ID}/ef248597/explore.1/result.json"),
            stdout_path=Path(f"/runs/{RUN_ID}/ef248597/explore.1/stdout.log"),
        ),
    )
    st.record_phase(
        "8831189b",
        "ef248597",
        models.PhaseRun(
            name="implement",
            kind="agent",
            status="started",
            started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
        ),
    )
    st.record_attempt(
        "8831189b",
        "ef248597",
        "implement",
        models.Attempt(n=1, dispatch=_dispatch(card="ef248597", phase="implement")),
    )


def test_load_run_rebuilds_the_tree_in_recorded_order(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        loaded = st.load_run(RUN_ID)
    finally:
        st.close()

    assert loaded is not None
    assert loaded.id == RUN_ID
    assert loaded.repo_dir == repo
    assert loaded.started_at == datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    assert loaded.config.harness_map["coder"].model == "sonnet"

    story = loaded.stories[0]
    assert [subtask.card_id for subtask in story.subtasks] == ["fdebc746", "ef248597"]
    subtask = story.subtasks[1]
    assert subtask.base_branch == "m1/task-fdebc746"
    assert [phase.name for phase in subtask.phases] == ["explore", "implement"]

    finished = subtask.phases[0].attempts[0]
    assert finished.status == "ok"
    assert finished.cost == pytest.approx(0.31)
    assert finished.stdout_path == Path(f"/runs/{RUN_ID}/ef248597/explore.1/stdout.log")
    assert finished.dispatch.role == "coder"

    in_flight = subtask.phases[1].attempts[0]
    assert in_flight.status == "started"
    assert in_flight.exit_code is None
    assert in_flight.cost is None


def test_load_run_returns_none_for_an_unknown_run(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        assert st.load_run("run-never-started") is None
    finally:
        st.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k load_run -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'load_run'`.

- [ ] **Step 3: Write the reader**

Add to the `Store` class in `src/agent_manager/store.py`, after the `record_*` methods:

```python
    # -- reading -----------------------------------------------------------

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 21 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): read the projection back into the run state tree"
```

---

### Task 5: Replay and `rebuild_from_journal`

**Files:**
- Modify: `src/agent_manager/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `store.Journal.read`, `store.JournalLine`, `store.Store` row writers and `store.Store.load_run` from Tasks 2-4.
- Produces:
  - `store.replay(lines: Iterable[JournalLine]) -> models.Run` — folds journal lines, in sequence order, into the §9 tree.
  - `store.Store.rebuild_from_journal(run_id: str) -> models.Run` — replays that run's journal, deletes only that run's rows, rewrites them, and returns the replayed tree.

- [ ] **Step 1: Write the failing test for the card's required truncation scenario**

Append to `tests/test_store.py`:

```python
def _truncate_db(repo: Path) -> Path:
    """Wipe the projection the way a crashed or corrupted disk would.

    The WAL sidecars are removed too: zeroing the main file while a populated
    `-wal` survives would not actually lose the rows.
    """
    db_path = paths.project_db_path(repo)
    db_path.write_bytes(b"")
    for suffix in ("-wal", "-shm"):
        db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
    return db_path


def test_a_db_truncated_mid_run_is_rebuilt_from_its_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    _record_full_run(st, repo)
    before = st.load_run(RUN_ID)
    st.close()

    db_path = _truncate_db(repo)
    assert db_path.stat().st_size == 0

    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        assert rebuilt.load_run(RUN_ID) is None
        returned = rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
    finally:
        rebuilt.close()

    assert after == before
    assert returned == before
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_a_db_truncated_mid_run_is_rebuilt_from_its_journal -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'rebuild_from_journal'`.

- [ ] **Step 3: Write `replay` and `rebuild_from_journal`**

Extend the import block in `src/agent_manager/store.py` with `from collections.abc import Iterable`, then add `replay` as a module-level function above `class Store`:

```python
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
```

Then add to the `Store` class, after `load_run`:

```python
    # -- rebuild -----------------------------------------------------------

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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_store.py::test_a_db_truncated_mid_run_is_rebuilt_from_its_journal -v`
Expected: PASS.

- [ ] **Step 5: Write the failing tests for idempotence and in-flight attempts**

Append to `tests/test_store.py`:

```python
def test_rebuilding_twice_changes_nothing_and_duplicates_nothing(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        once = st.rebuild_from_journal(RUN_ID)
        first = st.load_run(RUN_ID)
        twice = st.rebuild_from_journal(RUN_ID)
        second = st.load_run(RUN_ID)
        counts = {
            table: st.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("runs", "stories", "subtasks", "phases", "attempts")
        }
    finally:
        st.close()

    assert once == twice
    assert first == second
    assert counts == {"runs": 1, "stories": 1, "subtasks": 2, "phases": 3, "attempts": 2}


def test_an_in_flight_attempt_survives_the_rebuild_as_started(repo):
    # §9 lines 370-373: the store preserves `started` with nothing terminal.
    # Discarding it is the engine's job, not the store's.
    st = store.Store.open(repo, RUN_ID)
    _record_full_run(st, repo)
    st.close()

    _truncate_db(repo)
    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        run = rebuilt.rebuild_from_journal(RUN_ID)
        row = rebuilt.connection.execute(
            "SELECT * FROM attempts WHERE phase = 'implement'"
        ).fetchone()
    finally:
        rebuilt.close()

    attempt = run.stories[0].subtasks[1].phases[1].attempts[0]
    assert attempt.status == "started"
    assert attempt.exit_code is None
    assert attempt.duration is None
    assert attempt.tokens_in is None
    assert attempt.tokens_out is None
    assert attempt.cost is None
    assert row["status"] == "started"
    assert row["exit_code"] is None

    terminal = run.stories[0].subtasks[1].phases[0].attempts[0]
    assert terminal.status == "ok"
    assert terminal.exit_code == 0


def test_rebuild_picks_up_a_journal_line_whose_row_never_landed(repo):
    # The other half of the ordering guarantee: the row the failed SQLite write
    # never produced is materialised by the rebuild.
    st = store.Store.open(repo, RUN_ID)
    st.record_run(_run(repo))
    st.close()
    with pytest.raises(sqlite3.Error):
        st.record_story(_story())

    reopened = store.Store.open(repo, RUN_ID)
    try:
        run = reopened.rebuild_from_journal(RUN_ID)
        row = reopened.connection.execute("SELECT * FROM stories").fetchone()
    finally:
        reopened.close()

    assert [story.card_id for story in run.stories] == ["8831189b"]
    assert row["card_id"] == "8831189b"
    assert row["status"] == "started"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 25 passed.

- [ ] **Step 7: Write the failing tests for the loud-failure paths**

Append to `tests/test_store.py`:

```python
def _append_raw(journal: store.Journal, record: dict) -> None:
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def test_a_line_with_an_unknown_payload_key_raises_out_of_rebuild(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        journal = st.journal
        _append_raw(
            journal,
            {
                "seq": journal.last_seq() + 1,
                "ts": "2026-09-23T10:20:00+00:00",
                "run_id": RUN_ID,
                "event": "attempt_upsert",
                "story": "8831189b",
                "card": "ef248597",
                "phase": "implement",
                "attempt": 1,
                "payload": {
                    "n": 1,
                    "dispatch": _dispatch().model_dump(mode="json"),
                    "tokens": 10,
                },
            },
        )
        with pytest.raises(ValidationError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()
    assert "tokens" in str(excinfo.value)


def test_a_line_with_an_invalid_status_raises_out_of_rebuild(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        journal = st.journal
        _append_raw(
            journal,
            {
                "seq": journal.last_seq() + 1,
                "ts": "2026-09-23T10:20:00+00:00",
                "run_id": RUN_ID,
                "event": "story_upsert",
                "story": "8831189b",
                "card": None,
                "phase": None,
                "attempt": None,
                "payload": {
                    "card_id": "8831189b",
                    "title": "Foundations",
                    "level": 0,
                    "status": "finished",
                    "tip_branch": None,
                },
            },
        )
        with pytest.raises(ValidationError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()
    message = str(excinfo.value)
    for allowed in ("pending", "started", "done", "failed", "escalated"):
        assert allowed in message


def test_a_corrupt_line_raises_out_of_rebuild_naming_the_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        with st.journal.path.open("a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
        with pytest.raises(store.CorruptJournalError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
        message = str(excinfo.value)
        assert str(st.journal.path) in message
    finally:
        st.close()


def test_rebuilding_a_run_with_no_journal_raises(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.MissingJournalError) as excinfo:
            st.rebuild_from_journal("run-never-started")
        assert st.load_run("run-never-started") is None
    finally:
        st.close()
    assert "run-never-started" in str(excinfo.value)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 29 passed. All four are satisfied by the Step 3 implementation letting errors propagate; if any is swallowed, remove the `try`/`except` that is doing it.

- [ ] **Step 9: Write the failing test for a journal with no head (Review Focus 1)**

Append to `tests/test_store.py`:

```python
def test_a_journal_whose_head_is_missing_raises_a_journal_error(repo):
    # Review Focus 1: a story event with no run_upsert before it must name the
    # problem, not fail with an AttributeError on None.
    journal = store.Journal(RUN_ID)
    journal.append(
        "story_upsert",
        _story().model_dump(mode="json", exclude={"subtasks"}),
        story="8831189b",
    )
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.JournalError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()
    assert "run_upsert" in str(excinfo.value)


def test_a_line_naming_an_unknown_parent_raises_a_journal_error(repo):
    journal = store.Journal(RUN_ID)
    journal.append("run_upsert", _run(repo).model_dump(mode="json", exclude={"stories"}))
    journal.append(
        "subtask_upsert",
        _subtask().model_dump(mode="json", exclude={"phases"}),
        story="never-recorded",
        card="ef248597",
    )
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(store.JournalError) as excinfo:
            st.rebuild_from_journal(RUN_ID)
    finally:
        st.close()
    assert "never-recorded" in str(excinfo.value)
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 31 passed. Both are satisfied by the `raise JournalError(...)` branches in `replay`; if either raises `AttributeError` instead, fix `replay`.

- [ ] **Step 11: Write the failing test for rebuild scoping (Review Focus 4)**

Append to `tests/test_store.py`:

```python
def test_rebuilding_one_run_leaves_another_runs_rows_alone(repo):
    # Review Focus 4: one project DB holds every run. A rebuild is scoped.
    other_id = "run-2026-09-22-07"
    first = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(first, repo)
    finally:
        first.close()

    second = store.Store.open(repo, other_id)
    try:
        second.record_run(_run(repo, run_id=other_id))
        second.record_story(_story())
        second.record_subtask("8831189b", _subtask("aaaa1111"))
        untouched = second.load_run(other_id)
    finally:
        second.close()

    rebuilding = store.Store.open(repo, RUN_ID)
    try:
        rebuilding.rebuild_from_journal(RUN_ID)
        assert rebuilding.load_run(other_id) == untouched
        assert rebuilding.load_run(RUN_ID) is not None
    finally:
        rebuilding.close()
```

- [ ] **Step 12: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 32 passed. If run-b's rows vanish, `_delete_run` is missing a `WHERE run_id = ?`.

- [ ] **Step 13: Run the full suite**

Run: `uv run pytest`
Expected: all green — `tests/test_store.py` plus the pre-existing `tests/test_models.py`, `tests/test_paths.py` and `tests/test_package.py`.

- [ ] **Step 14: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): rebuild the projection from the append-only journal"
```
