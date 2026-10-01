<!-- task-pipeline: validated -->
# Save a floor atomically with an agent-phase checkpoint (subtask e600b86a)

Parent story: 5bb73149 "The turn's identity: a floor saved with every agent-phase checkpoint". Milestone plan Task 1.1 of `docs/superpowers/plans/2026-09-27-exactly-once.md` (design: `docs/superpowers/specs/2026-09-27-exactly-once-design.md`). Note: both docs are not yet merged to master; they live in the `.claude/worktrees/docs-exactly-once` worktree. The card quotes the plan excerpt in full, and that excerpt is the authority here.

## Scope

This subtask adds only the storage primitive and the attempt-number primitive:

1. `paths.highest_attempt(run_id: str, card: str, phase: str) -> int` in `src/agent_manager/paths.py`, next to `attempt_dir`. It scans `run_dir(run_id) / card` for consecutive `{phase}.{n}` directories starting at 1, the same way `dispatch.next_attempt` does today. It returns the highest present `n`, or 0 when there are none. It creates no card or phase-attempt directory, and it works when the card directory does not exist. Like `next_attempt` today, calling it still calls `paths.run_dir(run_id)`, whose existing side effect of creating the run's own root directory is unchanged and is not what "creates no directory" refers to.
2. `dispatch.next_attempt` (dispatch.py:59-70) becomes `paths.highest_attempt(run_id, card, phase) + 1`. Its behavior and docstring intent stay the same. `dispatch.py` must still not import pygents.
3. `store.TurnFloor` is a frozen dataclass with fields `phase: str`, `loop: int`, `source_run: str` and `floor: int`. `store.Checkpoint` gains a last field `floor: TurnFloor | None = None`, so every existing constructor call stays valid.
4. `_SCHEMA` gains a new table `checkpoint_floors (run_id, card_id, seq, phase, loop, source_run, floor)`:
   - The primary key is `(run_id, card_id, seq)`.
   - It has `CHECK (floor >= 0)`.
   - It is created with `IF NOT EXISTS` like the other tables, so existing databases pick it up on open.
   - Like `checkpoints`, it is row-only and outside the journal. Nothing journals it, and `rebuild_from_journal` neither reads, writes nor clears it.
   - No existing table gains a column or changes a CHECK (M9 C1).
5. `Store.save_checkpoint(..., floor: TurnFloor | None = None)`:
   - The MAX(seq) read, the `checkpoints` insert and, when `floor` is given, the `checkpoint_floors` insert all run inside the existing `with self._lock, self._fenced():` block, in one transaction with a single `self._commit()`.
   - The existing try/except (store.py:1219-1237) is widened to cover both inserts, so any `sqlite3.Error` from either one rolls back both, propagates unchanged and spends no seq.
   - The returned `Checkpoint` carries `floor` as given (None when omitted).
   - No new lock, connection, pragma or commit is added.
6. `latest_checkpoint`, `latest_turn_checkpoint` and the row-returning second query of `latest_open_checkpoint`:
   - Each reads `FROM checkpoints c LEFT JOIN checkpoint_floors f ON f.run_id = c.run_id AND f.card_id = c.card_id AND f.seq = c.seq`.
   - Each selects `c.*, f.phase AS floor_phase, f.loop AS floor_loop, f.source_run AS floor_source_run, f.floor AS floor_floor`.
   - Each keeps its existing WHERE and ORDER BY, with columns qualified `c.`. The subquery on `runs` is unchanged.
   - The first "newest" query in `latest_open_checkpoint` is unchanged.
   - `_checkpoint_from_row` builds a `TurnFloor` when `floor_phase` is not NULL and otherwise sets `floor=None`. A plain `sqlite3.Row` raises `IndexError`, not `None`, for a key it does not have, so this must check `"floor_phase" in row.keys()` (or equivalent) before indexing, not index unconditionally: that is what lets rows without those keys, such as other callers selecting `*` from `checkpoints` alone, also give `None` instead of raising.

Out of scope, owned by sibling 94088f7e (Task 1.2): `runtime/state.py`, `runtime/checkpoint.py` and `runtime/engine.py`, which cover computing the floor, carrying it across a resume, `Adoption`/`RunDeps.adopt`. This subtask passes no floor from any runtime caller. Existing callers keep saving floorless checkpoints.

Per "read the code as built first", the task result must say where each touched function was actually found (file:line on the m11 base) and how the plan excerpt was adapted. For example, tests use `tests/test_store.py`'s `repo` fixture, `Store.open(repo, RUN_ID)` and the `_save_checkpoint` helper (around line 1748), not the plan snippet's `opened`/`T0` names.

