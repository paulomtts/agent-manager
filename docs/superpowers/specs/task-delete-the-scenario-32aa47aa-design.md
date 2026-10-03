# Delete the scenario twins already covered in test_orchestrate.py (card 32aa47aa)

Parent story: e3555d8c "Retier tests/e2e/* and remove its duplicate twins". Blocked by 6956cc95 (delete the eight meta tests). Authority: `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 V6 (content of the `e2e_fake` tier), V1 (tier definitions), §5 (no deletion without an equivalent).

## Base

Work on top of the 6956cc95 work (branch `m15/task-delete-the-eight-6956cc95` or wherever it has landed), not bare master. That branch carries the `e2e_fake` marker, the `addopts` default-deselection and the `tests/e2e/` directory auto-mark; without them the `-m e2e_fake` check below is meaningless. This worktree already has them (pyproject.toml declares `e2e_fake`; `tests/conftest.py` maps `e2e` -> `e2e_fake`).

## Scope

Delete twelve tests under `tests/e2e/`, each a fake-claude, real-subprocess duplicate of a scenario `tests/test_orchestrate.py` already proves through `FakeDriver` (class at `tests/test_orchestrate.py:775`). V6's line numbers predate 6956cc95's meta-test deletion, so in this worktree some have moved. Go by test name, not by line number:

| V6 cite | Test to delete | Line in this worktree |
|---|---|---|
| `test_parallel_milestone.py:245` | `test_an_escalation_in_one_lane_stops_the_other_and_its_dependent_never_starts` | 238 |
| `test_parallel_milestone.py:441` | `test_a_story_blocked_by_two_stories_runs_on_their_merged_base` | 434 |
| `test_parallel_milestone.py:477` | `test_a_failed_blocker_leaves_the_merged_story_pending_with_no_base` | 470 |
| `test_parallel_milestone.py:513` | `test_a_base_the_resolver_cannot_finish_escalates_the_story_at_base_and_parks_a_sibling` | 506 |
| `test_integrate.py:302` | `test_a_resolver_that_does_not_finish_escalates_and_a_human_finish_lets_the_relaunch_complete` | 295 |
| `test_integrate.py:360` | `test_relaunching_an_integrated_milestone_moves_no_branch` | 353 |
| `test_milestone_run.py:69` | `test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line` | 69 |
| `test_board_comments.py:290` | `test_clean_milestone_run_posts_one_done_per_subtask_and_one_run_end` | 290 |
| `test_board_comments.py:337` | `test_escalation_then_resume_keeps_escalation_and_appends_resumed_done` | 330 |
| `test_board_comments.py:438` | `test_cancel_comments_in_progress_subtasks_and_milestone` | 431 |
| `test_board_comments.py:543` | `test_board_down_run_ends_done_with_warnings_and_next_life_flushes` | 536 |
| `test_production_wiring.py:41` | `test_run_card_drives_every_phase_under_a_fake_claude_and_the_board_says_done` | 41 |

Also remove any module-local helper, fixture or constant that only the deleted tests used. Shared ones stay. For example, `_launch_with_a1_review_failing` stays because `test_a_relaunch_after_the_escalation_finishes_and_skips_done_subtasks` still uses it, and the `completed_run`/`agent_attempts` fixtures stay because the rest of test_production_wiring.py still uses them. A `test_no_*_is_left_armed_for_later_tests` guard stays as long as its module still arms that mechanism.

Out of scope: no new tests, and no edits to `test_orchestrate.py` other than reading it. Do not build `FakeBoard` or touch `board.py` (that is V3/V4). Leave the meta tests to 6956cc95, and leave V7, V8 and V9 alone. The same goes for pytest-xdist, the pygents engine, the checkpoint format, the harness adapter contract, `dispatch.py`'s `LauncherFn`, the paid `e2e` tier and its 5-test cap, and milestone 14's `am run --board` work.

## Required behavior

- **Confirm the twin before each deletion (§5 invariant).** Read the matching `test_orchestrate.py` scenario as it is now and check that it asserts the same behavior: outcome, statuses, and board or comment effects where the e2e test asserted them. Likely twins, as starting points only:
  - escalation parks the sibling: `test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending`
  - merged base: `test_plan_levels_roots_a_two_blocker_story_on_its_merged_base`
  - failed blocker: `test_a_failed_blocker_leaves_the_merged_story_pending_and_builds_no_base`
  - base escalation: `test_a_failed_base_escalates_the_story_at_base_and_parks_a_running_sibling`
  - integrate escalation and relaunch: `test_a_relaunch_after_an_integrate_escalation_retries_integrate`
  - relaunch of an integrated milestone: `test_relaunching_a_finished_integrated_milestone_leaves_the_integration_tip`
  - stacked line: `test_subtasks_run_in_order_each_stacked_on_the_one_before`
  - resumed comments: `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review`
  - cancel comments: `test_a_cancel_comments_each_parked_subtask_and_the_milestone`
  - board down: `test_a_board_that_is_down_changes_only_warnings_and_a_relaunch_posts_the_rows`
  - the clean-run comment twin and the production_wiring `:41` twin (the card reads `done` on the board, every agent phase `ok`) must be located the same way.
- **Error path, no genuine twin.** If a cited test has no equivalent in `test_orchestrate.py`, or the twin misses an assertion the e2e test makes, do not delete that test. Keep it and record the gap (test name and the missing assertion) in the hand-off summary. Writing the missing unit test is not this subtask's job.
- **No module is emptied.** Every touched module keeps at least one real-subprocess wiring test. The expected survivors:

| Module | Remaining wiring tests |
|---|---|
| test_parallel_milestone.py | `test_two_lanes_overlap_in_implement_and_the_milestone_finishes`, `test_one_lane_runs_the_level_s_stories_one_after_the_other`, the journal test, the relaunch test, the async-lanes test |
| test_integrate.py | the no-resolver, same-line-conflict and suite-breaks tests |
| test_milestone_run.py | the review-failure relaunch test and the two pygents resume tests |
| test_board_comments.py | `test_the_brd_shim_fails_only_comment_calls_while_down` |
| test_production_wiring.py | the eleven tests that share `completed_run` and the critic-loop tests |

  test_board_comments.py is the thinnest. If a twin check keeps one of its four scenario tests, that also helps satisfy this rule.
- Other e2e modules and every non-e2e test file stay unchanged.

## Tests

No tests are added, so the tier placement is a statement about what is left:

- **Deleted tests:** `e2e_fake` tier (auto-marked under `tests/e2e/`). Per V1 and V6 they are duplicates of what the `unit` tier already owns.
- **Kept twins in `tests/test_orchestrate.py`:** `unit` tier (default, driven through `FakeDriver`). They stay as the authoritative scenario coverage. Their markers are not changed here.
- **Surviving tests in the five touched modules:** stay `e2e_fake` (opt-in), one wiring proof per scenario family.

## Verification

1. Pre-check for this subtask: `uv run pytest -m e2e_fake`. It must pass, which shows the survivors don't depend on fixture state or helpers that only the deleted tests set up.
2. Full suite: `uv run pytest`.

There is no typecheck or lint command.
