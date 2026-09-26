<!-- task-pipeline: validated -->
# Subtask e46098be: Document the pygents engine

Card `e46098be-302a-4d4b-811b-3abbceaebb66`, under story 1ec08da2 "Switch over". This is Task 5.4 of `docs/superpowers/plans/2026-09-25-pygents-engine.md` (Interfaces block, lines 1353-1364). The milestone's source of truth is `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (the "pygents addendum"). This card only documents. It follows the doc-only precedent of card 4c78e3ea ("Document Integrate").

## Scope

Files that may change: `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. No other file changes. That means no source, test, plan or addendum file.

Write from the code as built, never from a spec. Before writing, read `src/agent_manager/runtime/engine.py`, `src/agent_manager/runtime/checkpoint.py`, `src/agent_manager/workflow/task.py`, and `cli.py`'s `resume_run`, `select_resumable`, `checkpoint_resume_phase` and `orphan_attempts`. Every sentence added must be true of that code.

Out of scope:
- Sibling cards own the following, and this card must not touch them: wiring the critic loops (058981d3), the real-harness test (a300ab2b), and the move into `runtime/` or removal of `--engine` (7a744199).
- The addendum's §11 deferred items: the supervisor tree/orchestrator milestone, exactly-once phases, benchmarking rewritten prompts, and upstream pygents fixes.

## Observable changes

### 1. README, "Relaunching resumes" section (`README.md` around lines 260-270)

The current `am resume` paragraph says a run with a stopped subtask "is refused with exit code 3". That is no longer true, so rewrite the paragraph to state the following. The same stale claim also appears earlier, in the "Parallel runs" section (`README.md` around lines 205-210, in the paragraph starting "To continue, fix the escalation and relaunch..."): fix that occurrence the same way, so the README does not contradict itself in one place while it is corrected in another.
- `am resume <run-id>` continues a stopped (parked) or killed subtask from its newest checkpoint. It no longer refuses stopped subtasks.
- It resumes at the interrupted phase. Nothing before that phase re-runs. Attempts left recorded `started` with no terminal event are marked `harness_error`.
- If the workflow changed since the checkpoint was saved (digest mismatch), resume refuses before anything runs and exits 3 with an error envelope.
- A stopped walk exits 0, and an escalated one exits 1.
- It drives exactly one subtask. When more than one subtask is in flight (a milestone-shaped run), it is still refused with exit 3, and relaunching `am run --milestone` stays the way to continue a milestone.

Optional, only if it is stated accurately: the other refusals in `checkpoint_resume_phase`. These are: no checkpoint, a newest checkpoint of `done`, and a checkpoint left by a phase escalation.

### 2. README, "What an escalation report contains" section

Add one sentence: when a critic (`validate_spec` / `validate_plan`) returns blockers, the subtask gets one revision of the spec or plan with that feedback. A second blocker escalates at that critic's phase. `review` has no revision loop. The escalation payload shape is unchanged.

### 3. README, "Not there yet" section

Keep the bullet "`am resume` is not milestone-aware." `select_resumable` still refuses more than one in-flight subtask. Leave the other bullets and the deferred-links paragraph alone.

### 4. Main design doc pointer (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`)

Add one short paragraph to §6 "The phase contract", to §9 "State, durability and resume", or to both (the same paragraph repeated or cross-referenced). It says the following:
- These sections describe the original yaml-engine phase and resume model.
- The pygents engine replaced that model.
- The pygents addendum is authoritative for phases (§5 "Phase model and compiler") and for checkpoints and resume (§6 "Checkpoints and resume").

The paragraph should link `2026-09-25-pygents-engine-design.md` with section anchors. Nothing else in the main doc changes.

### Facts to state accurately, if mentioned

