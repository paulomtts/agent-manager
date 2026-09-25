<!-- task-pipeline: validated -->
# Document Integrate — subtask design (4c78e3ea)

Card: 4c78e3ea "Document Integrate". Parent story f96f2b53 "Prove it against a real harness, and document it", milestone db5b5a3b. Blocked by dca476c4 (done; it wrote only `tests/e2e/test_real_harness_integrate.py`). This card is docs only.

Governing designs: the Integrate addendum `docs/superpowers/specs/2026-09-25-integrate-design.md` (I1–I7, §4 Acceptance, §5 Limits, §6 Deferred), which extends the orchestration, parallel-stories and main (`2026-09-23-agent-manager-design.md`) specs.

Note: the exploration summary handed to this stage was truncated at 8000 characters, cut off mid-sentence at "Existing README defaults: --max-concurrent 4, --r". The missing text was not guessed. Every fact below was re-read from this worktree's source and README.

## Checkout precondition

Plain master (015d672) has no Integrate. This card's worktree, `.claude/worktrees/m5/task-document-integrate-4c78e3ea`, is stacked on the dca476c4 tip and has the whole implementation: `src/agent_manager/integration.py`, `steps/integrate.py`, the Integrate wiring in `orchestrate.py` and `cli.py`, the `resolver` bundle, and `workflow/builtin/integrate.yaml`. Its `README.md` and main spec are still as on master. If the stage finds `src/agent_manager/integration.py` missing, it must stop and report, not write docs.

## Scope

Files touched: `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§12 only). No file under `src/` or `tests/` changes. Do not touch `tests/e2e`: the sibling owns it.

### 1. README.md: new `#### Integrate` subsection (under "Milestone runs", after "Parallel runs" or after "What a clean run leaves behind")

Cover, from the source in this worktree:

- **When it runs.** After the last level, and only when every level finished clean. It also runs when nothing was left to drive (orchestrate.py ~603-609), so it runs on every relaunch.
- **Where.** One local branch, `<prefix>-integrate` (`integration.integration_branch`), in the worktree `<repo>/.claude/worktrees/<prefix>-integrate`.
- **Order.** `integration.merge_order`: levels from `dag.compute_integrate_levels` over every story in the milestone, done or not, and census order within a level. A story with no subtasks is skipped because it has no branch of its own.
- **Clean merges dispatch no agent.** Each tip is merged with `git merge --no-ff`. This is idempotent, so a tip that is already merged is a no-op.
- **Conflicts.** The merge is left in progress and a resolver agent is dispatched: role `resolver`, workflow `builtin/integrate.yaml`, with an agent phase `resolve` and then a deterministic `verify`. The resolver runs as a synthetic story titled "Integrate" with one synthetic subtask per conflicting tip, so `am status <run_id>` shows it. Git judges the resolver, not its `resolved` flag. `merge_completed_gate` needs no `MERGE_HEAD`, a clean tree, and no conflict markers in the touched files. `resolve` retries up to 2 attempts on `gate_failed`/`schema_invalid`.
- **Final verification (I4).** After the last merge, the `--verify` commands run once on the integrated branch. `--allow-no-verification` skips this, and the verification gate applies as elsewhere. A textually clean merge that breaks the suite escalates here.
- **What the run reports.** Described in the "clean run" and "escalation report" edits below.
- **What the human does next.** Review `<prefix>-integrate`, then merge it into the base branch yourself (for example, from a checkout of the base branch, `git merge m3-integrate`). State plainly that `am` never merges into the base branch and never pushes (rule 4, I5).
- **After an Integrate escalation.** Open the integration worktree, finish or fix the merge, and commit it. Then relaunch the same `am run --milestone` command. Committing first is required: `merge_tip` raises `MergeInProgressError` on a merge already in progress, and Integrate escalates again rather than touch it.
- **Limits, stated plainly (addendum §5).** (a) A resolver can misjudge what two edits meant even when the result compiles and passes. The final verification narrows that risk but does not remove it, so a human still reviews the branch. (b) Merges are sequential because there is one working tree. (c) Hundreds of stories means hundreds of sequential merges, and nothing batches them.

### 2. README.md: update earlier sections that say Integrate is missing

- **Preview with `--dry-run`:** `data` gains `integrate`: `{"branch", "worktree", "order"}`, where `order` is a list of `{"story", "tip"}` in merge order (cli.py ~931-945). The preview still writes nothing and creates no integration branch or worktree.
- **What a clean run leaves behind:** add `integrated`, `{"branch", "worktree", "merged", "resolved"}`, where `merged` and `resolved` are story ids in merge order (orchestrate.py `integrated_payload`). Say that `done: true` now comes only after Integrate succeeded. Keep `tips`, since those are the story branches that were merged into `integrated.branch`. Replace "Nothing is merged … Merging the tips is left to you" with: the tips are merged into `<prefix>-integrate` only, the base branch does not move, nothing is pushed, and merging the integration branch into the base is left to you.
- **What an escalation report contains:** add the Integrate escalation payload (orchestrate.py `integrate_escalated_payload`): `escalated: true`, `phase: "integrate"`, `story` (the story whose tip was being merged, or `null` when the final verification failed), `files` (the conflicting files, and empty for a merge already in progress or a verification failure), `detail`, `run_id`, `warnings`. It exits 1. The run is recorded `escalated`, and the branch and worktree are left exactly as Integrate left them.
- **Relaunching resumes:** relaunching a finished milestone still runs Integrate. With every tip already merged it is a no-op: no agent is dispatched, `completed` is empty, and `done` is reported with `integrated`. Relaunching after an Integrate escalation retries Integrate once the human has committed.
- **Not there yet:** remove the "There is no Integrate step" bullet. Keep "`am resume` is not milestone-aware" and "`watch`, `retry` and `cancel` do not exist". Add a link to §6 of the Integrate addendum, `docs/superpowers/specs/2026-09-25-integrate-design.md#6-deferred`, next to the existing orchestration §4 and parallel §5 links. Listing the other §6 items (`--no-integrate`, cost capture, the reviewer Plan-Hash brief, marking slow tests, per-story readiness) is optional. If listed, they must be listed as absent. None of them is described as existing anywhere.

### 3. Main spec §12

