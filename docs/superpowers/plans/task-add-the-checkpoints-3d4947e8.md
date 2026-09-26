<!-- task-pipeline: validated -->
# Subtask 3d4947e8: Add the checkpoints table to the store

Parent story: a6c7bff3 "Checkpoints and resume" (milestone 84c3b532). Source of truth: plan Task 4.1 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1162-1176`) and the pygents-engine spec §6 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:262-303`). If the two disagree, follow the plan's Interfaces block. The spec's prose at line 278 writes `latest_checkpoint(run_id, card_id)`, but the plan and this card both give `latest_checkpoint(card_id)` bound to the store's own run.

## Scope

Only `src/agent_manager/store.py` and `tests/test_store.py` change.

- Append the spec §6 `CREATE TABLE IF NOT EXISTS checkpoints (...)` to `_SCHEMA`, using the same text as spec lines 265-275 and the same style as the existing tables (`store.py:27-93`). The table has columns `run_id`, `card_id`, `seq`, `workflow`, `digest`, `reason` (with `CHECK (reason IN ('turn','parked','done','escalated'))`), `agent` and `saved_at`, and `PRIMARY KEY (run_id, card_id, seq)`. `open_db` already applies the schema idempotently, so it needs no other change.
- Add `Checkpoint`, a frozen plain dataclass (it is internal state, so not Pydantic). Its fields are `run_id: str`, `card_id: str`, `seq: int`, `workflow: str`, `digest: str`, `reason: str`, `agent: dict` and `saved_at: datetime`.
- Add three public methods to `Store`. Each one holds `self._lock` for its whole body, as the `record_*` methods do.
  - `save_checkpoint(card_id, *, workflow, digest, reason, agent, saved_at) -> Checkpoint` inserts one row under `self.run_id` and commits it. It assigns `seq` itself: 0 for the first row of `(self.run_id, card_id)`, and one more than the current maximum after that. `agent` is stored as `json.dumps(agent, sort_keys=True)`. `saved_at` is stored as ISO-8601 text, which is how `_iso` already stores datetimes. The method returns the `Checkpoint` it wrote.
  - `latest_checkpoint(card_id) -> Checkpoint | None` returns the row with the highest `seq` for `(self.run_id, card_id)`, or `None` if there is none. It does not filter on `reason`.
  - `latest_open_checkpoint(card_id, workflow) -> Checkpoint | None` looks across all runs. First it finds the card's newest row in any run and with any workflow, ordered by `saved_at` then `seq`. If that row's reason is `done`, it returns `None`. Otherwise it returns the newest row for that card and `workflow` whose `reason` is `turn`, `parked` or `escalated`, or `None` if there is none.
- Rows read back become `Checkpoint` values: `agent` goes through `json.loads` and `saved_at` through `datetime.fromisoformat`.

## Invariants

- Checkpoints are not part of the journal tree (G10):
  - `save_checkpoint` never touches `Journal`.
  - `EventKind` (`store.py:132-134`) gets no new kind.
  - `rebuild_from_journal` leaves `checkpoints` rows alone.
  - `SubtaskSummary`, journal lines and phase/attempt rows do not change.
- This is a deliberate, documented exception to the class docstring's rule that no public method writes a row on its own. Update the `Store` docstring so it says checkpoints are a row-only table outside the journal.
- The `agent` dict round-trips byte-equal: `json.dumps(saved.agent, sort_keys=True)` equals the stored text, and so does the value read back with `latest_checkpoint`.
- No `pygents` import, no hooks, no `ContextVar`, and nothing under `src/agent_manager/runtime/`.

## Error paths

- A `reason` outside the four allowed values is rejected by the `CHECK` constraint as `sqlite3.IntegrityError`. The error propagates unchanged, the lock is released, and no row is left behind. There is no separate Python-side validation.
- A duplicate `(run_id, card_id, seq)` cannot happen with a single writer, because `seq` is computed under the lock (P2).

## Tests (all in `tests/test_store.py`)

