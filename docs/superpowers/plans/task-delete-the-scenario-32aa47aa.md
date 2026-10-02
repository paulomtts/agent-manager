<!-- task-pipeline: validated -->
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

---

# Delete the scenario twins already covered in test_orchestrate.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove from `tests/e2e/` the fake-claude scenario tests whose behavior a cheaper `FakeDriver` twin in `tests/test_orchestrate.py` already proves, plus the module-local helpers only they used. Every touched module keeps at least one real-subprocess wiring test.

**Architecture:** This is a test-only deletion. Each task covers one e2e module and follows the same cycle. First run the twin(s) to prove the replacement exists and is green. Then run the doomed e2e test once as a baseline, delete it along with its now-orphaned helpers, and run the module under `-m e2e_fake` to show the survivors still pass and the deleted names are no longer collected. No production code under `src/` changes.

**Tech Stack:** Python 3.12, pytest 9 (`--import-mode=importlib`, tier markers `git`/`brd`/`e2e_fake`/`soak`/`e2e`), `uv`.

**Spec:** `docs/superpowers/specs/task-delete-the-scenario-32aa47aa-design.md` (prepended above). Parent authority: `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 V1/V6, §5.

## Global Constraints

- Work in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-delete-the-scenario-32aa47aa` on branch `m15/task-delete-the-scenario-32aa47aa` (cut from `m15/task-delete-the-eight-6956cc95`). Run every command from that directory. All paths below are relative to it.
- Do not add tests. Do not edit `tests/test_orchestrate.py`, `tests/conftest.py`, `tests/e2e/conftest.py`, `src/`, `pyproject.toml`, or any e2e module other than the four named in Tasks 1-4.
- Do not touch the 6956cc95 meta tests (already gone), FakeBoard/`board.py` (V3/V4), V7/V8/V9, pytest-xdist, the pygents engine, the checkpoint format, the harness adapter contract, `dispatch.py`'s `LauncherFn`, the paid `e2e` tier and its 5-test cap, or milestone 14's `am run --board` work.
- §5 invariant: "nothing here proposes deleting a test for being slow without replacing what it proved with a faster equivalent". If a twin check in any task fails (the twin is missing, red, or does not assert what the Twin decisions table says), do not delete that test. Keep it, skip only its deletion step, and add a line to the hand-off gap list (Task 5).
- Tier fact the executor must know. On this branch every `run_milestone` twin in `tests/test_orchestrate.py` carries `@pytest.mark.brd` and `@pytest.mark.git`. It is driven by `FakeDriver` (unit-tier in spirit) but still talks to the real `brd` board until V4's FakeBoard lands. The default `uv run pytest` therefore deselects these twins, so each task runs them explicitly with `-m brd`. Their markers are not changed here (spec: "Their markers are not changed here").
- Verification commands: `uv run pytest -m e2e_fake` (subtask pre-check), then `uv run pytest` (full suite). There is no typecheck or lint.

## Twin decisions (pre-read for the executor; re-confirm in each task)

These decisions come from reading both sides as they stand on this branch. A twin "covers" a test when it asserts the e2e test's outcome, run/story/subtask statuses, and board or comment effects. Some e2e assertions concern a git mechanism inside `bases.build` or `integration.integrate_milestone` that `test_orchestrate.py` deliberately stubs out. Each of those is covered by that mechanism's own default-tier `git` test, named in the third column. Assertions about where the fake `claude` process ran, or what it logged, are the wiring proof. The survivors keep that proof.

