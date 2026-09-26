<!-- task-pipeline: validated -->
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

---

# Reviewer Role Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the shipped `reviewer` role carry task.js's full Review-stage contract (review, test-integrity gate, fix-and-commit with both trailers, the three verbatim git facts, snake_case ReviewResult fields) and let it edit files.

**Architecture:** A prompt-and-policy change only. `src/agent_manager/roles/bundles/reviewer/system.md` is rewritten as a port of `task.js:795-826`; `policy.toml` gains `Edit` and `Write`. A new golden-brief test in `tests/roles/` loads the bundle through the existing `agent_manager.roles.loader.load_role` and asserts the required strings and tools. No Python source changes.

**Tech Stack:** Python, pytest, `uv`, Pydantic (`RoleBundle`/`Policy` in `src/agent_manager/roles/loader.py`, strict, `extra="forbid"`).

**Spec:** `docs/superpowers/specs/task-rewrite-the-reviewer-ee3cc742-design.md` (prepended verbatim above).

## Global Constraints

- Only these three paths change: `src/agent_manager/roles/bundles/reviewer/system.md`, `src/agent_manager/roles/bundles/reviewer/policy.toml`, `tests/roles/test_reviewer_brief.py` (new).
- Do NOT touch `src/agent_manager/results.py` (ReviewResult at 107-123 is unchanged), `src/agent_manager/steps/reducers.py`, `src/agent_manager/workflow/task.py`, the `critic` bundle, `src/agent_manager/steps/verify.py`, or anything under `src/agent_manager/runtime/`.
- `policy.toml`: `allowed_tools = ["Read", "Grep", "Glob", "Bash", "Edit", "Write"]`; `max_attempts = 1`, `required_capabilities = []` and `[default_model] claude = "opus"` stay exactly as they are; no new keys (Policy is `extra="forbid"`).
- Trailers, verbatim: `Co-Authored-By: Claude <noreply@anthropic.com>` and `Plan-Hash: $PLAN_HASH`.
- Hash command, verbatim: `PLAN_HASH=$(sha256sum "<plan>" | cut -c1-8)`.
- No `git -C` anywhere in the prompt (the harness cwd is already the worktree).
- `<base>` is the brief's `base_branch` input, `<plan>` its `plan_path` input.
- Result fields are snake_case: `findings`, `unresolved_blockers`, `fix_summary`, `porcelain`, `commit_count`, `tagged_count`, `plan_hash`.
- The whole default suite stays green: `uv run pytest` (including `tests/e2e`, on both engines).
- Do not add, commit or amend anything under `docs/superpowers/specs/` or `docs/superpowers/plans/` (the engine commits those).

## Review Focus

- The reviewer's `system.md` is the first thing in every review brief (`prompt.compose_brief`, `src/agent_manager/prompt.py:380`), and `tests/e2e/fake_claude.py` finds the result contract with a global `text.find("## Result contract")` (`fake_claude.py:88-96`) and the schema with a ```` ```json ```` fence, and the phase with a `^# phase:` line. A system prompt carrying any of those literals would make the fake (and a real agent) read the wrong contract; expected: none of them appear in `system.md`. Pinned by `test_reviewer_system_text_cannot_be_mistaken_for_brief_structure` in Task 1.
- A half-done port leaves task.js-only syntax behind (`git -C`, `${WORKTREE}`, `${baseRef}`, `${plan}`); expected: no `git -C` and no `${` in the prompt, and the three commands appear as exactly the three lines the spec gives. Pinned by `test_reviewer_brief_uses_agent_managers_inputs_and_result_fields` in Task 1.
- task.js's camelCase result names (`unresolvedBlockers`, `fixSummary`, `commitCount`, `taggedCount`, `planHash`) leaking into the prompt would contradict the snake_case schema in the result contract; expected: all seven snake_case names present, no camelCase ones. Pinned by the same test.
- The spec names the exact trailer `Co-Authored-By: Claude <noreply@anthropic.com>` and the exact hash command, but the plan-level golden test only checks the prefixes `Co-Authored-By:` and `sha256sum`; expected: the full strings are present. Pinned by the same test.
- Editing `policy.toml` could silently change `max_attempts`, `required_capabilities` or the model, which `test_loader.py` only partly covers (model only); expected: exactly the six tools and every other field unchanged. Pinned by `test_reviewer_policy_adds_edit_and_write_and_changes_nothing_else` in Task 1.

