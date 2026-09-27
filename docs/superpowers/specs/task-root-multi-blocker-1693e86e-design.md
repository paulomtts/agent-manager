# Root multi-blocker stories on a merged base in dag (card 1693e86e)

Subtask of e90a2247 ("Groundwork: the stop, an awaitable driver, multi-blocker roots"), milestone 7. Narrows Task 1.3 of `docs/superpowers/plans/2026-09-25-supervisor-tree.md` and `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §3 T7/T10, §4 (table and `RootPlan`), §8 (`--dry-run` row).

Note: the exploration findings handed to this stage were truncated mid-sentence at the test-tier placement rule. The tier assignments below were taken from the tier docstrings already in `tests/test_dag.py`/`tests/test_cli.py`/`tests/test_orchestrate.py` (design §14: pure helpers are unit tests; commands on real temp git + brd boards are Steps-tier fixtures), not from the missing text.

## Scope

In scope:

- `pyproject.toml`: add `grafo>=0.3.5` as a runtime dependency (`uv add "grafo>=0.3.5"`). No module imports grafo in this card; when it is imported later, only `orchestrate.py` may do so.
- `src/agent_manager/dag.py`:
  - New `@dataclass(frozen=True) class RootPlan` with `kind: Literal["base", "tip", "merged"]`, `branch: str` (the branch the story's first subtask builds on), `blockers: tuple[str, ...]` (in-milestone blockers, de-duplicated, in census order, i.e. the order they appear in `story.blocked_by` filtered to ids in `stories_by_id`).
  - New `base_branch_name(prefix, story) -> str` returning `f"{prefix}/base-{short_id(story.id)}"`.
  - `story_root(...)` returns a `RootPlan`: no in-milestone blockers gives `RootPlan("base", base_branch, ())`; exactly one gives `RootPlan("tip", <that blocker's story_tip>, (id,))`; two or more gives `RootPlan("merged", base_branch_name(prefix, story), (ids...))` and no longer raises `StackRootError`. The `seen`-based cycle backstop still raises `DependencyCycleError`.
  - `story_tip` (subtask-less fall-through) and `stack_bases` use `story_root(...).branch`; both still return plain strings / `dict[str, str]`.
  - `StackRootError` stays defined and importable (orchestrate raises it now).
- `src/agent_manager/cli.py` `dry_run_payload`: each story row's `"root"` is `RootPlan.branch`; when `kind == "merged"` the row gains `"merged_from": [blocker ids in census order]` (key absent otherwise). A merged root is not refused: the payload is returned and `am run --milestone ... --dry-run` exits 0 with `ok: true`. Cycles are still refused (`DependencyCycleError`).
- `src/agent_manager/orchestrate.py` `plan_levels`: a real run still refuses a merged root, as today, until Task 3.2. Because `dag` no longer raises, `plan_levels` must itself raise `dag.StackRootError` before anything is written when a planned story's root is `merged` — including the case where the root is reached through a subtask-less blocker whose own root is merged (today that also raised, via `story_tip` -> `story_root`). The message keeps today's shape: names the story `#<id>` and every in-milestone blocker `#<id>`, says a stack can only root on one parent branch, and suggests merging into `base_branch` or restructuring. Update the `plan_levels` docstring accordingly.

Out of scope (later subtasks / Task 3.2): `bases.py` merged-base builder, any grafo `Node`/`TreeExecutor` wiring, lane/supervisor code, dispatch behavior changes, StopSignal/checkpoint (364babde), `drive_subtask_async` (9b944409). `integration.py`'s `story_tip` use needs no change.

## Error paths

- Blocker cycle: `DependencyCycleError`, unchanged, from `assert_no_blocker_cycles` in both dry run and real run; `story_root`'s `seen` backstop still raises it.
- Two or more in-milestone blockers: dry run succeeds with `merged_from`; real run (`plan_levels` / `run_milestone`) raises `dag.StackRootError` naming both blockers, before any branch, worktree, run directory, or board write.
- Out-of-milestone blockers are ignored exactly as today (they neither count toward `merged` nor appear in `blockers`).

## Tests

Tier per the existing file docstrings (design §14): pure-function unit tier for `dag` functions, `dry_run_payload` and `plan_levels`; Steps tier (real temp git repo + real temp brd board, `@requires_git @requires_brd`) for the `am --dry-run` CLI test.

Test-helper note: `tests/test_dag.py`'s `_story(id, ...)` uses letter ids ("a", "b"), which `dag.short_id` rejects (needs 32 hex chars). Tests that hit `base_branch_name` must use UUID-shaped ids, e.g. a `_plan_id(n)`-style helper like the one in `tests/test_cli.py:840` / `tests/test_orchestrate.py:44`, or real UUIDs. There is no `story()` helper despite the plan's excerpt. This applies to the id of the BLOCKED story itself (the one whose `story_root` is asserted to be `merged`), not just to subtask ids: `base_branch_name` is computed from that story's own `id` even when a test only checks `.kind` and never reads `.branch`, so `_story("c", ["a", "b"], ...)` still raises inside `story_root`/`stack_bases` before any assertion runs. Every test below that puts a story into a `merged` root — the new `test_two_blockers_give_a_merged_root`, the rewritten `test_two_in_milestone_blockers_refuse_to_guess_a_root` (:463), and the rewritten `test_stack_bases_of_a_subtask_less_story_still_surfaces_a_root_error` (:517) — must give that blocked story a UUID-shaped id (a `_plan_id(n)`-style helper for story ids, distinct from `_gsub`'s subtask ids); its blockers' own ids do not need to change.

