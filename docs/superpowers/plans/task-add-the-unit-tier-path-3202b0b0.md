<!-- task-pipeline: validated -->
# Subtask 3202b0b0 — Unit-tier PATH-shim guard and per-test duration check

Parent story: 186fe934 "Define and enforce the test tiers" (milestone 66ed75cd). Source of truth: `docs/superpowers/specs/2026-10-02-test-tier-design.md`, section 3 V1 (tier table and budgets) and V2 (conftest mechanics, lines 106-116). This document narrows V2 to the two pieces this card owns.

## Precondition (check before implementing)

This card is blocked_by b4edda6b (auto-mark `tests/e2e/` as `e2e_fake`, `tests/steps/` as `git`), which in turn relies on 19b291e9 (registers `git`/`brd`/`e2e_fake`/`soak` markers and the addopts `-m` exclusion). Neither is present on master's `tests/conftest.py` (72 lines) or `pyproject.toml` (only `e2e` registered). The implementation branch must have both merged before work starts; verify this explicitly. If they are missing, stop and report — do not re-implement marker registration, addopts, or the auto-mark hook here.

## Scope

Only `tests/conftest.py` (plus new test file(s) under `tests/`). No change to `src/`, `pyproject.toml`, or any CLI-observable behaviour (spec section 5). The existing `isolated_data_home` fixture and the `pytest_sessionstart`/`pytest_sessionfinish` data-dir guard stay untouched; the new code sits beside them.

"Tier marker" means any of `git`, `brd`, `e2e_fake`, `soak`, `e2e`, as resolved on the item after collection (so directory auto-marks from b4edda6b count). An item with none of these is a unit-tier item.

### 1. PATH-shim guard (autouse fixture)

- For unit-tier items only, prepend a directory holding executable stubs named `brd`, `git`, and `claude` to `PATH` via `monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")` — the same technique as `tests/steps/test_rollup.py:582-600` (`fake_brd`) and `tests/e2e/test_board_comments.py:214-232` (`brd_shim`). Unlike `brd_shim`, the stub never resolves or delegates to a real binary.
- Each stub is a shebang script, chmod 0o755, that writes a line to stderr containing `<name>: forbidden in the unit tier` and exits 99.
- The stub directory may be built once per session (stubs are identical for every test); only the `PATH` prepend is per-test, and monkeypatch restores it at teardown.
- Items with any tier marker get no shim; `PATH` is left exactly as inherited.
- A test that sets `PATH` itself after the fixture runs wins (monkeypatch stacks), consistent with how `isolated_data_home` documents its interaction with `tests/test_paths.py`.

### 2. Per-test duration budget (`pytest_runtest_makereport`)

- Applies to the `call` phase only (setup/teardown time is not counted), and only when that phase otherwise passed — an already failed or skipped report is left alone.
- Unit-tier item with call duration > 0.5s: report becomes failed. `git`-marked item with call duration > 2s: report becomes failed. `brd`, `e2e_fake`, `soak`, `e2e` items: no budget check.
- The failure text names the tier, the budget, and the measured duration (e.g. `unit-tier budget exceeded: 0.73s > 0.5s`), so the fix — speed up or re-tier — is obvious from the report.
- The tier/budget decision is factored into a small pure helper (markers + duration -> violation message or `None`) so the thresholds are unit-testable without sleeping; the hook only extracts markers/duration and applies the result.

## Error paths

- Unit test spawns `brd`/`git`/`claude` (via `subprocess`, `shutil.which`-resolved path, or any code path using `PATH` lookup): the stub runs, exits 99, stderr carries the forbidden message. Whether the test fails depends on the caller checking the return code; the card's requirement is that the real binary is never reached and the forbidden message is visible.
- Code that invokes a binary by absolute path bypasses the shim — accepted limitation of the PATH technique, not addressed here.
- Duration overrun on an otherwise-passing test: fails with the budget message above; the test body's own assertions are not re-run.

## Out of scope (owned elsewhere)

- Marker registration, addopts, `--durations` flags — 19b291e9.
- Directory auto-mark `pytest_collection_modifyitems` — b4edda6b.
- e2e cap of 5 + `justification:` check, and the `pytest_runtest_setup` skip-when-binary-missing hook replacing `requires_git`/`requires_brd` — 3aa663b8.
- `FakeBoard`/`board.py` seam (V3), moving existing slow tests between tiers, pytest-xdist, any pygents/checkpoint/adapter/`LauncherFn` change, e2e tier size, milestone 14 work.

