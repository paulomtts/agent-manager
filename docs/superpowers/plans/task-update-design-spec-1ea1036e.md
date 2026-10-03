<!-- task-pipeline: validated -->
# Update design spec §14 and CLAUDE.md for the six test tiers (1ea1036e)

Subtask of story 1346e04e "Prove the new budget and update the docs", Milestone 15 (test tiers — a fast default suite). Narrows decision V1 of `docs/superpowers/specs/2026-10-02-test-tier-design.md` (§3, lines 84-92) and its §4 file map (lines 195-196) to the two documentation files. Docs-only: no source, test, `pyproject.toml` or `tests/conftest.py` change.

## Base

Work on the milestone's integrate line (`m15-integrate`, which this worktree already reflects), where the V1-V9 code has landed: `pyproject.toml` already declares the `git`, `brd`, `e2e_fake`, `soak` and `e2e` markers and the `addopts` expression `-m "not brd and not e2e_fake and not soak and not e2e"`. The docs must describe that landed state, not master.

## Scope

### 1. `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 "Testing" (lines 507-522)

- Replace the "Steps" bullet ("against temporary git repositories and a temporary `brd` board; no network") with the six-tier table from V1. One row per tier, columns: tier, marker / how to run, default or opt-in, what belongs there, budget.
  - `unit` — no marker, default — pure functions and anything driven through an injected fake (`FakeLauncher`, `FakeDriver`, a fake `board_api`, `FakeBoard`); no subprocess of any kind — ≤0.5s per test, tier ≤30s.
  - `git` — `@pytest.mark.git`, default — real `git` in `tmp_path` only, no `brd`, no `claude` — ≤2s per test, tier ≤45s.
  - `brd` — `@pytest.mark.brd`, opt-in `uv run pytest -m brd` — the real-`brd` adapter contract — tier ≤90s.
  - `e2e_fake` — `@pytest.mark.e2e_fake`, opt-in `-m e2e_fake` — production wiring under the fake `claude`, one test per scenario family — tier ≤8min.
  - `soak` — `@pytest.mark.soak`, opt-in `-m soak`, nightly — concurrency/race stress — no budget.
  - `e2e` — `@pytest.mark.e2e`, opt-in `-m e2e`, real `claude`, costs real money — hard-capped at 5 tests, each with a `justification:` docstring line naming what `e2e_fake` cannot observe.
- State the default-run target beside the table: `uv run pytest` runs `unit` + `git`, target ≤90s serial.
- State the placement rule in one sentence: a test's tier is chosen by what it actually spawns or touches, not by the directory it lives in.
- Reconcile the existing "End to end" bullet (one opt-in real-harness test) with the `e2e` row so §14 does not contradict itself — describe the tier as it is (opt-in, capped at 5); do not change what those tests verify.
- Leave the "Pure functions", "Adapters" and "Engine" bullets and the closing "The project verifies with `pytest`, matching `brd`." sentence intact except where they must point at the table.
- May reference `2026-10-02-test-tier-design.md` as the detailed rationale.

### 2. `CLAUDE.md` (repo root)

Add a testing/tiers section (or extend "Verification") that:
- says `uv run pytest` runs the default `unit` + `git` tiers;
- names each marker (`git`, `brd`, `e2e_fake`, `soak`, `e2e`) with a one-line meaning and the unmarked = `unit` rule;
- lists the opt-in commands `uv run pytest -m brd`, `uv run pytest -m e2e_fake`, `uv run pytest -m soak`, and `uv run pytest -m e2e` (real money, cap of 5, `justification:` line required);
- gives the placement rule (tier by what the test spawns/touches) so future milestone plans copy the right sentence;
- points at design spec §14 for the table.
Keep existing sections otherwise unchanged; the "no separate lint or typecheck command" line stays.

## Out of scope

