<!-- task-pipeline: validated -->
# Record control requests and leases in the store (card b973aa1d, plan Task 1.1)

Narrows `docs/superpowers/specs/2026-09-27-live-control-design.md` (decisions C1–C12) and Task 1.1 of `docs/superpowers/plans/2026-09-27-live-control.md` to this subtask. Both files are on the `docs/live-control` branch (`.claude/worktrees/docs-live-control/`), not on `master`. Parent story: ad03d386 "Control state and the park with a reason". Commit message: `feat(store): control requests, run leases and the cancelled status`.

## Scope

This subtask changes only the store and model layers: `src/agent_manager/models.py` and `src/agent_manager/store.py`, with tests in `tests/test_store.py` and `tests/test_models.py`. It adds no CLI, no async code, no watcher, no `Lease` class, and no `StopSignal` changes. Those belong to sibling 3113456a (Task 1.2: `runtime/stop.py`, new `control.py`), which consumes the names below exactly as given. Do not add anything else.

## Deliverables

1. **`models.Status`** (models.py:25) gains `"cancelled"`: `Literal["pending", "started", "done", "failed", "escalated", "stopped", "cancelled"]`.
2. **`_SCHEMA`** (store.py:28–106) gains two `CREATE TABLE IF NOT EXISTS` tables after `checkpoints`:
   - `run_controls`: columns `run_id TEXT NOT NULL`, `seq INTEGER NOT NULL`, `lease TEXT NOT NULL`, `command TEXT NOT NULL CHECK (command IN ('pause','cancel'))`, `requested_at TEXT NOT NULL`, `handled_at TEXT`, with `PRIMARY KEY (run_id, seq)`.
   - `run_leases`: columns `run_id TEXT PRIMARY KEY`, `token TEXT NOT NULL`, `pid INTEGER NOT NULL`, `host TEXT NOT NULL`, `acquired_at TEXT NOT NULL`, `heartbeat_at TEXT NOT NULL`, `accepting INTEGER NOT NULL`.

   No existing table gains a column and no CHECK constraint changes. `runs.status` has no CHECK, so `cancelled` needs no schema change. An existing pre-M9 database gets the two tables the next time `open_db` runs.
3. **Row types** (frozen dataclasses):
   - `LeaseRow(run_id: str, token: str, pid: int, host: str, acquired_at: datetime, heartbeat_at: datetime, accepting: bool)`
   - `ControlRow(run_id: str, seq: int, lease: str, command: str, requested_at: datetime, handled_at: datetime | None)`

   Timestamps are stored as text and parsed back the same way the existing store does. `accepting` comes back as a real `bool`.
4. **Free functions over a `sqlite3.Connection`**, in the style of `load_run` (store.py:416):
   - `run_status(conn, run_id) -> str | None` returns `runs.status`, or `None` if the run is unknown.
   - `read_lease(conn, run_id) -> LeaseRow | None`.
   - `control_requests(conn, run_id, *, lease: str | None = None) -> list[ControlRow]` returns rows in `seq` order. With `lease=None` it returns every lease's rows.
   - `@contextmanager immediate(conn)`:
     - If `conn.in_transaction`, commit the implicit transaction first.
     - Run `conn.execute("BEGIN IMMEDIATE")`.
     - Yield.
     - On normal exit, `conn.commit()`. On any exception, `conn.rollback()` and re-raise.

     This handles Python sqlite3's legacy-transaction behaviour.
   - `add_control(conn, run_id, *, lease, command, requested_at) -> ControlRow` inserts a row with `seq = max(seq) + 1` for that run, starting at 0, and `handled_at` NULL. It does not commit; it is meant to run inside `immediate`. An unknown `command` raises `sqlite3.IntegrityError` from the CHECK constraint. That CHECK is the only guard: no Python-side validation.
5. **`Store` methods.** Each one runs under `self._lock` and commits. Each is keyed to `self.run_id`.
   - `acquire_lease(*, token, pid, host, now) -> LeaseRow` upserts on `run_id` and sets `accepting = 1` and `acquired_at = heartbeat_at = now`.
   - `beat(token, now) -> None` sets `heartbeat_at`.
   - `close_window(token) -> None` sets `accepting = 0`.
   - `release_lease(token) -> None` deletes the row.

     `beat`, `close_window` and `release_lease` touch only the row whose `token` matches. With any other token they silently do nothing: no error.
   - `pending_controls(token) -> list[ControlRow]` returns rows for this run and this lease where `handled_at IS NULL`, in `seq` order.
   - `mark_control_handled(seq, now) -> None` sets `handled_at` on this run's row `seq`.
6. **`Store.latest_open_checkpoint(card_id, workflow)`** (store.py:922–944) changes as follows:
   - The newest-row query now `LEFT JOIN`s `runs` on `r.id = c.run_id`.
   - If the card's newest row (any run, any workflow) is `done`, the method returns `None`. It also returns `None` if that row belongs to a run whose status is `cancelled`.
   - Otherwise the second query is the same as today, with the added condition `AND run_id NOT IN (SELECT id FROM runs WHERE status = 'cancelled')`.

   The existing ordering stays: `saved_at DESC, seq DESC`. Update the docstring.
