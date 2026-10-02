<!-- task-pipeline: validated -->
# Subtask 6bb4f541: Let `supervise` accept an externally-provided semaphore

Parent story: 335671fb ("supervise's DAG-building becomes reusable and semaphore-injectable"). Governing design: `docs/superpowers/specs/2026-10-01-run-board-design.md` §3.5. Built on top of c0a1345c (`build_dag_tree` extraction), which this worktree already contains (`supervise` at `src/agent_manager/orchestrate.py:1358`, semaphore creation at line 1397).

## Scope

One function: `orchestrate.supervise`, plus one new test.

- Add a keyword-only parameter `slots: asyncio.Semaphore | None = None` to `supervise`'s signature.
- Replace the unconditional `slots = asyncio.Semaphore(max_concurrent)` with: use the passed semaphore when one is given, otherwise create `asyncio.Semaphore(max_concurrent)` exactly as today.
- Everything downstream already receives `slots` through the existing `slots=slots` kwargs into `lane(...)` and `base_only_lane(...)`; no other call site changes.
- Update `supervise`'s docstring with one sentence describing the parameter (shared budget when given; own semaphore of `max_concurrent` when omitted).

Out of scope (do not touch):
- `build_dag_tree` and the node/edge construction (owned by c0a1345c, already done).
- `run_milestone` and its split into `_run_milestone_async` + sync wrapper, including forwarding `slots` from there (owned by sibling a3eaf615).
- Anything board-level (`run_board`, cross-milestone dispatch) — next story.

## Observable behavior

- `slots` omitted / `None`: behavior is byte-for-byte what it is today — `supervise` creates its own `asyncio.Semaphore(max_concurrent)`. Solo `am run --milestone` is unaffected.
- `slots` given: `supervise` uses that instance directly and does not create one. Two `supervise()` calls running concurrently with the same instance share one concurrency budget: the number of lanes holding a slot across both calls never exceeds the semaphore's capacity.
- When `slots` is given, `max_concurrent` is still accepted (the signature keeps it required) but does not size anything; the caller's semaphore is authoritative. No validation or cross-check between the two is added.

## Error paths

None new. No type checking of `slots` beyond the annotation; the existing cancellation/`BaseException` and grafo-logger-restoration behavior of `supervise` is unchanged regardless of which semaphore is in use. A shared semaphore is released by each lane exactly as today (the lanes' existing `async with slots` handling), so one call ending, escalating, or being stopped does not leak slots held from the other call's perspective.

## Tests

Test-placement rule: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 — orchestration/supervisor ("Engine") behavior is tested with a fake adapter, running atop the Steps-tier fixtures (real temp git repo + real temp `brd` board via the `project` fixture) in `tests/test_orchestrate.py`. (The proposed `2026-10-02-test-tier-design.md` addendum is not adopted and does not apply.)

1. Default path unchanged — existing suite, especially `test_at_most_max_concurrent_lanes_run` (`tests/test_orchestrate.py`) and the other `max_concurrent` tests, passes unmodified. Tier: Engine (existing tests in `tests/test_orchestrate.py`); no edits.
2. New: two `supervise()` calls sharing one semaphore never exceed its slot count combined. Build two independently-constructed `SupervisorPlan`s (via `orchestrate.supervisor_plan`) with enough parallel-ready stories that each alone would saturate the semaphore, then `await asyncio.gather(supervise(plan_a, ..., slots=shared), supervise(plan_b, ..., slots=shared))` with one `asyncio.Semaphore(n)` (e.g. `n = 2`). Drive lanes with the existing `GatedDriver`/arrival-counting pattern from `test_at_most_max_concurrent_lanes_run`, using one driver/counter spanning both calls, and assert the combined high-water mark equals `n` (reaches but never exceeds it) and every lane of both plans eventually completes. Tier: Engine — `tests/test_orchestrate.py`, fake adapter over the `project` fixture. It calls `supervise()` directly (async), since `run_milestone`/`_run` do not expose `slots` until a3eaf615.

## Verification

- fullSuite: `uv run pytest`
- typecheck: none
- lint: none

---

# Shared-semaphore `supervise` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `orchestrate.supervise` takes an optional `slots: asyncio.Semaphore | None = None` and uses it instead of creating its own, so concurrent `supervise` calls can share one concurrency budget, while the default path stays exactly as it is.

**Architecture:** One conditional in `supervise` (`src/agent_manager/orchestrate.py`) replaces the unconditional `asyncio.Semaphore(max_concurrent)`. The already-existing `slots=slots` kwargs into `lane`/`base_only_lane` carry it the rest of the way. The new Engine-tier tests call `supervise` directly (it is `async`) on two milestones built on the real temp board, sharing one semaphore, with `GatedDriver` as the fake adapter.

**Tech Stack:** Python 3, asyncio, grafo, pytest, real `git` + `brd` CLIs via the `project` fixture.

**Spec:** `docs/superpowers/specs/task-let-supervise-accept-an-6bb4f541-design.md` (prepended verbatim above).

**Base state:** Branch `m14/task-let-supervise-accept-an-6bb4f541`, cut from `origin/m14/task-extract-build-dag-tree-c0a1345c`. `supervise` is at `src/agent_manager/orchestrate.py:1358`; it already calls `build_dag_tree` (line 1439). Do not assume any other subtask's code (in particular a3eaf615's `_run_milestone_async`) exists.