- Checkpoints are `Agent.to_dict()` snapshots, written to the store's `checkpoints` table at `BEFORE_TURN`.
- Reasons: a normal turn writes `"turn"`. When the cooperative stop is set, the turn writes `"parked"` and raises `Parked`. After a run finishes, `runtime/engine.py` writes `"done"` or `"escalated"`.
- Nothing is ever checkpointed at `AFTER_TURN`.
- `agent.run()` is always consumed to the end.
- Hooks are module-level, never closures.
- SubtaskSummary, journal lines, phase/attempt rows and escalation payloads are unchanged (G10).

Style: no hard-wrapped prose in new paragraphs. Match the README's existing voice.

## Error paths

None at runtime, because this card changes no code. The documentation risk is a claim the code contradicts. Treat each of these as a defect:
- saying `am resume` is milestone-aware
- saying the mismatch exits anything other than 3
- saying critics loop more than once
- saying `review` loops
- saying earlier phases re-run on resume

## Tests

None are added. Under the test-placement rule (main design spec §14, refined by pygents addendum §9), tiers exist for pure functions, steps, adapters, the engine, and one opt-in e2e test. Prose has no tier, and precedent card 4c78e3ea added no test files.

Verification: `uv run pytest` (the whole default suite, including `tests/e2e`) stays green and unchanged. It is run only to confirm that no code file was touched.

Commit message: `docs: document the pygents engine, checkpoints and critic loops`.

---

# Document the pygents engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `README.md` and the main design doc in line with the pygents engine as built: `am resume` continues stopped or killed subtasks from checkpoints, critics get one revision, and the design doc's §6/§9 point at the pygents addendum.

**Architecture:** Documentation only. Every sentence is derived from the code in this worktree (`src/agent_manager/cli.py`, `src/agent_manager/runtime/engine.py`, `src/agent_manager/runtime/checkpoint.py`, `src/agent_manager/runtime/compile.py`, `src/agent_manager/workflow/task.py`), never from a spec or addendum. Each prose change is framed as a RED/GREEN pair: a `grep` that proves the stale text is present (RED), the edit, then a `grep` that proves the stale text is gone and the new text is present (GREEN). No test file is added (spec "Tests": prose has no tier; precedent card 4c78e3ea).

**Tech Stack:** Markdown; `grep` for the RED/GREEN checks; `uv run pytest` for the unchanged-suite check.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-document-the-pygents-e46098be/docs/superpowers/specs/task-document-the-pygents-e46098be-design.md` (prepended above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-document-the-pygents-e46098be`. Run every command from there.

## Global Constraints

