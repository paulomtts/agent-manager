# Document milestone runs (subtask 7b6ad9bd)

Parent story 5017dd8c "Prove it against a real harness, and document it"; milestone 99e178cb. Blocked by f9e7bf4c (opt-in real-harness milestone test, done). This subtask is docs only: it edits `README.md` and adds one note to section 10 of the main design spec. It touches nothing under `src/` or `tests/`.

## Precondition

The implementation is merged into this worktree: `src/agent_manager/orchestrate.py`, `census.py`, `cli.py` with `--milestone` / `--dry-run` (`run` at line ~951, `_check_run_targets` at ~920, `dry_run_payload` at ~840) and `tests/e2e/test_milestone_run.py` / `test_real_harness_milestone.py` are all present. Write every statement from that code, not from the addendum. If the code and the addendum disagree, the README describes the code and the difference is reported in the handoff, not fixed here.

## Scope

### 1. README.md: a "Milestone runs" section

Add it under `## Usage`, after the existing `am run --card` / `am resume` text and before the JSON-envelope paragraph (or after it, as long as the envelope paragraph still applies to every command). Leave the existing content as it is. The section covers, in this order:

- **The command.** `am run --milestone <id|title> --branch-prefix <p> --verify '<cmd>'` (`--verify` is repeatable, passed through as written, run in the order given; `--allow-no-verification` is the explicit opt-out; `--repo-dir` defaults to `.`, `--base-branch` to `master`). `--milestone` takes a card id, an exact title (case-insensitive), or a title substring that matches exactly one milestone. An ambiguous substring is an error that lists the matches. `--card` and `--milestone` cannot be used together, one of them is required, a blank `--milestone` is refused, and `--dry-run` with `--card` is refused. All of these are usage errors.
- **`--dry-run` and the base column.** It prints `{"levels": [{"level", "stories": [{"story", "title", "root", "subtasks": [{"id", "title", "status", "branch", "base"}]}]}], "already_done": [...]}` and writes nothing: no run directory, no branches, no board changes. How to read it: each subtask's `base` is the previous subtask's branch in the story's full order, so a done subtask still anchors the next one. A story's first subtask builds on its blocker's tip. A blocked story whose root is the base branch means a `blocked_by` edge is missing on the board. Blocker cycles, and stories with two or more in-milestone blockers, are refused before anything is written.
- **What a clean run leaves behind.** Stories run one at a time, level by level. Subtasks run in order, each on its own local branch stacked on the previous one. The run does `git fetch origin` once (only if an `origin` remote exists) and `git worktree prune` once. Board statuses roll up to the story and the milestone. The payload is `{"done": true, "run_id", "levels": [{"level", "stories": [ids]}], "completed": [subtask ids], "tips": [{"story", "tip"}], "warnings"}`, and the exit code is 0. `tips` lists the branches for a human to merge. Nothing is merged or pushed.
- **What an escalation report contains.** The run stops at the first subtask that does not finish, whether it escalated or its driver raised. The payload is `{"escalated": true, "run_id", "level", "story", "subtask", "failed_phase", "detail", "warnings"}` and the exit code is `EXIT_ESCALATED` (1). The subtask, the story and the run are recorded as escalated. A coder that reports `blocked` ends the subtask escalated at `implement`, and review never runs. A handled error prints `{"ok": false, "error": ...}` and exits with `EXIT_ERROR` (3).
- **Relaunching resumes.** Running the same command again skips cards that are already done on the board. A killed subtask picks up in its existing worktree and does not redo a plan that already passed. Say plainly that `am resume <run-id>` is not milestone-aware: to continue a milestone, run the same `am run --milestone` command again.
- **What is not there.** Stories do not run in parallel (at most one at a time). There is no Integrate step. `am resume` is not milestone-aware. `watch`, `retry` and `cancel` do not exist. Link `docs/superpowers/specs/2026-09-24-orchestration-design.md`, section "4. Deferred to the follow-up milestone (found now, not cut yet)".

Confirm each key name and exit code against `orchestrate.run_milestone` (lines ~235-355), `cli.dry_run_payload`, `orchestrate.story_tips` and `cli.EXIT_*` as you write. Use the real names from the code, not the list above if they differ. Use the same prose style as the rest of the README (short paragraphs, fenced `bash` blocks).

### 2. Main spec, section 10: a Status note

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, add a short `Status:` paragraph at the end of section 10 ("CLI surface", before `## 11. Concurrency`). It says what exists now: `run` (with `--card`, or `--milestone` with `--dry-run`, `--repo-dir`, `--base-branch`, `--branch-prefix`, `--verify`, `--allow-no-verification`), `status`, `runs`, `logs` and `resume` (single card only). It says what is deferred: `watch`, `retry`, `cancel`, `--workflow`, `--harness`, `--max-concurrent` (runs are sequential for now) and a milestone-aware `resume`. It points to addendum section 4. Get the list of existing commands from the `@app.command(...)` registrations in `cli.py`. Do not change anything else in the spec, including the synopsis block.

## Verification (by hand, no new tests)

Run every command the README shows against a temporary git repo with a temporary `brd` board. Never use the real board. A command that does not work exactly as written is a README bug. Scratch files go in the session scratchpad. At minimum:

- `am run --milestone <title substring> --branch-prefix p --dry-run --repo-dir <tmp>`: check the payload shape and the bases on a board with two stories where one blocks the other and one story has two subtasks. Also check that the tmp repo has no new run directory and no new branches afterwards.
- Each refusal the README names: `--card` together with `--milestone`, neither flag, a blank `--milestone`, `--dry-run` with `--card`, an ambiguous substring. Each should give an error envelope and a non-zero exit.
- The clean-run and escalation payloads and the relaunch behavior: either drive them once with the fake claude from `tests/e2e/fake_claude.py` on PATH, or read them from `tests/e2e/test_milestone_run.py`, which already asserts them through the production path. Do not call the real `claude`: it costs money. Any fake used by hand follows the milestone-2 rule: it knows only what the brief says (no plan hash, no committing the spec or plan, and the result path comes only from the prompt text).
- `uv run pytest` stays green. Nothing is expected to change, since no code or tests are touched.

## Tests

None added. Per the main spec section 14 and addendum O8, unit, step, engine and e2e tiers exist for code. This card changes no code, and the milestone-run behavior the README describes is already covered in the default suite by `tests/e2e/test_milestone_run.py` (fake claude on PATH) and in the opt-in tier by `tests/e2e/test_real_harness_milestone.py` (`pytest -m e2e`). The README check above is a manual step, not a test file.

## Out of scope

Any change to `src/` or `tests/`. Parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, review counts measured by git, verification discovery: document these as absent and do not start any of them. Do not change the addendum.

---

# Document milestone runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document `am run --milestone` (command, `--dry-run` and its base column, clean-run and escalation payloads, relaunch, what is missing) in `README.md`, add a Status note to section 10 of the main design spec, and prove every README command by running it against a throwaway git repo and `brd` board.

**Architecture:** Docs only. Every statement is taken from the code already merged in this worktree: `src/agent_manager/cli.py` (`EXIT_ESCALATED = 1` at line 45, `EXIT_ERROR = 3` at line 48, `error_envelope` at line 166, `dry_run_payload` at line 840, `already_done_entries` at line 813, `_check_run_targets` at line 920, `run` at line 951), `src/agent_manager/orchestrate.py` (`story_tips` at line 118, `refresh_git` at line 139, `run_milestone` at lines 235-356), `src/agent_manager/census.py` (`find_milestone` at line 73) and `src/agent_manager/dag.py` (`story_root` / `stack_bases` at lines 277-337). The "test" for a README is running it: the RED step of each docs task is a `grep` that shows the text is missing, and Task 2 drives every README command by hand against a scratch board, with a fake `claude` on `PATH` so no money is spent.

**Tech Stack:** Markdown; `am` (Typer CLI, entry point `am = "agent_manager.cli:app"` in `pyproject.toml`); `git`; `brd`; `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd/docs/superpowers/specs/task-document-milestone-runs-7b6ad9bd-design.md` (prepended above).

## Global Constraints

- Docs only: no file under `src/` or `tests/` changes. The only files touched are `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.
- The README describes the code, not the addendum. Where they disagree, the README follows the code and the difference goes in the handoff note (Task 4), not into a fix.
- Leave the existing README content as it is. The new section goes under `## Usage`, after the JSON-envelope paragraph, before `## Develop`.
- In the main spec change nothing except adding the Status paragraph at the end of section 10, before `## 11. Concurrency`. The synopsis block stays as it is.
- Do not change `docs/superpowers/specs/2026-09-24-orchestration-design.md`.
- Never use the real board or the real `claude` (it costs money). The by-hand check uses a scratch repo, a scratch `brd` board, `XDG_DATA_HOME` inside the scratch dir, and the fake `tests/e2e/fake_claude.py` first on `PATH`, unmodified (milestone-2 rule: the fake knows only what the brief says).
- Match the README's existing style: short paragraphs, fenced `bash` blocks, prose wrapped at about 80 columns like the surrounding text.
- Verification command: `uv run pytest` (no lint, no typecheck).

## Review Focus

- A refusal a reader expects to be JSON is not JSON. `_check_run_targets` raises `typer.BadParameter`, so `--card` with `--milestone`, neither flag, a blank `--milestone`, and `--dry-run` with `--card` are Typer usage errors: message on stderr, empty stdout, exit 2. Only the ambiguous or unknown milestone comes back as the `ok: false` envelope with exit 3. The README must say this, and Task 2 Step 5 checks each case's stdout, stderr and exit code. (The spec's own verification bullet says "error envelope" for all five; that is a spec/code difference to report in Task 4, not to paper over.)
- A reader runs the README's dry-run command exactly as printed, from inside their repo, with no `--repo-dir`. Task 2 Step 4 runs it from the scratch repo's directory with the default `--repo-dir .` and `--base-branch master`, not with extra flags.
- A reader trusts "writes nothing". Task 2 Step 4 checks there is no `runs` directory under the scratch `XDG_DATA_HOME`, no new branch, no new worktree, and an unchanged `brd tree` before and after.
- A reader relaunches after an escalation and expects the done subtask to still anchor the next one. Task 2 Step 8 previews again after the escalation and checks the done subtask moved to `already_done` while the next subtask's `base` is still its branch.
- A reader relaunches a milestone that is already finished and expects nothing to happen. Task 2 Step 10 checks it exits 0 with `done: true` and an empty `completed`, and that `am resume <run-id>` on an escalated milestone run refuses (exit 3) instead of continuing the milestone (Task 2 Step 8).

---

### Task 1: Add the "Milestone runs" section to README.md

**Files:**
- Modify: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd/README.md:34-37` (insert between the envelope paragraph ending at line 35 and `## Develop` at line 37)

**Interfaces:**
- Consumes: nothing from other tasks. Facts come from the code lines named in Architecture.
- Produces: the README text Task 2 runs. Task 2 depends on these exact strings: the milestone needle `"document milestone runs"`, `--branch-prefix m3`, the section heading `### Milestone runs`, and the addendum link target `docs/superpowers/specs/2026-09-24-orchestration-design.md#4-deferred-to-the-follow-up-milestone-found-now-not-cut-yet`.

