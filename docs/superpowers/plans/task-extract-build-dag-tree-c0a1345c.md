<!-- task-pipeline: validated -->
# Extract `build_dag_tree` out of `supervise` (subtask c0a1345c)

Parent story: 335671fb, "supervise's DAG-building becomes reusable and semaphore-injectable". Milestone design: `docs/superpowers/specs/2026-10-01-run-board-design.md`, §3.4 ("The extraction") and §3.8. This document narrows that design to this one subtask.

## Scope

In `src/agent_manager/orchestrate.py`, move the node/edge construction inside `supervise` (today the `nodes = {...}` comprehension, the blocker `connect()` loop, and the `roots = [...]` list, orchestrate.py:1390-1406) into a new module-level generic helper, `build_dag_tree`. Then make `supervise` call it. Nothing else changes.

Signature, as agreed in §3.4, with one required adjustment: grafo's `Node.connect` is a coroutine (`supervise` already `await`s it), so the helper is `async def`:

```python
async def build_dag_tree(
    items: Sequence[T],
    *,
    id_of: Callable[[T], str],
    blockers_of: Callable[[T], Sequence[str]],
    node_factory: Callable[[T], Callable[..., Awaitable[Any]]],
    forward: Callable[[T], str | None] | None = None,
) -> tuple[dict[str, grafo.Node], list[grafo.Node]]
```

`T` is a module-level `TypeVar`. The helper must not reference `census`, `StoryPlan`, `SupervisorPlan`, `RootPlan`, `lane`, git, brd, or the store.

Out of scope, owned by other subtasks:
- 6bb4f541 owns the semaphore. Leave `slots = asyncio.Semaphore(max_concurrent)` and the `max_concurrent` parameter exactly as they are.
- a3eaf615 owns `run_milestone`. Do not touch it.
- `lane()` (orchestrate.py:1077), `node_coroutine`, `story_done`/`story_ok`, `blocker_tips`, `run_until_killed`, `collect_outcomes`, and the grafo-logger handling stay exactly as they are.
- No fix for grafo's 2+-parent join-starvation bug. The workaround is kept as it is.

## Observable behavior

For `build_dag_tree`:
- Each item gets one `grafo.Node(coroutine=node_factory(item), uuid=id_of(item), timeout=None)`. `nodes_by_id` is keyed by `id_of(item)` and follows the order of `items`.
- `blockers_of(item)` returns ids (strings), not items, so the helper also needs an id-to-item lookup (e.g. `items_by_id = {id_of(item): item for item in items}`, built once) to turn a blocker id back into the blocker item that `forward` expects.
- An item whose `blockers_of(item)` has exactly one id gets one edge, created with `await nodes[blocker].connect(nodes[id_of(item)], forward=...)`, where `blocker` is that one id, used to index both `nodes` and `items_by_id`. The forward name is `forward(items_by_id[blocker])` — i.e. `forward` is called with the blocker *item*, not the blocker id. If `forward` is `None`, or if it returns `None`, `connect` is called without a forward name.
- An item with two or more blockers gets no incoming edge. It becomes an executor root instead. The helper creates no events and does no waiting. Waiting on blockers is the job of the coroutine that `node_factory` builds. Today that is `lane` together with `story_done`/`story_ok`.
- `roots` holds, in `items` order, every item with zero blockers plus every item with two or more blockers. Items with exactly one blocker are never roots.
- Empty `items` returns `({}, [])`.
- A blocker id that is not among `items` is the caller's problem. The helper does not filter or validate blockers, which matches today's behavior.

For `supervise`, after the refactor:
- It calls `build_dag_tree` with:
  - `items=plan.stories`
  - `id_of=lambda s: s.id`
  - `blockers_of=lambda s: plan.roots[s.id].blockers`
  - `node_factory=node_coroutine`
  - `forward=lambda b: f"tip_{dag.short_id(b.id)}"`
