<!-- task-pipeline: validated -->
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

---

# Replace comment-body assertions with key/state assertions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the board-comments section of `tests/test_orchestrate.py`, replace every assertion that pins golden-twinned comment template text (header lines, `next:`/`relaunch:` lines) with outbox key/state assertions, and keep every orchestration-computed value as a narrow substring check, so that pass/fail outcomes are unchanged.

**Architecture:** This is a test-only refactor inside one file. Per-card comments are proven by their kind-encoding key plus a `"posted"` row in `_comment_states(project)`. Milestone run-ends are proven by the run-end key, a `"posted"` row, and the run's result flag. That flag is valid evidence of the header kind because `orchestrate.py:1801-1806` passes the same payload dict (plus `total`) to `comments.compose_run_end`, which picks its header kind from those flags (`comments.py:312`). The V4 §6 proof is a before/after diff of the pass/fail outcomes from one `-k` filter.

**Tech Stack:** Python, pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-replace-comment-body-a4e7c1a3-design.md` (prepended verbatim above).

## Global Constraints

- Work in worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-replace-comment-body-a4e7c1a3`, on branch `m15/task-replace-comment-body-a4e7c1a3`, which was cut from `m15/task-convert-the-project-b9c19b33`. Do not assume any other subtask's code exists.
- Only `tests/test_orchestrate.py` changes, and only the "board comments" section (helpers :5336-5358, tests :5361-6057). No production code. Do not change `tests/test_comments.py`, the fixtures, `tests/e2e/*`, `tests/test_cli.py`, or the `requires_git`/`requires_brd` markers.
- Every touched test stays in the `git` tier, in the existing flat `tests/test_orchestrate.py`. No new test files and no moves.
- If you are unsure about an assertion, keep it. Coverage must not shrink.
- Every test keeps its name and scenario. The pass/fail outcomes for `-k "comment or run_end or resumed_at_review"` must match before and after.
- Verification: `uv run pytest`. There is no typecheck or lint command.

## Review Focus

1. **The before/after diff passes trivially because the tests were skipped.** `requires_brd` skips the whole section when `brd` is not on PATH. The diff only proves something if the section's tests show up as `PASSED`. Task 1 Step 2 checks this.
2. **The result flag stops matching the run-end header.** This happens if `orchestrate.py` ever composes the run-end from a dict other than the one it returns. Task 1 Step 3 reads `orchestrate.py:1793-1808` and stops the plan if the payload is different.
3. **A deleted body assertion has no golden twin, so coverage is lost.** Task 5 Step 1 greps the section for any leftover header/`next:`/`relaunch:` text. Task 5 Step 2 checks that every golden twin named in the spec still exists in `tests/test_comments.py`.
4. **Cross-life runs come back in the wrong order.** The old header lists pinned escalated-then-done and paused-then-done. Tasks 3 and 4 replace them with an ordered equality between the outbox's run-end rows and the board's key order, plus each life's result flag.
5. **A new state assertion that can never fail.** Each conversion task has a liveness step: flip one new `"posted"` expectation to `"pending"`, watch the test fail, then revert.

---

### Task 1: Record the baseline and confirm the run-end payload

**Files:**
- Read: `src/agent_manager/orchestrate.py:1793-1808`
- Read: `src/agent_manager/comments.py:298-315`
- Test: `tests/test_orchestrate.py` (no edits in this task)

**Interfaces:**
- Consumes: nothing.
- Produces: `${TMPDIR:-/tmp}/a4e7c1a3-before.txt`, the sorted pass/fail lines that Task 5 diffs against.

- [ ] **Step 1: Capture the baseline outcomes**

Run from the worktree root:

```bash
uv run pytest tests/test_orchestrate.py -k "comment or run_end or resumed_at_review" -rA -q 2>&1 \
  | grep -E '^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) ' | sort > "${TMPDIR:-/tmp}/a4e7c1a3-before.txt"
cat "${TMPDIR:-/tmp}/a4e7c1a3-before.txt"
```

