<!-- task-pipeline: validated -->
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

---

# Auto-mark tests/e2e/ and tests/steps/ by directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `pytest_collection_modifyitems` hook to `tests/conftest.py` that marks unmarked `tests/e2e/` items `e2e_fake` and unmarked `tests/steps/` items `git`, driven by a pure, unit-tested helper.

**Architecture:** Two pure helpers in `tests/conftest.py` do the work: `relative_to_tests(path)` turns an item path into a path relative to the conftest's own `tests/` directory (or `None` when it is outside), and `default_tier_marker(rel_path, existing)` decides which tier marker to add (or `None`). A thin `pytest_collection_modifyitems` hook (`tryfirst=True`, so it runs before pytest's own `-m` deselection) collects each item's full marker-chain names via `item.iter_markers()`, calls the helpers, and calls `item.add_marker(...)`. Deselection stays with the existing `addopts` `-m` expression; nothing else changes.

**Tech Stack:** Python, pytest (importlib import mode, `pythonpath = ["tests"]`), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-auto-mark-tests-e2e-and-b4edda6b/docs/superpowers/specs/task-auto-mark-tests-e2e-and-b4edda6b-design.md` (prepended above). Milestone design: `docs/superpowers/specs/2026-10-02-test-tier-design.md` (V1, V2).

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-auto-mark-tests-e2e-and-b4edda6b`, branch `m15/task-auto-mark-tests-e2e-and-b4edda6b`, cut from `origin/m15/task-register-the-markers-19b291e9`. Run every command below from the worktree root. Do not assume any code from siblings 3202b0b0 or 3aa663b8 exists.

## Global Constraints

- Tier markers are exactly: `git`, `brd`, `e2e_fake`, `soak`, `e2e` (already registered in `pyproject.toml:33-39` by 19b291e9).
- `tests/e2e/` items get `e2e_fake`; `tests/steps/` items get `git`; everything else is left alone.
- "Unless already marked": any tier marker anywhere on the item's marker chain (function, class, module `pytestmark`) suppresses the default. Non-tier markers (`parametrize`, `skipif`, `asyncio`, ...) do not.
- Directory check uses the path relative to the `tests/` directory holding `tests/conftest.py` (first component `e2e` or `steps`). No dependence on cwd or rootdir, no substring matching on the absolute path.
- Paths outside `tests/` are skipped, never raise.
- The hook only adds markers; it never deselects or skips.
- Do not touch `pyproject.toml`, anything under `src/agent_manager/`, or any existing test module. Do not add `--strict-markers`.
- Do not implement the PATH-shim autouse fixture, the `pytest_runtest_makereport` duration budget (3202b0b0), the e2e cap / `justification:` check, or a `pytest_runtest_setup` marker skip (3aa663b8).
- New tests are unit tier: unmarked, live at `tests/test_conftest_tiers.py` (top level of `tests/`, like `tests/test_paths.py`), spawn no subprocess.
- The existing `isolated_data_home` fixture and `pytest_sessionstart` / `pytest_sessionfinish` data-dir guard in `tests/conftest.py` stay byte-for-byte unchanged.

## Review Focus

1. Module-level `pytestmark = pytest.mark.e2e` (the four `tests/e2e/test_real_harness*.py` modules, e.g. `tests/e2e/test_real_harness.py:39`): a reasonable person expects these to remain `e2e` only. The hook must read `item.iter_markers()`, not `own_markers`. Pinned by Task 1 test `test_e2e_dir_item_already_marked_e2e_is_left_alone` plus `test_tier_marker_mixed_with_non_tier_markers_still_counts`, and end-to-end by Task 2 Step 6 (`-m e2e` count unchanged, `-m e2e_fake` excludes `test_real_harness*`).
2. A path outside `tests/` that merely contains `e2e` or `steps` (e.g. a sibling `<repo>/e2e/test_x.py`, or a plugin item): expected to be left alone without raising. Pinned by Task 1 test `test_relative_to_tests_returns_none_outside_tests_dir`.
3. Invocation from a different cwd (`cd tests && uv run pytest`, or an IDE running from elsewhere): expected same marking. Pinned by Task 1 test `test_relative_to_tests_ignores_cwd`.
4. A future nested directory `tests/e2e/sub/test_x.py`: expected to still be `e2e_fake` because the first component is `e2e`. Pinned by Task 1 test `test_nested_dir_under_e2e_still_gets_e2e_fake`.
5. An item that carries a tier marker alongside non-tier ones (e.g. `{"skipif", "e2e"}` or `{"parametrize", "brd"}`): expected to keep its own tier and get nothing added. Pinned by Task 1 test `test_tier_marker_mixed_with_non_tier_markers_still_counts`.

