<!-- task-pipeline: validated -->
# Take the lease atomically with claims, and fence every run write (a7ed6c11)

Subtask of story f67dc4e4 ("Leases that own runs, cards and branches"), milestone 619c2a8e. This is Task 2.1 of `docs/superpowers/plans/2026-09-27-multi-process.md`, governed by `docs/superpowers/specs/2026-09-27-multi-process-design.md` (X4 fencing, X5 claims, X9 `BEGIN IMMEDIATE`). Branch prefix `m10`, base `master`. M9 is already merged, and a code read found no drift from its design.

Note on inputs: the exploration summary given to this stage was truncated at 8000 of 11924 characters, partway through the test list. That means the upstream stage over-ran its brief. I recovered the missing test names and the test-tier rule directly from the plan (Task 2.1, lines 213-314) and the design (§7 "Store"). I did not guess at the rest of the lost text.

## Scope

Only `src/agent_manager/store.py` changes, and its tests go in `tests/test_store.py`. The one possible exception is described under "Open point". Out of scope:
- `control.Lease.__enter__` switching to `take_lease` with real liveness and claims belongs to sibling ec7ae954.
- Adding `LeaseLostError` to `cli.HANDLED` belongs to another subtask.
- Card/branch claim keys for `--card` runs belong to Task 2.2.

`store.py` must not import `control`. Liveness is injected as the `is_live: Callable[[LeaseRow], bool]` argument. This subtask reuses M9's `run_leases`, `LeaseRow`, `_lease_from_row`, `read_lease`, `immediate` and `beat`/`close_window`/`release_lease` without changing them. It adds no new staleness rule and no pid files.

## Observable behaviour

**Schema.** `_SCHEMA` gains `CREATE TABLE IF NOT EXISTS run_claims (key TEXT PRIMARY KEY, run_id TEXT NOT NULL, token TEXT NOT NULL, claimed_at TEXT NOT NULL)`, placed after `run_leases`. It holds rows only and never appears in the journal. No existing table gains a column, and no CHECK constraint changes. The `BUSY_TIMEOUT_SECONDS` docstring stops calling two writers "unsupported (P2)".

**Types.**
- `ClaimRow(key, run_id, token, claimed_at: datetime)` is a frozen dataclass.
- `LeaseTake(lease: LeaseRow, displaced: LeaseRow | None)` is a frozen dataclass.
- `LeaseHeldError(RuntimeError)` carries `.holder: LeaseRow`.
- `ClaimHeldError(RuntimeError)` carries `.key: str` and `.holder: LeaseRow`.
- `LeaseLostError(BaseException)` carries `.run_id: str` and `.holder: LeaseRow | None`. It deliberately does not derive from `Exception`, so no `except Exception` can swallow it.

**Free functions.**
- `claim_conflicts(conn, keys, *, is_live, run_id=None) -> list[tuple[str, LeaseRow]]` is read-only. A key conflicts only when all three of these hold:
  - its `run_claims` row exists and has a `run_id` different from the argument;
  - that run's `run_leases` row carries the claim's token;
  - `is_live(lease_row)` is true.
- `held_claims(conn, run_id, token) -> list[ClaimRow]` returns rows in key order.

**`Store.take_lease(*, token, pid, host, now, is_live, claims=()) -> LeaseTake`** runs under `self._lock` inside one `immediate(self._conn)` transaction, in this order:
1. **Check the current lease.** If this run's lease row has another token and `is_live` is true for it, raise `LeaseHeldError(row)`. Otherwise that row, or `None`, becomes `displaced`.
2. **Check claims.** Call `claim_conflicts(conn, claims, is_live=is_live, run_id=self.run_id)`. If there is a conflict, the first one raises `ClaimHeldError(key, holder)`.
3. **Write and commit.** Upsert the lease with `accepting=1` and `acquired_at=heartbeat_at=now`. Upsert every claim with this `run_id`, `token` and `claimed_at=now`. Commit.

After the commit, `take_lease` calls `bind_lease(token)` and then `self._journal.reseek()`. The outcome is all-or-nothing. On any raise it writes no lease row, no claim row and no journal line, and the bound token stays unchanged. A key already recorded under this run's own id is rewritten under the new token (the resume case). A claim held under a dead lease is overwritten.

**Other `Store` / `Journal` additions.**
- `Store.bind_lease(token: str | None)` sets or clears the fencing token.
- `Store.release_claims(token)` deletes only the rows carrying that token.
- `Journal.reseek()` re-reads the highest `seq` under the journal lock. This lets the new owner continue the sequence after lines a stuck previous owner appended once `Journal.__init__` had already cached `_seq`.
- `Store.acquire_lease` is removed. The comment at store.py:1137 that mentions it gets updated.

**Fencing (X4, X9).**
- **Which writes are fenced.** With a token bound, each of these runs its whole body inside `with self._lock, self._fenced():`: `record_run`, `record_story`, `record_subtask`, `record_phase`, `record_attempt`, `save_checkpoint` and `rebuild_from_journal`.
- **What `_fenced()` does.** It opens `immediate`, reads this run's `run_leases` row and raises `LeaseLostError(self.run_id, holder_or_None)` if the row is missing or has a different token. Otherwise it sets `self._in_fence` for the duration of the body and resets it in a `finally`.
- **Commits.** Every `_write_*_row`, and `_delete_run` (which currently calls `self._conn.commit()` at store.py:1261), commits through `self._commit()`. `self._commit()` does nothing inside a fence, so the journal append and the row write commit atomically with the fence check. Outside a fence it commits normally.
- **Journal order.** The journal line is appended inside the fence, before the row. A store that has lost its lease appends nothing.
- **Unbound stores.** With no token bound, `_fenced()` is a no-op and every write behaves exactly as it does today.

## Error paths

| Condition | Result |
|---|---|
| A live foreign lease exists | `LeaseHeldError`; nothing written |
| Any claim key is held by another run under a live lease | `ClaimHeldError`, naming the first conflicting key; the lease and every other claim are rolled back |
| A write from a bound store whose lease was taken over or deleted | `LeaseLostError`, raised before anything is appended or written; the journal file is byte-identical |
| The database is locked for longer than the busy timeout | `sqlite3.OperationalError` propagates, unhandled, as today |

## Open point: `control.Lease` still calls `acquire_lease`

`control.Lease.__enter__` (src/agent_manager/control.py:108) still calls `self._store.acquire_lease(...)`. The plan removes `acquire_lease` in this task and changes `control.py` only in sibling ec7ae954. But this task must also leave `uv run pytest` green on its own. Removing the method without touching `control.py` would break every `Lease` user.

The narrowest fix that satisfies both constraints is to change that single call in this subtask to `take_lease(token=..., pid=..., host=..., now=..., is_live=lambda row: False)` and use its `.lease`. This keeps M9's unconditional-replace semantics. The call would now bind the token, which fences that store's writes to the lease's own token, and those writes still succeed. ec7ae954 then replaces the call with real liveness and claims.

The planning stage must confirm that this edit is acceptable and that the full suite stays green under the binding. If it is not acceptable, keep `acquire_lease` temporarily and leave its removal to ec7ae954.

## Tests (all in `tests/test_store.py`)

**Tier.** Every test below is in the **Steps** tier of the base design §14 (`2026-09-23-agent-manager-design.md:505-520`). They run against a real temporary SQLite database and journal, with no network and no harness dispatch. They are not engine-parametrised. Multi-process design §7 adds one requirement: a "second process" in store tests is a real `subprocess.Popen([sys.executable, "-c", ...])` child, and ordering is proven with pipes, never with sleeps.

