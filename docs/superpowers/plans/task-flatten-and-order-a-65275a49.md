<!-- task-pipeline: validated -->
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

---

# Flatten and Order a Milestone into a Census Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `order_siblings` and `flatten_milestone` (plus `CensusOrderError` and the `Census`/`StoryPlan`/`SubtaskPlan` frozen dataclasses) to the pure `census` module, ported one for one from `census.mjs`, and prove the composition against a real brd board.

**Architecture:** Both functions live in `src/agent_manager/census.py` next to the existing `find_milestone`. `order_siblings` is Kahn's algorithm over in-set `blocked_by` edges with a ready queue re-sorted by `(created_at or "", id)` after every pop; `flatten_milestone` applies it to the stories and to each story's subtasks and maps `"blocked"` to `"todo"`. The module stays pure (imports only `re`, `dataclasses` and `agent_manager.models`); the real-brd test lives in `tests/test_board.py`, which already owns the `temp_board` fixture.

**Tech Stack:** Python 3 (stdlib `dataclasses`), Pydantic `CardNode` from `agent_manager.models`, pytest, the `brd` CLI for the steps-tier test.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-flatten-and-order-a-65275a49/docs/superpowers/specs/task-flatten-and-order-a-65275a49-design.md` (prepended verbatim above). Behavioural source: `~/Code/leave-me-alone/plugins/leave-me-alone/scripts/census.mjs` and `census.test.mjs`.

**Worktree:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-flatten-and-order-a-65275a49`, branch `m3/task-flatten-and-order-a-65275a49`. All paths below are relative to it. Run every command from that directory. What already exists on this branch: `find_milestone` and `MilestoneNotFoundError` in `census.py` (card 20abebdf), `blocked_by`/`created_at` on `CardNode` and `board.roots` (card c1d53fc4). Nothing else from this milestone is assumed.

## Global Constraints

- `src/agent_manager/census.py` must not import `agent_manager.cli`, `subprocess` or `agent_manager.board` (the first two are enforced by `tests/test_census.py::test_census_imports_neither_cli_nor_subprocess`, which stays unchanged).
- Do not modify `src/agent_manager/models.py`, `src/agent_manager/board.py`, `src/agent_manager/dag.py`, `src/agent_manager/cli.py` or any driver/rollup/`run_milestone` code.
- Cycle message, verbatim: `census: N card(s) could not be ordered — cyclic blocked_by among siblings: <id>, <id>` (em dash `—`, ids joined by `", "`, stuck ids in input order).
- `CensusOrderError` subclasses `ValueError` so `cli.HANDLED` (`cli.py:760`) covers it.
- Ready-queue key: `(created_at or "", id)`, plain Python string ordering, re-sorted after every pop.
- `"blocked"` becomes `"todo"` only inside `flatten_milestone`; every other status passes through unchanged.
- `Census`, `StoryPlan`, `SubtaskPlan` are frozen dataclasses (not pydantic).
- No fake `claude` in any test.
- Verification: `uv run pytest` — the whole default suite, `tests/e2e` collection included, must stay green.

## Review Focus

- Re-sort after every pop: a card unlocked mid-walk that was created earlier than a card already waiting in the ready queue must come out first (a port that sorts once would get this wrong). Pinned in Task 1 by `test_ready_queue_is_re_sorted_after_every_pop`.
- `created_at` of `None` and identical `created_at` values: `None` sorts as `""` (first), and equal timestamps fall back to id order. Pinned in Task 1 by `test_missing_created_at_sorts_first_and_ties_break_on_id`.
- A self-block (card blocked by itself) is a cycle and raises; a duplicated blocker id in `blocked_by` still orders correctly rather than leaving the card stuck. Pinned in Task 1 by `test_self_block_is_a_cycle` and `test_duplicate_blocker_id_still_orders`.
- The cycle message counts and names only the stuck cards, in input order, and never drops an orderable sibling from the count. Pinned in Task 1 by `test_cycle_message_names_only_stuck_ids_in_input_order`.
- A cycle among one story's subtasks surfaces from `flatten_milestone` as `CensusOrderError` (a `ValueError`), not as a shortened subtask list; and a story's `blocked_by` in the census is not the same list object as the pydantic node's. Pinned in Task 2 by `test_subtask_cycle_propagates_from_flatten_milestone` and `test_story_blocked_by_is_a_copy_not_the_nodes_list`.

