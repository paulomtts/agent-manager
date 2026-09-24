# Flatten and order a milestone into a census (card 65275a49)

Parent story: b84d47e1 "Census: read a whole milestone from the board". Narrows addendum O1 (`docs/superpowers/specs/2026-09-24-orchestration-design.md:26-42`) to its last two pure functions. Siblings c1d53fc4 (`blocked_by`/`created_at` on `Card`/`CardNode`, `board.roots`) and 20abebdf (`find_milestone`) are done. This card builds on them and does not change them.

## Scope

All new code goes in `src/agent_manager/census.py`, which stays pure. It may import `agent_manager.models`. It must not import `agent_manager.cli`, `subprocess` or `agent_manager.board`. The existing AST test at `tests/test_census.py:148` enforces the first two.

1. `order_siblings(cards: list[CardNode] | None) -> list[CardNode]`, ported from `census.mjs:12-49`.
2. `flatten_milestone(root: CardNode) -> Census`, ported from `census.mjs:51-58` (`storedStatus`) and `census.mjs:100-113`.
3. Three frozen dataclasses. They are internal-only state and never cross a process boundary, so per CLAUDE.md they need not be pydantic:
   - `Census(milestone_title: str, stories: list[StoryPlan])`
   - `StoryPlan(id: str, title: str, status: str, blocked_by: list[str], subtasks: list[SubtaskPlan])`
   - `SubtaskPlan(id: str, title: str, status: str)`
4. One new error class, `CensusOrderError(ValueError)`. Subclassing `ValueError` lets `cli.HANDLED` (`cli.py:760`) wrap it without census importing cli. This follows the same reasoning as `MilestoneNotFoundError`.
5. Extend the module docstring so it covers all three functions and cites the `census.mjs` line ranges above.

Out of scope: `models.py` (owned by c1d53fc4), `board.py`, `dag.py` geometry (O2), the driver (O4), rollup (O5), `run_milestone` (O6), CLI wiring, and everything in addendum section 4.

## Observable behaviour

### `order_siblings`

- Empty input and `None` both return `[]`.
- It runs Kahn's algorithm over `blocked_by` edges. Only edges whose blocker id is in the input set count. An edge to a card outside the set is skipped silently.
- The ready queue is sorted by `(created_at or "", id)` using plain Python string ordering. That matches the JS `localeCompare` for ISO timestamps and hex ids. The queue is sorted at the start and again after every pop, exactly as the JS does.
- It returns the same `CardNode` objects, reordered. No card is ever dropped.
- If a cycle leaves any cards unordered, it raises `CensusOrderError` with the message `census: N card(s) could not be ordered — cyclic blocked_by among siblings: <id>, <id>`. The stuck ids appear in input order. The phrase "could not be ordered" must stay verbatim.

### `flatten_milestone`

- `milestone_title` is `root.title`.
- Stories are `order_siblings(root.children)`. Each story's subtasks are `order_siblings(story.children)`.
- A story's `blocked_by` is copied through unchanged, including ids outside the milestone.
- The status `"blocked"` becomes `"todo"`, and this is the only place that happens. Every other status passes through unchanged, including `in_progress` and `done`.
- A cycle among the stories or among one story's subtasks propagates as `CensusOrderError`.

## Tests

The test-placement rule is design spec §14 (`2026-09-23-agent-manager-design.md:479-493`):
- Pure functions get unit tests ported from the `.test.mjs` files.
- Tests against a real temporary brd board belong to the steps tier.

The engine tier and the opt-in `-m e2e` tier do not apply here, and no test needs a fake `claude`.

### Pure unit tier: `tests/test_census.py`

Port `census.test.mjs` one for one. Build inputs with the existing `ID(n)`/`node(n, title, **extra)` helpers and the existing `TREE` fixture, since `node` already accepts `blocked_by` and `created_at`. Update the module docstring so it no longer says only `find_milestone` is covered.

`order_siblings`, ported from `census.test.mjs:12-49`:
- `test_chain_runs_in_dependency_order_not_creation_order`
- `test_independent_siblings_keep_creation_order`
- `test_three_card_chain_resolves_fully`
- `test_edge_outside_sibling_set_does_not_order_siblings`
- `test_empty_and_none_give_empty_list`
- `test_cycle_raises_could_not_be_ordered`: uses `pytest.raises(CensusOrderError, match="could not be ordered")`, and also asserts the error is a `ValueError`.

`flatten_milestone`, ported from `census.test.mjs:106-131` using `TREE`:
- `test_flatten_produces_stories_with_ordered_subtasks`
- `test_story_blocked_by_carries_ids_through`
- `test_derived_blocked_reads_as_todo`: covers both the story and the subtask.
- `test_in_progress_and_done_pass_through`

Do not re-port the `find_milestone` cases. The purity test at line 148 stays as it is.

### Steps tier, real brd board: `tests/test_board.py`

Put this test in `tests/test_board.py` next to the `temp_board` fixture, the `_add_card`/`_brd_json` helpers and the `requires_brd` marker (lines ~237-275). No shared conftest exists, so this avoids moving the fixture.

- `test_census_from_a_real_board`, marked `requires_brd`. It does the following:
  - Creates a milestone with two stories on `temp_board`. The second story is blocked by the first, and the second story was created first. Each story has at least two subtasks, one blocked by the other, again with the dependent one created first.
  - Sets at least one card to `in_progress` or `done`.
  - Calls `board.roots(repo_dir=temp_board)`, then `find_milestone(roots, <title or id>)`, then `flatten_milestone`.
  - Asserts the following:
    - story and subtask order follows dependencies, not creation order
    - the dependent story's `blocked_by` contains the first story's id
    - the statuses that brd derives as `blocked` read as `todo`
    - `in_progress`/`done` pass through

  Find the brd syntax for creating a `blocked_by` edge and setting a status (`brd --help`, `brd block --help` or similar) when writing the test. Do not assume it.

The whole default suite (`uv run pytest`, including `tests/e2e` collection) must stay green.
