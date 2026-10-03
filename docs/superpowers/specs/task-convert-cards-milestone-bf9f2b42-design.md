# Convert `cards` / `milestone_board` / `resume_board` to FakeBoard (bf9f2b42)

Card: bf9f2b42-ae9d-4201-88da-2d43974fb55a. Parent story: a0b987c1 "Move test_cli.py off the real board". Milestone 15: test tiers (66ed75cd).
Narrows: `docs/superpowers/specs/2026-10-02-test-tier-design.md` V5 (lines 145-150), using the seam from V3 (lines 127-136) and the tier rule from V1 (lines 84-92).

## Starting state (checked in this worktree, not master)

- This worktree already has the V3 seam: `board.run_brd` (src/agent_manager/board.py:168), which every public board function calls. It also has `FakeBoard` and the `fake_board` fixture (tests/conftest.py:135, :401), plus the V1 markers and addopts in pyproject.toml:33-48. The exploration notes said these exist only on unmerged branches. That is true of master, but this worktree already contains them, so nothing has to be merged first. If `board.run_brd` or `fake_board` is missing when work starts, stop and report it. Do not rebuild them here.
- Find the fixtures by name, not by line number. In this worktree they are: `cards` at tests/test_cli.py:1254, `milestone_board` at :2573 (with `_block` at :2554 and `M2_SHAPE` at :2564), and `resume_board` at :4952. These numbers do not match the card's (:1148-1195, :2502-2532) or the exploration notes' (:5544).
- The exploration findings were cut off at 8000 chars, partway through "STEP 6 — test-tier PLACEMENT RULE". The upstream stage wrote more than its limit. The tier assignments below come straight from spec V1, not from the missing text.

## Scope

Change only the bodies of the three fixtures in tests/test_cli.py:

1. **`cards`**: request `fake_board` as well as `project`. Seed the milestone -> story -> subtask chain with `fake_board.add_card(title, parent_id=...)`. Keep the same titles and return the same `{"milestone", "story", "subtask"}` dict.
2. **`milestone_board`**: request `fake_board`. Seed the decoy root "Milestone 1: walking skeleton", then "Milestone 2: make the skeleton real" with the `M2_SHAPE` stories and subtasks. Build the B-after-A and C-after-B story chain and each story's subtask chain at creation time with `add_card(..., blocked_by=[previous_id])`, not with `_block`. Return the same `{"milestone", "stories", "subtasks", "titles"}` dict with the same keys and contents.
3. **`resume_board`**: same approach. Milestone 4, story A with a1 then a2 (a2 `blocked_by=[a1]`), and story B with b1. Story B is `blocked_by=[story_a]`, so story A must be seeded before story B. The returned dict keeps the same keys.

All three keep depending on `project`, because the CLI still needs a real git repo and directory. Any fixture docstring that says "a real brd board" or mentions `brd block` must be updated to describe the FakeBoard seeding.

## Constraints that come from FakeBoard

- `brd block` and `brd add` are not argv shapes FakeBoard answers (any argv outside show/tree/roots/update --status/comment add/comment list raises AssertionError). This is why blocking edges are seeded with `blocked_by` at creation instead of added afterwards.
- `add_card` rejects `status="blocked"`. Blocked status is derived from `blocked_by`, the same way real brd does it.
- No seeded title or description may contain `[[`.
- Seeding is not recorded in `fake_board.writes`. Tests that check that nothing was written must still pass.

## Out of scope

- The `project` fixture, including its real `git init` and `brd init`.
- The `_add_card` and `_block` helpers. Inline `_add_card`/`_block` calls still use them at test_cli.py:1413-1416, :2923, :2968 and :2989-2996, so they must stay. Converting those tests is not this card's work.
- The 5 fixture-less tests and the 8 `--pretty` tests. Sibling card 94580a42 is blocked on this card and owns them.
- Adding, removing or changing tier markers or the `requires_git`/`requires_brd` decorators. `requires_brd` still applies because `project` runs `brd init`.
- Everything listed in spec §8: xdist as the verify command, changes to the pygents engine, checkpoint format, harness adapter contract or `dispatch.py`'s LauncherFn, changes to the e2e tier, and M14's `am run --board`.

## Observable behaviour and error paths

- Every test that requests `cards`, `milestone_board` or `resume_board` (80 tests: 43 with a single-line signature, 37 with a multi-line one — counting by signature text, not by `def test_` line alone, catches both) keeps all of its assertions unchanged. It must pass or fail exactly as it did before the change.
- Board reads and writes inside those tests, including `board.show(...).status` checks such as test_run_card_really_moves_the_card_on_the_board (:1329) and status changes during setup, now go to the in-memory FakeBoard.
- If a test using these fixtures hits an argv FakeBoard does not answer, that AssertionError is a real finding. Report it. Do not weaken the assertion and do not extend FakeBoard in this card.
- If a test compares census or tree order and that order changes because FakeBoard's `created_at` values differ from real brd timestamps, that is drift. Fix it in the fixture seeding (pass explicit `created_at` values or blocking edges), not in the test.

## Test list and tier (spec V1 rule)

V1 rule: `unit` means no subprocess of any kind and driven through an injected fake. `git` means real git in `tmp_path` and no `brd`. `brd` (opt-in) means the real-brd adapter contract.

- **No new tests.** The card's deliverable is that the existing tests are unchanged.
- **Existing tests using the three fixtures: target tier `git`.** Their card data now comes from FakeBoard and their remaining subprocess use is real git through `project`. One leftover is outside this card: `project` still runs `brd init` once per test, so these tests do not fully meet `git`'s "no brd" condition yet. That is acceptable here because `project` is out of scope. Do not mark them `brd`, which would remove them from the default run. Do not mark them `unit`, because they spawn git.
- **Verification (as the card states it):**
  - `uv run pytest tests/test_cli.py -v` gives the same pass/fail/skip result for every test as a run on the pre-change tree.
  - Full `uv run pytest` is green.
  - Before changing anything, run `uv run pytest tests/test_cli.py -v` on the pre-change tree and record its wall time and test count as the baseline (an exploration note's figure of 234 tests in 257s, 72s of it fixture setup, is stale for this worktree -- `--collect-only` here currently shows 302 tests in the file -- so measure fresh rather than trusting that number). After the change, record the new wall time on the card next to that freshly measured baseline.