## Observable behavior and error paths

- A checkpoint saved with a floor reads back through all three readers with an equal `TurnFloor`. One saved without a floor reads back with `floor is None`. Old rows with no `checkpoint_floors` row also read back as `None`.
- A floor insert that fails, for example `floor=-1` hitting the CHECK, raises `sqlite3.IntegrityError`. Neither the checkpoint row nor the floor row persists, and the next save reuses the same seq.
- An invalid `reason` still fails as before and writes no floor row.
- A lost lease still raises `LeaseLostError` from `_fenced` before anything is written, with or without a floor.
- `rebuild_from_journal` leaves `checkpoint_floors` rows intact.
- `highest_attempt` returns 0 for a missing card dir and for a dir with no matching phase. It returns N for `phase.1..phase.N`, ignores other phases, and creates no card or phase-attempt directory (same as `next_attempt` today, it still leaves `paths.run_dir(run_id)` created as a side effect). `next_attempt` keeps returning the same values as today.

## Tests

Placement rule: tests mirror modules (CLAUDE.md; design spec §14). All of these are unit tests in the mirrored module test files. None belong in `tests/e2e` or `tests/runtime`.

`tests/test_paths.py` (unit, mirrors `paths.py`):
- `highest_attempt` is 0 when the card dir is missing, and no card or phase-attempt dir is created (the run root from `paths.run_dir` existing is not what this checks).
- It is 0 when the card dir exists with no matching phase.
- It returns the highest consecutive attempt and ignores other phases' directories.

`tests/test_dispatch.py` (unit, mirrors `dispatch.py`; adapt the existing `next_attempt` tests around lines 56-71):
- `next_attempt` equals `highest_attempt + 1` for none and for several existing attempts. Existing assertions stay green.

`tests/test_store.py` (unit, mirrors `store.py`):
- Saving with a floor returns a `Checkpoint` with that floor, and `latest_checkpoint`, `latest_turn_checkpoint` and `latest_open_checkpoint` each return an equal `TurnFloor`.
- Saving without a floor gives `floor is None` from all three readers. A mix of floored and floorless rows in one card reads back correctly per seq.
- `floor=-1` raises `sqlite3.IntegrityError`. No checkpoint row is left (`latest_checkpoint` is unchanged), and the next valid save gets the seq that would have been spent.
- An invalid `reason` with a floor raises `IntegrityError`, and no `checkpoint_floors` row exists.
- With a lost lease, saving with a floor raises `LeaseLostError` and writes neither row.
- `rebuild_from_journal` keeps `checkpoint_floors` rows, so a floor still reads back afterwards.
- The `checkpoint_floors` table exists after open. Existing tables' columns are unchanged, for example `checkpoints` columns via `PRAGMA table_info`.

## Verification

`uv run pytest` must pass with the whole suite green, including tests/e2e when they are run explicitly. There is no typecheck or lint step. Work on the subtask's own `m11` branch off the base with M9 and M10 merged. Nothing is pushed, and the base branch is never moved.

---

# Save a Floor Atomically Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `paths.highest_attempt` (with `dispatch.next_attempt` delegating to it) and a row-only `checkpoint_floors` table whose row is written in the same fenced transaction as its `checkpoints` row and read back as `Checkpoint.floor: TurnFloor | None` by all three checkpoint readers.

**Architecture:** `paths.highest_attempt` lifts the disk scan out of `dispatch.next_attempt`, which becomes `highest_attempt + 1`. In `store.py`, a new `TurnFloor` frozen dataclass, a new `checkpoint_floors` table in `_SCHEMA`, a widened try/except in `save_checkpoint` that covers both inserts under the one existing `_fenced()` transaction and one `_commit()`, and a shared `LEFT JOIN` select used by the three readers; `_checkpoint_from_row` builds a `TurnFloor` only when the joined `floor_phase` key is present and not NULL.

**Tech Stack:** Python 3, stdlib `sqlite3` and `dataclasses`, pytest, run with `uv run pytest`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-save-a-floor-atomically-e600b86a/docs/superpowers/specs/task-save-a-floor-atomically-e600b86a-design.md` (prepended verbatim above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-save-a-floor-atomically-e600b86a`, on branch `m11/task-save-a-floor-atomically-e600b86a`. Run every command from that directory. This branch is cut fresh from master; nothing from sibling subtask 94088f7e exists here and nothing in this plan depends on it.

## Where the real code was found (read on this branch before writing the plan)

