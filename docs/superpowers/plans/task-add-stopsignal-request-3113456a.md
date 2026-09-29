<!-- task-pipeline: validated -->
# Task 1.2 (card 3113456a): StopSignal.request and the control watcher

Story ad03d386 "Control state and the park with a reason", milestone 9 (live control). This narrows the agreed live-control design (decisions C1-C12 in `2026-09-27-live-control-design.md`, on branch `docs/live-control`) to one subtask. It is not a new design.

## Base

Task 1.1 (b973aa1d) is done. This worktree already contains its store API: `store.LeaseRow`, `ControlRow`, `read_lease`, `control_requests`, `immediate`, `add_control`, and `Store.acquire_lease/beat/close_window/release_lease/pending_controls/mark_control_handled`. The implementation may use that API but must not modify `store.py`, `models.py`, `tests/test_store.py` or `tests/test_models.py`.

## Scope

Two files and their tests:

1. `src/agent_manager/runtime/stop.py` (modify).
2. `src/agent_manager/control.py` (new).

Out of scope, owned by later tasks: wiring in `orchestrate.py` (`run_milestone` under `Lease`+`controlled`, outcome precedence C6, `controlled_payload`, resume refusal), `cli.py` (`am pause`/`am cancel`, `request_control`, `CliError` subclasses, `am status`, `run_card`), and `README.md`. The existing `stop.trigger(...)` call sites and `StopSignal()` construction in `orchestrate.py` stay unchanged. Also out of scope: `--wait`, push delivery, pausing Integrate, per-story pause, and resetting board cards on cancel.

## Observable behavior

### `runtime/stop.py`

- Add `Command = Literal["pause", "cancel"]` and a new attribute `StopSignal.requested: Command | None = None`.
- `request(command) -> bool`:
  - Sets `triggered = True` and calls `.pause()` on every registered agent.
  - Never touches `primary`.
  - Returns `True` iff `requested` changed:
    - `None -> pause`, `None -> cancel` and `pause -> cancel` change it.
    - `pause` after `cancel`, or a repeat of the same command, does not.
  - Registered agents are paused on every call, as `trigger` does.
- `trigger(story_id)`:
  - Sets `primary` only when `primary is None`, and returns `True` exactly then. So the first escalation after a control-driven pause still becomes primary.
  - Otherwise unchanged: it sets `triggered` and pauses agents.
- `register` behaves as before: an agent registered after a `request` is paused at once because `triggered` is set.
- The module docstring is updated to mention `request`.
- The module still imports nothing from pygents.

### `control.py`

Imports: only `agent_manager.store`, `agent_manager.runtime.stop` and the stdlib. Never `cli`, `orchestrate` or `grafo` (M7 rule). No new dependency and no socket, fifo or signal handler (C1); SQLite is the only channel.

- **Constants:** `CONTROL_POLL_SECONDS = 1.0`, `HEARTBEAT_SECONDS = 5.0`, `LEASE_STALE_SECONDS = 30.0`.
- **`pid_alive(pid) -> bool`:** calls `os.kill(pid, 0)`. Returns `True` on success, `False` on `ProcessLookupError`, and `True` on `PermissionError`.
- **`lease_is_live(lease, *, now, host=socket.gethostname(), alive=pid_alive, stale_after=LEASE_STALE_SECONDS) -> bool` (C2):** the lease is live iff `now - lease.heartbeat_at <= stale_after` and either `lease.host != host` or `alive(lease.pid)`. The `<=` makes the boundary live.
- **`Lease(store, *, heartbeat=HEARTBEAT_SECONDS, clock=_utcnow, pid=None, host=None)` (context manager):**
  - `__enter__` calls `store.acquire_lease(token=uuid4().hex, pid=os.getpid() if pid is None else pid, host=socket.gethostname() if host is None else host, now=clock())` and exposes the token.
  - `beat()` calls `store.beat(token, clock())`. It is a public method, not a private helper: it is part of this task's committed interface (`2026-09-27-live-control-design.md` §5 lists `Lease.token`, `.beat()`, `.close_window()`), even though only the heartbeat thread calls it in this task's own code.
  - `__enter__` then starts a daemon thread that calls `beat()` every `heartbeat` seconds by waiting on a `threading.Event`, never `time.sleep`.
  - `close_window()` calls `store.close_window(token)`.
  - `__exit__` sets the event, joins the thread and calls `release_lease(token)`. It does this on a normal exit and on an exception, and does not swallow the exception.
- **`apply_pending(store, stop, token, *, clock=_utcnow) -> list[ControlRow]`:**
  - For each row of `store.pending_controls(token)`, in `seq` order, it calls `stop.request(row.command)` and then `store.mark_control_handled(row.seq, clock())`.
  - It returns the rows it applied.
  - It acts only on rows under `token` (C4), so rows under an old token are never applied.
- **`async watch(store, stop, token, *, interval, clock)`:**
  - Runs forever: `apply_pending` each tick, then `await asyncio.sleep(interval)`.
  - A `sqlite3.OperationalError` in a tick is swallowed, and the loop continues on the next tick.
  - Any other exception propagates.
- **`async controlled(work, *, store, stop, lease, interval=CONTROL_POLL_SECONDS, clock=_utcnow)`:** implement it as given verbatim in the card.
  - It races `work` against `watch` with `asyncio.wait(FIRST_COMPLETED)` and returns `work`'s result.
  - If the watcher finishes first, it cancels `work` and re-raises through `watcher.result()`.
  - On any exception it cancels `work`.
  - Its `finally` always cancels and awaits the watcher, then calls `lease.close_window()`, then runs one final `apply_pending` sweep. That order is required: no request can be accepted after the sweep.

Invariants this task must not break: a control never produces `escalated` or a `failed_phase`. A control never cancels a running phase, since it only pauses agents through the existing `StopSignal` + `ON_PAUSE` + `Parked` path, and no second stop path is added. `controlled` cancels `work` only on a watcher crash or an exception, never to honour a control.

## Error paths

- `pid_alive`: `PermissionError` counts as alive, and `ProcessLookupError` as dead.
- `watch`: a transient `sqlite3.OperationalError` (for example, the database is locked by a second process) is swallowed per tick. Any other error ends the watcher, and `controlled` then cancels `work` and re-raises it.
- `Lease`: an exception inside the `with` body still stops the heartbeat thread and releases the lease, and the exception propagates.
- `Store` methods called with a stale token are silent no-ops (Task 1.1 behavior), which `Lease`/`apply_pending` rely on.