Expected: one `PASSED` line for each of the 15 tests listed in the spec's Tests section, among other matches.

- [ ] **Step 2: Confirm the section actually ran (Review Focus 1)**

```bash
grep -c '^SKIPPED' "${TMPDIR:-/tmp}/a4e7c1a3-before.txt"
grep -E 'test_a_paused_then_resumed_run_leaves_a_paused_then_a_done_run_end|test_a_clean_run_leaves_one_done_run_end_comment_on_the_milestone' "${TMPDIR:-/tmp}/a4e7c1a3-before.txt"
```

Expected: the first command prints `0`, and the second prints two `PASSED` lines. If anything is `SKIPPED`, `brd` or `git` is missing from PATH. Stop and report this instead of continuing, because a diff of two skip lists proves nothing.

- [ ] **Step 3: Confirm the run-end uses the returned payload (Review Focus 2)**

Read `src/agent_manager/orchestrate.py:1793-1808`. It must still read:

```python
            def comment_run_end(payload: dict[str, Any]) -> dict[str, Any]:
                ...
                comment = comments.compose_run_end(
                    run_id=run_id,
                    milestone_id=milestone_card.id,
                    token=lease.token,
                    payload={**payload, "total": total},
                )
                payload["warnings"].extend(post_comment(store, root, comment, run_id=run_id))
                return payload
```

Then read `src/agent_manager/comments.py:312`. It must still read `outcome = next((name for name in _RUN_OUTCOMES if payload.get(name)), "ended")`.

Expected: both match. The header kind is then the first set flag among `cancelled`, `escalated`, `paused`, `done` on the very dict `run_milestone` returns, so the tests' `result[...] is True` assertions stand in for the header line. If either does not match, stop. Keep the header-line assertions in Tasks 3 and 4 and record the mismatch in the PR description.

No commit; nothing changed.

---

### Task 2: Per-card comments in the card-65ed3c70 section

**Files:**
- Modify: `tests/test_orchestrate.py:5377-5381` (`test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story`)
- Modify: `tests/test_orchestrate.py:5518-5524` (`test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone`)
- Modify: `tests/test_orchestrate.py:5603-5607` (`test_a_failed_merged_base_comments_on_its_story`)
- Modify: `tests/test_orchestrate.py:5631-5633` (`test_a_subtask_less_storys_failed_base_comments_on_that_story`)

**Interfaces:**
- Consumes: the existing helpers `_comments(project, card_id) -> list[board.BoardComment]`, `_keys(found) -> list[str]` and `_comment_states(project) -> list[tuple[str, str]]` (`tests/test_orchestrate.py:5336-5358`).
- Produces: nothing new.

`test_a_second_life_escalating_at_the_same_phase_adds_a_second_escalation_comment` (:5533) is deliberately left alone. All of its body checks (`detail: boom`, `detail: still`, no `reason:`) are rule-2 values. In `test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone`, `phase: review` is kept on purpose. The per-card escalation comment takes its phase from the lane outcome, not from the result dict, so the result cannot vouch for it (spec: "If you are unsure about an assertion, keep it.").

- [ ] **Step 1: Drop the done header in the clean-run subtask test**

In `test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story`, replace:

```python
        assert comment.author == "am"
        assert comment.body.startswith(f"am · done · run {run_id}\n")
        assert f"branch: {_branch(project, subtask)}" in comment.body
        assert "(resumed at" not in comment.body
```

with:

```python
        assert comment.author == "am"
        assert f"branch: {_branch(project, subtask)}" in comment.body
        assert "(resumed at" not in comment.body
```

The `{run_id}/{subtask}/done` key assertion and the three `"posted"` states at :5384-5388 already prove the kind and the delivery.

- [ ] **Step 2: Convert the escalated subtask comment**

In `test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone`, replace:

```python
    body = found[0].body
    assert found[0].author == "am"
    assert body.startswith(f"am · escalated · run {run_id}\n")
    assert "phase: review" in body
    assert "detail: reviewer found a blocker" in body
    assert 'reason: "the [ [parser]] still drops input; no test"' in body
    assert f"next: `am resume {run_id}`" in body
```

with:

```python
    assert (key, "posted") in _comment_states(project)
    body = found[0].body
    assert found[0].author == "am"
    assert "phase: review" in body
    assert "detail: reviewer found a blocker" in body
    assert 'reason: "the [ [parser]] still drops input; no test"' in body
```

- [ ] **Step 3: Convert the failed-base story comment**

In `test_a_failed_merged_base_comments_on_its_story`, replace:

```python
    body = found[0].body
    assert found[0].author == "am"
    assert body.startswith(f"am · base failed · run {run_id}\n")
    assert f"base branch: {root_plan.branch}" in body
```

with:

```python
    assert (f"{run_id}/{story_c}/base-failed", "posted") in _comment_states(project)
    body = found[0].body
    assert found[0].author == "am"
    assert f"base branch: {root_plan.branch}" in body
```

- [ ] **Step 4: Add state to the subtask-less story's base-failed comment**

In `test_a_subtask_less_storys_failed_base_comments_on_that_story`, replace:

```python
    assert _keys(found) == [f"{run_id}/{story_j}/base-failed"]
    assert f"base branch: {root_plan.branch}" in found[0].body
```

with:

```python
    assert _keys(found) == [f"{run_id}/{story_j}/base-failed"]
    assert (f"{run_id}/{story_j}/base-failed", "posted") in _comment_states(project)
    assert f"base branch: {root_plan.branch}" in found[0].body
```

- [ ] **Step 5: Liveness check, RED (Review Focus 5)**

Temporarily change the line added in Step 2 to:

```python
    assert (key, "pending") in _comment_states(project)
```

Run: `uv run pytest tests/test_orchestrate.py::test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone -v`
Expected: FAIL with an `AssertionError` on `(key, 'pending') in [...]`, whose list shows the row as `'posted'`.

- [ ] **Step 6: Revert the liveness change**

Restore the line to:

```python
    assert (key, "posted") in _comment_states(project)
```

- [ ] **Step 7: Run the four tests, GREEN**

Run: `uv run pytest tests/test_orchestrate.py -v -k "test_a_clean_run_leaves_one_done_comment_on_each_subtask_and_none_on_a_story or test_an_escalation_comments_only_the_escalated_subtask_and_the_milestone or test_a_failed_merged_base_comments_on_its_story or test_a_subtask_less_storys_failed_base_comments_on_that_story"`
Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add tests/test_orchestrate.py
git commit -m "test: per-card comment assertions use outbox key/state, not template text (a4e7c1a3)"
```

---

### Task 3: Milestone run-ends in the card-65ed3c70 section

**Files:**
- Modify: `tests/test_orchestrate.py:5406-5411` (`test_a_clean_run_leaves_one_done_run_end_comment_on_the_milestone`)
- Modify: `tests/test_orchestrate.py:5432-5438` (`test_an_integrate_escalation_leaves_an_integrate_failed_run_end_comment`)
- Modify: `tests/test_orchestrate.py:5465-5469` (`test_a_lane_escalation_leaves_an_escalated_run_end_comment_naming_the_parked`)
- Modify: `tests/test_orchestrate.py:5578-5584` (`test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review`)

**Interfaces:**
- Consumes: `_comments`, `_keys`, `_comment_states` (same as Task 2). Relies on Task 1 Step 3's confirmation that the result flag is the header kind.
- Produces: nothing new.

- [ ] **Step 1: Convert the clean-run run-end**

In `test_a_clean_run_leaves_one_done_run_end_comment_on_the_milestone`, replace:

```python
    (comment,) = found
    assert comment.author == "am"
    assert comment.body.startswith(f"am · done · run {run_id}\n")
    assert "done: 3 of 3" in comment.body
    assert f"integrated: {INTEGRATION_BRANCH}" in comment.body
    assert f"next: `git merge {INTEGRATION_BRANCH}`" in comment.body
