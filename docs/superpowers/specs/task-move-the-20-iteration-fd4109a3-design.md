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