- `paths.attempt_dir`: `src/agent_manager/paths.py:54-58`; `paths.run_dir`: `src/agent_manager/paths.py:47-51` (creates the run root as a side effect).
- `dispatch.next_attempt`: `src/agent_manager/dispatch.py:59-70`; `dispatch.py` already imports `paths` (line 32) and never imports pygents.
- `_SCHEMA`: `src/agent_manager/store.py:29-134`, `checkpoints` table at lines 96-106, last table `run_claims` ends at line 133.
- `Checkpoint`: `src/agent_manager/store.py:560-577`; `_checkpoint_from_row`: lines 580-590.
- `immediate`: lines 795-813; `Store._fenced`: lines 898-925; `Store._commit`: lines 927-930.
- `Store.save_checkpoint`: lines 1195-1247 (try/except around the single insert at 1219-1237); `latest_checkpoint`: 1249-1257; `latest_turn_checkpoint`: 1259-1273; `latest_open_checkpoint`: 1275-1307 (row-returning query at 1300-1306).
- `rebuild_from_journal`: lines 1443-1484; `_delete_run`: 1486-1492 (does not touch `checkpoints`; it must not touch `checkpoint_floors` either, and this plan does not edit it).
- Test idioms adapted from the plan excerpt: `tests/test_store.py` uses the `repo` fixture (line 34), `store.Store.open(repo, RUN_ID)` with `try/finally st.close()`, `_at(minute)` (line 1744), `_save_checkpoint` (line 1748), `OTHER_RUN_ID` (line 1741), `_held_elsewhere` (line 1278), `_record_full_run` (line 657), `_alive`/`_dead` (lines 2446-2451) and the `stores` fixture (line 2605). The plan excerpt's `opened`/`T0` names are not used. `tests/test_dispatch.py` uses the `data_home` fixture (line 43), `RUN_ID`, `CARD`. `tests/test_paths.py` uses plain `monkeypatch`/`tmp_path` tests.

## File Structure

- Modify `src/agent_manager/paths.py`: add `highest_attempt` after `attempt_dir`.
- Modify `src/agent_manager/dispatch.py:59-70`: `next_attempt` delegates to `paths.highest_attempt`.
- Modify `src/agent_manager/store.py`: `_SCHEMA` gains `checkpoint_floors`; new `TurnFloor`; `Checkpoint.floor`; `_checkpoint_from_row`; new `_CHECKPOINT_SELECT` constant; `save_checkpoint`; the three readers.
- Modify `tests/test_paths.py`, `tests/test_dispatch.py`, `tests/test_store.py`: new unit tests (mirrored-module placement; nothing in `tests/e2e` or `tests/runtime`).
- Not touched: `src/agent_manager/runtime/state.py`, `src/agent_manager/runtime/checkpoint.py`, `src/agent_manager/runtime/engine.py` (sibling 94088f7e).

## Global Constraints

- No existing table gains a column, and no CHECK on an existing table changes (M9 C1); `checkpoint_floors` is a wholly new table.
- Checkpoint row and floor row are written in exactly one transaction, reusing the fenced `BEGIN IMMEDIATE` already used by `save_checkpoint`; no new lock, connection, pragma, or separate commit for the floor insert.
- `checkpoint_floors` is row-only and outside the journal: nothing journals it and `rebuild_from_journal` neither reads, writes nor clears it.
- `dispatch.py` never imports pygents.
- CLI envelope and exit codes unchanged.
- Do not touch `runtime/state.py`, `runtime/checkpoint.py`, `runtime/engine.py`; no runtime caller passes a floor.
- Verification: `uv run pytest`, whole suite green. Nothing is pushed; the base branch is never moved.

## Review Focus

1. A floor failure on a store that holds a lease (fenced path): the explicit `rollback()` inside `_fenced`'s `immediate` must leave no row, no open transaction, the lease intact and the next save at the same seq. Pinned in Task 4 (`test_a_refused_floor_under_a_held_lease_writes_nothing_and_keeps_the_lease`).
2. `floor=0` is the CHECK boundary and must be accepted, not refused. Pinned in Task 4 (`test_a_zero_floor_is_accepted`).
3. The join must match on `run_id` and `card_id` as well as `seq`: a floored seq 0 in another run or another card must not attach itself to this run's floorless seq 0. Pinned in Task 5 (`test_a_floor_never_attaches_to_another_runs_or_cards_row`).
4. A row selected without the joined columns (a plain `SELECT * FROM checkpoints`) must give `floor is None`, not `IndexError`. Pinned in Task 5 (`test_checkpoint_from_row_without_floor_columns_has_no_floor`).
5. A gap in attempt directories (`phase.1`, `phase.3`) must give 1 from `highest_attempt` (consecutive scan, so `next_attempt` stays 2 as today). Pinned in Task 1 (`test_highest_attempt_stops_at_the_first_gap`).