```

with:

```python
    assert (key, "posted") in _comment_states(project)
    (comment,) = found
    assert comment.author == "am"
    assert "done: 3 of 3" in comment.body
    assert f"integrated: {INTEGRATION_BRANCH}" in comment.body
```

`result["done"] is True` (:5401) stands in for the header. The `git merge` line is `test_run_end_golden_bodies[done]`'s job.

- [ ] **Step 2: Convert the integrate-escalation run-end**

In `test_an_integrate_escalation_leaves_an_integrate_failed_run_end_comment`, replace:

```python
    (comment,) = _comments(project, milestone)
    assert comment.body.startswith(f"am · escalated · run {run_id}\n")
    assert (
        f"integrate failed at integrate on [[{story_b}]]: the resolver did not finish"
        in comment.body
    )
    assert f"next: `am run --milestone {milestone}`" in comment.body
```

with:

```python
    found = _comments(project, milestone)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    assert (key, "posted") in _comment_states(project)
    assert (
        f"integrate failed at integrate on [[{story_b}]]: the resolver did not finish"
        in found[0].body
    )
```

The kept `integrate failed at …` line depends on the same `integrate_failed` branch (`comments.py:313-315`) that picks the `am run --milestone` hint. So the payload shape is still proven here, and the hint text is pinned by `test_run_end_names_the_story_an_integrate_conflict_stopped_at` and `test_run_end_reports_an_integrate_failure`.

- [ ] **Step 3: Convert the lane-escalation run-end**

In `test_a_lane_escalation_leaves_an_escalated_run_end_comment_naming_the_parked`, replace:

```python
    body = found[0].body
    assert body.startswith(f"am · escalated · run {run_id}\n")
    assert f"escalated: [[{a1}]] at review" in body
    assert f"parked: [[{b1}]]" in body
    assert f"next: `am resume {run_id}`" in body
```

with:

```python
    assert (key, "posted") in _comment_states(project)
    body = found[0].body
    assert f"escalated: [[{a1}]] at review" in body
    assert f"parked: [[{b1}]]" in body
```

- [ ] **Step 4: Convert the resumed-at-review cross-life test (Review Focus 4)**

In `test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review`, replace:

```python
    assert found[1].body.startswith(f"am · done · run {run_id}\n(resumed at review)\n")
    on_milestone = _comments(project, milestone)
    assert [comment.body.split("\n", 1)[0] for comment in on_milestone] == [
        f"am · escalated · run {run_id}",
        f"am · done · run {run_id}",
    ]
    assert len(set(_keys(on_milestone))) == 2
```

with:

```python
    assert "(resumed at review)" in found[1].body.split("\n")
    assert (keys[1], "posted") in _comment_states(project)
    assert first["escalated"] is True, first
    milestone_keys = _keys(_comments(project, milestone))
    assert len(milestone_keys) == 2 and len(set(milestone_keys)) == 2, milestone_keys
    assert all(key.startswith(f"{run_id}/{milestone}/run-end:") for key in milestone_keys)
    run_end_rows = [row for row in _comment_states(project) if "/run-end:" in row[0]]
    assert run_end_rows == [(key, "posted") for key in milestone_keys]
```

Together, `first["escalated"]`, the existing `result["done"]` (:5571), and the ordered equality between outbox insertion order and board order prove escalated-then-done.

- [ ] **Step 5: Liveness check, RED (Review Focus 5)**

Temporarily change the last line added in Step 4 to:

```python
    assert run_end_rows == [(key, "posted") for key in reversed(milestone_keys)]
