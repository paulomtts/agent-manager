# Add StopSignal and park subtasks through ON_PAUSE (card 364babde)

Parent story: e90a2247 "Groundwork: the stop, an awaitable driver, multi-blocker roots". Plan: `docs/superpowers/plans/2026-09-25-supervisor-tree.md`, Task 1.1. Design: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`, Decision T5.

## Scope

In scope:

- New `src/agent_manager/runtime/stop.py` with `StopSignal`.
- `runtime/checkpoint.py`: a new module-level `ON_PAUSE` hook `on_pause`, tagged `subtask`, beside the existing `before_turn`.
- `runtime/engine.py`: rename `_drive` to public `run_subtask_async`, and add a `stop: StopSignal | None = None` keyword to it and to `run_subtask`. `run_subtask` stays the `asyncio.run(run_subtask_async(...))` wrapper. The agent is registered with `stop` before the run and unregistered in a `finally`.
- `runtime/state.py`: `RunDeps` gains `stop: StopSignal | None = None`.
- Tests: the new `tests/runtime/test_stop.py`, plus one test added to the existing `tests/runtime/test_stop_bridge.py`.

Out of scope. Each item belongs to a sibling card or a later task:

- `cli.py`, including `drive_subtask_async`. That is card 9b944409 (Task 1.2).
- `dag.py`, `orchestrate.py`, `pyproject.toml` and the grafo dependency. That is card 1693e86e (Task 1.3).
- Removing `should_stop`, the `BEFORE_TURN` stop branch, or `RunDeps.should_stop`. That is Task 3.3. All three stay and must keep working, so `orchestrate.py`'s thread runner is unaffected.
- Switching `_forget` to `AgentRegistry.unregister`. The pre-0.7.0 workaround stays as it is.

## Observable behavior

`StopSignal` is one per run and lives on the run's single event loop. It is a plain class with no locking.

- `triggered: bool` starts as `False`. `primary: str | None` starts as `None`.
- `trigger(story_id) -> bool`:
  - The first call sets `triggered = True` and `primary = story_id`, then returns `True`.
  - Later calls return `False` and leave `primary` unchanged.
  - Every call, first or not, calls `.pause()` on every currently registered agent. It iterates over a copy of the registered set.
- `register(agent)` adds the agent to the set. If the signal is already triggered, it calls `agent.pause()` straight away.
- `unregister(agent)` removes the agent. Calling it with an agent that is not registered is a no-op and does not raise.
- `stop.py` imports nothing from pygents. It depends only on the agent having a `.pause()` method, so any object with `.pause()` works (the tests use a `FakeAgent`).

`on_pause(agent)` is registered with `@hook(AgentHook.ON_PAUSE, tags={"subtask"})`. It is module-level, not a closure, because pygents' `HookRegistry` is keyed on the function name.

- If `current_run` is unset, it returns without doing anything.
- Otherwise it reads `head = agent.to_dict()["queue"][0]["kwargs"]["phase"]`, calls `save(agent, "parked")` and raises `Parked(head)`.
- The raise is what ends the run. On its own, pygents' `pause()` only clears the internal pause event, and `run()` would then wait forever on `_pause_event.wait()`.
- pygents fires `ON_PAUSE` at the top of its `while` loop, between turns, so a turn that is already in flight finishes first. At that point `current_turn` is `None` and the next phase is `queue[0]`.
- The loop condition is checked before the pause gate. So if a pause lands after the last phase has finished and the queue is empty, no `ON_PAUSE` fires and the subtask ends `done` as usual.

Engine:

- `run_subtask_async` has `_drive`'s signature and body, plus `stop`. `run_subtask` forwards `stop`.
- `stop` is passed into `RunDeps`. Add the field after `should_stop` so the existing positional construction still binds correctly.
- In `_run`, when `deps.stop` is set:
  - call `stop.register(agent)` immediately before `agent.run()` is consumed;
  - call `stop.unregister(agent)` in the `finally`, so it runs on every exit path (done, parked, escalated, EngineError, BaseException).
- The existing `except checkpoint.Parked` branch handles the pause path unchanged:
  - no extra checkpoint row is written, so `parked` stays the newest;
  - the summary's `status` is `"stopped"` and its `detail` is `"stopped before <phase>"`.
- `CheckpointMismatch`, the reserved `extra_context` refusal and `_forget` behave exactly as before. A mismatch is still raised before any agent exists and before any registration.

Without a `stop` argument, behavior is byte-identical to today. The `should_stop` path, with the `BEFORE_TURN` hook saving `parked` and raising `Parked`, keeps working.

## Error paths

- `trigger` or `register` with no agents registered: no error.
- `unregister` of an unknown agent: no error.
- `on_pause` with no `current_run` (an agent tagged `subtask` driven outside the engine): no-op. The agent then stays paused, which is the caller's concern.
- An exception or cancellation inside `_run` still unregisters the agent, so a later `trigger` never calls `.pause()` on an agent whose run has finished.

## Tests

Test-placement rule: this repo does not sort tests into unit and integration tiers. Tests mirror the source tree under `tests/<module>/` (CLAUDE.md). The only tier with its own name is `tests/e2e/`, which holds the slow opt-in real-harness test. These are runtime-module tests, so all of them go in `tests/runtime/`. None of them goes in `tests/e2e/`.

`tests/runtime/test_stop.py` is a new file. Its bodies are copied verbatim from the plan's Task 1.1 Step 1 and use a `FakeAgent` that counts `.pause()` calls:

1. `test_first_trigger_is_primary`: the first `trigger("A")` returns `True`, the second `trigger("B")` returns `False`, and afterwards `primary == "A"` and `triggered` is true.
2. `test_trigger_pauses_registered_agents_and_late_registrations`: an agent registered before the trigger is paused once, and an agent registered after the trigger is paused once when it registers.
3. `test_unregistered_agents_are_not_paused`: register, unregister, then trigger; the pause count is 0.

`tests/runtime/test_stop_bridge.py` gets one new test. The existing M6 `should_stop` tests stay as they are, because they cover the path that remains until Task 3.3:

4. Two subtasks, A and B, run concurrently on one loop:
   - Both use the existing fake runner/store fixtures and run `run_subtask_async(..., stop=stop)` with one shared `StopSignal`, gathered on the same loop.
   - A's fake step calls `stop.trigger("A")` while B's current phase is in flight.
   - Ordering comes from `threading.Event`s that the fake steps block on, never from sleeps. Steps run off the loop through `bridge.call_step`'s `asyncio.to_thread`, so the synchronization primitive must be thread-safe, not `asyncio.Event` (which cannot be waited on synchronously from the worker thread); this is the same pattern the existing `test_stop_bridge.py`, `test_bridge.py` and `test_compile.py` already use.
   - Assertions for B:
     - its newest checkpoint has reason `parked`;
     - that checkpoint's stored agent has B's next phase at `queue[0]`;
     - its `SubtaskSummary.status == "stopped"`;
     - its `detail == "stopped before <that phase>"`.

Exit criterion: `uv run pytest` passes, with the whole default suite green, including `tests/e2e`'s default-collected parts and the existing M6 stop-bridge tests.

Note: the upstream exploration summary was cut off at its 8000-character cap in the middle of the test-placement paragraph. That means the upstream stage over-ran its brief. The placement rule above was confirmed against CLAUDE.md and the plan, which names both test files. It was not taken from the missing text.
