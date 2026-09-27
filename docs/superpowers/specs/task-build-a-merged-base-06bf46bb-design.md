# Build a merged base from clean merges (card 06bf46bb)

Subtask of story f7b2edd1 "Merged bases" (milestone c2a981a3). Plan: `docs/superpowers/plans/2026-09-25-supervisor-tree.md` Task 2.1. Design: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §5. Sibling Task 2.2 (card 8fe30578, "Resolve base conflicts with the Integrate resolver") extends this module and is out of scope here.

## Scope

Create `src/agent_manager/bases.py`, a plain async module (not a pygents Agent, no `grafo` import) that builds a multi-blocker story's merged base branch by cutting it from the first blocker tip, merging every other tip in cleanly, and verifying the result once. Add `tests/test_bases.py`.

## Interface (verbatim from the plan; Task 2.2 depends on this exact shape)

- `@dataclass(frozen=True) class BaseResult: branch: str; merged: list[str]; already_merged: list[str]; resolved: list[str]`. Field names and order are fixed. `resolved` is always `[]` in this task.
- `class BaseFailed(Exception)` with attributes `.detail: str` and `.stopped: bool`. `str(exc)` carries the detail. Every failure in this task has `stopped=False`.
- `async def build(root: RootPlan, tips: list[str], *, repo_dir, commands, allow_no_verification, store, run_id, story_id, runner_factory, stop) -> BaseResult`.
  - `root` is a `dag.RootPlan` of kind `"merged"`; `root.branch` is already `<prefix>/base-<short id of story>` (built by `dag.base_branch_name`), so `build` uses `root.branch` and does not re-derive it. `RootPlan` exists in this worktree's `dag.py` (landed by the Groundwork story); use it, do not define another.
  - `tips` are the blockers' tip refs, already in `root.blockers` order.
  - `store`, `run_id`, `story_id`, `runner_factory`, `stop` are accepted and unused in this task; they exist so Task 2.2 does not change the call site.

## Observable behavior

1. Worktree location: `<repo_dir>/.claude/worktrees/<root.branch>` (absolute), i.e. the convention of `cli.worktree_for` (`src/agent_manager/cli.py:165`). There is no worktree helper in `paths.py`. Reuse `cli.worktree_for` or its convention; do not introduce an import cycle (orchestrate will import bases later).
2. Cut `root.branch` from `tips[0]` with `steps.worktree.ensure` (via `asyncio.to_thread`). If the branch/worktree already exists it is reused (resume/relaunch).
3. For each `tip` in `tips[1:]`, call `steps.integrate.merge_tip(repo_dir, worktree, root.branch, tips[0], tip, git_runner=run_git)` via `asyncio.to_thread`:
   - clean merge: append `tip` to `merged`;
   - `already_merged`: append `tip` to `already_merged` and continue;
   - `conflict`: raise `BaseFailed` whose detail contains `conflict` and `resolver not wired` (and names the tip), `stopped=False`. The in-progress merge is left as `merge_tip` leaves it (never aborted). Task 2.2 replaces this branch.
   - `MergeInProgressError`: raise `BaseFailed(detail, stopped=False)` whose detail says an earlier conflict in the worktree was never resolved and a human must finish the merge there, then resume (the error's own message already says "never resolved").
4. Verify once, mirroring `integration._final_verification` (`src/agent_manager/integration.py:100-120`): `reducers.verification_gate(commands, bool(allow_no_verification), True)` first (an empty suite is judged before anything runs); if commands is non-empty, `verify.run_suite(commands, worktree)` via `asyncio.to_thread`, judged by `reducers.verification_passed_gate`. Any failure raises `BaseFailed(detail, stopped=False)` naming the worktree/reason.
5. Return `BaseResult(branch=root.branch, merged=..., already_merged=..., resolved=[])`.
6. Every git and verify call goes through `asyncio.to_thread`. Nothing is pushed; the milestone's base branch (e.g. `master`) is never checked out, moved, or written.

## Error paths

- Missing tip ref (e.g. a deleted local branch `m7/gone`), whether it is `tips[0]` (the cut) or a later tip (merge): raise `BaseFailed` whose message names the missing ref, `stopped=False`. No raw `GitError` escapes for this case, and nothing hangs.
- Conflict: `BaseFailed("conflict ... resolver not wired")`, as above.
- Merge already in progress in the base worktree: `BaseFailed` saying it was "never resolved", as above.
- Verification failure or a disallowed empty suite: `BaseFailed`, `stopped=False`.

## Tests: `tests/test_bases.py`

Tier: **Steps**. Every test goes here, per design §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md` lines 501-516): steps run against temporary git repos in `tmp_path`, with no network and no mocks of git, following the precedent in `tests/steps/test_integrate.py:1-9`. There is no `tests/conftest.py`. Port or adapt the repo helpers from `tests/steps/test_integrate.py` (`_init_repo`, `_make_tip`, `_git`, `_head`, `_commit`) and add the new helpers the plan names: a `two_story_repo` fixture (master plus tips `m7/a`, `m7/b`), `MASTER_BEFORE` (master's sha recorded at setup), `is_ancestor(repo, a, b)`, and `rev(repo, ref)`. Tests are `async def` (`asyncio_mode = "auto"`), and none of them sleep. Every test asserts `rev(repo, "master") == MASTER_BEFORE`.

1. `test_two_clean_tips_merge_into_the_base`: `RootPlan("merged", "m7/base-cccccccc", ("A","B"))` with tips `["m7/a","m7/b"]` and `commands=["true"]`. Expect `branch == "m7/base-cccccccc"`, `merged == ["m7/b"]`, `already_merged == []`, `resolved == []`, and both tips are ancestors of the base.
2. `test_building_twice_merges_nothing_the_second_time`: a second `build` call on the same repo gives `merged == []`, `already_merged == ["m7/b"]`, and the base sha is unchanged.
3. `test_a_tip_already_inside_the_other_is_already_merged` (Review Focus 4): B is stacked on A, and tips are ordered so that `tips[0]` is B's tip and A's tip follows. Expect `already_merged == [A's tip]`, `merged == []`, and the base equals B's tip.
4. `test_a_missing_tip_fails_naming_the_ref` (Review Focus 5): `pytest.raises(bases.BaseFailed, match="m7/gone")` with `stopped is False`, covering a missing later tip. Also cover a missing `tips[0]`, either with a parametrize or a second case.
5. `test_a_failing_verify_fails_the_base`: `commands=["false"]` raises `BaseFailed` with `stopped is False`.
6. `test_an_empty_suite_is_judged_before_running`: `commands=[]` with `allow_no_verification=False` raises `BaseFailed`. With `True`, the build succeeds.
7. `test_a_merge_in_progress_fails_for_a_human`: `MERGE_HEAD` is left in the base worktree, which raises `BaseFailed` matching "never resolved" with `stopped is False`.
8. `test_a_conflict_is_not_resolved_yet`: conflicting A/B tips raise `BaseFailed` matching `conflict.*resolver not wired`, with `stopped is False`.

The whole default suite (`uv run pytest`, including `tests/e2e`) must stay green.

## Out of scope

The following are out of scope: conflict resolution through the Integrate resolver, the synthetic `bases` story, `BASES_STORY_ID`/`BASES_STORY_TITLE`, populating `resolved`, and `stopped=True` (all Task 2.2). Also out: wiring `build` into `orchestrate`/`supervise` (Story 3), verification discovery, live pause/cancel/watch/retry, more than one `am` process per repo, a grafo `max_workers` option, and leave-me-alone multi-blocker support.