## Tests

The placement rule is design spec §14 "Testing": pure functions, then steps (real temp git/DB/journal, no network, no harness), then adapters, then engine, then an end-to-end tier under `tests/e2e/` (opt-in). None of these tests go under `tests/e2e/`.

Tests must not sleep to prove ordering. Fakes block on `asyncio.Event`, `asyncio.Barrier` or `threading.Event`. A "second process" is a second `store.open_db` connection.

**Adaptation:** the card's excerpts use an `opened_store` fixture and `root`/`_send`/`_within`/`_until` helpers that do not exist. `tests/test_control.py` defines them locally, following the `repo` fixture + `store.Store.open(repo, RUN_ID)` pattern in `tests/test_store.py`. The implementer notes this adaptation.

`tests/runtime/test_stop.py`, extending the existing file. Pure-function / runtime tier: no I/O, `FakeAgent`.
- `test_request_pauses_registered_agents_and_leaves_primary_unset`
- `test_request_returns_whether_requested_changed` (covers None→pause, pause→pause, pause→cancel, cancel→pause no-op)
- `test_cancel_overrides_pause_and_pause_after_cancel_is_a_noop`
- `test_agent_registered_after_request_is_paused_at_once`
- `test_first_trigger_after_a_request_becomes_primary`
- The existing `test_first_trigger_is_primary` and the other existing tests still pass unchanged.

`tests/test_control.py`. Steps tier: real temp SQLite through `Store.open`, no network, no harness dispatch.
- `test_pid_alive_true_for_self_false_for_missing_pid_true_on_permission_error` (inject `os.kill` via monkeypatch for the missing and permission cases)
- `test_lease_is_live_within_stale_window_same_host_live_pid`
- `test_lease_is_stale_past_window` (and the exact-boundary case is live)
- `test_lease_on_same_host_with_dead_pid_is_not_live`
- `test_lease_on_other_host_is_live_regardless_of_pid`
- `test_lease_acquires_on_enter_and_releases_on_exit` (read back via `store.read_lease` on a second connection)
- `test_lease_releases_on_exception_and_reraises`
- `test_lease_heartbeat_thread_beats_and_stops_on_exit` (fake clock, small `heartbeat`, synchronize on a `threading.Event`, assert the thread is joined)
- `test_apply_pending_requests_each_row_in_order_and_marks_handled`
- `test_a_request_under_an_old_lease_token_is_never_applied`
- `test_watch_swallows_operational_error_and_keeps_polling`
- `test_controlled_returns_work_result_and_applies_a_request_sent_mid_run` (the request comes from a second connection, and `interval=0`)
- `test_controlled_cancels_work_and_reraises_when_the_watcher_crashes`
- `test_controlled_closes_the_window_then_sweeps_once_on_exit` (a request inserted before exit is applied, and after exit `accepting` is False)
- `test_controlled_cancels_work_on_exception_and_always_stops_the_watcher`
- `test_control_module_imports_no_cli_orchestrate_or_grafo`

Verification: `uv run pytest` passes for the whole suite, including `tests/e2e`, on this task alone.

Note: the exploration summary for this task was truncated at 8000 characters, partway through its file:line reference list. This spec relies only on the parts that arrived and on the code in this worktree.

---

# StopSignal.request and the Control Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `StopSignal` a control-driven `request(command)` that parks without claiming `primary`, and add `control.py` (lease, heartbeat, pending-request sweep, watcher, and the `controlled` race) on top of Task 1.1's store API.

**Architecture:** `runtime/stop.py` stays a lock-free, pygents-free object on the run's one event loop; `request` reuses the same "pause every registered agent" path as `trigger`, so a control parks through the existing `ON_PAUSE` + `Parked` path and adds no second stop path. `control.py` is a thin layer over `store.Store`: a `Lease` context manager claims the run and keeps a heartbeat thread, `apply_pending` turns this lease's unhandled `run_controls` rows into `StopSignal.request` calls, `watch` polls that forever on the event loop, and `controlled` races the caller's `work` against `watch`, then closes the window and runs one final sweep.

