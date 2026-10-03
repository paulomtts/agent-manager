# Update design spec §14 and CLAUDE.md for the six test tiers (1ea1036e)

Subtask of story 1346e04e "Prove the new budget and update the docs", Milestone 15 (test tiers — a fast default suite). Narrows decision V1 of `docs/superpowers/specs/2026-10-02-test-tier-design.md` (§3, lines 84-92) and its §4 file map (lines 195-196) to the two documentation files. Docs-only: no source, test, `pyproject.toml` or `tests/conftest.py` change.

## Base

Work on the milestone's integrate line (`m15-integrate`, which this worktree already reflects), where the V1-V9 code has landed: `pyproject.toml` already declares the `git`, `brd`, `e2e_fake`, `soak` and `e2e` markers and the `addopts` expression `-m "not brd and not e2e_fake and not soak and not e2e"`. The docs must describe that landed state, not master.

## Scope

### 1. `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 "Testing" (lines 507-522)

- Replace the "Steps" bullet ("against temporary git repositories and a temporary `brd` board; no network") with the six-tier table from V1. One row per tier, columns: tier, marker / how to run, default or opt-in, what belongs there, budget.
  - `unit` — no marker, default — pure functions and anything driven through an injected fake (`FakeLauncher`, `FakeDriver`, a fake `board_api`, `FakeBoard`); no subprocess of any kind — ≤0.5s per test, tier ≤30s.
  - `git` — `@pytest.mark.git`, default — real `git` in `tmp_path` only, no `brd`, no `claude` — ≤2s per test, tier ≤45s.
  - `brd` — `@pytest.mark.brd`, opt-in `uv run pytest -m brd` — the real-`brd` adapter contract — tier ≤90s.
  - `e2e_fake` — `@pytest.mark.e2e_fake`, opt-in `-m e2e_fake` — production wiring under the fake `claude`, one test per scenario family — tier ≤8min.
  - `soak` — `@pytest.mark.soak`, opt-in `-m soak`, nightly — concurrency/race stress — no budget.
  - `e2e` — `@pytest.mark.e2e`, opt-in `-m e2e`, real `claude`, costs real money — hard-capped at 5 tests, each with a `justification:` docstring line naming what `e2e_fake` cannot observe.
- State the default-run target beside the table: `uv run pytest` runs `unit` + `git`, target ≤90s serial.
- State the placement rule in one sentence: a test's tier is chosen by what it actually spawns or touches, not by the directory it lives in.
- Reconcile the existing "End to end" bullet (one opt-in real-harness test) with the `e2e` row so §14 does not contradict itself — describe the tier as it is (opt-in, capped at 5); do not change what those tests verify.
- Leave the "Pure functions", "Adapters" and "Engine" bullets and the closing "The project verifies with `pytest`, matching `brd`." sentence intact except where they must point at the table.
- May reference `2026-10-02-test-tier-design.md` as the detailed rationale.

### 2. `CLAUDE.md` (repo root)

Add a testing/tiers section (or extend "Verification") that:
- says `uv run pytest` runs the default `unit` + `git` tiers;
- names each marker (`git`, `brd`, `e2e_fake`, `soak`, `e2e`) with a one-line meaning and the unmarked = `unit` rule;
- lists the opt-in commands `uv run pytest -m brd`, `uv run pytest -m e2e_fake`, `uv run pytest -m soak`, and `uv run pytest -m e2e` (real money, cap of 5, `justification:` line required);
- gives the placement rule (tier by what the test spawns/touches) so future milestone plans copy the right sentence;
- points at design spec §14 for the table.
Keep existing sections otherwise unchanged; the "no separate lint or typecheck command" line stays.

## Out of scope

- Any code, `pyproject.toml`, `tests/conftest.py` or test file edits (landed by V1-V9 siblings).
- Re-measuring the default tier or running opt-in tiers standalone, or recording before/after numbers (sibling 75f49b26, done).
- pytest-xdist / parallel as the canonical verify command; the pygents engine, checkpoint format, harness adapter contract, or `dispatch.py`'s `LauncherFn` seam; reducing the `e2e` tier below 5 tests or changing what they verify; milestone 14's `am run --board` work.
- Any doc other than the two above.

## Observable behaviour / error paths

No runtime behaviour changes. The only failure mode is the docs disagreeing with the code: marker names, opt-in commands and budgets in both files must match `pyproject.toml` (lines 46-61) and V1 exactly. A reviewer checks that §14 and CLAUDE.md agree with each other and with `pyproject.toml`.

## Tests

No new tests. Per the placement rule (tier by what a test spawns/touches), a docs-only change spawns nothing and adds no test to any tier.

Verification: `uv run pytest` (default `unit` + `git` run) stays green — confirms no non-doc file was accidentally altered. No typecheck or lint command exists.
