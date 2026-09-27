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
