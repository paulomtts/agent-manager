# Subtask c0dbd454: Run a milestone's stories as a grafo tree

Parent story: 92c0ab94 "The supervisor" (milestone c2a981a3, "Milestone 7: the supervisor tree"). Plan: `docs/superpowers/plans/2026-09-25-supervisor-tree.md`, Task 3.1. Design: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`, Decisions T1-T6, T9.

Prerequisite: this worktree must contain the unmerged groundwork branches `m7/task-add-stopsignal-and-park-364babde` (`runtime/stop.py` `StopSignal`), `m7/task-add-an-awaitable-9b944409` (`cli.drive_subtask_async`), `m7/task-root-multi-blocker-1693e86e` (`dag.RootPlan`, `dag.story_root` returning `RootPlan`). The `bases.py` branches may be present but are not used here.

## Scope

Rewrite `src/agent_manager/orchestrate.py` only (plus its tests). Replace the thread-pool level loop with one event loop running a `grafo.TreeExecutor` over story nodes.

- `async def supervise(plan, *, store, run_id, root, drive, commands, allow_no_verification, runner_factory, max_concurrent, stop) -> list[LaneOutcome]`.
- `class LaneEscalated(Exception)` and `class LaneStopped(Exception)`, each with `.outcome: LaneOutcome`.
- `run_milestone` keeps its current synchronous signature; its body becomes `asyncio.run(supervise(...))` with a fresh `StopSignal`.
- `Driver` protocol becomes async (`async def __call__(...) -> SubtaskDrive`); the default driver is `cli.drive_subtask_async`. The `should_stop` parameter stays on the protocol and on `drive_subtask_async` (Task 3.3 deletes it); the lane passes `stop=stop`.
- Delete `ThreadPoolExecutor`, the `RunStop` class, and the `for level in levels` barrier block.
- One `grafo.Node` per story, `uuid=story.id`, `timeout=None` always. One edge per in-milestone blocker (from `RootPlan.blockers`), `forward=f"tip_{dag.short_id(blocker)}"`. Executor roots are the stories with no in-milestone blocker. `orchestrate.py` is the only module that imports `grafo`.
- The `grafo` logger is set to CRITICAL for the duration of `supervise`.
- Supervisor and lanes are plain async functions. Only the subtask is a pygents `Agent` (T2).
- `levels` stays in the report, computed by `dag.compute_levels` as waves. It no longer affects scheduling.

## Observable behavior

- Lane: a story with no remaining subtasks returns its existing tip without taking a slot. Otherwise it takes a slot from `asyncio.Semaphore(max_concurrent)` only after grafo starts it (so after all blockers succeeded), then drives its remaining subtasks in order, stacking each on the previous subtask branch. It starts from `plan.roots[story.id].branch`.
- A story starts as soon as its own blockers finish, not when its level finishes. At most `max_concurrent` lanes run subtasks at once. A chain can finish with one slot.
- Stop checks: if `stop.triggered` after acquiring the slot, or before any subtask, the lane raises `LaneStopped(outcome stopped, before=<that subtask>)` without driving it.
- Escalation: `stop.trigger(story.id)` is called, then `LaneEscalated(outcome)` is raised. Running lanes park through `StopSignal` and new lanes are not started, because grafo does not release dependents of a lane that raised. A driven subtask returning `stopped` raises `LaneStopped`.
- The lane wraps every exception it sees while driving a subtask, not only a non-`done` summary status: a `try`/`except Exception` around each `await drive(...)` call catches anything the driver itself raises (a lane bug), calls `stop.trigger(story.id)`, and raises `LaneEscalated` with `detail=f"{type(error).__name__}: {error}"` at that subtask, exactly like a domain escalation. This is required, not optional: grafo's `executor.errors` is a flat `list[Exception]` with no link back to the node that raised it, so only `LaneEscalated`/`LaneStopped` -- because they carry `.outcome` -- let `collect_outcomes` attribute an error to a story and a subtask. `LaneEscalated`/`LaneStopped` themselves pass through this `except` unchanged (checked by type first, then re-raised, never re-wrapped).
- `collect_outcomes` (T6) runs after `executor.run()`. Node output gives `done`. `LaneEscalated` gives its outcome: the first one in `executor.errors` is primary and the rest are `also_escalated`. `LaneStopped` gives its outcome. Any other exception in `executor.errors` -- one that is neither `LaneEscalated` nor `LaneStopped`, meaning it escaped even the lane's own catch-all (a bug in `supervise` itself, building nodes or edges, outside any lane) -- gives `escalated` with the detail `"<Type>: <msg>"` and no `subtask` or `failed_phase`, since nothing ties it to one story. No output and no error gives `pending`.
- The report shape and the CLI envelope are unchanged apart from what T6 implies. `levels` is still reported.
- The base branch never moves and nothing is pushed.

## Error paths

- `root.kind == "merged"` (two or more in-milestone blockers) is refused explicitly in `orchestrate.py`. It must not rely on `dag.story_root` raising `StackRootError`, which the new `dag` no longer does for this case. Keep the existing refusal behavior: the same outcome or error the current test `test_a_story_with_two_blockers_is_refused_before_anything_is_written` in `tests/test_orchestrate.py` expects (its line number has drifted from an earlier count; find it by name), with that test adapted to the `RootPlan` source. Task 3.2 (8eca88e2) lifts this.
- An unexpected exception in a lane (a lane bug) becomes an `escalated` outcome, not a crash. grafo's traceback logging must not reach stdout. stdout stays exactly one JSON line.

## Out of scope

`bases.build`/`BaseFailed` wiring, the `bases` report key (Task 3.2). Removing `should_stop` and the `BEFORE_TURN` bridge (Task 3.3). `am resume` changes.

## Tests

Placement rule: design spec §14 "Testing" (`2026-09-23-agent-manager-design.md` lines 501-516) places tests by subject. Engine-level code is tested with a fake driver returning canned results. The one slow real-wiring run is the end-to-end tier. No test sleeps to prove ordering: fake drivers block on `asyncio.Event`s. Fakes know only what their brief tells them. Barrier tests are rewritten as dataflow tests, not weakened (T9).

Engine tier with a fake driver, in `tests/test_orchestrate.py`:
1. `test_a_story_starts_when_its_blocker_finishes_not_its_level`: C (blocked by A) starts while B is still gated on an Event.
2. `test_at_most_max_concurrent_lanes_run`: 5 ready stories, `max_concurrent=2`, peak 2.
3. `test_a_chain_finishes_with_one_slot`: A<-B<-C, `max_concurrent=1`, all `done`.
4. `test_an_escalation_parks_running_lanes_and_blocks_new_ones`: real M6 subtask agents with a fake runner. The escalating story is `escalated`, the running one is `stopped`/parked, and a dependent that has not started is `pending`.
5. `test_two_escalations_in_one_tick_give_one_primary`: one primary, the other `also_escalated`.
6. `test_stop_while_waiting_for_a_slot_ends_stopped` (Review Focus 3): the lane ends `stopped` and the driver is never called for it.
7. `test_every_node_has_no_timeout` (Review Focus 1): every built node has `_timeout is None`.
8. `test_a_lane_outlives_the_default_node_timeout` (Review Focus 1): grafo's default is patched to 0.05 s. A lane gated on an Event past that is not cancelled and ends `done`.
9. `test_a_lane_bug_becomes_escalated_with_type_and_message`: the driver raises `ValueError`. The outcome is `escalated` with `"ValueError: ..."` at the current subtask.
10. `test_a_lane_bug_keeps_stdout_one_json_line` (Review Focus 2): run through the CLI with `capsys`, the fake driver injected. stdout parses as a single JSON line and the traceback is not on stdout.
11. `test_merged_root_is_refused`: the existing two-blocker refusal test, adapted to `RootPlan(kind="merged")`.
12. `test_report_keeps_levels_as_waves`: `levels` equals `dag.compute_levels` output.
13. `test_only_orchestrate_imports_grafo`: a source scan of `src/agent_manager`.

End-to-end tier, `tests/e2e/test_parallel_milestone.py`: the existing parallel-milestone tests must pass through the real `run_milestone` -> `asyncio.run` -> `drive_subtask_async` wiring with the stand-in harness. Adjust only the level-barrier assertions, into dataflow assertions.

Verification: `uv run pytest`, the whole default suite green including `tests/e2e`.

Note: the exploration findings given to this stage were cut off at 8000 characters, partway through the test-placement rule's end-to-end bullet. The end-to-end tier above is taken from the plan's Task 3.1 file list and spec T9, not from the missing text.