**Tech Stack:** Python 3.12, stdlib `asyncio`/`threading`/`sqlite3`/`socket`/`uuid`, pytest + pytest-asyncio (`asyncio_mode = "auto"`, so `async def test_...` needs no marker), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m9/task-add-stopsignal-request-3113456a/docs/superpowers/specs/task-add-stopsignal-request-3113456a-design.md` (reproduced verbatim above).

## Notes for the implementer (read first)

- **Base:** this branch (`m9/task-add-stopsignal-request-3113456a`) was cut from `m9/task-record-control-requests-b973aa1d`. Task 1.1's store API is on it (checked in `src/agent_manager/store.py`: `LeaseRow` at line 571, `read_lease` at 600, `ControlRow` at 613, `control_requests` at 640, `immediate` at 661, `add_control` at 681, `Store.acquire_lease/beat/close_window/release_lease/pending_controls/mark_control_handled` at 1140-1209). Do not modify `store.py`, `models.py`, `tests/test_store.py` or `tests/test_models.py`.
- **Adaptation (must be noted in the commit message of Task 3):** the card's test excerpts use an `opened_store` fixture and `root`/`_send`/`_within`/`_until` helpers that do not exist in the codebase. `tests/test_control.py` defines them locally, following the `repo` fixture + `store.Store.open(repo, RUN_ID)` pattern of `tests/test_store.py:30-37`.
- **`controlled` source:** the spec says to implement `controlled` "as given verbatim in the card". The card text and the live-control plan (`docs/superpowers/plans/2026-09-27-live-control.md`, on branch `docs/live-control`) are not in this worktree, and the upstream summaries given to the plan writer were truncated (the spec summary at 2000 characters, the exploration summary at 8000 characters, which suggests those stages over-ran their brief). Task 6 below reconstructs `controlled` from the spec's behavioural description. If you can read the card (`brd` card 3113456a) or that plan, compare it with Task 6's code. Where they differ only in form, keep the card's version. Where they differ in behaviour, stop and report the difference; do not guess.
- **Name shadowing:** `control.py` takes a parameter called `store`, so it imports `from agent_manager.store import ControlRow, LeaseRow, Store`, not the module. In tests, never name a local variable `store`, because `from agent_manager import store` is the module.
- **Timing rule:** no test sleeps to prove ordering. `_until` yields with `asyncio.sleep(0)` under an `asyncio.timeout` bound. `threading.Event.wait(timeout=...)` is only a failure bound. `interval=3600` only parks the watcher; it is cancelled, never waited out.

## File structure

- Modify: `src/agent_manager/runtime/stop.py` (whole file, lines 1-41). Adds `Command`, `requested` and `request`, and changes `trigger`'s "first" rule.
- Modify: `tests/runtime/test_stop.py`. Appends 6 tests (pure/runtime tier, next to the existing `StopSignal` tests).
- Create: `src/agent_manager/control.py`. Constants, `pid_alive`, `lease_is_live`, `Lease`, `apply_pending`, `watch`, `controlled`.
- Create: `tests/test_control.py`. Steps tier (real temp SQLite through `Store.open`), top level of `tests/`, mirroring `src/agent_manager/control.py` the way `tests/test_store.py` mirrors `store.py`. Not under `tests/e2e/`.

## Global Constraints

- `control.py` imports only `agent_manager.store`, `agent_manager.runtime.stop` and the stdlib; never `cli`, `orchestrate` or `grafo`.
- No new runtime dependency; no socket, fifo or signal handler (C1); SQLite is the only channel.
- `runtime/stop.py` imports nothing from pygents.
- `CONTROL_POLL_SECONDS = 1.0`, `HEARTBEAT_SECONDS = 5.0`, `LEASE_STALE_SECONDS = 30.0`.
- Heartbeat waits on a `threading.Event`, never `time.sleep`.
- A control never cancels a running phase; `controlled` cancels `work` only on a watcher crash or an exception.
- `watch`/`apply_pending` act only on rows under their own token (C4).
- `controlled`'s `finally` order: stop the watcher, then `lease.close_window()`, then one final `apply_pending` sweep.
- The `orchestrate.py` call sites of `stop.trigger(...)` and `StopSignal()` are not touched.
- Verification: `uv run pytest` green for the whole suite.

## Review Focus

1. `StopSignal.request` given an unknown command string (for example `"stop"` from a typo in a later caller): expected to raise `ValueError` before any side effect, not silently park the run with a bogus `requested`. Test added to Task 1 (`test_an_unknown_command_is_refused_before_any_effect`).
2. `pid_alive` given `pid <= 0` (a corrupt or zeroed lease row): `os.kill(0, 0)` signals the caller's own process group and succeeds, so a naive implementation reports a dead lease as live. Expected: `False` without calling `os.kill`. Test added to Task 2 (`test_pid_alive_is_false_for_non_positive_pids_without_signalling`).
3. The heartbeat meeting `sqlite3.OperationalError: database is locked` (a second process holding the DB): an unhandled error would silently kill the daemon thread, the lease would go stale after 30 s, and `am pause` would wrongly refuse a live run. Expected: the beat is skipped and the thread keeps beating. Test added to Task 3 (`test_lease_heartbeat_survives_an_operational_error`).
4. The same command sent twice, then `cancel` (an impatient user running `am pause` twice before `am cancel`): expected every row marked handled, in order, and `requested == "cancel"`. Covered by using `pause, pause, cancel` in Task 4's `test_apply_pending_requests_each_row_in_order_and_marks_handled`.
5. The task running `controlled` cancelled from outside (Ctrl-C or the supervisor unwinding): expected `work` to be cancelled and awaited, the watcher stopped, the window closed and the final sweep still run. Covered in Task 6's `test_controlled_cancels_work_on_exception_and_always_stops_the_watcher` (outer-cancel half).

---

### Task 1: `StopSignal.request` and the `primary is None` trigger rule

**Files:**
- Modify: `src/agent_manager/runtime/stop.py:1-41`
- Test: `tests/runtime/test_stop.py` (append after line 37)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Command = Literal["pause", "cancel"]`; `StopSignal.requested: Command | None`; `StopSignal.request(command: Command) -> bool` (raises `ValueError` on an unknown command); `StopSignal.trigger(story_id: str) -> bool` (True iff `primary` was `None`). Used by `control.apply_pending` (Task 4).

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_stop.py`, and change its import line (line 7) to `import pytest` plus the existing import:

```python
import pytest

from agent_manager.runtime.stop import StopSignal
```

Append at the end of the file:

```python
def test_request_pauses_registered_agents_and_leaves_primary_unset():
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent)
    stop.request("pause")
    assert agent.paused == 1 and stop.triggered
    assert stop.primary is None and stop.requested == "pause"
    stop.request("pause")
    assert agent.paused == 2


def test_request_returns_whether_requested_changed():
    stop = StopSignal()
    assert stop.request("pause") is True
    assert stop.request("pause") is False
    assert stop.request("cancel") is True
    assert stop.request("pause") is False
    assert stop.request("cancel") is False
    assert stop.requested == "cancel"


def test_cancel_overrides_pause_and_pause_after_cancel_is_a_noop():
    paused_first = StopSignal()
    paused_first.request("pause")
    paused_first.request("cancel")
    assert paused_first.requested == "cancel"

    cancelled_first = StopSignal()
    assert cancelled_first.request("cancel") is True
    assert cancelled_first.request("pause") is False
    assert cancelled_first.requested == "cancel"


def test_agent_registered_after_request_is_paused_at_once():
    stop, late = StopSignal(), FakeAgent()
    stop.request("pause")
    stop.register(late)
    assert late.paused == 1


def test_first_trigger_after_a_request_becomes_primary():
    stop = StopSignal()
    stop.request("pause")
    assert stop.trigger("A") is True and stop.trigger("B") is False
    assert stop.primary == "A" and stop.requested == "pause" and stop.triggered