| e2e test (delete?) | Primary twin in `tests/test_orchestrate.py` | Mechanism-level cover (default tier) |
|---|---|---|
| `test_parallel_milestone.py::test_an_escalation_in_one_lane_stops_the_other_and_its_dependent_never_starts` (delete) | `test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending` (l.2143): payload with A/a1/review, `stopped` lists B; statuses A escalated, B stopped, b2 pending, C/c1 pending; c1 never driven; Integrate never called | none needed |
| `test_parallel_milestone.py::test_a_story_blocked_by_two_stories_runs_on_their_merged_base` (delete) | `test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it` (l.3153): base built once after both blockers, from both tips; c1 driven on the base; `result["bases"]`; C/c1 done. Also `test_plan_levels_roots_a_two_blocker_story_on_its_merged_base` (l.112) for the plan | `tests/test_bases.py::test_two_clean_tips_merge_into_the_base` (both tips are ancestors of the real base branch, master unmoved) |
| `test_parallel_milestone.py::test_a_failed_blocker_leaves_the_merged_story_pending_with_no_base` (delete) | `test_a_failed_blocker_leaves_the_merged_story_pending_and_builds_no_base` (l.3462): escalation names B/b1; `bases.build` never called; no `bases` key; C/c1 pending | none needed |
| `test_parallel_milestone.py::test_a_base_the_resolver_cannot_finish_escalates_the_story_at_base_and_parks_a_sibling` (delete) | `test_a_failed_base_escalates_the_story_at_base_and_parks_a_running_sibling` (l.3292): level 1, story C, subtask None, failed_phase base; D stopped; c1 pending and never driven | `tests/test_bases.py::test_a_resolver_that_gives_up_fails_the_base` (refusing resolver, base worktree in detail, MERGE_HEAD left in place for a human) |
| `test_integrate.py::test_a_resolver_that_does_not_finish_escalates_and_a_human_finish_lets_the_relaunch_complete` (delete) | `test_a_relaunch_after_an_integrate_escalation_retries_integrate` (l.1582): run escalated at integrate, relaunch drives nothing, retries Integrate, ends done with `integrated`; first run stays escalated | `tests/test_integration.py::test_a_refusing_resolver_escalates_and_leaves_merge_head_in_place` (l.687) and `::test_a_relaunch_after_a_human_finished_the_merge_succeeds_without_dispatch` (l.751) |
| `test_integrate.py::test_relaunching_an_integrated_milestone_moves_no_branch` (delete) | `test_relaunching_a_finished_integrated_milestone_leaves_the_integration_tip` (l.1631): relaunch drives nothing, same `integrated`, integration tip and main unmoved, no resolver (`_no_resolver` fails if one is dispatched) | none needed |
| `test_milestone_run.py::test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line` (delete) | `test_subtasks_run_in_order_each_stacked_on_the_one_before` (l.1164): order, each base is the previous branch, levels, completed, tips, `integrated`, all statuses done; plus `test_a_clean_milestone_is_integrated_before_the_run_is_recorded_done` (l.1453) for the real Integrate | Plan-Hash trailer and checkpoint rows stay proven by survivors `test_production_wiring.py::test_the_implement_commit_carries_a_plan_hash_trailer_review_agrees_with` and `::test_the_run_went_through_the_pygents_walk`. Board `done` for every card after a milestone run stays proven by survivor `test_milestone_run.py::test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it` |
| `test_board_comments.py::test_clean_milestone_run_posts_one_done_per_subtask_and_one_run_end` (delete) | `test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story` (l.5334) and `test_a_clean_run_leaves_one_done_run_end_comment_on_the_milestone` (l.5365): one `am · done` per subtask with key, author and branch line and no `(resumed at`; none on stories; one run-end with `done: N of N`, `integrated:`, `next: git merge`; warnings empty | none needed |
| `test_board_comments.py::test_escalation_then_resume_keeps_escalation_and_appends_resumed_done` (delete) | `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review` (l.5529): escalation kept, then `done` with `(resumed at review)`, two distinct milestone run-ends. Also `test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone` (l.5462): phase line, quoted `reason:` from `unresolved_blockers`, `next: am resume`, nothing on stories or pending cards | none needed |
| `test_board_comments.py::test_cancel_comments_in_progress_subtasks_and_milestone` (delete) | `test_a_cancel_comments_each_parked_subtask_and_the_milestone` (l.5790): a1 keeps only its done, each parked subtask gets one `cancelled` with branch and relaunch lines, stories get none, run-end `cancelled` with `done: 1 of 3`, `parked:`, `next: am run --milestone` | none needed |
| `test_board_comments.py::test_board_down_run_ends_done_with_warnings_and_next_life_flushes` (delete) | `test_a_board_that_is_down_changes_only_warnings_and_a_relaunch_posts_the_rows` (l.5657): run done with usual keys, one warning per unposted row, rows pending; relaunch posts each once (exact key lists), all rows posted | the `brd comment` failure path through a real `brd` subprocess stays proven by survivor `test_board_comments.py::test_the_brd_shim_fails_only_comment_calls_while_down` |
| `test_production_wiring.py::test_run_card_drives_every_phase_under_a_fake_claude_and_the_board_says_done` (**KEEP**) | **None.** `test_orchestrate.py` tests the milestone runner and has no `run_card` scenario. The nearest equivalents are in `tests/test_cli.py` (`test_run_card_drives_the_task_workflow_to_done` l.1300, `test_run_card_really_moves_the_card_on_the_board` l.1318), and both use a canned runner that records no attempts | Gap: no non-subprocess twin asserts that the last attempt of each of the seven agent phases has status `ok`. Per the spec's error path, keep it and report it (Task 5) |

Result: 11 of the 12 cited tests are deleted, plus one orphaned guard (`test_integrate.py::test_no_resolver_mode_is_left_armed_for_later_tests`, whose module no longer arms `FAKE_CLAUDE_RESOLVER`). That makes 12 fewer `e2e_fake` items. `test_production_wiring.py` is not edited.

## Review Focus

- A survivor that silently used a helper or constant removed as "orphaned" would fail with `NameError` only at run time, not at import. Each task's `-m e2e_fake` module run executes every survivor, so this shows up as a failure there.
- A leftover unused import or constant is not caught by any lint (none exists). Each task ends with a grep step that must return no matches for every removed name.
- The default `uv run pytest` deselects both the deleted tests and the `brd`-marked twins, so a green full suite proves nothing about either. Each task therefore runs its twins with `-m brd` (or the default tier for `git`-only cover tests), and Task 5 runs the whole twin set again.
- An `-m e2e_fake` item count that drops by more or less than 12 means something else was removed or a deletion was skipped. Task 5 compares it against the baseline recorded in Task 0.
- A `test_no_*_is_left_armed_for_later_tests` guard must be deleted only when its module no longer arms that mechanism. The `test_parallel_milestone.py` rendezvous guard and the `test_board_comments.py` brd-shim guard stay, because survivors still arm both. The `test_integrate.py` resolver guard goes. Each task has an explicit grep that confirms the arming call is still present (or absent) before the guard is kept (or removed).