7. **Journal isolation.** Nothing journals `run_controls` or `run_leases`. `rebuild_from_journal` and `_delete_run` (store.py:948–996) leave both tables untouched. A `cancelled` run status round-trips through the journal replay and `list_runs` the same way `stopped` does.

## Error paths

- An unknown `command` in `add_control` raises `sqlite3.IntegrityError`.
- An exception inside `immediate` rolls back and re-raises, so no partial rows are left.
- A lease method called with a wrong token does nothing.
- `read_lease` and `run_status` return `None` when there is no row.

## Tests

Tier per the milestone spec §7 placement rule: tests sit beside the module they exercise, and the store/DB layer goes in `tests/test_store.py`. The plan's test snippets use fixture names that do not exist on master (`opened_store`, `root`, `_project`, `_save`, `T0`/`T1`). Adapt them to the file's real conventions: the `repo` fixture, `store.Store.open(repo, RUN_ID)`, `_save_checkpoint(st, card, reason=..., saved_at=_at(i))`, `_run(repo, run_id)`. A "second process" is a second `store.open_db` connection.

- `tests/test_models.py` (models layer): `Status` accepts `"cancelled"`, and a `Run` or subtask model validates with it.
- `tests/test_store.py` (store layer):
  - `test_the_control_tables_appear_on_an_existing_database`: build a pre-M9 schema by hand, then `open_db` adds `run_controls` and `run_leases`.
  - `test_a_lease_is_touched_only_through_its_own_token`:
    - `acquire_lease` returns a row with `accepting=True` and `heartbeat_at == now`.
    - `beat`, `close_window` and `release_lease` with the token `"other"` leave the row equal to the original.
    - `beat` then `close_window` with the right token updates `heartbeat_at` and sets `accepting is False`.
    - `release_lease` with the right token makes `read_lease` return `None`.
  - `test_a_request_from_another_connection_is_pending_for_its_lease_only`:
    - A second connection calls `add_control` twice inside `immediate`, one `pause` under lease `t1` and one `cancel` under lease `old`.
    - `pending_controls("t1")` returns `["pause"]`.
    - After `mark_control_handled(0, T1)` it returns empty.
    - `control_requests(other, run_id)` shows `handled_at` as `[T1, None]`.
  - `test_immediate_rolls_back_on_error`: `add_control` followed by an exception inside `immediate` leaves no row, and the exception propagates.
  - `test_an_unknown_command_is_refused_by_the_check`: `add_control(..., command="resume")` raises `sqlite3.IntegrityError`.
  - `test_a_cancelled_runs_checkpoints_are_never_open` (Review Focus 4):
    - A `stopped` run and a later `cancelled` run both leave `parked` rows for card `c1`; `latest_open_checkpoint("c1", "task")` is `None`.
    - An unrelated card `c2` with a `parked` row in a `stopped` run still resolves to that run.
  - `test_a_cancelled_run_round_trips_through_the_journal_and_the_listing`: a run recorded with status `cancelled` survives `rebuild_from_journal` and shows as `cancelled` in `list_runs`.
  - Also assert that `rebuild_from_journal` leaves the `run_controls` and `run_leases` rows intact. This can be folded into the round-trip test.

Verification: `uv run pytest` passes for the whole suite, including `tests/e2e`. The work is on its own `m9`-prefixed branch off `master`, and nothing is pushed.

## Note on inputs

The exploration summary handed to this stage was truncated at 8000 characters, in the middle of the test-placement paragraph. That means the upstream stage over-ran its brief. The placement rule above was confirmed against the plan's Task 1.1 file list (`tests/test_store.py`, `tests/test_models.py`) rather than inferred from the missing text.

---

# Record control requests and leases in the store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the per-project SQLite the `cancelled` run status, a `run_controls` request table and a `run_leases` table, with the readers, the `immediate` write helper and the `Store` lease/control methods that sibling Task 1.2 consumes, and make a cancelled run's checkpoints never count as open.

**Architecture:** Everything lands in the two existing modules. `models.Status` gains one literal. `store._SCHEMA` gains two row-only `CREATE TABLE IF NOT EXISTS` tables that sit outside the journal exactly like `checkpoints`. Free functions (`run_status`, `read_lease`, `control_requests`, `immediate`, `add_control`) work over any connection so a second process (a second `open_db` connection) can write requests; `Store` methods own the running process's lease and mark requests handled under `self._lock`. `latest_open_checkpoint` joins `runs` to close cancelled runs' rows.

**Tech Stack:** Python 3.12, stdlib `sqlite3` (WAL, legacy transaction control, `BEGIN IMMEDIATE`), `contextlib.contextmanager`, frozen dataclasses, pydantic for `models`, pytest.

