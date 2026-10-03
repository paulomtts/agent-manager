<!-- task-pipeline: validated -->
# Measure the default tier and run every opt-in tier standalone (75f49b26)

Date: 2026-10-03
Card: 75f49b26-12bb-4d8f-b867-80c042e02ced. Parent story: 1346e04e "Prove the new budget and update the docs". Milestone: 66ed75cd "Milestone 15: test tiers — a fast default suite".
Governing doc: `docs/superpowers/specs/2026-10-02-test-tier-design.md` (the test-tier addendum, which extends design spec §14). §6 "Testing" (the final bullet and the V6/V7/V8 bullet) defines what this subtask has to prove.

## Scope

This is a measurement and regression-proof pass. It changes no source, test, config or doc files. Every earlier story in the milestone (V1–V9) shows `done` on the board, but the board status is not proof that *this* subtask's own worktree/branch was built on top of all nine of them — stories V4-V9 shipped on separate sibling branches and only land together once an Integrate step merges every story tip into a `m15-integrate`-style branch (design spec §12 "Failure, escalation, and blast radius", line 476: "Integrate merges every story tip into one local `<prefix>-integrate` branch... after the last level"). This subtask must not assume that merge has already happened just because the board says so. Its outputs are numbers and pass/fail results. They go in the commit message, which may be an empty/`--allow-empty` commit if nothing else changes, or in a short card comment for `am status` readers.

