# Compile a workflow into `agent_phase` and `step_phase` (subtask 023d918e)

Task 3.3 of story f9c19dc3 "Run a workflow on pygents". This narrows plan `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 3.3 (lines 758-1001) and design `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (G2, G3, §4, §5, §9) to this card. The plan's code listing for `compile.py` is the reference implementation; this document fixes scope, behaviour and tests.

Note: the exploration summary handed to this stage was truncated at 8000 characters (it overran its brief). Anything below that was cut off was re-read from the plan, the design spec and the code, not guessed.

## Scope

In scope:

1. `src/agent_manager/harness/launcher.py`: `run_direct(argv, *, cwd, timeout, stdout_path, on_spawn=None)`. When `on_spawn` is given it is called with the `Popen` object right after `Popen(...)` returns, before anything waits on the process. The existing `_kill_tree` becomes public as `kill_tree(process)`, and `_kill_tree` stays as an alias so existing callers are unaffected. Callers that omit `on_spawn` see no change in behaviour.
2. `src/agent_manager/runtime/state.py`: the `RunDeps` dataclass (`workflow`, `store`, `story_id`, `subtask`, `agent_runner`, `clock`, `should_stop=None`, `warnings: list[str]`, `skipped: list[str]`, with list defaults via `default_factory`) and `current_run: ContextVar[RunDeps]`.
3. `src/agent_manager/runtime/bridge.py`:
   - `async call_agent(runner, phase, context, rendered)` runs `runner(phase, context, rendered)` through `asyncio.to_thread`. It keeps a per-call list of spawned processes, filled by an `on_spawn` hook held in a `threading.local` inside the worker thread. On `asyncio.CancelledError` it calls `launcher.kill_tree(p)` on every process recorded for that call, then re-raises. G2 applies here: `to_thread` cannot be cancelled, so killing the process is the bridge's job.
   - `current_spawn_hook()` returns the hook bound to the call running on the current thread, or `None` outside a call.
   - `async call_step(fn, kwargs)` runs `fn(**kwargs)` off the event loop and returns its result.
   - `last_spawned_for_tests()` is a test-only accessor for the most recently spawned process.
   - `AgentRunner` passes `on_spawn=bridge.current_spawn_hook()` to its launcher only when `inspect.signature(self.launcher).parameters` accepts `on_spawn`. This keeps the fake launchers in existing tests working. It is the only edit to `dispatch.py`.
4. `src/agent_manager/engine.py`: extract `run_one_step(*, phase, table, store, story_id, subtask, clock, workflow=None) -> _Outcome` from `_run_deterministic` and `_skip_target`. `_run_deterministic` then delegates to it. This must be a pure extraction: the old engine's phase rows, warnings, error text and outcomes stay byte-identical (G10). `phase` may be a loader `DeterministicPhase` or a `phases.Step`, and `run`, each gate and `when` are resolved as `entry if callable(entry) else workflow.function(entry)`, the pattern Task 3.2 uses in dispatch. The function label passed to `bind_arguments` and used in messages is the string name, or `__name__` for a callable.
   - Deviation from the plan's signature: the plan lists no `workflow` keyword. String entries on a loader phase cannot be resolved without one, and `phases.Workflow` has no `.function`. So `workflow` is an optional keyword that is only consulted for string entries. `compile.py` does not need to pass it.
5. `src/agent_manager/runtime/compile.py`:
   - `Escalated(Exception)` carries `.phase` and `.detail`. Its message is `"{phase}: {detail}"`.
   - `Compiled` is a frozen dataclass with `workflow`, `agent_phase` and `step_phase` fields and three methods:
     - `turn_for(name, loop)` returns a `Turn` whose kwargs are `{"phase", "loop"}`. Its timeout is the `AgentPhase.timeout` for agent phases and 3600s for steps.
     - `first_turn()`.
     - `after(name, loop)` returns the next phase's turn, or `None` after the last phase.
   - `compile_workflow(wf)` is cached per `(wf.name, wf.digest())` behind a module `threading.Lock`. Concurrent callers get the same `Compiled` object and never trip pygents' duplicate-name `ValueError`. `clear_cache()` is for tests.
   - The two tools are registered with `@tool` under `agent_phase_{wf.name}_{digest[:8]}` and `step_phase_{wf.name}_{digest[:8]}`. Implementation must first check whether pygents keys `ToolRegistry` on `__name__` or on `__qualname__`, and set both if needed.
   - `step_phase(phase, loop, pool)` does the following:
     - Builds the table with `context.binding_table`, then calls `bridge.call_step(engine.run_one_step, …)` with `deps` read from `current_run`.
     - Extends `deps.warnings` with the outcome's warnings.
     - On failure, a `best_effort` step appends `"best-effort phase {name!r} failed: {detail}"` to `deps.warnings` and falls through to the next phase. Any other step raises `Escalated(phase, detail)`. A gate verdict is a failure too.
     - On success, yields a `ContextItem(id=phase, content=context.encode(result))`.
     - If the step returns a `skip_to` (from `when`), extends `deps.skipped` with the phase names strictly between this phase and the target, and yields the target's turn. Otherwise it yields `after(...)`.
   - `agent_phase(phase, loop, pool, memory)` does the following:
     - Builds the table with `context.binding_table`, renders with `prompt.render_prompt`, and awaits `bridge.call_agent(deps.agent_runner, p, table, rendered)`.
     - On success, yields the result item and then the next turn.
     - On `AgentPhaseFailed`, if `on_fail` is set and `loop < on_fail.max_loops`, it yields a feedback `ContextItem` (`{"for", "from", "detail"}`) and then `turn_for(on_fail.phase, loop + 1)`. Otherwise it raises `Escalated(phase, failure.detail)`.

