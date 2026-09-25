<!-- task-pipeline: validated -->
# Document parallel runs — subtask design (a2dad516)

Card: a2dad516 "Document parallel runs". Parent story 4633be8c "Prove it: parallel under a fake claude, against a real harness, and documented", milestone cdbfa10d. Blocked by 2af0e413 (done). Siblings 1976123f and 2af0e413 wrote only `tests/e2e`. This card is docs only.

Governing designs: main spec `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§11, §12) and the parallel addendum `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` (P1–P6, §4 limits, §5 deferred).

## Checkout precondition

Plain master (72a6cff) has no parallel implementation and no `--max-concurrent`. This card's worktree, `.claude/worktrees/m4/task-document-parallel-runs-a2dad516`, is stacked on the m4 tip (2af0e413's branch) and already contains it: `cli.DEFAULT_MAX_CONCURRENT = 4`, `models.RunConfig.max_concurrent_stories` defaults to 4, `orchestrate.run_milestone(max_concurrent=...)`, `escalated_payload` with `also_escalated`/`stopped`. All README claims are written and checked against the source in this worktree. If the stage finds itself on a checkout without `--max-concurrent` in `src/agent_manager/cli.py`, it must stop and report, not write docs against it.

## Scope

Files touched: `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§11 only). No file under `src/` or `tests/` changes.

### 1. README.md, "Milestone runs" section

Edit the existing section (it already has `[--max-concurrent N]`, the default-4 / 1-is-sequential blurb, the dry-run `data.max_concurrent` and per-level `concurrent`, and the exit-2 refusals). Keep those. Add or fix the following:

- **Parallel runs subsection** (new, e.g. `#### Parallel runs`), covering:
  - `--max-concurrent N` bounds how many of one level's stories run at once. Default 4. `1` runs them in sequence, the same as the old sequential runner. The value is recorded in the run's config (`max_concurrent_stories`).
  - What runs together: only stories in the same dependency level. Levels are barriers, so level N+1 starts only after every story of level N has finished. Subtasks in a story always run in order, each stacked on the one before.
  - Stop and `stopped`: the first escalation, or an exception inside a lane, sets a stop. Every other lane checks it before starting its next phase. A phase already running is never interrupted, so a stop waits for the running phase (for example a long `implement`) to finish. The lane's current subtask is then recorded `stopped` and no later level starts.
  - `stopped` versus `escalated`: `escalated` is a failure: a gate gave up, or the lane raised. `stopped` is a clean park between two phases. Nothing failed, and the work done so far is kept.
  - Relaunching the same `am run --milestone` command continues a stopped subtask from where it parked and skips every card already `done`. `am resume <run-id>` on a run that has stopped subtasks is refused, and its message says to relaunch the milestone command (cli.py `find_resumable` remedy text).
  - One process per repository: two `am` processes on the same repository or the same run are not supported (addendum P2, P3).
  - Limits, from addendum §4, stated plainly: (a) the repo's tests run side by side, because each lane's `verify` runs in its own worktree at the same time. Tests that use a fixed port, a shared file or a shared database will collide, and `--max-concurrent 1` is the fix. (b) `uv run pytest` builds a `.venv` in each worktree, which costs time and disk once per lane. (c) Machine load: N lanes means up to N `claude -p` processes at once, and nothing rate-limits them.
- **"What an escalation report contains"**: replace the stale sentence "The run stops at the first subtask that does not finish, and no later subtask or story starts" with the stop semantics above (in brief, pointing to the parallel subsection). State that the top-level `level`, `story`, `subtask`, `failed_phase`, `detail` describe the **first** escalation. Document the two optional keys, each present only when non-empty: `also_escalated`, a list of `{level, story, subtask, failed_phase, detail}` for other lanes that failed before they saw the stop, and `stopped`, a list of `{story, subtask, before_phase}` for parked lanes. Say that a stopped subtask and its story are recorded `stopped`, not `escalated`.
- **"Relaunching resumes"**: extend it to cover stopped work, not only escalations and killed runs.
- **Leftover wording**: line 40, "each story's subtasks run one at a time", is correct (subtasks are sequential) but reword it so it cannot be read as stories running one at a time. Any other "one at a time" wording that refers to stories must say `--max-concurrent 1`.
- **"Not there yet"**: keep Integrate, milestone-aware `am resume`, and `watch`/`retry`/`cancel`. Link the parallel addendum's deferred section, `docs/superpowers/specs/2026-09-24-parallel-stories-design.md#5-deferred`, either in place of or next to the existing orchestration-addendum §4 link. Do not describe per-story readiness, cost capture, the reviewer Plan-Hash brief or Ctrl-C handling as present. Listing them as deferred is optional, and only by linking to §5.

Every command shown in the README must match cli.py's option names in this worktree.

### 2. Main spec §11

Under `## 11. Concurrency` (line 424 in this worktree), add one short `**Status:**` paragraph. It says that as of milestone 4, stories in a dependency level run in parallel, bounded by `max_concurrent_stories` (default 4, set with `am run --milestone --max-concurrent N`), and that levels are barriers. Change nothing else in the spec.