- Any code, `pyproject.toml`, `tests/conftest.py` or test file edits (landed by V1-V9 siblings).
- Re-measuring the default tier or running opt-in tiers standalone, or recording before/after numbers (sibling 75f49b26, done).
- pytest-xdist / parallel as the canonical verify command; the pygents engine, checkpoint format, harness adapter contract, or `dispatch.py`'s `LauncherFn` seam; reducing the `e2e` tier below 5 tests or changing what they verify; milestone 14's `am run --board` work.
- Any doc other than the two above.

## Observable behaviour / error paths

No runtime behaviour changes. The only failure mode is the docs disagreeing with the code: marker names, opt-in commands and budgets in both files must match `pyproject.toml` (lines 46-61) and V1 exactly. A reviewer checks that §14 and CLAUDE.md agree with each other and with `pyproject.toml`.

## Tests

No new tests. Per the placement rule (tier by what a test spawns/touches), a docs-only change spawns nothing and adds no test to any tier.

Verification: `uv run pytest` (default `unit` + `git` run) stays green — confirms no non-doc file was accidentally altered. No typecheck or lint command exists.

---

# Six Test Tiers in Design Spec §14 and CLAUDE.md Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document the six pytest tiers (`unit`, `git`, `brd`, `e2e_fake`, `soak`, `e2e`) that already landed in `pyproject.toml` and `tests/conftest.py`, in design spec §14 and in `CLAUDE.md`, so future milestone plans place tests correctly.

**Architecture:** Docs-only change to two files. There is no executable behaviour to test, so each task's "RED" step is a `grep` that shows the required text is absent and the "GREEN" step is a `grep` that shows it is present; the full `uv run pytest` run at the end confirms nothing but docs changed.

**Tech Stack:** Markdown; pytest (via `uv run pytest`) for the final verification only.

