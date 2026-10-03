# Retry the WAL pragma in `open_db` until `BUSY_TIMEOUT_SECONDS` — design

Card `108f4310-f2d4-49ee-a24b-6f713248c1e8`, parent story `ec8e1635-0395-4530-a1a0-9eb498ae75e8`.

## Problem

`store.open_db` (`src/agent_manager/store.py:200-227`) runs
`conn.execute("PRAGMA journal_mode=WAL")` at line 223, right after
`sqlite3.connect(..., timeout=BUSY_TIMEOUT_SECONDS)`. When two `am` processes
open the same project database at the same moment, and the database is still
in rollback-journal mode (a fresh database, before either process has switched
it to WAL), one process can hold a RESERVED lock (a `BEGIN IMMEDIATE`, or the
implicit write transaction of the other process's `executescript(_SCHEMA)`)
while the other runs the pragma. SQLite does **not** call the busy handler for
this lock transition. The pragma fails **at once** with
`sqlite3.OperationalError: database is locked` and does not wait for the
30-second busy timeout. `open_db` raises, and the losing `am` process fails.

Confirmed while writing this spec, on the system SQLite with Python's
`sqlite3`, using a fresh database file:

| second connection holds | `PRAGMA journal_mode=WAL` on a `timeout=2` connection |
|---|---|
| `BEGIN IMMEDIATE` (RESERVED) | `OperationalError('database is locked')` after **0.0 s** (no busy wait) |
| `BEGIN EXCLUSIVE` | `OperationalError('database is locked')` after 2.0 s (busy handler ran) |
| `BEGIN` + `SELECT` (SHARED) | `OperationalError('database is locked')` after 2.0 s (busy handler ran) |

The RESERVED case is the race this card fixes. It is why
`tests/e2e/test_multi_process.py::test_two_milestones_with_different_prefixes_run_at_once_and_stay_apart`
(`tests/e2e/test_multi_process.py:281-321`) fails about 2 times in 8 on master.

## Inherited constraints

- The projection is per-project SQLite in WAL mode, separate from the
  append-only journal: D5 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:70`)
  and §11 Concurrency (same file, lines 447-448: "a single writer for the
  journal, and SQLite in WAL mode"). After `open_db` returns, the connection is
  still in WAL mode.
- Several `am` processes may run on one repository at once (§11 status note,
  same file, line 440). The multi-process addendum it names is not in this
  worktree, so the X4/X9 invariants are taken from their restatement in
  `BUSY_TIMEOUT_SECONDS`'s docstring (`src/agent_manager/store.py:162-169`): the
  busy timeout covers another process holding the database briefly, so a
  second `am` process must wait for a short lock, not fail.
- Tests in the store module are the "steps" I/O tier, §14 Testing (same file,
  lines 507-523): real temporary database files, no mocks, in the default
  `uv run pytest` suite. `tests/test_store.py:1-11` states the same rule for this
  module.
- Verification is `uv run pytest` (project `CLAUDE.md`). There is no lint and
  no typecheck.

## Required behavior

1. **Only the WAL pragma is retried.** In `open_db`, only the statement
   `PRAGMA journal_mode=WAL` is retried. `sqlite3.connect`, `executescript(_SCHEMA)`,
   `_add_missing_columns` and `commit` keep running exactly once, in the same
   order, with the same arguments.
2. **Retry condition.** The pragma is retried only when it raises
   `sqlite3.OperationalError` and `"database is locked"` appears in `str(error)`.
   Any other exception, including an `OperationalError` with a different
   message, propagates at once, unchanged, with no sleep.
3. **Pause between attempts.** Each pause starts at about 50 ms. A small
   capped backoff is allowed. Mirroring `locks._backoff` (0.05 s doubling, cap
   0.5 s, `src/agent_manager/locks.py:64-65`) is fine, but the backoff must be
   written in `store.py`; `locks` is not imported. A pause never runs past the
   deadline: it lasts at most the time remaining.
4. **Deadline.** The deadline is `BUSY_TIMEOUT_SECONDS` measured on
   `time.monotonic()` from the **first** pragma attempt. `open_db` reads the
   module-level name `BUSY_TIMEOUT_SECONDS` when it is called, so tests can
   patch it with `monkeypatch.setattr(store, "BUSY_TIMEOUT_SECONDS", ...)`. When a
   locked attempt fails and no time is left, `open_db` re-raises that
   `OperationalError` unchanged (a bare `raise`; not wrapped and not chained to
   a new exception).
5. **Success.** As soon as one pragma attempt succeeds, `open_db` carries on as
   before and returns a connection whose `PRAGMA journal_mode` reads `wal`,
   with the schema applied and the busy timeout at `BUSY_TIMEOUT_SECONDS`.
6. **Uncontended cost.** When nothing holds a lock, the pragma runs once and
   `open_db` never sleeps.
7. **No change elsewhere.** The value of `BUSY_TIMEOUT_SECONDS` stays `30.0`.
   No lock file is added. `locks.ProcessLock` and its lock-ordering rules are
   not used, imported or changed. The retry is written inline in `open_db`, or
   in a private helper in `store.py` that only `open_db` calls. `store.py` gains
   `import time`.
8. The `open_db` docstring gains one sentence: the WAL switch is retried until
   `BUSY_TIMEOUT_SECONDS`, because SQLite does not call the busy handler for
   that pragma when another connection holds a write lock.

## Tests

All new tests go in `tests/test_store.py` and use the existing `repo` fixture
(`tests/test_store.py:33-39`).

### T1 — `test_open_db_waits_out_a_writer_holding_a_fresh_db_before_wal`
**Tier:** steps I/O (§14). It needs a real SQLite file and a second real
connection to produce a real lock. The behavior is SQLite's own locking, so a
mock would test nothing.

- Create the parent directory of `paths.project_db_path(repo)`, then open a
  plain `sqlite3.connect(path, isolation_level=None)` holder and run
  `BEGIN IMMEDIATE`. The database is fresh, so it is still in rollback-journal
  mode. **It must be `BEGIN IMMEDIATE`, not `BEGIN EXCLUSIVE`.** EXCLUSIVE
  makes SQLite call the busy handler, so the test would pass on master and
  prove nothing (see the table above).
- Call `store.open_db(repo)` in a `threading.Thread`. Collect the returned
  connection or the raised exception in lists, following
  `test_open_db_connection_can_be_used_from_another_thread`
  (`tests/test_store.py:78-102`).
- After a short wait (about 0.3 s), while the worker is still retrying, run
  `holder.rollback()` (or `holder.execute("ROLLBACK")`) and close the holder.
  Then `join` the worker with a timeout (about 10 s). Assert that it finished.
- Assert that there are no errors, and that the returned connection reads
  `PRAGMA journal_mode` as `"wal"` and can run `SELECT COUNT(*) FROM runs`
  (result `0`). Close it.
- **Red on master:** the worker records `OperationalError('database is locked')`
  at once.

### T2 — `test_open_db_reraises_database_is_locked_after_the_deadline`
**Tier:** steps I/O (§14), for the same reason as T1.

- `monkeypatch.setattr(store, "BUSY_TIMEOUT_SECONDS", 0.3)`.
- Hold the database with the same fresh-db `BEGIN IMMEDIATE` holder and
  never release it during the call.
- Time `store.open_db(repo)` with `time.monotonic()`. Assert that
  `pytest.raises(sqlite3.OperationalError, match="database is locked")` catches it.
- Assert that the elapsed time is `>= 0.3` (the deadline was honoured, so the
  retry really ran) and `< 5` (it did not fall back to the 30 s default).
- Roll back and close the holder in a `finally`.
- **Red on master:** the error is raised but the elapsed time is about 0 s, so
  the lower-bound assertion fails. That lower bound is what proves the retry
  loop exists.

### T3 — the existing suite stays green
`test_open_db_connection_can_be_used_from_another_thread` and every other
`open_db` caller must pass unchanged. This shows that the uncontended path
(behavior 6) and the busy-timeout assertion
(`tests/test_store.py:99-101`) are untouched.

### E1 — evidence the real race is gone (not a new test)
**Tier:** existing end-to-end multi-process test, run by hand. The race needs
two real `am` processes, so it cannot be a unit test. Nothing is added to the
suite: no pytest-repeat dependency and no new marker. Run it in a shell loop:

```bash
for i in $(seq 10); do
  uv run pytest -q "tests/e2e/test_multi_process.py::test_two_milestones_with_different_prefixes_run_at_once_and_stay_apart" || echo "FAIL on iteration $i"
done
```

Expected: 10 passes and no `FAIL` line. Record the pass count in the
implementer's report. On master the same loop fails about 2 times in 8.

## Review Focus (hand-off to the planner)

These are inputs the tests above do not exercise:

1. **Non-lock `OperationalError` from the pragma** (for example `disk I/O error`).
   It must propagate on the first attempt, with no sleep and no retry. This is
   checked by review against behavior 2. It is not tested because no mock-free
   way exists to make the pragma fail with that error.
2. **Sleep overshooting the deadline.** The last pause is clamped to the time
   remaining, so the total time is about `BUSY_TIMEOUT_SECONDS` plus one
   attempt. T2's `< 5` bound catches a gross overshoot.
3. **Deadline read at import time.** If the implementation captures
   `BUSY_TIMEOUT_SECONDS` in a default argument or a module constant, T2's
   monkeypatch has no effect and T2 takes 30 s. The name must be read inside the
   call.
4. **Retrying more than the pragma**, for example a loop around
   `executescript`. That breaks behavior 1 and repeats DDL. The diff must show
   the loop around the pragma only.
5. **A holder that is busy the other way**: an EXCLUSIVE or SHARED holder that
   the busy handler already waits out. Behavior is unchanged: the busy handler
   waits up to `BUSY_TIMEOUT_SECONDS` inside one attempt, and then the retry
   deadline has expired, so that error is re-raised with no extra round. No
   test is needed.

## Out of scope

- Retrying any other statement in `open_db` or anywhere in `store.py`.
- New lock files. Any use of `locks.ProcessLock`, `locks._flock` or the lock
  ordering rules.
- Changing the value of `BUSY_TIMEOUT_SECONDS`.
- Closing the connection when `open_db` fails. Today it leaks on any failure
  after `connect`, and this card does not change that.
- Making the e2e loop a permanent test, or adding pytest-repeat.
- Any work on the sibling cards under story `ec8e1635`.
