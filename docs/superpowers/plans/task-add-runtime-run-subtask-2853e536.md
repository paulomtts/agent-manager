<!-- task-pipeline: validated -->
# Subtask 2853e536: Add `runtime.run_subtask` and run the engine tests on both engines

Card: 2853e536-63d2-44fd-8dd5-7c503b51ac4d. Story: f9c19dc3 ("Run a workflow on pygents"). Plan: `docs/superpowers/plans/2026-09-25-pygents-engine.md`, Task 3.4 (lines 1003-1116). Milestone spec: `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` §8, §9, §11. This subtask builds on 023d918e (`runtime/state.py`, `runtime/bridge.py`, `runtime/compile.py`, `engine.run_one_step`) and uses it as is.

## Scope

In scope:
- Create `src/agent_manager/runtime/engine.py` with one public function, `run_subtask`, plus private helpers (`_drive`, `_run`, `_collect`, `_forget`).
- Change `tests/test_engine.py`: add a `run_subtask` fixture parametrised over both engines, move the behavioural tests onto it, and add one explicit parity test.

Out of scope. Each of these belongs to another card:
- Tests for `Goto` revision loops and `feedback`. The loop branch already exists in `compile.agent_phase`, but its tests belong to b904b9e7.
- Checkpointing, `Parked`, and the `should_stop` stop bridge. These are plan Task 4.2 (`runtime/checkpoint.py`). `run_subtask` accepts `should_stop` and stores it in `RunDeps`, but nothing on the pygents engine reads it yet.
- `TurnTimeoutError` handling.
- The `--engine` CLI flag wiring.
- Deleting the old engine. That happens at switch-over.
- Adopting pygents 0.7.0, which includes `AgentRegistry.unregister`. See decision A2 of `2026-09-25-pygents-070-adoption-design.md`.
- Any change to `runtime/context.py`, `runtime/compile.py`, `runtime/state.py`, `runtime/bridge.py`, `prompt.py`, `dispatch.py` or `engine.py`, unless this task's own tests show that a change is required.

