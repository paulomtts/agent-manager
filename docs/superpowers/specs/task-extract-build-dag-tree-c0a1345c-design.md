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
