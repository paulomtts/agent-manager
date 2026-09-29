<!-- task-pipeline: validated -->
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

---

# Park Milestone Runs on Pause and Close Them on Cancel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run_milestone` hold a `control.Lease` for the run, run `supervise` under `control.controlled(...)`, and turn an applied `am pause` into a `stopped` run and an applied `am cancel` into a `cancelled` run (C6 precedence), without Integrate; refuse to resume a cancelled milestone run.

**Architecture:** One new pure payload builder (`controlled_payload`, with two small row helpers shared with `escalated_payload`) in `orchestrate.py`. `run_milestone`'s body from `store.record_run(run_record)` to its final record moves inside `with control.Lease(store) as lease:`, itself inside the existing `try/finally: store.close()`. The run's existing `StopSignal` is handed to `control.controlled`, which is the only thing that ever calls `stop.request`. After the tree returns, a four-branch C6 ladder chooses cancelled / escalated (+`control: "pause"`) / stopped / Integrate. `resumable_milestone_run` gains one refusal.

**Tech Stack:** Python 3, asyncio, grafo, SQLite (`store`), pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m9/task-park-milestone-runs-on-0e1edf31/docs/superpowers/specs/task-park-milestone-runs-on-0e1edf31-design.md` (prepended verbatim above).

**Branch / worktree:** `m9/task-park-milestone-runs-on-0e1edf31` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m9/task-park-milestone-runs-on-0e1edf31`, cut from `m9/task-add-stopsignal-request-3113456a`. Nothing is pushed; the base branch never moves. All paths below are relative to that worktree.

**Code read while planning (verified in this worktree):** `src/agent_manager/orchestrate.py` lines 138-197 (`escalated_payload`), 504-529 (`resumable_milestone_run`), 1244-1435 (`run_milestone`) match the spec's line numbers. `src/agent_manager/control.py` has `Lease` (79-147), `apply_pending` (150-167), `controlled` (191-228) and `CONTROL_POLL_SECONDS = 1.0`. `src/agent_manager/runtime/stop.py` has `Command = Literal["pause", "cancel"]` and `StopSignal.requested`. `src/agent_manager/store.py` has module-level `open_db`, `read_lease`, `control_requests`, `immediate`, `add_control`, `LeaseRow`, `ControlRow`. `models.Status` already includes `"cancelled"`. `tests/test_orchestrate.py` already defines `_milestone`, `_run`, `_resume`, `_load`, `_statuses`, `_census_levels`, `_subtasks_by_story`, `GatedDriver`, `FakeDriver`, `CheckpointDriver`, `IntegrateRecorder`/`integrate_recorder`, `fake_bases`, `_root_plan`, `_bases_entry`, `_plant`, `_await_stop`, `_within`, `_meet_then_await_stop`, `_LaneKilled`, `_run_or_fail_if_it_hangs`, `fresh_pygents`, `_ThreadWatch`, `_resume_root`, `_record_resume_run`, `RESUME_RUN_ID`, `STARTED_AT`, `WAIT`, `Gate`, `requires_git`, `requires_brd`. If any of these has moved when you start, note it in the task result.

**Note on inputs:** the exploration findings given to the planning stage were truncated mid-sentence in the test-placement rule. The tiers used here come from the spec's own Tests section (design §14 and the sibling Task 1.1 spec), not from the missing text.

## Global Constraints

- No new runtime dependency.
- No socket, fifo or signal handler; the only channel is per-project SQLite (C1).
- `control.py` never imports `cli` or `orchestrate`; only `orchestrate.py` imports `grafo`.
- No schema change: no new column, no CHECK change; `run_controls`/`run_leases` stay row-only.
- The park is milestone 7's (`StopSignal` + `ON_PAUSE` + `Parked`); no second stop path; a running phase is never cancelled to honour a control.
- A control never produces `escalated` or a `failed_phase`; a paused or cancelled run never runs Integrate in that invocation (C6).
- Every refusal stays before `store.record_run` (M7 order); the `Lease` sits inside the `try` that closes the store.
- Only `src/agent_manager/orchestrate.py` and `tests/test_orchestrate.py` change. `cli.py` belongs to df0e9d20 and 9f5467e3.
- Tests never sleep to prove ordering; every `run_milestone` call in a new test passes `control_interval=0`; a "second process" is a second `store.open_db` connection.
- Resume refusal message, exact: `f"run {run_id} was cancelled; start new work with am run --milestone"`.
- Verification: `uv run pytest`.

## Review Focus

1. A lane dies of a `BaseException` after a pause was applied: the exception propagates, the run stays `started` (never `stopped`/`cancelled`), the lease row is gone, Integrate never runs. Test: `test_a_crash_after_a_pause_propagates_releases_the_lease_and_records_neither` (Task 2).
2. A resume refused for a checkpoint saved under another workflow: the refusal comes before any lease is taken. Test: `test_a_refused_resume_never_takes_a_lease` (Task 2).
3. A request left unhandled under an earlier lease of the same run (sent to the interrupted process) must never pause the resumed run. Test: `test_a_request_left_under_an_earlier_lease_never_reaches_the_resumed_run` (Task 2).
4. An Integrate that raises still releases the lease and never records the run `done`. Test: `test_an_integrate_that_raises_still_releases_the_lease` (Task 2).

---

### Task 1: `controlled_payload` and the shared row helpers (pure tier)