---

## File Structure

- Modify: `tests/conftest.py` — append `TESTS_DIR`, `TIER_MARKERS`, `_DIRECTORY_TIERS`, `relative_to_tests`, `default_tier_marker`, and the `pytest_collection_modifyitems` hook after the existing `pytest_sessionfinish` (line 73). Lines 1-73 stay unchanged except the module docstring gets one added paragraph and the import line gains `PurePath` and `Iterable`.
- Create: `tests/test_conftest_tiers.py` — unit-tier tests for the two pure helpers.

Note on importing the helpers: `pyproject.toml:51` sets `pythonpath = ["tests"]`, so `from conftest import ...` resolves to `tests/conftest.py` (the same mechanism `tests/lockhelpers.py` relies on). If pytest registered the conftest under a different module name, Python imports a second copy of the module; that is harmless here because the helpers are pure and the module has no import-time side effects beyond computing constants.

---

### Task 0: Record the before counts

**Files:** none modified.

- [ ] **Step 1: Record the total collected count (no tier filter)**

Run: `uv run pytest --collect-only -q -m "" 2>&1 | tail -n 3`
Expected: a line like `N tests collected in X.XXs`. Write down N as `TOTAL_BEFORE`. (A command-line `-m` replaces the addopts one; `-m ""` disables mark selection.)

- [ ] **Step 2: Record the default selection count**

Run: `uv run pytest --collect-only -q 2>&1 | tail -n 3`
Expected: a line like `S/N tests collected (D deselected) in X.XXs`. Write down S as `DEFAULT_BEFORE` and D as `DESELECTED_BEFORE`.

- [ ] **Step 3: Record the e2e_fake, e2e, git, and tests/e2e counts**

Run each and write down the selected count:
```bash
uv run pytest --collect-only -q -m e2e_fake 2>&1 | tail -n 3
uv run pytest --collect-only -q -m e2e 2>&1 | tail -n 3
uv run pytest --collect-only -q -m git 2>&1 | tail -n 3
uv run pytest --collect-only -q -m "" tests/e2e 2>&1 | tail -n 3
uv run pytest --collect-only -q -m "" tests/steps 2>&1 | tail -n 3
```
Expected: `e2e_fake` selects 0 (`E2E_FAKE_BEFORE = 0`), `e2e` selects only `test_real_harness*` items (`E2E_BEFORE`), `git` selects 0 (`GIT_BEFORE = 0`; nothing is marked `git` yet), plus `E2E_DIR_TOTAL` and `STEPS_DIR_TOTAL`. These numbers go in the PR description.

---

### Task 1: Pure tier-decision helpers

