# Subtask 9b944409: Add an awaitable subtask driver

Parent story: e90a2247 ("Groundwork: the stop, an awaitable driver, multi-blocker roots"), milestone c2a981a3. This is Task 1.2 of `docs/superpowers/plans/2026-09-25-supervisor-tree.md`, narrowed from `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` (decisions T2, T5). The supervisor's lanes will `await cli.drive_subtask_async(..., stop=stop, resume_from=...)`, so the driver must run on the caller's event loop and not open its own.

## Prerequisite

Blocked by 364babde (StopSignal and ON_PAUSE parking). That work must be present in this worktree: `src/agent_manager/runtime/stop.py` (`StopSignal`) and `runtime.engine.run_subtask_async(..., should_stop=None, stop=None, resume_from=None)`. Both are present in this worktree. This subtask only calls them. It does not modify `runtime/stop.py`, `runtime/checkpoint.py`, or `runtime/engine.py`.

## Scope

Only `src/agent_manager/cli.py` and the tests that stub the engine walk.

1. Add `async def drive_subtask_async(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, should_stop=None, stop: StopSignal | None = None, resume_from=None) -> SubtaskDrive`.
   - The body is today's `drive_subtask` body (cli.py:654-679), moved without changes. It builds the runner from `runner_factory`, or from `default_runner_factory` when that is `None`, passing `store`, `run_id`, `story_id=parent.id` and `card_id=card.id`. It builds the same `walk` kwargs dict: `story_id`, `subtask`, `repo_dir`, `commands`, `card`, `parent_story`, `extra_context=gate_context(...)`, `agent_runner` and `should_stop`, plus `stop=stop`. `resume_from` is added only when it is not `None`.
   - It then runs `summary = await runtime_engine.run_subtask_async(task_workflow.TASK, store, **walk)` and returns `SubtaskDrive(summary, list(summary.warnings) + list(getattr(runner, "warnings", [])))`.
   - `should_stop` appears in this signature only as a pass-through for the sync wrapper. The plan's Task 3.3 lists `drive_subtask_async` among the functions it removes `should_stop` from. `stop` is keyword-only, typed `StopSignal | None`, and imported from `agent_manager.runtime.stop`.
   - The driver stays a plain coroutine, not a pygents Agent (T2). It does not import `grafo`.
2. `drive_subtask` keeps its exact M6 signature, including `should_stop` and without `stop`, and its docstring contract. Its body becomes `return asyncio.run(drive_subtask_async(...))`, forwarding every argument.

## Observable behavior

- For the same inputs, `drive_subtask_async` and `drive_subtask` give an equal `SubtaskDrive`: the same summary status, the same results keys and the same merged warnings list.
- Awaiting `drive_subtask_async` inside a running event loop succeeds. It never hits `RuntimeError: asyncio.run() cannot be called from a running event loop`.
- A `stop` passed to `drive_subtask_async` reaches `run_subtask_async` as the same object. `should_stop` and `resume_from` reach it exactly as `drive_subtask` passes them today.
- A fresh walk, with `resume_from=None`, calls the engine without a `resume_from` key, as it does today.
- Error paths are unchanged. The driver catches nothing: escalation is `summary.status == "escalated"`, a stop is `"stopped"`, and engine exceptions (`EngineError`, `CheckpointMismatch`) propagate. Calling the sync `drive_subtask` from inside a running loop still raises `asyncio.run`'s `RuntimeError`, as expected. Callers inside a loop must use the async form.

## Test adaptation

The engine stubs that back `drive_subtask` now intercept `run_subtask_async`, not `run_subtask`, because `drive_subtask` reaches the engine only through `drive_subtask_async`. These stubs must become `async def` stubs patched onto `runtime_engine.run_subtask_async`, recording the same `(workflow, store, kwargs)` tuple:

- `_record_walks` at `tests/test_cli.py:1486`
- the `exploding` patch at `tests/test_cli.py:1935`

`tests/test_integration.py:612`'s `_stub_walk` (used only by `test_resolve_conflict_walks_integrate_with_the_same_arguments`) stays exactly as it is, still patching `runtime_engine.run_subtask`. It backs `integration._resolve_conflict`, which calls `runtime_engine.run_subtask` directly and is untouched by this subtask (`integration.py` is out of scope); it never goes through `drive_subtask` or `drive_subtask_async`. Patching `run_subtask_async` there instead would let the real `run_subtask` forward its full keyword set (`card`, `parent_story`, `clock`, `should_stop`, `stop`, `resume_from`) into the recorded kwargs, breaking that test's exact `kwargs == {...}` assertion.

These existing tests must keep passing with their assertions unchanged, apart from the stub target:

- `test_drive_subtask_walks_task_with_the_same_arguments`
- `test_drive_subtask_drives_two_subtasks_under_one_store_and_run`
- `test_drive_subtask_hands_should_stop_to_the_engine`
- `test_drive_subtask_hands_resume_from_to_the_pygents_walk`

The only allowed change to their expected kwargs is the new `stop: None` key.

## Tests

All new tests go in `tests/test_cli.py`. Under the design spec's §14 placement rule, this is CLI-level driver behavior exercised with a fake runner factory and a stubbed or fake-adapter engine. It uses no real harness and no network, so it does not belong in `tests/e2e` or the `e2e` marker. Tests use the existing `_drive_row`, `_record_walks` and `_recording_factory` helpers. No test sleeps.

1. `test_drive_subtask_async_runs_inside_a_running_loop` (tests/test_cli.py, CLI driver tier with a fake runner factory). The test computes the expected `SubtaskDrive` by calling the sync `cli.drive_subtask(...)` first, from the test's own top-level (no loop running yet). It then, inside a coroutine run by `asyncio.run`, awaits `cli.drive_subtask_async(...)` with a fake runner factory for the same inputs. It asserts that no `RuntimeError` is raised by the `await`, and that the awaited result equals the expected `SubtaskDrive`, comparing status, results keys and warnings, including runner out-of-band warnings. It never calls the sync `drive_subtask` from inside the coroutine: that would itself raise `asyncio.run`'s `RuntimeError`, which is a different failure than the one this test guards against.
2. `test_drive_subtask_async_hands_stop_to_the_engine` (tests/test_cli.py, CLI driver tier with the stubbed engine via `_record_walks`). It passes a `StopSignal()` and asserts that the recorded `run_subtask_async` call received that same object as `stop`, with workflow `task_workflow.TASK` and the other kwargs matching the existing same-arguments test.
3. The existing four `drive_subtask` tests listed above, adapted to the async stub (tests/test_cli.py, same tier). Adapting them proves that the sync wrapper still forwards `should_stop` and `resume_from`, and that it leaves out `resume_from` on a fresh walk.

## Out of scope

- Any change to `runtime/engine.py`, `runtime/stop.py` or `runtime/checkpoint.py` (owned by 364babde).
- Removing `should_stop` (Task 3.3).
- grafo, `dag.py` roots and dry-run `merged_from` (1693e86e).
- `orchestrate.py`.
- Verification discovery, live pause/cancel/watch/retry, running more than one `am` process per repo, a `max_workers` option, and leave-me-alone multi-blocker support.

## Verification

- Full suite: `uv run pytest` (the whole default suite must be green, including `tests/e2e` as collected by default).
- Typecheck: none.
- Lint: none.
