# Add the sequential milestone runner (card c9037ac9)

Parent story: f8290fd6 "Run a milestone: rollup, the shared driver, the runner". Blocked by bf26f482 (rollup, done). This spec narrows addendum O6 (`docs/superpowers/specs/2026-09-24-orchestration-design.md`, lines 81-101), which extends `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. The spec only narrows that design and adds no new design.

## Prerequisites (already in this worktree's base)

These are present in this worktree and must be used as they are, without re-implementing them:

- `census.find_milestone`, `census.flatten_milestone`, `StoryPlan` and `SubtaskPlan`.
- `board.roots`, `board.tree`, `board.show`.
- In `dag`: `assert_no_blocker_cycles`, `compute_levels`, `remaining_subtasks`, `is_story_closed`, `is_subtask_done`, `stack_bases`, `story_tip`, `subtask_branch`, `DependencyCycleError`, `StackRootError`.
- `steps.rollup.set_status`.
- In `cli`: `drive_subtask`, `SubtaskDrive`, `RunnerFactory`, `resolve_repo_dir`, `mint_run_id`, `worktree_for`, `WORKFLOW_NAME`.
- `steps.worktree.run_git` and `GitError`.

`cli.dry_run_payload` already composes the pre-write half (cycles, then levels, then bases over `stories_by_id` of every story). The runner must derive the same geometry the same way.

## Scope

A new module `src/agent_manager/orchestrate.py` exposing:

```python
def run_milestone(milestone: str, *, repo_dir: Path, base_branch: str, branch_prefix: str,
                  commands: Sequence[str] = (), allow_no_verification: bool = False,
                  runner_factory: RunnerFactory | None = None,
                  driver: Driver | None = None,
                  clock: Callable[[], datetime] = <utcnow>) -> dict[str, Any]