**Files:**
- Modify: `tests/conftest.py:9-14` (imports, docstring) and append after line 73
- Test: `tests/test_conftest_tiers.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces (in `tests/conftest.py`):
  - `TESTS_DIR: Path` — `Path(__file__).resolve().parent`
  - `TIER_MARKERS: frozenset[str]` — `frozenset({"git", "brd", "e2e_fake", "soak", "e2e"})`
  - `relative_to_tests(path: os.PathLike[str] | str, tests_dir: Path = TESTS_DIR) -> PurePath | None`
  - `default_tier_marker(rel_path: PurePath, existing: Iterable[str]) -> str | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_conftest_tiers.py`:

```python
"""Unit tier: the directory-to-tier decision in `tests/conftest.py`.

Both helpers are pure (no subprocess, no collection), so these tests carry
no tier marker and run in the default suite. This file sits at the top of
`tests/`, so the auto-mark hook leaves it unmarked.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from conftest import TESTS_DIR, TIER_MARKERS, default_tier_marker, relative_to_tests


def test_tier_markers_are_the_five_registered_tiers():
    assert TIER_MARKERS == frozenset({"git", "brd", "e2e_fake", "soak", "e2e"})


def test_e2e_dir_item_gets_e2e_fake():
    assert default_tier_marker(PurePosixPath("e2e/test_x.py"), set()) == "e2e_fake"


def test_e2e_dir_item_already_marked_e2e_is_left_alone():
    assert default_tier_marker(PurePosixPath("e2e/test_real_harness.py"), {"e2e"}) is None


def test_steps_dir_item_gets_git():
    assert default_tier_marker(PurePosixPath("steps/test_worktree.py"), set()) == "git"


@pytest.mark.parametrize("tier", ["brd", "soak"])
def test_steps_dir_item_with_other_tier_marker_is_left_alone(tier):
    assert default_tier_marker(PurePosixPath("steps/test_rollup.py"), {tier}) is None


def test_non_tier_markers_do_not_count_as_marked():
    assert default_tier_marker(PurePosixPath("steps/test_verify.py"), {"parametrize", "skipif"}) == "git"


@pytest.mark.parametrize(
    "rel_path",
    ["test_cli.py", "nested/e2e_like/test_x.py", "nested/e2e/test_x.py", "runtime/steps/test_x.py"],
)
def test_other_directories_are_not_marked(rel_path):
    assert default_tier_marker(PurePosixPath(rel_path), set()) is None


def test_nested_dir_under_e2e_still_gets_e2e_fake():
    assert default_tier_marker(PurePosixPath("e2e/sub/test_x.py"), set()) == "e2e_fake"


@pytest.mark.parametrize(
    ("rel_path", "existing"),
    [
        ("e2e/test_real_harness.py", {"skipif", "e2e"}),
        ("steps/test_rollup.py", {"parametrize", "brd"}),
        ("e2e/test_x.py", {"asyncio", "e2e_fake"}),
    ],
)
def test_tier_marker_mixed_with_non_tier_markers_still_counts(rel_path, existing):
    assert default_tier_marker(PurePosixPath(rel_path), existing) is None


def test_existing_markers_may_be_any_iterable():
    assert default_tier_marker(PurePosixPath("e2e/test_x.py"), iter(["e2e"])) is None
    assert default_tier_marker(PurePosixPath("e2e/test_x.py"), ["skipif"]) == "e2e_fake"


def test_relative_to_tests_inside_tests_dir():
    rel = relative_to_tests(TESTS_DIR / "e2e" / "test_x.py")
    assert rel is not None
    assert rel.parts == ("e2e", "test_x.py")


def test_relative_to_tests_returns_none_outside_tests_dir():
    # A sibling of tests/ whose path still contains "e2e": no substring match.
    assert relative_to_tests(TESTS_DIR.parent / "e2e" / "test_x.py") is None
    assert relative_to_tests(Path("/") / "elsewhere" / "steps" / "test_x.py") is None


def test_relative_to_tests_ignores_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = relative_to_tests(TESTS_DIR / "steps" / "test_worktree.py")
    assert rel is not None
    assert rel.parts == ("steps", "test_worktree.py")


def test_relative_to_tests_accepts_an_explicit_tests_dir(tmp_path):
    rel = relative_to_tests(str(tmp_path / "e2e" / "test_x.py"), tests_dir=tmp_path)
    assert rel is not None
    assert rel.parts == ("e2e", "test_x.py")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_conftest_tiers.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'TESTS_DIR' from 'conftest'`.

- [ ] **Step 3: Write the minimal implementation**

In `tests/conftest.py`, replace lines 1-14 (docstring and imports) with:

```python
"""Suite-wide isolation from the user's real agent-manager data directory.

`paths.data_dir()` resolves `XDG_DATA_HOME` (else `~/.local/share`) at call time.
A test that opens a store or a run directory without pointing `XDG_DATA_HOME`
somewhere temporary writes into the real directory, and can read real run data.
The guard below fails the session if the real directory changed during it.