Constraints (the story's RULES block):
- Only `src/agent_manager/runtime/` imports `pygents`.
- The whole default suite, including `tests/e2e`, stays green.
- `agent.run()` is never broken or returned out of. There is no `AFTER_TURN` checkpoint and no closure hook.
- A fake runner knows only what its brief tells it.
- `SubtaskSummary`, journal lines, phase and attempt rows, and escalation payloads stay unchanged (G10).

## Interface

```python
def run_subtask(workflow: phases.Workflow, store, *, story_id: str, subtask, repo_dir: Path,
                commands: Sequence[str] = (), card=None, parent_story=None,
                extra_context: Mapping[str, Any] | None = None, agent_runner=None,
                clock: Callable = old._utcnow, should_stop: Callable[[], bool] | None = None,
                ) -> old.SubtaskSummary
```

`run_subtask` is synchronous and makes exactly one `asyncio.run(...)` call. It has no `start_phase` parameter, because resume belongs to the yaml engine.

## Observable behaviour

1. **Binding.** The binding is built as `old.subtask_context(subtask, repo_dir, commands, card=card, parent_story=parent_story)`. Then `extra_context` is merged in, then `old._document_paths(workflow, card)`. If `extra_context` contains any key in `old.RESERVED_CONTEXT_KEYS`, `run_subtask` raises `old.EngineError` with the same message as the old engine. It raises before any agent is built and before anything is recorded. A `_document_paths` error propagates unchanged.
2. **Agent.** One pygents `Agent` is built:
   - Its name is `f"{getattr(store, 'run_id', 'run')}:{subtask.card_id}"`.
   - Its description is `workflow.name`.
   - Its tools are `compiled.agent_phase` and `compiled.step_phase`, taken from `compile.compile_workflow(workflow)`.
   - It has a fresh `ContextPool`, a `ContextQueue(limit=10)`, and `tags=["subtask"]`.

   The pool is seeded with `context.seed_item(binding)`. `compiled.first_turn()` is put on the agent.
3. **Run.** A `RunDeps(workflow, store, story_id, subtask, agent_runner, clock, should_stop)` is set in `state.current_run` for the whole run and reset in `finally`. The agent runs with `async for _ in agent.run(): pass`, and the loop always runs to completion.
4. **Collect.** `summary.results` is built from the pool as `{item.id: context.decode(item.content)}`, excluding the `context.SUBTASK` and `context.SKIPPED` items. `summary.skipped` is `list(deps.skipped)` and `summary.warnings` is `list(deps.warnings)`. Collection happens on both the success path and the failure path.
5. **Success.** The summary's status stays at its default. The engine calls `old._record_subtask_status(store, story_id, subtask, summary.status)` and returns the summary.
6. **Registry cleanup.** Whether the run succeeds or fails, the agent's name is removed from `AgentRegistry`. That lets a second `run_subtask` in the same process (the next parametrised test, or the next card) reuse the name.
   - The installed pygents is already 0.7.0 (`uv.lock`), which does have `AgentRegistry.unregister`. Do not use it here: adopting it is a separate card (decision A2 of `2026-09-25-pygents-070-adoption-design.md`, "Out of scope" above). `_forget(name)` instead does `AgentRegistry._registry.pop(name, None)`, matching the pre-0.7.0 workaround the milestone spec describes, with a comment that points at decision A2 of `2026-09-25-pygents-070-adoption-design.md` for the eventual switch to `unregister`.
   - Do not use `_items`, which does not exist on `AgentRegistry`.

## Error paths (spec §8, minus what later cards own)

| Raised inside `run()` | `run_subtask` does |
|---|---|
| `compile.Escalated(phase, detail)` | Collect results, then return `old._escalate(summary, store, story_id, subtask, esc.phase, esc.detail)`. |
| `old.EngineError`, for example a missing agent runner or an input that cannot be resolved | Re-raise unchanged, with `.phase` and `.parameter` kept, as the old engine does. It must not be swallowed by the catch-all in the next row. |
| Any other `Exception` | Collect results, then return `old._escalate(..., <running phase>, f"{type(e).__name__}: {e}")`. |
| `BaseException` | Propagate and write nothing extra. |

"Running phase" is read from `agent.to_dict()["current_turn"]["kwargs"]["phase"]` after `run()` ends. If a test shows that this value is already `None` by then, the fallback is to have `agent_phase` and `step_phase` set a `running` field on `RunDeps` on entry. That fallback is the one allowed change to `compile.py` and `state.py`. Keep whichever approach the test proves works and delete the other.

## Tests

All tests live in `tests/test_engine.py`, in the **engine tier** (base spec §14, milestone spec §9). They use the existing hand-built fake registry and fake agent runner with canned results, a real temp SQLite store, and a real temp JSONL journal. They start no harness process and call no model. Nothing goes into `tests/e2e`, and no new tier is added.

1. **`run_subtask` fixture, `params=["yaml", "pygents"]`** (engine tier).
   - `yaml` returns `engine.run_subtask`.
   - `pygents` returns a wrapper that calls `new_engine.run_subtask(from_loader(workflow), store, **kw)`.
   - The wrapper calls `pytest.skip` when a `start_phase` kwarg is passed, because resume is yaml-only.
   - It also skips when a `should_stop` kwarg is passed, with a reason that points at plan Task 4.2. The stop bridge is not built yet, and every card must leave the suite green.
2. **Behavioural tests parametrised** (engine tier). Every test that walks a workflow through `engine.run_subtask` takes the fixture instead. That includes phase ordering, result binding, declared args, journalling, gates (pass, warn, fail, invalid verdict), `when`/`skip_to`, best-effort, agent-phase dispatch, missing runner, an unresolvable input, document paths, the `extra_context` binding and reserved-key refusal, escalation on a raising step or runner, the blocked coder and reviewer, the injected clock and the default clock.

   These tests stay `engine.*`-only:
   - `subtask_context` and alias tests that do not call `run_subtask`
   - `bind_arguments`
   - `_bind_result`
   - `run_one_step`
   - `start_phase` and resume tests
   - `SubtaskSummary`-only tests
   - loader tests
3. **`test_new_engine_returns_same_summary_for_the_shipped_task`** (engine tier). This test is not parametrised. It walks `load_builtin("task", <fake registry>)` once on each engine against separate temp stores. It then asserts that both engines give the same `status`, the same `results` keys, the same `skipped`, and the same `warnings`, and that the phase rows in the two stores are identical.
4. **Registry reuse** (engine tier). This check is covered implicitly: two back-to-back pygents runs with the same run id and card id both succeed. The parametrised suite relies on this, so no separate test is needed unless the parametrised suite fails to exercise it.

Verification: `uv run pytest`. There is no separate typecheck or lint step.

---

# `runtime.run_subtask` on both engines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a synchronous `agent_manager.runtime.engine.run_subtask` that walks a `phases.Workflow` on one pygents `Agent`, and run every behavioural test in `tests/test_engine.py` against both the yaml engine and the pygents engine.

**Architecture:** `run_subtask` makes one `asyncio.run` call into `_drive`. `_drive` builds the binding the way the old engine does, compiles the workflow into its two pygents tools, builds one `Agent`, and hands off to `_run`. `_run` sets `RunDeps` in `state.current_run`, consumes `agent.run()` to the end, and maps what comes out to a `SubtaskSummary` through the old engine's own `_escalate` and `_record_subtask_status`. Two parity gaps in already-merged code must be closed so the pygents half of the parametrised suite goes green, and the tests show both: `engine._document_paths` only recognises loader `AgentPhase`s, and `context.binding_table` lets a phase named like a reserved key (such as `worktree`) overwrite the engine's value.

**Tech Stack:** Python 3.12, pygents 0.7.0 (installed; `unregister` deliberately unused), pytest (asyncio auto mode for `async def` tests; every test in this plan is sync), `uv`.

**Spec:** `docs/superpowers/specs/task-add-runtime-run-subtask-2853e536-design.md` (prepended above). Background: `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 3.4 (lines 1003-1116), `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` §8, §9, §11.

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-add-runtime-run-subtask-2853e536` on branch `m6/task-add-runtime-run-subtask-2853e536`. That branch was cut from `m6/task-compile-a-workflow-into-023d918e`. Do not assume any other subtask's code is present.

## Global Constraints

- Only `src/agent_manager/runtime/` imports `pygents`. `tests/runtime/test_context.py::test_only_runtime_imports_pygents` enforces this, and `tests/` may import it.
- The whole default suite stays green after every task, including `tests/e2e`: `uv run pytest`.
- Never `break` or `return` out of `agent.run()`. Always write `async for _ in agent.run(): pass`. No `AFTER_TURN` checkpoint, and no pygents hook registered as a closure.
- A fake runner in a test knows only what its arguments tell it.
- `SubtaskSummary`, journal lines, phase and attempt rows, and escalation payloads stay unchanged (G10).
- Registry cleanup uses `AgentRegistry._registry.pop(name, None)` in `_forget`, never `AgentRegistry.unregister` (decision A2 of `2026-09-25-pygents-070-adoption-design.md`) and never `_items`.
- `run_subtask` has no `start_phase` parameter.
- `should_stop` is accepted and stored in `RunDeps`, and nothing on the pygents path reads it (plan Task 4.2).
- Out of scope: tests for `Goto` and `feedback` (b904b9e7), checkpointing and the stop bridge (Task 4.2), `TurnTimeoutError` handling, `--engine` CLI wiring, deleting the old engine, and adopting pygents 0.7.0.
- All new and moved tests live in `tests/test_engine.py` (engine tier). None go in `tests/e2e`, and no new test file is created.

## Review Focus

1. **Back-to-back runs in one process with the same run id and card id, after a run that raised.** The next run must succeed and must not fail with "already registered". Every parametrised test reuses `run-2026-09-23-01:ed77a917`. Pinned by `test_a_run_that_raised_leaves_its_agent_name_free_for_the_next_run` (Task 1).
2. **A `BaseException` (not an `Exception`) raised by a step.** It must propagate from both engines untouched. Only the phase's `started` row is written, no subtask status is recorded, and the agent name is still freed. Pinned by `test_a_base_exception_propagates_and_records_no_outcome` (Task 1).
3. **An escalation after some phases already finished.** The summary must still carry the earlier phases' results and warnings, because the failure path collects too. Pinned by `test_an_escalation_keeps_the_results_and_warnings_gathered_before_it` (Task 1).
4. **A phase named like a reserved context key (`worktree`, `spec_path`, `card_details`) on the pygents engine.** Its pooled result must not replace the engine's value in any later phase's binding table. Pinned by the three existing reserved-name tests, which move onto the fixture (Task 3).
5. **An error that escapes every phase's own handling (neither `Escalated` nor `EngineError`).** The subtask must be recorded escalated at the phase that was running, with `"{Type}: {message}"` as detail. The error must not propagate, and the failed phase must not be `?`. Pinned by `test_an_error_no_phase_handles_escalates_at_the_phase_that_was_running` (Task 5).

---

## File Structure

- **Create** `src/agent_manager/runtime/engine.py`. This is the pygents subtask engine: `run_subtask` (sync facade), `_drive` (binding, agent construction), `_run` (drive plus outcome mapping), `_collect` (pool to summary), and `_forget` (registry cleanup).
- **Modify** `src/agent_manager/engine.py:125-165`. `_document_paths` and `_writing_phase` must accept a declared `phases.AgentPhase` as well as a loader `AgentPhase` (Task 2).
- **Modify** `src/agent_manager/runtime/context.py:67-77`. `binding_table` skips pooled results whose id is a reserved context key, which is the pygents twin of `engine._bind_result` (Task 3).
- **Modify** `src/agent_manager/runtime/state.py:18-28`. Add `RunDeps.running` (Task 5).
- **Modify** `src/agent_manager/runtime/compile.py:105-107, 137-139`. `agent_phase` and `step_phase` set `deps.running = phase` on entry (Task 5).
- **Modify** `tests/test_engine.py`. Add the fixture, move the behavioural tests onto it, and add four new tests plus the parity test.

---

### Task 1: `run_subtask` on the pygents engine, the `run_subtask` fixture, and the behavioural tests that need no other change

**Files:**
- Create: `src/agent_manager/runtime/engine.py`
- Modify: `tests/test_engine.py` (imports at lines 18-24, fixture after the `store` fixture at line 237, test signatures and calls as listed)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes (all already on this branch):
  - `agent_manager.runtime.compile.compile_workflow(wf) -> Compiled` with `.agent_phase`, `.step_phase` and `.first_turn()`, plus `class Escalated(Exception)` with `.phase: str` and `.detail: str | None`
  - `agent_manager.runtime.state.RunDeps(workflow, store, story_id, subtask, agent_runner, clock, should_stop=None, warnings=[], skipped=[])` and `current_run: ContextVar[RunDeps]`
  - `agent_manager.runtime.context.seed_item(binding) -> ContextItem`, `decode(value)`, `SUBTASK = "subtask"` and `SKIPPED = "skipped"`
  - `agent_manager.engine`: `subtask_context`, `_document_paths`, `RESERVED_CONTEXT_KEYS`, `EngineError`, `SubtaskSummary`, `_escalate(summary, store, story_id, subtask, phase_name, detail) -> SubtaskSummary`, `_record_subtask_status(store, story_id, subtask, status)` and `_utcnow`
  - `agent_manager.workflow.phases.from_loader(loaded) -> phases.Workflow`
- Produces: `agent_manager.runtime.engine.run_subtask(workflow: phases.Workflow, store, *, story_id: str, subtask, repo_dir: Path, commands: Sequence[str] = (), card=None, parent_story=None, extra_context: Mapping[str, Any] | None = None, agent_runner=None, clock: Callable = old._utcnow, should_stop: Callable[[], bool] | None = None) -> old.SubtaskSummary`, and the `run_subtask` pytest fixture in `tests/test_engine.py` that later tasks' tests take.

- [ ] **Step 1: Add the imports and the fixture**

In `tests/test_engine.py`, change the import block (lines 18-24) to:

```python
from agent_manager import dispatch, engine, models, store as store_module
from agent_manager.errors import AgentPhaseFailed
from agent_manager.harness.base import Outcome
from agent_manager.runtime import engine as new_engine
from agent_manager.steps import integrate, reducers
from agent_manager.workflow import phases as phase_model
from agent_manager.workflow.loader import AgentPhase, load_builtin, load_workflow
from agent_manager.workflow.registry import BUILTIN_FUNCTION_NAMES, FunctionRegistry
```

Directly after the `store` fixture (which ends at line 236 with `opened.close()`), add:

```python
@pytest.fixture(params=["yaml", "pygents"])
def run_subtask(request):
    """`run_subtask` on each engine (pygents-engine design G7, §9).

    `yaml` is the old walk as is. `pygents` converts the loaded document with
    `phases.from_loader` and runs it on `runtime.engine`. Two kwargs have no
    pygents counterpart yet, and a test that passes one is skipped on that
    engine rather than run against something that does not exist.
    """
    if request.param == "yaml":
        return engine.run_subtask

    def run(workflow, store, **kw):
        if "start_phase" in kw:
            pytest.skip("start_phase is the yaml engine's resume")
        if "should_stop" in kw:
            pytest.skip(
                "the pygents stop bridge is plan Task 4.2 (runtime/checkpoint.py)"
            )
        return new_engine.run_subtask(phase_model.from_loader(workflow), store, **kw)

    return run
```

- [ ] **Step 2: Move this task's behavioural tests onto the fixture**

Make the same two-part change to each test listed below. Add `run_subtask` as the last parameter of the test function, and replace every `engine.run_subtask(` call inside that test with `run_subtask(`. Leave the arguments and assertions exactly as they are. Here is the first one in full as the pattern:

```python
def test_phases_run_in_document_order(store, run_subtask):
    calls: list[str] = []

    def make(name: str):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            return {"phase": name}

        return step

    workflow = _workflow(
        THREE_PHASES,
        {"step.alpha": make("alpha"), "step.beta": make("beta"), "step.gamma": make("gamma")},
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["alpha", "beta", "gamma"]
    assert summary.status == "done"
    assert summary.results == {
        "alpha": {"phase": "alpha"},
        "beta": {"phase": "beta"},
        "gamma": {"phase": "gamma"},
    }
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done"), ("gamma", "done")]
```

Tests to move in this task (current line numbers):
- `test_phases_run_in_document_order` (282)
- `test_a_phase_result_is_bound_into_a_later_phase` (311)
- `test_declared_args_reach_the_step_as_a_keyword` (335)
- `test_a_step_is_called_with_only_the_parameters_it_declares` (359)
- `test_every_state_edge_is_journalled_before_the_row_is_written` (387)
- `test_no_attempt_row_is_written_for_a_deterministic_phase` (418)
- `test_a_raising_step_escalates_and_the_exception_does_not_propagate` (484)
- `test_a_binding_failure_escalates_without_calling_the_step` (521)
- `test_a_non_mapping_step_result_escalates` (547). Its signature becomes `(store, returned, run_subtask)`.
- `test_a_failed_phase_result_is_not_offered_to_later_phases` (570)
- `test_a_passing_gate_lets_the_walk_continue` (603)
- `test_a_warning_gate_continues_and_surfaces_the_warning` (630)
- `test_a_failing_gate_escalates_and_stops_the_walk` (655)
- `test_a_warning_before_a_failing_gate_survives_into_the_summary` (686)
- `test_a_gate_returning_neither_none_nor_a_mapping_escalates` (729). Its signature becomes `(store, verdict, run_subtask)`.
- `test_a_gate_binds_the_phase_result_under_the_phase_name_too` (751)
- `test_a_truthy_when_jumps_to_skip_to` (819)
- `test_a_falsy_when_continues_to_the_next_phase` (838)
- `test_a_raising_when_fails_its_phase_rather_than_not_skipping` (855)
- `test_a_best_effort_failure_warns_and_does_not_sink_the_subtask` (894)
- `test_a_best_effort_phase_with_a_failing_gate_only_warns` (940)
- `test_a_best_effort_binding_failure_only_warns` (973)
- `test_a_failed_best_effort_phase_contributes_no_result` (1004)
- `test_an_agent_phase_goes_to_the_injected_runner` (1047)
- `test_an_agent_phase_with_no_runner_is_a_named_engine_error` (1076)
- `test_a_raising_step_writes_why_it_failed_into_the_journal` (1264)
- `test_a_failing_gate_writes_its_verdict_into_the_journal` (1294)
- `test_a_best_effort_failure_is_journalled_with_its_reason_too` (1319)
- `test_a_phase_that_succeeds_journals_no_failure_detail` (1354)
- `test_a_gate_on_a_phase_named_like_a_context_key_still_sees_the_real_value` (1377)
- `test_a_phase_is_timed_with_the_injected_clock` (1432)
- `test_the_default_clock_stamps_an_aware_utc_time` (1484)
- `test_a_document_with_no_path_inputs_needs_no_card` (1598)
- `test_an_unresolvable_input_raises_out_of_the_walk_before_the_runner` (1790)
- `test_a_failed_agent_phase_escalates_the_subtask_and_stops` (1918)
- `test_an_unexpected_error_from_the_agent_runner_escalates_rather_than_crashing` (1959)
- `test_a_successful_agent_phase_still_advances_the_walk` (1985)
- `test_extra_context_reaches_a_deterministic_phase_binding` (2005). Its signature becomes `(tmp_path: Path, run_subtask)`.
- `test_extra_context_may_not_redefine_a_reserved_key` (2044). Its signature becomes `(tmp_path: Path, run_subtask)`.
- `test_extra_context_may_not_redefine_the_base_branch_alias` (2076). Its signature becomes `(tmp_path: Path, run_subtask)`.
- These stop tests pass `should_stop`, so they run on yaml and skip on pygents through the fixture: `test_a_stop_requested_during_phase_three_stops_before_phase_four` (2358), `test_a_stop_already_requested_runs_no_phase_at_all` (2405), `test_a_stop_before_a_deterministic_phase_leaves_it_unstarted` (2436), `test_a_stop_before_an_agent_phase_leaves_the_runner_uncalled` (2473), `test_a_stop_before_an_agent_phase_wins_over_a_missing_runner` (2504), `test_a_should_stop_that_never_fires_changes_nothing` (2533), `test_should_stop_is_checked_only_at_visited_phases` (2585), `test_an_escalation_during_the_stop_request_wins_over_the_stop` (2613), `test_an_exception_from_should_stop_propagates_and_records_nothing` (2651).

Leave these on `engine.run_subtask` (they are resume tests): `test_starting_at_a_named_phase_runs_only_from_there` (1091), `test_an_unknown_starting_phase_is_an_error_before_anything_is_recorded` (1120), `test_a_resume_started_at_explore_never_re_runs_the_worktree_phase` (1694), and `test_a_stopped_subtask_can_be_driven_again_to_done` (2683). Tasks 2-4 move the document-path, reserved-name and shipped-`task` tests.

- [ ] **Step 3: Add the three Review Focus tests for this task**

Add these directly after `test_a_successful_agent_phase_still_advances_the_walk` (after line 2002):

```python
def test_an_escalation_keeps_the_results_and_warnings_gathered_before_it(
    store, run_subtask
):
    """Review Focus 3: the failure path collects too. A phase that finished,
    and a gate warning it raised, must survive into an escalated summary."""

    def alpha(card: str) -> dict[str, Any]:
        return {"phase": "alpha"}

    def beta(card: str) -> dict[str, Any]:
        raise OSError("disk went away")

    def alpha_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"warn": "looked thin"}

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "beta"
    assert summary.results == {"alpha": {"phase": "alpha"}}
    assert summary.warnings == ["phase 'alpha' gate 'alpha_gate' warned: looked thin"]


def test_a_run_that_raised_leaves_its_agent_name_free_for_the_next_run(
    store, run_subtask
):
    """Review Focus 1: pygents' AgentRegistry is process-wide and refuses a
    second agent under a name it holds. Both runs use the same run id and
    card id, so the second can only start if the first freed the name, even
    though it ended by raising."""
    workflow = _workflow(MIXED, {"step.work": lambda card, explore: {}})

    with pytest.raises(engine.EngineError):
        run_subtask(
            workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
        )

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=lambda phase, context, rendered: {"summary": "explored"},
    )

    assert summary.status == "done"


class _Abort(BaseException):
    """Not an `Exception`: neither engine may catch it."""


def test_a_base_exception_propagates_and_records_no_outcome(store, run_subtask):
    """Review Focus 2 and the last row of the spec's error table: a
    BaseException propagates, nothing past the phase's own `started` row is
    written, and the agent name is still freed for the next run."""

    def alpha(card: str) -> dict[str, Any]:
        raise _Abort("operator pulled the plug")

    aborting = _workflow(
        THREE_PHASES,
        {"step.alpha": alpha, "step.beta": lambda card: {}, "step.gamma": lambda card: {}},
    )

    with pytest.raises(_Abort):
        run_subtask(
            aborting, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
        )

    assert _journalled_phases(store) == [("alpha", "started")]
    assert _projected_subtask_status(store) is None

    finishing = _workflow(
        THREE_PHASES,
        {"step.alpha": lambda card: {}, "step.beta": lambda card: {}, "step.gamma": lambda card: {}},
    )
    summary = run_subtask(
        finishing, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )
    assert summary.status == "done"
```

(`_projected_subtask_status` is defined further down the module at line 2338. Module-level names resolve at call time, so the order does not matter.)

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -x -q`
Expected: collection ERROR `ModuleNotFoundError: No module named 'agent_manager.runtime.engine'`

- [ ] **Step 5: Write the implementation**

Create `src/agent_manager/runtime/engine.py`:

```python
"""The pygents subtask engine (pygents-engine design §4): one subtask, one agent, one loop.

`run_subtask` is the old engine's `run_subtask` with the walk replaced. The
binding table, the escalation and the final subtask row are the old engine's
own helpers, called exactly as it calls them, so a summary, a journal line or
a phase row cannot tell the two engines apart (G10). What differs is the walk:
the workflow is compiled into two pygents tools, one `Agent` runs them, and
`agent.run()` is always consumed to the end -- never broken or returned out of.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pygents import Agent, AgentRegistry, ContextPool, ContextQueue

from agent_manager import engine as old
from agent_manager.runtime import compile as C
from agent_manager.runtime import context
from agent_manager.runtime.state import RunDeps, current_run
from agent_manager.workflow.phases import Workflow


def run_subtask(
    workflow: Workflow,
    store: Any,
    *,
    story_id: str,
    subtask: Any,
    repo_dir: Path,
    commands: Sequence[str] = (),
    card: Any = None,
    parent_story: Any = None,
    extra_context: Mapping[str, Any] | None = None,
    agent_runner: Any = None,
    clock: Callable[[], Any] = old._utcnow,
    should_stop: Callable[[], bool] | None = None,
) -> old.SubtaskSummary:
    """Walk `workflow`'s phases for one subtask on pygents. One `asyncio.run`.

    No `start_phase`: resume belongs to the yaml engine. `should_stop` is kept
    in `RunDeps` and not yet read -- the stop bridge is plan Task 4.2.
    """
    return asyncio.run(
        _drive(
            workflow,
            store,
            story_id=story_id,
            subtask=subtask,
            repo_dir=repo_dir,
            commands=commands,
            card=card,
            parent_story=parent_story,
            extra_context=extra_context,
            agent_runner=agent_runner,
            clock=clock,
            should_stop=should_stop,
        )
    )


async def _drive(
    workflow: Workflow,
    store: Any,
    *,
    story_id: str,
    subtask: Any,
    repo_dir: Path,
    commands: Sequence[str],
    card: Any,
    parent_story: Any,
    extra_context: Mapping[str, Any] | None,
    agent_runner: Any,
    clock: Callable[[], Any],
    should_stop: Callable[[], bool] | None,
) -> old.SubtaskSummary:
    # The binding, built and refused exactly as the old engine builds it:
    # before any agent exists, so a refusal records nothing.
    binding = old.subtask_context(
        subtask, repo_dir, commands, card=card, parent_story=parent_story
    )
    if extra_context:
        reserved = sorted(set(extra_context) & set(old.RESERVED_CONTEXT_KEYS))
        if reserved:
            raise old.EngineError(
                "extra_context supplies "
                f"{', '.join(repr(key) for key in reserved)}, which the engine owns "
                f"(reserved: {', '.join(old.RESERVED_CONTEXT_KEYS)})"
            )
        binding.update(extra_context)
    binding.update(old._document_paths(workflow, card))

    compiled = C.compile_workflow(workflow)
    agent = Agent(
        f"{getattr(store, 'run_id', 'run')}:{subtask.card_id}",
        workflow.name,
        [compiled.agent_phase, compiled.step_phase],
        context_pool=ContextPool(),
        context_queue=ContextQueue(limit=10),
        tags=["subtask"],
    )
    try:
        await agent.context_pool.add(context.seed_item(binding))
        await agent.put(compiled.first_turn())
        deps = RunDeps(workflow, store, story_id, subtask, agent_runner, clock, should_stop)
        return await _run(agent, deps)
    finally:
        _forget(agent.name)


async def _run(agent: Agent, deps: RunDeps) -> old.SubtaskSummary:
    summary = old.SubtaskSummary()
    token = current_run.set(deps)
    try:
        async for _ in agent.run():  # consumed to the end, always
            pass
    except C.Escalated as esc:
        _collect(agent, deps, summary)
        return old._escalate(
            summary, deps.store, deps.story_id, deps.subtask, esc.phase, esc.detail
        )
    finally:
        current_run.reset(token)
    _collect(agent, deps, summary)
    old._record_subtask_status(deps.store, deps.story_id, deps.subtask, summary.status)
    return summary


def _collect(agent: Agent, deps: RunDeps, summary: old.SubtaskSummary) -> None:
    summary.results = {
        item.id: context.decode(item.content)
        for item in agent.context_pool.items
        if item.id not in (context.SUBTASK, context.SKIPPED)
    }
    summary.skipped = list(deps.skipped)
    summary.warnings = list(deps.warnings)


def _forget(name: str) -> None:
    """Free `name` in pygents' process-wide `AgentRegistry` for the next run.

    The installed pygents 0.7.0 has `AgentRegistry.unregister`, but switching
    to it is decision A2 of
    docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md, a card of
    its own. Until then this is the pre-0.7.0 workaround the milestone spec
    (§11) describes: pop the registry's dict, tolerating a name already gone.
    """
    AgentRegistry._registry.pop(name, None)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -q`
Expected: PASS for every `[yaml]` and `[pygents]` case moved in this task and for every test not yet moved. The stop tests show as `[pygents] SKIPPED` with the Task 4.2 reason. If a `[pygents]` case outside Tasks 2-4's lists fails, that is a real divergence. Debug it with superpowers:systematic-debugging, and do not weaken the assertion.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS (including `tests/e2e` and `tests/runtime`)

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/runtime/engine.py tests/test_engine.py
git commit -m "feat(runtime): run a subtask on pygents; engine tests run on both engines"
```

---

### Task 2: Document paths from a declared workflow

`engine._document_paths` and `engine._writing_phase` test `isinstance(phase, AgentPhase)` against the loader's `AgentPhase` (`src/agent_manager/engine.py:136`, `:158`). A `phases.Workflow` holds `phases.AgentPhase`, so on the pygents engine no `spec_path` or `plan_path` is ever bound. The moved tests show this, which makes it the change to `engine.py` the spec allows.

**Files:**
- Modify: `src/agent_manager/engine.py:125-165`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: the `run_subtask` fixture from Task 1.
- Produces: `engine._document_paths(workflow: Workflow | phase_model.Workflow, card) -> dict[str, str]`, same return as before and now also correct for a declared workflow.

- [ ] **Step 1: Move the document-path tests onto the fixture**

Apply Task 1's two-part change (add a `run_subtask` parameter, then `engine.run_subtask(` becomes `run_subtask(`) to:
- `test_document_paths_are_bound_from_the_writes_templates` (1532)
- `test_a_document_path_input_with_no_writing_phase_is_a_named_error` (1554)
- `test_a_document_path_input_with_no_card_is_a_named_error` (1581)

Keep `pytest.raises(engine.EngineError)` as it is: `engine.EngineError` is the class both engines raise.

- [ ] **Step 2: Run the tests to verify the pygents half fails**

Run: `uv run pytest tests/test_engine.py -q -k "document_path"`
Expected: the three `[yaml]` cases PASS and the three `[pygents]` cases FAIL:
- `test_document_paths_are_bound_from_the_writes_templates[pygents]` fails with `KeyError: 'spec_path'`, because the summary escalates and `seen` is empty.
- The two error tests fail with `Failed: DID NOT RAISE` or with a different `EngineError` parameter or message.

- [ ] **Step 3: Accept either agent-phase type**

In `src/agent_manager/engine.py`, replace lines 125-165 (`_document_paths` and `_writing_phase`) with:

```python
_AGENT_PHASES = (AgentPhase, phase_model.AgentPhase)
"""Both agent-phase types: the YAML loader's and the declared phase model's.
The pygents engine hands `_document_paths` a `phases.Workflow`, and a loader-only
check would find no agent phase in it and bind no document path at all."""


def _document_paths(
    workflow: Workflow | phase_model.Workflow, card: models.Card | None
) -> dict[str, str]:
    """`spec_path` / `plan_path` for the whole subtask, computed once, from the document.

    Computed at subtask start rather than when the `spec` and `plan` phases run:
    `plan_check` may `skip_to: implement`, and `implement` still declares both
    inputs. §7 calls them "paths in the repo, already committed" -- the path is a
    property of the card and the document, not of a phase having executed.
    """
    declared = {
        name
        for phase in workflow.phases
        if isinstance(phase, _AGENT_PHASES)
        for name in phase.inputs
        if name in _DOCUMENT_INPUTS
    }
    paths: dict[str, str] = {}
    for name in sorted(declared):
        source = _writing_phase(workflow, _DOCUMENT_INPUTS[name], name)
        if card is None:
            raise EngineError(
                f"is declared as an input, but no card was supplied to expand "
                f"{source.writes!r} (the stem comes from the card's id and title)",
                phase=source.name,
                parameter=name,
            )
        paths[name] = prompt.expand_writes(
            source.writes, card, phase=source.name, input_name=name
        )
    return paths


def _writing_phase(
    workflow: Workflow | phase_model.Workflow, phase_name: str, input_name: str
) -> AgentPhase | phase_model.AgentPhase:
    found = next((p for p in workflow.phases if p.name == phase_name), None)
    if not isinstance(found, _AGENT_PHASES) or found.writes is None:
        raise EngineError(
            f"is declared as an input, but this workflow has no agent phase named "
            f"{phase_name!r} with a `writes:` template to take the path from "
            f"(phases: {', '.join(workflow.phase_names)})",
            parameter=input_name,
        )
    return found
```

(`phase_model` is already imported at `src/agent_manager/engine.py:29` as `from agent_manager.workflow import phases as phase_model`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -q -k "document_path"`
Expected: PASS, all six cases.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "fix(engine): bind document paths for a declared workflow too"
```

---

### Task 3: A phase named like a reserved key never clobbers it on pygents

`context.binding_table` copies every pooled result into the table under its id (`src/agent_manager/runtime/context.py:69-71`). The shipped `task` workflow has a phase named `worktree`, so on pygents its result `{"created": True}` replaces the worktree path every later phase binds. The old engine prevents this in `_bind_result` using `RESERVED_CONTEXT_KEYS`. The moved tests show the gap, which makes it the change to `context.py` the spec allows.

**Files:**
- Modify: `src/agent_manager/runtime/context.py:10-19` (imports), `:67-77` (`binding_table`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: the `run_subtask` fixture from Task 1, and `engine.RESERVED_CONTEXT_KEYS`.
- Produces: `context.binding_table(pool, memory, phase) -> dict[str, Any]` with the same signature. A pooled item whose id is in `RESERVED_CONTEXT_KEYS` stays in the pool (so `summary.results` still has it) but never enters the table.

- [ ] **Step 1: Move the reserved-name tests onto the fixture**

Apply Task 1's two-part change to:
- `test_a_phase_named_like_a_context_key_runs_but_never_clobbers_it` (439)
- `test_a_phase_named_spec_path_never_clobbers_the_document_path` (1826)
- `test_a_phase_named_card_details_never_clobbers_the_cards_the_prompt_renders` (1889)

- [ ] **Step 2: Run the tests to verify the pygents half fails**

Run: `uv run pytest tests/test_engine.py -q -k "never_clobbers"`
Expected: the `[yaml]` cases PASS and the `[pygents]` cases FAIL. For example, `test_a_phase_named_like_a_context_key_runs_but_never_clobbers_it[pygents]` fails with `assert {'created': True, ...} == PosixPath('/repo/.claude/worktrees/m1/task-ed77a917')`.

- [ ] **Step 3: Skip reserved ids when building the table**

In `src/agent_manager/runtime/context.py`, add this import after `from pygents import ContextItem, ContextPool, ContextQueue` (line 19):

```python
from agent_manager.engine import RESERVED_CONTEXT_KEYS
```

Replace `binding_table` (lines 67-77) with:

```python
def binding_table(pool: ContextPool, memory: ContextQueue, phase: str) -> dict[str, Any]:
    """The seed, every phase result, and this phase's feedback, as one table.

    A result pooled under a reserved key -- the shipped `task` workflow's
    `worktree` phase is one -- stays in the pool, so the summary still reports
    it, but never replaces the engine's own value in the table: the pygents
    twin of `engine._bind_result`.
    """
    table: dict[str, Any] = dict(decode(pool.get(SUBTASK).content))
    for item in pool.items:
        if item.id in (SUBTASK, SKIPPED) or item.id in RESERVED_CONTEXT_KEYS:
            continue
        table[item.id] = decode(item.content)
    table["feedback"] = [
        dict(i.content)
        for i in memory.items
        if isinstance(i.content, Mapping) and i.content.get("for") == phase
    ]
    return table
```

(`agent_manager.engine` does not import `agent_manager.runtime`, so this adds no import cycle. `runtime/compile.py` already imports `agent_manager.engine`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py tests/runtime -q -k "never_clobbers or binding_table or compile"`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/runtime/context.py tests/test_engine.py
git commit -m "fix(runtime): a phase named like a reserved key never clobbers its binding"
```

---

### Task 4: The shipped `task` workflow on both engines, plus the parity test

These tests drive `builtin/task.yaml` end to end, and they depend on both of the fixes above. After Tasks 1-3 they are expected to pass on both engines straight away, because they pin parity rather than drive new code. A failure here is a real divergence. Debug it with superpowers:systematic-debugging, and do not weaken the assertion.

**Files:**
- Modify: `tests/test_engine.py` (the tests at lines 1146, 1678-1788, 2163 and 2234, plus a new test appended at the end of the file)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: the `run_subtask` fixture (Task 1); `new_engine.run_subtask` (Task 1); the module helpers `_builtin_functions(calls, *, validated)`, `_recording_runner(recorded)`, `_registry(functions)` and `_subtask()`; and the constants `STORY_ID`, `REPO`, `CARD`, `PARENT` and `FIXED`.
- Produces: `_walk_builtin(run_subtask, store, recorded, *, validated)`, which now takes the engine as its first argument.

- [ ] **Step 1: Move the shipped-workflow tests onto the fixture**

Apply Task 1's two-part change to:
- `test_the_builtin_task_document_walks_against_a_fake_registry` (1146)
- `test_a_blocked_coder_escalates_the_subtask_at_implement_and_review_never_runs` (2163)
- `test_a_reviewer_reporting_a_blocker_escalates_at_review_and_verify_never_runs` (2234)

Replace `_walk_builtin` (lines 1678-1691) with:

```python
def _walk_builtin(run_subtask, store, recorded: dict[str, Any], *, validated: bool) -> Any:
    calls: list[str] = []
    workflow = load_builtin("task", _registry(_builtin_functions(calls, validated=validated)))
    return run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        commands=["uv run pytest"],
        card=CARD,
        parent_story=PARENT,
        agent_runner=_recording_runner(recorded),
    )
```

In each of its four callers, add `run_subtask` as the last test parameter and pass it first:
- `test_every_document_path_input_renders_the_expanded_writes_template(store, run_subtask)` calls `_walk_builtin(run_subtask, store, recorded, validated=False)`
- `test_implement_gets_both_paths_even_when_plan_check_skipped_spec_and_plan(store, run_subtask)` calls `summary = _walk_builtin(run_subtask, store, recorded, validated=True)`
- `test_each_agent_phase_receives_exactly_the_inputs_it_declares(store, run_subtask)` calls `_walk_builtin(run_subtask, store, recorded, validated=False)`
- `test_the_explore_prompt_reads_the_cards_not_the_reserved_card_key(store, run_subtask)` calls `_walk_builtin(run_subtask, store, recorded, validated=False)`

- [ ] **Step 2: Append the explicit parity test at the end of `tests/test_engine.py`**

```python
# ── parity: the shipped `task` workflow on both engines (spec Tests 3) ──────


def test_new_engine_returns_same_summary_for_the_shipped_task(monkeypatch, tmp_path):
    """One explicit parity check, deliberately not parametrised (spec Tests 3):
    `builtin/task.yaml`, walked once on each engine against separate temp
    stores, ends in the same place -- status, result keys, skips, warnings,
    the steps called, and every phase row. The clock is fixed so the rows'
    timestamps compare too. Both plan-check outcomes are walked inside the
    one test: `validated=True` takes the `plan_check` skip, so `skipped` is
    compared non-empty."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    engines = {
        "yaml": engine.run_subtask,
        "pygents": lambda workflow, opened, **kw: new_engine.run_subtask(
            phase_model.from_loader(workflow), opened, **kw
        ),
    }
    for validated in (False, True):
        walked: dict[str, Any] = {}
        for name, run in engines.items():
            calls: list[str] = []
            workflow = load_builtin(
                "task", _registry(_builtin_functions(calls, validated=validated))
            )
            label = f"{name}-{'validated' if validated else 'fresh'}"
            opened = store_module.Store.open(tmp_path / label, f"run-parity-{label}")
            try:
                summary = run(
                    workflow,
                    opened,
                    story_id=STORY_ID,
                    subtask=_subtask(),
                    repo_dir=REPO,
                    commands=["uv run pytest"],
                    card=CARD,
                    parent_story=PARENT,
                    agent_runner=_recording_runner({}),
                    clock=lambda: FIXED,
                )
                rows = [
                    tuple(row)
                    for row in opened.connection.execute(
                        "SELECT name, kind, status, started_at, ended_at FROM phases"
                        " ORDER BY position"
                    ).fetchall()
                ]
            finally:
                opened.close()
            walked[name] = (summary, calls, rows)

        old_summary, old_calls, old_rows = walked["yaml"]
        new_summary, new_calls, new_rows = walked["pygents"]
        assert old_summary.status == new_summary.status == "done", validated
        assert sorted(new_summary.results) == sorted(old_summary.results), validated
        assert new_summary.skipped == old_summary.skipped, validated
        assert new_summary.warnings == old_summary.warnings, validated
        assert new_calls == old_calls, validated
        assert new_rows == old_rows, validated
        assert new_rows, "the walk recorded no phase rows to compare"
    assert new_summary.skipped, "the validated walk must exercise a non-empty skip"
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_engine.py -q -k "builtin or blocked or blocker or document_path_input_renders or both_paths or exactly_the_inputs or explore_prompt or same_summary"`
Expected: PASS on `[yaml]` and `[pygents]` for every moved test, and PASS for the single, unparametrised parity test.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_engine.py
git commit -m "test(engine): walk the shipped task workflow on both engines and pin their parity"
```

