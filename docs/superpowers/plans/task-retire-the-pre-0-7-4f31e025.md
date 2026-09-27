# Retire the pre-0.7 workaround rules in the addenda (subtask 4f31e025)

Parent story: dd4a87d5 "Adopt pygents 0.7.0". Plan: `docs/superpowers/plans/2026-09-25-pygents-070-adoption.md`, Task 1.3. Source-of-truth decision: `docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md` §A4.

Base branch: `m8/task-pin-pygents-0822234f`, the branch of blocker 0822234f. That branch already has the 0.7.0 code changes (`AgentRegistry.unregister` under `contextlib.suppress`, cancellation pinned), so the fixes these edits point to exist. Its addenda still carry the old text. Branch: `m8/task-retire-the-pre-0-7-4f31e025`.

## Scope

This subtask changes docs only. It touches no file under `src/` or `tests/`. It edits exactly these lines in `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (M6 addendum):

1. §2, the `AFTER_TURN` bullet (lines 37-40, which ends "Verified with a script against 0.6.7."): add the suffix "Fixed in pygents 0.7.0; see the adoption addendum."
2. §2, the early-exit bullet (lines 43-45, which ends "Callers must consume `run()` to the end."): add the same suffix.
3. §2, the closure-hooks bullet (lines 46-48, which ends "functions defined at module level do not."): add the same suffix.
4. §8, the rule sentence (lines 376-377, "The engine never breaks out of `agent.run()`; it reads results from the pool after the generator ends."): replace the whole sentence with "pygents ≥0.7.0 makes this safe; the engine still consumes `run()` and keeps global module-level hooks by design."
5. §11 Deferred, the item "Upstream pygents fixes: early exit from `run()` raising `SafeExecutionError`; closure hooks colliding in `HookRegistry`." (lines 421-422): mark it done, for example by appending "Done (pygents 0.7.0)." The item stays in the list.

The following files do not change:

- `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` (M7 addendum). Nothing in it forbids breaking out of `run()` or registering closure hooks. Line 49 only notes that early sketches "added ceremony" (including "a re-entry loop around `run()`"); it is not a rule, so it stays. §10 Deferred has no upstream-pygents item. The result must say this edit is a no-op, as the plan's Global Constraint requires.
- `CLAUDE.md` does not carry either rule, so it stays as it is.
- `2026-09-25-pygents-070-adoption-design.md` is the milestone's own spec. It is the only file that quotes the literal rule names "never break or return out of `agent.run()`" and "never register a pygents hook as a closure". It is not a target.
- The other `task-*-design.md` specs from earlier milestones are historical records of those milestones. They are not targets.

The literal rule strings named in §A4 do not appear verbatim in either addendum. The edits go only on the closest real statements listed above. Do not invent rule text to delete. Do not rewrite, reflow or rewrap any other prose. Keep the file's existing hard-wrap on the edited lines.

## Observable behaviour

- After the change, the plan's Step 1 grep (`grep -n "break or return out\|never register a pygents hook as a closure\|closure hooks\|SafeExecutionError\|AgentRegistry\|upstream pygents" docs/superpowers/specs/2026-09-25-*.md CLAUDE.md`) still hits the M6 §2 and §11 lines. Those lines now carry the "Fixed in pygents 0.7.0" suffix or the "done" mark.
- `grep -n "never breaks out of" docs/superpowers/specs/2026-09-25-pygents-engine-design.md` returns nothing.
- `git diff --stat` against the base branch shows exactly one changed file: `2026-09-25-pygents-engine-design.md`, plus this spec file and the plan file if the workflow adds them.

## Error paths

- A target line has drifted on the base branch (different wording or line number): match it by content, not by line number. If the statement is gone, report that and change nothing for it.
- The M7 addendum or `CLAUDE.md` does contain a forbidding rule on the actual base: replace that rule with the same one-liner. Otherwise report "no-op".
- `uv run pytest` fails: the failure cannot come from a docs edit. Report it as a problem on the base branch and do not fix `src/` or `tests/` here.

## Tests

No new tests. The change is docs-only and changes no behaviour. The test-placement rule in §14 of `docs/superpowers/specs/2026-09-23-agent-manager-design.md` assigns tiers by code shape: pure-function unit tests, step tests against a temp git repo or brd board, adapter `build_command` unit tests, fake-adapter engine tests, and the one opt-in end-to-end test. None of those tiers covers markdown content, so no doc-content test is added.

- Verification only: run the existing suite with `uv run pytest` and expect it to pass (existing tiers, unchanged).
- Then commit with the message "docs: retire the pre-0.7 pygents workaround rules".

Note: the upstream exploration summary was cut off at its 8000-character cap, in the middle of its file:line reference list. This spec's line references were re-checked directly against the worktree files, so the truncation does not affect them.

---

# Retire the pre-0.7 pygents workaround rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mark the M6 addendum's pre-0.7 pygents workaround statements as fixed in pygents 0.7.0 and replace its one "never break out of `run()`" rule sentence with the adoption one-liner, touching nothing else.

**Architecture:** Docs-only edit to one markdown file, `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`. Five content-anchored text replacements; the M7 addendum and `CLAUDE.md` are confirmed no-ops by grep. No `src/` or `tests/` change; the existing suite is re-run only to prove nothing broke.

**Tech Stack:** Markdown, `grep`, `git`, `uv run pytest`.

**Spec:** `docs/superpowers/specs/task-retire-the-pre-0-7-4f31e025-design.md` (prepended above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m8/task-retire-the-pre-0-7-4f31e025` on branch `m8/task-retire-the-pre-0-7-4f31e025`, cut from `m8/task-pin-pygents-0822234f`. Run every command from that directory.

