<!-- task-pipeline: validated -->
# Move the grandchild-kill test to soak and shrink the remaining timeouts — subtask design

Card: 1ad895f4-68fa-4f06-a1b1-c68b756b5e5b. Parent story: 838df0c9 ("Fix the e2e tier's accounting and shrink soak-adjacent timeouts"). Source of truth: `docs/superpowers/specs/2026-10-02-test-tier-design.md`, Decision V9 (the `test_launcher.py` clause) and Decision V1 (the tier table). This narrows V9 to one file; it adds no new design.

## Scope

One file only: `tests/harness/test_launcher.py`. No production code changes.

1. `test_a_timeout_kills_the_processes_the_child_started` (the grandchild-kill test, currently `:99-128`, `timeout=2.0`) carries `@pytest.mark.soak` and is excluded from the default run. Its body, its `timeout=2.0`, and its 5s grandchild-death polling stay as they are — it moves, it does not shrink.
2. `test_a_timeout_kills_the_child_and_returns_a_value` (currently `:77-96`, `timeout=0.5`) shrinks to `timeout=0.2` and stays in the default run. In the current worktree this test also carries `@pytest.mark.soak`; that marker must be removed — V9 moves only the grandchild-kill test, and shrinking this one is precisely what lets it stay in the default run under the unit budget.
3. `test_on_spawn_is_called_once_with_the_live_process` (currently `:339-358`) shrinks the child's `time.sleep(0.3)` to `time.sleep(0.1)`. Its `timeout=30.0` is a safety ceiling, not a cost, and is unchanged. It stays in the default run.

Before changing a value, read the test and confirm the smaller one keeps the assertion meaningful. If a test needs more margin to be reliable, use a larger value than suggested rather than forcing it (the card says so explicitly) — but the result must still fit the unit-tier 0.5s per-test call budget that `tests/conftest.py` enforces (`tier_budget_violation`), or the test fails in the default run.

## Observable behavior that must be preserved

- Timeout test: still asserts `timed_out is True`, `exit_code is None`, that `"before the sleep"` is in the partial log (so the child must print and flush before the 0.2s deadline — Python startup plus a print fits comfortably), and `duration < 20.0` (the kill happened rather than waiting out the 30s sleep). No assertion is removed or loosened.
- on_spawn test: still asserts the hook ran exactly once, `poll()` was `None` when the hook saw the process (the child must still be alive at hook time — 0.1s of sleep after interpreter startup is ample since the hook runs right after `Popen` returns), `args[0] == sys.executable`, and `returncode == exit_code == 0`.
- Grandchild-kill test: unchanged assertions; only its tier changes.

## Placement (per V1's rule: placement is decided by what the test actually drives)

| Test | Tier | Why |
|---|---|---|
| `test_a_timeout_kills_the_processes_the_child_started` | `soak` (`@pytest.mark.soak`, opt-in `-m soak`) | Deliberate robustness probe of process-group kill with a 2s wait and up to 5s polling; V9 names it explicitly. No budget applies. |
| `test_a_timeout_kills_the_child_and_returns_a_value` | default run, no marker (unit budget 0.5s) | V9 keeps it in the default run by shrinking it; it spawns only `sys.executable` by absolute path, so the unit-tier PATH shim does not interfere. |
| `test_on_spawn_is_called_once_with_the_live_process` | default run, no marker (unit budget 0.5s) | Same as above. |

All other tests in the file are untouched and keep their current placement.

## Error paths / risks

- If the 0.2s timeout races the child's flush on a slow machine, the `"before the sleep"` assertion fails; the remedy is a modestly wider timeout (still under the 0.5s unit budget), not dropping the assertion.
- If the on_spawn child exits before the hook polls, `polled is None` fails; the remedy is a wider sleep, not dropping the assertion.

## Out of scope