- Files that may change: `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. No source, test, plan or addendum file changes.
- Write from the code as built, never from a spec. Every sentence added must be true of that code.
- No hard-wrapped prose in new paragraphs. Match the README's existing voice.
- Keep the "Not there yet" bullet "`am resume` is not milestone-aware." and leave the other bullets and the deferred-links paragraph alone.
- In the main design doc, nothing but the pointer paragraph changes.
- Defects to avoid: saying `am resume` is milestone-aware; saying the mismatch exits anything other than 3; saying critics loop more than once; saying `review` loops; saying earlier phases re-run on resume.
- `uv run pytest` (whole default suite, including `tests/e2e`) stays green and unchanged.
- Commit message: `docs: document the pygents engine, checkpoints and critic loops`.

## Code facts this plan's prose rests on

Checked in this worktree before writing; the executor should re-read them if any sentence below looks doubtful.

- `cli.py:45-49`: `EXIT_ESCALATED = 1`, `EXIT_ERROR = 3`. `cli.py:1420-1427`: any `HANDLED` error prints an `ok: false` envelope and exits 3; a payload whose `status` is `"escalated"` exits 1; anything else (`"done"`, `"stopped"`) exits 0.
- `cli.py:404-449` `select_resumable`: exactly one subtask recorded `started` or `stopped` is resumable; zero or more than one raises `NotResumableError` (exit 3). An `escalated` subtask is not in the resumable set.
- `cli.py:452-468` `orphan_attempts` and `cli.py:1282-1288`: attempts recorded `started` are re-recorded `harness_error` before the walk; the payload lists them as `discarded_attempts` and names the continued phase as `resumed_from` (`cli.py:1327-1330`).
- `cli.py:471-512` `checkpoint_resume_phase`, called before the first write (`cli.py:1280-1281`): refuses, in order, no checkpoint, a newest checkpoint `done`, a digest other than `TASK.digest()` (`CheckpointMismatchError`, "workflow changed since checkpoint", exit 3), and a checkpoint with no pending turn (what a phase escalation leaves).
- `cli.py:1272-1331` `_resume_from_checkpoint`: drives one subtask through `drive_subtask(..., resume_from=checkpoint)` and returns; it starts no later subtask, story, level or Integrate.
- `cli.py:1384-1411`: `am resume` still accepts `--verify` and `--allow-no-verification`, but both "currently ha[ve] no effect: the checkpoint carries the verification suite the run started with".
- `runtime/checkpoint.py:54-64`: the only hook is `BEFORE_TURN`; it saves `parked` and raises `Parked` when the stop is set, else saves `turn`. `runtime/engine.py:172-219`: after `agent.run()` (consumed to the end) it saves `done` or `escalated`; a `BaseException` writes nothing and the last `turn` row stands. So a resume re-runs the interrupted phase from its start, and a phase that finished just before a kill, before the next `BEFORE_TURN` checkpoint was written, runs again (pygents addendum §6 "Known limit": phases are at-least-once).
- `workflow/task.py:69-94`: `validate_spec` has `on_fail=Goto("spec")`, `validate_plan` has `on_fail=Goto("plan")`; `workflow/phases.py:31` `Goto.max_loops = 1`. `review` (`task.py:105-116`) has no `on_fail`. `runtime/compile.py:146-153`: on the first failure the critic's `detail` is passed as feedback to the looped-to phase; on the second, `Escalated(phase, ...)` is raised with `phase` the critic's own name, so `failed_phase` is `validate_spec` or `validate_plan`.
- Pygents addendum heading anchors (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:175` and `:262`): `## 5. Phase model and compiler` -> `#5-phase-model-and-compiler`, `## 6. Checkpoints and resume` -> `#6-checkpoints-and-resume`.
- The addendum's §6 bullet "After an escalation it re-runs the failed phase with the loop count it had" is NOT what the code does: `select_resumable` never picks an `escalated` subtask and `checkpoint_resume_phase` refuses an escalation checkpoint. Do not copy that claim into the README.

## Review Focus

- A milestone run left with one escalated subtask and exactly one parked subtask: `select_resumable` picks the parked one, so `am resume` does run there. The README must say resume refuses when more than one subtask is recorded `started` or `stopped`, not that it refuses every milestone run, and must say it drives only that one subtask (no later subtask, story, level or Integrate). Checked in Task 1 Step 6.
- An operator passing a different `--verify` to `am resume` expecting it to be used: the README must say it is accepted but has no effect (the Usage paragraph currently says the opposite). Checked in Task 1 Step 6.
- An operator trying `am resume` after a phase escalation (the addendum wrongly suggests this re-runs the failed phase): the README must list it as a refusal with exit 3. Checked in Task 1 Step 6.
- A reader expecting a critic to loop until satisfied: the README must say exactly one revision, with `failed_phase` naming the critic, and that `review` never loops. Checked in Task 2 Step 4.
- A broken anchor in the design-doc pointer sends readers nowhere: the anchors must match the addendum's headings exactly. Checked in Task 3 Step 4.

---

### Task 1: README, `am resume` prose (Usage, Parallel runs, Relaunching resumes)

**Files:**
- Modify: `README.md:25-32` (Usage, the `am resume` paragraph and its example)
- Modify: `README.md:204-210` (Parallel runs, "To continue, fix the escalation..." paragraph)
- Modify: `README.md:268-270` (Relaunching resumes, the `am resume` paragraph)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the README anchor `#relaunching-resumes` (existing heading, unchanged), which the new Usage paragraph links to.