## Global Constraints

- Docs only: no file under `src/` or `tests/` changes.
- Only target file: `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`.
- Suffix text, verbatim: "Fixed in pygents 0.7.0; see the adoption addendum."
- Replacement one-liner, verbatim: "pygents ≥0.7.0 makes this safe; the engine still consumes `run()` and keeps global module-level hooks by design."
- Done mark for §11, verbatim: "Done (pygents 0.7.0)."
- Do not rewrite, reflow or rewrap any other prose. Keep the file's existing hard-wrap (continuation lines of a bullet are indented two spaces) on the edited lines.
- Do not invent rule text to delete: the literal §A4 rule names ("never break or return out of `agent.run()`", "never register a pygents hook as a closure") do not appear in either addendum.
- Match target lines by content, not line number. If a target statement is gone, report it and change nothing for it.
- Do not edit `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md`, or any other `task-*-design.md` unless the no-op check in Step 1 finds a real forbidding rule there.
- If `uv run pytest` fails, report it as a base-branch problem; do not fix `src/` or `tests/` here.
- Commit message: "docs: retire the pre-0.7 pygents workaround rules".

## Review Focus

- Drifted target lines: if the base branch's wording or line numbers differ from the spec's, the executor must anchor on content. Step 2's grep counts each anchor phrase before editing; a count of 0 means "report, skip that edit", not "guess".
- Accidental reflow of neighbouring prose: an editor that rewraps a paragraph would silently change lines outside scope. Step 6 checks the diff contains exactly the expected added/removed line counts.
- A forbidding rule actually present in the M7 addendum or `CLAUDE.md` on this base: Step 1 greps both; only a real hit triggers an edit there, otherwise the result reports "no-op".
- Broken markdown bullet continuation: new suffix lines must be indented two spaces so they stay inside their bullet. Step 5 greps for the exact indented form.
- A pre-existing red suite on the base branch: Step 7 runs `uv run pytest`; a failure is reported, not fixed, since a docs edit cannot cause it.

---

### Task 1: Retire the workaround statements in the M6 addendum