- [ ] **Step 1: Show the section is missing (RED)**

Run:
```bash
grep -n -e '--milestone' -e 'Milestone runs' /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd/README.md; echo "grep exit=$?"
```
Expected: no matching lines and `grep exit=1`.

- [ ] **Step 2: Re-check the facts the text states against the code**

Read these lines and confirm each against the text in Step 3 before writing. If one differs, write the code's version into Step 3's text and note the difference for Task 4.
- `src/agent_manager/cli.py:45-49`: `EXIT_ESCALATED = 1`, `EXIT_ERROR = 3`.
- `src/agent_manager/cli.py:166-169`: the error envelope is `{"ok": False, "error": {"type": <class name>, "message": <str>}}`.
- `src/agent_manager/cli.py:813-883`: dry-run keys `levels` / `level` / `stories` / `story` / `title` / `root` / `subtasks` / `id` / `title` / `status` / `branch` / `base`, and `already_done` entries `{"kind": "story", "id", "title"}` and `{"kind": "subtask", "id", "title", "story"}`.
- `src/agent_manager/cli.py:920-948`: the four `typer.BadParameter` refusals.
- `src/agent_manager/cli.py:969-979`: `--repo-dir` default `.`, `--base-branch` default `master`, `--branch-prefix` required.
- `src/agent_manager/census.py:73-108`: exact id, then exact case-insensitive title, then a substring matching exactly one root; zero or several matches raise `MilestoneNotFoundError` (a `ValueError`, so exit 3 with the envelope).
- `src/agent_manager/orchestrate.py:118-136, 139-150, 312-354`: `tips` entries `{"story", "tip"}` for every story with subtasks; `git fetch origin` only when a remote named exactly `origin` exists, then `git worktree prune`; the escalation dict keys; `failed_phase` is `None` and `detail` is `"<ExceptionType>: <message>"` when the driver raised; the clean dict keys.
- `src/agent_manager/dag.py:277-337`: `root` is `--base-branch` with no in-milestone blocker, the single blocker's tip otherwise, `StackRootError` with two or more; each later subtask's base is the previous subtask in the full list.
- `src/agent_manager/cli.py:393-433`: `select_resumable` refuses a run with zero or several `started` subtasks.
- `src/agent_manager/dag.py:17-25` and `src/agent_manager/dag.py:78-80`: branch names are `<prefix>/task-<slug>-<short id>`; `cli.worktree_for` puts worktrees under `<repo>/.claude/worktrees/<branch>` (see the `worktree` fixture comment in `tests/e2e/conftest.py:214-219`).

- [ ] **Step 3: Insert the section**

Use Edit on `README.md`. old_string (exact, lines 35-37):

```text
`{"ok": false, "error": {...}}` on a refusal. Add `--pretty` to indent it.

## Develop
```

new_string:

````markdown
`{"ok": false, "error": {...}}` on a refusal. Add `--pretty` to indent it.

### Milestone runs

Drive every remaining subtask of one milestone, one at a time, each on its own
local branch stacked on the branch before it:

```bash
am run --milestone "document milestone runs" \
  --branch-prefix m3 \
  --verify "uv run pytest"
```

`--milestone` takes the milestone card's id, its exact title (case does not
matter), or a piece of its title that matches exactly one root card. A piece
that matches several root cards is refused with the list of matches, and one
that matches none is refused with the list of root cards. Both come back as
`{"ok": false, "error": {...}}` with exit code 3.

`--branch-prefix` is required. Every branch the run cuts is named
`<prefix>/task-<title slug>-<first 8 hex of the card id>`, and its worktree is
`<repo>/.claude/worktrees/<branch>`. `--verify` is repeatable, passed through as
written, and run in the order given. With no `--verify`, pass
`--allow-no-verification` to opt out on purpose; with neither, the verification
gate refuses to go on. `--repo-dir` defaults to `.` and `--base-branch` to
`master`.

Some combinations are refused before anything is read: `--card` together with
`--milestone`, neither of them, a blank `--milestone`, and `--dry-run` with
`--card`. These are usage errors, like a missing `--branch-prefix`: Typer prints
the message on stderr, nothing is printed on stdout, and the exit code is 2.

#### Preview with `--dry-run`

```bash
am run --milestone "document milestone runs" --branch-prefix m3 --dry-run --pretty
```

The preview reads the board and writes nothing: no run directory, no branch, no
worktree, no board change. It exits 0. `data.levels` is a list of
`{"level", "stories"}`. Each story is `{"story", "title", "root", "subtasks"}`,
and each subtask is `{"id", "title", "status", "branch", "base"}`. Only the
subtasks still to run are listed. `data.already_done` lists what will not run:
`{"kind": "story", "id", "title"}` for a story with nothing left, and
`{"kind": "subtask", "id", "title", "story"}` for a done subtask of a story that
still has work.

Read the `base` column before a real run:

- A story with no blocker inside the milestone has `root` equal to
  `--base-branch`, and its first subtask builds on it.
- A story blocked by another story in the milestone roots on that story's tip,
  the branch of its last subtask, even when that story is already done.
- Every later subtask builds on the previous subtask's branch in the story's
  full order. A done subtask is not listed, but its branch still anchors the
  next one.
- A story you expected to wait on another, whose `root` is the base branch, is
  missing a `blocked_by` edge on the board. Add it with
  `brd block <story> --by <blocker>` and preview again.
- A cycle between stories, or a story blocked by two or more stories in the
  milestone, is refused with exit code 3 before anything is written. A stack
  roots on one branch only.

#### What a clean run leaves behind

Stories run one at a time, level by level, and each story's subtasks run in
order. Before the first subtask the run does `git fetch origin` once (only when
a remote named `origin` exists) and `git worktree prune` once. Each finished
subtask is `done` on the board, and the rollup moves its story and the
milestone with it.

