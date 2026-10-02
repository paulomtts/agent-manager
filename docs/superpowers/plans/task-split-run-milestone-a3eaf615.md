<!-- task-pipeline: validated -->
# Split `run_milestone` into a sync wrapper and an async core (card a3eaf615)

Parent story: 335671fb "supervise's DAG-building becomes reusable and semaphore-injectable". Milestone design: `docs/superpowers/specs/2026-10-01-run-board-design.md` §3.5. This is the last of the story's three subtasks.

## Base

Branch from the stacked sibling branch `m14/task-let-supervise-accept-an-6bb4f541` (tip `39f4743`), not bare master. On that base `supervise` already takes `slots: asyncio.Semaphore | None = None` and creates its own `asyncio.Semaphore(max_concurrent)` when given `None`, and `build_dag_tree` is already extracted. Line numbers below are from that base (`src/agent_manager/orchestrate.py`).

## Scope

All changes are in `src/agent_manager/orchestrate.py` and `tests/test_orchestrate.py`.

1. New `async def _run_milestone_async(...)`. It takes the same parameters as `run_milestone` (`milestone` positional; `repo_dir`, `base_branch`, `branch_prefix`, `commands`, `allow_no_verification`, `runner_factory`, `driver`, `clock`, `max_concurrent`, `resume_run_id`, `control_interval` keyword-only, same defaults), plus one new keyword-only parameter, `slots: asyncio.Semaphore | None = None`. Its body is everything `run_milestone` currently does after the argument-validation block: from `root = runs.resolve_repo_dir(repo_dir)` (line 1575) through the closing `finally: store.close()` (line 1756). That covers resume resolution, plan and levels, `refuse_claimed`, `refresh_git`, `Store.open`, `run_lease`, `record_run` and `record_plan`, `reopen_rows`, `supervise`, outcome precedence, Integrate, and payload construction. The code moves without changes, with two exceptions:
   - The inner `asyncio.run(control.controlled(supervise(...)))` (lines 1653-1679) becomes `await control.controlled(supervise(...))`.
   - The `supervise(...)` call gets `slots=slots` added to its keyword arguments. Every other argument stays as it is, including `max_concurrent=max_concurrent`.
2. `run_milestone` becomes a thin sync wrapper. It keeps its signature, return type, and docstring exactly as they are. It also keeps the validation block (lines 1568-1574: the `resume_run_id is None` checks on `max_concurrent < 1` and on a missing `milestone`, `base_branch` or `branch_prefix`). After validation it returns `asyncio.run(_run_milestone_async(...))`, passing every argument through unchanged and not passing `slots`, so the core falls back to `None` and `supervise` creates its own semaphore.
3. `_run_milestone_async` gets a short docstring. It should say that the function is `run_milestone`'s body without validation, that callers which skip the wrapper must validate their own arguments, and that `slots`, when given, is forwarded to `supervise` and bounds this run's lanes instead of `max_concurrent`.

## Observable behavior

- Solo `am run --milestone` behavior and every `run_milestone` payload are unchanged byte for byte: fresh, resumed, escalated, paused, cancelled, Integrate-escalated and done. The same goes for the side effects and their order: refusals before `refresh_git`/`Store.open`, lease and claims held from `record_run` to the final record, and the store closed on every exit.
- When `_run_milestone_async` is awaited in a caller's event loop with a `slots` semaphore, at most that semaphore's capacity of this run's lanes are in flight at once, whatever `max_concurrent` is. Every slot is released when the call returns.
- Blocking synchronous calls inside the core stay synchronous: board reads, `refresh_git`, and `integration.integrate_milestone`. Moving them off the loop thread is not part of this subtask.

## Error paths

- The `ValueError`s from validation are still raised by `run_milestone` before any event loop is created. `_run_milestone_async` does not repeat those checks.
- Every other exception propagates out of `asyncio.run` unchanged, as it does today, with the store closed and the lease and claims released. That includes `ClaimedError`, `RunIsLiveError`, resume refusals, git failures, and an Integrate exception.
- `run_milestone` stays sync-only. Like today, it cannot be called from inside a running event loop. Callers that already have a loop use `_run_milestone_async`.

