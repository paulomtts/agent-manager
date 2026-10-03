# Spec and plan bundles: do not commit (card c06c167b)

Subtask of story 32cd1358. This is a text-only card. Four role bundles' `system.md` each gain one instruction: the agent never runs `git commit`, because writing (or, for critics, editing) the document is the whole job, and the workflow's `docs_commit` step commits the spec and the plan. No design changes here.

## Inherited constraints

- The `docs_commit` step sits between `mark_validated` and `implement`. It is `docs_commit.commit_documents`, the step that commits the spec and the plan (pygents addendum §7, `docs/superpowers/specs/2026-09-25-pygents-engine-design.md:320-321`). It is wired at `src/agent_manager/workflow/task.py:96`, and `tests/workflow/test_task.py:95-115` asserts that wiring.
- `plan_hash` is "the `docs_commit` step's digest of the validated plan" (design §7, `docs/superpowers/specs/2026-09-23-agent-manager-design.md:297`). It reaches the implement phase as an input. Later phases receive `spec_path` and `plan_path` as "paths in the repo, already committed" (same table, line 294).
- `system.md` holds "the role's standing instructions" (design §8, `docs/superpowers/specs/2026-09-23-agent-manager-design.md:323`).
- Roles are tested as golden briefs. The shipped bundle is loaded through the public loader and its system text is checked (pygents addendum §9, `docs/superpowers/specs/2026-09-25-pygents-engine-design.md:402-403`).
- Test tiers come from design §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:507-529`). A role bundle is a package-file parse with no subprocess, so it belongs in the `unit` tier.
- Precedent for the voice: the coder bundle already states its half of this split, in its `## What you do not commit` section (`src/agent_manager/roles/bundles/coder/system.md:41-46`).

## Scope

Edit exactly these four files and add exactly one instruction to each. Keep every existing line byte-for-byte.

### `src/agent_manager/roles/bundles/spec_author/system.md`

Append one bullet after the last bullet (currently ending at line 14, "inherit."). It uses the same `- ` bullet form and wraps at about 80 columns, like its neighbours:

```markdown
- Never run `git commit`. Writing the spec file is the whole job: the
  workflow's `docs_commit` step commits the spec and the plan together, with
  the `Plan-Hash` trailer that ties them to the finished plan. The commit steps
  in the writing-plans format are for the engineer who executes the plan, not
  for you.
```

### `src/agent_manager/roles/bundles/planner/system.md`

Append one bullet after the last bullet (currently ending at line 13, "Prepend the spec ..."):

```markdown
- Never run `git commit`. Writing the plan file is the whole job: the
  workflow's `docs_commit` step commits the spec and the plan together, with
  the `Plan-Hash` trailer that ties them to the finished plan. The commit at
  the end of each task is a step you write for the engineer, not one you run.
```

The last sentence is required. The existing bullet on lines 6-8 ("a commit at the end of each task") and the vendored `methodology/writing-plans.md:10,139` ("Frequent commits", `git commit -m ...`) both talk about commits. They describe the content of the plan, not what the planner does, and the new bullet must not read as contradicting them. Do not edit the vendored methodology files.

### `src/agent_manager/roles/bundles/spec_critic/system.md`

Insert one prose paragraph between the "Verify every suspicion ... Fold every CONFIRMED fix ..." paragraph (line 14) and the "Calibration:" paragraph (line 16). Separate it with blank lines and write it as a single unwrapped line, matching the file's existing paragraph style:

```markdown
Never run `git commit`. Folding fixes into the spec file is the whole job: the workflow's `docs_commit` step commits the spec and the plan together, with the `Plan-Hash` trailer that ties them to the finished plan.
```

### `src/agent_manager/roles/bundles/plan_critic/system.md`

Insert one prose paragraph between the "Fold every CONFIRMED fix directly into the plan file ..." paragraph (line 17) and the "Return blockers=true only ..." line (line 19). Use the same blank-line separation and the same single-line style:

```markdown
Never run `git commit`. Folding fixes into the plan file is the whole job: the workflow's `docs_commit` step commits the spec and the plan together, with the `Plan-Hash` trailer that ties them to the finished plan.
```

