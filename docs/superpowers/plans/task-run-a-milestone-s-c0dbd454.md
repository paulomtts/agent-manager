<!-- task-pipeline: validated -->
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

---

# Run a milestone's stories as a grafo tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `orchestrate.py`'s thread-pool level loop with one event loop where every census story is a `grafo.Node` that starts the moment its blockers succeed, lanes take an `asyncio.Semaphore` slot, and outcomes are read from node outputs and `executor.errors` (T6).

**Architecture:** `run_milestone` (sync, unchanged signature) builds a `SupervisorPlan` (every census story with its `RootPlan` and tip, the pending stories' `PlannedStory` waves and store rows) and calls `asyncio.run(supervise(...))`. `supervise` builds one `grafo.Node(timeout=None)` per story and one edge per `RootPlan.blockers` entry, runs `grafo.TreeExecutor`, and hands node outputs, `executor.errors` and the lanes' finished outcomes to `collect_outcomes`. Each node coroutine is `lane(...)`: no remaining subtasks returns the tip; otherwise it takes a slot, checks `StopSignal` before each subtask, awaits the async driver, and raises `LaneEscalated`/`LaneStopped` to fail.

**Tech Stack:** Python 3.12, `grafo>=0.3.5` (already in `pyproject.toml`), `pygents>=0.6.7` (M6 engine, only via the driver), pytest + pytest-asyncio (`asyncio_mode = "auto"`), real temporary git repos and brd boards (the existing `project` fixture).

**Spec:** the spec above (`docs/superpowers/specs/task-run-a-milestone-s-c0dbd454-design.md`), plus `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` T1-T6, T9 and section 7 (lane states).

**Upstream notes (read before starting):** both summaries handed to this planning stage were truncated by the harness (spec summary at 2000 characters, exploration findings at 8000 characters), meaning the upstream stages over-ran their briefs. This plan was written from the spec file on disk and the code itself, not from the truncated text. Also: on this branch (cut from `m7/task-resolve-base-conflicts-8fe30578`) `orchestrate.plan_levels` ALREADY refuses `merged` roots explicitly through `_merged_root_behind` / `_merged_root_error` (`src/agent_manager/orchestrate.py:248-321`), `dag.story_root` already returns `RootPlan`, `cli.drive_subtask_async` and `runtime/stop.py` `StopSignal` already exist. This plan keeps that refusal untouched and does not assume anything beyond those files.

## Global Constraints

- Only `src/agent_manager/orchestrate.py` imports `grafo`, as `import grafo`, and references `grafo.Node` / `grafo.TreeExecutor` by attribute at call time (tests replace `grafo.Node`).
- Every `grafo.Node` is built with `timeout=None` (grafo's default is `60.0` and cancels the lane through `asyncio.wait_for`).
- Only the subtask is a pygents Agent (T2). `supervise` and `lane` are plain async functions. `orchestrate.py` never imports `pygents` (guarded by `test_the_engine_selecting_modules_never_import_pygents`).
- One `asyncio.run` per `run_milestone`; no `ThreadPoolExecutor`, no `threading` in `orchestrate.py` after Task 2.
- `run_milestone`'s signature is unchanged: `(milestone, *, repo_dir, base_branch, branch_prefix, commands=(), allow_no_verification=False, runner_factory=None, driver=None, clock=_utcnow, max_concurrent=1) -> dict[str, Any]`.
- `should_stop` stays on the `Driver` protocol and on `cli.drive_subtask_async` (Task 3.3 deletes it). The lane passes `stop=stop` and never passes `should_stop`.
- `merged` roots stay refused before any write (Task 3.2 lifts it). No `bases.build` wiring, no `bases` report key.
- CLI envelope unchanged (`{"ok": true, "data": ...}`); the report keeps `levels` as `dag.compute_levels` waves.
- No test sleeps to prove ordering: fakes block on `asyncio.Event` / `asyncio.Barrier`, bounded by `WAIT` so a broken run fails instead of hanging.
- The milestone's base branch never moves and nothing is pushed.
- Verification: `uv run pytest` — the whole default suite green, including `tests/e2e`.

## Review Focus

1. **A milestone with no stories at all.** `grafo.TreeExecutor.run()` computes `min(levels)` over its roots and raises `ValueError` on an empty list; a reasonable person expects a clean `done` run with `levels: []`. Test in Task 2 (`test_a_milestone_with_no_stories_finishes_without_a_tree`).
2. **A story behind a subtask-less story.** C blocked by J (no subtasks) blocked by A: C's stack roots on A's tip, so C must not start before A finishes, even though `dag.compute_levels` puts C in level 0. Test in Task 2 (`test_a_story_behind_a_subtask_less_story_waits_for_the_blocker_beneath`).
3. **The stop seen between two subtasks.** The lane never drives the next subtask; the report names it with `before_phase: null`, its row stays `pending`, and the e2e oracle must accept that instead of demanding a phase name. Test in Task 2 (`test_a_lane_between_subtasks_sees_the_stop_and_never_drives_the_next`, plus the e2e adjustment).
4. **Ctrl-C while two lanes run.** A `KeyboardInterrupt` must leave the loop, the sibling lane must be cancelled where it stands (not hang, not be recorded `escalated`), and the rows stay as they were for `am resume`. Test in Task 2 (`test_a_keyboard_interrupt_in_one_lane_cancels_the_other_and_propagates`).
5. **The grafo logger level after a run.** Silencing grafo must not leak past `supervise`: a later in-process run or library user sees grafo's own level again. Test in Task 3 (asserted inside `test_a_lane_bug_keeps_stdout_one_json_line`).

---

## File map

| File | Change | Task |
|---|---|---|
| `src/agent_manager/orchestrate.py` | `LaneKind` gains `pending`; `LaneOutcome.primary`; `LaneEscalated`, `LaneStopped`, `SupervisorPlan`, `supervisor_plan`, `collect_outcomes` | 1 |
| `src/agent_manager/orchestrate.py` | async `Driver`; `lane`, `supervise`; `run_milestone` over `asyncio.run`; `RunStop`, `run_story_lane`, thread pool deleted | 2 |
| `src/agent_manager/orchestrate.py` | grafo logger CRITICAL for the duration of `supervise` | 3 |
| `tests/test_orchestrate.py` | pure tests (Task 1); async fakes, rewritten barrier tests, new engine tests (Task 2); CLI capsys test (Task 3) | 1, 2, 3 |
| `tests/e2e/test_milestone_run.py` | capture the run's `StopSignal` instead of `RunStop` | 2 |
| `tests/e2e/test_parallel_milestone.py` | `before_phase` may be `None` (Task 2); dataflow naming and the async wiring guard (Task 4) | 2, 4 |

---

### Task 1: Lane outcomes, lane errors and T6 outcome collection

Pure pieces the supervisor needs, with no scheduling change yet. The existing thread runner keeps working.

**Files:**
- Modify: `src/agent_manager/orchestrate.py:30-32` (imports), `:61-84` (`LaneKind`, `LaneOutcome`), insert after `:84`, insert after `story_tips` (`:324-342`), `:471-472` (`run_story_lane`'s early return)
- Test: `tests/test_orchestrate.py` (pure section, before `# ── the runner, on a real repo and a real board`)

**Interfaces:**
- Consumes: `dag.story_root(story, stories_by_id, prefix, base_branch) -> dag.RootPlan`, `dag.story_tip(...) -> str`, `plan_levels(...) -> list[list[PlannedStory]]`.
- Produces:
  - `LaneKind = Literal["done", "escalated", "stopped", "pending"]` (`"not_started"` is gone).
  - `LaneOutcome(kind, story: str | None, level: int | None, subtask=None, failed_phase=None, detail=None, before_phase=None, completed=(), warnings=(), primary: bool = False)`.
  - `class LaneEscalated(Exception)` and `class LaneStopped(Exception)`, both `__init__(self, outcome: LaneOutcome)`, attribute `.outcome`.
  - `@dataclass(frozen=True) class SupervisorPlan: stories: tuple[census.StoryPlan, ...]; levels: tuple[tuple[PlannedStory, ...], ...]; roots: dict[str, dag.RootPlan]; tips: dict[str, str]; rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]`, property `planned -> dict[str, PlannedStory]` (wave order).
  - `supervisor_plan(stories, levels, rows, *, branch_prefix: str, base_branch: str) -> SupervisorPlan`.
  - `collect_outcomes(plan: SupervisorPlan, nodes: Mapping[str, Any], errors: Sequence[BaseException], finished: Mapping[str, LaneOutcome]) -> list[LaneOutcome]` — one outcome per planned story in wave order, then one per foreign error.

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrate.py`, change the imports at the top of the file:

```python
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
```

(the existing `from dataclasses import dataclass, field` line becomes the first line above; add the second line after `from pathlib import Path`).

In `test_the_escalated_payload_names_the_primary_and_lists_the_rest_in_census_order`, replace

```python
    queued = orchestrate.LaneOutcome(kind="not_started", story="E", level=2)
```

with

```python
    queued = orchestrate.LaneOutcome(kind="pending", story="E", level=2)
```

In `test_a_lone_escalation_payload_is_exactly_the_sequential_one`, replace

```python
    queued = orchestrate.LaneOutcome(kind="not_started", story="B", level=0)
```

with

```python
    queued = orchestrate.LaneOutcome(kind="pending", story="B", level=0)
```

Then add these tests right after `test_a_final_verification_escalation_payload_has_no_story`:

```python
def _supervisor_plan(stories: list[census.StoryPlan]) -> orchestrate.SupervisorPlan:
    levels = orchestrate.plan_levels(stories, branch_prefix="m3", base_branch="main")
    return orchestrate.supervisor_plan(
        stories, levels, {}, branch_prefix="m3", base_branch="main"
    )


def test_lane_errors_carry_their_outcome():
    escalated = orchestrate.LaneOutcome(
        kind="escalated", story="A", level=0, subtask="a1", detail="red"
    )
    stopped = orchestrate.LaneOutcome(kind="stopped", story="B", level=0, subtask="b1")

    raised = orchestrate.LaneEscalated(escalated)
    parked = orchestrate.LaneStopped(stopped)

    assert raised.outcome is escalated
    assert parked.outcome is stopped
    assert isinstance(raised, Exception) and isinstance(parked, Exception)
    assert not isinstance(raised, orchestrate.LaneStopped)


def test_the_supervisor_plan_roots_and_tips_every_census_story_done_ones_included():
    """T1: every census story becomes a node, so every one needs its root and
    tip; only the pending ones are planned for a lane."""
    a = _plan_story(1, [_plan_subtask(11, "done")], status="done")
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[a.id])

    plan = _supervisor_plan([a, b])

    assert [story.id for story in plan.stories] == [a.id, b.id]
    assert plan.roots[a.id] == dag.RootPlan("base", "main", ())
    assert plan.roots[b.id] == dag.RootPlan("tip", _branch_of(a.subtasks[-1]), (a.id,))
    assert plan.tips == {
        a.id: _branch_of(a.subtasks[-1]),
        b.id: _branch_of(b.subtasks[-1]),
    }
    assert list(plan.planned) == [b.id]
    assert plan.planned[b.id].level == 0


def test_outcomes_follow_t6_in_wave_order():
    """Node output with a finished outcome is `done`; the first LaneEscalated in
    `errors` is primary and the next is not; LaneStopped gives its outcome; a
    story with no output and no error is `pending`."""
    a = _plan_story(1, [_plan_subtask(11)])
    b = _plan_story(2, [_plan_subtask(21)])
    c = _plan_story(3, [_plan_subtask(31)])
    e = _plan_story(5, [_plan_subtask(51)])
    d = _plan_story(4, [_plan_subtask(41)], blocked_by=[a.id])
    plan = _supervisor_plan([a, b, c, d, e])
    done_a = orchestrate.LaneOutcome(kind="done", story=a.id, level=0, completed=(_plan_id(11),))
    b_escalated = orchestrate.LaneOutcome(
        kind="escalated", story=b.id, level=0, subtask=_plan_id(21), failed_phase="review", detail="b"
    )
    c_escalated = orchestrate.LaneOutcome(
        kind="escalated", story=c.id, level=0, subtask=_plan_id(31), failed_phase="verify", detail="c"
    )
    e_stopped = orchestrate.LaneOutcome(
        kind="stopped", story=e.id, level=0, subtask=_plan_id(51), before_phase="implement"
    )
    nodes = {
        a.id: SimpleNamespace(output="tip of a"),
        b.id: SimpleNamespace(output=None),
        c.id: SimpleNamespace(output=None),
        d.id: SimpleNamespace(output=None),
        e.id: SimpleNamespace(output=None),
    }
    errors = [
        orchestrate.LaneEscalated(c_escalated),
        orchestrate.LaneStopped(e_stopped),
        orchestrate.LaneEscalated(b_escalated),
    ]

    outcomes = orchestrate.collect_outcomes(plan, nodes, errors, {a.id: done_a})

    assert outcomes == [
        done_a,
        b_escalated,
        replace(c_escalated, primary=True),
        e_stopped,
        orchestrate.LaneOutcome(kind="pending", story=d.id, level=1),
    ]


def test_an_error_that_is_no_lane_error_is_escalated_with_its_type_and_message():
    """T6: an exception that escaped even the lane's own catch-all is tied to no
    story, so it carries no subtask, failed phase or level."""
    a = _plan_story(1, [_plan_subtask(11)])
    plan = _supervisor_plan([a])

    outcomes = orchestrate.collect_outcomes(
        plan, {a.id: SimpleNamespace(output=None)}, [RuntimeError("grafo broke")], {}
    )

    foreign = orchestrate.LaneOutcome(
        kind="escalated", story=None, level=None, detail="RuntimeError: grafo broke"
    )
    assert outcomes == [orchestrate.LaneOutcome(kind="pending", story=a.id, level=0), foreign]
    payload = orchestrate.escalated_payload("run-1", None, outcomes, [])
    assert (payload["story"], payload["subtask"], payload["failed_phase"], payload["level"]) == (
        None,
        None,
        None,
        None,
    )
    assert payload["detail"] == "RuntimeError: grafo broke"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "lane_errors_carry or supervisor_plan_roots or outcomes_follow_t6 or no_lane_error" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'LaneEscalated'` (and `supervisor_plan`, `collect_outcomes`).

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/orchestrate.py`, change the imports (lines 30-32):

```python
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
```

Replace `LaneKind` and `LaneOutcome` (lines 61-84) with:

```python
LaneKind = Literal["done", "escalated", "stopped", "pending"]
"""How one story's lane ended (supervisor-tree T6): finished, escalated, stopped
(parked by the stop, or saw it before a subtask), or never started by the tree."""


@dataclass(frozen=True)
class LaneOutcome:
    """What one story's lane did. Internal state, so a dataclass (CLAUDE.md).

    `subtask` is the subtask that escalated or was stopped before. `failed_phase`
    and `detail` describe an escalation, `before_phase` a stop. `completed` and
    `warnings` are this lane's own, in the order they arrived; `run_milestone`
    merges them across lanes in wave order. `story` and `level` are `None` only
    for an error no lane raised, which nothing ties to one story (T6).
    `primary` marks the first escalation in `executor.errors`.
    """

    kind: LaneKind
    story: str | None
    level: int | None
    subtask: str | None = None
    failed_phase: str | None = None
    detail: str | None = None
    before_phase: str | None = None
    completed: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    primary: bool = False


class LaneEscalated(Exception):
    """A lane's escalation, raised so grafo never releases its dependents (T6)."""

    def __init__(self, outcome: LaneOutcome) -> None:
        super().__init__(
            f"story {outcome.story} escalated at {outcome.subtask}: {outcome.detail}"
        )
        self.outcome = outcome


class LaneStopped(Exception):
    """A lane that parked, or saw the stop before a subtask (T6)."""

    def __init__(self, outcome: LaneOutcome) -> None:
        super().__init__(f"story {outcome.story} stopped before {outcome.subtask}")
        self.outcome = outcome
```

Insert after `story_tips` (after line 342):

```python
@dataclass(frozen=True)
class SupervisorPlan:
    """What `supervise` schedules (T1). Internal state, so a dataclass.

    `stories` is every census story, done ones included, in census order: each
    becomes a node. `roots` and `tips` cover all of them. `levels` are the
    pending stories' waves from `plan_levels`, and `rows` their store rows from
    `record_plan`.
    """

    stories: tuple[census.StoryPlan, ...]
    levels: tuple[tuple[PlannedStory, ...], ...]
    roots: dict[str, dag.RootPlan]
    tips: dict[str, str]
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]

    @property
    def planned(self) -> dict[str, PlannedStory]:
        """The pending stories by id, in wave order."""
        return {planned.story.id: planned for level in self.levels for planned in level}


def supervisor_plan(
    stories: Sequence[census.StoryPlan],
    levels: Sequence[Sequence[PlannedStory]],
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]],
    *,
    branch_prefix: str,
    base_branch: str,
) -> SupervisorPlan:
    """Every census story's root and tip beside the pending waves and their rows.

    Pure. `plan_levels` has already run the cycle check and refused `merged`
    roots for every pending story, so this derives geometry and refuses nothing.
    """
    stories = tuple(stories)
    by_id = {story.id: story for story in stories}
    return SupervisorPlan(
        stories=stories,
        levels=tuple(tuple(level) for level in levels),
        roots={
            story.id: dag.story_root(story, by_id, branch_prefix, base_branch)
            for story in stories
        },
        tips={
            story.id: dag.story_tip(story, by_id, branch_prefix, base_branch)
            for story in stories
        },
        rows=rows,
    )


def collect_outcomes(
    plan: SupervisorPlan,
    nodes: Mapping[str, Any],
    errors: Sequence[BaseException],
    finished: Mapping[str, LaneOutcome],
) -> list[LaneOutcome]:
    """One outcome per pending story in wave order, then one per foreign error (T6).

    A node with an output is `done` (its lane's finished outcome). A
    `LaneEscalated` gives its outcome, the first in `errors` marked `primary`.
    A `LaneStopped` gives its outcome. Anything else in `errors` escaped every
    lane's catch-all, so it is `escalated` with `"<Type>: <msg>"` and tied to no
    story. No output and no error is `pending`.
    """
    escalated: dict[str, LaneOutcome] = {}
    stopped: dict[str, LaneOutcome] = {}
    foreign: list[LaneOutcome] = []
    for error in errors:
        if isinstance(error, LaneEscalated):
            outcome = error.outcome if escalated else replace(error.outcome, primary=True)
            escalated.setdefault(outcome.story, outcome)
        elif isinstance(error, LaneStopped):
            stopped.setdefault(error.outcome.story, error.outcome)
        else:
            foreign.append(
                LaneOutcome(
                    kind="escalated",
                    story=None,
                    level=None,
                    detail=f"{type(error).__name__}: {error}",
                )
            )
    outcomes: list[LaneOutcome] = []
    for level in plan.levels:
        for planned in level:
            story_id = planned.story.id
            node = nodes.get(story_id)
            if node is not None and node.output is not None and story_id in finished:
                outcomes.append(finished[story_id])
            elif story_id in escalated:
                outcomes.append(escalated[story_id])
            elif story_id in stopped:
                outcomes.append(stopped[story_id])
            else:
                outcomes.append(LaneOutcome(kind="pending", story=story_id, level=planned.level))
    return outcomes + foreign
```

In `run_story_lane` (lines 471-472), replace

```python
    if stop.event.is_set():
        return LaneOutcome(kind="not_started", story=story_id, level=level)
```

with

```python
    if stop.event.is_set():
        return LaneOutcome(kind="pending", story=story_id, level=level)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: PASS (the new pure tests and every existing test in the file).

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): lane errors and T6 outcome collection"
```

---

### Task 2: Supervise the milestone as a grafo tree on one event loop

The scheduling change. Converting the `Driver` to async breaks every run test at once, so the fakes, the rewritten barrier tests, the new engine tests, the implementation and the two e2e adaptations land together.

**Files:**
- Modify: `src/agent_manager/orchestrate.py` — module docstring (`:1-25`), imports, `RunStop` (`:87-106` before Task 1, deleted), `Driver` (`:201-225`), `run_story_lane` (`:441-560`, replaced by `lane`), `run_milestone` (`:563-700`), new `supervise`.
- Modify: `tests/test_orchestrate.py` — imports, fakes (`FakeDriver`, `BranchingDriver`, `GatedDriver`, `CheckpointDriver`, the gate helpers), the tests named below.
- Modify: `tests/e2e/test_milestone_run.py:374-414` (`_hold_b1_in_plan_until_a1_escalates`).
- Modify: `tests/e2e/test_parallel_milestone.py:269-288` (the parked-lane assertions).

**Interfaces:**
- Consumes (Task 1): `LaneOutcome`, `LaneEscalated`, `LaneStopped`, `SupervisorPlan`, `supervisor_plan`, `collect_outcomes`. Existing: `StopSignal` (`.triggered`, `.primary`, `.trigger(story_id) -> bool`, `.register(agent)`, `.unregister(agent)`), `cli.drive_subtask_async(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, should_stop=None, stop=None, resume_from=None) -> cli.SubtaskDrive`, `cli.continuable_checkpoint(store, card_id)`, `grafo.Node(coroutine, kwargs=None, uuid=None, timeout=60.0, ...)`, `Node.connect(child, *, forward=...)` (async), `Node.output`, `grafo.TreeExecutor(uuid=..., roots=[...])`, `await executor.run()`, `executor.errors`.
- Produces:
  - `class Driver(Protocol)`: `async def __call__(self, *, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, should_stop=None, stop: StopSignal | None = None, resume_from=None) -> cli.SubtaskDrive`.
  - `async def lane(story, *, plan, store, run_id, root, drive, commands, allow_no_verification, runner_factory, slots, stop, finished) -> str` (returns the story's tip).
  - `async def supervise(plan: SupervisorPlan, *, store, run_id, root, drive, commands, allow_no_verification, runner_factory, max_concurrent, stop: StopSignal) -> list[LaneOutcome]`.
  - Module-level name `orchestrate.StopSignal`, read by `run_milestone` at call time (e2e tests replace it).

- [ ] **Step 1: Replace the test fakes with awaitable ones**

In `tests/test_orchestrate.py`, replace the import block (lines 19-38, as changed by Task 1) with:

```python
import ast
import asyncio
import inspect
import json
import logging
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import grafo
import pytest

from agent_manager import board, census, cli, dag, integration, models, orchestrate, paths
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.runtime.walk import SubtaskSummary
from agent_manager import store as store_module
from agent_manager.steps import rollup, worktree
from agent_manager.workflow import task as task_workflow
from agent_manager.workflow.phases import Step, Workflow
```

Update the module docstring's second bullet to say the runner is driven through an awaitable fake at the injected `driver` seam, on the run's one event loop:

```python
- `run_milestone` runs on Steps-tier fixtures -- a real temporary git repo and a
  real temporary brd board, with `XDG_DATA_HOME` under `tmp_path` so
  `paths.data_dir()` never touches the developer's own -- with the harness
  replaced at the injected `driver` seam by an awaitable fake that runs on the
  run's one event loop (supervisor-tree T3). No runner, adapter or `claude` is
  involved; production wiring under a fake `claude` belongs to tests/e2e.
```

Replace `FakeDriver` (lines 463-518) with:

```python
@dataclass
class FakeDriver:
    """Stands in for `cli.drive_subtask_async`. Never touches git or the board.

    `outcomes` scripts a card: missing means `done`, a `(phase, detail)` tuple
    means escalated at that phase, and an exception instance is raised.
    `warnings` gives a card's canned warnings. Every call is recorded, with a
    snapshot of the store's view of the run at that moment.
    """

    outcomes: dict[str, Any] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[models.Run | None] = field(default_factory=list)

    async def __call__(
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
        stop=None,
    ) -> cli.SubtaskDrive:
        self.calls.append(
            {
                "card": card.id,
                "parent": parent.id,
                "branch": subtask.branch,
                "base": subtask.base_branch,
                "worktree": subtask.worktree_path,
                "status": subtask.status,
                "run_id": run_id,
                "repo_dir": repo_dir,
                "commands": list(commands),
                "allow_no_verification": allow_no_verification,
                "runner_factory": runner_factory,
                "should_stop": should_stop,
                "stop": stop,
            }
        )
        self.snapshots.append(store.load_run(run_id))
        outcome = self.outcomes.get(card.id)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is None:
            summary = SubtaskSummary(status="done")
        else:
            phase, detail = outcome
            summary = SubtaskSummary(
                status="escalated", failed_phase=phase, detail=detail
            )
        return cli.SubtaskDrive(summary=summary, warnings=list(self.warnings.get(card.id, [])))
```

Replace `BranchingDriver.__call__` (lines 640-653) with:

```python
    async def __call__(self, *, store, run_id, card, parent, subtask, repo_dir, **kwargs):
        drive = await super().__call__(
            store=store,
            run_id=run_id,
            card=card,
            parent=parent,
            subtask=subtask,
            repo_dir=repo_dir,
            **kwargs,
        )
        if drive.summary.status == "done":
            _commit_branch(repo_dir, subtask.branch, subtask.base_branch, card.id)
            rollup.set_status(card.id, "done", repo_dir=repo_dir)
        return drive
```

Replace the block from `WAIT = 10.0` through the end of `GatedDriver` (lines 697-825) with:

```python
WAIT = 10.0
"""Seconds a supervisor test waits on a barrier or event before failing instead of hanging."""

OVERSHOOT_WINDOW = 1.0
"""Seconds the bound test holds each lane in flight, so that queued lanes would
enter the driver in that window if the lanes ignored `max_concurrent`."""

PATCHED_NODE_TIMEOUT = 0.05
"""grafo's default node timeout, patched down by the timeout test."""

LONGER_THAN_PATCHED_TIMEOUT = 0.3
"""How long that test's lane stays in flight: well past `PATCHED_NODE_TIMEOUT`."""

Gate = Callable[[StopSignal | None], Awaitable[None]]


async def _within(awaitable: Awaitable[Any], what: str) -> Any:
    """Await `awaitable`, failing after WAIT seconds instead of hanging the run.

    The failure is an `AssertionError` inside the driver, so the lane turns it
    into an escalation whose detail names what never happened.
    """
    try:
        return await asyncio.wait_for(awaitable, WAIT)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {what}") from None


class _StopWatch:
    """A `pause()`-only stand-in registered on the run's `StopSignal`, as a
    pygents subtask agent is: the signal pauses it when it fires."""

    def __init__(self) -> None:
        self.paused = asyncio.Event()

    def pause(self) -> None:
        self.paused.set()


async def _await_stop(stop: StopSignal | None) -> None:
    """Block until the run's stop fires, without sleeping. Pins that the lane
    handed the driver the run's `StopSignal` (T5)."""
    assert isinstance(stop, StopSignal), "the lane passed no StopSignal"
    watch = _StopWatch()
    stop.register(watch)
    try:
        await _within(watch.paused.wait(), "the run's stop")
    finally:
        stop.unregister(watch)


def _meet(barrier: asyncio.Barrier) -> Gate:
    """A gate that holds a call until every party of `barrier` is in flight."""

    async def gate(stop: StopSignal | None) -> None:
        await _within(barrier.wait(), "every party of the barrier")

    return gate


def _meet_then_await_stop(barrier: asyncio.Barrier) -> Gate:
    """A gate that meets `barrier`, then holds the call until the run's stop fires."""

    async def gate(stop: StopSignal | None) -> None:
        await _within(barrier.wait(), "every party of the barrier")
        await _await_stop(stop)

    return gate


def _census_levels(project: Path, milestone: str) -> list[list[str]]:
    """Each wave's story ids in census order, the order the tree starts them in.

    Sibling stories created in the same second are ordered by id, so a test that
    gives a lane a role by its position must read the order, not assume it.
    """
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=project))
    levels = orchestrate.plan_levels(plan.stories, branch_prefix=PREFIX, base_branch="main")
    return [[planned.story.id for planned in level] for level in levels]


def _subtasks_by_story(shape: dict[str, Any]) -> dict[str, list[str]]:
    return {shape["stories"][key]: shape["subtasks"][key] for key in shape["stories"]}


@dataclass
class GatedDriver:
    """An awaitable stand-in for `cli.drive_subtask_async`, for the supervisor tests.

    `gates[card]` is awaited first with the lane's `stop`; tests put
    `asyncio.Barrier`s and `asyncio.Event`s there, never sleeps. Then
    `outcomes[card]` decides: an exception instance is raised, a `(phase,
    detail)` tuple escalates, and `"done"` finishes as a phase already running
    would. With no entry the fake reaches its simulated phase boundary: if the
    stop has fired it parks as the engine does, with `"stopped before
    implement"`; otherwise it is done. Everything runs on the run's one loop,
    so no lock: `high_water` is the most calls ever in flight at once, and
    `returned[card]` is set when that card's call ends.
    """

    outcomes: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Gate] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    returned: dict[str, asyncio.Event] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    high_water: int = 0
    in_flight: int = 0

    async def __call__(
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
        stop=None,
    ) -> cli.SubtaskDrive:
        self.calls.append({"card": card.id, "parent": parent.id, "base": subtask.base_branch})
        self.in_flight += 1
        self.high_water = max(self.high_water, self.in_flight)
        try:
            gate = self.gates.get(card.id)
            if gate is not None:
                await gate(stop)
            outcome = self.outcomes.get(card.id)
            if isinstance(outcome, BaseException):
                raise outcome
            warnings = list(self.warnings.get(card.id, []))
            if isinstance(outcome, tuple):
                phase, detail = outcome
                summary = SubtaskSummary(
                    status="escalated", failed_phase=phase, detail=detail
                )
            elif outcome != "done" and stop is not None and stop.triggered:
                summary = SubtaskSummary(
                    status="stopped", detail="stopped before implement"
                )
            else:
                summary = SubtaskSummary(status="done")
            return cli.SubtaskDrive(summary=summary, warnings=warnings)
        finally:
            self.in_flight -= 1
            if card.id in self.returned:
                self.returned[card.id].set()
```

Replace `CheckpointDriver.__call__` (lines 2014-2016) with:

```python
    async def __call__(self, *, resume_from: Any = _ABSENT, **kwargs: Any) -> cli.SubtaskDrive:
        self.resumed[kwargs["card"].id] = resume_from
        return await super().__call__(**kwargs)
```

- [ ] **Step 2: Rewrite the tests that named the thread runner or a level barrier**

Delete `test_the_first_escalation_is_the_primary_and_every_escalation_sets_the_stop` (lines 185-194): `RunStop` is gone and `StopSignal` has its own tests in `tests/runtime/test_stop.py`.

Replace `test_no_driver_resolves_to_cli_drive_subtask_at_call_time` (lines 990-1003) with:

```python
@requires_git
@requires_brd
def test_no_driver_resolves_to_cli_drive_subtask_async_at_call_time(project, monkeypatch):
    """The default driver is the awaitable one (T3), read off `cli` when the run
    starts, never bound at import. The lane hands it the run's StopSignal and
    no `should_stop` (Task 3.3 deletes that parameter)."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    fake = FakeDriver()
    monkeypatch.setattr(cli, "drive_subtask_async", fake)

    result = _run(project, shape["milestone"], None)

    assert [call["card"] for call in fake.calls] == [a1]
    assert isinstance(fake.calls[0]["stop"], StopSignal)
    assert fake.calls[0]["should_stop"] is None
    assert result["done"] is True
```

Replace `test_a_story_with_two_blockers_is_refused_before_anything_is_written` (lines 1347-1371) with (spec test 11):

```python
@requires_git
@requires_brd
def test_merged_root_is_refused(project, monkeypatch):
    """A story whose `RootPlan` is `merged` is refused before anything is
    written: no git call, no run directory, no worktree (Task 3.2 lifts this)."""
    milestone = _add_card(project, "Milestone 3: orchestration")
    first = _add_card(project, "Story one", milestone)
    second = _add_card(project, "Story two", milestone)
    joined = _add_card(project, "Story three", milestone)
    for story in (first, second, joined):
        _add_card(project, f"only subtask of {story}", story)
    _block(project, joined, first)
    _block(project, joined, second)
    plan = census.flatten_milestone(board.tree(milestone, repo_dir=project))
    by_id = {story.id: story for story in plan.stories}
    root = dag.story_root(by_id[joined], by_id, PREFIX, "main")
    assert root.kind == "merged"
    assert set(root.blockers) == {first, second}
    porcelain_before = _git(project, "status", "--porcelain")
    git_calls = _record_git(monkeypatch)
    driver = FakeDriver()

    with pytest.raises(dag.StackRootError) as caught:
        _run(project, milestone, driver)

    assert f"#{first}" in str(caught.value)
    assert f"#{second}" in str(caught.value)
    assert driver.calls == []
    assert git_calls == []
    assert list(paths.data_dir().iterdir()) == []
    assert not (project / ".claude").exists()
    assert _git(project, "status", "--porcelain") == porcelain_before
```

Replace `test_every_story_of_a_level_runs_at_once_and_each_keeps_its_subtask_order` (lines 1680-1718) with:

```python
@requires_git
@requires_brd
def test_every_story_of_a_level_runs_at_once_and_each_keeps_its_subtask_order(project):
    shape = _milestone(project, {"A": 2, "B": 1, "C": 1})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    a1, a2 = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    all_three = asyncio.Barrier(3)
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
    assert result["done"] is True, result
    assert result["levels"] == [{"level": 0, "stories": order}]
    assert result["completed"] == [card for story in order for card in subtasks[story]]
    assert "also_escalated" not in result
    assert "stopped" not in result
    run = _load(project, result["run_id"])
    assert run.config == models.RunConfig(max_concurrent_stories=3)
    assert set(_statuses(run).values()) == {"done"}
```

Replace `test_in_flight_lanes_never_exceed_the_bound` (lines 1721-1752) with (spec test 2):

```python
@requires_git
@requires_brd
def test_at_most_max_concurrent_lanes_run(project):
    """Five ready stories, two slots. Every lane stays in flight until a third
    lane enters the driver or the window expires: without the bound all five
    arrive inside the window together; under it only two can be in flight."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1, "D": 1, "E": 1})
    subtasks = _subtasks_by_story(shape)
    arrivals = 0
    third_arrived = asyncio.Event()

    async def hold_until_a_third_lane_arrives(stop: StopSignal | None) -> None:
        nonlocal arrivals
        arrivals += 1
        if arrivals >= 3:
            third_arrived.set()
        try:
            await asyncio.wait_for(third_arrived.wait(), OVERSHOOT_WINDOW)
        except TimeoutError:
            pass

    driver = GatedDriver(
        gates={cards[0]: hold_until_a_third_lane_arrives for cards in subtasks.values()}
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True, result
    assert sorted(call["card"] for call in driver.calls) == sorted(
        card for cards in subtasks.values() for card in cards
    )
    assert driver.high_water == 2
```

Replace `test_an_escalation_parks_the_other_lane_and_no_later_level_starts` (lines 1755-1794) with:

```python
@requires_git
@requires_brd
def test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending(
    project, integrate_recorder
):
    """C is blocked by A. A escalates, so grafo never releases C: C stays
    `pending` because its own blocker failed (dataflow), not because of a level."""
    shape = _milestone(project, {"A": 1, "B": 2, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    pair = asyncio.Barrier(2)
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
    assert integrate_recorder.calls == []
```

Replace `test_a_lane_whose_first_subtask_finished_parks_its_next_subtask` (lines 1797-1827) with (Review Focus 3):

```python
@requires_git
@requires_brd
def test_a_lane_between_subtasks_sees_the_stop_and_never_drives_the_next(project):
    """The lane checks the stop before every subtask (spec, Observable
    behavior): b1 finished after A escalated, so b2 is never handed to the
    driver. It is reported stopped with no phase, and its row stays pending."""
    shape = _milestone(project, {"A": 1, "B": 2})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    b1, b2 = shape["subtasks"]["B"]
    pair = asyncio.Barrier(2)
    driver = GatedDriver(
        outcomes={a1: ("verify", "suite red"), b1: "done"},
        gates={a1: _meet(pair), b1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert sorted(call["card"] for call in driver.calls) == sorted([a1, b1])
    assert (result["story"], result["subtask"]) == (story_a, a1)
    assert result["stopped"] == [{"story": story_b, "subtask": b2, "before_phase": None}]
    assert result["completed"] == [b1]
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "done",
        b2: "pending",
    }
```

Replace `test_two_simultaneous_escalations_give_one_primary_and_one_also_escalated` (lines 1830-1882) with (spec test 5):

```python
@requires_git
@requires_brd
def test_two_escalations_in_one_tick_give_one_primary(project):
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    pair = asyncio.Barrier(2)
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
```

Replace `test_a_lane_that_raises_escalates_and_parks_its_sibling` (lines 1885-1918) with:

```python
@requires_git
@requires_brd
def test_a_lane_that_raises_escalates_and_parks_its_sibling(project):
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    pair = asyncio.Barrier(2)
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
```

Replace `test_a_story_queued_behind_the_bound_stays_pending_after_a_stop` (lines 1921-1948) with (spec test 6, Review Focus 3 of the parent plan):

```python
@requires_git
@requires_brd
def test_stop_while_waiting_for_a_slot_ends_stopped(project):
    """Three ready stories, two slots. `queued` has been started by the tree and
    waits for a slot when `first` escalates: it takes the slot, sees the stop,
    and ends `stopped` without its subtask ever reaching the driver."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1})
    (first, second, queued) = _census_levels(project, shape["milestone"])[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,), (q1,) = subtasks[first], subtasks[second], subtasks[queued]
    pair = asyncio.Barrier(2)
    driver = GatedDriver(
        outcomes={f1: ("review", "reviewer found a blocker")},
        gates={f1: _meet(pair), s1: _meet_then_await_stop(pair)},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert q1 not in [call["card"] for call in driver.calls]
    assert (result["story"], result["subtask"]) == (first, f1)
    assert result["stopped"] == [
        {"story": second, "subtask": s1, "before_phase": "implement"},
        {"story": queued, "subtask": q1, "before_phase": None},
    ]
    assert "also_escalated" not in result
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        first: "escalated",
        f1: "escalated",
        second: "stopped",
        s1: "stopped",
        queued: "stopped",
        q1: "pending",
    }
```

Replace `test_a_keyboard_interrupt_in_one_lane_parks_the_other_and_propagates` (lines 1951-1974) with (Review Focus 4):

```python
@requires_git
@requires_brd
def test_a_keyboard_interrupt_in_one_lane_cancels_the_other_and_propagates(project):
    """A BaseException is not an escalation (§7): it leaves the loop, and
    `asyncio.run` cancels the other lane where it stands. The rows stay as they
    were, for `am resume`."""
    shape = _milestone(project, {"A": 1, "B": 1})
    story_a, story_b = shape["stories"]["A"], shape["stories"]["B"]
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    pair = asyncio.Barrier(2)
    cancelled: list[str] = []

    async def meet_then_wait_to_be_cancelled(stop: StopSignal | None) -> None:
        await _within(pair.wait(), "a1 and b1 in flight together")
        try:
            await _within(asyncio.Event().wait(), "the lane to be cancelled")
        except asyncio.CancelledError:
            cancelled.append(b1)
            raise

    driver = GatedDriver(
        outcomes={a1: KeyboardInterrupt()},
        gates={a1: _meet(pair), b1: meet_then_wait_to_be_cancelled},
    )

    with pytest.raises(KeyboardInterrupt):
        _run(project, shape["milestone"], driver, max_concurrent=2)

    assert cancelled == [b1]
    run = _load(project, cli.mint_run_id(shape["milestone"], STARTED_AT))
    assert _statuses(run) == {
        "run": "started",
        story_a: "started",
        a1: "started",
        story_b: "started",
        b1: "started",
    }
```

Replace `test_warnings_and_completed_follow_census_order_not_finish_order` (lines 1977-1996) with:

```python
@requires_git
@requires_brd
def test_warnings_and_completed_follow_census_order_not_finish_order(project):
    shape = _milestone(project, {"A": 1, "B": 1})
    (first, second) = _census_levels(project, shape["milestone"])[0]
    subtasks = _subtasks_by_story(shape)
    (f1,), (s1,) = subtasks[first], subtasks[second]
    second_returned = asyncio.Event()

    async def after_second_returns(stop: StopSignal | None) -> None:
        await _within(second_returned.wait(), "the second lane's call to end")

    driver = GatedDriver(
        warnings={f1: ["first warned"], s1: ["second warned"]},
        gates={f1: after_second_returns},
        returned={s1: second_returned},
    )

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert sorted(call["card"] for call in driver.calls) == sorted([f1, s1])
    assert result["done"] is True, result
    assert result["completed"] == [f1, s1]
    assert result["warnings"] == ["first warned", "second warned"]
```

- [ ] **Step 3: Add the new engine-tier tests**

Append to `tests/test_orchestrate.py`, after `test_warnings_and_completed_follow_census_order_not_finish_order` and before `# ── relaunch continues an open checkpoint`:

```python
# ── the supervisor tree (supervisor-tree T1-T6) ─────────────────────────────


@requires_git
@requires_brd
def test_a_story_starts_when_its_blocker_finishes_not_its_level(project):
    """T1: C (blocked by A) starts the moment A is done, while B -- in A's wave
    -- is still in flight. b1 cannot finish until c1 has started, so under a
    level barrier b1 would time out and the run would escalate instead."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A"]})
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    c_started = asyncio.Event()

    async def hold_until_c_starts(stop: StopSignal | None) -> None:
        await _within(c_started.wait(), "c1 to start while b1 is in flight")

    async def mark_c_started(stop: StopSignal | None) -> None:
        c_started.set()

    driver = GatedDriver(gates={b1: hold_until_c_starts, c1: mark_c_started})

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True, result
    cards = [call["card"] for call in driver.calls]
    assert cards.index(a1) < cards.index(c1)
    assert next(call for call in driver.calls if call["card"] == c1)["base"] == _branch(
        project, a1
    )
    assert [level["stories"] for level in result["levels"]] == _census_levels(
        project, shape["milestone"]
    )


@requires_git
@requires_brd
def test_a_chain_finishes_with_one_slot(project):
    """T4: a lane takes its slot only after its blockers finished, so a chain
    never holds a slot while it waits and cannot deadlock."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1}, blocked_by={"B": ["A"], "C": ["B"]}
    )
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    driver = GatedDriver()

    result = _run(project, shape["milestone"], driver, max_concurrent=1)

    assert result["done"] is True, result
    assert [call["card"] for call in driver.calls] == [a1, b1, c1]
    assert [call["base"] for call in driver.calls] == [
        "main",
        _branch(project, a1),
        _branch(project, b1),
    ]
    assert result["completed"] == [a1, b1, c1]


@requires_git
@requires_brd
def test_a_story_behind_a_subtask_less_story_waits_for_the_blocker_beneath(project):
    """Review Focus 2: J has no subtasks, so C's stack roots on A's tip through
    it. `dag.compute_levels` puts C in wave 0 beside A, but C's node waits on
    J's, which waits on A's: c1 never starts before a1 has returned."""
    shape = _milestone(project, {"A": 1, "J": 0, "C": 1}, blocked_by={"J": ["A"], "C": ["J"]})
    (a1,) = shape["subtasks"]["A"]
    (c1,) = shape["subtasks"]["C"]
    a1_returned = asyncio.Event()

    async def a1_must_have_returned(stop: StopSignal | None) -> None:
        assert a1_returned.is_set(), "c1 started before a1, the tip it stacks on, returned"

    driver = GatedDriver(gates={c1: a1_must_have_returned}, returned={a1: a1_returned})

    result = _run(project, shape["milestone"], driver, max_concurrent=2)

    assert result["done"] is True, result
    assert [call["card"] for call in driver.calls] == [a1, c1]
    assert driver.calls[1]["base"] == _branch(project, a1)


@requires_git
@requires_brd
def test_a_milestone_with_no_stories_finishes_without_a_tree(project, integrate_recorder):
    """Review Focus 1: no story means no root node, and grafo's executor cannot
    run an empty tree; the run is still a clean `done` that integrates."""
    milestone = _add_card(project, "Milestone 3: nothing in it yet")
    driver = GatedDriver()

    result = _run(project, milestone, driver, max_concurrent=2)

    assert driver.calls == []
    assert result["done"] is True, result
    assert result["levels"] == []
    assert result["completed"] == []
    assert [call["stories"] for call in integrate_recorder.calls] == [[]]


@requires_git
@requires_brd
def test_a_lane_bug_becomes_escalated_with_type_and_message(project):
    """A driver that raises is a lane bug: `escalated` at the subtask it was
    driving, with `"<Type>: <msg>"`, never a crash."""
    shape = _milestone(project, {"A": 2})
    story_a = shape["stories"]["A"]
    a1, a2 = shape["subtasks"]["A"]
    driver = GatedDriver(outcomes={a2: ValueError("lane bug")})

    result = _run(project, shape["milestone"], driver)

    assert result["escalated"] is True, result
    assert (result["story"], result["subtask"], result["failed_phase"], result["detail"]) == (
        story_a,
        a2,
        None,
        "ValueError: lane bug",
    )
    assert result["warnings"] == []
    assert _statuses(_load(project, result["run_id"])) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "done",
        a2: "escalated",
    }


@requires_git
@requires_brd
def test_every_node_has_no_timeout(project, monkeypatch):
    """Review Focus 1: grafo's default node timeout (60 s) would cancel a lane
    mid-phase, so every node -- done stories' included -- is built with
    `timeout=None`."""
    built: list[grafo.Node] = []

    class RecordingNode(grafo.Node):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            built.append(self)

    monkeypatch.setattr(grafo, "Node", RecordingNode)
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "D": 1}, blocked_by={"C": ["A"]}
    )
    (d1,) = shape["subtasks"]["D"]
    for card in (d1, shape["stories"]["D"]):
        board.set_status(card, "done", repo_dir=project)

    result = _run(project, shape["milestone"], GatedDriver(), max_concurrent=2)

    assert result["done"] is True, result
    assert sorted(node.uuid for node in built) == sorted(shape["stories"].values())
    assert [node._timeout for node in built] == [None] * len(built)


def _patch_default_node_timeout(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    """Make `grafo.Node`'s default `timeout` `seconds` for this test."""
    init = grafo.Node.__init__
    params = list(inspect.signature(init).parameters)
    defaults = list(init.__defaults__)
    defaults[params.index("timeout") - (len(params) - len(defaults))] = seconds
    monkeypatch.setattr(init, "__defaults__", tuple(defaults))


async def _noop() -> None:
    return None


@requires_git
@requires_brd
def test_a_lane_outlives_the_default_node_timeout(project, monkeypatch):
    """Review Focus 1: with grafo's default patched to 0.05 s, a lane still in
    flight well past it is not cancelled and the story ends `done`."""
    _patch_default_node_timeout(monkeypatch, PATCHED_NODE_TIMEOUT)
    assert grafo.Node(coroutine=_noop)._timeout == PATCHED_NODE_TIMEOUT  # the patch bites
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]

    async def outlast_the_default(stop: StopSignal | None) -> None:
        released = asyncio.Event()
        asyncio.get_running_loop().call_later(LONGER_THAN_PATCHED_TIMEOUT, released.set)
        await _within(released.wait(), "the release timer")

    driver = GatedDriver(gates={a1: outlast_the_default})

    result = _run(project, shape["milestone"], driver)

    assert result["done"] is True, result
    assert result["completed"] == [a1]


@requires_git
@requires_brd
def test_report_keeps_levels_as_waves(project):
    """Levels stop being barriers but stay in the report: `dag.compute_levels`."""
    shape = _milestone(
        project, {"A": 1, "B": 1, "C": 1, "D": 1}, blocked_by={"C": ["A"], "D": ["C"]}
    )
    plan = census.flatten_milestone(board.tree(shape["milestone"], repo_dir=project))
    waves = dag.compute_levels(plan.stories)

    result = _run(project, shape["milestone"], GatedDriver(), max_concurrent=2)

    assert result["done"] is True, result
    assert result["levels"] == [
        {"level": index, "stories": [story.id for story in wave]}
        for index, wave in enumerate(waves)
    ]
    assert len(result["levels"]) == 3


def test_only_orchestrate_imports_grafo():
    """T10: grafo is a runtime dependency of exactly one module."""
    package = Path(orchestrate.__file__).parent
    importers: set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            if any(name == "grafo" or name.startswith("grafo.") for name in names):
                importers.add(path.relative_to(package).as_posix())
    assert importers == {"orchestrate.py"}


# ── real M6 subtask agents under the tree ───────────────────────────────────


@pytest.fixture
def fresh_pygents():
    """Fresh pygents registries and compile cache, as `tests/runtime/conftest.py`
    gives every runtime test: this module's agents must not collide with others."""
    from pygents import AgentRegistry, ToolRegistry

    from agent_manager.runtime import compile as compile_mod

    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()
    yield
    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()


class _ThreadWatch:
    """A `pause()`-only stand-in on the run's StopSignal whose flag a step, in
    its `to_thread` worker, can wait on."""

    def __init__(self) -> None:
        self.paused = threading.Event()

    def pause(self) -> None:
        self.paused.set()


@requires_git
@requires_brd
def test_an_escalation_parks_running_lanes_and_blocks_new_ones(project, fresh_pygents):
    """Spec test 4, on real M6 pygents subtask agents over step-only workflows
    (the fake runner): A's step fails once B's first phase is in flight; the
    lane triggers the stop; B's agent is paused, finishes its phase and parks
    before `b_second` through ON_PAUSE; C, blocked by A, is never started."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1}, blocked_by={"C": ["A"]})
    story_a, story_b, story_c = (shape["stories"][key] for key in "ABC")
    (a1,) = shape["subtasks"]["A"]
    (b1,) = shape["subtasks"]["B"]
    (c1,) = shape["subtasks"]["C"]
    b_in = threading.Event()
    watch = _ThreadWatch()
    ran: list[str] = []

    def a_work(card: str) -> dict[str, Any]:
        ran.append("a_work")
        if not b_in.wait(WAIT):
            raise RuntimeError("B's first phase never started")
        raise RuntimeError("a failed on purpose")

    def b_first(card: str) -> dict[str, Any]:
        ran.append("b_first")
        b_in.set()
        if not watch.paused.wait(WAIT):
            raise RuntimeError("the stop was never triggered")
        return {"b_first": 1}

    def b_second(card: str) -> dict[str, Any]:
        ran.append("b_second")
        return {"b_second": 2}

    def c_work(card: str) -> dict[str, Any]:
        ran.append("c_work")
        return {"c_work": 3}

    workflows = {
        a1: Workflow("m7_supervise_a_escalates", (Step("a_work", a_work),)),
        b1: Workflow("m7_supervise_b_parks", (Step("b_first", b_first), Step("b_second", b_second))),
        c1: Workflow("m7_supervise_c_never", (Step("c_work", c_work),)),
    }
    called: list[str] = []

    async def drive(*, store, run_id, card, parent, subtask, repo_dir, stop=None, **_: Any):
        called.append(card.id)
        if card.id == b1:
            stop.register(watch)
        try:
            summary = await runtime_engine.run_subtask_async(
                workflows[card.id],
                store,
                story_id=parent.id,
                subtask=subtask,
                repo_dir=repo_dir,
                stop=stop,
            )
        finally:
            if card.id == b1:
                stop.unregister(watch)
        return cli.SubtaskDrive(summary=summary, warnings=list(summary.warnings))

    result = _run(project, shape["milestone"], drive, max_concurrent=2)

    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert sorted(called) == sorted([a1, b1])
    assert sorted(ran) == ["a_work", "b_first"]
    assert result["escalated"] is True, result
    assert (result["story"], result["subtask"]) == (story_a, a1)
    assert "a failed on purpose" in result["detail"]
    assert "also_escalated" not in result
    assert result["stopped"] == [{"story": story_b, "subtask": b1, "before_phase": "b_second"}]
    assert _statuses(_load(project, run_id)) == {
        "run": "escalated",
        story_a: "escalated",
        a1: "escalated",
        story_b: "stopped",
        b1: "stopped",
        story_c: "pending",
        c1: "pending",
    }
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        newest = opened.latest_checkpoint(b1)
    finally:
        opened.close()
    assert newest.reason == "parked"
    assert newest.agent["queue"][0]["kwargs"]["phase"] == "b_second"
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: FAIL — every run test that reaches a driver fails with `AttributeError: 'coroutine' object has no attribute 'warnings'` (the thread lane calls the now-async fakes without awaiting them), `test_no_driver_resolves_to_cli_drive_subtask_async_at_call_time` fails because the default is still `cli.drive_subtask`, and `test_only_orchestrate_imports_grafo` fails with `assert set() == {'orchestrate.py'}`. The pure tests from Task 1 still pass.

- [ ] **Step 5: Rewrite `orchestrate.py`'s imports, docstring and `Driver`**

Replace the module docstring (lines 1-25) with:

```python
"""The milestone runner (orchestration addendum O6; supervisor-tree T1-T6).

`run_milestone` drives every remaining subtask of one milestone through the
shared per-subtask driver (O4) on one event loop: `asyncio.run(supervise(...))`.
`supervise` builds one `grafo.Node` per census story -- done ones included,
every one with `timeout=None` -- and one edge per in-milestone blocker, so a
`grafo.TreeExecutor` starts each story the moment all its blockers succeeded.
A story's lane takes one of `max_concurrent` slots only once it has started,
so a waiting story never holds a slot, and its subtasks stay strictly
sequential. Levels are no longer barriers; they stay in the report as waves.
A lane fails by raising `LaneEscalated` or `LaneStopped`, so grafo never
releases the dependents of a lane that did not finish clean, and
`collect_outcomes` reads every story's outcome after the tree ran (T6).

Every derivation belongs to a collaborator: the milestone and its census to
`census`, waves, stack bases, roots and tips to `dag`, board reads to `board`,
rollup to `steps.rollup`, git to `steps.worktree.run_git`, run state to
`Store`. The terminal merge of every story tip belongs to `integration`. This
module decides only the order of those calls and what a run records.

The order is load-bearing. Everything that can refuse -- an unknown milestone,
a blocker cycle, a story whose root would be a merged base -- runs before the
first write, so a refusal leaves no run directory, no store, no fetch and no
prune behind.

`cli` is imported as a module and every name on it is read at call time: the
CLI wiring card makes `cli` import this module, and binding a `cli` name at
import or definition time would break under that circular import. The clock
default is this module's own `_utcnow` for the same reason.

The module holds no mutable state of its own (O4). A run's `StopSignal` is
created by `run_milestone` for that run. This is the only module that imports
`grafo`.
"""
```

Replace the imports (lines 27-39) with:

```python
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

import grafo

from agent_manager import board, census, cli, dag, integration, models
from agent_manager.runtime.stop import StopSignal
from agent_manager.steps import rollup, worktree
from agent_manager.store import Checkpoint, Store
```

Delete the `RunStop` class entirely (the `@dataclass class RunStop:` block with `escalate`).

Replace the `Driver` protocol with:

```python
class Driver(Protocol):
    """`cli.drive_subtask_async`'s keyword signature: drive one subtask, report the result.

    The seam the tests replace. Awaited by the lane on the run's one event loop
    (T3). Annotations are strings (`from __future__ import annotations`), so no
    `cli` name is resolved when this module is imported.

    `stop` is the run's `StopSignal`, passed on every call. `should_stop` stays
    until Task 3.3 deletes it; the lane never passes it. `resume_from` (card
    02890d5d) is passed only when a relaunch found a checkpoint to continue,
    so a driver written before it keeps working.
    """

    async def __call__(
        self,
        *,
        store: Store,
        run_id: str,
        card: models.Card,
        parent: models.Card,
        subtask: models.SubtaskRun,
        repo_dir: Path,
        commands: Sequence[str] = (),
        allow_no_verification: bool = False,
        runner_factory: cli.RunnerFactory | None = None,
        should_stop: Callable[[], bool] | None = None,
        stop: StopSignal | None = None,
        resume_from: Checkpoint | None = None,
    ) -> cli.SubtaskDrive: ...
```

- [ ] **Step 6: Replace `run_story_lane` with `lane` and add `supervise`**

Replace the whole `run_story_lane` function with:

```python
async def lane(
    story: census.StoryPlan,
    *,
    plan: SupervisorPlan,
    store: Store,
    run_id: str,
    root: Path,
    drive: Driver,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: cli.RunnerFactory | None,
    slots: asyncio.Semaphore,
    stop: StopSignal,
    finished: dict[str, LaneOutcome],
) -> str:
    """One story's node coroutine (T1, T4, T6): drive its remaining subtasks, return its tip.

    A story with nothing left to run returns its tip without taking a slot.
    Otherwise the lane takes a slot -- grafo started it, so every blocker
    already succeeded -- and drives the remaining subtasks in census order,
    each on the base `record_plan` recorded for it. Before each subtask it
    checks the stop: if it fired, the story is recorded `stopped` and
    `LaneStopped` is raised with that subtask never driven. A `stopped`
    summary records the subtask and story `stopped` and raises `LaneStopped`.
    Any other non-`done` summary, or any `Exception` while handling a subtask
    (a lane bug), triggers the stop first and raises `LaneEscalated` at that
    subtask. `LaneEscalated`/`LaneStopped` pass through the catch-all
    unchanged. A `BaseException` is never caught.

    Each subtask's open checkpoint is looked up first
    (`cli.continuable_checkpoint`), inside the same `try`, and handed to the
    driver as `resume_from` when it can be continued.
    """
    planned = plan.planned.get(story.id)
    if planned is None:
        return plan.tips[story.id]
    story_row, subtask_rows = plan.rows[story.id]
    completed: list[str] = []
    warnings: list[str] = []

    def outcome(kind: LaneKind, subtask: str | None, **fields: Any) -> LaneOutcome:
        return LaneOutcome(
            kind=kind,
            story=story.id,
            level=planned.level,
            subtask=subtask,
            completed=tuple(completed),
            warnings=tuple(warnings),
            **fields,
        )

    async with slots:
        current: census.SubtaskPlan | None = None
        try:
            for position, subtask in enumerate(planned.remaining):
                current = subtask
                if stop.triggered:
                    store.record_story(story_row.model_copy(update={"status": "stopped"}))
                    raise LaneStopped(outcome("stopped", subtask.id))
                row = subtask_rows[subtask.id]
                card = await asyncio.to_thread(board.show, subtask.id, repo_dir=root)
                parent = await asyncio.to_thread(board.show, story.id, repo_dir=root)
                row = row.model_copy(update={"status": "started"})
                store.record_subtask(story.id, row)
                if position == 0:
                    store.record_story(story_row.model_copy(update={"status": "started"}))
                # Relaunch continuation (card 02890d5d): the keyword is passed
                # only when there is a row, so a driver that predates it works.
                extra: dict[str, Any] = {}
                checkpoint = cli.continuable_checkpoint(store, subtask.id)
                if checkpoint is not None:
                    extra["resume_from"] = checkpoint
                result = await drive(
                    store=store,
                    run_id=run_id,
                    card=card,
                    parent=parent,
                    subtask=row,
                    repo_dir=root,
                    commands=list(commands),
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    stop=stop,
                    **extra,
                )
                warnings.extend(result.warnings)
                summary = result.summary
                # A `stopped` summary is handled before the non-`done` branch:
                # a stop is not an escalation (P4).
                if summary.status == "stopped":
                    store.record_subtask(story.id, row.model_copy(update={"status": "stopped"}))
                    store.record_story(story_row.model_copy(update={"status": "stopped"}))
                    raise LaneStopped(
                        outcome(
                            "stopped",
                            subtask.id,
                            before_phase=stopped_before_phase(summary.detail),
                        )
                    )
                if summary.status != "done":
                    stop.trigger(story.id)
                    store.record_subtask(story.id, row.model_copy(update={"status": "escalated"}))
                    store.record_story(story_row.model_copy(update={"status": "escalated"}))
                    raise LaneEscalated(
                        outcome(
                            "escalated",
                            subtask.id,
                            failed_phase=summary.failed_phase,
                            detail=summary.detail,
                        )
                    )
                store.record_subtask(story.id, row.model_copy(update={"status": "done"}))
                completed.append(subtask.id)
            store.record_story(story_row.model_copy(update={"status": "done"}))
        except (LaneEscalated, LaneStopped):
            raise
        except Exception as error:  # not BaseException: Ctrl-C must still stop
            stop.trigger(story.id)
            if current is not None:
                store.record_subtask(
                    story.id,
                    subtask_rows[current.id].model_copy(update={"status": "escalated"}),
                )
            store.record_story(story_row.model_copy(update={"status": "escalated"}))
            raise LaneEscalated(
                outcome(
                    "escalated",
                    None if current is None else current.id,
                    detail=f"{type(error).__name__}: {error}",
                )
            ) from error
    finished[story.id] = outcome("done", None)
    return planned.tip


async def supervise(
    plan: SupervisorPlan,
    *,
    store: Store,
    run_id: str,
    root: Path,
    drive: Driver,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: cli.RunnerFactory | None,
    max_concurrent: int,
    stop: StopSignal,
) -> list[LaneOutcome]:
    """Run every census story as a grafo node and collect the outcomes (T1, T6).

    One `grafo.Node` per story, `uuid=story.id`, `timeout=None` always (grafo's
    60 s default would cancel a lane mid-phase). One edge per in-milestone
    blocker, forwarding the blocker's tip as `tip_<short id>` (Task 3.2 reads
    those for merged bases). The executor's roots are the stories with no
    in-milestone blocker; a milestone with no story has no tree to run.
    """
    slots = asyncio.Semaphore(max_concurrent)
    finished: dict[str, LaneOutcome] = {}

    def node_coroutine(story: census.StoryPlan) -> Callable[..., Any]:
        async def run(**tips: str) -> str:
            return await lane(
                story,
                plan=plan,
                store=store,
                run_id=run_id,
                root=root,
                drive=drive,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                slots=slots,
                stop=stop,
                finished=finished,
            )

        return run

    nodes = {
        story.id: grafo.Node(coroutine=node_coroutine(story), uuid=story.id, timeout=None)
        for story in plan.stories
    }
    for story in plan.stories:
        for blocker in plan.roots[story.id].blockers:
            await nodes[blocker].connect(
                nodes[story.id], forward=f"tip_{dag.short_id(blocker)}"
            )
    roots = [nodes[story.id] for story in plan.stories if not plan.roots[story.id].blockers]
    errors: list[BaseException] = []
    if roots:
        executor = grafo.TreeExecutor(uuid=run_id, roots=roots)
        await executor.run()
        errors = list(executor.errors)
    return collect_outcomes(plan, nodes, errors, finished)
```

- [ ] **Step 7: Rewrite `run_milestone`**

Replace the whole `run_milestone` function with:

```python
def run_milestone(
    milestone: str,
    *,
    repo_dir: Path,
    base_branch: str,
    branch_prefix: str,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: cli.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
) -> dict[str, Any]:
    """Drive every remaining subtask of `milestone` as a grafo tree, and report (O6, T1-T6).

    `milestone` is a card id or a title needle (O1). Everything that can refuse,
    `max_concurrent < 1` included, runs before the store is opened. Then one
    `milestone` run is recorded with its whole plan `pending`, and
    `asyncio.run(supervise(...))` runs every story the moment its blockers
    succeeded, at most `max_concurrent` at once. A subtask already `done` on
    the board is never driven, but its branch still anchors the next
    subtask's base. The card and its story are read fresh from the board
    before each subtask. The default driver is `cli.drive_subtask_async`,
    read at call time.

    The first escalation triggers the run's `StopSignal`: running subtasks
    park at their next phase boundary and are recorded `stopped`, a lane
    between subtasks or waiting for a slot ends `stopped` without driving
    anything more, and grafo starts no dependent of a failed lane, so those
    stories stay `pending`.

    When every lane finished clean -- or none had anything to run --
    Integrate folds every story tip into `<branch_prefix>-integrate` before
    the run is recorded. Success records `done` and adds `integrated`; an
    Integrate escalation records `escalated` and returns
    `integrate_escalated_payload`. An exception from Integrate propagates and
    the run is never recorded `done`.
    """
    if max_concurrent < 1:
        raise ValueError(f"max_concurrent must be at least 1, got {max_concurrent}")
    root = cli.resolve_repo_dir(repo_dir)
    milestone_card = census.find_milestone(board.roots(repo_dir=root), milestone)
    plan = census.flatten_milestone(board.tree(milestone_card.id, repo_dir=root))
    levels = plan_levels(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    tips = story_tips(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    drive = cli.drive_subtask_async if driver is None else driver

    # The first side effect. It runs after every refusal and before the store
    # is opened, so a failed fetch leaves no run directory behind.
    refresh_git(root)

    started_at = clock()
    run_id = cli.mint_run_id(milestone_card.id, started_at)
    store = Store.open(root, run_id)
    try:
        run_record = models.Run(
            id=run_id,
            workflow=MILESTONE_WORKFLOW,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(max_concurrent_stories=max_concurrent),
        )
        store.record_run(run_record)
        rows = record_plan(store, levels, root=root, branch_prefix=branch_prefix)
        warnings = reroll_stale_stories(plan.stories, root)
        completed: list[str] = []
        stop = StopSignal()

        outcomes = asyncio.run(
            supervise(
                supervisor_plan(
                    plan.stories,
                    levels,
                    rows,
                    branch_prefix=branch_prefix,
                    base_branch=base_branch,
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
            )
        )
        # Wave order, census order within a wave, never finish order.
        for outcome in outcomes:
            completed.extend(outcome.completed)
            warnings.extend(outcome.warnings)
        if any(outcome.kind == "escalated" for outcome in outcomes):
            store.record_run(run_record.model_copy(update={"status": "escalated"}))
            primary = next((outcome.story for outcome in outcomes if outcome.primary), None)
            return escalated_payload(run_id, primary, outcomes, warnings)

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
            return integrate_escalated_payload(run_id, outcome, warnings)

        store.record_run(run_record.model_copy(update={"status": "done"}))
        return {
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
    finally:
        store.close()
```

Also update `escalated_payload`'s docstring first line to `"""The escalated result for a run's lane outcomes, given in wave order.` (it is no longer one level's).

- [ ] **Step 8: Run the engine-tier tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: PASS, every test in the file, including `test_only_orchestrate_imports_grafo`, `test_every_node_has_no_timeout` and `test_a_lane_outlives_the_default_node_timeout`.

- [ ] **Step 9: Adapt the e2e test that captured `RunStop`**

In `tests/e2e/test_milestone_run.py`, add after `from agent_manager.harness import launcher`:

```python
from agent_manager.runtime.stop import StopSignal
```

Replace `_hold_b1_in_plan_until_a1_escalates` (lines 374-414) with:

```python
def _hold_b1_in_plan_until_a1_escalates(monkeypatch, board_shape) -> dict[str, bool]:
    """Make the first launch park b1 before `validate_plan`, deterministically.

    Test scaffolding in the manager process, never seen by the fake. a1's
    `review` launch waits until b1 is inside `plan`; b1's `plan` launch waits
    until the run's `StopSignal` fires, which a1's review failure (the
    review-fail marker) does. When b1's plan returns, its agent has been
    paused, so ON_PAUSE parks b1 before `validate_plan`. The signal is
    captured by a subclass because `run_milestone` builds it at call time;
    its `fired` is a `threading.Event` because the held launch runs in a
    `to_thread` worker. Returns the switch that turns the hold off for the
    relaunch.
    """
    stops: list[StopSignal] = []

    class CapturedStop(StopSignal):
        def __init__(self) -> None:
            super().__init__()
            self.fired = threading.Event()
            stops.append(self)

        def trigger(self, story_id: str) -> bool:
            first = super().trigger(story_id)
            self.fired.set()
            return first

    monkeypatch.setattr(orchestrate, "StopSignal", CapturedStop)
    (a1,) = board_shape["subtasks"]["A"]
    (b1,) = board_shape["subtasks"]["B"]
    b1_in_plan = threading.Event()
    hold = {"on": True}
    real = launcher.run_direct

    def held(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        if hold["on"]:
            attempt = _attempt_of(stdout_path)
            if attempt == (b1, "plan"):
                b1_in_plan.set()
                if not stops[-1].fired.wait(WAIT):
                    raise AssertionError("a1's escalation never triggered the run's stop")
            elif attempt == (a1, "review"):
                if not b1_in_plan.wait(WAIT):
                    raise AssertionError("b1 never reached plan")
        return real(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path, on_spawn=on_spawn
        )

    monkeypatch.setattr(cli, "run_direct", held)
    return hold
```

If `from dataclasses import dataclass` in that file is now unused, leave it only if another name in the file uses it; otherwise delete the import line.

- [ ] **Step 10: Let the parallel e2e oracle accept a stop seen between subtasks**

In `tests/e2e/test_parallel_milestone.py`, in `test_an_escalation_in_one_lane_stops_the_other_and_the_next_level_never_starts`, replace

```python
    phase_names = task_workflow.TASK.phase_names
    before = parked["before_phase"]
    assert before in phase_names, parked
    later = set(phase_names[phase_names.index(before):])
```

with

```python
    phase_names = task_workflow.TASK.phase_names
    before = parked["before_phase"]
    if before is None:
        # The lane saw the stop between b1 and b2 and never drove b2 (T6).
        assert parked["subtask"] == b2, parked
        later = set(phase_names)
    else:
        assert before in phase_names, parked
        later = set(phase_names[phase_names.index(before):])
```

and replace

```python
    stopped_row = rows[parked["subtask"]]
    assert stopped_row.status == "stopped"
```

with

```python
    stopped_row = rows[parked["subtask"]]
    assert stopped_row.status == ("pending" if before is None else "stopped")
```

- [ ] **Step 11: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, including `tests/e2e/test_parallel_milestone.py`, `tests/e2e/test_milestone_run.py` and `tests/e2e/test_integrate.py`.

- [ ] **Step 12: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py tests/e2e/test_milestone_run.py tests/e2e/test_parallel_milestone.py
git commit -m "feat(orchestrate): run stories as a grafo tree on one event loop"
```

---

### Task 3: Keep grafo's logging off stdout (Review Focus 2)

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (imports, `supervise`)
- Test: `tests/test_orchestrate.py`

**Interfaces:**
- Consumes (Task 2): `supervise(...)`, `cli.app`, `cli.EXIT_ESCALATED`, the default driver read as `cli.drive_subtask_async`.
- Produces: `supervise` sets `logging.getLogger("grafo")` to `CRITICAL` on entry and restores the previous level on every exit.

- [ ] **Step 1: Write the failing test**

Append to the `# ── the supervisor tree` section of `tests/test_orchestrate.py`:

```python
@requires_git
@requires_brd
def test_a_lane_bug_keeps_stdout_one_json_line(project, monkeypatch, capsys):
    """Review Focus 2, through the CLI: grafo logs a failing node with a
    traceback. A handler on stdout is attached to grafo's logger for this
    test, so any grafo record would land there; stdout must still be exactly
    one JSON line, and grafo's own level is back once the run ends."""
    shape = _milestone(project, {"A": 1})

    async def buggy(**kwargs: Any) -> cli.SubtaskDrive:
        raise ValueError("lane bug")

    monkeypatch.setattr(cli, "drive_subtask_async", buggy)
    grafo_logger = logging.getLogger("grafo")
    level_before = grafo_logger.level
    loud = logging.StreamHandler(sys.stdout)  # capsys's stdout, captured here
    grafo_logger.addHandler(loud)
    try:
        with pytest.raises(SystemExit) as exited:
            cli.app(
                [
                    "run",
                    "--milestone",
                    shape["milestone"],
                    "--repo-dir",
                    str(project),
                    "--base-branch",
                    "main",
                    "--branch-prefix",
                    PREFIX,
                    "--max-concurrent",
                    "1",
                ],
                prog_name="am",
            )
    finally:
        grafo_logger.removeHandler(loud)

    assert exited.value.code == cli.EXIT_ESCALATED
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) == 1, out
    envelope = json.loads(lines[0])
    assert envelope["ok"] is True
    assert envelope["data"]["escalated"] is True
    assert envelope["data"]["detail"] == "ValueError: lane bug"
    assert "Traceback" not in out
    assert grafo_logger.level == level_before
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_orchestrate.py::test_a_lane_bug_keeps_stdout_one_json_line -v`
Expected: FAIL at `assert len(lines) == 1` — stdout carries grafo's `Node ... has no expected type` warnings and the `Error on Node(...)` record with a `Traceback`, before the JSON line.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/orchestrate.py`, add `import logging` after `import asyncio`, and add this constant after `MILESTONE_WORKFLOW`'s docstring:

```python
GRAFO_LOGGER = "grafo"
"""grafo's logger. It logs every failing node with a traceback on its own
handler; `supervise` silences it so stdout stays one JSON line (T6)."""
```

Replace `supervise` with:

```python
async def supervise(
    plan: SupervisorPlan,
    *,
    store: Store,
    run_id: str,
    root: Path,
    drive: Driver,
    commands: Sequence[str],
    allow_no_verification: bool,
    runner_factory: cli.RunnerFactory | None,
    max_concurrent: int,
    stop: StopSignal,
) -> list[LaneOutcome]:
    """Run every census story as a grafo node and collect the outcomes (T1, T6).

    One `grafo.Node` per story, `uuid=story.id`, `timeout=None` always (grafo's
    60 s default would cancel a lane mid-phase). One edge per in-milestone
    blocker, forwarding the blocker's tip as `tip_<short id>` (Task 3.2 reads
    those for merged bases). The executor's roots are the stories with no
    in-milestone blocker; a milestone with no story has no tree to run.

    The `grafo` logger is at CRITICAL for exactly this call: a lane's
    escalation is data in the outcomes, never a traceback on a stream, and
    grafo's own level is restored on every exit.
    """
    grafo_logger = logging.getLogger(GRAFO_LOGGER)
    level_before = grafo_logger.level
    grafo_logger.setLevel(logging.CRITICAL)
    try:
        slots = asyncio.Semaphore(max_concurrent)
        finished: dict[str, LaneOutcome] = {}

        def node_coroutine(story: census.StoryPlan) -> Callable[..., Any]:
            async def run(**tips: str) -> str:
                return await lane(
                    story,
                    plan=plan,
                    store=store,
                    run_id=run_id,
                    root=root,
                    drive=drive,
                    commands=commands,
                    allow_no_verification=allow_no_verification,
                    runner_factory=runner_factory,
                    slots=slots,
                    stop=stop,
                    finished=finished,
                )

            return run

        nodes = {
            story.id: grafo.Node(coroutine=node_coroutine(story), uuid=story.id, timeout=None)
            for story in plan.stories
        }
        for story in plan.stories:
            for blocker in plan.roots[story.id].blockers:
                await nodes[blocker].connect(
                    nodes[story.id], forward=f"tip_{dag.short_id(blocker)}"
                )
        roots = [nodes[story.id] for story in plan.stories if not plan.roots[story.id].blockers]
        errors: list[BaseException] = []
        if roots:
            executor = grafo.TreeExecutor(uuid=run_id, roots=roots)
            await executor.run()
            errors = list(executor.errors)
        return collect_outcomes(plan, nodes, errors, finished)
    finally:
        grafo_logger.setLevel(level_before)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_orchestrate.py::test_a_lane_bug_keeps_stdout_one_json_line -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "fix(orchestrate): keep grafo's node logging off stdout during a run"
```

---

### Task 4: The parallel e2e tier speaks dataflow and pins the async wiring

The e2e tier is the oracle (T9): its assertions already hold after Task 2. This task renames the one test whose name claims a level barrier, and adds a guard that the production path awaits `cli.drive_subtask_async` on the run's loop. Both are guards: they are expected to PASS on first run, and each would fail if Task 2's wiring regressed (a sync `drive_subtask` call inside the loop raises `RuntimeError: asyncio.run() cannot be called from a running event loop`).

**Files:**
- Modify: `tests/e2e/test_parallel_milestone.py:1-13` (module docstring), `:239-242` (test name, docstring), append one test before `test_no_rendezvous_is_left_armed_for_later_tests`.

**Interfaces:**
- Consumes: fixtures `parallel_board`, `rendezvous`, `run_milestone_cli` (`tests/e2e/conftest.py`); `cli.drive_subtask_async`, `cli.drive_subtask`, `StopSignal`.
- Produces: nothing new for other tasks.

- [ ] **Step 1: Update the module docstring**

Replace lines 1-13 of `tests/e2e/test_parallel_milestone.py` with:

```python
"""Default-suite e2e tier: parallel stories through the production wiring.

Addendum P7 and main spec section 14: `am run --milestone --max-concurrent N`
runs through `typer.testing.CliRunner` on the real `cli.app` with no
`runner_factory` and no `driver`, so `orchestrate.run_milestone` reaches
`asyncio.run(supervise(...))`, the grafo tree, `cli.drive_subtask_async`,
`cli.default_runner_factory`, the real `ClaudeAdapter` and
`launcher.run_direct`. The only stand-in is the fake `claude` first on `PATH`,
armed with an implement-only rendezvous: at count 2 a run can only finish if
two lanes were inside implement at the same time. Unmarked on purpose.

Each test builds its own repo and board (`parallel_board`): A (a1 -> a2) and B
(b1 -> b2) are independent roots, C (c1) is blocked by A. Levels are waves in
the report only; C is scheduled by its blocker A (supervisor-tree T1).
"""
```

- [ ] **Step 2: Rename the escalation test to its dataflow meaning**

Replace

```python
def test_an_escalation_in_one_lane_stops_the_other_and_the_next_level_never_starts(
    parallel_board, rendezvous, run_milestone_cli, read_fake_log
):
    """Spec test 4 (P4, P5)."""
```

with

```python
def test_an_escalation_in_one_lane_stops_the_other_and_its_dependent_never_starts(
    parallel_board, rendezvous, run_milestone_cli, read_fake_log
):
    """Spec test 4 (P4, P5), as dataflow (T1, T6): C never starts because its
    own blocker A escalated and grafo does not release a failed lane's
    dependents, not because a level barrier held it."""
```

- [ ] **Step 3: Add the async wiring guard**

Add after the import block of `tests/e2e/test_parallel_milestone.py`:

```python
from agent_manager.runtime.stop import StopSignal
```

Insert before `test_no_rendezvous_is_left_armed_for_later_tests`:

```python
def test_the_lanes_await_drive_subtask_async_on_the_runs_loop(
    parallel_board, rendezvous, run_milestone_cli, monkeypatch
):
    """T3: one event loop per run. Every subtask goes through the awaitable
    `cli.drive_subtask_async`, handed the run's `StopSignal`; the sync
    `cli.drive_subtask` (its own `asyncio.run`) is never reached."""
    awaited: list[str] = []
    real = cli.drive_subtask_async

    async def spy(**kwargs):
        awaited.append(kwargs["card"].id)
        assert isinstance(kwargs["stop"], StopSignal), kwargs.get("stop")
        return await real(**kwargs)

    def forbidden(**kwargs):
        raise AssertionError("the sync cli.drive_subtask was reached from a milestone run")

    monkeypatch.setattr(cli, "drive_subtask_async", spy)
    monkeypatch.setattr(cli, "drive_subtask", forbidden)

    result = _run_two_lanes(parallel_board, rendezvous, run_milestone_cli)

    assert result.exit_code == 0, (result.output, result.exception)
    assert _envelope(result)["done"] is True
    assert sorted(awaited) == sorted(
        card for chain in parallel_board["subtasks"].values() for card in chain
    )
```

- [ ] **Step 4: Run the e2e module**

Run: `uv run pytest tests/e2e/test_parallel_milestone.py -v`
Expected: PASS (guards; see the task intro for what each would catch).

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, the whole default suite including `tests/e2e`.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/test_parallel_milestone.py
git commit -m "test(e2e): parallel milestone oracle speaks dataflow and pins the async driver"
```

---

## Self-Review

**1. Spec coverage.**
- `supervise` signature, `LaneEscalated`/`LaneStopped` with `.outcome`: Task 1 (errors), Task 2 Step 6 (`supervise`).
- `run_milestone` sync signature over `asyncio.run` with a fresh `StopSignal`: Task 2 Step 7.
- Async `Driver`, default `cli.drive_subtask_async`, `should_stop` kept, lane passes `stop=stop`: Task 2 Step 5-7; pinned by `test_no_driver_resolves_to_cli_drive_subtask_async_at_call_time` and Task 4's guard.
- Delete `ThreadPoolExecutor`, `RunStop`, the level loop: Task 2 Steps 5-7; e2e `RunStop` capture replaced in Step 9.
- One node per story, `uuid=story.id`, `timeout=None`, edges from `RootPlan.blockers` with `tip_<short id>`, roots = no in-milestone blocker, only `orchestrate.py` imports grafo: Task 2 Step 6; tests 7, 8, 13.
- grafo logger CRITICAL for the duration of `supervise`: Task 3; test 10.
- Lane behaviors (no remaining -> tip without slot; slot after start; stop check before each subtask; trigger-then-raise; `stopped` summary -> `LaneStopped`; catch-all lane bug -> `LaneEscalated` at that subtask; Lane errors re-raised unchanged): Task 2 Step 6; tests 1, 3, 6, 9, `test_a_lane_between_subtasks_sees_the_stop_and_never_drives_the_next`, `test_a_board_read_that_fails_inside_a_lane_is_an_escalation_of_that_subtask` (unchanged).
- `collect_outcomes` T6 incl. primary and foreign errors: Task 1 tests.
- `levels` as waves: Task 2 Step 7; test 12.
- `merged` refusal kept explicit in `orchestrate.py`, test adapted to `RootPlan`: existing `plan_levels` refusal untouched; test 11 in Task 2 Step 2.
- Lane bug -> `escalated`, stdout one JSON line: tests 9, 10.
- Engine tests 1-13: 1, 3, 5, 6, 7, 8, 9, 12, 13 and 4 in Task 2; 2 replaces `test_in_flight_lanes_never_exceed_the_bound`; 10 in Task 3; 11 in Task 2.
- E2E tier through the real wiring, barrier assertions turned into dataflow ones: Task 2 Step 10, Task 4.
- Base branch never moves: existing `main_before` assertions in the unchanged tests and e2e.

**2. Placeholder scan.** No TBD/TODO; every code step carries the code; tests are written out in full, including the ones only renamed.

**3. Type consistency.** `LaneOutcome(kind, story, level, ..., primary)` is used the same in Tasks 1-3; `SupervisorPlan.planned`, `.roots`, `.tips`, `.rows`, `.levels` match between `supervisor_plan`, `collect_outcomes`, `lane` and `supervise`; `collect_outcomes(plan, nodes, errors, finished)` has the same order at its only call site; `supervise` keyword names match `run_milestone`'s call; fakes accept `stop=` and `should_stop=` as the `Driver` protocol declares.

**4. Review Focus.** Five uncovered failure modes listed at the top, each with its test in the owning task (1, 2, 3, 4 in Task 2; 5 in Task 3).