- `tests/test_store.py`'s lease-race parametrization (sibling fd4109a3).
- `justification:` lines in the e2e modules (sibling d4542989, done).
- Marker registration, `addopts`, conftest budget/shim logic (already present in this worktree's `pyproject.toml` and `tests/conftest.py`).
- pytest-xdist, the pygents engine, checkpoint format, harness adapter contract, `dispatch.py`'s `LauncherFn` seam, the e2e tier's size or content, milestone 14's `am run --board`.

## Verification

- `uv run pytest tests/harness/test_launcher.py -v` green, repeated several times with no new flakiness and no unit-budget failures; the grandchild-kill test shows as deselected.
- `uv run pytest tests/harness/test_launcher.py -m soak -v` collects and passes exactly the grandchild-kill test (also visible in a full `uv run pytest -m soak`).
- `uv run pytest` (full default suite) green.

---

# Move the grandchild-kill test to soak and shrink the remaining timeouts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In `tests/harness/test_launcher.py`, keep only the grandchild-kill test in the `soak` tier, and shrink the two remaining slow launcher tests (0.5s timeout to 0.2s, 0.3s child sleep to 0.1s) so they run in the default suite within the 0.5s unit budget, without weakening any assertion.

**Architecture:** Test-only change in one file. A new unit-tier placement guard in the same module (the pattern `tests/e2e/test_exactly_once.py::test_this_module_runs_in_the_default_suite_unmarked` already uses: read `pytestmark` off the test function) pins which of the three tests carry which marker. The existing conftest budget hook (`tier_budget_violation`, `UNIT_BUDGET_S = 0.5`) is the RED signal for the timeout shrink: once the `soak` marker comes off, the 0.5s-timeout test necessarily runs past the 0.5s unit budget and fails until it is shrunk.

**Tech Stack:** Python, pytest (markers selected by the `addopts` `-m` expression in `pyproject.toml`), `uv`.

**Spec:** `docs/superpowers/specs/task-move-the-grandchild-1ad895f4-design.md` (prepended above), implementing Decision V9's `test_launcher.py` clause of `docs/superpowers/specs/2026-10-02-test-tier-design.md`.

## Global Constraints

- One file only: `tests/harness/test_launcher.py`. No production code changes (nothing under `src/`).
- Do not touch `tests/test_store.py` (sibling fd4109a3), the `justification:` lines in `tests/e2e/test_real_harness*.py` (sibling d4542989), `pyproject.toml`, or `tests/conftest.py`.
- Unit-tier per-test call budget: 0.5s (`UNIT_BUDGET_S` in `tests/conftest.py`); a duration exactly on the budget passes.
- Grandchild-kill test keeps `timeout=2.0` and its 5.0s polling deadline unchanged.
- Timeout test target `timeout=0.2`; on_spawn child target `time.sleep(0.1)`; on_spawn `timeout=30.0` unchanged. A wider value is allowed only if margin is needed, and must still fit the 0.5s unit budget.
- No assertion in any of the three tests is removed or loosened.
- Verification: `uv run pytest` (full suite); no lint or typecheck command exists.

## Review Focus

1. A later edit re-adds `@pytest.mark.soak` to the timeout test (or to the on_spawn test) and silently drops it from the default run — expected: the default run fails. Pinned by `test_only_the_grandchild_kill_launcher_test_is_soak` (Task 1).
2. A later edit drops `@pytest.mark.soak` from the grandchild-kill test, putting a 2s-plus test back in the default run — expected: the default run fails. Pinned by the same guard (Task 1), and independently by the unit budget.
3. The 0.2s timeout fires before the child has printed and flushed `before the sleep` on a slow or loaded machine — expected: still green. Exercised by the repeated-run step in Task 3; remedy is widening the timeout (for example to 0.3), never dropping the assertion.
4. The on_spawn child exits before the hook calls `poll()` — expected: `polled is None` still holds. Exercised by the repeated-run step in Task 3; the hook runs right after `Popen` returns (`src/agent_manager/harness/launcher.py:147-159`), and interpreter startup alone exceeds the gap, so 0.1s is ample.
5. `-m soak` run over the file selects anything besides the grandchild-kill test, or nothing — expected: exactly one passed. Checked in Task 3 Step 3.

---

### Task 1: Placement guard, un-soak the timeout test, shrink its timeout to 0.2s

**Files:**
- Modify: `tests/harness/test_launcher.py:77-96` (`test_a_timeout_kills_the_child_and_returns_a_value`)
- Test: `tests/harness/test_launcher.py` (new guard appended at the end of the module, unit tier, no marker)

**Interfaces:**
- Consumes: the module-level test functions `test_a_timeout_kills_the_processes_the_child_started`, `test_a_timeout_kills_the_child_and_returns_a_value`, `test_on_spawn_is_called_once_with_the_live_process` (referenced by name; the guard reads their `pytestmark` attribute).
- Produces: `test_only_the_grandchild_kill_launcher_test_is_soak()` — a unit-tier test with no fixtures and no subprocess.

Tier note: the guard is unit tier (no marker) because it only inspects function attributes; it lives in `tests/harness/test_launcher.py` next to the tests it pins, as `tests/e2e/test_exactly_once.py` does for its own proof. `tests/harness/` has no directory default tier in `tests/conftest.py` (`_DIRECTORY_TIERS` only covers `e2e` and `steps`), so unmarked tests there are unit tier.

- [ ] **Step 1: Write the failing placement guard**

Append to the end of `tests/harness/test_launcher.py` (after `test_kill_tree_is_public_and_the_old_name_is_an_alias`):

```python


def _marks(test_function) -> set[str]:
    return {mark.name for mark in getattr(test_function, "pytestmark", [])}


def test_only_the_grandchild_kill_launcher_test_is_soak():
    # Test-tier spec V9: the grandchild-kill probe is the one launcher test that
    # belongs in soak. The other two slow-looking tests were shrunk to fit the
    # unit budget so they keep running on every `uv run pytest`; a soak marker
    # creeping back onto them would deselect them silently. Marks are read off
    # the functions because a mark there deselects them but not this guard.
    assert _marks(test_a_timeout_kills_the_processes_the_child_started) == {"soak"}
    assert _marks(test_a_timeout_kills_the_child_and_returns_a_value) == set()
    assert _marks(test_on_spawn_is_called_once_with_the_live_process) == set()
```

- [ ] **Step 2: Run the guard to verify it fails**

Run: `uv run pytest tests/harness/test_launcher.py::test_only_the_grandchild_kill_launcher_test_is_soak -v`
Expected: FAIL on the second assert, `AssertionError: assert {'soak'} == set()` (the timeout test still carries `@pytest.mark.soak` at line 77).

- [ ] **Step 3: Remove the soak marker from the timeout test**

In `tests/harness/test_launcher.py`, replace:

```python
@pytest.mark.soak
def test_a_timeout_kills_the_child_and_returns_a_value(tmp_path):
```

with:

```python
def test_a_timeout_kills_the_child_and_returns_a_value(tmp_path):
```

Leave the `@pytest.mark.soak` on `test_a_timeout_kills_the_processes_the_child_started` (line 99) exactly as it is.

- [ ] **Step 4: Run the guard and the timeout test; the guard passes, the timeout test now fails its unit budget**

Run: `uv run pytest tests/harness/test_launcher.py::test_only_the_grandchild_kill_launcher_test_is_soak tests/harness/test_launcher.py::test_a_timeout_kills_the_child_and_returns_a_value -v`
Expected: the guard PASSES; `test_a_timeout_kills_the_child_and_returns_a_value` FAILS with `unit-tier budget exceeded: 0.5xxs > 0.5s` (its `timeout=0.5` alone consumes the whole budget). This is the RED for the shrink.

- [ ] **Step 5: Shrink the timeout to 0.2s**

In `test_a_timeout_kills_the_child_and_returns_a_value`, replace:

```python
        cwd=tmp_path,
        timeout=0.5,
        stdout_path=log,
```

with:

```python
        cwd=tmp_path,
        timeout=0.2,
        stdout_path=log,
```

Do not change any assertion: `timed_out is True`, `exit_code is None`, `"before the sleep" in log.read_text()`, `duration < 20.0` all stay. The child's `print('before the sleep', flush=True)` happens right after interpreter startup (well under 0.2s), so the partial-log assertion keeps its meaning.

- [ ] **Step 6: Run both tests to verify they pass**

Run: `uv run pytest tests/harness/test_launcher.py::test_only_the_grandchild_kill_launcher_test_is_soak tests/harness/test_launcher.py::test_a_timeout_kills_the_child_and_returns_a_value -v`
Expected: 2 passed. If the timeout test fails on `"before the sleep"`, widen `timeout=0.2` to `timeout=0.3` (still under the 0.5s budget) and rerun; do not touch the assertion.

- [ ] **Step 7: Commit**

```bash
git add tests/harness/test_launcher.py
git commit -m "test: keep only the grandchild-kill launcher test in soak; shrink the timeout test to 0.2s"
```

---

### Task 2: Shrink the on_spawn child's sleep to 0.1s

**Files:**
- Modify: `tests/harness/test_launcher.py` (`test_on_spawn_is_called_once_with_the_live_process`, the `time.sleep(0.3)` line, currently `:348`)

**Interfaces:**
- Consumes: Task 1's guard `test_only_the_grandchild_kill_launcher_test_is_soak` (already asserts this test stays unmarked).
- Produces: nothing new.

This is a cost reduction with no behavior change, so no new assertion can go red for it; the RED signal is the unit budget it must keep fitting, and the guard from Task 1 already pins that it stays unmarked. Step 1 records the baseline so Step 4 shows the saving.

- [ ] **Step 1: Record the current duration**

Run: `uv run pytest tests/harness/test_launcher.py::test_on_spawn_is_called_once_with_the_live_process -v --durations=0 --durations-min=0`
Expected: 1 passed; the durations table shows its `call` around 0.3-0.4s.

- [ ] **Step 2: Shrink the sleep**

In `test_on_spawn_is_called_once_with_the_live_process`, replace:

```python
        [sys.executable, "-c", "import time; time.sleep(0.3); print('done')"],
```

with:

```python
        [sys.executable, "-c", "import time; time.sleep(0.1); print('done')"],
```

Leave `timeout=30.0` and every assertion (`len(seen) == 1`, `polled is None`, `process.args[0] == sys.executable`, `process.returncode == outcome.exit_code == 0`) unchanged.

- [ ] **Step 3: Run it to verify it passes**

Run: `uv run pytest tests/harness/test_launcher.py::test_on_spawn_is_called_once_with_the_live_process -v --durations=0 --durations-min=0`
Expected: 1 passed, `call` duration lower than the Step 1 baseline (roughly 0.1-0.2s). If `polled is None` fails, widen the sleep to `0.2` (still under budget) and rerun; do not touch the assertion.

- [ ] **Step 4: Commit**

```bash
git add tests/harness/test_launcher.py
git commit -m "test: shrink the on_spawn launcher test's child sleep to 0.1s"
```

---

### Task 3: Verify stability, soak selection, and the full suite

**Files:**
- None modified (verification only). If a step fails, the fix goes back into `tests/harness/test_launcher.py` per the widen-not-weaken rule in Tasks 1 and 2, then this task reruns.

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: nothing.

- [ ] **Step 1: Run the file verbosely**

Run: `uv run pytest tests/harness/test_launcher.py -v`
Expected: all collected tests pass, `1 deselected` (the grandchild-kill test), no `unit-tier budget exceeded` failures, and neither `test_a_timeout_kills_the_child_and_returns_a_value` nor `test_on_spawn_is_called_once_with_the_live_process` listed in the `slowest durations` section (it only lists 0.5s and up).

- [ ] **Step 2: Repeat the file run to check for new flakiness**

Run: `for i in 1 2 3 4 5 6 7 8 9 10; do uv run pytest tests/harness/test_launcher.py -v || break; done`
Expected: ten consecutive green runs, each reporting `1 deselected`. Any failure in the two shrunk tests means widening the value as described in Tasks 1 and 2, committing, and repeating this step.

- [ ] **Step 3: Confirm `-m soak` picks up exactly the moved test**

Run: `uv run pytest tests/harness/test_launcher.py -m soak -v`
Expected: exactly `test_a_timeout_kills_the_processes_the_child_started PASSED`, `1 passed`, every other test in the file deselected.

- [ ] **Step 4: Confirm it is in the full soak run too**

Run: `uv run pytest -m soak -v`
Expected: green, and the output includes `tests/harness/test_launcher.py::test_a_timeout_kills_the_processes_the_child_started PASSED` and does not include `test_a_timeout_kills_the_child_and_returns_a_value`.

- [ ] **Step 5: Run the full default suite**

Run: `uv run pytest`
Expected: green; no `unit-tier budget exceeded` failures.

- [ ] **Step 6: Confirm the diff stays in scope**

Run: `git diff --stat m15/task-add-justification-d4542989 HEAD -- . ':!docs'`
Expected: exactly one file listed, `tests/harness/test_launcher.py` (the commits from Tasks 1 and 2, plus any widening commit from Step 2). Nothing under `src/`, no `tests/test_store.py`, no `tests/conftest.py`, no `pyproject.toml`.