### Literal strings every one of the four texts must contain

These literals are what the new test pins. Each must appear byte-for-byte:

- ``Never run `git commit` `` (with the backticks)
- `docs_commit`
- `Plan-Hash`

## Observable behaviour

- `load_role(r).system`, for each `r` in `spec_author`, `planner`, `spec_critic`, `plan_critic`, contains the three literals above.
- The brief that `prompt.compose_brief` renders for the `spec`, `plan`, `validate_spec` and `validate_plan` phases therefore carries the instruction, because the system text opens every brief. Nothing else in the brief changes.
- Everything that holds today still holds:
  - `load_role` succeeds for all four roles. `system.md` stays non-empty UTF-8 (`roles/loader.py:181-185`).
  - Every methodology filename in `VENDORED.lock` still appears in the system text: `writing-plans.md` for `spec_author` and `planner` (`tests/roles/test_loader.py:523-537`).
  - The critic golden briefs keep their needles: "Verify every suspicion", "Fold every CONFIRMED fix", "blockers=true only", and each role's `CRITERIA` (`tests/roles/test_critic_briefs.py:17-42`).
  - Neither critic text contains `## Result contract`, ` ```json `, a line starting with `# phase:` or `# role:`, `${`, or `task-pipeline: validated` (`tests/roles/test_critic_briefs.py:45-68`). The new paragraphs contain none of these.
  - `policy.toml` is unchanged in all four bundles. In particular, `allowed_tools` stays `["Read", "Grep", "Glob", "Write"]` for spec_author and planner, and `["Read", "Grep", "Glob"]` for the critics. `test_critic_policy_is_the_generic_critic_policy_unchanged` pins the critics' value.

### Error paths

There is no runtime error path, because this card adds no code. The only failures it can cause happen at test time:

- An edit that partially overwrites a pinned needle (for example, splitting "Fold every CONFIRMED fix" across a rewrapped line) makes `test_critic_brief` fail.
- An edit that leaves a bundle's text without one of the three new literals makes the new test fail, and the failure names both the role and the literal.

## Tests

All new and existing tests here are in the `unit` tier. They load package files through `agent_manager.roles.loader.load_role` and spawn no subprocess, git or harness (design §14 table, `docs/superpowers/specs/2026-09-23-agent-manager-design.md:519`; pygents addendum §9 "roles: golden briefs", lines 402-403). They are placed under `tests/roles/`, which mirrors `src/agent_manager/roles/` per CLAUDE.md. `tests/conftest.py` does not auto-mark that directory, so they stay unmarked.

New file: `tests/roles/test_document_briefs.py`, with a module docstring that cites this card and the golden-brief placement.