A clean run exits 0, and `data` holds:

- `done`: `true`.
- `run_id`: the run, for `am status <run_id>` and `am logs`.
- `levels`: the stories this run had work for, as `{"level", "stories"}` with
  story ids.
- `completed`: the subtask ids finished in this run, in order.
- `tips`: `{"story", "tip"}` for every story in the milestone that has
  subtasks, naming the branch its stack ends on.
- `warnings`: board writes that failed but did not stop the run, as text.

Nothing is merged and nothing is pushed. The branches stay local and stacked,
and the base branch does not move. Merging the tips is left to you.

#### What an escalation report contains

The run stops at the first subtask that does not finish, and no later subtask
or story starts. It exits 1, and `data` holds `escalated` (`true`), `run_id`,
`level`, `story`, `subtask`, `failed_phase`, `detail` and `warnings`.
`failed_phase` is the phase that gave up (for example `review`) and `detail`
says why. When the subtask's driver raised instead, `failed_phase` is `null`
and `detail` is `<ExceptionType>: <message>`. The subtask, its story and the
run are recorded `escalated`, and `am status <run_id>` shows the whole plan. A
coder that reports `blocked` ends its subtask escalated at `implement`, and
review never runs.

When the run cannot start at all (an unknown or ambiguous milestone, a blocker
cycle, a board error), it prints `{"ok": false, "error": {...}}` and exits 3.

#### Relaunching resumes

To go on after an escalation or a killed run, fix the cause and run the same
`am run --milestone` command again. It starts a new run that skips every card
already `done` on the board. A subtask that was killed part way picks up in its
existing worktree and does not redo a plan that already passed. Relaunching a
finished milestone drives nothing and reports `done` with an empty
`completed`.

`am resume <run-id>` is not milestone-aware and does not continue a milestone.
Relaunch the `am run --milestone` command instead.

#### Not there yet

- Stories never run in parallel: one story at a time, even inside a level.
- There is no Integrate step: nothing merges the story tips into one branch.
- `am resume` is not milestone-aware.
- `watch`, `retry` and `cancel` do not exist.

