# Honour pause and cancel in `--card` runs (card 9f5467e3)

Subtask of story 09a1fc18 "Honour pause and cancel in milestone and card runs" (milestone bdc5838b). Narrows live-control design C11 (`docs/superpowers/specs/2026-09-27-live-control-design.md:170-176`, on branch `docs/live-control`) and plan Task 2.3 (`docs/superpowers/plans/2026-09-27-live-control.md:408-421`). No new design here. Builds on the m9 branch chain (base: sibling df0e9d20's branch), where `control.py`, `StopSignal.request`, the store's lease/control tables, `am pause`/`am cancel` and `resume_run`'s C9/C10 refusals already exist. Read the code as built before editing; plan line numbers are from master@6d69e3a.

## Scope

In scope, and only this:

- `src/agent_manager/cli.py`: `run_card` (~793) and `_resume_from_checkpoint` (~1394).
- Their tests in `tests/test_cli.py`.

Out of scope (owned by done siblings, do not touch): `orchestrate.run_milestone`, `controlled_payload`, `resumable_milestone_run` (0e1edf31); `request_control`, the `pause`/`cancel` commands, `status_payload`'s `control`, `resume_run`'s cancelled/live refusals (df0e9d20). `control.py`, `runtime/stop.py`, `store.py` and `models.py` are reused as-is. `drive_subtask` (sync) stays unchanged for any other caller.

## Behaviour

Both functions:

- Gain a keyword `control_interval: float = control.CONTROL_POLL_SECONDS`.
- Keep every existing pre-store refusal and ordering (board reads before a run id exists; `checkpoint_resume_phase` refusal before the first write; orphan attempts marked `harness_error`; run/story/subtask recorded `started`).
- Hold `with control.Lease(store) as lease:` around the walk and the final records (the plan places it right after the `started` records; wrapping from the `started` `record_run` on is equivalent and acceptable). The lease is released on every exit, including when the walk raises.
- Replace the `drive_subtask(...)` call with `stop = StopSignal()` and `asyncio.run(control.controlled(drive_subtask_async(..., stop=stop), store=store, stop=stop, lease=lease, interval=control_interval))`, passing the same arguments as today (`resume_from=checkpoint` in `_resume_from_checkpoint`). This mirrors `orchestrate.run_milestone`'s wiring.
- Final records: run status is `"cancelled"` when `stop.requested == "cancel"`, else `summary.status`. Story and subtask rows always get `summary.status` (a cancelled run's subtask and story stay `stopped` as the park left them). The payload's `"status"` is the run's status. All other payload keys are unchanged.

Resulting outcomes:

- Pause while a phase runs: the running phase finishes (no phase is cancelled), the engine parks before the next phase through `ON_PAUSE` + `Parked`, the newest checkpoint is `parked`, run/story/subtask are `stopped`, payload `status == "stopped"`, exit 0. `am resume <run-id>` continues from the parked phase via `select_resumable` and can end `done`.
- Cancel: same park; run `cancelled`, story/subtask `stopped`, payload `status == "cancelled"`, exit 0. `am resume` refuses it (exit 3, existing `NotResumableError` from df0e9d20).
- No request: behaviour and payload identical to today.
- A control never yields `escalated` or `failed_phase`. If the subtask escalates and a cancel was also applied, the run is `cancelled` (C6 precedence) and exits 0; with only a pause, `summary.status` stands (`escalated` -> exit 1).

Exit codes are unchanged: the existing mapping keys off `payload["status"] == "escalated"` (cli.py ~1231, ~1612), so `stopped` and `cancelled` exit 0 and `escalated` exits 1; confirm it still holds with the new run status. CLI envelope unchanged (`{"ok": true, "data": ...}`, errors `{"ok": false, "error": {"type","message"}}` exit 3, `--pretty`).

## Constraints

- SQLite is the only control channel; no new dependency, socket, fifo or signal handler. No schema, CHECK or journal changes; lease/control rows are never journalled.
- The only stop mechanism is `StopSignal` + `ON_PAUSE` + `Parked`; never cancel a running phase to honour a control, and nothing runs past the park.
- `control.py` must not import `cli`; `cli.py` already imports `control`. Only `orchestrate.py` imports grafo.

## Error paths

- Walk raises (e.g. `EngineError`): `controlled` cancels the walk, closes the window, sweeps, and re-raises; the `Lease` context releases the lease; the store is closed by the existing `finally`; the error surfaces as today. No final status rows are written by this path beyond what exists today.
- Watcher `sqlite3.OperationalError` is retried inside `control.watch`; nothing new to handle here.

## Tests

All in `tests/test_cli.py` (default suite tier: fake runner factories, temporary git repos/boards, no real `claude`), per the repo's placement rule (`pyproject.toml` excludes `-m e2e`; `tests/e2e/` is reserved for the opt-in real-`claude` test). Use the existing `runner_factory` fake-runner pattern and the `requires_git`/`requires_brd` markers. No sleeps for ordering: block fakes on `threading.Event`/`asyncio.Event`; the "second process" is a second `store.open_db` connection that reads the token with `read_lease` and inserts the request (or calls the `am pause`/`am cancel` path). A small `control_interval` may be passed; the fake's first agent phase sends the request, then waits until the stop has fired (e.g. an object with `.pause()` registered on the stop that sets an event) without blocking the loop the watcher runs on.

1. `run --card` paused mid-phase: payload `status == "stopped"`, exit 0; run/story/subtask rows `stopped`; newest checkpoint `parked`; the second phase was not dispatched. Tier: default (`tests/test_cli.py`).
2. `am resume <run-id>` of that paused run continues from the parked phase and ends `done`, exit 0. Tier: default.
3. `run --card` cancelled mid-phase: payload `status == "cancelled"`, exit 0; run row `cancelled`, story/subtask `stopped`; `am resume` then exits 3. Tier: default.
4. Lease released on every exit: after a normal `--card` run `read_lease` is `None`; also after a run whose walk raises `EngineError`. Tier: default.
5. `_resume_from_checkpoint` honours a pause too: a resumed task run paused mid-phase ends `stopped` with a `parked` checkpoint and releases its lease. Tier: default.
6. Regression: an uncontrolled `--card` run's payload and exit code are unchanged (existing tests stay green). Tier: default.

Verification: `uv run pytest` (whole suite, including `tests/e2e` default-unmarked tests) green. No typecheck or lint command.