Note: once this lands, existing unmarked tests that spawn real binaries or exceed 0.5s will fail. Re-tiering them is not this card's work, but `uv run pytest` must be green at merge; if failures appear, report them with their file:line rather than silently marking tests here — unless the fix is a one-line marker on a test that obviously belongs in another tier by the V1 rule, which should be called out in the commit.

## Tests

New file `tests/test_tier_guards.py`. Hook-level behaviour is exercised with pytest's `pytester` (in-process `runpytest`, conftest content loaded from the real `tests/conftest.py`), which needs `pytester` enabled (`pytest_plugins = ["pytester"]` in `tests/conftest.py` or the test module, whichever pytest permits for a non-root conftest).

Tier for each test follows the V1 placement rule (tier = what the test actually touches):

1. `test_budget_helper_unit_over_half_second_violates` — pure helper, no markers, 0.51s -> violation; 0.5s -> none. Tier: unit (pure function).
2. `test_budget_helper_git_over_two_seconds_violates` — `{"git"}`, 2.01s -> violation; 1.9s -> none. Tier: unit.
3. `test_budget_helper_opt_in_tiers_have_no_budget` — `brd`/`e2e_fake`/`soak`/`e2e` with 100s -> none. Tier: unit.
4. `test_unmarked_test_calling_brd_fails_with_forbidden_message` — the card's deliberately-broken test: nested unmarked test runs `subprocess.run(["brd", "--version"], check=True, capture_output=True)`; assert the nested run fails and output contains `forbidden in the unit tier` and exit code 99. Tier: `git` (explicit marker) — it spawns the stub script, a subprocess, so it cannot be unit, and the marker exempts it from the very shim it is testing; no real brd/claude is touched.
5. `test_unmarked_test_sees_stubs_for_git_and_claude` — nested unmarked test asserts `shutil.which("git")` and `shutil.which("claude")` resolve into the stub dir. Tier: `git` (nested session spawns nothing, but runs under pytester alongside test 4; keep the meta-tests together under one marker).
6. `test_marked_test_gets_real_path` — nested `@pytest.mark.git` test asserts `shutil.which("git")` is not the stub. Tier: `git`.
7. `test_slow_unmarked_test_fails_budget` — nested unmarked test sleeps 0.6s; assert it is reported failed with the unit budget message. Tier: `git` (outer wall time ~0.7s, within the 2s git budget; the 2s git-tier threshold is covered by test 2 rather than by a >2s sleep that would itself blow the outer budget).
8. `test_failing_slow_test_keeps_original_failure` — nested unmarked test sleeps 0.6s then asserts False; report shows the assertion failure, not a replaced budget message. Tier: `git`.

Verification: `uv run pytest` green on the implementation branch. Additionally, run once by hand a throwaway unmarked test outside `tests/steps/`/`tests/e2e/` that calls real `brd`, confirm it fails with `brd: forbidden in the unit tier`, then delete it (test 4 is the permanent form of this check).

---

# Unit-tier PATH-shim guard and per-test duration check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `tests/conftest.py` stop unmarked (unit-tier) tests from reaching real `brd`/`git`/`claude` via `PATH`, and fail unit tests whose call phase runs over 0.5s and `git` tests over 2s.

**Architecture:** Everything lives in `tests/conftest.py`, appended after the existing b4edda6b auto-mark section (which defines `TIER_MARKERS`). One pure helper (`tier_budget_violation`) makes the budget decision. A `pytest_runtest_makereport` wrapper applies it to passing call-phase reports. A session-scoped fixture builds the `/bin/sh` stub directory, and a function-scoped autouse fixture puts that directory at the front of `PATH` only for items with no tier marker. Tests live in a new top-level `tests/test_tier_guards.py`. The pure tests are unmarked (unit tier). The hook-level tests run a nested pytest under `pytester` and are marked `git`.

