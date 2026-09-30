<!-- task-pipeline: validated -->
# Subtask 6bdc1690 — Add Run.milestone_id and stop relying on the short-id parse

Story: 20096657 "Stop losing phase failure reasons and milestone identity" (milestone 13, architecture cleanup). Parent design: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md` §3 "S2" (lines ~143-151), §5 (lines ~291-299), §6 (lines ~301-336).

## Prerequisite

Blocked by sibling 503e0d1b "Persist PhaseRun.detail through the store" (done, on branch `m13/task-persist-phaserun-detail-503e0d1b`, e.g. commit 8856405). Its `store.py` changes — the `_ADDED_COLUMNS` tuple and the `_add_missing_columns(conn)` helper called from `open_db` after `executescript(_SCHEMA)` and before `commit()` — must be merged or rebased into this branch before work starts. This subtask reuses that mechanism; it does not add a second one.

## Scope

- `src/agent_manager/models.py`: `class Run` gains `milestone_id: str | None = None`.
- `src/agent_manager/store.py`:
  - `_SCHEMA`'s `runs` `CREATE TABLE IF NOT EXISTS` gains `milestone_id TEXT` as its last column (the `_ADDED_COLUMNS` invariant: fresh and migrated databases end with the same column order).
  - `_ADDED_COLUMNS` gains `("runs", "milestone_id", "TEXT")`.
  - `Store._write_run_row` writes `milestone_id` on insert and on the `ON CONFLICT ... UPDATE` path.
  - `load_run` reads `row["milestone_id"]` into `models.Run(...)`.
- `src/agent_manager/orchestrate.py`:
  - `run_milestone`: the fresh-run `models.Run(...)` construction sets `milestone_id=milestone_card.id`. The resume branch (`resumed.model_copy(update={"status": "started"})`) also sets `milestone_id=milestone_card.id`, so a pre-migration run resolved once by the fallback is stamped from then on.
  - `find_run_milestone` takes the resumed `models.Run` (not just its id). When `run.milestone_id` is set, it returns the root whose full `id` equals it. Only when `milestone_id` is None (runs written before this migration) does it fall back to the existing parse: `run.id.rsplit("-", 1)[-1]` matched against `dag.short_id(node.id)`. Update the docstring, which currently says `models.Run` records no milestone id. The one caller (`run_milestone`, currently `find_run_milestone(roots, resumed.id)`) passes `resumed`.

## Observable behavior

- New milestone runs persist their milestone card's full id. Resume uses that id, so two root cards whose 8-character short ids collide no longer make a resume ambiguous.
- Runs recorded before this change still resume through the short-id fallback, with the same result and the same errors as today.
- Opening an existing database adds the nullable column in place (`ALTER TABLE ... ADD COLUMN`). Existing rows read back as `milestone_id=None`. Nothing is rebuilt or dropped.
- `rebuild_from_journal` needs no change. The journal's `record_run` dump (`model_dump(mode="json", exclude={"stories"})`) carries the new field automatically, and older journal lines without it validate to None.
- No CLI output, envelope, or exit-code change (spec §5).

## Error paths

- `milestone_id` set, but no root card on the board has that id (card deleted or reparented): raise `cli.NotResumableError` with a message naming the recorded milestone id. Do not fall back to the short-id parse in this case, because the recorded id is authoritative.
- `milestone_id` None, and the fallback finds zero or several matching roots: the existing `NotResumableError` wording stays unchanged.

## Out of scope

- The `phases.detail` column, `_write_phase_row`, the phase-detail read, and the design of `_add_missing_columns` all belong to 503e0d1b.
- `cli.mint_run_id` and the run-id format stay unchanged.
- Also out of scope: the pygents turn/phase model, the checkpoint format, the harness adapter contract, adding a typecheck or CI gate, rewriting boundary monkeypatches, and replacing grafo.

## Tests

Tier placement follows CLAUDE.md: tests mirror source modules under `tests/`, and `tests/e2e/` is only for whole-process CLI wiring. Everything here is store/model/orchestrate unit logic, so none of it goes in e2e.

- `tests/test_models.py`: `Run` validates without `milestone_id` (defaults to None) and round-trips a given value.
- `tests/test_store.py`: a run recorded with `milestone_id` comes back with it from `load_run`.
- `tests/test_store.py`: a database created with the old `runs` schema (no `milestone_id`) gains the column on `open_db`. Its existing run loads with `milestone_id=None`. The column is last, and the fresh and migrated `PRAGMA table_info(runs)` column orders match.
- `tests/test_store.py`: `rebuild_from_journal` restores `milestone_id` from a journal that has it, and yields None from a journal line that lacks it.
- `tests/test_orchestrate.py`: `find_run_milestone` with two root cards whose short ids collide. Each run, with its `milestone_id` recorded, resolves to its own card. This is the required regression case from the card and spec §6.
- `tests/test_orchestrate.py`: `find_run_milestone` with `milestone_id=None` still uses the short-id parse. Update the existing test near line 3665 (`test_a_resumed_run_names_its_milestone_by_the_short_id_in_its_run_id`), both of whose calls pass a raw run id string (`RESUME_RUN_ID` and a second literal), to each pass a `Run` with `milestone_id=None` and that id. Its ambiguity/not-found error still raises.
- `tests/test_orchestrate.py`: `find_run_milestone` with a `milestone_id` that matches no root raises `NotResumableError`.
- `tests/test_orchestrate.py`: `run_milestone` stamps `milestone_id` on a fresh run's stored record. Resuming a pre-migration run (no `milestone_id`) leaves its record stamped with the resolved card id.

## Verification

`uv run pytest` (full suite, per CLAUDE.md; no typecheck or lint command).

---

# Run.milestone_id Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist each milestone run's full milestone card id (`Run.milestone_id`) and resume from it, keeping the short-id parse only as a fallback for runs recorded before the column existed.

**Architecture:** A nullable field on `models.Run` flows through the journal automatically (`record_run` dumps the model) and through the SQLite projection via one new `runs.milestone_id` column, added to fresh databases by `_SCHEMA` and to old ones by the existing `_ADDED_COLUMNS`/`_add_missing_columns` migration. `orchestrate.run_milestone` stamps the id on both fresh and resumed runs, and `orchestrate.find_run_milestone` matches on it by full id before falling back to the short-id parse.

**Tech Stack:** Python, Pydantic v2, sqlite3, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-add-run-milestone-id-6bdc1690/docs/superpowers/specs/task-add-run-milestone-id-6bdc1690-design.md` (prepended verbatim above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-add-run-milestone-id-6bdc1690` on branch `m13/task-add-run-milestone-id-6bdc1690`. Run every command from that directory.

## Global Constraints

- Migration is additive only: `ALTER TABLE ... ADD COLUMN`, nullable, through the existing `_ADDED_COLUMNS` tuple. No second migration mechanism, no table rebuild, no drop.
- `milestone_id TEXT` is the LAST column of the `runs` `CREATE TABLE IF NOT EXISTS` in `_SCHEMA`, so fresh and migrated `PRAGMA table_info(runs)` orders match.
- `models.Run.milestone_id: str | None = None` — defaulted, because `_Model` is `extra="forbid"` and old journal lines carry no such key.
- `rebuild_from_journal` is not changed.
- No CLI output, envelope, or exit-code change (`cli.status_payload` picks run fields through the explicit `RUN_IDENTITY` list, so leave that list alone).
- `cli.mint_run_id` and the run-id format stay unchanged.
- Do not touch `PhaseRun.detail`, `_write_phase_row`, the phase-detail read, or the design of `_add_missing_columns` (sibling 503e0d1b owns them).
- A recorded `milestone_id` that matches no root raises `cli.NotResumableError` naming that id and never falls back to the short-id parse.
- With `milestone_id` None, the fallback's `NotResumableError` wording stays byte-identical: `run {run_id!r} belongs to milestone {short}, and {n} root cards on the board have that short id`.
- New tests go in `tests/test_models.py`, `tests/test_store.py`, `tests/test_orchestrate.py`. None in `tests/e2e/`.
- Verification: `uv run pytest`. There is no typecheck or lint command.

## Review Focus

1. Re-recording an existing run row with a new `milestone_id` (the resume stamping path goes through `ON CONFLICT(id) DO UPDATE`) must overwrite the stored value, not keep the old NULL. Pinned in Task 2, `test_a_runs_milestone_id_round_trips_through_load_run_and_is_overwritten`.
2. Opening an already-migrated database a second time must add nothing and raise nothing (no "duplicate column" error). Pinned in Task 2, `test_a_runs_table_from_before_milestone_id_gains_the_column_and_keeps_its_row` (reopen block).
3. A run whose recorded `milestone_id` is gone from the board, while a different root happens to share the run id's short id, must be refused rather than silently resumed against the wrong milestone. Pinned in Task 3, `test_a_recorded_milestone_id_no_root_carries_is_refused_without_the_short_id_fallback`.
4. A `run_upsert` journal line written before the field existed (key absent, not `null`) must rebuild to `milestone_id=None` rather than failing `extra="forbid"` validation or the row write. Pinned in Task 2, `test_a_run_upsert_line_from_before_milestone_id_rebuilds_to_none`.
5. `board.roots` returning `None` with a recorded `milestone_id` must raise `NotResumableError`, not `TypeError`. Pinned in Task 3 (the `None` roots assertion in the same refusal test).

---

## Pre-flight: confirm the prerequisite is on this branch

- [ ] **Step 1: Check the sibling's migration helper exists**

Run: `grep -n "_ADDED_COLUMNS\|def _add_missing_columns\|_add_missing_columns(conn)" src/agent_manager/store.py`
Expected: matches at the `_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (` definition (~line 140) containing `("phases", "detail", "TEXT"),`, at `def _add_missing_columns(conn: sqlite3.Connection) -> None:` (~line 151), and at the call inside `open_db` (~line 192). If any is missing, stop: the branch was not cut from `m13/task-persist-phaserun-detail-503e0d1b`, and this plan must not be executed until it is.

- [ ] **Step 2: Confirm the suite is green before touching anything**

Run: `uv run pytest -q`
Expected: all tests pass (skips allowed for `requires_git`/`requires_brd` only if those tools are absent).

---

### Task 1: `models.Run.milestone_id`

**Files:**
- Modify: `src/agent_manager/models.py:152-163` (`class Run`)
- Test: `tests/test_models.py` (insert after `test_minimal_run_needs_only_its_identity_fields`, which ends at ~line 303)

**Interfaces:**
- Consumes: nothing.
- Produces: `models.Run.milestone_id: str | None` (default `None`). Tasks 2-4 read and set it by that exact name.

- [ ] **Step 1: Write the failing test**

Insert into `tests/test_models.py` immediately after `test_minimal_run_needs_only_its_identity_fields`:

```python
MILESTONE_ID = "9c44c2fb-0000-4000-8000-000000000000"


def test_a_run_without_a_milestone_id_defaults_to_none_and_a_given_one_round_trips():
    # S2: the milestone a run drives is recorded by full card id. It is
    # optional because task runs have none and journal lines from before the
    # field carry no such key, which `extra="forbid"` would otherwise reject.
    identity = dict(
        id="20260924T120000Z-9c44c2fb",
        workflow="milestone",
        repo_dir=Path("/home/dev/agent-manager"),
        base_branch="main",
        branch_prefix="m1/",
    )
    assert models.Run(**identity).milestone_id is None

    stamped = models.Run(**identity, milestone_id=MILESTONE_ID)
    restored = models.Run.model_validate(stamped.model_dump(mode="json"))
    assert restored.milestone_id == MILESTONE_ID
    assert restored == stamped

    old_line = stamped.model_dump(mode="json", exclude={"stories", "milestone_id"})
    assert "milestone_id" not in old_line
    assert models.Run.model_validate(old_line).milestone_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_a_run_without_a_milestone_id_defaults_to_none_and_a_given_one_round_trips -v`
Expected: FAIL with `AttributeError: 'Run' object has no attribute 'milestone_id'` (first assertion).

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/models.py`, replace the body of `class Run` so it reads:

```python
class Run(_Model):
    """The root of the state tree: one invocation of one workflow."""

    id: str = Field(min_length=1)
    workflow: str = Field(min_length=1)
    repo_dir: Path
    base_branch: str = Field(min_length=1)
    branch_prefix: str = Field(min_length=1)
    status: Status = "pending"
    started_at: datetime | None = None
    config: RunConfig = Field(default_factory=RunConfig)
    milestone_id: str | None = None
    """The full id of the milestone card a `milestone` run drives.

    Resume finds its milestone by this id. It is None for a task run, and for
    a milestone run recorded before the field existed: those fall back to the
    short id at the end of the run id (`orchestrate.find_run_milestone`).
    Defaulted because journal lines written before it carry no such key.
    """

    stories: list[StoryRun] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (the new test and every existing one in the file).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/models.py tests/test_models.py
git commit -m "feat(models): add optional Run.milestone_id"
```

---

### Task 2: Persist `milestone_id` in the `runs` projection

**Files:**
- Modify: `src/agent_manager/store.py:29-39` (`_SCHEMA` runs table), `:140-142` (`_ADDED_COLUMNS`), `:486-495` (`load_run`'s `models.Run(...)`), `:872-899` (`Store._write_run_row`)
- Test: `tests/test_store.py` (insert after `test_a_phases_table_from_before_detail_gains_the_column_and_rebuild_fills_it`, which ends at ~line 1959, before the `# -- checkpoints` comment block)

**Interfaces:**
- Consumes: `models.Run.milestone_id: str | None` (Task 1).
- Produces: `store.load_run(conn, run_id)` and `Store.load_run(run_id)` return runs with `milestone_id` populated from `runs.milestone_id`; `Store.record_run(run)` persists and overwrites it. Tasks 3-4 rely on `resumable_milestone_run` (which uses the free `load_run`) returning the stored value.

- [ ] **Step 1: Write the failing tests**

Insert into `tests/test_store.py` after `test_a_phases_table_from_before_detail_gains_the_column_and_rebuild_fills_it` and before the `# -- checkpoints ----` comment:

```python
# -- run milestone id ----------------------------------------------------------
#
# `Run.milestone_id` is the full id of the milestone card a run drives
# (architecture cleanup S2). The journal carries it through `record_run`'s
# model dump; these pin that the projection does too. Steps tier: real temp DB
# and journal, no harness.

MILESTONE_ID = "9c44c2fb-0000-4000-8000-000000000000"

RUN_COLUMNS = [
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
    "config",
    "milestone_id",
]
"""`milestone_id` is last: `ALTER TABLE ... ADD COLUMN` appends, so a fresh and
a migrated database only agree if the schema puts it there too."""


def _run_columns(conn: sqlite3.Connection) -> list[str]:
    return [row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()]


def test_a_fresh_runs_table_carries_milestone_id_as_its_last_column(repo):
    conn = store.open_db(repo)
    try:
        columns = _run_columns(conn)
    finally:
        conn.close()

    assert columns == RUN_COLUMNS


def test_a_runs_milestone_id_round_trips_through_load_run_and_is_overwritten(repo):
    # A resume re-records an existing run row to stamp it, so the upsert's
    # conflict clause must assign the new value, not keep the old NULL.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        unstamped = st.load_run(RUN_ID)
        st.record_run(_run(repo).model_copy(update={"milestone_id": MILESTONE_ID}))
        via_store = st.load_run(RUN_ID)
        via_connection = store.load_run(st.connection, RUN_ID)
        rows = st.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        st.close()

    assert unstamped is not None and unstamped.milestone_id is None
    assert via_store is not None and via_store.milestone_id == MILESTONE_ID
    assert via_connection is not None and via_connection.milestone_id == MILESTONE_ID
    assert rows == 1


_LEGACY_RUNS = """
DROP TABLE runs;
CREATE TABLE runs (
    id            TEXT PRIMARY KEY,
    workflow      TEXT NOT NULL,
    repo_dir      TEXT NOT NULL,
    base_branch   TEXT NOT NULL,
    branch_prefix TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TEXT,
    config        TEXT NOT NULL
);
INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix, status,
                  started_at, config)
VALUES ('run-2026-09-23-01', 'milestone', '/repo', 'main', 'm1/', 'escalated',
        NULL, '{}');
"""
"""The `runs` table exactly as it shipped before `milestone_id`, with one row."""


def test_a_runs_table_from_before_milestone_id_gains_the_column_and_keeps_its_row(repo):
    fresh = store.open_db(repo)
    try:
        fresh_columns = _run_columns(fresh)
        fresh.executescript(_LEGACY_RUNS)
        fresh.commit()
    finally:
        fresh.close()

    migrated = store.open_db(repo)
    try:
        migrated_columns = _run_columns(migrated)
        kept = [
            (row["id"], row["status"], row["milestone_id"])
            for row in migrated.execute("SELECT id, status, milestone_id FROM runs")
        ]
        old = store.load_run(migrated, RUN_ID)
    finally:
        migrated.close()

    assert migrated_columns == fresh_columns == RUN_COLUMNS
    assert kept == [(RUN_ID, "escalated", None)]
    assert old is not None
    assert (old.id, old.status, old.milestone_id) == (RUN_ID, "escalated", None)

    # Opening an already-migrated database again adds nothing and raises nothing.
    again = store.open_db(repo)
    try:
        reopened_columns = _run_columns(again)
    finally:
        again.close()
    assert reopened_columns == RUN_COLUMNS


def test_a_runs_milestone_id_survives_a_rebuild_from_the_journal(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo).model_copy(update={"milestone_id": MILESTONE_ID}))
    finally:
        st.close()

    _truncate_db(repo)
    rebuilt = store.Store.open(repo, RUN_ID)
    try:
        returned = rebuilt.rebuild_from_journal(RUN_ID)
        after = rebuilt.load_run(RUN_ID)
    finally:
        rebuilt.close()

    assert returned.milestone_id == MILESTONE_ID
    assert after is not None and after.milestone_id == MILESTONE_ID
    assert after == returned


def test_a_run_upsert_line_from_before_milestone_id_rebuilds_to_none(repo):
    # A journal written before the field existed has no `milestone_id` key at
    # all (not `null`): it must still validate, and project a NULL column.
    payload = _run(repo).model_dump(mode="json", exclude={"stories", "milestone_id"})
    assert "milestone_id" not in payload
    st = store.Store.open(repo, RUN_ID)
    try:
        _append_raw(
            st.journal,
            {
                "seq": 1,
                "ts": "2026-09-23T10:00:00+00:00",
                "run_id": RUN_ID,
                "event": "run_upsert",
                "payload": payload,
            },
        )
        returned = st.rebuild_from_journal(RUN_ID)
        after = st.load_run(RUN_ID)
        row = st.connection.execute(
            "SELECT milestone_id FROM runs WHERE id = ?", (RUN_ID,)
        ).fetchone()
    finally:
        st.close()

    assert returned.milestone_id is None
    assert after is not None and after.milestone_id is None
    assert row["milestone_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v -k "milestone_id"`
Expected: FAIL. `test_a_fresh_runs_table_carries_milestone_id_as_its_last_column` fails on the column list (no `milestone_id`); the round-trip and journal-rebuild tests fail because `load_run` returns `milestone_id=None`; the legacy-table test fails on the column list; the old-line test errors with `sqlite3.OperationalError: no such column: milestone_id`.

- [ ] **Step 3: Add the column to `_SCHEMA`**

In `src/agent_manager/store.py`, change the `runs` table in `_SCHEMA` to:

```python
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
```

- [ ] **Step 4: Register the column in `_ADDED_COLUMNS`**

In `src/agent_manager/store.py`, change the tuple to:

```python
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("phases", "detail", "TEXT"),
    ("runs", "milestone_id", "TEXT"),
)
```

- [ ] **Step 5: Read the column in `load_run`**

In `src/agent_manager/store.py`, in the free function `load_run`, change the `models.Run(...)` construction to:

```python
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
```

- [ ] **Step 6: Write the column in `Store._write_run_row`**

In `src/agent_manager/store.py`, replace `_write_run_row` with:

```python
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
        self._conn.commit()
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (the five new tests and every existing store test, including the explicit-column `INSERT INTO runs` fixtures near lines 122, 2311 and 2522, which stay valid because the new column is nullable).

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): persist runs.milestone_id via the additive column migration"
```

---

### Task 3: `find_run_milestone` matches on the recorded id

**Files:**
- Modify: `src/agent_manager/orchestrate.py:581-597` (`find_run_milestone`), `:1380` (its one caller in `run_milestone`)
- Test: `tests/test_orchestrate.py:3661-3667` (replace `test_a_resumed_run_names_its_milestone_by_the_short_id_in_its_run_id` and add new tests right after it, before `def _resume_stories`)

**Interfaces:**
- Consumes: `models.Run.milestone_id` (Task 1).
- Produces: `orchestrate.find_run_milestone(roots: Sequence[models.CardNode] | None, run: models.Run) -> models.CardNode`. Raises `cli.NotResumableError`. Task 4's `run_milestone` calls it as `find_run_milestone(roots, resumed)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrate.py`, replace the whole of `test_a_resumed_run_names_its_milestone_by_the_short_id_in_its_run_id` (lines ~3661-3667) with:

```python
def _milestone_run(run_id: str, milestone_id: str | None) -> models.Run:
    """A recorded milestone run as `resumable_milestone_run` hands it on."""
    return models.Run(
        id=run_id,
        workflow="milestone",
        repo_dir=Path("/repo"),
        base_branch="main",
        branch_prefix=PREFIX,
        status="escalated",
        milestone_id=milestone_id,
    )


def test_a_resumed_run_names_its_milestone_by_the_short_id_in_its_run_id():
    """A run recorded before `milestone_id` existed falls back to the short
    id `cli.mint_run_id` put at the end of its run id, errors unchanged."""
    wanted = models.CardNode(id=_plan_id(9), title="Milestone 9", status="todo")
    other = models.CardNode(id=_plan_id(8), title="Milestone 8", status="todo")

    assert (
        orchestrate.find_run_milestone([other, wanted], _milestone_run(RESUME_RUN_ID, None))
        is wanted
    )
    missing = "20260924T120000Z-00000007"
    with pytest.raises(cli.NotResumableError) as caught:
        orchestrate.find_run_milestone([other, wanted], _milestone_run(missing, None))
    assert str(caught.value) == (
        f"run {missing!r} belongs to milestone 00000007, and 0 root"
        " cards on the board have that short id"
    )


def test_two_milestones_whose_short_ids_collide_each_resume_by_their_recorded_id():
    """S2's regression case: two roots share the eight-character short id, so
    the run id alone is ambiguous, but each recorded full id picks its own."""
    first = models.CardNode(
        id="00000009-0000-4000-8000-000000000001", title="Milestone 9a", status="todo"
    )
    second = models.CardNode(
        id="00000009-0000-4000-8000-000000000002", title="Milestone 9b", status="todo"
    )
    assert dag.short_id(first.id) == dag.short_id(second.id) == "00000009"
    roots = [first, second]

    assert orchestrate.find_run_milestone(roots, _milestone_run(RESUME_RUN_ID, first.id)) is first
    assert (
        orchestrate.find_run_milestone(
            roots, _milestone_run("20260924T130000Z-00000009", second.id)
        )
        is second
    )
    # Without the record the same pair is still ambiguous: the fallback is unchanged.
    with pytest.raises(cli.NotResumableError, match="2 root cards"):
        orchestrate.find_run_milestone(roots, _milestone_run(RESUME_RUN_ID, None))


def test_a_recorded_milestone_id_no_root_carries_is_refused_without_the_short_id_fallback():
    """The recorded id is authoritative: a root that merely shares the run
    id's short id is not a stand-in for a deleted or reparented milestone."""
    lookalike = models.CardNode(id=_plan_id(9), title="Milestone 9", status="todo")
    gone = "00000009-0000-4000-8000-00000000dead"
    run = _milestone_run(RESUME_RUN_ID, gone)

    with pytest.raises(cli.NotResumableError) as caught:
        orchestrate.find_run_milestone([lookalike], run)
    assert str(caught.value) == (
        f"run {RESUME_RUN_ID!r} belongs to milestone {gone}, and no root card"
        " on the board has that id"
    )
    with pytest.raises(cli.NotResumableError, match=gone):
        orchestrate.find_run_milestone(None, run)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -v -k "find_run_milestone or names_its_milestone or short_ids_collide or no_root_carries"`
Expected: FAIL. Each of the three tests errors with `AttributeError: 'Run' object has no attribute 'rsplit'` because `find_run_milestone` still treats its second argument as a run-id string.

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/orchestrate.py`, replace `find_run_milestone` with:

```python
def find_run_milestone(
    roots: Sequence[models.CardNode] | None, run: models.Run
) -> models.CardNode:
    """The root card a resumed milestone `run` drives.

    `run.milestone_id` is the milestone's full card id, recorded when the run
    started, and is authoritative: the root with exactly that id, or
    `cli.NotResumableError` if the board has none (the card was deleted or
    reparented). It never falls back to the short id, which could name a
    different milestone.

    A run recorded before `milestone_id` existed has None there. For those,
    `cli.mint_run_id` built the run id as `<timestamp>-<short milestone id>`,
    so the one root whose short id ends the run id is the milestone. Zero or
    several such roots is `cli.NotResumableError`. A title edit breaks
    neither lookup.
    """
    if run.milestone_id is not None:
        for node in roots or []:
            if node.id == run.milestone_id:
                return node
        raise cli.NotResumableError(
            f"run {run.id!r} belongs to milestone {run.milestone_id}, and no root"
            " card on the board has that id"
        )
    short = run.id.rsplit("-", 1)[-1]
    matches = [node for node in roots or [] if dag.short_id(node.id) == short]
    if len(matches) != 1:
        raise cli.NotResumableError(
            f"run {run.id!r} belongs to milestone {short}, and {len(matches)} root"
            " cards on the board have that short id"
        )
    return matches[0]
```

Then, in `run_milestone`, change the caller (line ~1380) from:

```python
        milestone_card = find_run_milestone(roots, resumed.id)
```

to:

```python
        milestone_card = find_run_milestone(roots, resumed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: PASS (the three tests above plus every existing `run_milestone(resume_run_id=...)` test, which now resolve through the fallback since Task 4 has not stamped anything yet).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): resume a milestone by its recorded id, short id as fallback"
```

---

### Task 4: `run_milestone` stamps `milestone_id` on fresh and resumed runs

**Files:**
- Modify: `src/agent_manager/orchestrate.py:1342-1345` (docstring), `:1392-1404` (fresh `models.Run(...)` and resume `model_copy`)
- Test: `tests/test_orchestrate.py` — fresh-run test after `test_the_bound_is_recorded_in_the_run_config` (~line 1944-1950); resume test immediately before the `@requires_git` decorator of `test_a_request_left_under_an_earlier_lease_never_reaches_the_resumed_run` (~line 4274)

**Interfaces:**
- Consumes: `models.Run.milestone_id` (Task 1); `store.load_run`/`Store.record_run` persisting it (Task 2); `find_run_milestone(roots, resumed)` (Task 3).
- Produces: every milestone run recorded by `run_milestone` carries `milestone_id == milestone_card.id`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrate.py`, insert right after `test_the_bound_is_recorded_in_the_run_config`:

```python
@requires_git
@requires_brd
def test_a_fresh_run_records_its_milestones_full_id(project):
    shape = _milestone(project, {"A": 1})

    result = _run(project, shape["milestone"], FakeDriver())

    assert result["done"] is True, result
    assert _load(project, result["run_id"]).milestone_id == shape["milestone"]
```

And insert right before the `@requires_git` line above `test_a_request_left_under_an_earlier_lease_never_reaches_the_resumed_run`:

```python
@requires_git
@requires_brd
def test_resuming_a_run_recorded_before_milestone_id_stamps_it(project):
    """A pre-migration run resolves through the short-id fallback once, and
    its record carries the resolved milestone's full id from then on."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    assert first["escalated"] is True, first
    run_id = first["run_id"]
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        opened.record_run(_load(project, run_id).model_copy(update={"milestone_id": None}))
    finally:
        opened.close()
    assert _load(project, run_id).milestone_id is None

    result = _resume(project, run_id, FakeDriver())

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert _load(project, run_id).milestone_id == shape["milestone"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -v -k "records_its_milestones_full_id or before_milestone_id_stamps_it"`
Expected: FAIL on the final assertion of each: `assert None == '<milestone uuid>'`. (If `brd` or `git` is not installed both are SKIPPED; install them — these tests must actually run.)

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/orchestrate.py`, in `run_milestone`, change the fresh-run construction and the resume copy to:

```python
        run_id = cli.mint_run_id(milestone_card.id, started_at)
        run_record = models.Run(
            id=run_id,
            workflow=MILESTONE_WORKFLOW,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(max_concurrent_stories=max_concurrent),
            milestone_id=milestone_card.id,
        )
    else:
        run_id = resumed.id
        # Stamps a run recorded before `milestone_id` existed, so the next
        # resume no longer needs the short-id fallback.
        run_record = resumed.model_copy(
            update={"status": "started", "milestone_id": milestone_card.id}
        )
```

- [ ] **Step 4: Update the `run_milestone` docstring**

In the same function's docstring, replace:

```
    `resume_run_id` continues that milestone run instead (card 54e4ec29).
    `milestone`, `base_branch`, `branch_prefix`, `max_concurrent` and
    `clock` are then not read: the milestone is the one the run id names
    (`find_run_milestone`) and the rest is what the run recorded. The plan is
```

with:

```
    `resume_run_id` continues that milestone run instead (card 54e4ec29).
    `milestone`, `base_branch`, `branch_prefix`, `max_concurrent` and
    `clock` are then not read: the milestone is the one the run recorded
    (`find_run_milestone`), and the rest is what the run recorded too. Both a
    fresh and a resumed run are recorded with `milestone_id` set to the
    milestone card's full id, which stamps a run recorded before that field
    existed. The plan is
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: PASS (both new tests and all existing orchestrate tests, including the resume ones that now resolve through the recorded id).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): stamp Run.milestone_id on fresh and resumed milestone runs"
```

---

### Task 5: Full verification

**Files:** none changed.

**Interfaces:**
- Consumes: everything above.
- Produces: a green suite.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: every test passes, including `tests/test_cli.py` (status payloads unchanged, since `status_payload` selects `RUN_IDENTITY` fields explicitly) and `tests/e2e/` (resume flows now go through the recorded id).

- [ ] **Step 2: Confirm scope**

Run: `git diff --stat m13/task-persist-phaserun-detail-503e0d1b...HEAD`
Expected: only `src/agent_manager/models.py`, `src/agent_manager/store.py`, `src/agent_manager/orchestrate.py`, `tests/test_models.py`, `tests/test_store.py`, `tests/test_orchestrate.py` (plus this plan and the spec under `docs/superpowers/`). No change to `cli.py`, `_add_missing_columns`, `_write_phase_row`, or anything under `tests/e2e/`.
