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
