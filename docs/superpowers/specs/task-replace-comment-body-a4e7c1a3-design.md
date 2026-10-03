# Replace comment-body assertions with key/state assertions (a4e7c1a3)

Parent: 8460355c "Move test_orchestrate.py off the real board". Milestone design: `docs/superpowers/specs/2026-10-02-test-tier-design.md`, Decision V4: "Comment-body assertions become outbox key/state assertions; the exact body text is `test_comments.py`'s job alone." This subtask is that sentence and nothing else. It is blocked by sibling b9c19b33, which landed `FakeBoard`/`run_brd` and converted the `project`/`_milestone` fixtures; this subtask builds on that worktree state.

## Scope

Only `tests/test_orchestrate.py`, the "board comments" section (helpers `_comments` :5336, `_keys` :5341, `_comment_states` :5349, tests :5361-6057). No production code. `tests/test_comments.py` stays unchanged and remains the only place that pins literal body text. Fixtures, `tests/e2e/*` (V6), `tests/test_cli.py` (V5), and tier markers/skip hooks (V2) are out of scope.

## Rule for each body assertion

Classify each `comment.body` / `body` assertion in that section as one of the following.

1. **Template text that has a golden twin: remove it or convert it.** This covers the header line `am · <kind> · run {run_id}` and fixed template wording that is fully determined by the kind and run/milestone id. That wording is `next: \`am resume …\``, `next: \`am run --milestone …\``, `next: \`git merge …\``, `relaunch: \`am run --milestone …\``, and `phase: <p>` where the phase already appears in the key or the result. The golden twins are `test_escalated_with_a_reason_golden_body` (:116), `test_done_fresh_golden_body`/`test_done_resumed_golden_body` (:239/:258), `test_cancelled_golden_body` (:306), `test_base_failed_golden_body_goes_on_the_story` (:327), `test_run_end_golden_bodies` [done/escalated/paused/cancelled] (:437), `test_run_end_names_the_story_an_integrate_conflict_stopped_at` (:467) and `test_run_end_of_a_cancel_that_escalated_names_the_escalated_card` (:482). Replace the removed assertion with the following:
   - For per-card comments, the key already encodes the kind (`/done`, `/cancelled`, `/base-failed`, `/escalated:<tok>`). Rely on the existing `_keys(...)` assertion and add `(key, "posted")` membership in `_comment_states(project)` wherever the test does not already assert state.
   - For milestone run-ends, the key (`{run_id}/{milestone}/run-end:<tok>`) does not encode the kind. The kind is proven by the run's result flag (`result["done"|"escalated"|"cancelled"|"paused"]`), which the test already asserts, together with the run-end key and a `"posted"` state. First check that `orchestrate.py:1801` composes the run-end from that same payload. If it does not, keep the header-line assertion and say so in the PR.
   - The two cross-life header-line lists (:5580-5583 and :6050-6053) become run-end key assertions plus the first and second life's result flags. Each list must have 2 distinct keys, all prefixed `{run_id}/{milestone}/run-end:`, in board order, each `posted`.
2. **Value computed by orchestration: keep it.** No golden test can pin these values because they come from the run itself. Keep each one as a narrow substring assertion and drop only its template prefix if that adds nothing. They are:
   - the subtask's real branch (`branch: {_branch(...)}`, :5380, :5851, :5936)
   - `done: N of M`, where `total` reaches the comment only (:5409, :5861, :6024)
   - the real integration branch (:5410)
   - the parked list and its order (:5468, :5862, :5903, :6025)
   - the escalated card and phase (:5467, :5902)
   - the integrate-failure story and detail (:5435)
   - the driver's `detail:` passthrough (:5522, :5549-5550, :5607, :5633)
   - the `reason:` extracted from real phase results, with brackets broken, and its absence when there are no results (:5523, :5551)
   - `(resumed at review)` derived from the checkpoint (:5578, :5381 negative)
   - `base branch: {root_plan.branch}` (:5606, :5632)
   - `stopped before: implement` and its absence for a queued lane (:5850, :5935, :5939)

If you are unsure about an assertion, keep it. Coverage must not shrink, per spec §5: "Every retiered test keeps its assertions; nothing is deleted without … a faster equivalent already existing."

## Observable behavior and error paths

There is no runtime behavior change. Every test in the section keeps its name and scenario, and passes and fails exactly as before. Error-path tests that already assert by state, such as `test_a_cancel_whose_comments_the_board_refuses_is_still_cancelled_with_warnings` (:5975, `["pending","pending"]`), stay unchanged. The proof method from V4 §6 is to run the board-comments tests before and after with the same `-k` filters and diff the pass/fail outcomes, which must be identical.

## Tests

All touched tests stay in the `git` tier, per V1 and V4. They use real git in tmp_path, drive the board through the injected `FakeBoard`, and make no brd/claude subprocess calls. Their existing `requires_git`/`requires_brd` markers are left unchanged; V2 owns those. No new test files and no moves.

- `test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story` (git)
- `test_a_clean_run_leaves_one_done_run_end_comment_on_the_milestone` (git)
- `test_an_integrate_escalation_leaves_an_integrate_failed_run_end_comment` (git)
- `test_a_lane_escalation_leaves_an_escalated_run_end_comment_naming_the_parked` (git)
- `test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone` (git)
- `test_a_second_life_escalating_at_the_same_phase_adds_a_second_escalation_comment` (git; only the `reason:`/`detail:` checks, which are kept per rule 2)
- `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review` (git)
- `test_a_failed_merged_base_comments_on_its_story` (git)
- `test_a_subtask_less_storys_failed_base_comments_on_that_story` (git; kept per rule 2, add state)
- The cancel test at :5819 (the test whose docstring is "Spec test 1: a2 and b1 park under the cancel…") (git)
- `test_a_cancel_with_an_escalated_lane_comments_only_the_parked_subtask_as_cancelled` (git)
- `test_a_cancel_on_a_lane_waiting_for_a_slot_comments_its_subtask_without_a_phase` (git)
- `test_a_cancel_while_a_base_builds_comments_no_story_and_still_ends_the_run` (git)
- `test_a_pause_leaves_exactly_one_paused_run_end_on_the_milestone` (git)
- `test_a_paused_then_resumed_run_leaves_a_paused_then_a_done_run_end` (git)
- `tests/test_comments.py` golden-body tests listed above (unit): unchanged, and they are the sole owners of the literal body text.

## Verification

{"fullSuite": ["uv run pytest"], "typecheck": "", "lint": [], "verificationSource": "CLAUDE.md, .github/workflows/publish.yml, pyproject.toml (all from origin/master)"}