def test_an_unknown_command_is_refused_before_any_effect():
    # Review Focus 1.
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent)
    with pytest.raises(ValueError, match="stop"):
        stop.request("stop")  # type: ignore[arg-type]
    assert agent.paused == 0 and not stop.triggered and stop.requested is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_stop.py -v`
Expected: the 3 existing tests PASS; the 6 new tests FAIL with `AttributeError: 'StopSignal' object has no attribute 'request'`.

- [ ] **Step 3: Write the implementation**

Replace the whole of `src/agent_manager/runtime/stop.py` with:

```python
"""The milestone's cooperative stop (supervisor-tree design T5, live control C3).

One `StopSignal` per run, living on the run's one event loop, so it takes no
lock. Two things fire it, and both pause every registered subtask agent:

- `trigger(story_id)` is an escalation. The first escalating story becomes
  `primary`, even when a control request fired the signal before it.
- `request(command)` is a live control (`am pause`/`am cancel`). It records
  `requested` and never touches `primary`. `cancel` overrides `pause`, and
  `pause` after `cancel` changes nothing.

`register` pauses an agent at once if the signal has already fired. A paused
pygents agent fires `ON_PAUSE` before its next turn, where
`runtime/checkpoint.py`'s `on_pause` saves `parked` and raises `Parked`.

Nothing here imports pygents: an agent is anything with a `.pause()`.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

Command = Literal["pause", "cancel"]


class StopSignal:
    def __init__(self) -> None:
        self.triggered = False
        self.primary: str | None = None
        self.requested: Command | None = None
        self._agents: set[Any] = set()

    def trigger(self, story_id: str) -> bool:
        """Pause every registered agent; True only for the first escalation, which becomes `primary`."""
        first = self.primary is None
        self.triggered = True
        if first:
            self.primary = story_id
        self._pause_all()
        return first

    def request(self, command: Command) -> bool:
        """Pause every registered agent for a control; True iff `requested` changed.

        `cancel` overrides `pause`; `pause` after `cancel` and a repeated
        command leave `requested` as it was. `primary` is never touched.
        """
        if command not in get_args(Command):
            raise ValueError(f"unknown control command: {command!r}")
        self.triggered = True
        self._pause_all()
        if self.requested == "cancel" or self.requested == command:
            return False
        self.requested = command
        return True

    def register(self, agent: Any) -> None:
        """Track `agent`; pause it at once if the signal already fired."""
        self._agents.add(agent)
        if self.triggered:
            agent.pause()

    def unregister(self, agent: Any) -> None:
        """Stop tracking `agent`. A no-op for an agent never registered."""
        self._agents.discard(agent)

    def _pause_all(self) -> None:
        for agent in list(self._agents):
            agent.pause()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_stop.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Run the callers' suites to confirm `trigger` is unchanged for them**

Run: `uv run pytest tests/test_orchestrate.py tests/runtime -q`
Expected: PASS (no run fires `request` yet, so `primary is None` is the same condition as `not triggered` for every existing caller).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/runtime/stop.py tests/runtime/test_stop.py
git commit -m "feat(stop): add StopSignal.request and make trigger's primary rule primary-is-None"
```

---

### Task 2: `control.py` constants, `pid_alive`, `lease_is_live`, and the import rule

**Files:**
- Create: `src/agent_manager/control.py`
- Test: `tests/test_control.py` (create)

**Interfaces:**
- Consumes: `agent_manager.store.LeaseRow` (frozen dataclass: `run_id, token, pid, host, acquired_at, heartbeat_at, accepting`).
- Produces: `CONTROL_POLL_SECONDS: float`, `HEARTBEAT_SECONDS: float`, `LEASE_STALE_SECONDS: float`; `pid_alive(pid: int) -> bool`; `lease_is_live(lease: LeaseRow, *, now: datetime, host: str = socket.gethostname(), alive: Callable[[int], bool] = pid_alive, stale_after: float = LEASE_STALE_SECONDS) -> bool`; `_utcnow() -> datetime`. The later `cli.py` task will use `lease_is_live` to refuse requests to dead runs.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_control.py`:

```python
"""Live control's lease, heartbeat and request watcher (live-control spec C1-C4).

Steps tier of spec §14: a real temporary SQLite database and journal opened
through `store.Store.open`, no network, no harness dispatch, so these run in
the default `uv run pytest` suite and not under `tests/e2e/`. A "second
process" is a second `store.open_db` connection.

No test sleeps to prove ordering: `_until` yields to the loop under a time
bound, and threads synchronise on a `threading.Event`.

Adaptation: the card's excerpts name an `opened_store` fixture and
`root`/`_send`/`_within`/`_until` helpers that did not exist. They are
defined here, after `tests/test_store.py`'s `repo` + `Store.open` pattern.
"""

from __future__ import annotations

import ast
import asyncio
import itertools
import os
import socket
import sqlite3
import sys
import threading
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

import pytest

from agent_manager import control, store
from agent_manager.runtime.stop import StopSignal

RUN_ID = "run-2026-09-27-01"
HEARTBEAT_THREAD = "am-lease-heartbeat"

T = TypeVar("T")


@pytest.fixture
def root(monkeypatch, tmp_path) -> Path:
    """A redirected data dir plus a stand-in for the project worktree."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "repo"
    project.mkdir()
    return project


@pytest.fixture
def opened_store(root) -> Iterator[store.Store]:
    st = store.Store.open(root, RUN_ID)
    try:
        yield st
    finally:
        st.close()


def _at(seconds: float) -> datetime:
    return datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def _lease_row(*, heartbeat_at: datetime, pid: int = 4242, host: str = "build-box") -> store.LeaseRow:
    return store.LeaseRow(
        run_id=RUN_ID,
        token="t1",
        pid=pid,
        host=host,
        acquired_at=_at(0),
        heartbeat_at=heartbeat_at,
        accepting=True,
    )


def _send(root: Path, token: str, command: str, at: datetime | None = None) -> store.ControlRow:
    """Insert one request from a second connection, as `am pause` would."""
    conn = store.open_db(root)
    try:
        with store.immediate(conn):
            return store.add_control(
                conn, RUN_ID, lease=token, command=command, requested_at=at or _at(0)
            )
    finally:
        conn.close()


def _read_lease(root: Path) -> store.LeaseRow | None:
    conn = store.open_db(root)
    try:
        return store.read_lease(conn, RUN_ID)
    finally:
        conn.close()


def _requests(root: Path) -> list[store.ControlRow]:
    conn = store.open_db(root)
    try:
        return store.control_requests(conn, RUN_ID)
    finally:
        conn.close()


async def _within(awaitable: Awaitable[T], limit: float = 5.0) -> T:
    """Await `awaitable`, failing the test instead of hanging past `limit`."""
    return await asyncio.wait_for(awaitable, limit)