**Spec:** the spec above (`docs/superpowers/specs/task-record-control-requests-b973aa1d-design.md` in this worktree), narrowing `docs/superpowers/specs/2026-09-27-live-control-design.md` (branch `docs/live-control`).

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m9/task-record-control-requests-b973aa1d` on `m9/task-record-control-requests-b973aa1d`, freshly cut from `master`. No other subtask's code exists here: there is no `control.py`, no `StopSignal.request`, and nothing in this plan may assume them.

**Input note:** both upstream summaries handed to this stage were truncated (the spec summary at 2000 chars, the exploration summary at 8000 chars, mid-way through the test-placement paragraph), which means those stages over-ran their briefs. This plan works from the spec read from disk and from the code on this branch, not from the missing text.

## Global Constraints

- Touch only `src/agent_manager/models.py`, `src/agent_manager/store.py`, `tests/test_models.py`, `tests/test_store.py`.
- No new runtime dependency. No socket, fifo or signal handler: the only channel is the per-project SQLite (C1).
- `run_controls` and `run_leases` are row-only tables outside the journal, like `checkpoints`. Nothing journals them, and `rebuild_from_journal` / `_delete_run` leave them alone. No existing table gains a column, and no existing `CHECK` changes (the schema is `CREATE TABLE IF NOT EXISTS` only).
- `add_control`'s table `CHECK (command IN ('pause','cancel'))` is the sole guard against unknown commands: no Python-side validation.
- `immediate(conn)`: commit an open implicit transaction first, then `conn.execute("BEGIN IMMEDIATE")`, yield, `conn.commit()` on success, `conn.rollback()` and re-raise on any exception.
- No async code, no watcher, no `Lease` class, no `StopSignal` change (those are sibling card 3113456a).
- No test sleeps to prove ordering.
- Tests follow `tests/test_store.py`'s real conventions: the `repo` fixture, `store.Store.open(repo, RUN_ID)`, `OTHER_RUN_ID`, `_at(minute)`, `_save_checkpoint(...)`, `_run(repo, run_id)`, `_truncate_db(repo)`. Nothing called `opened_store`, `root`, `_project`, `_save`, `T0`, `T1`.
- Verification for every task: `uv run pytest` (whole suite green, `tests/e2e` included).
- Branch prefix `m9`, nothing pushed, `master` never moves. The final task's commit uses the card's message: `feat(store): control requests, run leases and the cancelled status`.

## Review Focus

1. **`immediate` on a connection that already has an implicit transaction open** (an earlier uncommitted `INSERT` on the same connection): a person expects the helper to work, not to raise `cannot start a transaction within a transaction`, and expects the earlier write to be committed too. Test in Task 4 (`test_immediate_commits_an_implicit_transaction_first`).
2. **`immediate` actually takes the write lock at `BEGIN`**, so a second writer cannot slip in between reading `MAX(seq)` and inserting: while inside `immediate`, another connection's `BEGIN IMMEDIATE` fails with `database is locked` (timeout 0, no sleep). Test in Task 4 (`test_immediate_holds_the_write_lock_from_begin`).
3. **A resumed run re-acquires the lease under a new token**: the old token's `beat`/`close_window`/`release_lease` must then do nothing, and releasing a token in one run must never delete another run's lease that happens to share the token string. Test in Task 3 (`test_reacquiring_a_lease_replaces_the_old_token_and_other_runs_are_untouched`).
4. **Control `seq` is per run, and handling is per run**: two runs each number from 0, and `mark_control_handled(0, ...)` on one run leaves the other run's `seq` 0 pending. Test in Task 4 (`test_control_seqs_are_numbered_and_handled_per_run`).
5. **A cancelled run's older row must not be resurrected when a newer non-cancelled row of another workflow is newest**: the card is not closed, but `latest_open_checkpoint(card, "task")` must skip the cancelled run's `parked` row and return the older stopped run's row. Test in Task 5 (`test_latest_open_checkpoint_skips_a_cancelled_runs_row_when_it_is_not_newest`).

---

## File map

| File | Change | Task |
|---|---|---|
| `src/agent_manager/models.py:25-29` | `Status` gains `"cancelled"`, docstring updated | 1 |
| `tests/test_models.py:886-901` | exact-literal test updated; new `cancelled` acceptance test appended | 1 |
| `src/agent_manager/store.py:28-106` | `_SCHEMA` gains `run_controls`, `run_leases` | 2 |
| `src/agent_manager/store.py:14-22` | imports: `contextmanager`, `Iterator` | 3, 4 |
| `src/agent_manager/store.py:~537` (after `_checkpoint_from_row`) | `LeaseRow`, `ControlRow`, `_lease_from_row`, `_control_from_row`, `read_lease`, `control_requests`, `immediate`, `add_control`, `run_status` | 3, 4, 6 |
| `src/agent_manager/store.py:~945` (after `latest_open_checkpoint`) | new `Store` section: `acquire_lease`, `beat`, `close_window`, `release_lease`, `pending_controls`, `mark_control_handled` | 3, 4 |
| `src/agent_manager/store.py:922-944` | `latest_open_checkpoint` joins `runs` | 5 |
| `tests/test_store.py` (append after line 2067) | new section `# -- run controls and leases` | 2-6 |

---

### Task 1: `Status` gains `cancelled`

**Files:**
- Modify: `src/agent_manager/models.py:25-29`
- Test: `tests/test_models.py:886-901` (modify) and append after line 907

**Interfaces:**
- Consumes: nothing.
- Produces: `models.Status = Literal["pending", "started", "done", "failed", "escalated", "stopped", "cancelled"]`. `Run`, `StoryRun`, `SubtaskRun`, `PhaseRun` accept `status="cancelled"`; `store.RunSummary.status` (typed `models.Status`) therefore accepts it too. `AttemptStatus` is unchanged.

- [ ] **Step 1: Write the failing tests**