- `test_take_lease_refuses_a_live_foreign_lease`: t1 holds a live lease, so t2 gets `LeaseHeldError` and `.holder.token == "t1"`.
- `test_take_lease_takes_over_a_dead_lease`: the result has `displaced.token == "t1"` and `lease.token == "t2"`.
- `test_claims_are_all_or_nothing`: `ClaimHeldError.key == "card:x"`. Afterwards run-b has no lease row, and `held_claims` for run-b is empty, because `card:y` was rolled back too.
- `test_a_claim_under_a_dead_lease_is_overwritten`.
- `test_a_resume_rewrites_its_own_runs_claims`: same run id; after t1 dies, t2 owns the keys.
- `test_claim_conflicts_is_read_only_and_ignores_the_runs_own`.
- A `release_claims` test that checks it deletes only its own token's rows. The design §7 requires this behaviour, but the plan gives no test name for it.
- `test_a_taken_over_store_writes_nothing`: `record_run`, `record_subtask` and `save_checkpoint` each raise `LeaseLostError`, and the journal bytes are unchanged.
- `test_the_new_owner_continues_the_sequence`: b's first write after taking over gets `seq == 2`, because `reseek` picks up line 1, which a wrote after b opened its store.
- `test_an_unbound_store_writes_as_before`.
- `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner`:
  - It is parametrised with `attempt` in `range(20)`.
  - Two real child processes use a ready/go rendezvous over pipes, with `is_live=lambda row: row.token != "t0"`.
  - The sorted outcomes are `["LeaseHeldError", "took"]`.
- The three existing tests that call `acquire_lease` switch to `take_lease(..., is_live=lambda row: False)`, with their assertions otherwise unchanged: `test_a_lease_is_touched_only_through_its_own_token` (line 2121), `test_reacquiring_a_lease_replaces_the_old_token_and_other_runs_are_untouched` (line 2160), and the `acquire_lease` call inside `test_a_cancelled_run_round_trips_through_the_journal_and_the_listing` (line 2394). No other test in the file calls `acquire_lease`; in particular the tests in the 2191-2351 range (`test_a_request_from_another_connection_is_pending_for_its_lease_only`, `test_control_seqs_are_numbered_and_handled_per_run`, the `test_immediate_*` tests) use `lease="t1"` only as a `run_controls` column value and are unrelated to `acquire_lease`/`take_lease`.

Verification: `uv run pytest` passes for the whole suite, `tests/e2e` included.

---

# Take the lease atomically with claims, and fence every run write — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A run's lease and its claim keys are taken in one `BEGIN IMMEDIATE` transaction, and once a `Store` holds a lease token every journalled write and checkpoint is fenced by that token, so a store whose lease was taken over appends and writes nothing.

**Architecture:** Everything lives in `src/agent_manager/store.py`: a new row-only `run_claims` table, five new types, two read-only free functions, and on `Store` a `take_lease` that replaces `acquire_lease`, plus `bind_lease`/`release_claims`, a `_fenced()` context manager and a `_commit()` that the row writers call instead of `self._conn.commit()`. `Journal.reseek()` lets a new owner continue the `seq` numbering. `control.Lease` gets the smallest edit that keeps the suite green without `acquire_lease` (resolution of the spec's open point, below).

**Tech Stack:** Python 3, stdlib `sqlite3` (legacy transaction mode, WAL), `dataclasses`, pytest, `subprocess` for the two-process race test. Run everything with `uv run pytest`.

**Spec:** `docs/superpowers/specs/task-take-the-lease-a7ed6c11-design.md` (prepended above). Parent plan: `docs/superpowers/plans/2026-09-27-multi-process.md` Task 2.1 (readable from `/home/paulomtts/Code/agent-manager/.claude/worktrees/docs-multi-process/`).

**Input note:** the exploration summary handed to this planning stage was itself truncated at 8000 of 11924 characters, mid test list. That truncation is evidence the upstream stage over-ran its brief. This plan does not rely on the lost text: it works from the corrected spec on disk, the parent plan's Task 2.1 (read directly), and the code as it stands on this branch.

**Line-number note:** the exploration findings quoted `control.py:112` and some `store.py` lines; on this branch (cut from `m10/task-put-board-writes-and-43043f10`) the call is at `src/agent_manager/control.py:108` and the store lines are as cited in each task below. Every reference in this plan was re-read on this branch.

## Resolution of the spec's open point

Confirmed: this subtask edits `control.Lease` in two small places, because removing `acquire_lease` otherwise breaks every `Lease` user (`cli.py:875`, `cli.py:1478`, `orchestrate.py:1423`, `tests/test_control.py`).

1. Task 2: `Lease.__enter__` calls `self._store.take_lease(..., is_live=lambda row: False)` — M9's unconditional replace, now binding the token.
2. Task 3: `Lease.__exit__` calls `self._store.bind_lease(None)` after `release_lease`, in a `finally`. Reason: once fencing exists, a store left bound to a token whose row `release_lease` just deleted would raise `LeaseLostError` on any later write. Every production write today sits inside the `with control.Lease(...)` block (checked: `cli.py:875-910`, `cli.py:1478-1512`, `orchestrate.py:1423-1528`), so nothing breaks either way, but unbinding keeps M9's "after the block, the store writes unfenced" contract exactly and costs one line. ec7ae954 owns `Lease` from then on and may revisit it.

## Global Constraints

- `store.py` must not import `control`; liveness is injected as `is_live: Callable[[LeaseRow], bool]`.
- `run_claims (key TEXT PRIMARY KEY, run_id TEXT NOT NULL, token TEXT NOT NULL, claimed_at TEXT NOT NULL)` is row-only, outside the journal; no existing table gains a column; no CHECK changes.
- Reuse M9's `run_leases`, `LeaseRow`, `_lease_from_row`, `read_lease`, `immediate`, `beat`, `close_window`, `release_lease` unchanged. No new staleness rule, no pid files.
- Every cross-process check-and-set uses `BEGIN IMMEDIATE` via `store.immediate`.
- `LeaseLostError` derives from `BaseException`, not `Exception`.
- Inside a fence the journal line is appended before the row, and `_write_*_row`/`_delete_run` never commit on their own.
- Tests are Steps tier (base design §14): real temp SQLite DB and journal, no network, no harness, in `tests/test_store.py` (and `tests/test_control.py` for the one `control.Lease` test); a "second process" in the race test is a real `subprocess.Popen([sys.executable, "-c", ...])`, ordered by pipes, never by sleeps.
- Every task leaves `uv run pytest` green on its own, `tests/e2e` included.
- Branch `m10/task-take-the-lease-a7ed6c11`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-take-the-lease-a7ed6c11`. No other subtask's code is assumed present.

## Review Focus

1. A `take_lease` that raises (`LeaseHeldError` or `ClaimHeldError`) must leave the store unbound, so its later writes are not fenced to a token it never got — pinned in Task 3's `test_an_unbound_store_writes_as_before`.
2. A bound store whose lease row was deleted (not replaced) gets `LeaseLostError` with `holder is None`, and still appends nothing — pinned at the end of Task 3's `test_a_taken_over_store_writes_nothing`.
3. `rebuild_from_journal` on a taken-over store must not delete or rewrite the new owner's projection — pinned in Task 3's `test_a_taken_over_store_writes_nothing` (rebuild is in its write list, then the projection is checked).
4. A refused write inside a fence (an unknown checkpoint `reason` raising `sqlite3.IntegrityError`) leaves no open transaction, spends no `seq`, and the next fenced write still commits — pinned in Task 3's `test_a_bound_store_commits_each_write_inside_its_fence`.
5. After `control.Lease` exits, its store writes as M9 did; while it is held, a takeover by another store fences it — pinned in Task 3's `test_a_lease_fences_its_store_only_while_it_is_held` in `tests/test_control.py`.

---

### Task 1: The `run_claims` table, the new types, `claim_conflicts` and `held_claims`

**Files:**
- Modify: `src/agent_manager/store.py:18` (imports), `src/agent_manager/store.py:118-127` (`_SCHEMA`), insert after `src/agent_manager/store.py:609` (after `read_lease`, before `ControlRow`)
- Test: `tests/test_store.py` (append a new section at the end of the file, after line 2425)

**Interfaces:**
- Consumes: `LeaseRow`, `read_lease(conn, run_id) -> LeaseRow | None`, `immediate(conn)`, `open_db(root)` (all existing).
- Produces:
  - `@dataclass(frozen=True) class ClaimRow: key: str; run_id: str; token: str; claimed_at: datetime`
  - `@dataclass(frozen=True) class LeaseTake: lease: LeaseRow; displaced: LeaseRow | None`
  - `class LeaseHeldError(RuntimeError)`, `__init__(self, holder: LeaseRow)`, attribute `.holder`
  - `class ClaimHeldError(RuntimeError)`, `__init__(self, key: str, holder: LeaseRow)`, attributes `.key`, `.holder`
  - `class LeaseLostError(BaseException)`, `__init__(self, run_id: str, holder: LeaseRow | None)`, attributes `.run_id`, `.holder`
  - `claim_conflicts(conn: sqlite3.Connection, keys: Iterable[str], *, is_live: Callable[[LeaseRow], bool], run_id: str | None = None) -> list[tuple[str, LeaseRow]]` (in `keys` order; `is_live` is only called for a claim whose run's lease row carries the claim's token)
  - `held_claims(conn: sqlite3.Connection, run_id: str, token: str) -> list[ClaimRow]` (ordered by `key`)
  - Test helpers in `tests/test_store.py`: `_alive(row) -> True`, `_dead(row) -> False`, `_plant_lease(repo, run_id, *, token) -> store.LeaseRow`, `_plant_claim(repo, key, *, run_id, token) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python
# -- run claims, lease takeover and fencing ----------------------------------------
#
# Multi-process design X4/X5/X9. Steps tier: real temp DB and journal, no
# harness. A "second process" is a second `Store`/connection, except in the
# two-process race test, which uses real child processes ordered by pipes.


def _alive(row: store.LeaseRow) -> bool:
    return True


def _dead(row: store.LeaseRow) -> bool:
    return False


def _plant_lease(repo: Path, run_id: str, *, token: str) -> store.LeaseRow:
    """A `run_leases` row, as another process's `take_lease` would have left it."""
    conn = store.open_db(repo)
    try:
        with store.immediate(conn):
            conn.execute(
                "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                " heartbeat_at, accepting) VALUES (?, ?, 1, 'h', ?, ?, 1)"
                " ON CONFLICT(run_id) DO UPDATE SET token = excluded.token",
                (run_id, token, _at(0).isoformat(), _at(0).isoformat()),
            )
        row = store.read_lease(conn, run_id)
    finally:
        conn.close()
    assert row is not None
    return row