Inside `## 12. Failure, escalation, and blast radius` (line 445), add one short `**Status:**` paragraph. It says that as of milestone 5, Integrate merges every story tip into a local `<prefix>-integrate` branch after the last level, and an unfinished merge or a failed final verification escalates like any other gate. It also says that the blast-radius guarantee holds: Integrate never touches `main`/`master` or the base branch and never pushes (I5). Change nothing else in the spec.

## Observable behavior / acceptance

- The README describes only behavior present in this worktree's `src/`. It makes no claim that a deferred item would have to make true.
- Every command in the README runs as written against a temporary git repo and a temporary `brd` board, with all temp files in the session scratchpad. Build the repo and board the way `tests/e2e/test_integrate.py` and `tests/e2e/conftest.py` do: a milestone with two independent stories in one level, and one story blocked by another. Check by hand:
  - `am run --milestone … --branch-prefix … --dry-run --pretty` shows `data.integrate` with `branch` `<prefix>-integrate`, the worktree path, and `order` in merge order. It exits 0, and afterwards no `<prefix>-integrate` branch or worktree exists.
  - Real runs, with the fake `claude` from `tests/e2e/fake_claude.py` on `PATH` (never a real harness). (a) A clean milestone reports `done` and `integrated`, `integrated.resolved` is empty, and the base branch ref is unchanged. (b) A relaunch changes nothing. (c) The documented human step, `git merge <prefix>-integrate` from the base checkout, works as written.
  - The Integrate escalation and recovery path (commit in the integration worktree, then relaunch) is exercised as far as the fake claude scenarios in `tests/e2e/test_integrate.py` allow. Any path that cannot be run that way must not be presented as verified output.
  - Existing README commands still run. The `[--max-concurrent N]` synopsis is placeholder syntax and is checked both with and without the option.
- If a command does not run as written, the fix goes in the README, not in `src/`.
- Rule 2: `uv run pytest` (the default suite) stays green.

## Error paths

- The checkout lacks Integrate: stop and report.
- A README command fails as written: fix the README text.
- An implementation fact disagrees with this spec (key name, path, exit code): the source in this worktree wins, and the discrepancy is noted in the hand-off.

## Tests

Test placement follows main spec §14: pure functions get unit tests, steps run against temp repos and a temp board, the engine is driven by a fake adapter, and end-to-end tests are opt-in and excluded from the default suite. A docs-only card adds **no new tests**. Integrate's behavior is already covered by the default-suite tests of the earlier m5 cards and by `tests/e2e/test_integrate.py` (fake claude) and `tests/e2e/test_real_harness_integrate.py` (opt-in, `-m e2e`).

- README command check: a **manual** run against a temp repo and temp board, as listed above. It is not added to any tier. If a later card automates the dry-run `integrate` key check, that belongs in the default suite in `tests/test_cli.py` (CLI/steps tier, temp board, no harness), not in `tests/e2e`.
- Regression: the full default suite, `uv run pytest`, passes unchanged.

---

# Document Integrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document the milestone-5 Integrate step in `README.md` and add a short Status note to §12 of the main spec, with every README command checked by hand against a throwaway repo and board.

**Architecture:** Docs only. No file under `src/` or `tests/` changes, and no test is added (spec "Tests"). Each docs task uses a scratch check script as its RED/GREEN cycle: the script fails on the current text (RED), the edit lands, and the script passes (GREEN). The last task runs every README command by hand against a throwaway git repo plus `brd` board in the session scratchpad, with the fake `claude` from `tests/e2e/fake_claude.py` first on `PATH`, then runs the full default suite.

**Tech Stack:** Markdown, `am` (Typer CLI, console script `am = "agent_manager.cli:app"` in `pyproject.toml`), `brd`, git, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-document-integrate-4c78e3ea/docs/superpowers/specs/task-document-integrate-4c78e3ea-design.md` (prepended above), governed by `docs/superpowers/specs/2026-09-25-integrate-design.md`.

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-document-integrate-4c78e3ea`, branch `m5/task-document-integrate-4c78e3ea`, cut from `m5/task-add-the-opt-in-real-dca476c4`. Every command below runs from that worktree unless a step says `cd "$R"`.

**Scratchpad:** `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad`. If the executing session has a different scratchpad directory, replace this prefix with that session's scratchpad everywhere it appears; never use the repo or plain `/tmp`.

## Global Constraints