In `tests/test_models.py`, replace the whole function `test_status_grew_by_exactly_stopped_and_still_rejects_unknown_values` (lines 886-901) with:

```python
def test_status_is_exactly_the_lifecycle_set_and_still_rejects_unknown_values():
    assert typing.get_args(models.Status) == (
        "pending",
        "started",
        "done",
        "failed",
        "escalated",
        "stopped",
        "cancelled",
    )
    with pytest.raises(ValidationError) as excinfo:
        models.SubtaskRun(
            card_id="a3dd82f4", branch="m4/x-a3dd82f4", base_branch="main", status="halted"
        )
    message = str(excinfo.value)
    for allowed in ("pending", "started", "done", "failed", "escalated", "stopped", "cancelled"):
        assert allowed in message
```

Then append at the end of the file (after `test_attempt_status_does_not_pick_up_stopped`):

```python
def test_every_status_carrying_model_accepts_cancelled():
    assert models.PhaseRun(name="implement", kind="agent", status="cancelled").status == "cancelled"
    assert (
        models.SubtaskRun(
            card_id="a3dd82f4", branch="m9/x-a3dd82f4", base_branch="main", status="cancelled"
        ).status
        == "cancelled"
    )
    assert (
        models.StoryRun(card_id="9bfb5ac2", title="Cancel", level=0, status="cancelled").status
        == "cancelled"
    )
    assert (
        models.Run(
            id="run-2026-09-29-01",
            workflow="milestone",
            repo_dir=Path("/home/dev/agent-manager"),
            base_branch="main",
            branch_prefix="m9/",
            status="cancelled",
        ).status
        == "cancelled"
    )


def test_attempt_status_does_not_pick_up_cancelled():
    with pytest.raises(ValidationError):
        models.Attempt(n=1, dispatch=_dispatch(), status="cancelled")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -k "lifecycle_set or accepts_cancelled or pick_up_cancelled" -v`
Expected: `test_status_is_exactly_the_lifecycle_set...` FAILS (tuple lacks `"cancelled"`), `test_every_status_carrying_model_accepts_cancelled` FAILS with `ValidationError` (`Input should be 'pending', ... or 'stopped'`), `test_attempt_status_does_not_pick_up_cancelled` PASSES already (it is a guard).

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/models.py`, replace lines 25-29 with:

```python
Status = Literal[
    "pending", "started", "done", "failed", "escalated", "stopped", "cancelled"
]
"""Lifecycle of a run, story, subtask or phase. `started` is the non-terminal
state resume keys off (§9). `stopped` (addendum P4) is a clean stop on request
between phases: it is not `failed`, and relaunching the same command continues
it. `cancelled` (live control, C9) is a run closed for good by `am cancel`: its
checkpoints are never continued and it is never resumed."""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: all PASS.

Then run the whole suite: `uv run pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/models.py tests/test_models.py
git commit -m "feat(models): the cancelled run status"
```

---

### Task 2: `run_controls` and `run_leases` tables

**Files:**
- Modify: `src/agent_manager/store.py:95-106` (end of `_SCHEMA`)
- Test: `tests/test_store.py` (append a new section after line 2067)

**Interfaces:**
- Consumes: nothing.
- Produces: tables `run_controls(run_id, seq, lease, command, requested_at, handled_at)` with `PRIMARY KEY (run_id, seq)` and `CHECK (command IN ('pause', 'cancel'))`; `run_leases(run_id PRIMARY KEY, token, pid, host, acquired_at, heartbeat_at, accepting)`. Both created by `store.open_db` on new and existing databases.

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/test_store.py`:

```python
# -- run controls and leases -----------------------------------------------------
#
# Row-only tables outside the journal (live-control spec C1/C2), like
# `checkpoints`. A "second process" is a second `store.open_db` connection.
# Steps tier: real temp DB and journal, no harness.


def test_the_control_tables_appear_on_an_existing_database(repo):
    # A pre-M9 database: every table but the two new ones, with a row in it.
    first = store.open_db(repo)
    first.execute("DROP TABLE IF EXISTS run_controls")
    first.execute("DROP TABLE IF EXISTS run_leases")
    first.execute(
        "INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,"
        " status, started_at, config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (RUN_ID, "milestone", str(repo), "main", "m1/", "stopped", None, "{}"),
    )
    first.commit()
    first.close()

    conn = store.open_db(repo)
    try:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        controls = [
            row["name"] for row in conn.execute("PRAGMA table_info(run_controls)").fetchall()
        ]
        leases = [
            row["name"] for row in conn.execute("PRAGMA table_info(run_leases)").fetchall()
        ]
        kept = [row["id"] for row in conn.execute("SELECT id FROM runs").fetchall()]
    finally:
        conn.close()

    assert {"run_controls", "run_leases"} <= names
    assert controls == ["run_id", "seq", "lease", "command", "requested_at", "handled_at"]
    assert leases == [
        "run_id",
        "token",
        "pid",
        "host",
        "acquired_at",
        "heartbeat_at",
        "accepting",
    ]
    assert kept == [RUN_ID]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_the_control_tables_appear_on_an_existing_database -v`
Expected: FAIL on `assert {"run_controls", "run_leases"} <= names`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/store.py`, replace the tail of `_SCHEMA` (the `checkpoints` table through the closing `"""`, lines 95-106) with:

```python
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
"""
```

(The `checkpoints` block is unchanged; it is repeated only to anchor the edit.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): run_controls and run_leases tables"
```

---

### Task 3: Run leases (`LeaseRow`, `read_lease`, `acquire_lease`, `beat`, `close_window`, `release_lease`)

**Files:**
- Modify: `src/agent_manager/store.py` — insert after `_checkpoint_from_row` (ends line 536) and add a new `Store` section after `latest_open_checkpoint` (ends line 944)
- Test: `tests/test_store.py` (append to the section started in Task 2)

**Interfaces:**
- Consumes: the `run_leases` table (Task 2).
- Produces:
  - `@dataclass(frozen=True) class LeaseRow: run_id: str; token: str; pid: int; host: str; acquired_at: datetime; heartbeat_at: datetime; accepting: bool`
  - `read_lease(conn: sqlite3.Connection, run_id: str) -> LeaseRow | None`
  - `Store.acquire_lease(self, *, token: str, pid: int, host: str, now: datetime) -> LeaseRow`
  - `Store.beat(self, token: str, now: datetime) -> None`
  - `Store.close_window(self, token: str) -> None`
  - `Store.release_lease(self, token: str) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_a_lease_is_touched_only_through_its_own_token(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        lease = st.acquire_lease(token="t1", pid=42, host="h", now=_at(0))
        assert lease == store.LeaseRow(
            run_id=RUN_ID,
            token="t1",
            pid=42,
            host="h",
            acquired_at=_at(0),
            heartbeat_at=_at(0),
            accepting=True,
        )
        assert store.read_lease(st.connection, RUN_ID) == lease

        st.beat("other", _at(1))
        st.close_window("other")
        st.release_lease("other")
        assert store.read_lease(st.connection, RUN_ID) == lease

        st.beat("t1", _at(1))
        st.close_window("t1")
        row = store.read_lease(st.connection, RUN_ID)
        assert row is not None
        assert row.heartbeat_at == _at(1)
        assert row.acquired_at == _at(0)
        assert row.accepting is False

        st.release_lease("t1")
        assert store.read_lease(st.connection, RUN_ID) is None
        assert store.read_lease(st.connection, "run-never-leased") is None
        assert st.connection.in_transaction is False
    finally:
        st.close()

    with pytest.raises(dataclasses.FrozenInstanceError):
        lease.token = "t2"  # type: ignore[misc]


def test_reacquiring_a_lease_replaces_the_old_token_and_other_runs_are_untouched(repo):
    # Review Focus 3: a resumed run takes a new token; the old one is dead, and
    # a token string shared with another run never reaches that run's row.
    other = store.Store.open(repo, OTHER_RUN_ID)
    st = store.Store.open(repo, RUN_ID)
    try:
        theirs = other.acquire_lease(token="new", pid=7, host="h", now=_at(0))
        st.acquire_lease(token="old", pid=1, host="h", now=_at(0))
        st.close_window("old")
        fresh = st.acquire_lease(token="new", pid=2, host="h", now=_at(5))

        st.beat("old", _at(9))
        st.close_window("old")
        st.release_lease("old")
        assert store.read_lease(st.connection, RUN_ID) == fresh
        assert fresh.accepting is True
        assert (
            st.connection.execute(
                "SELECT COUNT(*) FROM run_leases WHERE run_id = ?", (RUN_ID,)
            ).fetchone()[0]
            == 1
        )

        st.release_lease("new")
        assert store.read_lease(st.connection, RUN_ID) is None
        assert store.read_lease(st.connection, OTHER_RUN_ID) == theirs
    finally:
        st.close()
        other.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "lease" -v`
Expected: both FAIL with `AttributeError: 'Store' object has no attribute 'acquire_lease'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/store.py`, insert directly after `_checkpoint_from_row` (after line 536, before `class Store:`):

```python
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
```

Then insert a new section in `class Store`, directly after `latest_open_checkpoint` (after line 944, before `# -- rebuild ----`):

```python
    # -- leases --------------------------------------------------------------
    #
    # A row-only table outside the journal (live control C2): nothing here
    # calls `self._journal`, and `rebuild_from_journal` leaves the rows alone.
    # Every method but `acquire_lease` touches only the row whose token
    # matches; any other token is a silent no-op.

    def acquire_lease(
        self, *, token: str, pid: int, host: str, now: datetime
    ) -> LeaseRow:
        """Claim this run under `token`, replacing any earlier claim, window open."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, 1)"
                " ON CONFLICT(run_id) DO UPDATE SET"
                " token=excluded.token, pid=excluded.pid, host=excluded.host,"
                " acquired_at=excluded.acquired_at,"
                " heartbeat_at=excluded.heartbeat_at, accepting=1",
                (self.run_id, token, pid, host, _iso(now), _iso(now)),
            )
            self._conn.commit()
            return LeaseRow(
                run_id=self.run_id,
                token=token,
                pid=pid,
                host=host,
                acquired_at=now,
                heartbeat_at=now,
                accepting=True,
            )

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k "lease" -v`
Expected: PASS. Then `uv run pytest` — all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): run leases keyed by token"
```

---

### Task 4: Control requests (`ControlRow`, `control_requests`, `immediate`, `add_control`, `pending_controls`, `mark_control_handled`)

**Files:**
- Modify: `src/agent_manager/store.py:14-22` (imports); insert after `read_lease` (Task 3); add two methods at the end of the `# -- leases` section of `Store` (Task 3)
- Test: `tests/test_store.py` (append)