## Observable behavior / acceptance

- The README describes only behavior present in this worktree's `src/`. It contains no claim that a later milestone would have to make true.
- Every command block in the README runs as written against a temporary git repo plus a temporary `brd` board with a small milestone (at least two independent stories in one level, and one story blocked by another). Check by hand:
  - `am run --milestone ... --branch-prefix ... --dry-run --pretty` with no `--max-concurrent` shows `max_concurrent: 4`. With `--max-concurrent 1` and with `2`, each level's `concurrent` equals `min(len(level), N)`. Exit 0, and nothing written.
  - The refusals the README lists: `--max-concurrent 0`, and `--max-concurrent` with `--card`, each exit 2 with nothing on stdout.
  - Milestone resolution errors the README cites give exit 3.
  - Real-run commands (`am run --milestone ... --verify ...`, a relaunch, and `am resume` refusing a run with a stopped subtask) are run with the sibling fake `claude` from `tests/e2e` on `PATH`, not a real harness. If a command cannot be run that way, the README must not present it as verified output.
  - If a command does not run as written, the fix goes in the README, not in `src/`.
- Rule 2: `uv run pytest` (default suite, `-m "not e2e"`) stays green.

## Error paths

- Checkout lacks the parallel implementation: stop and report. Do not document it.
- A README command fails when run as written: fix the README text so it matches the CLI.
- An implementation fact disagrees with these findings (for example a default, a key name or an exit code): the source in this worktree wins, and the discrepancy is noted in the hand-off.

## Tests

Per main spec §14 placement (pure functions get unit tests, steps run against temp repos and a temp board, the engine is driven by a fake adapter, and e2e is opt-in and excluded from the default suite), a docs-only card needs **no new tests**. The existing behavior is already covered by `tests/test_cli.py`, `tests/test_orchestrate.py` and the `tests/e2e` parallel proofs.

- README command check: a **manual** run against a temp repo and temp board, as listed above. It is not added to e2e. If a later stage chooses to automate the dry-run/refusal checks, they belong in the default suite in **`tests/test_cli.py`** (CLI tier, temp board, no network) and not in `tests/e2e`. The real-run checks that need the fake `claude` stay manual for this card.
- Regression: the full default suite, `uv run pytest`, must pass unchanged.

---

# Document Parallel Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document the m4 parallel milestone runner in `README.md` (a new "Parallel runs" subsection, the stop/`stopped` semantics, the extended escalation report, relaunch, limits, deferred work) and add a one-paragraph `**Status:**` note to main spec §11, with every README command checked by hand against a temporary repo and `brd` board.

**Architecture:** Docs only. No file under `src/` or `tests/` changes. Each docs task uses a `grep` check as its RED/GREEN cycle: the check fails on the current text and passes once the edit lands. A final manual task runs every README command against a throwaway git repo plus `brd` board in the session scratchpad, with the sibling fake `claude` (`tests/e2e/fake_claude.py`) first on `PATH`, and the default suite (`uv run pytest`) closes it out.

**Tech Stack:** Markdown, `am` (Typer CLI, console script `am = "agent_manager.cli:app"` in `pyproject.toml`), `brd`, git, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-document-parallel-runs-a2dad516/docs/superpowers/specs/task-document-parallel-runs-a2dad516-design.md` (prepended verbatim above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-document-parallel-runs-a2dad516` unless written absolute. Run every command from that directory unless a step says otherwise.

## Global Constraints