def _plant_claim(repo: Path, key: str, *, run_id: str, token: str) -> None:
    """A `run_claims` row, as another process's `take_lease` would have left it."""
    conn = store.open_db(repo)
    try:
        with store.immediate(conn):
            conn.execute(
                "INSERT INTO run_claims (key, run_id, token, claimed_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                " run_id = excluded.run_id, token = excluded.token",
                (key, run_id, token, _at(0).isoformat()),
            )
    finally:
        conn.close()


def test_the_claims_table_appears_on_an_existing_database(repo):
    # A pre-M10 database: everything but `run_claims`.
    first = store.open_db(repo)
    first.execute("DROP TABLE IF EXISTS run_claims")
    first.commit()
    first.close()

    conn = store.open_db(repo)
    try:
        claims = [
            row["name"] for row in conn.execute("PRAGMA table_info(run_claims)").fetchall()
        ]
        leases = [
            row["name"] for row in conn.execute("PRAGMA table_info(run_leases)").fetchall()
        ]
    finally:
        conn.close()

    assert claims == ["key", "run_id", "token", "claimed_at"]
    # No existing table gains a column.
    assert leases == [
        "run_id",
        "token",
        "pid",
        "host",
        "acquired_at",
        "heartbeat_at",
        "accepting",
    ]