Every test here is in the Steps tier. That follows the placement rule in `2026-09-23-agent-manager-design.md:497-511` §14, refined by `2026-09-25-pygents-engine-design.md:379-401` §9: they run against a real temporary SQLite DB and journal, with no network and no harness dispatch. They reuse the existing `repo` fixture (`tests/test_store.py:26-40`), and they are not engine-parametrised, because store access does not depend on the engine.

1. `seq` starts at 0 and increments per card within a run. A second card starts again from 0.
2. `latest_checkpoint` returns the row with the highest `seq` for this store's run. It returns `None` for an unknown card, and it ignores another run's rows for the same card.
3. `save_checkpoint(..., reason="bogus")` raises `sqlite3.IntegrityError`, and no row is written.
4. `latest_open_checkpoint` returns an older run's `parked` row when read from a new run's store. It ignores rows for a different `workflow`.
5. `latest_open_checkpoint` returns `None` when the card's newest row across runs is `done`.
6. The `agent` dict, nested and with unsorted keys, round-trips byte-equal through JSON, both through the returned `Checkpoint` and after reading it back.
7. `rebuild_from_journal` leaves the checkpoints rows alone, and `save_checkpoint` appends no journal line.

## Out of scope

The following belong to sibling cards and must not be started here:

- `runtime/checkpoint.py`, the `BEFORE_TURN` hook, `Parked`, and the `done`/`escalated` saves (921ed349).
- `run_subtask(resume_from=...)`, `CheckpointMismatch` and `Agent.from_dict` (5698e4f6).
- `--engine` plumbing (7fdec762).
- `am resume` and milestone relaunch continuation (02890d5d).
- The supervisor tree, exactly-once phases, benchmarking prompts and upstream pygents fixes.

Do not touch `cli.py`, `orchestrate.py` or `integration.py`.

## Verification

Run `uv run pytest`. The whole default suite must pass, including `tests/e2e`. There are no typecheck or lint commands. Commit as `feat(store): add the checkpoints table` only when asked to.

---

# Checkpoints Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a row-only `checkpoints` table to the SQLite projection, with a frozen `Checkpoint` dataclass and three `Store` methods to save a checkpoint, read the latest one for this run, and find a card's open checkpoint across runs.

**Architecture:** The table is appended to `store._SCHEMA`, so `open_db` creates it idempotently. The `Store` methods hold the existing re-entrant `self._lock` and talk to `self._conn` directly. They never touch `Journal`, because checkpoints sit outside the journaled §9 tree, and `rebuild_from_journal` / `_delete_run` stay unchanged so checkpoint rows survive a rebuild.