**Files:**
- Modify: `src/agent_manager/orchestrate.py:56` (import), `:138-197` (`escalated_payload`, reuse the helpers), insert new functions right after line 197
- Test: `tests/test_orchestrate.py` (insert after `test_an_unnamed_primary_falls_back_to_the_first_escalation_in_census_order`, currently ending at line 264)

**Interfaces:**
- Consumes: `LaneOutcome` (orchestrate.py:93), `Command` from `agent_manager.runtime.stop`.
- Produces:
  - `stopped_row(outcome: LaneOutcome) -> dict[str, Any]` — `{"story", "subtask", "before_phase"}`.
  - `escalation_row(outcome: LaneOutcome) -> dict[str, Any]` — `{"level", "story", "subtask", "failed_phase", "detail"}`.
  - `controlled_payload(run_id: str, command: Command, outcomes: Sequence[LaneOutcome], warnings: list[str]) -> dict[str, Any]` — used by Task 3.

- [ ] **Step 1: Write the failing tests**

Insert after `test_an_unnamed_primary_falls_back_to_the_first_escalation_in_census_order` in `tests/test_orchestrate.py`:

```python
def test_controlled_payload_on_pause_lists_stopped_completed_pending_and_the_resume_hint():
    """C12: every stopped lane in census order, every lane's completed work in
    wave order (not only a stopped lane's), the pending stories, and the hint."""
    parked = orchestrate.LaneOutcome(
        kind="stopped",
        story="A",
        level=0,
        subtask="a2",
        before_phase="implement",
        completed=("a1",),
    )
    finished = orchestrate.LaneOutcome(kind="done", story="B", level=0, completed=("b1", "b2"))
    between = orchestrate.LaneOutcome(kind="stopped", story="C", level=1, subtask="c1")
    queued = orchestrate.LaneOutcome(kind="pending", story="D", level=1)

    payload = orchestrate.controlled_payload(
        "run-1", "pause", [parked, finished, between, queued], ["gate warned"]
    )

    assert payload == {
        "paused": True,
        "run_id": "run-1",
        "stopped": [
            {"story": "A", "subtask": "a2", "before_phase": "implement"},
            {"story": "C", "subtask": "c1", "before_phase": None},
        ],
        "completed": ["a1", "b1", "b2"],
        "pending": ["D"],
        "warnings": ["gate warned"],
        "resume": "am resume run-1",
    }
    assert list(payload) == [
        "paused", "run_id", "stopped", "completed", "pending", "warnings", "resume"
    ]


def test_controlled_payload_on_cancel_has_no_resume_and_lists_escalations_primary_first():
    first = orchestrate.LaneOutcome(
        kind="escalated", story="A", level=0, subtask="a1", failed_phase="review", detail="x"
    )
    parked = orchestrate.LaneOutcome(
        kind="stopped", story="B", level=0, subtask="b1", before_phase="implement"
    )
    primary = orchestrate.LaneOutcome(
        kind="escalated",
        story="C",
        level=0,
        subtask="c1",
        failed_phase="verify",
        detail="y",
        primary=True,
    )

    payload = orchestrate.controlled_payload("run-1", "cancel", [first, parked, primary], [])

    assert payload == {
        "cancelled": True,
        "run_id": "run-1",
        "stopped": [{"story": "B", "subtask": "b1", "before_phase": "implement"}],
        "completed": [],
        "pending": [],
        "warnings": [],
        "escalations": [
            {"level": 0, "story": "C", "subtask": "c1", "failed_phase": "verify", "detail": "y"},
            {"level": 0, "story": "A", "subtask": "a1", "failed_phase": "review", "detail": "x"},
        ],
    }
    assert list(payload) == [
        "cancelled", "run_id", "stopped", "completed", "pending", "warnings", "escalations"
    ]
    # No outcome marked primary: the first in census order leads, as in `escalated_payload`.
    unmarked = orchestrate.controlled_payload(
        "run-1", "cancel", [first, replace(primary, primary=False)], []
    )
    assert [row["story"] for row in unmarked["escalations"]] == ["A", "C"]


def test_controlled_payload_on_cancel_without_escalations_omits_escalations_and_never_has_escalated():
    parked = orchestrate.LaneOutcome(
        kind="stopped", story="A", level=0, subtask="a1", before_phase="plan"
    )

    for command in ("pause", "cancel"):
        payload = orchestrate.controlled_payload("run-1", command, [parked], [])
        assert "escalated" not in payload
        assert "failed_phase" not in payload
    cancelled = orchestrate.controlled_payload("run-1", "cancel", [parked], [])
    assert "escalations" not in cancelled
    assert "resume" not in cancelled
    assert "paused" not in cancelled
```