---

### Task 1: `paths.highest_attempt`

**Files:**
- Modify: `src/agent_manager/paths.py:54-58` (add after `attempt_dir`)
- Test: `tests/test_paths.py` (append at end of file)

**Interfaces:**
- Consumes: `paths.run_dir(run_id: str) -> Path`, `paths.attempt_dir(run_id, card, phase, attempt) -> Path` (existing).
- Produces: `paths.highest_attempt(run_id: str, card: str, phase: str) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paths.py`:

```python
def test_highest_attempt_is_zero_and_creates_nothing_for_a_missing_card(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert paths.highest_attempt("run-abc", "abc123", "implement") == 0
    # The run root may exist (run_dir's own side effect); the card dir must not.
    assert not (tmp_path / "agent-manager" / "runs" / "run-abc" / "abc123").exists()


def test_highest_attempt_is_zero_when_no_directory_matches_the_phase(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    paths.attempt_dir("run-abc", "abc123", "review", 1)
    card_dir = paths.run_dir("run-abc") / "abc123"
    before = sorted(p.name for p in card_dir.iterdir())

    assert paths.highest_attempt("run-abc", "abc123", "implement") == 0
    assert sorted(p.name for p in card_dir.iterdir()) == before == ["review.1"]


def test_highest_attempt_is_the_highest_consecutive_attempt_of_its_phase(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    for n in (1, 2, 3):
        paths.attempt_dir("run-abc", "abc123", "implement", n)
    for n in (1, 2, 3, 4, 5):
        paths.attempt_dir("run-abc", "abc123", "review", n)

    assert paths.highest_attempt("run-abc", "abc123", "implement") == 3
    assert paths.highest_attempt("run-abc", "abc123", "review") == 5
    assert not (paths.run_dir("run-abc") / "abc123" / "implement.4").exists()


def test_highest_attempt_stops_at_the_first_gap(monkeypatch, tmp_path):
    # Same consecutive scan as dispatch.next_attempt has always done.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    paths.attempt_dir("run-abc", "abc123", "implement", 1)
    paths.attempt_dir("run-abc", "abc123", "implement", 3)

    assert paths.highest_attempt("run-abc", "abc123", "implement") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -k highest_attempt -v`
Expected: 4 FAIL with `AttributeError: module 'agent_manager.paths' has no attribute 'highest_attempt'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/paths.py`, append after `attempt_dir` (after line 58):

```python


def highest_attempt(run_id: str, card: str, phase: str) -> int:
    """The highest attempt number `phase` of `card` has a directory for, or 0.

    Scans `{phase}.1`, `{phase}.2`, ... on disk and stops at the first absent
    one, so a resumed run sees the attempts a previous process made. Creates
    no card or attempt directory; `run_dir` still creates the run's own root.
    """
    card_dir = run_dir(run_id) / card
    attempt = 0
    while (card_dir / f"{phase}.{attempt + 1}").exists():
        attempt += 1
    return attempt
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/paths.py tests/test_paths.py
git commit -m "feat(paths): add highest_attempt disk scan"
```

---

### Task 2: `dispatch.next_attempt` delegates to `highest_attempt`

**Files:**
- Modify: `src/agent_manager/dispatch.py:59-70`
- Test: `tests/test_dispatch.py` (insert after `test_attempt_numbers_are_per_phase`, line 71)

**Interfaces:**
- Consumes: `paths.highest_attempt(run_id: str, card: str, phase: str) -> int` (Task 1).
- Produces: `dispatch.next_attempt(run_id: str, card: str, phase: str) -> int`, unchanged signature and values.

- [ ] **Step 1: Write the failing tests**

Insert into `tests/test_dispatch.py` directly after `test_attempt_numbers_are_per_phase` (after line 71):

```python
def test_next_attempt_is_one_past_paths_highest_attempt(data_home, monkeypatch):
    seen = []

    def highest(run_id, card, phase):
        seen.append((run_id, card, phase))
        return 41

    monkeypatch.setattr(paths, "highest_attempt", highest)

    assert dispatch.next_attempt(RUN_ID, CARD, "explore") == 42
    assert seen == [(RUN_ID, CARD, "explore")]


@pytest.mark.parametrize("existing", [0, 1, 3])
def test_next_attempt_equals_highest_attempt_plus_one_on_disk(data_home, existing):
    for n in range(1, existing + 1):
        paths.attempt_dir(RUN_ID, CARD, "explore", n)

    assert paths.highest_attempt(RUN_ID, CARD, "explore") == existing
    assert dispatch.next_attempt(RUN_ID, CARD, "explore") == existing + 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -k "next_attempt or attempt_numbers or first_attempt or never_reused" -v`