Out of scope:

- `runtime/engine.py`, `run_subtask`, `drive()` and parametrizing `tests/test_engine.py` belong to 2853e536.
- The `feedback` prompt resolver and the Goto loop's behavioural tests belong to b904b9e7. Only the loop mechanics above land here.
- `runtime/context.py` belongs to 8ae25085 and is consumed, not modified.
- `dispatch.evaluate_gates` and `prompt.py` belong to 1bbb532d and are not modified.
- Checkpointing, stop bridging and `should_stop` handling are not implemented here. The field exists only so later tasks can use it.

## Invariants

- Only `src/agent_manager/runtime/` imports pygents. `workflow/phases.py`, `task.py` and `integrate.py` stay pygents-free.
- Tools and hooks are module-level or registered once per compilation, never re-registered under an existing name. No pygents hook is registered as a closure (this task adds no hooks).
- Nothing here breaks or returns out of `agent.run()`. Tests consume `run()` to the end.
- The whole default suite stays green, including `tests/e2e` and the old engine's tests, which are unchanged.
- Fake runners in tests know only what their brief tells them.

## Error paths

- A step raises, returns a non-mapping, or gets a gate verdict. `run_one_step` records the phase row `failed` and returns `ok=False`. It never raises for a step's own exception, just as today. `step_phase` then either warns (`best_effort`) or raises `Escalated`.
- A gate `warn` verdict becomes a warning in `deps.warnings`, and the step continues.
- The agent runner raises `AgentPhaseFailed`. The phase loops through `Goto` if loops remain, and otherwise raises `Escalated(phase, detail)`.
- `call_agent` is cancelled. Every process spawned by that call is killed with `kill_tree` and `CancelledError` propagates. Processes spawned by other concurrent calls are untouched, because the hook is per-thread and per-call.
- `current_run` is unset when a tool runs: the `LookupError` propagates. This is a programming error, and `run_subtask` (3.4) always sets it.

## Tests

Test placement follows the project rule that tests mirror source under `tests/`. All of these run in the default suite. None needs the `e2e` marker, which pyproject reserves for the paid real-`claude` run, so none goes in `tests/e2e`. Parity parametrization over engines is 3.4's job, not this card's.

- `tests/runtime/conftest.py` (unit tier, `tests/runtime/`): an autouse fixture that clears `ToolRegistry` and `AgentRegistry` and calls `compile.clear_cache()` before and after each test.
- `tests/runtime/test_compile.py` (unit tier, mirrors `runtime/compile.py`). It drives a real pygents `Agent` with fake runners and a fake store:
  - `test_linear_flow_stores_every_result`: steps run in order, each binds the previous result, and the pool holds each result.
  - `test_skip_to_records_skipped_phases`: `when` is true, so execution jumps to `skip_to`, and the phases in between are recorded as skipped and never run.
  - `test_best_effort_failure_is_a_warning`: a `best_effort` step raises, the next phase still runs, and `deps.warnings` names the failure.
  - `test_step_gate_verdict_escalates`: a gate returns `{"blocked": …, "detail": "d"}`, which raises `Escalated` with `.phase == "a"` and `"d"` in `.detail`.
  - `test_agent_phase_failure_escalates`: the runner raises `AgentPhaseFailed` with no `on_fail`, which raises `Escalated(phase, detail)`.
  - `test_two_threads_compiling_at_once_share_one_compilation`: 8 threads compile the same workflow and get exactly one `Compiled` identity, with no `ValueError`.
- `tests/runtime/test_bridge.py` (unit tier, mirrors `runtime/bridge.py`):
  - `test_cancelling_a_call_kills_its_process`: the runner uses `launcher.run_direct` with `on_spawn=bridge.current_spawn_hook()` on a sleeping child. After the task is cancelled, `CancelledError` propagates and `last_spawned_for_tests().poll()` is not `None`.
- `tests/harness/test_launcher.py` (unit tier, existing file; additions only):
  - `on_spawn` is called once with the live `Popen` before the process exits.
  - Omitting `on_spawn` leaves behaviour unchanged.
  - `kill_tree` is exported and `_kill_tree is kill_tree`.
- Existing `tests/test_engine.py` and the rest of the suite stay unchanged and green. Together they prove that the `run_one_step` extraction is pure.
