# Cap the e2e tier and consolidate the requires_git/requires_brd copies (card 3aa663b8)

Parent story: 186fe934 "Define and enforce the test tiers". Source of truth: `docs/superpowers/specs/2026-10-02-test-tier-design.md`, decision V2 (tier table in V1). This subtask narrows V2 to its last two pieces. It builds on the blocker branch `m15/task-add-the-unit-tier-path-3202b0b0` (card 3202b0b0), whose `tests/conftest.py` already has the directory auto-mark `pytest_collection_modifyitems`, the unit-tier PATH shim and the per-tier budget `pytest_runtest_makereport`. pyproject.toml markers and addopts are already in place and are not touched.

## Scope

1. **e2e cap and justification check at collection.** In `tests/conftest.py`, after the directory auto-mark has run, fail the run if more than 5 collected items carry the `e2e` marker anywhere on their marker chain (`iter_markers`, so module `pytestmark = pytest.mark.e2e` counts), or if any `e2e` item's docstring has no line that starts with `justification:` once leading whitespace is stripped. The check counts items before `-m` deselection, so it fires in the default run as well as under `-m e2e`. The decision logic goes in a pure helper, for example `e2e_tier_violations(items) -> list[str]`, which takes (nodeid, marker names, docstring) triples. The hook raises `pytest.UsageError` (or `pytest.exit` with a usage-error code) with every violation in one message. For a parametrized `e2e` function, each parametrization is one item and counts toward the cap. Its docstring is the underlying function's `__doc__`.
2. **One marker-driven skip hook.** Add a single `pytest_runtest_setup(item)` to `tests/conftest.py`. For each of `git` and `brd` that the item carries on its marker chain, it calls `pytest.skip(f"the {name} CLI must be installed for the {name} tier")` when `shutil.which(name)` is None. Put the decision in a pure helper, for example `missing_binary(markers, which=shutil.which) -> str | None`, so it can be tested without touching `PATH`. Items with neither marker are never skipped by this hook. Because the hook runs after collection, the auto-added `git` on `tests/steps/` items counts.
3. **Delete the copies.** Remove the 11 `requires_git = pytest.mark.skipif(shutil.which(...) ...)` / `requires_brd = ...` definitions and every `@requires_git` / `@requires_brd` application. The definitions are at `tests/test_cli.py:1193,1197`, `tests/test_orchestrate.py:650,654`, `tests/test_board.py:22`, `tests/test_bases.py:39`, `tests/steps/test_docs_commit.py:19`, `tests/steps/test_verify.py:37`, `tests/steps/test_worktree.py:24`, `tests/steps/test_integrate.py:27` and `tests/steps/test_rollup.py:41`. Also remove any `shutil` imports and docstring references this leaves unused, such as the `requires_brd` mention in `steps/test_rollup.py`'s module docstring, and that same docstring's parenthetical naming `test_verify.py`'s now-deleted local `requires_git`. Replacement rule — applied per item (per `@requires_git`/`@requires_brd` occurrence), not per file; a file named below as "already marked" can still contain a straggler that falls under the "no marker" bullet instead, so check each occurrence's own markers rather than assuming every occurrence in that file matches:
   - `@requires_git` on an item that already ends up `git`-marked, either explicitly or through the `tests/steps/` auto-mark: delete it.
   - `@requires_git` on an item marked `brd` or `soak` (it needs both binaries): replace it with `@pytest.mark.git`. Selection does not change because addopts still deselects `brd`/`soak`, and the budget does not change because opt-in tiers win over `git`.
   - `@requires_brd` on an item already marked `brd` (test_board.py, test_cli.py and test_orchestrate.py after the 3202b0b0 bulk re-marking): delete it.
   - `@requires_brd` on an item with no `brd` marker (`tests/steps/test_rollup.py`, which is auto-marked `git` today, including its three `@pytest.mark.soak` concurrency probes at `:269,395,441` — they gain `@pytest.mark.brd` alongside their existing `soak` marker, per the next sentence; also the two stragglers `tests/test_cli.py:3085` and `tests/test_board.py:592` (the latter already `@pytest.mark.soak`), which the 3202b0b0 bulk re-marking did not cover): replace it with `@pytest.mark.brd`. This moves those tests out of the default run, which is the placement V1/V8 already gives real-brd tests. Moving the concurrency probes (`test_rollup.py:269,439`) to their own dedicated `soak`-only tier split is V8's work and is not done here — they simply end up carrying both `soak` and `brd`. A `brd`-marked item that also needs `soak` keeps whatever marker it has.
   - Do not touch the 3202b0b0 bulk-marking itself. Only the `requires_*` lines change.