- Today's "merged" check (`kind == "merged"`) becomes a blocker-count check. The two are equivalent, because `dag.RootPlan` defines `"merged"` as two or more de-duplicated in-milestone blockers (dag.py:258-262).
- Nodes, edges, forward kwarg names, roots, and their order are identical to today. The signature, return value (`list[LaneOutcome]`), payload shape, and error and stop behavior do not change.
- Move the generic part of the existing docstring (one node per item, single-blocker edges, merged items as extra roots, and why) into `build_dag_tree`'s docstring. Keep the story-specific parts in `supervise`'s docstring.

## Error paths

The helper adds no new error handling. Exceptions raised by `node_factory` or `connect` propagate as they do today. A lane's `BaseException` is still caught by `node_coroutine` and still routed through `fatal`/`killed`. That stays inside `supervise`, not in the helper.

## Tests

Test tiers follow the agent-manager design spec §14 (Testing): pure functions get unit tests next to the logic; steps run against temp git repos and a temp brd board; the engine is driven with a fake adapter; end-to-end is one slow, opt-in, real-harness test. `build_dag_tree` does no I/O, git, or brd work, so its tests belong in the pure-function unit tier. Put them in `tests/test_orchestrate.py`, near the existing `test_plan_levels_*` pure-logic tests, using plain dataclass items and trivial async coroutines.

New tests, all in the pure-function unit tier, `tests/test_orchestrate.py`:
1. `test_build_dag_tree_single_blocker_wires_edge`. A chain A <- B: one node per item with the right uuid and `timeout=None`; B is reachable from A by edge with the forward name `forward(A)`; `roots == [A]`. Running a `grafo.TreeExecutor` over the roots delivers A's return value to B under that kwarg name.
2. `test_build_dag_tree_multi_blocker_is_root_not_edge`. A and B both block C: C has no incoming edge (A's and B's children do not include C), and `roots == [A, B, C]` in items order.
3. `test_build_dag_tree_no_forward_connects_without_kwarg`. With `forward=None`, the single-blocker edge exists and the child is called with no forwarded kwarg.
4. `test_build_dag_tree_empty_items`. Returns `({}, [])`.

Behavior-preservation gate, unchanged files: the existing `supervise` suite in `tests/test_orchestrate.py` (pure-function and engine tiers), plus `tests/e2e/test_parallel_milestone.py` and `tests/e2e/test_real_harness_parallel.py`, which cover the merged-base and parallel-stories paths. They must pass with no edits to their assertions or fixtures. `uv run pytest` is the full gate. There is no separate lint or typecheck step.

---

# Extract `build_dag_tree` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `supervise`'s node/edge/roots construction into a generic `async def build_dag_tree` helper in `src/agent_manager/orchestrate.py`, and make `supervise` call it with no behavior change.

**Architecture:** Task 1 adds `build_dag_tree` (plus a module-level `TypeVar` `T`) next to `run_until_killed`, test-first with pure unit tests on plain dataclass items and trivial coroutines. Task 2 replaces the inline block in `supervise` (orchestrate.py:1390-1406) with one call to the helper and moves the generic half of the docstring onto the helper; the existing `supervise` suite and `tests/e2e/test_parallel_milestone.py` are the gate and are not edited.

**Tech Stack:** Python 3.12, `grafo` (pinned, `.venv/lib/python3.12/site-packages/grafo/components.py`), pytest with `asyncio_mode = "auto"` (pyproject.toml:32, so `async def test_...` runs without a marker), `uv`.

**Spec:** `docs/superpowers/specs/task-extract-build-dag-tree-c0a1345c-design.md` (prepended verbatim above).

## Global Constraints

