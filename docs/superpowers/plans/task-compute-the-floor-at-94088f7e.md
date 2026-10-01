<!-- task-pipeline: validated -->
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

---

# Floor at Save Time, Carried Across a Resume: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every agent-phase `turn`/`parked` checkpoint gets a `TurnFloor` computed at save time (or carried unchanged from the resume checkpoint), and a resumed run starts with `RunDeps.adopt` built from that checkpoint's floor.

**Architecture:** `runtime/state.py` gains a frozen `Adoption` value and `RunDeps.adopt` / `RunDeps.take_adoption`. `runtime/checkpoint.py`'s `save` asks a new private `_floor(deps, agent, reason)` for the floor and passes it to `Store.save_checkpoint(..., floor=)`, which sibling e600b86a already built. `runtime/engine.py` seeds `adopt` from `resume_from.floor` at the one `RunDeps(...)` construction.

**Tech Stack:** Python 3, pygents, SQLite (via `agent_manager.store`), pytest with `asyncio_mode = "auto"`, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-compute-the-floor-at-94088f7e/docs/superpowers/specs/task-compute-the-floor-at-94088f7e-design.md` (prepended above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m11/task-compute-the-floor-at-94088f7e`; run every command from there. Branch: `m11/task-compute-the-floor-at-94088f7e`, cut from `m11/task-save-a-floor-atomically-e600b86a`. Nothing is pushed.

## Global Constraints

- Only `src/agent_manager/runtime/state.py`, `src/agent_manager/runtime/checkpoint.py`, `src/agent_manager/runtime/engine.py` and their tests change. No change to `store.py`, `paths.py` or `dispatch.py`.
- Already present on this branch (sibling e600b86a), use as-is: `agent_manager.store.TurnFloor(phase: str, loop: int, source_run: str, floor: int)` (frozen dataclass), `Checkpoint.floor: TurnFloor | None = None`, `Store.save_checkpoint(card_id, *, workflow, digest, reason, agent, saved_at, floor: TurnFloor | None = None)`, `paths.highest_attempt(run_id: str, card: str, phase: str) -> int`.
- `before_turn` stays the only `turn` write point (E2). `before_turn` and `on_pause` keep their module-level `@hook(..., tags={"subtask"})` form and call `save` exactly as now. No new hooks.
- Steps are never floored (E9): a step head gets `floor=None`.
- `save` never clears `deps.adopt`; only `take_adoption` does. Nothing in this subtask calls `take_adoption` outside tests.
- No CLI, envelope, exit-code, report or warning-line change.
- Tests live in `tests/runtime/` (spec §14 runtime/engine-with-fakes tier), never `tests/e2e/`. Crashes are `_Crash(BaseException)`; no sleeps.
- Verification: `uv run pytest` (no lint, no typecheck).

## Review Focus

1. A next turn naming a phase the workflow does not have (stale or hand-edited kwargs): `_floor` must return `None`, not raise `WorkflowError` from `Workflow.phase`. Pinned in Task 2 (`test_an_unknown_phase_records_no_floor`).
2. A `turn`/`parked` save from an agent with no `current_turn` and an empty queue: `floor=None`, no `IndexError`. Pinned in Task 2 (`test_an_agent_with_no_next_turn_records_no_floor`).
3. An adoption for the right phase but a different loop (a critic looped back): the loop is part of the identity, so the floor is recomputed, not carried. Pinned in Task 2 (`test_a_non_matching_adoption_is_recomputed`, loop-mismatch case).
4. `save` leaving `deps.adopt` untouched, so a later re-save of the same turn still carries the floor. Pinned in Task 2 (`test_a_matching_adoption_is_carried_unchanged` asserts `deps.adopt` survives).
5. An `escalated` row on a resumed run whose `adopt` is still set: it must get `floor=None`, not the carried floor. Pinned in Task 3 (`test_a_carried_floor_survives_a_resume` asserts the closing `escalated` row has no floor).

---

### Task 1: `Adoption` and `RunDeps.take_adoption`