The Usage paragraph is not in the spec's enumerated list, but it sits in `README.md` (an allowed file) and makes the same kind of now-false claim the spec orders fixed in "Parallel runs" ("The verification suite is not recorded, so a resume is told it the same way a fresh run was", contradicted by `cli.py:1391-1401`). It is fixed here for the spec's own reason: so the README does not contradict itself.

- [ ] **Step 1: RED, prove the stale claims are present**

Run:

```bash
grep -n "does not continue stopped work" README.md
grep -n "On a run with a stopped subtask it is refused" README.md
grep -n "so a resume is told" README.md
```

Expected: three matches, at lines 207, 269 and 27 respectively. If any command prints nothing, stop and re-read `README.md` before editing: the text moved.

- [ ] **Step 2: Rewrite the Usage paragraph**

In `README.md`, replace exactly this text:

````markdown
Pick a killed run back up at the phase it died in. There is no `--base-branch`
and no `--branch-prefix` here: both were decided when the run started and are
recorded on the run. The verification suite is not recorded, so a resume is told
it the same way a fresh run was:

```bash
am resume 20260923T140506Z-19efcddc --verify "uv run pytest"
```
````

with:

````markdown
Pick a stopped or killed subtask back up at the phase it was interrupted in. There is no `--base-branch` and no `--branch-prefix` here: both were decided when the run started and are recorded on the run. `--verify` and `--allow-no-verification` are still accepted but have no effect: the checkpoint carries the verification suite and the opt-out the run started with. See [Relaunching resumes](#relaunching-resumes) for what a resume does and when it is refused.

```bash
am resume 20260923T140506Z-19efcddc
```
````

- [ ] **Step 3: Rewrite the Parallel runs paragraph**

In `README.md`, replace exactly this text:

```markdown
To continue, fix the escalation and relaunch the same `am run --milestone`
command (see [Relaunching resumes](#relaunching-resumes)). The stopped subtask
picks up where it parked, and every card already `done` on the board is
skipped. `am resume <run-id>` does not continue stopped work: on a run with a
stopped subtask it is refused with `{"ok": false, "error": {...}}` and exit
code 3, and the message names the stopped subtasks and says to relaunch the
milestone command.
```

with:

```markdown
To continue, fix the escalation and relaunch the same `am run --milestone` command (see [Relaunching resumes](#relaunching-resumes)). The stopped subtask picks up where it parked, and every card already `done` on the board is skipped. `am resume <run-id>` is not the way to continue a milestone. It drives exactly one subtask and nothing after it: a run with more than one subtask recorded `started` or `stopped` is refused with `{"ok": false, "error": {...}}` and exit code 3, and a resume never starts a later subtask, story, level or Integrate.
```

- [ ] **Step 4: Rewrite the Relaunching resumes `am resume` paragraph**

In `README.md`, replace exactly this text:

```markdown
`am resume <run-id>` is not milestone-aware and does not continue a milestone.
On a run with a stopped subtask it is refused with exit code 3, and its message
says to relaunch. Relaunch the `am run --milestone` command instead.
```

with:

```markdown
`am resume <run-id>` continues one stopped (parked) or killed subtask from its newest checkpoint. It no longer refuses a stopped subtask. A checkpoint is saved before every phase runs, so the walk goes on at the interrupted phase, which runs again from its start, and nothing before that phase re-runs. One exception: a phase that finished just before the process was killed, before the next checkpoint was saved, runs again too, because phases are at-least-once. Attempts left recorded `started` with no terminal event are marked `harness_error` first. `data` names the phase the walk continued at as `resumed_from` and lists the marked attempts as `discarded_attempts`. A resumed walk that ends `done` or `stopped` exits 0, and one that escalates exits 1.

Resume refuses before anything runs, with `{"ok": false, "error": {...}}` and exit code 3, when:

- the workflow changed since the checkpoint was saved (its digest no longer matches). Start a fresh `am run --card`.
- the subtask has no checkpoint (the run died before its first turn, or it predates checkpoints), its newest checkpoint is `done`, or its newest checkpoint was left by a phase escalation. An escalated subtask is never resumed.
- the run has no subtask recorded `started` or `stopped`, or more than one of them (a milestone-shaped run).

`am resume` is not milestone-aware: it drives that one subtask and stops, and never runs a later subtask, story, level or Integrate. To continue a milestone, relaunch the `am run --milestone` command.
```

- [ ] **Step 5: GREEN, prove the stale claims are gone**

Run:

```bash
grep -n "does not continue stopped work" README.md
grep -n "On a run with a stopped subtask it is refused" README.md
grep -n "so a resume is told" README.md
```

Expected: no output from any of the three.

- [ ] **Step 6: GREEN, prove the Review Focus facts are stated**

Run:

```bash
grep -c "more than one subtask recorded \`started\` or \`stopped\`" README.md
grep -c "still accepted but have no effect" README.md
grep -c "left by a phase escalation" README.md
grep -c "It no longer refuses a stopped subtask" README.md
grep -n "milestone-aware" README.md
```

Expected: `1`, `1`, `1`, `1`, and the last command prints exactly two lines: the new "`am resume` is not milestone-aware: it drives that one subtask" sentence in Relaunching resumes, and the untouched "Not there yet" bullet "- `am resume` is not milestone-aware." Neither line says resume *is* milestone-aware.

- [ ] **Step 7: Confirm only README.md changed so far**

Run: `git status --porcelain -- . ':!docs/superpowers/specs/task-document-the-pygents-e46098be-design.md' ':!docs/superpowers/plans/task-document-the-pygents-e46098be.md'`
Expected: exactly ` M README.md`.

---

### Task 2: README, critic revision loop in "What an escalation report contains"; "Not there yet" untouched

**Files:**
- Modify: `README.md` (the "What an escalation report contains" paragraph ending "and review never runs.", originally lines 237-238; Task 1 shifted line numbers, so match by text)

**Interfaces:**
- Consumes: nothing from Task 1 (independent text).
- Produces: nothing other tasks rely on.

- [ ] **Step 1: RED, prove the critic loop is undocumented**

Run: `grep -c "one revision" README.md`
Expected: `0`.

- [ ] **Step 2: Add the critic-loop sentences**

In `README.md`, replace exactly this text:

```markdown
plan. A coder that reports `blocked` ends its subtask escalated at `implement`,
and review never runs.
```

with:

```markdown
plan. A coder that reports `blocked` ends its subtask escalated at `implement`,
and review never runs.

When a critic, `validate_spec` or `validate_plan`, reports blockers, the subtask gets one revision: `spec` (or `plan`) runs again with the critic's reason as feedback, and then the critic runs again. A second block escalates at that critic's phase, so `failed_phase` is `validate_spec` or `validate_plan`. `review` has no revision loop. The report's shape is the same either way.
```

- [ ] **Step 3: Leave "Not there yet" alone**

Do not edit the "#### Not there yet" section. Its three bullets (including "- `am resume` is not milestone-aware.") and the deferred-links paragraph stay exactly as they are: `select_resumable` (`src/agent_manager/cli.py:404-449`) still refuses more than one in-flight subtask.

- [ ] **Step 4: GREEN, prove the loop is stated and bounded**

Run:

```bash
grep -c "the subtask gets one revision" README.md
grep -c "\`review\` has no revision loop" README.md
grep -n "^- \`am resume\` is not milestone-aware.$" README.md
grep -n "^- \`watch\`, \`retry\` and \`cancel\` do not exist.$" README.md
```

Expected: `1`, `1`, one line, one line. Also read the new paragraph once and confirm it never says a critic loops more than once and never says `review` loops.

---

### Task 3: Design doc pointer in §6 and §9

**Files:**
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:256` (insert after the `## 6. The phase contract` heading)
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:348` (insert after the `## 9. State, durability and resume` heading)

**Interfaces:**
- Consumes: the addendum headings `## 5. Phase model and compiler` and `## 6. Checkpoints and resume` in `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (not modified).
- Produces: nothing other tasks rely on.

- [ ] **Step 1: RED, prove there is no pointer yet**

Run: `grep -c "2026-09-25-pygents-engine-design.md" docs/superpowers/specs/2026-09-23-agent-manager-design.md`
Expected: `0`.

- [ ] **Step 2: Insert the pointer under §6**

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, replace exactly this text:

```markdown
## 6. The phase contract

**Deterministic phase.**
```

with:

```markdown
## 6. The phase contract

> **Superseded by the pygents engine.** This section and §9 describe the original yaml-engine phase and resume model. The pygents engine replaced that model. For phases, the [pygents addendum's §5 "Phase model and compiler"](2026-09-25-pygents-engine-design.md#5-phase-model-and-compiler) is authoritative, and for checkpoints and resume, its [§6 "Checkpoints and resume"](2026-09-25-pygents-engine-design.md#6-checkpoints-and-resume) is.

**Deterministic phase.**
```

- [ ] **Step 3: Insert the same pointer under §9**

In the same file, replace exactly this text:

````markdown
## 9. State, durability and resume

```
Run
````

with:

````markdown
## 9. State, durability and resume

> **Superseded by the pygents engine.** This section and §6 describe the original yaml-engine phase and resume model. The pygents engine replaced that model. For phases, the [pygents addendum's §5 "Phase model and compiler"](2026-09-25-pygents-engine-design.md#5-phase-model-and-compiler) is authoritative, and for checkpoints and resume, its [§6 "Checkpoints and resume"](2026-09-25-pygents-engine-design.md#6-checkpoints-and-resume) is.

```
Run
````

- [ ] **Step 4: GREEN, prove the pointers exist and their anchors resolve**

Run:

```bash
grep -c "Superseded by the pygents engine" docs/superpowers/specs/2026-09-23-agent-manager-design.md
grep -n "^## 5. Phase model and compiler$" docs/superpowers/specs/2026-09-25-pygents-engine-design.md
grep -n "^## 6. Checkpoints and resume$" docs/superpowers/specs/2026-09-25-pygents-engine-design.md
git diff --stat -- docs/superpowers/specs/2026-09-23-agent-manager-design.md
```

Expected: `2`; one line at 175; one line at 262 (so the anchors `#5-phase-model-and-compiler` and `#6-checkpoints-and-resume` match GitHub's slugs of those headings); and the diff stat shows only insertions (`4 insertions(+)`, no deletions) in that one file.

---

### Task 4: Full suite and commit

**Files:**
- No file changes; verification and commit of Tasks 1-3.

**Interfaces:**
- Consumes: the edits of Tasks 1-3.
- Produces: one commit on branch `m6/task-document-the-pygents-e46098be`.

- [ ] **Step 1: Confirm only the two allowed files changed**

Run: `git status --porcelain -- . ':!docs/superpowers/specs/task-document-the-pygents-e46098be-design.md' ':!docs/superpowers/plans/task-document-the-pygents-e46098be.md'`
Expected: exactly two lines, ` M README.md` and ` M docs/superpowers/specs/2026-09-23-agent-manager-design.md`. Anything under `src/` or `tests/` means a stray edit: revert it with `git checkout -- <path>`.

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest`
Expected: PASS, the same pass count as before these edits (no code or test file changed).

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-23-agent-manager-design.md
git commit -m "docs: document the pygents engine, checkpoints and critic loops"
```

Expected: one commit touching exactly those two files (`git show --stat HEAD`).