**Tech Stack:** Python 3, `sqlite3`, `dataclasses`, `json`, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-add-the-checkpoints-3d4947e8/docs/superpowers/specs/task-add-the-checkpoints-3d4947e8-design.md` (prepended above). Upstream sources: `docs/superpowers/plans/2026-09-25-pygents-engine.md:1162-1176` (Task 4.1 Interfaces block, authoritative) and `docs/superpowers/specs/2026-09-25-pygents-engine-design.md:262-303` (§6).

**Branch / worktree:** `m6/task-add-the-checkpoints-3d4947e8` at `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-add-the-checkpoints-3d4947e8`, cut from `m6/task-add-goto-revision-loops-b904b9e7`. This plan assumes no sibling subtask's code exists: there is no `src/agent_manager/runtime/checkpoint.py`, no `Parked`, and no `--engine` plumbing, and none is needed. All paths below are relative to this worktree.

## Global Constraints

- Only `src/agent_manager/store.py` and `tests/test_store.py` change. Do not touch `cli.py`, `orchestrate.py` or `integration.py`, and create nothing under `src/agent_manager/runtime/` or `tests/runtime/`.
- No `pygents` import, no hooks, no `ContextVar` in `store.py`.
- `EventKind` stays exactly `Literal["run_upsert", "story_upsert", "subtask_upsert", "phase_upsert", "attempt_upsert"]`. `save_checkpoint` never calls `self._journal`.
- `rebuild_from_journal` and `_delete_run` are not modified. They must leave `checkpoints` rows alone.
- `Checkpoint` is a frozen plain dataclass, not Pydantic (CLAUDE.md: plain dataclasses for internal-only state).
- `reason` is validated only by the SQL `CHECK (reason IN ('turn', 'parked', 'done', 'escalated'))`. The failure surfaces as `sqlite3.IntegrityError`, with no extra Python-side check.
- `agent` is stored as `json.dumps(agent, sort_keys=True)`. `saved_at` is stored as ISO-8601 text via `_iso`.
- Every new `Store` method holds `self._lock` for its whole body.
- Tests are Steps tier: they are appended to `tests/test_store.py`, use the existing `repo` fixture with a real temp SQLite DB and journal, and are not engine-parametrised.
- Verification: `uv run pytest`. The whole default suite must be green, including `tests/e2e`. There is no lint or typecheck command.
- Commit message for the card: `feat(store): add the checkpoints table`. Only commit when the orchestrating workflow asks for commits.

## Review Focus

1. A card that finished (`done`) in an earlier run and got a newer `turn` row in a later run. Only the newest row decides, so `latest_open_checkpoint` returns the later run's `turn` row, not `None`. The test goes in Task 2.
2. The card's newest row is `done` under a *different* workflow than the one asked about. The spec says the newest row "in any run and with any workflow", so the card is closed and the result is `None`. The test goes in Task 2.
3. Two rows with the same `saved_at`. `seq` breaks the tie, so the higher-`seq` row counts as newest, whether it is `parked` (returned) or `done` (closes the card). The test goes in Task 2.
4. An `agent` dict that is not JSON-serialisable, such as one holding a `datetime`. `save_checkpoint` raises `TypeError`, writes no row, releases the lock and does not spend a `seq`. The test goes in Task 1.
5. The caller mutates its `agent` dict after saving. The returned `Checkpoint` must not change with it, because its `agent` is decoded from the stored text rather than aliased to the caller's dict. The test goes in Task 1.

---

### Task 1: The `checkpoints` table, `Checkpoint`, `save_checkpoint` and `latest_checkpoint`

**Files:**
- Modify: `src/agent_manager/store.py:14-25` (imports), `src/agent_manager/store.py:75-93` (`_SCHEMA` tail), `src/agent_manager/store.py:490-505` (add `Checkpoint` before `Store`; update the `Store` docstring), `src/agent_manager/store.py:774-785` (add the checkpoint methods after `load_run`)
- Test: `tests/test_store.py:14-23` (imports), and new tests appended after the last line (`tests/test_store.py:1728`)

**Interfaces:**
- Consumes: `store._iso(value: datetime | None) -> str | None` (`store.py:261`), `Store._lock` (an `RLock`), `Store._conn` (a `sqlite3.Connection` with `row_factory = sqlite3.Row`), `Store.run_id -> str`. Test helpers already in `tests/test_store.py`: the `repo` fixture, `RUN_ID`, `_record_full_run(st, repo)`, `_held_elsewhere(lock) -> bool`.
- Produces:
  - `store.Checkpoint`, a `@dataclass(frozen=True)` with `run_id: str`, `card_id: str`, `seq: int`, `workflow: str`, `digest: str`, `reason: str`, `agent: dict`, `saved_at: datetime`.
  - `store._checkpoint_from_row(row: sqlite3.Row) -> Checkpoint`.
  - `Store.save_checkpoint(card_id: str, *, workflow: str, digest: str, reason: str, agent: dict, saved_at: datetime) -> Checkpoint`.
  - `Store.latest_checkpoint(card_id: str) -> Checkpoint | None`.
  - Test helpers `_at(minute: int) -> datetime` and `_save_checkpoint(st, card_id, *, reason="turn", workflow="task", digest="sha256:aaa", agent=None, saved_at=None) -> store.Checkpoint`, which Task 2 reuses.

- [ ] **Step 1: Add the test imports**

In `tests/test_store.py`, replace:

```python
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
```

with:

```python
import dataclasses
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/test_store.py` (after `test_a_stopped_run_survives_a_rebuild_from_the_journal`):

```python


