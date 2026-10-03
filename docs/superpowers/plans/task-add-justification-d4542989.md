<!-- task-pipeline: validated -->
# Subtask d4542989: Verify the `justification:` lines on the five e2e tests

Parent: 838df0c9 "Fix the e2e tier's accounting and shrink soak-adjacent timeouts" (milestone 66ed75cd). Source of truth: `docs/superpowers/specs/2026-10-02-test-tier-design.md` decisions V1 (lines 84-92), V2 (lines 94-116) and V9 (lines 163-169).

## Starting state (checked in this worktree, not master)

The sibling e2e-cap story (3aa663b8) has already been integrated into this worktree. That means three things are already in place:

- The collection check exists in `tests/conftest.py`: `E2E_CAP = 5`, `JUSTIFICATION_PREFIX = "justification:"`, `has_justification`, `e2e_tier_violations` and the `E2ETierCap` plugin.
- All five `e2e` tests already have a docstring line that starts with `justification:`.
- Per V2's rule, the sibling wrote these lines. This subtask's job is to check that each one is accurate and to fix any that are not. It does not write them from scratch.

If the implementer finds a different base, where the lines or the check are missing, they must stop and report it. They must not add the collection check here, because that work belongs to the sibling.

## Scope

The only files this subtask may touch are the docstrings of these five tests:

| Test | File:line |
|---|---|
| `test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch` | `tests/e2e/test_real_harness.py:131` (justification at :138) |
| `test_the_run_went_through_the_pygents_walk` | `tests/e2e/test_real_harness.py:187` (justification at :191) |
| `test_the_real_claude_resolves_a_real_merge_conflict_at_integrate` | `tests/e2e/test_real_harness_integrate.py:247` (justification at :255) |
| `test_the_real_claude_drives_a_two_story_milestone_to_done` | `tests/e2e/test_real_harness_milestone.py:263` (justification at :270) |
| `test_the_real_claude_drives_two_independent_stories_in_parallel` | `tests/e2e/test_real_harness_parallel.py:283` (justification at :291) |

## The accuracy check

V1 says a justification must name what `e2e_fake` cannot observe. For each of the five tests, compare its justification against the `e2e_fake` tests that cover the same scenario in `tests/e2e/`. A justification is accurate only if two things hold:

- The `e2e_fake` tier really does not observe the thing it names.
- The test's own assertions really do depend on that thing.

Three outcomes are possible for each test:

- **Accurate:** leave it unchanged.
- **Wrong but a true reason exists:** rewrite the line to state that true reason.
- **No true reason exists:** keep the test, put no placeholder in the docstring, and flag it. "Flag" means a brd comment on d4542989 or the parent 838df0c9, and a note in the PR or commit message. The comment names the test and explains why no honest justification exists.

The test must stay even in the third case. Reducing the tier below 5 tests is out of scope (spec §8), and so is changing what the tests verify. Do not edit a test's original summary prose. The only exception is when that prose is what makes the justification wrong.

Findings from the spec stage that the implementer must resolve:

1. **`test_the_run_went_through_the_pygents_walk`: likely not honestly justifiable.** Its current line gives two reasons: the test costs nothing extra, and it guards against the real run skipping the walk. Neither reason names something `e2e_fake` cannot observe. `tests/e2e/test_production_wiring.py:221` is an identical `e2e_fake` test that makes the same `checkpoint_rows > 0` assertion. The implementer should either find a real-claude-only reason the assertion depends on, or apply the third outcome above and flag it.
2. **`test_the_real_claude_drives_two_independent_stories_in_parallel`: the stated reason is inaccurate.** The line claims `e2e_fake` cannot show two real processes overlapping in wall time. But `tests/e2e/test_parallel_milestone.py:106` already proves overlap with fake-claude subprocesses, using a rendezvous. The rewrite must name only what is truly unobservable there. Candidates are two real model sessions with real tool permissions running at once in sibling worktrees, and overlap that happens on its own under real, variable durations rather than overlap the rendezvous forces. Use only reasons the test's assertions actually depend on.
3. **The other three tests (main toy-card, integrate, milestone): appear accurate.** The first is about the real CLI's argv, exit codes, tool permissions and result-file compliance. The second is about real reasoning over a real conflict. The third is about output and timing variability across sequential real dispatches. Confirm each against the matching `e2e_fake` twin before leaving it unchanged.