---

### Task 5: An error no phase handles escalates at the phase that was running

The spec's error table has three `Exception` rows. `Escalated` became an escalation in Task 1. `EngineError` must be re-raised unchanged. Any other `Exception` must be escalated at the running phase. The spec names `agent.to_dict()["current_turn"]` as the first way to find the running phase and `RunDeps.running` as the fallback, "keep whichever the test proves works and delete the other". The steps below try the first way and let the test decide.

**Files:**
- Modify: `src/agent_manager/runtime/engine.py` (`_run`)
- Modify: `src/agent_manager/runtime/state.py:18-28` (`RunDeps`), used only if Step 5 shows the fallback is needed
- Modify: `src/agent_manager/runtime/compile.py:105-107`, `:137-139`, used only if Step 5 shows the fallback is needed
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `new_engine.run_subtask` (Task 1), `agent_manager.runtime.bridge.call_step(fn, kwargs)` (async, looked up as a module attribute by `compile.step_phase` at call time), and `engine._render_error(error) -> str`.
- Produces: `RunDeps.running: str | None = None`, the name of the phase whose tool was entered last, which `agent_phase` and `step_phase` set on entry. This applies only if the fallback is kept.

- [ ] **Step 1: Write the failing test**

Add `from agent_manager.runtime import bridge` to the imports of `tests/test_engine.py`, after `from agent_manager.harness.base import Outcome`. Then append at the end of the file:

```python
# ── pygents only: the spec's error table, third row ─────────────────────────


def test_an_error_no_phase_handles_escalates_at_the_phase_that_was_running(
    store, monkeypatch
):
    """Review Focus 5. Not parametrised: the yaml engine has no bridge. An
    `Exception` that is neither `Escalated` nor `EngineError` -- here the
    bridge itself failing under `beta` -- must end the subtask escalated at
    `beta`, in the old engine's `{Type}: {message}` form, not propagate."""
    real_call_step = bridge.call_step

    async def call_step(fn, kwargs):
        if kwargs["phase"].name == "beta":
            raise RuntimeError("the worker pool is gone")
        return await real_call_step(fn, kwargs)

    monkeypatch.setattr(bridge, "call_step", call_step)
    workflow = _workflow(
        THREE_PHASES,
        {
            "step.alpha": lambda card: {"phase": "alpha"},
            "step.beta": lambda card: {},
            "step.gamma": lambda card: {},
        },
    )

    summary = new_engine.run_subtask(
        phase_model.from_loader(workflow),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "beta"
    assert summary.detail == "RuntimeError: the worker pool is gone"
    assert summary.results == {"alpha": {"phase": "alpha"}}
    assert _projected_phases(store) == [("alpha", "done")]
    assert _subtask_journal_statuses(store) == ["escalated"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_engine.py::test_an_error_no_phase_handles_escalates_at_the_phase_that_was_running -v`
