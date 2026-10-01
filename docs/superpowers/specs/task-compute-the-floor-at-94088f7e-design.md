# Compute the floor at save time and carry it across a resume (card 94088f7e)

Task 1.2 of `docs/superpowers/plans/2026-09-27-exactly-once.md`, under story 5bb73149 "The turn's identity: a floor saved with every agent-phase checkpoint". Decisions come from `docs/superpowers/specs/2026-09-27-exactly-once-design.md` (E4/E5/E11); this document only narrows them to this subtask. Note: neither doc is merged to master yet; both live in the `.claude/worktrees/docs-exactly-once` worktree, not this one.

## Base

This subtask builds on sibling e600b86a (Task 1.1), which is not on `master`. The branch must contain `m11/task-save-a-floor-atomically-e600b86a`: `store.TurnFloor(phase, loop, source_run, floor)`, `Checkpoint.floor`, `Store.save_checkpoint(..., floor=TurnFloor | None)`, and `paths.highest_attempt(run_id, card, phase) -> int`. This worktree already has them. None of those are changed here.

## Scope

Three source files and their tests. Nothing else.

1. `src/agent_manager/runtime/state.py`
   - New `@dataclass(frozen=True) class Adoption` with fields `phase: str`, `loop: int`, `source_run: str`, `floor: int`. Its fields match `TurnFloor`'s, so `Adoption(**vars(turn_floor))` works.
   - `RunDeps.adopt: Adoption | None = None`.
   - `RunDeps.take_adoption(phase: str, loop: int) -> Adoption | None` always sets `self.adopt = None`. It returns the old value only when that value's `(phase, loop)` equals the arguments. Otherwise it returns `None`, and a mismatch still clears `adopt`. A second call always returns `None`.

2. `src/agent_manager/runtime/checkpoint.py`
   - `save(agent, reason)` passes `floor=` to `deps.store.save_checkpoint`. A new private helper (`_floor(deps, agent, reason)`) computes it:
     - Returns `None` unless `reason` is `"turn"` or `"parked"`. `done` and `escalated` rows get no floor.
     - The next turn is the agent's `current_turn` if it has one; otherwise it is the head of the queue. Read it from `agent.to_dict()`, the same way `on_pause` already reads `queue[0]["kwargs"]["phase"]`. The phase and loop come from that turn's kwargs `{"phase", "loop"}`. If there is no next turn, or its phase is not an `AgentPhase` in `deps.workflow` (it is a step, E9), return `None`.
     - If `getattr(deps.store, "run_id", None)` is falsy, return `None`. This covers fake stores without a run.
     - If `deps.adopt` is set and its `(phase, loop)` matches, return `TurnFloor(**vars(deps.adopt))`. That is the carried floor, with `source_run` and `floor` unchanged. `save` does not clear `adopt`; only `take_adoption` does.
     - Otherwise return `TurnFloor(phase, loop, store.run_id, paths.highest_attempt(store.run_id, subtask.card_id, phase))`.
   - New imports: `agent_manager.paths`, `agent_manager.store.TurnFloor`, `agent_manager.workflow.phases.AgentPhase`. None of them import pygents or `runtime`, so they add no import cycle.
   - `before_turn` stays the only `turn` write point (E2). It and `on_pause` keep their current module-level `@hook(..., tags={"subtask"})` form and call `save` exactly as they do now. No new hooks are added.

3. `src/agent_manager/runtime/engine.py`
   - At `RunDeps(...)` construction (currently line 181, shared by both paths), pass `adopt=Adoption(**vars(resume_from.floor))` when `resume_from is not None and resume_from.floor is not None`. Otherwise pass `adopt=None`. A fresh run and a resume from a floor-less row (older rows, step heads) both start with no adoption.

## Observable behavior

- An agent-phase `turn` row and an agent-phase `parked` row each get a `checkpoint_floors` row with that turn's `(phase, loop)`. On a first run, its `source_run` is this run's id and its `floor` is the highest attempt directory on disk for that phase (0 if none).
- A resumed run that re-saves the same `(phase, loop)` before consuming the adoption writes the carried floor, with the original `source_run` and the original number. It does not write a recomputed one. This lets the floor survive resume chains.
- Step-head rows, `done` rows, `escalated` rows, and rows saved through a store with no `run_id` have `floor=None`.
- No CLI, envelope, exit-code, or report change. Nothing in this subtask reads the floor to skip or adopt a dispatch, and nothing writes a warning line. Consuming `take_adoption` belongs to a later task.

## Error paths

- `TurnFloor`/`checkpoint_floors` validation, such as a negative floor, stays the sibling's concern. A failing insert rolls back the checkpoint row as the sibling built it. This subtask adds no new exceptions.
- A missing or empty queue with no `current_turn` gives `floor=None`, not an `IndexError`, inside `_floor`. `on_pause`'s own `queue[0]` read is unchanged.

## Out of scope

Reading the source run's journal (`Store.replay_journal`), re-validating result files, re-running gates, the `AgentRunner.adopt`/dispatch-skip logic, the `warnings` line, adopting started/orphaned attempts, persisting step results, moving the checkpoint write point, and any change to `store.py`, `paths.py` or `dispatch.py`.

## Tests

The tier rule is `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14. These tests drive the engine/runtime with fakes (canned agent runner, temp SQLite+JSONL store, no real harness), so they go in the mirrored unit tree `tests/runtime/`, not `tests/e2e/`. They follow the existing conventions: the `fresh_pygents` autouse fixture, crashes as a `BaseException` subclass (`_Crash`) raised at a named point, and no sleeps.

`tests/runtime/test_checkpoint.py` (runtime tier: pure unit plus temp store):
- `take_adoption` returns the adoption on a `(phase, loop)` match and clears it, so a second call returns `None`.
- `take_adoption` on a mismatch returns `None` and still clears `adopt`.
- An agent-phase `turn` save under a store with `run_id` and no adoption records `TurnFloor(phase, loop, run_id, highest_attempt)`. Seed `{phase}.1`/`{phase}.2` attempt dirs and assert floor 2. With no dirs, assert floor 0.
- A `parked` save whose queue head is an agent phase records a floor for that head.
- A save whose next turn is a step phase records `floor=None`.
- `done` and `escalated` saves record `floor=None`.
- A store without `run_id` records `floor=None`.
- With `deps.adopt` matching the next turn, the saved floor equals the adoption (original `source_run` and `floor`), even when `highest_attempt` on disk differs. With a non-matching `deps.adopt`, the floor is recomputed.

`tests/runtime/test_resume.py` (engine-with-fakes tier, resume exercised via a crash mid-phase):
- Resuming from a checkpoint whose `floor` is set builds `RunDeps.adopt == Adoption(**vars(floor))`. The next `turn` row for that `(phase, loop)` carries the same floor (`source_run` of the first run) rather than the resumed run's id.
- Resuming from a checkpoint with `floor=None` gives `adopt=None`, and the next agent-phase row gets a freshly computed floor under the resuming run's id.

Verification: `uv run pytest` (full suite; no lint or typecheck commands exist).