async def _until(predicate: Callable[[], bool], limit: float = 5.0) -> None:
    """Yield to the loop until `predicate()` holds; a bound, not a sleep."""
    async with asyncio.timeout(limit):
        while not predicate():
            await asyncio.sleep(0)


def _heartbeat_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == HEARTBEAT_THREAD]


class FakeAgent:
    def __init__(self) -> None:
        self.paused = 0

    def pause(self) -> None:
        self.paused += 1


class Wrapped:
    """A real `Store` with some methods overridden; everything else passes through."""

    def __init__(self, inner: store.Store) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# -- pid_alive and lease_is_live (C2) -----------------------------------------


def test_pid_alive_true_for_self_false_for_missing_pid_true_on_permission_error(monkeypatch):
    assert control.pid_alive(os.getpid()) is True

    def missing(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(control.os, "kill", missing)
    assert control.pid_alive(4242) is False

    def forbidden(pid: int, sig: int) -> None:
        raise PermissionError(pid)

    monkeypatch.setattr(control.os, "kill", forbidden)
    assert control.pid_alive(4242) is True


def test_pid_alive_is_false_for_non_positive_pids_without_signalling(monkeypatch):
    # Review Focus 2: kill(0, 0) signals our own process group and succeeds.
    calls: list[int] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: calls.append(pid))
    assert control.pid_alive(0) is False
    assert control.pid_alive(-1) is False
    assert calls == []


def test_lease_is_live_within_stale_window_same_host_live_pid():
    lease = _lease_row(heartbeat_at=_at(0))
    assert control.lease_is_live(lease, now=_at(10), host="build-box", alive=lambda pid: True)


def test_lease_is_stale_past_window():
    lease = _lease_row(heartbeat_at=_at(0))
    assert control.lease_is_live(
        lease, now=_at(control.LEASE_STALE_SECONDS), host="build-box", alive=lambda pid: True
    )
    assert not control.lease_is_live(
        lease,
        now=_at(control.LEASE_STALE_SECONDS + 0.001),
        host="build-box",
        alive=lambda pid: True,
    )
    assert not control.lease_is_live(
        lease, now=_at(5), host="build-box", alive=lambda pid: True, stale_after=4.0
    )


def test_lease_on_same_host_with_dead_pid_is_not_live():
    seen: list[int] = []

    def dead(pid: int) -> bool:
        seen.append(pid)
        return False

    lease = _lease_row(heartbeat_at=_at(0), pid=4242)
    assert not control.lease_is_live(lease, now=_at(1), host="build-box", alive=dead)
    assert seen == [4242]


def test_lease_on_other_host_is_live_regardless_of_pid():
    lease = _lease_row(heartbeat_at=_at(0), host="other-box")
    assert control.lease_is_live(lease, now=_at(1), host="build-box", alive=lambda pid: False)
    assert not control.lease_is_live(
        lease, now=_at(31), host="build-box", alive=lambda pid: False
    )


def test_control_constants_match_the_design():
    assert control.CONTROL_POLL_SECONDS == 1.0
    assert control.HEARTBEAT_SECONDS == 5.0
    assert control.LEASE_STALE_SECONDS == 30.0