- Branch `m14/task-extract-build-dag-tree-c0a1345c`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m14/task-extract-build-dag-tree-c0a1345c`, cut from origin/master. No other subtask's code (6bb4f541's `slots` parameter, a3eaf615's `_run_milestone_async`) exists here; do not add it.
- `build_dag_tree` is `async def`, module-level in `src/agent_manager/orchestrate.py`, with exactly the spec's signature; `T` is a module-level `TypeVar`.
- The helper must not reference `census`, `StoryPlan`, `SupervisorPlan`, `RootPlan`, `lane`, git, brd, or the store (code or docstring).
- Every node is built with `timeout=None`, and via the module attribute `grafo.Node` looked up at call time (existing tests `test_every_node_has_no_timeout` and `test_each_blocker_forwards_its_tip_to_its_dependent` at tests/test_orchestrate.py:2651-2711 monkeypatch `grafo.Node`).
- Leave untouched: `slots = asyncio.Semaphore(max_concurrent)`, the `max_concurrent` parameter, `run_milestone`, `lane`, `node_coroutine`, `story_done`/`story_ok`, `blocker_tips`, `run_until_killed`, `collect_outcomes`, grafo-logger handling.
- No edits to existing test assertions or fixtures in `tests/test_orchestrate.py`, `tests/e2e/test_parallel_milestone.py`, `tests/e2e/test_real_harness_parallel.py`.
- `grafo` stays imported only by `orchestrate.py` (`test_only_orchestrate_imports_grafo`); tests may import it.
- Verification: `uv run pytest`. No lint or typecheck command exists.

## Review Focus

1. `forward` is provided but returns `None` for a particular blocker: the edge must still exist and the child must be called with no forwarded kwarg (spec: "or if it returns `None`"). Pinned by `test_build_dag_tree_forward_returning_none_connects_without_kwarg` in Task 1.
2. Items listed with a 2+-blocker item *before* a zero-blocker item: `roots` must follow `items` order, not "plain roots first, merged roots after". Pinned by `test_build_dag_tree_roots_follow_items_order` in Task 1.
3. `forward` receives the blocker *item*, not its id string: a caller whose `forward` reads an attribute (as `supervise`'s `b.id` does) must not crash. Pinned in `test_build_dag_tree_single_blocker_wires_edge` (its `_dag_forward` reads `item.id`, which a `str` lacks) in Task 1.
4. A 2+-blocker item that itself blocks a single-blocker item (the `joined -> d` shape from `test_plan_levels_roots_a_story_behind_a_subtask_less_two_blocker_story_on_that_base`): the merged item is a root and still gets its own outgoing forwarded edge. Pinned by `test_build_dag_tree_merged_item_still_forwards_to_its_dependent` in Task 1.
5. `supervise` still builds nodes through a monkeypatchable `grafo.Node` and forwards `tip_<short id>`: covered by the existing, unchanged `test_every_node_has_no_timeout` and `test_each_blocker_forwards_its_tip_to_its_dependent` (tests/test_orchestrate.py:2651-2711), re-run in Task 2.

---

## File Structure

- Modify `src/agent_manager/orchestrate.py`:
  - line 55: add `TypeVar` to the `typing` import.
  - after the module constants (after `LaneKind`, ~line 79): add `T = TypeVar("T")`.
  - between `run_until_killed` (ends line 1303) and `supervise` (line 1306): add `build_dag_tree`.
  - `supervise` docstring (lines 1319-1342) and body lines 1390-1406: replace with the helper call.
- Modify `tests/test_orchestrate.py`: add the `build_dag_tree` unit tests in the `# ── pure plans ──` section, right after `test_a_milestone_with_nothing_pending_plans_no_levels` (ends line 160), before `test_story_tips_name_every_story_with_subtasks_in_census_order` (line 163). This is the existing pure-function tier for orchestrate (file docstring lines 3-6).

---

### Task 1: `build_dag_tree` helper with its pure unit tests

**Files:**
- Modify: `src/agent_manager/orchestrate.py:55` (typing import), `:77-79` (add `T` after `LaneKind`), insert new function between line 1303 and line 1306
- Test: `tests/test_orchestrate.py` (insert after line 160)

**Interfaces:**
- Consumes: `grafo.Node(coroutine=..., uuid=..., timeout=None)`, `await grafo.Node.connect(child, forward=str | None)`, `grafo.Node.children: list[Node]`, `grafo.Node._forward_map: dict[str, tuple[str, ...]]`, `grafo.TreeExecutor(uuid=..., roots=...)` with `await executor.run()`.
- Produces: `orchestrate.T = TypeVar("T")` and