def test_lease_errors_name_their_holder_and_a_lost_lease_is_not_an_exception():
    holder = store.LeaseRow(
        run_id=RUN_ID,
        token="t1",
        pid=42,
        host="h",
        acquired_at=_at(0),
        heartbeat_at=_at(0),
        accepting=True,
    )
    held = store.LeaseHeldError(holder)
    assert isinstance(held, RuntimeError) and held.holder == holder

    claimed = store.ClaimHeldError("card:x", holder)
    assert isinstance(claimed, RuntimeError)
    assert (claimed.key, claimed.holder) == ("card:x", holder)

    lost = store.LeaseLostError(RUN_ID, None)
    assert (lost.run_id, lost.holder) == (RUN_ID, None)
    assert isinstance(lost, BaseException) and not isinstance(lost, Exception)
    with pytest.raises(store.LeaseLostError):
        try:
            raise store.LeaseLostError(RUN_ID, holder)
        except Exception:  # must not catch it
            pytest.fail("`except Exception` swallowed LeaseLostError")

    take = store.LeaseTake(lease=holder, displaced=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        take.displaced = holder  # type: ignore[misc]


def test_claim_conflicts_is_read_only_and_ignores_the_runs_own(repo):
    holder = _plant_lease(repo, "run-a", token="ta")
    _plant_claim(repo, "card:x", run_id="run-a", token="ta")
    # run-c's lease has moved on to a new token: its old claim is dead.
    _plant_lease(repo, "run-c", token="tc-new")
    _plant_claim(repo, "card:z", run_id="run-c", token="tc-old")
    # run-d has no lease row at all.
    _plant_claim(repo, "card:w", run_id="run-d", token="td")
    keys = ["card:x", "card:z", "card:w", "card:never"]

    seen: list[store.LeaseRow] = []

    def live(row: store.LeaseRow) -> bool:
        seen.append(row)
        return True

    conn = store.open_db(repo)
    try:
        changes = conn.total_changes
        assert store.claim_conflicts(conn, keys, is_live=live, run_id="run-b") == [
            ("card:x", holder)
        ]
        # Liveness is asked only of a claim whose run's lease still carries its token.
        assert seen == [holder]
        assert store.claim_conflicts(conn, keys, is_live=_alive) == [("card:x", holder)]
        assert store.claim_conflicts(conn, keys, is_live=_alive, run_id="run-a") == []
        assert store.claim_conflicts(conn, keys, is_live=_dead, run_id="run-b") == []
        assert store.claim_conflicts(conn, [], is_live=_alive) == []
        assert conn.total_changes == changes
        assert conn.in_transaction is False
    finally:
        conn.close()


def test_held_claims_lists_one_tokens_keys_in_key_order(repo):
    _plant_claim(repo, "card:b", run_id="run-a", token="ta")
    _plant_claim(repo, "branch:m10/x", run_id="run-a", token="ta")
    _plant_claim(repo, "card:c", run_id="run-a", token="old")
    _plant_claim(repo, "card:d", run_id="run-b", token="ta")

    conn = store.open_db(repo)
    try:
        rows = store.held_claims(conn, "run-a", "ta")
        nobody = store.held_claims(conn, "run-a", "nobody")
    finally:
        conn.close()

    assert rows == [
        store.ClaimRow(key="branch:m10/x", run_id="run-a", token="ta", claimed_at=_at(0)),
        store.ClaimRow(key="card:b", run_id="run-a", token="ta", claimed_at=_at(0)),
    ]
    assert nobody == []
    with pytest.raises(dataclasses.FrozenInstanceError):
        rows[0].token = "other"  # type: ignore[misc]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "claims_table or lease_errors_name or claim_conflicts or held_claims" -v`
Expected: 4 FAIL — `test_the_claims_table_appears_on_an_existing_database` with `assert [] == ['key', 'run_id', 'token', 'claimed_at']`; `test_lease_errors_name_their_holder_and_a_lost_lease_is_not_an_exception` with `AttributeError: module 'agent_manager.store' has no attribute 'LeaseHeldError'`; `test_claim_conflicts_is_read_only_and_ignores_the_runs_own` and `test_held_claims_lists_one_tokens_keys_in_key_order` with `sqlite3.OperationalError: no such table: run_claims` from `_plant_claim`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/store.py`, change the import at line 18:

```python
from collections.abc import Callable, Iterable, Iterator
```

In `_SCHEMA`, after the `run_leases` table (after line 126, before the closing `"""` on line 127), add:

```sql

CREATE TABLE IF NOT EXISTS run_claims (
    key        TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    token      TEXT NOT NULL,
    claimed_at TEXT NOT NULL
);
```

After `read_lease` (after line 609, before `@dataclass(frozen=True) class ControlRow`), insert:

```python
@dataclass(frozen=True)
class ClaimRow:
    """One key a run's lease owns: a row of `run_claims` (multi-process X5).

    Row-only and outside the journal, like `LeaseRow`. A claim counts only
    while the `run_leases` row of `run_id` still carries `token` and is live;
    otherwise the next `Store.take_lease` naming the key overwrites it.
    """

    key: str
    run_id: str
    token: str
    claimed_at: datetime


def _claim_from_row(row: sqlite3.Row) -> ClaimRow:
    return ClaimRow(
        key=row["key"],
        run_id=row["run_id"],
        token=row["token"],
        claimed_at=datetime.fromisoformat(row["claimed_at"]),
    )


@dataclass(frozen=True)
class LeaseTake:
    """What `Store.take_lease` took, and the earlier lease row it replaced, if any."""

    lease: LeaseRow
    displaced: LeaseRow | None


class LeaseHeldError(RuntimeError):
    """Another process holds this run's lease and it is live (multi-process X5)."""

    def __init__(self, holder: LeaseRow) -> None:
        super().__init__(
            f"run {holder.run_id!r} is held by a live lease"
            f" (pid {holder.pid} on {holder.host})"
        )
        self.holder = holder


class ClaimHeldError(RuntimeError):
    """A claim key belongs to another run whose lease is live (multi-process X5)."""

    def __init__(self, key: str, holder: LeaseRow) -> None:
        super().__init__(
            f"{key!r} is claimed by run {holder.run_id!r}, whose lease is live"
            f" (pid {holder.pid} on {holder.host})"
        )
        self.key = key
        self.holder = holder


class LeaseLostError(BaseException):
    """A bound store's lease was taken over or deleted: it must write nothing (X4).

    A `BaseException`, not an `Exception`, so no `except Exception` in the
    engine can swallow it and carry on writing a run this process no longer
    owns. `holder` is the lease row now in place, or `None` if there is none.
    """

    def __init__(self, run_id: str, holder: LeaseRow | None) -> None:
        who = (
            "no process holds it now"
            if holder is None
            else f"pid {holder.pid} on {holder.host} holds it now"
        )
        super().__init__(f"this process lost the lease of run {run_id!r}: {who}")
        self.run_id = run_id
        self.holder = holder


def claim_conflicts(
    conn: sqlite3.Connection,
    keys: Iterable[str],
    *,
    is_live: Callable[[LeaseRow], bool],
    run_id: str | None = None,
) -> list[tuple[str, LeaseRow]]:
    """The keys of `keys`, in order, that another run's live lease holds.

    Read-only. A key conflicts when its `run_claims` row names a run other
    than `run_id`, that run's `run_leases` row still carries the claim's
    token, and `is_live` says that lease row is live. `is_live` is injected so
    this module never imports `control`; it is asked only about a claim whose
    token still matches its run's lease.
    """
    conflicts: list[tuple[str, LeaseRow]] = []
    for key in keys:
        claim = conn.execute(
            "SELECT run_id, token FROM run_claims WHERE key = ?", (key,)
        ).fetchone()
        if claim is None or claim["run_id"] == run_id:
            continue
        lease = read_lease(conn, claim["run_id"])
        if lease is None or lease.token != claim["token"] or not is_live(lease):
            continue
        conflicts.append((key, lease))
    return conflicts


def held_claims(conn: sqlite3.Connection, run_id: str, token: str) -> list[ClaimRow]:
    """Every claim `run_id` holds under `token`, in key order."""
    rows = conn.execute(
        "SELECT * FROM run_claims WHERE run_id = ? AND token = ? ORDER BY key",
        (run_id, token),
    ).fetchall()
    return [_claim_from_row(row) for row in rows]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k "claims_table or lease_errors_name or claim_conflicts or held_claims" -v`
Expected: 4 PASS.

Then run: `uv run pytest`
Expected: the whole suite PASSes (nothing else changed behaviour).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): add run_claims, the lease error types, claim_conflicts and held_claims"
```

---

### Task 2: `take_lease`, `bind_lease`, `release_claims`, `Journal.reseek`; remove `acquire_lease`

**Files:**
- Modify: `src/agent_manager/store.py:205-223` (`Journal` docstring and a new `reseek` after `last_seq`), `src/agent_manager/store.py:733-736` (`Store.__init__`), `src/agent_manager/store.py:1133-1163` (lease section comment; `acquire_lease` replaced by `take_lease`, `bind_lease`, `release_claims`)
- Modify: `src/agent_manager/control.py:106-113` (`Lease.__enter__`)
- Test: `tests/test_store.py:14-25` (imports), `tests/test_store.py:2124`, `tests/test_store.py:2166-2169`, `tests/test_store.py:2394` (migrate off `acquire_lease`), and append to the end of the file

**Interfaces:**
- Consumes (Task 1): `ClaimRow`, `LeaseTake`, `LeaseHeldError`, `ClaimHeldError`, `claim_conflicts`, `held_claims`; test helpers `_alive`, `_dead`, `_plant_lease`, `_plant_claim`.
- Produces:
  - `Journal.reseek(self) -> None`
  - `Store.take_lease(self, *, token: str, pid: int, host: str, now: datetime, is_live: Callable[[LeaseRow], bool], claims: Iterable[str] = ()) -> LeaseTake`
  - `Store.bind_lease(self, token: str | None) -> None` (sets `self._token`)
  - `Store.release_claims(self, token: str) -> None` (deletes this run's rows under `token` only)
  - `Store._token: str | None`, `Store._in_fence: bool` (initialised in `__init__`; used by Task 3)
  - Test fixture `stores(run_id: str = RUN_ID) -> store.Store` in `tests/test_store.py` (opens stores on `repo`, closes them all at teardown).
  - `Store.acquire_lease` no longer exists.

- [ ] **Step 1: Write the failing tests**

In `tests/test_store.py`, extend the imports (lines 14-25) to:

```python
import dataclasses
import json
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from agent_manager import models, paths, store
```

Migrate the three `acquire_lease` calls (assertions otherwise unchanged):

At line 2124 replace
```python
        lease = st.acquire_lease(token="t1", pid=42, host="h", now=_at(0))
```
with
```python
        lease = st.take_lease(
            token="t1", pid=42, host="h", now=_at(0), is_live=lambda row: False
        ).lease
```

At lines 2166-2169 replace
```python
        theirs = other.acquire_lease(token="new", pid=7, host="h", now=_at(0))
        st.acquire_lease(token="old", pid=1, host="h", now=_at(0))
        st.close_window("old")
        fresh = st.acquire_lease(token="new", pid=2, host="h", now=_at(5))
```
with
```python
        theirs = other.take_lease(
            token="new", pid=7, host="h", now=_at(0), is_live=lambda row: False
        ).lease
        st.take_lease(token="old", pid=1, host="h", now=_at(0), is_live=lambda row: False)
        st.close_window("old")
        fresh = st.take_lease(
            token="new", pid=2, host="h", now=_at(5), is_live=lambda row: False
        ).lease
```

At line 2394 replace
```python
        lease = st.acquire_lease(token="t1", pid=42, host="h", now=_at(0))
```
with
```python
        lease = st.take_lease(
            token="t1", pid=42, host="h", now=_at(0), is_live=lambda row: False
        ).lease
```

Append to the end of `tests/test_store.py`:

```python
@pytest.fixture
def stores(repo) -> Iterator[Callable[..., store.Store]]:
    """Open any number of `Store`s on `repo`, each on its own connection; close them all."""
    opened: list[store.Store] = []

    def open_store(run_id: str = RUN_ID) -> store.Store:
        st = store.Store.open(repo, run_id)
        opened.append(st)
        return st

    yield open_store
    for st in opened:
        st.close()


def test_take_lease_refuses_a_live_foreign_lease(stores):
    mine = stores()
    mine.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    other = stores()

    with pytest.raises(store.LeaseHeldError) as caught:
        other.take_lease(
            token="t2", pid=2, host="h", now=_at(1), is_live=_alive, claims=["card:x"]
        )

    assert caught.value.holder.token == "t1"
    kept = store.read_lease(other.connection, RUN_ID)
    assert kept is not None and kept.token == "t1"
    assert store.held_claims(other.connection, RUN_ID, "t2") == []
    assert other.connection.in_transaction is False


def test_take_lease_takes_over_a_dead_lease(stores):
    first = stores()
    first.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    first.close_window("t1")
    second = stores()

    took = second.take_lease(token="t2", pid=2, host="h2", now=_at(5), is_live=_dead)

    assert took.displaced is not None and took.displaced.token == "t1"
    assert took.lease == store.LeaseRow(
        run_id=RUN_ID,
        token="t2",
        pid=2,
        host="h2",
        acquired_at=_at(5),
        heartbeat_at=_at(5),
        accepting=True,
    )
    assert store.read_lease(second.connection, RUN_ID) == took.lease
    # A run nobody ever leased has nothing to displace.
    fresh = stores(OTHER_RUN_ID).take_lease(
        token="t3", pid=3, host="h", now=_at(0), is_live=_alive
    )
    assert fresh.displaced is None


def test_claims_are_all_or_nothing(stores):
    a, b = stores("run-a"), stores("run-b")
    a.take_lease(token="ta", pid=1, host="h", now=_at(0), is_live=_alive, claims=["card:x"])

    with pytest.raises(store.ClaimHeldError) as caught:
        b.take_lease(
            token="tb",
            pid=2,
            host="h",
            now=_at(0),
            is_live=_alive,
            claims=["card:y", "card:x"],
        )

    assert caught.value.key == "card:x"
    assert (caught.value.holder.run_id, caught.value.holder.token) == ("run-a", "ta")
    assert store.read_lease(b.connection, "run-b") is None
    assert store.held_claims(b.connection, "run-b", "tb") == []  # card:y rolled back too
    assert [claim.key for claim in store.held_claims(b.connection, "run-a", "ta")] == [
        "card:x"
    ]
    assert b.connection.in_transaction is False


def test_a_claim_under_a_dead_lease_is_overwritten(stores):
    a, b = stores("run-a"), stores("run-b")
    a.take_lease(token="ta", pid=1, host="h", now=_at(0), is_live=_alive, claims=["card:x"])

    took = b.take_lease(
        token="tb", pid=2, host="h", now=_at(3), is_live=_dead, claims=["card:x"]
    )

    assert took.displaced is None
    assert store.held_claims(b.connection, "run-b", "tb") == [
        store.ClaimRow(key="card:x", run_id="run-b", token="tb", claimed_at=_at(3))
    ]
    assert store.held_claims(b.connection, "run-a", "ta") == []
    # run-a's lease row itself is not b's to touch.
    other_lease = store.read_lease(b.connection, "run-a")
    assert other_lease is not None and other_lease.token == "ta"


def test_a_resume_rewrites_its_own_runs_claims(stores):
    keys = ["card:x", "branch:m10/x"]
    first = stores()
    first.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive, claims=keys)
    second = stores()

    took = second.take_lease(
        token="t2", pid=2, host="h", now=_at(4), is_live=_dead, claims=keys
    )

    assert took.displaced is not None and took.displaced.token == "t1"
    assert [
        (claim.key, claim.token, claim.claimed_at)
        for claim in store.held_claims(second.connection, RUN_ID, "t2")
    ] == [("branch:m10/x", "t2", _at(4)), ("card:x", "t2", _at(4))]
    assert store.held_claims(second.connection, RUN_ID, "t1") == []


def test_release_claims_deletes_only_its_own_tokens_rows(repo, stores):
    a, b = stores("run-a"), stores("run-b")
    a.take_lease(
        token="ta", pid=1, host="h", now=_at(0), is_live=_alive, claims=["card:x", "card:y"]
    )
    b.take_lease(token="tb", pid=2, host="h", now=_at(0), is_live=_alive, claims=["card:z"])
    _plant_claim(repo, "card:q", run_id="run-a", token="stale")

    a.release_claims("nobody")
    a.release_claims("ta")

    conn = a.connection
    assert store.held_claims(conn, "run-a", "ta") == []
    assert [claim.key for claim in store.held_claims(conn, "run-a", "stale")] == ["card:q"]
    assert [claim.key for claim in store.held_claims(conn, "run-b", "tb")] == ["card:z"]
    # The lease itself is `release_lease`'s business.
    kept = store.read_lease(conn, "run-a")
    assert kept is not None and kept.token == "ta"
    assert conn.in_transaction is False


def test_the_new_owner_continues_the_sequence(repo, stores):
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    b = stores()  # opened, its seq cached at 0, before a's write
    assert a.record_run(_run(repo)).seq == 1  # a writes seq 1 while still the owner

    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)

    assert b.record_run(_run_with_status(repo, RUN_ID, "stopped")).seq == 2
    assert [line.seq for line in b.journal.read()] == [1, 2]


_TAKER = """
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent_manager import store

root, run_id, token = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
st = store.Store.open(root, run_id)
try:
    print("ready", flush=True)
    if sys.stdin.readline().strip() != "go":
        raise SystemExit("no go")
    try:
        st.take_lease(
            token=token,
            pid=0,
            host="h",
            now=datetime.now(timezone.utc),
            is_live=lambda row: row.token != "t0",
        )
    except store.LeaseHeldError as error:
        print(type(error).__name__, flush=True)
    else:
        print("took", flush=True)
finally:
    st.close()
"""


def _taker(repo: Path, run_id: str, token: str) -> "subprocess.Popen[str]":
    """A real second process that takes `run_id`'s lease once told "go" on stdin.

    It inherits `XDG_DATA_HOME`/`HOME` from the `repo` fixture's monkeypatched
    environment, so it opens the same projection and journal as this test.
    """
    return subprocess.Popen(
        [sys.executable, "-c", _TAKER, str(repo), run_id, token],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )


@pytest.mark.parametrize("attempt", range(20))
def test_two_processes_taking_one_dead_lease_leave_exactly_one_owner(repo, attempt):
    _plant_lease(repo, RUN_ID, token="t0")  # a dead owner: t0 is dead to both children
    tokens = ("ta", "tb")
    children = [_taker(repo, RUN_ID, token) for token in tokens]
    try:
        for child in children:
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            assert child.stdin is not None
            child.stdin.write("go\n")
            child.stdin.flush()
        outcomes = {
            token: child.communicate(timeout=60)[0].strip()
            for token, child in zip(tokens, children)
        }
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()

    assert sorted(outcomes.values()) == ["LeaseHeldError", "took"]
    assert [child.returncode for child in children] == [0, 0]
    winner = next(token for token, outcome in outcomes.items() if outcome == "took")
    conn = store.open_db(repo)
    try:
        lease = store.read_lease(conn, RUN_ID)
    finally:
        conn.close()
    assert lease is not None and lease.token == winner
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "take_lease or claims_are_all or dead_lease_is_overwritten or resume_rewrites or release_claims or continues_the_sequence or two_processes or lease_is_touched or reacquiring or cancelled_run_round_trips" -v`
Expected: all FAIL with `AttributeError: 'Store' object has no attribute 'take_lease'` (the race children print a traceback to stderr and the `sorted(outcomes...)` assertion fails).

- [ ] **Step 3: Implement `Journal.reseek`**

In `src/agent_manager/store.py`, replace the `Journal` class docstring (lines 206-211) with:

```python
    """Append-only JSONL log for one run: the truth the projection is built from.

    The threads of the process that holds a run's lease share one `Journal`.
    The highest sequence number on disk is read when the journal is opened and
    cached; a lock serialises appends from those threads. A process that takes
    the lease over calls `reseek`, because the previous owner may have appended
    after this journal was opened (multi-process X4).
    """
```

After `last_seq` (after line 223), add:

```python
    def reseek(self) -> None:
        """Re-read the highest `seq` on disk into the cache, under the append lock.

        Called by `Store.take_lease` once the lease is this process's: a stuck
        previous owner may have appended lines after `__init__` cached `_seq`,
        and the new owner must number its first line after them.
        """
        with self._lock:
            self._seq = self.last_seq()
```

- [ ] **Step 4: Implement `take_lease`, `bind_lease`, `release_claims`; remove `acquire_lease`**

In `Store.__init__` (lines 733-736), replace the body with:

```python
    def __init__(self, conn: sqlite3.Connection, journal: Journal) -> None:
        self._conn = conn
        self._journal = journal
        self._lock = threading.RLock()
        self._token: str | None = None
        self._in_fence = False
```

Replace lines 1133-1163 (the section comment and the whole `acquire_lease` method) with:

```python
    # -- leases, claims and control requests -----------------------------------
    #
    # Row-only tables outside the journal (live control C2, multi-process X5):
    # nothing here calls `self._journal`, and `rebuild_from_journal` leaves the
    # rows alone. `take_lease` is the only check-and-set; every other method
    # touches only the rows whose token matches, and any other token is a
    # silent no-op.

    def take_lease(
        self,
        *,
        token: str,
        pid: int,
        host: str,
        now: datetime,
        is_live: Callable[[LeaseRow], bool],
        claims: Iterable[str] = (),
    ) -> LeaseTake:
        """Take this run's lease under `token`, with every key of `claims`, atomically.

        One `BEGIN IMMEDIATE` transaction (X5, X9): a live lease under another
        token raises `LeaseHeldError`; otherwise that row, or `None`, is the
        `displaced` one. Then the first key another run holds under a live
        lease raises `ClaimHeldError`. Only then are the lease (window open)
        and every claim upserted and committed. Any raise rolls all of it
        back and leaves the bound token as it was. On success the store is
        bound to `token` and the journal re-reads its highest `seq`.
        """
        keys = list(claims)
        with self._lock:
            with immediate(self._conn):
                current = read_lease(self._conn, self.run_id)
                if current is not None and current.token != token and is_live(current):
                    raise LeaseHeldError(current)
                conflicts = claim_conflicts(
                    self._conn, keys, is_live=is_live, run_id=self.run_id
                )
                if conflicts:
                    key, holder = conflicts[0]
                    raise ClaimHeldError(key, holder)
                self._conn.execute(
                    "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                    " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, 1)"
                    " ON CONFLICT(run_id) DO UPDATE SET"
                    " token=excluded.token, pid=excluded.pid, host=excluded.host,"
                    " acquired_at=excluded.acquired_at,"
                    " heartbeat_at=excluded.heartbeat_at, accepting=1",
                    (self.run_id, token, pid, host, _iso(now), _iso(now)),
                )
                for key in keys:
                    self._conn.execute(
                        "INSERT INTO run_claims (key, run_id, token, claimed_at)"
                        " VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                        " run_id=excluded.run_id, token=excluded.token,"
                        " claimed_at=excluded.claimed_at",
                        (key, self.run_id, token, _iso(now)),
                    )
            self.bind_lease(token)
            self._journal.reseek()
            return LeaseTake(
                lease=LeaseRow(
                    run_id=self.run_id,
                    token=token,
                    pid=pid,
                    host=host,
                    acquired_at=now,
                    heartbeat_at=now,
                    accepting=True,
                ),
                displaced=current,
            )

    def bind_lease(self, token: str | None) -> None:
        """Fence this store's run writes to `token`, or stop fencing with `None`."""
        with self._lock:
            self._token = token

    def release_claims(self, token: str) -> None:
        """Delete this run's claims held under `token`; any other row is untouched."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM run_claims WHERE run_id = ? AND token = ?",
                (self.run_id, token),
            )
            self._conn.commit()
```

(`beat`, `close_window`, `release_lease`, `pending_controls` and `mark_control_handled`, which follow, are unchanged.)

- [ ] **Step 5: Switch `control.Lease.__enter__` off `acquire_lease`**

In `src/agent_manager/control.py`, replace lines 106-113:

```python
    def __enter__(self) -> Lease:
        self.token = uuid4().hex
        self._store.acquire_lease(
            token=self.token,
            pid=os.getpid() if self._pid is None else self._pid,
            host=socket.gethostname() if self._host is None else self._host,
            now=self._clock(),
        )
```

with:

```python
    def __enter__(self) -> Lease:
        self.token = uuid4().hex
        self._store.take_lease(
            token=self.token,
            pid=os.getpid() if self._pid is None else self._pid,
            host=socket.gethostname() if self._host is None else self._host,
            now=self._clock(),
            # M9's unconditional replace; ec7ae954 injects `lease_is_live` and claims.
            is_live=lambda row: False,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k "take_lease or claims_are_all or dead_lease_is_overwritten or resume_rewrites or release_claims or continues_the_sequence or two_processes or lease_is_touched or reacquiring or cancelled_run_round_trips" -v`
Expected: all PASS (the race test 20 times).

Run: `uv run pytest tests/test_control.py -v`
Expected: all PASS (the `Lease` tests now go through `take_lease`).

Run: `uv run pytest`
Expected: the whole suite PASSes, `tests/e2e` included. If anything still references `acquire_lease`, it fails with `AttributeError`; a search of `src/` and `tests/` on this branch found only the four call sites changed above.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/store.py src/agent_manager/control.py tests/test_store.py
git commit -m "feat(store): take the run lease with its claims atomically, replacing acquire_lease"
```

---

### Task 3: Fence every run write to the bound token

**Files:**
- Modify: `src/agent_manager/store.py:130-151` (`BUSY_TIMEOUT_SECONDS` and `open_db` docstrings), `src/agent_manager/store.py:715-731` (`Store` docstring), add `_fenced`/`_commit` after `Store.close` (after line 756), `src/agent_manager/store.py:758-830` (recording comment and the five `record_*`), `src/agent_manager/store.py:838-998` (five `_write_*_row` commits), `src/agent_manager/store.py:1019-1071` (`save_checkpoint`), `src/agent_manager/store.py:1213-1261` (`rebuild_from_journal`, `_delete_run`)
- Modify: `src/agent_manager/control.py:121-131` (`Lease.__exit__`)
- Test: `tests/test_store.py` (append), `tests/test_control.py:33` (import) and a new test after `test_lease_heartbeat_survives_an_operational_error` (after line 315)

**Interfaces:**
- Consumes (Task 2): `Store.take_lease`, `Store.bind_lease`, `Store._token`, `Store._in_fence`, `LeaseLostError`, `_lease_from_row`, `immediate`; test fixture `stores`, helpers `_alive`, `_dead`, `_run`, `_run_with_status`, `_story`, `_subtask`, `_dispatch`, `_save_checkpoint`, `_record_full_run`, `_at` (all existing in `tests/test_store.py`).
- Produces:
  - `Store._fenced(self) -> contextlib.AbstractContextManager[None]` (a `@contextmanager`; no-op when `self._token is None`)
  - `Store._commit(self) -> None` (no-op while `self._in_fence`)
  - `control.Lease.__exit__` leaves the store unbound (`bind_lease(None)`) on every exit.

- [ ] **Step 1: Write the failing store tests**

Append to the end of `tests/test_store.py`:

```python
def test_a_taken_over_store_writes_nothing(repo, stores):
    a = stores()
    a.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)
    a.record_run(_run(repo))
    a.record_story(_story())
    b = stores()
    b.take_lease(token="t2", pid=2, host="h", now=_at(1), is_live=_dead)
    before = a.journal.path.read_bytes()

    writes = [
        lambda: a.record_run(_run_with_status(repo, RUN_ID, "done")),
        lambda: a.record_story(_story().model_copy(update={"status": "done"})),
        lambda: a.record_subtask("8831189b", _subtask()),
        lambda: a.record_phase(
            "8831189b",
            "ef248597",
            models.PhaseRun(name="explore", kind="agent", status="started"),
        ),
        lambda: a.record_attempt(
            "8831189b",
            "ef248597",
            "explore",
            models.Attempt(n=1, dispatch=_dispatch(phase="explore")),
        ),
        lambda: _save_checkpoint(a, "ef248597", saved_at=_at(2)),
        lambda: a.rebuild_from_journal(RUN_ID),
    ]
    for write in writes:
        with pytest.raises(store.LeaseLostError) as caught:
            write()
        assert caught.value.run_id == RUN_ID
        assert caught.value.holder is not None and caught.value.holder.token == "t2"
        assert a.connection.in_transaction is False

    assert a.journal.path.read_bytes() == before
    # The new owner's projection is exactly what a wrote while it was the owner.
    assert store.run_status(b.connection, RUN_ID) == "started"
    projected = b.load_run(RUN_ID)
    assert projected is not None
    assert [(story.status, story.subtasks) for story in projected.stories] == [("started", [])]
    assert b.latest_checkpoint("ef248597") is None

    # A deleted (not replaced) lease is lost too, and names no holder.
    b.release_lease("t2")
    with pytest.raises(store.LeaseLostError) as caught:
        a.record_run(_run(repo))
    assert caught.value.holder is None
    assert a.journal.path.read_bytes() == before


def test_an_unbound_store_writes_as_before(repo, stores):
    holder = stores()
    holder.take_lease(token="t9", pid=9, host="h", now=_at(0), is_live=_alive)
    st = stores()
    with pytest.raises(store.LeaseHeldError):
        st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)

    # Neither the refused take nor the foreign live lease binds or fences `st`.
    _record_full_run(st, repo)
    _save_checkpoint(st, "ef248597", saved_at=_at(1))
    st.rebuild_from_journal(RUN_ID)
    assert st.connection.in_transaction is False

    reader = store.open_db(repo)
    try:
        assert store.run_status(reader, RUN_ID) == "started"
        projected = store.load_run(reader, RUN_ID)
    finally:
        reader.close()
    assert projected is not None
    assert [subtask.card_id for subtask in projected.stories[0].subtasks] == [
        "fdebc746",
        "ef248597",
    ]
    kept = store.read_lease(st.connection, RUN_ID)
    assert kept is not None and kept.token == "t9"