See section 4 of the
[orchestration addendum](docs/superpowers/specs/2026-09-24-orchestration-design.md#4-deferred-to-the-follow-up-milestone-found-now-not-cut-yet).

## Develop
````

- [ ] **Step 4: Show the section is there (GREEN)**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && grep -c -e '--milestone' README.md && grep -n -e '^### Milestone runs' -e '^## Develop' -e '^## Usage' README.md && git diff --stat
```
Expected: a count of at least 5; `## Usage` before `### Milestone runs` before `## Develop`; `git diff --stat` lists `README.md` only, with insertions and 0 deletions.

- [ ] **Step 5: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && git add README.md && git commit -m "docs(readme): document am run --milestone" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Run every README milestone command against a scratch repo and board

No file in the repository is created here. All scratch files go under the session scratchpad. If any command in this task does not behave as the README says, the README is wrong: fix the README text (Edit on `README.md`), re-run the failing step, and commit the fix in Step 12. Do not change `src/` or `tests/` to make the README true.

**Files:**
- Modify (only if a check fails): `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd/README.md`
- Scratch (not committed): `$SCRATCHPAD/am-readme-check/` where `$SCRATCHPAD` is the session scratchpad directory from your environment block.

**Interfaces:**
- Consumes: Task 1's README text: needle `"document milestone runs"`, `--branch-prefix m3`, default `--repo-dir .` and `--base-branch master`.
- Produces: a list of README fixes (if any) and observed mismatches for Task 4's handoff.

- [ ] **Step 1: Make sure the worktree's venv is current**

Run this without any scratch environment (the scratch `XDG_DATA_HOME` would hide uv's own managed Python):
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && uv sync && test -x .venv/bin/am && echo "am ok"
```
Expected: `am ok`.

- [ ] **Step 2: Write the scratch environment file**

Replace `/path/to/scratchpad` with the literal session scratchpad path from your environment block, then run:
```bash
export SCRATCHPAD=/path/to/scratchpad
mkdir -p "$SCRATCHPAD/am-readme-check"
cat > "$SCRATCHPAD/am-readme-check/env.sh" <<'EOF'
export W=/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd
export SCRATCH="$SCRATCHPAD/am-readme-check"
export XDG_DATA_HOME="$SCRATCH/xdg"
export REPO="$SCRATCH/repo"
export PATH="$SCRATCH/bin:$PATH"
export PY="$W/.venv/bin/python"
am() { "$W/.venv/bin/am" "$@"; }
EOF
echo "SCRATCHPAD=$SCRATCHPAD" 
```
Every later block starts with `export SCRATCHPAD=/path/to/scratchpad; source "$SCRATCHPAD/am-readme-check/env.sh"` (same literal path), because shell state does not survive between calls. `am` runs the worktree's venv entry point directly, never `uv run`, so the scratch `XDG_DATA_HOME` does not reach uv.

- [ ] **Step 3: Build the scratch repo and board**

The board is one milestone with story A (a1 then a2, chained by `brd block`) and story B blocked by A (b1), plus a decoy milestone whose title shares the text `Milestone 3` for the ambiguity check.
```bash
export SCRATCHPAD=/path/to/scratchpad; source "$SCRATCHPAD/am-readme-check/env.sh"
set -euo pipefail
cardid() { "$PY" -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])'; }
git init -b master "$REPO"
git -C "$REPO" config user.email readme-check@example.com
git -C "$REPO" config user.name "readme check"
git -C "$REPO" config commit.gpgsign false
echo base > "$REPO/README.md"
git -C "$REPO" add README.md
git -C "$REPO" commit -m base
cd "$REPO"
brd init --name readme-check
git add -A
git commit -m "brd init"
M=$(brd add --title "Milestone 3: document milestone runs" | cardid)
DECOY=$(brd add --title "Milestone 30: a decoy for the ambiguity check" | cardid)
A=$(brd add --title "Story A: the first level" --parent "$M" | cardid)
B=$(brd add --title "Story B: blocked by story A" --parent "$M" | cardid)
brd block "$B" --by "$A" > /dev/null
A1=$(brd add --title "a1 first subtask of story A" --parent "$A" | cardid)
A2=$(brd add --title "a2 second subtask of story A" --parent "$A" | cardid)
brd block "$A2" --by "$A1" > /dev/null
B1=$(brd add --title "b1 only subtask of story B" --parent "$B" | cardid)
printf 'export M=%s DECOY=%s A=%s B=%s A1=%s A2=%s B1=%s\n' "$M" "$DECOY" "$A" "$B" "$A1" "$A2" "$B1" >> "$SCRATCH/env.sh"
tail -n 1 "$SCRATCH/env.sh"
```
Expected: one `export M=... B1=...` line with seven UUIDs.

- [ ] **Step 4: Run the README's dry-run command as written, and check it wrote nothing**

Write the checker, snapshot, run the command from inside the repo exactly as the README prints it, then compare:
```bash
export SCRATCHPAD=/path/to/scratchpad; source "$SCRATCHPAD/am-readme-check/env.sh"
cat > "$SCRATCH/check_dry.py" <<'EOF'
import json
import os
import sys

env = os.environ
envelope = json.load(open(sys.argv[1]))
assert set(envelope) == {"ok", "data"} and envelope["ok"] is True, envelope
data = envelope["data"]
assert set(data) == {"levels", "already_done"}, data.keys()
assert data["already_done"] == [], data["already_done"]
levels = data["levels"]
assert [level["level"] for level in levels] == [0, 1], levels
(a,) = levels[0]["stories"]
(b,) = levels[1]["stories"]
for story in (a, b):
    assert set(story) == {"story", "title", "root", "subtasks"}, story.keys()
assert (a["story"], b["story"]) == (env["A"], env["B"])
assert a["root"] == "master", a["root"]
a1, a2 = a["subtasks"]
(b1,) = b["subtasks"]
for subtask in (a1, a2, b1):
    assert set(subtask) == {"id", "title", "status", "branch", "base"}, subtask.keys()
    short = subtask["id"].replace("-", "")[:8].lower()
    assert subtask["branch"].startswith("m3/task-") and subtask["branch"].endswith(short), subtask
assert (a1["id"], a2["id"], b1["id"]) == (env["A1"], env["A2"], env["B1"])
assert a1["base"] == "master", a1
assert a2["base"] == a1["branch"], (a2, a1)
assert b["root"] == a2["branch"], (b["root"], a2["branch"])
assert b1["base"] == a2["branch"], b1
print("dry-run shape and bases OK")
EOF
cd "$REPO"
brd tree "$M" > "$SCRATCH/tree-before.json"
git branch --format='%(refname:short)' > "$SCRATCH/branches-before.txt"
git worktree list --porcelain > "$SCRATCH/worktrees-before.txt"
am run --milestone "document milestone runs" --branch-prefix m3 --dry-run --pretty > "$SCRATCH/dry.json"; echo "exit=$?"
"$PY" "$SCRATCH/check_dry.py" "$SCRATCH/dry.json"
brd tree "$M" > "$SCRATCH/tree-after.json"
git branch --format='%(refname:short)' > "$SCRATCH/branches-after.txt"
git worktree list --porcelain > "$SCRATCH/worktrees-after.txt"
diff "$SCRATCH/tree-before.json" "$SCRATCH/tree-after.json" && echo "board unchanged"
diff "$SCRATCH/branches-before.txt" "$SCRATCH/branches-after.txt" && echo "branches unchanged"
diff "$SCRATCH/worktrees-before.txt" "$SCRATCH/worktrees-after.txt" && echo "worktrees unchanged"
test ! -e "$XDG_DATA_HOME/agent-manager/runs" && echo "no run directory"
cat "$SCRATCH/branches-after.txt"
```
Expected: `exit=0`, `dry-run shape and bases OK`, `board unchanged`, `branches unchanged`, `worktrees unchanged`, `no run directory`, and the branch list is just `master`.

- [ ] **Step 5: Check every refusal the README names**

```bash
export SCRATCHPAD=/path/to/scratchpad; source "$SCRATCHPAD/am-readme-check/env.sh"
cd "$REPO"
check() {
  am "$@" > "$SCRATCH/out.txt" 2> "$SCRATCH/err.txt"
  echo "=== exit=$? :: am $*"
  echo "--- stdout:"; cat "$SCRATCH/out.txt"
  echo "--- stderr:"; tail -n 6 "$SCRATCH/err.txt"
}
check run --card "$A1" --milestone "$M" --branch-prefix m3
check run --branch-prefix m3
check run --milestone "   " --branch-prefix m3
check run --card "$A1" --dry-run --branch-prefix m3
check run --milestone "document milestone runs" --dry-run
check run --milestone "Milestone 3" --branch-prefix m3 --dry-run
check run --milestone "no such milestone" --branch-prefix m3 --dry-run
test ! -e "$XDG_DATA_HOME/agent-manager/runs" && echo "still no run directory"
```
Expected, in order:
1. `exit=2`, empty stdout, stderr names `--card` / `--milestone` and says "give --card or --milestone, not both".
2. `exit=2`, empty stdout, stderr says "one of --card or --milestone is required".
3. `exit=2`, empty stdout, stderr says "--milestone needs a card id or a title substring, not a blank string".
4. `exit=2`, empty stdout, stderr says "--dry-run previews a milestone and does not apply to --card".
5. `exit=2`, empty stdout, stderr names the missing `--branch-prefix` option.
6. `exit=3`, stdout is one line `{"error":{"message":"ambiguous milestone \"Milestone 3\" — matches: Milestone 3: document milestone runs, Milestone 30: a decoy for the ambiguity check","type":"MilestoneNotFoundError"},"ok":false}`.
7. `exit=3`, stdout is the envelope with `"type":"MilestoneNotFoundError"` and a message starting `no milestone card matching "no such milestone" — root cards are:`.
8. `still no run directory`.

Then check the other two needle forms the README promises (id and exact title in another case):
```bash
export SCRATCHPAD=/path/to/scratchpad; source "$SCRATCHPAD/am-readme-check/env.sh"
cd "$REPO"
am run --milestone "$M" --branch-prefix m3 --dry-run > "$SCRATCH/by-id.json"; echo "exit=$?"
am run --milestone "MILESTONE 3: DOCUMENT MILESTONE RUNS" --branch-prefix m3 --dry-run > "$SCRATCH/by-title.json"; echo "exit=$?"
"$PY" "$SCRATCH/check_dry.py" "$SCRATCH/by-id.json"
"$PY" "$SCRATCH/check_dry.py" "$SCRATCH/by-title.json"
```
Expected: two `exit=0` and two `dry-run shape and bases OK`.

If stdout for cases 1-5 is JSON, or the exit code is not 2, the README's usage-error paragraph is wrong: rewrite it to what was observed.

- [ ] **Step 6: Put the fake claude first on PATH, and prove it is the one found**

```bash
export SCRATCHPAD=/path/to/scratchpad; source "$SCRATCHPAD/am-readme-check/env.sh"
mkdir -p "$SCRATCH/bin"
{ printf '#!%s\n' "$PY"; cat "$W/tests/e2e/fake_claude.py"; } > "$SCRATCH/bin/claude"
chmod 755 "$SCRATCH/bin/claude"
command -v claude
test "$(command -v claude)" = "$SCRATCH/bin/claude" && echo "fake claude first on PATH"
```
Expected: the path printed is `$SCRATCH/bin/claude` and `fake claude first on PATH`. Do not go on to Step 7 unless both lines appear: otherwise the real `claude` would run and spend money. The fake is used unmodified, exactly as `tests/e2e/conftest.py:158-169` installs it, so it knows only what the brief says.

- [ ] **Step 7: Run the README's main command and make it escalate at a2's review**

The fake fails the review of any branch named in `.git/fake-claude-review-fail` (`tests/e2e/fake_claude.py:202-230`). The README's `--verify "uv run pytest"` has no suite to run in this toy repo, so the one flag substituted is `--verify "git rev-parse --verify HEAD"` (the command `tests/e2e/conftest.py:32` uses); every other flag is as the README prints it.
```bash
export SCRATCHPAD=/path/to/scratchpad; source "$SCRATCHPAD/am-readme-check/env.sh"
cd "$REPO"
test "$(command -v claude)" = "$SCRATCH/bin/claude" || { echo "STOP: fake claude is not first on PATH"; exit 1; }
A1_BRANCH=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["levels"][0]["stories"][0]["subtasks"][0]["branch"])' "$SCRATCH/dry.json")
A2_BRANCH=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["levels"][0]["stories"][0]["subtasks"][1]["branch"])' "$SCRATCH/dry.json")
B1_BRANCH=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["levels"][1]["stories"][0]["subtasks"][0]["branch"])' "$SCRATCH/dry.json")
printf 'export A1_BRANCH=%s A2_BRANCH=%s B1_BRANCH=%s\n' "$A1_BRANCH" "$A2_BRANCH" "$B1_BRANCH" >> "$SCRATCH/env.sh"
MASTER_BEFORE=$(git rev-parse master); echo "export MASTER_BEFORE=$MASTER_BEFORE" >> "$SCRATCH/env.sh"
printf '%s\n' "$A2_BRANCH" > "$REPO/.git/fake-claude-review-fail"
am run --milestone "document milestone runs" \
  --branch-prefix m3 \
  --verify "git rev-parse --verify HEAD" > "$SCRATCH/first.json"; echo "exit=$?"
