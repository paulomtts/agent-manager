<!-- task-pipeline: validated -->
# Lease with claims for `--card` runs and task-run resumes (card ec7ae954)

Narrows Task 2.2 of `docs/superpowers/plans/2026-09-27-multi-process.md` (milestone 10, decisions X1–X11 in `docs/superpowers/specs/2026-09-27-multi-process-design.md`). Story f67dc4e4 "Leases that own runs, cards and branches". Branch prefix `m10`.

## Dependency

This task consumes the store API of sibling task a7ed6c11 (branch `m10/task-take-the-lease-a7ed6c11`, not yet on master): `take_lease`, `bind_lease`, `release_claims`, `claim_conflicts`, `held_claims`, `LeaseHeldError`, `ClaimHeldError`, `LeaseLostError`, `ClaimRow`, `LeaseTake`. Read those as built on that branch; do not change `store.py` or the schema (`run_claims` stays row-only). Current master's `control.Lease` (control.py:79-133) still calls `store.acquire_lease` and must be rewritten against the new API.

## Scope

`src/agent_manager/control.py`:
- `card_claim(card_id) -> str` returning `"card:<id>"`; `branch_claim(branch) -> str` returning `"branch:<name>"` (the latter is produced here, consumed by sibling 1a3fdd73).
- `Lease(store, *, claims: Sequence[str] = (), clock=_utcnow, heartbeat=HEARTBEAT_SECONDS, pid=None, host=None)`. `__enter__` computes `now = clock()` and calls `store.take_lease(token=..., pid=..., host=..., now=now, is_live=lambda row: lease_is_live(row, now=now), claims=claims)`, sets `self.displaced` from the result (the dead holder taken over, or `None`), then starts the heartbeat. `__exit__` stops the heartbeat, then `store.release_claims(token)`, then `store.release_lease(token)` — only this token's rows — on every exit path including exceptions. `lease_is_live` (control.py:65-74) is reused unchanged; no second liveness mechanism.

`src/agent_manager/cli.py`:
- `class ClaimedError(CliError)` with attributes `.key` and `.run_id`.
- `@contextmanager run_lease(store, *, claims=()) -> Iterator[control.Lease]`: a thin wrapper — enters `control.Lease`, translates `store.LeaseHeldError` to the existing `RunIsLiveError` (cli.py:162) and `store.ClaimHeldError` to `ClaimedError`, and forwards exit (including exceptions) to `Lease.__exit__`.
- `refuse_claimed(root, keys, *, run_id=None) -> None`: read-only preflight over `store.claim_conflicts` filtered by `control.lease_is_live`; raises `ClaimedError` for the first live conflict. Takes no lease, claim or lock and writes nothing. `run_id` excludes the run's own (resumed) rows. The message is built generically from the conflicting key, not hardcoded to "card": split the key on its first `:` into a kind ("card" or "branch") and an id/name, so the same code produces a correct message when sibling 1a3fdd73 later calls `refuse_claimed` with branch claims. Shape: "{kind} {id} is being driven by run {run_id} (pid {pid} on {host}, heartbeat {age}s ago); wait for it, or `am pause {run_id}`" (X11), reusing `_heartbeat_age` (cli.py:1680) for the age.
- `HANDLED` (cli.py:1078-1083) gains `store.LeaseLostError` (a `BaseException`, so it must be listed explicitly). `locks.LockTimeoutError` is already there, from the ProcessLock sibling task; leave it alone.
- `run_card` (cli.py:807-920): after its board reads and before `Store.open`, call `refuse_claimed(root, [card_claim(card.id)])`; right after `Store.open` and before `record_run`, enter `run_lease(store, claims=[card_claim(card.id)])` in place of the current unconditional `control.Lease(store)` (M9 entered the lease after `record_run`).
- `_resume_from_checkpoint` (cli.py:1434-1520): same pattern with the resumable subtask's card — `refuse_claimed(root, [card_claim(subtask.card_id)], run_id=run.id)` after its board reads and before `Store.open`, then `run_lease(store, claims=[card_claim(subtask.card_id)])` in place of the current `control.Lease(store)`. When `lease.displaced` is set, the payload gains `"took_over": {"pid", "host", "heartbeat_at"}` of the displaced holder. M9's C10 read-only check in `resume_run` stays first.
- `control_view` (cli.py:282-311, called from `status_for` at cli.py:1305) gains a `claims: Sequence[str]` parameter and puts it under a new `"claims"` key in its returned dict. `status_for` computes it as `held_claims(conn, wanted, lease.token)` when `lease` is not `None` and `control.lease_is_live(lease, now=now)`, else `()`. `status_payload`'s own fallback dict for "no control" (cli.py:332, `{"lease": None, "requests": []}`) also gains `"claims": []`, so the key is always present either way.

Out of scope: `orchestrate.py`, `milestone_claims`, and `run_milestone`'s use of `run_lease`/`refuse_claimed` (sibling 1a3fdd73); any `store.py`/schema change (a7ed6c11); ProcessLock (sibling ProcessLock task).

## Observable behaviour and error paths

- Envelope unchanged: `{"ok": true, "data": ...}` / `{"ok": false, "error": {"type", "message"}}`, exit 3 on refusal, `--pretty` still works.
- Card held by a live lease: exit 3, type `ClaimedError`, message shaped as `refuse_claimed` builds it (X11, see Scope above). A refusal leaves no run row, journal, fetch, prune or worktree; the only allowed leftover is an empty run directory when preflight passed but `take_lease` lost the race.
- Resume against a live lease: `RunIsLiveError` with M9 C10's message unchanged.
- Holder dead (per `lease_is_live`): no refusal; the lease is taken over and, on resume, reported in `took_over`.
- Lease lost mid-walk: the run stops without writing; `LeaseLostError` reaches the envelope at exit 3 unchanged (it is a `store.py` type, out of scope here), with `error_envelope`'s existing `str(error)` giving `"this process lost the lease of run '<run_id>': pid <pid> on <host> holds it now"` (or `"...: no process holds it now"` with no new holder).
- Claims and lease are released on success, on a failed step, and when the walk raises.
- Readers (`am status`, `am runs`, `am logs`, `am run --dry-run`) never take a lease, claim or lock.
- Lock ordering rules (in-process lock before flock, board and git locks never together, no store transaction under a ProcessLock) must not be violated by the new code.

## Tests

Placement rule (design spec §14 "Testing"): pure functions get plain unit tests; anything touching git or board state is a "Steps" test against real temporary git repos and a temporary brd board, no network, no filesystem mocking; `tests/e2e/` is reserved for the single opt-in real-harness test. None of these belong in e2e. No test sleeps for cross-process ordering; use pipes, marker files, fake-claude rendezvous or exit codes, and children inherit the test `XDG_DATA_HOME`. `_plant_lease` writes `run_leases` and `run_claims` rows over a second `open_db` connection inside `store.immediate` with `heartbeat_at = now`.

`tests/test_control.py`:
- `card_claim` / `branch_claim` return `"card:<id>"` / `"branch:<name>"` — unit (pure functions).
- `Lease` passes `claims`, `now`, and an `is_live` built from `lease_is_live` to `take_lease` and exposes `displaced` — Steps-style default suite (real temp store).
- `Lease.__exit__` releases claims then the lease, only for its own token, also on exception — Steps-style default suite (real temp store).

`tests/test_cli.py` (all Steps-style default suite: real temp git repo, real board, `CliRunner`, fake harness):
- `test_a_card_run_is_refused_while_another_live_run_claims_the_card` — exit 3 `ClaimedError`, message names the other run and pid, no new run, worktree or run directory.
- `test_a_dead_claim_does_not_refuse` — planted pid of a reaped child; the run proceeds.
- `test_the_lease_is_bound_before_the_first_journal_line` — spy on `Store.record_run`: `store._token` is not `None` on its first call.
- `test_a_card_run_releases_its_claims_on_every_exit` — including when the walk raises.
- `test_resume_takes_over_a_dead_lease_and_says_so` — `took_over.pid` equals the dead pid.
- `test_a_lease_lost_mid_walk_is_an_envelope_at_exit_3` — the runner's first phase takes the lease over from a second connection; error type `LeaseLostError`.
- `test_status_lists_the_claims_of_the_live_lease` — `control.claims` lists the keys; `[]` with no live lease.
- `test_readers_never_take_a_lease_or_a_lock` — status/runs/logs/`run --dry-run` with `store.Store.take_lease` and `locks.ProcessLock.acquire` (both already on this base branch) patched to raise all still succeed.

## Verification

`uv run pytest` (full suite; no separate lint or typecheck, per CLAUDE.md).

---

