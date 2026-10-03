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