`tests/test_dag.py` (pure tier):

- New: `test_two_blockers_give_a_merged_root` — story C blocked by A and B (plus an outside id) gets `RootPlan(kind="merged", branch=f"{PREFIX}/base-{short_id(c.id)}", blockers=(a.id, b.id))`, blockers in census order.
- New: `base_branch_name` returns `<prefix>/base-<short id>`.
- New or updated: no blockers -> `kind="base"`, `branch=BASE`, `blockers=()`; one blocker -> `kind="tip"`, `branch` = blocker's tip, `blockers=(id,)`; a duplicated blocker is still one (`tip`).
- New: `stack_bases` of a two-blocker story roots its first subtask on the merged base branch.
- Rewrite `test_two_in_milestone_blockers_refuse_to_guess_a_root` (:463) into a merged-root assertion.
- Rewrite `test_stack_bases_of_a_subtask_less_story_still_surfaces_a_root_error` (:517): no longer raises; returns `{}` (a subtask-less story) and `story_root` for it is `merged`.
- Every existing `story_root(...)` comparison to a bare string compares `.branch` (or the full `RootPlan`).
- Existing cycle tests stay passing unchanged.

`tests/test_cli.py`:

- Pure tier: rewrite `test_the_dry_run_refuses_a_story_with_two_in_milestone_blockers` (:966) to assert the payload is returned, C's row has `root == f"m3/base-{short_id(c.id)}"` and `merged_from == [a.id, b.id]`, and rows with base/tip roots have no `merged_from` key. This test already builds story ids via `_plan_id`/`_plan_story`, so no id changes are needed here.
- Steps tier: rewrite `test_a_story_blocked_by_two_stories_is_an_envelope_naming_both` (:2645) into a success test: `am --dry-run` exits 0, `ok: true`, the joined story's row has `merged_from == [first, second]` and `root` naming its `base-` branch, and nothing is written (`_assert_nothing_written`, `_forbid_writes` kept). Real brd card ids from `_add_card` are already UUIDs, so no id changes are needed here either.

`tests/test_orchestrate.py` — must pass UNCHANGED (not edited by this card):

- `test_plan_levels_refuses_a_story_with_two_in_milestone_blockers` (:99, pure tier).
- `test_a_story_with_two_blockers_is_refused_before_anything_is_written` (~:1309, Steps tier, real `run_milestone`).

Whole default suite (`uv run pytest`, including `tests/e2e`) stays green.
