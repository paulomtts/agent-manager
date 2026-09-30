# Park milestone runs on pause and close them on cancel (card 0e1edf31)

Task 2.1 of the live-control plan (`docs/superpowers/plans/2026-09-27-live-control.md`, design `docs/superpowers/specs/2026-09-27-live-control-design.md`, both on branch `docs/live-control`). Parent story 09a1fc18 "Honour pause and cancel in milestone and card runs". This spec narrows the agreed design to `run_milestone` only. It does not add to it.

## Starting point

This worktree already contains Task 1.1's work (`models.Status` with `"cancelled"`, and the store's lease/control tables and methods) and Task 1.2's work: `src/agent_manager/control.py` (`Lease`, `apply_pending`, `watch`, `controlled`, `CONTROL_POLL_SECONDS`), `runtime.stop.Command` / `StopSignal.request` / `StopSignal.requested`. `LaneKind` is `Literal["done", "escalated", "stopped", "pending"]` (orchestrate.py:88). Line references below are from this worktree and match master @ 6d69e3a. The implementer should re-read the code before editing and record in the task result anything that has moved.

## Scope

Only `src/agent_manager/orchestrate.py` and `tests/test_orchestrate.py` change. The following belong to other cards and stay out of this one:

- `cli.py` pause/cancel commands, the new error types, `status_payload`'s `control` key and `resume_run` refusals belong to df0e9d20.
- `run_card` and `_resume_from_checkpoint` belong to 9f5467e3.

## Behaviour

1. **Lease around the run.** In `run_milestone` (line 1244), everything from `store.record_run(run_record)` (line 1349) through the final `store.record_run(...)` (lines 1392/1416/1419) and its return sits inside `with control.Lease(store) as lease:`.
   - The `Lease` block sits inside the existing `try: ... finally: store.close()` (lines 1340/1434), so the lease is released before the store closes.
   - Every refusal, including `resume_checkpoints`'s, still happens before `store.record_run`, in the M7 order.
2. **Controlled supervise.** `asyncio.run(supervise(...))` (lines 1358-1378) becomes `asyncio.run(control.controlled(supervise(...), store=store, stop=stop, lease=lease, interval=control_interval))`. The existing `stop = StopSignal()` is the one passed in, so there is no second stop path. The park stays milestone 7's: `StopSignal`, `ON_PAUSE` and `Parked`. A running phase is never cancelled to honour a control.
3. **New keyword.** `run_milestone` gains `control_interval: float = control.CONTROL_POLL_SECONDS`. Update its docstring to match.
4. **Outcome precedence (C6).** Once `controlled(...)` returns and `completed`, `warnings` and `built_bases` have been gathered as today, the first matching branch wins:
   1. If `stop.requested == "cancel"`: record the run `cancelled` and return `report(controlled_payload(run_id, "cancel", outcomes, warnings))`.
   2. If any outcome is `escalated`: keep today's branch (record `escalated`, `escalated_payload`). When `stop.requested == "pause"`, also set `payload["control"] = "pause"` before calling `report`.
   3. If `stop.requested == "pause"`: record the run `stopped` and return `report(controlled_payload(run_id, "pause", outcomes, warnings))`.
   4. Otherwise, run today's Integrate path unchanged (lines 1396-1433).

   A paused or cancelled run never runs Integrate in that invocation. A control never produces `escalated` or a `failed_phase`. Every branch goes through `report(...)`, which adds `bases` and `resumed` on the same terms as today.
5. **`controlled_payload(run_id: str, command: Command, outcomes: Sequence[LaneOutcome], warnings: list[str]) -> dict[str, Any]`** (C12) is a new pure function placed beside `escalated_payload`. `Command` is imported from `agent_manager.runtime.stop`. Its keys, in this order:
   - `"paused": True` or `"cancelled": True`
   - `run_id`
   - `stopped`: `{story, subtask, before_phase}` rows for `stopped` outcomes, in census order. This is the same row shape as `escalated_payload`.
   - `completed`: every outcome's `completed` subtasks, in wave order. Unlike `escalated_payload`, this is not limited to stopped lanes.
   - `pending`: the story ids of `pending` outcomes.
   - `warnings`
   - `"resume": f"am resume {run_id}"`, only on pause.
   - `escalations`: only on cancel, and only when there are escalated outcomes. Rows use the `also_escalated` shape `{level, story, subtask, failed_phase, detail}`, with the primary escalation first. The primary is picked as `escalated_payload` picks it: the outcome with `primary`, falling back to the first in census order.

   It never has an `escalated` key.
