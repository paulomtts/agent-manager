# Run a level's stories on a bounded lane pool (card fe2c716b)

Subtask of story 1a254739 "Run a level's stories in parallel" (milestone cdbfa10d). Narrows the parallel-stories addendum (`docs/superpowers/specs/2026-09-24-parallel-stories-design.md`, decisions P1, P4, P5, P6) to `run_milestone`'s walk. The sibling 69bcf17e owns the `--max-concurrent` flag and the default of 4.

## Dependency check

P4 (card 9bfb5ac2) is present in this worktree: `models.Status` includes `stopped`, `engine.SubtaskSummary.status` can be `stopped` (and `engine._stop` sets `detail = "stopped before <phase>"`, with `failed_phase` left as None), and `should_stop: Callable[[], bool] | None` is threaded through `engine.run_subtask`, `cli.drive_subtask` and the `orchestrate.Driver` Protocol. This card consumes those and does not change `engine.py`, `models.py` or `cli.py`. It is not present on `master` (HEAD 72a6cff). Planning must work in this worktree's branch, or on a base where 9bfb5ac2 is merged.

## Scope

All changes go in `src/agent_manager/orchestrate.py` and `tests/test_orchestrate.py`.

1. **One-story lane.** Move the per-story body of `run_milestone` (the subtask loop, row updates and escalation handling) into a module-level function that runs one `PlannedStory` against the shared store, run id, rows, driver and stop event. It returns a frozen dataclass holding:
   - `kind`, one of `done`, `escalated`, `stopped` or `not_started`;
   - the story id and the level;
   - the subtask id;
   - `failed_phase` and `detail`, for an escalation;
   - `before_phase`, for a stop;
   - the lane's own `completed` subtask ids and `warnings`, in the order they arrived.

   The lane keeps no module state. The lock and the event are arguments that `run_milestone` creates for each run.
2. **Stop checks inside a lane.** Before a story's first subtask, the lane checks the stop event. If it is set, the lane returns `not_started`, and the story and its subtasks stay `pending`, just as today's sequential run leaves later stories. After the story has started, the lane never checks the event itself. It passes `should_stop=event.is_set` to the driver, and the engine parks between phases (P4). A running phase is never interrupted.
3. **Recording a stopped subtask.** When the driver returns `summary.status == "stopped"`, the lane records the subtask and its story `stopped`, never `escalated` or `failed`, and returns `stopped`. `before_phase` comes from a small pure helper. The helper strips the `"stopped before "` prefix from `summary.detail` and returns None if the prefix is missing. The engine's summary has no dedicated field for this phase. The lane handles a `stopped` summary before the non-`done` branch, and the stale comment at orchestrate.py:323-326 goes.
4. **The pool.** `run_milestone` gains `max_concurrent: int = 1`. A value below 1 raises `ValueError` before any write, to keep the module's refuse-before-first-side-effect order. Each level runs its stories on a `ThreadPoolExecutor(max_workers=max_concurrent)`, one lane per story. The run waits for every lane of level N before level N+1 is considered. Subtasks inside a story stay strictly sequential. Per-story readiness is deferred.
5. **The first escalation.** One `threading.Event` and one `threading.Lock` are created for each run. A lane that escalates takes the lock, sets the event, and records itself as the primary escalation only if none exists yet. Otherwise it is an extra escalation. The same applies when the driver returns `escalated` and when a lane raises.
6. **Exceptions.** An `Exception` raised anywhere inside a lane after it picked up a subtask becomes an escalation of that subtask, with `failed_phase=None` and `detail=f"{type(error).__name__}: {error}"`. The subtask and its story are recorded `escalated`. `BaseException` is not caught as an escalation. The lane sets the stop event, so siblings park, and lets the exception propagate. It reaches the caller through `future.result()` after the pool shuts down. `test_a_keyboard_interrupt_from_the_driver_is_not_swallowed` must still pass.
7. **Assembling results after the barrier.** After each level's barrier, `run_milestone` walks the lane outcomes in the level's census order, not completion order. It extends `completed` and `warnings` from them. This is deterministic at any bound and identical to today at `max_concurrent=1`. A `done` lane records its story `done`, as today.
8. **Payloads.** If any lane escalated, no later level is scheduled and the run is recorded `escalated`. The return value is the existing escalated payload built from the primary escalation: `escalated`, `run_id`, `level`, `story`, `subtask`, `failed_phase`, `detail` and `warnings`. Two keys are added:
   - `also_escalated`: the other escalations in census order, each with the same shape minus `escalated`, `run_id` and `warnings`;
   - `stopped`: a list of `{story, subtask, before_phase}` for each parked lane, in census order.

   Each of these two keys is present only when its list is non-empty, so a run at `max_concurrent=1` returns exactly today's dict. A clean run returns today's payload unchanged.
