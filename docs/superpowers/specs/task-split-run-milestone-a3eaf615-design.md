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
