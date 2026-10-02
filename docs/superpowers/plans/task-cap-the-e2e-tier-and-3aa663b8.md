<!-- task-pipeline: validated -->
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

---

# Cap the e2e tier and consolidate the binary skips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fail collection when the `e2e` tier exceeds 5 items or an `e2e` item lacks a `justification:` docstring line, and replace every scattered `requires_git`/`requires_brd`/`skipif(shutil.which(...))` copy with one marker-driven `pytest_runtest_setup` skip in `tests/conftest.py`.

**Architecture:** Two pure helpers in `tests/conftest.py` (`e2e_tier_violations`, `missing_binary`) carry all decisions and are unit-tested directly. A small plugin object `E2ETierCap`, registered from `pytest_configure`, runs the cap check in a `tryfirst` `pytest_collection_modifyitems` (before `-m` deselection); a module-level `pytest_runtest_setup` runs the binary skip. `tests/e2e/conftest.py`'s `toolchain` fixture calls `missing_binary`. Then every local copy is deleted, with a per-occurrence marker replacement.

**Tech Stack:** Python 3.12, pytest 9 (pytester plugin, importlib import mode, `pythonpath = ["tests"]`), `uv`.

**Spec:** `docs/superpowers/specs/task-cap-the-e2e-tier-and-3aa663b8-design.md` (prepended verbatim above). Parent source of truth: `docs/superpowers/specs/2026-10-02-test-tier-design.md`, decisions V1 and V2.

## Global Constraints