Expected: `test_next_attempt_is_one_past_paths_highest_attempt` FAILS (`assert 1 == 42`, because `next_attempt` still scans disk itself); the parametrized on-disk test and the three existing tests PASS.

- [ ] **Step 3: Write the minimal implementation**

Replace `next_attempt` in `src/agent_manager/dispatch.py` (lines 59-70) with:

```python
def next_attempt(run_id: str, card: str, phase: str) -> int:
    """The lowest attempt number this phase has no directory for yet.

    Scanned from disk rather than counted in memory: a resumed run finds the
    attempts a previous process made, and overwriting one would destroy the
    prompt, result and log that are the only evidence of what happened.
    """
    return paths.highest_attempt(run_id, card, phase) + 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py tests/test_paths.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "refactor(dispatch): next_attempt is highest_attempt + 1"
```

---

### Task 3: `TurnFloor`, `Checkpoint.floor`, and the `checkpoint_floors` table

**Files:**
- Modify: `src/agent_manager/store.py:29-134` (`_SCHEMA`), `src/agent_manager/store.py:560-577` (`Checkpoint`, plus new `TurnFloor` above it)
- Test: `tests/test_store.py` (append at end of file)

**Interfaces:**
- Consumes: nothing new.
- Produces: `store.TurnFloor(phase: str, loop: int, source_run: str, floor: int)` (frozen dataclass); `store.Checkpoint.floor: TurnFloor | None = None` (last field); table `checkpoint_floors(run_id TEXT, card_id TEXT, seq INTEGER, phase TEXT, loop INTEGER, source_run TEXT, floor INTEGER CHECK (floor >= 0), PRIMARY KEY (run_id, card_id, seq))`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python
# -- checkpoint floors -----------------------------------------------------------
#
# Exactly-once Task 1.1: a row-only `checkpoint_floors` table written in the
# same fenced transaction as its `checkpoints` row. Steps tier: real temp DB and
# journal, no harness.

FLOOR = store.TurnFloor(phase="implement", loop=2, source_run=OTHER_RUN_ID, floor=3)


def _count(st: store.Store, table: str) -> int:
    return st.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_open_db_creates_the_checkpoint_floors_table(repo):
    conn = store.open_db(repo)
    try:
        info = conn.execute("PRAGMA table_info(checkpoint_floors)").fetchall()
        checkpoint_columns = [
            row["name"] for row in conn.execute("PRAGMA table_info(checkpoints)").fetchall()
        ]
    finally:
        conn.close()

    assert [row["name"] for row in info] == [
        "run_id",
        "card_id",
        "seq",
        "phase",
        "loop",
        "source_run",
        "floor",
    ]
    assert [row["name"] for row in sorted(info, key=lambda r: r["pk"]) if row["pk"]] == [
        "run_id",
        "card_id",
        "seq",
    ]
    # M9 C1: the existing table is untouched.
    assert checkpoint_columns == [
        "run_id",
        "card_id",
        "seq",
        "workflow",
        "digest",
        "reason",
        "agent",
        "saved_at",
    ]


def test_checkpoint_floors_refuses_a_negative_floor(repo):
    conn = store.open_db(repo)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO checkpoint_floors (run_id, card_id, seq, phase, loop,"
                " source_run, floor) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (RUN_ID, "card-a", 0, "implement", 0, RUN_ID, -1),
            )
        conn.rollback()
    finally:
        conn.close()