**Tech Stack:** Python 3.12, pytest >= 9.1.1 (new-style `wrapper=True` hooks, `pytester`), pytest-asyncio (already a dev dep), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/docs/superpowers/specs/task-add-the-unit-tier-path-3202b0b0-design.md` (reproduced above). Parent design: `docs/superpowers/specs/2026-10-02-test-tier-design.md` sections V1 and V2.

## Global Constraints

- Only `tests/conftest.py` and new/edited files under `tests/` change. No change to `src/`, `pyproject.toml`, or any CLI-observable behaviour.
- `isolated_data_home`, `pytest_sessionstart`, `pytest_sessionfinish`, `relative_to_tests`, `default_tier_marker` and the existing `pytest_collection_modifyitems` stay byte-for-byte as they are.
- Tier markers: exactly `TIER_MARKERS = frozenset({"git", "brd", "e2e_fake", "soak", "e2e"})`, already defined at `tests/conftest.py:83`. Reuse it; do not redefine it.
- Budgets: unit <= 0.5s per test, git <= 2s per test, measured on the `call` phase only. `brd`/`e2e_fake`/`soak`/`e2e` have no per-test budget.
- Stub: names `brd`, `git`, `claude`. Writes `<name>: forbidden in the unit tier` to stderr and exits `99`. Never delegates to a real binary.
- Do not add marker registration, addopts, `--durations`, a directory auto-mark, an e2e cap/`justification:` check, or a skip-when-binary-missing hook. Those belong to 19b291e9, b4edda6b and 3aa663b8.
- Verification command: `uv run pytest` (no lint or typecheck command exists).

## Deliberate deviations from the spec's test list (each is called out in its task)

- **Subprocess pytester, not in-process.** `tests/test_conftest_tiers.py` does `from conftest import ...` (allowed by `pythonpath = ["tests"]`), so `sys.modules["conftest"]` already holds `tests/conftest.py` by the time these tests run. An in-process nested run that imports a second `conftest.py` in prepend mode then fails with `ImportPathMismatchError`. `pytester.runpytest_subprocess()` avoids that and also guarantees the stub is exec'd by a real child process. `pytest_plugins = ["pytester"]` goes in the test module. Pytest still accepts that in test modules, while it rejects it in non-root conftests loaded after configure.
- **Test 4 does not pass `capture_output=True` to the nested `subprocess.run`.** With `capture_output=True` the stub's stderr is held inside the `CalledProcessError` and never printed, so the "forbidden" text would be invisible in the report. That defeats the card's "fail loudly" requirement. Without it, the stub's stderr goes to fd 2, pytest's fd capture records it, and it appears under "Captured stderr call".
- **Test 6 checks that `PATH` equals the inherited `PATH`, not just `shutil.which("git")`.** That is stronger ("PATH is left exactly as inherited") and works on machines without git. It also covers a class-level marker (Review Focus 5).
- **Sleeps are 0.55s rather than 0.6s.** That keeps each outer `git` test (subprocess pytest startup plus sleep) comfortably under its own 2s budget.

## Review Focus

1. An item carrying both `git` and an opt-in tier (`git`+`soak`, `git`+`brd`): a person expects the opt-in tier to win (no budget), not a 2s failure on a stress test. Pinned in Task 1 (`test_budget_helper_opt_in_tiers_have_no_budget` parametrization).
2. A duration exactly on the threshold (0.5s unit, 2.0s git): expected to pass, since the budget is "<=". Pinned in Task 1 (the `== 0.5` / `== 2.0` asserts).
3. A test with a slow fixture setup but a fast body: expected to pass, because only the call phase counts. Pinned in Task 3 (`test_slow_fixture_setup_is_not_counted`).
4. A test that sleeps past the budget and then calls `pytest.skip`: expected to stay skipped, not turn into a budget failure. Pinned in Task 3 (`test_slow_test_that_skips_stays_skipped`).
5. A tier marker inherited from a class (not on the function): expected to exempt the test from the shim, with `PATH` byte-identical to the inherited one. Pinned in Task 2 (`test_marked_test_gets_real_path`, class `TestClassMarkedBrd`).

---

### Task 0: Verify the precondition (no code)

**Files:**
- Read only: `tests/conftest.py`, `pyproject.toml`

- [ ] **Step 1: Confirm the b4edda6b auto-mark hook is on this branch**

Run: `grep -n "TIER_MARKERS = frozenset\|def default_tier_marker\|def pytest_collection_modifyitems" /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/tests/conftest.py`
Expected: three matching lines (around 83, 99, 116).

- [ ] **Step 2: Confirm the 19b291e9 markers and addopts are on this branch**

Run: `grep -n "e2e_fake:\|soak:\|brd:\|git:\|not brd and not e2e_fake and not soak and not e2e" /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/pyproject.toml`
Expected: the four marker lines (35-38) and the `addopts` line (48).

If either step finds nothing: STOP. Report "precondition failed: 19b291e9/b4edda6b not merged on m15/task-add-the-unit-tier-path-3202b0b0" and do not continue. Do not re-implement them.

- [ ] **Step 3: Record the baseline**

Run: `uv run pytest -q 2>&1 | tail -n 5`
Expected: all green. Note the pass count and wall time; Task 5 compares against them.

---

### Task 1: Pure budget helper

**Files:**
- Modify: `tests/conftest.py` (append after line 133, the end of `pytest_collection_modifyitems`)
- Create: `tests/test_tier_guards.py`

**Interfaces:**
- Consumes: `TIER_MARKERS` (`tests/conftest.py:83`), `Iterable` (already imported at `tests/conftest.py:17`).
- Produces: `UNIT_BUDGET_S: float = 0.5`, `GIT_BUDGET_S: float = 2.0`, `tier_budget_violation(markers: Iterable[str], duration: float) -> str | None`. Message formats are exactly `"unit-tier budget exceeded: {duration:.3f}s > 0.5s"` and `"git-tier budget exceeded: {duration:.3f}s > 2s"`.

- [ ] **Step 1: Write the failing tests (unit tier, unmarked)**

Create `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/tests/test_tier_guards.py`:

```python
"""Tier guards in `tests/conftest.py`: the unit-tier PATH shim and the per-test budget.

The budget helper and the stub text are pure, so their tests carry no tier
marker and run in the unit tier. The hook-level tests start a nested pytest in
a subprocess (pytester) that execs the stub scripts, so they are marked `git`:
a subprocess cannot be unit tier, and the marker keeps them out of the very
shim they police. They touch no real git, brd or claude. This file sits at the
top of `tests/`, so the directory auto-mark leaves it alone.
"""