```

- `Driver` does not exist yet. `orchestrate.py` defines it as a `typing.Protocol` whose `__call__` takes `cli.drive_subtask`'s keyword-only parameters and returns `cli.SubtaskDrive`. Under `TYPE_CHECKING` or as a string annotation, so that no cli name is resolved at import time.
- The clock default is a module-local `_utcnow` (`datetime.now(timezone.utc)`), not `cli._utcnow`, which is private and would break the circular-import rule.
- `milestone` is a card id or a title needle, as in O1.
- `driver` has `cli.drive_subtask`'s keyword signature and returns a `SubtaskDrive`-shaped value (`.summary.status`, `.summary.failed_phase`, `.summary.detail`, `.warnings`). When it is `None`, it resolves to `cli.drive_subtask` at call time, not at definition time. The sibling card will make `cli` import `orchestrate`, so this module must tolerate the circular import: use `from agent_manager import cli` with attribute access at call time, and do not bind cli names in default arguments.
- The module holds no module-level mutable state (O4).

Out of scope:

- The CLI wiring, the removal of `MilestoneRunNotImplementedError` and any tests/e2e work. All of these belong to 3e0ab2b9.
- Parallel stories, Integrate, a milestone-aware `am resume`, watch/retry/cancel, review counts measured by git, and verification discovery.
- Any change to `run_card` or `drive_subtask` behaviour.

## Observable behaviour, in order

1. **Before any write** (no run directory, store, fetch, prune, worktree or board write):
   - Resolve the repo dir.
   - Resolve the milestone with `census.find_milestone(board.roots(...), milestone)`.
   - Build the census with `flatten_milestone(board.tree(milestone.id))`.
   - Call `dag.assert_no_blocker_cycles(stories)`.
   - Compute `dag.compute_levels(stories)`.
   - Compute `stack_bases` and `story_tip` for every pending story, using `stories_by_id` over all stories, done ones included.
   - Preflight `load_builtin(cli.WORKFLOW_NAME)`, as `run_card` does.

   `DependencyCycleError`, `StackRootError` (two or more in-milestone blockers), `MilestoneNotFoundError` and `BoardError` propagate unchanged from this step.
2. **Once per run:**
   - Run `git fetch origin` only if `git -C <root> remote` lists `origin`. A repo with no `origin` skips the fetch silently.
   - Then run `git worktree prune`.

   Both go through `steps.worktree.run_git`. A `GitError` from either propagates.
3. **Record the plan.**
   - `run_id = cli.mint_run_id(milestone.id, clock())`, then `Store.open(root, run_id)`. The store is closed in a `finally`.
   - Write one `models.Run` with `workflow="milestone"`, `config=models.RunConfig()`, `status="started"` and `started_at` from the clock.
   - Every pending story gets a `StoryRun`: `level` is its level index, `tip_branch` is its tip, `status="pending"`.
   - Every remaining subtask of those stories gets a `SubtaskRun`: `branch` is the derived branch, `base_branch` is from `stack_bases`, `worktree_path` is `cli.worktree_for(root, branch)`, `status="pending"`.
   - All rows are written through `record_run`, `record_story` and `record_subtask`.
4. **Re-roll stale stories.**
   - The target is a story that is not closed, has no remaining subtasks, and whose card status is not `done`. It is re-rolled with `rollup.set_status(anchor, "done", repo_dir=root)`.
   - The anchor is one of its individually done subtasks: the last one in census order. This ports `storyRollupAnchor`. Writing a subtask that is already done is harmless, and the rollup's walk to the root repairs the story and the milestone.
   - When no subtask is individually done, skip the story. A story with no subtasks is one example.
   - A `BoardError` here is appended to `warnings` and does not stop the run, because rollup is best effort, like `mark_done` in `task.yaml`.
5. **Walk the levels.** Levels run in order, stories within a level run in census order, and subtasks run strictly in the full census order. A subtask already `done` by census status is skipped and never driven, but it still anchors the next subtask's base. The bases come from step 1 and are not recomputed. For each remaining subtask:
   - Read `card = board.show(subtask.id)` and `parent = board.show(story.id)` fresh.
   - Record the subtask `started` and the story `started`, the story on its first subtask only.
   - Call `driver(store=, run_id=, card=, parent=, subtask=<the SubtaskRun>, repo_dir=root, commands=, allow_no_verification=, runner_factory=)`.
   - Append the driver's `warnings` to the run's warnings.
6. **Success of a subtask.**
   - When `summary.status == "done"`, record the subtask `done` and append its id to `completed`.
   - When it was the story's last remaining subtask, record the story `done`.
7. **Escalation.**
   - The trigger is the first subtask whose summary status is not `done`, or any `Exception` raised by the driver (not `BaseException`, so `KeyboardInterrupt` still propagates).
   - Record that subtask, its story and the run as `escalated`, and stop scheduling.
   - Return `{"escalated": True, "run_id", "level", "story", "subtask", "failed_phase", "detail", "warnings"}`.
   - For an exception, `failed_phase` is `None` and `detail` is `"<ExceptionType>: <message>"`.
8. **Clean run.** Record the run `done` and return `{"done": True, "run_id", "levels", "completed", "tips", "warnings"}`:
   - `levels`: `[{level, stories: [story ids]}]`.
   - `completed`: the subtask ids driven to done in this run, in order.
   - `tips`: `[{story, tip}]` for every census story that has subtasks, in census order, so the human knows what to merge.

   A milestone with nothing pending still records a run and returns `done`, with empty levels.

The runner returns plain dicts. The `{"ok": true, "data": ...}` envelope belongs to the CLI.

## Tests: `tests/test_orchestrate.py`

**Tier.** Per design spec §14 (the only testing reference; CLAUDE.md indexes no separate standard), a module that composes steps is tested against a real temporary git repo and a real temporary brd board, with no network. The harness is replaced at the injected seam. Here that seam is a fake `driver`, so no runner, adapter or `claude` is involved. That puts these tests at the top level, mirroring `src/agent_manager/orchestrate.py`, next to `tests/test_cli.py`. They are not in `tests/e2e`: production wiring with a fake `claude` is 3e0ab2b9's work. Reuse the fixture pattern from `test_cli.py`: the `project` fixture (git on `main` plus `brd init`, with `XDG_DATA_HOME` under tmp_path), `_add_card`, and the `requires_git`/`requires_brd` skips. Set `blocked_by` edges and `done` statuses through `brd` itself.

**The fake driver.** It records each call's `card.id`, `subtask.branch`, `subtask.base_branch` and `subtask.worktree_path`. It returns a canned `SubtaskDrive`, `done` by default and scriptable per card to escalate or raise. It never touches git or the board.

| # | Test | Asserts |
|---|------|---------|
| 1 | call order and stacking | Story A has two subtasks. Story B is blocked by A and has one subtask. The call order is A1, A2, B1. A1's base is `main`, A2's base is A1's branch, and B1's base is A2's branch (A's tip). The worktree comes from `worktree_for`. The result is `done`, `completed` holds all three, and `tips` names both stories. The store shows the run, both stories and all subtasks `done`. |
| 2 | done subtask skipped but anchors | A1 is already `done` on the board. The driver is called only for A2, and A2's base is still A1's branch. A1 never appears in the recorded remaining subtasks. |
| 3 | escalation stops before the next story | A1 escalates at a named phase. B1 is never called. The result carries `escalated`, `level`, `story`=A, `subtask`=A1, `failed_phase` and `detail`. The store has A1, A and the run `escalated`, and B1 `pending`. |
| 4 | raising driver is an escalation | The driver raises `RuntimeError`. The runner returns the escalation payload (no exception escapes) with `failed_phase` None and a detail naming the exception, and the store records `escalated`. |
| 5 | no origin skips the fetch | The fixture repo has no remote, and the run succeeds. Assert no fetch was attempted, either by wrapping `run_git` via monkeypatch or by checking that the recorded argv lists contain `worktree prune` but no `fetch`. A companion case adds a local bare repo as `origin` (still offline) and asserts that the fetch ran exactly once. |
| 6 | two-blocker refusal writes nothing | Story C is blocked by both A and B. `run_milestone` raises `StackRootError` naming both, the driver is never called, no run directory appears under the data dir, no store is created, and no fetch or prune ran. |
| 7 | stale story re-rolled | A story has all subtasks `done` but the story card is `todo`. After the run, the story is `done` on the board, and the milestone is too when nothing else remains. The driver is never called. |

**Verification.** `uv run pytest` must stay green, including tests/e2e and every existing `run --card` test. There is no lint or typecheck step.