```python
async def build_dag_tree(
    items: Sequence[T],
    *,
    id_of: Callable[[T], str],
    blockers_of: Callable[[T], Sequence[str]],
    node_factory: Callable[[T], Callable[..., Awaitable[Any]]],
    forward: Callable[[T], str | None] | None = None,
) -> tuple[dict[str, grafo.Node], list[grafo.Node]]
```

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrate.py`, insert immediately after the end of `test_a_milestone_with_nothing_pending_plans_no_levels` (after line 160, keeping two blank lines between definitions). `dataclass`, `Any`, `Awaitable`, `Callable`, `grafo` and `orchestrate` are already imported at the top of the file (lines 32-44).

```python
@dataclass(frozen=True)
class _DagItem:
    """A plain item for `build_dag_tree`: an id and the ids blocking it."""

    id: str
    blockers: tuple[str, ...] = ()


def _dag_factory(
    calls: dict[str, dict[str, Any]],
) -> Callable[[_DagItem], Callable[..., Awaitable[str]]]:
    """A `node_factory` whose coroutine records the kwargs it got and returns `out-<id>`."""

    def factory(item: _DagItem) -> Callable[..., Awaitable[str]]:
        async def run(**forwarded: Any) -> str:
            calls[item.id] = forwarded
            return f"out-{item.id}"

        return run

    return factory


def _dag_forward(item: _DagItem) -> str:
    """Reads `.id`, so it fails loudly if handed a blocker id instead of the item."""
    return f"from_{item.id}"


async def _build(
    items: list[_DagItem],
    calls: dict[str, dict[str, Any]],
    forward: Callable[[_DagItem], str | None] | None = _dag_forward,
) -> tuple[dict[str, grafo.Node], list[grafo.Node]]:
    return await orchestrate.build_dag_tree(
        items,
        id_of=lambda item: item.id,
        blockers_of=lambda item: item.blockers,
        node_factory=_dag_factory(calls),
        forward=forward,
    )


async def test_build_dag_tree_single_blocker_wires_edge():
    """A <- B: one timeout-less node per item, keyed and ordered by item; B is
    reached from A by an edge forwarding A's output as `forward(A)`; A alone
    is a root."""
    a, b = _DagItem("a"), _DagItem("b", ("a",))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, b], calls)

    assert list(nodes) == ["a", "b"]
    assert [node.uuid for node in nodes.values()] == ["a", "b"]
    assert [node._timeout for node in nodes.values()] == [None, None]
    assert nodes["a"].children == [nodes["b"]]
    assert nodes["b"].children == []
    assert roots == [nodes["a"]]

    await grafo.TreeExecutor(uuid="single", roots=roots).run()

    assert calls == {"a": {}, "b": {"from_a": "out-a"}}


async def test_build_dag_tree_multi_blocker_is_root_not_edge():
    """A and B both block C: C gets no incoming edge and is a root itself, so
    `roots` is every item, in items order. The helper does no waiting: C runs
    with no forwarded kwargs."""
    a, b = _DagItem("a"), _DagItem("b")
    c = _DagItem("c", ("a", "b"))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, b, c], calls)

    assert nodes["a"].children == []
    assert nodes["b"].children == []
    assert roots == [nodes["a"], nodes["b"], nodes["c"]]

    await grafo.TreeExecutor(uuid="merged", roots=roots).run()

    assert calls == {"a": {}, "b": {}, "c": {}}


async def test_build_dag_tree_no_forward_connects_without_kwarg():
    """With `forward=None` the single-blocker edge still exists, but nothing is
    forwarded along it."""
    a, b = _DagItem("a"), _DagItem("b", ("a",))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, b], calls, forward=None)

    assert nodes["a"].children == [nodes["b"]]
    assert nodes["a"]._forward_map == {}
    assert roots == [nodes["a"]]

    await grafo.TreeExecutor(uuid="unforwarded", roots=roots).run()

    assert calls == {"a": {}, "b": {}}