---

### Task 1: `order_siblings` and `CensusOrderError`

**Files:**
- Modify: `src/agent_manager/census.py:1-14` (module docstring), append after line 89 (end of file)
- Test: `tests/test_census.py:1-7` (module docstring), `tests/test_census.py:15` (import), append at end of file

**Interfaces:**
- Consumes: `agent_manager.models.CardNode` (fields `id: str`, `title: str`, `status: str`, `blocked_by: list[str]` default `[]`, `created_at: str | None` default `None`, `children: list[CardNode]`); test helpers `ID(n: int) -> str` and `node(n: int, title: str, **extra) -> CardNode` already in `tests/test_census.py:19-33`.
- Produces: `class CensusOrderError(ValueError)` and `def order_siblings(cards: list[CardNode] | None) -> list[CardNode]` in `agent_manager.census`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_census.py`, replace the module docstring (lines 1-7) with:

```python
"""Behaviour of the pure `census` functions, ported from `census.test.mjs`.

`find_milestone` is ported from `census.test.mjs:70-104`, `order_siblings`
from `census.test.mjs:12-49` and `flatten_milestone` from
`census.test.mjs:106-131`.

Tier: pure-function unit tests, per design §14 lines 479-493. `census` is a
pure module, so these tests build `CardNode` values in memory and call the
functions directly -- no `tmp_path`, no subprocess, no `brd`, no fake claude.
The one real-board census test lives in `tests/test_board.py`, next to the
`temp_board` fixture.
"""
```

Replace the import at line 15:

```python
from agent_manager.census import MilestoneNotFoundError, find_milestone
```

with:

```python
from agent_manager.census import (
    CensusOrderError,
    MilestoneNotFoundError,
    find_milestone,
    order_siblings,
)
```

Append at the end of the file:

```python
# --- order_siblings: census.test.mjs:12-49, one for one ------------------


def titles(cards: list[CardNode]) -> list[str]:
    return [card.title for card in cards]


def test_chain_runs_in_dependency_order_not_creation_order():
    # b was created first but is blocked by a: a must come first.
    b = node(2, "b", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(1)])
    a = node(1, "a", created_at="2026-01-02T00:00:00Z")
    assert titles(order_siblings([b, a])) == ["a", "b"]


def test_independent_siblings_keep_creation_order():
    a = node(1, "a", created_at="2026-01-02T00:00:00Z")
    b = node(2, "b", created_at="2026-01-01T00:00:00Z")
    assert titles(order_siblings([a, b])) == ["b", "a"]


def test_three_card_chain_resolves_fully():
    c = node(3, "c", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(2)])
    b = node(2, "b", created_at="2026-01-02T00:00:00Z", blocked_by=[ID(1)])
    a = node(1, "a", created_at="2026-01-03T00:00:00Z")
    assert titles(order_siblings([c, b, a])) == ["a", "b", "c"]


def test_edge_outside_sibling_set_does_not_order_siblings():
    # Blocked by a card in another story: irrelevant to ordering HERE.
    a = node(1, "a", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(9)])
    b = node(2, "b", created_at="2026-01-02T00:00:00Z")
    assert titles(order_siblings([a, b])) == ["a", "b"]


def test_empty_and_none_give_empty_list():
    assert order_siblings([]) == []
    assert order_siblings(None) == []


def test_cycle_raises_could_not_be_ordered():
    # Defensive: a cycle cannot be persisted by brd, but truncating the list
    # would silently remove subtasks from a milestone.
    a = node(1, "a", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(2)])
    b = node(2, "b", created_at="2026-01-02T00:00:00Z", blocked_by=[ID(1)])
    with pytest.raises(CensusOrderError, match="could not be ordered") as caught:
        order_siblings([a, b])
    assert isinstance(caught.value, ValueError)


# --- order_siblings: spec additions and review focus ----------------------


def test_census_order_error_is_a_value_error():
    # ValueError is already in cli.HANDLED; no cli.py change is needed.
    assert issubclass(CensusOrderError, ValueError)


