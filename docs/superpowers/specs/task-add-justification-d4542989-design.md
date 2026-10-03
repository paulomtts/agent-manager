# Subtask d4542989: Verify the `justification:` lines on the five e2e tests

Parent: 838df0c9 "Fix the e2e tier's accounting and shrink soak-adjacent timeouts" (milestone 66ed75cd). Source of truth: `docs/superpowers/specs/2026-10-02-test-tier-design.md` decisions V1 (lines 84-92), V2 (lines 94-116) and V9 (lines 163-169).

## Starting state (checked in this worktree, not master)

The sibling e2e-cap story (3aa663b8) has already been integrated into this worktree. That means three things are already in place:

- The collection check exists in `tests/conftest.py`: `E2E_CAP = 5`, `JUSTIFICATION_PREFIX = "justification:"`, `has_justification`, `e2e_tier_violations` and the `E2ETierCap` plugin.
- All five `e2e` tests already have a docstring line that starts with `justification:`.
- Per V2's rule, the sibling wrote these lines. This subtask's job is to check that each one is accurate and to fix any that are not. It does not write them from scratch.

If the implementer finds a different base, where the lines or the check are missing, they must stop and report it. They must not add the collection check here, because that work belongs to the sibling.

## Scope

The only files this subtask may touch are the docstrings of these five tests:

| Test | File:line |
|---|---|
| `test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch` | `tests/e2e/test_real_harness.py:131` (justification at :138) |
| `test_the_run_went_through_the_pygents_walk` | `tests/e2e/test_real_harness.py:187` (justification at :191) |
| `test_the_real_claude_resolves_a_real_merge_conflict_at_integrate` | `tests/e2e/test_real_harness_integrate.py:247` (justification at :255) |
| `test_the_real_claude_drives_a_two_story_milestone_to_done` | `tests/e2e/test_real_harness_milestone.py:263` (justification at :270) |
| `test_the_real_claude_drives_two_independent_stories_in_parallel` | `tests/e2e/test_real_harness_parallel.py:283` (justification at :291) |

## The accuracy check

V1 says a justification must name what `e2e_fake` cannot observe. For each of the five tests, compare its justification against the `e2e_fake` tests that cover the same scenario in `tests/e2e/`. A justification is accurate only if two things hold:

- The `e2e_fake` tier really does not observe the thing it names.
- The test's own assertions really do depend on that thing.

Three outcomes are possible for each test:

- **Accurate:** leave it unchanged.
- **Wrong but a true reason exists:** rewrite the line to state that true reason.
- **No true reason exists:** keep the test, put no placeholder in the docstring, and flag it. "Flag" means a brd comment on d4542989 or the parent 838df0c9, and a note in the PR or commit message. The comment names the test and explains why no honest justification exists.

The test must stay even in the third case. Reducing the tier below 5 tests is out of scope (spec §8), and so is changing what the tests verify. Do not edit a test's original summary prose. The only exception is when that prose is what makes the justification wrong.

Findings from the spec stage that the implementer must resolve:

1. **`test_the_run_went_through_the_pygents_walk`: likely not honestly justifiable.** Its current line gives two reasons: the test costs nothing extra, and it guards against the real run skipping the walk. Neither reason names something `e2e_fake` cannot observe. `tests/e2e/test_production_wiring.py:221` is an identical `e2e_fake` test that makes the same `checkpoint_rows > 0` assertion. The implementer should either find a real-claude-only reason the assertion depends on, or apply the third outcome above and flag it.
2. **`test_the_real_claude_drives_two_independent_stories_in_parallel`: the stated reason is inaccurate.** The line claims `e2e_fake` cannot show two real processes overlapping in wall time. But `tests/e2e/test_parallel_milestone.py:106` already proves overlap with fake-claude subprocesses, using a rendezvous. The rewrite must name only what is truly unobservable there. Candidates are two real model sessions with real tool permissions running at once in sibling worktrees, and overlap that happens on its own under real, variable durations rather than overlap the rendezvous forces. Use only reasons the test's assertions actually depend on.
3. **The other three tests (main toy-card, integrate, milestone): appear accurate.** The first is about the real CLI's argv, exit codes, tool permissions and result-file compliance. The second is about real reasoning over a real conflict. The third is about output and timing variability across sequential real dispatches. Confirm each against the matching `e2e_fake` twin before leaving it unchanged.

Every edited line must still start with `justification:` after leading whitespace is stripped (`has_justification`). Otherwise collection fails with `pytest.UsageError("e2e tier check failed: ...")`.

## Out of scope

- `tests/harness/test_launcher.py`: the soak move and timeout shrinks belong to sibling 1ad895f4.
- The `range(20)` lease-race parametrization in `tests/test_store.py`: belongs to sibling fd4109a3.
- `tests/conftest.py` and `pyproject.toml`: their mechanics belong to the e2e-cap story.
- Any test body or any assertion.
- Adding, removing or re-marking tests.
- Anything under `src/`.

## Tests

This subtask adds and moves no tests. Acceptance rests on the existing tiers:

- **Collection check**, run as part of the default run's collection, `unit`+`git` tiers: `uv run pytest --collect-only -m e2e` lists exactly 5 items with no `e2e tier check failed` error.
- **The five tests themselves**, `e2e` tier (opt-in, real money): they keep their `@pytest.mark.e2e` module marker and are not run by this subtask. The only change is docstring text.
- **Full suite**, default `unit`+`git` run: `uv run pytest` stays green. Only docstrings change, so nothing else can regress.

Verification contract: `uv run pytest`. There is no typecheck or lint step (source: CLAUDE.md).

## Note on inputs

The exploration summary was cut off mid-sentence at 8000 characters, in its file:line reference list. Every reference used in this spec was re-checked directly against this worktree. The missing tail is not relied on.