Every edited line must still start with `justification:` after leading whitespace is stripped (`has_justification`). Otherwise collection fails with `pytest.UsageError("e2e tier check failed: ...")`.

## Out of scope

- `tests/harness/test_launcher.py`: the soak move and timeout shrinks belong to sibling 1ad895f4.
- The `range(20)` lease-race parametrization in `tests/test_store.py`: belongs to sibling fd4109a3.
- `tests/conftest.py` and `pyproject.toml`: their mechanics belong to the e2e-cap story.
- Any test body or any assertion.
- Adding, removing or re-marking tests.
- Anything under `src/`.

## Tests

This subtask adds and moves no tests. Acceptance rests on the existing tiers:

- **Collection check**, run as part of the default run's collection, `unit`+`git` tiers: `uv run pytest --collect-only -m e2e` lists exactly 5 items with no `e2e tier check failed` error.
- **The five tests themselves**, `e2e` tier (opt-in, real money): they keep their `@pytest.mark.e2e` module marker and are not run by this subtask. The only change is docstring text.
- **Full suite**, default `unit`+`git` run: `uv run pytest` stays green. Only docstrings change, so nothing else can regress.

Verification contract: `uv run pytest`. There is no typecheck or lint step (source: CLAUDE.md).

## Note on inputs

The exploration summary was cut off mid-sentence at 8000 characters, in its file:line reference list. Every reference used in this spec was re-checked directly against this worktree. The missing tail is not relied on.

---

# Verify the e2e `justification:` lines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each of the five `e2e` tests' `justification:` docstring line state only what the `e2e_fake` tier truly cannot observe, rewriting the one inaccurate line, honestly disclosing and flagging the one test with no real reason, and leaving the three accurate lines untouched.

**Architecture:** Docstring-only edits in two files (`tests/e2e/test_real_harness_parallel.py`, `tests/e2e/test_real_harness.py`), checked by the collection-time `E2ETierCap` plugin already in `tests/conftest.py:152-222`. No test body, marker, conftest, pyproject or `src/` change. Each edit is pinned by a transient `grep` RED/GREEN check plus `uv run pytest --collect-only -m e2e`, then the full default suite.

**Tech Stack:** Python, pytest (custom collection hook in `tests/conftest.py`), `uv`, `brd` (board comments).

**Spec:** `docs/superpowers/specs/task-add-justification-d4542989-design.md` (reproduced verbatim above).

## Global Constraints

- Only docstring text of the five tests in `tests/e2e/test_real_harness.py`, `tests/e2e/test_real_harness_integrate.py`, `tests/e2e/test_real_harness_milestone.py`, `tests/e2e/test_real_harness_parallel.py` may change. No test body, assertion, `pytestmark`, fixture, import, conftest, `pyproject.toml` or `src/` edit.
- Every five-test docstring must keep a line that, after `lstrip()`, starts with `justification:` (`tests/conftest.py:156-160` `has_justification`), or collection raises `pytest.UsageError("e2e tier check failed: ...")` for the WHOLE suite, default run included (the plugin is `tryfirst`, ahead of `-m` deselection).
- Exactly 5 items carry `e2e` (`E2E_CAP = 5`, `tests/conftest.py:152`); this subtask adds, removes and re-marks nothing.
- The five `e2e` tests are never run by this subtask (real money). Only `--collect-only -m e2e` and the default `uv run pytest`.
- Do not edit a test's original summary prose (the docstring text above the `justification:` paragraph).
- Verification contract: `uv run pytest`. No typecheck, no lint (CLAUDE.md).
- Plan-stage decision for the "no true reason" outcome: removing the line would fail collection (constraint 2) and editing conftest is out of scope, so the line is kept but its text is replaced with an honest disclosure that begins `justification: none --` and states why there is no real-claude-only reason. This is not a placeholder: a placeholder pretends to justify; this line says, truthfully, that nothing justifies it and points to the flag. The flag itself (brd comment plus commit-message note) is mandatory.

