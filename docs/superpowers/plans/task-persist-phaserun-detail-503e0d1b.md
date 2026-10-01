<!-- task-pipeline: validated -->
# Persist PhaseRun.detail through the store — subtask design

Card: 503e0d1b-8f11-46b3-9a05-b2465761f931 (subtask of story 20096657 "Stop losing phase failure reasons and milestone identity").
Source of truth: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, Decision S2 (§3), §5 Compatibility, §6 Testing. That doc's line numbers have drifted; the current locations in `src/agent_manager/store.py` are listed below and must be re-read before implementing.

## Problem

`models.PhaseRun.detail` already exists and `dispatch.py` / `walk.py` fill it in memory (for example a phase's failure reason). `record_phase` already journals it, because `model_dump(exclude={"attempts"})` includes `detail`, and `replay()` already restores it through `PhaseRun.model_validate`. The SQL projection drops it: the `phases` table has no `detail` column, `_write_phase_row` does not write it, and `load_run` does not read it back. So any run loaded from the DB has `detail=None` on every phase.

## Scope

All changes are in `src/agent_manager/store.py`, plus one or more tests in `tests/test_store.py`.

1. **Schema.** The `phases` table in `_SCHEMA` (currently store.py:64-75) gets a nullable `detail TEXT` column. New databases get it straight from `CREATE TABLE IF NOT EXISTS`.
2. **Migration guard in `open_db()`** (store.py:139). Databases created before this change already have a `phases` table without `detail`, so `CREATE TABLE IF NOT EXISTS` does nothing for them. `open_db` must add the column with `ALTER TABLE phases ADD COLUMN detail TEXT`, but only when the column is missing. SQLite has no `ADD COLUMN IF NOT EXISTS` and the repo has no `ALTER TABLE` precedent, so the guard has to be written here. It can either check `PRAGMA table_info(phases)` first, or catch only the "duplicate column name" `sqlite3.OperationalError`. Requirements:
   - Opening the same DB any number of times is safe.
   - The migration is not destructive: no table rebuild or copy, and existing rows keep their data with `detail` NULL.
   - Any other `OperationalError` is re-raised, not swallowed.
   `open_db`'s own docstring currently says reopening an existing database
   "neither destroys nor migrates what is already there"; that claim becomes
   false once this guard exists, so update the docstring to describe the new,
   additive-only migration alongside the existing `CREATE ... IF NOT EXISTS`
   behaviour.
3. **Write.** `Store._write_phase_row` (store.py:919) includes `phase.detail` in its insert/upsert, including on the update path, so a phase that later gains or changes `detail` is overwritten.
4. **Read.** The module-level `load_run` (store.py:437; `PhaseRun` is built at around line 494) selects `detail` and passes it to `models.PhaseRun(detail=...)`. `Store.load_run` (store.py:1002) only wraps this function, so it needs no change of its own.

`rebuild_from_journal` (store.py:1213) needs no change. It replays the journal, which already carries `detail`, and writes rows through `_write_phase_row`. Once step 3 lands, it restores `detail` for old runs.

## Observable behaviour

- `load_run` / `Store.load_run` returns `PhaseRun.detail` exactly as it was recorded, or `None` if none was recorded.
- After `rebuild_from_journal`, `detail` is restored from the journal, including for runs recorded before the column existed.
- There is no CLI surface change beyond `detail` now showing up where it was silently dropped before. §5 describes this as "a strict improvement, not breaking", and the `{"ok": true, "data": ...}` envelope is unchanged.

## Error paths

- Legacy DB without the column: `open_db` adds it, and existing phase rows read back with `detail=None`.
- `open_db` run again on a DB that has already been migrated: nothing happens and no error is raised.
- Unrelated SQLite errors during the guard: re-raised.

## Out of scope

- `replay`, `record_phase` and the journal format, which already carry `detail`.
- Sibling subtask 6bdc1690 (`runs.milestone_id`, `models.Run.milestone_id`, `run_milestone`, `find_run_milestone`, the colliding-short-id test). That subtask is blocked on this one, but it owns all of those.
- Changes to pygents' turn/phase model, the checkpoint format, or the harness adapter contract.
- Adding a typecheck or CI gate.
- Rewriting the boundary monkeypatches.
- Replacing or removing grafo.

## Tests

Tier rule: design spec §14 "Testing" and the `tests/test_store.py` docstring. `store.py` does I/O against a real temporary SQLite DB and journal, with no network, no mocks and no harness, so these tests belong in the **Steps** tier. Put them in `tests/test_store.py` next to the existing `load_run` / `rebuild_from_journal` tests. None of them go in `tests/e2e` or `tests/runtime`.

1. **Steps tier:** a phase recorded as `failed` with a non-empty `detail` comes back with the same `status` and `detail` from `load_run`.
2. **Steps tier:** the same run, after `rebuild_from_journal`, still has that `detail` when loaded with `load_run`.
3. **Steps tier:** a phase re-recorded with a changed `detail` reads back the latest value (the upsert updates the column).
4. **Steps tier:** for a legacy DB, create a `phases` table with the old column set (no `detail`) and insert a row. Then:
   - `open_db` adds the column, and the existing row survives with `detail=None`.
   - A second `open_db` does not raise.
   - `rebuild_from_journal` fills `detail` in from the journal.

## Verification

`uv run pytest` (per CLAUDE.md there is no separate lint or typecheck command).

---

# Persist PhaseRun.detail through the store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `PhaseRun.detail` survive the SQLite projection: written by `_write_phase_row`, read back by `load_run`, restored by `rebuild_from_journal`, and added in place to databases created before the column existed.

**Architecture:** One nullable `detail TEXT` column goes at the end of the `phases` table in `_SCHEMA`. The phase row writer and the module-level `load_run` gain that one field. `open_db` gains a small additive-only migration: after `CREATE TABLE IF NOT EXISTS`, it reads `PRAGMA table_info(phases)` and runs `ALTER TABLE phases ADD COLUMN detail TEXT` only if the column is missing. Nothing is caught, so any SQLite error propagates unchanged. `replay`, `record_phase` and the journal already carry `detail` and are not touched.

**Tech Stack:** Python, stdlib `sqlite3`, Pydantic models (`agent_manager.models`), pytest, run through `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-persist-phaserun-detail-503e0d1b/docs/superpowers/specs/task-persist-phaserun-detail-503e0d1b-design.md` (reproduced verbatim above). Upstream source of truth: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, Decision S2.

**Working directory:** every command below runs from the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-persist-phaserun-detail-503e0d1b` on branch `m13/task-persist-phaserun-detail-503e0d1b`. That branch was cut fresh from master, so none of sibling subtask 6bdc1690's code (`runs.milestone_id` and related) exists on it, and this plan does not rely on any of it.

## Global Constraints

- All production changes are in `src/agent_manager/store.py`. All tests go in `tests/test_store.py` (Steps tier: real temporary SQLite and journal, no mocks, no harness). Nothing goes in `tests/e2e` or `tests/runtime`.
- The migration is additive only: no table rebuild, no copy, no `DROP`. Existing rows keep their data, with `detail` NULL.
- Opening the same DB any number of times is safe.
- Any `OperationalError` other than the one the guard avoids is re-raised, not swallowed. The guard used here checks `PRAGMA table_info` first and catches nothing.
- Do not touch `replay`, `record_phase`, the journal format, `models.py`, the `runs` table, `models.Run`, `run_milestone`, or `find_run_milestone`.
- CLI envelope `{"ok": true, "data": ...}` is unchanged; no CLI code is edited.
- Verification: `uv run pytest` (no separate lint or typecheck command exists).

## Review Focus

- A real failure reason is multi-line command output with non-ASCII characters. It must round-trip byte-for-byte, not be truncated at the first newline or mangled. Pinned in Task 1 by `FAILURE_DETAIL`, which is used in every round-trip test.
- A phase that failed with a reason and is later re-recorded `done` with `detail=None` (a retry that passed) must read back `None`, not the stale reason. The upsert must assign `detail=excluded.detail`, not `COALESCE`. Pinned in Task 1 by `test_re_recording_a_phase_overwrites_its_detail`.
- A sibling phase recorded without a `detail` must stay `None` next to a failed phase that has one, so detail does not leak across rows. Pinned in Task 1 by the `implement` phase in `_record_failed_phase`, checked in every round-trip test.
- A fresh DB and a migrated legacy DB must end up with the same `phases` column list in the same order. `ALTER TABLE` appends at the end, so `detail` must also be last in `_SCHEMA`. Pinned by `PHASE_COLUMNS` in Task 1 (fresh) and Task 2 (migrated).
- Reopening a DB that has already been migrated must not fail with "duplicate column name". Writing through `_write_phase_row` into a migrated DB must also work. Pinned in Task 2 by the second `open_db` and the `rebuild_from_journal` in the legacy test.

---

### Task 1: Write and read `detail` through the `phases` projection

**Files:**
- Modify: `src/agent_manager/store.py:64-75` (`phases` table in `_SCHEMA`)
- Modify: `src/agent_manager/store.py:494-500` (`models.PhaseRun(...)` in module-level `load_run`)
- Modify: `src/agent_manager/store.py:919-948` (`Store._write_phase_row`)
- Test: `tests/test_store.py`, new section inserted after `test_a_stopped_run_survives_a_rebuild_from_the_journal` (ends at line 1730) and before the `# -- checkpoints` comment (line 1733)

**Interfaces:**
- Consumes: existing test helpers in `tests/test_store.py`: the `repo` fixture, `RUN_ID`, `_run(repo)`, `_story()` (card `"8831189b"`), `_subtask()` (card `"ef248597"`), `_truncate_db(repo)`. Existing `store.Store.open`, `Store.record_*`, `Store.load_run`, module-level `store.load_run(conn, run_id)`, `Store.rebuild_from_journal`.
- Produces (test module, used again by Task 2): `FAILURE_DETAIL: str`, `PHASE_COLUMNS: list[str]`, `_record_failed_phase(st: store.Store, repo: Path, detail: str | None = FAILURE_DETAIL) -> None`, `_phase_details(run: models.Run | None) -> list[tuple[str, str, str | None]]`, `_EXPECTED_DETAILS: list[tuple[str, str, str | None]]`. Production: the `phases` table has a trailing nullable `detail` column on fresh databases, and `_write_phase_row` / `load_run` write and read it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_store.py`, find this exact text (the end of `test_a_stopped_run_survives_a_rebuild_from_the_journal` and the start of the checkpoints section):

```python
    assert _stopped_levels(returned) == ("stopped", "stopped", "stopped", "stopped")
    assert after == returned
    assert row["status"] == "stopped"


# -- checkpoints ---------------------------------------------------------------
```

Replace it with:

```python
    assert _stopped_levels(returned) == ("stopped", "stopped", "stopped", "stopped")
    assert after == returned
    assert row["status"] == "stopped"


# -- phase detail --------------------------------------------------------------
#
# `PhaseRun.detail` is why a phase failed (architecture cleanup S2). The journal
# always carried it; these pin that the projection does too. Steps tier: real
# temp DB and journal, no harness.

FAILURE_DETAIL = (
    "verify failed: `uv run pytest` exited 1\n"
    "  FAILED tests/test_store.py::test_naïve_path — assert 'ü' == 'u'\n"
    "  3 failed, 212 passed"
)
"""Multi-line and non-ASCII, the way a real failure reason arrives."""

PHASE_COLUMNS = [
    "run_id",
    "story_id",
    "card_id",
    "name",
    "kind",
    "status",
    "started_at",
    "ended_at",
    "position",
    "detail",
]
"""`detail` is last: `ALTER TABLE ... ADD COLUMN` appends, so a fresh and a
migrated database only agree if the schema puts it there too."""


def _record_failed_phase(
    st: store.Store, repo: Path, detail: str | None = FAILURE_DETAIL
) -> None:
    """One subtask with a finished `implement` (no detail) and a failed `verify`."""
    st.record_run(_run(repo))
    st.record_story(_story())
    st.record_subtask("8831189b", _subtask())
    st.record_phase(
        "8831189b",
        "ef248597",
        models.PhaseRun(
            name="implement",
            kind="agent",
            status="done",
            started_at=datetime(2026, 9, 23, 10, 13, tzinfo=timezone.utc),
            ended_at=datetime(2026, 9, 23, 10, 20, tzinfo=timezone.utc),
        ),
    )
    st.record_phase(
        "8831189b",
        "ef248597",
        models.PhaseRun(
            name="verify",
            kind="deterministic",
            status="failed",
            started_at=datetime(2026, 9, 23, 10, 21, tzinfo=timezone.utc),
            ended_at=datetime(2026, 9, 23, 10, 22, tzinfo=timezone.utc),
            detail=detail,
        ),
    )


def _phase_details(run: models.Run | None) -> list[tuple[str, str, str | None]]:
    assert run is not None
    return [
        (phase.name, phase.status, phase.detail)
        for phase in run.stories[0].subtasks[0].phases
    ]


_EXPECTED_DETAILS = [
    ("implement", "done", None),
    ("verify", "failed", FAILURE_DETAIL),
]


def test_a_fresh_phases_table_carries_detail_as_its_last_column(repo):
    conn = store.open_db(repo)
    try:
        columns = [
            row["name"] for row in conn.execute("PRAGMA table_info(phases)").fetchall()
        ]
    finally:
        conn.close()

    assert columns == PHASE_COLUMNS


def test_a_failed_phase_detail_round_trips_through_load_run(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_failed_phase(st, repo)
        via_store = st.load_run(RUN_ID)
        via_connection = store.load_run(st.connection, RUN_ID)
    finally:
        st.close()

    assert _phase_details(via_store) == _EXPECTED_DETAILS
    assert _phase_details(via_connection) == _EXPECTED_DETAILS


def test_a_failed_phase_detail_survives_a_rebuild_from_the_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_failed_phase(st, repo)
    finally:
        st.close()

    _truncate_db(repo)
    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        returned = rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
    finally:
        rebuilt.close()

    assert _phase_details(after) == _EXPECTED_DETAILS
    assert after == returned


def test_re_recording_a_phase_overwrites_its_detail(repo):
    # The upsert must assign the new value, including NULL: a retry that
    # passes must not keep the old failure reason.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_failed_phase(st, repo, detail="first reason")
        verify = models.PhaseRun(
            name="verify",
            kind="deterministic",
            status="failed",
            detail="second reason",
        )
        st.record_phase("8831189b", "ef248597", verify)
        changed = st.load_run(RUN_ID)
        st.record_phase(
            "8831189b",
            "ef248597",
            verify.model_copy(update={"status": "done", "detail": None}),
        )
        cleared = st.load_run(RUN_ID)
        rows = st.connection.execute(
            "SELECT COUNT(*) FROM phases WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
    finally:
        st.close()

    assert _phase_details(changed) == [
        ("implement", "done", None),
        ("verify", "failed", "second reason"),
    ]
    assert _phase_details(cleared) == [
        ("implement", "done", None),
        ("verify", "done", None),
    ]
    assert rows == 2


# -- checkpoints ---------------------------------------------------------------
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v -k "fresh_phases_table_carries_detail or failed_phase_detail or re_recording_a_phase_overwrites"`

Expected: all 4 FAIL.
- `test_a_fresh_phases_table_carries_detail_as_its_last_column` fails because the column list has no `"detail"`.
- `test_a_failed_phase_detail_round_trips_through_load_run` fails with `('verify', 'failed', None) != ('verify', 'failed', 'verify failed: ...')`.
- `test_a_failed_phase_detail_survives_a_rebuild_from_the_journal` fails on the `_phase_details(after)` assertion, where `detail` is `None`.
- `test_re_recording_a_phase_overwrites_its_detail` fails because `changed` has `None` where `"second reason"` is expected.

- [ ] **Step 3: Add the column to `_SCHEMA`**

In `src/agent_manager/store.py`, replace the `phases` table block (lines 64-75):

```python
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
```

with:

```python
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
```

(`detail` goes last, after `position`, so a fresh table matches what `ALTER TABLE ... ADD COLUMN` produces in Task 2.)

- [ ] **Step 4: Write `detail` in `Store._write_phase_row`**

In `src/agent_manager/store.py`, replace the whole body of `_write_phase_row` (lines 919-948):

```python
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
```

with:

```python
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
        self._conn.commit()
```

(`detail=excluded.detail`, not `COALESCE`, so re-recording with `detail=None` clears it. The insert names its columns, so where `detail` sits in the table does not matter here.)

- [ ] **Step 5: Read `detail` in the module-level `load_run`**

In `src/agent_manager/store.py`, inside `load_run` (around line 494), replace:

```python
                phase = models.PhaseRun(
                    name=phase_row["name"],
                    kind=phase_row["kind"],
                    status=phase_row["status"],
                    started_at=phase_row["started_at"],
                    ended_at=phase_row["ended_at"],
                )
```

with:

```python
                phase = models.PhaseRun(
                    name=phase_row["name"],
                    kind=phase_row["kind"],
                    status=phase_row["status"],
                    started_at=phase_row["started_at"],
                    ended_at=phase_row["ended_at"],
                    detail=phase_row["detail"],
                )
```

`Store.load_run` (around line 1002) delegates to this function, so leave it unchanged.

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v -k "fresh_phases_table_carries_detail or failed_phase_detail or re_recording_a_phase_overwrites"`

Expected: 4 passed.

- [ ] **Step 7: Run the whole store module to check nothing regressed**

Run: `uv run pytest tests/test_store.py`

Expected: all pass. The existing rebuild-equality tests, such as `test_a_db_truncated_mid_run_is_rebuilt_from_its_journal` and `test_rebuilding_twice_changes_nothing_and_duplicates_nothing`, compare whole `Run` objects and must still be equal.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "Persist PhaseRun.detail in the phases projection"
```

---

### Task 2: Add `detail` in place to a pre-existing `phases` table in `open_db`

**Files:**
- Modify: `src/agent_manager/store.py:130-161` (new `_ADDED_COLUMNS` constant and `_add_missing_columns` helper placed between `BUSY_TIMEOUT_SECONDS` and `open_db`, then `open_db` body and docstring)
- Test: `tests/test_store.py`, appended to the `# -- phase detail` section added in Task 1, immediately before the `# -- checkpoints` comment

**Interfaces:**
- Consumes (from Task 1): `FAILURE_DETAIL`, `PHASE_COLUMNS`, `_record_failed_phase(st, repo, detail=FAILURE_DETAIL)`, `_phase_details(run)`, `_EXPECTED_DETAILS`. Also the `detail` column in `_SCHEMA`, `_write_phase_row` writing it, and `load_run` reading it.
- Produces: `store._ADDED_COLUMNS: tuple[tuple[str, str, str], ...]` holding `(table, column, sql_type)`, and `store._add_missing_columns(conn: sqlite3.Connection) -> None`, called from `open_db` after `executescript(_SCHEMA)` and before `commit()`. Both are module-private. `open_db`'s signature is unchanged: `open_db(root: Path) -> sqlite3.Connection`.

- [ ] **Step 1: Write the failing test**

In `tests/test_store.py`, find this exact text (the end of `test_re_recording_a_phase_overwrites_its_detail` from Task 1 and the checkpoints header):

```python
    assert _phase_details(cleared) == [
        ("implement", "done", None),
        ("verify", "done", None),
    ]
    assert rows == 2


# -- checkpoints ---------------------------------------------------------------
```

Replace it with:

```python
    assert _phase_details(cleared) == [
        ("implement", "done", None),
        ("verify", "done", None),
    ]
    assert rows == 2


_LEGACY_PHASES = """
DROP TABLE phases;
CREATE TABLE phases (
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
INSERT INTO phases (run_id, story_id, card_id, name, kind, status,
                    started_at, ended_at, position)
VALUES ('run-2026-09-23-01', '8831189b', 'ef248597', 'verify', 'deterministic',
        'failed', NULL, NULL, 0);
"""
"""The `phases` table exactly as it shipped before `detail`, with one row."""


def test_a_phases_table_from_before_detail_gains_the_column_and_rebuild_fills_it(repo):
    # The journal already carries the reason; only the projection lost it.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_failed_phase(st, repo)
    finally:
        st.close()

    legacy = store.open_db(repo)
    legacy.executescript(_LEGACY_PHASES)
    legacy.commit()
    legacy.close()

    migrated = store.open_db(repo)
    try:
        columns = [
            row["name"]
            for row in migrated.execute("PRAGMA table_info(phases)").fetchall()
        ]
        kept = [
            (row["name"], row["status"], row["detail"])
            for row in migrated.execute("SELECT name, status, detail FROM phases")
        ]
        stale = store.load_run(migrated, RUN_ID)
    finally:
        migrated.close()

    assert columns == PHASE_COLUMNS
    assert kept == [("verify", "failed", None)]
    assert _phase_details(stale) == [("verify", "failed", None)]

    # Opening an already-migrated database again adds nothing and raises nothing.
    again = store.open_db(repo)
    try:
        reopened_columns = [
            row["name"] for row in again.execute("PRAGMA table_info(phases)").fetchall()
        ]
    finally:
        again.close()
    assert reopened_columns == PHASE_COLUMNS

    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
    finally:
        rebuilt.close()

    assert _phase_details(after) == _EXPECTED_DETAILS


# -- checkpoints ---------------------------------------------------------------
```

(The literal `'run-2026-09-23-01'` in `_LEGACY_PHASES` is `RUN_ID`, and `'8831189b'` / `'ef248597'` are the story and subtask cards `_record_failed_phase` uses. `runs`, `stories` and `subtasks` are left as Task 1 wrote them, so `load_run` finds the legacy phase row under its subtask.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_a_phases_table_from_before_detail_gains_the_column_and_rebuild_fills_it -v`

Expected: FAIL with `sqlite3.OperationalError: no such column: detail`, raised by the `SELECT name, status, detail FROM phases` on the reopened legacy database, because `CREATE TABLE IF NOT EXISTS` leaves the old table as it is.

- [ ] **Step 3: Add the additive migration to `open_db`**

In `src/agent_manager/store.py`, replace the `open_db` function (lines 139-161):

```python
def open_db(root: Path) -> sqlite3.Connection:
    """Open the per-project projection, applying the schema idempotently.

    WAL mode is set before the schema so a reader never blocks the writer. Every
    `CREATE` is `IF NOT EXISTS`, so reopening an existing database neither
    destroys nor migrates what is already there.

    The connection may be used from any thread of the one process that writes a
    run (P2), so `check_same_thread` is off; `Store` serialises that use behind
    its own lock. `BUSY_TIMEOUT_SECONDS` covers a reader in another process,
    such as `am status`, holding the database briefly. Two `am` processes
    writing one run remain unsupported.
    """
    conn = sqlite3.connect(
        paths.project_db_path(root),
        timeout=BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
```

with:

```python
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("phases", "detail", "TEXT"),
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

    WAL mode is set before the schema so a reader never blocks the writer. Every
    `CREATE` is `IF NOT EXISTS`, so reopening an existing database never
    destroys what is already there. The only migration is additive:
    `_add_missing_columns` appends each column in `_ADDED_COLUMNS` that an older
    table lacks, as a nullable column. Existing rows keep their data and read
    the new column as NULL. It is a no-op on a database that already has the
    column, so opening the same database any number of times is safe.

    The connection may be used from any thread of the one process that writes a
    run (P2), so `check_same_thread` is off; `Store` serialises that use behind
    its own lock. `BUSY_TIMEOUT_SECONDS` covers a reader in another process,
    such as `am status`, holding the database briefly. Two `am` processes
    writing one run remain unsupported.
    """
    conn = sqlite3.connect(
        paths.project_db_path(root),
        timeout=BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _add_missing_columns(conn)
    conn.commit()
    return conn
```

(The f-strings only interpolate values from the module constant `_ADDED_COLUMNS`, never input. `row["name"]` works because `row_factory` is set before the call.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_store.py::test_a_phases_table_from_before_detail_gains_the_column_and_rebuild_fills_it -v`

Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`

Expected: every test passes. The e2e marker is excluded by the default `addopts`. Pay attention to `test_reopening_an_existing_db_keeps_its_rows`, `test_the_control_tables_appear_on_an_existing_database` and `test_open_db_creates_every_projection_table`, which all reopen databases through the changed `open_db`.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "Add the phases.detail column in place on databases that predate it"
```