---

### Task 1: The reviewer role carries task.js's Review contract and may edit

**Files:**
- Create: `tests/roles/test_reviewer_brief.py`
- Modify: `src/agent_manager/roles/bundles/reviewer/system.md:1-10` (whole file replaced)
- Modify: `src/agent_manager/roles/bundles/reviewer/policy.toml:1` (the `allowed_tools` line only)

Test placement: golden-brief tier (pygents-engine-design.md §9 lines 379-401, "roles: golden briefs"). The existing sibling in this tier is `tests/roles/test_loader.py`, which imports from `agent_manager.roles` and loads shipped bundles through the public API with no model call, no git repo, no harness; the new file sits next to it and does the same.

**Interfaces:**
- Consumes: `agent_manager.roles.loader.load_role(name: str, *, root: Path | None = None) -> RoleBundle`; `RoleBundle.system: str`; `RoleBundle.policy: Policy`; `Policy.allowed_tools: list[str]`, `Policy.max_attempts: int`, `Policy.required_capabilities: list[str]`, `Policy.default_model: dict[str, str]` (all in `src/agent_manager/roles/loader.py:61-114`).
- Produces: nothing new in Python. The reviewer bundle's text and policy are consumed at dispatch by `prompt.compose_brief` and, downstream, by the existing gates `reducers.review_blockers_gate`, `reducers.review_gate`, `reducers.plan_hash_gate_adapter` via `results.ReviewResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/roles/test_reviewer_brief.py` with exactly this content (the first test's body is verbatim from Plan Task 2.2 Step 1):

```python
"""Golden brief for the `reviewer` role (card ee3cc742).

Placement: pygents-engine-design.md §9 (lines 379-401) tests roles as golden
briefs -- the shipped bundle is loaded through the public loader and its
system text is checked for the instructions the review phase depends on. No
model call, no git repository, no harness, same tier as `test_loader.py`.
"""

from agent_manager.roles.loader import load_role


def test_reviewer_brief_carries_the_task_js_contract():
    role = load_role("reviewer")
    text = role.system
    for needle in (
        "git status --porcelain",
        "git rev-list --count",
        'grep -c "^Plan-Hash: $PLAN_HASH"',
        "Co-Authored-By:",
        "Plan-Hash: $PLAN_HASH",
        "sha256sum",
        "unresolved_blockers",
        "Never weaken, skip, xfail, or delete a test",
        "report their output verbatim",
    ):
        assert needle in text, needle
    assert {"Edit", "Write"} <= set(role.policy.allowed_tools)


THREE_COMMANDS = (
    "git status --porcelain",
    "git rev-list --count <base>..HEAD",
    'PLAN_HASH=$(sha256sum "<plan>" | cut -c1-8); '
    'git log <base>..HEAD --format=%B | grep -c "^Plan-Hash: $PLAN_HASH"',
)

RESULT_FIELDS = (
    "findings",
    "unresolved_blockers",
    "fix_summary",
    "porcelain",
    "commit_count",
    "tagged_count",
    "plan_hash",
)


def test_reviewer_brief_uses_agent_managers_inputs_and_result_fields():
    text = load_role("reviewer").system
    lines = [line.strip() for line in text.splitlines()]

    for command in THREE_COMMANDS:
        assert command in lines, command
    for needle in (
        "base_branch",
        "plan_path",
        "git diff <base>...HEAD",
        'PLAN_HASH=$(sha256sum "<plan>" | cut -c1-8)',
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        *(f"`{field}`" for field in RESULT_FIELDS),
    ):
        assert needle in text, needle
    for leftover in (
        "git -C",
        "${",
        "unresolvedBlockers",
        "fixSummary",
        "commitCount",
        "taggedCount",
        "planHash",
    ):
        assert leftover not in text, leftover


def test_reviewer_system_text_cannot_be_mistaken_for_brief_structure():
    # The system text opens every review brief (prompt.compose_brief). These
    # literals are how the brief's own result contract and phase header are
    # found (prompt.py:339, :352; tests/e2e/fake_claude.py:48, :73, :79), so
    # the role must never carry them itself.
    text = load_role("reviewer").system
    assert "## Result contract" not in text
    assert "```json" not in text
    assert not any(
        line.startswith(("# phase:", "# role:")) for line in text.splitlines()
    )