- Files touched: `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§12 only). No file under `src/` or `tests/` changes. Do not touch `tests/e2e`.
- The README describes only behavior present in this worktree's `src/`. It makes no claim that a deferred item would have to make true.
- Out of scope, never documented as existing: `--no-integrate`, milestone-aware `am resume`, `watch`/`retry`/`cancel`, cost capture, the reviewer Plan-Hash brief, marking slow tests, per-story readiness.
- State plainly that `am` never merges into the base branch and never pushes (rule 4, I5).
- If a command does not run as written, the fix goes in the README, not in `src/`.
- An implementation fact that disagrees with the spec: the source in this worktree wins, and the discrepancy is noted in the hand-off.
- Rule 2: `uv run pytest` (the default suite) stays green.
- CLI envelope: `{"ok": true, "data": ...}` on success, `{"ok": false, "error": {...}}` on a refusal; exit codes 0 clean, 1 escalated, 2 usage, 3 refusal.
- Real runs use the fake `claude` from `tests/e2e/fake_claude.py`, never a real harness. All temp files live in the session scratchpad.

## Facts read from the source (the README text below is built from these)

- `integration.integration_branch(prefix)` returns `f"{prefix}-integrate"`; the worktree is `cli.worktree_for(root, branch)`, i.e. `<repo>/.claude/worktrees/<prefix>-integrate`, absolute.
- `integration.merge_order`: levels from `dag.compute_integrate_levels` over every story, census order within a level, stories with no subtasks skipped.
- `steps.integrate.merge_tip`: refuses with `MergeInProgressError` (message "a merge is already in progress in <worktree>: ... A human must finish it -- resolve the conflict and commit in <worktree> -- then relaunch.") when `MERGE_HEAD` exists; `merge-base --is-ancestor` makes an already-merged tip a no-op; otherwise `git merge --no-ff --no-edit <tip>`; a conflict is left in progress.
- `merge_completed_gate`: passes only with no `MERGE_HEAD`, clean `git status --porcelain --untracked-files=all`, and no touched file holding both `<<<<<<< ` and `>>>>>>> ` lines; the resolver's result is never read.
- `workflow/builtin/integrate.yaml`: `resolve` (agent, role `resolver`, gate `merge_completed_gate`, retry `max_attempts: 2` on `gate_failed`, `schema_invalid`), then `verify` (deterministic `verify.run_suite`, gate `verification_passed_gate`).
- Synthetic story: card id `integrate`, title `Integrate`, one subtask per conflicting story (card id = the story id). Recorded only when a tip conflicts.
- `integrated_payload`: `{"branch", "worktree" (str), "merged", "resolved"}`; `merged` includes already-merged stories, `resolved` only this run's resolver fixes.
- `integrate_escalated_payload`: `{"escalated": true, "phase": "integrate", "story", "files", "detail", "run_id", "warnings"}`; `story` is `None` for a failed final verification; `files` is `[]` for a merge already in progress and for a failed final verification.
- `run_milestone`: Integrate runs after every level finished clean, and also when nothing was left to drive; a lane escalation returns before it. `done: true` and `integrated` only on success.
- `dry_run_payload`: `data.integrate = {"branch", "worktree", "order": [{"story", "tip"}, ...]}`; writes nothing.
- `_final_verification`: with no `--verify` and `--allow-no-verification`, it is skipped; with neither, `verification_gate` escalates.

## Review Focus

- A human relaunches after an Integrate escalation without committing first: the README must say the relaunch escalates again (with `files` empty and a `detail` saying a merge is already in progress) and touches nothing. Pinned by Task 5 Step 6.
- `--repo-dir` left at its default `.`: `data.integrate.worktree` and `data.integrated.worktree` must still be absolute paths, as the README says `<repo>/.claude/worktrees/<prefix>-integrate`. Pinned by Task 5 Steps 3 and 4.
- The documented human merge `git merge m3-integrate` is run from the base checkout while `m3-integrate` is checked out in its own worktree: it must work as written. Pinned by Task 5 Step 5.
- A relaunch with `--allow-no-verification` and no `--verify`: the README says the final check is skipped, so it must report `done` and not escalate. Pinned by Task 5 Step 4.
- Links inside the README (`#integrate`, `#what-a-clean-run-leaves-behind`, `#what-an-escalation-report-contains`, `#relaunching-resumes`, and the addendum's `#6-deferred`) must point at headings that exist. Pinned by the check scripts in Tasks 2 and 3.

---

### Task 0: Confirm the checkout has Integrate and record the baseline suite

**Files:** none changed.

**Interfaces:**
- Consumes: nothing.
- Produces: the baseline pass count for Task 5 Step 9.

- [ ] **Step 1: Confirm the implementation is on this branch**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-document-integrate-4c78e3ea
test -f src/agent_manager/integration.py && test -f src/agent_manager/steps/integrate.py && test -f src/agent_manager/workflow/builtin/integrate.yaml && echo present
git merge-base --is-ancestor m5/task-add-the-opt-in-real-dca476c4 HEAD && echo stacked
git diff --stat m5/task-add-the-opt-in-real-dca476c4 -- README.md docs/superpowers/specs/2026-09-23-agent-manager-design.md
```

Expected: `present`, `stacked`, and an empty diff stat. If `present` is missing, stop and report (spec "Error paths"); do not write docs.

- [ ] **Step 2: Record the baseline default suite**

Run: `uv run pytest -q`
Expected: PASS. Write down the final `N passed` count (and any `skipped`/`deselected` counts) for Task 5 Step 9.

---

### Task 1: Dry-run preview and clean-run report mention Integrate

**Files:**
- Modify: `README.md:88-91` (end of "Preview with `--dry-run`" paragraph)
- Modify: `README.md:118-130` ("What a clean run leaves behind" list and closing paragraph)
- Create (scratch, never committed): `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh`

**Interfaces:**
- Consumes: facts `dry_run_payload`, `integrated_payload`, `run_milestone` above.
- Produces: README links to `#integrate`, which Task 2 creates.

- [ ] **Step 1: Write the failing check**

Save as `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh`:

```bash
#!/usr/bin/env bash
F=/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-document-integrate-4c78e3ea/README.md
fail=0
need() { grep -qF -- "$1" "$F" || { echo "MISSING: $1"; fail=1; }; }
gone() { if grep -qF -- "$1" "$F"; then echo "STALE: $1"; fail=1; fi; }
need '`data.integrate` is the plan for [Integrate](#integrate)'
need '`order` lists `{"story", "tip"}` in the order the tips will be merged'
need 'The preview creates neither the branch nor the worktree.'
need 'reported only once [Integrate](#integrate) has merged every tip and passed its final check'
need '- `integrated`: `{"branch", "worktree", "merged", "resolved"}`'
need 'The tips are merged into `<prefix>-integrate` and nowhere else.'
need 'Merging the integration branch into the base branch is left to you'
gone 'Nothing is merged and nothing is pushed.'
gone 'Merging the tips is left to you.'
exit $fail
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh; echo "exit=$?"`
Expected: seven `MISSING:` lines, two `STALE:` lines, `exit=1`.

- [ ] **Step 3: Add `data.integrate` to the dry-run section**

In `README.md`, replace:

```markdown
`{"kind": "subtask", "id", "title", "story"}` for a done subtask of a story that
still has work.
```

with:

```markdown
`{"kind": "subtask", "id", "title", "story"}` for a done subtask of a story that
still has work.

`data.integrate` is the plan for [Integrate](#integrate), the step that runs after the last level: `{"branch", "worktree", "order"}`. `branch` is `<prefix>-integrate`, `worktree` is its worktree, `<repo>/.claude/worktrees/<prefix>-integrate`, and `order` lists `{"story", "tip"}` in the order the tips will be merged. It names every story that has subtasks, done or not, because Integrate merges them all. The preview creates neither the branch nor the worktree.
```

- [ ] **Step 4: Update the clean-run `done` bullet**

In `README.md`, replace:

```markdown
- `done`: `true`.
```

with:

```markdown
- `done`: `true`, reported only once [Integrate](#integrate) has merged every tip and passed its final check.
```

- [ ] **Step 5: Add `integrated` and replace the "nothing is merged" paragraph**

In `README.md`, replace:

```markdown
- `tips`: `{"story", "tip"}` for every story in the milestone that has
  subtasks, naming the branch its stack ends on.
- `warnings`: board writes that failed but did not stop the run, as text.

Nothing is merged and nothing is pushed. The branches stay local and stacked,
and the base branch does not move. Merging the tips is left to you.
```