(`replace` is already imported from `dataclasses` at the top of the test module.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k controlled_payload -v`
Expected: 3 FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'controlled_payload'`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/orchestrate.py`, change line 56:

```python
from agent_manager.runtime.stop import Command, StopSignal
```

Replace the body of `escalated_payload` from `also = [` through the `completed = [...]` list (lines 169-190) so its row dicts come from the shared helpers (behaviour unchanged, existing payload tests guard it):

```python
    also = [escalation_row(outcome) for outcome in escalations if outcome is not primary]
    stopped = [stopped_row(outcome) for outcome in outcomes if outcome.kind == "stopped"]
    completed = [
        subtask
        for outcome in outcomes
        if outcome.kind == "stopped"
        for subtask in outcome.completed
    ]
```

Insert these three functions immediately before `def escalated_payload(` (so the helpers are defined above their first use; `controlled_payload` sits beside `escalated_payload`):

```python
def stopped_row(outcome: LaneOutcome) -> dict[str, Any]:
    """A stopped lane as every payload lists it: the subtask it stopped at and
    the phase it parked before (None when it stopped between subtasks)."""
    return {
        "story": outcome.story,
        "subtask": outcome.subtask,
        "before_phase": outcome.before_phase,
    }


def escalation_row(outcome: LaneOutcome) -> dict[str, Any]:
    """An escalated lane as `also_escalated` and `escalations` list it."""
    return {
        "level": outcome.level,
        "story": outcome.story,
        "subtask": outcome.subtask,
        "failed_phase": outcome.failed_phase,
        "detail": outcome.detail,
    }


def controlled_payload(
    run_id: str,
    command: Command,
    outcomes: Sequence[LaneOutcome],
    warnings: list[str],
) -> dict[str, Any]:
    """The result of a run a control ended (live control C12), outcomes in wave order.

    `paused` or `cancelled`, then `run_id`, `stopped` (census order, the
    `escalated_payload` row shape), `completed` (every lane's finished
    subtasks, wave order), `pending` (story ids) and `warnings`. A pause adds
    the `resume` hint. A cancel adds `escalations` only when a lane really
    escalated, primary first -- the outcome marked `primary`, else the first
    in census order. There is never an `escalated` key: a control is not an
    escalation.
    """
    payload: dict[str, Any] = {
        "paused" if command == "pause" else "cancelled": True,
        "run_id": run_id,
        "stopped": [stopped_row(outcome) for outcome in outcomes if outcome.kind == "stopped"],
        "completed": [subtask for outcome in outcomes for subtask in outcome.completed],
        "pending": [outcome.story for outcome in outcomes if outcome.kind == "pending"],
        "warnings": warnings,
    }
    if command == "pause":
        payload["resume"] = f"am resume {run_id}"
        return payload
    escalations = [outcome for outcome in outcomes if outcome.kind == "escalated"]
    if escalations:
        primary = next((outcome for outcome in escalations if outcome.primary), escalations[0])
        payload["escalations"] = [escalation_row(primary)] + [
            escalation_row(outcome) for outcome in escalations if outcome is not primary
        ]
    return payload
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "payload" -v`
Expected: PASS for the three new tests and for every existing `escalated_payload` / `integrate_escalated_payload` / `bases_payload` test.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): add controlled_payload for paused and cancelled runs (C12)"
```

---

### Task 2: Hold a lease and run the tree under `control.controlled` (engine tier)

**Files:**
- Modify: `src/agent_manager/orchestrate.py:54` (import `control`), `:1244-1296` (signature + docstring), `:1339-1435` (body)
- Modify: `tests/test_orchestrate.py:40` (import `control`)
- Test: `tests/test_orchestrate.py` (new section appended at the end of the file)

**Interfaces:**
- Consumes: `control.Lease(store)` (context manager, `.token`), `control.controlled(work, *, store, stop, lease, interval)`, `control.CONTROL_POLL_SECONDS`, `store.read_lease`, `store.add_control`, `store.immediate`, `store.control_requests`.
- Produces: `run_milestone(..., control_interval: float = control.CONTROL_POLL_SECONDS)`; test helpers `_lease(project, run_id)`, `_send(project, run_id, command, *, token=None)`, `_controls(project, run_id)`, `_send_then_await_stop(project, run_id, command) -> Gate`, used by Task 3.

- [ ] **Step 1: Add the test import**

In `tests/test_orchestrate.py`, change line 40 to:

```python
from agent_manager import bases, board, census, cli, control, dag, integration, models, orchestrate, paths
```

- [ ] **Step 2: Write the failing tests and helpers**

Append to the end of `tests/test_orchestrate.py`:

```python
# ── live control: pause and cancel (card 0e1edf31) ──────────────────────────


def _lease(project: Path, run_id: str) -> store_module.LeaseRow | None:
    """The run's lease row, read over a second connection as `am status` would."""
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return store_module.read_lease(conn, run_id)
    finally:
        conn.close()


def _send(project: Path, run_id: str, command: str, *, token: str | None = None) -> None:
    """Insert one request over a second connection, as `am pause`/`am cancel` would.

    Addressed to the live lease's token unless `token` names another one.
    """
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        if token is None:
            lease = store_module.read_lease(conn, run_id)
            assert lease is not None and lease.accepting, "no open lease to address the request to"
            token = lease.token
        with store_module.immediate(conn):
            store_module.add_control(
                conn, run_id, lease=token, command=command, requested_at=STARTED_AT
            )
    finally:
        conn.close()


def _controls(project: Path, run_id: str) -> list[store_module.ControlRow]:
    conn = store_module.open_db(cli.resolve_repo_dir(project))
    try:
        return store_module.control_requests(conn, run_id)
    finally:
        conn.close()


def _send_then_await_stop(project: Path, run_id: str, command: str) -> Gate:
    """A gate that sends `command` mid-subtask, then holds the call until the
    run's watcher has applied it and the stop fired (no sleeps)."""

    async def gate(stop: StopSignal | None) -> None:
        _send(project, run_id, command)
        await _await_stop(stop)

    return gate


@requires_git
@requires_brd
def test_a_run_with_no_control_integrates_as_before(project, integrate_recorder):
    shape = _milestone(project, {"A": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]

    result = _run(project, shape["milestone"], FakeDriver(), control_interval=0)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result["done"] is True, result
    assert not {"paused", "cancelled", "control", "escalated"} & set(result)
    assert len(integrate_recorder.calls) == 1
    assert _statuses(_load(project, run_id)) == {"run": "done", story_a: "done", a1: "done"}
    assert _controls(project, run_id) == []


@requires_git
@requires_brd
def test_the_lease_is_released_and_its_window_closed_when_run_milestone_returns(
    project, integrate_recorder, monkeypatch
):
    """C2/C4: the lease is held through Integrate with its window already
    closed (`controlled` closed it when the tree returned), and gone once
    `run_milestone` returns."""
    shape = _milestone(project, {"A": 1})
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    seen: list[store_module.LeaseRow | None] = []

    def integrate_reading_the_lease(**kwargs: Any) -> Any:
        seen.append(_lease(project, run_id))
        return integrate_recorder(**kwargs)

    monkeypatch.setattr(integration, "integrate_milestone", integrate_reading_the_lease)

    result = _run(project, shape["milestone"], FakeDriver(), control_interval=0)

    assert result["done"] is True, result
    (during,) = seen
    assert during is not None, "no lease was held while Integrate ran"
    assert during.run_id == run_id
    assert during.accepting is False
    assert _lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_crash_after_a_pause_propagates_releases_the_lease_and_records_neither(
    project, integrate_recorder
):
    """Error paths: a lane's BaseException leaves the run as §7 says; the
    pause already applied does not turn it into `stopped`, and the lease is
    released on the way out."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    driver = GatedDriver(
        outcomes={a1: _LaneKilled("the manager died after the pause")},
        gates={a1: _send_then_await_stop(project, run_id, "pause")},
    )

    with pytest.raises(_LaneKilled):
        _run_or_fail_if_it_hangs(
            lambda: _run(project, shape["milestone"], driver, control_interval=0)
        )

    assert _load(project, run_id).status == "started"
    assert _lease(project, run_id) is None
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_a_refused_resume_never_takes_a_lease(project, monkeypatch):
    """Error paths: `resume_checkpoints`' refusal comes before `record_run`,
    so before the lease."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    _plant(project, run_id, a1, "turn", queue=("review",), digest="saved-under-another-task")

    def never(self: control.Lease) -> control.Lease:
        pytest.fail("a lease was taken before the resume was refused")

    monkeypatch.setattr(control.Lease, "__enter__", never)

    with pytest.raises(cli.CheckpointMismatchError):
        _resume(project, run_id, CheckpointDriver(), control_interval=0)

    assert _lease(project, run_id) is None


@requires_git
@requires_brd
def test_a_request_left_under_an_earlier_lease_never_reaches_the_resumed_run(project):
    """C4: a pause addressed to the interrupted process's token stays unhandled
    and the resumed run, under its own fresh lease, finishes clean."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    run_id = first["run_id"]
    _send(project, run_id, "pause", token="the-interrupted-processes-lease")

    result = _resume(project, run_id, FakeDriver(), control_interval=0)

    assert result["done"] is True, result
    assert result["resumed"] is True
    assert [row.handled_at for row in _controls(project, run_id)] == [None]
    assert _load(project, run_id).status == "done"


@requires_git
@requires_brd
def test_an_integrate_that_raises_still_releases_the_lease(project, integrate_recorder):
    shape = _milestone(project, {"A": 1})
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    integrate_recorder.outcome = RuntimeError("integrate blew up")

    with pytest.raises(RuntimeError, match="integrate blew up"):
        _run(project, shape["milestone"], FakeDriver(), control_interval=0)

    assert _lease(project, run_id) is None
    assert _load(project, run_id).status == "started"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "no_control_integrates or lease or crash_after_a_pause or earlier_lease or integrate_that_raises_still" -v`
Expected: 6 FAIL with `TypeError: run_milestone() got an unexpected keyword argument 'control_interval'` (the refused-resume test fails the same way, not with `CheckpointMismatchError`).

- [ ] **Step 4: Add the import and the keyword**

In `src/agent_manager/orchestrate.py`, change line 54 to:

```python
from agent_manager import bases, board, census, cli, control, dag, integration, models
```

Change the `run_milestone` signature (lines 1244-1257) to:

```python
def run_milestone(
    milestone: str | None,
    *,
    repo_dir: Path,
    base_branch: str | None = None,
    branch_prefix: str | None = None,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: cli.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
    resume_run_id: str | None = None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
```

In its docstring, replace the sentence `Then one \`milestone\` run is recorded with its whole plan \`pending\`, and \`asyncio.run(supervise(...))\` runs every story` with:

```
    Then the run takes a `control.Lease`, one `milestone` run is recorded
    with its whole plan `pending`, and `asyncio.run(control.controlled(
    supervise(...)))` runs every story
```

and append this paragraph before the closing `"""`:

```

    The lease (live control C2) is held from `record_run` to the run's final
    record, and released before the store closes; every refusal comes before
    it. `controlled` polls this lease's `am pause`/`am cancel` requests every
    `control_interval` seconds and applies them to the run's one
    `StopSignal`; it closes the window and sweeps once more when the tree
    returns, before Integrate. A crash propagates and releases the lease.
```

- [ ] **Step 5: Wrap the body in the lease and run the tree under `controlled`**

Replace lines 1339-1435 (from `store = Store.open(root, run_id)` through `store.close()`) with:

```python
    store = Store.open(root, run_id)
    try:
        checkpoints: dict[str, Checkpoint] | None = None
        cards: list[tuple[str, Workflow]] = []
        if resumed is not None:
            cards = open_cards(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
            # The store's own refusal, a checkpoint saved under another
            # workflow, comes before the first write and before git is touched.
            checkpoints = resume_checkpoints(store, cards)
            refresh_git(root)
        # After every refusal, and inside the `try` that closes the store, so
        # the lease is released before `store.close()` (live control C2).
        with control.Lease(store) as lease:
            store.record_run(run_record)
            rows = record_plan(store, levels, root=root, branch_prefix=branch_prefix)
            if resumed is not None:
                # After `record_plan`, which records every planned row `pending`.
                reopen_rows(store, resumed, {card_id for card_id, _workflow in cards})
            warnings = reroll_stale_stories(plan.stories, root)
            completed: list[str] = []
            stop = StopSignal()

            # `controlled` only ever parks the run through `stop` (C3); it
            # closes the window and runs a final sweep before returning.
            outcomes = asyncio.run(
                control.controlled(
                    supervise(
                        supervisor_plan(
                            plan.stories,
                            levels,
                            rows,
                            branch_prefix=branch_prefix,
                            base_branch=base_branch,
                            checkpoints=checkpoints,
                        ),
                        store=store,
                        run_id=run_id,
                        root=root,
                        drive=drive,
                        commands=list(commands),
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                        max_concurrent=max_concurrent,
                        stop=stop,
                    ),
                    store=store,
                    stop=stop,
                    lease=lease,
                    interval=control_interval,
                )
            )
            # Wave order, census order within a wave, never finish order.
            for outcome in outcomes:
                completed.extend(outcome.completed)
                warnings.extend(outcome.warnings)
            built_bases = bases_payload(outcomes)

            def report(payload: dict[str, Any]) -> dict[str, Any]:
                """Every payload shape on the same terms: `bases` when built, `resumed` on a resume."""
                if resumed is not None:
                    payload["resumed"] = True
                return with_bases(payload, built_bases)

            if any(outcome.kind == "escalated" for outcome in outcomes):
                store.record_run(run_record.model_copy(update={"status": "escalated"}))
                primary = next((outcome.story for outcome in outcomes if outcome.primary), None)
                return report(escalated_payload(run_id, primary, outcomes, warnings))

            # Integrate (addendum I6) runs only once every lane finished clean,
            # and also when there was nothing left to drive: that is how a relaunch
            # retries an Integrate escalation, and why a finished milestone's
            # relaunch is a no-op merge. Read as `integration.integrate_milestone`
            # so a test can replace it, as `driver` is. It needs a factory for a
            # conflicting tip; `None` is production's, read off `cli` now.
            factory = cli.default_runner_factory if runner_factory is None else runner_factory
            outcome = integration.integrate_milestone(
                stories=plan.stories,
                repo_dir=root,
                base_branch=base_branch,
                branch_prefix=branch_prefix,
                commands=list(commands),
                allow_no_verification=allow_no_verification,
                store=store,
                run_id=run_id,
                runner_factory=factory,
            )
            if isinstance(outcome, integration.IntegrateEscalation):
                # The branch and worktree stay exactly as Integrate left them (I5).
                store.record_run(run_record.model_copy(update={"status": "escalated"}))
                return report(integrate_escalated_payload(run_id, outcome, warnings))

            store.record_run(run_record.model_copy(update={"status": "done"}))
            return report(
                {
                    "done": True,
                    "run_id": run_id,
                    "levels": [
                        {"level": index, "stories": [planned.story.id for planned in level]}
                        for index, level in enumerate(levels)
                    ],
                    "completed": completed,
                    "tips": tips,
                    "warnings": warnings,
                    "integrated": integrated_payload(outcome),
                }
            )
    finally:
        store.close()
```

Also update the module docstring's second paragraph (line 4), replacing `on one event loop: \`asyncio.run(supervise(...))\`.` with `on one event loop: \`asyncio.run(control.controlled(supervise(...)))\`, under the run's \`control.Lease\`.`

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "no_control_integrates or lease or crash_after_a_pause or earlier_lease or integrate_that_raises_still" -v`
Expected: 6 PASS.

- [ ] **Step 7: Run the whole module to check nothing regressed**

Run: `uv run pytest tests/test_orchestrate.py tests/test_control.py -v`
Expected: all PASS (existing tests use the default `control_interval`; `controlled` returns as soon as the tree does, so they do not wait on the poll).

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): hold a lease and run the milestone tree under control.controlled"
```

---

### Task 3: C6 outcome precedence — park on pause, close on cancel (engine tier)

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (the escalated branch inside `run_milestone`, the block starting `if any(outcome.kind == "escalated" for outcome in outcomes):` written in Task 2; and the `run_milestone` docstring)
- Test: `tests/test_orchestrate.py` (append to the live-control section from Task 2)

**Interfaces:**
- Consumes: `controlled_payload` and `escalated_payload` (Task 1), `StopSignal.requested: Command | None`, test helpers `_send`, `_send_then_await_stop`, `_controls`, `_lease` (Task 2).
- Produces: `run_milestone` returns a `controlled_payload` for cancel/pause, `escalated_payload` plus `"control": "pause"` for an escalation under pause; the run is recorded `cancelled` / `stopped` accordingly.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
def test_a_paused_milestone_parks_records_stopped_and_skips_integrate(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 2, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "pause")})

    result = _run(project, shape["milestone"], driver, control_interval=0)

    assert [call["card"] for call in driver.calls] == [a1]
    assert result == {
        "paused": True,
        "run_id": run_id,
        "stopped": [{"story": story_a, "subtask": a1, "before_phase": "implement"}],
        "completed": [],
        "pending": [story_b],
        "warnings": [],
        "resume": f"am resume {run_id}",
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "stopped",
        story_a: "stopped",
        a1: "stopped",
        a2: "pending",
        story_b: "pending",
        b1: "pending",
    }
    assert integrate_recorder.calls == []
    assert [(row.command, row.handled_at is not None) for row in _controls(project, run_id)] == [
        ("pause", True)
    ]


@requires_git
@requires_brd
def test_a_pause_applied_after_the_last_lane_already_finished_still_skips_integrate(
    project, integrate_recorder, monkeypatch
):
    """C6 case 3 applies even when every lane had already finished: the
    request lands after the watcher stopped and before the window closed, so
    only `controlled`'s final sweep applies it."""
    shape = _milestone(project, {"A": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    real_close_window = control.Lease.close_window

    def the_request_lands_then_the_window_closes(self: control.Lease) -> None:
        _send(project, run_id, "pause")
        real_close_window(self)

    monkeypatch.setattr(control.Lease, "close_window", the_request_lands_then_the_window_closes)
    driver = GatedDriver()

    result = _run(project, shape["milestone"], driver, control_interval=0)

    assert [call["card"] for call in driver.calls] == [a1]
    assert result == {
        "paused": True,
        "run_id": run_id,
        "stopped": [],
        "completed": [a1],
        "pending": [],
        "warnings": [],
        "resume": f"am resume {run_id}",
    }
    assert _statuses(_load(project, run_id)) == {"run": "stopped", story_a: "done", a1: "done"}
    assert integrate_recorder.calls == []
    assert [row.handled_at is not None for row in _controls(project, run_id)] == [True]


@requires_git
@requires_brd
def test_a_lane_waiting_for_a_slot_ends_stopped_on_a_pause(project, integrate_recorder):
    """Three ready stories, two slots: `queued` waits for a slot when the
    pause lands, takes it, sees the stop and never reaches the driver."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1})
    (first, second, queued) = _census_levels(project, shape["milestone"])[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,), (q1,) = subtasks[first], subtasks[second], subtasks[queued]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_then_pause(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "both slotted lanes in flight")
        _send(project, run_id, "pause")
        await _await_stop(stop)

    driver = GatedDriver(gates={f1: meet_then_pause, s1: _meet_then_await_stop(pair)})

    result = _run(project, shape["milestone"], driver, max_concurrent=2, control_interval=0)

    assert q1 not in [call["card"] for call in driver.calls]
    assert result["paused"] is True, result
    assert result["stopped"] == [
        {"story": first, "subtask": f1, "before_phase": "implement"},
        {"story": second, "subtask": s1, "before_phase": "implement"},
        {"story": queued, "subtask": q1, "before_phase": None},
    ]
    assert _statuses(_load(project, run_id)) == {
        "run": "stopped",
        first: "stopped",
        f1: "stopped",
        second: "stopped",
        s1: "stopped",
        queued: "stopped",
        q1: "pending",
    }
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_a_cancelled_milestone_records_cancelled_and_skips_integrate(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 2})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    driver = GatedDriver(gates={a1: _send_then_await_stop(project, run_id, "cancel")})

    result = _run(project, shape["milestone"], driver, control_interval=0)

    assert [call["card"] for call in driver.calls] == [a1]
    assert result == {
        "cancelled": True,
        "run_id": run_id,
        "stopped": [{"story": story_a, "subtask": a1, "before_phase": "implement"}],
        "completed": [],
        "pending": [],
        "warnings": [],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "cancelled",
        story_a: "stopped",
        a1: "stopped",
        a2: "pending",
    }
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_cancel_after_pause_wins_and_records_cancelled(project, integrate_recorder):
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)

    async def pause_then_cancel(stop: StopSignal | None) -> None:
        _send(project, run_id, "pause")
        await _await_stop(stop)
        _send(project, run_id, "cancel")

    driver = GatedDriver(gates={a1: pause_then_cancel})

    result = _run(project, shape["milestone"], driver, control_interval=0)

    assert result["cancelled"] is True, result
    assert "paused" not in result and "resume" not in result
    assert _load(project, run_id).status == "cancelled"
    assert [(row.command, row.handled_at is not None) for row in _controls(project, run_id)] == [
        ("pause", True),
        ("cancel", True),
    ]
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_an_escalation_under_pause_stays_escalated_and_carries_control_pause(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_pause_then_escalate(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "a1 and b1 in flight together")
        _send(project, run_id, "pause")
        await _await_stop(stop)

    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: meet_pause_then_escalate, b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2, control_interval=0)

    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": 0,
        "story": story_a,
        "subtask": a1,
        "failed_phase": "review",
        "detail": "reviewer found a blocker",
        "warnings": [],
        "stopped": [{"story": story_b, "subtask": b1, "before_phase": "implement"}],
        "control": "pause",
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
    }
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_a_cancel_with_an_escalated_lane_records_cancelled_and_lists_escalations(
    project, integrate_recorder
):
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    pair = asyncio.Barrier(2)

    async def meet_cancel_then_escalate(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "a1 and b1 in flight together")
        _send(project, run_id, "cancel")
        await _await_stop(stop)

    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: meet_cancel_then_escalate, b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2, control_interval=0)

    assert result == {
        "cancelled": True,
        "run_id": run_id,
        "stopped": [{"story": story_b, "subtask": b1, "before_phase": "implement"}],
        "completed": [],
        "pending": [],
        "warnings": [],
        "escalations": [
            {
                "level": 0,
                "story": story_a,
                "subtask": a1,
                "failed_phase": "review",
                "detail": "reviewer found a blocker",
            }
        ],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "cancelled",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
    }
    assert integrate_recorder.calls == []


@requires_git
@requires_brd
def test_a_resumed_paused_run_reports_resumed_and_bases_through_report(project, fake_bases):
    """Every branch goes through `report`: a pause on a resume carries
    `resumed` and the merged base C built in this invocation."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A", "B"]})
    story_c = shape["stories"]["C"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    root_plan = _root_plan(project, shape["milestone"], story_c)
    first = _run(project, shape["milestone"], FakeDriver(outcomes={a1: ("review", "boom")}))
    assert first["escalated"] is True, first
    run_id = first["run_id"]
    driver = GatedDriver(gates={c1: _send_then_await_stop(project, run_id, "pause")})

    result = _resume(project, run_id, driver, control_interval=0)

    assert result["paused"] is True, result
    assert result["resumed"] is True
    assert result["resume"] == f"am resume {run_id}"
    assert result["bases"] == [_bases_entry(story_c, root_plan)]
    assert result["stopped"] == [{"story": story_c, "subtask": c1, "before_phase": "implement"}]
    assert sorted(result["completed"]) == sorted([a1, b1])
    assert _load(project, run_id).status == "stopped"


@requires_git
@requires_brd
def test_a_pause_lets_the_running_phase_finish_and_parks_before_the_next(
    project, fresh_pygents, integrate_recorder
):
    """Success Criterion 1, on a real M6 pygents subtask agent over a
    step-only workflow: the pause lands while `first` runs; `first` finishes;
    the agent parks through ON_PAUSE with `second` at the queue head; `second`
    never runs; the run records `stopped`."""
    shape = _milestone(project, {"A": 1})
    story_a = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    watch = _ThreadWatch()
    ran: list[str] = []

    def first(card: str) -> dict[str, Any]:
        ran.append("first")
        _send(project, run_id, "pause")
        if not watch.paused.wait(WAIT):
            raise RuntimeError("the pause never reached the run")
        return {"first": 1}

    def second(card: str) -> dict[str, Any]:
        ran.append("second")
        return {"second": 2}

    workflow = Workflow("m9_pause_parks", (Step("first", first), Step("second", second)))

    async def drive(*, store, run_id, card, parent, subtask, repo_dir, stop=None, **_: Any):
        stop.register(watch)
        try:
            summary = await runtime_engine.run_subtask_async(
                workflow,
                store,
                story_id=parent.id,
                subtask=subtask,
                repo_dir=repo_dir,
                stop=stop,
            )
        finally:
            stop.unregister(watch)
        return cli.SubtaskDrive(summary=summary, warnings=list(summary.warnings))

    result = _run(project, shape["milestone"], drive, control_interval=0)

    assert ran == ["first"]
    assert result["paused"] is True, result
    assert result["stopped"] == [{"story": story_a, "subtask": a1, "before_phase": "second"}]
    assert result["resume"] == f"am resume {run_id}"
    assert _statuses(_load(project, run_id)) == {
        "run": "stopped",
        story_a: "stopped",
        a1: "stopped",
    }
    assert integrate_recorder.calls == []
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        newest = opened.latest_checkpoint(a1)
    finally:
        opened.close()
    assert newest.reason == "parked"
    assert newest.agent["queue"][0]["kwargs"]["phase"] == "second"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "paused_milestone or pause_applied_after or waiting_for_a_slot_ends_stopped_on_a_pause or cancelled_milestone_records or cancel_after_pause or escalation_under_pause or cancel_with_an_escalated or resumed_paused or running_phase_finish" -v`
Expected: 9 FAIL. The pause/cancel runs fall through to Integrate and return `{"done": True, ...}` (e.g. `KeyError: 'paused'` or dict mismatch, and the run is recorded `done`); the escalation-under-pause payload lacks `"control"`; the cancel-with-escalation run returns the escalated payload.

- [ ] **Step 3: Implement the C6 ladder**

In `src/agent_manager/orchestrate.py`, inside `run_milestone`, replace the block written in Task 2:

```python
            if any(outcome.kind == "escalated" for outcome in outcomes):
                store.record_run(run_record.model_copy(update={"status": "escalated"}))
                primary = next((outcome.story for outcome in outcomes if outcome.primary), None)
                return report(escalated_payload(run_id, primary, outcomes, warnings))
```

with:

```python
            # Outcome precedence (live control C6): the first match wins. A
            # control is never an escalation, and a paused or cancelled run
            # never reaches Integrate in this invocation.
            if stop.requested == "cancel":
                store.record_run(run_record.model_copy(update={"status": "cancelled"}))
                return report(controlled_payload(run_id, "cancel", outcomes, warnings))
            if any(outcome.kind == "escalated" for outcome in outcomes):
                store.record_run(run_record.model_copy(update={"status": "escalated"}))
                primary = next((outcome.story for outcome in outcomes if outcome.primary), None)
                payload = escalated_payload(run_id, primary, outcomes, warnings)
                if stop.requested == "pause":
                    payload["control"] = "pause"
                return report(payload)
            if stop.requested == "pause":
                store.record_run(run_record.model_copy(update={"status": "stopped"}))
                return report(controlled_payload(run_id, "pause", outcomes, warnings))
```

In the `run_milestone` docstring, after the paragraph that begins `The first escalation triggers the run's \`StopSignal\``, insert:

```

    An applied `am cancel` or `am pause` fires the same `StopSignal` through
    `stop.request`, so lanes park exactly as for an escalation. Once the tree
    returns, the first match wins (C6): a cancel records the run `cancelled`
    and returns `controlled_payload`; an escalation records `escalated` as
    below, with `control: "pause"` added when a pause was applied; a pause
    records `stopped` and returns `controlled_payload` with its `resume`
    hint. Only a run with none of these reaches Integrate.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "paused_milestone or pause_applied_after or waiting_for_a_slot_ends_stopped_on_a_pause or cancelled_milestone_records or cancel_after_pause or escalation_under_pause or cancel_with_an_escalated or resumed_paused or running_phase_finish" -v`
Expected: 9 PASS.

- [ ] **Step 5: Run the module**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS (existing escalation tests never set `stop.requested`, so they keep the payload without `control`).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): park milestone runs on pause and close them on cancel (C6)"
```

---

### Task 4: Refuse to resume a cancelled milestone run (steps tier)

**Files:**
- Modify: `src/agent_manager/orchestrate.py:504-529` (`resumable_milestone_run`)
- Test: `tests/test_orchestrate.py` (insert after `test_a_task_run_and_an_unknown_run_are_not_milestone_resumes`, currently ending at line 3546)

**Interfaces:**
- Consumes: `cli.NotResumableError`, `cli.UnknownRunError`, test helpers `_resume_root`, `_record_resume_run`, `RESUME_RUN_ID`.
- Produces: `resumable_milestone_run(root, run_id)` raises `cli.NotResumableError("run <id> was cancelled; start new work with am run --milestone")` for a `cancelled` milestone run.

- [ ] **Step 1: Write the failing test**

Insert after `test_a_task_run_and_an_unknown_run_are_not_milestone_resumes`:

```python
def test_a_cancelled_milestone_run_is_refused_for_resume(tmp_path, monkeypatch):
    """C9: unknown run, then wrong workflow, then cancelled -- the earlier
    refusals still win for a run that is also cancelled."""
    root = _resume_root(tmp_path, monkeypatch)
    _record_resume_run(root, status="cancelled")

    with pytest.raises(cli.NotResumableError) as caught:
        orchestrate.resumable_milestone_run(root, RESUME_RUN_ID)

    assert str(caught.value) == (
        f"run {RESUME_RUN_ID} was cancelled; start new work with am run --milestone"
    )
    task_run = "20260924T120000Z-00000008"
    _record_resume_run(root, task_run, workflow="task", status="cancelled")
    with pytest.raises(cli.NotResumableError, match="'task'"):
        orchestrate.resumable_milestone_run(root, task_run)
    with pytest.raises(cli.UnknownRunError, match="no-such-run"):
        orchestrate.resumable_milestone_run(root, "no-such-run")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_orchestrate.py::test_a_cancelled_milestone_run_is_refused_for_resume -v`
Expected: FAIL with `Failed: DID NOT RAISE <class 'agent_manager.cli.NotResumableError'>`.

- [ ] **Step 3: Implement the refusal**

In `src/agent_manager/orchestrate.py`, replace lines 504-529 (`resumable_milestone_run`) with:

```python
def resumable_milestone_run(root: Path, run_id: str) -> models.Run:
    """The recorded milestone run `run_id`, or the refusal that says why not.

    Read-only through the free `open_db` / `load_run`, like `cli.resume_run`:
    `Store.open` would construct a `Journal`. Refused, in this order (live
    control C9): an unknown run, a run of another workflow, then a
    `cancelled` run and a `done` run (card 54e4ec29, card 0e1edf31).
    """
    conn = open_db(root)
    try:
        run = load_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise cli.UnknownRunError(
            f"run {run_id!r} is not in the projection for {root}"
            " (`agent-manager runs` lists the ones that are)"
        )
    if run.workflow != MILESTONE_WORKFLOW:
        raise cli.NotResumableError(
            f"run {run_id!r} is a {run.workflow!r} run, not a {MILESTONE_WORKFLOW!r} run"
        )
    if run.status == "cancelled":
        raise cli.NotResumableError(
            f"run {run_id} was cancelled; start new work with am run --milestone"
        )
    if run.status == "done":
        raise cli.NotResumableError(
            f"run {run.id} finished; start new work with am run --milestone"
        )
    return run
```

Also, in the `run_milestone` docstring, change `an unknown, non-milestone or \`done\` run` to `an unknown, non-milestone, \`cancelled\` or \`done\` run`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_orchestrate.py -k "resumable or refused_for_resume or finished_milestone_run or not_milestone_resumes" -v`
Expected: PASS for the new test and the three existing `resumable_milestone_run` tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all PASS, no skips beyond the ones present before this card (tests marked `requires_git`/`requires_brd` skip only when `git`/`brd` are missing).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): refuse to resume a cancelled milestone run (C9)"
```

- [ ] **Step 7: Record anything that moved**

In the task result, list any line number in this plan that did not match the code when you started (the plan was written against this worktree, where they matched master @ 6d69e3a), and confirm `cli.py` was not touched.