from __future__ import annotations

import pytest

from conftest import GIT_BUDGET_S, UNIT_BUDGET_S, tier_budget_violation


def test_budget_constants_match_the_v1_table():
    assert UNIT_BUDGET_S == 0.5
    assert GIT_BUDGET_S == 2.0


def test_budget_helper_unit_over_half_second_violates():
    assert tier_budget_violation(set(), 0.51) == "unit-tier budget exceeded: 0.510s > 0.5s"
    assert tier_budget_violation(set(), 0.5) is None
    assert tier_budget_violation(set(), 0.01) is None


def test_budget_helper_git_over_two_seconds_violates():
    assert tier_budget_violation({"git"}, 2.01) == "git-tier budget exceeded: 2.010s > 2s"
    assert tier_budget_violation({"git"}, 2.0) is None
    assert tier_budget_violation({"git"}, 1.9) is None


@pytest.mark.parametrize(
    "markers",
    [{"brd"}, {"e2e_fake"}, {"soak"}, {"e2e"}, {"git", "soak"}, {"git", "brd"}],
    ids=["brd", "e2e_fake", "soak", "e2e", "git+soak", "git+brd"],
)
def test_budget_helper_opt_in_tiers_have_no_budget(markers):
    assert tier_budget_violation(markers, 100.0) is None


def test_budget_helper_ignores_non_tier_markers():
    assert tier_budget_violation({"parametrize", "asyncio"}, 0.6) is not None
    assert tier_budget_violation(iter(["skipif", "git"]), 1.0) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tier_guards.py -v`
Expected: collection error `ImportError: cannot import name 'GIT_BUDGET_S' from 'conftest'`.

- [ ] **Step 3: Write the minimal implementation**

Append to the end of `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/tests/conftest.py` (after the `pytest_collection_modifyitems` body):

```python


UNIT_BUDGET_S = 0.5
GIT_BUDGET_S = 2.0

# Opt-in tiers have no per-test budget, and win over `git` when an item has both.
_UNBUDGETED_TIERS = TIER_MARKERS - {"git"}


def tier_budget_violation(markers: Iterable[str], duration: float) -> str | None:
    """The budget failure message for a call phase of `duration` seconds, or None.

    `markers` is every marker name on the item's chain. No tier marker means the
    unit budget; `git` alone means the git budget; any opt-in tier means none.
    A duration exactly on the budget passes.
    """
    names = set(markers)
    if names & _UNBUDGETED_TIERS:
        return None
    tier, budget = ("git", GIT_BUDGET_S) if "git" in names else ("unit", UNIT_BUDGET_S)
    if duration <= budget:
        return None
    return f"{tier}-tier budget exceeded: {duration:.3f}s > {budget:g}s"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tier_guards.py tests/test_conftest_tiers.py -v`
Expected: all PASS (10 tests in `test_tier_guards.py`, counting the 6 parametrized cases, plus the existing `test_conftest_tiers.py` tests).

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_tier_guards.py
git commit -m "Add the pure per-tier test budget helper to tests/conftest.py"
```

---

### Task 2: Unit-tier PATH shim

**Files:**
- Modify: `tests/conftest.py` (module docstring lines 1-12; append after `tier_budget_violation`)
- Modify: `tests/test_tier_guards.py` (header imports; append tests)