cat > "$SCRATCH/check_escalated.py" <<'EOF'
import json
import os
import sys

env = os.environ
envelope = json.load(open(sys.argv[1]))
assert envelope["ok"] is True, envelope
data = envelope["data"]
assert set(data) == {"escalated", "run_id", "level", "story", "subtask", "failed_phase", "detail", "warnings"}, data.keys()
assert data["escalated"] is True
assert data["level"] == 0, data
assert data["story"] == env["A"], data
assert data["subtask"] == env["A2"], data
assert data["failed_phase"] == "review", data
assert isinstance(data["detail"], str) and data["detail"], data
assert isinstance(data["warnings"], list), data
print("escalation payload OK", data["run_id"])
EOF
"$PY" "$SCRATCH/check_escalated.py" "$SCRATCH/first.json"
RUN1=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["run_id"])' "$SCRATCH/first.json")
echo "export RUN1=$RUN1" >> "$SCRATCH/env.sh"
git branch --format='%(refname:short)' | grep -Fx "$B1_BRANCH" && echo "UNEXPECTED: b1 branch exists" || echo "b1 never started"
test "$(git rev-parse master)" = "$MASTER_BEFORE" && echo "master did not move"
brd show "$A1" | "$PY" -c 'import json,sys; print("a1", json.load(sys.stdin)["data"]["status"])'
```
Expected: `exit=1`, `escalation payload OK <run id>`, `b1 never started`, `master did not move`, `a1 done`.

- [ ] **Step 8: Preview again, check the done subtask still anchors, check status, and check `am resume` does not continue the milestone**

```bash
export SCRATCHPAD=/path/to/scratchpad; source "$SCRATCHPAD/am-readme-check/env.sh"
cd "$REPO"
am run --milestone "document milestone runs" --branch-prefix m3 --dry-run --pretty > "$SCRATCH/dry2.json"; echo "exit=$?"
"$PY" - "$SCRATCH/dry2.json" <<'EOF'
import json
import os
import sys