```

Run: `uv run pytest tests/test_orchestrate.py::test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review -v`
Expected: FAIL with an `AssertionError` comparing two two-element lists in opposite order.

- [ ] **Step 6: Revert the liveness change**

Restore the line to:

```python
    assert run_end_rows == [(key, "posted") for key in milestone_keys]
```

- [ ] **Step 7: Run the four tests, GREEN**

Run: `uv run pytest tests/test_orchestrate.py -v -k "test_a_clean_run_leaves_one_done_run_end_comment_on_the_milestone or test_an_integrate_escalation_leaves_an_integrate_failed_run_end_comment or test_a_lane_escalation_leaves_an_escalated_run_end_comment_naming_the_parked or test_a_resume_after_the_fix_keeps_the_escalation_and_adds_done_resumed_at_review"`
Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add tests/test_orchestrate.py
git commit -m "test: run-end assertions use key, posted state and result flag, not headers (a4e7c1a3)"
```

---

### Task 4: Cancel and pause comments (card-5d9a875f section)

**Files:**
- Modify: `tests/test_orchestrate.py:5847-5863` (`test_a_cancel_comments_each_parked_subtask_and_the_milestone`)
- Modify: `tests/test_orchestrate.py:5900-5904` (`test_a_cancel_with_an_escalated_lane_comments_only_the_parked_subtask_as_cancelled`)
- Modify: `tests/test_orchestrate.py:5936-5937` (`test_a_cancel_on_a_lane_waiting_for_a_slot_comments_its_subtask_without_a_phase`)
- Modify: `tests/test_orchestrate.py:5968-5969` (`test_a_cancel_while_a_base_builds_comments_no_story_and_still_ends_the_run`)
- Modify: `tests/test_orchestrate.py:6021-6026` (`test_a_pause_leaves_exactly_one_paused_run_end_on_the_milestone`)
- Modify: `tests/test_orchestrate.py:6049-6056` (`test_a_paused_then_resumed_run_leaves_a_paused_then_a_done_run_end`)

**Interfaces:**
- Consumes: `_comments`, `_keys`, `_comment_states` (same as Task 2), and Task 1 Step 3's confirmation.
- Produces: nothing new.

`test_a_cancel_whose_comments_the_board_refuses_is_still_cancelled_with_warnings` (:5975) already asserts by state and stays untouched.

- [ ] **Step 1: Convert the parked-subtask cancel comments**

In `test_a_cancel_comments_each_parked_subtask_and_the_milestone`, replace:

```python
        assert comment.author == "am"
        assert comment.body.startswith(f"am · cancelled · run {run_id}\n")
        assert "stopped before: implement" in comment.body
        assert f"branch: {_branch(project, subtask)}" in comment.body
        assert f"relaunch: `am run --milestone {milestone}`" in comment.body
```

with:

```python
        assert comment.author == "am"
        assert "stopped before: implement" in comment.body
        assert f"branch: {_branch(project, subtask)}" in comment.body
```

- [ ] **Step 2: Convert the same test's cancelled run-end**

In the same test, replace:

```python
    body = on_milestone[0].body
    assert body.startswith(f"am · cancelled · run {run_id}\n")
    assert "done: 1 of 3" in body
    assert "parked: " + ", ".join(f"[[{card}]]" for card in parked) in body
    assert f"next: `am run --milestone {milestone}`" in body
```

with:

```python
    body = on_milestone[0].body
    assert "done: 1 of 3" in body
    assert "parked: " + ", ".join(f"[[{card}]]" for card in parked) in body
```

The existing `cancelled_keys == …` and `all(state == "posted" …)` (:5864-5866) plus `result["cancelled"]` (:5840) cover the kind and the delivery.

- [ ] **Step 3: Convert the cancel-with-escalated-lane run-end**

In `test_a_cancel_with_an_escalated_lane_comments_only_the_parked_subtask_as_cancelled`, replace:

```python
    (comment,) = _comments(project, milestone)
    assert comment.body.startswith(f"am · cancelled · run {run_id}\n")
    assert f"escalated: [[{a1}]] at review" in comment.body
    assert f"parked: [[{b1}]]" in comment.body
    assert f"next: `am run --milestone {milestone}`" in comment.body
```

with:

```python
    found = _comments(project, milestone)
    (key,) = _keys(found)
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    states = _comment_states(project)
    assert (f"{run_id}/{b1}/cancelled", "posted") in states
    assert (key, "posted") in states
    assert f"escalated: [[{a1}]] at review" in found[0].body
    assert f"parked: [[{b1}]]" in found[0].body
```

- [ ] **Step 4: Convert the queued-lane cancel comment**

In `test_a_cancel_on_a_lane_waiting_for_a_slot_comments_its_subtask_without_a_phase`, replace:

```python
    assert f"branch: {_branch(project, q1)}" in body
    assert f"relaunch: `am run --milestone {milestone}`" in body
```

with:

```python
    assert f"branch: {_branch(project, q1)}" in body
    assert (f"{run_id}/{q1}/cancelled", "posted") in _comment_states(project)
```

- [ ] **Step 5: Convert the cancel-while-base-builds run-end**

In `test_a_cancel_while_a_base_builds_comments_no_story_and_still_ends_the_run`, replace:

```python
    (comment,) = _comments(project, milestone)
    assert comment.body.startswith(f"am · cancelled · run {run_id}\n")
    assert result["warnings"] == []
```

with:

```python
    (key,) = _keys(_comments(project, milestone))
    assert key.startswith(f"{run_id}/{milestone}/run-end:")
    assert (key, "posted") in _comment_states(project)
    assert result["warnings"] == []
```

- [ ] **Step 6: Convert the paused run-end**

In `test_a_pause_leaves_exactly_one_paused_run_end_on_the_milestone`, replace:

```python
    body = found[0].body
    assert found[0].author == "am"
    assert body.startswith(f"am · paused · run {run_id}\n")
    assert "done: 0 of 3" in body
    assert f"parked: [[{a1}]]" in body
    assert f"next: `am resume {run_id}`" in body
```

with:

```python
    assert _comment_states(project) == [(key, "posted")]
    body = found[0].body
    assert found[0].author == "am"
    assert "done: 0 of 3" in body
    assert f"parked: [[{a1}]]" in body
```

- [ ] **Step 7: Convert the paused-then-resumed cross-life test (Review Focus 4)**

In `test_a_paused_then_resumed_run_leaves_a_paused_then_a_done_run_end`, replace:

```python
    on_milestone = _comments(project, milestone)
    assert [comment.body.split("\n", 1)[0] for comment in on_milestone] == [
        f"am · paused · run {run_id}",
        f"am · done · run {run_id}",
    ]
    keys = _keys(on_milestone)
    assert len(set(keys)) == 2, keys
    assert all(key.startswith(f"{run_id}/{milestone}/run-end:") for key in keys)
```

with:

```python
    keys = _keys(_comments(project, milestone))
    assert len(keys) == 2 and len(set(keys)) == 2, keys
    assert all(key.startswith(f"{run_id}/{milestone}/run-end:") for key in keys)
    run_end_rows = [row for row in _comment_states(project) if "/run-end:" in row[0]]
    assert run_end_rows == [(key, "posted") for key in keys]
```

`first["paused"]` (:6044) and `again["done"]` (:6048) are the two lives' result flags, and they are already asserted. The `/run-end:` filter is needed because the second life also posts `a1`'s `done` comment.

- [ ] **Step 8: Liveness check, RED (Review Focus 5)**

Temporarily change the line added in Step 6 to:

```python
    assert _comment_states(project) == [(key, "pending")]
```

Run: `uv run pytest tests/test_orchestrate.py::test_a_pause_leaves_exactly_one_paused_run_end_on_the_milestone -v`
Expected: FAIL with an `AssertionError` showing `[(…, 'posted')] == [(…, 'pending')]`.