# -- checkpoints ---------------------------------------------------------------
#
# A row-only table outside the journal (pygents-engine spec §6). Steps tier:
# real temp DB and journal, no harness, not engine-parametrised.

OTHER_RUN_ID = "run-2026-09-24-01"


def _at(minute: int) -> datetime:
    return datetime(2026, 9, 25, 12, minute, tzinfo=timezone.utc)


def _save_checkpoint(
    st: store.Store,
    card_id: str = "ef248597",
    *,
    reason: str = "turn",
    workflow: str = "task",
    digest: str = "sha256:aaa",
    agent: dict | None = None,
    saved_at: datetime | None = None,
) -> store.Checkpoint:
    return st.save_checkpoint(
        card_id,
        workflow=workflow,
        digest=digest,
        reason=reason,
        agent={"turn": 0} if agent is None else agent,
        saved_at=_at(0) if saved_at is None else saved_at,
    )


def test_open_db_creates_the_checkpoints_table(repo):
    conn = store.open_db(repo)
    try:
        columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(checkpoints)").fetchall()
        ]
    finally:
        conn.close()
    assert columns == [
        "run_id",
        "card_id",
        "seq",
        "workflow",
        "digest",
        "reason",
        "agent",
        "saved_at",
    ]


def test_save_checkpoint_numbers_each_cards_rows_from_zero(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        seqs = [_save_checkpoint(st, "card-a", saved_at=_at(i)).seq for i in range(3)]
        other = _save_checkpoint(st, "card-b", saved_at=_at(3))
        rows = st.connection.execute(
            "SELECT run_id, card_id, seq FROM checkpoints ORDER BY card_id, seq"
        ).fetchall()
    finally:
        st.close()

    assert seqs == [0, 1, 2]
    assert other.seq == 0
    assert other.run_id == RUN_ID
    assert [tuple(row) for row in rows] == [
        (RUN_ID, "card-a", 0),
        (RUN_ID, "card-a", 1),
        (RUN_ID, "card-a", 2),
        (RUN_ID, "card-b", 0),
    ]


def test_latest_checkpoint_is_the_highest_seq_of_this_stores_run(repo):
    other = store.Store.open(repo, OTHER_RUN_ID)
    try:
        for i in range(5):
            _save_checkpoint(other, "card-a", digest="sha256:other", saved_at=_at(i))
    finally:
        other.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(10))
        newest = _save_checkpoint(st, "card-a", reason="parked", saved_at=_at(11))
        latest = st.latest_checkpoint("card-a")
        unknown = st.latest_checkpoint("card-never-saved")
    finally:
        st.close()

    assert latest == newest
    assert latest is not None
    assert latest.run_id == RUN_ID
    assert latest.seq == 1
    assert latest.reason == "parked"
    assert latest.digest == "sha256:aaa"
    assert latest.saved_at == _at(11)
    assert unknown is None