async def test_build_dag_tree_forward_returning_none_connects_without_kwarg():
    """A `forward` that returns `None` for a blocker behaves as no `forward` for
    that edge only."""
    a, b = _DagItem("a"), _DagItem("b", ("a",))
    c, d = _DagItem("c"), _DagItem("d", ("c",))
    calls: dict[str, dict[str, Any]] = {}

    def only_a(item: _DagItem) -> str | None:
        return "from_a" if item.id == "a" else None

    nodes, roots = await _build([a, b, c, d], calls, forward=only_a)

    assert nodes["c"].children == [nodes["d"]]
    assert nodes["c"]._forward_map == {}
    assert roots == [nodes["a"], nodes["c"]]

    await grafo.TreeExecutor(uuid="partial", roots=roots).run()

    assert calls == {"a": {}, "b": {"from_a": "out-a"}, "c": {}, "d": {}}


async def test_build_dag_tree_roots_follow_items_order():
    """A merged item listed before a plain root keeps its place: roots are in
    items order, not plain roots first."""
    a, b = _DagItem("a"), _DagItem("b")
    c = _DagItem("c", ("a", "b"))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, c, b], calls)

    assert list(nodes) == ["a", "c", "b"]
    assert roots == [nodes["a"], nodes["c"], nodes["b"]]


async def test_build_dag_tree_merged_item_still_forwards_to_its_dependent():
    """A merged item is a root, and a single-blocker item behind it is still
    reached by an edge forwarding the merged item's output."""
    a, b = _DagItem("a"), _DagItem("b")
    joined = _DagItem("joined", ("a", "b"))
    d = _DagItem("d", ("joined",))
    calls: dict[str, dict[str, Any]] = {}

    nodes, roots = await _build([a, b, joined, d], calls)

    assert nodes["joined"].children == [nodes["d"]]
    assert roots == [nodes["a"], nodes["b"], nodes["joined"]]

    await grafo.TreeExecutor(uuid="joined", roots=roots).run()

    assert calls["d"] == {"from_joined": "out-joined"}


async def test_build_dag_tree_empty_items():
    assert await _build([], {}) == ({}, [])
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k build_dag_tree -v`
Expected: 7 tests FAIL, each with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'build_dag_tree'`.

- [ ] **Step 3: Add `TypeVar` to the typing import**

In `src/agent_manager/orchestrate.py` line 55, replace:

```python
from typing import Any, Literal, Protocol
```

with:

```python
from typing import Any, Literal, Protocol, TypeVar
```

- [ ] **Step 4: Add the module-level `T`**

In `src/agent_manager/orchestrate.py`, directly after the `LaneKind` definition and its docstring (lines 77-79, ending `...or never started by the tree."""`), insert:

```python


T = TypeVar("T")
"""An item `build_dag_tree` turns into one grafo node."""
```

- [ ] **Step 5: Write `build_dag_tree`**

In `src/agent_manager/orchestrate.py`, between the end of `run_until_killed` (`    await running`, line 1303) and `async def supervise(` (line 1306), insert (keeping two blank lines on each side):

```python
async def build_dag_tree(
    items: Sequence[T],
    *,
    id_of: Callable[[T], str],
    blockers_of: Callable[[T], Sequence[str]],
    node_factory: Callable[[T], Callable[..., Awaitable[Any]]],
    forward: Callable[[T], str | None] | None = None,
) -> tuple[dict[str, grafo.Node], list[grafo.Node]]:
    """Build the grafo nodes and edges for `items`, and pick the executor's roots.

    One `grafo.Node` per item, `uuid=id_of(item)`, `timeout=None` always
    (grafo's 60 s default would cancel a long-running node). `nodes_by_id` is
    keyed by `id_of(item)`, in `items` order.

    An item with exactly one blocker gets one edge from that blocker. Its
    output is forwarded as `forward(blocker_item)` -- `forward` is handed the
    blocker item, not its id -- or not at all when `forward` is `None` or
    returns `None`.

    An item with two or more blockers gets no incoming edge and is one of the
    executor's roots itself: grafo's dynamic worker pool can starve a
    2+-parent join forever when an unrelated sibling node is still in flight
    (confirmed in the pinned grafo release; not fixed here). This helper
    creates no events and does no waiting: the coroutine `node_factory`
    builds for such an item must wait on its blockers itself.

    `roots` is, in `items` order, every item with no blocker plus every item
    with two or more. A blocker id not among `items` is the caller's to
    avoid; nothing is filtered or validated. Empty `items` gives `({}, [])`.
    """
    nodes = {
        id_of(item): grafo.Node(coroutine=node_factory(item), uuid=id_of(item), timeout=None)
        for item in items
    }
    items_by_id = {id_of(item): item for item in items}
    roots: list[grafo.Node] = []
    for item in items:
        blockers = blockers_of(item)
        if len(blockers) == 1:
            (blocker,) = blockers
            parent = nodes[blocker]
            name = None if forward is None else forward(items_by_id[blocker])
            await parent.connect(nodes[id_of(item)], forward=name)
        else:
            roots.append(nodes[id_of(item)])
    return nodes, roots
```

