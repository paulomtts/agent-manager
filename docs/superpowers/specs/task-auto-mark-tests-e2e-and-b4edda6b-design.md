# Auto-mark tests/e2e/ and tests/steps/ by directory (card b4edda6b)

Parent story: 186fe934 "Define and enforce the test tiers". Milestone design: `docs/superpowers/specs/2026-10-02-test-tier-design.md`, decision V2 (and V1 for the tier definitions). This card narrows V2 to the directory auto-mark only.

## Scope

Add a `pytest_collection_modifyitems` hook to `tests/conftest.py`, next to the existing data-dir guard and `isolated_data_home` fixture (which stay unchanged). The hook gives each collected item a default tier marker based on its directory:

- An item under `tests/e2e/` gets `e2e_fake`.
- An item under `tests/steps/` gets `git`.
- An item anywhere else is left alone.

"Unless already marked" is a hard requirement. If an item already has any tier marker (`git`, `brd`, `e2e_fake`, `soak`, `e2e`), the hook does not add one. This applies whether the marker is on the function, the class, or a module-level `pytestmark`, so the check uses the item's full marker chain (`iter_markers` / `get_closest_marker`), not only `own_markers`. Non-tier markers (`parametrize`, `skipif`, etc.) don't count as "already marked". In practice this means the four `@pytest.mark.e2e` modules under `tests/e2e/` (`test_real_harness.py`, `test_real_harness_integrate.py`, `test_real_harness_parallel.py`, `test_real_harness_milestone.py`) stay `e2e` and do not also get `e2e_fake`. Any `tests/steps/` test already marked `brd` or `soak` keeps that marker and does not get `git`.

The directory check works on the item's path relative to the `tests/` directory that holds this conftest (the first path component is `e2e` or `steps`). It must not depend on the invocation cwd or rootdir, and must not use substring matching on the absolute path. Conftest files themselves are not items, so they are unaffected.

The hook only adds markers. It never deselects or skips anything itself. Deselection still comes from the addopts `-m "not brd and not e2e_fake and not soak and not e2e"` that sibling 19b291e9 already added. `pyproject.toml` is not touched.

## Observable behavior

- `uv run pytest --collect-only -q` with no `-m` collects the same total number of items as before the change. Because addopts applies, the number *selected* by default goes down by every `tests/e2e/` item. `tests/steps/` items are still selected, since `git` is a default tier.
- `uv run pytest --collect-only -m e2e_fake` lists the 10 `tests/e2e/` modules that are not `test_real_harness*`, and none of the four `test_real_harness*` modules.
- `uv run pytest --collect-only -m e2e` still lists exactly the existing `test_real_harness*` items.
- `uv run pytest --collect-only -m git` lists every `tests/steps/` item that had no tier marker before.
- `uv run pytest` passes. The PR description must state, with before/after `--collect-only` counts (not just a green run), that `tests/e2e/` no longer runs by default.

## Known consequence (not fixed here)

The 8 `test_this_module_runs_in_the_default_suite_unmarked` tests under `tests/e2e/` (for example `test_production_wiring.py:170`, `test_parallel_milestone.py:99`) assert that their node and module have no own markers. After this change they are deselected by default. Under `-m e2e_fake` they will fail, because the marker the hook adds shows up in `own_markers`. Deleting them belongs to V6, not this card. Leave them as they are and mention the failure in the PR.

## Error paths

- A marker name the hook adds that isn't registered: impossible after 19b291e9, and `--strict-markers` (if enabled) would surface it at collection.
- An item whose path is outside `tests/` (a plugin or rootdir change): skip it, don't raise.

## Out of scope (owned by siblings or excluded by spec §8)

- The unit-tier PATH-shim autouse fixture and the `pytest_runtest_makereport` duration budget belong to 3202b0b0.
- The e2e cap of 5, the `justification:` docstring check, and the marker-driven `pytest_runtest_setup` skip that replaces the `requires_git`/`requires_brd` copies belong to 3aa663b8.
- Moving or deleting tests, splitting `test_fake_claude.py` (V7), the rollup/board brd-vs-soak split (V8), and the `FakeBoard` seam (V3) are all out of scope.
- Also excluded: pytest-xdist, any `pyproject.toml` change, and any change to `src/agent_manager/`.

## Tests

Put the decision logic in a small pure helper inside `tests/conftest.py`: given an item's path relative to `tests/` and the set of marker names it already has, return the marker to add, or `None`. The hook calls this helper. Test it from a new `tests/test_conftest_tiers.py`. Per V1, all of these tests belong in the **unit** tier (unmarked, default): they call a pure function and spawn no subprocess. The file lives at the top level of `tests/`, so the hook doesn't mark it.

1. `test_e2e_dir_item_gets_e2e_fake`: a `e2e/test_x.py` path with no markers returns `e2e_fake`. Tier: unit.
2. `test_e2e_dir_item_already_marked_e2e_is_left_alone`: a `e2e/test_real_harness.py` path with `{"e2e"}` returns `None`. Tier: unit.
3. `test_steps_dir_item_gets_git`: a `steps/test_worktree.py` path with no markers returns `git`. Tier: unit.
4. `test_steps_dir_item_with_other_tier_marker_is_left_alone`: a `steps/test_rollup.py` path with `{"brd"}`, and again with `{"soak"}`, returns `None`. Tier: unit.
5. `test_non_tier_markers_do_not_count_as_marked`: a `steps/...` path with `{"parametrize", "skipif"}` returns `git`. Tier: unit.
6. `test_other_directories_are_not_marked`: `test_cli.py` and `nested/e2e_like/test_x.py` (where `e2e` is not the first component) return `None`. Tier: unit.

Verification is `uv run pytest`, plus the `--collect-only` before/after counts listed under Observable behavior. No lint or typecheck command exists.