def test_a_checkpoint_with_an_unknown_reason_is_refused_and_writes_nothing(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _save_checkpoint(st, "card-a", reason="bogus")

        assert _held_elsewhere(st._lock) is False
        assert st.connection.in_transaction is False
        assert st.connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
        # The refused save spent no seq.
        assert _save_checkpoint(st, "card-a").seq == 0
    finally:
        st.close()


def test_a_checkpoint_whose_agent_is_not_json_is_refused_and_writes_nothing(repo):
    # Review Focus 4: the agent dict is encoded before anything is written.
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(TypeError):
            _save_checkpoint(st, "card-a", agent={"when": _at(0)})

        assert _held_elsewhere(st._lock) is False
        assert st.connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
        assert _save_checkpoint(st, "card-a").seq == 0
    finally:
        st.close()


def test_a_checkpoints_agent_round_trips_byte_equal(repo):
    agent = {
        "zeta": [3, {"b": 2, "a": 1}],
        "alpha": {"nested": {"y": None, "x": "é"}},
        "count": 7,
    }
    st = store.Store.open(repo, RUN_ID)
    try:
        saved = _save_checkpoint(st, "card-a", agent=agent)
        stored = st.connection.execute(
            "SELECT agent FROM checkpoints WHERE run_id = ? AND card_id = ?",
            (RUN_ID, "card-a"),
        ).fetchone()["agent"]
        read = st.latest_checkpoint("card-a")
    finally:
        st.close()

    assert stored == json.dumps(agent, sort_keys=True)
    assert json.dumps(saved.agent, sort_keys=True) == stored
    assert read is not None
    assert json.dumps(read.agent, sort_keys=True) == stored
    assert read.agent == agent
    assert read == saved

    # Review Focus 5: the returned value is not aliased to the caller's dict.
    agent["count"] = 8
    assert saved.agent["count"] == 7

    with pytest.raises(dataclasses.FrozenInstanceError):
        saved.seq = 9  # type: ignore[misc]


def test_checkpoints_stay_out_of_the_journal_and_survive_a_rebuild(repo):
    # G10: checkpoints are not part of the journaled tree.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        before = [line.seq for line in st.journal.read()]
        saved = _save_checkpoint(st, "ef248597", reason="parked")
        after = [line.seq for line in st.journal.read()]
        st.rebuild_from_journal(RUN_ID)
        kept = st.latest_checkpoint("ef248597")
    finally:
        st.close()

    assert after == before
    assert kept == saved
    assert set(get_args(store.EventKind)) == {
        "run_upsert",
        "story_upsert",
        "subtask_upsert",
        "phase_upsert",
        "attempt_upsert",
    }
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k checkpoint -v`

Expected: FAIL. `test_open_db_creates_the_checkpoints_table` fails its assertion (`[] == [...]`, since `PRAGMA table_info` of a missing table returns no rows). The other six fail with `AttributeError: 'Store' object has no attribute 'save_checkpoint'`.

- [ ] **Step 4: Add the `dataclass` import to `store.py`**

In `src/agent_manager/store.py`, replace:

```python
import json
import os
import sqlite3
import threading
from collections.abc import Iterable
```

with:

```python
import json
import os
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
```

- [ ] **Step 5: Append the `checkpoints` table to `_SCHEMA`**

In `src/agent_manager/store.py`, replace the tail of `_SCHEMA`:

```python
    dispatch     TEXT NOT NULL,
    PRIMARY KEY (run_id, story_id, card_id, phase, n)
);
"""
```

with:

```python
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
"""
```

- [ ] **Step 6: Add `Checkpoint` and its row reader before `class Store`**

In `src/agent_manager/store.py`, replace:

```python
    return run


class Store:
```

with:

```python
    return run


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


def _checkpoint_from_row(row: sqlite3.Row) -> Checkpoint:
    return Checkpoint(
        run_id=row["run_id"],
        card_id=row["card_id"],
        seq=row["seq"],
        workflow=row["workflow"],
        digest=row["digest"],
        reason=row["reason"],
        agent=json.loads(row["agent"]),
        saved_at=datetime.fromisoformat(row["saved_at"]),
    )


class Store:
```

- [ ] **Step 7: Update the `Store` docstring**

In `src/agent_manager/store.py`, replace:

```python
    Every `record_*` appends the journal line first and writes the row second.
    There is deliberately no public method that writes a row on its own.
```

with:

```python
    Every `record_*` appends the journal line first and writes the row second.
    There is deliberately no public method that writes a tree row on its own.
    The one exception is `checkpoints` (pygents spec §6): a row-only table
    outside the journal. `save_checkpoint` writes its row and never touches the
    journal, and `rebuild_from_journal` leaves those rows alone.
```

- [ ] **Step 8: Add `save_checkpoint` and `latest_checkpoint`**

In `src/agent_manager/store.py`, replace:

```python
        with self._lock:
            return load_run(self._conn, run_id)

    # -- rebuild -------------------------------------------------------------
