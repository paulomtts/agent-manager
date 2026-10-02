<!-- task-pipeline: validated -->
# Move the 20-iteration lease-race parametrization to soak (fd4109a3)

Parent: 838df0c9 "Fix the e2e tier's accounting and shrink soak-adjacent timeouts" (milestone 66ed75cd). Source of truth: `docs/superpowers/specs/2026-10-02-test-tier-design.md`, decisions V1 (tier table) and V9 (this card). Blocked by 1ad895f4; this worktree already carries that lineage (`pyproject.toml:38` registers `soak: concurrency stress; opt-in, no budget`, and `pyproject.toml:48` addopts excludes `soak` from the default run).

## Scope

Only `tests/test_store.py`, and only the parametrization of `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner` (`tests/test_store.py:3191-3219`). Today `@pytest.mark.parametrize("attempt", range(20))` spawns two real child processes per iteration through `_TAKER`/`_taker()` (`tests/test_store.py:3147-3188`), with `_plant_lease` (`tests/test_store.py:2845`) as the setup. That is 40 process spawns to check one property.

V9 lets the implementer pick ONE of these:

- **A. Move (the structurally preferred option).** Add `@pytest.mark.soak` to the test and keep `range(20)`. V1 defines `soak` as "concurrency/race stress, no budget". This is a multi-process race probe, and the section comment at `tests/test_store.py:~2834` describes it the same way (multi-process design X4/X5/X9, real child processes ordered by pipes).
- **B. Shrink.** Change it to `range(3)`, leave it unmarked so it stays in the default run, and only do this if three iterations still give useful confidence. The test spawns subprocesses, so the default-tier budget enforced by `tests/conftest.py` applies to each parametrized case. Before choosing B, time a single iteration against that budget. If it does not fit comfortably, choose A.

The commit message must name the option chosen and say why. The spec requires this.

What the test asserts does not change under either option. The two children still race on one dead lease, the outcomes are exactly `["LeaseHeldError", "took"]`, both children exit 0, and the stored lease belongs to the winner. Do not change `_TAKER`, `_taker`, `_plant_lease`, the 60s `communicate` timeout, or the assertions.

## Out of scope

- `tests/harness/test_launcher.py`, which belongs to sibling 1ad895f4.
- The `justification:` docstrings on the five e2e tests, which belong to sibling d4542989.
- `pyproject.toml` marker registration and addopts, and `tests/conftest.py`, which are already in place.
- pytest-xdist or parallel execution as the verify command.
- Changes to the pygents engine, the checkpoint format, the harness adapter contract, or `dispatch.py`'s `LauncherFn` seam.
- Reducing the e2e tier below 5 tests or changing what those tests verify.
- Milestone-14 `am run --board` work.

## Observable behavior

- **Option A:** `uv run pytest` no longer collects any `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner[*]` case. `uv run pytest -m soak` collects all 20 cases and they pass.
- **Option B:** `uv run pytest` collects exactly 3 cases (`[0]`, `[1]`, `[2]`), and they pass within the default-tier budget without any conftest budget failure.

## Error paths

None are new. The existing failure modes stay as they are: a child that never prints `ready`, a `communicate` timeout, two winners or zero winners, or a nonzero exit. The `finally` block still kills any child that is left running.

## Tests

| Test | Tier (per V1 and the `tests/conftest.py` placement rule) |
|---|---|
| `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner[0..19]` (option A) | `soak`: a multi-process race stress probe that spawns real child processes and has no budget |
| `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner[0..2]` (option B) | default tier, unmarked: a cheap smoke of the same race that must meet the default-tier per-test budget enforced by conftest |

No other tests are added or removed.

## Verification

- `uv run pytest` must be green.
- If option A is chosen, `uv run pytest -m soak` must also pass, including all 20 moved cases.

---

# Lease-race soak move (fd4109a3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner`'s `range(20)` parametrization out of the default run and into the opt-in `soak` tier, without changing what it asserts.

