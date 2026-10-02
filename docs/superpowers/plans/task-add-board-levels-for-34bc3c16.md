<!-- task-pipeline: validated -->
# Subtask 34bc3c16 — Add `board_levels` for the board's dependency ordering

Parent story: 488d9bf4 ("am run --board drives every open milestone as one DAG"). Source design: `docs/superpowers/specs/2026-10-01-run-board-design.md` §3.3 (function) and §3.8 (tests). This document narrows that design to this one subtask; it adds no new scope.

## Scope

Add one pure function and its unit tests:

```python
def board_levels(roots: list[CardNode]) -> list[list[CardNode]]
```

- Location: `src/agent_manager/dag.py`, next to `topological_levels` (dag.py:143-166), which it structurally mirrors. Placing it in `census.py` instead is acceptable if the implementer finds that module's conventions fit better; `dag.py` is the default because the cycle error and the leveling engine already live there.
- `CardNode` is the existing pydantic model at `src/agent_manager/models.py:200-215`; it is imported, not modified.
- The module stays pure: no I/O, no subprocess, no `brd` calls (an existing invariant stated in dag.py's module docstring).

Out of scope (owned by sibling subtasks, do not touch):
- baef4f94 "Add orchestrate.run_board": milestone-claims computation, shared `asyncio.Semaphore`, `build_dag_tree` wiring, board payload assembly, e2e tests.
- d78b3118 "Wire am run --board into the CLI": the `--board` flag, mutual-exclusion refusals, `--branch-prefix` derivation, `--dry-run --board` output.
- `board_levels` is not wired into dispatch or anything else by this card. Per §3.3/§3.4 it is used only for `--dry-run --board` display and for computing the claim set up front; execution order is the grafo tree's job.

## Observable behavior

1. Pending filter (mirrors `dag.compute_levels`, dag.py:169-183). A root is kept only if it still has work: its own `status` is not `done` (case-insensitive, same test as `is_story_closed`) and it has at least one descendant in `children` (at any depth) whose status is not `done`. Roots failing this are dropped before leveling; their ids then sit outside the root set, so cards they blocked land in level 0, exactly as a dropped story does in `compute_levels`. Implementer note: §3.3's wording ("not `done`, or with a remaining milestone to run") is read as this conjunction because it is explicitly "mirroring `dag.compute_levels`'s pending filter"; if the implementer finds a clearer reading in §3.3's context, the mirror of `compute_levels` wins.
2. Leveling. Kept roots are grouped into levels by their own `blocked_by` edges. A root is ready once every id in its `blocked_by` is either outside the kept root set (ignored) or already placed in an earlier level — the same `dep not in ids or dep in placed` rule as `topological_levels`.
3. Order. Each level preserves input order; nothing is re-sorted (card ids are opaque). The same `CardNode` objects are returned; the input list is not mutated.
4. Empty input, or input where every root is filtered out, returns `[]`.

## Error paths

- A cycle among kept roots raises `dag.DependencyCycleError` (dag.py:134-140) — the existing class, not a new one. The message is the same shape as `topological_levels`'s, reworded for milestones, naming every unplaced root, e.g. `dag: dependency cycle among milestones #<id>, #<id>`. Because it subclasses `ValueError` (already in `cli.HANDLED`), no CLI changes are needed.
- No other errors are raised; unknown or external `blocked_by` ids are silently ignored.

## Tests

All tests go in `tests/test_dag.py`, unmarked, in the default suite. `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 already puts `dag.py`'s pure functions in unit tests with no marker, and `pyproject.toml`'s only registered marker is `e2e` (opt-in) — nothing here needs a new marker. This matches the existing unmarked `topological_levels` tests (tests/test_dag.py ~219-349). Each builds `CardNode`s in memory; none touches git, brd, or fake-claude.

Note: a separate, not-yet-merged proposal (`docs/superpowers/specs/2026-10-02-test-tier-design.md`, status "proposed", no milestone scope assigned) would later introduce a named `unit` tier for this same rule. That file is not present on this branch; if it lands first, `board_levels`'s tests need no change — they already satisfy its stated criteria for the `unit` tier (pure function, no subprocess) — but do not cite it or add its marker until it actually exists in this worktree.

| Test | Marker |
|---|---|
| Independent open roots each sit in level 0, in input order (mirrors `test_independent_roots_share_level_zero_in_input_order`) | none (default suite) |
| A `blocked_by` chain A <- B <- C yields one root per level in chain order (mirrors `test_linear_chain_is_one_story_per_level`) | none (default suite) |
| A two-root cycle raises `DependencyCycleError` whose message names both ids and says "milestones" (mirrors `test_a_two_story_cycle_stops_the_level_engine_naming_both`) | none (default suite) |
| A `blocked_by` edge to an id outside the root set is ignored; the root lands in level 0 (mirrors `test_an_external_blocker_is_ignored_by_the_level_engine`) | none (default suite) |
| A `done` root (and a root with no not-done descendants) is dropped, and a root it blocked lands in level 0 | none (default suite) |

Verification: `uv run pytest` (no separate lint or typecheck command).

---

# `board_levels` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the pure function `dag.board_levels(roots: list[CardNode]) -> list[list[CardNode]]` that drops roots with no work left and groups the rest into dependency levels by their `blocked_by` edges, with unit tests in `tests/test_dag.py`.

**Architecture:** `board_levels` lives in `src/agent_manager/dag.py` directly after `compute_integrate_levels`, structurally mirroring `topological_levels` (same `ids` / `placed` / `rest` loop, same `dep not in ids or dep in placed` rule) but over `models.CardNode`. A private recursive helper `_has_open_descendant` implements the pending filter, mirroring `compute_levels`'s `not is_story_closed(story) and remaining_subtasks(story)`. A cycle raises the existing `DependencyCycleError` with the message `dag: dependency cycle among milestones #a, #b`. No I/O, nothing wired into dispatch or the CLI.

**Tech Stack:** Python 3, Pydantic (`CardNode`), pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-add-board-levels-for-34bc3c16/docs/superpowers/specs/task-add-board-levels-for-34bc3c16-design.md` (prepended above), narrowing `docs/superpowers/specs/2026-10-01-run-board-design.md` §3.3 and §3.8.

## Global Constraints

- `dag.py` stays pure: no I/O, no subprocess, no `brd` calls.
- Do not define a new exception class; reuse `dag.DependencyCycleError`.
- Cycle message shape: `dag: dependency cycle among milestones #<id>, #<id>` (unplaced roots, in input order, `", "`-joined, each prefixed `#`).
- Do not modify `CardNode` (`src/agent_manager/models.py:200-215`); import it only.
- Do not touch `orchestrate.py`, `cli.py`, or any e2e test: those belong to sibling cards baef4f94 and d78b3118. Do not assume their code exists on this branch.
- Tests go in `tests/test_dag.py`, with no pytest marker (default unit suite). Do not add markers or new test files.
- Done-ness is `(status or "").lower() == "done"`, case-insensitive, the same test as `is_story_closed`.
- Verification: `uv run pytest`.

## Review Focus

- Status case: a root or descendant whose status is `"DONE"` or `"Done"` must count as done, the same way `is_story_closed` treats it. Pinned by `test_board_levels_reads_done_case_insensitively` (Task 2).
- Deep descendants: a milestone whose direct children are all `done` but which still has a not-done grandchild still has work and must be kept. Pinned by `test_board_levels_keeps_a_root_whose_only_open_work_is_a_grandchild` (Task 2).
- Self-blocking root: a root that lists its own id in `blocked_by` is a one-node cycle and must raise, not loop forever. Pinned by `test_a_self_blocking_milestone_stops_board_levels` (Task 1).
- Partial cycle: when a cycle sits downstream of a placeable root, the error names only the unplaced roots, not the root that was already placed. Pinned by `test_board_levels_names_only_the_unplaced_milestones_of_a_cycle` (Task 1).
- Identity and immutability: callers (baef4f94's claim computation) get back the same `CardNode` objects, and the input list is not changed. Pinned by `test_board_levels_returns_the_same_objects_and_leaves_the_input_alone` (Task 1).

---

## File Structure

- Modify: `src/agent_manager/dag.py` — add `from agent_manager.models import CardNode` import, a docstring line naming `board_levels`, the private helper `_has_open_descendant`, and `board_levels` after `compute_integrate_levels` (currently ending at line 193).
- Modify: `tests/test_dag.py` — add `board_levels` to the `agent_manager.dag` import block (lines 6-27), add `from agent_manager.models import CardNode`, and append a `board_levels` section (helper `_root`, `_node_ids`, and tests) at the end of the file (after line 631).

`models.py` imports nothing from `agent_manager`, so `dag.py` importing `CardNode` creates no import cycle (`census.py` already does the same at its line 37).

---

### Task 1: Level open roots by their `blocked_by` edges

**Files:**
- Modify: `src/agent_manager/dag.py:1-36` (docstring and imports), insert after `:193` (end of `compute_integrate_levels`)
- Test: `tests/test_dag.py:5-27` (imports), append after `:631`

**Interfaces:**
- Consumes: `DependencyCycleError` (dag.py:134), `CardNode` (models.py:200) with fields `id: str`, `status: str`, `blocked_by: list[str]`, `children: list[CardNode]`.
- Produces: `board_levels(roots: list[CardNode]) -> list[list[CardNode]]` in `agent_manager.dag`. Task 2 extends it with the pending filter without changing the signature. Test helpers `_root(id, blocked_by=None, status="todo", children=None) -> CardNode` and `_node_ids(levels) -> list[list[str]]` in `tests/test_dag.py`, reused by Task 2.

- [ ] **Step 1: Add the imports to the test module**

In `tests/test_dag.py`, change the import block at lines 5-27 so `board_levels` is imported and `CardNode` is available. The block becomes:

```python
from agent_manager.census import StoryPlan, SubtaskPlan
from agent_manager.dag import (
    DependencyCycleError,
    RootPlan,
    StackRootError,
    assert_no_blocker_cycles,
    base_branch_name,
    board_levels,
    compute_integrate_levels,
    compute_levels,
    is_story_closed,
    is_subtask_done,
    ref_matches_card,
    remaining_subtasks,
    short_id,
    slugify,
    stack_bases,
    story_root,
    story_tip,
    subtask_branch,
    task_branch,
    task_stem,
    topological_levels,
)
from agent_manager.models import CardNode
```

- [ ] **Step 2: Write the failing leveling tests**

Append to the end of `tests/test_dag.py`:

```python


# ── board levels ────────────────────────────────────────────────────────────


def _root(
    id: str,
    blocked_by: list[str] | None = None,
    status: str = "todo",
    children: list[CardNode] | None = None,
) -> CardNode:
    """A milestone root; by default open with one todo story, so it has work."""
    return CardNode(
        id=id,
        title=f"milestone {id}",
        status=status,
        blocked_by=list(blocked_by or []),
        children=[CardNode(id=f"{id}-story", title="story", status="todo")]
        if children is None
        else children,
    )


def _node_ids(levels: list[list[CardNode]]) -> list[list[str]]:
    return [[node.id for node in level] for level in levels]


def test_independent_milestones_share_level_zero_in_input_order():
    assert _node_ids(board_levels([_root("y"), _root("x")])) == [["y", "x"]]


def test_a_milestone_chain_is_one_milestone_per_level():
    roots = [_root("a"), _root("b", ["a"]), _root("c", ["b"])]
    assert _node_ids(board_levels(roots)) == [["a"], ["b"], ["c"]]


def test_board_levels_keeps_input_order_not_id_order_in_a_shared_level():
    roots = [_root("a"), _root("c", ["a"]), _root("b", ["a"]), _root("d", ["b", "c"])]
    assert _node_ids(board_levels(roots)) == [["a"], ["c", "b"], ["d"]]


def test_an_external_blocker_is_ignored_by_board_levels():
    roots = [_root("a", ["not-on-this-board"]), _root("b", ["a"])]
    assert _node_ids(board_levels(roots)) == [["a"], ["b"]]


def test_a_duplicated_blocker_does_not_hold_a_milestone_back():
    roots = [_root("a"), _root("b", ["a", "a"])]
    assert _node_ids(board_levels(roots)) == [["a"], ["b"]]


def test_board_levels_returns_the_same_objects_and_leaves_the_input_alone():
    roots = [_root("a"), _root("b", ["a"]), _root("c", ["a"])]
    before = list(roots)
    levels = board_levels(roots)
    assert roots == before
    assert [id(node) for level in levels for node in level] == [id(r) for r in roots]


def test_board_levels_of_no_roots_is_empty():
    assert board_levels([]) == []


def test_a_two_milestone_cycle_stops_board_levels_naming_both():
    roots = [_root("a", ["b"]), _root("b", ["a"])]
    with pytest.raises(DependencyCycleError, match="dependency cycle among milestones #a, #b"):
        board_levels(roots)


def test_board_levels_names_only_the_unplaced_milestones_of_a_cycle():
    roots = [_root("root"), _root("a", ["root", "b"]), _root("b", ["a"])]
    with pytest.raises(DependencyCycleError) as caught:
        board_levels(roots)
    message = str(caught.value)
    assert "#a, #b" in message
    assert "#root" not in message


def test_a_self_blocking_milestone_stops_board_levels():
    with pytest.raises(DependencyCycleError, match="milestones #a"):
        board_levels([_root("a", ["a"])])
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v`
Expected: collection ERROR for `tests/test_dag.py` with `ImportError: cannot import name 'board_levels' from 'agent_manager.dag'`.

- [ ] **Step 4: Import `CardNode` in `dag.py` and name the function in the module docstring**

In `src/agent_manager/dag.py`, replace lines 24-26 of the module docstring:

```python
derived from the census and never discovered. All of them read
``census.StoryPlan`` and ``census.SubtaskPlan`` by attribute, keep census
order, and ignore blockers outside the milestone.
```

with:

```python
derived from the census and never discovered. All of them read
``census.StoryPlan`` and ``census.SubtaskPlan`` by attribute, keep census
order, and ignore blockers outside the milestone. ``board_levels`` is the
same leveling one level up: open milestone roots (``models.CardNode``)
grouped by their own ``blocked_by`` edges, for display and claims only.
```

Then replace the import line at line 36:

```python
from agent_manager.census import StoryPlan, SubtaskPlan
```

with:

```python
from agent_manager.census import StoryPlan, SubtaskPlan
from agent_manager.models import CardNode
```

- [ ] **Step 5: Write the minimal `board_levels` implementation**

In `src/agent_manager/dag.py`, insert directly after `compute_integrate_levels` (after its `return topological_levels(stories)` line) and before the `# ── cycle detection` comment:

```python


def board_levels(roots: list[CardNode]) -> list[list[CardNode]]:
    """Open milestone roots grouped into dependency levels, each in input order.

    The board-wide twin of ``topological_levels``: a root is ready once every
    blocker is either outside ``roots`` (ignored: it is not this board's to
    order) or already placed. The same ``CardNode`` objects come back; the
    input list is not touched. Used only for display and for computing the
    claim set up front; it never drives execution order.
    """
    ids = {root.id for root in roots}
    placed: set[str] = set()
    levels: list[list[CardNode]] = []
    rest = list(roots)
    while rest:
        ready = [
            root
            for root in rest
            if all(dep not in ids or dep in placed for dep in root.blocked_by or [])
        ]
        if not ready:
            listed = ", ".join(f"#{root.id}" for root in rest)
            raise DependencyCycleError(f"dag: dependency cycle among milestones {listed}")
        levels.append(ready)
        placed.update(root.id for root in ready)
        rest = [root for root in rest if root.id not in placed]
    return levels
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS, including the ten new `board_levels` tests and every pre-existing test in the file.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "Add dag.board_levels to level open milestone roots by blocked_by"
```

---

### Task 2: Drop roots that have no work left

**Files:**
- Modify: `src/agent_manager/dag.py` (the `board_levels` function added in Task 1, plus a new private helper directly above it)
- Test: `tests/test_dag.py` (append after the Task 1 tests)

**Interfaces:**
- Consumes: `board_levels(roots: list[CardNode]) -> list[list[CardNode]]` from Task 1; test helpers `_root(id, blocked_by=None, status="todo", children=None) -> CardNode` and `_node_ids(levels) -> list[list[str]]` from Task 1.
- Produces: `_has_open_descendant(node: CardNode) -> bool` (private, `dag.py`); `board_levels` now filters before leveling. Signature unchanged, so sibling card baef4f94 consumes exactly `board_levels(roots: list[CardNode]) -> list[list[CardNode]]`.

- [ ] **Step 1: Write the failing filter tests**

Append to the end of `tests/test_dag.py`:

```python


def _card(id: str, status: str = "todo", children: list[CardNode] | None = None) -> CardNode:
    return CardNode(id=id, title=f"card {id}", status=status, children=list(children or []))


def test_a_done_milestone_is_dropped_and_what_it_blocked_moves_to_level_zero():
    roots = [_root("a", status="done"), _root("b", ["a"])]
    assert _node_ids(board_levels(roots)) == [["b"]]


def test_an_open_milestone_with_only_done_descendants_is_dropped():
    roots = [
        _root("a", children=[_card("a1", "done", [_card("a1x", "done")]), _card("a2", "done")]),
        _root("b", ["a"]),
    ]
    assert _node_ids(board_levels(roots)) == [["b"]]


def test_an_open_milestone_with_no_children_is_dropped():
    roots = [_root("a", children=[]), _root("b")]
    assert _node_ids(board_levels(roots)) == [["b"]]


def test_board_levels_keeps_a_root_whose_only_open_work_is_a_grandchild():
    roots = [_root("a", children=[_card("a1", "done", [_card("a1x", "todo")])])]
    assert _node_ids(board_levels(roots)) == [["a"]]


def test_board_levels_reads_done_case_insensitively():
    roots = [
        _root("a", status="DONE"),
        _root("b", children=[_card("b1", "Done")]),
        _root("c", ["a", "b"]),
    ]
    assert _node_ids(board_levels(roots)) == [["c"]]


def test_board_levels_of_only_finished_milestones_is_empty():
    roots = [_root("a", status="done"), _root("b", children=[])]
    assert board_levels(roots) == []


def test_a_cycle_through_a_done_milestone_does_not_stop_board_levels():
    roots = [_root("a", ["b"], status="done"), _root("b", ["a"])]
    assert _node_ids(board_levels(roots)) == [["b"]]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dag.py -v -k "board_levels or milestone"`
Expected: FAIL for `test_a_done_milestone_is_dropped_and_what_it_blocked_moves_to_level_zero` (got `[["a"], ["b"]]`), `test_an_open_milestone_with_only_done_descendants_is_dropped`, `test_an_open_milestone_with_no_children_is_dropped`, `test_board_levels_reads_done_case_insensitively`, `test_board_levels_of_only_finished_milestones_is_empty`, and `test_a_cycle_through_a_done_milestone_does_not_stop_board_levels` (raises `DependencyCycleError`). `test_board_levels_keeps_a_root_whose_only_open_work_is_a_grandchild` already passes, and so do all Task 1 tests.

- [ ] **Step 3: Add the pending filter**

In `src/agent_manager/dag.py`, insert this helper directly above `def board_levels`:

```python
def _has_open_descendant(node: CardNode) -> bool:
    """True when any card nested under ``node``, at any depth, is not ``done``."""
    return any(
        (child.status or "").lower() != "done" or _has_open_descendant(child)
        for child in node.children
    )


```

Then, in `board_levels`, replace the docstring and the first line of the body:

```python
    """Open milestone roots grouped into dependency levels, each in input order.

    The board-wide twin of ``topological_levels``: a root is ready once every
    blocker is either outside ``roots`` (ignored: it is not this board's to
    order) or already placed. The same ``CardNode`` objects come back; the
    input list is not touched. Used only for display and for computing the
    claim set up front; it never drives execution order.
    """
    ids = {root.id for root in roots}
    placed: set[str] = set()
    levels: list[list[CardNode]] = []
    rest = list(roots)
```

with:

```python
    """Open milestone roots grouped into dependency levels, each in input order.

    The board-wide twin of ``compute_levels``: a root still has work only if
    it is not ``done`` and some card under it, at any depth, is not ``done``.
    Any other root is dropped first, so its id sits outside the set and a
    root it blocked lands in level 0. A kept root is ready once every blocker
    is either outside the kept set (ignored: it is not this board's to order)
    or already placed. The same ``CardNode`` objects come back; the input list
    is not touched. Used only for display and for computing the claim set up
    front; it never drives execution order.
    """
    pending = [
        root
        for root in roots
        if (root.status or "").lower() != "done" and _has_open_descendant(root)
    ]
    ids = {root.id for root in pending}
    placed: set[str] = set()
    levels: list[list[CardNode]] = []
    rest = list(pending)
```

The rest of the loop (`while rest:` through `return levels`) stays exactly as written in Task 1.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dag.py -v`
Expected: PASS for all seven new filter tests, all ten Task 1 tests, and every pre-existing test.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no failures or errors (opt-in `e2e` tests deselected/skipped as usual).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/dag.py tests/test_dag.py
git commit -m "Drop finished milestone roots from board_levels, mirroring compute_levels"
```