**Files:**
- Modify: `src/agent_manager/runtime/state.py:11-39`
- Test: `tests/runtime/test_checkpoint.py` (append; extend imports at lines 9-25)

**Interfaces:**
- Consumes: `agent_manager.store.TurnFloor` (sibling, only in a test).
- Produces: `agent_manager.runtime.state.Adoption(phase: str, loop: int, source_run: str, floor: int)` (frozen dataclass); `RunDeps.adopt: Adoption | None = None` (keyword field, last); `RunDeps.take_adoption(self, phase: str, loop: int) -> Adoption | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/runtime/test_checkpoint.py`, add `import dataclasses` to the stdlib imports (after `import asyncio`), and add this import after `from agent_manager.runtime import engine as runtime_engine`:

```python
from agent_manager.runtime.state import Adoption, RunDeps, current_run
```

Then append to the end of the file:

```python
# ── the floor (exactly-once 1.2, card 94088f7e) ──────────────────────────────


def _commit(card: str) -> dict[str, Any]:
    return {}


def _floor_workflow() -> Workflow:
    """One agent phase, `explore`, and one step, `commit`."""
    return Workflow(
        "floors", (AgentPhase("explore", "explorer", (), None), Step("commit", _commit))
    )


def _deps(opened: Any, adopt: Adoption | None = None) -> RunDeps:
    return RunDeps(
        _floor_workflow(), opened, STORY_ID, _subtask(), None, lambda: FIXED, adopt=adopt
    )


def test_an_adoption_is_built_from_a_turn_floor_and_is_frozen():
    adoption = Adoption(**vars(store_module.TurnFloor("explore", 1, "run-earlier", 2)))

    assert adoption == Adoption("explore", 1, "run-earlier", 2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        adoption.floor = 3


def test_run_deps_start_with_no_adoption():
    assert _deps(None).adopt is None


def test_take_adoption_returns_a_match_once():
    adoption = Adoption("explore", 0, "run-earlier", 3)
    deps = _deps(None, adopt=adoption)

    assert deps.take_adoption("explore", 0) == adoption
    assert deps.adopt is None
    assert deps.take_adoption("explore", 0) is None


@pytest.mark.parametrize("phase, loop", [("explore", 1), ("commit", 0)])
def test_take_adoption_clears_on_a_mismatch(phase, loop):
    deps = _deps(None, adopt=Adoption("explore", 0, "run-earlier", 3))

    assert deps.take_adoption(phase, loop) is None
    assert deps.adopt is None


def test_take_adoption_with_nothing_carried_returns_none():
    deps = _deps(None)

    assert deps.take_adoption("explore", 0) is None
    assert deps.adopt is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_checkpoint.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'Adoption' from 'agent_manager.runtime.state'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/runtime/state.py`, insert this class between `from agent_manager.runtime.stop import StopSignal` and `@dataclass\nclass RunDeps:`:

```python
@dataclass(frozen=True)
class Adoption:
    """A turn a resumed run carries from its checkpoint's floor (exactly-once E4/E5).

    Same fields as `store.TurnFloor`, so `Adoption(**vars(floor))` builds one.
    Plain values only: nothing here imports the store or pygents.
    """

    phase: str
    loop: int
    source_run: str
    floor: int


```

Then, inside `RunDeps`, after the `running` field and its docstring (after line 36), add:

```python
    adopt: Adoption | None = None
    """The floor carried from the resume checkpoint, until `take_adoption`
    consumes it. `checkpoint.save` reads it but never clears it."""

    def take_adoption(self, phase: str, loop: int) -> Adoption | None:
        """The carried adoption if it is for `(phase, loop)`, else `None`.

        Always clears `adopt`, on a mismatch too, so a second call returns `None`.
        """
        carried, self.adopt = self.adopt, None
        if carried is not None and (carried.phase, carried.loop) == (phase, loop):
            return carried
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_checkpoint.py -v`
Expected: PASS, every test in the file.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runtime/state.py tests/runtime/test_checkpoint.py
git commit -m "feat(runtime): Adoption and RunDeps.take_adoption"
```

---

### Task 2: `_floor` in `checkpoint.save`

**Files:**
- Modify: `src/agent_manager/runtime/checkpoint.py:26-56`
- Test: `tests/runtime/test_checkpoint.py` (append; extend imports)

**Interfaces:**
- Consumes: `Adoption`, `RunDeps.adopt` (Task 1); `store.TurnFloor`, `paths.highest_attempt(run_id, card, phase) -> int`, `Store.save_checkpoint(..., floor=)` (sibling).
- Produces: `checkpoint._floor(deps: RunDeps, agent: Any, reason: str) -> TurnFloor | None`; `checkpoint.save(agent, reason)` now always passes `floor=` (a `TurnFloor` or `None`) to `store.save_checkpoint`.

- [ ] **Step 1: Write the failing tests**

In `tests/runtime/test_checkpoint.py`, change `from agent_manager import models, store as store_module` to:

```python
from agent_manager import models, paths, store as store_module
```

Then append to the end of the file:

```python
class _Stored:
    """A stand-in agent: `save` reads it only through `to_dict()`."""

    def __init__(self, current: dict | None = None, queue: tuple[dict, ...] = ()) -> None:
        self._state = {"current_turn": current, "queue": list(queue)}

    def to_dict(self) -> dict:
        return self._state


def _turn(phase: str, loop: int = 0) -> dict:
    return {"kwargs": {"phase": phase, "loop": loop}}


def _save(deps: RunDeps, agent: Any, reason: str) -> None:
    token = current_run.set(deps)
    try:
        checkpoint.save(agent, reason)
    finally:
        current_run.reset(token)


def _seed_attempts(phase: str, count: int) -> None:
    for attempt in range(1, count + 1):
        paths.attempt_dir(RUN_ID, CARD_ID, phase, attempt)


def _saved_floor(opened) -> store_module.TurnFloor | None:
    return opened.latest_checkpoint(CARD_ID).floor


def test_an_agent_turn_records_the_highest_attempt_on_disk(store):
    _seed_attempts("explore", 2)

    _save(_deps(store), _Stored(current=_turn("explore", 0)), "turn")

    assert _saved_floor(store) == store_module.TurnFloor("explore", 0, RUN_ID, 2)


def test_an_agent_turn_with_no_attempts_records_floor_zero(store):
    _save(_deps(store), _Stored(current=_turn("explore", 0)), "turn")

    assert _saved_floor(store) == store_module.TurnFloor("explore", 0, RUN_ID, 0)


def test_a_parked_row_records_the_floor_of_its_queue_head(store):
    _seed_attempts("explore", 1)

    _save(_deps(store), _Stored(queue=(_turn("explore", 1), _turn("commit", 1))), "parked")

    assert _saved_floor(store) == store_module.TurnFloor("explore", 1, RUN_ID, 1)


def test_the_turn_in_flight_wins_over_the_queue_head(store):
    _save(_deps(store), _Stored(current=_turn("commit"), queue=(_turn("explore"),)), "turn")

    assert _saved_floor(store) is None


def test_a_step_head_records_no_floor(store):
    _save(_deps(store), _Stored(queue=(_turn("commit"),)), "parked")

    assert _saved_floor(store) is None


def test_an_unknown_phase_records_no_floor(store):
    _save(_deps(store), _Stored(current=_turn("gone")), "turn")

    assert _saved_floor(store) is None


@pytest.mark.parametrize("reason", ["turn", "parked"])
def test_an_agent_with_no_next_turn_records_no_floor(store, reason):
    _save(_deps(store), _Stored(), reason)

    assert _saved_floor(store) is None


@pytest.mark.parametrize("reason", ["done", "escalated"])
def test_after_run_rows_record_no_floor(store, reason):
    _seed_attempts("explore", 2)

    _save(_deps(store), _Stored(current=_turn("explore", 0)), reason)

    assert store.latest_checkpoint(CARD_ID).reason == reason
    assert _saved_floor(store) is None