- The e2e cap is exactly 5 (`E2E_CAP = 5`); the justification marker is a docstring line starting `justification:` after leading whitespace is stripped (case-sensitive, start of line only).
- The cap check counts items before `-m` deselection, so it fires in the default run and under `-m e2e`. All violations go in one `pytest.UsageError`.
- The skip reason is exactly `the {name} CLI must be installed for the {name} tier`, for `name` in `git`, `brd`, checked in that order (first missing wins).
- `toolchain` in `tests/e2e/conftest.py` stays module-scoped and keeps its message `the {tool} CLI must be installed for the e2e tier`.
- Do not touch `pyproject.toml`, the directory auto-mark `pytest_collection_modifyitems`, the PATH shim, the budget `pytest_runtest_makereport`, or the 3202b0b0 bulk re-marking. Only `requires_*`/`skipif(shutil.which(...))` lines change in test modules.
- Do NOT add `justification:` lines to the 5 existing `e2e` tests (V9's work). Do not weaken the check to get a green default run.
- Out of scope: pytest-xdist, pygents engine, checkpoint format, harness adapter contract, `dispatch.py` LauncherFn seam, milestone 14 `am run --board`, V3 FakeBoard/`run_brd`, V4–V8.
- Tier placement (V1): helper/source-scan tests are unit tier (no marker, no subprocess, <=0.5s each). Tests that run `pytester.runpytest_subprocess()` carry `@pytest.mark.git` (<=2s each). All new tests go in `tests/test_tier_guards.py`, beside the sibling's pytester tests and its `run_nested` helper.
- Verification command for this repo: `uv run pytest`.

## Notes on the spec (read before Task 1)

1. **Two module-level copies the spec's list omits.** `tests/test_integration.py:48-51` (`pytestmark = pytest.mark.skipif(shutil.which("git") is None, ...)`) and `tests/test_integrate_workflow.py:40-46` (`pytestmark = [pytest.mark.git, pytest.mark.skipif(shutil.which("git") is None, ...)]`) are the remaining two of the card's "~14 copies" (11 listed definitions + these 2 + `toolchain`). The spec's own source-scan test ("no `skipif(shutil.which(` remain anywhere under `tests/`") forbids them once the scan tolerates the line break after `skipif(`, so Task 5 removes them. In `test_integration.py` every git-using test is already `@pytest.mark.git` (lines 384-756); the two unmarked tests (`:642`, `:815`) already pass in the unit tier under 3202b0b0's stub shim.
2. **The nested skip test runs without `-m brd`.** The spec asks for a `brd` test "run with `-m brd`" and an unmarked test "in the same session is not skipped"; under `-m brd` the unmarked one would be deselected, not run. The nested pytester session has its own rootdir and ini (`NESTED_INI`), so it has no addopts; running it with no `-m` selects everything, which lets one session prove both halves.
3. **The default run is blocked by design until V9.** After Task 2, `uv run pytest` exits 4 (usage error) listing the 5 existing `e2e` tests as unjustified. Per the spec this is expected and must not be worked around. For the "otherwise green" check, this plan uses `uv run pytest --ignore-glob='*/e2e/test_real_harness*.py'`, which removes those four modules from collection without touching the check. Report both results in the PR.
4. **`missing_binary`'s `which` default is `None`, resolved to `shutil.which` at call time** (the spec's "for example" signature defaults to `shutil.which` at definition time). Resolving it at call time lets the hook's unit test monkeypatch `shutil.which` without a subprocess.
5. **The cap check is a registered plugin object, not a second module function**, because `tests/conftest.py` already defines `pytest_collection_modifyitems` (the sibling's auto-mark) and a module cannot hold two functions of that name. Putting the check inside the existing hook would break the sibling's unit test `test_hook_reads_the_inherited_marker_chain_not_only_own_markers` (`tests/test_conftest_tiers.py:141`), which passes a docstring-less fake item carrying `e2e`. The check reads only the `e2e` marker, which the auto-mark never adds, so the two hooks' relative order does not matter; `tryfirst` keeps it ahead of pytest's own `-m` deselection.
6. The orientation summary handed to this planner was truncated at 2000 characters. This plan was written from the spec file on disk, which is complete; nothing was guessed from the truncated text.

## Review Focus

- A parametrized `e2e` function counts once per parameter set, and a module-level `pytestmark = pytest.mark.e2e` puts every test in the module in the tier. Expected: 6 parametrizations of one function fail the cap. Pinned by `test_parametrized_module_marked_e2e_counts_each_parametrization` (Task 2).
- `e2e_fake` is a different tier and must never count toward the `e2e` cap, even though the name contains `e2e`. Pinned by `test_e2e_fake_items_never_count_toward_the_cap` (Task 1).
- Exactly 5 justified `e2e` items is allowed. Under the default selection the run then proceeds normally, with the e2e items deselected and not an error. Pinned by `test_five_justified_e2e_tests_collect_and_are_deselected_by_default` (Task 2).
- When an item is marked both `git` and `brd` and both binaries are missing, it gets one skip that names `git`. Pinned by the `test_needs_both` item in `test_missing_binaries_skip_their_tier_and_leave_unmarked_tests_alone` (Task 3).
- `skipif(` followed by a line break and then `shutil.which(` is still a copy. The source scan must catch the multi-line form used in `test_integration.py` and `test_integrate_workflow.py`. Pinned by the whitespace-tolerant regex in `test_no_local_binary_skip_copies_remain_under_tests` (Task 5).

---

### Task 1: The pure e2e cap/justification helper

**Files:**
- Modify: `tests/conftest.py` (insert after line 140, the end of `pytest_collection_modifyitems`, before `UNIT_BUDGET_S = 0.5`)
- Test: `tests/test_tier_guards.py` (unit tier, no marker)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `E2E_CAP: int = 5`, `JUSTIFICATION_PREFIX: str = "justification:"`, `has_justification(docstring: str | None) -> bool`, `e2e_tier_violations(items: Iterable[tuple[str, Iterable[str], str | None]]) -> list[str]`. Violation strings are exactly `f"the e2e tier is capped at {E2E_CAP} tests, but {count} carry the e2e marker: {', '.join(nodeids)}"` (first, when over the cap) followed by one `f"{nodeid}: an e2e test's docstring needs a line starting `justification:`"` per unjustified item, in input order.

- [ ] **Step 1: Write the failing tests**

In `tests/test_tier_guards.py`, replace the module docstring and the `from conftest import (...)` block (lines 1-25) with:

```python
"""Tier guards in `tests/conftest.py`: the unit-tier PATH shim, the per-test budget,
the e2e cap and justification check, and the git/brd binary skip.

The helpers, the stub text and the source scans are pure, so their tests carry
no tier marker and run in the unit tier. The hook-level tests start a nested
pytest in a subprocess (pytester) that execs the stub scripts, so they are
marked `git`: a subprocess cannot be unit tier, and the marker keeps them out of
the very shim they police. They touch no real git, brd or claude. This file sits
at the top of `tests/`, so the directory auto-mark leaves it alone.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import (
    E2E_CAP,
    GIT_BUDGET_S,
    STUB_EXIT_CODE,
    STUB_NAMES,
    UNIT_BUDGET_S,
    e2e_tier_violations,
    has_justification,
    stub_script,
    tier_budget_violation,
)
```

Then append to the end of `tests/test_tier_guards.py`:

```python
# ── the e2e cap and justification check ─────────────────────────────────────

JUSTIFIED = "Drives the real claude.\n\njustification: only the real binary shows this.\n"
UNJUSTIFIED_MESSAGE = "an e2e test's docstring needs a line starting `justification:`"


def _e2e(nodeid: str, doc: str | None = JUSTIFIED) -> tuple[str, list[str], str | None]:
    return (nodeid, ["e2e"], doc)


def test_e2e_cap_matches_the_v2_table():
    assert E2E_CAP == 5


def test_five_justified_e2e_items_and_any_non_e2e_items_pass():
    items = [_e2e(f"t.py::test_e2e_{i}") for i in range(5)]
    items += [(f"t.py::test_plain_{i}", ["git", "parametrize"], None) for i in range(50)]
    assert e2e_tier_violations(items) == []


def test_e2e_fake_items_never_count_toward_the_cap():
    items = [(f"t.py::test_fake_{i}", ["e2e_fake"], None) for i in range(9)]
    assert e2e_tier_violations(items) == []


def test_a_sixth_e2e_item_is_a_cap_violation_naming_every_nodeid():
    items = [_e2e(f"t.py::test_e2e_{i}") for i in range(6)]
    assert e2e_tier_violations(items) == [
        "the e2e tier is capped at 5 tests, but 6 carry the e2e marker: "
        + ", ".join(f"t.py::test_e2e_{i}" for i in range(6))
    ]


@pytest.mark.parametrize(
    "doc",
    [None, "", "Drives the real claude.\nNo reason given.\n"],
    ids=["no-docstring", "empty-docstring", "no-justification-line"],
)
def test_an_e2e_item_without_a_justification_line_is_a_violation(doc):
    assert e2e_tier_violations([_e2e("t.py::test_paid", doc)]) == [
        f"t.py::test_paid: {UNJUSTIFIED_MESSAGE}"
    ]


def test_an_indented_justification_line_passes():
    doc = "Drives the real claude.\n\n    justification: only the real binary shows this.\n    "
    assert has_justification(doc)
    assert e2e_tier_violations([_e2e("t.py::test_paid", doc)]) == []


def test_justification_mid_line_does_not_count():
    doc = "Needs a justification: but not at the start of the line.\n"
    assert not has_justification(doc)
    assert e2e_tier_violations([_e2e("t.py::test_paid", doc)]) == [
        f"t.py::test_paid: {UNJUSTIFIED_MESSAGE}"
    ]


def test_cap_and_justification_violations_are_reported_together():
    items = [_e2e(f"t.py::test_e2e_{i}") for i in range(5)] + [_e2e("t.py::test_bare", None)]
    violations = e2e_tier_violations(items)
    assert len(violations) == 2
    assert violations[0].startswith("the e2e tier is capped at 5 tests, but 6 carry the e2e marker: ")
    assert violations[1] == f"t.py::test_bare: {UNJUSTIFIED_MESSAGE}"


def test_marker_names_may_be_any_iterable():
    assert e2e_tier_violations([("t.py::test_paid", iter(["e2e"]), JUSTIFIED)]) == []
    assert e2e_tier_violations([("t.py::test_plain", iter(["skipif"]), None)]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tier_guards.py -v`
Expected: collection ERROR for `tests/test_tier_guards.py` with `ImportError: cannot import name 'E2E_CAP' from 'conftest'`.

- [ ] **Step 3: Write the minimal implementation**

In `tests/conftest.py`, insert between the end of `pytest_collection_modifyitems` (line 140, `item.add_marker(marker)`) and `UNIT_BUDGET_S = 0.5`:

```python
E2E_CAP = 5
JUSTIFICATION_PREFIX = "justification:"


def has_justification(docstring: str | None) -> bool:
    """True when some line of `docstring`, leading whitespace stripped, starts `justification:`."""
    if not docstring:
        return False
    return any(line.lstrip().startswith(JUSTIFICATION_PREFIX) for line in docstring.splitlines())


def e2e_tier_violations(items: Iterable[tuple[str, Iterable[str], str | None]]) -> list[str]:
    """Every e2e-tier rule `items` break, as messages; empty when none.

    Each item is a (nodeid, marker names on its chain, docstring) triple. Only the
    exact `e2e` marker counts (`e2e_fake` is another tier). Over the cap gives one
    message naming the count, the cap and every e2e nodeid; each e2e item with no
    `justification:` line gives one more, in input order.
    """
    e2e = [(nodeid, doc) for nodeid, markers, doc in items if "e2e" in set(markers)]
    violations: list[str] = []
    if len(e2e) > E2E_CAP:
        nodeids = ", ".join(nodeid for nodeid, _ in e2e)
        violations.append(
            f"the e2e tier is capped at {E2E_CAP} tests, but {len(e2e)} carry the e2e marker: {nodeids}"
        )
    for nodeid, doc in e2e:
        if not has_justification(doc):
            violations.append(
                f"{nodeid}: an e2e test's docstring needs a line starting `{JUSTIFICATION_PREFIX}`"
            )
    return violations
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tier_guards.py tests/test_conftest_tiers.py -v`
Expected: PASS for every test (the sibling's existing tests included).

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_tier_guards.py
git commit -m "Add the pure e2e cap and justification helper"
```

---

### Task 2: Enforce the cap at collection through a registered plugin

**Files:**
- Modify: `tests/conftest.py` (module docstring lines 1-19; insert after `e2e_tier_violations` from Task 1)
- Test: `tests/test_tier_guards.py` (unit tests unmarked; pytester tests `@pytest.mark.git`; `run_nested` at lines 46-57 gains `*args`)

**Interfaces:**
- Consumes: `e2e_tier_violations`, `E2E_CAP` (Task 1).
- Produces: `item_docstring(item: object) -> str | None`; `class E2ETierCap` with hook method `pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None` that raises `pytest.UsageError("e2e tier check failed:\n" + "\n".join(violations))`; `E2E_CAP_PLUGIN_NAME = "agent-manager-e2e-tier-cap"`; `pytest_configure(config)` registering it once. In `tests/test_tier_guards.py`: `run_nested(pytester, source, *args)`, `DEFAULT_SELECTION`, `_Mark`, `_FakeItem` (used again in Task 3).

- [ ] **Step 1: Write the failing tests**

In `tests/test_tier_guards.py`, extend the `from conftest import (...)` block to:

```python
from conftest import (
    E2E_CAP,
    GIT_BUDGET_S,
    STUB_EXIT_CODE,
    STUB_NAMES,
    UNIT_BUDGET_S,
    E2ETierCap,
    e2e_tier_violations,
    has_justification,
    item_docstring,
    stub_script,
    tier_budget_violation,
)
```

Replace `run_nested` (lines 46-57) with:

```python
def run_nested(pytester: pytest.Pytester, source: str, *args: str) -> pytest.RunResult:
    """Run `source` as `test_nested.py` under a copy of the real tests/conftest.py.

    A subprocess, not in-process: tests/test_conftest_tiers.py has already put
    tests/conftest.py into `sys.modules["conftest"]`, so an in-process nested
    conftest import would hit ImportPathMismatchError. The child inherits this
    process's environment, including `PATH`. `args` go to the nested pytest.
    """
    pytester.makeconftest(REAL_CONFTEST.read_text())
    pytester.makeini(NESTED_INI)
    pytester.makepyfile(test_nested=source)
    return pytester.runpytest_subprocess(*args)
```

Append to the end of `tests/test_tier_guards.py`:

```python
# The nested session reads NESTED_INI, not pyproject.toml, so it has no addopts;
# the cap tests pass the default selection explicitly to prove the check runs
# before deselection.
DEFAULT_SELECTION = "not brd and not e2e_fake and not soak and not e2e"


def test_default_selection_matches_pyproject_addopts(pytestconfig):
    assert DEFAULT_SELECTION in pytestconfig.getini("addopts")


class _Mark:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeItem:
    """The surface the new hooks read: `nodeid`, the marker chain, the test function."""

    def __init__(self, nodeid: str, chain: tuple[str, ...] | list[str] = (), doc: str | None = None) -> None:
        self.nodeid = nodeid
        self._chain = [_Mark(name) for name in chain]

        def function() -> None:
            pass

        function.__doc__ = doc
        self.function = function

    def iter_markers(self, name: str | None = None):
        return iter(m for m in self._chain if name is None or m.name == name)


def test_item_docstring_reads_the_underlying_function():
    assert item_docstring(_FakeItem("t.py::test_paid", doc=JUSTIFIED)) == JUSTIFIED
    assert item_docstring(_FakeItem("t.py::test_bare")) is None
    assert item_docstring(object()) is None


def test_cap_plugin_passes_five_justified_e2e_items():
    items = [_FakeItem(f"t.py::test_{i}", chain=["skipif", "e2e"], doc=JUSTIFIED) for i in range(5)]
    E2ETierCap().pytest_collection_modifyitems(items)


def test_cap_plugin_raises_one_usage_error_listing_every_violation():
    items = [_FakeItem(f"t.py::test_{i}", chain=["e2e"], doc=JUSTIFIED) for i in range(6)]
    items.append(_FakeItem("t.py::test_bare", chain=["e2e"]))
    with pytest.raises(pytest.UsageError) as excinfo:
        E2ETierCap().pytest_collection_modifyitems(items)
    lines = str(excinfo.value).splitlines()
    assert lines[0] == "e2e tier check failed:"
    assert lines[1].startswith(f"the e2e tier is capped at {E2E_CAP} tests, but 7 carry the e2e marker: ")
    assert lines[2] == f"t.py::test_bare: {UNJUSTIFIED_MESSAGE}"
    assert len(lines) == 3


@pytest.mark.git
def test_six_e2e_tests_fail_collection_under_the_default_selection(pytester):
    source = "import pytest\n" + "".join(
        f"\n@pytest.mark.e2e\ndef test_paid_{i}():\n    '''justification: only real claude shows this.'''\n"
        for i in range(6)
    )
    result = run_nested(pytester, source, "-m", DEFAULT_SELECTION)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*the e2e tier is capped at 5 tests, but 6 carry the e2e marker*"])


@pytest.mark.git
def test_parametrized_module_marked_e2e_counts_each_parametrization(pytester):
    result = run_nested(
        pytester,
        """
        import pytest

        pytestmark = pytest.mark.e2e

        @pytest.mark.parametrize("n", range(6))
        def test_paid(n):
            '''justification: only real claude shows this.'''
        """,
        "-m",
        DEFAULT_SELECTION,
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*the e2e tier is capped at 5 tests, but 6 carry the e2e marker*"])


@pytest.mark.git
def test_an_unjustified_e2e_test_fails_collection_under_the_default_selection(pytester):
    result = run_nested(
        pytester,
        """
        import pytest

        @pytest.mark.e2e
        def test_unjustified():
            '''Drives the real claude, with no reason given.'''

        @pytest.mark.e2e
        def test_justified():
            '''Drives the real claude.

            justification: only real claude shows this.
            '''

        def test_unmarked():
            pass
        """,
        "-m",
        DEFAULT_SELECTION,
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*test_nested.py::test_unjustified: {UNJUSTIFIED_MESSAGE}*"])
    result.stderr.no_fnmatch_line("*test_nested.py::test_justified:*")


@pytest.mark.git
def test_five_justified_e2e_tests_collect_and_are_deselected_by_default(pytester):
    source = "import pytest\n\ndef test_unmarked():\n    pass\n" + "".join(
        f"\n@pytest.mark.e2e\ndef test_paid_{i}():\n    '''justification: only real claude shows this.'''\n"
        for i in range(5)
    )
    result = run_nested(pytester, source, "-m", DEFAULT_SELECTION)
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, deselected=5)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tier_guards.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'E2ETierCap' from 'conftest'`.

- [ ] **Step 3: Write the minimal implementation**

In `tests/conftest.py`, insert directly after `e2e_tier_violations` (Task 1):

```python
def item_docstring(item: object) -> str | None:
    """The docstring of the Python function behind `item`, or None.

    Each parametrization of a function is its own item with that same function,
    so all of them share its docstring. Non-function items have no docstring.
    """
    return getattr(getattr(item, "function", None), "__doc__", None)


class E2ETierCap:
    """The collection-time e2e cap and justification check, as its own plugin.

    A plugin object because this module already defines
    `pytest_collection_modifyitems` for the directory auto-mark. `tryfirst` puts
    it ahead of pytest's own `-m` deselection, so the default run (which
    deselects `e2e`) still sees every e2e item. It reads only the `e2e` marker,
    which the auto-mark never adds, so its order against the auto-mark does not
    matter.
    """

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        violations = e2e_tier_violations(
            (item.nodeid, [mark.name for mark in item.iter_markers()], item_docstring(item))
            for item in items
        )
        if violations:
            raise pytest.UsageError("e2e tier check failed:\n" + "\n".join(violations))


E2E_CAP_PLUGIN_NAME = "agent-manager-e2e-tier-cap"


def pytest_configure(config: pytest.Config) -> None:
    """Register the e2e cap check once per session."""
    if not config.pluginmanager.has_plugin(E2E_CAP_PLUGIN_NAME):
        config.pluginmanager.register(E2ETierCap(), E2E_CAP_PLUGIN_NAME)
```

Then in the module docstring of `tests/conftest.py`, replace:

```python
A passing call phase over its tier's budget (unit 0.5s, `git` 2s; the opt-in tiers
have none) is turned into a failure naming the tier, budget and measured time.
"""
```

with:

```python
A passing call phase over its tier's budget (unit 0.5s, `git` 2s; the opt-in tiers
have none) is turned into a failure naming the tier, budget and measured time.

At collection, before `-m` deselection, the run fails with a usage error when more
than 5 items carry `e2e` or any `e2e` item's docstring has no line starting
`justification:`, so the check also fires in the default run.
"""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tier_guards.py tests/test_conftest_tiers.py -v`
Expected: PASS for every test.

- [ ] **Step 5: Confirm the expected default-run block**

Run: `uv run pytest`
Expected: exit code 4; stderr starts `ERROR: e2e tier check failed:` and lists exactly 5 lines of the form `tests/e2e/test_real_harness*.py::<test>: an e2e test's docstring needs a line starting `justification:``, and no `capped at` line. This is the spec's expected state until V9. Do not change the check or the e2e tests.

Run: `uv run pytest --ignore-glob='*/e2e/test_real_harness*.py'`
Expected: PASS (same results as before this task plus the new tests).

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_tier_guards.py
git commit -m "Fail collection when the e2e tier is over its cap or unjustified"
```

---

### Task 3: The marker-driven git/brd skip hook

**Files:**
- Modify: `tests/conftest.py` (imports at lines 23-25; module docstring; append at end of file after `pytest_runtest_makereport`)
- Test: `tests/test_tier_guards.py`

**Interfaces:**
- Consumes: `_FakeItem`, `run_nested(pytester, source, *args)` (Task 2).
- Produces: `BINARY_TIERS: tuple[str, ...] = ("git", "brd")`, `missing_binary(markers: Iterable[str], which: Callable[[str], str | None] | None = None) -> str | None`, `binary_skip_reason(name: str) -> str`, module-level hook `pytest_runtest_setup(item: pytest.Item) -> None`. Task 4 imports `BINARY_TIERS` and `missing_binary`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_tier_guards.py`, change the imports at the top to:

```python
import os
import shutil
from pathlib import Path

import pytest

from conftest import (
    BINARY_TIERS,
    E2E_CAP,
    GIT_BUDGET_S,
    STUB_EXIT_CODE,
    STUB_NAMES,
    UNIT_BUDGET_S,
    E2ETierCap,
    binary_skip_reason,
    e2e_tier_violations,
    has_justification,
    item_docstring,
    missing_binary,
    pytest_runtest_setup,
    stub_script,
    tier_budget_violation,
)
```

Append to the end of `tests/test_tier_guards.py`:

```python
# ── the git/brd binary skip ─────────────────────────────────────────────────


def _nothing_installed(name: str) -> str | None:
    return None


def _everything_installed(name: str) -> str | None:
    return f"/usr/bin/{name}"


def test_binary_tiers_are_git_then_brd():
    assert BINARY_TIERS == ("git", "brd")
    assert binary_skip_reason("brd") == "the brd CLI must be installed for the brd tier"


@pytest.mark.parametrize(
    "markers",
    [set(), {"parametrize", "skipif"}, {"soak"}, {"e2e_fake"}, {"e2e"}],
    ids=["none", "non-tier", "soak", "e2e_fake", "e2e"],
)
def test_items_without_git_or_brd_are_never_skipped(markers):
    assert missing_binary(markers, which=_nothing_installed) is None


@pytest.mark.parametrize("name", ["git", "brd"])
def test_a_marked_item_names_its_missing_binary(name):
    assert missing_binary({name, "parametrize"}, which=_nothing_installed) == name
    assert missing_binary({name}, which=_everything_installed) is None


def test_both_missing_names_git_first_and_only_the_missing_one_otherwise():
    assert missing_binary(["brd", "git"], which=_nothing_installed) == "git"
    only_brd_missing = lambda name: None if name == "brd" else f"/usr/bin/{name}"  # noqa: E731
    assert missing_binary(iter(["git", "brd"]), which=only_brd_missing) == "brd"


def test_missing_binary_defaults_to_shutil_which_at_call_time(monkeypatch):
    monkeypatch.setattr(shutil, "which", _nothing_installed)
    assert missing_binary({"git"}) == "git"
    monkeypatch.setattr(shutil, "which", _everything_installed)
    assert missing_binary({"git"}) is None


def test_the_hook_skips_a_brd_item_when_brd_is_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", _nothing_installed)
    with pytest.raises(pytest.skip.Exception, match="the brd CLI must be installed for the brd tier"):
        pytest_runtest_setup(_FakeItem("t.py::test_board", chain=["brd"]))


def test_the_hook_leaves_unmarked_and_satisfied_items_alone(monkeypatch):
    monkeypatch.setattr(shutil, "which", _nothing_installed)
    pytest_runtest_setup(_FakeItem("t.py::test_pure"))
    monkeypatch.setattr(shutil, "which", _everything_installed)
    pytest_runtest_setup(_FakeItem("t.py::test_board", chain=["brd", "git"]))


@pytest.mark.git
def test_missing_binaries_skip_their_tier_and_leave_unmarked_tests_alone(pytester, tmp_path, monkeypatch):
    # No `-m`: the nested session has no addopts, so every test is selected and
    # the unmarked one can show it is not skipped. PATH holds an empty directory,
    # so neither git nor brd is found; python is spawned by absolute path.
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    monkeypatch.setenv("PATH", str(bare))
    result = run_nested(
        pytester,
        """
        import pytest

        @pytest.mark.brd
        def test_needs_brd():
            pass

        @pytest.mark.git
        def test_needs_git():
            pass

        @pytest.mark.brd
        @pytest.mark.git
        def test_needs_both():
            pass

        def test_unmarked():
            pass
        """,
        "-rs",
    )
    result.assert_outcomes(passed=1, skipped=3)
    lines = result.stdout.lines
    assert sum("the brd CLI must be installed for the brd tier" in line for line in lines) == 1
    assert sum("the git CLI must be installed for the git tier" in line for line in lines) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tier_guards.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'BINARY_TIERS' from 'conftest'`.

- [ ] **Step 3: Write the minimal implementation**

In `tests/conftest.py`, change the imports (lines 23-25) to:

```python
import os
import shutil
from collections.abc import Callable, Generator, Iterable
from pathlib import Path, PurePath
```

Append to the end of `tests/conftest.py`:

```python
BINARY_TIERS = ("git", "brd")
"""The tiers whose marker means "needs this binary on PATH", in skip-reason order."""


def missing_binary(
    markers: Iterable[str], which: Callable[[str], str | None] | None = None
) -> str | None:
    """The first of `git`, `brd` that `markers` names and `which` cannot find, or None.

    `markers` is every marker name on the item's chain. `which` defaults to
    `shutil.which`, looked up at call time. Items with neither marker are never
    reported, whatever `which` says.
    """
    names = set(markers)
    lookup = shutil.which if which is None else which
    for name in BINARY_TIERS:
        if name in names and lookup(name) is None:
            return name
    return None


def binary_skip_reason(name: str) -> str:
    return f"the {name} CLI must be installed for the {name} tier"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip a `git`- or `brd`-marked item whose binary is not on PATH.

    Runs after collection, so the auto-added `git` on tests/steps/ items counts,
    and before fixture setup, so no fixture spawns a missing binary first.
    """
    missing = missing_binary(mark.name for mark in item.iter_markers())
    if missing is not None:
        pytest.skip(binary_skip_reason(missing))
```

In the module docstring of `tests/conftest.py`, replace:

```python
At collection, before `-m` deselection, the run fails with a usage error when more
than 5 items carry `e2e` or any `e2e` item's docstring has no line starting
`justification:`, so the check also fires in the default run.
"""
```

with:

```python
At collection, before `-m` deselection, the run fails with a usage error when more
than 5 items carry `e2e` or any `e2e` item's docstring has no line starting
`justification:`, so the check also fires in the default run.

An item marked `git` or `brd` is skipped at setup when that binary is not on
`PATH`; this is the suite's only such check (tests/e2e/conftest.py's `toolchain`
calls the same `missing_binary`).
"""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tier_guards.py tests/test_conftest_tiers.py -v`
Expected: PASS for every test.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_tier_guards.py
git commit -m "Skip git- and brd-marked tests when their binary is missing"
```

---

### Task 4: Point the e2e `toolchain` gate at the shared check

**Files:**
- Modify: `tests/e2e/conftest.py:11-25` (imports) and `:143-148` (`toolchain`)
- Test: `tests/test_tier_guards.py` (unit tier source check)

**Interfaces:**
- Consumes: `BINARY_TIERS`, `missing_binary` (Task 3), imported in `tests/e2e/conftest.py` as `from conftest import BINARY_TIERS, missing_binary` (resolved through `pythonpath = ["tests"]`, the same way the test modules import it).
- Produces: nothing new. `toolchain` keeps `scope="module"` and the message `the {tool} CLI must be installed for the e2e tier`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tier_guards.py`:

```python
E2E_CONFTEST = Path(__file__).resolve().parent / "e2e" / "conftest.py"


def test_the_e2e_toolchain_gate_uses_the_shared_binary_check():
    source = E2E_CONFTEST.read_text(encoding="utf-8")
    assert "from conftest import BINARY_TIERS, missing_binary" in source
    assert "missing_binary(BINARY_TIERS)" in source
    assert 'pytest.skip(f"the {missing} CLI must be installed for the e2e tier")' in source
    assert "shutil.which" not in source
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tier_guards.py::test_the_e2e_toolchain_gate_uses_the_shared_binary_check -v`
Expected: FAIL on the first assert (`from conftest import BINARY_TIERS, missing_binary` not in source).

- [ ] **Step 3: Write the minimal implementation**

In `tests/e2e/conftest.py`, replace:

```python
import json
import os
import shutil
import subprocess
```

with:

```python
import json
import os
import subprocess
```

and replace:

```python
import pytest
from typer.testing import CliRunner

from agent_manager import board, census, cli, dag, models, paths, store
```

with:

```python
import pytest
from conftest import BINARY_TIERS, missing_binary
from typer.testing import CliRunner

from agent_manager import board, census, cli, dag, models, paths, store
```

and replace:

```python
@pytest.fixture(scope="module")
def toolchain() -> None:
    """Skip the whole module when the real CLIs this tier needs are missing."""
    for tool in ("git", "brd"):
        if shutil.which(tool) is None:
            pytest.skip(f"the {tool} CLI must be installed for the e2e tier")
```

with:

```python
@pytest.fixture(scope="module")
def toolchain() -> None:
    """Skip the whole module when the real CLIs this tier needs are missing.

    A fixture, not a marker: these items carry neither `git` nor `brd` (adding
    one would stop the directory auto-mark and pull them into the default run),
    so the root conftest's skip hook never fires for them. The check itself is
    the root conftest's `missing_binary`, the suite's one binary-presence check.
    """
    missing = missing_binary(BINARY_TIERS)
    if missing is not None:
        pytest.skip(f"the {missing} CLI must be installed for the e2e tier")
```

- [ ] **Step 4: Run the tests to verify they pass and the e2e conftest still loads**

Run: `uv run pytest tests/test_tier_guards.py -v`
Expected: PASS.

Run: `uv run pytest tests/e2e --ignore-glob='*/e2e/test_real_harness*.py' -m e2e_fake --collect-only -q`
Expected: exit 0, a list of `tests/e2e/...` items, no `ImportError` from `tests/e2e/conftest.py`.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/conftest.py tests/test_tier_guards.py
git commit -m "Route the e2e toolchain gate through the shared binary check"
```

---

### Task 5: Delete every local binary-skip copy

**Files:**
- Modify: `tests/test_orchestrate.py:27,650-657` and 123 `@requires_git`/`@requires_brd` pairs
- Modify: `tests/test_cli.py:19,1193-1200,3085` and its `@requires_git`/`@requires_brd` pairs
- Modify: `tests/test_board.py:12,22-25,592` and its `@requires_brd` lines
- Modify: `tests/test_bases.py:22,39-42,491` and its `@requires_git` lines
- Modify: `tests/test_integration.py:23,48-51`
- Modify: `tests/test_integrate_workflow.py:21,40-46`
- Modify: `tests/steps/test_docs_commit.py:10,19-22`, `tests/steps/test_verify.py:19,37-40`, `tests/steps/test_worktree.py:10,24-27`, `tests/steps/test_integrate.py:13,27-30` and their `@requires_git` lines
- Modify: `tests/steps/test_rollup.py:15-18,27,41-44` and its 17 `@requires_brd` lines
- Test: `tests/test_tier_guards.py` (unit tier source scan)

**Interfaces:**
- Consumes: the Task 3 hook (the `git`/`brd` markers now do the skipping).
- Produces: no `requires_git`/`requires_brd` name and no `skipif(` + `shutil.which(` anywhere under `tests/`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tier_guards.py` (also add `import re` to the stdlib imports at the top, between `import os` and `import shutil`):

```python
TESTS_ROOT = Path(__file__).resolve().parent
_LOCAL_SKIP_COPIES = (
    re.compile(r"\brequires_(?:git|brd)\b"),
    re.compile(r"skipif\(\s*shutil\.which\("),
)


def test_no_local_binary_skip_copies_remain_under_tests():
    # This file is skipped: it holds the patterns themselves.
    offenders = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _LOCAL_SKIP_COPIES:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(TESTS_ROOT)}:{line}: {match.group(0)!r}")
    assert offenders == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tier_guards.py::test_no_local_binary_skip_copies_remain_under_tests -v`
Expected: FAIL; the `offenders` list names `test_bases.py:39`, `test_board.py:22`, `test_cli.py:1193`, `test_integrate_workflow.py:42`, `test_integration.py:48`, `test_orchestrate.py:650`, the `steps/` files, and every decorator line.

- [ ] **Step 3: `tests/test_orchestrate.py` (every occurrence is a `brd`-marked test needing both)**

Every `@requires_git` in this file sits directly above `@requires_brd`, under a `@pytest.mark.brd` (lines 1172-6139, including `:5091-5094` and `:5344-5347` with a `parametrize` between). Per the spec: `@requires_git` on a `brd` item becomes `@pytest.mark.git`, `@requires_brd` on a `brd` item is deleted. Use Edit with `replace_all: true`:

old:
```python
@requires_git
@requires_brd
```
new:
```python
@pytest.mark.git
```

Then replace (Edit, single occurrence):

```python
requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the runner's steps-tier fixtures",
)
requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the runner's steps-tier fixtures",
)

STARTED_AT = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
```

with:

```python
STARTED_AT = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
```

and replace `import shlex\nimport shutil\nimport socket\n` with `import shlex\nimport socket\n`.

- [ ] **Step 4: `tests/test_cli.py` (pairs on `brd` items, plus one unmarked straggler)**

Every pair in this file sits under `@pytest.mark.brd` (lines 1309-7109, including `:6200` and `:6676-6679`). Edit with `replace_all: true`:

old:
```python
@requires_git
@requires_brd
```
new:
```python
@pytest.mark.git
```

The straggler at `:3085` has no tier marker, so it gets `brd`. Replace:

```python
@requires_brd
def test_a_milestone_dry_run_outside_a_brd_project_is_a_board_error(tmp_path, monkeypatch):
```

with:

```python
@pytest.mark.brd
def test_a_milestone_dry_run_outside_a_brd_project_is_a_board_error(tmp_path, monkeypatch):
```

Replace the definitions:

```python
requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the CLI's steps-tier fixtures",
)
requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the CLI's steps-tier fixtures",
)


def _git(cwd: Path, *args: str) -> str:
```

with:

```python
def _git(cwd: Path, *args: str) -> str:
```

and replace `import os\nimport shutil\n` with `import os\n`.

- [ ] **Step 5: `tests/test_board.py` (`brd` items, plus the `soak` straggler)**

Edit with `replace_all: true`:

old:
```python
@pytest.mark.brd
@requires_brd
```
new:
```python
@pytest.mark.brd
```

The straggler at `:591-592` is `soak` only, so it gets `brd` beside `soak`. Replace:

```python
@pytest.mark.soak
@requires_brd
def test_brd_update_survives_concurrent_writers(temp_board):
```

with:

```python
@pytest.mark.soak
@pytest.mark.brd
def test_brd_update_survives_concurrent_writers(temp_board):
```

Replace:

```python
from agent_manager import board, census, locks, models

requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the board adapter's steps-tier tests",
)


def test_show_argv_is_a_list_of_plain_arguments():
```

with:

```python
from agent_manager import board, census, locks, models


def test_show_argv_is_a_list_of_plain_arguments():
```

and replace `import json\nimport shutil\nimport subprocess\n` with `import json\nimport subprocess\n`.

- [ ] **Step 6: `tests/test_bases.py` (every occurrence is already `git`)**

Edit with `replace_all: true`:

old:
```python
@pytest.mark.git
@requires_git
```
new:
```python
@pytest.mark.git
```

The one at `:491` sits above a `parametrize` whose function is already `@pytest.mark.git` (`:497`). Replace:

```python
@requires_git
@pytest.mark.parametrize(
    "tips",
```

with:

```python
@pytest.mark.parametrize(
    "tips",
```

Replace:

```python
from agent_manager.store import Checkpoint, Store

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the bases steps-tier tests",
)

BASE = "m7/base-cccccccc"
```

with:

```python
from agent_manager.store import Checkpoint, Store

BASE = "m7/base-cccccccc"
```

and replace `import json\nimport shutil\nimport subprocess\n` with `import json\nimport subprocess\n`.

- [ ] **Step 7: `tests/test_integration.py` and `tests/test_integrate_workflow.py` (module-level copies)**

In `tests/test_integration.py` every git-using test is already `@pytest.mark.git`. Replace:

```python
from agent_manager.workflow import integrate as integrate_workflow

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for integrate_milestone's tests",
)

BASE = "main"
```

with:

```python
from agent_manager.workflow import integrate as integrate_workflow

BASE = "main"
```

and replace `import shlex\nimport shutil\nimport subprocess\n` with `import shlex\nimport subprocess\n`.

In `tests/test_integrate_workflow.py`, replace:

```python
pytestmark = [
    pytest.mark.git,
    pytest.mark.skipif(
        shutil.which("git") is None,
        reason="the git CLI must be installed for the integrate workflow's engine-tier tests",
    ),
]
```

with:

```python
pytestmark = pytest.mark.git
```

and replace `import json\nimport shutil\nimport subprocess\n` with `import json\nimport subprocess\n`.

- [ ] **Step 8: The four `tests/steps/` git files (auto-marked `git`, so every decorator is deleted)**

In each of `tests/steps/test_docs_commit.py`, `tests/steps/test_verify.py`, `tests/steps/test_worktree.py`, `tests/steps/test_integrate.py`, Edit with `replace_all: true`:

old:
```python

@requires_git
```
new:
```python

```

(that is, old is `"\n@requires_git\n"` and new is `"\n"`).

Then remove each definition and import:

`tests/steps/test_docs_commit.py`: replace

```python
from agent_manager.steps import docs_commit, reducers

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the docs-commit step's steps-tier tests",
)

SPEC_RELATIVE
```

with

```python
from agent_manager.steps import docs_commit, reducers

SPEC_RELATIVE
```

and `import hashlib\nimport shutil\nimport subprocess\n` with `import hashlib\nimport subprocess\n`.

`tests/steps/test_verify.py`: replace

```python
    plain_text,
)

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the verify step's read-only test",
)


def _py(script: str) -> str:
```

with

```python
    plain_text,
)


def _py(script: str) -> str:
```

and `import shlex\nimport shutil\nimport subprocess\n` with `import shlex\nimport subprocess\n`.

`tests/steps/test_worktree.py`: replace

```python
from agent_manager.steps.worktree import GitError

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the worktree step's steps-tier tests",
)


def _git(cwd: Path, *args: str) -> str:
```

with

```python
from agent_manager.steps.worktree import GitError


def _git(cwd: Path, *args: str) -> str:
```

and `import os\nimport shutil\nimport subprocess\n` with `import os\nimport subprocess\n`.

`tests/steps/test_integrate.py`: replace

```python
from agent_manager.steps.worktree import GitError, run_git

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the integrate step's steps-tier tests",
)

BRANCH = "m5-integrate"
```

with

```python
from agent_manager.steps.worktree import GitError, run_git

BRANCH = "m5-integrate"
```

and `import os\nimport shutil\nimport subprocess\n` with `import os\nimport subprocess\n`.

- [ ] **Step 9: `tests/steps/test_rollup.py` (auto-marked `git`, so every occurrence gets `brd`)**

None of the 17 occurrences carries `brd`; three sit under `@pytest.mark.soak` (`:268-269`, `:394-395`, `:440-441`) and gain `brd` beside it. Edit with `replace_all: true`:

old:
```python
@requires_brd
```
new:
```python
@pytest.mark.brd
```

Replace the definition:

```python
from agent_manager.steps import rollup

requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the roll-up step's steps-tier tests",
)


@pytest.fixture
```

with:

```python
from agent_manager.steps import rollup


@pytest.fixture
```

Replace `import os\nimport shutil\nimport subprocess\n` with `import os\nimport subprocess\n`.

Replace the module-docstring paragraph:

```python
`tests/steps/` has no `conftest.py` (`test_verify.py` defines its own
`requires_git` marker locally), so the brd helpers are lifted from
`tests/test_board.py`: the `requires_brd` marker, the `temp_board` fixture,
`_add_card` and `_brd_json`.
```

with:

```python
`tests/steps/` has no `conftest.py`, so the brd helpers are lifted from
`tests/test_board.py`: the `temp_board` fixture, `_add_card` and `_brd_json`.
Every test that needs the real brd carries `@pytest.mark.brd`, and the root
`tests/conftest.py` skips it when `brd` is not installed.
```

- [ ] **Step 10: Run the scan and the touched modules**

Run: `uv run pytest tests/test_tier_guards.py -v`
Expected: PASS, including `test_no_local_binary_skip_copies_remain_under_tests`.

Run: `uv run pytest tests/test_orchestrate.py tests/test_cli.py tests/test_board.py tests/test_bases.py tests/test_integration.py tests/test_integrate_workflow.py tests/steps -q`
Expected: PASS; no `NameError: name 'requires_git'` / `'requires_brd'` / `'shutil'` at import.

Run: `uv run pytest tests/steps/test_rollup.py -m "not brd" --collect-only -q`
Expected: only the `fake_brd`/flock tests (`test_a_rollup_waits_for_another_process_holding_the_board_lock` through `test_the_board_lock_is_one_per_resolved_repository_and_defaults_to_the_cwd`) and the pure `test_stored_status_*`/`test_rollup_status_*` tests; none of the 17 real-board tests.

Run: `uv run pytest tests/test_orchestrate.py tests/test_cli.py tests/test_board.py tests/steps/test_rollup.py -m brd --collect-only -q`
Expected: exit 0, collects the real-brd tests (including `test_a_milestone_dry_run_outside_a_brd_project_is_a_board_error`).

If `brd` is installed, also run: `uv run pytest tests/steps/test_rollup.py tests/test_board.py -m "brd and not soak" -q`
Expected: PASS (no behaviour change for tests whose binary is present).

- [ ] **Step 11: Commit**

```bash
git add tests/test_tier_guards.py tests/test_orchestrate.py tests/test_cli.py tests/test_board.py tests/test_bases.py tests/test_integration.py tests/test_integrate_workflow.py tests/steps/test_docs_commit.py tests/steps/test_verify.py tests/steps/test_worktree.py tests/steps/test_integrate.py tests/steps/test_rollup.py
git commit -m "Replace every local requires_git/requires_brd copy with tier markers"
```

---

### Task 6: Full verification and the PR note

**Files:**
- None modified.

**Interfaces:**
- Consumes: everything above.
- Produces: the verification record and PR-description text.

- [ ] **Step 1: Run the repo's verification command**

Run: `uv run pytest`
Expected: exit code 4. stderr is `ERROR: e2e tier check failed:` followed by exactly 5 lines, each `tests/e2e/test_real_harness*.py::<test>: an e2e test's docstring needs a line starting `justification:``, from `test_real_harness.py`, `test_real_harness_integrate.py`, `test_real_harness_milestone.py` and `test_real_harness_parallel.py`. There is no `capped at` line. Any other line is a bug to fix before continuing.

- [ ] **Step 2: Run the rest of the default suite**

Run: `uv run pytest --ignore-glob='*/e2e/test_real_harness*.py'`
Expected: PASS, with no budget failures and no errors.

- [ ] **Step 3: Record the PR note**

Put this paragraph in the PR description (and in the final report for the card):

```text
Expected: `uv run pytest` fails collection (exit 4) on this branch. The new e2e check
requires a `justification:` docstring line on every `e2e` test, and the 5 existing
real-harness e2e tests (tests/e2e/test_real_harness*.py) do not have one yet. Adding
those lines is spec V9's work (docs/superpowers/specs/2026-10-02-test-tier-design.md),
owned by a sibling subtask outside this story; this branch deliberately adds no
placeholder lines. With those four modules ignored
(`uv run pytest --ignore-glob='*/e2e/test_real_harness*.py'`) the suite is green.
Also folded in beyond the spec's list: the module-level `skipif(shutil.which("git"))`
copies in tests/test_integration.py and tests/test_integrate_workflow.py.
```