def test_turn_floor_is_frozen_and_checkpoint_floor_defaults_to_none():
    assert [f.name for f in dataclasses.fields(store.TurnFloor)] == [
        "phase",
        "loop",
        "source_run",
        "floor",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        FLOOR.floor = 4  # type: ignore[misc]

    assert dataclasses.fields(store.Checkpoint)[-1].name == "floor"
    plain = store.Checkpoint(
        run_id=RUN_ID,
        card_id="card-a",
        seq=0,
        workflow="task",
        digest="sha256:aaa",
        reason="turn",
        agent={},
        saved_at=_at(0),
    )
    assert plain.floor is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "checkpoint_floors or turn_floor" -v`
Expected: collection ERROR with `AttributeError: module 'agent_manager.store' has no attribute 'TurnFloor'` (raised by the module-level `FLOOR`).

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/store.py`, inside `_SCHEMA`, insert directly after the `checkpoints` table (after line 106, before `CREATE TABLE IF NOT EXISTS run_controls`):

```sql

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
```

Directly above `@dataclass(frozen=True)\nclass Checkpoint:` (line 560), insert:

```python
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


```

In `class Checkpoint`, add the last field after `saved_at: datetime` (line 577):

```python
    floor: TurnFloor | None = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: all PASS (including the existing `test_open_db_creates_the_checkpoints_table`).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): add TurnFloor and the checkpoint_floors table"
```

---

### Task 4: `save_checkpoint` writes the floor in the same transaction

**Files:**
- Modify: `src/agent_manager/store.py:1195-1247` (`Store.save_checkpoint`)
- Test: `tests/test_store.py` (append at end of file, after Task 3's tests)

**Interfaces:**
- Consumes: `store.TurnFloor`, `Checkpoint.floor`, table `checkpoint_floors` (Task 3); `Store._fenced`, `Store._commit`, `_iso` (existing).
- Produces: `Store.save_checkpoint(card_id: str, *, workflow: str, digest: str, reason: str, agent: dict, saved_at: datetime, floor: TurnFloor | None = None) -> Checkpoint`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python
def _save_floored(
    st: store.Store,
    card_id: str = "card-a",
    *,
    floor: store.TurnFloor | None = FLOOR,
    reason: str = "turn",
    saved_at: datetime | None = None,
) -> store.Checkpoint:
    return st.save_checkpoint(
        card_id,
        workflow="task",
        digest="sha256:aaa",
        reason=reason,
        agent={"turn": 0},
        saved_at=_at(0) if saved_at is None else saved_at,
        floor=floor,
    )


def test_save_checkpoint_writes_the_floor_row_with_the_checkpoint(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        saved = _save_floored(st)
        plain = _save_floored(st, floor=None, saved_at=_at(1))
        rows = st.connection.execute(
            "SELECT run_id, card_id, seq, phase, loop, source_run, floor"
            " FROM checkpoint_floors ORDER BY seq"
        ).fetchall()
        journaled = st.journal.path.exists()
    finally:
        st.close()

    assert saved.floor == FLOOR
    assert saved.seq == 0
    assert plain.floor is None
    assert plain.seq == 1
    # Only the floored save wrote a floor row, keyed like its checkpoint row.
    assert [tuple(row) for row in rows] == [
        (RUN_ID, "card-a", 0, "implement", 2, OTHER_RUN_ID, 3)
    ]
    # Row-only: nothing was journaled (no journal file was ever created).
    assert journaled is False


def test_a_zero_floor_is_accepted(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        zero = dataclasses.replace(FLOOR, floor=0)
        saved = _save_floored(st, floor=zero)
        assert _count(st, "checkpoint_floors") == 1
    finally:
        st.close()

    assert saved.floor == zero


def test_a_negative_floor_is_refused_and_writes_nothing(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        kept = _save_floored(st, floor=None)
        with pytest.raises(sqlite3.IntegrityError):
            _save_floored(st, floor=dataclasses.replace(FLOOR, floor=-1), saved_at=_at(1))

        assert _held_elsewhere(st._lock) is False
        assert st.connection.in_transaction is False
        assert _count(st, "checkpoints") == 1
        assert _count(st, "checkpoint_floors") == 0
        assert st.latest_checkpoint("card-a") == kept
        # The refused save spent no seq: the next one takes seq 1.
        assert _save_floored(st, saved_at=_at(2)).seq == 1
    finally:
        st.close()


def test_an_unknown_reason_with_a_floor_writes_no_floor_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _save_floored(st, reason="bogus")

        assert st.connection.in_transaction is False
        assert _count(st, "checkpoints") == 0
        assert _count(st, "checkpoint_floors") == 0
        assert _save_floored(st).seq == 0
    finally:
        st.close()


def test_a_refused_floor_under_a_held_lease_writes_nothing_and_keeps_the_lease(stores):
    st = stores()
    st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)

    with pytest.raises(sqlite3.IntegrityError):
        _save_floored(st, floor=dataclasses.replace(FLOOR, floor=-1))

    assert _held_elsewhere(st._lock) is False
    assert st.connection.in_transaction is False
    assert _count(st, "checkpoints") == 0
    assert _count(st, "checkpoint_floors") == 0
    lease = store.read_lease(st.connection, RUN_ID)
    assert lease is not None and lease.token == "t1"
    saved = _save_floored(st)
    assert saved.seq == 0
    assert saved.floor == FLOOR
    assert _count(st, "checkpoint_floors") == 1


def test_a_taken_over_store_saves_neither_row_with_a_floor(stores):
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    b = stores()
    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)

    with pytest.raises(store.LeaseLostError) as caught:
        _save_floored(a)

    assert caught.value.holder is not None and caught.value.holder.token == "t2"
    assert a.connection.in_transaction is False
    assert _count(b, "checkpoints") == 0
    assert _count(b, "checkpoint_floors") == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "floor or taken_over_store_saves" -v`
Expected: the six new tests FAIL with `TypeError: Store.save_checkpoint() got an unexpected keyword argument 'floor'`; Task 3's tests still PASS.

- [ ] **Step 3: Write the minimal implementation**

Replace `Store.save_checkpoint` in `src/agent_manager/store.py` (lines 1195-1247) with:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): save a checkpoint's floor in the same fenced transaction"
```

---

### Task 5: The three readers return the floor

**Files:**
- Modify: `src/agent_manager/store.py:580-590` (`_checkpoint_from_row`, plus new `_CHECKPOINT_SELECT` below it), `src/agent_manager/store.py:1249-1307` (`latest_checkpoint`, `latest_turn_checkpoint`, `latest_open_checkpoint`) — line numbers shift by the Task 3/4 insertions; locate by name.
- Test: `tests/test_store.py` (append at end of file, after Task 4's tests)

**Interfaces:**
- Consumes: `store.TurnFloor`, `Checkpoint.floor`, `checkpoint_floors` (Task 3); `save_checkpoint(..., floor=...)` (Task 4); test helpers `FLOOR`, `_count`, `_save_floored` (Tasks 3-4).
- Produces: `latest_checkpoint`, `latest_turn_checkpoint`, `latest_open_checkpoint` return `Checkpoint` with `floor` populated from the join; `_checkpoint_from_row(row)` tolerates rows without the `floor_*` keys.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python
def test_every_reader_returns_the_saved_floor(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        saved = _save_floored(st)
        latest = st.latest_checkpoint("card-a")
        turn = st.latest_turn_checkpoint("card-a")
        open_ = st.latest_open_checkpoint("card-a", "task")
    finally:
        st.close()

    for read in (latest, turn, open_):
        assert read is not None
        assert read.floor == FLOOR
        assert read == saved


def test_every_reader_returns_no_floor_for_a_floorless_row(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_checkpoint(st, "card-a", reason="turn")
        reads = [
            st.latest_checkpoint("card-a"),
            st.latest_turn_checkpoint("card-a"),
            st.latest_open_checkpoint("card-a", "task"),
        ]
    finally:
        st.close()

    for read in reads:
        assert read is not None
        assert read.floor is None


def test_floored_and_floorless_rows_of_one_card_read_back_per_seq(repo):
    other_floor = store.TurnFloor(phase="review", loop=0, source_run=RUN_ID, floor=0)
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_floored(st, saved_at=_at(0))
        assert st.latest_checkpoint("card-a").floor == FLOOR

        plain = _save_floored(st, floor=None, saved_at=_at(1))
        assert st.latest_checkpoint("card-a").floor is None

        parked = _save_floored(st, floor=other_floor, reason="parked", saved_at=_at(2))
        latest = st.latest_checkpoint("card-a")
        turn = st.latest_turn_checkpoint("card-a")
        open_ = st.latest_open_checkpoint("card-a", "task")
    finally:
        st.close()

    assert latest == parked and latest.floor == other_floor
    assert turn == plain and turn.floor is None
    assert open_ == parked and open_.floor == other_floor


def test_a_floor_never_attaches_to_another_runs_or_cards_row(repo):
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        old_saved = _save_floored(old, "card-a", saved_at=_at(0))
    finally:
        old.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        floored_b = _save_floored(st, "card-b", saved_at=_at(0))
        own = _save_floored(st, "card-a", floor=None, saved_at=_at(1))
        latest = st.latest_checkpoint("card-a")
        turn = st.latest_turn_checkpoint("card-a")
        open_ = st.latest_open_checkpoint("card-a", "task")
        b = st.latest_checkpoint("card-b")
    finally:
        st.close()

    # All three rows have seq 0; only the matching (run_id, card_id, seq) joins.
    assert own.seq == old_saved.seq == floored_b.seq == 0
    for read in (latest, turn, open_):
        assert read is not None
        assert read.run_id == RUN_ID and read.card_id == "card-a"
        assert read.floor is None
    assert b is not None and b.floor == FLOOR


def test_latest_open_checkpoint_returns_an_older_runs_floor(repo):
    old = store.Store.open(repo, OTHER_RUN_ID)
    try:
        parked = _save_floored(old, "card-a", reason="parked", saved_at=_at(0))
    finally:
        old.close()

    st = store.Store.open(repo, RUN_ID)
    try:
        found = st.latest_open_checkpoint("card-a", "task")
    finally:
        st.close()

    assert found == parked
    assert found is not None and found.floor == FLOOR


def test_a_rebuild_keeps_checkpoint_floors(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        before = [line.seq for line in st.journal.read()]
        saved = _save_floored(st, "ef248597")
        after = [line.seq for line in st.journal.read()]
        st.rebuild_from_journal(RUN_ID)
        kept = st.latest_checkpoint("ef248597")
        floors = _count(st, "checkpoint_floors")
    finally:
        st.close()

    assert after == before
    assert floors == 1
    assert kept == saved
    assert kept is not None and kept.floor == FLOOR


def test_checkpoint_from_row_without_floor_columns_has_no_floor(repo):
    # Regression guard: passes before this task's change and must keep passing
    # once _checkpoint_from_row reads the joined floor_* keys.
    st = store.Store.open(repo, RUN_ID)
    try:
        _save_floored(st)
        row = st.connection.execute(
            "SELECT * FROM checkpoints WHERE run_id = ? AND card_id = ?",
            (RUN_ID, "card-a"),
        ).fetchone()
    finally:
        st.close()

    assert store._checkpoint_from_row(row).floor is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "reader or per_seq or never_attaches or older_runs_floor or rebuild_keeps or without_floor_columns" -v`
Expected: `test_every_reader_returns_the_saved_floor`, `test_floored_and_floorless_rows_of_one_card_read_back_per_seq`, `test_a_floor_never_attaches_to_another_runs_or_cards_row` (on the `b.floor == FLOOR` assertion), `test_latest_open_checkpoint_returns_an_older_runs_floor` and `test_a_rebuild_keeps_checkpoint_floors` FAIL with `assert None == TurnFloor(...)` (readers do not join yet). `test_every_reader_returns_no_floor_for_a_floorless_row` and the regression guard `test_checkpoint_from_row_without_floor_columns_has_no_floor` PASS.

- [ ] **Step 3: Write the minimal implementation**

Replace `_checkpoint_from_row` in `src/agent_manager/store.py` with the function below, followed by the new constant:

```python
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
```

Replace `latest_checkpoint` with:

```python
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
```

Replace `latest_turn_checkpoint` with:

```python
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
```

In `latest_open_checkpoint`, leave the first `newest` query unchanged and replace only the second query (the `row = self._conn.execute("SELECT * FROM checkpoints WHERE card_id = ? AND workflow = ?" ...` statement) with:

```python
            row = self._conn.execute(
                _CHECKPOINT_SELECT
                + " WHERE c.card_id = ? AND c.workflow = ?"
                " AND c.reason IN ('turn', 'parked', 'escalated')"
                " AND c.run_id NOT IN (SELECT id FROM runs WHERE status = 'cancelled')"
                " ORDER BY c.saved_at DESC, c.seq DESC LIMIT 1",
                (card_id, workflow),
            ).fetchone()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: all PASS, including every existing `latest_*checkpoint` and cancelled-run test.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): checkpoint readers join and return the turn floor"
```

---

### Task 6: Full-suite verification

**Files:** none modified.

**Interfaces:**
- Consumes: everything above.
- Produces: a green suite on branch `m11/task-save-a-floor-atomically-e600b86a`.

- [ ] **Step 1: Confirm the out-of-scope files are untouched**

Run: `git diff --stat master -- src/agent_manager/runtime/state.py src/agent_manager/runtime/checkpoint.py src/agent_manager/runtime/engine.py`
Expected: no output.

- [ ] **Step 2: Confirm `dispatch.py` still imports no pygents**

Run: `git grep -n pygents -- src/agent_manager/dispatch.py`
Expected: only the pre-existing docstring/comment mentions, if any, and no `import pygents` / `from pygents` line.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest`
Expected: all PASS (the default selection excludes `e2e` via `pyproject.toml` `addopts`).

- [ ] **Step 4: Run the opt-in e2e tier**

Run: `uv run pytest -m e2e`
Expected: PASS, or the same skips as on master when no real harness is available; no new failure.

- [ ] **Step 5: Commit only if anything changed**

Nothing should have changed in this task. If a fix was needed, commit it with a message naming the failing test it fixed; do not push and do not move `master`.