def test_a_bound_store_commits_each_write_inside_its_fence(repo, stores):
    st = stores()
    st.take_lease(token="t1", pid=1, host="h", now=_at(0), is_live=_alive)

    _record_full_run(st, repo)
    first = _save_checkpoint(st, "ef248597", saved_at=_at(1))
    # A refused write inside the fence rolls back, spends no seq, leaves nothing open.
    with pytest.raises(sqlite3.IntegrityError):
        _save_checkpoint(st, "ef248597", reason="bogus", saved_at=_at(2))
    assert st.connection.in_transaction is False
    second = _save_checkpoint(st, "ef248597", saved_at=_at(3))
    st.rebuild_from_journal(RUN_ID)
    assert st.connection.in_transaction is False

    # Another connection sees every write: each fence committed its own work.
    reader = store.open_db(repo)
    try:
        assert store.run_status(reader, RUN_ID) == "started"
        projected = store.load_run(reader, RUN_ID)
        seqs = [
            row["seq"]
            for row in reader.execute(
                "SELECT seq FROM checkpoints WHERE run_id = ? ORDER BY seq", (RUN_ID,)
            ).fetchall()
        ]
    finally:
        reader.close()
    assert projected is not None
    assert [subtask.card_id for subtask in projected.stories[0].subtasks] == [
        "fdebc746",
        "ef248597",
    ]
    assert (first.seq, second.seq) == (0, 1)
    assert seqs == [0, 1]