1. **Integration precondition — confirm the branch actually contains V1-V9, not just the board.** Before measuring anything, run `uv run pytest --collect-only -q` (default tier) and `--collect-only -q -m brd`, `-m e2e_fake`, `-m soak`. The addendum's post-retier shape is: default tier collects roughly 250 fewer items than the full 2750-item suite (most of `test_cli.py`'s and `test_orchestrate.py`'s real-board tests move out), and `-m brd`/`-m e2e_fake`/`-m soak` each collect a substantial, non-trivial number of items (dozens to ~175), not zero and not a single item. If instead the default tier collects nearly all ~2750 items and/or `-m e2e_fake` or `-m soak` collects **zero** tests, that means this worktree's branch predates the V5-V9 merges (the retiering and auto-marking haven't landed here yet) — stop, do not measure, and report the branch as not yet built on the integrated milestone state; that is a blocking precondition for this subtask, not something to fix by rebasing, cherry-picking or re-marking tests here. Only proceed to steps 2-4 once collection confirms the retiered shape.
2. **Quiet-machine precondition.** Before measuring, check for other `pytest` processes, especially in sibling worktrees under `.claude/worktrees/`. Exploration already saw one: `uv run pytest -q` in `m17/task-retry-the-wal-pragma-in-108f4310`. The addendum's own audit (§2, lines 44-46) found that a concurrent run inflates wall time by 10-20%. Wait for those runs to finish before taking the measurement. If the machine never goes quiet, record the concurrent runs next to the number and say the number is inflated.
3. **Default-tier measurement.** Run `uv run pytest --durations=0`. Record:
   - the "before" baseline: 2592 passed in 1267s (addendum §1/§2)
   - the "after" figures: pass/skip/fail counts and wall time
   - the slowest entries from the durations report

   Then confirm the default tier (`unit`+`git`) is at or under the V1 target of 90s serial (addendum §3 line 92).
4. **Opt-in tiers standalone.** Run each of these as its own invocation and confirm it passes:
   - `uv run pytest -m brd`
   - `uv run pytest -m e2e_fake`
   - `uv run pytest -m soak`

   Record the pass count and wall time for each. This is the main regression check: it catches a retiered test that silently depended on fixture state that only existed in the old default run (addendum §6, lines 202-204).
5. **Not run:** `uv run pytest -m e2e`. That tier costs real money and is hard-capped at 5 tests. Run it only if the operator explicitly says to.

## Observable result

The deliverable is a short record containing:
- before and after default-tier wall time
- default-tier pass count
- whether the ≤90s budget was met
- pass count and wall time for each of `brd`, `e2e_fake` and `soak`
- a note saying whether the machine was quiet

`uv run pytest` (the canonical verify command) still passes.

## Error paths

- **The branch is not actually built on the integrated V1-V9 state** (step 1's collection counts don't match the retiered shape — e.g. `-m e2e_fake` or `-m soak` collects zero tests, or the default tier still collects nearly the full ~2750-item suite): stop before measuring. Report this as a blocking precondition gap, not as a regression or a budget failure, and name the collection counts that revealed it. Do not work around it by rebasing, merging sibling branches or re-marking tests in this subtask — that integration is the Integrate step's job, not this one's.
- **Default tier over 90s:** do not "fix" it here by retiering, deleting or editing tests. Record the measured time and the top offenders from `--durations=0`, then report it as a failure of the budget. The rework belongs to a follow-up card, not this one.
- **An opt-in tier fails standalone (but collection counts looked right in step 1):** record which tests failed and the failure output. Report it as a regression from the earlier retier stories. Do not patch fixtures or tests in this subtask.
- **A binary is missing** (`git`/`brd` not on PATH, so the skip hook or `skipif` guard skips the tests): report the skips explicitly. Do not treat a skipped run as a passing one.
- **Machine cannot be made quiet:** say so in the record and qualify the wall-time figures as inflated.

## Tests

This subtask adds no new tests. It only runs existing tiers, and the tier membership it measures is the one the V1 placement rule already assigned (addendum §3, lines 84-92):
- a test is `unit` only if it spawns no subprocess at all and is driven only through injected fakes
- a test is `git` only if it uses real git and nothing else real
- `brd`, `e2e_fake`, `soak` and `e2e` are opt-in tiers, for real brd, fake-claude wiring, deliberate concurrency stress and real-money claude respectively

If some unforeseen need for a test came up, the test would go in its tier by that rule. It would not go in the default tier out of habit.

## Out of scope

- Editing `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 or `CLAUDE.md`. Both belong to the sibling subtask 1ea1036e "Update design spec section 14 and CLAUDE.md", which is blocked on this subtask.
- Any source, test, `pyproject.toml` or `tests/conftest.py` change.
- Using pytest-xdist or parallel execution as the canonical verify command.
- The pygents engine, the checkpoint format, the harness adapter contract, and `dispatch.py`'s `LauncherFn` seam.
- Shrinking or re-scoping the 5 `e2e` tests.
- Milestone 14's `am run --board` work.

---

# Measure the Default Tier and Run Every Opt-in Tier Standalone — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the measurement on collection counts that prove the branch includes the integrated V1-V9 work. If the gate passes, measure the default (`unit`+`git`) tier's wall time against the 90s budget, run `-m brd`, `-m e2e_fake` and `-m soak` each on their own, and record the numbers in an `--allow-empty` commit and a card comment. Change no files in the repo.

**Architecture:** This subtask has no production code and adds no tests, so RED/GREEN works differently here. Each task first writes down its pass criterion as an executable shell check (the "RED" step: the gate exists and says what failing looks like). It then runs the real command and judges the output against that check (the "GREEN" step). Raw logs go to `/tmp/am-75f49b26/`, which is outside the repo, so no report file ever lands in the worktree. Every task ends with an explicit decision: continue, or stop and report. Task 1's stop path is terminal for the whole plan.

**Tech Stack:** `uv`, `pytest` (markers `git`, `brd`, `e2e_fake`, `soak`, `e2e` registered in `pyproject.toml:33-39`; `addopts` at `pyproject.toml:48` deselects the opt-in tiers), bash, `pgrep`, `git`, `brd`.

**Spec:** `docs/superpowers/specs/task-measure-the-default-75f49b26-design.md` (prepended above). Governing doc: `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 (V1, lines 84-92) and §6 (lines 196-207).

**Working directory for every command:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-measure-the-default-75f49b26`

## Pre-read finding: Task 1 is expected to STOP on this branch as cut

The plan author read the code statically while writing the plan and ran nothing. Here is what the branch contains (cut from `m15/task-replace-comment-body-a4e7c1a3`):

- `pyproject.toml:33-48` already has the six markers and the `addopts` deselection (V1 is present).
- `tests/conftest.py` has `FakeBoard`/`fake_board` (V3 is present). It has **no** `pytest_collection_modifyitems` hook (no auto-mark of `tests/e2e/` as `e2e_fake`, no auto-mark of `tests/steps/` as `git`), **no** `pytest_runtest_setup` binary-skip hook, and no per-test budget check. Its only hooks are `pytest_sessionstart`/`pytest_sessionfinish` (lines 62, 66).
- A grep of `tests/` for `e2e_fake|soak|mark.git|mark.brd` finds exactly one marker use, `@pytest.mark.brd` at `tests/test_board.py:1275`. It finds no `e2e_fake` marks and no `soak` marks.

So on this branch, `-m e2e_fake` and `-m soak` will very likely collect **zero** tests, `-m brd` will collect 1, and the default tier will deselect only the 5 `e2e` tests plus that 1 `brd` test. That is exactly the "branch is not built on the integrated V1-V9 state" error path in the spec. The implementer must still run Task 1 and let the real collection counts decide. If the counts confirm this finding, the work ends at Task 1 Step 5 (report and stop). Do **not** rebase, merge sibling branches or add markers to get past the gate.

## Global Constraints

- Change no file in the repo: no source, test, `pyproject.toml`, `tests/conftest.py`, `CLAUDE.md`, or `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. The only git write allowed is one `git commit --allow-empty` (Task 5).
- Never run `uv run pytest -m e2e`, and never use any `-m` expression that selects `e2e`, unless the operator explicitly says to. That tier costs real money and is hard-capped at 5 tests.
- No pytest-xdist and no `-n`. Every measurement is serial.
- Budget: "Default run (`unit`+`git`) target: ≤90s serial" (addendum §3 line 92).
- Before baseline, copied verbatim: 2592 passed in 1267s (addendum §1/§2).
- A skipped test is not a passing test. A run that exits 5 ("no tests collected") is not a pass.
- Canonical verify command: `uv run pytest`.
- Log directory: `/tmp/am-75f49b26/`, outside the repo and never committed.

## Review Focus

1. **Exit code hidden by `tee`.** `uv run pytest ... | tee log` returns `tee`'s exit status, so a failing run looks green. Every run step captures `${PIPESTATUS[0]}` and checks it. Pinned in Tasks 1, 3, 4 and 5.
2. **Exit code 5 (no tests collected) read as success.** `-m soak` collecting nothing would print no failures. Task 1 gates on a count of at least 2 per opt-in tier, and Task 4 treats exit 5 as a failure.
3. **Skips counted as passes when `git`/`brd` is missing from PATH.** Task 2 checks that both binaries are present, and Tasks 3/4 pull the `skipped` count out of the summary line and report any non-zero value explicitly.
4. **A concurrent pytest run from another session starts in the middle of the measurement.** Task 2 checks before the run, and Task 3 Step 4 checks again right after. If the second check finds another run, the figure is marked inflated.
5. **Command-line `-m` vs `addopts -m`.** The opt-in runs depend on a command-line `-m` replacing the `addopts` `-m` (the comment at `pyproject.toml:43-45` says it does). Task 1's collection counts are the check: if `-m brd` collected 0 because `addopts` was combined with it instead of replaced, the gate would catch it.

---

### Task 1: Integration precondition gate (collection shape)

**Files:**
- Create: none in the repo. Logs go to `/tmp/am-75f49b26/collect-{default,brd,e2e_fake,soak}.log`
- Modify: none
- Test: none (runs existing collection only)

**Interfaces:**
- Consumes: nothing.
- Produces: `/tmp/am-75f49b26/gate.txt`, which contains either `GATE=PASS` or `GATE=FAIL`, followed by the four collection summary lines. Tasks 2-5 run only if it says `GATE=PASS`.

- [ ] **Step 1: Write the gate (RED — the pass criterion, written down before any count is seen)**

Create the gate script outside the repo:

```bash
mkdir -p /tmp/am-75f49b26
cat > /tmp/am-75f49b26/gate.sh <<'EOF'
#!/usr/bin/env bash
# Pass criterion from spec step 1: every opt-in tier collects a non-trivial
# number of items (>= 2, "not zero and not a single item"), and the default
# tier deselects a real slice of the suite (>= 100; the addendum expects ~250),
# rather than "nearly all ~2750" still being selected.
set -u
d=/tmp/am-75f49b26
selected() {  # prints the selected count from a `pytest --collect-only -q` log
  local line; line=$(grep -E '(tests? collected|no tests collected)' "$1" | tail -n 1)
  if grep -q 'no tests collected' <<<"$line"; then echo 0; return; fi
  grep -oE '^[0-9]+' <<<"$line" | head -n 1
}
deselected() {
  grep -E 'collected' "$1" | tail -n 1 | grep -oE '[0-9]+ deselected' | grep -oE '[0-9]+' || echo 0
}
def_sel=$(selected $d/collect-default.log); def_desel=$(deselected $d/collect-default.log)
brd=$(selected $d/collect-brd.log); fake=$(selected $d/collect-e2e_fake.log); soak=$(selected $d/collect-soak.log)
verdict=PASS
[ "${def_desel:-0}" -ge 100 ] || verdict=FAIL
for n in "${brd:-0}" "${fake:-0}" "${soak:-0}"; do [ "$n" -ge 2 ] || verdict=FAIL; done
{
  echo "GATE=$verdict"
  echo "default: selected=$def_sel deselected=$def_desel"
  echo "brd: selected=$brd"
  echo "e2e_fake: selected=$fake"
  echo "soak: selected=$soak"
} | tee $d/gate.txt
[ "$verdict" = PASS ]
EOF
chmod +x /tmp/am-75f49b26/gate.sh
```

- [ ] **Step 2: Run the four collections**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-measure-the-default-75f49b26
uv run pytest --collect-only -q            2>&1 | tee /tmp/am-75f49b26/collect-default.log  | tail -n 2; echo "exit=${PIPESTATUS[0]}"
uv run pytest --collect-only -q -m brd      2>&1 | tee /tmp/am-75f49b26/collect-brd.log      | tail -n 2; echo "exit=${PIPESTATUS[0]}"
uv run pytest --collect-only -q -m e2e_fake 2>&1 | tee /tmp/am-75f49b26/collect-e2e_fake.log | tail -n 2; echo "exit=${PIPESTATUS[0]}"
uv run pytest --collect-only -q -m soak     2>&1 | tee /tmp/am-75f49b26/collect-soak.log     | tail -n 2; echo "exit=${PIPESTATUS[0]}"
```

Expected: each command prints a summary line like `2480/2750 tests collected (270 deselected) in 4.10s`, or `no tests collected (2750 deselected)` with `exit=5`. An exit of `2`/`4` (collection or usage error) is not a count. If you see one, stop and report the error text as the blocker.

- [ ] **Step 3: Run the gate**

```bash
/tmp/am-75f49b26/gate.sh; echo "gate_exit=$?"
```

Expected on the integrated milestone state: `GATE=PASS`, `gate_exit=0`. Expected on this branch as cut (see "Pre-read finding"): `GATE=FAIL` with `e2e_fake: selected=0`, `soak: selected=0`, `brd: selected=1`.

- [ ] **Step 4: If `GATE=PASS`, continue to Task 2.** Nothing to commit.

- [ ] **Step 5: If `GATE=FAIL`, report the blocker and STOP the plan here**

Post the blocker on the card (the body goes in on stdin, which is how `brd comment add <id> - --author <author>` reads it, per `src/agent_manager/board.py:128-131`):

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-measure-the-default-75f49b26
{
  echo "75f49b26 blocked: branch is not built on the integrated V1-V9 state; measurement not taken."
  echo
  echo "Collection counts (uv run pytest --collect-only -q [-m <tier>]):"
  sed 1d /tmp/am-75f49b26/gate.txt
  echo
  echo "Branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
  echo "Needs: the m15 Integrate step (all V1-V9 story tips merged) before this subtask can measure."
  echo "Not done here by design (spec Error paths): no rebase, no merge of sibling branches, no re-marking tests."
} | brd comment add 75f49b26-12bb-4d8f-b867-80c042e02ced - --author am
```

Then escalate to the caller with the same text. Do not run Tasks 2-5, do not commit, and do not move the card to `done`.

---

### Task 2: Quiet-machine and binary preconditions

**Files:**
- Create: none in the repo. Log: `/tmp/am-75f49b26/quiet.txt`
- Modify: none
- Test: none

**Interfaces:**
- Consumes: `GATE=PASS` in `/tmp/am-75f49b26/gate.txt` (Task 1).
- Produces: `/tmp/am-75f49b26/quiet.txt`, whose first line is `QUIET=yes`, or `QUIET=no` followed by the competing process lines. Task 5 copies it into the record.

- [ ] **Step 1: Confirm the gate passed**

```bash
head -n 1 /tmp/am-75f49b26/gate.txt
```

Expected: `GATE=PASS`. If it shows anything else, stop: Task 1 Step 5 applies.

- [ ] **Step 2: Confirm both binaries resolve (so tests are not silently skipped)**

```bash
command -v git && git --version
command -v brd && brd --help >/dev/null && echo "brd: ok"
```

Expected: `git --version` prints a version, and `brd --help` prints usage and the chained `echo` prints `brd: ok` (`brd` has no `--version` option — it exits 2 with "No such option: --version" if you try it, which is not a sign brd is missing). If either binary is missing, record it (this runs after Step 4 writes `quiet.txt`, so append it then), carry on, and treat every skip in Tasks 3/4 as caused by the missing binary (spec Error paths: do not count those as passes):

```bash
for b in git brd; do command -v "$b" >/dev/null || echo "MISSING=$b"; done > /tmp/am-75f49b26/missing.txt
cat /tmp/am-75f49b26/missing.txt
```

Task 2 Step 4 appends this file to `quiet.txt`, so the missing binaries end up in the final record.

- [ ] **Step 3: Look for other pytest runs**

```bash
pgrep -af 'pytest' | grep -v -e pgrep -e 'grep' || echo "none"
```

Expected for a quiet machine: `none`. If any line shows up (for example `uv run pytest -q` under `.claude/worktrees/m17/task-retry-the-wal-pragma-in-108f4310`), wait for it to finish. Re-check every 2 minutes for up to 30 minutes:

```bash
for i in $(seq 1 15); do
  others=$(pgrep -af 'pytest' | grep -v -e pgrep -e grep)
  [ -z "$others" ] && break
  echo "waiting ($i/15): $others"; sleep 120
done
```

- [ ] **Step 4: Record quietness**

```bash
others=$(pgrep -af 'pytest' | grep -v -e pgrep -e grep)
if [ -z "$others" ]; then echo "QUIET=yes" > /tmp/am-75f49b26/quiet.txt
else printf 'QUIET=no\n%s\n' "$others" > /tmp/am-75f49b26/quiet.txt; fi
cat /tmp/am-75f49b26/missing.txt >> /tmp/am-75f49b26/quiet.txt
cat /tmp/am-75f49b26/quiet.txt
```

Expected: `QUIET=yes`. With `QUIET=no`, continue anyway, but Task 5 labels every wall-time figure as inflated by roughly 10-20% (addendum §2, lines 44-46).

---

### Task 3: Default-tier measurement against the 90s budget

**Files:**
- Create: none in the repo. Log: `/tmp/am-75f49b26/default.log`, `/tmp/am-75f49b26/default.txt`
- Modify: none
- Test: none (runs the existing default tier)

**Interfaces:**
- Consumes: `/tmp/am-75f49b26/quiet.txt` (Task 2).
- Produces: `/tmp/am-75f49b26/default.txt` with these lines: `exit=`, `summary=`, `seconds=`, `budget=MET|MISSED`, `post_quiet=`, then the 15 slowest durations entries.

- [ ] **Step 1: Write the budget check (RED — the criterion, written before the run)**

```bash
cat > /tmp/am-75f49b26/budget.sh <<'EOF'
#!/usr/bin/env bash
# Pass criterion: exit 0, zero failed/errors, and wall time <= 90s serial
# (addendum §3 line 92). Skips are reported, never counted as passes.
set -u
log=/tmp/am-75f49b26/default.log
summary=$(grep -E '^=+ .*( in [0-9.]+s).* =+$' "$log" | tail -n 1)
secs=$(grep -oE ' in [0-9.]+s' <<<"$summary" | grep -oE '[0-9.]+')
skipped=$(grep -oE '[0-9]+ skipped' <<<"$summary" | grep -oE '[0-9]+' || echo 0)
budget=$(awk -v s="${secs:-99999}" 'BEGIN{print (s<=90)?"MET":"MISSED"}')
echo "summary=$summary"; echo "seconds=$secs"; echo "skipped=$skipped"; echo "budget=$budget"
[ "$budget" = MET ]
EOF
chmod +x /tmp/am-75f49b26/budget.sh
```

- [ ] **Step 2: Run the default tier with full durations**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-measure-the-default-75f49b26
uv run pytest --durations=0 2>&1 | tee /tmp/am-75f49b26/default.log | tail -n 5
echo "exit=${PIPESTATUS[0]}" | tee /tmp/am-75f49b26/default.txt
```

Expected: `exit=0`, and a final line of the form `===== N passed[, M skipped] in S.SSs (...) =====` with no `failed` or `error`.

- [ ] **Step 3: Apply the budget check and capture the slowest entries**

```bash
/tmp/am-75f49b26/budget.sh | tee -a /tmp/am-75f49b26/default.txt; echo "budget_exit=${PIPESTATUS[0]}"
echo "slowest:" >> /tmp/am-75f49b26/default.txt
grep -A 15 -E '^=+ slowest' /tmp/am-75f49b26/default.log | tail -n 15 >> /tmp/am-75f49b26/default.txt
```

Expected: `budget=MET`, `budget_exit=0`, `skipped=0`.

- [ ] **Step 4: Re-check quietness right after the run**

```bash
others=$(pgrep -af 'pytest' | grep -v -e pgrep -e grep)
echo "post_quiet=$([ -z "$others" ] && echo yes || echo "no: $others")" | tee -a /tmp/am-75f49b26/default.txt
```

Expected: `post_quiet=yes`. If it says `no`, the figure counts as inflated even if Task 2 said `QUIET=yes`.

- [ ] **Step 5: Decide**

- `exit=0` and `budget=MET`: continue to Task 4.
- `exit` is not 0 (default-tier failures): record the failing test ids, `grep -E '^(FAILED|ERROR) ' /tmp/am-75f49b26/default.log >> /tmp/am-75f49b26/default.txt`, then continue to Task 4 so the opt-in tiers are measured as well. Task 5 reports this as a regression. Do not edit tests. Exception: if `grep -q 'DATA-DIR GUARD FAILED' /tmp/am-75f49b26/default.log` matches but there are no `FAILED`/`ERROR` test ids, the non-zero exit is `tests/conftest.py`'s real-data-directory guard tripping because a concurrent process (not this run's own tests) wrote to the shared real brd data dir during the run — this is the same contamination Task 2 Step 3 watches for, not a code regression. Re-run the quietness check (Task 2 Step 3/4); once quiet, redo Task 3 once before reporting anything as a regression.
- `budget=MISSED`: the 15 slowest entries are already in `default.txt`. Continue to Task 4. Task 5 reports it as a budget failure that needs a follow-up card. Do not retier, delete or edit tests (spec Error paths).
- `skipped` > 0: keep it for Task 5. If Task 2 recorded `MISSING=`, attribute the skips to the missing binary.

---

### Task 4: Run `brd`, `e2e_fake`, `soak` each standalone

**Files:**
- Create: none in the repo. Logs: `/tmp/am-75f49b26/tier-{brd,e2e_fake,soak}.log`, `/tmp/am-75f49b26/tiers.txt`
- Modify: none
- Test: none (runs the existing opt-in tiers)

**Interfaces:**
- Consumes: Task 1 gate passed.
- Produces: `/tmp/am-75f49b26/tiers.txt`, one line per tier: `<tier> exit=<n> summary=<pytest final line>`, plus `FAILED`/`ERROR` lines for any tier that failed.

- [ ] **Step 1: Write the per-tier criterion (RED)**

```bash
cat > /tmp/am-75f49b26/tier.sh <<'EOF'
#!/usr/bin/env bash
# Usage: tier.sh <brd|e2e_fake|soak>. Pass = exit 0 (exit 5 "no tests
# collected" is a FAIL), zero failed/errors. Skips are reported, not passed.
set -u
t=$1; log=/tmp/am-75f49b26/tier-$t.log
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-measure-the-default-75f49b26
uv run pytest -m "$t" 2>&1 | tee "$log" | tail -n 3
rc=${PIPESTATUS[0]}
summary=$(grep -E '^=+ .* in [0-9.]+s.* =+$' "$log" | tail -n 1)
echo "$t exit=$rc summary=$summary" | tee -a /tmp/am-75f49b26/tiers.txt
[ "$rc" -ne 0 ] && grep -E '^(FAILED|ERROR) ' "$log" | sed "s/^/  $t /" >> /tmp/am-75f49b26/tiers.txt
[ "$rc" -eq 0 ]
EOF
chmod +x /tmp/am-75f49b26/tier.sh
: > /tmp/am-75f49b26/tiers.txt
```

The `-m "$t"` takes exactly one of the three literal tier names. It never takes `e2e` or an expression that would select `e2e`.

- [ ] **Step 2: Run `brd` standalone**

```bash
/tmp/am-75f49b26/tier.sh brd; echo "tier_exit=$?"
```

Expected: `brd exit=0 summary===== N passed in S.SSs ====`, `tier_exit=0`.

- [ ] **Step 3: Run `e2e_fake` standalone**

```bash
/tmp/am-75f49b26/tier.sh e2e_fake; echo "tier_exit=$?"
```

Expected: `e2e_fake exit=0`, `tier_exit=0`, wall time within the ≤8min tier budget (addendum §3).

- [ ] **Step 4: Run `soak` standalone**

```bash
/tmp/am-75f49b26/tier.sh soak; echo "tier_exit=$?"
```

Expected: `soak exit=0`, `tier_exit=0` (this tier has no budget).

- [ ] **Step 5: Decide**

```bash
cat /tmp/am-75f49b26/tiers.txt
```

If all three show `exit=0`, continue to Task 5. If any tier shows a non-zero exit, its `FAILED`/`ERROR` ids are already in `tiers.txt`. Continue to Task 5, which reports it as a regression from the earlier retier stories. Do not patch fixtures or tests (spec Error paths). `exit=5` here means the tier collected nothing even though Task 1 said it would, so report the mismatch alongside it. As in Task 3: if a tier's log has `DATA-DIR GUARD FAILED` (`tests/conftest.py`'s real-data-directory guard) but no `FAILED`/`ERROR` test ids, that is concurrent-process contamination, not a regression — re-check quietness and re-run that tier once quiet before reporting it.

---

### Task 5: Canonical verify, then record the numbers (commit + card comment)

**Files:**
- Create: none in the repo. Log: `/tmp/am-75f49b26/verify.log`, `/tmp/am-75f49b26/record.txt`
- Modify: none (empty commit only)
- Test: none

**Interfaces:**
- Consumes: `quiet.txt` (Task 2), `default.txt` (Task 3), `tiers.txt` (Task 4).
- Produces: one `--allow-empty` commit on `m15/task-measure-the-default-75f49b26` whose message is the record, and the same record as a comment on card 75f49b26.

- [ ] **Step 1: Run the canonical verify command**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-measure-the-default-75f49b26
uv run pytest 2>&1 | tee /tmp/am-75f49b26/verify.log | tail -n 3; echo "verify_exit=${PIPESTATUS[0]}"
```

Expected: `verify_exit=0`.

- [ ] **Step 2: Confirm the worktree is untouched**

```bash
git status --porcelain
```

Expected: no output. Any output means a file changed, which this subtask forbids. Restore it with `git checkout -- <path>` (tracked files) or remove it (untracked) before going on.

- [ ] **Step 3: Assemble the record**

```bash
d=/tmp/am-75f49b26
{
  echo "test: measure default tier and run opt-in tiers standalone (75f49b26)"
  echo
  echo "No files changed; measurement and regression record only."
  echo
  echo "Before (addendum §1/§2): 2592 passed in 1267s."
  echo "After, default tier (unit+git, uv run pytest --durations=0):"
  grep -E '^(exit|summary|seconds|skipped|budget|post_quiet)=' $d/default.txt | sed 's/^/  /'
  echo "Budget: <=90s serial (addendum §3 line 92)."
  echo
  echo "Opt-in tiers, each run standalone:"
  sed 's/^/  /' $d/tiers.txt
  echo
  echo "Machine:"
  sed 's/^/  /' $d/quiet.txt
  echo
  echo "Slowest default-tier entries:"
  sed -n '/^slowest:/,$p' $d/default.txt | sed 1d | head -n 10 | sed 's/^/  /'
  echo
  echo "Canonical verify (uv run pytest): $(grep -E '^=+ .* in [0-9.]+s.* =+$' $d/verify.log | tail -n 1)"
  echo "Not run: -m e2e (real money, needs explicit operator go-ahead)."
} > $d/record.txt
cat $d/record.txt
```

Expected: every section has a value filled in. If `QUIET=no` or `post_quiet=no...` appears, add the following line by hand under "Machine:": `Wall-time figures are inflated (concurrent pytest runs; addendum §2 estimates 10-20%).`

- [ ] **Step 4: Commit the record as an empty commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-measure-the-default-75f49b26
git commit --allow-empty -F /tmp/am-75f49b26/record.txt
git log -1 --stat
```

Expected: a new commit with no files in `--stat`.

- [ ] **Step 5: Post the same record on the card**

```bash
brd comment add 75f49b26-12bb-4d8f-b867-80c042e02ced - --author am < /tmp/am-75f49b26/record.txt
```

Expected: a JSON envelope `{"ok": true, ...}`.

- [ ] **Step 6: Final outcome**

- All green (`verify_exit=0`, `budget=MET`, all three tiers `exit=0`, `skipped=0`, quiet): the card's work is verified and committed.
- Anything else (budget missed, a tier failed, unexplained skips, inflated figures): the record already says so. Report it to the caller as a failure of the named kind, not as success, and leave the follow-up rework to a new card. Nothing in this subtask fixes it.