## Accuracy verdicts (made at plan time against the worktree)

| Test | Verdict | Evidence |
|---|---|---|
| main toy-card (`test_real_harness.py:131`) | Accurate, unchanged | Twin `tests/e2e/test_production_wiring.py:41` drives the same `cli.run_card` path with `tests/e2e/fake_claude.py`, whose result files are scripted to validate; the real test validates every result file against `TASK.phase(name).result` (`test_real_harness.py:168-184`), which depends on the real model complying with the brief and the real CLI accepting the argv and tool permissions. |
| pygents walk (`test_real_harness.py:187`) | No true reason: disclose and flag | Twin `tests/e2e/test_production_wiring.py:221` asserts the identical `checkpoint_rows(project, completed_run["run_id"]) > 0` on the identical no-injection `cli.run_card` path; the checkpoint is written by the engine's BEFORE_TURN hook, independent of what the model does. The current line's two reasons ("no added cost", "guards against skipping the walk") name nothing `e2e_fake` cannot observe. |
| integrate (`test_real_harness_integrate.py:247`) | Accurate, unchanged | Twin `tests/e2e/test_integrate.py:254` resolves with `fake_claude.py:794-801`'s mechanical `keep_both_sides`; the real test asserts `{"add", "sub"} <= _top_level_functions(...)`, no markers and a green suite (`test_real_harness_integrate.py:311-359`), which depend on a real model reasoning over the conflicting code. |
| milestone (`test_real_harness_milestone.py:263`) | Accurate, unchanged | Twin `tests/e2e/test_milestone_run.py:69` covers the stacked flow with fixed instant fake replies; the real test needs three sequential real dispatches (real durations, real outputs) to all reach `done` without escalation, and `{"add", "sub", "calc"} <= defined` plus a green suite on the last tip (`test_real_harness_milestone.py:275-336`). |
| parallel (`test_real_harness_parallel.py:283`) | Inaccurate: rewrite | Twin `tests/e2e/test_parallel_milestone.py:106` already proves two fake-claude processes overlap in implement, via a rendezvous (`_run_two_lanes`, `:87-92`). What it cannot observe is unforced overlap of two real, variable-length model sessions (the span assertion at `test_real_harness_parallel.py:370-374`) and each real session landing its own working change on its own branch (`:320-358`). |

## Review Focus

- An edited line that no longer starts with `justification:` after leading whitespace is stripped: the whole default `uv run pytest` must die at collection with `e2e tier check failed`. Pinned by the `--collect-only -m e2e` GREEN step in Tasks 2 and 3 and the full-suite step in Task 4.
- A docstring edit that breaks the triple-quoted string (stray `"""`, lost closing quotes): a `SyntaxError` at collection. Pinned by the same `--collect-only -m e2e` steps, which import each module.
- An edit that slips past the docstring into the test body, `pytestmark` or another test: pinned by the `git diff -U0` check in Tasks 2 and 3, which must show only docstring lines changed.
- The pygents flag existing only in the docstring and never reaching a human: pinned by Task 3 Step 5 (brd comment) and Step 7 (commit-message note), with `brd comment list` to confirm.
- The parallel rewrite claiming something its assertions do not depend on: the new text names only the span assertion and the per-branch function/suite assertions, each cited by line in the verdict table; Task 2 Step 3 re-reads those lines before editing.

---

### Task 1: Confirm the starting state

