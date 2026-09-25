<!-- task-pipeline: validated -->
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

---

# Run a Level's Stories on a Bounded Lane Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `run_milestone` runs each dependency level's stories as lanes on a `ThreadPoolExecutor(max_concurrent)`, with a barrier between levels, a per-run cooperative stop shared by every lane, one primary escalation, and parked lanes reported `stopped`; `max_concurrent=1` stays byte-identical to today.

**Architecture:** The per-story body of `run_milestone` moves into `run_story_lane`, which returns a frozen `LaneOutcome`. Per-run shared state (`threading.Event` plus `threading.Lock` plus the primary story id) lives in a `RunStop` dataclass that `run_milestone` creates per run, so the module still holds no mutable state. After each level's pool shuts down, outcomes are walked in census order and the pure `escalated_payload` builds the escalated result.

**Tech Stack:** Python 3, `concurrent.futures.ThreadPoolExecutor`, `threading`, pytest, real `git` and `brd` CLIs in the Steps-tier fixtures, `uv`.

**Spec:** `docs/superpowers/specs/task-run-a-level-s-stories-fe2c716b-design.md` (prepended above, verbatim).

**Working directory:** every path below is relative to the worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-run-a-level-s-stories-fe2c716b` (branch `m4/task-run-a-level-s-stories-fe2c716b`, cut from `m4/task-let-the-engine-stop-0d8b7c9a`). Run every command from there.

## Global Constraints

- Only `src/agent_manager/orchestrate.py` and `tests/test_orchestrate.py` change. Do not edit `engine.py`, `models.py`, `cli.py`, `store.py`, `board.py` or anything under `tests/e2e`.
- Precondition already on this branch (verify in Task 1 Step 0, do not build): `models.Status` contains `"stopped"`; `engine.SubtaskSummary.status` is `Literal["done", "escalated", "stopped"]`; `engine._stop` writes `detail = f"stopped before {phase_name}"`; `cli.drive_subtask` and `orchestrate.Driver` accept `should_stop: Callable[[], bool] | None = None`; `store.Store` serialises its record methods behind a `threading.RLock` and its connection has `check_same_thread=False`.
- `run_milestone` gains `max_concurrent: int = 1`; the default stays 1 (the default of 4 and the CLI flag belong to sibling 69bcf17e).
- `max_concurrent < 1` raises `ValueError` before any git call, store open or run directory.
- The run's config is `models.RunConfig(max_concurrent_stories=max_concurrent)`.
- `cli` is imported as a module and every `cli` name is read at call time (circular import). The module holds no mutable state; the lock and event are created per run inside `run_milestone`.
- Catch `Exception`, never `BaseException`, as an escalation. A `BaseException` sets the stop event and propagates.
- The escalated payload keys are exactly `escalated`, `run_id`, `level`, `story`, `subtask`, `failed_phase`, `detail`, `warnings`, plus `also_escalated` and `stopped` only when non-empty. The clean payload is unchanged.
- Every existing test in `tests/test_orchestrate.py` passes without modification except the `FakeDriver` signature gaining `should_stop=None` (not recorded in its call dict).
- Tests synchronise with `threading.Barrier` and `threading.Event` only, each with a timeout (`WAIT = 10.0`), never `time.sleep`.
- Verification: `uv run pytest` (full suite, no separate lint or typecheck).

## Review Focus

- A board read (`board.show`) that raises inside a lane, before the driver is called, should become an escalation of that subtask rather than tearing down the run with sibling lanes orphaned. Test: `test_a_board_read_that_fails_inside_a_lane_is_an_escalation_of_that_subtask` (Task 3).
- A Ctrl-C (`KeyboardInterrupt`) in one parallel lane should still propagate to the caller, and the sibling lane should park and be recorded `stopped` rather than run on. Test: `test_a_keyboard_interrupt_in_one_lane_parks_the_other_and_propagates` (Task 5).
- Lanes finishing out of census order should still produce `completed` and `warnings` in census order, so the payload is deterministic. Test: `test_warnings_and_completed_follow_census_order_not_finish_order` (Task 5).
- A story whose first subtask finishes `done` after the stop is set should drive its next subtask only to have the engine park it before its first phase: first subtask `done`, second `stopped`, story `stopped`, and the report names the second subtask. Test: `test_a_lane_whose_first_subtask_finished_parks_its_next_subtask` (Task 5).
- A negative bound (not only 0) should be refused before any side effect. Test: `test_a_bound_below_one_is_refused_before_anything_is_written[-1]` (Task 4).

---

## File Structure

- Modify `src/agent_manager/orchestrate.py`: new imports; new `STOPPED_PREFIX`, `stopped_before_phase`, `LaneKind`, `LaneOutcome`, `RunStop`, `escalated_payload`, `run_story_lane`; `run_milestone` gains `max_concurrent`, the lane pool and the level barrier; module docstring updated.
- Modify `tests/test_orchestrate.py`: new imports; `FakeDriver` gains `should_stop=None`; new unit tests for the pure helpers; new `WAIT`, `_meet`, `_meet_then_await_stop`, `_await`, `_await_stop`, `_census_levels`, `_subtasks_by_story`, `GatedDriver`; new Steps-tier `run_milestone` tests.

Existing test file layout: pure unit tests sit under `# ── pure plans ──` (lines 30-125), the Steps-tier runner tests under `# ── the runner, on a real repo and a real board ──` (line 127 onward), with `FakeDriver` at lines 234-289 and `_run` at 291-300. New unit tests go at the end of the pure section (after line 124); new Steps-tier tests go at the end of the file.

