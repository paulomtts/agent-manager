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
