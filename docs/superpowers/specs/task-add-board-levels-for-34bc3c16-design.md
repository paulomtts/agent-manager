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
