# Add `orchestrate.run_board` — subtask design (card baef4f94)

Parent story: 488d9bf4 "am run --board drives every open milestone as one DAG" (milestone 26d1ce3b). Source of truth: `docs/superpowers/specs/2026-10-01-run-board-design.md` §§3.4 (`run_board`), 3.6, 3.7, 3.8. This narrows that agreed design to one function plus its tests.

Note on inputs: the exploration summary handed to this stage was truncated at 8000 characters mid-sentence in its test list. The test list below was completed from spec §3.8 directly, not guessed; anything else the cut-off text may have said is not reflected here.

## Prerequisites (not in scope, must be present before implementing)

`orchestrate.run_board` calls primitives that exist only on unmerged sibling branches, not on `master` (e193dd3):

- `dag.board_levels(roots)` — branch `m14/task-add-board-levels-for-34bc3c16` (card 34bc3c16, this card's `blocked_by`).
- `orchestrate.build_dag_tree(items, *, id_of, blockers_of, node_factory, forward=None)` — branch `m14/task-extract-build-dag-tree-c0a1345c`.
- `supervise(..., slots=None)` — branch `m14/task-let-supervise-accept-an-6bb4f541`.
- `orchestrate._run_milestone_async(..., slots=None)` — branch `m14/task-split-run-milestone-a3eaf615`.

The implementer brings these in by merging/rebasing those branches into this worktree. They are not re-derived, reimplemented or modified here.

## Scope

One new public function, `orchestrate.run_board(...)`, which is additive only. Its keyword arguments mirror `run_milestone`'s run-shaping ones: `repo_dir`, `base_branch`, `commands`, `allow_no_verification`, `runner_factory`, `driver`, `clock`, `max_concurrent`, `control_interval`. It also takes the per-milestone branch prefix from its caller, as a mapping or callable keyed by milestone. Deriving that prefix (`dag.task_stem`) belongs to d78b3118. `run_board` has no `milestone` and no `resume_run_id` parameter.

Out of scope: any change to `cli.py`'s `am run` command, the `--board` flag, the mutual-exclusion refusals, prefix derivation and `--dry-run --board` wiring (all d78b3118). Also out of scope: any change to `run_milestone`'s signature, docstring contract or payload, to `supervise`, or to `build_dag_tree`. No board-level `Run` record and no `am resume --board`.

## Observable behavior

1. **Validate.** The function validates once, up front, for the whole board, and does it before reading anything that writes. It replicates the pre-asyncio checks `run_milestone` makes: `max_concurrent >= 1`, a base branch is present, and each milestone's branch prefix is valid. A bad argument raises the same error type `run_milestone` raises for it.
2. **Read and level.** It reads `board.roots()`. Open milestone roots are leveled with `dag.board_levels`, which already drops done roots that have no open descendant. A `DependencyCycleError` from `board_levels` propagates before any claim check or run directory exists.
3. **Pre-flight claims.** For every open milestone it computes `milestone_claims(milestone_id, stories, branch_prefix)`, taking the stories from the same census `run_milestone` uses. It unions the keys in level order and then in-level order, deduplicating with `list(dict.fromkeys(...))` so a key's first occurrence keeps its place. It then calls `cli.refuse_claimed(repo_root, keys)` once. A `ClaimedError` propagates, and when it does no milestone has started, no run directory exists and no lease has been taken. This check adds to each milestone's own pre-flight inside `_run_milestone_async` and does not replace it.
4. **One semaphore, one loop.** One `asyncio.Semaphore(max_concurrent)` is created and one `asyncio.run(...)` covers the whole board. Inside that loop, `build_dag_tree` runs over the open milestone cards with `id_of` = card id and `blockers_of` = that card's `blocked_by` restricted to open milestones on the board. That restriction is the caller's job per `build_dag_tree`'s contract; a done blocker counts as satisfied. A single `grafo.TreeExecutor` runs the tree, so grafo's edges set the order and there is no level-stepping loop.
5. **Node coroutine.** Every milestone's coroutine runs through one `milestone_done`/`milestone_ok` pair of dicts (the same mechanism `story_done`/`story_ok` use at the story level): it first waits on every one of its own blockers' completion signals — this applies whenever a milestone has one or more blockers, not only 2+. This is deliberate and not merely mirroring `build_dag_tree`'s edge-vs-root split: the node must not raise (see below), so a single-blocker milestone's `build_dag_tree` edge always fires once its one blocker's node completes, clean or not — `build_dag_tree` only gates *scheduling*, never the blocker's outcome. The dependent's own coroutine, not the edge, is what decides whether to dispatch. Once every blocker is confirmed clean (or there are none), the coroutine awaits `_run_milestone_async(milestone_id, ..., slots=<shared semaphore>)`. It must not raise: any `Exception` becomes an `escalated` outcome whose error string is `"<Type>: <msg>"`, the same as the foreign-error convention in `collect_outcomes`. Not raising matters here specifically because the board's single `grafo.TreeExecutor` covers every milestone in one run: a node that raised would stop that executor from enqueuing any node not already in flight, which would disturb independent siblings board-wide — unlike a solo milestone's own `supervise()` tree, where that scope is already just the one milestone. A failing milestone therefore never cancels or disturbs its siblings.
6. **Blocked propagation.** A milestone counts as clean only when its payload status is `done`. If any blocker is not clean (escalated, stopped, cancelled, raised, or itself blocked), the dependent is never dispatched. That check is uniform across blocker counts: a single-blocker milestone's coroutine reads its one blocker's signal the same way a 2+-blocker milestone's coroutine reads all of its blockers' signals (step 5) — the dependent is never dispatched even though `build_dag_tree`'s edge already fired. The dependent is reported as `{"milestone_id": id, "status": "blocked", "blocked_by": [<ids of the non-clean blockers>]}`. Nothing is created for a blocked milestone: no Run row, no lease, no run directory.
7. **Payload.** The function returns a plain dict and does not wrap it in the CLI envelope:
   `{"ok": bool, "board": True, "levels": [{"level": i, "milestones": [ids...]}, ...], "milestones": [...]}`.
   - `levels` comes from step 2's `board_levels` output, which supports the dry-run preview.
   - `milestones` has one entry per open milestone, in level order then in-level order. A dispatched milestone's entry is its full `_run_milestone_async` payload plus `milestone_id` and `status`. An undispatched one gets the blocked shape above.
   - `ok` is `True` only if every entry's `status` is `"done"`. An empty board (no open milestones) returns `ok: True` with empty `levels` and `milestones`, and makes no `refuse_claimed` call that could matter.
8. **Named limitation, kept on purpose.** There is no board-level bookkeeping beyond each milestone's own Run row. The docstring states the recovery path: re-run `am run --board`, where done milestones drop out through `board_levels` and claims, or run `am resume <run-id>` per milestone.

## Error paths (summary)

| Condition | Result |
|---|---|
| Invalid args | Raises before any read or write |
| Cycle among milestones | `DependencyCycleError`, nothing started |
| Claim conflict anywhere on the board at start | `ClaimedError` from the single upfront `refuse_claimed`, nothing started |
| Claim taken mid-run | That milestone's own pre-flight refuses it; it is reported non-done, its dependents are `blocked`, independent siblings are unaffected |
| A milestone raises | Recorded as `escalated` with `"<Type>: <msg>"`, siblings unaffected, dependents `blocked` |

## Tests

Placement rule: the rule in force on this checkout is main design §14 together with the precedent in `tests/e2e/test_parallel_milestone.py`. Fake-`claude` production-wiring tests go in `tests/e2e/`, unmarked, and run in the default suite. The test-tier addendum (`2026-10-02-test-tier-design.md`, V1/V2/V6) is still only *proposed* and explicitly excludes milestone 14's files. If it lands, `tests/e2e/` gets auto-marked `e2e_fake` with no edit needed here. Do not apply the `e2e` marker, which is the real-money tier.

New module `tests/e2e/test_run_board.py`, in the same shape as `test_parallel_milestone.py`. It uses the fake `claude` first on `PATH`, a real temp git repo and a real temp brd board, and calls `orchestrate.run_board` directly with no `driver`/`runner_factory` (no CLI flag exists yet). Every test belongs to the e2e tier above.

1. Two independent milestones: both finish, `ok: True`, both entries `done`, and both appear in level 0.
2. A `blocked_by` pair (B blocked by A): both `done`, A's levels place it before B, and every one of A's subtasks finished before B's first subtask started, checked from the Run rows or the fake-claude log timestamps.
3. Escalation isolation: A escalates (fake claude armed to fail A's subtask), independent sibling C still finishes `done`, and B (blocked by A) is reported `{"status": "blocked", "blocked_by": [A]}`. B has no Run row and none of its subtasks were spawned. `ok: False`.
4. Shared semaphore cap: two independent milestones with `max_concurrent=1`. The concurrency-tracking rendezvous pattern from `test_parallel_milestone.py` shows at most one story inside implement at any moment across both milestones, and with `max_concurrent=2` the rendezvous at count 2 completes.
5. Upfront claim refusal: a live lease planted on a subtask of the second-level milestone makes `run_board` raise `ClaimedError`. No run directory or Run row exists for any milestone, including the first-level one.

Default-tier `uv run pytest` must stay green, and the existing `run_milestone` and `supervise` tests must pass unchanged.