env = os.environ
data = json.load(open(sys.argv[1]))["data"]
assert [(e["kind"], e["id"], e["story"]) for e in data["already_done"]] == [("subtask", env["A1"], env["A"])], data["already_done"]
(a,) = data["levels"][0]["stories"]
(a2,) = a["subtasks"]
assert a2["id"] == env["A2"] and a2["base"] == env["A1_BRANCH"], a2
(b,) = data["levels"][1]["stories"]
assert b["root"] == env["A2_BRANCH"], b
print("done subtask still anchors the next one")
EOF
am status "$RUN1" > "$SCRATCH/status1.json"; echo "status exit=$?"
am resume "$RUN1" --verify "git rev-parse --verify HEAD" > "$SCRATCH/resume1.json"; echo "resume exit=$?"
cat "$SCRATCH/resume1.json"
```
Expected: `exit=0`, `done subtask still anchors the next one`, `status exit=0`, `resume exit=3` with an envelope whose `type` is `NotResumableError` and whose message says the run has no subtask recorded `started`.

- [ ] **Step 9: Remove the marker and relaunch the same command (clean run)**

```bash
export SCRATCHPAD=/path/to/scratchpad; source "$SCRATCHPAD/am-readme-check/env.sh"
cd "$REPO"
test "$(command -v claude)" = "$SCRATCH/bin/claude" || { echo "STOP: fake claude is not first on PATH"; exit 1; }
rm "$REPO/.git/fake-claude-review-fail"
am run --milestone "document milestone runs" \
  --branch-prefix m3 \
  --verify "git rev-parse --verify HEAD" > "$SCRATCH/second.json"; echo "exit=$?"
"$PY" - "$SCRATCH/second.json" <<'EOF'
import json
import os
import sys

env = os.environ
envelope = json.load(open(sys.argv[1]))
assert envelope["ok"] is True, envelope
data = envelope["data"]
assert set(data) == {"done", "run_id", "levels", "completed", "tips", "warnings"}, data.keys()
assert data["done"] is True
assert data["run_id"] != env["RUN1"], data["run_id"]
assert data["levels"] == [{"level": 0, "stories": [env["A"]]}, {"level": 1, "stories": [env["B"]]}], data["levels"]
assert data["completed"] == [env["A2"], env["B1"]], data["completed"]
assert data["tips"] == [{"story": env["A"], "tip": env["A2_BRANCH"]}, {"story": env["B"], "tip": env["B1_BRANCH"]}], data["tips"]
assert isinstance(data["warnings"], list)
print("clean-run payload OK")
EOF
for card in "$A1" "$A2" "$B1" "$A" "$B" "$M"; do
  brd show "$card" | "$PY" -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(d["status"], d["title"])'
done
git merge-base --is-ancestor "$A1_BRANCH" "$A2_BRANCH" && echo "a1 under a2"
git merge-base --is-ancestor "$A2_BRANCH" "$B1_BRANCH" && echo "a2 under b1"
test "$(git rev-parse master)" = "$MASTER_BEFORE" && echo "master did not move"
test -z "$(git remote)" && echo "no remote: nothing could be pushed"
git branch --format='%(refname:short)'
```
Expected: `exit=0`, `clean-run payload OK`, six lines each starting `done`, `a1 under a2`, `a2 under b1`, `master did not move`, `no remote: nothing could be pushed`, and the branch list is `master` plus the three `m3/task-...` branches.

- [ ] **Step 10: Relaunch a finished milestone**

```bash
export SCRATCHPAD=/path/to/scratchpad; source "$SCRATCHPAD/am-readme-check/env.sh"
cd "$REPO"
test "$(command -v claude)" = "$SCRATCH/bin/claude" || { echo "STOP: fake claude is not first on PATH"; exit 1; }
am run --milestone "document milestone runs" \
  --branch-prefix m3 \
  --verify "git rev-parse --verify HEAD" > "$SCRATCH/third.json"; echo "exit=$?"
"$PY" -c 'import json,sys; d=json.load(open(sys.argv[1]))["data"]; assert d["done"] is True and d["completed"] == [] and d["levels"] == [], d; print("finished milestone drives nothing")' "$SCRATCH/third.json"
```
Expected: `exit=0` and `finished milestone drives nothing`. (`levels` is empty because `dag.compute_levels` drops stories with nothing left; if it is not empty, drop that part of the assertion, keep the rest, and note it for Task 4.)

- [ ] **Step 11: Cross-check against the default-suite test that asserts the same behavior**

Run:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && uv run pytest tests/e2e/test_milestone_run.py -v
```
Expected: 3 passed. This is the production-path proof of the clean-run, escalation and relaunch paragraphs (`tests/e2e/test_milestone_run.py:54-199`). The coder-reports-`blocked` sentence is taken from addendum acceptance 4; confirm it is covered by grepping:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && grep -rn 'implement_blocked_gate' tests/workflow/test_builtin_task.py && uv run pytest tests/workflow/test_builtin_task.py -k implement_blocked -v
```
Expected: `tests/workflow/test_builtin_task.py:592` `test_implement_blocked_gate_reads_a_real_dumped_blocked_result_and_blocks` (and the gate rows near lines 481-528 asserting `verdict["blocked"] == "implement"`) are listed and pass. That gate sits on the `implement` phase (`test_builtin_task.py:226`), before `review`, which is what the README sentence claims. If they are missing or fail, remove that sentence from the README and note it for Task 4.

- [ ] **Step 12: Commit any README fixes, then clean up the scratch dir**

If Steps 4-11 needed README edits:
```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && git add README.md && git commit -m "docs(readme): match the milestone section to what the commands do" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
If no edit was needed, there is nothing to commit. Then:
```bash
export SCRATCHPAD=/path/to/scratchpad; rm -rf "$SCRATCHPAD/am-readme-check"
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && git status --porcelain
```
Expected: empty `git status --porcelain`.