6. **Resume guard.** `resumable_milestone_run` (line 504) refuses a `cancelled` run with `cli.NotResumableError(f"run {run_id} was cancelled; start new work with am run --milestone")`.
   - Refusal order (C9) is unknown run, then wrong workflow, then cancelled/done. The cancelled check comes immediately before the `done` check.
   - Update the docstring's list of refusals.

## Constraints

- No new runtime dependency.
- No socket, fifo or signal handler. The only channel is per-project SQLite (C1).
- `control.py` still imports neither `cli` nor `orchestrate`. Only `orchestrate.py` imports `grafo`.
- No schema change: no new column and no CHECK change.

## Error paths

- A crash in `supervise`, or in the watcher, still propagates. `controlled` cancels the work, closes the window and runs its final sweep. The `Lease` releases on exit, then `store.close()` runs. A crash does not record `cancelled` or `stopped`.
- A refusal before `record_run`, such as a checkpoint saved under another workflow, is raised before any lease is taken.

## Tests

The test-placement rule is design spec §14 "Testing" (pure functions / steps / adapters / engine / opt-in e2e), plus live-control §7. `run_milestone` tests use a fake `drive` or harness, so they are in the **engine** tier. `controlled_payload` is a **pure-function** tier test. `resumable_milestone_run` reads a temp SQLite, so it is in the **steps** tier. All of these go in `tests/test_orchestrate.py` and none go under `tests/e2e/`.

Rules for all tests:
- No sleeps to prove ordering. Fakes block on `asyncio.Event`, `asyncio.Barrier` or `threading.Event`.
- `run_milestone` is always called with `control_interval=0`.
- A "second process" that inserts a control row is a second `store.open_db` connection, addressed to the live lease's token.

Tests:
- `test_controlled_payload_on_pause_lists_stopped_completed_pending_and_the_resume_hint` (pure).
- `test_controlled_payload_on_cancel_has_no_resume_and_lists_escalations_primary_first` (pure).
- `test_controlled_payload_on_cancel_without_escalations_omits_escalations_and_never_has_escalated` (pure).
- `test_a_paused_milestone_parks_records_stopped_and_skips_integrate` (engine). The fake blocks mid-subtask; a pause row is inserted; the run records `stopped`; the payload has `paused` and `resume`; the Integrate recorder is not called.
- `test_a_pause_applied_after_the_last_lane_already_finished_still_skips_integrate` (engine). Every lane finishes before the pause row lands, so only `controlled`'s final sweep (not a mid-run tick) applies it. The run still records `stopped`, the payload's `stopped` list is empty, and the Integrate recorder is not called (C6's "case 3 applies even when every lane had already finished").
- `test_a_lane_waiting_for_a_slot_ends_stopped_on_a_pause` (engine). With `max_concurrent` lower than the number of ready lanes, a pause reaches a lane that never took its slot; it ends `stopped` without ever calling the driver.
- `test_a_cancelled_milestone_records_cancelled_and_skips_integrate` (engine).
- `test_cancel_after_pause_wins_and_records_cancelled` (engine).
- `test_an_escalation_under_pause_stays_escalated_and_carries_control_pause` (engine).
- `test_a_cancel_with_an_escalated_lane_records_cancelled_and_lists_escalations` (engine).
- `test_a_run_with_no_control_integrates_as_before` (engine). The existing clean-run tests keep passing unchanged, which also covers this.
- `test_the_lease_is_released_and_its_window_closed_when_run_milestone_returns` (engine). While Integrate runs (an Integrate stand-in that reads the lease), `read_lease` for the run is still live with `accepting` false; after `run_milestone` returns, no live lease for the run and the window is closed.
- `test_a_resumed_paused_run_reports_resumed_and_bases_through_report` (engine).
- `test_a_cancelled_milestone_run_is_refused_for_resume` (steps). Also check that an unknown run and a wrong-workflow run are still refused first.
- `test_a_pause_lets_the_running_phase_finish_and_parks_before_the_next` (`@requires_git @requires_brd`, opt-in, real pygents agents via the `fresh_pygents` pattern; not under `tests/e2e/`, same as `test_an_escalation_parks_running_lanes_and_blocks_new_ones`). This is the plan's Success Criterion 1 for this task: the phase in flight when the pause lands finishes and records its result; the newest checkpoint parks with the next phase at the queue head; that next phase never runs.

## Note on inputs

The exploration findings handed to this stage were cut off at 8000 characters, in the middle of the test-placement rule. That is a sign the upstream stage over-ran its brief. The placement stated above was taken directly from design spec §14 and the sibling spec `task-add-stopsignal-request-3113456a-design.md`, not from the missing text.