**Interfaces:**
- Consumes: `TIER_MARKERS`, `Path`, `os` (already imported in `tests/conftest.py`).
- Produces: `STUB_NAMES: tuple[str, ...] = ("brd", "git", "claude")`, `STUB_EXIT_CODE: int = 99`, `STUB_DIR_PREFIX: str = "unit-tier-stubs"`, `stub_script(name: str) -> str`, session fixture `unit_tier_stub_dir -> Path`, autouse fixture `unit_tier_path_shim -> None`. Task 3's tests reuse the helper `run_nested(pytester, source) -> pytest.RunResult` defined here in `tests/test_tier_guards.py`.

- [ ] **Step 1: Write the failing tests**

In `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/tests/test_tier_guards.py`, replace the import block:

```python
from __future__ import annotations

import pytest

from conftest import GIT_BUDGET_S, UNIT_BUDGET_S, tier_budget_violation
```

with:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import (
    GIT_BUDGET_S,
    STUB_EXIT_CODE,
    STUB_NAMES,
    UNIT_BUDGET_S,
    stub_script,
    tier_budget_violation,
)

# Enabled here, not in tests/conftest.py: pytest rejects `pytest_plugins` in a
# conftest that is not loaded at startup, and a test module is always allowed.
pytest_plugins = ["pytester"]

REAL_CONFTEST = Path(__file__).resolve().parent / "conftest.py"

# The nested session has its own rootdir (pytester's tmp dir), so it does not
# read pyproject.toml; register the tier markers to keep its output clean.
NESTED_INI = """\
[pytest]
markers =
    git: real git in tmp_path
    brd: real brd
    e2e_fake: fake claude
    soak: concurrency stress
    e2e: real claude
"""


def run_nested(pytester: pytest.Pytester, source: str) -> pytest.RunResult:
    """Run `source` as `test_nested.py` under a copy of the real tests/conftest.py.

    A subprocess, not in-process: tests/test_conftest_tiers.py has already put
    tests/conftest.py into `sys.modules["conftest"]`, so an in-process nested
    conftest import would hit ImportPathMismatchError. The child inherits this
    process's environment, including `PATH`.
    """
    pytester.makeconftest(REAL_CONFTEST.read_text())
    pytester.makeini(NESTED_INI)
    pytester.makepyfile(test_nested=source)
    return pytester.runpytest_subprocess()
```

Then append to the end of the same file:

```python


def test_stub_script_forbids_the_named_binary():
    assert STUB_NAMES == ("brd", "git", "claude")
    assert STUB_EXIT_CODE == 99
    for name in STUB_NAMES:
        text = stub_script(name)
        assert text.startswith("#!/bin/sh\n")
        assert f"{name}: forbidden in the unit tier" in text
        assert f"exit {STUB_EXIT_CODE}" in text


