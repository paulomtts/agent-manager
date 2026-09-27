# Resolve base conflicts with the Integrate resolver (card 8fe30578)

Subtask of "Merged bases" (f7b2edd1), milestone c2a981a3. This narrows an agreed design to one subtask. It does not add a new one. Sources: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` (T7, §5, §9 Testing) and `docs/superpowers/plans/2026-09-25-supervisor-tree.md` Task 2.2. Where this doc and those disagree, those win.

## Scope

In `src/agent_manager/bases.py`, replace the conflict branch of `build` with a call to the Integrate resolver. That branch currently raises `BaseFailed("conflict ... resolver not wired ...")`. Nothing else in `bases.py` changes. The clean-merge path, the missing-tip check, the `MergeInProgressError` handling and verification all belong to sibling 06bf46bb, which is done. `build`'s signature does not change: it already accepts `store`, `run_id`, `story_id`, `runner_factory` and `stop`.

Out of scope:
- lane and supervisor handling of `BaseFailed` (`LaneEscalated` with `failed_phase: "base"`, `LaneStopped`)
- resuming a parked base resolver from its checkpoint (`resume_from`)
- `orchestrate.py` wiring
- dry-run `merged_from` output
- the run `data.bases` field

All of those belong to later Story 3 and Story 4 cards.

Dependency: this card needs `runtime.stop.StopSignal` and `runtime.engine.run_subtask_async`. They were built on `m7/task-add-stopsignal-and-park-364babde`, which brd does not list as a blocker. Both are already present in this worktree (`src/agent_manager/runtime/stop.py`, `src/agent_manager/runtime/engine.py:94`). Confirm they are there before writing tests. If they are missing, stop and flag it.

## Observable behavior

New module constants: `BASES_STORY_ID = "bases"` and `BASES_STORY_TITLE = "Merged bases"`.

When `merge_tip` reports `conflict` for a tip in `tips[1:]`, `build` does the following:

1. **Records the synthetic story, on first need only.** It records `StoryRun(card_id="bases", title="Merged bases", level=0, status="started")`. This follows the lazy `story_recorded` pattern in `integration.integrate_milestone` (integration.py:213-234).
   - A build with only clean merges records nothing.
   - A run gets one `bases` story row, even when several stories build bases or one base hits several conflicts. `record_story` is an upsert keyed on `(run_id, card_id)`.
2. **Records the synthetic subtask.** It records `SubtaskRun(card_id=f"base-{story_id}", branch=root.branch, base_branch=tips[0], status="started", worktree_path=<base worktree>)` with `store.record_subtask("bases", ...)`. This must happen before the engine journals its first phase, as in integration.py:151.
3. **Awaits the resolver on the running loop.** It awaits `runtime.engine.run_subtask_async(workflow.integrate.INTEGRATE, store, ...)` with these arguments:
   - `story_id="bases"` and `subtask=` the row from step 2
   - `repo_dir=` the resolved repo and `commands=` the suite
   - `extra_context={"merge_tip": tip, "conflict_files": [str(f) for f in result["files"]], **cli.gate_context(commands, allow_no_verification)}`
   - `agent_runner=runner_factory(store=store, run_id=run_id, story_id="bases", card_id=f"base-{story_id}")`
   - `stop=stop`

   The resolver is awaited directly, not wrapped in `to_thread`, because it is already async. Every git and verify call that stays in `build` still goes through `asyncio.to_thread`.
4. **Branches on `summary.status`:**
   - `"done"`: the tip is appended to both `merged` and `resolved`, and the loop continues. `BaseResult.resolved` lists exactly the tips a resolver fixed, in the order given. `merged` still lists every tip merged after the first, resolved or not, the same way `integrate_milestone` appends to both lists.
   - `"stopped"` (parked by `stop`): raise `BaseFailed(detail, stopped=True)`. `detail` names the tip, the base branch and the engine's `stopped before <phase>`. The engine has already saved a `parked` checkpoint for card `base-<story id>` and recorded the subtask as `stopped`.
   - Anything else (escalated): raise `BaseFailed(detail, stopped=False)`. `detail` follows `integration._resolver_detail`. It names the tip, the base branch, `failed_phase`, the status and the engine's detail. It also says the branch and worktree are left as they are, and that a human must finish the merge there and then relaunch.

After a resolved conflict, final verification runs exactly as it does today, once, after all tips. A verification failure still raises `BaseFailed(..., stopped=False)`.

Invariants:
- `bases.py` stays a plain async function: no grafo import, not a pygents Agent (milestone rules 1 and 2).
- `BaseResult` stays a frozen dataclass.
- The milestone's base branch is never checked out, merged into or moved, and nothing is pushed (rule 6).
- On failure, the base branch and worktree are left as they are for a human.

## Error paths

| Case | Result |
|---|---|
| Resolver escalates (for example, refuse mode leaves `MERGE_HEAD`, so `merge_completed_gate` blocks) | `BaseFailed(stopped=False)`, resolver detail as above; subtask row `escalated` |
| `stop.trigger` fires while the resolver's first phase is in flight | That turn finishes, the agent parks before the next phase, `parked` checkpoint for `base-<story id>`, `BaseFailed(stopped=True)` |
| Conflict when `store`, `story_id` or `runner_factory` is `None` | `BaseFailed(stopped=False)`: the resolver cannot run, the merge is left in progress in the worktree for a human; nothing is recorded. `stop=None` is valid and means no stop |
| `MergeInProgressError`, missing tip, failed or absent verification | Unchanged from 06bf46bb |

## Tests

Placement follows design §14 as refined for this repo:
- `bases.py` is a **Steps** component, so its tests go in `tests/test_bases.py` against real temporary git repos (the existing `conflicting_repo` / `two_story_repo` fixtures), with no mocks of git.
- The resolver is supplied through `runner_factory` as an injected launcher double, the way `tests/test_integration.py`'s `FakeResolver` factory does it. That double plays fake `claude`'s resolver mode: resolve means keep both sides and commit; refuse means claim resolved but leave `MERGE_HEAD`. It knows only what its brief (prompt) tells it (rule 5).
- No test sleeps. The stop test triggers from inside the launcher double's first call (rule 4).

In `tests/test_bases.py` (Steps tier):
1. `test_a_conflict_is_resolved_by_the_integrate_resolver` replaces `test_a_conflict_is_not_resolved_yet`. With conflicting tips A and B for story C, `build` checks all of the following:
   - It returns `resolved == [B tip]`, with B also in `merged`.
   - The base branch contains both tips' commits.
   - The worktree has no `MERGE_HEAD`.
   - The milestone base branch is unchanged.
   - The resolver was called once with the conflict files.
2. `test_the_resolver_walk_is_journalled_under_the_bases_story`: after a resolved conflict, `store.load_run(run_id)`, which is what `am status` reads, has one story `bases` titled "Merged bases". That story has subtask `base-<C id>` with the INTEGRATE phase rows. The subtask row exists before the first phase row (journal order).
3. `test_a_clean_build_records_no_bases_story`: a clean two-tip build leaves no `bases` story in the store.
4. `test_two_bases_in_one_run_share_one_bases_story`: two conflicting builds for different stories in the same store give one `bases` story with two subtasks, `base-<C id>` and `base-<D id>`.
5. `test_a_resolver_that_gives_up_fails_the_base`: in refuse mode, `build` raises `BaseFailed` with `stopped is False` and a detail naming the tip, the base branch and the failed phase. The subtask is `escalated`, the merge is left in the worktree, and the milestone base is unchanged.
6. `test_a_stop_during_the_resolver_parks_it`: the launcher double calls `stop.trigger(...)` during the resolver's first phase. `build` raises `BaseFailed` with `stopped is True`, `store.latest_checkpoint(f"base-{C}")` has kind `parked`, the subtask is `stopped`, and no later phase ran.
7. `test_a_conflict_without_a_resolver_fails_for_a_human`: a conflict with `runner_factory=None` raises `BaseFailed(stopped=False)`, records nothing and leaves the merge in progress.
8. Extend the existing `test_every_git_and_verify_call_runs_off_the_event_loop_thread`, or add a conflict-path twin of it, so that the git and verify calls around a resolved conflict are also shown to run off the loop thread.
9. The existing `test_build_is_a_plain_coroutine_that_does_not_import_grafo` and the clean-merge tests stay as they are, and must stay green.

In `tests/e2e/test_fake_claude.py` (production-wiring tier, unmarked, default suite): reuse M5's resolver-mode tests (`test_the_resolver_*`, around lines 1064-1204) unchanged. They pin the resolve and refuse behavior that the test_bases.py launcher double copies. Do not add a test marked `e2e`, and do not add anything to `tests/test_orchestrate.py`.

## Verification

- fullSuite: `uv run pytest` (the whole default suite, including `tests/e2e`, must be green)
- typecheck: none
- lint: none
