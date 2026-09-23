# Subtask 5ee2ee50 — Port the review gate, the plan-hash gate and `count_of`

Parent story: `90d6bbf6` "Gates: the reducers ported from task.js" (milestone `352e955b`, Milestone 1: walking skeleton). Source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §5 "Reducers to port faithfully", §6 phase contract, §14 Testing. Behavioural specification: `.claude/workflows/task.js` PURE region (lines 85, 92-108, 163-194) and the sibling plugin's `/home/paulomtts/Code/leave-me-alone/plugins/leave-me-alone/workflows/task.test.mjs` lines 59-138 and 189-219.

## Scope

Add three more pure reducers to the existing `src/agent_manager/steps/reducers.py` — `count_of`, `review_gate`, and the plan-hash logic — and their unit tests to the existing `tests/steps/test_reducers.py`. Work resumes from branch `m1/task-port-the-exploration-ef33352b` (worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m1/task-port-the-exploration-ef33352b`), which already carries `verification_gate`, `exploration_output_gate` and the helpers `_json` / `_field`. Those two gates and their tests are the done sibling's (`ef33352b`) and are not to be re-touched; the helpers may be reused.

Out of scope: `short_id` / `stem_of` (already ported elsewhere by `01d725d6`), the `plan_check` and `verify.run_suite` steps that will *call* these gates (`d3feb87e`, `9c3b1ffb`), `status_write_outcome`, `shell_quote` (§5: does not port), and all milestone-level orchestration.

Invariants carried from the story: every reducer is pure — no filesystem, network, model calls or module-level mutable state. Malformed *content* yields a verdict, never an exception. A gate returns `None` to pass or a `dict` to fail; `review_gate` additionally may return a `{"warn": ...}` dict.

## Observable behaviour

### `count_of(value)`

Faithful port of `countOf`. Returns the number when `value` is a real number; returns the parsed number when `value` is a non-blank string that JS `Number()` would parse (whitespace-trimmed decimal, so `"3"` → `3`); otherwise returns a not-a-number sentinel — `float("nan")` — which is neither zero nor a valid integer. `None`, `""`, `"   "`, `"three"` and any other type all reach the sentinel. Python's `bool` is *not* a number here: `True` must reach the sentinel, matching JS where `typeof true !== 'number'`. The port must never coerce an absent count to zero (`int(value or 0)` and friends are forbidden) — that is the exact fabrication the upstream comment at task.js lines 80-84 exists to prevent.

The predicate `review_gate` uses must treat `3.0` (from `"3"`) as an integer and `1.5`, `nan` and `True` as not integers, matching `Number.isInteger`.

### `review_gate(review, branch, base_branch)`

Checks in this order, stopping at the first that fires:

1. **Dirty worktree.** `review["porcelain"]`, coerced as JS `String(x || '')` does (any falsy value becomes `""`) and then stripped, is non-empty → `{"blocked": "tests", "detail": ...}`. The detail says the worktree is still dirty, that nothing was pushed, and carries the porcelain text itself so the evidence travels with the verdict. This runs before any count check, even when the counts are also bad.
2. **Unusable counts.** `count_of(review["commitCount"])` or `count_of(review["taggedCount"])` is not an integer → `{"warn": ...}` whose text names the two raw reported values and ends with "Plan-Hash gate skipped". No `blocked` key. A `review` of `None`, `{}` or any non-mapping lands here rather than raising.
3. **Zero commits** → `{"blocked": "implement", "detail": ...}` naming `branch` and `base_branch` ("branch X has no commits on top of Y — implementation produced nothing to ship.").
4. **Untagged commits** (`tagged_count < commit_count`) → `{"blocked": "implement", "detail": ...}` stating "only N of M commits", explaining a future run would read the branch as stale and hard-reset it, that nothing was pushed, and containing the literal "Do NOT re-run this subtask" language.
5. Otherwise `None`. More trailers than commits is a pass, not a failure.

Counts are read off the mapping defensively (reuse `_field`), and keys stay camelCase (`porcelain`, `commitCount`, `taggedCount`) because these dicts come from harness result JSON. Where a detail or warn string embeds a compared value, render it JS-style rather than with Python `repr`, per the `json.dumps` quirk the sibling gate already documents (`True`/`1` render differently under Python equality than in JSON).

### Plan hash

The card names one `plan_hash_gate`; task.js implements two functions and both behaviours must survive. Port them as `is_plan_hash(value)` and `plan_hash_mismatch(impl_hash, review_hash)`, optionally with a thin `plan_hash_gate` wrapper that returns `None` or a verdict dict; the reconciliation decision is the implementer's, but neither behaviour may be dropped or merged away.

- `is_plan_hash(value)`: `True` only for a `str` of exactly 8 lowercase hex characters. Uppercase, 7 or 9 characters, non-hex letters, empty, leading whitespace, `None` and integers are all `False`. Anchor the pattern at both ends (`fullmatch`, or `^...$` with no trailing-newline laxity — Python `$` also matches before a final newline, so `"a1b2c3d4\n"` must not pass).
- `plan_hash_mismatch(impl_hash, review_hash)`: `None` when either hash is not a valid plan hash (a stage that failed to report its hash says nothing about the other; inventing drift there sends someone after a phantom), and `None` when both are valid and equal. When both are valid and differ, returns the detail string naming both hashes, saying the plan's bytes were "modified after implementation", that every trailer on the branch is stale and a future resume would hard-reset the work.

## Error paths

No input shape raises. `review` may be `None`, a non-mapping, or missing any key; counts may be any type; hashes may be any type. Malformed content always produces a verdict, a warning, or `None`. Nothing in this subtask reads the filesystem or shells out, so there are no I/O error paths.

## Tests

All tests below are **pure-function unit tests** per §14 (lines 477-492): `steps/reducers.py` is named there as a pure module whose tests are ported alongside the logic from the `.test.mjs` behavioural spec, with no harness, filesystem or network. They therefore go in the existing flat `tests/steps/test_reducers.py` alongside the sibling gates' tests — *not* in the "Steps" tier (temporary git repo + temporary brd board), which is reserved for code that touches git or brd; these gates touch neither. No adapter, engine or end-to-end tests are in scope.

Ported from `task.test.mjs` lines 59-138 (`reviewGate`):

1. A clean tree with tagged commits proceeds to Ship (`None`).
2. A dirty worktree blocks with `blocked == "tests"`, detail matching "nothing was pushed" and containing the porcelain line.
3. Whitespace-only porcelain (`"\n"`, `"   "`) is a clean tree — git's trailing newline must not block every run.
4. The dirty-tree check runs before the counts: `{"porcelain": "?? new.js", "commitCount": 0, "taggedCount": 0}` yields `blocked == "tests"`.
5. Zero commits → `blocked == "implement"`, detail naming branch and base.
6. `commitCount=3, taggedCount=2` → `blocked == "implement"`, detail matching "only 2 of 3 commits" and "Do NOT re-run this subtask".
7. `commitCount=3, taggedCount=4` → `None`.
8. Unusable counts warn and skip, parametrised over `commitCount` in `None`, `""`, `"three"`, `1.5`; `taggedCount` in absent/`None`, `float("nan")`, `True` — each returns a truthy `warn` matching "Plan-Hash gate skipped", with no `blocked` key.
9. A missing review object (`None`, `{}`, and — Python's stand-in for `undefined` — an absent mapping) warns instead of raising.
10. Numeric-string counts are usable: `"3"`/`"3"` → `None`; `"3"`/`"2"` → `blocked == "implement"`.

Ported from `task.test.mjs` lines 189-219 (`isPlanHash` / `planHashMismatch`):

11. `is_plan_hash` accepts `"a1b2c3d4"` and `"00000000"`; rejects `"A1B2C3D4"`, `"a1b2c3d"`, `"a1b2c3d4e"`, `"a1b2c3g4"`, `""`, `"  a1b2c3d4"`, `None`, `12345678`, and (Python-specific) `"a1b2c3d4\n"`.
12. Matching hashes report no drift (`None`).
13. A hash that changed mid-run returns a detail naming both hashes and matching "modified after implementation".
14. Drift is not claimed when either hash is unusable: `(None, "a1b2c3d4")`, `("a1b2c3d4", "")`, `("not-a-hash", "a1b2c3d4")`, `(None, None)` all return `None`.

Added for the Python port (no JS counterpart, because JS coercion differs):

15. `count_of` direct tests: `3` → `3`; `"3"` → `3`; `" 3 "` → `3`; `None`, `""`, `"   "`, `"three"`, `True`, `[]`, `{}` → a not-a-number result that is neither `0` nor an integer. Explicitly assert `count_of(True)` is not usable and that no input is silently read as zero.

## Verification

`uv run pytest` (the repo's single verification command per CLAUDE.md; no lint or typecheck step exists).
