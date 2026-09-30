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