**Files:**
- Read only: `tests/conftest.py:152-222`, `tests/e2e/test_real_harness.py`, `tests/e2e/test_real_harness_integrate.py`, `tests/e2e/test_real_harness_milestone.py`, `tests/e2e/test_real_harness_parallel.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a confirmed base on which Tasks 2-4 run. If any check here fails, STOP and report; do not add the collection check or the lines yourself (spec "Starting state").

- [ ] **Step 1: Confirm the collection check exists**

Run (from `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-add-justification-d4542989`):

```bash
grep -n -E '^E2E_CAP = 5$|^JUSTIFICATION_PREFIX = "justification:"$|^def has_justification|^def e2e_tier_violations|^class E2ETierCap' tests/conftest.py
```

Expected: five lines (`152`, `153`, `156`, `163`, `195`). Fewer means the wrong base: stop and report.

- [ ] **Step 2: Confirm all five justification lines exist**

```bash
grep -n 'justification:' tests/e2e/test_real_harness.py tests/e2e/test_real_harness_integrate.py tests/e2e/test_real_harness_milestone.py tests/e2e/test_real_harness_parallel.py
```

Expected: exactly five hits, at `test_real_harness.py:138`, `test_real_harness.py:191`, `test_real_harness_integrate.py:255`, `test_real_harness_milestone.py:270`, `test_real_harness_parallel.py:291`.

- [ ] **Step 3: Confirm the baseline collection**

```bash
uv run pytest --collect-only -q -m e2e
```

Expected: the five nodeids below, a summary line reporting 5 selected/collected (the rest deselected), and no `e2e tier check failed`:

```
tests/e2e/test_real_harness.py::test_the_real_claude_drives_the_toy_card_to_done_on_one_tagged_branch
tests/e2e/test_real_harness.py::test_the_run_went_through_the_pygents_walk
tests/e2e/test_real_harness_integrate.py::test_the_real_claude_resolves_a_real_merge_conflict_at_integrate
tests/e2e/test_real_harness_milestone.py::test_the_real_claude_drives_a_two_story_milestone_to_done
tests/e2e/test_real_harness_parallel.py::test_the_real_claude_drives_two_independent_stories_in_parallel
```

No commit: nothing changed.

---

### Task 2: Rewrite the parallel test's inaccurate justification

**Files:**
- Modify: `tests/e2e/test_real_harness_parallel.py:291-295` (docstring of `test_the_real_claude_drives_two_independent_stories_in_parallel`)

**Interfaces:**
- Consumes: Task 1's confirmed base.
- Produces: a docstring whose `justification:` line names unforced real-session overlap and per-branch real work, not process overlap in general.

- [ ] **Step 1: RED: the accurate line is not there yet**

```bash
grep -n 'justification: two real `claude -p` sessions' tests/e2e/test_real_harness_parallel.py
```

Expected: no output, exit status 1.

- [ ] **Step 2: Confirm the twin really proves overlap with fakes**

```bash
grep -n -E 'def _run_two_lanes|rendezvous.arm\(2\)|def test_two_lanes_overlap_in_implement_and_the_milestone_finishes' tests/e2e/test_parallel_milestone.py
```

Expected: hits at `:87`, `:89` and `:106`. This is the evidence that the current line's claim ("a fake-claude stand-in ... cannot exercise genuine scheduling/timing overlap between two real external processes") is false: the twin forces and proves overlap of two real fake-claude subprocesses.

- [ ] **Step 3: Re-read the assertions the new line will cite**

Read `tests/e2e/test_real_harness_parallel.py:320-374` and confirm: independence of the two branches (`:320-325`), per-tip suite green and `add`/`greet` defined (`:340-358`), and the implement-span overlap assertion `a_start < b_end and b_start < a_end` (`:374`). The new line names only these.

- [ ] **Step 4: Replace the justification paragraph**

In `tests/e2e/test_real_harness_parallel.py`, replace exactly this text:

```python
    justification: verifies real process-level concurrency between two
    independently-spawned `claude -p` processes actually overlapping in wall
    time -- a fake-claude stand-in's near-instant, deterministic replies
    cannot exercise genuine scheduling/timing overlap between two real
    external processes."""
```

with:

```python
    justification: two real `claude -p` sessions, each with real tool
    permissions, working sibling worktrees at the same time with nothing
    forcing them to overlap. The `e2e_fake` twin
    (`test_parallel_milestone.py::test_two_lanes_overlap_in_implement_and_the_milestone_finishes`)
    already proves overlap, but only through a rendezvous that holds scripted
    fake processes inside implement until both arrive; it cannot observe
    whether real, variable-length model sessions still overlap unforced (the
    implement-span assertion) and each still lands its own working change
    (`add` in `calc.py`, `greet` in `hello.py`, a green suite on both tips)
    on its own independent branch."""