**Spec:** `docs/superpowers/specs/task-update-design-spec-1ea1036e-design.md` (prepended above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-update-design-spec-1ea1036e`. Run every command from that directory, on branch `m15/task-update-design-spec-1ea1036e`.

## Global Constraints

- Edit exactly two files: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` and `CLAUDE.md`. No change to `pyproject.toml`, `tests/conftest.py`, any test file, any source file, or any other doc.
- Marker names, exactly: `git`, `brd`, `e2e_fake`, `soak`, `e2e`; an unmarked test is `unit`.
- Opt-in commands, exactly: `uv run pytest -m brd`, `uv run pytest -m e2e_fake`, `uv run pytest -m soak`, `uv run pytest -m e2e`.
- Budgets, exactly: `unit` ≤0.5s per test, tier ≤30s; `git` ≤2s per test, tier ≤45s; `brd` tier ≤90s; `e2e_fake` tier ≤8min; `soak` no budget; `e2e` hard-capped at 5 tests, each with a `justification:` docstring line naming what `e2e_fake` cannot observe.
- Default run: `uv run pytest` runs `unit` + `git`, target ≤90s serial.
- Placement rule sentence: "a test's tier is chosen by what it actually spawns or touches, not by the directory it lives in."
- Do not re-measure the suite or run the opt-in tiers (owned by sibling 75f49b26, done). Do not describe pytest-xdist/parallel as the verify command. Do not change what the `e2e` tests verify.
- The "Pure functions", "Adapters" and "Engine" bullets of §14 and the closing sentence "The project verifies with `pytest`, matching `brd`." stay word-for-word. In `CLAUDE.md` the "Verification" and "Conventions" sections stay word-for-word, including "There is no separate lint or typecheck command."

## Review Focus

- §14 and `CLAUDE.md` disagreeing with each other on a budget or command (for example one says `e2e_fake` ≤8min and the other omits it, or one says `-m e2e_fake` and the other `-m e2e-fake`): a reader of either file should get the same tier facts. Task 3 Step 1 greps both files for every marker and opt-in command.
- Docs disagreeing with `pyproject.toml` lines 46-61 (marker names, the `addopts` `-m "not brd and not e2e_fake and not soak and not e2e"` expression): a reader running the documented command should get the documented tier. Task 3 Step 1 cross-checks the markers against `pyproject.toml`.
- The old "End to end" bullet still saying "one slow, opt-in test" next to a table that says up to 5: a reader should not see §14 contradict itself. Task 1 Step 1/4 greps for the old wording's absence.
- A reader assuming the directory decides the tier because `tests/conftest.py` auto-marks `tests/steps/` as `git` and `tests/e2e/` as `e2e_fake`: they should learn that the auto-mark is only a default and an explicit marker wins. Task 2's CLAUDE.md text states this.
- An accidental edit to a non-doc file (editor reformat, stray save): the default suite should stay green and `git diff --stat` should list only the two docs. Task 3 Steps 2-3.

---

### Task 1: Replace the "Steps" bullet in design spec §14 with the six-tier table

**Files:**
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:507-522`

**Interfaces:**
- Consumes: nothing.
- Produces: §14 heading `## 14. Testing` with the tier table; Task 2's `CLAUDE.md` text points at "design spec §14" and must match this table's markers, commands and budgets.

- [ ] **Step 1: Show the required text is absent (RED)**

Run:

```bash
grep -n '| `e2e_fake`' docs/superpowers/specs/2026-09-23-agent-manager-design.md; echo "table-exit=$?"
grep -n 'not by the directory it lives in' docs/superpowers/specs/2026-09-23-agent-manager-design.md; echo "rule-exit=$?"
grep -n 'one slow, opt-in test' docs/superpowers/specs/2026-09-23-agent-manager-design.md; echo "old-e2e-exit=$?"
grep -n 'temporary `brd` board' docs/superpowers/specs/2026-09-23-agent-manager-design.md; echo "old-steps-exit=$?"
```

Expected: `table-exit=1`, `rule-exit=1`, and the old wording present — line 519 printed with `old-e2e-exit=0`, line 512 printed with `old-steps-exit=0`.

- [ ] **Step 2: Replace the "Steps" bullet with the tier table**

Use an exact string replacement in `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.

Old text (lines 512-513):

```markdown
- **Steps** — against temporary git repositories and a temporary `brd` board;
  no network.
```

New text:

```markdown

Tests are split into six tiers by pytest marker; the rationale is in
`2026-10-02-test-tier-design.md`. A test's tier is chosen by what it actually
spawns or touches, not by the directory it lives in.

| Tier | Marker / how to run | Default or opt-in | What belongs there | Budget |
|---|---|---|---|---|
| `unit` | no marker; `uv run pytest` | default | Pure functions and anything driven through an injected fake (`FakeLauncher`, `FakeDriver`, a fake `board_api`, `FakeBoard`); no subprocess of any kind | ≤0.5s per test, tier ≤30s |
| `git` | `@pytest.mark.git`; `uv run pytest` | default | Real `git` in `tmp_path` only; no `brd`, no `claude` | ≤2s per test, tier ≤45s |
| `brd` | `@pytest.mark.brd`; `uv run pytest -m brd` | opt-in | The real-`brd` adapter contract | tier ≤90s |
| `e2e_fake` | `@pytest.mark.e2e_fake`; `uv run pytest -m e2e_fake` | opt-in | Production wiring under the fake `claude`, one test per scenario family | tier ≤8min |
| `soak` | `@pytest.mark.soak`; `uv run pytest -m soak` | opt-in, nightly | Concurrency/race stress | no budget |
| `e2e` | `@pytest.mark.e2e`; `uv run pytest -m e2e` | opt-in; real `claude`, costs real money | The real-harness runs `e2e_fake` cannot stand in for | hard-capped at 5 tests, each with a `justification:` docstring line naming what `e2e_fake` cannot observe |

The default run, `uv run pytest`, runs `unit` + `git` only; its target is
≤90s serial.

```

(The leading and trailing blank lines end the bullet list before the paragraph/table and restart it before the "Adapters" bullet, so the table renders.)

- [ ] **Step 3: Reconcile the "End to end" bullet with the `e2e` row**

Use an exact string replacement in the same file.

Old text:

```markdown
- **End to end** — one slow, opt-in test that runs a two-subtask toy milestone
  with a real harness, marked and excluded from the default suite.
```

New text:

```markdown
- **End to end** — the `e2e` tier in the table above: slow, opt-in tests that
  run a toy milestone with a real harness, marked and excluded from the default
  suite, hard-capped at 5, each with a `justification:` docstring line.
```

- [ ] **Step 4: Show the required text is present and the rest of §14 is intact (GREEN)**

Run:

```bash
grep -n '| `e2e_fake`' docs/superpowers/specs/2026-09-23-agent-manager-design.md; echo "table-exit=$?"
grep -n 'not by the directory it lives in' docs/superpowers/specs/2026-09-23-agent-manager-design.md; echo "rule-exit=$?"
grep -n '≤90s serial' docs/superpowers/specs/2026-09-23-agent-manager-design.md; echo "default-exit=$?"
grep -n 'one slow, opt-in test' docs/superpowers/specs/2026-09-23-agent-manager-design.md; echo "old-e2e-exit=$?"
grep -n 'temporary `brd` board' docs/superpowers/specs/2026-09-23-agent-manager-design.md; echo "old-steps-exit=$?"
grep -c '^- \*\*\(Pure functions\|Adapters\|Engine\|End to end\)\*\*' docs/superpowers/specs/2026-09-23-agent-manager-design.md
grep -n 'The project verifies with `pytest`, matching `brd`.' docs/superpowers/specs/2026-09-23-agent-manager-design.md
```

Expected: `table-exit=0`, `rule-exit=0`, `default-exit=0`, `old-e2e-exit=1`, `old-steps-exit=1`, the bullet count prints `4`, and the closing sentence is printed once.

Then read `docs/superpowers/specs/2026-09-23-agent-manager-design.md` from the `## 14. Testing` heading to `## 15. Migration` and confirm: the table sits between the "Pure functions" and "Adapters" bullets, has 6 data rows, and every row's values match the Global Constraints above.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-09-23-agent-manager-design.md
git commit -m "docs: replace design spec §14 Steps bullet with the six-tier table (1ea1036e)"
```

---

### Task 2: Add a "Test tiers" section to CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (insert between the "Verification" section ending "There is no separate lint or typecheck command." and the `## Conventions` heading)

**Interfaces:**
- Consumes: Task 1's §14 table (markers, commands, budgets must match it word-for-word in values).
- Produces: nothing later tasks call; Task 3 greps this section.

- [ ] **Step 1: Show the required text is absent (RED)**

Run:

```bash
grep -n '## Test tiers' CLAUDE.md; echo "heading-exit=$?"
grep -n 'uv run pytest -m e2e_fake' CLAUDE.md; echo "e2e_fake-exit=$?"
grep -n 'not by the directory it lives in' CLAUDE.md; echo "rule-exit=$?"
```

Expected: `heading-exit=1`, `e2e_fake-exit=1`, `rule-exit=1`.

- [ ] **Step 2: Insert the section**

Use an exact string replacement in `CLAUDE.md`.

Old text:

```markdown
There is no separate lint or typecheck command.

## Conventions
```

New text:

```markdown
There is no separate lint or typecheck command.

## Test tiers

`uv run pytest` runs the default `unit` + `git` tiers (target ≤90s serial). The
other tiers are opt-in: the `-m` expression in `pyproject.toml`'s `addopts`
excludes them, and a `-m` on the command line replaces it.

- unmarked = `unit` — pure functions and anything driven through an injected
  fake (`FakeLauncher`, `FakeDriver`, a fake `board_api`, `FakeBoard`); no
  subprocess of any kind (stub `brd`/`git`/`claude` on `PATH` exit 99). Default;
  ≤0.5s per test, tier ≤30s.
- `@pytest.mark.git` — real `git` in `tmp_path` only; no `brd`, no `claude`.
  Default; ≤2s per test, tier ≤45s.
- `@pytest.mark.brd` — the real `brd` binary (adapter contract). Opt-in:
  `uv run pytest -m brd`; tier ≤90s.
- `@pytest.mark.e2e_fake` — production wiring under the fake `claude`, one test
  per scenario family. Opt-in: `uv run pytest -m e2e_fake`; tier ≤8min.
- `@pytest.mark.soak` — concurrency/race stress, run nightly. Opt-in:
  `uv run pytest -m soak`; no budget.
- `@pytest.mark.e2e` — real `claude`, costs real money. Opt-in:
  `uv run pytest -m e2e`. Hard cap of 5 tests; each needs a `justification:`
  docstring line naming what `e2e_fake` cannot observe.

Placement rule: a test's tier is chosen by what it actually spawns or touches,
not by the directory it lives in. `tests/conftest.py` auto-marks unmarked items
under `tests/steps/` as `git` and under `tests/e2e/` as `e2e_fake`, but only as a
default — mark the test explicitly when what it spawns says otherwise.

The full table is in design spec §14
(`docs/superpowers/specs/2026-09-23-agent-manager-design.md`).

## Conventions
```

- [ ] **Step 3: Show the required text is present and the other sections are intact (GREEN)**

Run:

```bash
grep -n '## Test tiers' CLAUDE.md; echo "heading-exit=$?"
for cmd in 'uv run pytest -m brd' 'uv run pytest -m e2e_fake' 'uv run pytest -m soak' 'uv run pytest -m e2e`'; do grep -q -- "$cmd" CLAUDE.md && echo "ok: $cmd" || echo "MISSING: $cmd"; done
for m in '@pytest.mark.git' '@pytest.mark.brd' '@pytest.mark.e2e_fake' '@pytest.mark.soak' '@pytest.mark.e2e`'; do grep -q -- "$m" CLAUDE.md && echo "ok: $m" || echo "MISSING: $m"; done
grep -n 'not by the directory it lives in' CLAUDE.md; echo "rule-exit=$?"
grep -n 'There is no separate lint or typecheck command.' CLAUDE.md
grep -c '^## ' CLAUDE.md
```

Expected: `heading-exit=0`; four `ok:` command lines and five `ok:` marker lines, no `MISSING`; `rule-exit=0`; the lint/typecheck line printed once; the `## ` heading count prints `3` (Verification, Test tiers, Conventions).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document the six test tiers and opt-in commands in CLAUDE.md (1ea1036e)"
```

---

### Task 3: Cross-check both docs against pyproject.toml and run the full suite

**Files:**
- Read only: `pyproject.toml:46-61`, `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: the verification evidence for the card.

- [ ] **Step 1: Cross-check markers and the addopts expression**

Run:

```bash
grep -n 'not brd and not e2e_fake and not soak and not e2e' pyproject.toml
for m in git brd e2e_fake soak; do grep -q "\"$m:" pyproject.toml && echo "pyproject ok: $m" || echo "pyproject MISSING: $m"; done
grep -q '"e2e:' pyproject.toml && echo "pyproject ok: e2e"
for f in docs/superpowers/specs/2026-09-23-agent-manager-design.md CLAUDE.md; do for t in '≤0.5s per test' '≤2s per test' '≤8min' '≤90s serial' 'justification:'; do grep -q -- "$t" "$f" && echo "ok $f: $t" || echo "MISSING $f: $t"; done; done
```

Expected: the `addopts` line (line 61) printed; five `pyproject ok:` lines; ten `ok` lines (five per doc), no `MISSING`. If anything is missing, fix the doc (never `pyproject.toml`) and amend the matching task's commit.

- [ ] **Step 2: Confirm only the two docs changed on this branch**

Run:

```bash
git diff --stat m15/task-measure-the-default-75f49b26..HEAD
```

Expected: only these files listed — `CLAUDE.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, plus this card's own spec and plan if they were committed on this branch (`docs/superpowers/specs/task-update-design-spec-1ea1036e-design.md`, `docs/superpowers/plans/task-update-design-spec-1ea1036e.md`). No file under `src/`, `tests/`, and not `pyproject.toml`.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest`
Expected: PASS (exit code 0; the default `unit` + `git` run, opt-in tiers deselected). Do not run `-m brd`, `-m e2e_fake`, `-m soak` or `-m e2e`, and do not record timings — that belongs to sibling 75f49b26.

No commit in this task; Tasks 1 and 2 hold the only changes.