**Interfaces:**
- Consumes: the `run_controls` table (Task 2); the `# -- leases` section of `Store` (Task 3) as the insertion point.
- Produces:
  - `@dataclass(frozen=True) class ControlRow: run_id: str; seq: int; lease: str; command: str; requested_at: datetime; handled_at: datetime | None`
  - `control_requests(conn: sqlite3.Connection, run_id: str, *, lease: str | None = None) -> list[ControlRow]` (seq order; every lease when `lease is None`)
  - `@contextmanager immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]`
  - `add_control(conn: sqlite3.Connection, run_id: str, *, lease: str, command: str, requested_at: datetime) -> ControlRow` (no commit)
  - `Store.pending_controls(self, token: str) -> list[ControlRow]`
  - `Store.mark_control_handled(self, seq: int, now: datetime) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_a_request_from_another_connection_is_pending_for_its_lease_only(repo):
    st = store.Store.open(repo, RUN_ID)
    other = store.open_db(repo)
    try:
        with store.immediate(other):
            first = store.add_control(
                other, RUN_ID, lease="t1", command="pause", requested_at=_at(0)
            )
            second = store.add_control(
                other, RUN_ID, lease="old", command="cancel", requested_at=_at(0)
            )
        assert first == store.ControlRow(
            run_id=RUN_ID,
            seq=0,
            lease="t1",
            command="pause",
            requested_at=_at(0),
            handled_at=None,
        )
        assert second.seq == 1

        assert [row.command for row in st.pending_controls("t1")] == ["pause"]
        assert st.pending_controls("t1") == [first]

        st.mark_control_handled(0, _at(1))
        assert st.pending_controls("t1") == []
        assert [row.handled_at for row in store.control_requests(other, RUN_ID)] == [
            _at(1),
            None,
        ]
        assert [row.seq for row in store.control_requests(other, RUN_ID, lease="old")] == [1]
        assert store.control_requests(other, "run-never-controlled") == []
    finally:
        other.close()
        st.close()


def test_control_seqs_are_numbered_and_handled_per_run(repo):
    # Review Focus 4.
    conn = store.open_db(repo)
    try:
        with store.immediate(conn):
            a = store.add_control(conn, RUN_ID, lease="t1", command="pause", requested_at=_at(0))
            b = store.add_control(
                conn, OTHER_RUN_ID, lease="t9", command="cancel", requested_at=_at(0)
            )
            c = store.add_control(conn, RUN_ID, lease="t1", command="cancel", requested_at=_at(1))
        assert (a.seq, b.seq, c.seq) == (0, 0, 1)

        st = store.Store.open(repo, RUN_ID)
        try:
            st.mark_control_handled(0, _at(2))
        finally:
            st.close()

        assert [row.handled_at for row in store.control_requests(conn, RUN_ID)] == [_at(2), None]
        assert [row.handled_at for row in store.control_requests(conn, OTHER_RUN_ID)] == [None]
    finally:
        conn.close()


def test_immediate_rolls_back_on_error(repo):
    conn = store.open_db(repo)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with store.immediate(conn):
                store.add_control(conn, RUN_ID, lease="t1", command="pause", requested_at=_at(0))
                raise RuntimeError("boom")

        assert conn.in_transaction is False
        assert store.control_requests(conn, RUN_ID) == []
        # Nothing was spent: the next request is still seq 0.
        with store.immediate(conn):
            again = store.add_control(
                conn, RUN_ID, lease="t1", command="pause", requested_at=_at(1)
            )
        assert again.seq == 0
        assert len(store.control_requests(conn, RUN_ID)) == 1
    finally:
        conn.close()


def test_an_unknown_command_is_refused_by_the_check(repo):
    conn = store.open_db(repo)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with store.immediate(conn):
                store.add_control(
                    conn, RUN_ID, lease="t1", command="resume", requested_at=_at(0)
                )
        assert conn.in_transaction is False
        assert store.control_requests(conn, RUN_ID) == []
    finally:
        conn.close()


def test_immediate_commits_an_implicit_transaction_first(repo):
    # Review Focus 1: Python's legacy sqlite3 mode opens an implicit
    # transaction on the first INSERT; `immediate` must not trip over it.
    conn = store.open_db(repo)
    try:
        conn.execute(
            "INSERT INTO runs (id, workflow, repo_dir, base_branch, branch_prefix,"
            " status, started_at, config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (RUN_ID, "milestone", str(repo), "main", "m9/", "started", None, "{}"),
        )
        assert conn.in_transaction is True
        with store.immediate(conn):
            store.add_control(conn, RUN_ID, lease="t1", command="pause", requested_at=_at(0))
        assert conn.in_transaction is False
    finally:
        conn.close()

    reader = store.open_db(repo)
    try:
        assert reader.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert [row.command for row in store.control_requests(reader, RUN_ID)] == ["pause"]
    finally:
        reader.close()


def test_immediate_holds_the_write_lock_from_begin(repo):
    # Review Focus 2: BEGIN IMMEDIATE, not a deferred BEGIN, so no second
    # writer can land between reading MAX(seq) and the insert.
    conn = store.open_db(repo)
    blocker = sqlite3.connect(paths.project_db_path(repo), timeout=0)
    try:
        with store.immediate(conn):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                blocker.execute("BEGIN IMMEDIATE")
        blocker.execute("BEGIN IMMEDIATE")
        blocker.rollback()
    finally:
        blocker.close()
        conn.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "request_from_another or seqs_are_numbered or immediate or unknown_command" -v`