Note: `connect(..., forward=None)` is grafo's own default and returns before touching `_forward_map` (components.py:262-263), so it is exactly "connect without a forward name".

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k build_dag_tree -v`
Expected: 7 passed.

- [ ] **Step 7: Check the helper names nothing story-specific**

Use the Grep tool (or `rg`) over the body of `build_dag_tree` in `src/agent_manager/orchestrate.py` for `census|StoryPlan|SupervisorPlan|RootPlan|lane|git|brd|store|story` restricted to the lines of the new function.
Expected: no matches inside `build_dag_tree` (its docstring above deliberately says "node", not "lane" or "story").

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: all pass (the helper is not wired in yet, so nothing else changed).

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): add generic build_dag_tree helper"
```

---

### Task 2: `supervise` builds its tree through `build_dag_tree`

This is a behavior-preserving refactor, so it adds no new test: the RED/GREEN evidence is the existing, unedited `supervise` suite, run green before and after the change.

**Files:**
- Modify: `src/agent_manager/orchestrate.py` — `supervise` docstring (currently lines 1319-1342, shifted down by Task 1's insertions) and the block from `nodes = {` to the closing `]` of `roots = [...]` (currently lines 1390-1406, shifted likewise)
- Test (gate, unchanged): `tests/test_orchestrate.py`, `tests/e2e/test_parallel_milestone.py`, `tests/e2e/test_real_harness_parallel.py`

**Interfaces:**
- Consumes: `build_dag_tree` from Task 1 (signature above); `SupervisorPlan.stories: Sequence[census.StoryPlan]`, `SupervisorPlan.roots[story_id].blockers: tuple[str, ...]` (de-duplicated, dag.py:254-269), `dag.short_id(card_id: str) -> str`, the existing local `node_coroutine(story)`.
- Produces: no new names. `supervise`'s signature and `list[LaneOutcome]` return are unchanged.

- [ ] **Step 1: Baseline the gate before touching `supervise`**

Run: `uv run pytest tests/test_orchestrate.py tests/e2e/test_parallel_milestone.py`
Expected: all pass. Record the pass count; Step 5 must match it.

- [ ] **Step 2: Replace the inline tree-building block**

In `supervise`, replace exactly this block:

```python
        nodes = {
            story.id: grafo.Node(coroutine=node_coroutine(story), uuid=story.id, timeout=None)
            for story in plan.stories
        }
        for story in plan.stories:
            root_plan = plan.roots[story.id]
            if root_plan.kind == "merged":
                continue
            for blocker in root_plan.blockers:
                await nodes[blocker].connect(
                    nodes[story.id], forward=f"tip_{dag.short_id(blocker)}"
                )
        roots = [
            nodes[story.id]
            for story in plan.stories
            if not plan.roots[story.id].blockers or plan.roots[story.id].kind == "merged"
        ]
```

with:

```python
        nodes, roots = await build_dag_tree(
            items=plan.stories,
            id_of=lambda story: story.id,
            blockers_of=lambda story: plan.roots[story.id].blockers,
            node_factory=node_coroutine,
            forward=lambda blocker: f"tip_{dag.short_id(blocker.id)}",
        )
```

Everything before it (`slots`, `finished`, `story_done`, `story_ok`, `fatal`, `killed`, `node_coroutine`) and after it (`errors`, `if roots:`, `TreeExecutor`, `run_until_killed`, `collect_outcomes`, the `finally` restoring the logger) stays byte-for-byte as is.

Equivalence: `"base"` has zero blockers (root, no edge), `"tip"` exactly one (edge, not root), `"merged"` two or more (root, no edge) per dag.py:258-262, so `len(blockers)` reproduces the old `kind` checks; nodes, edges, edge order, kwarg names and root order are all walked in `plan.stories` order as before.

- [ ] **Step 3: Move the generic half of the docstring onto the helper**

In `supervise`'s docstring, replace the first paragraph:

```python
    """Run every census story as a grafo node and collect the outcomes (T1, T6).

    One `grafo.Node` per story, `uuid=story.id`, `timeout=None` always (grafo's
    60 s default would cancel a lane mid-phase). One edge per in-milestone
    blocker, forwarding the blocker's tip as `tip_<short id>`, for a story
    whose root is a single blocker. A story rooted on a `merged` base (two or
    more in-milestone blockers) is instead one of the executor's roots itself,
    with no incoming edge: grafo's dynamic worker pool can starve a 2+-parent
    join forever when an unrelated sibling lane is still in flight (confirmed
    outside this module, in the pinned grafo release; not a `dag`/`bases`
    defect, and out of scope to fix in grafo). Its lane instead waits on each
    blocker's own completion, signalled by `story_done`/`story_ok` below, and
    reads the blocker's tip off `plan.tips` (`blocker_tips`); every lane sets
    its own signal on exit, success or not, so this never hangs. The
    executor's roots are therefore the stories with no in-milestone blocker,
    plus every merged-root story; a milestone with no story has no tree to run.
```

with:

```python
    """Run every census story as a grafo node and collect the outcomes (T1, T6).

    The tree comes from `build_dag_tree` over `plan.stories`, each story's
    blockers being its `plan.roots` in-milestone blockers: one node per story,
    one edge per single-blocker story forwarding the blocker's tip as
    `tip_<short id>`, and every story rooted on a `merged` base (two or more
    in-milestone blockers) as an extra executor root, for the grafo
    join-starvation reason `build_dag_tree` documents (a grafo limitation,
    not a `dag`/`bases` defect). Such a story's lane waits on each blocker's own completion,
    signalled by `story_done`/`story_ok` below, and reads the blocker's tip
    off `plan.tips` (`blocker_tips`); every lane sets its own signal on exit,
    success or not, so this never hangs. A milestone with no story has no
    tree to run.
```

Leave the two following docstring paragraphs (the `BaseException` / `run_until_killed` one and the `grafo` logger one) unchanged.

- [ ] **Step 4: Confirm `supervise` no longer builds nodes inline**

Use the Grep tool on `src/agent_manager/orchestrate.py` for `grafo\.Node\(` and for `\.connect\(`.
Expected: exactly one match each, both inside `build_dag_tree`.

- [ ] **Step 5: Run the gate**

Run: `uv run pytest tests/test_orchestrate.py tests/e2e/test_parallel_milestone.py`
Expected: all pass, same count as Step 1 plus the 7 `build_dag_tree` tests. Watch in particular `test_every_node_has_no_timeout`, `test_each_blocker_forwards_its_tip_to_its_dependent`, `test_a_milestone_with_no_stories_finishes_without_a_tree` and the merged-base tests.

- [ ] **Step 6: Run the opt-in real-harness parallel gate if `claude` is available**

`tests/e2e/test_real_harness_parallel.py` is `pytestmark = pytest.mark.e2e` and deselected by `addopts = -m "not e2e"`. If a real `claude` is on PATH and spending is acceptable, run:
`uv run pytest -m e2e tests/e2e/test_real_harness_parallel.py`
Expected: pass. If not available, record that it was skipped as opt-in; the file is not edited either way.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: all pass, no existing test file's assertions edited (`git diff --stat` shows only `src/agent_manager/orchestrate.py` and `tests/test_orchestrate.py`, the latter with additions only).

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/orchestrate.py
git commit -m "refactor(orchestrate): build supervise's tree with build_dag_tree"
```