def test_returns_the_same_card_objects_and_leaves_the_input_alone():
    b = node(2, "b", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(1)])
    a = node(1, "a", created_at="2026-01-02T00:00:00Z")
    given = [b, a]
    ordered = order_siblings(given)
    assert ordered[0] is a
    assert ordered[1] is b
    assert given == [b, a]
    assert given[0] is b


def test_ready_queue_is_re_sorted_after_every_pop():
    # a (oldest) and c (newest) start ready; b is unlocked by a. b was created
    # before c, so once it is ready it must jump ahead of c.
    a = node(1, "a", created_at="2026-01-01T00:00:00Z")
    b = node(2, "b", created_at="2026-01-02T00:00:00Z", blocked_by=[ID(1)])
    c = node(3, "c", created_at="2026-01-04T00:00:00Z")
    assert titles(order_siblings([c, b, a])) == ["a", "b", "c"]


def test_missing_created_at_sorts_first_and_ties_break_on_id():
    dated = node(1, "dated", created_at="2026-01-05T00:00:00Z")
    undated = node(2, "undated", created_at=None)
    assert titles(order_siblings([dated, undated])) == ["undated", "dated"]

    same = "2026-01-03T00:00:00Z"
    later_id = node(7, "seven", created_at=same)
    earlier_id = node(4, "four", created_at=same)
    assert titles(order_siblings([later_id, earlier_id])) == ["four", "seven"]


def test_self_block_is_a_cycle():
    a = node(1, "a", blocked_by=[ID(1)])
    with pytest.raises(CensusOrderError) as caught:
        order_siblings([a])
    assert str(caught.value) == (
        "census: 1 card(s) could not be ordered — "
        f"cyclic blocked_by among siblings: {ID(1)}"
    )


def test_duplicate_blocker_id_still_orders():
    b = node(2, "b", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(1), ID(1)])
    a = node(1, "a", created_at="2026-01-02T00:00:00Z")
    assert titles(order_siblings([b, a])) == ["a", "b"]


