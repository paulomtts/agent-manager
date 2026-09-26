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
