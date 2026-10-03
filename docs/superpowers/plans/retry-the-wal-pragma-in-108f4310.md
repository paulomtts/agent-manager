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

---

# Retry the WAL pragma in `open_db` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `store.open_db` retry `PRAGMA journal_mode=WAL` on `database is locked` until `BUSY_TIMEOUT_SECONDS`. Then a second `am` process that races a writer on a fresh database waits for the lock instead of failing.

**Architecture:** Add a private helper `_enable_wal(conn)` in `src/agent_manager/store.py`, called only by `open_db` in place of the bare pragma. The helper loops on the pragma alone, sleeping between attempts with a 0.05 s pause that doubles up to a 0.5 s cap. Each pause is clamped to the time left before a deadline of `time.monotonic() + BUSY_TIMEOUT_SECONDS`, which is read at call time. Once no time is left, the helper re-raises the last lock error with a bare `raise`. Any other error propagates at once.

**Tech Stack:** Python 3 stdlib `sqlite3` and `time`, pytest, `uv`.

**Spec:** `docs/superpowers/specs/retry-the-wal-pragma-in-108f4310.md` (reproduced verbatim above this line).

## Global Constraints

- Only the statement `PRAGMA journal_mode=WAL` is retried. `sqlite3.connect`, `executescript(_SCHEMA)`, `_add_missing_columns` and `commit` run exactly once, in the same order, with the same arguments.
- Retry only on `sqlite3.OperationalError` whose `str(error)` contains `"database is locked"`. Anything else propagates at once, unchanged, with no sleep.
- First pause is about 50 ms. The backoff is capped, and its logic lives in `store.py`. `agent_manager.locks` is **not** imported.
- Deadline = `BUSY_TIMEOUT_SECONDS` on `time.monotonic()` from the first pragma attempt. The name is read at call time, never captured in a default argument. On expiry, use a bare `raise` (no wrapping, no chaining).
- `BUSY_TIMEOUT_SECONDS` stays `30.0`. No lock file is added. `locks.ProcessLock` is not used. `store.py` gains `import time`.
- The `open_db` docstring gains one sentence: the WAL switch is retried until `BUSY_TIMEOUT_SECONDS`, because SQLite does not call the busy handler for that pragma when another connection holds a write lock.
- Tests are in the "steps" I/O tier: real temporary SQLite files and no mocks of SQLite, in `tests/test_store.py`, using the `repo` fixture.
- Verification: `uv run pytest`. There is no lint and no typecheck.

## Review Focus

1. **Non-lock error from the pragma.** It must propagate on the first attempt, with no sleep. Pinned mock-free by `test_open_db_does_not_retry_an_error_other_than_database_is_locked`, which points the project DB path at a file of junk bytes. The pragma then raises `sqlite3.DatabaseError('file is not a database')` at once (verified on SQLite 3.53.4). With the default 30 s deadline, a too-broad `except sqlite3.DatabaseError` would retry for 30 s, and the test's `< 2 s` bound catches that. An `OperationalError` with a different message stays review-only (spec Review Focus 1). The diff must check the message.
2. **Retrying while nothing holds a lock (behavior 6).** In the uncontended case, `open_db` must never sleep. Pinned by `test_open_db_does_not_sleep_when_nothing_holds_a_lock`, which swaps `store.time.sleep` for a recorder and asserts it was never called. SQLite stays real.
3. **Deadline captured at import time.** Pinned by T2 (`test_open_db_reraises_database_is_locked_after_the_deadline`). It patches `store.BUSY_TIMEOUT_SECONDS` to 0.3 and asserts the call takes `< 5 s`, which fails if the 30 s default is used instead.
4. **Sleep overshooting the deadline.** The last pause is `min(pause, remaining)`. T2's `< 5` bound catches a gross overshoot. A tighter bound would be timing-flaky, so the clamp itself is checked by review.
5. **Loop wrapping more than the pragma** (DDL repeated). T1 asserts that the schema was applied once and works (`SELECT COUNT(*) FROM runs` → `0`). Review confirms that the diff's `while` body contains only the pragma.

---

## File Structure