## Global Constraints

- Only `supervise` changes in `src/agent_manager/orchestrate.py`; `build_dag_tree`, `lane`, `base_only_lane`, `run_milestone` stay untouched.
- New parameter is exactly `slots: asyncio.Semaphore | None = None`, keyword-only, placed last in `supervise`'s signature.
- `slots is None` path creates `asyncio.Semaphore(max_concurrent)` exactly as today.
- `max_concurrent` stays required; no cross-check against the passed semaphore's size.
- No new error paths, no type checks of `slots`.
- Tests go in `tests/test_orchestrate.py` (Engine tier per design spec §14), use `GatedDriver`, the `project` fixture, and `@requires_git`/`@requires_brd`.
- Existing tests are not edited.
- Verification: `uv run pytest`.

## Review Focus

1. Caller passes `max_concurrent` larger than the shared semaphore's capacity: the semaphore must win. Pinned by Task 1's test, which passes `max_concurrent=3` with `asyncio.Semaphore(2)` and asserts `high_water == 2`.
2. Slots must all be back after two sharing calls finish clean (no leak): Task 1's test acquires the shared semaphore to full capacity afterwards within `WAIT` seconds.
3. One sharing call escalates while the other still has work: the escalation stops only its own call (each has its own `StopSignal`), the other call still drives every lane, the combined peak stays at or below capacity, and the semaphore is back to full capacity afterwards. Pinned by Task 1's second test.
4. Two lanes from different calls contending at the exact same moment: covered by the arrival-counting gate in Task 1's first test, which holds early arrivals in flight for `OVERSHOOT_WINDOW` so any overshoot would be observed.
5. Out of scope, flagged for the board-level story and a3eaf615: `supervise` saves and restores the `grafo` logger level per call, so two overlapping calls can restore in the wrong order and leave `grafo` at CRITICAL (call B saves A's CRITICAL as its "before"). The spec fixes this behavior as unchanged, so no test pins it here; the new tests save and restore the `grafo` level themselves so they cannot leak a silenced logger into later tests.

---

### Task 1: `supervise` accepts and uses an external semaphore

**Files:**
- Modify: `src/agent_manager/orchestrate.py:1358-1397` (`supervise` signature, docstring, semaphore line)
- Test: `tests/test_orchestrate.py` (insert new helpers and two tests directly after `test_at_most_max_concurrent_lanes_run`, i.e. after its last line `    assert driver.high_water == 2` at line 2282 and before `test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending`)

**Interfaces:**
- Consumes (all existing): `orchestrate.plan_levels(stories, *, branch_prefix, base_branch)`, `orchestrate.record_plan(store, levels, *, root, branch_prefix)`, `orchestrate.supervisor_plan(stories, levels, rows, *, branch_prefix, base_branch)`, `orchestrate.MILESTONE_WORKFLOW`, `store_module.Store.open(root, run_id)`, `cli.resolve_repo_dir`, `cli.mint_run_id`, test helpers `_milestone`, `_subtasks_by_story`, `_load`, `_statuses`, `GatedDriver`, `PREFIX`, `STARTED_AT`, `WAIT`, `OVERSHOOT_WINDOW`, `requires_git`, `requires_brd`.
- Produces: `orchestrate.supervise(plan, *, store, run_id, root, drive, commands, allow_no_verification, runner_factory, max_concurrent, stop, slots: asyncio.Semaphore | None = None) -> list[LaneOutcome]`. Sibling a3eaf615 will forward `slots` into this keyword.

- [ ] **Step 1: Write the failing tests**

Insert this block into `tests/test_orchestrate.py` right after `test_at_most_max_concurrent_lanes_run` (after line 2282, keeping two blank lines on each side):

```python
def _supervised_run(
    project: Path, milestone: str
) -> tuple[store_module.Store, str, orchestrate.SupervisorPlan]:
    """What `run_milestone` sets up before it calls `supervise`, without the
    lease, the git refresh or Integrate: a recorded run, its planned rows, and
    the plan built from them. The caller closes the store."""
    root = cli.resolve_repo_dir(project)
    stories = census.flatten_milestone(board.tree(milestone, repo_dir=root)).stories
    levels = orchestrate.plan_levels(stories, branch_prefix=PREFIX, base_branch="main")
    run_id = cli.mint_run_id(milestone, STARTED_AT)
    store = store_module.Store.open(root, run_id)
    store.record_run(
        models.Run(
            id=run_id,
            workflow=orchestrate.MILESTONE_WORKFLOW,
            repo_dir=root,
            base_branch="main",
            branch_prefix=PREFIX,
            status="started",
            started_at=STARTED_AT,
            config=models.RunConfig(max_concurrent_stories=3),
            milestone_id=milestone,
        )
    )
    rows = orchestrate.record_plan(store, levels, root=root, branch_prefix=PREFIX)
    plan = orchestrate.supervisor_plan(
        stories, levels, rows, branch_prefix=PREFIX, base_branch="main"
    )
    return store, run_id, plan


async def _supervise_shared(
    project: Path,
    store: store_module.Store,
    run_id: str,
    plan: orchestrate.SupervisorPlan,
    driver: Any,
    slots: asyncio.Semaphore,
) -> list[orchestrate.LaneOutcome]:
    """One `supervise` call on the shared `slots`, with its own `StopSignal`.
    `max_concurrent=3` is deliberately larger than any shared semaphore these
    tests pass: the caller's semaphore, not `max_concurrent`, is the bound."""
    return await orchestrate.supervise(
        plan,
        store=store,
        run_id=run_id,
        root=cli.resolve_repo_dir(project),
        drive=driver,
        commands=[],
        allow_no_verification=True,
        runner_factory=None,
        max_concurrent=3,
        stop=StopSignal(),
        slots=slots,
    )


async def _refill(slots: asyncio.Semaphore, capacity: int) -> None:
    """Acquire `slots` `capacity` times, then release them all: fails after
    WAIT seconds if any lane left a slot held."""
    for _ in range(capacity):
        await _within(slots.acquire(), "a slot a finished lane should have released")
    for _ in range(capacity):
        slots.release()


@requires_git
@requires_brd
def test_two_supervise_calls_sharing_one_semaphore_never_exceed_it_combined(project):
    """Two milestones of three ready stories each, one shared two-slot
    semaphore, `max_concurrent=3` per call. Every lane stays in flight until a
    third lane enters the driver or the window expires: if each call used its
    own semaphore, a third (and more) would arrive inside the window; shared,
    only two lanes across both calls can ever be in flight."""
    shape_a = _milestone(project, {"A": 1, "B": 1, "C": 1})
    shape_b = _milestone(project, {"D": 1, "E": 1, "F": 1})
    subtasks = {**_subtasks_by_story(shape_a), **_subtasks_by_story(shape_b)}
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
    store_a, run_a, plan_a = _supervised_run(project, shape_a["milestone"])
    store_b, run_b, plan_b = _supervised_run(project, shape_b["milestone"])

    async def scenario() -> tuple[list[orchestrate.LaneOutcome], list[orchestrate.LaneOutcome]]:
        shared = asyncio.Semaphore(2)
        outcomes_a, outcomes_b = await asyncio.gather(
            _supervise_shared(project, store_a, run_a, plan_a, driver, shared),
            _supervise_shared(project, store_b, run_b, plan_b, driver, shared),
        )
        await _refill(shared, 2)
        return outcomes_a, outcomes_b

    # Two overlapping calls each save and restore grafo's level; restore it
    # here so this test can never leave grafo silenced for later tests.
    grafo_logger = logging.getLogger(orchestrate.GRAFO_LOGGER)
    level_before = grafo_logger.level
    try:
        outcomes_a, outcomes_b = asyncio.run(scenario())
    finally:
        grafo_logger.setLevel(level_before)
        store_a.close()
        store_b.close()

    assert driver.high_water == 2
    assert sorted(call["card"] for call in driver.calls) == sorted(
        card for cards in subtasks.values() for card in cards
    )
    for shape, outcomes, run_id in (
        (shape_a, outcomes_a, run_a),
        (shape_b, outcomes_b, run_b),
    ):
        assert [outcome.kind for outcome in outcomes] == ["done", "done", "done"]
        assert {outcome.story for outcome in outcomes} == set(shape["stories"].values())
        statuses = _statuses(_load(project, run_id))
        del statuses["run"]  # only run_milestone records the run's final status
        assert set(statuses.values()) == {"done"}


@requires_git
@requires_brd
def test_an_escalation_in_one_sharing_call_leaves_the_other_its_slots(project):
    """Milestone X's only lane escalates; milestone Y's three lanes share the
    same two-slot semaphore. The escalation stops only X (each call has its own
    StopSignal), every Y lane still runs to done, the combined peak never
    exceeds two, and both slots are free once both calls return."""
    shape_x = _milestone(project, {"X": 1})
    shape_y = _milestone(project, {"P": 1, "Q": 1, "R": 1})
    (x1,) = shape_x["subtasks"]["X"]
    y_subtasks = _subtasks_by_story(shape_y)
    driver = GatedDriver(outcomes={x1: ("review", "reviewer found a blocker")})
    store_x, run_x, plan_x = _supervised_run(project, shape_x["milestone"])
    store_y, run_y, plan_y = _supervised_run(project, shape_y["milestone"])

    async def scenario() -> tuple[list[orchestrate.LaneOutcome], list[orchestrate.LaneOutcome]]:
        shared = asyncio.Semaphore(2)
        outcomes_x, outcomes_y = await asyncio.gather(
            _supervise_shared(project, store_x, run_x, plan_x, driver, shared),
            _supervise_shared(project, store_y, run_y, plan_y, driver, shared),
        )
        await _refill(shared, 2)
        return outcomes_x, outcomes_y

    grafo_logger = logging.getLogger(orchestrate.GRAFO_LOGGER)
    level_before = grafo_logger.level
    try:
        outcomes_x, outcomes_y = asyncio.run(scenario())
    finally:
        grafo_logger.setLevel(level_before)
        store_x.close()
        store_y.close()

    assert driver.high_water <= 2
    assert [(outcome.kind, outcome.subtask) for outcome in outcomes_x] == [("escalated", x1)]
    assert [outcome.kind for outcome in outcomes_y] == ["done", "done", "done"]
    assert sorted(call["card"] for call in driver.calls) == sorted(
        [x1, *(card for cards in y_subtasks.values() for card in cards)]
    )
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "sharing_one_semaphore or sharing_call_leaves" -v`
Expected: both FAIL with `TypeError: supervise() got an unexpected keyword argument 'slots'`.

- [ ] **Step 3: Add the parameter to `supervise`'s signature**

In `src/agent_manager/orchestrate.py`, change the end of `supervise`'s parameter list from:

```python
    max_concurrent: int,
    stop: StopSignal,
) -> list[LaneOutcome]:
    """Run every census story as a grafo node and collect the outcomes (T1, T6).
```

to:

```python
    max_concurrent: int,
    stop: StopSignal,
    slots: asyncio.Semaphore | None = None,
) -> list[LaneOutcome]:
    """Run every census story as a grafo node and collect the outcomes (T1, T6).
```

- [ ] **Step 4: Document the parameter in the docstring**

In the same docstring, replace:

```python
    signal on exit, success or not, so this never hangs. A milestone with no
    story has no tree to run.

    A lane that dies of a `BaseException` other than a cancellation ends the
```

with:

```python
    signal on exit, success or not, so this never hangs. A milestone with no
    story has no tree to run.

    `slots` is the semaphore every lane takes its slot from: when given it is
    used as is, so concurrent `supervise` calls handed the same one share one
    budget and `max_concurrent` sizes nothing; when omitted this call makes its
    own `asyncio.Semaphore(max_concurrent)`, as a solo run always has.

    A lane that dies of a `BaseException` other than a cancellation ends the
```

- [ ] **Step 5: Make the semaphore creation conditional**

In the same function body, replace:

```python
    try:
        slots = asyncio.Semaphore(max_concurrent)
        finished: dict[str, LaneOutcome] = {}
```

with:

```python
    try:
        if slots is None:
            slots = asyncio.Semaphore(max_concurrent)
        finished: dict[str, LaneOutcome] = {}
```

Leave the existing `slots=slots` kwarg in `node_coroutine`'s `lane(...)` call and everything else in `supervise` unchanged.

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "sharing_one_semaphore or sharing_call_leaves" -v`
Expected: 2 passed.

- [ ] **Step 7: Run the unchanged default-path tests**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: every test in the file PASSES, including `test_at_most_max_concurrent_lanes_run` and every other `_run(..., max_concurrent=...)` test, with none of them edited.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: all tests pass (the `e2e`-marked test stays deselected by default as before).

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: let supervise accept an externally-provided semaphore"
```