with:

```markdown
- `tips`: `{"story", "tip"}` for every story in the milestone that has
  subtasks, naming the branch its stack ends on. These are the branches
  Integrate merged.
- `warnings`: board writes that failed but did not stop the run, as text.
- `integrated`: `{"branch", "worktree", "merged", "resolved"}`. `branch` is `<prefix>-integrate` and `worktree` is its worktree. `merged` lists, in merge order, the story ids whose tip is in `branch`, including tips an earlier run already merged. `resolved` lists the story ids whose conflict a resolver fixed in this run.

The tips are merged into `<prefix>-integrate` and nowhere else. The story branches stay local and stacked, the base branch does not move, and nothing is pushed. Merging the integration branch into the base branch is left to you (see [Integrate](#integrate)).
```

- [ ] **Step 6: Run the check to verify it passes**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh; echo "exit=$?"`
Expected: no output lines before `exit=0`.

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Show Integrate in the README's dry-run and clean-run reports

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 2: The `#### Integrate` subsection

**Files:**
- Modify: `README.md` (insert before `#### Parallel runs`, i.e. right after "What a clean run leaves behind")
- Create (scratch, never committed): `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task2.sh`

**Interfaces:**
- Consumes: the `#integrate` links Task 1 added; facts `integration_branch`, `merge_order`, `merge_tip`, `merge_completed_gate`, `integrate.yaml`, `_final_verification` above.
- Produces: the heading `#### Integrate` (anchor `#integrate`) that Tasks 1 and 3 link to; it links to `#what-a-clean-run-leaves-behind` and `#what-an-escalation-report-contains`.

- [ ] **Step 1: Write the failing check**

Save as `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task2.sh`:

```bash
#!/usr/bin/env bash
F=/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-document-integrate-4c78e3ea/README.md
fail=0
need() { grep -qF -- "$1" "$F" || { echo "MISSING: $1"; fail=1; }; }
gone() { if grep -qF -- "$1" "$F"; then echo "STALE: $1"; fail=1; fi; }
# Exactly one Integrate heading, placed before Parallel runs.
[ "$(grep -cx '#### Integrate' "$F")" = 1 ] || { echo "HEADING: want exactly one '#### Integrate'"; fail=1; }
i=$(grep -nx '#### Integrate' "$F" | cut -d: -f1); p=$(grep -nx '#### Parallel runs' "$F" | cut -d: -f1)
[ -n "$i" ] && [ -n "$p" ] && [ "$i" -lt "$p" ] || { echo "ORDER: Integrate must come before Parallel runs"; fail=1; }
# Link targets used by this section exist.
grep -qx '#### What a clean run leaves behind' "$F" || { echo "ANCHOR: what-a-clean-run-leaves-behind"; fail=1; }
grep -qx '#### What an escalation report contains' "$F" || { echo "ANCHOR: what-an-escalation-report-contains"; fail=1; }
need 'It runs only when every level finished clean'
need 'every relaunch of the milestone runs it again'
need '`<repo>/.claude/worktrees/<prefix>-integrate`'
need 'merged with `git merge --no-ff`, and no agent is dispatched'
need 'a resolver agent (role `resolver`)'
need 'shows up as a story titled `Integrate`'
need 'Git decides whether the resolver finished, not the resolver'
need '(no `MERGE_HEAD`)'
need '`resolve` gets 2 attempts'
need 'the `--verify` commands run once on the integrated branch'
need '`am` never merges into the base branch and never pushes.'
need 'git merge m3-integrate'
need 'Commit before you relaunch.'
need '**A resolver can misjudge what two edits meant**'
need '**Merges are sequential.**'
need 'hundreds of sequential merges'
exit $fail
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task2.sh; echo "exit=$?"`
Expected: `HEADING:`, `ORDER:` and many `MISSING:` lines, `exit=1`.

- [ ] **Step 3: Insert the subsection**

In `README.md`, replace:

````markdown
#### Parallel runs

`--max-concurrent N` bounds how many stories of one level run at once. It
````

with:

````markdown
#### Integrate

After the last level, the run merges every story's tip into one local branch, `<prefix>-integrate`, and checks the result once. This step is Integrate. It runs only when every level finished clean: an escalation or a stop ends the run before it. It also runs when there was nothing left to drive, so every relaunch of the milestone runs it again.

Where, and in what order:

- The branch is `<prefix>-integrate` (with `--branch-prefix m3`, `m3-integrate`), and its worktree is `<repo>/.claude/worktrees/<prefix>-integrate`. The branch is cut from the base branch the first time and reused after that.
- Tips are merged one at a time. The order goes by dependency level over every story of the milestone, done or not, and follows the census order within a level. A story with no subtasks has no branch of its own and is skipped. `--dry-run` shows the exact order in `data.integrate.order`.

How each tip is merged:

- A tip that merges cleanly is merged with `git merge --no-ff`, and no agent is dispatched. A tip the branch already contains is skipped, so running Integrate again never merges anything twice.
- A tip that conflicts is left mid-merge, and a resolver agent (role `resolver`) is dispatched into the integration worktree with the tip and the list of conflicting files. It runs the builtin `integrate` workflow: a `resolve` phase, then a `verify` phase that runs your `--verify` commands. In `am status <run_id>` the resolver shows up as a story titled `Integrate`, with one subtask per conflicting story.
- Git decides whether the resolver finished, not the resolver's own report. The merge counts as finished only when no merge is in progress (no `MERGE_HEAD`), `git status` is clean, and no file the merge touched still holds conflict markers. `resolve` gets 2 attempts. A resolver that does not finish escalates, and the merge is left in progress for you.

After the last tip, the `--verify` commands run once on the integrated branch. Two stories that each pass alone can still break the suite together, even when git merged them without a conflict, and that failure escalates here. With `--allow-no-verification` and no `--verify`, this check is skipped.