```

- [ ] **Step 2: Write the failing `control.Lease` test**

In `tests/test_control.py`, change line 33:

```python
from agent_manager import control, models, store
```

After `test_lease_heartbeat_survives_an_operational_error` (after line 315), add:

```python
def test_a_lease_fences_its_store_only_while_it_is_held(root, opened_store):
    # Review Focus 5 of the a7ed6c11 plan.
    run = models.Run(
        id=RUN_ID,
        workflow="task",
        repo_dir=root,
        base_branch="main",
        branch_prefix="m10/",
        status="started",
        config=models.RunConfig(),
    )
    with control.Lease(opened_store):
        opened_store.record_run(run)
        thief = store.Store.open(root, RUN_ID)
        try:
            thief.take_lease(
                token="thief", pid=1, host="elsewhere", now=_at(0), is_live=lambda row: False
            )
        finally:
            thief.close()
        with pytest.raises(store.LeaseLostError) as caught:
            opened_store.record_run(run.model_copy(update={"status": "done"}))
        assert caught.value.holder is not None and caught.value.holder.token == "thief"

    # Out of the block the store is unbound and writes as M9 did.
    assert opened_store.record_run(run.model_copy(update={"status": "done"})).event == "run_upsert"
```

- [ ] **Step 3: Run the tests to verify the right ones fail**

Run: `uv run pytest tests/test_store.py tests/test_control.py -k "taken_over_store or unbound_store or bound_store_commits or fences_its_store" -v`
Expected:
- `test_a_taken_over_store_writes_nothing` FAILs with `Failed: DID NOT RAISE <class 'agent_manager.store.LeaseLostError'>`.
- `test_a_lease_fences_its_store_only_while_it_is_held` FAILs with `Failed: DID NOT RAISE <class 'agent_manager.store.LeaseLostError'>`.
- `test_an_unbound_store_writes_as_before` and `test_a_bound_store_commits_each_write_inside_its_fence` PASS already: they are guards for the no-op branch of `_fenced()` and for `_commit()` inside a fence, and must still pass after Step 4.

- [ ] **Step 4: Add `_fenced` and `_commit`**

In `src/agent_manager/store.py`, after `Store.close` (after line 756), add:

```python
    @contextmanager
    def _fenced(self) -> Iterator[None]:
        """Run one write as a single transaction fenced by the bound token (X4, X9).

        With no token bound this is a no-op and the write commits as it always
        has. Otherwise it opens `immediate`, and if this run's lease row is gone
        or carries another token it raises `LeaseLostError` before the body
        runs, so nothing is appended or written. While the body runs,
        `_in_fence` makes `_commit` a no-op: the journal append and the row
        write commit together when `immediate` exits, or roll back on a raise.
        Callers already hold `self._lock`.
        """
        if self._token is None:
            yield
            return
        with immediate(self._conn):
            row = self._conn.execute(
                "SELECT * FROM run_leases WHERE run_id = ?", (self.run_id,)
            ).fetchone()
            if row is None or row["token"] != self._token:
                raise LeaseLostError(
                    self.run_id, None if row is None else _lease_from_row(row)
                )
            self._in_fence = True
            try:
                yield
            finally:
                self._in_fence = False

    def _commit(self) -> None:
        """Commit a row write, unless a fence will commit it with its journal line."""
        if not self._in_fence:
            self._conn.commit()