# Lease with claims for `--card` runs and task-run resumes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `am run --card` and a task-run `am resume` take their run's lease atomically with a `card:<id>` claim, refuse (exit 3, `ClaimedError`/`RunIsLiveError`) when another live run holds either, report a dead holder they took over, release everything on every exit, and show a live lease's claims in `am status`.

**Architecture:** `control.Lease` becomes the one place that calls `Store.take_lease` with claims and a `lease_is_live`-based liveness check, and the one place that releases claims then lease. `cli.py` adds a read-only preflight (`refuse_claimed`) that runs before `Store.open` so a refused run leaves no run directory, and a thin `run_lease` context manager that translates the store's lease/claim errors into `CliError` subclasses the existing envelope already renders. `status_for` reads `held_claims` for a live lease only.

**Tech Stack:** Python 3, Typer, SQLite (`sqlite3`), pytest, `typer.testing.CliRunner`, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-lease-with-claims-for-ec7ae954/docs/superpowers/specs/task-lease-with-claims-for-ec7ae954-design.md` (reproduced verbatim above).

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-lease-with-claims-for-ec7ae954` on `m10/task-lease-with-claims-for-ec7ae954`, cut from `m10/task-take-the-lease-a7ed6c11`. Run every command below from that worktree root.

## Global Constraints