```

with:

```python
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
    ) -> Checkpoint:
        """Write the next checkpoint of `card_id` under this store's run.

        `seq` is 0 for the card's first row in this run and one past the
        highest after that. An unknown `reason` is refused by the table's
        `CHECK` as `sqlite3.IntegrityError`; the statement is rolled back, the
        error propagates unchanged and no `seq` is spent.
        """
        with self._lock:
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
                self._conn.commit()
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
            )

    def latest_checkpoint(self, card_id: str) -> Checkpoint | None:
        """The highest-`seq` checkpoint of `card_id` in this store's run, any reason."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checkpoints WHERE run_id = ? AND card_id = ?"
                " ORDER BY seq DESC LIMIT 1",
                (self.run_id, card_id),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)

    # -- rebuild -------------------------------------------------------------
```

- [ ] **Step 9: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k checkpoint -v`

Expected: PASS for all seven tests (`test_open_db_creates_the_checkpoints_table`, `test_save_checkpoint_numbers_each_cards_rows_from_zero`, `test_latest_checkpoint_is_the_highest_seq_of_this_stores_run`, `test_a_checkpoint_with_an_unknown_reason_is_refused_and_writes_nothing`, `test_a_checkpoint_whose_agent_is_not_json_is_refused_and_writes_nothing`, `test_a_checkpoints_agent_round_trips_byte_equal`, `test_checkpoints_stay_out_of_the_journal_and_survive_a_rebuild`).

- [ ] **Step 10: Run the whole store file**

Run: `uv run pytest tests/test_store.py -v`

Expected: PASS for every test. The existing `test_open_db_creates_every_projection_table` and `test_rebuilding_twice_changes_nothing_and_duplicates_nothing` still pass, because they only check a subset of tables or a fixed list of tables.

- [ ] **Step 11: Commit (only if the orchestrating workflow commits per task)**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): add the checkpoints table"
```

---

### Task 2: `latest_open_checkpoint` across runs

**Files:**
- Modify: `src/agent_manager/store.py` (add `latest_open_checkpoint` directly after `latest_checkpoint`, which Task 1 added)
- Test: `tests/test_store.py` (append after `test_checkpoints_stay_out_of_the_journal_and_survive_a_rebuild`)

**Interfaces:**
- Consumes (from Task 1): `store.Checkpoint`, `store._checkpoint_from_row(row) -> Checkpoint`, `Store.save_checkpoint(card_id, *, workflow, digest, reason, agent, saved_at) -> Checkpoint`, `Store.latest_checkpoint(card_id) -> Checkpoint | None`. Test helpers `_at(minute)`, `_save_checkpoint(st, card_id, *, reason, workflow, digest, agent, saved_at)`, `OTHER_RUN_ID`, `RUN_ID`.
- Produces: `Store.latest_open_checkpoint(card_id: str, workflow: str) -> Checkpoint | None`. Siblings 921ed349, 5698e4f6 and 02890d5d consume it later.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python


def test_latest_open_checkpoint_finds_an_older_runs_parked_row(repo):
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(old, "card-a", reason="turn", saved_at=_at(0))
        parked = _save_checkpoint(old, "card-a", reason="parked", saved_at=_at(1))
        # A newer, still-open row under another workflow: ignored by the filter.
        _save_checkpoint(old, "card-a", reason="turn", workflow="integrate", saved_at=_at(2))
    finally:
        old.close()

    new = store.Store.open(repo, RUN_ID)
    try:
        found = new.latest_open_checkpoint("card-a", "task")
        own = new.latest_checkpoint("card-a")
        other_workflow = new.latest_open_checkpoint("card-a", "never-ran")
        unknown = new.latest_open_checkpoint("card-never-saved", "task")
    finally:
        new.close()

    assert found == parked
    assert found is not None
    assert found.run_id == OTHER_RUN_ID
    assert found.seq == 1
    assert found.reason == "parked"
    assert own is None
    assert other_workflow is None
    assert unknown is None


def test_latest_open_checkpoint_is_none_when_the_newest_row_is_done(repo):
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(old, "card-a", reason="turn", saved_at=_at(0))
        _save_checkpoint(old, "card-a", reason="parked", saved_at=_at(1))
    finally:
        old.close()

    new = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(new, "card-a", reason="done", saved_at=_at(2))
        assert new.latest_open_checkpoint("card-a", "task") is None
    finally:
        new.close()


def test_latest_open_checkpoint_is_none_when_a_done_row_of_another_workflow_is_newest(repo):
    # Review Focus 2: the newest row "in any run and with any workflow" decides.
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(old, "card-a", reason="parked", saved_at=_at(0))
    finally:
        old.close()

    new = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(new, "card-a", reason="done", workflow="integrate", saved_at=_at(1))
        assert new.latest_open_checkpoint("card-a", "task") is None
    finally:
        new.close()


def test_latest_open_checkpoint_returns_a_later_runs_row_after_an_earlier_done(repo):
    # Review Focus 1: a card finished in one run and reopened in a later one.
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        _save_checkpoint(old, "card-a", reason="done", saved_at=_at(0))
    finally:
        old.close()

    new = store.Store.open(repo, RUN_ID)
    try:
        reopened = _save_checkpoint(new, "card-a", reason="turn", saved_at=_at(1))
        found = new.latest_open_checkpoint("card-a", "task")
    finally:
        new.close()

    assert found == reopened
    assert found is not None
    assert found.run_id == RUN_ID


def test_latest_open_checkpoint_returns_an_escalated_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(0))
        escalated = _save_checkpoint(st, "card-a", reason="escalated", saved_at=_at(1))
        assert st.latest_open_checkpoint("card-a", "task") == escalated
    finally:
        st.close()


def test_latest_open_checkpoint_breaks_a_saved_at_tie_with_seq(repo):
    # Review Focus 3: equal timestamps are ordered by seq.
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-a", reason="turn", saved_at=_at(5))
        parked = _save_checkpoint(st, "card-a", reason="parked", saved_at=_at(5))
        assert st.latest_open_checkpoint("card-a", "task") == parked

        _save_checkpoint(st, "card-b", reason="parked", saved_at=_at(5))
        _save_checkpoint(st, "card-b", reason="done", saved_at=_at(5))
        assert st.latest_open_checkpoint("card-b", "task") is None
    finally:
        st.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k latest_open_checkpoint -v`

Expected: FAIL. All six fail with `AttributeError: 'Store' object has no attribute 'latest_open_checkpoint'`.

- [ ] **Step 3: Implement `latest_open_checkpoint`**

In `src/agent_manager/store.py`, replace:

```python
                (self.run_id, card_id),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)

    # -- rebuild -------------------------------------------------------------
```

with:

```python
                (self.run_id, card_id),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)

    def latest_open_checkpoint(self, card_id: str, workflow: str) -> Checkpoint | None:
        """The newest open checkpoint of `card_id` for `workflow`, across every run.

        The card's newest row in any run and any workflow decides first: if it
        is `done`, the card is closed and this returns `None`. Otherwise it is
        the newest `turn`/`parked`/`escalated` row of `workflow`, or `None`.
        "Newest" is `saved_at` descending, then `seq` descending.
        """
        with self._lock:
            newest = self._conn.execute(
                "SELECT reason FROM checkpoints WHERE card_id = ?"
                " ORDER BY saved_at DESC, seq DESC LIMIT 1",
                (card_id,),
            ).fetchone()
            if newest is None or newest["reason"] == "done":
                return None
            row = self._conn.execute(
                "SELECT * FROM checkpoints WHERE card_id = ? AND workflow = ?"
                " AND reason IN ('turn', 'parked', 'escalated')"
                " ORDER BY saved_at DESC, seq DESC LIMIT 1",
                (card_id, workflow),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)

    # -- rebuild -------------------------------------------------------------
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k checkpoint -v`

Expected: PASS for all thirteen checkpoint tests (seven from Task 1 and six from Task 2).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`

Expected: the whole default suite passes, including `tests/e2e`, with no failures and no errors.

- [ ] **Step 6: Commit (only when the orchestrating workflow asks for one)**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): add the checkpoints table"
```

If Task 1 was already committed separately with that message, use `git commit -m "feat(store): find a card's open checkpoint across runs"` here instead.