---

### Task 0: Baseline

**Files:**
- Read only: `pyproject.toml`, `tests/conftest.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `BASELINE_E2E_FAKE`, the `-m e2e_fake` collected-item count, and a green `-m e2e_fake` run. Task 5 compares against both.

- [ ] **Step 1: Confirm the tier machinery from 6956cc95 is present**

Run: `grep -n 'e2e_fake' pyproject.toml tests/conftest.py`
Expected: pyproject's `markers` has an `e2e_fake:` line, and `addopts` contains `not e2e_fake`. `tests/conftest.py` has `_DIRECTORY_TIERS = {"e2e": "e2e_fake", "steps": "git"}`. If any of these is missing, stop: the branch was not cut from `m15/task-delete-the-eight-6956cc95`.

- [ ] **Step 2: Record the e2e_fake item count**

Run: `uv run pytest -m e2e_fake --collect-only -q | tail -n 1`
Expected: a line like `N/M tests collected (K deselected)`. Write N down as `BASELINE_E2E_FAKE`.

- [ ] **Step 3: Confirm the e2e_fake tier is green before any change**

Run: `uv run pytest -m e2e_fake -q`
Expected: all pass. If anything fails here, stop and report it. A pre-existing failure would hide what this subtask breaks.

---

### Task 1: test_parallel_milestone.py: delete four scenario twins

**Files:**
- Modify: `tests/e2e/test_parallel_milestone.py` (docstring lines 12-17, import line 29, helper lines 79-86, test lines 238-323, block lines 414-575)
- Read only: `tests/test_orchestrate.py:112`, `:2143`, `:3153`, `:3292`, `:3462`; `tests/test_bases.py:217`, `:724`

**Interfaces:**
- Consumes: Task 0's green baseline.
- Produces: a module whose survivors are `test_two_lanes_overlap_in_implement_and_the_milestone_finishes`, `test_one_lane_runs_the_level_s_stories_one_after_the_other`, `test_the_journal_of_a_two_lane_run_is_contiguous_and_rebuilds_the_projection`, `test_a_relaunch_after_the_escalation_finishes_and_skips_done_subtasks`, `test_the_lanes_await_drive_subtask_async_on_the_runs_loop`, and the guard `test_no_rendezvous_is_left_armed_for_later_tests`.

- [ ] **Step 1: Read each twin and check it against the Twin decisions table**

Open `tests/test_orchestrate.py` at lines 112, 2143, 3153, 3292 and 3462, and `tests/test_bases.py` at lines 217 and 724. Each must assert what its row in the Twin decisions table says. If one does not, keep the matching e2e test, skip only its part of Step 4, and note the gap for Task 5.

- [ ] **Step 2: Run the twins and show they pass**

Run:
```bash
uv run pytest -m brd -q \
  "tests/test_orchestrate.py::test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending" \
  "tests/test_orchestrate.py::test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it" \
  "tests/test_orchestrate.py::test_a_failed_base_escalates_the_story_at_base_and_parks_a_running_sibling" \
  "tests/test_orchestrate.py::test_a_failed_blocker_leaves_the_merged_story_pending_and_builds_no_base"
uv run pytest -q \
  "tests/test_orchestrate.py::test_plan_levels_roots_a_two_blocker_story_on_its_merged_base" \
  "tests/test_bases.py::test_two_clean_tips_merge_into_the_base" \
  "tests/test_bases.py::test_a_resolver_that_gives_up_fails_the_base"
```
Expected: `4 passed`, then `3 passed`. If any is red or deselected, treat that twin as missing (Step 1 rule).

- [ ] **Step 3: Run the four doomed e2e tests once as the "before" picture**

Run:
```bash
uv run pytest -m e2e_fake -q \
  "tests/e2e/test_parallel_milestone.py::test_an_escalation_in_one_lane_stops_the_other_and_its_dependent_never_starts" \
  "tests/e2e/test_parallel_milestone.py::test_a_story_blocked_by_two_stories_runs_on_their_merged_base" \
  "tests/e2e/test_parallel_milestone.py::test_a_failed_blocker_leaves_the_merged_story_pending_with_no_base" \
  "tests/e2e/test_parallel_milestone.py::test_a_base_the_resolver_cannot_finish_escalates_the_story_at_base_and_parks_a_sibling"