## Out of scope

- `run_board`, its `node_factory`, and the board-level dispatch in §3.4 belong to a later story.
- No further changes to `supervise`'s signature or body (sibling 6bb4f541) and no changes to `build_dag_tree` (sibling c0a1345c).
- No CLI changes.

## Tests

The test-placement rule is `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14. The test-tier addendum (2026-10-02) is still only proposed, so §14 is the rule that applies.

- **Existing suite, unchanged (all current tiers).** All existing `run_milestone` unit, Engine and `e2e` tests must pass without edits. That includes `_run` in `tests/test_orchestrate.py` and the resume helper that calls `run_milestone(None, ...)`. Run with `uv run pytest`.
- **New: `_run_milestone_async` awaited inside an existing loop with an external semaphore.** This is an Engine tier test in `tests/test_orchestrate.py`, beside `test_two_supervise_calls_sharing_one_semaphore_never_exceed_it_combined`. It is not in `tests/e2e/` and not in a new file.
  - Setup: decorate with `@requires_git`/`@requires_brd` and use the real `project` fixture. Build a milestone of three ready stories with `_milestone`. Use a `GatedDriver` whose gate holds each lane until a second lane arrives or `OVERSHOOT_WINDOW` expires.
  - Inside `asyncio.run(scenario())`, create `asyncio.Semaphore(1)` and `await orchestrate._run_milestone_async(...)`. Pass the same kwargs `_run` uses (`base_branch="main"`, `branch_prefix=PREFIX`, `clock=lambda: STARTED_AT`), plus `max_concurrent=3` and `slots=shared`. Handle Integrate the same way the existing `run_milestone` Engine tests do.
  - Assert:
    - `driver.high_water == 1`, so the external semaphore bounded the run and `max_concurrent` did not.
    - Every subtask was driven.
    - The returned payload has `done: True` and the same shape `run_milestone` returns.
    - `_refill(shared, 1)` succeeds, so no slot was left held.
  - Restore grafo's logger level in a `finally`, as the sibling test does.

---

# Split `run_milestone` into a Sync Wrapper and an Async Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move everything `run_milestone` does after argument validation into a new `async def _run_milestone_async(..., slots=None)` that forwards `slots` to `supervise`, leaving `run_milestone` as a validate-then-`asyncio.run(...)` wrapper whose signature, docstring and payloads are unchanged.

**Architecture:** One file of source changes (`src/agent_manager/orchestrate.py`). The body of `run_milestone` from line 1575 to line 1756 is cut verbatim into a new coroutine function placed directly after `run_milestone`. Only two edits are made inside the moved code: `asyncio.run(control.controlled(...))` becomes `await control.controlled(...)`, and `slots=slots` is added to the `supervise(...)` call. Tests go in `tests/test_orchestrate.py` (Engine tier, design spec §14), next to the sibling shared-semaphore tests.

**Tech Stack:** Python 3, asyncio, grafo, pytest, `uv`. Real temp git repo + real temp brd board fixtures (`project`, `_milestone`), fake driver (`GatedDriver`), autouse `integrate_recorder` fixture for Integrate.

**Spec:** `docs/superpowers/specs/task-split-run-milestone-a3eaf615-design.md` (reproduced verbatim above). Note: the orchestration prompt's spec summary was truncated at 2000 characters; this plan was written from the spec on disk, not from that summary.

## Global Constraints

- Work on branch `m14/task-split-run-milestone-a3eaf615` in worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-split-run-milestone-a3eaf615`, cut from `m14/task-let-supervise-accept-an-6bb4f541` (tip `39f4743`). `supervise(..., slots: asyncio.Semaphore | None = None)` (orchestrate.py:1358-1370) already exists there; do not change it. Do not touch `build_dag_tree`.
- `run_milestone`'s signature, return type, and docstring stay byte-for-byte unchanged.
- No existing test may be edited. The full suite must pass: `uv run pytest`.
- No CLI changes, no `run_board`.
- There is no lint or typecheck command in this repo.
- The new tests live in `tests/test_orchestrate.py` (Engine tier per design spec §14), not `tests/e2e/` and not a new file.