```

- [ ] **Step 5: Fence the five `record_*` methods**

In `src/agent_manager/store.py`, replace the recording comment (lines 758-763) with:

```python
    # -- recording ---------------------------------------------------------
    #
    # Each method holds the store lock, and the fence of the bound lease token,
    # across its whole body: the journal line is appended first and the row
    # written second (§9), with no other record able to land in between. A
    # store whose lease was lost raises `LeaseLostError` before appending. If
    # the row write raises, the line stays on disk and the exception
    # propagates unchanged.
```

Then, in each of these five methods, change the first body line `with self._lock:` to `with self._lock, self._fenced():` and change nothing else:
- `record_run` (line 766)
- `record_story` (line 781)
- `record_subtask` (line 791)
- `record_phase` (line 804)
- `record_attempt` (line 818)

For example, `record_story` becomes:

```python
    def record_story(self, story: models.StoryRun) -> JournalLine:
        with self._lock, self._fenced():
            line = self._journal.append(
                "story_upsert",
                story.model_dump(mode="json", exclude={"subtasks"}),
                story=story.card_id,
            )
            self._write_story_row(self.run_id, story)
            return line
```

- [ ] **Step 6: Route the row writers' commits through `_commit`**

In `src/agent_manager/store.py`, change `self._conn.commit()` to `self._commit()` at exactly these lines, and nowhere else:
- line 865 (`_write_run_row`)
- line 888 (`_write_story_row`)
- line 917 (`_write_subtask_row`)
- line 948 (`_write_phase_row`)
- line 998 (`_write_attempt_row`)
- line 1261 (`_delete_run`)

The commits in `beat`, `close_window`, `release_lease`, `mark_control_handled` and `release_claims` stay `self._conn.commit()`: those are lease/control rows, never fenced.

- [ ] **Step 7: Fence `save_checkpoint` and `rebuild_from_journal`**

In `save_checkpoint`, change line 1036 `with self._lock:` to `with self._lock, self._fenced():` and line 1058 `self._conn.commit()` to `self._commit()`. The `except sqlite3.Error: self._conn.rollback(); raise` stays: inside a fence it ends the fence's transaction and `immediate` then re-raises without committing, so a refused row spends no `seq`.

In `rebuild_from_journal`, change line 1225 `with self._lock:` to `with self._lock, self._fenced():`, and add one sentence to its docstring's last paragraph so it reads:

```python
        The store lock is held from reading the journal through the delete and
        every rewrite, so no `record_*` lands between the delete and the
        rewrite. `_delete_run` is only called from here and takes no lock of
        its own. With a lease token bound, the delete and every rewrite are
        one fenced transaction: a store that lost its lease touches no row.