**Architecture:** This plan takes spec option A. Option A is the one the spec calls structurally preferred, and V1 defines `soak` as "concurrency/race stress, no budget", which describes this test: two real child processes race on one dead lease. Option B would leave three 2-process spawns per run in the unit tier. That tier has a 0.5s per-test budget (`tests/conftest.py:225`, `UNIT_BUDGET_S = 0.5`), and two cold `python -c` interpreters each importing `agent_manager.store` take a large share of it, so B would not fit comfortably. The change is one decorator line. A unit-tier guard test pins the marker and the full `range(20)`, following the precedent sibling 1ad895f4 set in `tests/harness/test_launcher.py:403-415` (`_marks` plus `test_only_the_grandchild_kill_launcher_test_is_soak`).

**Tech Stack:** Python 3.12, pytest 9 (markers, `parametrize`), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-move-the-20-iteration-fd4109a3/docs/superpowers/specs/task-move-the-20-iteration-fd4109a3-design.md` (prepended above), which derives from `docs/superpowers/specs/2026-10-02-test-tier-design.md` V1 and V9.

## Global Constraints

- Only `tests/test_store.py` changes.
- Do not change `_TAKER`, `_taker`, `_plant_lease`, the 60s `communicate` timeout, or the race test's assertions.
- Keep `range(20)`. Option A moves the iterations; it does not shrink them.
- Do not touch `tests/harness/test_launcher.py`, the e2e `justification:` docstrings, `pyproject.toml`, or `tests/conftest.py`.
- Do not use pytest-xdist or parallel execution as the verify command.
- Do not change the pygents engine, the checkpoint format, the harness adapter contract, or `dispatch.py`'s `LauncherFn` seam.
- Keep the e2e tier at its current 5 tests.
- The commit message must name the option chosen (A, move to soak) and say why.
- Verification: `uv run pytest` is green, and `uv run pytest -m soak` passes, including all 20 moved cases.

## Review Focus

- Default-run collection after the move: `uv run pytest` must collect zero `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner[*]` cases. Checked in Task 1 Step 6 with a `--collect-only` run.
- Soak-run collection after the move: `uv run pytest -m soak` must select all 20 cases, `[0]` through `[19]`, not a shrunk subset. The guard pins the `range(20)` args, and Task 1 Step 7 counts the cases.
- A soak mark that silently drops off the race test later (for example in a refactor or merge) would put 40 spawns back into the unit tier, where the budget would fail them. The guard test in Task 1 pins `{"parametrize", "soak"}`.
- An accidental extra tier marker, such as `git`, would move the test into a tier whose budget applies. Under conftest's `_UNBUDGETED_TIERS` rule soak still wins, but the guard asserts the exact mark set, so any extra marker fails it.
- The unit-tier PATH shim no longer applies to the race test once it carries `soak`. The children run as `sys.executable -c ...` by absolute path, so their behavior does not change either way. No extra test is needed. The soak run in Step 7 confirms this.

---

### Task 1: Mark the lease-race parametrization `soak`, guarded by a unit-tier marker test

**Files:**
- Modify: `tests/test_store.py:3191` (add `@pytest.mark.soak` above the `parametrize` decorator)
- Test: `tests/test_store.py` (new guard function inserted directly after the race test, i.e. after line 3222 and before `def test_a_taken_over_store_writes_nothing` at line 3225). The guard is a unit-tier test: it is unmarked, spawns nothing, and reads only function attributes. It lives in the module it guards, as the sibling's guard does in `tests/harness/test_launcher.py`.

**Interfaces:**
- Consumes: `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner` (module-level function in `tests/test_store.py`). The `pytestmark` attribute that pytest decorators set on it is a `list[pytest.Mark]`, where each `Mark` has `.name: str` and `.args: tuple`.
- Produces: `_lease_race_marks() -> dict[str, tuple]`, a module-private helper that maps mark name to args, and `test_the_lease_race_parametrization_runs_all_twenty_attempts_in_soak()`. Nothing else depends on them.

- [ ] **Step 1: Write the failing guard test**

Insert this block into `tests/test_store.py` immediately after the race test's last line, `    assert lease is not None and lease.token == winner`, and before `def test_a_taken_over_store_writes_nothing(repo, stores):`. Keep two blank lines on each side:

```python
def _lease_race_marks() -> dict[str, tuple]:
    test = test_two_processes_taking_one_dead_lease_leave_exactly_one_owner
    return {mark.name: mark.args for mark in getattr(test, "pytestmark", [])}


