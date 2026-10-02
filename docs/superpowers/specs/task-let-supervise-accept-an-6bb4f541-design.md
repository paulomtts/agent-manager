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