- Do not change `src/agent_manager/store.py` or the schema; `run_claims` stays row-only.
- Do not touch `src/agent_manager/orchestrate.py` (sibling 1a3fdd73 owns milestone claims and `run_milestone`'s use of `run_lease`/`refuse_claimed`).
- One liveness mechanism only: `control.lease_is_live` (control.py:65-76), unchanged. No new heartbeat, pid file or staleness rule.
- Claim keys are exactly `"card:<id>"` and `"branch:<name>"`.
- `ClaimedError` message: `"{kind} {id} is being driven by run {run_id} (pid {pid} on {host}, heartbeat {age}s ago); wait for it, or `am pause {run_id}`"`, kind/id from splitting the key on its first `:`, age from `_heartbeat_age` (cli.py:1680).
- `RunIsLiveError` message stays M9 C10's: `"run {id} is still running in pid {pid} on {host} (heartbeat {age}s ago); wait for it to exit, or `am status {id}`"`.
- Envelope unchanged (`{"ok": true, "data": ...}` / `{"ok": false, "error": {"type", "message"}}`), refusals exit 3, `--pretty` unchanged.
- `HANDLED` gains `store_module.LeaseLostError`; `locks.LockTimeoutError` stays.
- A refused run leaves no run row, journal, worktree; the only allowed leftover is an empty run directory when the preflight passed but `take_lease` lost the race.
- Readers (`status`, `runs`, `logs`, `run --dry-run`) never take a lease, claim or lock.
- The new code takes no `ProcessLock` and opens no store transaction under one (lock-ordering rules unchanged).
- No test sleeps for ordering; tests use real temp git repos, a real temp brd board and `XDG_DATA_HOME` under `tmp_path`; nothing goes in `tests/e2e/`.
- Verification: `uv run pytest` (no lint or typecheck command exists).

## Review Focus

1. A claim taken by another live run after the preflight passed but before `take_lease` (a race): `run --card` must still refuse with `ClaimedError` at exit 3, leaving only an empty run directory and no run row. Pinned in Task 4 (`test_a_claim_taken_after_the_preflight_is_refused_with_only_an_empty_run_dir`).
2. A task-run resume that passes M9's C10 check but then finds a live lease at `take_lease` (another `am resume` won the race): must be `RunIsLiveError` with C10's exact message and must not have written orphan-attempt rows. This is why the resume's lease is taken right after `Store.open` (the "same pattern" as `run_card`), before the orphan writes, rather than where M9 entered it. Pinned in Task 6 (`test_a_resume_that_loses_the_lease_race_is_run_is_live_and_writes_nothing`).
3. A `branch:` key (sibling 1a3fdd73 will pass them) must read `branch m10/task-x ... is being driven by run ...`, not `card ...`, including when the branch name holds `/`. Pinned in Task 3 (`test_refuse_claimed_names_the_kind_and_the_live_holder`).
4. A lease taken over mid-block by another process: on exit this process must delete only its own token's claims and lease, never the new holder's. Pinned in Task 2 (`test_lease_exit_leaves_a_new_holders_lease_and_claims_alone`).
5. A stale lease whose claim rows were never cleaned up (the process was killed): `am status` must show `claims: []`, not the dead claims. Pinned in Task 7 (`test_status_lists_the_claims_of_the_live_lease[stale]`).

Known consequence outside this card's code: `orchestrate.py:1423` also enters `control.Lease(store)`. After Task 2 it will honour a live holder (raising `store.LeaseHeldError` raw) instead of replacing it unconditionally. `resume_run`'s C10 check still runs first for milestone resumes, and translating that error for milestone runs is sibling 1a3fdd73's job; do not edit `orchestrate.py` here.

---

### Task 1: Claim-key helpers `card_claim` and `branch_claim`

**Files:**
- Modify: `src/agent_manager/control.py` (add two functions after `lease_is_live`, i.e. after line 76)
- Test: `tests/test_control.py` (new section before `# -- Lease ---` at line 233)

**Interfaces:**
- Consumes: nothing.
- Produces: `control.card_claim(card_id: str) -> str` returning `f"card:{card_id}"`; `control.branch_claim(branch: str) -> str` returning `f"branch:{branch}"`.

- [ ] **Step 1: Write the failing test**

Insert into `tests/test_control.py` immediately above the line `# -- Lease ----------------------------------------------------------------------`:

```python
# -- claim keys (multi-process X5) ---------------------------------------------


def test_card_claim_and_branch_claim_name_their_keys():
    card = "ec7ae954-0000-4000-8000-000000000000"
    assert control.card_claim(card) == f"card:{card}"
    assert control.branch_claim("m10/task-lease-with-claims-for-ec7ae954") == (
        "branch:m10/task-lease-with-claims-for-ec7ae954"
    )


```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_control.py::test_card_claim_and_branch_claim_name_their_keys -v`
Expected: FAIL with `AttributeError: module 'agent_manager.control' has no attribute 'card_claim'`

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/control.py`, insert after the end of `lease_is_live` (after `    return lease.host != host or alive(lease.pid)`) and before `class Lease:`:

```python


def card_claim(card_id: str) -> str:
    """The `run_claims` key that says a run is driving card `card_id` (X5)."""
    return f"card:{card_id}"


def branch_claim(branch: str) -> str:
    """The `run_claims` key that says a run owns git branch `branch` (X5)."""
    return f"branch:{branch}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_control.py::test_card_claim_and_branch_claim_name_their_keys tests/test_control.py::test_control_module_imports_no_cli_orchestrate_or_grafo -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/control.py tests/test_control.py
git commit -m "feat(control): add card_claim and branch_claim keys"
```

---

### Task 2: `Lease` takes claims with a real liveness check, exposes `displaced`, and releases claims then lease

**Files:**
- Modify: `src/agent_manager/control.py:22` (imports), `src/agent_manager/control.py:79-138` (`Lease` docstring, `__init__`, `__enter__`, `__exit__`)
- Test: `tests/test_control.py` (helpers after `_requests` at line 102; tests appended to the `# -- Lease ---` section, after `test_a_lease_fences_its_store_only_while_it_is_held`, before `# -- apply_pending (C4) ---`)

**Interfaces:**
- Consumes: `store.Store.take_lease(*, token, pid, host, now, is_live, claims) -> store.LeaseTake` (fields `lease`, `displaced: LeaseRow | None`); `store.Store.release_claims(token)`; `store.Store.release_lease(token)`; `store.Store.bind_lease(token | None)`; `store.LeaseHeldError` (`.holder`); `store.ClaimHeldError` (`.key`, `.holder`); `control.lease_is_live`.
- Produces: `control.Lease(store, *, claims: Sequence[str] = (), heartbeat: float = HEARTBEAT_SECONDS, clock: Callable[[], datetime] = _utcnow, pid: int | None = None, host: str | None = None)`; attributes `token: str` and `displaced: store.LeaseRow | None`. `__enter__` raises `store.LeaseHeldError` / `store.ClaimHeldError` unchanged and leaves no rows and no heartbeat thread when it does.

- [ ] **Step 1: Write the test helpers**

In `tests/test_control.py`, insert after the `_requests` function (ends at line 102):

```python


def _plant(
    root: Path,
    *,
    run_id: str = RUN_ID,
    token: str = "t0",
    pid: int = 4242,
    host: str = "build-box",
    heartbeat_at: datetime,
    claims: tuple[str, ...] = (),
) -> None:
    """A lease row and its claims, written by a second connection as another `am` would."""
    conn = store.open_db(root)
    try:
        with store.immediate(conn):
            conn.execute(
                "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, 1)"
                " ON CONFLICT(run_id) DO UPDATE SET token=excluded.token,"
                " pid=excluded.pid, host=excluded.host,"
                " acquired_at=excluded.acquired_at,"
                " heartbeat_at=excluded.heartbeat_at, accepting=1",
                (run_id, token, pid, host, _at(0).isoformat(), heartbeat_at.isoformat()),
            )
            for key in claims:
                conn.execute(
                    "INSERT INTO run_claims (key, run_id, token, claimed_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " run_id=excluded.run_id, token=excluded.token,"
                    " claimed_at=excluded.claimed_at",
                    (key, run_id, token, _at(0).isoformat()),
                )
    finally:
        conn.close()


def _held(root: Path, token: str) -> list[str]:
    conn = store.open_db(root)
    try:
        return [claim.key for claim in store.held_claims(conn, RUN_ID, token)]
    finally:
        conn.close()


def _all_claims(root: Path) -> list[tuple[str, str, str]]:
    """Every `run_claims` row as `(key, run_id, token)`, in key order."""
    conn = store.open_db(root)
    try:
        return [
            (row["key"], row["run_id"], row["token"])
            for row in conn.execute(
                "SELECT key, run_id, token FROM run_claims ORDER BY key"
            ).fetchall()
        ]
    finally:
        conn.close()
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_control.py`, insert after `test_a_lease_fences_its_store_only_while_it_is_held` (ends with the line `    assert opened_store.record_run(run.model_copy(update={"status": "done"})).event == "run_upsert"`) and before `# -- apply_pending (C4) ---`:

```python


def test_a_lease_takes_its_claims_and_displaces_nothing_on_a_fresh_run(root, opened_store):
    with control.Lease(
        opened_store,
        claims=["card:a", "branch:m10/b"],
        pid=4242,
        host="build-box",
        clock=lambda: _at(0),
    ) as lease:
        assert lease.displaced is None
        assert _held(root, lease.token) == ["branch:m10/b", "card:a"]
    assert _all_claims(root) == []


def test_a_lease_takes_over_a_holder_that_is_stale_at_its_own_clock(root, opened_store):
    _plant(root, token="dead", host="other-box", heartbeat_at=_at(0))

    with control.Lease(
        opened_store, clock=lambda: _at(control.LEASE_STALE_SECONDS + 1)
    ) as lease:
        assert lease.displaced is not None
        assert (lease.displaced.token, lease.displaced.host) == ("dead", "other-box")
        row = _read_lease(root)
        assert row is not None and row.token == lease.token
    assert _read_lease(root) is None


def test_a_lease_refuses_a_live_holder_and_leaves_no_trace(root, opened_store):
    # Live on another host, and exactly at the (inclusive) stale boundary of
    # the lease's own clock: proves `is_live` is `lease_is_live(now=clock())`.
    _plant(root, token="alive", host="other-box", heartbeat_at=_at(0))

    with pytest.raises(store.LeaseHeldError) as caught:
        with control.Lease(
            opened_store,
            claims=["card:a"],
            clock=lambda: _at(control.LEASE_STALE_SECONDS),
        ):
            pytest.fail("entered a lease another live process holds")

    assert caught.value.holder.token == "alive"
    row = _read_lease(root)
    assert row is not None and row.token == "alive"
    assert _all_claims(root) == []
    assert _heartbeat_threads() == []


def test_a_lease_refuses_a_key_another_live_run_claims(root, opened_store):
    _plant(
        root,
        run_id="run-other",
        token="theirs",
        host="other-box",
        heartbeat_at=_at(0),
        claims=("card:a",),
    )

    with pytest.raises(store.ClaimHeldError) as caught:
        with control.Lease(opened_store, claims=["card:a"], clock=lambda: _at(1)):
            pytest.fail("entered a lease whose claim another live run holds")

    assert caught.value.key == "card:a"
    assert caught.value.holder.run_id == "run-other"
    assert _read_lease(root) is None
    assert _all_claims(root) == [("card:a", "run-other", "theirs")]
    assert _heartbeat_threads() == []


def test_lease_exit_releases_claims_then_lease_even_on_exception(root, opened_store):
    events: list[tuple[str, str]] = []

    class Recording(Wrapped):
        def release_claims(self, token: str) -> None:
            events.append(("release_claims", token))
            self._inner.release_claims(token)

        def release_lease(self, token: str) -> None:
            events.append(("release_lease", token))
            self._inner.release_lease(token)

    _plant(
        root,
        run_id="run-other",
        token="theirs",
        host="other-box",
        heartbeat_at=_at(0),
        claims=("card:z",),
    )

    with pytest.raises(RuntimeError, match="inside the run"):
        with control.Lease(
            Recording(opened_store), claims=["card:a"], clock=lambda: _at(1)
        ) as lease:
            assert _held(root, lease.token) == ["card:a"]
            raise RuntimeError("inside the run")

    assert events == [("release_claims", lease.token), ("release_lease", lease.token)]
    assert _read_lease(root) is None
    assert _all_claims(root) == [("card:z", "run-other", "theirs")]
    assert _heartbeat_threads() == []


def test_lease_exit_leaves_a_new_holders_lease_and_claims_alone(root, opened_store):
    # Review Focus 4: taken over mid-block, this lease releases only its own token.
    with control.Lease(opened_store, claims=["card:a"], clock=lambda: _at(0)):
        thief = store.Store.open(root, RUN_ID)
        try:
            thief.take_lease(
                token="thief",
                pid=1,
                host="elsewhere",
                now=_at(1),
                is_live=lambda row: False,
                claims=["card:a"],
            )
        finally:
            thief.close()

    row = _read_lease(root)
    assert row is not None and row.token == "thief"
    assert _all_claims(root) == [("card:a", RUN_ID, "thief")]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_control.py -k "claims or displaces or stale_at_its_own_clock or live_holder or another_live_run or releases_claims_then_lease or new_holders" -v`
Expected: FAIL — `TypeError: Lease.__init__() got an unexpected keyword argument 'claims'` for the claim tests, `AttributeError: 'Lease' object has no attribute 'displaced'` for the stale takeover, and `Failed: DID NOT RAISE <class 'agent_manager.store.LeaseHeldError'>` for the live-holder test.

- [ ] **Step 4: Update the import**

In `src/agent_manager/control.py`, change line 22:

```python
from collections.abc import Awaitable, Callable
```

to:

```python
from collections.abc import Awaitable, Callable, Sequence
```

- [ ] **Step 5: Rewrite `Lease.__init__`, `__enter__` and `__exit__`**

In `src/agent_manager/control.py`, replace the whole block from `class Lease:` through the end of `__exit__` (the lines ending `            self._store.bind_lease(None)`) with:

```python
class Lease:
    """This process's claim on a run and its keys, held for a `with` block (C2, X5).

    `__enter__` takes a fresh token and calls `Store.take_lease` with `claims`,
    `now = clock()` and `is_live` built on `lease_is_live` at that `now`, so a
    live holder of the run or of any key refuses the lease
    (`store.LeaseHeldError`, `store.ClaimHeldError`) and a dead one is taken
    over and kept in `displaced`. Only then does it start a daemon heartbeat
    thread. That thread waits on a `threading.Event`, never `time.sleep`, so
    `__exit__` wakes it at once. `__exit__` stops and joins it, then releases
    this token's claims, then this token's lease, on any exit, and never
    swallows the exception. A process that took the lease over keeps its rows.
    """

    def __init__(
        self,
        store: Store,
        *,
        claims: Sequence[str] = (),
        heartbeat: float = HEARTBEAT_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
        pid: int | None = None,
        host: str | None = None,
    ) -> None:
        self._store = store
        self._claims = tuple(claims)
        self._heartbeat = heartbeat
        self._clock = clock
        self._pid = pid
        self._host = host
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self.token = ""
        self.displaced: LeaseRow | None = None

    def __enter__(self) -> Lease:
        self.token = uuid4().hex
        now = self._clock()
        taken = self._store.take_lease(
            token=self.token,
            pid=os.getpid() if self._pid is None else self._pid,
            host=socket.gethostname() if self._host is None else self._host,
            now=now,
            is_live=lambda row: lease_is_live(row, now=now),
            claims=self._claims,
        )
        self.displaced = taken.displaced
        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._keep_beating, name="am-lease-heartbeat", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        try:
            try:
                self._store.release_claims(self.token)
            finally:
                self._store.release_lease(self.token)
        finally:
            # This process no longer holds the run: stop fencing its writes to
            # a token that is gone, as M9's store never fenced them.
            self._store.bind_lease(None)
```

- [ ] **Step 6: Run the control tests to verify they pass**

Run: `uv run pytest tests/test_control.py -v`
Expected: PASS (every test in the file, old and new)

- [ ] **Step 7: Run the neighbouring suites for regressions**

Run: `uv run pytest tests/test_cli.py tests/test_orchestrate.py tests/test_store.py -q`
Expected: PASS (no test plants a live lease that a run then has to replace)

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/control.py tests/test_control.py
git commit -m "feat(control): Lease takes claims with lease_is_live, exposes displaced, releases claims then lease"
```

---

### Task 3: `ClaimedError`, `refuse_claimed` and `run_lease` in `cli.py`

**Files:**
- Modify: `src/agent_manager/cli.py:20-27` (imports), `src/agent_manager/cli.py:163-164` (add `ClaimedError` after `RunIsLiveError`), `src/agent_manager/cli.py:794-805` (add helpers after `card_run_status`), `src/agent_manager/cli.py:1587-1594` (`resume_run` uses the shared message helper)
- Test: `tests/test_cli.py` (extend `_plant_lease` at lines 5140-5174; add `OTHER_RUN_ID`, `_claim_rows`; new tests appended at end of file)

**Interfaces:**
- Consumes: `store_module.claim_conflicts(conn, keys, *, is_live, run_id=None) -> list[tuple[str, LeaseRow]]`; `store_module.open_db(root)`; `store_module.LeaseHeldError.holder`; `store_module.ClaimHeldError.key/.holder`; `control.Lease(store, *, claims=...)` (Task 2); `control.lease_is_live`; `cli._heartbeat_age(lease, now) -> int` (cli.py:1680); `cli._utcnow()`.
- Produces:
  - `class ClaimedError(CliError)` with `__init__(self, message: str, *, key: str, run_id: str)` and attributes `.key`, `.run_id`.
  - `refuse_claimed(root: Path, keys: Sequence[str], *, run_id: str | None = None) -> None` (raises `ClaimedError`).
  - `@contextmanager run_lease(store: Store, *, claims: Sequence[str] = ()) -> Iterator[control.Lease]` (raises `RunIsLiveError` / `ClaimedError` on enter).
  - private `_run_is_live_error(lease: LeaseRow, now: datetime) -> RunIsLiveError`, `_claimed_error(key: str, holder: LeaseRow, now: datetime) -> ClaimedError`.
  - test helpers `_plant_lease(root, *, run_id=CONTROL_RUN_ID, token="life-2", pid=None, host=None, heartbeat_at=CONTROL_NOW, accepting=True, claims=())`, `_claim_rows(root) -> list[tuple[str, str, str]]`, constant `OTHER_RUN_ID`.

- [ ] **Step 1: Extend the `_plant_lease` test helper and add `_claim_rows`**

In `tests/test_cli.py`, replace the whole `_plant_lease` function (lines 5140-5174) with:

```python
def _plant_lease(
    root: Path,
    *,
    run_id: str = CONTROL_RUN_ID,
    token: str = "life-2",
    pid: int | None = None,
    host: str | None = None,
    heartbeat_at: datetime = CONTROL_NOW,
    accepting: bool = True,
    claims: tuple[str, ...] = (),
) -> None:
    """A `run_leases` row and its `run_claims`, as another process's `Lease` would leave them.

    Written over a second `open_db` connection inside `store.immediate`.
    Defaults to this process on this host with a heartbeat at the frozen
    clock: live by C2.
    """
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        with store_module.immediate(conn):
            conn.execute(
                "INSERT INTO run_leases (run_id, token, pid, host, acquired_at,"
                " heartbeat_at, accepting) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id) DO UPDATE SET token=excluded.token,"
                " pid=excluded.pid, host=excluded.host, acquired_at=excluded.acquired_at,"
                " heartbeat_at=excluded.heartbeat_at, accepting=excluded.accepting",
                (
                    run_id,
                    token,
                    os.getpid() if pid is None else pid,
                    HERE if host is None else host,
                    _at(-60).isoformat(),
                    heartbeat_at.isoformat(),
                    int(accepting),
                ),
            )
            for key in claims:
                conn.execute(
                    "INSERT INTO run_claims (key, run_id, token, claimed_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " run_id=excluded.run_id, token=excluded.token,"
                    " claimed_at=excluded.claimed_at",
                    (key, run_id, token, heartbeat_at.isoformat()),
                )
    finally:
        conn.close()


OTHER_RUN_ID = "20260930T080000Z-a1b2c3d4"
"""Another run, driven by another `am` process, that holds a claim."""


def _claim_rows(root: Path) -> list[tuple[str, str, str]]:
    """Every `run_claims` row as `(key, run_id, token)`, in key order."""
    conn = store_module.open_db(cli.resolve_repo_dir(root))
    try:
        return [
            (row["key"], row["run_id"], row["token"])
            for row in conn.execute(
                "SELECT key, run_id, token FROM run_claims ORDER BY key"
            ).fetchall()
        ]
    finally:
        conn.close()
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python


# ── leases with claims (card ec7ae954) ──────────────────────────────────────


@pytest.mark.parametrize(
    "key, named",
    [
        ("card:card-1", "card card-1"),
        ("branch:m10/task-x-ec7ae954", "branch m10/task-x-ec7ae954"),
    ],
    ids=["card", "branch"],
)
def test_refuse_claimed_names_the_kind_and_the_live_holder(projection, monkeypatch, key, named):
    """X11's wording, built from the key so a branch claim reads as a branch
    (Review Focus 3). Read-only: no row changes, no run directory."""
    _freeze_clock(monkeypatch)
    _plant_lease(projection, heartbeat_at=_at(-7), claims=(key,))
    before = (_lease(projection), _claim_rows(projection))

    with pytest.raises(cli.ClaimedError) as caught:
        cli.refuse_claimed(cli.resolve_repo_dir(projection), [key])

    assert str(caught.value) == (
        f"{named} is being driven by run {CONTROL_RUN_ID}"
        f" (pid {os.getpid()} on {HERE}, heartbeat 7s ago);"
        f" wait for it, or `am pause {CONTROL_RUN_ID}`"
    )
    assert (caught.value.key, caught.value.run_id) == (key, CONTROL_RUN_ID)
    assert isinstance(caught.value, cli.CliError)
    assert (_lease(projection), _claim_rows(projection)) == before
    assert not (paths.data_dir() / "runs").exists()


def test_refuse_claimed_passes_the_runs_own_claims_unclaimed_keys_and_dead_ones(
    projection, monkeypatch
):
    _freeze_clock(monkeypatch)
    root = cli.resolve_repo_dir(projection)
    _plant_lease(projection, heartbeat_at=_at(-5), claims=("card:card-1",))

    cli.refuse_claimed(root, ["card:card-1"], run_id=CONTROL_RUN_ID)
    cli.refuse_claimed(root, ["card:someone-else"])

    _plant_lease(projection, heartbeat_at=_at(-31), claims=("card:card-1",))
    cli.refuse_claimed(root, ["card:card-1"])


def test_run_lease_turns_a_held_claim_into_claimed_error_and_takes_nothing(
    projection, monkeypatch
):
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    _plant_lease(
        projection,
        run_id=OTHER_RUN_ID,
        heartbeat_at=now - timedelta(seconds=7),
        claims=("card:card-1",),
    )
    opened = store_module.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
    try:
        with pytest.raises(cli.ClaimedError) as caught:
            with cli.run_lease(opened, claims=["card:card-1"]):
                pytest.fail("entered a lease whose claim another live run holds")
    finally:
        opened.close()

    assert (caught.value.key, caught.value.run_id) == ("card:card-1", OTHER_RUN_ID)
    assert str(caught.value) == (
        f"card card-1 is being driven by run {OTHER_RUN_ID}"
        f" (pid {os.getpid()} on {HERE}, heartbeat 7s ago);"
        f" wait for it, or `am pause {OTHER_RUN_ID}`"
    )
    assert _lease(projection) is None
    assert _claim_rows(projection) == [("card:card-1", OTHER_RUN_ID, "life-2")]


def test_run_lease_turns_a_held_lease_into_c10s_run_is_live_error(projection, monkeypatch):
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    _plant_lease(projection, heartbeat_at=now - timedelta(seconds=5))
    opened = store_module.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
    try:
        with pytest.raises(cli.RunIsLiveError) as caught:
            with cli.run_lease(opened, claims=["card:card-1"]):
                pytest.fail("entered a lease another live process holds")
    finally:
        opened.close()

    assert str(caught.value) == (
        f"run {CONTROL_RUN_ID} is still running in pid {os.getpid()} on {HERE}"
        " (heartbeat 5s ago); wait for it to exit,"
        f" or `am status {CONTROL_RUN_ID}`"
    )
    assert _claim_rows(projection) == []


def test_run_lease_releases_its_claims_and_lease_when_the_body_raises(projection):
    opened = store_module.Store.open(cli.resolve_repo_dir(projection), CONTROL_RUN_ID)
    try:
        with pytest.raises(ValueError, match="the walk raised"):
            with cli.run_lease(opened, claims=["card:card-1"]) as lease:
                assert _claim_rows(projection) == [("card:card-1", CONTROL_RUN_ID, lease.token)]
                assert _lease(projection) is not None
                raise ValueError("the walk raised")
    finally:
        opened.close()

    assert _lease(projection) is None
    assert _claim_rows(projection) == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "refuse_claimed or run_lease" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'ClaimedError'` (and `'refuse_claimed'` / `'run_lease'`)

- [ ] **Step 4: Add the imports**

In `src/agent_manager/cli.py`, change:

```python
import asyncio
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
```

to:

```python
import asyncio
import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
```

- [ ] **Step 5: Add `ClaimedError`**

In `src/agent_manager/cli.py`, after:

```python
class RunIsLiveError(CliError):
    """`am resume` was asked for a run another live process still holds (C10)."""
```

insert:

```python


class ClaimedError(CliError):
    """A card or branch this run needs is claimed by another run's live lease (X5, X11).

    `key` is the claim key (`card:<id>` or `branch:<name>`) and `run_id` the
    run that holds it, so a script can act on the refusal without parsing
    the message.
    """

    def __init__(self, message: str, *, key: str, run_id: str) -> None:
        super().__init__(message)
        self.key = key
        self.run_id = run_id
```

- [ ] **Step 6: Add the refusal builders, `refuse_claimed` and `run_lease`**

In `src/agent_manager/cli.py`, after the end of `card_run_status` (the line `    return summary.status` just before `def run_card(`), insert:

```python


def _run_is_live_error(lease: store_module.LeaseRow, now: datetime) -> RunIsLiveError:
    """C10's refusal of a run another live process holds, worded once for every caller."""
    return RunIsLiveError(
        f"run {lease.run_id} is still running in pid {lease.pid} on {lease.host}"
        f" (heartbeat {_heartbeat_age(lease, now)}s ago); wait for it to exit,"
        f" or `am status {lease.run_id}`"
    )


def _claimed_error(key: str, holder: store_module.LeaseRow, now: datetime) -> ClaimedError:
    """X11's refusal of a claimed key. The kind and name come from the key itself,
    split on its first `:`, so a `branch:` claim reads as a branch."""
    kind, _, name = key.partition(":")
    return ClaimedError(
        f"{kind} {name} is being driven by run {holder.run_id}"
        f" (pid {holder.pid} on {holder.host},"
        f" heartbeat {_heartbeat_age(holder, now)}s ago);"
        f" wait for it, or `am pause {holder.run_id}`",
        key=key,
        run_id=holder.run_id,
    )


def refuse_claimed(root: Path, keys: Sequence[str], *, run_id: str | None = None) -> None:
    """Refuse, before any write, a run whose keys another live run already claims.

    Read-only preflight (X5): one `open_db` connection, `claim_conflicts`
    judged by `control.lease_is_live` at `_utcnow()`, closed on every path.
    It takes no lease, claim or lock, so a refusal here leaves no run
    directory. `run_id` excludes that run's own rows (a resume). The first
    live conflict raises `ClaimedError`; `take_lease` re-checks atomically.
    """
    now = _utcnow()
    conn = store_module.open_db(root)
    try:
        conflicts = store_module.claim_conflicts(
            conn,
            keys,
            is_live=lambda row: control.lease_is_live(row, now=now),
            run_id=run_id,
        )
    finally:
        conn.close()
    if conflicts:
        key, holder = conflicts[0]
        raise _claimed_error(key, holder, now)


@contextmanager
def run_lease(store: Store, *, claims: Sequence[str] = ()) -> Iterator[control.Lease]:
    """Hold `control.Lease(store, claims=claims)` for the block, with CLI refusals.

    A thin wrapper: only entering is translated -- `store.LeaseHeldError`
    becomes C10's `RunIsLiveError`, `store.ClaimHeldError` becomes
    `ClaimedError` -- and the block's exit, an exception included, is
    `Lease.__exit__`'s, which releases the claims then the lease.
    """
    stack = ExitStack()
    try:
        lease = stack.enter_context(control.Lease(store, claims=claims))
    except store_module.LeaseHeldError as error:
        raise _run_is_live_error(error.holder, _utcnow()) from error
    except store_module.ClaimHeldError as error:
        raise _claimed_error(error.key, error.holder, _utcnow()) from error
    with stack:
        yield lease
```

- [ ] **Step 7: Make `resume_run` use the shared C10 message**

In `src/agent_manager/cli.py`, inside `resume_run`, replace:

```python
        if lease is not None and control.lease_is_live(lease, now=now):
            raise RunIsLiveError(
                f"run {run.id} is still running in pid {lease.pid} on {lease.host}"
                f" (heartbeat {_heartbeat_age(lease, now)}s ago); wait for it to exit,"
                f" or `am status {run.id}`"
            )
```

with:

```python
        if lease is not None and control.lease_is_live(lease, now=now):
            raise _run_is_live_error(lease, now)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "refuse_claimed or run_lease or resume_refuses_a_run_whose_lease_is_live or status_shows_the_lease or request_to" -v`
Expected: PASS (new tests, plus the existing C10 and control tests that use `_plant_lease`)

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add ClaimedError, refuse_claimed preflight and run_lease wrapper"
```

---

### Task 4: `run_card` refuses a claimed card before `Store.open` and takes its lease with the card claim

**Files:**
- Modify: `src/agent_manager/cli.py` `run_card` (currently lines 807-926; after Task 3 it has moved down by the inserted helpers — locate it by `def run_card(`)
- Test: `tests/test_cli.py` (import list at lines 33-46; new tests appended at end of file)

**Interfaces:**
- Consumes: `refuse_claimed`, `run_lease` (Task 3); `control.card_claim` (Task 1).
- Produces: `run_card` behaviour — `ClaimedError` before `Store.open` when a live run claims `card:<id>`; lease plus `card:<id>` claim bound before the first `record_run`; claims and lease released on every exit. Test helper `_reaped_pid() -> int`.

- [ ] **Step 1: Import `control` in the tests**

In `tests/test_cli.py`, change:

```python
from agent_manager import (
    board,
    census,
    cli,
    dag,
```

to:

```python
from agent_manager import (
    board,
    census,
    cli,
    control,
    dag,
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python


def _reaped_pid() -> int:
    """The pid of a child that has exited and been waited for: dead by `pid_alive`."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _run_dirs() -> list[Path]:
    runs_root = paths.data_dir() / "runs"
    return sorted(runs_root.iterdir()) if runs_root.exists() else []


def _recorded_run_ids(project: Path) -> list[str]:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return [summary.id for summary in store_module.list_runs(conn)]
    finally:
        conn.close()


@requires_git
@requires_brd
def test_a_card_run_is_refused_while_another_live_run_claims_the_card(
    project, cards, monkeypatch
):
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=now - timedelta(seconds=7),
        claims=(key,),
    )
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "type": "ClaimedError",
            "message": (
                f"card {cards['subtask']} is being driven by run {OTHER_RUN_ID}"
                f" (pid {os.getpid()} on {HERE}, heartbeat 7s ago);"
                f" wait for it, or `am pause {OTHER_RUN_ID}`"
            ),
        },
    }
    assert _run_dirs() == []
    assert _recorded_run_ids(project) == []
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]
    worktrees = [
        line
        for line in _git(project, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    assert len(worktrees) == 1, worktrees
    assert _git(project, "branch", "--format=%(refname:short)").split() == ["main"]


@requires_git
@requires_brd
def test_a_claim_taken_after_the_preflight_is_refused_with_only_an_empty_run_dir(
    project, cards, monkeypatch
):
    """Review Focus 1: the preflight passed, then another run claimed the card
    before `take_lease`; the only leftover is the empty run directory."""
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=now - timedelta(seconds=7),
        claims=(key,),
    )
    monkeypatch.setattr(cli, "refuse_claimed", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    error = json.loads(result.stdout)["error"]
    assert error["type"] == "ClaimedError"
    assert error["message"].startswith(f"card {cards['subtask']} is being driven by run {OTHER_RUN_ID}")
    (run_dir,) = _run_dirs()
    assert list(run_dir.iterdir()) == []
    assert _recorded_run_ids(project) == []
    assert _claim_rows(project) == [(key, OTHER_RUN_ID, "other-life")]


@requires_git
@requires_brd
def test_a_dead_claim_does_not_refuse(project, cards):
    dead = _reaped_pid()
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="dead-life",
        pid=dead,
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )

    payload = cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    assert payload["status"] == "done"
    # The dead claim was taken over by this run, then released with its lease.
    assert _claim_rows(project) == []


@requires_git
@requires_brd
def test_the_lease_is_bound_before_the_first_journal_line(project, cards, monkeypatch):
    real_record_run = store_module.Store.record_run
    first: list[tuple[str | None, list[str]]] = []

    def spy(self, run):
        if not first:
            token = self._token
            held = (
                []
                if token is None
                else [
                    claim.key
                    for claim in store_module.held_claims(self.connection, self.run_id, token)
                ]
            )
            first.append((token, held))
        return real_record_run(self, run)

    monkeypatch.setattr(store_module.Store, "record_run", spy)

    cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        runner_factory=lambda **kwargs: fake_runner(),
    )

    ((token, held),) = first
    assert token is not None
    assert held == [control.card_claim(cards["subtask"])]


@pytest.mark.parametrize("outcome", ["done", "escalated", "raises"])
@requires_git
@requires_brd
def test_a_card_run_releases_its_claims_on_every_exit(project, cards, monkeypatch, outcome):
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    key = control.card_claim(cards["subtask"])
    during: list[list[tuple[str, str, str]]] = []

    if outcome == "raises":

        async def exploding(*args, **kwargs):
            during.append(_claim_rows(project))
            raise EngineError("no value for a required parameter", phase="explore")

        monkeypatch.setattr(cli.runtime_engine, "run_subtask_async", exploding)

    inner = fake_runner(fail="review" if outcome == "escalated" else None)

    def watching(phase, context, rendered):
        if phase.name == "explore":
            during.append(_claim_rows(project))
        return inner(phase, context, rendered)

    def drive() -> dict[str, Any]:
        return cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            branch_prefix="m1",
            clock=lambda: CRASHED_AT,
            runner_factory=lambda **kwargs: watching,
            control_interval=CONTROL_TICK,
        )

    if outcome == "raises":
        with pytest.raises(EngineError, match="explore"):
            drive()
    else:
        assert drive()["status"] == outcome

    ((row,),) = during
    assert row[:2] == (key, run_id)
    assert _claim_rows(project) == []
    assert _card_lease(project, run_id) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "claims_the_card or after_the_preflight or dead_claim_does_not_refuse or bound_before_the_first_journal_line or releases_its_claims_on_every_exit" -v`
Expected: FAIL — the refusal tests fail with `Failed: the milestone dry run reached cli.default_runner_factory` (no refusal yet); `test_a_dead_claim_does_not_refuse` fails on `_claim_rows(project) == []` (the planted claim is still there); the journal test fails on `held == [...]` (got `[]`); the release tests fail on `((row,),) = during` (`ValueError: not enough values to unpack`, no claim held during the walk).

- [ ] **Step 4: Add the preflight to `run_card`**

In `src/agent_manager/cli.py`, inside `run_card`, replace:

```python
    branch = dag.task_branch(branch_prefix, card)
    worktree = worktree_for(root, branch)
    started_at = clock()
    run_id = mint_run_id(card.id, started_at)

    store = Store.open(root, run_id)
```

with:

```python
    branch = dag.task_branch(branch_prefix, card)
    worktree = worktree_for(root, branch)
    claims = [control.card_claim(card.id)]
    # Read-only and before `Store.open`, so a refused card leaves no run
    # directory (X5); `take_lease` below re-checks atomically.
    refuse_claimed(root, claims)
    started_at = clock()
    run_id = mint_run_id(card.id, started_at)

    store = Store.open(root, run_id)
```

- [ ] **Step 5: Take the lease with the claim, before any record**

Still in `run_card`, replace:

```python
        # Inside the `try` that closes the store, so the lease is released
        # before `store.close()` on every exit, a raising walk included (C2).
        with control.Lease(store) as lease:
            store.record_run(run_record)
```

with:

```python
        # Inside the `try` that closes the store, so the claims and the lease
        # are released before `store.close()` on every exit, a raising walk
        # included (C2, X5). Taken before `record_run`, so every run write is
        # fenced by this token; a lost race is `ClaimedError` with nothing
        # written but the empty run directory.
        with run_lease(store, claims=claims) as lease:
            store.record_run(run_record)
```

Also update the `run_card` docstring paragraph that begins `Live control (C11): from the \`started\` rows through the final ones the run holds a \`control.Lease\`` so its first sentence reads:

```python
    Live control (C11) and claims (X5): the card is refused before
    `Store.open` if another live run claims it, and from before the
    `started` rows through the final ones the run holds a `control.Lease`
    with the `card:<id>` claim (`run_lease`), and the walk runs under `control.controlled`,
```

keeping the rest of that paragraph unchanged.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "claims_the_card or after_the_preflight or dead_claim_does_not_refuse or bound_before_the_first_journal_line or releases_its_claims_on_every_exit or card_run or run_card" -v`
Expected: PASS (new tests plus the existing `run_card` and live-control card tests)

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): run --card refuses a claimed card and leases it before the first record"
```

---

### Task 5: `LeaseLostError` reaches the envelope at exit 3

**Files:**
- Modify: `src/agent_manager/cli.py` `HANDLED` tuple and its docstring (currently lines 1078-1093; locate by `HANDLED: tuple[type[BaseException], ...] = (`)
- Test: `tests/test_cli.py` (append at end of file)

**Interfaces:**
- Consumes: `store_module.LeaseLostError` (a `BaseException`, message `"this process lost the lease of run '<run_id>': pid <pid> on <host> holds it now"`); `Store.take_lease` from a second `Store`.
- Produces: `cli.HANDLED` contains `store_module.LeaseLostError`.

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/test_cli.py`:

```python


@requires_git
@requires_brd
def test_a_lease_lost_mid_walk_is_an_envelope_at_exit_3(project, cards, monkeypatch):
    """The first phase lets a second process take the run's lease over; the
    next fenced write raises `LeaseLostError`, which the command renders."""
    taken: list[str] = []

    def thief_factory(*, store, run_id, story_id, card_id):
        inner = fake_runner()

        def runner(phase, context, rendered):
            if phase.name == "explore" and not taken:
                thief = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
                try:
                    thief.take_lease(
                        token="thief",
                        pid=1,
                        host="elsewhere",
                        now=datetime.now(timezone.utc),
                        is_live=lambda row: False,
                    )
                finally:
                    thief.close()
                taken.append(run_id)
            return inner(phase, context, rendered)

        return runner

    monkeypatch.setattr(cli, "default_runner_factory", thief_factory)

    result = _invoke(project, cards["subtask"])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    (run_id,) = taken
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "type": "LeaseLostError",
            "message": (
                f"this process lost the lease of run {run_id!r}:"
                " pid 1 on elsewhere holds it now"
            ),
        },
    }
    lease = _card_lease(project, run_id)
    assert lease is not None and lease.token == "thief"
    assert _loaded(project, run_id).status == "started"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_a_lease_lost_mid_walk_is_an_envelope_at_exit_3 -v`
Expected: FAIL on `assert result.exit_code == cli.EXIT_ERROR` — exit code is `1` because `CliRunner` caught the unhandled `LeaseLostError`

- [ ] **Step 3: Add `LeaseLostError` to `HANDLED`**

In `src/agent_manager/cli.py`, replace:

```python
HANDLED: tuple[type[BaseException], ...] = (
    CliError,
    board.BoardError,
    EngineError,
    ValueError,
    locks.LockTimeoutError,
)
```

with:

```python
HANDLED: tuple[type[BaseException], ...] = (
    CliError,
    board.BoardError,
    EngineError,
    ValueError,
    locks.LockTimeoutError,
    store_module.LeaseLostError,
)
```

and, in the docstring right below it, replace its last two lines:

```python
refusal, not a bug; nothing below the CLI catches it. Anything outside this
tuple is a bug in this program and should crash loudly with its stack intact.
```

with:

```python
refusal, not a bug; nothing below the CLI catches it. `store_module.LeaseLostError`
is in it because another process took this run's lease over mid-walk (spec X4):
the fence stopped every write, and the operator gets the envelope naming the new
holder. It is a `BaseException`, so it has to be listed by name. Anything outside
this tuple is a bug in this program and should crash loudly with its stack intact.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_a_lease_lost_mid_walk_is_an_envelope_at_exit_3 tests/test_cli.py::test_a_handled_error_from_a_milestone_run_is_an_envelope tests/test_cli.py::test_an_unhandled_error_from_a_milestone_run_crashes_loudly -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): render LeaseLostError as an envelope at exit 3"
```

---

### Task 6: Task-run resume refuses a claimed card, leases right after `Store.open`, and reports `took_over`

**Files:**
- Modify: `src/agent_manager/cli.py` `_resume_from_checkpoint` (currently lines 1434-1534; locate by `def _resume_from_checkpoint(`)
- Test: `tests/test_cli.py` (append at end of file)

**Interfaces:**
- Consumes: `refuse_claimed`, `run_lease` (Task 3); `control.card_claim` (Task 1); `control.Lease.displaced` (Task 2); test helpers `_crash_pygents`, `_resume_factory`, `_resume_card_run`, `_card_lease`, `_attempt_rows`, `_checkpoint_rows`, `_Forbidden`, `_reaped_pid` (Task 4), `_plant_lease`, `_claim_rows` (Task 3), `RESUME_KEYS`.
- Produces: resume payload key `"took_over": {"pid": int, "host": str, "heartbeat_at": str}` present only when a dead lease row was displaced; `RunIsLiveError` (C10 message) when the lease is lost to a live process between C10 and `take_lease`, with no orphan-attempt writes.

Decision recorded here: the spec says "same pattern" as `run_card` and "in place of the current `control.Lease(store)`". The resume's lease is entered right after `Store.open`, wrapping the checkpoint read and the orphan-attempt writes, so a lost race writes nothing (Review Focus 2) and the orphan writes are fenced. A checkpoint refusal inside the block releases the lease and claim on its way out.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python


@requires_git
@requires_brd
def test_resume_takes_over_a_dead_lease_and_says_so(project, cards):
    run_id = _crash_pygents(project, cards, "plan")
    dead = _reaped_pid()
    beat = datetime.now(timezone.utc)
    _plant_lease(
        project,
        run_id=run_id,
        token="crashed-life",
        pid=dead,
        heartbeat_at=beat,
        claims=(control.card_claim(cards["subtask"]),),
    )

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert payload["status"] == "done", payload
    assert payload["took_over"] == {
        "pid": dead,
        "host": HERE,
        "heartbeat_at": beat.isoformat(),
    }
    assert set(payload) == RESUME_KEYS | {"took_over"}
    assert _card_lease(project, run_id) is None
    assert _claim_rows(project) == []


@requires_git
@requires_brd
def test_a_resume_refuses_a_card_another_live_run_claims_and_writes_nothing(
    project, cards, monkeypatch
):
    run_id = _crash_pygents(project, cards, "plan")
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        project,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=now - timedelta(seconds=7),
        claims=(key,),
    )
    # `resume` passes no runner factory, so without this a missing refusal
    # would reach the real `dispatch.AgentRunner`.
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))
    before = (_attempt_rows(project), _checkpoint_rows(project), _runs_snapshot())

    result = runner.invoke(cli.app, ["resume", run_id, "--repo-dir", str(project)])

    assert result.exit_code == cli.EXIT_ERROR, result.output
    assert json.loads(result.stdout)["error"] == {
        "type": "ClaimedError",
        "message": (
            f"card {cards['subtask']} is being driven by run {OTHER_RUN_ID}"
            f" (pid {os.getpid()} on {HERE}, heartbeat 7s ago);"
            f" wait for it, or `am pause {OTHER_RUN_ID}`"
        ),
    }
    assert (_attempt_rows(project), _checkpoint_rows(project), _runs_snapshot()) == before
    assert _card_lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_resume_that_loses_the_lease_race_is_run_is_live_and_writes_nothing(
    project, cards, monkeypatch
):
    """Review Focus 2: C10 in `resume_run` passed, then another `am resume`
    took the lease; `_resume_from_checkpoint` must refuse before the orphan
    writes."""
    run_id = _crash_pygents(project, cards, "plan")
    now = datetime.now(timezone.utc)
    _freeze_clock(monkeypatch, now)
    _plant_lease(project, run_id=run_id, token="racer", heartbeat_at=now - timedelta(seconds=5))
    before = (_attempt_rows(project), _checkpoint_rows(project), store_module.Journal(run_id).read())

    with pytest.raises(cli.RunIsLiveError) as caught:
        _resume_card_run(project, run_id, _Forbidden("runner_factory"))

    assert str(caught.value) == (
        f"run {run_id} is still running in pid {os.getpid()} on {HERE}"
        " (heartbeat 5s ago); wait for it to exit,"
        f" or `am status {run_id}`"
    )
    assert (
        _attempt_rows(project),
        _checkpoint_rows(project),
        store_module.Journal(run_id).read(),
    ) == before
    lease = _card_lease(project, run_id)
    assert lease is not None and lease.token == "racer"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "takes_over_a_dead_lease or resume_refuses_a_card_another_live_run or loses_the_lease_race" -v`
Expected: FAIL — `KeyError: 'took_over'` for the takeover test; the claim test fails with `Failed: the milestone dry run reached cli.default_runner_factory` (no refusal, so the walk started); the race test gets a raw `agent_manager.store.LeaseHeldError` (not `RunIsLiveError`) after the orphan attempt was already rewritten.

- [ ] **Step 3: Add the preflight to `_resume_from_checkpoint`**

In `src/agent_manager/cli.py`, inside `_resume_from_checkpoint`, replace:

```python
    orphans = orphan_attempts(subtask)
    resumed = subtask.model_copy(update={"status": "started"})

    store = Store.open(root, run.id)
    try:
        checkpoint = store.latest_checkpoint(subtask.card_id)
        phase = checkpoint_resume_phase(checkpoint, card_id=subtask.card_id, run_id=run.id)
        for orphan, attempt in orphans:
            store.record_attempt(
                story.card_id,
                subtask.card_id,
                orphan.name,
                attempt.model_copy(update={"status": "harness_error"}),
            )
        # After every refusal, and inside the `try` that closes the store, so
        # the lease is released before `store.close()` on every exit (C2).
        with control.Lease(store) as lease:
            store.record_run(run.model_copy(update={"status": "started"}))
```

with:

```python
    orphans = orphan_attempts(subtask)
    resumed = subtask.model_copy(update={"status": "started"})
    claims = [control.card_claim(subtask.card_id)]
    # Read-only and before `Store.open` (X5); the run's own claims are not a
    # conflict, and `take_lease` below re-checks atomically.
    refuse_claimed(root, claims, run_id=run.id)

    store = Store.open(root, run.id)
    try:
        # Right after `Store.open` and inside the `try` that closes the store:
        # a lost race refuses before the orphan writes, every write below is
        # fenced, and the claims and lease are released before `store.close()`
        # on every exit, a checkpoint refusal included (C2, X5).
        with run_lease(store, claims=claims) as lease:
            checkpoint = store.latest_checkpoint(subtask.card_id)
            phase = checkpoint_resume_phase(
                checkpoint, card_id=subtask.card_id, run_id=run.id
            )
            for orphan, attempt in orphans:
                store.record_attempt(
                    story.card_id,
                    subtask.card_id,
                    orphan.name,
                    attempt.model_copy(update={"status": "harness_error"}),
                )
            store.record_run(run.model_copy(update={"status": "started"}))
```

(The remaining body of the `with` block — `record_story`, `record_subtask`, the `StopSignal`, the `asyncio.run(control.controlled(...))`, and the three final `record_*` calls — stays exactly as it is, at the same indentation.)

- [ ] **Step 4: Add `took_over` to the payload**

Still in `_resume_from_checkpoint`, replace:

```python
        return {
            "run_id": run.id,
            "card_id": subtask.card_id,
```

with:

```python
        payload: dict[str, Any] = {
            "run_id": run.id,
            "card_id": subtask.card_id,
```

and replace the end of that dict:

```python
            "discarded_attempts": [
                {"phase": orphan.name, "n": attempt.n} for orphan, attempt in orphans
            ],
        }
    finally:
        store.close()
```

with:

```python
            "discarded_attempts": [
                {"phase": orphan.name, "n": attempt.n} for orphan, attempt in orphans
            ],
        }
        if lease.displaced is not None:
            # A dead holder's lease was taken over (X5): say whose.
            payload["took_over"] = {
                "pid": lease.displaced.pid,
                "host": lease.displaced.host,
                "heartbeat_at": lease.displaced.heartbeat_at.isoformat(),
            }
        return payload
    finally:
        store.close()
```

Also update the docstring paragraph beginning `Live control (C11), as in \`run_card\`:` to read:

```python
    Live control (C11) and claims (X5), as in `run_card`: the card is refused
    before `Store.open` if another live run claims it, and from right after
    `Store.open` through the final rows this life of the run holds a fresh
    `control.Lease` with the `card:<id>` claim (`run_lease`); a dead holder it
    took over is reported under `took_over`. The walk runs under
    `control.controlled`. A pause parks it `stopped`; a cancel parks it and
    records the run `cancelled` (`card_run_status`).
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "takes_over_a_dead_lease or resume_refuses_a_card_another_live_run or loses_the_lease_race or pygents or resumed_card or resume" -v`
Expected: PASS (new tests plus every existing resume test, including `set(payload) == RESUME_KEYS` where no lease was displaced)

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): task-run resume leases with its card claim and reports took_over"
```

---

### Task 7: `am status` lists the live lease's claims; readers take nothing

**Files:**
- Modify: `src/agent_manager/cli.py` `control_view` (lines 282-312), `status_payload` fallback (line 332), `status_for` (currently lines 1305-1310; locate by `state = control_view(`)
- Test: `tests/test_cli.py` — update `test_the_status_payload_defaults_to_an_empty_control`, `test_status_of_a_run_with_no_lease_shows_an_empty_control`, `test_status_shows_the_lease_and_every_lifes_requests_in_seq_order`; append two new tests at end of file

**Interfaces:**
- Consumes: `store_module.held_claims(conn, run_id, token) -> list[ClaimRow]`; `control.lease_is_live`; `run_lease` (Task 3, patched out in the readers test); `_plant_lease` with `claims` (Task 3); `_record_for_logs`, `LOGS_RUN_ID`, `_dry_run`, `milestone_board`.
- Produces: `control_view(lease, requests, *, now, claims: Sequence[str] = ()) -> dict` with keys `lease`, `requests`, `claims` (a `list[str]`); `status_payload`'s default `control` is `{"lease": None, "requests": [], "claims": []}`.

- [ ] **Step 1: Update the three existing status tests to expect the key**

In `tests/test_cli.py`:

Replace:

```python
    assert cli.status_payload(_pure_run([]))["control"] == {"lease": None, "requests": []}
```

with:

```python
    assert cli.status_payload(_pure_run([]))["control"] == {
        "lease": None,
        "requests": [],
        "claims": [],
    }
```

In `test_status_of_a_run_with_no_lease_shows_an_empty_control`, replace:

```python
        assert json.loads(result.stdout)["data"]["control"] == {
            "lease": None,
            "requests": [],
        }
```

with:

```python
        assert json.loads(result.stdout)["data"]["control"] == {
            "lease": None,
            "requests": [],
            "claims": [],
        }
```

In `test_status_shows_the_lease_and_every_lifes_requests_in_seq_order`, replace the closing of the expected dict:

```python
                {
                    "command": "pause",
                    "requested_at": _at(-10).isoformat(),
                    "handled_at": None,
                },
            ],
        }
    assert (_controls(projection), _lease(projection)) == before