- [ ] **Step 9: Revert the liveness change**

Restore the line to:

```python
    assert _comment_states(project) == [(key, "posted")]
```

- [ ] **Step 10: Run the six tests, GREEN**

Run: `uv run pytest tests/test_orchestrate.py -v -k "test_a_cancel_comments_each_parked_subtask_and_the_milestone or test_a_cancel_with_an_escalated_lane_comments_only_the_parked_subtask_as_cancelled or test_a_cancel_on_a_lane_waiting_for_a_slot_comments_its_subtask_without_a_phase or test_a_cancel_while_a_base_builds_comments_no_story_and_still_ends_the_run or test_a_pause_leaves_exactly_one_paused_run_end_on_the_milestone or test_a_paused_then_resumed_run_leaves_a_paused_then_a_done_run_end"`
Expected: 6 passed.

- [ ] **Step 11: Commit**

```bash
git add tests/test_orchestrate.py
git commit -m "test: cancel/pause comment assertions use outbox key/state, not template text (a4e7c1a3)"
```

---

### Task 5: Coverage guard, the V4 §6 before/after diff, and the full suite

**Files:**
- Read: `tests/test_orchestrate.py`, `tests/test_comments.py` (no edits)

**Interfaces:**
- Consumes: `${TMPDIR:-/tmp}/a4e7c1a3-before.txt` from Task 1.
- Produces: `${TMPDIR:-/tmp}/a4e7c1a3-after.txt`.

- [ ] **Step 1: No template text left in the section (Review Focus 3)**

```bash
awk 'NR>=5333 && NR<=6075' tests/test_orchestrate.py | grep -nE 'am · |next: `|relaunch: `'
```

Expected: no output, and exit status 1. Any hit is a rule-1 assertion that was missed. Convert it the same way as the matching task.

- [ ] **Step 2: Every golden twin still exists (Review Focus 3)**

```bash
grep -nE 'def (test_escalated_with_a_reason_golden_body|test_done_fresh_golden_body|test_done_resumed_golden_body|test_cancelled_golden_body|test_base_failed_golden_body_goes_on_the_story|test_run_end_golden_bodies|test_run_end_names_the_story_an_integrate_conflict_stopped_at|test_run_end_of_a_cancel_that_escalated_names_the_escalated_card)\(' tests/test_comments.py
git diff --stat m15/task-convert-the-project-b9c19b33 -- tests/test_comments.py
```

Expected: 8 `def` lines from the first command, and empty output from the second (so `test_comments.py` is unchanged).

- [ ] **Step 3: Only the one file changed**

```bash
git diff --stat m15/task-convert-the-project-b9c19b33 -- . ':!docs'
```

Expected: exactly one file listed, `tests/test_orchestrate.py`.

- [ ] **Step 4: Capture the after outcomes**

```bash
uv run pytest tests/test_orchestrate.py -k "comment or run_end or resumed_at_review" -rA -q 2>&1 \
  | grep -E '^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) ' | sort > "${TMPDIR:-/tmp}/a4e7c1a3-after.txt"
```

- [ ] **Step 5: Diff before and after (V4 §6 proof)**

```bash
diff "${TMPDIR:-/tmp}/a4e7c1a3-before.txt" "${TMPDIR:-/tmp}/a4e7c1a3-after.txt"
```

Expected: no output, and exit status 0. The pass/fail outcomes are identical.

- [ ] **Step 6: Full suite**

Run: `uv run pytest`
Expected: all tests pass, and no new failures or errors.

- [ ] **Step 7: Commit (only if Steps 1-6 required a fix)**

```bash
git add tests/test_orchestrate.py
git commit -m "test: finish comment-body to key/state conversion (a4e7c1a3)"
```

If no fix was needed, there is nothing to commit. Tasks 2-4 already hold the work.