```

- [ ] **Step 8: Update the docstrings that still say two writers are unsupported**

Replace the `BUSY_TIMEOUT_SECONDS` docstring (lines 131-136) with:

```python
"""How long a statement on the projection waits for a lock held by another
connection before raising `sqlite3.OperationalError: database is locked`.

It covers a reader in another process, such as `am status`, holding the
database briefly, and a second `am` process's short `BEGIN IMMEDIATE` write
transactions: a lease take-over, or one fenced journal line and row
(multi-process X4, X9)."""
```

In the `open_db` docstring (lines 146-150), replace the paragraph starting "The connection may be used from any thread" with:

```python
    The connection may be used from any thread of the process that holds the
    run's lease, so `check_same_thread` is off; `Store` serialises that use
    behind its own lock. `BUSY_TIMEOUT_SECONDS` covers another process holding
    the database briefly; two processes never write one run, because every
    run write is fenced by the lease token (multi-process X4).
```

In the `Store` class docstring (lines 725-730), replace the paragraph starting "One process writes a given run (P2)" with:

```python
    The threads of the process holding a run's lease share one `Store`. A
    single re-entrant lock serialises every use of the shared connection. Each
    `record_*` holds it across the journal append and the row write, so the two
    are one critical section and journal order equals row order; `close`,
    `load_run` and `rebuild_from_journal` hold it too. Once `take_lease` has
    bound a token, every run write also runs inside `_fenced()`, one
    `BEGIN IMMEDIATE` transaction that first checks the token still holds the
    lease (multi-process X4). The lock never covers the caller's own work,
    only the append and the row write.
```

- [ ] **Step 9: Unbind the store when `control.Lease` exits**

In `src/agent_manager/control.py`, replace `Lease.__exit__`'s last line (line 131):

```python
        self._store.release_lease(self.token)
```

with:

```python
        try:
            self._store.release_lease(self.token)
        finally:
            # This process no longer holds the run: stop fencing its writes to
            # a token that is gone, as M9's store never fenced them.
            self._store.bind_lease(None)
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py tests/test_control.py -k "taken_over_store or unbound_store or bound_store_commits or fences_its_store" -v`
Expected: 4 PASS.

Run: `uv run pytest`
Expected: the whole suite PASSes, `tests/e2e` included. Every production write already happens inside its `with control.Lease(...)` block, where the store is bound to its own live token, so the fence passes for them. If a CLI/orchestrate test fails with `LeaseLostError`, a write is happening outside or after its lease block, or a test replaces the lease row mid-run: read that test before changing any code.

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/store.py src/agent_manager/control.py tests/test_store.py tests/test_control.py
git commit -m "feat(store): fence every run write to the bound lease token"
```
