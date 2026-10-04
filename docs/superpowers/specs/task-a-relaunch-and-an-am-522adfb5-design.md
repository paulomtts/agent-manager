# A relaunch and an `am resume` both see a reset run correctly — subtask design

Card 522adfb5 (story 816cb8b3 "am reset closes a run nobody is driving", milestone 246a77c3). Narrows `docs/superpowers/specs/2026-10-03-am-reset-design.md` §3.6, §3.7 and §4 tests 8, 9, 12 to this subtask.

## Scope

This subtask adds proof and documentation only. The `am reset RUN_ID` command, its refusals, `took_over`, `already_cancelled`, `cards`/`open_in` and the `DeadRunError` message pointer already landed with siblings 736d6728 and af52db54, and they are present on this worktree's branch (`src/agent_manager/cli.py:2349` `reset_run`, `cli.py:2441` `@app.command("reset")`, `cli.py:2046-2049` the cancelled-run refusal in `resume_run`, `cli.py:2167-2173` the dead-run message). Do not re-implement or reshape any of that.

No production code change is expected. Relaunch already resolves `runs.continuable_checkpoint(store, subtask.id)` (`orchestrate.py:1253-1255`), which goes through `Store.latest_open_checkpoint`'s cancelled-run exclusion, and passes `resume_from` only when the result is non-None. `worktree.ensure` already recreates a removed worktree and re-cuts a deleted branch. If a test shows one of these does not hold, stop and report it. Do not patch around it here.

Out of scope, carried from the story: `am reset` writing any git or brd state; re-ensuring the worktree on resume (that is the separate `2026-10-03-resume-worktree-reensure-design.md`); journal/DB divergence detection. The project-wide "never" rules still hold: no raw SQL against the projection from a command, no write outside `_fenced()` once a token is bound, no push, no `main`/`master`.

## Observable behaviour to prove (§3.6)

- **Relaunch after reset.** A new `am run --milestone` dispatches a card whose newest checkpoint belongs to a reset (now `cancelled`) run with no `resume_from`. The card's walk starts at the `worktree` phase. This holds whether the checkpoint's worktree directory still exists or was removed by hand.
- **Resume of the reset run.** `am resume <reset-run-id>` raises `NotResumableError` with exactly `run <id> was cancelled; start new work with \`am run --milestone\``, which is the same wording an `am cancel`led run gets. It exits 3 for both `task` and `milestone` workflows and writes nothing, because the check precedes `Store.open`.

## Documentation (§3.7), README.md

Write this from the command and envelope fields as they actually landed on this branch, not from the spec's plan.

- In "Pausing and cancelling a run", after the Ctrl-C/pause/cancel list, add one paragraph introducing `am reset <run-id>`. It covers these points:
  - It is for a run nobody is driving: one that crashed, or one that is terminal and was torn down by hand.
  - It records the run `cancelled` exactly as a cancel would, so everything the section says about a cancelled run applies.
  - It writes no git and touches no card.
  - A repeat reports `already_cancelled`.
  - It has three refusals: an unknown run, a live run (with a pointer to `am cancel`), and a `done` run.
  - It reports the cards it checkpointed in `cards`, with `open_in` naming the run a relaunch would still adopt from.
- The `DeadRunError` bullet (currently README.md:400) gains the `am reset` pointer that the landed message already carries.
- In "Relaunching resumes", the sentence "A relaunch after an `am cancel` ignores the cancelled run's checkpoints…" (README.md:322) gains "or an `am reset`".
- The watch section's `run_upsert` row needs no change.

## Tests

Tier placement follows CLAUDE.md's rule, restated in design spec §14 (`2026-09-23-agent-manager-design.md`): a test's tier is chosen by what it actually spawns or touches, not by its directory, and a test is marked explicitly when the directory default does not fit.

1. **Relaunch after reset starts fresh (spec test 8, main body).** Tier: `unit`, unmarked. It runs through `orchestrate` with `FakeDriver` and the store, with no subprocess. Model it on `tests/test_orchestrate.py:3678` (`test_a_pygents_relaunch_continues_a_matching_open_checkpoint_and_starts_the_rest_fresh`) and place it in `tests/test_orchestrate.py`.
   - Setup: give a card an open checkpoint under run X, then reset X through `cli.reset_run`/the command, not a hand-written status.
   - Assertions: the relaunch dispatches the card with no `resume_from` key, and its walk begins at `worktree`.
   - Parametrize it twice: the checkpoint's worktree directory present, and absent.
2. **Git companion: `worktree.ensure` recreates and re-cuts (spec test 8, git proof).** Tier: `git`. It spawns real `git` in `tmp_path`.
   - Both cases already exist in `tests/steps/test_worktree.py`:
     - `test_an_rm_rf_worktree_whose_branch_survives_is_re_added_with_its_commits` (:990) covers a surviving branch whose worktree was removed.
     - `test_an_rm_rf_worktree_whose_branch_is_also_gone_is_re_cut_from_base` (:1032) covers a deleted branch.
   - Both currently rely on the directory auto-mark. Satisfy the companion by adding an explicit `@pytest.mark.git` to these two tests rather than duplicating them.
   - Add a new test only if the implementer finds a §3.6 case they do not cover.
3. **`am resume` of a reset run refuses (spec test 9).** Tier: `unit`, unmarked. It uses the `projection` fixture with `brd`/`git`/`claude` stubbed off `PATH`. Place it in `tests/test_cli.py`.
   - Parametrize it over `workflow` ∈ {`task`, `milestone`}.
   - Setup: plant a resettable run and reset it with `am reset`.
   - Assertions: `am resume` gives exit 3, envelope `{"type": "NotResumableError", "message": "run <id> was cancelled; start new work with \`am run --milestone\`"}`, and `_resume_guard_state` is unchanged.
   - Reuse the shape of `test_resume_writes_nothing_when_it_refuses` (:4789) and `test_resume_refuses_a_cancelled_run_and_writes_nothing` (:6732), including `_forbid_resume`.
4. **The 2026-10-03 incident, replayed (spec test 12).** Tier: `e2e_fake`, marked explicitly with `@pytest.mark.e2e_fake`. It uses production wiring and spawns the fake `claude` per phase.
   - Placement: under `tests/e2e/`. `test_production_wiring.py` was suggested. The closest existing analogue is `tests/e2e/test_live_control.py:322` (cancel, then relaunch from scratch), and putting the test beside it reuses its hold/pause helpers and fixtures. The plan stage picks one module and states why.
   - Steps:
     1. Pause a milestone mid-subtask under the fake `claude`.
     2. Remove that subtask's worktree directory by hand.
     3. Run `am reset <run-id>` and assert exit 0 and the envelope.
     4. Run a fresh `am run --milestone`.
   - Assertions:
     - The card is driven from `worktree` to `done`.
     - No phase is continued from the reset run's checkpoint.
     - `am resume` of the reset run refuses.

Verification: `uv run pytest` covers tests 1–3, and `uv run pytest -m e2e_fake` covers test 4.

Note: the exploration summary handed to this stage was truncated at 8000 of 9257 characters, mid "File:line ref" section, which means that stage over-ran its brief. The references above were re-checked directly against this worktree rather than taken from the missing text.