It also gives directory-conventional tests a default tier marker: items under
`tests/e2e/` get `e2e_fake` and items under `tests/steps/` get `git`, unless the
item already carries a tier marker anywhere on its marker chain. The hook only
adds markers; the addopts `-m` expression in pyproject.toml does the deselecting.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePath

import pytest
```

Then append after the existing `pytest_sessionfinish` (after current line 73):

```python


TESTS_DIR = Path(__file__).resolve().parent

TIER_MARKERS = frozenset({"git", "brd", "e2e_fake", "soak", "e2e"})

_DIRECTORY_TIERS = {"e2e": "e2e_fake", "steps": "git"}


def relative_to_tests(path: os.PathLike[str] | str, tests_dir: Path = TESTS_DIR) -> PurePath | None:
    """`path` relative to `tests_dir`, or None when it lies outside it.

    Resolved first, so neither the invocation cwd nor the rootdir matters.
    """
    try:
        return Path(path).resolve().relative_to(tests_dir)
    except ValueError:
        return None


def default_tier_marker(rel_path: PurePath, existing: Iterable[str]) -> str | None:
    """The tier marker an item at `rel_path` (relative to tests/) should get.

    None when its first directory is neither `e2e` nor `steps`, or when
    `existing` (every marker name on the item's chain) already holds a tier.
    """
    if len(rel_path.parts) < 2:
        return None
    tier = _DIRECTORY_TIERS.get(rel_path.parts[0])
    if tier is None:
        return None
    if TIER_MARKERS.intersection(existing):
        return None
    return tier
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_conftest_tiers.py -v`
Expected: all tests PASS (20 items including parametrized cases), none deselected.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_conftest_tiers.py
git commit -m "Add the directory-to-tier decision helpers to tests/conftest.py"
```

---

### Task 2: Wire the collection hook and verify the counts

**Files:**
- Modify: `tests/conftest.py` (append after `default_tier_marker` from Task 1)

**Interfaces:**
- Consumes: `relative_to_tests(path) -> PurePath | None`, `default_tier_marker(rel_path, existing) -> str | None` from Task 1.
- Produces: `pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None` (a pytest hook; nothing calls it directly).

- [ ] **Step 1: Run the failing collection check (RED)**

The hook's observable contract is the marker selection, so the failing check is the selection itself.

