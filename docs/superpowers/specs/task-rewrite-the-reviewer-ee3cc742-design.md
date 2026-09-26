# Rewrite the reviewer role from task.js's Review stage (card ee3cc742)

Parent story be007353 "Close the task.js gaps". Narrows G9 point 1 of `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (lines 331-347) and Plan Task 2.2 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:478-534`). Blocked-by 468daea9 (done) already made `review_blockers_gate` consume `unresolved_blockers`; this card makes the reviewer actually produce what the three review gates read.

## Scope

In scope, and nothing else:

1. `src/agent_manager/roles/bundles/reviewer/system.md` — replace the 9-line stub with a port of task.js's Review prompt (`~/Code/leave-me-alone/plugins/leave-me-alone/workflows/task.js:795-826`).
2. `src/agent_manager/roles/bundles/reviewer/policy.toml` — `allowed_tools = ["Read", "Grep", "Glob", "Bash", "Edit", "Write"]`. `max_attempts = 1`, `required_capabilities = []` and `[default_model] claude = "opus"` stay exactly as they are. No new keys (Policy is `extra="forbid"`, `roles/loader.py:50-66`).
3. New test `tests/roles/test_reviewer_brief.py`.

Out of scope: `results.py` (ReviewResult at 107-123 is unchanged), `steps/reducers.py` (`review_gate` :181, `plan_hash_gate` :261, `review_blockers_gate` — already implemented), `workflow/task.py` (the review phase already declares inputs `branch`, `base_branch`, `plan_path` at line 100), the critic / spec_critic / plan_critic bundles (c7bea6a2), `steps/verify.py` (cf8b3888), anything under `runtime/`, SubtaskSummary/journal/escalation shapes.

## Observable behavior: the reviewer's standing instructions

`load_role("reviewer").system` must carry every instruction of task.js's Review prompt, adapted as follows:

- The harness cwd is already the worktree: commands carry no `git -C <worktree>`.
- `<base>` is the brief's `base_branch` input and `<plan>` its `plan_path` input; the spec is found via the plan. The diff reviewed is `git diff <base>...HEAD`.
- The implementer's report and card id are not interpolated (they are not role-level text); the prompt refers to "the plan and the spec it came from" and the repo's own standards docs cited in the plan.

Instructions to preserve (wording may be adapted, meaning may not):

- Review the full branch diff against the plan and spec; check each new test file's path against the repo's test-placement rule (wrong tier is a finding); one line per finding, severity-tagged blocker/major/minor, no praise, no scope creep; verify each finding against the actual code before reporting.
- Test-integrity gate on the test portion of the diff: no weakened or deleted assertions, no tautologies, no tests mirroring the implementation, every new behavior has a test that would fail without its code; a violation is a finding, fixed if possible, blocker-severity if not. Contains the literal sentence `Never weaken, skip, xfail, or delete a test` (to make anything pass).
- The reviewer is the only stage that writes: everything that needs changing is changed and COMMITTED here; uncommitted work stops the run.
- Fix real findings in the same pass, TDD where behavior changes (failing test first), commit granularly.
- Every commit — fixes and lint/format fixes alike — ends with both trailers: `Co-Authored-By: Claude <noreply@anthropic.com>` and `Plan-Hash: $PLAN_HASH`.
- Compute `PLAN_HASH` once before the first commit: `PLAN_HASH=$(sha256sum "<plan>" | cut -c1-8)`; explain that one untagged commit sinks the subtask.
- Run the repo's own lint/format commands and commit their fixes, tagged the same way, so the tree is clean.
- Skip findings that prove wrong on inspection and note why in `fix_summary`.
- FINALLY, after committing, run exactly these three commands and report their output verbatim (literal phrase `report their output verbatim`), without interpreting, acting on, or changing anything in response:
  ```
  git status --porcelain
  git rev-list --count <base>..HEAD
  PLAN_HASH=$(sha256sum "<plan>" | cut -c1-8); git log <base>..HEAD --format=%B | grep -c "^Plan-Hash: $PLAN_HASH"
  ```
- Result fields, snake_case, matching ReviewResult: `findings` (every finding raised, severity-tagged, fixed or not; `[]` if clean), `unresolved_blockers` (ONLY blocker-severity findings still standing after the fix pass — fixed or correctly-dismissed blockers excluded; an empty list is a claim nothing blocker-severity remains), `fix_summary` (fixed vs skipped and why; empty string if no findings), `porcelain` (first command's output exactly; empty string if nothing printed), `commit_count` (second command's number), `tagged_count` (third command's number), `plan_hash` (the 8 characters `$PLAN_HASH` held, not the command).

## Error paths

No new code paths. The only failure modes are the loader's existing ones: an empty `system.md` or a `policy.toml` that fails validation raises `RoleBundleError` at load time. Both are already covered by `tests/roles/test_loader.py`; the parametrized shipped-bundle tests there (`test_every_shipped_bundle_loads`, `test_every_shipped_bundle_pins_its_claude_model` expecting `opus`) must remain green unchanged. Downstream misbehavior of a real reviewer (dirty tree, untagged commits, standing blockers) is caught by the existing gates, not here.

## Tests

Test-placement rule: agent-manager-design.md §14 (lines 497-508) places tests by what they exercise, and pygents-engine-design.md §9 (lines 379-401) specifies "roles: golden briefs" — a role test asserts its loaded system-prompt text carries the required strings, with no model call and no running phase.

- `tests/roles/test_reviewer_brief.py::test_reviewer_brief_carries_the_task_js_contract` — **golden-brief tier** (roles). Body exactly as the plan's Task 2.2 Step 1: `load_role("reviewer")`, asserts each needle in `role.system` (`git status --porcelain`, `git rev-list --count`, `grep -c "^Plan-Hash: $PLAN_HASH"`, `Co-Authored-By:`, `Plan-Hash: $PLAN_HASH`, `sha256sum`, `unresolved_blockers`, `Never weaken, skip, xfail, or delete a test`, `report their output verbatim`), and `{"Edit", "Write"} <= set(role.policy.allowed_tools)`. Attribute names `system` and `policy.allowed_tools` are confirmed at `roles/loader.py:107-113` and `:64`. Must fail against the current stub before the rewrite.
- Existing `tests/roles/test_loader.py` shipped-bundle tests — unchanged, must stay green.

Done when `uv run pytest` (whole default suite, including `tests/e2e`, on both engines) is green. Suggested commit: `feat(roles): the reviewer fixes, commits and reports task.js's three git facts`.