```
Expected: `4 passed`.

- [ ] **Step 4: Delete, bottom-up so earlier line numbers stay valid**

4a. Delete lines 414-575. That span runs from `SHARED = "shared.txt"` through the two blank lines after the last statement of `test_a_base_the_resolver_cannot_finish_escalates_the_story_at_base_and_parks_a_sibling` (`    assert _git(root, "rev-parse", "main").strip() == main_before`, line 573). It removes `SHARED`, `BASE_LINE`, `A_LINE`, `B_LINE`, `_local_branches`, `_merge_in_progress` and the three merged-base tests. Afterwards the async-lanes test's closing `    )` (old line 411) is followed by exactly two blank lines and then `def test_no_rendezvous_is_left_armed_for_later_tests():`.

4b. Delete lines 238-323. That span runs from `def test_an_escalation_in_one_lane_stops_the_other_and_its_dependent_never_starts(` through the two blank lines after its last statement (`    assert _git(root, "rev-parse", "main").strip() == main_before`, line 321). Afterwards `_launch_with_a1_review_failing`'s closing `    )` is followed by two blank lines and then `def test_a_relaunch_after_the_escalation_finishes_and_skips_done_subtasks(`.

4c. Remove `_subtask_rows`, now unused. Replace:
```python
def _subtask_rows(run: models.Run) -> dict[str, models.SubtaskRun]:
    return {
        subtask.card_id: subtask
        for story in run.stories
        for subtask in story.subtasks
    }


def _run_two_lanes(parallel_board, rendezvous, run_milestone_cli):
```
with:
```python
def _run_two_lanes(parallel_board, rendezvous, run_milestone_cli):
```

4d. Remove the import that only the deleted escalation test used. Delete the line:
```python
from agent_manager.workflow import task as task_workflow
```

4e. Replace the docstring's last paragraph (lines 12-17):
```python
Each test builds its own repo and board. On `parallel_board`, A (a1 -> a2) and
B (b1 -> b2) are independent roots and C (c1) is blocked by A alone: the
lone-blocker fast path. Levels are waves in the report only; C is scheduled by
its blocker A (supervisor-tree T1). On `merged_base_board`, C (c1) is blocked
by both A (a1) and B (b1), so its lane builds a merged base from their tips
before c1 runs (supervisor-tree §5), while D (d1 -> d2 -> d3) runs beside.
"""
```
with:
```python
Each test builds its own repo and board. On `parallel_board`, A (a1 -> a2) and
B (b1 -> b2) are independent roots and C (c1) is blocked by A alone: the
lone-blocker fast path. Levels are waves in the report only; C is scheduled by
its blocker A (supervisor-tree T1). The escalation and merged-base scenarios
are proven through `FakeDriver` in tests/test_orchestrate.py (test-tier V6);
this module keeps the real-subprocess wiring proofs.
"""
```

- [ ] **Step 5: Show the deleted names are gone and no survivor needs a removed name**

Run:
```bash
grep -nE 'test_an_escalation_in_one_lane|test_a_story_blocked_by_two_stories|test_a_failed_blocker_leaves|test_a_base_the_resolver_cannot_finish|_subtask_rows|task_workflow|_local_branches|_merge_in_progress|\bSHARED\b|BASE_LINE|A_LINE|B_LINE|merged_base_board|fake_resolver' tests/e2e/test_parallel_milestone.py
grep -n 'rendezvous.arm' tests/e2e/test_parallel_milestone.py
```
Expected: the first grep prints nothing. The second prints at least one line (`_run_two_lanes` and `_launch_with_a1_review_failing` still arm the rendezvous), so the rendezvous guard stays.

- [ ] **Step 6: Run the module's survivors**

Run: `uv run pytest -m e2e_fake -q tests/e2e/test_parallel_milestone.py`
Expected: `6 passed` (five wiring tests and the rendezvous guard).

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_parallel_milestone.py
git commit -m "test(e2e): drop parallel_milestone scenario twins covered in test_orchestrate.py

V6: the escalation, merged-base, failed-blocker and failed-base scenarios
are proven through FakeDriver in tests/test_orchestrate.py (with the base
git mechanics in tests/test_bases.py); the module keeps its five
real-subprocess wiring tests."
```

---

### Task 2: test_integrate.py: delete two scenario twins and the orphaned resolver guard

**Files:**
- Modify: `tests/e2e/test_integrate.py` (import line 18, test lines 295-391, guard lines 439-445)
- Read only: `tests/test_orchestrate.py:1582`, `:1631`; `tests/test_integration.py:687`, `:751`

**Interfaces:**
- Consumes: Task 0's green baseline.
- Produces: a module whose survivors are `test_stories_that_touch_different_files_integrate_with_no_resolver`, `test_a_same_line_conflict_is_resolved_verified_and_left_on_the_integration_branch`, and `test_a_clean_merge_that_breaks_the_suite_escalates_at_integrate`.

- [ ] **Step 1: Read each twin and check it against the Twin decisions table**

Open `tests/test_orchestrate.py` at lines 1582 and 1631, and `tests/test_integration.py` at lines 687 and 751. Each must assert what its row says. On a mismatch, keep that e2e test, skip only its part of Step 4, and note the gap for Task 5. Keep in mind that if `test_a_resolver_that_does_not_finish_escalates_and_a_human_finish_lets_the_relaunch_complete` is kept, Step 4c must be skipped too, because the module would still arm `FAKE_CLAUDE_RESOLVER`.

- [ ] **Step 2: Run the twins and show they pass**

Run:
```bash
uv run pytest -m brd -q \
  "tests/test_orchestrate.py::test_a_relaunch_after_an_integrate_escalation_retries_integrate" \
  "tests/test_orchestrate.py::test_relaunching_a_finished_integrated_milestone_leaves_the_integration_tip"
uv run pytest -q \
  "tests/test_integration.py::test_a_refusing_resolver_escalates_and_leaves_merge_head_in_place" \
  "tests/test_integration.py::test_a_relaunch_after_a_human_finished_the_merge_succeeds_without_dispatch"
```
Expected: `2 passed`, then `2 passed`.

- [ ] **Step 3: Run the two doomed e2e tests once as the "before" picture**

Run:
```bash
uv run pytest -m e2e_fake -q \
  "tests/e2e/test_integrate.py::test_a_resolver_that_does_not_finish_escalates_and_a_human_finish_lets_the_relaunch_complete" \
  "tests/e2e/test_integrate.py::test_relaunching_an_integrated_milestone_moves_no_branch"
```
Expected: `2 passed`.

- [ ] **Step 4: Delete, bottom-up**

4a. Delete the guard at the end of the file, lines 439-445: the two blank lines after `    _assert_base_untouched(root, main_before)` (the last line of `test_a_clean_merge_that_breaks_the_suite_escalates_at_integrate`, line 438) and the whole of:
```python
def test_no_resolver_mode_is_left_armed_for_later_tests():
    """Review focus: scenario 2 arms `FAKE_CLAUDE_RESOLVER` through the
    function-scoped `monkeypatch`; it must be gone once that test ends, or every
    later resolve in the session would refuse. Kept last in the module."""
    assert "FAKE_CLAUDE_RESOLVER" not in os.environ
```
The file then ends with `    _assert_base_untouched(root, main_before)` and a single trailing newline.

4b. Delete lines 295-391. That span runs from `def test_a_resolver_that_does_not_finish_escalates_and_a_human_finish_lets_the_relaunch_complete(` through the two blank lines after the last statement of `test_relaunching_an_integrated_milestone_moves_no_branch` (`    _assert_base_untouched(root, main_before)`, line 389). Afterwards the same-line-conflict test's final `    _assert_base_untouched(root, main_before)` is followed by two blank lines and then `def test_a_clean_merge_that_breaks_the_suite_escalates_at_integrate(`.

4c. Remove the import that only the guard used. Delete the line:
```python
import os
```

- [ ] **Step 5: Show the deleted names are gone and nothing arms the resolver any more**

Run:
```bash
grep -nE 'test_a_resolver_that_does_not_finish|test_relaunching_an_integrated_milestone|test_no_resolver_mode_is_left_armed|fake_resolver|FAKE_CLAUDE_RESOLVER|\bos\.' tests/e2e/test_integrate.py
```
Expected: no output.

- [ ] **Step 6: Run the module's survivors**

Run: `uv run pytest -m e2e_fake -q tests/e2e/test_integrate.py`
Expected: `3 passed`.

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_integrate.py
git commit -m "test(e2e): drop integrate scenario twins covered in test_orchestrate.py

V6: the integrate-escalation relaunch and the integrated-relaunch scenarios
are proven through FakeDriver in tests/test_orchestrate.py, the refusing
resolver and human finish in tests/test_integration.py. The resolver guard
goes too: nothing left in the module arms FAKE_CLAUDE_RESOLVER."
```

---

### Task 3: test_milestone_run.py: delete the clean stacked-line twin

**Files:**
- Modify: `tests/e2e/test_milestone_run.py` (test lines 69-126)
- Read only: `tests/test_orchestrate.py:1164`, `:1453`

**Interfaces:**
- Consumes: Task 0's green baseline.
- Produces: a module whose survivors are `test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it`, `test_a_pygents_run_killed_in_plan_resumes_without_redispatching_explore_or_spec`, and `test_a_pygents_relaunch_continues_the_parked_card_from_its_checkpoint`.

- [ ] **Step 1: Read each twin and check it against the Twin decisions table**

Open `tests/test_orchestrate.py` at lines 1164 and 1453. Also confirm the survivor cover named in the table is still there: `tests/e2e/test_production_wiring.py:134` (Plan-Hash trailer), `:207` (checkpoint rows), and `tests/e2e/test_milestone_run.py:210-211` (every card `done` on the board). On a mismatch, keep the e2e test and note the gap for Task 5.

- [ ] **Step 2: Run the twins and show they pass**

Run:
```bash
uv run pytest -m brd -q \
  "tests/test_orchestrate.py::test_subtasks_run_in_order_each_stacked_on_the_one_before" \
  "tests/test_orchestrate.py::test_a_clean_milestone_is_integrated_before_the_run_is_recorded_done"
```
Expected: `2 passed`.

- [ ] **Step 3: Run the doomed e2e test once as the "before" picture**

Run: `uv run pytest -m e2e_fake -q "tests/e2e/test_milestone_run.py::test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line"`
Expected: `1 passed`.

- [ ] **Step 4: Delete the test**

Delete lines 69-126. That span runs from `def test_a_clean_three_story_milestone_runs_to_done_on_one_stacked_line(` through the two blank lines after its last statement (`    assert rows > 0, rows`, line 124). Afterwards `_branches`'s `    return _git(root, "branch", "--format=%(refname:short)").split()` is followed by two blank lines and then `def _load_run(root: Path, run_id: str) -> models.Run:`. Remove no helper: `_git`, `_is_ancestor`, `_envelope`, `_all_cards`, `INTEGRATION_BRANCH` and `_branches` are all still used by `test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it`.

- [ ] **Step 5: Show the test is gone and every kept helper is still used**

Run:
```bash
grep -n 'test_a_clean_three_story_milestone' tests/e2e/test_milestone_run.py
grep -cE '_all_cards\(|INTEGRATION_BRANCH|_branches\(|_is_ancestor\(' tests/e2e/test_milestone_run.py
```
Expected: the first prints nothing. The second prints a count of at least 8 (each name has its definition plus at least one use in the review-failure test).

- [ ] **Step 6: Run the module's survivors**

Run: `uv run pytest -m e2e_fake -q tests/e2e/test_milestone_run.py`
Expected: `3 passed`.

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_milestone_run.py
git commit -m "test(e2e): drop the clean stacked-line twin covered in test_orchestrate.py

V6: subtask order, stacking, levels, tips and Integrate are proven through
FakeDriver in tests/test_orchestrate.py; the module keeps the review-failure
relaunch and the two pygents resume wiring tests."
```

---

### Task 4: test_board_comments.py: delete four comment-scenario twins, keep the brd-shim proof

**Files:**
- Modify (full rewrite, since most of the module's helpers become orphaned): `tests/e2e/test_board_comments.py`
- Read only: `tests/test_orchestrate.py:5334`, `:5365`, `:5462`, `:5529`, `:5657`, `:5790`

**Interfaces:**
- Consumes: Task 0's green baseline. Fixtures `milestone_board`, `toolchain`, `fake_claude_bin` from `tests/e2e/conftest.py` (unchanged).
- Produces: a module with `test_the_brd_shim_fails_only_comment_calls_while_down` (the one wiring test) and the guard `test_no_brd_shim_is_left_armed_for_later_tests`, which stays because `brd_shim` still arms the shim.

- [ ] **Step 1: Read each twin and check it against the Twin decisions table**

Open `tests/test_orchestrate.py` at lines 5334, 5365, 5462, 5529, 5657 and 5790. Each must assert what its row says. On a mismatch, keep that e2e test: do not use the Step 4 full-file content, and instead delete only the confirmed tests and their now-unused helpers by hand. Note the gap for Task 5.

- [ ] **Step 2: Run the twins and show they pass**

Run:
```bash
uv run pytest -m brd -q \
  "tests/test_orchestrate.py::test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story" \
  "tests/test_orchestrate.py::test_a_clean_run_leaves_one_done_run_end_comment_on_the_milestone" \
  "tests/test_orchestrate.py::test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone" \
  "tests/test_orchestrate.py::test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review" \
  "tests/test_orchestrate.py::test_a_board_that_is_down_changes_only_warnings_and_a_relaunch_posts_the_rows" \
  "tests/test_orchestrate.py::test_a_cancel_comments_each_parked_subtask_and_the_milestone"
```
Expected: `6 passed`.

- [ ] **Step 3: Run the four doomed e2e tests once as the "before" picture**

Run:
```bash
uv run pytest -m e2e_fake -q \
  "tests/e2e/test_board_comments.py::test_clean_milestone_run_posts_one_done_per_subtask_and_one_run_end" \
  "tests/e2e/test_board_comments.py::test_escalation_then_resume_keeps_escalation_and_appends_resumed_done" \
  "tests/e2e/test_board_comments.py::test_cancel_comments_in_progress_subtasks_and_milestone" \
  "tests/e2e/test_board_comments.py::test_board_down_run_ends_done_with_warnings_and_next_life_flushes"
```
Expected: `4 passed`.

- [ ] **Step 4: Replace the module with its surviving content**

Overwrite `tests/e2e/test_board_comments.py` with exactly this content. The shim script, `BrdShim`, the `brd_shim` fixture, `_on`, the survivor test and the guard are byte-for-byte the current ones. Only the docstring, the imports and the removal of orphaned helpers differ.

```python
"""e2e_fake tier: the `brd` shim behind the board-down scenario (card 649a8e88).

Board-comments design B8 and its §6 "Testing". The outcome-comment scenarios
(clean run, escalation and `am resume`, cancel, a run during which
`brd comment` was down) are proven through `FakeDriver` in
tests/test_orchestrate.py (test-tier V6). What stays here is the
real-subprocess proof that a `brd` first on `PATH` which fails only
`brd comment ...` calls surfaces as `BoardError` through the real `board`
module, against a real temporary board (`milestone_board`), while card reads
and status writes still go through.
"""

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_manager import board

KEY_LINE = "am-key: "
"""How every outcome comment's last line starts (board-comments B5)."""

BRD_FAIL_COMMENTS_ENV = "AM_E2E_BRD_FAIL_COMMENTS"
"""Set (to anything non-empty) and the shim fails every `brd comment ...` call.
Must equal the variable name written into `BRD_SHIM`."""

BRD_SHIM = """#!/bin/sh
# Test scaffolding (card 649a8e88): the board is "down" for comments only.
if [ -n "${AM_E2E_BRD_FAIL_COMMENTS:-}" ] && [ "$1" = "comment" ]; then
    echo "brd shim: the board is down for comments" >&2
    exit 1
fi
exec @REAL_BRD@ "$@"
"""
"""A `brd` that exits 1 with stderr only (what `board._run` turns into
`BoardError`) on `brd comment ...` while the env var is set, and otherwise
`exec`s the real `brd` with the same argv and stdin. Card reads and status
writes always pass, so a run can proceed (B8 makes only comments best-effort)."""


@dataclass
class BrdShim:
    """Switches the `brd` shim between down (comments fail) and up.

    The env var goes through the test's own function-scoped `monkeypatch`,
    so it is undone when the test ends. `am` and `brd` children inherit it:
    `board._run` passes no `env=`.
    """

    path: Path
    monkeypatch: pytest.MonkeyPatch

    def down(self) -> None:
        self.monkeypatch.setenv(BRD_FAIL_COMMENTS_ENV, "1")

    def up(self) -> None:
        self.monkeypatch.delenv(BRD_FAIL_COMMENTS_ENV, raising=False)


@pytest.fixture
def brd_shim(tmp_path, monkeypatch, toolchain, fake_claude_bin) -> BrdShim:
    """The shim, first on `PATH` (ahead of the fake `claude`'s dir too), up.

    The real `brd` is resolved before the shim's dir is on `PATH` and baked
    into the script, so the shim can never call itself. The `PATH` change is
    the test's own `monkeypatch`, undone at teardown.
    """
    real = shutil.which("brd")
    assert real is not None
    bin_dir = tmp_path / "brd-shim"
    bin_dir.mkdir()
    shim = bin_dir / "brd"
    shim.write_text(BRD_SHIM.replace("@REAL_BRD@", shlex.quote(real)), encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    switch = BrdShim(path=shim, monkeypatch=monkeypatch)
    switch.up()
    return switch


def _on(root: Path, card_id: str) -> list[board.BoardComment]:
    """`brd comment list <card>`, oldest first, through the real board module."""
    return board.comment_list(card_id, repo_dir=root)


def test_the_brd_shim_fails_only_comment_calls_while_down(milestone_board, brd_shim):
    """Scaffolding check: while down, `brd comment list/add` fail the way a
    dead board does (`BoardError`) and nothing is posted; card reads and a
    status write still go through; once up, comments work again."""
    root = milestone_board["root"]
    a1 = milestone_board["subtasks"]["A"][0]
    assert Path(shutil.which("brd")) == brd_shim.path

    brd_shim.down()
    with pytest.raises(board.BoardError):
        board.comment_list(a1, repo_dir=root)
    with pytest.raises(board.BoardError):
        board.comment_add(a1, f"shim check\n{KEY_LINE}shim/{a1}/check", repo_dir=root)
    status = board.show(a1, repo_dir=root).status
    assert board.set_status(a1, status, repo_dir=root).status == status
    assert board.tree(milestone_board["milestone"], repo_dir=root).id == milestone_board["milestone"]

    brd_shim.up()
    assert _on(root, a1) == []  # the add while down never reached the board
    assert os.environ.get(BRD_FAIL_COMMENTS_ENV) is None


def test_no_brd_shim_is_left_armed_for_later_tests():
    """Review Focus 2: the shim's env var and `PATH` entry are the test's own
    `monkeypatch`, so both are gone once its test ends. Kept last in the module."""
    assert os.environ.get(BRD_FAIL_COMMENTS_ENV) is None
    found = shutil.which("brd")
    assert found is None or Path(found).parent.name != "brd-shim", found
```

- [ ] **Step 5: Show the deleted names are gone and every kept name is used**

Run:
```bash
grep -nE 'test_clean_milestone_run|test_escalation_then_resume|test_cancel_comments|test_board_down_run|_resume\(|_hold\(|_control_while_held|_signal_when_applied|_in_background|_latest_run_id|_run_status|_attempt_of|_outbox|_comment_warnings|_assert_shape|_assert_scoped|_key_of|_subtasks\(|_all_cards|_envelope|RESUMED_LINE|HELD_PHASE|\bWAIT\b|\bVERIFY\b|INTEGRATION_BRANCH|CliRunner|\bcomments\.|\bcontrol\.|\bstore\.|launcher|threading|\bjson\b|\bre\.' tests/e2e/test_board_comments.py
grep -cE 'KEY_LINE|_on\(|brd_shim\.(down|up)' tests/e2e/test_board_comments.py
```
Expected: the first grep prints nothing. The second prints a count of at least 5.

- [ ] **Step 6: Run the module's survivors**

Run: `uv run pytest -m e2e_fake -q tests/e2e/test_board_comments.py`
Expected: `2 passed` (the shim wiring test and the guard).

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_board_comments.py
git commit -m "test(e2e): drop board-comment scenario twins covered in test_orchestrate.py

V6: the clean-run, escalation-and-resume, cancel and board-down comment
scenarios are proven through FakeDriver in tests/test_orchestrate.py; the
module keeps the real-subprocess brd-shim proof and its guard, and loses
the helpers only the deleted scenarios used."
```

---

### Task 5: Full verification and hand-off

**Files:**
- Read only: `tests/e2e/test_production_wiring.py` (not modified; see the Twin decisions table)

**Interfaces:**
- Consumes: `BASELINE_E2E_FAKE` from Task 0, and Tasks 1-4 committed.
- Produces: the hand-off summary, including the gap list.

- [ ] **Step 1: Confirm test_production_wiring.py is untouched and its kept test still runs**

Run:
```bash
git diff --stat m15/task-delete-the-eight-6956cc95 -- tests/e2e/test_production_wiring.py
uv run pytest -m e2e_fake -q "tests/e2e/test_production_wiring.py::test_run_card_drives_every_phase_under_a_fake_claude_and_the_board_says_done"
```
Expected: an empty diff stat, then `1 passed`.

- [ ] **Step 2: Confirm only the four intended files changed**

Run: `git diff --stat m15/task-delete-the-eight-6956cc95 -- . ':!docs'`
Expected: exactly `tests/e2e/test_parallel_milestone.py`, `tests/e2e/test_integrate.py`, `tests/e2e/test_milestone_run.py` and `tests/e2e/test_board_comments.py`, all with deletions (plus the docstring and imports edits). No other path.

- [ ] **Step 3: Check the e2e_fake item count dropped by exactly 12**

Run: `uv run pytest -m e2e_fake --collect-only -q | tail -n 1`
Expected: the collected count equals `BASELINE_E2E_FAKE - 12` (4 + 3 + 1 + 4). Subtract 1 from that 12 for every test a twin check kept. Any other number means something extra was removed or a deletion was missed. Find which before going on.

- [ ] **Step 4: Run the whole twin set once more**

Run:
```bash
uv run pytest -m brd -q \
  "tests/test_orchestrate.py::test_an_escalation_parks_the_other_lane_and_its_dependent_stays_pending" \
  "tests/test_orchestrate.py::test_a_merged_root_story_builds_its_base_after_both_blockers_and_runs_on_it" \
  "tests/test_orchestrate.py::test_a_failed_base_escalates_the_story_at_base_and_parks_a_running_sibling" \
  "tests/test_orchestrate.py::test_a_failed_blocker_leaves_the_merged_story_pending_and_builds_no_base" \
  "tests/test_orchestrate.py::test_a_relaunch_after_an_integrate_escalation_retries_integrate" \
  "tests/test_orchestrate.py::test_relaunching_a_finished_integrated_milestone_leaves_the_integration_tip" \
  "tests/test_orchestrate.py::test_subtasks_run_in_order_each_stacked_on_the_one_before" \
  "tests/test_orchestrate.py::test_a_clean_milestone_is_integrated_before_the_run_is_recorded_done" \
  "tests/test_orchestrate.py::test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story" \
  "tests/test_orchestrate.py::test_a_clean_run_leaves_one_done_run_end_comment_on_the_milestone" \
  "tests/test_orchestrate.py::test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone" \
  "tests/test_orchestrate.py::test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review" \
  "tests/test_orchestrate.py::test_a_board_that_is_down_changes_only_warnings_and_a_relaunch_posts_the_rows" \
  "tests/test_orchestrate.py::test_a_cancel_comments_each_parked_subtask_and_the_milestone"
```
Expected: `14 passed`.

- [ ] **Step 5: Subtask pre-check (spec Verification 1)**

Run: `uv run pytest -m e2e_fake`
Expected: all pass, 0 failed, 0 errors.

- [ ] **Step 6: Full suite (spec Verification 2)**

Run: `uv run pytest`
Expected: all pass, 0 failed, 0 errors.

- [ ] **Step 7: Write the hand-off summary (in the final message, not a file)**

Include:
- Deleted (11 tests plus 1 guard), by module, with the twin each relied on, taken from the Twin decisions table and adjusted for any keep decided in Steps 1 of Tasks 1-4.
- Kept with a gap: `tests/e2e/test_production_wiring.py::test_run_card_drives_every_phase_under_a_fake_claude_and_the_board_says_done`. Reason: `tests/test_orchestrate.py` has no `run_card` scenario. Missing assertion: no non-subprocess test asserts that the last attempt of each of the seven agent phases (`explore`, `spec`, `validate_spec`, `plan`, `validate_plan`, `implement`, `review`) is recorded with status `ok`. The board-`done` half is covered by `tests/test_cli.py::test_run_card_really_moves_the_card_on_the_board`. Add any other test a twin check kept in Tasks 1-4, with its missing assertion.
- Tier note for the parent story. The kept `run_milestone` twins in `tests/test_orchestrate.py` are still `brd`+`git` (opt-in `brd` tier), not the `unit` tier the spec states, until V4's FakeBoard lands. Until then the default `uv run pytest` runs neither the deleted e2e tests nor their twins.
- Shared conftest fixtures in `tests/e2e/conftest.py` are not touched by this subtask (spec: "Shared ones stay"). `merged_base_board` is still used by `tests/e2e/test_milestone_resume.py`. `fake_resolver` (and its `FakeResolver` class) has no remaining user after this subtask: its only users were the two deleted tests `test_parallel_milestone.py::test_a_base_the_resolver_cannot_finish_...` and `test_integrate.py::test_a_resolver_that_does_not_finish_...`. Report it as a candidate for removal by whichever story owns `tests/e2e/conftest.py`. Confirm with `grep -rn 'fake_resolver' tests/e2e/ --include='test_*.py'` (expected: no output).

- [ ] **Step 8: Commit check**

Run: `git status --porcelain`
Expected: no output (everything was committed in Tasks 1-4; this task only verifies).