A clean Integrate is what lets the run report `done`, with an `integrated` key (see [What a clean run leaves behind](#what-a-clean-run-leaves-behind)). A failed one is an escalation (see [What an escalation report contains](#what-an-escalation-report-contains)).

`am` never merges into the base branch and never pushes. Review `<prefix>-integrate`, then merge it yourself from a checkout of the base branch:

```bash
git switch master
git merge m3-integrate
```

After an Integrate escalation:

1. Open the integration worktree, `<repo>/.claude/worktrees/<prefix>-integrate`. The report's `detail` names it.
2. Finish or fix the merge there: resolve the conflicts, `git add` the files and `git commit`. When the final check failed instead, commit a fix on the branch.
3. Relaunch the same `am run --milestone` command. Every story is already `done`, so only Integrate runs: tips already merged are skipped, and the final check runs again.

Commit before you relaunch. Integrate will not start a merge while another is in progress in its worktree. It escalates again, with a `detail` saying a merge is already in progress, and touches nothing.

Limits, stated plainly:

- **A resolver can misjudge what two edits meant**, even when the result compiles and passes. The final check narrows that risk but does not remove it, so review the integration branch before you merge it, as with every branch here.
- **Merges are sequential.** There is one integration worktree, so tips are merged one after another, and `--max-concurrent` does not apply.
- **Nothing batches them.** A milestone with hundreds of stories means hundreds of sequential merges.

#### Parallel runs

`--max-concurrent N` bounds how many stories of one level run at once. It
````

- [ ] **Step 4: Run both checks to verify they pass**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task2.sh && bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh; echo "exit=$?"`
Expected: no output lines before `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Document Integrate in the README

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 3: Escalation report, relaunching, and "Not there yet"

**Files:**
- Modify: `README.md` ("What an escalation report contains": insert before "When the run cannot start at all")
- Modify: `README.md` ("Relaunching resumes": the "Relaunching a finished milestone" sentence)
- Modify: `README.md` ("Not there yet": bullets and link paragraph)
- Create (scratch, never committed): `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task3.sh`

**Interfaces:**
- Consumes: `#integrate` from Task 2; facts `integrate_escalated_payload`, `run_milestone`, `MergeInProgressError` above; addendum heading `## 6. Deferred` (anchor `#6-deferred`) in `docs/superpowers/specs/2026-09-25-integrate-design.md:126`.
- Produces: nothing later tasks consume beyond the final README.

- [ ] **Step 1: Write the failing check**

Save as `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task3.sh`:

```bash
#!/usr/bin/env bash
WT=/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-document-integrate-4c78e3ea
F=$WT/README.md
fail=0
need() { grep -qF -- "$1" "$F" || { echo "MISSING: $1"; fail=1; }; }
gone() { if grep -qF -- "$1" "$F"; then echo "STALE: $1"; fail=1; fi; }
need 'An escalation at [Integrate](#integrate) has its own shape.'
need '`phase` (`"integrate"`), `story`, `files`, `detail`, `run_id` and `warnings`'
need 'or `null` when the final check of the integrated branch failed'
need 'It is empty when a merge was already in progress'
need 'left exactly as Integrate left them'
need 'Relaunching a finished milestone drives no subtask but still runs [Integrate](#integrate).'
need 'it merges nothing and dispatches no agent'
need 'Relaunching after an Integrate escalation runs Integrate again'
need 'There is no `--no-integrate` option'
need '[Integrate addendum](docs/superpowers/specs/2026-09-25-integrate-design.md#6-deferred)'
need '- `am resume` is not milestone-aware.'
need '- `watch`, `retry` and `cancel` do not exist.'
gone 'There is no Integrate step'
gone 'Relaunching a finished milestone drives nothing and'
grep -qx '## 6. Deferred' "$WT/docs/superpowers/specs/2026-09-25-integrate-design.md" || { echo "ANCHOR: addendum 6-deferred"; fail=1; }
grep -qx '#### Relaunching resumes' "$F" || { echo "ANCHOR: relaunching-resumes"; fail=1; }
exit $fail
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task3.sh; echo "exit=$?"`
Expected: ten `MISSING:` lines (the two kept bullets already exist), two `STALE:` lines, `exit=1`.

- [ ] **Step 3: Add the Integrate escalation payload**

In `README.md`, replace:

```markdown
When the run cannot start at all (an unknown or ambiguous milestone, a blocker
cycle, a board error), it prints `{"ok": false, "error": {...}}` and exits 3.
```

with:

```markdown
An escalation at [Integrate](#integrate) has its own shape. The run exits 1, and `data` holds `escalated` (`true`), `phase` (`"integrate"`), `story`, `files`, `detail`, `run_id` and `warnings`. There is no `integrated` key.

- `story` is the story whose tip was being merged, or `null` when the final check of the integrated branch failed.
- `files` lists the conflicting files the resolver did not finish. It is empty when a merge was already in progress in the integration worktree, and when the final check failed.
- `detail` says what went wrong and names the integration worktree.

The run is recorded `escalated`. The integration branch and its worktree are left exactly as Integrate left them, a merge still in progress included, so you can finish it there. See [Integrate](#integrate) for what to do next.

When the run cannot start at all (an unknown or ambiguous milestone, a blocker
cycle, a board error), it prints `{"ok": false, "error": {...}}` and exits 3.
```

- [ ] **Step 4: Update "Relaunching resumes"**

In `README.md`, replace:

```markdown
that already passed. Relaunching a finished milestone drives nothing and
reports `done` with an empty `completed`.
```

with:

```markdown
that already passed. Relaunching a finished milestone drives no subtask but still runs [Integrate](#integrate). With every tip already merged, it merges nothing and dispatches no agent, runs the final check again, and reports `done` with an empty `completed` and an `integrated` whose `resolved` is empty. Relaunching after an Integrate escalation runs Integrate again, so commit your fix in the integration worktree first.
```

- [ ] **Step 5: Update "Not there yet"**

In `README.md`, replace:

```markdown
- There is no Integrate step: nothing merges the story tips into one branch.
- `am resume` is not milestone-aware.
- `watch`, `retry` and `cancel` do not exist.

See section 4 of the
[orchestration addendum](docs/superpowers/specs/2026-09-24-orchestration-design.md#4-deferred-to-the-follow-up-milestone-found-now-not-cut-yet)
and section 5 of the
[parallel-stories addendum](docs/superpowers/specs/2026-09-24-parallel-stories-design.md#5-deferred)
for everything deferred.
```

with:

```markdown
- `am resume` is not milestone-aware.
- `watch`, `retry` and `cancel` do not exist.
- There is no `--no-integrate` option: a milestone run that finishes clean always ends with Integrate.

See section 4 of the
[orchestration addendum](docs/superpowers/specs/2026-09-24-orchestration-design.md#4-deferred-to-the-follow-up-milestone-found-now-not-cut-yet),
section 5 of the
[parallel-stories addendum](docs/superpowers/specs/2026-09-24-parallel-stories-design.md#5-deferred)
and section 6 of the
[Integrate addendum](docs/superpowers/specs/2026-09-25-integrate-design.md#6-deferred)
for everything deferred.
```

- [ ] **Step 6: Run all three checks to verify they pass**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task3.sh && bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task2.sh && bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh; echo "exit=$?"`
Expected: no output lines before `exit=0`.

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Describe Integrate escalations and relaunches in the README

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 4: Status note in main spec §12

**Files:**
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:458-464` (after the "Blast radius, stated plainly." paragraph, before `## 13. Where this is more efficient`)
- Create (scratch, never committed): `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task4.sh`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing later tasks consume.

- [ ] **Step 1: Write the failing check**

Save as `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task4.sh`:

```bash
#!/usr/bin/env bash
WT=/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-document-integrate-4c78e3ea
S=$WT/docs/superpowers/specs/2026-09-23-agent-manager-design.md
fail=0
section=$(awk '/^## 12\. /{on=1} /^## 13\. /{on=0} on' "$S")
echo "$section" | grep -qF '**Status:** as of milestone 5, Integrate merges every story tip' || { echo "MISSING: Status note inside section 12"; fail=1; }
echo "$section" | grep -qF 'never pushes (Integrate addendum I5' || { echo "MISSING: I5 guarantee"; fail=1; }
# Only additions: nothing else in the spec changes.
read -r added deleted _ < <(cd "$WT" && git diff --numstat m5/task-add-the-opt-in-real-dca476c4 -- "$S")
[ "${deleted:-0}" = 0 ] || { echo "CHANGED: $deleted line(s) deleted from the spec"; fail=1; }
[ "${added:-0}" -le 2 ] || { echo "CHANGED: $added lines added, want at most 2 (blank + note)"; fail=1; }
exit $fail
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task4.sh; echo "exit=$?"`
Expected: two `MISSING:` lines, `exit=1`.

- [ ] **Step 3: Add the Status paragraph**

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, replace:

```markdown
local branches, nothing is pushed, and `main`/`master` is never touched. The
launcher seam exists so `bwrap` can close the rest later; that is deliberately
not v1.
```

with:

```markdown
local branches, nothing is pushed, and `main`/`master` is never touched. The
launcher seam exists so `bwrap` can close the rest later; that is deliberately
not v1.

**Status:** as of milestone 5, Integrate merges every story tip into one local `<prefix>-integrate` branch, in its own worktree, after the last level, then runs the verification suite on it once. A merge the resolver does not finish, a merge already in progress, or a failed final verification escalates like any other gate: the run stops, and the branch and worktree are left for a human. The blast-radius guarantee holds: Integrate never checks out, merges into or moves `main`/`master` or the base branch, and never pushes (Integrate addendum I5, `2026-09-25-integrate-design.md`).
```

- [ ] **Step 4: Run the check to verify it passes**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task4.sh; echo "exit=$?"`
Expected: no output lines before `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-09-23-agent-manager-design.md
git commit -m "$(cat <<'EOF'
Note Integrate's status in the design spec's blast-radius section

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 5: Run every README command by hand against a temp repo and board, then the full suite

**Files:**
- Create (scratch, never committed): `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/setup.sh`
- Modify only if a command fails as written: `README.md`

**Interfaces:**
- Consumes: the final `README.md` from Tasks 1-3; `tests/e2e/fake_claude.py` (read only, copied to a scratch `claude`). The fake's switches, as `tests/e2e/conftest.py` pins them: `.git/info/attributes` with `IMPLEMENTATION.md merge=union`; the implement-edits marker `.git/fake-claude-implement-edits` (JSON: subtask branch -> `{relative path: full content}`); `FAKE_CLAUDE_RESOLVER=refuse` makes `resolve` not finish.
- Produces: the hand-off evidence (exit codes and key fields).

`--verify "git rev-parse --verify HEAD"` stands in for the README's `"uv run pytest"` in every real run: the toy repo has no Python project, and what is checked is the command shape and the report, not the suite. The milestone is titled so the README's `--milestone "document milestone runs"` matches it as written, and the repo is on `master` so `--base-branch` stays at its documented default.

- [ ] **Step 1: Write the temp repo + board setup script**

Save as `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/setup.sh`:

```bash
#!/usr/bin/env bash
# A throwaway repo on `master` that is also a brd board, with one milestone:
# A (a1) and B (b1) independent in level 0, C (c1) blocked by A in level 1.
# Usage: setup.sh clean|conflict
#   clean:    the fake coder only writes IMPLEMENTATION.md (union-merged), so Integrate needs no resolver.
#   conflict: master gets shared.txt, and A and B each rewrite its one line, so B's tip conflicts.
set -euo pipefail
MODE=${1:?clean or conflict}
SCRATCH=/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check
WT=/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-document-integrate-4c78e3ea
rm -rf "$SCRATCH/repo" "$SCRATCH/xdg" "$SCRATCH/bin" "$SCRATCH"/*.json
mkdir -p "$SCRATCH/bin"
export XDG_DATA_HOME="$SCRATCH/xdg"

# The fake claude, first on PATH (same shape as tests/e2e/conftest.py::fake_claude_bin).
{ echo "#!$(command -v python3)"; cat "$WT/tests/e2e/fake_claude.py"; } > "$SCRATCH/bin/claude"
chmod 755 "$SCRATCH/bin/claude"

R="$SCRATCH/repo"
git init -q -b master "$R"
git -C "$R" config user.email readme-check@example.com
git -C "$R" config user.name "readme check"
git -C "$R" config commit.gpgsign false
echo base > "$R/README.md"
git -C "$R" add README.md && git -C "$R" commit -qm base
(cd "$R" && brd init --name readme-check >/dev/null)
git -C "$R" add -A && git -C "$R" commit -qm "brd init"
# As tests/e2e/conftest.py::UNION_ATTRIBUTE: lets git fold the fake coder's IMPLEMENTATION.md.
mkdir -p "$R/.git/info" && echo "IMPLEMENTATION.md merge=union" > "$R/.git/info/attributes"

add() { (cd "$R" && brd add --title "$1" ${2:+--parent "$2"}) | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["id"])'; }
block() { (cd "$R" && brd block "$1" --by "$2" >/dev/null); }
M=$(add "Milestone: document milestone runs")
A=$(add "Story A: independent" "$M")
B=$(add "Story B: independent of A" "$M")
C=$(add "Story C: blocked by A" "$M"); block "$C" "$A"
A1=$(add "a1: only subtask of A" "$A")
B1=$(add "b1: only subtask of B" "$B")
C1=$(add "c1: only subtask of C" "$C")

if [ "$MODE" = conflict ]; then
  printf 'the line both stories rewrite\n' > "$R/shared.txt"
  git -C "$R" add shared.txt && git -C "$R" commit -qm "seed shared.txt"
  (cd "$R" && uv run --project "$WT" am run --milestone "document milestone runs" --branch-prefix m3 --dry-run) > "$SCRATCH/plan.json"
  python3 - "$SCRATCH/plan.json" "$A1" "$B1" "$R/.git/fake-claude-implement-edits" <<'PY'
import json, sys
plan, a1, b1, marker = sys.argv[1:]
data = json.load(open(plan))["data"]
branch = {s["id"]: s["branch"] for l in data["levels"] for st in l["stories"] for s in st["subtasks"]}
json.dump({branch[a1]: {"shared.txt": "story A rewrote this line\n"},
           branch[b1]: {"shared.txt": "story B rewrote this line\n"}}, open(marker, "w"))
PY
fi

cat > "$SCRATCH/env.sh" <<EOF
export XDG_DATA_HOME="$SCRATCH/xdg"
export PATH="$SCRATCH/bin:\$PATH"
export R="$R" WT="$WT" SCRATCH="$SCRATCH"
export M=$M A=$A B=$B C=$C A1=$A1 B1=$B1 C1=$C1
am() { uv run --project "\$WT" am "\$@"; }
j() { python3 -c "import json,sys;d=json.load(open(sys.argv[1]))['data'];print(eval(sys.argv[2]))" "\$@"; }
EOF
echo "setup done ($MODE): source $SCRATCH/env.sh"
```

- [ ] **Step 2: Build the clean fixture**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/setup.sh clean`
Expected: `setup done (clean): ...`. Then, in the same shell for every later step until Step 6: `source /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/env.sh && cd "$R"`; `command -v claude` prints `$SCRATCH/bin/claude`.

- [ ] **Step 3: Dry-run exactly as the README writes it, and check `data.integrate`**

Run:

```bash
am run --milestone "document milestone runs" --branch-prefix m3 --dry-run --pretty | tee "$SCRATCH/dry.json"; echo "exit=${PIPESTATUS[0]}"
j "$SCRATCH/dry.json" 'd["integrate"]["branch"], d["integrate"]["worktree"], [o["story"] for o in d["integrate"]["order"]]'
echo "want: m3-integrate $R/.claude/worktrees/m3-integrate [$A, $B, $C]"
git branch --format='%(refname:short)'; ls .claude/worktrees 2>/dev/null; ls "$XDG_DATA_HOME" 2>/dev/null
```

Expected: `exit=0`; the `j` line prints `('m3-integrate', '<R>/.claude/worktrees/m3-integrate', ['<A>', '<B>', '<C>'])`, matching the `want:` line (absolute path even though `--repo-dir` defaulted to `.`; order A, B, C; each `tip` is a `m3/task-...` branch). The last line lists only `master`, no worktree directory and no run directory. If any differs, the source wins: fix the README text and note it for the hand-off.

- [ ] **Step 4: A clean real run, both synopsis forms, the relaunch no-op, and the no-verification relaunch**

Run:

```bash
MASTER_BEFORE=$(git rev-parse master)
am run --milestone "document milestone runs" --branch-prefix m3 --verify "git rev-parse --verify HEAD" --max-concurrent 2 --pretty | tee "$SCRATCH/run1.json"; echo "exit=${PIPESTATUS[0]}"
j "$SCRATCH/run1.json" 'd["done"], d["integrated"]'
TIPS_BEFORE=$(git for-each-ref --format='%(refname:short) %(objectname)' refs/heads)
am run --milestone "document milestone runs" --branch-prefix m3 --verify "git rev-parse --verify HEAD" --pretty | tee "$SCRATCH/run2.json"; echo "exit=${PIPESTATUS[0]}"
j "$SCRATCH/run2.json" 'd["done"], d["completed"], d["integrated"]["merged"], d["integrated"]["resolved"]'
am run --milestone "document milestone runs" --branch-prefix m3 --allow-no-verification | tee "$SCRATCH/run3.json"; echo "exit=${PIPESTATUS[0]}"
j "$SCRATCH/run3.json" 'd.get("done"), d.get("escalated")'
[ "$(git for-each-ref --format='%(refname:short) %(objectname)' refs/heads)" = "$TIPS_BEFORE" ] && echo "no branch moved"
[ "$(git rev-parse master)" = "$MASTER_BEFORE" ] && echo "master unchanged"
git remote; git for-each-ref refs/remotes
am status "$(j "$SCRATCH/run1.json" 'd["run_id"]')" --pretty | grep -c '"Integrate"'
```

Expected:
- First run (synopsis with `--max-concurrent N`): `exit=0`; `done` is `True`; `integrated` is `{'branch': 'm3-integrate', 'worktree': '<R>/.claude/worktrees/m3-integrate', 'merged': ['<A>', '<B>', '<C>'], 'resolved': []}`.
- Second run (synopsis without the option, the README's "relaunching a finished milestone"): `exit=0`; `True [] ['<A>', '<B>', '<C>'] []`.
- Third run (`--allow-no-verification`, no `--verify`): `exit=0`; `True None`.
- `no branch moved`, `master unchanged`, and `git remote` / `for-each-ref refs/remotes` print nothing (nothing pushed).
- The `am status` grep prints `0`: a clean merge records no Integrate story (the README only promises it for a conflict).

- [ ] **Step 5: The human merge, exactly as the README writes it**

Run:

```bash
git switch master
git merge m3-integrate; echo "exit=$?"
git merge-base --is-ancestor m3-integrate master && echo "integrated into master"
```

Expected: `git switch` says `Already on 'master'`; the merge exits 0 (a fast-forward here, since `master` never moved); `integrated into master`. This works although `m3-integrate` is checked out in its own worktree.

- [ ] **Step 6: The conflict path: resolver refuses, relaunch without committing, human commits, relaunch**

Rebuild with the conflict fixture:

```bash
bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/setup.sh conflict
source /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/env.sh && cd "$R"
```

Then run (as `tests/e2e/test_integrate.py::test_a_resolver_that_does_not_finish_escalates_and_a_human_finish_lets_the_relaunch_complete` does):

```bash
MASTER_BEFORE=$(git rev-parse master)
IW="$R/.claude/worktrees/m3-integrate"
export FAKE_CLAUDE_RESOLVER=refuse
am run --milestone "document milestone runs" --branch-prefix m3 --verify "git rev-parse --verify HEAD" --pretty | tee "$SCRATCH/esc1.json"; echo "exit=${PIPESTATUS[0]}"
j "$SCRATCH/esc1.json" 'sorted(d), d["phase"], d["story"], d["files"], "integrated" in d'
j "$SCRATCH/esc1.json" 'd["detail"]'
git -C "$IW" rev-parse --verify --quiet MERGE_HEAD >/dev/null && echo "merge left in progress"
am status "$(j "$SCRATCH/esc1.json" 'd["run_id"]')" --pretty | grep -c '"Integrate"'

# Relaunch WITHOUT committing: the README says this escalates again and touches nothing.
unset FAKE_CLAUDE_RESOLVER
am run --milestone "document milestone runs" --branch-prefix m3 --verify "git rev-parse --verify HEAD" --pretty | tee "$SCRATCH/esc2.json"; echo "exit=${PIPESTATUS[0]}"
j "$SCRATCH/esc2.json" 'd["phase"], d["story"], d["files"]'
j "$SCRATCH/esc2.json" 'd["detail"]'

# The human step the README documents: finish the merge in the integration worktree and commit.
printf 'story A rewrote this line\nstory B rewrote this line\n' > "$IW/shared.txt"
git -C "$IW" add shared.txt
git -C "$IW" commit --no-edit -q; echo "commit exit=$?"
am run --milestone "document milestone runs" --branch-prefix m3 --verify "git rev-parse --verify HEAD" --pretty | tee "$SCRATCH/done.json"; echo "exit=${PIPESTATUS[0]}"
j "$SCRATCH/done.json" 'd["done"], d["completed"], d["integrated"]["merged"], d["integrated"]["resolved"]'
[ "$(git rev-parse master)" = "$MASTER_BEFORE" ] && echo "master unchanged"
```

Expected:
- First run: `exit=1`; keys are exactly `['detail', 'escalated', 'files', 'phase', 'run_id', 'story', 'warnings']`; `phase` `'integrate'`, `story` `'<B>'`, `files` contains `'shared.txt'`, `False` for `integrated`; `detail` names `$IW`; `merge left in progress`; the `am status` grep prints at least `1` (the synthetic `Integrate` story).
- Relaunch without committing: `exit=1`; `phase` `'integrate'`, `story` `'<A>'` (the first tip Integrate tried), `files` `[]`; `detail` contains `a merge is already in progress in` and `$IW`.
- `commit exit=0`.
- Final relaunch: `exit=0`; `True [] ['<A>', '<B>', '<C>'] []`.
- `master unchanged`.

- [ ] **Step 7: The resolver-success path and the `am status` Integrate story**

Rebuild the conflict fixture and run with the resolver in its default mode (as `test_a_same_line_conflict_is_resolved_verified_and_left_on_the_integration_branch` does):

```bash
bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/setup.sh conflict
source /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/env.sh && cd "$R"
unset FAKE_CLAUDE_RESOLVER
am run --milestone "document milestone runs" --branch-prefix m3 --verify "git rev-parse --verify HEAD" --pretty | tee "$SCRATCH/resolved.json"; echo "exit=${PIPESTATUS[0]}"
j "$SCRATCH/resolved.json" 'd["done"], d["integrated"]["merged"], d["integrated"]["resolved"]'
am status "$(j "$SCRATCH/resolved.json" 'd["run_id"]')" --pretty
git show m3-integrate:shared.txt
```

Expected: `exit=0`; `True ['<A>', '<B>', '<C>'] ['<B>']`; the `am status` output lists a story titled `Integrate` with one subtask whose id is `<B>` and whose phases are `resolve` then `verify`; `shared.txt` holds both stories' lines and no conflict markers.

The final-check escalation (`story: null`, `files: []`) is not reproduced by hand: it needs a real suite that is green per story and red once merged. It is covered by `tests/e2e/test_integrate.py::test_a_clean_merge_that_breaks_the_suite_escalates_at_integrate`, which runs in the default suite (Step 9). The README states that shape from `orchestrate.integrate_escalated_payload` and shows no sample output for it; say so in the hand-off.

- [ ] **Step 8: Fix the README if any step above did not run as written**

For each mismatch found in Steps 3-7, edit only `README.md` so it matches what the CLI did (never `src/`), re-run the failing step, then re-run the doc checks:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-document-integrate-4c78e3ea
for n in 1 2 3 4; do bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task$n.sh || echo "check_task$n failed"; done
```

Expected: no `failed` lines. If a README wording fix breaks a check's pinned phrase, update the phrase in that scratch check to the new wording (the check is scratch, not a test) and record the change for the hand-off. If nothing mismatched, skip this step and record "no README changes from the hand check".

- [ ] **Step 9: Confirm no src/tests change and run the full default suite**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-document-integrate-4c78e3ea
git diff --name-only m5/task-add-the-opt-in-real-dca476c4 -- src tests
git diff --name-only m5/task-add-the-opt-in-real-dca476c4
git status --porcelain
uv run pytest -q
```

Expected: the first command prints nothing. The second lists only `README.md`, `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, and this card's own spec/plan files under `docs/superpowers/` if they are committed. `git status --porcelain` shows only uncommitted README fixes from Step 8 (if any) and this card's spec/plan files if not yet committed. `uv run pytest -q` passes with the same counts as Task 0 Step 2.

- [ ] **Step 10: Commit any README fixes from the hand check, then clean up**

Only if Step 8 changed `README.md`:

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Match the README's Integrate text to what the CLI does

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

Then remove the scratch fixture: `rm -rf /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check`.

Hand-off notes to carry forward: the final-check escalation was verified by `tests/e2e/test_integrate.py`, not by hand; a relaunch without committing reports the first tip Integrate tried (story A) as `story`, not the story whose merge is stuck; and any fact from Steps 3-7 that disagreed with the spec.
