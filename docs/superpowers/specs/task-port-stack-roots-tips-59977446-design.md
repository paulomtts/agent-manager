# Port stack roots, tips and bases (card 59977446)

Subtask of story 09a9203b "Stack geometry, and a dry run that shows it". Narrows decision O2 of `docs/superpowers/specs/2026-09-24-orchestration-design.md` (lines 44-54), which extends `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. Port source: `~/Code/leave-me-alone/plugins/leave-me-alone/workflows/orchestrator.js:222-268`.

## Base

Build on sibling c4e32e4a's finished work (branch `m3/task-port-dependency-levels-c4e32e4a`, HEAD 0c3401d). Its `dag.py` already holds the doneness helpers, the levels, `DependencyCycleError` and `assert_no_blocker_cycles`, and imports `StoryPlan` and `SubtaskPlan` from `census.py`. Do not re-port or change any of that. If the working branch does not yet contain it, integrate that branch first.

## Scope

Only `src/agent_manager/dag.py` and `tests/test_dag.py`. The code stays pure: no I/O, no subprocess, no `brd`. Update the module docstring's paragraph that lists what the module holds so it also mentions stack geometry.

Out of scope: `cli.py`, including everything in `am run --milestone --dry-run` (sibling 36faf21e). Also out: the levels and cycle code, parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, and git-measured review counts.

## Behavior

Types: `story` is a `StoryPlan`. `stories_by_id` is a `Mapping[str, StoryPlan]` for the milestone's stories. `prefix` and `base_branch` are `str`. `seen` is an optional `set[str]` that defaults to a fresh set per top-level call. Never use a shared mutable default.

- `subtask_branch(prefix, subtask) -> str` returns `task_branch(prefix, subtask)`. It adds no naming logic of its own, so there is one naming source. Port the orchestrator's comment about why geometry never consults PR head refs, condensed: the prefix is part of the milestone's identity, and determinism beats reconciling against an external system.
- `story_tip(story, stories_by_id, prefix, base_branch, seen=None) -> str`: if `story.subtasks` is non-empty, it returns `subtask_branch(prefix, story.subtasks[-1])`. That is the last subtask of the FULL ordered list, whatever its status. A story with no subtasks contributes no branch, so the call falls through to `story_root(...)` with the same `seen`.
- `story_root(story, stories_by_id, prefix, base_branch, seen=None) -> str`:
  - If `story.id` is already in `seen`, raise `DependencyCycleError`. The message names the story and says a cycle was reached while computing its stack root.
  - Otherwise add `story.id` to `seen`.
  - Blockers are the entries of `story.blocked_by` that are keys of `stories_by_id`, in their original order. External blockers are ignored.
  - With 0 blockers, return `base_branch`.
  - With 1 blocker, return that blocker's `story_tip(...)` with the same `seen`. This holds even when the blocker's status is `done`, because done does not mean its code landed anywhere.
  - With 2 or more, raise `StackRootError`.
  - Port the orchestrator's incident comments, condensed: why external blockers are skipped, and why multiple blockers are refused rather than guessed.
- `stack_bases(story, stories_by_id, prefix, base_branch) -> dict[str, str]`: maps each subtask id in `story.subtasks` (FULL list, census order, never `remaining_subtasks`) to its base branch.
  - Index 0 gets `story_root(story, ...)`.
  - Index i gets `subtask_branch(prefix, story.subtasks[i-1])`. A done first subtask therefore still anchors the second.
  - A story with no subtasks still computes its root, so root errors surface, and returns `{}`.

The docstrings note that `assert_no_blocker_cycles` must run before any of these functions. The `seen` guard is only a backstop, because a cycle between two populated stories never recurses back to trip it.

## Error paths

- `StackRootError(ValueError)` is a new class, declared in the style of `DependencyCycleError`. Its docstring gives the reason for subclassing `ValueError`: it is already in `cli.HANDLED`. It is raised when a story has 2 or more in-milestone blockers. The message must contain:
  - the story id, as `#<id>`
  - the blocker count and every blocker id, as `#<id>`, comma-joined
  - a statement that a stack can only root on ONE parent branch
  - the instruction to merge those blockers into `<base_branch>` first, or to restructure the dependencies so the story has a single blocker
- The cycle guard in `story_root` reuses `DependencyCycleError`. It is a cycle, and it is already a `ValueError`.
- `subtask_branch` passes through `task_branch`'s own `ValueError` for a malformed card id without changing it.

## Tests

The tier rule comes from the design doc's section 14 "Testing", `2026-09-23-agent-manager-design.md:477-492`: pure `dag.py` functions get plain unit tests. Every test below is therefore a **unit test in `tests/test_dag.py`** and builds `StoryPlan`/`SubtaskPlan` fixtures in memory. None goes in `test_cli.py` or `tests/e2e`. The whole default suite, including `tests/e2e`, must stay green.

1. `subtask_branch(prefix, s) == task_branch(prefix, s)` for a `SubtaskPlan`.
2. A story with no blockers roots on `base_branch`.
3. A story whose only blockers are outside `stories_by_id` roots on `base_branch`.
4. A story with one in-milestone blocker roots on that blocker's tip, which is the branch of the blocker's last subtask.
5. A blocker with status `done`, all subtasks `done`, still yields its tip.
6. Two blockers raise `StackRootError`. The message contains the story id, both blocker ids, and `base_branch`. The error is also an instance of `ValueError`.
7. A blocker with no subtasks falls through to its own root: A has no blockers, B (no subtasks) is blocked by A, and C is blocked by B. C's root is A's tip. Also check that a subtask-less story with no blockers has a tip equal to `base_branch`.
8. Cycle guard: two subtask-less stories block each other, and `story_root` raises `DependencyCycleError`. A pre-populated `seen` containing the story's id also raises.
9. `stack_bases` uses the FULL list. With a first subtask that is `done`, index 0 maps to the story root and index 1 maps to the done subtask's branch. Every subtask id appears.
10. `stack_bases` for a story with no subtasks returns `{}`.
11. The milestone-2 board: three stories chained (S1, then S2 blocked by S1, then S3 blocked by S2) with 2-3 subtasks each. Assert the full `stack_bases` map of every story. S1's first subtask bases on `base_branch`, S2's first on S1's last subtask branch, S3's first on S2's last subtask branch, and every other subtask on its predecessor.