def test_reviewer_policy_adds_edit_and_write_and_changes_nothing_else():
    policy = load_role("reviewer").policy
    assert policy.allowed_tools == ["Read", "Grep", "Glob", "Bash", "Edit", "Write"]
    assert policy.max_attempts == 1
    assert policy.required_capabilities == []
    assert policy.default_model == {"claude": "opus"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/roles/test_reviewer_brief.py -v`

Expected:
- `test_reviewer_brief_carries_the_task_js_contract` FAILS with `AssertionError: git status --porcelain` (the stub has none of the needles).
- `test_reviewer_brief_uses_agent_managers_inputs_and_result_fields` FAILS with `AssertionError: git status --porcelain` (first of the three commands missing).
- `test_reviewer_policy_adds_edit_and_write_and_changes_nothing_else` FAILS on the `allowed_tools` equality (`["Read", "Grep", "Glob", "Bash"]`).
- `test_reviewer_system_text_cannot_be_mistaken_for_brief_structure` PASSES: it is a regression pin that the stub already satisfies; it must still pass after Step 3.

If any of the three expected failures fails for a different reason (for example an `ImportError` or `RoleBundleError`), stop and fix the test, not the bundle.

- [ ] **Step 3: Rewrite `system.md`**

Replace the whole of `src/agent_manager/roles/bundles/reviewer/system.md` with exactly this content (note the command fences are plain ```` ``` ````, never ```` ```json ````, and no line starts with `# phase:`):

````markdown
# Reviewer

You review a finished branch against its plan and the spec that plan came from, fix what is wrong, commit every change you make, and then report three git facts.

Your working directory is already the subtask's worktree, checked out on the branch named in this brief's `## branch` section. Run every command below from here, exactly as written; never add a `-C` option. Throughout, `<base>` means the value of this brief's `## base_branch` section (the `base_branch` input) and `<plan>` means the path in its `## plan_path` section (the `plan_path` input). The plan cites the spec it came from and this repo's own architecture and standards docs; read them.

## Review the diff

Review the full branch diff, `git diff <base>...HEAD`, against the plan and the spec it came from.

- Check every new test file's path against this repo's own test-placement rule (cited in the plan). A test sitting in the wrong tier is a finding, with the same severity a wrong-tier test would earn in this repo's own review discipline.
- One line per finding, severity-tagged `blocker`, `major` or `minor`. No praise, no scope creep.
- Verify each finding against the actual code before reporting it.

## The test-integrity gate

Then apply the test-integrity gate to the test portion of that same diff: no weakened or deleted assertions, no tautologies, no tests that merely mirror the implementation, and every new behavior has a test that would fail without its code. A violation here is a finding like any other: raise it, fix it, and if it genuinely cannot be fixed it is blocker-severity. Never weaken, skip, xfail, or delete a test to make anything pass.

## You are the only stage that writes

You are the only stage that reads this diff and the only one that writes: the stage after you runs the suite and reports, and is forbidden to fix anything. So everything that needs changing must be changed HERE, and everything you change must be COMMITTED here. Uncommitted work never lands on the branch at all, and will stop the run.

## Fix and commit

If you find any real findings, fix them yourself in the same pass, on this branch. Use TDD wherever behavior changes: write the failing test first and watch it fail, then fix. Commit granularly, one small commit per fix.

Compute `PLAN_HASH` once, before your first commit:

```
PLAN_HASH=$(sha256sum "<plan>" | cut -c1-8)
```

That is the same value every commit already on this branch carries. End EVERY commit you make, fixes and lint/format fixes alike, with both of these trailers, each on its own line at the end of the message, with `$PLAN_HASH` expanded to its 8 characters:

```
Co-Authored-By: Claude <noreply@anthropic.com>
Plan-Hash: $PLAN_HASH
```

The pipeline counts the commits carrying that `Plan-Hash` value and STOPS the run if any commit on the branch lacks it, so a single untagged fix commit sinks the whole subtask.

Also run this repo's own lint and format commands and commit any fixes they require, tagged the same way, so the tree is clean when you finish.

Skip any finding that turns out to be wrong on closer inspection: note why in `fix_summary` instead of "fixing" it.

## Finally: the three git facts

FINALLY, once you have finished committing, run exactly these three commands and report their output verbatim. Do not interpret them, do not act on them, and do not change anything in response to them: they are read by the pipeline itself, which decides what they mean.

```
git status --porcelain
git rev-list --count <base>..HEAD
PLAN_HASH=$(sha256sum "<plan>" | cut -c1-8); git log <base>..HEAD --format=%B | grep -c "^Plan-Hash: $PLAN_HASH"
```

## What you return

Write your result to the path this brief's result contract names, with exactly these fields:

- `findings`: every finding you raised, severity-tagged, whether or not you went on to fix it. `[]` if the diff was clean.
- `unresolved_blockers`: ONLY the blocker-severity findings still standing after your fix pass. A blocker you actually fixed, or correctly determined was wrong, does NOT belong here. This list stops the pipeline before the card is marked done, so an empty list is a claim that nothing blocker-severity is left in the code.
- `fix_summary`: what you fixed versus skipped, and why. Empty string if `findings` was empty.
- `porcelain`: the FIRST command's output exactly as printed. Empty string if it printed nothing.
- `commit_count`: the SECOND command's number.
- `tagged_count`: the THIRD command's number.
- `plan_hash`: the value `$PLAN_HASH` held when you ran that third command: the 8 characters, not the command.
````

Why "never add a `-C` option" rather than naming `git -C`: `test_reviewer_brief_uses_agent_managers_inputs_and_result_fields` forbids the literal `git -C` anywhere in the text, so the adaptation is stated without reintroducing it.

- [ ] **Step 4: Add `Edit` and `Write` to the policy**

In `src/agent_manager/roles/bundles/reviewer/policy.toml`, replace line 1 only. The whole file must then read exactly:

```toml
allowed_tools = ["Read", "Grep", "Glob", "Bash", "Edit", "Write"]
max_attempts = 1
required_capabilities = []

[default_model]
claude = "opus"
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/roles/test_reviewer_brief.py -v`
Expected: 4 passed.

- [ ] **Step 6: Run the loader's shipped-bundle tests**

Run: `uv run pytest tests/roles/test_loader.py -v`
Expected: all pass, including `test_every_shipped_bundle_loads[reviewer]` and `test_every_shipped_bundle_pins_its_claude_model[reviewer]` (unchanged file).

- [ ] **Step 7: Run the whole default suite**

Run: `uv run pytest`
Expected: all green, including `tests/e2e` (the fake `claude` reads the review brief whose head is now this new `system.md`; the Review Focus guard test is what keeps that parse intact). If an e2e test fails, read its message before touching anything: the fix belongs in `system.md`'s wording, never in a test or in `fake_claude.py`.

- [ ] **Step 8: Commit**

Stage only the three files (never anything under `docs/superpowers/`). The message's last line is the `Plan-Hash:` trailer carrying exactly the 8-character value in your brief's `## plan_hash` section, per your standing coder instructions. That value hashes this plan file, so it cannot be written into the plan itself; copy it from the brief, and do not compute it by hashing the plan yourself. Set it in the shell first, then commit:

```bash
PLAN_HASH_FROM_BRIEF="<paste the 8 characters from the brief's ## plan_hash section>"
git add tests/roles/test_reviewer_brief.py src/agent_manager/roles/bundles/reviewer/system.md src/agent_manager/roles/bundles/reviewer/policy.toml
git commit -m "feat(roles): the reviewer fixes, commits and reports task.js's three git facts" -m "Plan-Hash: $PLAN_HASH_FROM_BRIEF"
```

Then confirm the trailer landed: `git log -1 --format=%B` must end with `Plan-Hash: ` followed by that value.