4. **`tests/e2e/conftest.py` `toolchain`.** Keep it as a module-scoped, fixture-driven gate. The `e2e_fake`/`e2e` items it guards carry neither `git` nor `brd`, and adding `git` to them would stop the directory auto-mark and pull them into the default run. Its inline `shutil.which` loop changes to call the same helper the hook uses (imported from the root `conftest`), so the suite has exactly one binary-presence check. Its skip message and module scope stay the same.

## Out of scope

pytest-xdist, the pygents engine, the checkpoint format, the harness adapter contract, `dispatch.py`'s LauncherFn seam, milestone 14's `am run --board`, FakeBoard/`run_brd` (V3), the V4–V8 conversions and splits, and the count and content of the 5 existing `e2e` tests. Do NOT add `justification:` lines to them. That is V9's work, owned by a sibling outside this story.

## Expected verification state

`uv run pytest` must otherwise be green. The 5 existing `e2e` tests (the `pytestmark = pytest.mark.e2e` modules `tests/e2e/test_real_harness.py`, `test_real_harness_integrate.py`, `test_real_harness_milestone.py` and `test_real_harness_parallel.py`) do not have `justification:` lines yet. The new check is therefore expected to fail collection until the V9 subtask lands. The PR description must say so and name V9 as the fix. If the implementer finds the default run blocked entirely, they report it and do not work around it by weakening the check or adding placeholder lines.

## Error paths

- More than 5 `e2e` items: the collection error names the count, the cap (5) and the offending nodeids.
- `e2e` item with a missing or empty docstring, or no `justification:` line: the collection error names each such nodeid. Both kinds of violation are reported together.
- `git`/`brd` binary missing: that item is skipped with a reason naming the binary. It is not a failure.
- Both markers present and both binaries missing: one skip that names the first missing binary.

## Tests

Put them in `tests/test_tier_guards.py` / `tests/test_conftest_tiers.py`, beside the sibling's hook tests. Placement follows V1: pure helper calls run no subprocess and are **unit**. Tests that drive the real conftest through `pytester.runpytest_subprocess()` spawn a subprocess and are **`@pytest.mark.git`**, following the existing pytester tests in `tests/test_tier_guards.py`.

- unit: `e2e_tier_violations` returns nothing for 5 justified `e2e` items and for any number of non-`e2e` items.
- unit: a 6th `e2e` item produces a cap violation listing the count and nodeids.
- unit: an `e2e` item with no docstring, and one whose docstring has no line starting `justification:`, each produce a violation. An indented `    justification: ...` line passes.
- unit: `justification:` appearing mid-line (not at the start of a line) does not count.
- unit: `missing_binary` returns None for items with no `git`/`brd` marker, even when `which` reports everything missing.
- unit: `missing_binary` names `git` / `brd` when the item carries that marker and the injected `which` returns None, and returns None when the binary is found.
- unit: no `requires_git` / `requires_brd` name and no `skipif(shutil.which(` remain anywhere under `tests/`. This is a source scan.
- git (pytester): a nested session with 6 `e2e`-marked tests fails collection, and with 1 unjustified `e2e` test fails collection, under the default `-m` addopts.
- git (pytester): a nested `@pytest.mark.brd` test, run with `PATH` lacking `brd` and `-m brd`, is reported skipped with the binary named. An unmarked test in the same session is not skipped.