- Files touched: `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§11 only). No file under `src/` or `tests/` changes.
- The README describes only behavior present in this worktree's `src/`. It contains no claim that a later milestone would have to make true.
- Every command shown in the README must match cli.py's option names in this worktree.
- If a command does not run as written, the fix goes in the README, not in `src/`.
- An implementation fact that disagrees with the spec: the source in this worktree wins, and the discrepancy is noted in the hand-off.
- Do not describe per-story readiness, cost capture, the reviewer Plan-Hash brief or Ctrl-C handling as present. Integrate, a milestone-aware `am resume`, and `watch`/`retry`/`cancel` stay under "Not there yet".
- No new tests. If dry-run/refusal checks are ever automated they go in `tests/test_cli.py`, never `tests/e2e`.
- Rule 2: `uv run pytest` (default suite, `-m "not e2e"`) stays green.
- No hard-wrapped prose is required, but the README's existing ~80-column wrapping style is kept in edited paragraphs so diffs read cleanly.

Implementation facts this plan was written against (read from this worktree's `src/`):

- `src/agent_manager/cli.py:833` `DEFAULT_MAX_CONCURRENT = 4`; `:1009-1017` refuse `--max-concurrent` below 1 and with `--card` via `typer.BadParameter` (exit 2); `:917` per-level `"concurrent": min(len(level), max_concurrent)`; `:922` `"max_concurrent"`.
- `src/agent_manager/cli.py:426-443` `select_resumable`: a run with no `started` subtask and at least one `stopped` one raises `NotResumableError` whose message ends "`<ids>` stopped cleanly and did not fail, so relaunch the same `agent-manager run --milestone` command that started this run to continue from where it stopped". `resume` prints `{"ok": false, "error": ...}` and exits 3 (`EXIT_ERROR`, `:1441-1443`). Note the message says `agent-manager`, not `am`: the README must not quote it literally.
- `src/agent_manager/models.py:143` `max_concurrent_stories: int = Field(default=4, gt=0)`.
- `src/agent_manager/orchestrate.py:109-158` `escalated_payload`: top-level keys describe the primary (first) escalation; `also_escalated` is `[{level, story, subtask, failed_phase, detail}]`, `stopped` is `[{story, subtask, before_phase}]`, each present only when non-empty.
- `src/agent_manager/orchestrate.py:383-384` a lane whose stop is already set never starts (story stays `pending`); `:410-413` an `Exception` in a lane is an escalation with `failed_phase` `null` and `detail` `<ExceptionType>: <message>`; `:425-427` a `stopped` summary records the subtask and story `stopped`; `:529-559` levels are barriers (one `ThreadPoolExecutor` per level) and an escalated level returns before the next level.

## Review Focus

- A reader of a repo whose tests bind a fixed port or share a database: expects the README to tell them plainly that the default of 4 lanes will make those tests collide and that `--max-concurrent 1` is the fix. Pinned by Task 1 Step 1's grep for `fixed port` and `--max-concurrent 1`.
- A reader who hits `am resume` on a stopped run and copies the command out of the error: the message says `agent-manager run --milestone`, but the installed script is `am`. The README must tell them to relaunch `am run --milestone` in its own words and must not quote the message. Pinned by Task 2 Step 1's grep that `agent-manager run --milestone` does not appear in `README.md`.
- A reader following in-page links: `[Parallel runs](#parallel-runs)`, `[Relaunching resumes](#relaunching-resumes)` and the `#5-deferred` link into the addendum must resolve to real headings. Pinned by Task 2 Step 5's anchor check.
- A reader who sees "one at a time" and concludes stories are sequential by default: after this card no README line says stories run one at a time except in the phrase with `--max-concurrent 1`. Pinned by Task 1 Step 1's grep over "one at a time".
- A reader who reads "Not there yet" as a list of features that exist: Integrate, per-story readiness, cost capture, Plan-Hash and Ctrl-C handling must not be described as present. Pinned by Task 2 Step 1's grep that "Not there yet" still lists Integrate and that `Ctrl-C`, `Plan-Hash`, `cost capture` and `per-story readiness` appear nowhere in `README.md` (case-insensitive).

---

### Task 0: Confirm the checkout carries the parallel runner

**Files:**
- Read only: `src/agent_manager/cli.py`, `src/agent_manager/models.py`, `src/agent_manager/orchestrate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a go/no-go for Tasks 1-4. On no-go, stop and report; write no docs.

- [ ] **Step 1: Check the branch and the implementation facts**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-document-parallel-runs-a2dad516
git rev-parse --abbrev-ref HEAD
grep -n 'DEFAULT_MAX_CONCURRENT = 4' src/agent_manager/cli.py
grep -n '"--max-concurrent"' src/agent_manager/cli.py
grep -n 'max_concurrent_stories: int = Field(default=4' src/agent_manager/models.py
grep -n 'payload\["also_escalated"\]\|payload\["stopped"\]' src/agent_manager/orchestrate.py
grep -n 'stopped cleanly and did not fail' src/agent_manager/cli.py
```

Expected: branch `m4/task-document-parallel-runs-a2dad516`, and every `grep` prints at least one line. If any `grep` prints nothing, STOP: the checkout lacks the parallel implementation. Report it and do not edit any doc.

- [ ] **Step 2: Baseline the default suite**

Run: `uv run pytest -q`
Expected: all tests pass (no failures, no errors). Record the pass count; Task 4 compares against it.

---

### Task 1: README — reword the intro and add the "Parallel runs" subsection

**Files:**
- Modify: `README.md:39-51` (the "Milestone runs" intro and the `--max-concurrent` blurb)
- Modify: `README.md:106-127` (insert the new subsection after "What a clean run leaves behind", before "What an escalation report contains" at `README.md:129`)

**Interfaces:**
- Consumes: Task 0's go.
- Produces: a heading `#### Parallel runs` (GitHub anchor `#parallel-runs`) that Task 2 links to from the escalation report and "Relaunching resumes".

- [ ] **Step 1: Write the failing doc check (RED)**

Save this as a scratch script (not committed) at `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh`:

```bash
#!/usr/bin/env bash
# Task 1 doc check: fails until the Parallel runs subsection and the reworded intro land.
set -u
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-document-parallel-runs-a2dad516
fail=0
need() { grep -qF -- "$1" README.md || { echo "MISSING: $1"; fail=1; }; }
need '#### Parallel runs'
need 'Levels are barriers'
need 'max_concurrent_stories'
need 'a stop waits for the running phase'
need '`stopped` is not `escalated`'
need 'fixed port, a shared file or a shared database'
need '`--max-concurrent 1`'
need '`.venv` in each worktree'
need 'nothing rate-limits them'
need 'Two `am` processes on the same repository'
# "one at a time" may only appear next to `--max-concurrent 1`, never as a claim about stories.
if grep -n 'one at a time' README.md | grep -v -- '--max-concurrent 1' ; then
  echo "STALE: 'one at a time' without --max-concurrent 1"; fail=1
fi
exit $fail
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh`
Expected: exit 1, with `MISSING: #### Parallel runs` among the lines and a `STALE:` line for `README.md:40` ("each story's subtasks run one at a time").

- [ ] **Step 3: Reword the "Milestone runs" intro**

In `README.md`, replace this exact text (lines 39-51):

```markdown
Drive every remaining subtask of one milestone. A level's stories run side by
side, and each story's subtasks run one at a time, each on its own local branch
stacked on the branch before it:

```bash
am run --milestone "document milestone runs" \
  --branch-prefix m3 \
  --verify "uv run pytest" \
  [--max-concurrent N]
```

`--max-concurrent N` is how many of a level's stories run at once. It defaults
to 4. `--max-concurrent 1` runs stories one at a time, as before.
```

with:

```markdown
Drive every remaining subtask of one milestone. Stories in the same dependency
level run side by side, up to `--max-concurrent` of them at once. Inside a
story, subtasks always run in order, each on its own local branch stacked on
the branch before it:

```bash
am run --milestone "document milestone runs" \
  --branch-prefix m3 \
  --verify "uv run pytest" \
  [--max-concurrent N]
```

`--max-concurrent N` is how many of a level's stories run at once. It defaults
to 4. `--max-concurrent 1` runs a level's stories one at a time, as the runner
did before parallel runs. See [Parallel runs](#parallel-runs) for what runs
together, how a run stops, and the limits.
```

- [ ] **Step 4: Insert the "Parallel runs" subsection**

In `README.md`, find this exact text (end of "What a clean run leaves behind"):

```markdown
Nothing is merged and nothing is pushed. The branches stay local and stacked,
and the base branch does not move. Merging the tips is left to you.

#### What an escalation report contains
```

and replace it with:

```markdown
Nothing is merged and nothing is pushed. The branches stay local and stacked,
and the base branch does not move. Merging the tips is left to you.

#### Parallel runs

`--max-concurrent N` bounds how many stories of one level run at once. It
defaults to 4, and `--max-concurrent 1` runs them in sequence, exactly as the
sequential runner did. The run records the value in its config as
`max_concurrent_stories`.

What runs together:

- Only stories in the same dependency level. Levels are barriers: level N+1
  starts only after every story of level N has finished.
- Never two subtasks of one story. A story's subtasks run in order, each
  stacked on the branch before it.
- Each story runs in its own lane and each subtask in its own worktree, so no
  two lanes share a working directory.

How a run stops. The first escalation in any lane, or an exception raised
inside a lane, sets the run's stop. Every other lane checks the stop before it
starts its next phase. A phase already running is never interrupted, so
a stop waits for the running phase to finish: a lane in the middle of a long
`implement` finishes it and then parks. The subtask that lane was on is
recorded `stopped`, and so is its story. A story of the same level that had
not started yet stays `pending`, and no later level starts.

`stopped` is not `escalated`:

- `escalated` is a failure. A gate gave up (for example `review`), or the lane
  raised an exception. Something needs fixing before you go on.
- `stopped` is a clean park between two phases. Nothing failed, the phases
  that finished are kept, and the subtask's worktree and branch stay where they
  are.

To continue, fix the escalation and relaunch the same `am run --milestone`
command (see [Relaunching resumes](#relaunching-resumes)). The stopped subtask
picks up in its existing worktree, and every card already `done` on the board
is skipped. `am resume <run-id>` does not continue stopped work: on a run with
a stopped subtask it is refused with `{"ok": false, "error": {...}}` and exit
code 3, and the message names the stopped subtasks and says to relaunch the
milestone command.

Run one `am` process per repository. Two `am` processes on the same repository,
or on the same run, are not supported: the run's store and its lock, `git
worktree add`, and the board writes and rollup are serialised inside one
process only.

Limits, stated plainly:

- **Your test suite runs side by side.** Each lane's `verify` phase runs your
  `--verify` commands in its own worktree while other lanes do the same. Tests
  that use a fixed port, a shared file or a shared database will collide. Run
  such a repository with `--max-concurrent 1`.
- **One environment per lane.** `uv run pytest` builds
  a `.venv` in each worktree, which takes time and disk once per lane.
- **Machine load.** N lanes means up to N `claude -p` processes at once, N
  times the request rate, and nothing rate-limits them.

#### What an escalation report contains
```

Note: the Task 1 check greps single lines, so each checked phrase must sit on one line. The text above is wrapped so that `a stop waits for the running phase`, `fixed port, a shared file or a shared database`, `` `.venv` in each worktree `` and `` Two `am` processes on the same repository `` each fall on one line; keep them that way if you re-wrap. The spec's "cli.py `find_resumable`" is `select_resumable` (`src/agent_manager/cli.py:393`) in this worktree; note that in the hand-off.

- [ ] **Step 5: Run the doc check to verify it passes (GREEN)**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh; echo "exit=$?"`
Expected: no `MISSING:` or `STALE:` lines, `exit=0`.

If any `need` fails only because a phrase wrapped across two lines, re-wrap that paragraph so the phrase sits on one line, then re-run.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Document parallel milestone runs in the README

Add a Parallel runs subsection: the --max-concurrent bound, level
barriers, the cooperative stop, stopped versus escalated, relaunching,
one process per repository, and the addendum section 4 limits. Reword
the intro so it cannot be read as stories running one at a time.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 2: README — escalation report keys, relaunch of stopped work, "Not there yet"

**Files:**
- Modify: `README.md` "What an escalation report contains" (originally `README.md:129-142`)
- Modify: `README.md` "Relaunching resumes" (originally `README.md:144-154`)
- Modify: `README.md` "Not there yet" (originally `README.md:156-163`)

**Interfaces:**
- Consumes: the `#### Parallel runs` heading from Task 1 (anchor `#parallel-runs`).
- Produces: the final README; Task 4 runs its commands.

- [ ] **Step 1: Write the failing doc check (RED)**

Save as `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task2.sh`:

```bash
#!/usr/bin/env bash
# Task 2 doc check: fails until the report keys, relaunch text and deferred link land.
set -u
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-document-parallel-runs-a2dad516
fail=0
need() { grep -qF -- "$1" README.md || { echo "MISSING: $1"; fail=1; }; }
never() { if grep -niF -- "$1" README.md; then echo "FORBIDDEN: $1"; fail=1; fi; }
need 'describe the first escalation'
need '`also_escalated`'
need '{"level", "story", "subtask", "failed_phase", "detail"}'
need '{"story", "subtask", "before_phase"}'
need 'recorded `stopped`, not `escalated`'
need 'a stopped lane or a killed run'
need 'parallel-stories-design.md#5-deferred'
need 'There is no Integrate step'
never 'The run stops at the first subtask that does not finish'
never 'agent-manager run --milestone'
never 'Ctrl-C'
never 'Plan-Hash'
never 'cost capture'
never 'per-story readiness'
exit $fail
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task2.sh`
Expected: exit 1, with `MISSING: describe the first escalation`, `MISSING: parallel-stories-design.md#5-deferred`, and `FORBIDDEN: The run stops at the first subtask that does not finish` among the lines.

- [ ] **Step 3: Rewrite "What an escalation report contains"**

In `README.md`, replace this exact text:

```markdown
The run stops at the first subtask that does not finish, and no later subtask
or story starts. It exits 1, and `data` holds `escalated` (`true`), `run_id`,
`level`, `story`, `subtask`, `failed_phase`, `detail` and `warnings`.
`failed_phase` is the phase that gave up (for example `review`) and `detail`
says why. When the subtask's driver raised instead, `failed_phase` is `null`
and `detail` is `<ExceptionType>: <message>`. The subtask, its story and the
run are recorded `escalated`, and `am status <run_id>` shows the whole plan. A
coder that reports `blocked` ends its subtask escalated at `implement`, and
review never runs.
```

with:

```markdown
The first escalation stops the run: the other lanes of its level park at their
next phase boundary, and no later level starts (see
[Parallel runs](#parallel-runs)). The run exits 1, and `data` holds `escalated`
(`true`), `run_id`, `level`, `story`, `subtask`, `failed_phase`, `detail` and
`warnings`. These top-level fields describe the first escalation.
`failed_phase` is the phase that gave up (for example `review`) and `detail`
says why. When the subtask's driver raised instead, `failed_phase` is `null`
and `detail` is `<ExceptionType>: <message>`. The escalated subtask, its story
and the run are recorded `escalated`, and `am status <run_id>` shows the whole
plan. A coder that reports `blocked` ends its subtask escalated at `implement`,
and review never runs.

Two more keys appear only when they are not empty:

- `also_escalated`: a list of
  `{"level", "story", "subtask", "failed_phase", "detail"}`, one for each other
  lane that failed before it saw the stop.
- `stopped`: a list of `{"story", "subtask", "before_phase"}`, one for each lane
  the stop parked. `before_phase` is the phase it would have run next. A
  stopped subtask and its story are recorded `stopped`, not `escalated`.
```

- [ ] **Step 4: Extend "Relaunching resumes" and "Not there yet"**

In `README.md`, replace this exact text:

```markdown
To go on after an escalation or a killed run, fix the cause and run the same
`am run --milestone` command again. It starts a new run that skips every card
already `done` on the board. A subtask that was killed part way picks up in its
existing worktree and does not redo a plan that already passed. Relaunching a
finished milestone drives nothing and reports `done` with an empty
`completed`.

`am resume <run-id>` is not milestone-aware and does not continue a milestone.
Relaunch the `am run --milestone` command instead.

#### Not there yet

- There is no Integrate step: nothing merges the story tips into one branch.
- `am resume` is not milestone-aware.
- `watch`, `retry` and `cancel` do not exist.

See section 4 of the
[orchestration addendum](docs/superpowers/specs/2026-09-24-orchestration-design.md#4-deferred-to-the-follow-up-milestone-found-now-not-cut-yet).
```

with:

```markdown
To go on after an escalation, a stopped lane or a killed run, fix the cause
and run the same `am run --milestone` command again. It starts a new run that
skips every card already `done` on the board. A subtask that was stopped or
killed part way picks up in its existing worktree and does not redo a plan
that already passed. Relaunching a finished milestone drives nothing and
reports `done` with an empty `completed`.

`am resume <run-id>` is not milestone-aware and does not continue a milestone.
On a run with a stopped subtask it is refused with exit code 3, and its message
says to relaunch. Relaunch the `am run --milestone` command instead.

#### Not there yet

- There is no Integrate step: nothing merges the story tips into one branch.
- `am resume` is not milestone-aware.
- `watch`, `retry` and `cancel` do not exist.

See section 4 of the
[orchestration addendum](docs/superpowers/specs/2026-09-24-orchestration-design.md#4-deferred-to-the-follow-up-milestone-found-now-not-cut-yet)
and section 5 of the
[parallel-stories addendum](docs/superpowers/specs/2026-09-24-parallel-stories-design.md#5-deferred)
for everything deferred.
```

- [ ] **Step 5: Run the doc checks and the anchor check (GREEN)**

Run:

```bash
bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh; echo "task1 exit=$?"
bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task2.sh; echo "task2 exit=$?"
grep -n '^#### Parallel runs$' README.md
grep -n '^#### Relaunching resumes$' README.md
grep -n '^## 5\. Deferred$' docs/superpowers/specs/2026-09-24-parallel-stories-design.md
grep -n '^## 4\. Deferred to the follow-up milestone' docs/superpowers/specs/2026-09-24-orchestration-design.md
```

Expected: `task1 exit=0`, `task2 exit=0`, and each of the four anchor `grep`s prints exactly one line (GitHub slugs `parallel-runs`, `relaunching-resumes`, `5-deferred`, and the existing orchestration §4 slug).

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Document the parallel escalation report and relaunching stopped work

The top-level escalation fields describe the first escalation, and the
optional also_escalated and stopped keys are listed with their shapes.
Relaunching now covers stopped lanes, am resume's refusal on a stopped
run is stated, and Not there yet links the parallel addendum section 5.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 3: Main spec §11 — the Status note

**Files:**
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:424-429` (insert after the `## 11. Concurrency` heading only)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing later tasks rely on.

- [ ] **Step 1: Write the failing doc check (RED)**

Run:

```bash
awk '/^## 11\. Concurrency$/{on=1;next} /^## 12\./{on=0} on' docs/superpowers/specs/2026-09-23-agent-manager-design.md | grep -F '**Status:**'
```

Expected: no output, exit 1 (§11 has no Status note yet).

- [ ] **Step 2: Insert the Status paragraph**

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, replace this exact text:

```markdown
## 11. Concurrency

Stories within a dependency level run in parallel, bounded by
```

with:

```markdown
## 11. Concurrency

**Status:** as of milestone 4, stories in a dependency level run in parallel,
bounded by `max_concurrent_stories` (default 4, set with
`am run --milestone --max-concurrent N`). Levels are barriers: the next level
starts only after every story of the current one has finished. See the
parallel-stories addendum, `2026-09-24-parallel-stories-design.md`.

Stories within a dependency level run in parallel, bounded by
```

- [ ] **Step 3: Run the check to verify it passes (GREEN), and that nothing else moved**

Run:

```bash
awk '/^## 11\. Concurrency$/{on=1;next} /^## 12\./{on=0} on' docs/superpowers/specs/2026-09-23-agent-manager-design.md | grep -F '**Status:**'
git diff --stat -- docs/superpowers/specs/2026-09-23-agent-manager-design.md
git diff -U0 -- docs/superpowers/specs/2026-09-23-agent-manager-design.md | grep '^-[^-]'
```

Expected: the first command prints the `**Status:**` line; `--stat` shows only insertions (`6 insertions(+)`); the last command prints nothing (no line of the spec was removed or changed).

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-23-agent-manager-design.md
git commit -m "$(cat <<'EOF'
Note in spec section 11 that stories now run in parallel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 4: Run every README command by hand against a temp repo and board, then the full suite

**Files:**
- Create (scratch, never committed): `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/setup.sh`
- Modify only if a command fails as written: `README.md`

**Interfaces:**
- Consumes: the final `README.md` from Tasks 1-2; `tests/e2e/fake_claude.py` (read only, copied to a scratch `claude`).
- Produces: the hand-off evidence (exit codes and key fields) for the final report.

- [ ] **Step 1: Write the temp repo + board setup script**

Save as `/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/setup.sh`:

```bash
#!/usr/bin/env bash
# A throwaway repo on `master` that is also a brd board, with one milestone:
# A (a1 -> a2) and B (b1 -> b2) independent in level 0, C (c1) blocked by A in level 1.
# The milestone title contains "document milestone runs" so the README's commands match it as written.
set -euo pipefail
SCRATCH=/tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check
WT=/home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-document-parallel-runs-a2dad516
rm -rf "$SCRATCH/repo" "$SCRATCH/xdg" "$SCRATCH/bin" "$SCRATCH/rendezvous"
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

add() { (cd "$R" && brd add --title "$1" ${2:+--parent "$2"}) | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["id"])'; }
block() { (cd "$R" && brd block "$1" --by "$2" >/dev/null); }
M=$(add "Milestone: document milestone runs")
A=$(add "Story A: independent" "$M")
B=$(add "Story B: independent of A" "$M")
C=$(add "Story C: blocked by A" "$M"); block "$C" "$A"
A1=$(add "a1: first of A" "$A"); A2=$(add "a2: second of A" "$A"); block "$A2" "$A1"
B1=$(add "b1: first of B" "$B"); B2=$(add "b2: second of B" "$B"); block "$B2" "$B1"
C1=$(add "c1: only of C" "$C")
cat > "$SCRATCH/env.sh" <<EOF
export XDG_DATA_HOME="$SCRATCH/xdg"
export PATH="$SCRATCH/bin:\$PATH"
export R="$R" WT="$WT" SCRATCH="$SCRATCH"
export M=$M A=$A B=$B C=$C A1=$A1 A2=$A2 B1=$B1 B2=$B2 C1=$C1
am() { uv run --project "\$WT" am "\$@"; }
EOF
echo "setup done: source $SCRATCH/env.sh"
```

- [ ] **Step 2: Build the fixture**

Run: `bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/setup.sh`
Expected: `setup done: ...`. Then, in the same shell for every later step: `source /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check/env.sh && cd "$R"`, and `command -v claude` prints `$SCRATCH/bin/claude`.

- [ ] **Step 3: Dry-run, exactly as the README writes it, plus `--max-concurrent 1` and `2`**

Run (the README's dry-run line with `--base-branch` left at its default `master`):

```bash
am run --milestone "document milestone runs" --branch-prefix m3 --dry-run --pretty; echo "exit=$?"
am run --milestone "document milestone runs" --branch-prefix m3 --dry-run --max-concurrent 1 | python3 -c 'import json,sys;d=json.load(sys.stdin)["data"];print(d["max_concurrent"],[(l["level"],len(l["stories"]),l["concurrent"]) for l in d["levels"]])'
am run --milestone "document milestone runs" --branch-prefix m3 --dry-run --max-concurrent 2 | python3 -c 'import json,sys;d=json.load(sys.stdin)["data"];print(d["max_concurrent"],[(l["level"],len(l["stories"]),l["concurrent"]) for l in d["levels"]])'
git branch --format='%(refname:short)'; ls .claude/worktrees 2>/dev/null; ls "$XDG_DATA_HOME" 2>/dev/null
```

Expected: first command prints `"max_concurrent": 4`, level 0 with two stories and `"concurrent": 2`, level 1 with one story and `"concurrent": 1`, and `exit=0`. Second prints `1 [(0, 2, 1), (1, 1, 1)]`. Third prints `2 [(0, 2, 2), (1, 1, 1)]`. The last line lists only `master`, no worktree directory, and no run directory (nothing written). If any differs, the source wins: fix the README text and note it for the hand-off.

- [ ] **Step 4: The usage refusals (exit 2, nothing on stdout) and the resolution errors (exit 3)**

Run:

```bash
out=$(am run --milestone "document milestone runs" --branch-prefix m3 --max-concurrent 0 2>/dev/null); echo "exit=$? stdout=[$out]"
out=$(am run --card "$A1" --branch-prefix m3 --max-concurrent 2 2>/dev/null); echo "exit=$? stdout=[$out]"
am run --milestone "no such milestone anywhere" --branch-prefix m3 --dry-run; echo "exit=$?"
am run --milestone "Story" --branch-prefix m3 --dry-run; echo "exit=$?"
```

Expected: the two refusals print `exit=2 stdout=[]`. The unknown milestone prints `{"ok": false, "error": {...}}` and `exit=3`. The `"Story"` needle matches no root card (stories are not roots), so it is also `{"ok": false, ...}` with `exit=3`. The README's other listed refusals (`--card` with `--milestone`, neither, blank `--milestone`, `--dry-run` with `--card`) were verified by the earlier cards and are unchanged by this one; spot-check one: `out=$(am run --card "$A1" --milestone x --branch-prefix m3 2>/dev/null); echo "exit=$? stdout=[$out]"` prints `exit=2 stdout=[]`.

- [ ] **Step 5: A real run that escalates in one lane and stops the other**

The fake `claude` fails `review` for any branch named in `.git/fake-claude-review-fail`, and the rendezvous makes lanes A and B be inside `implement` together (as `tests/e2e/test_parallel_milestone.py::_launch_with_a1_review_failing` does). Run:

```bash
A1_BRANCH=$(am run --milestone "document milestone runs" --branch-prefix m3 --dry-run | python3 -c "import json,sys;d=json.load(sys.stdin)['data'];print([s['branch'] for l in d['levels'] for st in l['stories'] for s in st['subtasks'] if s['id']=='$A1'][0])")
echo "$A1_BRANCH" > .git/fake-claude-review-fail
export FAKE_CLAUDE_RENDEZVOUS_DIR="$SCRATCH/rendezvous" FAKE_CLAUDE_RENDEZVOUS_COUNT=2
am run --milestone "document milestone runs" --branch-prefix m3 --verify "git rev-parse --verify HEAD" --max-concurrent 2 --pretty | tee "$SCRATCH/first.json"; echo "exit=${PIPESTATUS[0]}"
```

`--verify "git rev-parse --verify HEAD"` stands in for the README's `"uv run pytest"`: the toy repo has no Python project, and what is being checked is the command shape and the report, not the suite.

Expected: `exit=1`; `data.escalated` is `true`, `data.story` is `$A`, `data.subtask` is `$A1`, `data.failed_phase` is `"review"`; `data.stopped` is a one-element list `{"story": "$B", "subtask": <$B1 or $B2>, "before_phase": <a phase name>}`; no `also_escalated` key. Then `am status "$(python3 -c "import json;print(json.load(open('$SCRATCH/first.json'))['data']['run_id'])")" --pretty` shows story A `escalated`, story B `stopped`, story C `pending`. If `stopped` is absent (lane B finished first; the ordering margin is not guaranteed), re-run from Step 2 once; if it is absent again, record that the stopped case was not reproduced by hand and rely on `tests/e2e/test_parallel_milestone.py` for it, and say so in the hand-off.

- [ ] **Step 6: `am resume` refuses the stopped run**

Run:

```bash
RUN1=$(python3 -c "import json;print(json.load(open('$SCRATCH/first.json'))['data']['run_id'])")
am resume "$RUN1" --verify "git rev-parse --verify HEAD" --pretty; echo "exit=$?"
```

Expected: `{"ok": false, "error": {...}}` whose message contains `stopped cleanly and did not fail, so relaunch the same` and `run --milestone`, and `exit=3`. This matches the README's "refused with exit code 3, and its message says to relaunch".

- [ ] **Step 7: Relaunch continues the stopped work and skips done cards**

Run:

```bash
rm .git/fake-claude-review-fail
unset FAKE_CLAUDE_RENDEZVOUS_DIR FAKE_CLAUDE_RENDEZVOUS_COUNT
am run --milestone "document milestone runs" --branch-prefix m3 --verify "git rev-parse --verify HEAD" --max-concurrent 2 --pretty | tee "$SCRATCH/second.json"; echo "exit=${PIPESTATUS[0]}"
am run --milestone "document milestone runs" --branch-prefix m3 --verify "git rev-parse --verify HEAD" --pretty; echo "exit=$?"
```

Expected: the relaunch prints `data.done` `true`, `exit=0`, and `data.completed` does not contain any subtask that was already `done` after the first run (for example `$B1` when the stop parked `$B2`). `brd show "$M"` reports the milestone `done`. The second relaunch (the README's "Relaunching a finished milestone") prints `done: true` with an empty `completed` and `exit=0`.

- [ ] **Step 8: Fix the README if any step above did not run as written**

For each mismatch found in Steps 3-7, edit only `README.md` so it matches what the CLI did (never `src/`), re-run the failing step, then re-run both doc checks:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-document-parallel-runs-a2dad516
bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task1.sh && bash /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/check_task2.sh; echo "exit=$?"
```

Expected: `exit=0`. If nothing mismatched, skip this step and record "no README changes from the hand check".

- [ ] **Step 9: Confirm no src/tests change and run the full default suite**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m4/task-document-parallel-runs-a2dad516
git diff --name-only m4/task-add-the-opt-in-real-2af0e413 -- src tests
git status --porcelain
uv run pytest -q
```

Expected: the first command prints nothing (no file under `src/` or `tests/` differs from the base branch); `git status --porcelain` shows only uncommitted README fixes from Step 8 (if any) and this plan file if it is not yet committed; `uv run pytest -q` passes with the same count as Task 0 Step 2.

- [ ] **Step 10: Commit any README fixes from the hand check**

Only if Step 8 changed `README.md`:

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Match the README's parallel-run commands to what the CLI does

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

Then clean up the scratch fixture: `rm -rf /tmp/claude-1000/-home-paulomtts-Code-agent-manager/35908f83-f2db-4359-8e6d-4e671783ea06/scratchpad/readme-check`.

Hand-off notes to carry forward: the `am resume` refusal text says `agent-manager run --milestone` while the installed script is `am` (documented in the README's own words, not quoted); `run_milestone`'s Python default is `max_concurrent=1` while the CLI default is 4 (the README documents the CLI); and any fact from Steps 3-7 that disagreed with the spec.