Expected: FAIL with `RuntimeError: the worker pool is gone` propagating out of `run_subtask`.

- [ ] **Step 3: Add the `EngineError` and catch-all branches, reading the running phase from `to_dict()`**

In `src/agent_manager/runtime/engine.py`, replace `_run` with:

```python
async def _run(agent: Agent, deps: RunDeps) -> old.SubtaskSummary:
    summary = old.SubtaskSummary()
    token = current_run.set(deps)
    try:
        async for _ in agent.run():  # consumed to the end, always
            pass
    except C.Escalated as esc:
        _collect(agent, deps, summary)
        return old._escalate(
            summary, deps.store, deps.story_id, deps.subtask, esc.phase, esc.detail
        )
    except old.EngineError:
        # A missing runner or an unresolvable input: a wiring or document bug
        # the old engine raises to its caller, `.phase`/`.parameter` intact.
        raise
    except Exception as error:
        _collect(agent, deps, summary)
        running = agent.to_dict()["current_turn"]
        phase = running["kwargs"]["phase"] if running else "?"
        return old._escalate(
            summary, deps.store, deps.story_id, deps.subtask, phase, old._render_error(error)
        )
    finally:
        current_run.reset(token)
    _collect(agent, deps, summary)
    old._record_subtask_status(deps.store, deps.story_id, deps.subtask, summary.status)
    return summary
```