Expected: all six FAIL with `AttributeError: module 'agent_manager.store' has no attribute 'immediate'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/store.py`, change the imports (lines 14-22) to:

```python
import json
import os
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
```

Insert directly after `read_lease` (added in Task 3), still before `class Store:`:

```python
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
```

Then append these two methods to the end of the `# -- leases` section of `Store` (after `release_lease`, before `# -- rebuild ----`):

```python
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
```

Also rename that section's header comment from `# -- leases ---` to `# -- leases and control requests ---` so it covers both.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k "request_from_another or seqs_are_numbered or immediate or unknown_command" -v`
Expected: all six PASS. Then `uv run pytest` — all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): control requests written under BEGIN IMMEDIATE"
```

---

### Task 5: A cancelled run's checkpoints are never open

**Files:**
- Modify: `src/agent_manager/store.py:922-944` (`Store.latest_open_checkpoint`)
- Test: `tests/test_store.py` (append)

**Interfaces:**
- Consumes: `models.Status` including `"cancelled"` (Task 1); existing test helpers `_run`, `_save_checkpoint`, `_at`.
- Produces: `Store.latest_open_checkpoint(card_id: str, workflow: str) -> Checkpoint | None` with the cancelled-run rule; test helpers `_run_with_status(repo, run_id, status) -> models.Run` and `_checkpoint_in_run(repo, run_id, status, card_id, *, reason, saved_at, workflow="task") -> store.Checkpoint` (Task 6 uses `_run_with_status`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def _run_with_status(repo: Path, run_id: str, status: str) -> models.Run:
    return models.Run.model_validate({**_run(repo, run_id).model_dump(), "status": status})


def _checkpoint_in_run(
    repo: Path,
    run_id: str,
    status: str,
    card_id: str,
    *,
    reason: str,
    saved_at: datetime,
    workflow: str = "task",
) -> store.Checkpoint:
    """Record `run_id` with `status`, then save one checkpoint of `card_id` under it."""
    st = store.Store.open(repo, run_id)
    try:
        st.record_run(_run_with_status(repo, run_id, status))
        return _save_checkpoint(st, card_id, reason=reason, workflow=workflow, saved_at=saved_at)
    finally:
        st.close()


def test_a_cancelled_runs_checkpoints_are_never_open(repo):
    # Review Focus 4 of the milestone plan.
    _checkpoint_in_run(repo, "run-r1", "stopped", "c1", reason="parked", saved_at=_at(0))
    _checkpoint_in_run(repo, "run-r2", "cancelled", "c1", reason="parked", saved_at=_at(1))
    unrelated = _checkpoint_in_run(
        repo, "run-r3", "stopped", "c2", reason="parked", saved_at=_at(0)
    )

    st = store.Store.open(repo, "run-r4")
    try:
        closed = st.latest_open_checkpoint("c1", "task")
        found = st.latest_open_checkpoint("c2", "task")
    finally:
        st.close()

    assert closed is None
    assert found == unrelated
    assert found is not None and found.run_id == "run-r3"


def test_latest_open_checkpoint_skips_a_cancelled_runs_row_when_it_is_not_newest(repo):
    # Review Focus 5: the newest row is open (another workflow, a live run), so
    # the card is not closed; the cancelled run's parked row is still skipped.
    older = _checkpoint_in_run(repo, "run-r1", "stopped", "c3", reason="turn", saved_at=_at(0))
    _checkpoint_in_run(repo, "run-r2", "cancelled", "c3", reason="parked", saved_at=_at(1))
    _checkpoint_in_run(
        repo, "run-r3", "stopped", "c3", reason="turn", workflow="integrate", saved_at=_at(2)
    )

    st = store.Store.open(repo, "run-r4")
    try:
        found = st.latest_open_checkpoint("c3", "task")
    finally:
        st.close()

    assert found == older
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "cancelled_runs" -v`
Expected: both FAIL — the first with `assert Checkpoint(run_id='run-r2', ...) is None`, the second with the `run-r2` parked row returned instead of `older`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/store.py`, replace `latest_open_checkpoint` (lines 922-944) with:

```python
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
                "SELECT * FROM checkpoints WHERE card_id = ? AND workflow = ?"
                " AND reason IN ('turn', 'parked', 'escalated')"
                " AND run_id NOT IN (SELECT id FROM runs WHERE status = 'cancelled')"
                " ORDER BY saved_at DESC, seq DESC LIMIT 1",
                (card_id, workflow),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k "latest_open_checkpoint or cancelled_runs" -v`
Expected: all PASS (the existing `latest_open_checkpoint` tests, which record no `runs` rows, still pass through the `LEFT JOIN`). Then `uv run pytest` — all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): a cancelled run's checkpoints are never open"
```

