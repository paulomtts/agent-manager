# 1.5 e2e_fake: dependent milestones really stack — spec

Card: `d8b6ed12` (subtask of story `8fd3e3de`, blocked by `5bfe746d`).
Parent design: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`
(cited below as **D**, by section and line).

## Purpose

The stacking decision (`orchestrate.milestone_bases`) and its wiring into
`run_board` / `_run_board_async` are already implemented and unit-pinned
(cards `5b772688`, `5bfe746d`; `tests/test_orchestrate.py`). Nothing yet shows,
through the production wiring and real `git`/`brd` under the fake `claude`,
that a blocked milestone's branches **actually contain** its blocker's commits.
This card adds that proof, at the `e2e_fake` tier, as D §Testing (lines 112-114)
asks: "a two-milestone board where the second is blocked by the first: the
second's worktree contains the first's commits; a three-milestone chain", plus
the D §1 table row "one blocker B that is `done` (not `merged`) **and** whose
integrate branch exists locally → `integrate_branch(B)`" (line 45) exercised as
a relaunch.

This card is **tests only**. No production source changes. If a test written to
this spec fails against the current code, that is a real defect: stop and
report it rather than bending the assertion.

## Inherited constraints

- A milestone with exactly one open blocker B runs on `<prefix(B)>-integrate`
  (D §1 table, line 44).
- A `done` (not `merged`) blocker whose integrate branch exists locally is a
  stack target too (D line 45); "open" is `dag.board_levels`' notion and
  `merged`/`canceled`/`archived` are landed (D lines 49-51).
- Stories of a stacked milestone root on the milestone's base, and its own
  Integrate merges into its own `<prefix>-integrate` (D lines 66-68).
- `am` never touches `--base-branch` (D §Non-goals, line 30): `main` must be
  byte-identical (same `rev-parse`) before and after every board run.
- Chain, not merged base, for multi-blocker milestones (D lines 31-32): the
  three-chain must be strictly linear, each link on its immediate blocker.
- Tier rules (CLAUDE.md "Test tiers"): `@pytest.mark.e2e_fake` = production
  wiring under the fake `claude`, opt-in with `uv run pytest -m e2e_fake`, tier
  budget ≤8 min. The default `uv run pytest` must remain green and unaffected.
- JSON keys additive only; journal stays schema 1 (card text). This card adds
  no keys.

## Where

All new tests go in `tests/e2e/test_run_board.py` (the existing `run_board`
e2e_fake module; tests mirror `src/agent_manager/orchestrate.py`'s board run).

- Each new test carries an explicit `@pytest.mark.e2e_fake`. The module stays
  unmarked at module level (`test_this_module_runs_in_the_default_suite_unmarked`
  checks this and must keep passing).
- Insert the new tests **after**
  `test_a_two_open_blocker_milestone_refuses_the_whole_board_up_front` and
  **before** `test_no_rendezvous_is_left_armed_for_later_tests`, which must stay
  last in the module.
- Reuse the module's helpers: `board_root` fixture, `_milestone`, `_run_board`,
  `_entries`, `_load_run`, `_git`, `_local_branches`. Use
  `integration.integration_branch(prefix)` for integrate names and
  `cli.worktree_for(root, branch)` for worktree paths. One small shared helper
  is allowed, e.g. `_is_ancestor(root, ancestor, descendant) -> bool` wrapping
  `git merge-base --is-ancestor` (exit 0 → True, exit 1 → False, anything else
  raises). Do not duplicate existing helpers.
- Keep each milestone at one story / one subtask (`_milestone`) to stay inside
  the tier budget.

## Observable behavior each test pins

Notation: for milestone `m`, `I(m) = f"{m['prefix']}-integrate"`, `S(m) = m['branch']`
(its only subtask's branch). "Contains X" means `git merge-base --is-ancestor X <ref>`
succeeds in the repo at `root`.

### T1 — two milestones, B blocked by A, B stacks on A

`test_a_blocked_milestone_stacks_on_its_blockers_integrate_branch` (e2e_fake).

Setup: `a = _milestone(root, "A", "ba")`, `b = _milestone(root, "B", "bb", blocked_by=(a["id"],))`;
record `main_before = rev-parse main`. Run `_run_board(root, a, b)`.

Must observe:
1. `result["ok"] is True`; levels are `[[A], [B]]` (as
   `test_a_blocked_by_pair_runs_in_order` asserts); both entries `done`.
2. A's run row: `_load_run(..A..).base_branch == "main"`.
   B's run row: `_load_run(..B..).base_branch == I(a)` (`"ba-integrate"`).
   Every subtask row in B's run (`run.stories[*].subtasks[*]`) whose story is
   B's root story has `base_branch == I(a)`.
3. Stacking is real in git: `S(b)` contains `S(a)` and contains the tip of
   `I(a)`; `I(b)` contains `I(a)`.
4. If `cli.worktree_for(root, S(b))` still exists after the run, its `HEAD`
   contains `S(a)` and `IMPLEMENTATION.md` content that A's coder wrote is
   reachable there (`git log` of `S(b)` includes A's subtask commit). The
   ancestry assertions in (3) are the binding check; this one is applied only
   when the worktree is present, so the test does not depend on worktree
   cleanup policy.
5. Negative control: `main` does not contain `S(a)` nor `I(a)`, and
   `rev-parse main == main_before`.

### T2 — three-milestone chain A ← B ← C

`test_a_three_milestone_chain_stacks_each_link_on_the_one_before` (e2e_fake).

Setup: `a` (`"ba"`), `b` (`"bb"`, blocked by a), `c` (`"bc"`, blocked by b).
Run `_run_board(root, a, b, c, max_concurrent=2)` (two slots, so only the
dependency edges order them).

Must observe:
1. `ok`; levels `[[A], [B], [C]]`; all three entries `done`.
2. Run bases: A `"main"`, B `I(a)`, C `I(b)` — C is on its **immediate**
   blocker, not on `I(a)` and not on `main` (no flattening).
3. Transitive containment: `S(c)` contains `S(a)`, `S(b)`, `I(a)`, `I(b)`;
   `I(c)` contains `I(b)`, which contains `I(a)`.
4. The chain is ordered: `S(b)` does **not** contain `S(c)`, and `S(a)` does
   not contain `S(b)` (the stack points one way only).
5. `rev-parse main == main_before`.

### T3 — relaunch with a `done` blocker whose integrate branch survives

`test_a_relaunch_stacks_a_new_milestone_on_a_done_blockers_integrate_branch` (e2e_fake).

Setup and first run: `a = _milestone(root, "A", "ba")`; `first = _run_board(root, a)`.
Then:
- `first["ok"] is True`; `I(a)` is in `_local_branches(root)`.
- Precondition pin: A's milestone card is finished but not landed —
  `board.show(a["id"]).status == "done"` (the rollup sets it; it must not be
  `merged`). If the rollup leaves it at another non-landed status, the test
  sets it to `done` explicitly through `board.set_status` before continuing,
  so the scenario is exactly D line 45's row.

Second run: `b = _milestone(root, "B", "bb", blocked_by=(a["id"],))`;
`second = _run_board(root, a, b)` — `a` is passed so the helper's
`branch_prefix_of` can name A's prefix, which `board_prefixes(roots=...)`
derives for a non-open blocker root.

Must observe:
1. `second["ok"] is True`; `second["levels"] == [{"level": 0, "milestones": [b["id"]]}]`
   — A dropped out as done, so B is level 0, and only B has an entry.
2. B's run row `base_branch == I(a)` (not `"main"`), although A was not
   dispatched in this run.
3. `S(b)` contains `S(a)` and `I(a)`; `I(b)` contains `I(a)`.
4. A's branches were not rewritten: `rev-parse I(a)` after the second run
   equals its value after the first run.
5. `rev-parse main` is unchanged across both runs.

Out of this test (already unit-pinned in `tests/test_orchestrate.py`): the
sibling rows "done blocker without a local integrate branch → `main`" and
"`merged` blocker → `main`". They may be added here only if they cost no extra
board run; they are not required.

## Error paths

No new error path is introduced. The existing e2e refusal
(`test_a_two_open_blocker_milestone_refuses_the_whole_board_up_front`) covers
`MilestoneBlockersError` and must keep passing untouched.

## Test list and tiers

| Test | Tier | Why this tier |
|---|---|---|
| T1 `test_a_blocked_milestone_stacks_on_its_blockers_integrate_branch` | `e2e_fake` | Needs production wiring end to end (real `brd`, real `git` worktrees/merges, fake `claude` coder commits); unit fakes cannot show commits really landing on a stacked branch. |
| T2 `test_a_three_milestone_chain_stacks_each_link_on_the_one_before` | `e2e_fake` | Same; transitive ancestry across three real Integrates only exists with real git under the production walk. |
| T3 `test_a_relaunch_stacks_a_new_milestone_on_a_done_blockers_integrate_branch` | `e2e_fake` | Two real board runs; the second's base depends on `_local_branch_exists` reading a branch the first run's real Integrate left behind. |
| existing `test_this_module_runs_in_the_default_suite_unmarked` | unit (default) | Unchanged; must still pass, proving no module-level marker was added. |

Verification: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py` green
(new tests included), and `uv run pytest` (default tiers) green with the same
test count as before (the new tests are opt-in).

TDD note: these are characterization tests of shipped behavior, so they are
expected to pass on first run. To prove each one can fail, the implementer
temporarily breaks the wiring (e.g. pass `base_branch` instead of
`bases[card.id]` in `_run_board_async`), observes T1–T3 fail on the base and
ancestry assertions, then reverts. The temporary break is never committed.

## Out of scope

- `am run --board --detach` and everything in D §2 (lines 76-96), including the
  detached `am watch --all` / pause / resume e2e scenario in D line 114-115 —
  sibling cards.
- README changes (D lines 118-119).
- Any production code change in `orchestrate.py`, `dag.py`, `census.py`, `cli.py`.
- Merged-base resolution for multi-blocker milestones (D Non-goals, line 31).
- New fixtures or changes to `tests/e2e/fake_claude.py` / `tests/conftest.py`.
