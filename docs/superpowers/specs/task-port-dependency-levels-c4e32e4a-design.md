# Port dependency levels and cycle detection (card c4e32e4a)

Parent story 09a9203b "Stack geometry, and a dry run that shows it" (milestone 99e178cb). This card narrows decision O2 of `docs/superpowers/specs/2026-09-24-orchestration-design.md`, which extends `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. The StoryPlan shape comes from O1.

## Scope

Add pure functions for doneness, dependency levels and blocker-cycle detection to `src/agent_manager/dag.py`. Test them in `tests/test_dag.py`. The functions are ported from `~/Code/leave-me-alone/plugins/leave-me-alone/workflows/orchestrator.js`: lines 84-101 for doneness, 131-165 for levels, and 185-208 for the cycle check.

`dag.py` stays pure: no I/O, no subprocess, no `brd`. It already makes that promise in its module docstring. Extend the docstring to cover the new functions. The functions take `census.StoryPlan` and `census.SubtaskPlan` objects, which are frozen dataclasses defined in `src/agent_manager/census.py` on this branch. Fields are read by attribute (`id`, `title`, `status`, `blocked_by`, `subtasks`). `dag.py` may import `census` for type hints, because `census` is also pure. Do not change `census.py`; it belongs to card b84d47e1.

Out of scope, because other cards own it:
- `subtask_branch`, `story_tip`, `story_root`, `stack_bases` and the two-blocker error belong to sibling 59977446.
- `am run --milestone --dry-run` and any `cli.py` change belong to sibling 36faf21e.
- Section 4 of the orchestration spec rules out parallel stories, Integrate, milestone-aware resume, watch/retry/cancel, git-measured review counts and verification discovery.
- `storyRollupAnchor` (orchestrator.js 104-125) is not part of this card.

## Observable behavior

- `is_subtask_done(subtask) -> bool` is true when `subtask.status.lower() == "done"`.
- `is_story_closed(story) -> bool` is true when `story.status.lower() == "done"`.
- `remaining_subtasks(story) -> list[SubtaskPlan]`:
  - If the story is closed, it returns `[]`, whatever the subtasks' own statuses are.
  - Otherwise it returns the subtasks that are not done, in their existing order.
- `topological_levels(stories) -> list[list[StoryPlan]]`:
  - Let `ids` be the set of input story ids.
  - On each pass, "ready" means every remaining story whose `blocked_by` entries are each either outside `ids` or already placed.
  - Each ready batch becomes one level and keeps input order. The loop repeats until nothing remains.
  - Empty input returns `[]`.
  - The same `StoryPlan` objects come back. The input list is not mutated.
- `compute_levels(stories)`:
  - Drops every story that is closed or has no remaining subtasks.
  - Keeps census order for the rest, with no re-sorting.
  - Returns `topological_levels` of the pending stories.
  - A dropped story's id is outside the pending set, so a story blocked only by a finished story lands in level 0.
- `compute_integrate_levels(stories)` returns `topological_levels` over every story, finished or not.
- `assert_no_blocker_cycles(stories) -> None`:
  - Runs a depth-first search from each story in input order, marking stories as visiting or done.
  - Follows only blockers whose id is a story in the input.
  - Returns `None` when the stories have no cycle.
- Everywhere, blockers outside the milestone (ids not among the input stories) are ignored.

## Error paths

Add one error type, `DependencyCycleError(ValueError)`, to `dag.py`. It subclasses `ValueError` for the same reason as census's errors: `ValueError` is already in `cli.HANDLED`, so a later CLI caller gets an `ok: false` envelope without `dag.py` importing `cli`.

- **`topological_levels` finds no ready story while some remain.** It raises `DependencyCycleError`. The message lists every story still unplaced, in input order, as `#id, #id`, after the text `dependency cycle among stories `.
- **`assert_no_blocker_cycles` reaches a visiting story again.** It raises `DependencyCycleError` with a message containing `dependency cycle among stories #a -> #b -> #a`. The trail is sliced from the first occurrence of the repeated id, and the repeated id is appended at the end.
- **Message wording.** Port both messages from orchestrator.js. The JS `orchestrator:` prefix may be swapped for `dag:`, and the JS text after the cycle (`— no stack can be rooted until it is broken`) may be kept. Tests assert only on the substrings above.

## Comments to port (condensed)

Keep the orchestrator's incident reasoning next to the code:
- brd `status` is the only source of truth for doneness.
- A story marked done is never re-dispatched. During the 2026-08-17 outage, per-subtask lookups returned null and closed stories were re-implemented. The story's single status field cannot be corrupted piecemeal, and a story closed by mistake is reopened by hand.
- Card ids have no inherent order, so census order is the only stable order, and pending stories keep it.
- Integrate levels include finished stories, because a finished story's tip still has to be folded in.
- The cycle check has to run before any geometry. `story_tip` returns a branch immediately for a populated story, so a cycle between two populated stories never trips `story_root`'s own guard.

## Tests

All of these tests go in `tests/test_dag.py`, extending the existing file with plain pytest functions (parametrized where natural). §14 of the base design spec puts them in the pure unit-test tier: "Pure functions (dag.py, ...) — unit tests ported alongside the logic". They need no git, brd, subprocess or fake `claude`, and nothing goes under `tests/e2e/`. Build the inputs with real `census.StoryPlan` and `census.SubtaskPlan` instances.

1. **Doneness is case-insensitive.** `is_subtask_done` and `is_story_closed` both accept `"done"` and `"DONE"`, and reject `"todo"` and `"in_progress"`. (Unit)
2. **A closed story has no remaining subtasks.** `remaining_subtasks` returns `[]` for a closed story, even when its subtasks are todo. (Unit)
3. **Open story, mixed subtasks.** On an open story with a mix of done and todo subtasks, `remaining_subtasks` returns the not-done subtasks in order. (Unit)
4. **Linear chain.** For A ← B ← C, `compute_levels` returns `[[A], [B], [C]]`. (Unit)
5. **Diamond.** For A, then B and C blocked by A, then D blocked by B and C, the result is `[[A], [B, C], [D]]`, with B and C in input order. (Unit)
6. **Independent roots.** Two unblocked stories share level 0, in input order. (Unit)
7. **Done story, dispatch versus integrate.** Story A is done and B is blocked by A. `compute_levels` returns `[[B]]`, without A. `compute_integrate_levels` returns `[[A], [B]]`. Also check that a story that is open but has every subtask done is dropped from `compute_levels`. (Unit)
8. **External blocker ignored.** A story blocked by an id outside the input is placed in level 0 by `topological_levels`, and `assert_no_blocker_cycles` does not raise for it. (Unit)
9. **Two-story cycle.** A and B block each other. `assert_no_blocker_cycles` raises `DependencyCycleError` with a message containing `#a -> #b -> #a`, where the actual ids replace `a` and `b` and the trail starts from the first story walked. `topological_levels` also raises `DependencyCycleError`, naming both. (Unit)
10. **No cycle, no error.** On an acyclic milestone, such as the diamond, `assert_no_blocker_cycles` returns `None`. (Unit)

Verification: run `uv run pytest`. The whole default suite must pass, including `tests/e2e`.