- Modify: `src/agent_manager/store.py`
  - Imports (lines 14-17): add `import time` after `import threading`.
  - After `BUSY_TIMEOUT_SECONDS` and its docstring (lines 162-169): add `_WAL_RETRY_FIRST_PAUSE`, `_WAL_RETRY_PAUSE_CAP` and `_enable_wal(conn)`.
  - `open_db` (lines 200-227): add one docstring sentence, and replace `conn.execute("PRAGMA journal_mode=WAL")` (line 223) with `_enable_wal(conn)`.
- Modify: `tests/test_store.py`
  - Imports (lines 14-23): add `import time` after `import threading`.
  - After `test_open_db_connection_can_be_used_from_another_thread` (ends line 102): add `_hold_fresh_db_reserved` helper and four tests.

There is one task. The helper and its tests make one deliverable that a reviewer approves or rejects as a whole.

---

### Task 1: Retry the WAL pragma in `open_db`

**Files:**
- Modify: `src/agent_manager/store.py:14-17` (imports), `:162-169` (insert after), `:200-227` (`open_db`)
- Test: `tests/test_store.py:14-23` (imports), insert after line 102

**Interfaces:**
- Consumes: `store.open_db(root: Path) -> sqlite3.Connection`, `store.BUSY_TIMEOUT_SECONDS: float`, `paths.project_db_path(root: Path) -> Path`, the `repo` fixture in `tests/test_store.py`.
- Produces: `store._enable_wal(conn: sqlite3.Connection) -> None` (private, called only by `open_db`), plus the module constants `store._WAL_RETRY_FIRST_PAUSE = 0.05` and `store._WAL_RETRY_PAUSE_CAP = 0.5`. `open_db`'s public signature is unchanged.

- [ ] **Step 1: Add `import time` to the test module**

In `tests/test_store.py`, change the import block:

```python
import dataclasses
import json
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
```

to:

```python
import dataclasses
import json
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_store.py`, insert this block directly after the end of `test_open_db_connection_can_be_used_from_another_thread` (after its `conn.close()` at line 102), before `def test_open_db_creates_every_projection_table`:

```python
def _hold_fresh_db_reserved(repo: Path) -> sqlite3.Connection:
    """A second connection holding a RESERVED lock on a fresh, pre-WAL database.

    It must be `BEGIN IMMEDIATE`: SQLite fails `PRAGMA journal_mode=WAL` at once
    against a RESERVED lock without calling the busy handler, which is the race
    `open_db` retries. `BEGIN EXCLUSIVE` would make the busy handler run and so
    prove nothing.
    """
    path = paths.project_db_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    return holder


def test_open_db_waits_out_a_writer_holding_a_fresh_db_before_wal(repo):
    holder = _hold_fresh_db_reserved(repo)
    opened: list[sqlite3.Connection] = []
    errors: list[BaseException] = []

    def open_it() -> None:
        try:
            opened.append(store.open_db(repo))
        except BaseException as error:  # surfaced by the assertion below
            errors.append(error)

    worker = threading.Thread(target=open_it)
    try:
        worker.start()
        time.sleep(0.3)
        holder.execute("ROLLBACK")
    finally:
        holder.close()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert errors == []
    conn = opened[0]
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        conn.close()


def test_open_db_reraises_database_is_locked_after_the_deadline(repo, monkeypatch):
    monkeypatch.setattr(store, "BUSY_TIMEOUT_SECONDS", 0.3)
    holder = _hold_fresh_db_reserved(repo)
    try:
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            store.open_db(repo)
        elapsed = time.monotonic() - started
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert elapsed >= 0.3
    assert elapsed < 5


def test_open_db_does_not_retry_an_error_other_than_database_is_locked(repo):
    # Junk bytes make the WAL pragma raise DatabaseError('file is not a
    # database') at once. With the default 30 s deadline, a retry would show up
    # as a long wait.
    path = paths.project_db_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a database" * 200)

    started = time.monotonic()
    with pytest.raises(sqlite3.DatabaseError, match="file is not a database"):
        store.open_db(repo)

    assert time.monotonic() - started < 2


def test_open_db_does_not_sleep_when_nothing_holds_a_lock(repo, monkeypatch):
    # Behavior 6: uncontended, the pragma runs once and open_db never pauses.
    pauses: list[float] = []
    monkeypatch.setattr(store.time, "sleep", pauses.append)

    conn = store.open_db(repo)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()

    assert pauses == []
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v -k "waits_out_a_writer or reraises_database_is_locked or other_than_database_is_locked or does_not_sleep_when_nothing"`