## Review Focus

1. Validation `ValueError`s must still be raised before any event loop is created; if validation drifts into the core, `run_milestone(max_concurrent=0)` would start a loop first. Pinned by `test_run_milestone_refuses_bad_arguments_before_starting_an_event_loop` in Task 1 (a characterization guard; it passes before and after the change).
2. The wrapper must not pass `slots`, and the core must expose exactly the wrapper's parameters plus `slots`; a dropped or renamed pass-through kwarg (e.g. `control_interval`, `resume_run_id`) would silently fall back to a default. Pinned by `test_the_async_core_takes_run_milestones_parameters_plus_slots` in Task 1.
3. A caller's semaphore smaller than `max_concurrent` must bound the run, and every slot must be free when the core returns. Pinned by `test_the_async_core_awaited_in_a_running_loop_is_bounded_by_the_callers_semaphore` in Task 1.
4. The run row must still record `max_concurrent_stories=max_concurrent` (not the semaphore's capacity) when `slots` is given, since a later resume reads it back. Asserted in the same Task 1 test.
5. Exceptions (driver crash, `ClaimedError`, Integrate exception) must still close the store and release the lease; covered by the existing unedited suite, since the `try/finally` and `with cli.run_lease(...)` move verbatim. The reviewer should check that the moved block keeps the same indentation and that no line of 1575-1756 was dropped.

---

### Task 1: Extract `_run_milestone_async` and make `run_milestone` a sync wrapper

**Files:**
- Modify: `src/agent_manager/orchestrate.py:1567-1756` (end of `run_milestone`'s docstring through `finally: store.close()`)
- Test: `tests/test_orchestrate.py` (insert after `test_an_escalation_in_one_sharing_call_leaves_the_other_its_slots`, which ends at line 2475, before `test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending` at line 2478)

**Interfaces:**
- Consumes: `orchestrate.supervise(plan, *, store, run_id, root, drive, commands, allow_no_verification, runner_factory, max_concurrent, stop, slots: asyncio.Semaphore | None = None)` from the base branch; test helpers already in `tests/test_orchestrate.py`: `requires_git`, `requires_brd`, `project`, `_milestone`, `_subtasks_by_story`, `_census_stories`, `GatedDriver`, `OVERSHOOT_WINDOW`, `_refill`, `_load`, `_statuses`, `PREFIX`, `STARTED_AT`, autouse `integrate_recorder` (`IntegrateRecorder`).
- Produces: `async def _run_milestone_async(milestone: str | None, *, repo_dir: Path, base_branch: str | None = None, branch_prefix: str | None = None, commands: Sequence[str] = (), allow_no_verification: bool = False, runner_factory: runs.RunnerFactory | None = None, driver: Driver | None = None, clock: Callable[[], datetime] = _utcnow, max_concurrent: int = 1, resume_run_id: str | None = None, control_interval: float = control.CONTROL_POLL_SECONDS, slots: asyncio.Semaphore | None = None) -> dict[str, Any]` in `agent_manager.orchestrate`. The later `run_board` story will call it directly.

- [ ] **Step 1: Write the failing tests**

Insert into `tests/test_orchestrate.py` immediately after the last line of `test_an_escalation_in_one_sharing_call_leaves_the_other_its_slots` (line 2475, the closing `)` of its final `assert sorted(...)`), keeping two blank lines between top-level definitions:

```python
def test_the_async_core_takes_run_milestones_parameters_plus_slots():
    """`_run_milestone_async` is a coroutine function taking exactly
    `run_milestone`'s parameters, same kinds and defaults, plus a keyword-only
    `slots=None`; `run_milestone` itself gains nothing."""
    assert inspect.iscoroutinefunction(orchestrate._run_milestone_async)
    wrapper = inspect.signature(orchestrate.run_milestone).parameters
    core = dict(inspect.signature(orchestrate._run_milestone_async).parameters)
    slots = core.pop("slots")
    assert slots.kind is inspect.Parameter.KEYWORD_ONLY
    assert slots.default is None
    assert "slots" not in wrapper
    assert list(core) == list(wrapper)
    for name, parameter in wrapper.items():
        assert core[name].kind is parameter.kind, name
        assert core[name].default == parameter.default, name


def test_run_milestone_refuses_bad_arguments_before_starting_an_event_loop(
    tmp_path, monkeypatch
):
    """Validation stays in the sync wrapper: a bad bound is refused before
    `asyncio.run` is ever called."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    def no_loop(*args: Any, **kwargs: Any) -> Any:
        for arg in args:
            if inspect.iscoroutine(arg):
                arg.close()
        pytest.fail("an event loop was started before validation")

    monkeypatch.setattr(orchestrate.asyncio, "run", no_loop)

    with pytest.raises(ValueError, match="max_concurrent"):
        orchestrate.run_milestone(
            "Milestone 3",
            repo_dir=tmp_path,
            base_branch="main",
            branch_prefix=PREFIX,
            max_concurrent=0,
        )


@requires_git
@requires_brd
def test_the_async_core_awaited_in_a_running_loop_is_bounded_by_the_callers_semaphore(
    project, integrate_recorder
):
    """Three ready stories, `max_concurrent=3`, but the caller hands the core a
    one-slot semaphore from its own running loop. Every lane stays in flight
    until a second lane enters the driver or the window expires: bounded by
    `max_concurrent`, a second lane would arrive inside the window; bounded by
    the caller's semaphore, only one lane is ever in flight. The run still
    finishes `done` with `run_milestone`'s payload and frees its slot."""
    shape = _milestone(project, {"A": 1, "B": 1, "C": 1})
    subtasks = _subtasks_by_story(shape)
    arrivals = 0
    second_arrived = asyncio.Event()

    async def hold_until_a_second_lane_arrives(stop: StopSignal | None) -> None:
        nonlocal arrivals
        arrivals += 1
        if arrivals >= 2:
            second_arrived.set()
        try:
            await asyncio.wait_for(second_arrived.wait(), OVERSHOOT_WINDOW)
        except TimeoutError:
            pass

    driver = GatedDriver(
        gates={cards[0]: hold_until_a_second_lane_arrives for cards in subtasks.values()}
    )

    async def scenario() -> dict[str, Any]:
        shared = asyncio.Semaphore(1)
        result = await orchestrate._run_milestone_async(
            shape["milestone"],
            repo_dir=project,
            base_branch="main",
            branch_prefix=PREFIX,
            driver=driver,
            clock=lambda: STARTED_AT,
            max_concurrent=3,
            slots=shared,
        )
        await _refill(shared, 1)
        return result

    grafo_logger = logging.getLogger(orchestrate.GRAFO_LOGGER)
    level_before = grafo_logger.level
    try:
        result = asyncio.run(scenario())
    finally:
        grafo_logger.setLevel(level_before)

    order = _census_stories(project, shape["milestone"])
    run_id = cli.mint_run_id(shape["milestone"], STARTED_AT)
    assert driver.high_water == 1
    assert sorted(call["card"] for call in driver.calls) == sorted(
        card for cards in subtasks.values() for card in cards
    )
    assert set(result) == {
        "done",
        "run_id",
        "levels",
        "completed",
        "tips",
        "warnings",
        "integrated",
    }
    assert result["done"] is True
    assert result["run_id"] == run_id
    assert result["levels"] == [{"level": 0, "stories": order}]
    assert result["completed"] == [card for story in order for card in subtasks[story]]
    assert set(result["integrated"]["merged"]) == set(order)
    (integrate_call,) = integrate_recorder.calls
    assert integrate_call["run_status"] == "started"
    run = _load(project, run_id)
    assert run.config == models.RunConfig(max_concurrent_stories=3)
    assert set(_statuses(run).values()) == {"done"}
```

(`inspect`, `logging`, `asyncio`, `Any`, `pytest`, `cli`, `models`, `orchestrate` and `StopSignal` are already imported at the top of `tests/test_orchestrate.py`, lines 21-53.)

- [ ] **Step 2: Run the new tests to verify the RED state**

Run: `uv run pytest tests/test_orchestrate.py -v -k "async_core or before_starting_an_event_loop"`
Expected: `test_the_async_core_takes_run_milestones_parameters_plus_slots` and `test_the_async_core_awaited_in_a_running_loop_is_bounded_by_the_callers_semaphore` FAIL with `AttributeError: module 'agent_manager.orchestrate' has no attribute '_run_milestone_async'`. `test_run_milestone_refuses_bad_arguments_before_starting_an_event_loop` PASSES already (it is a characterization guard that must keep passing after the split).

- [ ] **Step 3: Replace `run_milestone`'s body with the sync wrapper and add the async core**

In `src/agent_manager/orchestrate.py`, keep lines 1483-1574 (signature, docstring, and validation block) exactly as they are. Cut lines 1575-1756 (from `    root = runs.resolve_repo_dir(repo_dir)` through `        store.close()`) and replace them with this wrapper tail:

```python
    return asyncio.run(
        _run_milestone_async(
            milestone,
            repo_dir=repo_dir,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            driver=driver,
            clock=clock,
            max_concurrent=max_concurrent,
            resume_run_id=resume_run_id,
            control_interval=control_interval,
        )
    )


async def _run_milestone_async(
    milestone: str | None,
    *,
    repo_dir: Path,
    base_branch: str | None = None,
    branch_prefix: str | None = None,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
    resume_run_id: str | None = None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
    slots: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """`run_milestone`'s body without its argument validation, awaitable in a
    caller's own event loop.

    A caller that skips `run_milestone` must validate its own arguments first:
    a fresh run needs `max_concurrent >= 1` and a `milestone`, `base_branch`
    and `branch_prefix`. `slots`, when given, is forwarded to `supervise` and
    bounds this run's lanes instead of `max_concurrent`, so several runs can
    share one semaphore; `None` lets `supervise` make its own
    `asyncio.Semaphore(max_concurrent)`, as `run_milestone` does. The run is
    still recorded with `max_concurrent`. Blocking calls (board reads,
    `refresh_git`, Integrate) stay synchronous on the loop thread.
    """
```

Then paste the cut lines 1575-1756 verbatim directly below that docstring, at the same 4-space indentation they had inside `run_milestone` (no re-indentation is needed: both are top-level function bodies).

- [ ] **Step 4: Turn the inner `asyncio.run` into an `await` and forward `slots`**

Inside the pasted body of `_run_milestone_async`, replace this block (formerly orchestrate.py:1653-1679):

```python
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
```

with:

```python
            outcomes = await control.controlled(
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
                    slots=slots,
                ),
                store=store,
                stop=stop,
                lease=lease,
                interval=control_interval,
            )
```

The comment above it (`# \`controlled\` only ever parks the run through \`stop\` (C3); ...`) stays. Nothing else in the moved body changes. Confirm with a search that `_run_milestone_async` contains no remaining `asyncio.run(` and that `run_milestone` contains exactly one (the wrapper's).

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -v -k "async_core or before_starting_an_event_loop"`
Expected: all three PASS.

- [ ] **Step 6: Run the full suite to verify nothing else changed**

Run: `uv run pytest`
Expected: every test passes, with no existing test edited. In particular `test_at_most_max_concurrent_lanes_run` (the wrapper still bounds by `max_concurrent` because it passes no `slots`), `test_a_bound_below_one_is_refused_before_anything_is_written`, `test_a_fresh_run_without_a_prefix_is_refused_before_anything`, the resume tests calling `run_milestone(None, ...)`, and the live-control pause/cancel tests.

- [ ] **Step 7: Commit**

```bash
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-split-run-milestone-a3eaf615 add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-split-run-milestone-a3eaf615 commit -m "refactor: split run_milestone into a sync wrapper and an async core

_run_milestone_async holds everything after validation and forwards an
optional caller-owned semaphore to supervise; run_milestone validates and
asyncio.runs it, unchanged for every caller (card a3eaf615)."
```