- [ ] **Step 4: Run the test to see which way works**

Run: `uv run pytest tests/test_engine.py::test_an_error_no_phase_handles_escalates_at_the_phase_that_was_running -v`
Expected: FAIL on `assert summary.failed_phase == "beta"` with `'?' == 'beta'`. `pygents.Agent.run` clears `_current_turn` in its own `finally` (`.venv/lib/python3.12/site-packages/pygents/agent.py:536-538`), so `current_turn` is already `None` once the error leaves `run()`. If the test PASSES instead, keep Step 3 as written, skip Steps 5-6, and go to Step 7.

- [ ] **Step 5: Use the fallback: the tools record the running phase in `RunDeps`**

In `src/agent_manager/runtime/state.py`, replace the `RunDeps` dataclass (lines 18-28) with:

```python
@dataclass
class RunDeps:
    workflow: Any
    store: Any
    story_id: str
    subtask: Any
    agent_runner: Callable[..., Any] | None
    clock: Callable[[], Any]
    should_stop: Callable[[], bool] | None = None
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    running: str | None = None
    """The phase whose tool was entered last. pygents clears the agent's
    `current_turn` before an error leaves `run()`, so the engine reads the
    phase an unexpected error escaped from here instead."""
```

In `src/agent_manager/runtime/compile.py`, make `deps.running = phase` the line directly after `deps = current_run.get()` in both tools. In `agent_phase` (lines 105-107) that gives:

```python
    async def agent_phase(phase: str, loop: int, pool: ContextPool, memory: ContextQueue):
        deps = current_run.get()
        deps.running = phase
        p = deps.workflow.phase(phase)
```

and in `step_phase` (lines 137-139):

```python
    async def step_phase(phase: str, loop: int, pool: ContextPool):
        deps = current_run.get()
        deps.running = phase
        p = deps.workflow.phase(phase)
```

In `src/agent_manager/runtime/engine.py`, replace the catch-all branch in `_run` with one that reads `deps.running`, and delete the `to_dict()` read:

```python
    except Exception as error:
        _collect(agent, deps, summary)
        # `deps.running` is only `None` if the error came before any tool was
        # entered; there is no phase to name then.
        return old._escalate(
            summary,
            deps.store,
            deps.story_id,
            deps.subtask,
            deps.running or "?",
            old._render_error(error),
        )
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_engine.py::test_an_error_no_phase_handles_escalates_at_the_phase_that_was_running tests/runtime -v`
Expected: PASS (the `tests/runtime` compile tests still build `RunDeps` by keyword, and the new field has a default)

- [ ] **Step 7: Check that only the resume tests still call the old engine directly**

Run: `uv run pytest tests/test_engine.py -q -k "no_runner or unresolvable or raised_leaves"`
Expected: PASS on both engines. This confirms the new `EngineError` branch re-raises and the catch-all does not swallow it.

Then use Grep with pattern `engine\.run_subtask\(` on `tests/test_engine.py`. Expected: matches only inside `test_starting_at_a_named_phase_runs_only_from_there`, `test_an_unknown_starting_phase_is_an_error_before_anything_is_recorded`, `test_a_resume_started_at_explore_never_re_runs_the_worktree_phase`, `test_a_stopped_subtask_can_be_driven_again_to_done`, and the `engines` dict of `test_new_engine_returns_same_summary_for_the_shipped_task`.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 9: Commit**

If Steps 5-6 ran (the expected path):

```bash
git add src/agent_manager/runtime/engine.py src/agent_manager/runtime/state.py src/agent_manager/runtime/compile.py tests/test_engine.py
git commit -m "feat(runtime): escalate an unexpected error at the phase that was running"
```

If Step 4 passed and Steps 5-6 were skipped, only `engine.py` and `tests/test_engine.py` changed. Stage just those two files, because `git add` fails on paths that have no changes:

```bash
git add src/agent_manager/runtime/engine.py tests/test_engine.py
git commit -m "feat(runtime): escalate an unexpected error at the phase that was running"
```