@pytest.mark.git
def test_unmarked_test_calling_brd_fails_with_forbidden_message(pytester):
    # The card's deliberately broken test: an unmarked test that calls brd.
    # No capture_output, so the stub's stderr reaches pytest's fd capture and
    # shows up in the report instead of hiding inside CalledProcessError.
    result = run_nested(
        pytester,
        """
        import subprocess

        def test_calls_brd():
            subprocess.run(["brd", "--version"], check=True)
        """,
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*brd: forbidden in the unit tier*"])
    result.stdout.fnmatch_lines(["*non-zero exit status 99*"])


@pytest.mark.git
def test_unmarked_test_sees_stubs_for_git_and_claude(pytester):
    result = run_nested(
        pytester,
        """
        import shutil
        import subprocess
        from pathlib import Path

        import pytest

        @pytest.mark.parametrize("name", ["brd", "git", "claude"])
        def test_stub_shadows(name):
            found = shutil.which(name)
            assert found is not None
            assert Path(found).parent.name.startswith("unit-tier-stubs")
            done = subprocess.run([name, "--version"], capture_output=True, text=True)
            assert done.returncode == 99
            assert done.stderr == f"{name}: forbidden in the unit tier\\n"
            assert done.stdout == ""
        """,
    )
    result.assert_outcomes(passed=3)


@pytest.mark.git
def test_marked_test_gets_real_path(pytester, monkeypatch):
    # This outer test is `git`-marked, so its own PATH is the unshimmed one.
    monkeypatch.setenv("OUTER_PATH", os.environ["PATH"])
    result = run_nested(
        pytester,
        """
        import os

        import pytest

        def _path_is_inherited():
            return os.environ["PATH"] == os.environ["OUTER_PATH"]

        @pytest.mark.git
        def test_function_marked_git():
            assert _path_is_inherited()

        @pytest.mark.brd
        class TestClassMarkedBrd:
            def test_inside(self):
                assert _path_is_inherited()

        def test_unmarked_control():
            assert not _path_is_inherited()
            first = os.environ["PATH"].split(os.pathsep)[0]
            assert os.path.basename(first).startswith("unit-tier-stubs")
        """,
    )
    result.assert_outcomes(passed=3)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tier_guards.py -v`
Expected: collection error `ImportError: cannot import name 'STUB_EXIT_CODE' from 'conftest'`.

- [ ] **Step 3: Add the stub constants and `stub_script` only, then re-run to see the hook-level RED**

Append to the end of `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/tests/conftest.py`:

```python


STUB_NAMES = ("brd", "git", "claude")
STUB_EXIT_CODE = 99
STUB_DIR_PREFIX = "unit-tier-stubs"


def stub_script(name: str) -> str:
    """A shell script that refuses to be `name`: one stderr line, exit 99, no delegation."""
    return f"#!/bin/sh\necho '{name}: forbidden in the unit tier' >&2\nexit {STUB_EXIT_CODE}\n"
```

Run: `uv run pytest tests/test_tier_guards.py -v`
Expected: `test_stub_script_forbids_the_named_binary` PASSES. The three `git`-marked tests FAIL. `test_unmarked_test_calling_brd_fails_with_forbidden_message` fails on the `assert_outcomes`/`fnmatch_lines` for "forbidden", whether or not real brd is installed. `test_unmarked_test_sees_stubs_for_git_and_claude` fails on the stub-dir assertion. `test_marked_test_gets_real_path` fails on `test_unmarked_control`.

- [ ] **Step 4: Write the fixtures**

Append to the end of `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/tests/conftest.py`:

```python


@pytest.fixture(scope="session")
def unit_tier_stub_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One directory of `brd`/`git`/`claude` stubs, built once per session."""
    bin_dir = tmp_path_factory.mktemp(STUB_DIR_PREFIX)
    for name in STUB_NAMES:
        script = bin_dir / name
        script.write_text(stub_script(name))
        script.chmod(0o755)
    return bin_dir


@pytest.fixture(autouse=True)
def unit_tier_path_shim(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the stubs first on `PATH` for items with no tier marker.

    The same prepend-a-bin-dir technique as `fake_brd` (tests/steps/test_rollup.py)
    and `brd_shim` (tests/e2e/test_board_comments.py), except the stubs never
    hand off to a real binary. Items with any tier marker keep `PATH` exactly as
    inherited, and the stub directory is only built once a unit item needs it. A
    test that sets `PATH` itself runs after this and wins, as monkeypatch calls
    stack. A binary invoked by absolute path bypasses the shim.
    """
    if TIER_MARKERS.intersection(mark.name for mark in request.node.iter_markers()):
        return
    bin_dir = request.getfixturevalue("unit_tier_stub_dir")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', os.defpath)}")
```

Then update the module docstring. Replace:

```python
item already carries a tier marker anywhere on its marker chain. The hook only
adds markers; the addopts `-m` expression in pyproject.toml does the deselecting.
"""
```

with:

```python
item already carries a tier marker anywhere on its marker chain. The hook only
adds markers; the addopts `-m` expression in pyproject.toml does the deselecting.

Unit-tier items (no tier marker after collection) run with stub `brd`, `git` and
`claude` scripts first on `PATH`; each stub prints `<name>: forbidden in the unit
tier` to stderr and exits 99, so an accidental real spawn fails loudly.
"""
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tier_guards.py tests/test_conftest_tiers.py tests/test_paths.py -v`
Expected: all PASS. `tests/test_paths.py` is included because it monkeypatches environment variables itself (stacking on top of autouse fixtures) and must still pass with the shim in place.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_tier_guards.py
git commit -m "Shadow brd/git/claude with exit-99 stubs for unit-tier tests"
```

---

### Task 3: Per-test duration check hook

**Files:**
- Modify: `tests/conftest.py` (line 17 import; module docstring; append the hook)
- Modify: `tests/test_tier_guards.py` (append tests)

**Interfaces:**
- Consumes: `tier_budget_violation` (Task 1), `run_nested` (Task 2, in `tests/test_tier_guards.py`).
- Produces: hook `pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Generator[None, pytest.TestReport, pytest.TestReport]`.

- [ ] **Step 1: Write the tests**

Append to `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/tests/test_tier_guards.py`:

```python


@pytest.mark.git
def test_slow_unmarked_test_fails_budget(pytester):
    result = run_nested(
        pytester,
        """
        import time

        def test_slow():
            time.sleep(0.55)
        """,
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*unit-tier budget exceeded: *s > 0.5s*"])


@pytest.mark.git
def test_failing_slow_test_keeps_original_failure(pytester):
    result = run_nested(
        pytester,
        """
        import time

        def test_slow_and_wrong():
            time.sleep(0.55)
            assert 1 == 2, "original failure"
        """,
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*original failure*"])
    result.stdout.no_fnmatch_line("*budget exceeded*")


@pytest.mark.git
def test_slow_fixture_setup_is_not_counted(pytester):
    result = run_nested(
        pytester,
        """
        import time

        import pytest

        @pytest.fixture
        def slow_setup():
            time.sleep(0.55)

        def test_fast_body(slow_setup):
            pass
        """,
    )
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*budget exceeded*")


@pytest.mark.git
def test_slow_test_that_skips_stays_skipped(pytester):
    result = run_nested(
        pytester,
        """
        import time

        import pytest

        def test_slow_then_skip():
            time.sleep(0.55)
            pytest.skip("skipped after the budget")
        """,
    )
    result.assert_outcomes(skipped=1)
    result.stdout.no_fnmatch_line("*budget exceeded*")
```

- [ ] **Step 2: Run the tests to verify the RED**

Run: `uv run pytest tests/test_tier_guards.py -v -k "slow"`
Expected: `test_slow_unmarked_test_fails_budget` FAILS (the nested run reports `passed=1`, not `failed=1`). The other three PASS already. They are guards that the hook must not break (no replaced failure, setup not counted, skip preserved), so passing before the hook exists is correct.

- [ ] **Step 3: Write the hook**

In `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/tests/conftest.py`, replace line 17:

```python
from collections.abc import Iterable
```

with:

```python
from collections.abc import Generator, Iterable
```

Append to the end of the file:

```python


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Fail a passing call phase that ran over its tier's per-test budget.

    `tryfirst` makes this the outermost wrapper, so it sees the report after the
    other plugins (xfail handling) have settled it. Setup and teardown are not
    counted, and a failed or skipped report keeps its own outcome and text.
    """
    report = yield
    if report.when == "call" and report.passed:
        violation = tier_budget_violation((mark.name for mark in item.iter_markers()), call.duration)
        if violation is not None:
            report.outcome = "failed"
            report.longrepr = violation
    return report
```

Then extend the module docstring. Replace:

```python
`claude` scripts first on `PATH`; each stub prints `<name>: forbidden in the unit
tier` to stderr and exits 99, so an accidental real spawn fails loudly.
"""
```

with:

```python
`claude` scripts first on `PATH`; each stub prints `<name>: forbidden in the unit
tier` to stderr and exits 99, so an accidental real spawn fails loudly.

A passing call phase over its tier's budget (unit 0.5s, `git` 2s; the opt-in tiers
have none) is turned into a failure naming the tier, budget and measured time.
"""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tier_guards.py tests/test_conftest_tiers.py -v --durations=0`
Expected: all PASS. In the durations listing, every `git`-marked test in `test_tier_guards.py` is under 2.0s (roughly 0.5-1.5s: subprocess pytest startup plus at most one 0.55s sleep). If any is over 2.0s on this machine, report it rather than raising the budget.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_tier_guards.py
git commit -m "Fail unit tests over 0.5s and git tests over 2s in tests/conftest.py"
```

---

### Task 4: By-hand proof that the guard intercepts a real brd call

**Files:**
- Create then delete: `tests/test_zz_throwaway_real_brd.py` (never committed)

- [ ] **Step 1: Write the throwaway test**

Create `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/tests/test_zz_throwaway_real_brd.py`:

```python
import subprocess


def test_calls_real_brd_without_a_marker():
    subprocess.run(["brd", "--version"], check=True)
```

- [ ] **Step 2: Run it and confirm it fails loudly**

Run: `uv run pytest tests/test_zz_throwaway_real_brd.py -q`
Expected: `1 failed`. The output contains `brd: forbidden in the unit tier` under "Captured stderr call" and `returned non-zero exit status 99`.

- [ ] **Step 3: Delete it**

Run: `rm /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-the-unit-tier-path-3202b0b0/tests/test_zz_throwaway_real_brd.py && git status --short`
Expected: no `tests/test_zz_throwaway_real_brd.py` in the status output.

---

### Task 5: Full suite and triage of newly caught tests

The guard is designed to catch existing tests. Several unmarked modules call real `brd`/`git` today: `tests/test_board.py` (the `@requires_brd` tests, fixture `temp_board` at `:241-261`), `tests/test_bases.py` (`_git`/`_init_repo` at `:48-95`), `tests/test_orchestrate.py` (`project`/`_milestone`, real git plus a real brd board), `tests/test_cli.py`, and single uses in `tests/test_integration.py`, `tests/test_integrate_workflow.py` and `tests/test_comments.py`. Some others are likely over 0.5s (e.g. the `tests/test_store.py` lease-race spawns). Expect red here. This task decides what may be fixed with a marker and what must be reported.

**Files:**
- Possibly modify: test files named by the failure list, one marker line per test only, per the rules below.

- [ ] **Step 1: Run the full suite and capture the failure list**

Run: `uv run pytest -q -rfE 2>&1 | tee /tmp/claude-tier-guard-run.txt; grep -E "^(FAILED|ERROR) " /tmp/claude-tier-guard-run.txt`
Expected: a long combined list of `FAILED` and `ERROR` lines — most of the damage shows up as `ERROR at setup of ...` from shared fixtures (`project`, `temp_board`) that spawn real `git`/`brd` once per test, not as `FAILED`. `-rf` alone omits errors from the short summary and `tail -n 120` truncates a summary this size (hundreds of lines on this codebase as of 2026-10-02); use the grep above, not a tail, to see every entry. Each line carries either `forbidden in the unit tier` (a real spawn caught, surfaced as an ERROR when it happens in fixture setup) or `budget exceeded` (too slow).

- [ ] **Step 2: Classify each failure with these rules (no other fixes are allowed)**

Each rule permits exactly one added marker line per test, by the V1 rule "tier = what the test actually touches":

- (a) The test spawns only `git` (no `brd`, no `claude`), either directly or through `src/` code such as `bases.build`/`steps.worktree`. Add `@pytest.mark.git` on the line directly above its `def` (above any existing `@requires_git`). If every test in a module needs it, a single `pytestmark = pytest.mark.git` line below the imports is the one-line form. The test stays in the default run.
- (b) The test spawns real `brd`, directly or through a fixture (`temp_board`, `project`, `_milestone`). Add `@pytest.mark.brd` on the line directly above its `def`. The exception is `tests/test_board.py:575`, the 8-thread `brd update` stress probe, which gets `@pytest.mark.soak` (design V8). This moves the test to the opt-in `-m brd` run.
- (c) A unit-budget failure with no real binary involved, or a `git`-tier test over 2s (e.g. a slow `tests/steps/test_rollup.py`/`test_store.py` test, a `claude`-spawning test). Add `@pytest.mark.soak` — this is a temporary, coarse landing spot; V8/V9 give it a more precise tier later. Record `file:line — test name — reason` in the commit message regardless.

**There is no escalation gate and no size cap.** Applying (a)/(b)/(c) markers to every currently-failing test — however many that turns out to be, including all of `tests/test_orchestrate.py`/`tests/test_cli.py`'s real-board tests — is this task's explicit, pre-approved scope (per the card's own instructions; the design's V4/V5/V8/V9 progressively un-mark and convert pieces of this back to the default tier later, this task does not wait for them). Do not stop to ask; do not report a list instead of fixing it. The only thing this step must not do is change any test's behavior or assertions — marker lines only.

- [ ] **Step 3: Apply the (a)/(b)/(c) markers to every failing test**

For each entry from Step 1/2, add the single marker line described above (file-level `pytestmark = pytest.mark.X` is fine when a whole file is uniform). Make sure `import pytest` already exists in that module (every listed module already imports it).

- [ ] **Step 4: Run the full suite to verify it is green**

Run: `uv run pytest -q 2>&1 | tail -n 20`
Expected: `0 failed`. The pass count is lower than the Task 0 baseline only by the number of tests marked `brd`/`soak` in Step 3, plus the new `tests/test_tier_guards.py` tests added on top. Then run `uv run pytest -m brd -q 2>&1 | tail -n 5` and `uv run pytest -m soak -q 2>&1 | tail -n 5` and confirm the re-marked tests still pass when selected.

- [ ] **Step 5: Commit (only the files Step 3 touched)**

```bash
git add tests/test_board.py tests/test_bases.py
git commit -m "Tier tests the unit-tier guard caught: git-only -> git, real brd -> brd, slow/unclear -> soak

Marked by the V1 rule (tier = what the test touches), one marker line each:
<paste the (a)/(b)/(c) file:line list from Step 1/2 here, verbatim>
tests/test_board.py:575 -> soak (V8 stress probe)."
```

Adjust the `git add` paths to exactly the files Step 3 changed, and replace the angle-bracket line with the actual list from Step 2 before committing. The commit body must name every re-marked test, since the spec requires each marker to be called out.