**Files:**
- Modify: `docs/superpowers/specs/2026-09-25-pygents-engine-design.md:37-48` (§2 bullets), `:376-377` (§8 rule sentence), `:421-422` (§11 Deferred item)
- Unchanged (verified no-op): `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`, `CLAUDE.md`
- Test: none added (no §14 tier covers markdown content); verification is grep checks plus the existing `uv run pytest`.

**Interfaces:**
- Consumes: nothing from other tasks; the base branch `m8/task-pin-pygents-0822234f` already carries the 0.7.0 code changes the new text points to.
- Produces: the three verbatim strings in Global Constraints, present in the M6 addendum, for later readers and the story's Integrate step.

- [ ] **Step 1: Confirm the M7 addendum and CLAUDE.md are no-ops**

Run:

```bash
grep -n -i "break or return out\|never register a pygents hook as a closure\|closure hooks\|never breaks out of\|SafeExecutionError\|upstream pygents" docs/superpowers/specs/2026-09-25-supervisor-tree-design.md CLAUDE.md
```

Expected: no output (exit status 1). This confirms neither file carries a rule forbidding breaking out of `run()` or closure hooks; record "M7 addendum: no-op; CLAUDE.md: no-op" for the result. Line 49 of the M7 addendum ("a re-entry loop around `run()`") is a note about sketches, not a rule, and stays untouched. If this grep does print a line that forbids breaking out of `run()` or registering closure hooks, replace that sentence with "pygents ≥0.7.0 makes this safe; the engine still consumes `run()` and keeps global module-level hooks by design." keeping the file's wrap, and include that file in Step 8's `git add`.

- [ ] **Step 2: Run the RED check (targets present, new text absent)**

Run:

```bash
f=docs/superpowers/specs/2026-09-25-pygents-engine-design.md
grep -c "Verified with a script against 0.6.7." "$f"
grep -c "turn still marked running. Callers must consume \`run()\` to the end." "$f"
grep -c "functions defined at module level do not." "$f"
grep -c "The engine never breaks out of \`agent.run()\`; it reads results from the pool after the" "$f"
grep -c "closure hooks colliding in \`HookRegistry\`." "$f"
grep -c "Fixed in pygents 0.7.0; see the adoption addendum." "$f"
grep -c "pygents ≥0.7.0 makes this safe" "$f"
grep -c "Done (pygents 0.7.0)." "$f"
```

Expected: `1`, `1`, `1`, `1`, `1`, then `0`, `0`, `0`. The five anchors exist exactly once and none of the new text exists yet. If any of the first five prints `0`, that statement has drifted or is gone: report it and skip its edit below.

- [ ] **Step 3: Add the suffix to the three §2 bullets**

In `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`, make these three exact replacements (each adds one two-space-indented continuation line to the bullet; no existing line changes).

AFTER_TURN bullet. Replace:

```markdown
  not: the finished turn is still `current_turn`, and `from_dict` would replay it.
  Verified with a script against 0.6.7.
```

with:

```markdown
  not: the finished turn is still `current_turn`, and `from_dict` would replay it.
  Verified with a script against 0.6.7.
  Fixed in pygents 0.7.0; see the adoption addendum.
```

Early-exit bullet. Replace:

```markdown
  `SafeExecutionError` when the generator closes: its `finally` resets the hooks of a
  turn still marked running. Callers must consume `run()` to the end.
```

with:

```markdown
  `SafeExecutionError` when the generator closes: its `finally` resets the hooks of a
  turn still marked running. Callers must consume `run()` to the end.
  Fixed in pygents 0.7.0; see the adoption addendum.
```

Closure-hooks bullet. Replace:

```markdown
  hooks defined as closures break on the second agent. Global `@hook(..., tags=...)`
  functions defined at module level do not.
```

with:

```markdown
  hooks defined as closures break on the second agent. Global `@hook(..., tags=...)`
  functions defined at module level do not.
  Fixed in pygents 0.7.0; see the adoption addendum.
```

- [ ] **Step 4: Replace the §8 rule sentence and mark the §11 item done**