```

with:

```python
                {
                    "command": "pause",
                    "requested_at": _at(-10).isoformat(),
                    "handled_at": None,
                },
            ],
            "claims": [],
        }
    assert (_controls(projection), _lease(projection)) == before
```

- [ ] **Step 2: Write the new failing tests**

Append to the end of `tests/test_cli.py`:

```python


@pytest.mark.parametrize(
    "heartbeat_at, shown",
    [
        (CONTROL_NOW - timedelta(seconds=5), ["branch:m10/task-x", "card:card-1"]),
        (CONTROL_NOW - timedelta(seconds=31), []),
    ],
    ids=["live", "stale"],
)
def test_status_lists_the_claims_of_the_live_lease(projection, monkeypatch, heartbeat_at, shown):
    """A live lease's claims in key order; a stale lease's leftover claims are
    not shown (Review Focus 5). `status` stays read-only."""
    _freeze_clock(monkeypatch)
    _plant_run(projection)
    _plant_lease(
        projection,
        heartbeat_at=heartbeat_at,
        claims=("card:card-1", "branch:m10/task-x"),
    )
    before = (_lease(projection), _claim_rows(projection))

    result = runner.invoke(cli.app, ["status", CONTROL_RUN_ID, "--repo-dir", str(projection)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["control"]["claims"] == shown
    assert (_lease(projection), _claim_rows(projection)) == before


@requires_git
@requires_brd
def test_readers_never_take_a_lease_or_a_lock(project, milestone_board, monkeypatch):
    _record_for_logs(project, LOGS_RUN_ID)
    _plant_lease(
        project,
        run_id=LOGS_RUN_ID,
        token="reader-test",
        heartbeat_at=datetime.now(timezone.utc),
        claims=("card:card-1",),
    )
    before = (_card_lease(project, LOGS_RUN_ID), _claim_rows(project))

    def forbidden(name: str):
        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"a reader reached {name}")

        return refuse

    monkeypatch.setattr(store_module.Store, "take_lease", forbidden("Store.take_lease"))
    monkeypatch.setattr(locks.ProcessLock, "acquire", forbidden("ProcessLock.acquire"))
    monkeypatch.setattr(cli, "run_lease", forbidden("cli.run_lease"))

    status = runner.invoke(cli.app, ["status", LOGS_RUN_ID, "--repo-dir", str(project)])
    assert status.exit_code == 0, status.output
    assert json.loads(status.stdout)["data"]["control"]["claims"] == ["card:card-1"]

    listed = runner.invoke(cli.app, ["runs", "--repo-dir", str(project)])
    assert listed.exit_code == 0, listed.output

    logged = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(project)]
    )
    assert logged.exit_code == 0, logged.output

    previewed = _dry_run(project, "make the skeleton real")
    assert previewed.exit_code == 0, previewed.output

    assert (_card_lease(project, LOGS_RUN_ID), _claim_rows(project)) == before
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "empty_control or every_lifes_requests or lists_the_claims or readers_never_take" -v`
Expected: FAIL — the dict-equality tests fail because `control` has no `"claims"` key; `test_status_lists_the_claims_of_the_live_lease` and `test_readers_never_take_a_lease_or_a_lock` fail with `KeyError: 'claims'`.

- [ ] **Step 4: Add `claims` to `control_view` and the fallback**

In `src/agent_manager/cli.py`, replace the `control_view` signature and docstring opening:

```python
def control_view(
    lease: store_module.LeaseRow | None,
    requests: Sequence[store_module.ControlRow],
    *,
    now: datetime,
) -> dict[str, Any]:
    """C12's `control` key: the lease or `None`, and every life's requests in seq order.
```

with:

```python
def control_view(
    lease: store_module.LeaseRow | None,
    requests: Sequence[store_module.ControlRow],
    *,
    now: datetime,
    claims: Sequence[str] = (),
) -> dict[str, Any]:
    """C12's `control` key: the lease or `None`, every life's requests in seq
    order, and `claims`, the keys the live lease holds (X5; empty otherwise).