def test_the_lease_race_parametrization_runs_all_twenty_attempts_in_soak():
    # Test-tier spec V9, option A: the two-process lease race is a concurrency
    # stress probe (40 real child spawns for one property), so it lives in the
    # opt-in `soak` tier with all 20 attempts instead of being shrunk into the
    # unit tier's 0.5s budget. Marks are read off the function because a soak
    # mark deselects the race test from the default run but not this guard.
    marks = _lease_race_marks()
    assert set(marks) == {"parametrize", "soak"}
    assert marks["parametrize"] == ("attempt", range(20))
```

- [ ] **Step 2: Run the guard to verify it fails**

Run: `uv run pytest tests/test_store.py::test_the_lease_race_parametrization_runs_all_twenty_attempts_in_soak -v`
Expected: FAIL at `assert set(marks) == {"parametrize", "soak"}`. pytest shows `{'parametrize'} == {'parametrize', 'soak'}`, with `'soak'` listed as an extra item in the right set.

- [ ] **Step 3: Add the soak marker to the race test**

In `tests/test_store.py`, replace:

```python
@pytest.mark.parametrize("attempt", range(20))
def test_two_processes_taking_one_dead_lease_leave_exactly_one_owner(repo, attempt):
```

with:

```python
@pytest.mark.soak
@pytest.mark.parametrize("attempt", range(20))
def test_two_processes_taking_one_dead_lease_leave_exactly_one_owner(repo, attempt):
```

Leave the test body, `_TAKER`, `_taker`, and `_plant_lease` exactly as they are.

- [ ] **Step 4: Run the guard to verify it passes**

Run: `uv run pytest tests/test_store.py::test_the_lease_race_parametrization_runs_all_twenty_attempts_in_soak -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Run the full default suite**

Run: `uv run pytest`
Expected: all selected tests pass, and the run exits 0. The `--durations` report no longer lists any `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner[...]` case.

- [ ] **Step 6: Confirm the default run no longer collects the race cases**

Run: `uv run pytest tests/test_store.py -k test_two_processes_taking_one_dead_lease_leave_exactly_one_owner --collect-only -q`
Expected: no `test_two_processes_taking_one_dead_lease_leave_exactly_one_owner[` node ids in the output, and a summary line reading `no tests collected (178 deselected) in ...` (every test in the module deselected, either by `-k` not matching it or by the addopts `-m` excluding `soak`). pytest exits with code 5 (no tests collected), which is expected here.

- [ ] **Step 7: Run the soak tier and confirm all 20 moved cases pass**

Run: `uv run pytest -m soak -v`
Expected: the output lists `tests/test_store.py::test_two_processes_taking_one_dead_lease_leave_exactly_one_owner[0]` through `[19]`, 20 cases in all, each `PASSED`, alongside the other existing soak tests (`tests/test_locks.py`, `tests/harness/test_launcher.py`). Any soak test that is also marked `brd` or `git` may be skipped if that binary is missing. The run exits 0 with no failures.

- [ ] **Step 8: Commit with the option and its justification**

```bash
git add tests/test_store.py
git commit -m "$(cat <<'EOF'
Move the 20-iteration lease-race parametrization to soak

Option A of test-tier spec V9: test_two_processes_taking_one_dead_lease_leave_exactly_one_owner
keeps all range(20) attempts and gains @pytest.mark.soak, leaving the default
run.

Why A over B (shrink to range(3) in the default tier): the test is a
multi-process race probe, two real child interpreters racing on one dead lease
per attempt (40 spawns for one property), which is exactly V1's definition of
soak ("concurrency/race stress, no budget"). Shrinking it would keep two cold
python -c interpreters per case in the unit tier, whose 0.5s per-test budget
(tests/conftest.py UNIT_BUDGET_S) they do not fit comfortably, and three
attempts would give a weak signal for a race that only shows up across many
interleavings. Keeping all 20 in soak keeps the full stress run available via
`uv run pytest -m soak`.

A unit-tier guard pins the soak mark and the full range(20) so the move cannot
silently regress.
EOF
)"
```