---

### Task 1: The before-phase helper

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (insert after `MILESTONE_WORKFLOW`, lines 36-37)
- Test: `tests/test_orchestrate.py` (append to the pure section, after line 124)

**Interfaces:**
- Consumes: nothing.
- Produces: `orchestrate.STOPPED_PREFIX: str = "stopped before "`; `orchestrate.stopped_before_phase(detail: str | None) -> str | None`.

- [ ] **Step 0: Confirm the P4 precondition is on this branch**

Run: `uv run python -c "import typing, inspect; from agent_manager import models, engine, cli, orchestrate; assert 'stopped' in typing.get_args(models.Status); assert 'should_stop' in inspect.signature(cli.drive_subtask).parameters; print('ok')"`
Expected: `ok`. If it fails, stop and report: this card depends on 9bfb5ac2 and must not absorb it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrate.py`, directly after `test_story_tips_name_every_story_with_subtasks_in_census_order` (ends line 124):

```python
def test_the_before_phase_is_read_out_of_a_stopped_detail():
    """`engine._stop` writes "stopped before <phase>"; the summary has no field
    of its own for that phase, so the helper reads it out of `detail`."""
    assert orchestrate.stopped_before_phase("stopped before implement") == "implement"
    assert orchestrate.stopped_before_phase("reviewer found a blocker") is None
    assert orchestrate.stopped_before_phase(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrate.py::test_the_before_phase_is_read_out_of_a_stopped_detail -v`
Expected: FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'stopped_before_phase'`

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/orchestrate.py`, directly after the `MILESTONE_WORKFLOW` constant and its docstring (line 37), insert:

```python


STOPPED_PREFIX = "stopped before "
"""How `engine._stop` opens a stopped subtask's `detail` (addendum P4)."""


def stopped_before_phase(detail: str | None) -> str | None:
    """The phase a stopped subtask would have run next, read out of its detail.

    `engine._stop` writes `"stopped before <phase>"` and the summary has no
    field of its own for the phase, so this strips the prefix. A detail without
    the prefix, or no detail at all, gives None.
    """
    if detail is None or not detail.startswith(STOPPED_PREFIX):
        return None
    return detail[len(STOPPED_PREFIX):]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrate.py::test_the_before_phase_is_read_out_of_a_stopped_detail -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "Read a stopped subtask's next phase out of its detail"
```

---

### Task 2: Lane outcome, per-run stop and the escalated payload

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (imports lines 23-29; insert new definitions after `stopped_before_phase` from Task 1)
- Test: `tests/test_orchestrate.py` (pure section, after the Task 1 test)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `orchestrate.LaneKind = Literal["done", "escalated", "stopped", "not_started"]`
  - `@dataclass(frozen=True) class LaneOutcome: kind: LaneKind; story: str; level: int; subtask: str | None = None; failed_phase: str | None = None; detail: str | None = None; before_phase: str | None = None; completed: tuple[str, ...] = (); warnings: tuple[str, ...] = ()`
  - `@dataclass class RunStop: event: threading.Event; lock: threading.Lock; primary: str | None = None; def escalate(self, story_id: str) -> None`
  - `escalated_payload(run_id: str, primary_story: str | None, outcomes: Sequence[LaneOutcome], warnings: list[str]) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrate.py` after `test_the_before_phase_is_read_out_of_a_stopped_detail`:

```python
def test_the_first_escalation_is_the_primary_and_every_escalation_sets_the_stop():
    stop = orchestrate.RunStop()
    assert not stop.event.is_set()
    assert stop.primary is None

    stop.escalate("story-b")
    stop.escalate("story-a")

    assert stop.event.is_set()
    assert stop.primary == "story-b"


def test_the_escalated_payload_names_the_primary_and_lists_the_rest_in_census_order():
    also = orchestrate.LaneOutcome(
        kind="escalated",
        story="A",
        level=2,
        subtask="a1",
        failed_phase="review",
        detail="reviewer found a blocker",
    )
    parked = orchestrate.LaneOutcome(
        kind="stopped", story="B", level=2, subtask="b2", before_phase="implement"
    )
    primary = orchestrate.LaneOutcome(
        kind="escalated",
        story="C",
        level=2,
        subtask="c1",
        failed_phase=None,
        detail="RuntimeError: boom",
    )
    done = orchestrate.LaneOutcome(kind="done", story="D", level=2, completed=("d1",))
    queued = orchestrate.LaneOutcome(kind="not_started", story="E", level=2)

    payload = orchestrate.escalated_payload(
        "run-1", "C", [also, parked, primary, done, queued], ["gate warned"]
    )

    assert payload == {
        "escalated": True,
        "run_id": "run-1",
        "level": 2,
        "story": "C",
        "subtask": "c1",
        "failed_phase": None,
        "detail": "RuntimeError: boom",
        "warnings": ["gate warned"],
        "also_escalated": [
            {
                "level": 2,
                "story": "A",
                "subtask": "a1",
                "failed_phase": "review",
                "detail": "reviewer found a blocker",
            }
        ],
        "stopped": [{"story": "B", "subtask": "b2", "before_phase": "implement"}],
    }


def test_a_lone_escalation_payload_is_exactly_the_sequential_one():
    """No `also_escalated` or `stopped` key when those lists are empty, so a
    run at `max_concurrent=1` returns today's dict."""
    only = orchestrate.LaneOutcome(
        kind="escalated", story="A", level=0, subtask="a1", failed_phase="verify", detail="red"
    )
    queued = orchestrate.LaneOutcome(kind="not_started", story="B", level=0)

    payload = orchestrate.escalated_payload("run-1", "A", [only, queued], [])

    assert payload == {
        "escalated": True,
        "run_id": "run-1",
        "level": 0,
        "story": "A",
        "subtask": "a1",
        "failed_phase": "verify",
        "detail": "red",
        "warnings": [],
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "primary or escalated_payload or lone_escalation" -v`
Expected: 3 FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'RunStop'` / `'LaneOutcome'`

- [ ] **Step 3: Write minimal implementation**

In `src/agent_manager/orchestrate.py`, replace the imports block (lines 23-29):

```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
```

with:

```python
from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
```

Then, directly after `stopped_before_phase` (Task 1), insert:

```python


LaneKind = Literal["done", "escalated", "stopped", "not_started"]
"""How one story's lane ended: finished, escalated, parked by the stop after it
had started, or never started because the stop was already set."""


@dataclass(frozen=True)
class LaneOutcome:
    """What one story's lane did. Internal state, so a dataclass (CLAUDE.md).

    `subtask` is the subtask that escalated or was parked. `failed_phase` and
    `detail` describe an escalation, `before_phase` a stop. `completed` and
    `warnings` are this lane's own, in the order they arrived; `run_milestone`
    merges them across lanes in census order.
    """

    kind: LaneKind
    story: str
    level: int
    subtask: str | None = None
    failed_phase: str | None = None
    detail: str | None = None
    before_phase: str | None = None
    completed: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass
class RunStop:
    """One run's cooperative stop, shared by every lane of that run (P4, P6).

    `run_milestone` creates one per run, so this module still holds no mutable
    state of its own. Each lane hands `event.is_set` to the driver as
    `should_stop`. `lock` decides which escalation came first, so `primary` is
    well defined however the lanes interleave.
    """

    event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    primary: str | None = None

    def escalate(self, story_id: str) -> None:
        """Set the stop, and name `story_id` the primary escalation if none is yet."""
        with self.lock:
            if self.primary is None:
                self.primary = story_id
            self.event.set()


def escalated_payload(
    run_id: str,
    primary_story: str | None,
    outcomes: Sequence[LaneOutcome],
    warnings: list[str],
) -> dict[str, Any]:
    """The escalated result for one level's lane outcomes, given in census order.

    The top-level keys describe the primary escalation, as the sequential
    runner always did. `also_escalated` lists the other escalations and
    `stopped` the parked lanes, both in census order, and each key is present
    only when its list is non-empty. A `primary_story` that names no escalated
    outcome falls back to the first escalation in census order.
    """
    escalations = [outcome for outcome in outcomes if outcome.kind == "escalated"]
    primary = next(
        (outcome for outcome in escalations if outcome.story == primary_story),
        escalations[0],
    )
    payload: dict[str, Any] = {
        "escalated": True,
        "run_id": run_id,
        "level": primary.level,
        "story": primary.story,
        "subtask": primary.subtask,
        "failed_phase": primary.failed_phase,
        "detail": primary.detail,
        "warnings": warnings,
    }
    also = [
        {
            "level": outcome.level,
            "story": outcome.story,
            "subtask": outcome.subtask,
            "failed_phase": outcome.failed_phase,
            "detail": outcome.detail,
        }
        for outcome in escalations
        if outcome is not primary
    ]
    stopped = [
        {"story": outcome.story, "subtask": outcome.subtask, "before_phase": outcome.before_phase}
        for outcome in outcomes
        if outcome.kind == "stopped"
    ]
    if also:
        payload["also_escalated"] = also
    if stopped:
        payload["stopped"] = stopped
    return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "primary or escalated_payload or lone_escalation or before_phase" -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "Add the lane outcome, the per-run stop and the escalated payload"
```

---

### Task 3: Extract one story's lane out of `run_milestone`

Sequential still: this task moves the per-story body into `run_story_lane`, wires `should_stop`, records a `stopped` summary, and widens the `Exception` catch to cover the whole subtask body (spec item 6: "anywhere inside a lane after it picked up a subtask").

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (insert `run_story_lane` before `run_milestone`, currently line 236; replace `run_milestone`'s walk, currently lines 288-346)
- Modify: `tests/test_orchestrate.py:249-261` (`FakeDriver.__call__` signature)
- Test: `tests/test_orchestrate.py` (append at end of file)

**Interfaces:**
- Consumes: `LaneOutcome`, `RunStop`, `escalated_payload`, `stopped_before_phase` (Tasks 1-2); `Driver` (orchestrate.py:46-66); `PlannedStory`; `record_plan`'s return type `dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]`.
- Produces: `run_story_lane(planned: PlannedStory, *, store: Store, run_id: str, rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]], root: Path, drive: Driver, commands: Sequence[str], allow_no_verification: bool, runner_factory: cli.RunnerFactory | None, stop: RunStop) -> LaneOutcome`.

- [ ] **Step 1: Give `FakeDriver` the `should_stop` keyword**

The lane will pass `should_stop=` to every driver; without this every existing test would turn into a `TypeError` escalation. In `tests/test_orchestrate.py`, replace:

```python
        commands=(),
        allow_no_verification=False,
        runner_factory=None,
    ) -> cli.SubtaskDrive:
        self.calls.append(
```

with:

```python
        commands=(),
        allow_no_verification=False,
        runner_factory=None,
        should_stop=None,
    ) -> cli.SubtaskDrive:
        self.calls.append(
```

Do not add `should_stop` to the recorded call dict.

- [ ] **Step 2: Write the failing test**

Append to the end of `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
def test_a_board_read_that_fails_inside_a_lane_is_an_escalation_of_that_subtask(
    project, monkeypatch
):
    """Spec item 6: an `Exception` anywhere inside a lane after it picked up a
    subtask escalates that subtask, not only one raised by the driver."""
    shape = _milestone(project, {"A": 1, "B": 1}, blocked_by={"B": ["A"]})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    real_show = board.show

    def failing_show(card_id: str, *, repo_dir: Any = None) -> models.Card:
        if card_id == a1:
            raise board.BoardError("brd is down", argv=["brd", "show", card_id])
        return real_show(card_id, repo_dir=repo_dir)

    monkeypatch.setattr(board, "show", failing_show)
    driver = FakeDriver()

    result = _run(project, shape["milestone"], driver)

    assert driver.calls == []
    assert (result["escalated"], result["level"], result["story"], result["subtask"]) == (
        True,
        0,
        story_a,
        a1,
    )
    assert result["failed_phase"] is None
    assert result["detail"].startswith("BoardError: ")
    assert "brd is down" in result["detail"]
    assert "also_escalated" not in result
    assert "stopped" not in result
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "pending",
        b1: "pending",
    }
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrate.py::test_a_board_read_that_fails_inside_a_lane_is_an_escalation_of_that_subtask -v`
Expected: FAIL with `agent_manager.board.BoardError: brd is down` propagating out of `run_milestone` (today only the `drive(...)` call is inside the `try`).

- [ ] **Step 4: Add `run_story_lane`**

In `src/agent_manager/orchestrate.py`, directly before `def run_milestone(` (after `record_plan`), insert:

```python
def run_story_lane(
    planned: PlannedStory,
    *,
    store: Store,
    run_id: str,
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]],
    root: Path,
    drive: Driver,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: cli.RunnerFactory | None,
    stop: RunStop,
) -> LaneOutcome:
    """Drive one story's remaining subtasks in order, and say how the lane ended.

    The stop is checked here once, before the story's first subtask: if it is
    already set the story never starts and its rows stay `pending`. After that
    the lane never checks it itself; it hands `stop.event.is_set` to the driver
    and the engine parks between phases (P4), so a running phase is never
    interrupted. A `stopped` summary records the subtask and story `stopped`.
    Any other non-`done` result, or an `Exception` raised while handling a
    subtask, escalates through `stop.escalate`. A `BaseException` sets the stop
    so sibling lanes park, and propagates.
    """
    story_id = planned.story.id
    level = planned.level
    if stop.event.is_set():
        return LaneOutcome(kind="not_started", story=story_id, level=level)

    story_row, subtask_rows = rows[story_id]
    completed: list[str] = []
    warnings: list[str] = []
    for position, subtask in enumerate(planned.remaining):
        row = subtask_rows[subtask.id]
        try:
            card = board.show(subtask.id, repo_dir=root)
            parent = board.show(story_id, repo_dir=root)
            row = row.model_copy(update={"status": "started"})
            store.record_subtask(story_id, row)
            if position == 0:
                store.record_story(story_row.model_copy(update={"status": "started"}))
            result = drive(
                store=store,
                run_id=run_id,
                card=card,
                parent=parent,
                subtask=row,
                repo_dir=root,
                commands=list(commands),
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                should_stop=stop.event.is_set,
            )
        except Exception as error:  # not BaseException: Ctrl-C must still stop
            status = "escalated"
            failed_phase: str | None = None
            detail: str | None = f"{type(error).__name__}: {error}"
        except BaseException:
            stop.event.set()
            raise
        else:
            warnings.extend(result.warnings)
            status = result.summary.status
            failed_phase = result.summary.failed_phase
            detail = result.summary.detail

        # A `stopped` summary is handled before the non-`done` branch: a stop
        # is not an escalation (P4).
        if status == "stopped":
            store.record_subtask(story_id, row.model_copy(update={"status": "stopped"}))
            store.record_story(story_row.model_copy(update={"status": "stopped"}))
            return LaneOutcome(
                kind="stopped",
                story=story_id,
                level=level,
                subtask=subtask.id,
                before_phase=stopped_before_phase(detail),
                completed=tuple(completed),
                warnings=tuple(warnings),
            )
        if status != "done":
            stop.escalate(story_id)
            store.record_subtask(story_id, row.model_copy(update={"status": "escalated"}))
            store.record_story(story_row.model_copy(update={"status": "escalated"}))
            return LaneOutcome(
                kind="escalated",
                story=story_id,
                level=level,
                subtask=subtask.id,
                failed_phase=failed_phase,
                detail=detail,
                completed=tuple(completed),
                warnings=tuple(warnings),
            )

        store.record_subtask(story_id, row.model_copy(update={"status": "done"}))
        completed.append(subtask.id)

    store.record_story(story_row.model_copy(update={"status": "done"}))
    return LaneOutcome(
        kind="done",
        story=story_id,
        level=level,
        completed=tuple(completed),
        warnings=tuple(warnings),
    )


```

- [ ] **Step 5: Make `run_milestone` walk lanes (still sequential)**

In `run_milestone`, replace everything from `        completed: list[str] = []` through `                store.record_story(story_row.model_copy(update={"status": "done"}))` (the whole `for level in levels:` loop, currently lines 288-346) with:

```python
        completed: list[str] = []
        stop = RunStop()

        for level in levels:
            outcomes = [
                run_story_lane(
                    planned,
                    store=store,
                    run_id=run_id,
                    rows=rows,
                    root=root,
                    drive=drive,
                    commands=list(commands),
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    stop=stop,
                )
                for planned in level
            ]
            # Census order, never completion order: deterministic at any bound.
            for outcome in outcomes:
                completed.extend(outcome.completed)
                warnings.extend(outcome.warnings)
            if any(outcome.kind == "escalated" for outcome in outcomes):
                store.record_run(run_record.model_copy(update={"status": "escalated"}))
                return escalated_payload(run_id, stop.primary, outcomes, warnings)
```

Leave the `store.record_run(... "done")` line and the clean payload after the loop unchanged. The old comment about `stopped` ("Every non-`done` result is recorded `escalated` here...") is gone with the old loop body.

- [ ] **Step 6: Run the new test and the whole orchestrate file**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS, including every pre-existing test (`test_an_escalation_stops_the_run_before_the_next_story`, `test_a_driver_that_raises_is_recorded_as_an_escalation`, `test_a_keyboard_interrupt_from_the_driver_is_not_swallowed`, `test_every_drivers_warnings_reach_the_result_in_order`) unmodified.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "Run each story as a lane that passes the run's stop to the driver"
```

---

### Task 4: `max_concurrent` is validated and recorded

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (`run_milestone` signature, first body line, and the `models.RunConfig()` in `run_record`)
- Test: `tests/test_orchestrate.py` (append at end of file)

**Interfaces:**
- Consumes: `run_milestone` from Task 3.
- Produces: `run_milestone(..., clock: Callable[[], datetime] = _utcnow, max_concurrent: int = 1) -> dict[str, Any]`; raises `ValueError` whose message contains `max_concurrent` when `max_concurrent < 1`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
@pytest.mark.parametrize("bound", [0, -1])
def test_a_bound_below_one_is_refused_before_anything_is_written(project, monkeypatch, bound):
    shape = _milestone(project, {"A": 1})
    git_calls = _record_git(monkeypatch)
    driver = FakeDriver()

    with pytest.raises(ValueError, match="max_concurrent"):
        _run(project, shape["milestone"], driver, max_concurrent=bound)

    assert driver.calls == []
    assert git_calls == []
    assert list(paths.data_dir().iterdir()) == []


@requires_git
@requires_brd
def test_the_bound_is_recorded_in_the_run_config(project):
    shape = _milestone(project, {"A": 1})

    result = _run(project, shape["milestone"], FakeDriver(), max_concurrent=2)

    assert result["done"] is True
    assert _load(project, result["run_id"]).config == models.RunConfig(max_concurrent_stories=2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "bound_below_one or bound_is_recorded" -v`
Expected: 3 FAIL with `TypeError: run_milestone() got an unexpected keyword argument 'max_concurrent'`

- [ ] **Step 3: Write minimal implementation**

In `run_milestone`'s signature, replace:

```python
    clock: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
```

with:

```python
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
) -> dict[str, Any]:
```

Replace the first body line:

```python
    root = cli.resolve_repo_dir(repo_dir)
```

with:

```python
    if max_concurrent < 1:
        raise ValueError(f"max_concurrent must be at least 1, got {max_concurrent}")
    root = cli.resolve_repo_dir(repo_dir)
```

In `run_record`, replace:

```python
            config=models.RunConfig(),
```

with:

```python
            config=models.RunConfig(max_concurrent_stories=max_concurrent),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS (the existing `assert run.config == models.RunConfig()` still holds at the default of 1).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "Validate and record run_milestone's max_concurrent"
```

---

### Task 5: The bounded lane pool with a level barrier

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (imports; module docstring lines 1-21; `run_milestone` docstring and walk loop)
- Modify: `tests/test_orchestrate.py:14-27` (imports)
- Test: `tests/test_orchestrate.py` (new helpers after `_record_git`, currently ending line 335; new tests at end of file)

**Interfaces:**
- Consumes: `run_story_lane`, `RunStop`, `escalated_payload` (Tasks 2-3), `max_concurrent` (Task 4).
- Produces: nothing new public; `run_milestone` now runs each level on `ThreadPoolExecutor(max_workers=max_concurrent)`. Test helpers: `WAIT: float`, `_meet(barrier) -> gate`, `_meet_then_await_stop(barrier) -> gate`, `_await(event) -> None`, `_await_stop(should_stop) -> None`, `_census_levels(project, milestone) -> list[list[str]]`, `_subtasks_by_story(shape) -> dict[str, list[str]]`, `GatedDriver`.

- [ ] **Step 1: Add the test imports**

In `tests/test_orchestrate.py`, replace:

```python
import json
import shutil
import subprocess
from dataclasses import dataclass, field
```

with:

```python
import json
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
```

- [ ] **Step 2: Add the gated driver and its helpers**

Insert directly after `_record_git` (after its `return calls`, currently line 335):

```python


WAIT = 10.0
"""Seconds a lane-pool test waits on a barrier or event before failing instead of hanging."""

Gate = Callable[[Any], None]


def _await(event: threading.Event) -> None:
    assert event.wait(timeout=WAIT), "a gated test's event was never set"


def _await_stop(should_stop: Any) -> None:
    """Block until the run's stop is set, without sleeping.

    `run_milestone` hands each driver `event.is_set` (spec item 2), so the
    run's event is that bound method's `__self__`. Waiting on it is
    synchronisation by event, and it pins that wiring.
    """
    assert should_stop is not None, "the lane passed no should_stop"
    event = should_stop.__self__
    assert isinstance(event, threading.Event)
    _await(event)


def _meet(barrier: threading.Barrier) -> Gate:
    """A gate that holds a call until every party of `barrier` is in flight."""

    def gate(should_stop: Any) -> None:
        barrier.wait(timeout=WAIT)

    return gate


def _meet_then_await_stop(barrier: threading.Barrier) -> Gate:
    """A gate that meets `barrier`, then holds the call until the run's stop is set."""

    def gate(should_stop: Any) -> None:
        barrier.wait(timeout=WAIT)
        _await_stop(should_stop)

    return gate


def _census_levels(project: Path, milestone: str) -> list[list[str]]:
    """Each dispatch level's story ids in census order, the order lanes are submitted in.

    Sibling stories created in the same second are ordered by id, so a test that
    gives a lane a role by its queue position must read the order, not assume it.
    """
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=project))
    levels = orchestrate.plan_levels(plan.stories, branch_prefix=PREFIX, base_branch="main")
    return [[planned.story.id for planned in level] for level in levels]


def _subtasks_by_story(shape: dict[str, Any]) -> dict[str, list[str]]:
    return {shape["stories"][key]: shape["subtasks"][key] for key in shape["stories"]}


@dataclass
class GatedDriver:
    """A thread-safe stand-in for `cli.drive_subtask`, for the lane-pool tests.

    `gates[card]` runs first, with the driver's `should_stop`; tests put
    barriers and events there, never sleeps. Then `outcomes[card]` decides: an
    exception instance is raised, a `(phase, detail)` tuple escalates, and
    `"done"` finishes as a phase already running would. With no entry the fake
    reaches its simulated phase boundary: if `should_stop()` is true it parks
    as the engine does, with `"stopped before implement"`; otherwise it is
    done. Calls are recorded under a lock, `high_water` is the most calls ever
    in flight at once, and `returned[card]` is set when that card's call ends.
    """

    outcomes: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Gate] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    returned: dict[str, threading.Event] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    high_water: int = 0
    in_flight: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(
        self,
        *,
        store,
        run_id,
        card,
        parent,
        subtask,
        repo_dir,
        commands=(),
        allow_no_verification=False,
        runner_factory=None,
        should_stop=None,
    ) -> cli.SubtaskDrive:
        with self.lock:
            self.calls.append(
                {"card": card.id, "parent": parent.id, "base": subtask.base_branch}
            )
            self.in_flight += 1
            self.high_water = max(self.high_water, self.in_flight)
        try:
            gate = self.gates.get(card.id)
            if gate is not None:
                gate(should_stop)
            outcome = self.outcomes.get(card.id)
            if isinstance(outcome, BaseException):
                raise outcome
            warnings = list(self.warnings.get(card.id, []))
            if isinstance(outcome, tuple):
                phase, detail = outcome
                summary = engine.SubtaskSummary(
                    status="escalated", failed_phase=phase, detail=detail
                )
            elif outcome != "done" and should_stop is not None and should_stop():
                summary = engine.SubtaskSummary(
                    status="stopped", detail="stopped before implement"
                )
            else:
                summary = engine.SubtaskSummary(status="done")
            return cli.SubtaskDrive(summary=summary, warnings=warnings)
        finally:
            with self.lock:
                self.in_flight -= 1
            if card.id in self.returned:
                self.returned[card.id].set()
```

- [ ] **Step 3: Write the failing lane-pool tests**

Append to the end of `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
def test_every_story_of_a_level_runs_at_once_and_each_keeps_its_subtask_order(project):
    shape = _milestone(project, {"A": 2, "B": 1, "C": 1})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    all_three = threading.Barrier(3)
    driver = GatedDriver(gates={a1: _meet(all_three), b1: _meet(all_three), c1: _meet(all_three)})

    result = _run(project, shape["milestone"], driver, max_concurrent=3)

    (order,) = _census_levels(project, shape["milestone"])
    subtasks = _subtasks_by_story(shape)
    cards = [call["card"] for call in driver.calls]
    assert sorted(cards) == sorted([a1, a2, b1, c1])
    assert cards.index(a1) < cards.index(a2)
    assert {call["card"]: call["base"] for call in driver.calls} == {
        a1: "main",
        a2: _branch(project, a1),
        b1: "main",
        c1: "main",
    }
    assert {call["card"]: call["parent"] for call in driver.calls} == {
        a1: story_a,
        a2: story_a,
        b1: story_b,
        c1: story_c,
    }
    assert driver.high_water == 3
    assert result["done"] is True
    assert result["levels"] == [{"level": 0, "stories": order}]
    assert result["completed"] == [card for story in order for card in subtasks[story]]
    assert "also_escalated" not in result
    assert "stopped" not in result
    run = _load(project, result["run_id"])
    assert run.config == models.RunConfig(max_concurrent_stories=3)
    assert set(_statuses(run).values()) == {"done"}


@requires_git
@requires_brd
def test_in_flight_lanes_never_exceed_the_bound(project):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1, "D": 1})
    (order,) = _census_levels(project, shape["milestone"])
    subtasks = _subtasks_by_story(shape)
    pair = threading.Barrier(2)
    driver = GatedDriver(
        gates={subtasks[order[0]][0]: _meet(pair), subtasks[order[1]][0]: _meet(pair)}
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True
    assert sorted(call["card"] for call in driver.calls) == sorted(
        card for cards in subtasks.values() for card in cards
    )
    assert driver.high_water == 2


@requires_git
@requires_brd
def test_an_escalation_parks_the_other_lane_and_no_later_level_starts(project):
    shape = _milestone(project, {"A": 1, "B": 2, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: ("review", "reviewer found a blocker")},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert sorted(call["card"] for call in driver.calls) == sorted([a1, b1])
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
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
        b2: "pending",
        story_c: "pending",
        c1: "pending",
    }


@requires_git
@requires_brd
def test_a_lane_whose_first_subtask_finished_parks_its_next_subtask(project):
    """After a story has started the lane never checks the stop itself: its
    next subtask is handed to the driver, and the engine parks it (P4)."""
    shape = _milestone(project, {"A": 1, "B": 2})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: ("verify", "suite red"), b1: "done"},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert sorted(call["card"] for call in driver.calls) == sorted([a1, b1, b2])
    assert next(call for call in driver.calls if call["card"] == b2)["base"] == _branch(
        project, b1
    )
    assert (result["story"], result["subtask"]) == (story_a, a1)
    assert result["stopped"] == [{"story": story_b, "subtask": b2, "before_phase": "implement"}]
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "done",
        b2: "stopped",
    }


@requires_git
@requires_brd
def test_two_simultaneous_escalations_give_one_primary_and_one_also_escalated(project):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: ("review", "a blocker"), b1: ("verify", "b suite red")},
        gates={a1: _meet(pair), b1: _meet(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    entries = {
        story_a: {
            "level": 0,
            "story": story_a,
            "subtask": a1,
            "failed_phase": "review",
            "detail": "a blocker",
        },
        story_b: {
            "level": 0,
            "story": story_b,
            "subtask": b1,
            "failed_phase": "verify",
            "detail": "b suite red",
        },
    }
    primary = result["story"]
    assert primary in entries
    (other,) = set(entries) - {primary}
    assert result == {
        "escalated": True,
        "run_id": run_id,
        **entries[primary],
        "warnings": [],
        "also_escalated": [entries[other]],
    }
    assert c1 not in [call["card"] for call in driver.calls]
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "escalated",
        b1: "escalated",
        story_c: "pending",
        c1: "pending",
    }


@requires_git
@requires_brd
def test_a_lane_that_raises_escalates_and_parks_its_sibling(project):
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: RuntimeError("harness vanished")},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert result == {
        "escalated": True,
        "run_id": run_id,
        "level": 0,
        "story": story_a,
        "subtask": a1,
        "failed_phase": None,
        "detail": "RuntimeError: harness vanished",
        "warnings": [],
        "stopped": [{"story": story_b, "subtask": b1, "before_phase": "implement"}],
    }
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
    }


@requires_git
@requires_brd
def test_a_story_queued_behind_the_bound_stays_pending_after_a_stop(project):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1})
    (first, second, queued) = _census_levels(project, shape["milestone"])[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,), (q1,) = subtasks[first], subtasks[second], subtasks[queued]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={f1: ("review", "reviewer found a blocker")},
        gates={f1: _meet(pair), s1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert q1 not in [call["card"] for call in driver.calls]
    assert (result["story"], result["subtask"]) == (first, f1)
    assert result["stopped"] == [{"story": second, "subtask": s1, "before_phase": "implement"}]
    assert "also_escalated" not in result
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        first: "escalated",
        f1: "escalated",
        second: "stopped",
        s1: "stopped",
        queued: "pending",
        q1: "pending",
    }


@requires_git
@requires_brd
def test_a_keyboard_interrupt_in_one_lane_parks_the_other_and_propagates(project):
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    pair = threading.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: KeyboardInterrupt()},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    with pytest.raises(KeyboardInterrupt):
        _run(project, shape["milestone"], driver, max_concurrent=2)

    run = _load(project, cli.mint_run_id(shape["milestone"], STARTED_AT))
    assert _statuses(run) == {
        "run": "started",
        story_a: "started",
        a1: "started",
        story_b: "stopped",
        b1: "stopped",
    }


@requires_git
@requires_brd
def test_warnings_and_completed_follow_census_order_not_finish_order(project):
    shape = _milestone(project, {"A": 1, "B": 1})
    (first, second) = _census_levels(project, shape["milestone"])[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,) = subtasks[first], subtasks[second]
    second_returned = threading.Event()
    driver = GatedDriver(
        warnings={f1: ["first warned"], s1: ["second warned"]},
        gates={f1: lambda should_stop: _await(second_returned)},
        returned={s1: second_returned},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert sorted(call["card"] for call in driver.calls) == sorted([f1, s1])
    assert result["done"] is True
    assert result["completed"] == [f1, s1]
    assert result["warnings"] == ["first warned", "second warned"]
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "at_once or never_exceed or parks or simultaneous or raises_escalates or queued_behind or keyboard_interrupt_in_one_lane or census_order_not_finish" -v`
Expected: FAIL. Under the sequential loop the first gated lane waits on a barrier or event no other lane can reach, the wait times out after `WAIT` seconds (`threading.BrokenBarrierError` or the `_await` assertion), which becomes an escalation, so the payload and status assertions fail (for example `assert result["done"] is True` gets `KeyError: 'done'`). Expect roughly 10 seconds per test.

- [ ] **Step 5: Run the level on a thread pool**

In `src/agent_manager/orchestrate.py`, add to the imports (after `import threading`):

```python
from concurrent.futures import ThreadPoolExecutor
```

so the block reads:

```python
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
```

In `run_milestone`, replace:

```python
        for level in levels:
            outcomes = [
                run_story_lane(
                    planned,
                    store=store,
                    run_id=run_id,
                    rows=rows,
                    root=root,
                    drive=drive,
                    commands=list(commands),
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    stop=stop,
                )
                for planned in level
            ]
```

with:

```python
        for level in levels:
            with ThreadPoolExecutor(
                max_workers=max_concurrent, thread_name_prefix="am-lane"
            ) as pool:
                futures = [
                    pool.submit(
                        run_story_lane,
                        planned,
                        store=store,
                        run_id=run_id,
                        rows=rows,
                        root=root,
                        drive=drive,
                        commands=list(commands),
                        allow_no_verification=allow_no_verification,
                        runner_factory=runner_factory,
                        stop=stop,
                    )
                    for planned in level
                ]
            # Leaving the `with` block is the level barrier: every lane has
            # returned. `result()` re-raises a lane's BaseException here, after
            # the pool has shut down, in census order.
            outcomes = [future.result() for future in futures]
```

- [ ] **Step 6: Update the docstrings**

Replace the module docstring's first paragraph:

```python
"""The sequential milestone runner (orchestration addendum O6).

`run_milestone` drives every remaining subtask of one milestone, one at a time,
through the shared per-subtask driver (O4). Every derivation belongs to a
```

with:

```python
"""The milestone runner (orchestration addendum O6, parallel-stories P1/P4/P6).

`run_milestone` drives every remaining subtask of one milestone through the
shared per-subtask driver (O4). Each dependency level's stories run as lanes on
a pool bounded by `max_concurrent`; a story's subtasks stay strictly sequential
and level N+1 starts only after every lane of level N returns. Every derivation belongs to a
```

Replace the last module-docstring line:

```python
The module holds no mutable state of its own (O4).
"""
```

with:

```python
The module holds no mutable state of its own (O4). A run's lock and stop event
live in a `RunStop` that `run_milestone` creates for that run.
"""
```

Replace `run_milestone`'s docstring:

```python
    """Drive every remaining subtask of `milestone`, one at a time, and report (O6).

    `milestone` is a card id or a title needle (O1). Everything that can refuse
    runs before the store is opened. Then one `milestone` run is recorded with
    its whole plan `pending`, and levels, stories and subtasks are walked in
    order. A subtask already `done` on the board is never driven, but its
    branch still anchors the next subtask's base. The card and its story are
    read fresh from the board before each subtask.
    """
```

with:

```python
    """Drive every remaining subtask of `milestone`, level by level, and report (O6).

    `milestone` is a card id or a title needle (O1). Everything that can refuse,
    `max_concurrent < 1` included, runs before the store is opened. Then one
    `milestone` run is recorded with its whole plan `pending`, and each level's
    stories run as lanes on a pool of `max_concurrent` threads, with a barrier
    between levels. A subtask already `done` on the board is never driven, but
    its branch still anchors the next subtask's base. The card and its story
    are read fresh from the board before each subtask.

    The first escalation sets the run's stop: lanes already running park at
    their next phase boundary and are recorded `stopped`, lanes not yet started
    leave their story `pending`, and no later level is scheduled. At
    `max_concurrent=1` the result is exactly the sequential runner's.
    """
```

- [ ] **Step 7: Run the orchestrate tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS, the new lane-pool tests quickly (no `WAIT` timeouts), and every pre-existing test unmodified.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "Run a level's stories on a bounded lane pool"
```

---

### Task 6: Full verification

**Files:** none changed.

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest`
Expected: all PASS, including `tests/test_cli.py` (its `_patch_run_milestone` fake accepts `**kwargs`) and `tests/e2e/test_milestone_run.py`. The slow real-harness e2e test stays excluded by default.

- [ ] **Step 2: Confirm no file outside scope changed**

Run: `git diff --stat m4/task-let-the-engine-stop-0d8b7c9a...HEAD`
Expected: only `src/agent_manager/orchestrate.py`, `tests/test_orchestrate.py`, and the spec and plan under `docs/superpowers/`.