```

The summary paragraph above it (`:286-289`) stays unchanged.

- [ ] **Step 5: GREEN: the new line is there and collection still passes**

```bash
grep -n 'justification: two real `claude -p` sessions' tests/e2e/test_real_harness_parallel.py
uv run pytest --collect-only -q -m e2e
```

Expected: one grep hit at `:291`; collection lists the same five nodeids as Task 1 Step 3, 5 selected, no `e2e tier check failed`, no `SyntaxError`.

- [ ] **Step 6: Confirm only docstring lines changed**

```bash
git diff -U0 -- tests/e2e/test_real_harness_parallel.py
```

Expected: a single hunk starting at line 291, every `-`/`+` line inside the docstring, the closing `"""` on the last `+` line, nothing else touched.

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_real_harness_parallel.py
git commit -m "test: state what e2e_fake cannot observe in the parallel e2e test

The old justification said e2e_fake cannot show two processes overlapping,
but test_parallel_milestone.py:106 proves that overlap with fake-claude
processes via a rendezvous. The real test's own assertions depend on
unforced overlap of two real model sessions and on each landing its own
working change on its own branch; the line now says that."
```

---

### Task 3: Disclose and flag the pygents-walk test's missing justification

**Files:**
- Modify: `tests/e2e/test_real_harness.py:191-193` (docstring of `test_the_run_went_through_the_pygents_walk`)

**Interfaces:**
- Consumes: Task 1's confirmed base.
- Produces: an honest `justification: none --` line, a brd comment on card d4542989, and a commit message carrying the same flag.

- [ ] **Step 1: RED: the disclosure is not there yet**

```bash
grep -n 'justification: none -- no real-claude-only behavior' tests/e2e/test_real_harness.py
```

Expected: no output, exit status 1.

- [ ] **Step 2: Confirm the identical e2e_fake twin**

```bash
grep -n -A3 'def test_the_run_went_through_the_pygents_walk' tests/e2e/test_production_wiring.py tests/e2e/test_real_harness.py
```

Expected: both hits show the same body, `rows = checkpoint_rows(project, completed_run["run_id"])` and `assert rows > 0, ...`. The twin lives in `tests/e2e/` with no tier marker, so `tests/conftest.py:101,115-128` gives it `e2e_fake`. Both `completed_run` fixtures call `cli.run_card` with no `runner_factory` (`test_real_harness.py:122-128`); the checkpoint comes from the engine's BEFORE_TURN hook, not the model. There is no real-claude-only behavior this assertion depends on: the third outcome applies.

- [ ] **Step 3: Replace the justification paragraph**

In `tests/e2e/test_real_harness.py`, replace exactly this text:

```python
    justification: rides the same real-claude run as this module's main test
    (no separate dispatch, no added cost); without it a refactor could make
    the real run silently skip the pygents walk and nothing would notice."""