class _RunlessStore:
    """A store with no `run_id`, recording every `save_checkpoint` call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def save_checkpoint(self, card_id: str, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_a_store_without_a_run_records_no_floor():
    runless = _RunlessStore()

    _save(_deps(runless), _Stored(current=_turn("explore", 0)), "turn")

    assert len(runless.calls) == 1
    assert runless.calls[0]["floor"] is None


def test_a_matching_adoption_is_carried_unchanged(store):
    _seed_attempts("explore", 2)
    adoption = Adoption("explore", 1, "run-earlier", 7)
    deps = _deps(store, adopt=adoption)

    _save(deps, _Stored(current=_turn("explore", 1)), "turn")

    assert _saved_floor(store) == store_module.TurnFloor("explore", 1, "run-earlier", 7)
    assert deps.adopt == adoption  # save reads the adoption, never consumes it


@pytest.mark.parametrize(
    "adoption",
    [
        Adoption("explore", 0, "run-earlier", 7),  # same phase, another loop
        Adoption("commit", 1, "run-earlier", 7),  # another phase, same loop
    ],
)
def test_a_non_matching_adoption_is_recomputed(store, adoption):
    _seed_attempts("explore", 2)

    _save(_deps(store, adopt=adoption), _Stored(current=_turn("explore", 1)), "turn")

    assert _saved_floor(store) == store_module.TurnFloor("explore", 1, RUN_ID, 2)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_checkpoint.py -v`
Expected: FAIL in `test_an_agent_turn_records_the_highest_attempt_on_disk`, `test_an_agent_turn_with_no_attempts_records_floor_zero`, `test_a_parked_row_records_the_floor_of_its_queue_head`, `test_a_matching_adoption_is_carried_unchanged` and both `test_a_non_matching_adoption_is_recomputed` cases with `AssertionError: assert None == TurnFloor(...)`; `test_a_store_without_a_run_records_no_floor` fails with `KeyError: 'floor'`. The `floor is None` tests already pass (guards for the new branches).

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/runtime/checkpoint.py`, replace the import block (lines 28-33):

```python
from typing import Any

from pygents import AgentHook, hook

from agent_manager.runtime import walk
from agent_manager.runtime.state import current_run
```

with:

```python
from typing import Any

from pygents import AgentHook, hook

from agent_manager import paths
from agent_manager.runtime import walk
from agent_manager.runtime.state import RunDeps, current_run
from agent_manager.store import TurnFloor
from agent_manager.workflow.phases import AgentPhase

_FLOORED = ("turn", "parked")
"""The reasons whose row names a turn still to run, so it carries that turn's floor."""
```

Then replace `save` (lines 44-56) with:

```python
def save(agent: Any, reason: str) -> None:
    """Write `agent` as the next checkpoint of the running subtask, or nothing with no run."""
    deps = current_run.get(None)
    if deps is None:
        return
    deps.store.save_checkpoint(
        deps.subtask.card_id,
        workflow=deps.workflow.name,
        digest=deps.workflow.digest(),
        reason=reason,
        agent=agent.to_dict(),
        saved_at=walk._utcnow(),
        floor=_floor(deps, agent, reason),
    )


def _floor(deps: RunDeps, agent: Any, reason: str) -> TurnFloor | None:
    """The floor of the agent-phase turn `agent` would run next, or `None` (exactly-once 1.2).

    Only `turn` and `parked` rows name a turn still to run. The next turn is
    the one in flight, else the queue head; a step (E9), a phase the workflow
    does not have, or no turn at all gets no floor, and neither does a store
    with no run. A carried adoption for the same `(phase, loop)` is written
    unchanged, so the floor survives a chain of resumes; otherwise the floor
    is the highest attempt this run's directory holds for the phase.
    """
    if reason not in _FLOORED:
        return None
    state = agent.to_dict()
    turn = state.get("current_turn") or next(iter(state.get("queue") or ()), None)
    if turn is None:
        return None
    phase, loop = turn["kwargs"]["phase"], turn["kwargs"]["loop"]
    if not any(
        isinstance(p, AgentPhase) and p.name == phase for p in deps.workflow.phases
    ):
        return None
    run_id = getattr(deps.store, "run_id", None)
    if not run_id:
        return None
    carried = deps.adopt
    if carried is not None and (carried.phase, carried.loop) == (phase, loop):
        return TurnFloor(**vars(carried))
    return TurnFloor(
        phase, loop, run_id, paths.highest_attempt(run_id, deps.subtask.card_id, phase)
    )