def test_control_module_imports_no_cli_orchestrate_or_grafo():
    tree = ast.parse(Path(control.__file__).read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "control.py uses absolute imports only"
            base = node.module or ""
            if base == "agent_manager":
                modules |= {f"agent_manager.{alias.name}" for alias in node.names}
            else:
                modules.add(base)
    ours = {name for name in modules if name.split(".")[0] == "agent_manager"}
    theirs = {name.split(".")[0] for name in modules} - {"agent_manager"}
    assert ours <= {"agent_manager.store", "agent_manager.runtime.stop"}
    assert theirs <= set(sys.stdlib_module_names) | {"__future__"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_control.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'control' from 'agent_manager'`.

- [ ] **Step 3: Write the implementation**

Create `src/agent_manager/control.py`:

```python
"""Live control of a running milestone: the lease and the request watcher (live control C1-C4).

A running `am` process claims its run with a `Lease`: a row in `run_leases`
under a fresh token, kept fresh by a daemon heartbeat thread. `am pause` and
`am cancel` in another process insert `run_controls` rows addressed to that
token. `watch`, on the run's event loop, turns this lease's pending rows into
`StopSignal.request` calls, which park the run through the existing
`ON_PAUSE` + `Parked` path. No control ever cancels a running phase.

SQLite is the only channel: no socket, fifo or signal handler (C1). This
module imports only `store`, `runtime.stop` and the stdlib; never `cli`,
`orchestrate` or `grafo`.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable
from datetime import datetime, timezone

from agent_manager.store import LeaseRow

CONTROL_POLL_SECONDS = 1.0
"""How often `watch` looks for new requests."""

HEARTBEAT_SECONDS = 5.0
"""How often a held `Lease` moves its `heartbeat_at`."""

LEASE_STALE_SECONDS = 30.0
"""A lease whose heartbeat is older than this is dead (C2)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pid_alive(pid: int) -> bool:
    """Whether process `pid` exists on this host.

    A non-positive pid is never alive: `os.kill(0, 0)` would signal this
    process's own group and succeed. `PermissionError` means the process
    exists but belongs to someone else, so it counts as alive.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lease_is_live(
    lease: LeaseRow,
    *,
    now: datetime,
    host: str = socket.gethostname(),
    alive: Callable[[int], bool] = pid_alive,
    stale_after: float = LEASE_STALE_SECONDS,
) -> bool:
    """C2: fresh heartbeat (boundary inclusive), and another host or a live pid here."""
    if (now - lease.heartbeat_at).total_seconds() > stale_after:
        return False
    return lease.host != host or alive(lease.pid)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_control.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/control.py tests/test_control.py
git commit -m "feat(control): add lease liveness (C2) and pid_alive"
```

---

### Task 3: the `Lease` context manager and its heartbeat thread

**Files:**
- Modify: `src/agent_manager/control.py` (imports, and append after `lease_is_live`)
- Test: `tests/test_control.py` (append)

**Interfaces:**
- Consumes: `Store.acquire_lease(*, token: str, pid: int, host: str, now: datetime) -> LeaseRow`, `Store.beat(token: str, now: datetime) -> None`, `Store.close_window(token: str) -> None`, `Store.release_lease(token: str) -> None`.
- Produces: `class Lease(store: Store, *, heartbeat: float = HEARTBEAT_SECONDS, clock: Callable[[], datetime] = _utcnow, pid: int | None = None, host: str | None = None)` with `__enter__() -> Lease`, `__exit__(...) -> None` (never swallows), attribute `token: str` (32 hex chars once entered), `beat() -> None`, `close_window() -> None`. The heartbeat thread is named `"am-lease-heartbeat"`. `controlled` (Task 6) calls `lease.token` and `lease.close_window()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_control.py`:

```python
# -- Lease ----------------------------------------------------------------------


def test_lease_acquires_on_enter_and_releases_on_exit(root, opened_store):
    with control.Lease(opened_store, pid=4242, host="build-box", clock=lambda: _at(0)) as lease:
        assert len(lease.token) == 32 and int(lease.token, 16) >= 0
        assert _read_lease(root) == store.LeaseRow(
            run_id=RUN_ID,
            token=lease.token,
            pid=4242,
            host="build-box",
            acquired_at=_at(0),
            heartbeat_at=_at(0),
            accepting=True,
        )
    assert _read_lease(root) is None
    assert _heartbeat_threads() == []

    with control.Lease(opened_store) as mine:
        row = _read_lease(root)
        assert row is not None
        assert (row.pid, row.host) == (os.getpid(), socket.gethostname())
    assert mine.token != lease.token


def test_lease_releases_on_exception_and_reraises(root, opened_store):
    with pytest.raises(RuntimeError, match="inside the run"):
        with control.Lease(opened_store):
            assert _read_lease(root) is not None
            raise RuntimeError("inside the run")
    assert _read_lease(root) is None
    assert _heartbeat_threads() == []


def test_lease_close_window_stops_accepting(root, opened_store):
    with control.Lease(opened_store) as lease:
        lease.close_window()
        row = _read_lease(root)
        assert row is not None and row.accepting is False


def test_lease_heartbeat_thread_beats_and_stops_on_exit(root, opened_store):
    ticks = itertools.count()
    beaten = threading.Event()

    class Counting(Wrapped):
        beats = 0

        def beat(self, token: str, now: datetime) -> None:
            self._inner.beat(token, now)
            Counting.beats += 1
            if Counting.beats >= 2:
                beaten.set()

    with control.Lease(Counting(opened_store), heartbeat=0.001, clock=lambda: _at(next(ticks))):
        assert beaten.wait(timeout=5.0)
        threads = _heartbeat_threads()
        assert len(threads) == 1 and threads[0].daemon
        row = _read_lease(root)
        assert row is not None and row.heartbeat_at > row.acquired_at == _at(0)
    assert not threads[0].is_alive()
    assert _read_lease(root) is None


def test_lease_heartbeat_survives_an_operational_error(root, opened_store):
    # Review Focus 3: a locked database must not kill the heartbeat thread.
    recovered = threading.Event()

    class Flaky(Wrapped):
        calls = 0

        def beat(self, token: str, now: datetime) -> None:
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise sqlite3.OperationalError("database is locked")
            self._inner.beat(token, now)
            recovered.set()

    ticks = itertools.count()
    with control.Lease(Flaky(opened_store), heartbeat=0.001, clock=lambda: _at(next(ticks))):
        assert recovered.wait(timeout=5.0)
        assert _heartbeat_threads()[0].is_alive()
    assert _heartbeat_threads() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_control.py -k lease_ -v`
Expected: the 5 new tests FAIL with `AttributeError: module 'agent_manager.control' has no attribute 'Lease'`; Task 2's `lease_is_live` tests still PASS.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/control.py`, replace the import block with:

```python
from __future__ import annotations

import os
import socket
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from types import TracebackType
from uuid import uuid4

from agent_manager.store import LeaseRow, Store
```

Append after `lease_is_live`:

```python
class Lease:
    """This process's claim on a run, held for the length of a `with` block (C2).

    `__enter__` takes a fresh token and starts a daemon heartbeat thread. That
    thread waits on a `threading.Event`, never `time.sleep`, so `__exit__`
    wakes it at once. `__exit__` stops and joins it and releases the lease on
    any exit, and never swallows the exception.
    """

    def __init__(
        self,
        store: Store,
        *,
        heartbeat: float = HEARTBEAT_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
        pid: int | None = None,
        host: str | None = None,
    ) -> None:
        self._store = store
        self._heartbeat = heartbeat
        self._clock = clock
        self._pid = pid
        self._host = host
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self.token = ""

    def __enter__(self) -> Lease:
        self.token = uuid4().hex
        self._store.acquire_lease(
            token=self.token,
            pid=os.getpid() if self._pid is None else self._pid,
            host=socket.gethostname() if self._host is None else self._host,
            now=self._clock(),
        )
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
        self._store.release_lease(self.token)

    def beat(self) -> None:
        """Move this lease's heartbeat to `clock()`."""
        self._store.beat(self.token, self._clock())

    def close_window(self) -> None:
        """Stop accepting control requests under this lease."""
        self._store.close_window(self.token)

    def _keep_beating(self) -> None:
        while not self._stopped.wait(self._heartbeat):
            try:
                self.beat()
            except sqlite3.OperationalError:
                # A second process holds the database; the next beat retries.
                continue
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_control.py -v`
Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/control.py tests/test_control.py
git commit -m "feat(control): add the Lease context manager and its heartbeat thread

tests/test_control.py defines the card's opened_store/root/_send/_within/_until
helpers locally (they did not exist), after tests/test_store.py's repo +
Store.open pattern."
```

---

### Task 4: `apply_pending` and the one-lease rule (C4)

**Files:**
- Modify: `src/agent_manager/control.py` (imports, and append after `Lease`)
- Test: `tests/test_control.py` (append)

**Interfaces:**
- Consumes: `Store.pending_controls(token: str) -> list[ControlRow]` (seq order, unhandled only), `Store.mark_control_handled(seq: int, now: datetime) -> None`, `StopSignal.request(command: Command) -> bool` (Task 1), `Lease` (Task 3).
- Produces: `apply_pending(store: Store, stop: StopSignal, token: str, *, clock: Callable[[], datetime] = _utcnow) -> list[ControlRow]`. Used by `watch` (Task 5) and `controlled` (Task 6).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_control.py`:

```python
# -- apply_pending (C4) --------------------------------------------------------


def test_apply_pending_requests_each_row_in_order_and_marks_handled(root, opened_store):
    # Review Focus 4: a repeated pause, then cancel.
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent)
    with control.Lease(opened_store) as lease:
        for command in ("pause", "pause", "cancel"):
            _send(root, lease.token, command)

        applied = control.apply_pending(opened_store, stop, lease.token, clock=lambda: _at(7))

        assert [(row.seq, row.command) for row in applied] == [
            (0, "pause"),
            (1, "pause"),
            (2, "cancel"),
        ]
        assert stop.requested == "cancel" and stop.primary is None
        assert agent.paused == 3
        assert [row.handled_at for row in _requests(root)] == [_at(7)] * 3
        assert control.apply_pending(opened_store, stop, lease.token) == []


def test_a_request_under_an_old_lease_token_is_never_applied(root, opened_store):
    stop = StopSignal()
    with control.Lease(opened_store) as old:
        old_token = old.token
    _send(root, old_token, "cancel")

    with control.Lease(opened_store) as lease:
        assert control.apply_pending(opened_store, stop, lease.token) == []

    assert stop.requested is None and not stop.triggered
    assert [(row.lease, row.handled_at) for row in _requests(root)] == [(old_token, None)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_control.py -k "apply_pending or old_lease" -v`
Expected: both FAIL with `AttributeError: module 'agent_manager.control' has no attribute 'apply_pending'`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/control.py`, add to the imports:

```python
from typing import cast

from agent_manager.runtime.stop import Command, StopSignal
```

and change the store import to:

```python
from agent_manager.store import ControlRow, LeaseRow, Store
```

Append after `Lease`:

```python
def apply_pending(
    store: Store,
    stop: StopSignal,
    token: str,
    *,
    clock: Callable[[], datetime] = _utcnow,
) -> list[ControlRow]:
    """Apply this lease's unhandled requests in `seq` order and mark each handled.

    Only rows addressed to `token` are read (C4), so a request sent to an
    earlier life of the run never reaches this one. Returns the rows applied.
    """
    applied: list[ControlRow] = []
    for row in store.pending_controls(token):
        stop.request(cast(Command, row.command))
        store.mark_control_handled(row.seq, clock())
        applied.append(row)
    return applied
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_control.py -v`
Expected: all 15 tests PASS (including `test_control_module_imports_no_cli_orchestrate_or_grafo`, now with `agent_manager.runtime.stop` imported).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/control.py tests/test_control.py
git commit -m "feat(control): apply a lease's pending requests to the StopSignal (C4)"
```

---

### Task 5: `watch`, the per-tick poller

**Files:**
- Modify: `src/agent_manager/control.py` (imports, and append after `apply_pending`)
- Test: `tests/test_control.py` (append)

**Interfaces:**
- Consumes: `apply_pending` (Task 4).
- Produces: `async def watch(store: Store, stop: StopSignal, token: str, *, interval: float = CONTROL_POLL_SECONDS, clock: Callable[[], datetime] = _utcnow) -> NoReturn`. It never returns: it ends only by cancellation or a non-`OperationalError` exception. `controlled` (Task 6) runs it as a task.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_control.py`:

```python
# -- watch ---------------------------------------------------------------------


async def test_watch_swallows_operational_error_and_keeps_polling(root, opened_store):
    class Locked(Wrapped):
        calls = 0

        def pending_controls(self, token: str) -> list[store.ControlRow]:
            Locked.calls += 1
            if Locked.calls <= 2:
                raise sqlite3.OperationalError("database is locked")
            return self._inner.pending_controls(token)

    stop = StopSignal()
    _send(root, "t1", "pause")
    task = asyncio.create_task(control.watch(Locked(opened_store), stop, "t1", interval=0))
    try:
        await _until(lambda: stop.requested == "pause")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert Locked.calls >= 3
    assert task.cancelled()

    class Broken(Wrapped):
        def pending_controls(self, token: str) -> list[store.ControlRow]:
            raise RuntimeError("not a lock")

    with pytest.raises(RuntimeError, match="not a lock"):
        await _within(control.watch(Broken(opened_store), StopSignal(), "t1", interval=0))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_control.py::test_watch_swallows_operational_error_and_keeps_polling -v`
Expected: FAIL with `AttributeError: module 'agent_manager.control' has no attribute 'watch'`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/control.py`, add `import asyncio` to the stdlib imports and change the typing import to `from typing import NoReturn, cast`. Append after `apply_pending`:

```python
async def watch(
    store: Store,
    stop: StopSignal,
    token: str,
    *,
    interval: float = CONTROL_POLL_SECONDS,
    clock: Callable[[], datetime] = _utcnow,
) -> NoReturn:
    """Apply this lease's requests every `interval` seconds, forever.

    A `sqlite3.OperationalError` (a second process holding the database) is
    swallowed and retried on the next tick; any other error ends the watcher.
    """
    while True:
        try:
            apply_pending(store, stop, token, clock=clock)
        except sqlite3.OperationalError:
            pass
        await asyncio.sleep(interval)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_control.py -v`
Expected: all 16 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/control.py tests/test_control.py
git commit -m "feat(control): add the watch poller that survives a locked database"
```

---

### Task 6: `controlled`, racing work against the watcher

**Files:**
- Modify: `src/agent_manager/control.py` (imports, and append after `watch`)
- Test: `tests/test_control.py` (append)

**Interfaces:**
- Consumes: `watch` (Task 5), `apply_pending` (Task 4), `Lease.token` and `Lease.close_window()` (Task 3).
- Produces: `async def controlled(work: Awaitable[T], *, store: Store, stop: StopSignal, lease: Lease, interval: float = CONTROL_POLL_SECONDS, clock: Callable[[], datetime] = _utcnow) -> T`. The later `orchestrate.py`/`cli.py` tasks wrap `supervise(...)` and `drive_subtask_async(...)` in it, inside `with Lease(store):`.

Before Step 3, see "`controlled` source" in the implementer notes at the top. Compare this code with the card's verbatim `controlled` if you can read it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_control.py`:

```python
# -- controlled ----------------------------------------------------------------


async def _blocked_forever(started: asyncio.Event, cancelled: list[bool]) -> str:
    started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        cancelled.append(True)
        raise
    return "unreachable"


async def test_controlled_returns_work_result_and_applies_a_request_sent_mid_run(
    root, opened_store
):
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent)
    with control.Lease(opened_store) as lease:

        async def work() -> str:
            _send(root, lease.token, "pause")
            await _until(lambda: stop.requested == "pause")
            return "done"

        result = await _within(
            control.controlled(work(), store=opened_store, stop=stop, lease=lease, interval=0)
        )
    assert result == "done"
    assert agent.paused >= 1 and stop.primary is None
    assert [row.handled_at is not None for row in _requests(root)] == [True]


async def test_controlled_cancels_work_and_reraises_when_the_watcher_crashes(opened_store):
    started, cancelled = asyncio.Event(), []

    class Exploding(Wrapped):
        exploded = False

        def pending_controls(self, token: str) -> list[store.ControlRow]:
            if started.is_set() and not Exploding.exploded:
                Exploding.exploded = True
                raise RuntimeError("boom")
            return self._inner.pending_controls(token)

    with control.Lease(opened_store) as lease:
        with pytest.raises(RuntimeError, match="boom"):
            await _within(
                control.controlled(
                    _blocked_forever(started, cancelled),
                    store=Exploding(opened_store),
                    stop=StopSignal(),
                    lease=lease,
                    interval=0,
                )
            )
    assert cancelled == [True]


async def test_controlled_closes_the_window_then_sweeps_once_on_exit(root, opened_store):
    events: list[str] = []

    class Recording(Wrapped):
        def pending_controls(self, token: str) -> list[store.ControlRow]:
            events.append("pending")
            return self._inner.pending_controls(token)

        def close_window(self, token: str) -> None:
            events.append("close_window")
            self._inner.close_window(token)

    recording, stop = Recording(opened_store), StopSignal()
    with control.Lease(recording) as lease:

        async def work() -> str:
            # The watcher's first tick has run and it is now parked on a long
            # interval, so only the final sweep can see this request.
            await _until(lambda: "pending" in events)
            _send(root, lease.token, "pause")
            return "done"

        assert (
            await _within(
                control.controlled(work(), store=recording, stop=stop, lease=lease, interval=3600)
            )
            == "done"
        )
        assert events == ["pending", "close_window", "pending"]
        assert stop.requested == "pause"
        row = _read_lease(root)
        assert row is not None and row.accepting is False
    assert [row.handled_at is not None for row in _requests(root)] == [True]


async def test_controlled_cancels_work_on_exception_and_always_stops_the_watcher(
    root, opened_store
):
    before = asyncio.all_tasks()
    stop = StopSignal()
    with control.Lease(opened_store) as lease:
        # Review Focus 5: the task running `controlled` is cancelled from outside.
        started, cancelled = asyncio.Event(), []
        outer = asyncio.create_task(
            control.controlled(
                _blocked_forever(started, cancelled),
                store=opened_store,
                stop=stop,
                lease=lease,
                interval=0,
            )
        )
        await _within(started.wait())
        _send(root, lease.token, "cancel")
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer
        assert cancelled == [True]
        assert asyncio.all_tasks() == before
        row = _read_lease(root)
        assert row is not None and row.accepting is False
        assert stop.requested == "cancel"

        async def failing() -> str:
            raise ValueError("work failed")

        with pytest.raises(ValueError, match="work failed"):
            await _within(
                control.controlled(failing(), store=opened_store, stop=stop, lease=lease, interval=0)
            )
        assert asyncio.all_tasks() == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_control.py -k controlled -v`
Expected: all 4 FAIL with `AttributeError: module 'agent_manager.control' has no attribute 'controlled'`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/control.py`, change the collections import to `from collections.abc import Awaitable, Callable` and the typing import to `from typing import NoReturn, TypeVar, cast`, and add below the imports:

```python
T = TypeVar("T")
```

Append after `watch`:

```python
async def controlled(
    work: Awaitable[T],
    *,
    store: Store,
    stop: StopSignal,
    lease: Lease,
    interval: float = CONTROL_POLL_SECONDS,
    clock: Callable[[], datetime] = _utcnow,
) -> T:
    """Run `work` with `watch` beside it and return `work`'s result.

    A control never cancels `work`; it only parks the run through `stop`.
    `work` is cancelled only when the watcher crashes (its error is re-raised)
    or on any other exception, including this task being cancelled. On every
    exit the watcher is stopped, then the window is closed, then one final
    sweep runs. In that order, no request can be accepted after the sweep.
    """
    work_task = asyncio.ensure_future(work)
    watcher = asyncio.create_task(
        watch(store, stop, lease.token, interval=interval, clock=clock)
    )
    try:
        done, _ = await asyncio.wait(
            {work_task, watcher}, return_when=asyncio.FIRST_COMPLETED
        )
        if work_task in done:
            return work_task.result()
        watcher.result()
        raise RuntimeError("the control watcher stopped without an error")
    except BaseException:
        work_task.cancel()
        await asyncio.gather(work_task, return_exceptions=True)
        raise
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        lease.close_window()
        apply_pending(store, stop, lease.token, clock=clock)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_control.py -v`
Expected: all 20 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/control.py tests/test_control.py
git commit -m "feat(control): add controlled, racing work against the watcher"
```

---

### Task 7: Full verification

**Files:** none changed.

**Interfaces:**
- Consumes: everything above.
- Produces: a green suite on this branch alone.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with no failures and no errors. The e2e test is deselected by `addopts` (`-m "not e2e"`), and `tests/e2e/` modules still collect cleanly. If the data-dir guard in `tests/conftest.py` prints `DATA-DIR GUARD FAILED`, a test opened a store without the `root` fixture. Fix that test; do not touch the guard.

- [ ] **Step 2: Confirm the scope boundary**

Run: `git diff --stat m9/task-record-control-requests-b973aa1d...HEAD`
Expected: only `src/agent_manager/runtime/stop.py`, `src/agent_manager/control.py`, `tests/runtime/test_stop.py`, `tests/test_control.py` and this plan/spec under `docs/superpowers/` are listed. `store.py`, `models.py`, `orchestrate.py`, `cli.py`, `README.md`, `tests/test_store.py` and `tests/test_models.py` are absent.