```

with:

```python
    justification: none -- no real-claude-only behavior. The `e2e_fake` twin
    `test_production_wiring.py::test_the_run_went_through_the_pygents_walk`
    makes the same `checkpoint_rows > 0` assertion on the same no-injection
    `cli.run_card` path, and the checkpoint is written by the engine's
    BEFORE_TURN hook, not by anything the model does. Kept because shrinking
    the e2e tier below 5 is out of scope (test-tier spec section 8); flagged
    on brd card d4542989 instead of given an invented reason."""
```

The summary paragraph above it (`:188-189`) stays unchanged.

- [ ] **Step 4: GREEN: the disclosure is there and collection still passes**

```bash
grep -n 'justification: none -- no real-claude-only behavior' tests/e2e/test_real_harness.py
uv run pytest --collect-only -q -m e2e
```

Expected: one grep hit at `:191`; the same five nodeids as Task 1 Step 3, 5 selected, no `e2e tier check failed`, no `SyntaxError`.

- [ ] **Step 5: Flag it on the board**

`brd` resolves entity ids by exact match only (no short-prefix lookup:
`brd show d4542989` fails with `CardNotFoundError`, `brd comment add d4542989 ...`
fails with `EntityNotFoundError`). Use the card's full id,
`d4542989-14b3-4b24-bfb6-0bb4c4b3909a` (confirm with `brd list` if it differs
in your base).

```bash
brd comment add d4542989-14b3-4b24-bfb6-0bb4c4b3909a - --author claude <<'EOF'
Flag (V1/V9 accuracy check): tests/e2e/test_real_harness.py::test_the_run_went_through_the_pygents_walk has no honest e2e justification. Its only assertion, checkpoint_rows(project, run_id) > 0, is made identically by the e2e_fake test tests/e2e/test_production_wiring.py::test_the_run_went_through_the_pygents_walk on the same no-injection cli.run_card path, and the checkpoint is written by the engine's BEFORE_TURN hook, independent of the model. The test is kept (shrinking the e2e tier below 5 is out of scope, test-tier spec section 8), and its docstring now says "justification: none -- ..." rather than an invented reason. Follow-up for a human: fold the assertion into the module's main e2e test or retire it, freeing an e2e slot.
EOF
```

- [ ] **Step 6: Confirm the comment landed**

```bash
brd comment list d4542989-14b3-4b24-bfb6-0bb4c4b3909a
```

Expected: the comment from Step 5 is listed.

- [ ] **Step 7: Commit, with the flag in the message**

```bash
git add tests/e2e/test_real_harness.py
git commit -m "test: disclose that the pygents-walk e2e test has no real-claude reason

FLAG: test_real_harness.py::test_the_run_went_through_the_pygents_walk
names nothing e2e_fake cannot observe. test_production_wiring.py makes the
identical checkpoint_rows > 0 assertion under fake claude on the same
cli.run_card path. Kept (the e2e tier may not shrink below 5 in this
work); its justification line now says so instead of giving an invented
reason. Flagged on brd card d4542989 for a human to fold or retire."
```

Then confirm only docstring lines changed in that commit:

```bash
git show -U0 HEAD -- tests/e2e/test_real_harness.py
```

Expected: a single hunk starting at line 191, every changed line inside the docstring, closing `"""` on the last `+` line.

---

### Task 4: Confirm the three accurate lines and run the full suite

**Files:**
- Read only: `tests/e2e/test_real_harness.py:138-142`, `tests/e2e/test_real_harness_integrate.py:255-258`, `tests/e2e/test_real_harness_milestone.py:270-273`

**Interfaces:**
- Consumes: Tasks 2 and 3's commits.
- Produces: a green default suite and an unchanged state for the three accurate tests.

- [ ] **Step 1: Confirm the three accurate lines are untouched**

```bash
git diff HEAD~2 -- tests/e2e/test_real_harness_integrate.py tests/e2e/test_real_harness_milestone.py
git diff HEAD~2 -U0 -- tests/e2e/test_real_harness.py
```

Expected: no output for the integrate and milestone files; the `test_real_harness.py` diff has only the hunk at `:191` (the main toy-card line at `:138-142` unchanged). The verdicts for these three are in the "Accuracy verdicts" table above; re-check them against the cited twin lines if anything in those files differs from the line numbers given.

- [ ] **Step 2: Confirm every five-test line still satisfies the check**

```bash
grep -n -E '^\s*justification:' tests/e2e/test_real_harness.py tests/e2e/test_real_harness_integrate.py tests/e2e/test_real_harness_milestone.py tests/e2e/test_real_harness_parallel.py
```

Expected: exactly five hits: `test_real_harness.py:138`, `test_real_harness.py:191`, `test_real_harness_integrate.py:255`, `test_real_harness_milestone.py:270`, `test_real_harness_parallel.py:291`.

- [ ] **Step 3: Run the full default suite**

```bash
uv run pytest
```

Expected: exit 0, no `e2e tier check failed`, no failures; the five `e2e` tests are deselected (never run).

- [ ] **Step 4: Confirm the branch touched only the two intended files**

```bash
git diff --stat m15/task-cap-the-e2e-tier-and-3aa663b8...HEAD
```

Expected: the plan file and spec (if committed on this branch), `tests/e2e/test_real_harness.py` and `tests/e2e/test_real_harness_parallel.py` only. Nothing under `src/`, no `tests/conftest.py`, no `pyproject.toml`, no `tests/harness/test_launcher.py`, no `tests/test_store.py`.

No commit: nothing changed in this task.