```

Leave `before_turn` and `on_pause` exactly as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_checkpoint.py -v`
Expected: PASS, every test in the file (the existing step-only engine tests still pass: their heads are steps, so their rows get `floor=None`).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runtime/checkpoint.py tests/runtime/test_checkpoint.py
git commit -m "feat(runtime): compute the turn floor at checkpoint save time"
```

---

### Task 3: Seed `RunDeps.adopt` from the resume checkpoint's floor

**Files:**
- Modify: `src/agent_manager/runtime/engine.py:25` and `:181`
- Test: `tests/runtime/test_resume.py` (append; extend imports at lines 13-31)

**Interfaces:**
- Consumes: `Adoption`, `RunDeps.adopt` (Task 1); `checkpoint._floor` behaviour via `save` (Task 2); `Checkpoint.floor`, `TurnFloor` (sibling).
- Produces: `run_subtask` / `run_subtask_async` with `resume_from=` a checkpoint whose `floor` is set run with `current_run.get().adopt == Adoption(**vars(resume_from.floor))`; otherwise `adopt is None`.

- [ ] **Step 1: Write the failing tests**

In `tests/runtime/test_resume.py`, change `from agent_manager import models, store as store_module` to:

```python
from agent_manager import models, paths, store as store_module
```

and add after `from agent_manager.runtime.stop import StopSignal`:

```python
from agent_manager.runtime.state import Adoption, current_run
from agent_manager.store import TurnFloor
```

(`dataclasses` is already imported.) Then append to the end of the file:

```python
# ── the floor across a resume (exactly-once 1.2, card 94088f7e) ──────────────


def _crash_on_the_second_spec(names: list[str]):
    """`_loop_workflow`'s runner: the critic fails once, and the second pass of
    `spec` (loop 1) dies with `_Crash`."""

    def runner(phase, table, rendered):
        names.append(phase.name)
        if phase.name == "validate_spec":
            raise AgentPhaseFailed("validate_spec", outcome="gate_failed", detail="no error path")
        if phase.name == "spec" and names.count("spec") == 2:
            raise _Crash("killed on the second pass of spec")
        return {"ok": True}

    return runner


def _critic_fails_recording(seen: list[tuple[str, Adoption | None]]):
    """A resumed run's runner: records each phase with the run's `adopt`, and the
    critic fails again, which escalates (loop 1 already spent the Goto)."""

    def runner(phase, table, rendered):
        seen.append((phase.name, current_run.get().adopt))
        if phase.name == "validate_spec":
            raise AgentPhaseFailed(
                "validate_spec", outcome="gate_failed", detail="still no error path"
            )
        return {"ok": True}

    return runner


def _floors(opened) -> list[tuple[str, TurnFloor | None]]:
    """Every checkpoint row's reason and floor, oldest first."""
    rows = opened.connection.execute(
        "SELECT c.reason, f.phase, f.loop, f.source_run, f.floor"
        " FROM checkpoints c LEFT JOIN checkpoint_floors f"
        " ON f.run_id = c.run_id AND f.card_id = c.card_id AND f.seq = c.seq"
        " ORDER BY c.card_id, c.seq"
    ).fetchall()
    return [
        (row[0], None if row[1] is None else TurnFloor(row[1], row[2], row[3], row[4]))
        for row in rows
    ]


def _crash_in_the_loop(opened):
    names: list[str] = []
    wf = _loop_workflow()
    with pytest.raises(_Crash):
        _go(wf, opened, agent_runner=_crash_on_the_second_spec(names))
    assert names == ["spec", "validate_spec", "spec"]
    return wf, opened.latest_checkpoint(CARD_ID)