- `test_document_roles_do_not_commit[spec_author|planner|spec_critic|plan_critic]` (unit, golden brief). Parametrized over the four roles. Asserts that `load_role(role).system` contains each of ``Never run `git commit` ``, `docs_commit` and `Plan-Hash`, using `assert needle in text, (role, needle)` so a failure names both.
- `test_planner_keeps_the_commit_step_it_writes_for_the_engineer` (unit, golden brief). Asserts that the planner text still contains "a commit at the" (the existing bullet's wording; `planner/system.md:7-8` wraps it across the line break as "a commit at the" / "end of each task", so the needle must not span that break) and also contains "not one you run". Together these pin the distinction between "you write a commit step" and "you do not commit".

Existing tests that must stay green unchanged (no edits to them):

- `tests/roles/test_critic_briefs.py` (unit): every test.
- `tests/roles/test_loader.py` (unit): the shipped-roles, default-model and methodology-filename checks.
- `tests/test_prompt.py` (unit): brief rendering. If any test pins a full rendered brief for one of these four roles, update only the expected text to include the new instruction. A grep for `spec_author|planner|spec_critic|plan_critic` in that file before editing settles whether any test does.

## Out of scope

- Engine enforcement that the agent did not commit, for example a gate that checks no new commit appeared after the spec or plan phase, or a hook that blocks `git commit`. That is the next card in the story ("the belt").
- Any `policy.toml` change. None of these four bundles grants `Bash` today, so the instruction is best-effort text on top of a tool list that already does not offer the command. Do not add or remove tools.
- The vendored `methodology/writing-plans.md` copies and `VENDORED.lock`.
- The coder, reviewer, explorer and resolver bundles. The coder's `## What you do not commit` section stays as it is.
- `steps/docs_commit.py`, `workflow/task.py`, and any change to commit subject, trailer or ordering.

## Verification

`uv run pytest` passes, with the whole default (`unit` + `git`) suite green. There is no typecheck or lint command.

---

# Spec and Plan Bundles Do Not Commit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one "never run `git commit`" instruction to each of the `spec_author`, `planner`, `spec_critic` and `plan_critic` role bundles' `system.md`, pinned by a new golden-brief test.

**Architecture:** Text-only change to four package data files under `src/agent_manager/roles/bundles/`. A new unit-tier test file, `tests/roles/test_document_briefs.py`, loads each shipped bundle through the public `load_role` and asserts the new literals are present (RED first, GREEN after the edits). No Python source changes.

**Tech Stack:** Markdown role bundles; pytest via `uv run pytest`.

**Spec:** `docs/superpowers/specs/spec-and-plan-bundles-c06c167b.md` (prepended above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m18/task-spec-and-plan-bundles-c06c167b`. Run every command from that directory, on branch `m18/task-spec-and-plan-bundles-c06c167b`.

## Global Constraints

- Edit exactly four bundle files: `src/agent_manager/roles/bundles/{spec_author,planner,spec_critic,plan_critic}/system.md`. Add exactly one instruction to each. Keep every existing line byte-for-byte (the diff for each file must show only added lines).
- Each new instruction contains, byte-for-byte: ``Never run `git commit` `` (with backticks), `docs_commit`, `Plan-Hash`.
- `spec_author` and `planner`: a `- ` bullet appended after the last bullet, wrapped at about 80 columns with two-space continuation indent.
- `spec_critic` and `plan_critic`: one unwrapped single-line paragraph, separated by blank lines, at the position the spec names.
- No `policy.toml` change in any bundle; no change to the vendored `methodology/writing-plans.md` copies or `VENDORED.lock`; no change to the coder, reviewer, explorer or resolver bundles; no change to `steps/docs_commit.py` or `workflow/task.py`.
- No edits to `tests/roles/test_critic_briefs.py`, `tests/roles/test_loader.py` or `tests/test_prompt.py`. (Checked while planning: `tests/test_prompt.py` builds synthetic roles via `_role(...)` and pins no shipped system text for these four roles, so it needs no update.)
- New tests are unmarked (`unit` tier): they only parse package files through `load_role`, no subprocess.

## Review Focus

- The `spec_author` bullet must keep the "commit steps are for the engineer" distinction, the same way the planner's does: a spec author reading "Never run `git commit`" next to the writing-plans methodology's "Frequent commits" should know the commit steps are content, not actions. The spec only pins this for the planner; Task 1 adds `test_spec_author_leaves_the_commit_steps_to_the_engineer`.
- An edit that rewrites or rewraps an existing line (editor reflow, trailing-whitespace strip, lost final newline): an agent's standing instructions should not shift silently. Task 1 Step 7 checks `git diff --numstat` shows 0 deleted lines for all four files.
- A critic paragraph accidentally wrapped across lines would still pass the literal checks only if no needle spans the break; the critics' file style is one line per paragraph. Task 1 adds `test_critic_do_not_commit_paragraph_is_one_line`, which asserts the full sentence is a single line of the file.

(Only three uncovered failure modes were found: everything else the spec implies — forbidden brief-structure literals, the critics' `policy.toml`, methodology filenames, critic needles — is already pinned by existing tests that must stay green.)

---

### Task 1: Pin and add the "do not commit" instruction in the four document-role bundles

**Files:**
- Create: `tests/roles/test_document_briefs.py`
- Modify: `src/agent_manager/roles/bundles/spec_author/system.md:14` (append after)
- Modify: `src/agent_manager/roles/bundles/planner/system.md:13` (append after)
- Modify: `src/agent_manager/roles/bundles/spec_critic/system.md:14-16` (insert between)
- Modify: `src/agent_manager/roles/bundles/plan_critic/system.md:17-19` (insert between)

**Interfaces:**
- Consumes: `agent_manager.roles.loader.load_role(name: str, *, root: Path | None = None) -> RoleBundle`; `RoleBundle.system: str` is the full `system.md` text.
- Produces: nothing other tasks use.

- [ ] **Step 1: Write the failing tests**

Create `tests/roles/test_document_briefs.py` with exactly:

```python
"""Golden briefs for the four document roles: they never commit (card c06c167b).

Placement: pygents-engine-design.md §9 (lines 402-403) tests roles as golden
briefs -- the shipped bundle is loaded through the public loader and its
system text is checked. The spec and the plan are committed by the workflow's
`docs_commit` step, so the roles that write or edit them must say they never
run `git commit`. No model call, no git repository, no harness: unit tier,
same as `test_critic_briefs.py` and `test_loader.py`.
"""

import pytest

from agent_manager.roles.loader import load_role

DOCUMENT_ROLES = ("spec_author", "planner", "spec_critic", "plan_critic")

DO_NOT_COMMIT = ("Never run `git commit`", "docs_commit", "Plan-Hash")

CRITIC_PARAGRAPH = {
    "spec_critic": (
        "Never run `git commit`. Folding fixes into the spec file is the whole"
        " job: the workflow's `docs_commit` step commits the spec and the plan"
        " together, with the `Plan-Hash` trailer that ties them to the finished"
        " plan."
    ),
    "plan_critic": (
        "Never run `git commit`. Folding fixes into the plan file is the whole"
        " job: the workflow's `docs_commit` step commits the spec and the plan"
        " together, with the `Plan-Hash` trailer that ties them to the finished"
        " plan."
    ),
}


@pytest.mark.parametrize("role", DOCUMENT_ROLES)
def test_document_roles_do_not_commit(role):
    text = load_role(role).system
    for needle in DO_NOT_COMMIT:
        assert needle in text, (role, needle)


def test_planner_keeps_the_commit_step_it_writes_for_the_engineer():
    # planner/system.md wraps the existing bullet as "a commit at the" /
    # "end of each task", so the needle stops before the line break.
    text = load_role("planner").system
    assert "a commit at the" in text
    assert "not one you run" in text


def test_spec_author_leaves_the_commit_steps_to_the_engineer():
    text = load_role("spec_author").system
    assert "are for the engineer who executes the plan" in text


@pytest.mark.parametrize("role", sorted(CRITIC_PARAGRAPH))
def test_critic_do_not_commit_paragraph_is_one_line(role):
    lines = load_role(role).system.splitlines()
    assert CRITIC_PARAGRAPH[role] in lines, role
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/roles/test_document_briefs.py -v`
Expected: FAIL — 4 × `test_document_roles_do_not_commit` with `AssertionError: ('<role>', 'Never run `git commit`')`, `test_planner_keeps_the_commit_step_it_writes_for_the_engineer` failing on `"not one you run"`, `test_spec_author_leaves_the_commit_steps_to_the_engineer` failing, and 2 × `test_critic_do_not_commit_paragraph_is_one_line` failing. 8 failed in total.

- [ ] **Step 3: Append the bullet to `spec_author/system.md`**

The file currently ends with (line 13-14, final newline present):

```markdown
- Cite the parent design spec by section and line for every constraint you
  inherit.
```

Use the Edit tool on `src/agent_manager/roles/bundles/spec_author/system.md`, replacing `old_string`:

```markdown
- Cite the parent design spec by section and line for every constraint you
  inherit.
```

with `new_string`:

```markdown
- Cite the parent design spec by section and line for every constraint you
  inherit.
- Never run `git commit`. Writing the spec file is the whole job: the
  workflow's `docs_commit` step commits the spec and the plan together, with
  the `Plan-Hash` trailer that ties them to the finished plan. The commit steps
  in the writing-plans format are for the engineer who executes the plan, not
  for you.
```

The file must still end with a single trailing newline after `  for you.`.

- [ ] **Step 4: Append the bullet to `planner/system.md`**

Use the Edit tool on `src/agent_manager/roles/bundles/planner/system.md`, replacing `old_string`:

```markdown
- Prepend the spec to the plan, then check the plan back against it.
```

with `new_string`:

```markdown
- Prepend the spec to the plan, then check the plan back against it.
- Never run `git commit`. Writing the plan file is the whole job: the
  workflow's `docs_commit` step commits the spec and the plan together, with
  the `Plan-Hash` trailer that ties them to the finished plan. The commit at
  the end of each task is a step you write for the engineer, not one you run.
```

Do not touch lines 6-8 ("... a commit at the" / "end of each task.").

- [ ] **Step 5: Insert the paragraph into `spec_critic/system.md`**

Use the Edit tool on `src/agent_manager/roles/bundles/spec_critic/system.md`, replacing `old_string` (the blank line 15 and the whole line-16 paragraph, so line 14 stays untouched):

```markdown

Calibration: only flag what would cause a real problem when planning or implementing. Minor wording and stylistic preference are not issues; this stage gates a run.
```

with `new_string`:

```markdown

Never run `git commit`. Folding fixes into the spec file is the whole job: the workflow's `docs_commit` step commits the spec and the plan together, with the `Plan-Hash` trailer that ties them to the finished plan.

Calibration: only flag what would cause a real problem when planning or implementing. Minor wording and stylistic preference are not issues; this stage gates a run.
```

The new paragraph is a single line (no wrapping), with one blank line before it and one after it.

- [ ] **Step 6: Insert the paragraph into `plan_critic/system.md`**

Use the Edit tool on `src/agent_manager/roles/bundles/plan_critic/system.md`, replacing `old_string`:

```markdown

Return blockers=true only if something unresolvable remains (spec contradiction needing a human decision) with the reason.
```

with `new_string`:

```markdown

Never run `git commit`. Folding fixes into the plan file is the whole job: the workflow's `docs_commit` step commits the spec and the plan together, with the `Plan-Hash` trailer that ties them to the finished plan.

Return blockers=true only if something unresolvable remains (spec contradiction needing a human decision) with the reason.
```

The new paragraph is a single line, between the "Fold every CONFIRMED fix directly into the plan file ..." paragraph and the "Return blockers=true only ..." line, with one blank line on each side.

- [ ] **Step 7: Check the edits only added lines**

Run: `git diff --numstat -- src/agent_manager/roles/bundles/`
Expected exactly (added, deleted, path):

```
2	0	src/agent_manager/roles/bundles/plan_critic/system.md
4	0	src/agent_manager/roles/bundles/planner/system.md
5	0	src/agent_manager/roles/bundles/spec_author/system.md
2	0	src/agent_manager/roles/bundles/spec_critic/system.md
```

Any non-zero second column means an existing line changed (reflow, whitespace strip, lost final newline) — undo that change. Any other path listed (a `policy.toml`, a `methodology/` file, another bundle) must be reverted with `git checkout -- <path>`.

- [ ] **Step 8: Run the new and existing role tests to verify they pass**

Run: `uv run pytest tests/roles/ -v`
Expected: PASS — all 8 tests in `tests/roles/test_document_briefs.py`, plus every test in `tests/roles/test_critic_briefs.py` and `tests/roles/test_loader.py`, with no edits to the latter two.

- [ ] **Step 9: Run the default suite**

Run: `uv run pytest`
Expected: PASS — the whole default (`unit` + `git`) suite green, no failures or errors. There is no lint or typecheck command.

- [ ] **Step 10: Commit**

```bash
git add tests/roles/test_document_briefs.py \
  src/agent_manager/roles/bundles/spec_author/system.md \
  src/agent_manager/roles/bundles/planner/system.md \
  src/agent_manager/roles/bundles/spec_critic/system.md \
  src/agent_manager/roles/bundles/plan_critic/system.md
git commit -m "feat: document-role bundles say docs_commit commits, never the agent (c06c167b)"
```
<!-- task-pipeline: validated -->
