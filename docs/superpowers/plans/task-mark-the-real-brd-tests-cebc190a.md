<!-- task-pipeline: validated -->
# Subtask cebc190a — Mark the real-brd tests `brd` and the stress probes `soak`

Parent story: 9d458d73 ("Retier test_rollup.py and test_board.py's real-brd and soak tests"), milestone 66ed75cd. This is the story's only child, so no sibling owns any part of this scope.

Source of truth: `docs/superpowers/specs/2026-10-02-test-tier-design.md` §1 (V1, six tiers) and V8 (the decision this card implements). Together they extend design §14 of `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.

## Starting state in this worktree (verified, not master)

The blocker story 186fe934 ("Define and enforce the test tiers") is already integrated into this worktree's base. That is not true of `master`.

- `pyproject.toml:33-39` registers `e2e`, `git`, `brd`, `e2e_fake` and `soak`. `addopts` (`:48`) carries `-m "not brd and not e2e_fake and not soak and not e2e"`, so `-m brd` and `-m soak` are real selectors and the default run excludes both tiers.
- The local `requires_brd` skipif helpers are already gone from both files. The root `tests/conftest.py` skips `brd`-marked tests when `brd` is not on PATH (as the docstring at `tests/steps/test_rollup.py:17-18` describes).
- The coarse bulk-marking pass is already applied. Every real-brd test in both files carries `@pytest.mark.brd`, and soak is stacked on `test_rollup.py:264`, `test_rollup.py:436` and `test_board.py:571`. It is also stacked on `test_rollup.py:390`, which is wrong.

If the executing worktree turns out not to have the markers registered (for example, after a rebase onto bare `master`), stop and report. Without registration the marks would only raise unknown-marker warnings, and the acceptance commands below would not mean anything. Do not register markers or add conftest machinery here; that work belongs to 186fe934.

## Scope (the delta)

1. In `tests/steps/test_rollup.py`, remove the `@pytest.mark.soak` line at `:388` from `test_a_failed_rollup_releases_the_board_lock` (`:390`), leaving `@pytest.mark.brd` only. The test runs no threads, barrier or iteration loop meant to provoke a race. It only asserts that two error paths (unknown card, and ancestry past the cap) release the lock. Under V1/V8 that makes it `brd`, not `soak`.
2. Confirm that the final marker set in both files matches the list below exactly, and fix any mismatch. Nothing else changes: test bodies, fixtures, helpers, conftest, pyproject, the `fake_brd`-driven tests and the pure tests are all out of scope.

## Final marker set

Placement rule (tier spec §1): a test's tier depends on what it actually touches or spawns.
- A real-brd adapter-contract test is `brd`.
- A deliberate concurrency/stress probe is `soak`, stacked with `brd` when it also spawns real brd.
- Argv-building, decode and fake-driven tests stay unmarked (`unit`).

`tests/steps/test_rollup.py` (17 real-brd test functions in all)
- `soak` + `brd` (exactly two, per V8):
  - `test_the_walk_is_capped_at_sixteen_ancestors` (`:264`)
  - `test_concurrent_rollups_reach_done` (`:436`; uses `threading.Barrier` inside a `_RACE_ITERATIONS` loop)
- `brd` only (15 tests): every other `temp_board` test from `:82` through `:405`. After the fix, this includes `test_a_failed_rollup_releases_the_board_lock` (`:390`).
- Unmarked/unit (unchanged):
  - the `fake_brd` process-lock tests (`:644`, `:669`, `:695`, `:709`, `:717`)
  - the pure `stored_status`/`rollup_status` tests (`:737`-`:769`)

`tests/test_board.py` (36 real-brd test functions in all)
- `soak` + `brd` (exactly one): `test_brd_update_survives_concurrent_writers` (`:571`).
- `brd` only (35 tests): the other `temp_board`/real-binary tests between `:276` and `:1064`.
- Unmarked/unit (unchanged):
  - the argv-builder, `_run` and decode/envelope tests (`:22`-`:228`)
  - the parametrized shape-validation tests that monkeypatch the runner (`:379`, `:855`, `:945`, `:1074`, `:1094`)
  - `:478`, and `:1107`-`:1165`

Across the two files, exactly three tests carry `soak`: the three named in V8.

## Observable behavior / acceptance

- `uv run pytest` passes and does not run any of the `brd` or `soak` tests above.
- `uv run pytest -m soak tests/steps/test_rollup.py tests/test_board.py --collect-only -q` lists exactly the three V8 tests. `test_a_failed_rollup_releases_the_board_lock` must not appear.
- `uv run pytest -m brd tests/steps/test_rollup.py tests/test_board.py --collect-only -q` lists 53 tests: 17 from rollup and 36 from board. None of them is parametrized. Because `-m brd` replaces the addopts expression, the count includes the stacked soak tests.
- With `brd` installed, `uv run pytest -m brd` passes. Without `brd`, those tests skip rather than fail.
- With `brd` installed, `uv run pytest -m soak` passes.

Error paths: this subtask adds none. The only failure mode is a worktree that lacks the tier markers, which is a stop-and-report condition (see above).

## Tests

No new tests. This subtask changes only the tier assignment of existing tests, so its test list is the marker set above. Verify with the collect-only counts and the full-suite command `uv run pytest` (CLAUDE.md). There is no lint or typecheck command.

## Out of scope (tier spec §8 / card)

- pytest-xdist or parallel verify commands
- marker registration, auto-mark hooks, the PATH shim and the e2e cap (all story 186fe934)
- any change to the pygents engine, the checkpoint format, the harness adapter contract, or `dispatch.py`'s `LauncherFn` seam
- changes to the `e2e` tier
- milestone 14's `am run --board` work
- rewording design §14

---

# Mark the real-brd tests `brd` and the stress probes `soak` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `soak` tier in `tests/steps/test_rollup.py` and `tests/test_board.py` contain exactly the three V8 concurrency/stress probes, by removing the stray `@pytest.mark.soak` from `test_a_failed_rollup_releases_the_board_lock`. Then confirm the whole brd/soak/unit marker set of both files against the spec.

**Architecture:** This is a marker-only change to one test file. There is no production code and no new test. The "failing test" in the RED step is the spec's own acceptance probe: `pytest -m soak ... --collect-only -q` currently collects 4 tests and must collect exactly 3. GREEN is a one-line deletion. The remaining steps re-check the whole marker inventory and run the full suite.

**Tech Stack:** Python 3.12, pytest 9 (markers registered in `pyproject.toml`), `uv`.

**Spec:** `docs/superpowers/specs/task-mark-the-real-brd-tests-cebc190a-design.md` (prepended above), implementing V8 of `docs/superpowers/specs/2026-10-02-test-tier-design.md`.

## Global Constraints

- The verify command is `uv run pytest` (CLAUDE.md). There is no lint or typecheck command.
- Markers `e2e`, `git`, `brd`, `e2e_fake` and `soak` must already be registered in `pyproject.toml:33-39`, and `addopts` (`pyproject.toml:48`) must contain `-m "not brd and not e2e_fake and not soak and not e2e"`. If either is missing, STOP AND REPORT. Do not register markers or edit conftest/pyproject.
- Exactly three tests carry `soak` across the two files: `test_the_walk_is_capped_at_sixteen_ancestors`, `test_concurrent_rollups_reach_done` and `test_brd_update_survives_concurrent_writers`.
- `-m brd` on the two files collects 53 tests: 17 from rollup and 36 from board.
- Do not touch test bodies, fixtures, helpers, `tests/conftest.py`, `pyproject.toml`, the `fake_brd` tests or the pure tests.

## Review Focus

- Stacked marker order. The other two soak tests put `@pytest.mark.soak` directly above `@pytest.mark.brd`. Delete only the `soak` line at `:388` and leave `@pytest.mark.brd` immediately above `def`. Step 1's collect-only output pins this, and Step 4's `-m brd` count of 53 confirms the test kept `brd`.
- A deleted `brd` mark by mistake. This would drop the test into the default unit run, where the PATH stub would make it fail or misbehave. Step 4 (`-m brd` collects 53) and Step 5 (full suite) catch it.
- A stray soak mark elsewhere in the two files that the spec's line numbers miss. Step 3's grep inventory checks every `@pytest.mark.soak` and `@pytest.mark.brd` line in both files against the spec list.
- A worktree without registered markers. With `--strict-markers` absent, `-m soak` would collect 0 and look like a different failure. Step 0 checks registration first.
- Running without `brd` on PATH. The `-m brd` tests skip instead of fail (conftest `:332`). Collect-only counts do not depend on the binary, so acceptance holds either way. Step 6 runs the tiers only if `brd` is installed.

---

### Task 1: Retier `test_a_failed_rollup_releases_the_board_lock` from soak to brd and verify the full marker set

**Files:**
- Modify: `tests/steps/test_rollup.py:388-390`
- Test: `tests/steps/test_rollup.py`, `tests/test_board.py` (collect-only acceptance; no new test file. The tier for both files' real-brd tests is `brd`/`soak` by marker, and the files stay where they are.)

**Interfaces:**
- Consumes: the registered `brd` and `soak` markers (`pyproject.toml:33-39`) and the addopts tier filter (`pyproject.toml:48`), both from story 186fe934 and already on this branch.
- Produces: nothing code-level. The output is the final tier assignment of the two files.

- [ ] **Step 0: Confirm the tier markers are registered (stop-and-report gate)**

Run: `grep -nE '"(brd|soak|git|e2e_fake|e2e):' pyproject.toml && grep -n 'not brd and not e2e_fake and not soak and not e2e' pyproject.toml`

Expected: five marker lines (`e2e`, `git`, `brd`, `e2e_fake`, `soak`) at `pyproject.toml:34-38` and the `addopts` line at `:48`. If any is missing, STOP. Report that the worktree lacks story 186fe934's markers, and do not continue.

- [ ] **Step 1: Run the soak acceptance probe and watch it fail (RED)**

Run: `uv run pytest -m soak tests/steps/test_rollup.py tests/test_board.py --collect-only -q`

Expected (FAIL against the spec, which requires exactly 3): four node IDs, including the stray one, and a "4/... tests collected" summary.

```
tests/steps/test_rollup.py::test_the_walk_is_capped_at_sixteen_ancestors
tests/steps/test_rollup.py::test_a_failed_rollup_releases_the_board_lock
tests/steps/test_rollup.py::test_concurrent_rollups_reach_done
tests/test_board.py::test_brd_update_survives_concurrent_writers
```

If it lists only the three V8 tests, someone has already applied the fix. Skip to Step 3.

- [ ] **Step 2: Remove the stray soak mark (GREEN)**

In `tests/steps/test_rollup.py`, replace these lines:

```python
@pytest.mark.soak
@pytest.mark.brd
def test_a_failed_rollup_releases_the_board_lock(temp_board):
```

with:

```python
@pytest.mark.brd
def test_a_failed_rollup_releases_the_board_lock(temp_board):
```

Leave the test body (`:391-401`) unchanged.

- [ ] **Step 3: Re-run the soak probe and inventory every mark (GREEN check)**

Run: `uv run pytest -m soak tests/steps/test_rollup.py tests/test_board.py --collect-only -q`

Expected: exactly these three node IDs and a "3/... tests collected" summary. `test_a_failed_rollup_releases_the_board_lock` must be absent.

```
tests/steps/test_rollup.py::test_the_walk_is_capped_at_sixteen_ancestors
tests/steps/test_rollup.py::test_concurrent_rollups_reach_done
tests/test_board.py::test_brd_update_survives_concurrent_writers
```

Then run: `grep -nE 'pytestmark|mark\.soak' tests/steps/test_rollup.py tests/test_board.py`

Expected: exactly three lines, `tests/steps/test_rollup.py:262`, `tests/steps/test_rollup.py:433` (shifted up by one from `:434`, since deleting line `:388` shifts every later line up by one) and `tests/test_board.py:569`, all `@pytest.mark.soak`, and no `pytestmark` line.

- [ ] **Step 4: Verify the brd count**

Run: `uv run pytest -m brd tests/steps/test_rollup.py tests/test_board.py --collect-only -q`

Expected: "53/... tests collected". That is 17 IDs under `tests/steps/test_rollup.py` (including `test_a_failed_rollup_releases_the_board_lock` and both rollup soak tests) and 36 under `tests/test_board.py` (including `test_brd_update_survives_concurrent_writers`). None of them is parametrized, so there are no `[...]` suffixes. The unit tests must not appear: the `fake_brd` lock tests (`test_a_rollup_waits_for_another_process_holding_the_board_lock`, `test_a_nested_rollup_walk_runs_every_brd_call_under_one_flock`, `test_a_board_lock_timeout_propagates_before_any_brd_call`, `test_a_failed_rollup_releases_the_board_flock`, `test_the_board_lock_is_one_per_resolved_repository_and_defaults_to_the_cwd`), the `stored_status`/`rollup_status` tests, the argv/decode tests, the monkeypatched parametrized shape tests and `test_write_lock_is_reentrant`.

If the count is not 53, compare `grep -c '^@pytest.mark.brd' tests/steps/test_rollup.py` (expect 17) and `grep -c '^@pytest.mark.brd' tests/test_board.py` (expect 36) against the spec's "Final marker set", and fix only the mismatched marker line.

- [ ] **Step 5: Run the full default suite**

Run: `uv run pytest`

Expected: PASS. Then run `uv run pytest tests/steps/test_rollup.py tests/test_board.py --collect-only -q`. It should report 53 deselected (`brd` and `soak` excluded by addopts), and none of the three soak node IDs should appear among the collected tests.

- [ ] **Step 6: Run the opt-in tiers if `brd` is installed**

Run: `command -v brd && uv run pytest -m brd tests/steps/test_rollup.py tests/test_board.py && uv run pytest -m soak tests/steps/test_rollup.py tests/test_board.py`

Expected: if `brd` is on PATH, both runs PASS, with 53 tests for `-m brd` and 3 for `-m soak`. If `command -v brd` prints nothing, skip this step and note it in the report. Without the binary those tests skip via `tests/conftest.py:332` instead of failing.

- [ ] **Step 7: Commit**

```bash
git add tests/steps/test_rollup.py
git commit -m "Retier test_a_failed_rollup_releases_the_board_lock from soak to brd

It drives no threads or race loop, so under tier spec V8 only the three
explicit concurrency probes stay soak."
```