9. **Config.** The run record's config becomes `models.RunConfig(max_concurrent_stories=max_concurrent)`. The default stays 1.

Out of scope:
- store, git and board locks (P2 and P3, separate cards);
- the CLI flag and the default of 4 (69bcf17e);
- Integrate;
- per-story readiness;
- milestone-aware resume;
- Ctrl-C handling beyond not swallowing it;
- watch, retry and cancel;
- the README.

Running two `am` processes on one run or repo is unsupported.

## Observable behaviour and error paths

- At `max_concurrent=1`, every existing test in `tests/test_orchestrate.py` passes unchanged. That covers call order, row statuses, payload equality and warning order. `tests/e2e` stays green.
- After an escalation, a lane that had already started parks at its next phase boundary and is reported and recorded `stopped`. A lane that had not started its story leaves it `pending` and is not reported. No story of a later level starts.
- Only a lane's `Exception` becomes an escalation. A `BaseException` propagates.

## Tests

Tier rule: design spec §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`), as applied by the `tests/test_orchestrate.py` module docstring:
- Pure helpers get **unit** tests on hand-built objects.
- `run_milestone` gets **Steps-tier** tests: the `project` fixture (a real temp git repo, a temp brd board, and `XDG_DATA_HOME` under `tmp_path`), `_milestone`, `_run`, `_load` and `_statuses`, with the harness replaced at the `driver` seam.
- Nothing goes in `tests/e2e`.

Every test goes in `tests/test_orchestrate.py`.

Test fakes:
- `FakeDriver` gains a `should_stop=None` keyword. It is not added to the recorded call dict, so existing assertions are unchanged.
- The concurrency tests use a gated fake driver. It blocks on per-card `threading.Event` or `threading.Barrier` objects, and it returns `SubtaskSummary(status="stopped", detail="stopped before <phase>")` when `should_stop()` is true at its simulated boundary.
- Synchronisation is by events and barriers only. The tests never use `sleep`.

| Test | Tier |
|---|---|
| The before-phase helper returns the phase from `"stopped before implement"` and returns None for a detail without the prefix or a None detail. | unit |
| Every story of a level is driven, and each story's subtasks are called in census order with correct bases, at `max_concurrent=3`. A barrier holds all three lanes in flight at once. | Steps |
| In-flight lanes never exceed the bound. With 4 stories in one level at `max_concurrent=2`, a high-water mark counted under a lock is exactly 2. | Steps |
| One escalation sets the stop. The other in-flight lane parks at its next boundary and is recorded `stopped`, along with its story. It appears in `stopped` with its `before_phase`. The run is `escalated`, and no story of level 1 is driven or leaves `pending`. | Steps |
| Two simultaneous escalations produce one primary and one `also_escalated` entry. Both subtasks and stories are recorded `escalated`. | Steps |
| A driver that raises `RuntimeError` in one lane becomes an escalation with `failed_phase=None` and the `"RuntimeError: ..."` detail, and it parks the sibling lane. | Steps |
| A story queued behind the bound that has not started when the stop is set stays `pending` and is absent from `stopped`. | Steps |
| `max_concurrent=1` behaves as before: the existing escalation, raise and clean-run tests pass unmodified, and a clean run's payload has no `also_escalated` or `stopped` keys. | Steps (existing tests, unchanged) |
| The run's recorded `config.max_concurrent_stories` equals the argument, and `max_concurrent=0` raises `ValueError` before any run directory exists. | Steps |