In the same file, §8 "Errors", replace:

```markdown
The engine never breaks out of `agent.run()`; it reads results from the pool after the
generator ends.
```

with:

```markdown
pygents ≥0.7.0 makes this safe; the engine still consumes `run()` and keeps global
module-level hooks by design.
```

In §11 "Deferred", replace:

```markdown
- Upstream pygents fixes: early exit from `run()` raising `SafeExecutionError`;
  closure hooks colliding in `HookRegistry`.
```

with:

```markdown
- Upstream pygents fixes: early exit from `run()` raising `SafeExecutionError`;
  closure hooks colliding in `HookRegistry`. Done (pygents 0.7.0).
```

The item stays in the list; nothing else in §8 or §11 changes.

- [ ] **Step 5: Run the GREEN check**

Run:

```bash
f=docs/superpowers/specs/2026-09-25-pygents-engine-design.md
grep -c "^  Fixed in pygents 0.7.0; see the adoption addendum.$" "$f"
grep -n "never breaks out of" "$f"
grep -c "^pygents ≥0.7.0 makes this safe; the engine still consumes \`run()\` and keeps global$" "$f"
grep -c "^module-level hooks by design.$" "$f"
grep -c "^  closure hooks colliding in \`HookRegistry\`. Done (pygents 0.7.0).$" "$f"
grep -n "break or return out\|never register a pygents hook as a closure\|closure hooks\|SafeExecutionError\|AgentRegistry\|upstream pygents" docs/superpowers/specs/2026-09-25-*.md CLAUDE.md
```

Expected: `3`; no output for `never breaks out of`; `1`; `1`; `1`. The last grep (the plan's own Step 1 grep) still hits `2026-09-25-pygents-engine-design.md` on the §2 `SafeExecutionError` line, the §2 `AgentRegistry` line, the "tests clear `ToolRegistry`, `AgentRegistry` and the cache through a fixture." line, and the §11 lines (line numbers shift by up to 3 because of the inserted suffix lines), with the §11 line now ending "Done (pygents 0.7.0)."; the `2026-09-25-pygents-070-adoption-design.md` hits are unchanged.

- [ ] **Step 6: Confirm the diff is exactly the intended hunks**

Run:

```bash
git diff --stat
git diff --numstat
git status --porcelain -- src tests CLAUDE.md docs/superpowers/specs/2026-09-25-supervisor-tree-design.md docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md
```

Expected: `git diff --stat` lists only `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` among tracked files (the spec and this plan may appear as untracked/added by the workflow). `git diff --numstat` shows `6	3	docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (3 new suffix lines + 2 replacement §8 lines + 1 replacement §11 line added; 2 old §8 lines + 1 old §11 line removed). The `git status --porcelain` command prints nothing. If the counts differ, an editor reflowed other prose: run `git checkout -- docs/superpowers/specs/2026-09-25-pygents-engine-design.md` and redo Steps 3-5.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS (exit 0), the same result as on the base branch. A failure cannot come from this docs edit: report it as a problem on `m8/task-pin-pygents-0822234f` and do not change `src/` or `tests/`.

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/specs/2026-09-25-pygents-engine-design.md docs/superpowers/specs/task-retire-the-pre-0-7-4f31e025-design.md docs/superpowers/plans/task-retire-the-pre-0-7-4f31e025.md
git commit -m "docs: retire the pre-0.7 pygents workaround rules

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```

In the result, state: M6 addendum edited (five statements); M7 addendum edit is a no-op (no forbidding rule; line 49 is a note, left alone; §10 has no upstream-pygents item); `CLAUDE.md` is a no-op (carries neither rule); the literal §A4 rule names exist only in `2026-09-25-pygents-070-adoption-design.md`, which is not a target. Also note the upstream exploration summary was truncated at its 8000-character cap mid file:line list; every reference used here was re-checked directly against the worktree, so the truncation did not affect the plan.