Expected:
- `test_open_db_waits_out_a_writer_holding_a_fresh_db_before_wal`: FAIL. `errors == [OperationalError('database is locked')]`, raised at once by the unretried pragma.
- `test_open_db_reraises_database_is_locked_after_the_deadline`: FAIL on `assert elapsed >= 0.3`, because elapsed is about 0.0.
- `test_open_db_does_not_retry_an_error_other_than_database_is_locked`: PASS. It is a guard: master never retries anything, and it must stay green after the change.
- `test_open_db_does_not_sleep_when_nothing_holds_a_lock`: FAIL with `AttributeError: module 'agent_manager.store' has no attribute 'time'`.

If T1 or T2 passes here, stop: the holder is not producing the RESERVED race. Check that it uses `BEGIN IMMEDIATE` on a fresh file.

- [ ] **Step 4: Add `import time` to `store.py`**

In `src/agent_manager/store.py`, change:

```python
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
```

to:

```python
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
```

- [ ] **Step 5: Add the retry helper**

In `src/agent_manager/store.py`, directly after the `BUSY_TIMEOUT_SECONDS` docstring (the line ending `(multi-process X4, X9)."""`) and before `_ADDED_COLUMNS`, insert:

```python


_WAL_RETRY_FIRST_PAUSE = 0.05
"""Seconds `_enable_wal` waits after the first locked attempt; each later pause doubles."""

_WAL_RETRY_PAUSE_CAP = 0.5
"""The longest single pause `_enable_wal` takes between attempts."""


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switch `conn` to WAL mode, retrying while the database is locked.

    SQLite does not call the busy handler when this pragma meets another
    connection's RESERVED lock on a database still in rollback-journal mode; it
    fails at once with `database is locked`. So the pragma alone is retried,
    pausing 0.05 s and doubling up to 0.5 s, each pause clamped to the time left,
    until `BUSY_TIMEOUT_SECONDS` (read now, so tests can patch it) has passed
    since the first attempt. Then the last error is re-raised unchanged. Any
    other error propagates on the first attempt.
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_SECONDS
    pause = _WAL_RETRY_FIRST_PAUSE
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as error:
            if "database is locked" not in str(error):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(pause, remaining))
            pause = min(pause * 2, _WAL_RETRY_PAUSE_CAP)
```

(The blank lines at the top keep two blank lines between top-level definitions, matching the file.)

- [ ] **Step 6: Call the helper from `open_db` and extend its docstring**

In `src/agent_manager/store.py`, replace the whole `open_db` function:

```python
def open_db(root: Path) -> sqlite3.Connection:
    """Open the per-project projection, applying the schema idempotently.

    WAL mode is set before the schema so a reader never blocks the writer. Every
    `CREATE` is `IF NOT EXISTS`, so reopening an existing database never
    destroys what is already there. The only migration is additive:
    `_add_missing_columns` appends each column in `_ADDED_COLUMNS` that an older
    table lacks, as a nullable column. Existing rows keep their data and read
    the new column as NULL. It is a no-op on a database that already has the
    column, so opening the same database any number of times is safe.

    The connection may be used from any thread of the process that holds the
    run's lease, so `check_same_thread` is off; `Store` serialises that use
    behind its own lock. `BUSY_TIMEOUT_SECONDS` covers another process holding
    the database briefly; two processes never write one run, because every
    run write is fenced by the lease token (multi-process X4).
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

with:

```python
def open_db(root: Path) -> sqlite3.Connection:
    """Open the per-project projection, applying the schema idempotently.

    WAL mode is set before the schema so a reader never blocks the writer. The
    WAL switch is retried until `BUSY_TIMEOUT_SECONDS`, because SQLite does not
    call the busy handler for that pragma when another connection holds a write
    lock. Every `CREATE` is `IF NOT EXISTS`, so reopening an existing database
    never destroys what is already there. The only migration is additive:
    `_add_missing_columns` appends each column in `_ADDED_COLUMNS` that an older
    table lacks, as a nullable column. Existing rows keep their data and read
    the new column as NULL. It is a no-op on a database that already has the
    column, so opening the same database any number of times is safe.

    The connection may be used from any thread of the process that holds the
    run's lease, so `check_same_thread` is off; `Store` serialises that use
    behind its own lock. `BUSY_TIMEOUT_SECONDS` covers another process holding
    the database briefly; two processes never write one run, because every
    run write is fenced by the lease token (multi-process X4).
    """
    conn = sqlite3.connect(
        paths.project_db_path(root),
        timeout=BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    _enable_wal(conn)
    conn.executescript(_SCHEMA)
    _add_missing_columns(conn)
    conn.commit()
    return conn
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v -k "waits_out_a_writer or reraises_database_is_locked or other_than_database_is_locked or does_not_sleep_when_nothing"`

Expected: 4 passed. T2 should take about 0.3 s, and none of the four should take more than about 1 s.

- [ ] **Step 8: Run the whole store module (T3)**

Run: `uv run pytest tests/test_store.py -q`

Expected: all pass, including `test_open_db_connection_can_be_used_from_another_thread` unchanged.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest`

Expected: all pass. There is no lint or typecheck step.

- [ ] **Step 10: Collect E1 evidence (by hand; nothing added to the suite)**

Run:

```bash
for i in $(seq 10); do
  uv run pytest -q "tests/e2e/test_multi_process.py::test_two_milestones_with_different_prefixes_run_at_once_and_stay_apart" || echo "FAIL on iteration $i"
done
```

Expected: 10 passes and no `FAIL on iteration` line. Record the pass count (for example "E1: 10/10") in the implementer's report. If any iteration fails, read its traceback. A `database is locked` from `open_db` means the fix does not cover the race and must be investigated. A failure anywhere else is a separate defect and is out of scope for this card. Report it, don't fix it.

- [ ] **Step 11: Review the diff against the constraints**

Run: `git diff -- src/agent_manager/store.py`

Check:
- The only `while` loop added wraps `conn.execute("PRAGMA journal_mode=WAL")` alone.
- `executescript`, `_add_missing_columns` and `commit` still appear exactly once in `open_db`, in the same order.
- There is no `import` of `agent_manager.locks`.
- `BUSY_TIMEOUT_SECONDS = 30.0` is unchanged and is read inside `_enable_wal`, not in a default argument.
- The message check is `"database is locked" not in str(error)` → `raise`.
- Both re-raises are bare `raise`.

- [ ] **Step 12: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "fix: retry the WAL pragma in open_db until BUSY_TIMEOUT_SECONDS (108f4310)

SQLite fails PRAGMA journal_mode=WAL at once, without calling the busy
handler, when another connection holds a RESERVED lock on a database still
in rollback-journal mode. Retry only that pragma with a capped 0.05 s
backoff until the busy timeout.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Spec coverage map

| Spec item | Where |
|---|---|
| Behavior 1: only the pragma is retried | Step 5 (`_enable_wal` loop body), Step 6 (single call site), Step 11 check |
| Behavior 2: retry condition / other errors propagate | Step 5 (`except OperationalError` + message check), Review Focus 1 test |
| Behavior 3: ~50 ms pause, capped backoff, clamped, in `store.py`, no `locks` | Step 5 (`_WAL_RETRY_FIRST_PAUSE`, `_WAL_RETRY_PAUSE_CAP`, `min(pause, remaining)`) |
| Behavior 4: deadline from first attempt, read at call time, bare re-raise | Step 5, T2 |
| Behavior 5: success → wal, schema, busy timeout | T1, T3 (existing busy-timeout assertion) |
| Behavior 6: uncontended never sleeps | Review Focus 2 test |
| Behavior 7: no other change; `import time` | Step 4, Step 11 |
| Behavior 8: docstring sentence | Step 6 |
| T1, T2 | Step 2 |
| T3 | Step 8, Step 9 |
| E1 | Step 10 |
<!-- task-pipeline: validated -->