```

and at the end of the returned dict replace:

```python
            for row in requests
        ],
    }
```

with:

```python
            for row in requests
        ],
        "claims": list(claims),
    }
```

In `status_payload`, replace:

```python
        "control": {"lease": None, "requests": []} if control is None else control,
```

with:

```python
        "control": {"lease": None, "requests": [], "claims": []}
        if control is None
        else control,
```

- [ ] **Step 5: Read the live lease's claims in `status_for`**

In `src/agent_manager/cli.py`, inside `status_for`, replace:

```python
        state = control_view(
            store_module.read_lease(conn, wanted),
            store_module.control_requests(conn, wanted),
            now=_utcnow(),
        )
        return status_payload(run, state)
```

with:

```python
        lease = store_module.read_lease(conn, wanted)
        now = _utcnow()
        # Only a live lease's claims count (X5): a dead one's leftover rows
        # are anyone's to take, so they are not shown as held.
        claims = (
            [claim.key for claim in store_module.held_claims(conn, wanted, lease.token)]
            if lease is not None and control.lease_is_live(lease, now=now)
            else []
        )
        state = control_view(
            lease,
            store_module.control_requests(conn, wanted),
            now=now,
            claims=claims,
        )
        return status_payload(run, state)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "status or readers_never_take or runs_ or logs_" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): status lists the live lease's claims; pin that readers take nothing"
```

---

### Task 8: Full verification

**Files:** none changed unless a regression is found.

**Interfaces:**
- Consumes: everything above.
- Produces: a green `uv run pytest`.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no failures or errors (the opt-in e2e test stays deselected by the default configuration).

- [ ] **Step 2: Confirm the out-of-scope files are untouched**

Run: `git diff --stat m10/task-take-the-lease-a7ed6c11 -- src/agent_manager/store.py src/agent_manager/orchestrate.py`
Expected: no output.

- [ ] **Step 3: Commit (only if Step 1 required a fix)**

```bash
git add src/agent_manager/cli.py src/agent_manager/control.py tests/test_cli.py tests/test_control.py
git commit -m "fix: keep the full suite green with leases and claims"
```