---

### Task 3: Add the Status note to section 10 of the main spec

**Files:**
- Modify: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd/docs/superpowers/specs/2026-09-23-agent-manager-design.md:408-414` (append after the `--dry-run` paragraph, before `## 11. Concurrency`)

**Interfaces:**
- Consumes: the `@app.command(...)` registrations in `src/agent_manager/cli.py` (lines 951 `run`, 1078 `status`, 1113 `runs`, 1171 `logs`, 1321 `resume`).
- Produces: nothing later tasks use.

- [ ] **Step 1: Show the note is missing (RED)**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && sed -n '390,414p' docs/superpowers/specs/2026-09-23-agent-manager-design.md | grep -n 'Status' ; echo "grep exit=$?"
```
Expected: no match and `grep exit=1`.

- [ ] **Step 2: Confirm the list of commands from the code**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && grep -n '@app.command' src/agent_manager/cli.py
```
Expected: exactly `run`, `status`, `runs`, `logs`, `resume`. If the list differs, change the "Exists" sentence in Step 3 to match it.

- [ ] **Step 3: Insert the note**

Use Edit on `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. old_string (exact, lines 411-414):

```text
blocker's tip. A blocked story rooted at the milestone base means a missing
`blockedBy` edge.

## 11. Concurrency
```

new_string:

```markdown
blocker's tip. A blocked story rooted at the milestone base means a missing
`blockedBy` edge.

**Status (milestone 3).** The synopsis above is the target surface, not what
exists today. Exists: `run` (with `--card`, or `--milestone` with `--dry-run`,
plus `--repo-dir`, `--base-branch`, `--branch-prefix`, `--verify` and
`--allow-no-verification`), `status`, `runs`, `logs`, and `resume` for a
single-card run. Deferred: `watch`, `retry`, `cancel`, `--workflow`,
`--harness`, `--max-concurrent` (runs are sequential, one story at a time), and
a milestone-aware `resume`. See section 4 of the orchestration addendum,
`2026-09-24-orchestration-design.md`.

## 11. Concurrency
```

- [ ] **Step 4: Show the note is there and nothing else changed (GREEN)**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && grep -n -e '^\*\*Status (milestone 3)' -e '^## 10\.' -e '^## 11\.' docs/superpowers/specs/2026-09-23-agent-manager-design.md && git diff --numstat docs/superpowers/specs/2026-09-23-agent-manager-design.md
```
Expected: `## 10.` line, then the `**Status (milestone 3).**` line, then `## 11.`; numstat shows `9	0` (nine lines added: blank line plus eight note lines, zero removed).

- [ ] **Step 5: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && git add docs/superpowers/specs/2026-09-23-agent-manager-design.md && git commit -m "docs(spec): note which CLI commands exist and which are deferred" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Full verification and handoff

**Files:**
- None modified.

**Interfaces:**
- Consumes: the commits from Tasks 1-3 and the notes collected in Task 2.
- Produces: the handoff note for the card.

- [ ] **Step 1: Confirm only docs changed on the branch**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && git diff --name-only m3/task-add-the-opt-in-real-f9e7bf4c..HEAD
```
Expected: `README.md`, `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, and this card's own spec/plan files under `docs/superpowers/` if they were committed on this branch. Nothing under `src/` or `tests/`, and not `docs/superpowers/specs/2026-09-24-orchestration-design.md`.

- [ ] **Step 2: Run the full suite**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-document-milestone-runs-7b6ad9bd && uv run pytest
```
Expected: all pass, the same count as before this card (no code or tests changed).

- [ ] **Step 3: Write the handoff note**

Report these in the card's handoff (not as a file, and not fixed here), plus anything Task 2 found:
- The spec's Verification bullet says each refusal gives "an error envelope". In the code, `--card` with `--milestone`, neither flag, a blank `--milestone`, and `--dry-run` with `--card` are `typer.BadParameter` usage errors (stderr, no JSON, exit 2, `cli.py:920-948`); only an ambiguous or unknown milestone gives the envelope (exit 3). The README follows the code.
- The existing README line "Every command prints one line of JSON ... `{"ok": false, "error": {...}}` on a refusal" is not true for Typer usage errors. It was left as it is, because the spec says to leave existing content alone; the new section says it plainly for the milestone refusals.
- Main spec section 10's synopsis shows `--base-branch main`; the code default is `master` (`cli.py:972-974`). The synopsis was left as it is, per the spec.
- Addendum section 4 says "`--verify` is required here"; in the code `--verify` is optional at parse time and the verification gate refuses a run with neither `--verify` nor `--allow-no-verification` (`steps/reducers.py:30-40`). The README describes the gate.
- Any README edit made in Task 2, and any assertion in Task 2 that had to be relaxed (for example Step 10's `levels == []`).
