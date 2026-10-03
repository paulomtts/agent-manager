# Measure the default tier and run every opt-in tier standalone (75f49b26)

Date: 2026-10-03
Card: 75f49b26-12bb-4d8f-b867-80c042e02ced. Parent story: 1346e04e "Prove the new budget and update the docs". Milestone: 66ed75cd "Milestone 15: test tiers — a fast default suite".
Governing doc: `docs/superpowers/specs/2026-10-02-test-tier-design.md` (the test-tier addendum, which extends design spec §14). §6 "Testing" (the final bullet and the V6/V7/V8 bullet) defines what this subtask has to prove.

## Scope

This is a measurement and regression-proof pass. It changes no source, test, config or doc files. Every earlier story in the milestone (V1–V9) shows `done` on the board, but the board status is not proof that *this* subtask's own worktree/branch was built on top of all nine of them — stories V4-V9 shipped on separate sibling branches and only land together once an Integrate step merges every story tip into a `m15-integrate`-style branch (design spec §12 "Failure, escalation, and blast radius", line 476: "Integrate merges every story tip into one local `<prefix>-integrate` branch... after the last level"). This subtask must not assume that merge has already happened just because the board says so. Its outputs are numbers and pass/fail results. They go in the commit message, which may be an empty/`--allow-empty` commit if nothing else changes, or in a short card comment for `am status` readers.

1. **Integration precondition — confirm the branch actually contains V1-V9, not just the board.** Before measuring anything, run `uv run pytest --collect-only -q` (default tier) and `--collect-only -q -m brd`, `-m e2e_fake`, `-m soak`. The addendum's post-retier shape is: default tier collects roughly 250 fewer items than the full 2750-item suite (most of `test_cli.py`'s and `test_orchestrate.py`'s real-board tests move out), and `-m brd`/`-m e2e_fake`/`-m soak` each collect a substantial, non-trivial number of items (dozens to ~175), not zero and not a single item. If instead the default tier collects nearly all ~2750 items and/or `-m e2e_fake` or `-m soak` collects **zero** tests, that means this worktree's branch predates the V5-V9 merges (the retiering and auto-marking haven't landed here yet) — stop, do not measure, and report the branch as not yet built on the integrated milestone state; that is a blocking precondition for this subtask, not something to fix by rebasing, cherry-picking or re-marking tests here. Only proceed to steps 2-4 once collection confirms the retiered shape.
2. **Quiet-machine precondition.** Before measuring, check for other `pytest` processes, especially in sibling worktrees under `.claude/worktrees/`. Exploration already saw one: `uv run pytest -q` in `m17/task-retry-the-wal-pragma-in-108f4310`. The addendum's own audit (§2, lines 44-46) found that a concurrent run inflates wall time by 10-20%. Wait for those runs to finish before taking the measurement. If the machine never goes quiet, record the concurrent runs next to the number and say the number is inflated.
3. **Default-tier measurement.** Run `uv run pytest --durations=0`. Record:
   - the "before" baseline: 2592 passed in 1267s (addendum §1/§2)
   - the "after" figures: pass/skip/fail counts and wall time
   - the slowest entries from the durations report

   Then confirm the default tier (`unit`+`git`) is at or under the V1 target of 90s serial (addendum §3 line 92).
4. **Opt-in tiers standalone.** Run each of these as its own invocation and confirm it passes:
   - `uv run pytest -m brd`
   - `uv run pytest -m e2e_fake`
   - `uv run pytest -m soak`

   Record the pass count and wall time for each. This is the main regression check: it catches a retiered test that silently depended on fixture state that only existed in the old default run (addendum §6, lines 202-204).
5. **Not run:** `uv run pytest -m e2e`. That tier costs real money and is hard-capped at 5 tests. Run it only if the operator explicitly says to.

## Observable result

The deliverable is a short record containing:
- before and after default-tier wall time
- default-tier pass count
- whether the ≤90s budget was met
- pass count and wall time for each of `brd`, `e2e_fake` and `soak`
- a note saying whether the machine was quiet

`uv run pytest` (the canonical verify command) still passes.

## Error paths

- **The branch is not actually built on the integrated V1-V9 state** (step 1's collection counts don't match the retiered shape — e.g. `-m e2e_fake` or `-m soak` collects zero tests, or the default tier still collects nearly the full ~2750-item suite): stop before measuring. Report this as a blocking precondition gap, not as a regression or a budget failure, and name the collection counts that revealed it. Do not work around it by rebasing, merging sibling branches or re-marking tests in this subtask — that integration is the Integrate step's job, not this one's.
- **Default tier over 90s:** do not "fix" it here by retiering, deleting or editing tests. Record the measured time and the top offenders from `--durations=0`, then report it as a failure of the budget. The rework belongs to a follow-up card, not this one.
- **An opt-in tier fails standalone (but collection counts looked right in step 1):** record which tests failed and the failure output. Report it as a regression from the earlier retier stories. Do not patch fixtures or tests in this subtask.
- **A binary is missing** (`git`/`brd` not on PATH, so the skip hook or `skipif` guard skips the tests): report the skips explicitly. Do not treat a skipped run as a passing one.
- **Machine cannot be made quiet:** say so in the record and qualify the wall-time figures as inflated.

## Tests

This subtask adds no new tests. It only runs existing tiers, and the tier membership it measures is the one the V1 placement rule already assigned (addendum §3, lines 84-92):
- a test is `unit` only if it spawns no subprocess at all and is driven only through injected fakes
- a test is `git` only if it uses real git and nothing else real
- `brd`, `e2e_fake`, `soak` and `e2e` are opt-in tiers, for real brd, fake-claude wiring, deliberate concurrency stress and real-money claude respectively

If some unforeseen need for a test came up, the test would go in its tier by that rule. It would not go in the default tier out of habit.

## Out of scope

- Editing `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 or `CLAUDE.md`. Both belong to the sibling subtask 1ea1036e "Update design spec section 14 and CLAUDE.md", which is blocked on this subtask.
- Any source, test, `pyproject.toml` or `tests/conftest.py` change.
- Using pytest-xdist or parallel execution as the canonical verify command.
- The pygents engine, the checkpoint format, the harness adapter contract, and `dispatch.py`'s `LauncherFn` seam.
- Shrinking or re-scoping the 5 `e2e` tests.
- Milestone 14's `am run --board` work.
