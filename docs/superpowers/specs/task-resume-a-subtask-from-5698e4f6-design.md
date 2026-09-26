# Resume a subtask from its checkpoint (card 5698e4f6)

Parent story: a6c7bff3 "Checkpoints and resume" (milestone m6). Narrowed from plan Task 4.3 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1253-1280`) and pygents-engine design §6/§8. This subtask adds the engine-level resume primitive only.

## Scope

Modify `src/agent_manager/runtime/engine.py` only. No new source files. Add `tests/runtime/test_resume.py`.

Out of scope, owned by sibling cards:
- `store.Checkpoint`, `save_checkpoint`, `latest_checkpoint`, `latest_open_checkpoint` (3d4947e8, done). This card only consumes `store.Checkpoint`.
- `runtime/checkpoint.py` hooks, `Parked`, `save`, and the body of `_run` / `_collect` / `_forget` (921ed349, done). None of these change.
- `--engine` plumbing through cli/orchestrate/integration (7fdec762).
- `am resume --engine pygents`, exit code 3 on mismatch, orphan-attempt marking, milestone relaunch from open checkpoints (02890d5d).
- Switching `_forget` to `AgentRegistry.unregister` (pygents 0.7.0 adoption, decision A2). That has not landed on this base. `_forget` still pops `AgentRegistry._registry`, so this card uses the same pop-and-tolerate-missing style.

## Interface

- `runtime.engine.CheckpointMismatch(Exception)` is a new module-level exception. Its message names the checkpoint's digest and the workflow's digest.
- `runtime.engine.run_subtask(..., resume_from: store.Checkpoint | None = None)` is a new keyword-only parameter. It defaults to `None`, and it is passed through to `_drive`. Every other parameter and the `SubtaskSummary` return type stay the same. Update the `run_subtask` docstring, because its "No `start_phase`: resume belongs to the yaml engine" sentence will be wrong once this lands.

## Observable behavior

The `_drive` order is:
1. Build the binding and refuse reserved `extra_context` keys, unchanged.
2. Call `C.compile_workflow(workflow)`. This registers the digest-prefixed tools.
3. If `resume_from` is `None`, keep today's path: `Agent(...)`, `seed_item`, `first_turn`.
4. If `resume_from` is given:
   - If `resume_from.digest != workflow.digest()`, raise `CheckpointMismatch`. This happens before any agent is built and before `run()`, so no checkpoint row, phase row, attempt row or journal line is written.
   - Otherwise, drop any stale `AgentRegistry` entry under the checkpointed agent's name, tolerating a missing entry.
   - Build the agent with `Agent.from_dict(resume_from.agent)`.
   - Do not call `seed_item` or `first_turn`. The pool, which holds the seed and earlier phase results, and the queue, which holds the pending turn and its loop count, come from the checkpoint.
   - Compiling in step 2 before `from_dict` is what makes a stale or changed workflow fail early instead of binding the wrong tools.
5. Both paths then share the unchanged `RunDeps` + `_run` + `finally: _forget(agent.name)`. `agent.run()` is still consumed to the end. It is never broken or returned out of.

A resumed run produces the same shapes as a fresh run (G10): the same `SubtaskSummary` (results include the restored earlier phases, taken from the pool), the same phase and attempt rows for the phases it actually dispatches, and the same escalation payloads and final subtask status. Checkpoints keep being written by the existing hooks: `turn` at each BEFORE_TURN, and `done`/`escalated`/`parked` at the end.

Error paths:
- On a digest mismatch, `CheckpointMismatch` propagates to the caller and nothing is recorded. Mapping it to exit 3 is not this card's job.
- A `BaseException` during a resumed run passes through unchanged, exactly as in a fresh run. The last `turn` row stands, so the run can be resumed again.
- `EngineError` and the escalation branches behave as in `_run` today.

## Tests (`tests/runtime/test_resume.py`)

All five tests belong to the "Engine" tier from agent-manager design §14 and pygents-engine design §9: engine tests driven by a fake runner/adapter, with simulated mid-phase crashes. They live under `tests/runtime/` to mirror `runtime/engine.py`. They are not `e2e` and they run in the default suite. They rely on the existing autouse `fresh_pygents` fixture in `tests/runtime/conftest.py` for `ToolRegistry`/`AgentRegistry`/compile-cache cleanup and add no registry cleanup of their own.

1. `test_a_crash_resumes_at_the_phase_it_died_in`: the fake runner raises `KeyboardInterrupt` in phase `c` of a workflow a..e, and the call propagates it. The latest checkpoint has reason `turn` and its queue head is `c`. Resuming with `resume_from=` that row dispatches exactly `c, d, e`. The results for `a` and `b` are present in the summary, taken from the pool without being re-run. The status is done.
2. `test_loop_count_survives_a_resume`: the run crashes inside a looped phase on its second pass (loop=1). After resuming, the dispatched turn carries loop=1.
3. `test_a_changed_workflow_is_refused`: take a checkpoint saved under workflow W and call with W′, whose digest differs. `CheckpointMismatch` is raised, no new checkpoint row is written, and no phase is dispatched.
4. `test_a_parked_subtask_resumes`: `should_stop` parks before `b`. Resuming from the `parked` checkpoint runs `b..end`, and the subtask status is done.
5. `test_resuming_twice_in_one_process`: resume the same card twice in a row in one process with no `ValueError` from `AgentRegistry` (Review Focus 4).

## Verification

`uv run pytest` must pass for the whole default suite, including the non-e2e `tests/e2e` wiring tests. There is no `--engine` selector yet (7fdec762, not landed on this base): "the whole default suite" already covers both the yaml engine's tests and the pygents engine's tests as separate files, so nothing new needs to be added to exercise "both engines".