Run: `uv run pytest --collect-only -q -m e2e_fake 2>&1 | tail -n 3`
Expected (fails the spec's Observable behavior): `no tests collected (N deselected)` / 0 selected, while the spec requires the 10 non-`test_real_harness*` `tests/e2e/` modules.

Run: `uv run pytest --collect-only -q -m git 2>&1 | tail -n 3`
Expected (fails the spec): 0 selected, while the spec requires every `tests/steps/` item.

- [ ] **Step 2: Write the hook**

Append to `tests/conftest.py`, after `default_tier_marker`:

```python


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Add the directory default tier marker to each item that has no tier yet.

    `tryfirst` so the markers exist before pytest's own `-m` deselection runs.
    `iter_markers` walks function, class and module `pytestmark`, so a module
    marked `e2e` (tests/e2e/test_real_harness*.py) keeps `e2e` alone.
    """
    rel_paths: dict[Path, PurePath | None] = {}
    for item in items:
        path = Path(item.path)
        if path not in rel_paths:
            rel_paths[path] = relative_to_tests(path)
        rel_path = rel_paths[path]
        if rel_path is None:
            continue
        marker = default_tier_marker(rel_path, {mark.name for mark in item.iter_markers()})
        if marker is not None:
            item.add_marker(marker)
```

- [ ] **Step 3: Verify e2e_fake selects exactly the 10 fake-wiring modules (GREEN)**

Run: `uv run pytest --collect-only -q -m e2e_fake 2>&1 | grep '::' | cut -d: -f1 | sort -u`
Expected: exactly these 10 paths, and no `test_real_harness*`:
```
tests/e2e/test_board_comments.py
tests/e2e/test_exactly_once.py
tests/e2e/test_fake_claude.py
tests/e2e/test_integrate.py
tests/e2e/test_live_control.py
tests/e2e/test_milestone_resume.py
tests/e2e/test_milestone_run.py
tests/e2e/test_multi_process.py
tests/e2e/test_parallel_milestone.py
tests/e2e/test_production_wiring.py
```

- [ ] **Step 4: Verify e2e is unchanged**

Run: `uv run pytest --collect-only -q -m e2e 2>&1 | tail -n 3`
Expected: selected count equals `E2E_BEFORE` from Task 0 Step 3.

Run: `uv run pytest --collect-only -q -m e2e 2>&1 | grep '::' | cut -d: -f1 | sort -u`
Expected: only `tests/e2e/test_real_harness.py`, `tests/e2e/test_real_harness_integrate.py`, `tests/e2e/test_real_harness_milestone.py`, `tests/e2e/test_real_harness_parallel.py`.

- [ ] **Step 5: Verify git selects every tests/steps/ item**

Run: `uv run pytest --collect-only -q -m git 2>&1 | tail -n 3`
Expected: selected count equals `STEPS_DIR_TOTAL` from Task 0 Step 3 (no `tests/steps/` item carries `brd` or `soak` today).

Run: `uv run pytest --collect-only -q -m git 2>&1 | grep '::' | cut -d: -f1 | sort -u`
Expected: only paths under `tests/steps/`.

- [ ] **Step 6: Verify the total is unchanged and the default selection shrank by tests/e2e/**

Run: `uv run pytest --collect-only -q -m "" 2>&1 | tail -n 3`
Expected: total equals `TOTAL_BEFORE + 20` (the 20 new items from `tests/test_conftest_tiers.py`); nothing else added or lost.

Run: `uv run pytest --collect-only -q 2>&1 | tail -n 3`
Expected: selected equals `DEFAULT_BEFORE + 20 - (E2E_DIR_TOTAL - E2E_BEFORE)`, i.e. every `tests/e2e/` item that was selected before is now deselected. Confirm with:

Run: `uv run pytest --collect-only -q 2>&1 | grep -c '^tests/e2e/'`
Expected: `0`.

Run: `uv run pytest --collect-only -q 2>&1 | grep -c '^tests/steps/'`
Expected: equals `STEPS_DIR_TOTAL`.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with no `tests/e2e/` test executed. Do NOT run `uv run pytest -m e2e_fake` as a gate: per the spec's "Known consequence", the 8 `test_this_module_runs_in_the_default_suite_unmarked` tests fail under it, and fixing them belongs to V6.

- [ ] **Step 8: Confirm nothing outside scope changed**

Run: `git diff --stat origin/m15/task-register-the-markers-19b291e9 -- . ':!docs'`
Expected: only `tests/conftest.py` and `tests/test_conftest_tiers.py`. `pyproject.toml` and `src/agent_manager/` absent.

Run: `git diff origin/m15/task-register-the-markers-19b291e9 -- tests/conftest.py`
Expected: only the docstring paragraph, the two import changes, and the appended block; `isolated_data_home`, `pytest_sessionstart`, `pytest_sessionfinish` untouched.

- [ ] **Step 9: Commit**

```bash
git add tests/conftest.py
git commit -m "Auto-mark tests/e2e/ as e2e_fake and tests/steps/ as git by directory"
```

- [ ] **Step 10: Write the PR / card note**

Record for the PR description (fill with the numbers measured in Task 0 and Task 2):
- Total collected before / after (`-m ""`): `TOTAL_BEFORE` / `TOTAL_BEFORE + 20`.
- Default selection before / after: `DEFAULT_BEFORE` / measured value; the drop is every `tests/e2e/` item, which no longer runs by default (run them with `uv run pytest -m e2e_fake`).
- `-m e2e` unchanged at `E2E_BEFORE`.
- `-m git` now selects all `STEPS_DIR_TOTAL` `tests/steps/` items.
- Known failure: under `-m e2e_fake`, the 8 `test_this_module_runs_in_the_default_suite_unmarked` tests in `tests/e2e/` fail because the hook adds `e2e_fake` to `own_markers`; removing them is V6.