---

### Task 6: `run_status`, and a cancelled run round-trips the journal with controls and leases left alone

**Files:**
- Modify: `src/agent_manager/store.py` — add `run_status` directly after `load_run` (ends line 503)
- Test: `tests/test_store.py` (append)

**Interfaces:**
- Consumes: `LeaseRow`/`read_lease`/`Store.acquire_lease` (Task 3); `immediate`/`add_control`/`control_requests` (Task 4); test helper `_run_with_status` (Task 5); existing `_truncate_db`, `store.list_runs`.
- Produces: `run_status(conn: sqlite3.Connection, run_id: str) -> str | None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_store.py`:

```python
def test_a_cancelled_run_round_trips_through_the_journal_and_the_listing(repo):
    st = store.Store.open(repo, RUN_ID)
    other = store.open_db(repo)
    try:
        st.record_run(_run_with_status(repo, RUN_ID, "cancelled"))
        lease = st.acquire_lease(token="t1", pid=42, host="h", now=_at(0))
        with store.immediate(other):
            store.add_control(other, RUN_ID, lease="t1", command="cancel", requested_at=_at(1))
        controls = store.control_requests(other, RUN_ID)
        journal_before = [line.event for line in st.journal.read()]

        rebuilt = st.rebuild_from_journal(RUN_ID)

        # Nothing journals the control tables, and the rebuild leaves them alone.
        assert [line.event for line in st.journal.read()] == journal_before
        assert journal_before == ["run_upsert"]
        assert store.read_lease(st.connection, RUN_ID) == lease
        assert store.control_requests(st.connection, RUN_ID) == controls
        assert rebuilt.status == "cancelled"
        assert store.run_status(st.connection, RUN_ID) == "cancelled"
        assert store.run_status(st.connection, "run-never-recorded") is None
    finally:
        other.close()
        st.close()

    # From the journal alone: a wiped projection replays `cancelled`.
    _truncate_db(repo)
    replayed = store.Store.open(repo, RUN_ID)
    try:
        replayed.rebuild_from_journal(RUN_ID)
        summaries = store.list_runs(replayed.connection)
        status = store.run_status(replayed.connection, RUN_ID)
    finally:
        replayed.close()

    assert [(summary.id, summary.status) for summary in summaries] == [(RUN_ID, "cancelled")]
    assert status == "cancelled"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_a_cancelled_run_round_trips_through_the_journal_and_the_listing -v`
Expected: FAIL with `AttributeError: module 'agent_manager.store' has no attribute 'run_status'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/store.py`, insert directly after `load_run` (after line 503, before `@dataclass(frozen=True) class Checkpoint`):

```python
def run_status(conn: sqlite3.Connection, run_id: str) -> str | None:
    """`runs.status` of `run_id`, or `None` if the run was never recorded.

    A free function over a connection, like `load_run`, for a reader in
    another process that needs the status alone (`am pause`, `am resume`).
    """
    row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    return None if row is None else row["status"]
```

Also extend the `Store` class docstring's journal-exception sentence (lines 544-546) to name the new tables:

```python
    There is deliberately no public method that writes a tree row on its own.
    The exceptions are `checkpoints` (pygents spec §6), `run_controls` and
    `run_leases` (live control C1/C2): row-only tables outside the journal.
    Their methods write rows and never touch the journal, and
    `rebuild_from_journal` leaves those rows alone.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: all PASS.

Then the full verification: `uv run pytest`
Expected: the whole suite PASSES, `tests/e2e` included.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): control requests, run leases and the cancelled status"
```

---

## Self-review record

- **Spec coverage:** Deliverable 1 → Task 1. 2 → Task 2. 3 → Tasks 3 (`LeaseRow`) and 4 (`ControlRow`). 4 → `read_lease` Task 3; `control_requests`/`immediate`/`add_control` Task 4; `run_status` Task 6. 5 → lease methods Task 3; `pending_controls`/`mark_control_handled` Task 4. 6 → Task 5. 7 → Task 6 (no journal lines, rows kept across `rebuild_from_journal`, `cancelled` replayed and listed); `_delete_run` is not modified in any task. Error paths: IntegrityError (Task 4), rollback (Task 4), wrong token no-op (Task 3), `None` readers (Tasks 3 and 6). The spec's eight tests all exist under the spec's names; extra Review Focus tests added.
- **Placeholder scan:** every code step has complete code; no TBD/"similar to".
- **Type consistency:** `LeaseRow`, `ControlRow`, `read_lease`, `control_requests(conn, run_id, *, lease=None)`, `immediate(conn)`, `add_control(conn, run_id, *, lease, command, requested_at)`, `run_status(conn, run_id)`, `acquire_lease(*, token, pid, host, now)`, `beat(token, now)`, `close_window(token)`, `release_lease(token)`, `pending_controls(token)`, `mark_control_handled(seq, now)` are spelled identically in every task and match sibling Task 1.2's consumption list.
- **Existing-test impact:** `tests/test_models.py::test_status_grew_by_exactly_stopped_and_still_rejects_unknown_values` asserts the exact `Status` tuple and is replaced in Task 1; no other test asserts the exact table set or the exact `Status` tuple.