def test_a_carried_floor_survives_a_resume(store):
    wf, crashed = _crash_in_the_loop(store)
    # The first run floored the turn it died in under its own id.
    assert crashed.reason == "turn"
    assert crashed.floor == TurnFloor("spec", 1, RUN_ID, 0)
    # As if that row had itself been carried from an earlier run: the resume
    # must keep the earlier run's id and number, not recompute its own.
    carried = TurnFloor("spec", 1, "run-earlier", 7)
    seen: list[tuple[str, Adoption | None]] = []

    summary = _go(
        wf,
        store,
        agent_runner=_critic_fails_recording(seen),
        resume_from=dataclasses.replace(crashed, floor=carried),
    )

    assert seen[0] == ("spec", Adoption("spec", 1, "run-earlier", 7))
    assert summary.status == "escalated"
    assert _floors(store)[crashed.seq + 1:] == [
        ("turn", carried),
        ("turn", TurnFloor("validate_spec", 1, RUN_ID, 0)),
        ("escalated", None),
    ]


def test_a_floorless_row_resumes_with_a_fresh_floor(store):
    wf, crashed = _crash_in_the_loop(store)
    for attempt in (1, 2):
        paths.attempt_dir(RUN_ID, CARD_ID, "spec", attempt)
    seen: list[tuple[str, Adoption | None]] = []

    _go(
        wf,
        store,
        agent_runner=_critic_fails_recording(seen),
        resume_from=dataclasses.replace(crashed, floor=None),
    )

    assert seen[0] == ("spec", None)
    assert _floors(store)[crashed.seq + 1] == ("turn", TurnFloor("spec", 1, RUN_ID, 2))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_resume.py -k "floor" -v`
Expected: `test_a_carried_floor_survives_a_resume` FAILS at `assert seen[0] == ("spec", Adoption("spec", 1, "run-earlier", 7))` with `('spec', None) != ('spec', Adoption(...))`. `test_a_floorless_row_resumes_with_a_fresh_floor` passes already (it guards the `adopt=None` branch).

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/runtime/engine.py`, change line 25:

```python
from agent_manager.runtime.state import RunDeps, current_run
```

to:

```python
from agent_manager.runtime.state import Adoption, RunDeps, current_run
```

and replace line 181:

```python
        deps = RunDeps(workflow, store, story_id, subtask, agent_runner, clock, stop=stop)
```

with:

```python
        # The resume checkpoint's floor is carried as the run's adoption, so a
        # re-save of that turn writes it unchanged (exactly-once E4/E5). A fresh
        # run, or a row saved with no floor, starts with none.
        adopt = (
            None
            if resume_from is None or resume_from.floor is None
            else Adoption(**vars(resume_from.floor))
        )
        deps = RunDeps(
            workflow, store, story_id, subtask, agent_runner, clock, stop=stop, adopt=adopt
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: PASS, every test in the file.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runtime/engine.py tests/runtime/test_resume.py
git commit -m "feat(runtime): carry the resume checkpoint's floor as the run's adoption"
```

---

### Task 4: Full-suite verification

**Files:** none changed unless the suite exposes a regression.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: a green `uv run pytest`.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no failures or errors (the `e2e` marker stays deselected by `addopts`).

- [ ] **Step 2: If anything fails, diagnose before touching code**

Use superpowers:systematic-debugging. The likeliest cause is a test elsewhere that runs an agent phase through a fake store whose `run_id` is a non-string stand-in (e.g. a `MagicMock`), which now reaches `paths.highest_attempt`. Fix the fake in that test (give it no `run_id`, or a real string with `XDG_DATA_HOME` pointed at `tmp_path`), not `_floor`, and rerun `uv run pytest`.

- [ ] **Step 3: Commit any fix**

Only if Step 2 changed a file:

```bash
git add -A tests/
git commit -m "test: give agent-phase fake stores a real run id for the floor"
```