def test_cycle_message_names_only_stuck_ids_in_input_order():
    free = node(3, "free", created_at="2026-01-01T00:00:00Z")
    b = node(2, "b", created_at="2026-01-02T00:00:00Z", blocked_by=[ID(1)])
    a = node(1, "a", created_at="2026-01-03T00:00:00Z", blocked_by=[ID(2)])
    with pytest.raises(CensusOrderError) as caught:
        order_siblings([b, free, a])
    assert str(caught.value) == (
        "census: 2 card(s) could not be ordered — "
        f"cyclic blocked_by among siblings: {ID(2)}, {ID(1)}"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_census.py -v`
Expected: collection ERROR for the whole file with `ImportError: cannot import name 'CensusOrderError' from 'agent_manager.census'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/census.py`, replace the module docstring (lines 1-14) with:

```python
"""Find one milestone among the board's root cards, and order card siblings.

Both are ported exactly from the leave-me-alone plugin's `census.mjs`,
including their messages, so the two tools refuse in the same words.

`find_milestone` (`census.mjs:62-98`): `--milestone` used to be a small
integer. A UUID is not typeable, so a title substring is accepted too -- but
never guessed at: zero matches or two matches is an error naming what was on
the board, and the caller decides what to type next.

`order_siblings` (`census.mjs:12-49`): execution order for one card's
children comes from the `blocked_by` edges between the siblings themselves, so
the board states the order rather than encoding it in titles. Edges pointing
outside the sibling set are ignored -- a subtask blocked by another story's
card is a dispatch concern, not a sibling-ordering one. Independent siblings
keep creation order. A cycle is an error; cards are never silently dropped.

`MilestoneNotFoundError` and `CensusOrderError` subclass `ValueError` on
purpose: `ValueError` is already in `cli.HANDLED`, so a CLI caller turns them
into an `ok: false` envelope without this module importing `cli` (which will
import this module).

This module is pure: no I/O, no subprocesses, no ``brd``. It imports
`agent_manager.models` and nothing from `cli` or `board`.
"""
```

Append at the end of the file (after `find_milestone`):

```python
class CensusOrderError(ValueError):
    """Some siblings could not be ordered: their `blocked_by` edges form a cycle."""


def order_siblings(cards: list[CardNode] | None) -> list[CardNode]:
    """`cards` in execution order, by the `blocked_by` edges among them.

    Kahn's algorithm, as `census.mjs:12-49`. Only edges whose blocker is in
    `cards` count. Ready cards go earliest-created first (`created_at or ""`,
    then id), and the ready queue is re-sorted after every pop. The same
    `CardNode` objects come back, reordered; the input list is not touched.
    """
    pool = list(cards or [])
    if not pool:
        return []

    by_id = {card.id: card for card in pool}
    indegree = {card.id: 0 for card in pool}
    unlocks: dict[str, list[str]] = {card.id: [] for card in pool}

    for card in pool:
        for blocker_id in card.blocked_by:
            if blocker_id not in by_id:
                continue
            unlocks[blocker_id].append(card.id)
            indegree[card.id] += 1

    def earliest_first(card_id: str) -> tuple[str, str]:
        return (by_id[card_id].created_at or "", card_id)

    ready = sorted(
        (card.id for card in pool if indegree[card.id] == 0), key=earliest_first
    )
    ordered: list[CardNode] = []
    while ready:
        card_id = ready.pop(0)
        ordered.append(by_id[card_id])
        for unlocked in unlocks[card_id]:
            indegree[unlocked] -= 1
            if indegree[unlocked] == 0:
                ready.append(unlocked)
        ready.sort(key=earliest_first)

    if len(ordered) != len(pool):
        placed = {id(card) for card in ordered}
        stuck = [card.id for card in pool if id(card) not in placed]
        raise CensusOrderError(
            f"census: {len(stuck)} card(s) could not be ordered — "
            f"cyclic blocked_by among siblings: {', '.join(stuck)}"
        )
    return ordered
```

(`placed` compares object identity, as the JS `ordered.includes(card)` does; pydantic `==` would compare by value.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_census.py -v`
Expected: PASS for every test in the file, including the unchanged `test_census_imports_neither_cli_nor_subprocess` and all existing `find_milestone` tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all tests pass (brd-dependent ones may be skipped only if `brd` is not on PATH).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/census.py tests/test_census.py
git commit -m "feat(census): order siblings by blocked_by, port of census.mjs:12-49"
```

---

### Task 2: `flatten_milestone`, the census dataclasses, and the real-board test

**Files:**
- Modify: `src/agent_manager/census.py` (module docstring from Task 1, imports at the top, append after `order_siblings`)
- Test: `tests/test_census.py` (import block from Task 1, append at end of file)
- Test: `tests/test_board.py:16` (import), append after `test_roots_carries_a_cross_milestone_blocked_by_edge` (ends line 640), before `test_roots_outside_a_brd_project_raises_board_error`

**Interfaces:**
- Consumes: `order_siblings(cards: list[CardNode] | None) -> list[CardNode]` and `CensusOrderError` from Task 1; `find_milestone(roots: list[CardNode] | None, needle: str | int) -> CardNode` (existing); `board.roots(*, repo_dir: Path | None = None) -> list[models.CardNode]` and `board.set_status(card_id, status, *, repo_dir)` (existing); test helpers in `tests/test_board.py`: `temp_board` fixture, `_add_card(root, title, parent=None) -> str`, `_brd_json(root, *args) -> object`, `_node_by_id(nodes, card_id) -> CardNode`, `requires_brd`. The brd edge syntax is `brd block <blocked_id> --by <blocker_id>` (verified: already used at `tests/test_board.py:538, 575, 628`); the status writer is `board.set_status` (wraps `brd update <id> --status <status>`, `tests/test_board.py:32-40`). brd refuses to store `blocked` (`tests/test_board.py:469-477`), so `blocked` only ever appears as brd's derived status.
- Produces, in `agent_manager.census`:
  - `@dataclass(frozen=True) class SubtaskPlan: id: str; title: str; status: str`
  - `@dataclass(frozen=True) class StoryPlan: id: str; title: str; status: str; blocked_by: list[str]; subtasks: list[SubtaskPlan]`
  - `@dataclass(frozen=True) class Census: milestone_title: str; stories: list[StoryPlan]`
  - `def flatten_milestone(root: CardNode) -> Census`

- [ ] **Step 1: Write the failing unit tests**

In `tests/test_census.py`, replace the import block written in Task 1:

```python
from agent_manager.census import (
    CensusOrderError,
    MilestoneNotFoundError,
    find_milestone,
    order_siblings,
)
```

with:

```python
from agent_manager.census import (
    Census,
    CensusOrderError,
    MilestoneNotFoundError,
    StoryPlan,
    SubtaskPlan,
    find_milestone,
    flatten_milestone,
    order_siblings,
)
```

Append at the end of the file:

```python
# --- flatten_milestone: census.test.mjs:106-131, one for one -------------


def test_flatten_produces_stories_with_ordered_subtasks():
    plan = flatten_milestone(TREE)
    assert plan.milestone_title == "Milestone 12: CSV export"
    assert [story.title for story in plan.stories] == [
        "Story: CSV writer",
        "Story: Document it",
    ]
    assert [subtask.title for subtask in plan.stories[0].subtasks] == [
        "feat: write rows",
        "feat: quoting",
    ]


def test_story_blocked_by_carries_ids_through():
    assert flatten_milestone(TREE).stories[1].blocked_by == [ID(2)]


def test_derived_blocked_reads_as_todo():
    # brd projects blocked at read time; readiness is the orchestrator's DAG
    # walk, so blocked must not survive into the census as a distinct state.
    plan = flatten_milestone(TREE)
    assert plan.stories[1].status == "todo"
    assert plan.stories[1].subtasks[0].status == "todo"


def test_in_progress_and_done_pass_through():
    tree = node(
        1,
        "M",
        children=[
            node(2, "S", status="done", children=[node(3, "T", status="in_progress")])
        ],
    )
    story = flatten_milestone(tree).stories[0]
    assert story.status == "done"
    assert story.subtasks[0].status == "in_progress"


# --- flatten_milestone: spec additions and review focus -------------------


def test_flatten_builds_exactly_the_frozen_census_shape():
    assert flatten_milestone(TREE) == Census(
        milestone_title="Milestone 12: CSV export",
        stories=[
            StoryPlan(
                id=ID(2),
                title="Story: CSV writer",
                status="todo",
                blocked_by=[],
                subtasks=[
                    SubtaskPlan(id=ID(4), title="feat: write rows", status="todo"),
                    SubtaskPlan(id=ID(5), title="feat: quoting", status="todo"),
                ],
            ),
            StoryPlan(
                id=ID(3),
                title="Story: Document it",
                status="todo",
                blocked_by=[ID(2)],
                subtasks=[
                    SubtaskPlan(id=ID(6), title="docs: usage", status="todo"),
                ],
            ),
        ],
    )


def test_census_values_are_frozen():
    plan = flatten_milestone(TREE)
    with pytest.raises(AttributeError):
        plan.milestone_title = "other"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        plan.stories[0].status = "done"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        plan.stories[0].subtasks[0].title = "other"  # type: ignore[misc]


def test_stories_are_ordered_by_blocked_by_not_by_tree_order():
    later_story = node(
        2, "Story: second", created_at="2026-01-01T00:00:00Z", blocked_by=[ID(3)]
    )
    first_story = node(3, "Story: first", created_at="2026-01-02T00:00:00Z")
    tree = node(1, "M", children=[later_story, first_story])
    assert [story.id for story in flatten_milestone(tree).stories] == [ID(3), ID(2)]


def test_story_blocked_by_keeps_ids_outside_the_milestone():
    foreign = "ffffffff-0000-4000-8000-000000000000"
    tree = node(1, "M", children=[node(2, "S", blocked_by=[foreign])])
    assert flatten_milestone(tree).stories[0].blocked_by == [foreign]


def test_story_blocked_by_is_a_copy_not_the_nodes_list():
    story_node = TREE.children[1]
    plan = flatten_milestone(TREE)
    assert plan.stories[1].blocked_by == story_node.blocked_by
    assert plan.stories[1].blocked_by is not story_node.blocked_by


def test_other_statuses_pass_through_unchanged():
    tree = node(1, "M", children=[node(2, "S", status="review")])
    assert flatten_milestone(tree).stories[0].status == "review"


def test_milestone_with_no_stories_is_an_empty_census():
    plan = flatten_milestone(node(1, "Milestone 0: empty"))
    assert plan == Census(milestone_title="Milestone 0: empty", stories=[])


def test_subtask_cycle_propagates_from_flatten_milestone():
    tree = node(
        1,
        "M",
        children=[
            node(
                2,
                "S",
                children=[
                    node(4, "a", blocked_by=[ID(5)]),
                    node(5, "b", blocked_by=[ID(4)]),
                ],
            )
        ],
    )
    with pytest.raises(CensusOrderError, match="could not be ordered") as caught:
        flatten_milestone(tree)
    assert isinstance(caught.value, ValueError)


def test_story_cycle_propagates_from_flatten_milestone():
    tree = node(
        1,
        "M",
        children=[
            node(2, "S1", blocked_by=[ID(3)]),
            node(3, "S2", blocked_by=[ID(2)]),
        ],
    )
    with pytest.raises(CensusOrderError, match="could not be ordered"):
        flatten_milestone(tree)
```

- [ ] **Step 2: Write the failing real-board test**

In `tests/test_board.py`, replace line 16:

```python
from agent_manager import board, models
```

with:

```python
from agent_manager import board, census, models
```

(Importing the module, not the names, keeps the rest of `test_board.py` collectable while `flatten_milestone` does not exist yet: only this one test fails with `AttributeError`.)

Insert this test after `test_roots_carries_a_cross_milestone_blocked_by_edge` (which ends at line 640) and before `test_roots_outside_a_brd_project_raises_board_error`:

```python
@requires_brd
def test_census_from_a_real_board(temp_board):
    # Steps tier (design §14): board.roots -> find_milestone -> flatten_milestone
    # over real `brd tree` output. Every dependent card is created BEFORE its
    # blocker, so creation order alone would never put it second; only the
    # blocked_by edge can.
    milestone = _add_card(temp_board, "Milestone 7: census")
    docs_story = _add_card(temp_board, "Story: document it", milestone)
    writer_story = _add_card(temp_board, "Story: CSV writer", milestone)
    quoting = _add_card(temp_board, "feat: quoting", writer_story)
    rows = _add_card(temp_board, "feat: write rows", writer_story)
    examples = _add_card(temp_board, "docs: examples", docs_story)
    usage = _add_card(temp_board, "docs: usage", docs_story)
    _brd_json(temp_board, "block", docs_story, "--by", writer_story)
    _brd_json(temp_board, "block", quoting, "--by", rows)
    _brd_json(temp_board, "block", examples, "--by", usage)
    board.set_status(writer_story, "in_progress", repo_dir=temp_board)
    board.set_status(rows, "done", repo_dir=temp_board)

    roots = board.roots(repo_dir=temp_board)

    # Preconditions on brd's own output, so the assertions below mean something.
    assert (
        _node_by_id(roots, docs_story).created_at
        <= _node_by_id(roots, writer_story).created_at
    )
    assert _node_by_id(roots, docs_story).status == "blocked"
    assert _node_by_id(roots, examples).status == "blocked"

    plan = census.flatten_milestone(census.find_milestone(roots, "census"))

    assert plan.milestone_title == "Milestone 7: census"
    assert [story.id for story in plan.stories] == [writer_story, docs_story]
    writer, docs = plan.stories
    assert writer.title == "Story: CSV writer"
    assert [subtask.id for subtask in writer.subtasks] == [rows, quoting]
    assert [subtask.id for subtask in docs.subtasks] == [usage, examples]

    assert writer.blocked_by == []
    assert docs.blocked_by == [writer_story]

    # brd's derived `blocked` reads as `todo`; stored statuses pass through.
    assert docs.status == "todo"
    assert [subtask.status for subtask in docs.subtasks] == ["todo", "todo"]
    assert writer.status == "in_progress"
    assert [subtask.status for subtask in writer.subtasks] == ["done", "todo"]
    every_status = [story.status for story in plan.stories] + [
        subtask.status for story in plan.stories for subtask in story.subtasks
    ]
    assert "blocked" not in every_status
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_census.py tests/test_board.py::test_census_from_a_real_board -v`
Expected: `tests/test_census.py` errors at collection with `ImportError: cannot import name 'Census' from 'agent_manager.census'`; `test_census_from_a_real_board` FAILS with `AttributeError: module 'agent_manager.census' has no attribute 'flatten_milestone'` (it reports SKIPPED instead only if `brd` is not on PATH — install brd before continuing, since this test is the card's end-to-end proof).

- [ ] **Step 4: Write the minimal implementation**

In `src/agent_manager/census.py`, replace the module docstring written in Task 1 with the final version:

```python
"""Read one milestone off the board's root cards and flatten it into a census.

Three pure functions, each ported exactly from the leave-me-alone plugin's
`census.mjs`, including its messages, so the two tools refuse in the same
words.

`find_milestone` (`census.mjs:62-98`): `--milestone` used to be a small
integer. A UUID is not typeable, so a title substring is accepted too -- but
never guessed at: zero matches or two matches is an error naming what was on
the board, and the caller decides what to type next.

`order_siblings` (`census.mjs:12-49`): execution order for one card's
children comes from the `blocked_by` edges between the siblings themselves, so
the board states the order rather than encoding it in titles. Edges pointing
outside the sibling set are ignored -- a subtask blocked by another story's
card is a dispatch concern, not a sibling-ordering one. Independent siblings
keep creation order. A cycle is an error; cards are never silently dropped.

`flatten_milestone` (`census.mjs:51-58` and `census.mjs:100-113`): the
milestone's tree becomes ordered stories, each with ordered subtasks. brd
derives `blocked` at read time, while the orchestrator decides readiness with
its own DAG walk, so `blocked` is read as `todo` here, in one place, rather
than checked for everywhere.

`MilestoneNotFoundError` and `CensusOrderError` subclass `ValueError` on
purpose: `ValueError` is already in `cli.HANDLED`, so a CLI caller turns them
into an `ok: false` envelope without this module importing `cli` (which will
import this module).

This module is pure: no I/O, no subprocesses, no ``brd``. It imports
`agent_manager.models` and nothing from `cli` or `board`.
"""
```

Replace the import block:

```python
import re

from agent_manager.models import CardNode
```

with:

```python
import re
from dataclasses import dataclass

from agent_manager.models import CardNode
```

Append at the end of the file (after `order_siblings`):

```python
@dataclass(frozen=True)
class SubtaskPlan:
    """One subtask of a story, as the census reports it."""

    id: str
    title: str
    status: str


@dataclass(frozen=True)
class StoryPlan:
    """One story of the milestone, with its subtasks in execution order."""

    id: str
    title: str
    status: str
    blocked_by: list[str]
    subtasks: list[SubtaskPlan]


@dataclass(frozen=True)
class Census:
    """A milestone flattened: its title and its stories in execution order."""

    milestone_title: str
    stories: list[StoryPlan]


def _stored_status(status: str) -> str:
    """brd's derived `blocked` read as `todo` (`census.mjs:51-58`)."""
    return "todo" if status == "blocked" else status


def flatten_milestone(root: CardNode) -> Census:
    """`root`'s stories and each story's subtasks, ordered by `order_siblings`.

    Port of `census.mjs:100-113`. A story's `blocked_by` is copied through
    unchanged, ids outside the milestone included. A cycle among the stories
    or among one story's subtasks raises `CensusOrderError`.
    """
    stories = [
        StoryPlan(
            id=story.id,
            title=story.title,
            status=_stored_status(story.status),
            blocked_by=list(story.blocked_by),
            subtasks=[
                SubtaskPlan(
                    id=subtask.id,
                    title=subtask.title,
                    status=_stored_status(subtask.status),
                )
                for subtask in order_siblings(story.children)
            ],
        )
        for story in order_siblings(root.children)
    ]
    return Census(milestone_title=root.title, stories=stories)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_census.py tests/test_board.py::test_census_from_a_real_board -v`
Expected: PASS for every test (the real-board test PASSES, not SKIPPED).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all tests pass, `tests/e2e` collection included; `test_census_imports_neither_cli_nor_subprocess` still passes unchanged.

- [ ] **Step 7: Confirm census.py stays pure**

Run: `grep -nE "^(import|from) " src/agent_manager/census.py`
Expected: exactly three lines — `import re`, `from dataclasses import dataclass`, `from agent_manager.models import CardNode`. No `board`, `cli` or `subprocess`.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/census.py tests/test_census.py tests/test_board.py
git commit -m "feat(census): flatten a milestone into ordered stories and subtasks"
```
