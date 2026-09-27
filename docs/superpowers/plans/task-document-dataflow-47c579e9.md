<!-- task-pipeline: validated -->
# Document dataflow scheduling, merged bases and milestone resume (card 47c579e9)

Subtask of story 03ac7831 "Documentation", milestone c2a981a3 "Milestone 7: the supervisor tree". Narrows plan Task 5.1 in `docs/superpowers/plans/2026-09-25-supervisor-tree.md`. Source of truth: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` (T1-T10, sections 1-10). Documentation only.

## Scope

Files changed, and nothing else:

- `README.md`, the milestone-run sections listed below.
- `docs/superpowers/specs/2026-09-23-agent-manager-design.md`: one added line pointing at the supervisor-tree addendum (`2026-09-25-supervisor-tree-design.md`).

Document behavior as built. The source is `src/agent_manager/orchestrate.py` (`supervise`, `lane`, `base_only_lane`, `build_merged_base`, `blocker_tips`, `run_milestone`, `bases_payload`/`with_bases`), `src/agent_manager/bases.py`, `src/agent_manager/runtime/stop.py` and `cli.resume_run`/`cli.dry_run_payload`, all in this worktree. Where the code and the addendum disagree, the code wins, and the disagreement is noted in the commit body.

Out of scope: any code or test change. That includes the stale `cli.dry_run_payload` docstring (cli.py:884-886 still says the real run refuses a multi-blocker story), because this card is docs only. Also out of scope, per the milestone: verification discovery, live pause/cancel/watch/retry, more than one `am` process per repo, a grafo `max_workers` option, and leave-me-alone's multi-blocker support.

## Required README changes (observable result)

1. **Dataflow scheduling, not level barriers.** Every sentence that says levels are barriers, or that a level runs "after" another, changes. A story starts as soon as all of its own blockers have succeeded. Levels (waves) remain only as a preview and report grouping (`data.levels`). The affected text:
   - "What a clean run leaves behind": "Levels run one after another…" (README.md:110).
   - Integrate: "After the last level" (README.md:134). The new wording is "after every story".
   - "Parallel runs": `--max-concurrent` bounds stories running at once across the milestone, not "of one level" (README.md:174). Also the "Levels are barriers" bullet (README.md:181-182), and "a story of the same level… no later level starts" (README.md:192). The stop semantics must match the code: which stories stay `pending` once the stop is set.
   - README.md:201: this paragraph says `am resume` is not the way to continue a milestone. Rewrite it to offer both relaunch and `am resume <run-id>`.
   - The escalation report intro (README.md:219-220): the other lanes park, and no new story starts. `level` in the payload is the story's wave.
2. **Dry run (README.md:73-106).** Document the `merged_from` key: it appears on a story row only when its root is a merged base, lists the blockers in `blocked_by` order, and is absent otherwise. Add a `base` bullet saying a story with two or more blockers roots on its merged base branch. Remove "or a story blocked by two or more stories in the milestone" and "A stack roots on one branch only". Only a cycle is still refused with exit 3.
3. **New "Multiple blockers" section.** Place it after "Parallel runs". It covers:
   - The branch name `<prefix>/base-<short id of story>`.
   - How the base is built: the blockers' tips are merged in census order, then verified once.
   - A clean merge is silent.
   - A conflict goes to the Integrate resolver. In `am status`, that shows as a synthetic story titled "Merged bases" with subtask `base-<story id>`.
   - A `MergeInProgressError` escalates, and the human finishes the merge by hand and then resumes.
   - A story with one blocker keeps the fast path: no base branch and no extra verify.
   - The base branch never moves, and nothing is pushed.
   - A failed base in the escalation report: `failed_phase: "base"`, `subtask: null`.
   - The run's `data.bases` list, `[{"story","branch","blockers"}]`, which is present only when non-empty.
4. **"Relaunching resumes" (README.md:253-269).** `am resume <run-id>` on a `milestone` run continues the milestone under the same run id. It re-derives the plan from the board, reuses the recorded `branch_prefix`, `base_branch` and `max_concurrent_stories`, and reports `resumed: true`. Refusals, each with exit 3 and the `{"ok": false, "error": {...}}` envelope:
   - the run is `done`;
   - any open subtask or base-resolver checkpoint has a stale digest. This refuses the whole resume, and nothing is written.

   Resume of a `task` run is unchanged. The existing bullets become explicitly about `task` runs, including the one that refuses "more than one in-flight subtask". Delete README.md:269 ("`am resume` is not milestone-aware…").
5. **"Not there yet".** Delete the bullet "`am resume` is not milestone-aware." (README.md:273). Add the supervisor-tree addendum's deferred section to the "See section …" pointer list if that addendum has one. Do not invent one.

The docs must not contradict the invariants:

- only `orchestrate.py` imports grafo, and every Node has `timeout=None`;
- only the subtask is a pygents Agent;
- the base branch never moves, and nothing is pushed;
- one `am` process per repo.

Keep the house style: JSON envelope names, and exact key names in backticks.

## Error paths

None at runtime, since no code changes. The authoring error paths are these:

- README text that states behavior the code does not have. Guard against it by checking every claim against the modules named above and against the tests in `tests/test_bases.py`, `tests/test_orchestrate.py`, `tests/e2e/test_parallel_milestone.py` and `tests/e2e/test_milestone_resume.py`.
- Leftover stale text. Before committing, grep README.md for `barrier`, `not milestone-aware`, `two or more stories` and `level N+1`, and each must return nothing.

## Tests

No new tests. The testing section of the design spec (section 14: pure functions, steps, adapters, engine, and one opt-in end to end per feature) has no tier for prose. No test in the repo asserts README content: `tests/steps/test_docs_commit.py` covers the docs-commit step, not this README.

- Verification: `uv run pytest` passes in full, including `tests/e2e`. This proves that no code was touched by accident.
- Commit message: `docs: dataflow scheduling, merged bases and milestone resume`.

Note: the exploration findings passed to this stage were cut off at 8000 characters, partway through the test-placement rule. The testing tiers above were re-read directly from the design spec (lines 501-516), so the tier guidance here does not depend on the missing text.

---

# Document Dataflow Scheduling, Merged Bases and Milestone Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `README.md` in line with the milestone-7 code as built (dataflow scheduling, merged bases, milestone-wide `am resume`) and add one pointer line to the design spec, with no code or test change.

**Architecture:** Documentation only. Every README sentence below was checked against the code in this worktree: `src/agent_manager/orchestrate.py`, `src/agent_manager/bases.py`, `src/agent_manager/dag.py` (`story_root`, `RootPlan`, `base_branch_name`), `src/agent_manager/runtime/stop.py` and `src/agent_manager/cli.py` (`resume_run`, `resume`, `dry_run_payload`, `continuable_checkpoint`). Each task replaces exact old README text with exact new text, bracketed by a grep that fails before (the stale text is there) and passes after (it is gone, the new text is there). That grep is the prose analogue of RED/GREEN. The full `uv run pytest` at the end proves no code was touched.

**Tech Stack:** Markdown, `grep`, `uv run pytest`.

**Spec:** `docs/superpowers/specs/task-document-dataflow-47c579e9-design.md` (prepended above, verbatim).

## Global Constraints

- Files changed: `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (one added line). Nothing else. No file under `src/` or `tests/` changes.
- The `cli.dry_run_payload` docstring (cli.py:884-886) stays stale. Do not touch it.
- After the last task, `grep -nE 'barrier|not milestone-aware|two or more stories|level N\+1' README.md` returns nothing. New text therefore never uses the words "barrier" or the phrase "two or more stories"; it says "two or more blockers".
- The docs never contradict: only `orchestrate.py` imports grafo and every Node has `timeout=None`; only the subtask is a pygents Agent; the milestone's base branch never moves and nothing is pushed; one `am` process per repository.
- Exact key names in backticks; the envelope is `{"ok": true, "data": ...}` / `{"ok": false, "error": {...}}`.
- Commit message: `docs: dataflow scheduling, merged bases and milestone resume`. One commit, at the end (Task 5), because the spec fixes a single message.
- Verification: `uv run pytest`, full suite, green.

### Where the code disagrees with the spec (code wins; goes in the commit body)

- **Merge order of a merged base.** The spec (and addendum §4/§5) says the blockers' tips are merged "in census order". The code merges them in the order the story's `blocked_by` lists them, in-milestone blockers only, de-duplicated: `dag.story_root` builds `RootPlan.blockers` from `dict.fromkeys(story.blocked_by)`, `orchestrate.blocker_tips` returns tips in `root_plan.blockers` order, and `bases.build` cuts from `tips[0]` and merges `tips[1:]` in that order. `bases_payload` says `blockers` is "the order the tips were merged in". The README documents `blocked_by` order.
- **Who stays `pending` after a stop.** Addendum §9 says "a waiting story ends `pending`". In the code (`lane`), a story whose blockers all finished clean is started by grafo even after the stop fires; when it gets its slot it sees the stop and ends `stopped` (recorded `stopped`, listed under `stopped` with `before_phase: null`). Only a story whose blocker escalated or stopped never starts and stays `pending`. The README documents the code.
- **`--verify` on resume.** README.md:25 says `--verify` and `--allow-no-verification` "have no effect" on `am resume`. For a milestone run that is now false: `cli.resume` passes them to `run_milestone`, which uses them for every subtask with no checkpoint, every merged base and Integrate (see the `--verify` help text at cli.py:1474-1481). The spec does not list README.md:25, but its first error path ("README text that states behavior the code does not have") covers it, so Task 4 fixes it.

## Review Focus

These are the reader situations the spec implies but does not spell out, most likely to bite first. There is no test tier for prose (design spec section 14), so each is pinned by a grep step in the task that owns the text.

- A user resumes a milestone run without repeating `--verify`: the README must say the suite is not recorded and must be passed again, or fresh subtasks, merged bases and Integrate hit the verification gate. Pinned in Task 4 Step 4 (grep for `Pass your \`--verify\` commands again`).
- A user hits a stale-digest refusal on `am resume` and needs a way forward: the README must say that relaunching with `am run --milestone` is lenient and starts such a card afresh (`cli.continuable_checkpoint`). Pinned in Task 4 Step 4.
- A user reads an escalation where `subtask` is `null` and `level` is `null`: the README must explain a failed base (`failed_phase: "base"`) and a base-only story (no subtasks of its own, `level: null`). Pinned in Task 3 Step 4.
- A user edits a merged base's conflict by hand and relaunches without committing: the README must say an unfinished merge makes the base fail untouched (`MergeInProgressError`) and to commit first. Pinned in Task 3 Step 4.
- A user expects the merged base to follow census order: the README must state `blocked_by` order and that `data.bases[].blockers` / `merged_from` show it. Pinned in Task 2 Step 4 and Task 3 Step 4.

---

### Task 1: Dataflow scheduling replaces level barriers

**Files:**
- Modify: `README.md:36-39` (Milestone runs intro), `README.md:48-51` (`--max-concurrent`), `README.md:90` (dry-run Integrate sentence), `README.md:110-111` (clean run), `README.md:121-122` (`levels` bullet), `README.md:134` (Integrate intro), `README.md:174-201` (Parallel runs), `README.md:219-223` (escalation intro)
- Test: none (no prose tier in design spec section 14); the grep in Steps 1 and 4 is the check.

**Interfaces:**
- Consumes: nothing.
- Produces: README anchors `#parallel-runs` (unchanged) and links to `#multiple-blockers` (the section Task 3 creates) and `#relaunching-resumes` (unchanged heading).

- [ ] **Step 1: Confirm the stale scheduling text is there (RED)**

Run from `/home/paulomtts/Code/agent-manager/.claude/worktrees/m7/task-document-dataflow-47c579e9`:

```bash
grep -nE 'barrier|level N\+1|no later level|after the last level|After the last level|of a level.s stories|of one level|Levels run one after another|Stories in the same dependency|every level finished|not the way to continue' README.md
```

Expected: matches on lines 36, 48, 90, 110, 134, 174, 181, 192, 201 and 220. (The pattern says `Levels run one after another`, not bare `one after another`, because line 169's "tips are merged one after another" is about Integrate and stays.) If none match, stop: the branch is not the one this plan was written against.

- [ ] **Step 2: Rewrite the Milestone runs intro, `--max-concurrent`, dry-run Integrate sentence, clean-run and Integrate wording**

Edit `README.md`, replacing each old block with the new one exactly.

Old (lines 36-39):

```
Drive every remaining subtask of one milestone. Stories in the same dependency
level run side by side, up to `--max-concurrent` of them at once. Inside a
story, subtasks always run in order, each on its own local branch stacked on
the branch before it:
```

New:

```
Drive every remaining subtask of one milestone. A story starts as soon as every story it is blocked by has finished clean, and up to `--max-concurrent` stories run at once. Inside a story, subtasks always run in order, each on its own local branch stacked on the branch before it:
```

Old (lines 48-51):

```
`--max-concurrent N` is how many of a level's stories run at once. It defaults
to 4. `--max-concurrent 1` runs a level's stories one at a time, as the runner
did before parallel runs. See [Parallel runs](#parallel-runs) for what runs
together, how a run stops, and the limits.
```

New:

```
`--max-concurrent N` is how many stories run at once, across the whole milestone. It defaults to 4, and `--max-concurrent 1` runs one story at a time. See [Parallel runs](#parallel-runs) for what runs together, how a run stops, and the limits, and [Multiple blockers](#multiple-blockers) for a story with two or more blockers.
```

Old (in line 90):

```
`data.integrate` is the plan for [Integrate](#integrate), the step that runs after the last level:
```

New:

```
`data.integrate` is the plan for [Integrate](#integrate), the step that runs after every story has finished:
```

Old (lines 110-111):

```
Levels run one after another, a level's stories run on up to `--max-concurrent`
lanes, and each story's subtasks run in order. Before the first subtask the run
```

New:

```
Each story starts as soon as its blockers have finished clean, up to `--max-concurrent` stories run at once, and each story's subtasks run in order. Before the first subtask the run
```

Old (lines 121-122):

```
- `levels`: the stories this run had work for, as `{"level", "stories"}` with
  story ids.
```

New:

```
- `levels`: the stories this run had work for, as `{"level", "stories"}` with
  story ids, grouped into waves the way `--dry-run` groups them. The grouping is for reading the report only: no story waited for the rest of its wave.
```

Old (start of line 134):

```
After the last level, the run merges every story's tip into one local branch, `<prefix>-integrate`, and checks the result once. This step is Integrate. It runs only when every level finished clean: an escalation or a stop ends the run before it.
```

New:

```
After every story has finished, the run merges every story's tip into one local branch, `<prefix>-integrate`, and checks the result once. This step is Integrate. It runs only when every story finished clean: an escalation or a stop ends the run before it.
```

(The rest of line 134, from "It also runs when there was nothing left to drive", stays as it is.)

- [ ] **Step 3: Rewrite "Parallel runs" and the escalation intro**

Old (lines 174-201, from "`--max-concurrent N` bounds" through the paragraph ending "a resume never starts a later subtask, story, level or Integrate."):

```
`--max-concurrent N` bounds how many stories of one level run at once. It
defaults to 4, and `--max-concurrent 1` runs them in sequence, exactly as the
sequential runner did. The run records the value in its config as
`max_concurrent_stories`.

What runs together:

- Only stories in the same dependency level. Levels are barriers: level N+1
  starts only after every story of level N has finished.
- Never two subtasks of one story. A story's subtasks run in order, each
  stacked on the branch before it.

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
- `stopped` is a clean park between two phases. Nothing failed, and the work
  done so far is kept.

To continue, fix the escalation and relaunch the same `am run --milestone` command (see [Relaunching resumes](#relaunching-resumes)). The stopped subtask picks up where it parked, and every card already `done` on the board is skipped. `am resume <run-id>` is not the way to continue a milestone. It drives exactly one subtask and nothing after it: a run with more than one subtask recorded `started` or `stopped` is refused with `{"ok": false, "error": {...}}` and exit code 3, and a resume never starts a later subtask, story, level or Integrate.
```

New:

```
`--max-concurrent N` bounds how many stories run at once, across the whole milestone. It defaults to 4, and `--max-concurrent 1` runs one story at a time. The run records the value in its config as `max_concurrent_stories`.

What runs together:

- A story starts as soon as every story it is blocked by inside the milestone has finished clean. It does not wait for the rest of its level: with A blocking C and an unrelated B still running, C starts the moment A is done. Levels, or waves, remain only as a way to read the plan, in `--dry-run` and in the report's `levels`.
- A story that has started takes one of the `--max-concurrent` slots, and waits for one if all are taken. A story still waiting for its blockers holds no slot.
- Never two subtasks of one story. A story's subtasks run in order, each stacked on the branch before it.
- A story with two or more blockers first builds a merged base from their tips, after they have all finished clean. See [Multiple blockers](#multiple-blockers).

How a run stops. The first escalation in any lane, an exception raised inside a lane, or a merged base that fails, sets the run's stop. Every other lane checks the stop before each subtask and before it starts its next phase. A phase already running is never interrupted, so a stop waits for the running phase to finish: a lane in the middle of a long `implement` finishes it and then parks. The subtask that lane was on is recorded `stopped`, and so is its story. A lane that is between two subtasks, or that only gets its slot after the stop, ends `stopped` too, without driving anything more. A story whose blocker escalated or stopped never starts, and stays `pending`. Integrate does not run.

`stopped` is not `escalated`:

- `escalated` is a failure. A gate gave up (for example `review`), or the lane
  raised an exception. Something needs fixing before you go on.
- `stopped` is a clean park between two phases. Nothing failed, and the work
  done so far is kept.

To continue, fix the escalation, then either relaunch the same `am run --milestone` command, which starts a new run, or run `am resume <run-id>`, which continues this run under the same run id (see [Relaunching resumes](#relaunching-resumes)). Either way the stopped subtask picks up where it parked, and every card already `done` on the board is skipped.
```

Old (lines 219-223):

```
The first escalation stops the run: the other lanes of its level park at their
next phase boundary, and no later level starts (see
[Parallel runs](#parallel-runs)). The run exits 1, and `data` holds `escalated`
(`true`), `run_id`, `level`, `story`, `subtask`, `failed_phase`, `detail` and
`warnings`. These top-level fields describe the first escalation.
```

New:

```
The first escalation stops the run: the other lanes park at their next phase boundary, no new story starts, and Integrate does not run (see [Parallel runs](#parallel-runs)). The run exits 1, and `data` holds `escalated` (`true`), `run_id`, `level`, `story`, `subtask`, `failed_phase`, `detail` and `warnings`. These top-level fields describe the first escalation. `level` is the story's wave, the level `--dry-run` lists it under; nothing waited on it.
```

- [ ] **Step 4: Confirm the stale scheduling text is gone (GREEN)**

```bash
grep -nE 'barrier|level N\+1|no later level|after the last level|After the last level|of a level.s stories|of one level|Levels run one after another|Stories in the same dependency|every level finished|not the way to continue' README.md
grep -n 'A story starts as soon as every story it is blocked by' README.md
grep -n "the level \`--dry-run\` lists it under" README.md
```

Expected: the first command prints nothing; the second prints two lines (Milestone runs intro and Parallel runs); the third prints one line.

---

### Task 2: Dry run documents `merged_from` and stops refusing two or more blockers

**Files:**
- Modify: `README.md:81-84` (`data.levels` and story row shape), `README.md:92-106` (the `base` column bullets)
- Test: none (no prose tier); grep in Steps 1 and 4.

**Interfaces:**
- Consumes: the `#multiple-blockers` anchor (created in Task 3; the link is valid once Task 3 lands, and nothing checks links before then).
- Produces: nothing later tasks rely on.

- [ ] **Step 1: Confirm the refusal text is there (RED)**

```bash
grep -nE 'two or more stories|A stack|roots on one branch only|"root", "subtasks"\}' README.md
```

Expected: matches on line 83 (`{"story", "title", "root", "subtasks"}`), line 104 (`two or more stories`) and line 106 (`roots on one branch only`).

- [ ] **Step 2: Rewrite the `data.levels` and story-row sentences**

Old (lines 81-84, from "`data.levels` is a list"):

```
`data.levels` is a list of
`{"level", "concurrent", "stories"}`, where `concurrent` is how many of that
level's stories would run at once. Each story is `{"story", "title", "root", "subtasks"}`,
and each subtask is `{"id", "title", "status", "branch", "base"}`. Only the
```

New:

```
`data.levels` groups the stories still to run into waves, as a list of `{"level", "concurrent", "stories"}`. It is a preview only: the real run starts each story as soon as its own blockers have finished, not when a whole level has. `concurrent` is how many of that level's stories could run at once, `min(stories in the level, max_concurrent)`. Each story is `{"story", "title", "root", "subtasks"}`, plus `merged_from` when its root is a merged base (see below), and each subtask is `{"id", "title", "status", "branch", "base"}`. Only the
```

(Line 81 opens with "`data.max_concurrent` echoes it (4 when not given)." and that sentence stays; the old block starts right after it on the same line, so match on the exact text above.)

- [ ] **Step 3: Rewrite the `base` column bullets**

Old (lines 94-106):

```
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
```

New:

```
- A story with no blocker inside the milestone has `root` equal to
  `--base-branch`, and its first subtask builds on it.
- A story with exactly one blocker in the milestone roots on that story's tip,
  the branch of its last subtask, even when that story is already done.
- A story with two or more blockers in the milestone roots on its own merged base, `<prefix>/base-<short id of the story>`, and its first subtask builds on it. Its row gains `merged_from`, the ids of those blockers in the order its `blocked_by` lists them, which is the order their tips are merged. Every other row has no `merged_from` key. See [Multiple blockers](#multiple-blockers).
- Every later subtask builds on the previous subtask's branch in the story's
  full order. A done subtask is not listed, but its branch still anchors the
  next one.
- A story you expected to wait on another, whose `root` is the base branch, is
  missing a `blocked_by` edge on the board. Add it with
  `brd block <story> --by <blocker>` and preview again.
- A cycle between stories is refused with exit code 3 before anything is written. A story with two or more blockers is not refused.
```

- [ ] **Step 4: Confirm (GREEN)**

```bash
grep -nE 'two or more stories|roots on one branch only' README.md
grep -n 'Its row gains `merged_from`' README.md
grep -n 'A cycle between stories is refused with exit code 3' README.md
grep -n 'in the order its `blocked_by` lists them' README.md
```

Expected: the first prints nothing; each of the other three prints exactly one line.

---

### Task 3: New "Multiple blockers" section, `data.bases`, and the failed-base report

**Files:**
- Modify: `README.md` — insert a new `#### Multiple blockers` section between the end of "Parallel runs" (the "Limits, stated plainly" list ending "nothing rate-limits them.") and `#### What an escalation report contains`; add a `bases` bullet to "What a clean run leaves behind" (after the `integrated` bullet, line 128); add a failed-base paragraph and a `bases` bullet to "What an escalation report contains" (after line 229 and in the "Two more keys" list at lines 233-240).
- Test: none (no prose tier); grep in Steps 1 and 5.

**Interfaces:**
- Consumes: nothing.
- Produces: the heading `#### Multiple blockers`, whose GitHub anchor is `#multiple-blockers` (linked from Tasks 1 and 2).

- [ ] **Step 1: Confirm the section does not exist yet (RED)**

```bash
grep -nE '^#### Multiple blockers|`bases`|failed_phase` `"base"`|Merged bases' README.md
```

Expected: no output.

- [ ] **Step 2: Insert the "Multiple blockers" section**

Old (end of "Parallel runs" and the next heading):

```
- **Machine load.** N lanes means up to N `claude -p` processes at once, and
  nothing rate-limits them.

#### What an escalation report contains
```

New:

```
- **Machine load.** N lanes means up to N `claude -p` processes at once, and
  nothing rate-limits them.

#### Multiple blockers

A story with two or more blockers in the milestone does not root on any one of their tips. It roots on its own merged base, a local branch named `<prefix>/base-<short id of the story>` (with `--branch-prefix m3` and story `4f1c2a9e-…`, `m3/base-4f1c2a9e`), in the worktree `<repo>/.claude/worktrees/<prefix>/base-<short id of the story>`. `--dry-run` shows that branch as the story's `root` and lists the blockers under `merged_from`.

The story's lane builds the base after every blocker has finished clean and the lane has taken its slot, before its first subtask:

1. The branch is cut from the first blocker's tip. Blockers are taken in the order the story's `blocked_by` lists them. Only blockers inside the milestone count, and a blocker listed twice counts once.
2. Every other blocker's tip is merged in, one at a time, in that order. A tip that merges cleanly is merged with no agent and nothing reported. A tip the base already contains is skipped, so a relaunch or a resume never merges anything twice.
3. Your `--verify` commands run once on the base. With `--allow-no-verification` and no `--verify`, this check is skipped. A failed check fails the base.

A tip that conflicts is left mid-merge, and the same resolver [Integrate](#integrate) uses (role `resolver`, the builtin `integrate` workflow: `resolve`, then `verify`) is dispatched into the base's worktree with the tip and the conflicting files. In `am status <run_id>` it shows up under a story titled `Merged bases` (story id `bases`), with one subtask `base-<story id>` per story whose base needed a resolver. A resolver that does not finish fails the base, and the merge is left in progress in the worktree.

If the base's worktree already holds an unfinished merge from an earlier run (`MergeInProgressError`), the base fails and nothing in the worktree is touched. Finish the merge there by hand, `git add` the files and `git commit`, then run `am resume <run-id>` or relaunch.

A failed base escalates its story before any of the story's subtasks run, and the run stops as for any escalation (see [What an escalation report contains](#what-an-escalation-report-contains)). A resolver parked by the stop is not a failure: the story is recorded `stopped`, and `am resume` continues the resolver from its checkpoint.

A story with one blocker keeps the fast path: it roots directly on that blocker's tip, with no base branch and no extra verify. A story with two or more blockers and no subtasks of its own still builds its base, because a story it blocks roots there.

Every merged base a lane built in this run, including one an earlier run had already built, is listed in `data.bases` as `{"story", "branch", "blockers"}`, in wave order, where `blockers` is the order the tips were merged. The key is present only when the list is not empty, on a clean run and on an escalation alike.

The merged base is one more local branch. The milestone's base branch is never checked out, merged into or moved, and nothing is pushed. Integrate later finds each blocker's tip already inside the story's tip, so those merges are no-ops.

#### What an escalation report contains
```

- [ ] **Step 3: Add `bases` to the clean-run keys**

Old (line 128, the `integrated` bullet, unchanged, used as the anchor):

```
- `integrated`: `{"branch", "worktree", "merged", "resolved"}`. `branch` is `<prefix>-integrate` and `worktree` is its worktree. `merged` lists, in merge order, the story ids whose tip is in `branch`, including tips an earlier run already merged. `resolved` lists the story ids whose conflict a resolver fixed in this run.
```

New:

```
- `integrated`: `{"branch", "worktree", "merged", "resolved"}`. `branch` is `<prefix>-integrate` and `worktree` is its worktree. `merged` lists, in merge order, the story ids whose tip is in `branch`, including tips an earlier run already merged. `resolved` lists the story ids whose conflict a resolver fixed in this run.
- `bases`: `{"story", "branch", "blockers"}` for every merged base this run built, only when there is one. See [Multiple blockers](#multiple-blockers).
```

- [ ] **Step 4: Add the failed-base paragraph and the `bases` key to the escalation report**

Old (line 228-229, end of the first escalation paragraph):

```
plan. A coder that reports `blocked` ends its subtask escalated at `implement`,
and review never runs.
```

New:

```
plan. A coder that reports `blocked` ends its subtask escalated at `implement`,
and review never runs.

A merged base that fails (see [Multiple blockers](#multiple-blockers)) escalates with `failed_phase` `"base"` and `subtask` `null`, because no subtask of the story ran. `detail` says why and names the base branch and its worktree. The story is recorded `escalated`. When that story has no subtasks of its own, `level` is `null` too. A merged base that raised an unexpected error instead has `failed_phase` `null` and `detail` `<ExceptionType>: <message>`.
```

Old (lines 238-240, the `stopped` bullet):

```
- `stopped`: a list of {"story", "subtask", "before_phase"}, one for each lane
  the stop parked. `before_phase` is the phase it would have run next. A
  stopped subtask and its story are recorded `stopped`, not `escalated`.
```

New:

```
- `stopped`: a list of {"story", "subtask", "before_phase"}, one for each lane
  the stop parked. `before_phase` is the phase it would have run next, or `null` for a lane that stopped before starting its subtask. `subtask` is `null` for a lane whose merged base's resolver was parked. A
  stopped subtask and its story are recorded `stopped`, not `escalated`.
- `bases`: the merged bases built before the run stopped, as on a clean run.
```

And change the list's lead-in on line 233, old:

```
Two more keys appear only when they are not empty:
```

New:

```
Three more keys appear only when they are not empty:
```

- [ ] **Step 5: Confirm (GREEN)**

```bash
grep -n '^#### Multiple blockers' README.md
grep -n '<prefix>/base-<short id of the story>' README.md
grep -n 'Merged bases' README.md
grep -n 'MergeInProgressError' README.md
grep -n 'failed_phase` `"base"` and `subtask` `null`' README.md
grep -n '`bases`: ' README.md
grep -n 'A story with one blocker keeps the fast path' README.md
grep -n 'never checked out, merged into or moved, and nothing is pushed' README.md
grep -nE 'barrier|two or more stories' README.md
```

Expected: two lines for `<prefix>/base-<short id of the story>` (the Task 2 dry-run bullet and this section's first paragraph), two lines for `` `bases`: `` (clean run and escalation), exactly one line for every other positive check, and nothing for the final command.

---

### Task 4: Milestone-wide `am resume`

**Files:**
- Modify: `README.md:25` (Usage resume paragraph), `README.md:117-128` (add a `resumed` bullet to the clean-run keys), `README.md:261-269` (Relaunching resumes), `README.md:271-283` (Not there yet)
- Test: none (no prose tier); grep in Steps 1 and 5.

**Interfaces:**
- Consumes: the `bases` bullet Task 3 added after `integrated` (the `resumed` bullet goes after it).
- Produces: nothing later tasks rely on.

- [ ] **Step 1: Confirm the stale resume text is there (RED)**

```bash
grep -nE 'not milestone-aware|have no effect: the checkpoint|a milestone-shaped run' README.md
```

Expected: matches on lines 25, 267, 269 and 273.

- [ ] **Step 2: Rewrite the Usage resume paragraph**

Old (line 25):

```
Pick a stopped or killed subtask back up at the phase it was interrupted in. There is no `--base-branch` and no `--branch-prefix` here: both were decided when the run started and are recorded on the run. `--verify` and `--allow-no-verification` are still accepted but have no effect: the checkpoint carries the verification suite and the opt-out the run started with. See [Relaunching resumes](#relaunching-resumes) for what a resume does and when it is refused.
```

New:

```
Pick a run back up where it was interrupted: a `--card` run's one stopped or killed subtask, at the phase it was interrupted in, or a `--milestone` run's whole milestone, under the same run id. There is no `--base-branch`, no `--branch-prefix` and no `--max-concurrent` here: they were decided when the run started and are recorded on the run. The verification suite is not recorded. A walk continued from a checkpoint keeps the suite and the opt-out the run started with, so on a `--card` run `--verify` and `--allow-no-verification` have no effect. On a milestone run, pass the same `--verify` commands (or `--allow-no-verification`) again: they are what every subtask with no checkpoint, every merged base and Integrate run. See [Relaunching resumes](#relaunching-resumes) for what a resume does and when it is refused.
```

- [ ] **Step 3: Add `resumed` to the clean-run keys**

Old (the `bases` bullet Task 3 added):

```
- `bases`: `{"story", "branch", "blockers"}` for every merged base this run built, only when there is one. See [Multiple blockers](#multiple-blockers).
```

New:

```
- `bases`: `{"story", "branch", "blockers"}` for every merged base this run built, only when there is one. See [Multiple blockers](#multiple-blockers).
- `resumed`: `true`, only on a run continued by `am resume`, on every report shape. `completed` then lists only what finished in that invocation.
```

- [ ] **Step 4: Rewrite "Relaunching resumes" from line 261, and "Not there yet"**

Old (lines 261-283, from "`am resume <run-id>` continues one stopped" through "for everything deferred."):

```
`am resume <run-id>` continues one stopped (parked) or killed subtask from its newest checkpoint. It no longer refuses a stopped subtask. A checkpoint is saved before every phase runs, so the walk goes on at the interrupted phase, which runs again from its start, and nothing before that phase re-runs. One exception: a phase that finished just before the process was killed, before the next checkpoint was saved, runs again too, because phases are at-least-once. Attempts left recorded `started` with no terminal event are marked `harness_error` first. `data` names the phase the walk continued at as `resumed_from` and lists the marked attempts as `discarded_attempts`. A resumed walk that ends `done` or `stopped` exits 0, and one that escalates exits 1.

Resume refuses before anything runs, with `{"ok": false, "error": {...}}` and exit code 3, when:

- the workflow changed since the checkpoint was saved (its digest no longer matches). Start a fresh `am run --card`.
- the subtask has no checkpoint (the run died before its first turn, or it predates checkpoints), its newest checkpoint is `done`, or its newest checkpoint was left by a phase escalation. An escalated subtask is never resumed.
- the run has no subtask recorded `started` or `stopped`, or more than one of them (a milestone-shaped run).

`am resume` is not milestone-aware: it drives that one subtask and stops, and never runs a later subtask, story, level or Integrate. To continue a milestone, relaunch the `am run --milestone` command.

#### Not there yet

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

New:

```
`am resume <run-id>` on a milestone run continues that milestone under the same run id, instead of starting a new run. It finds the milestone from the run id, reads the board again and derives the plan exactly as a relaunch does (no story or milestone state is saved), and reuses the `branch_prefix`, `base_branch` and `max_concurrent_stories` the run recorded. Attempts left recorded `started` with no terminal event are marked `harness_error`, every open subtask recorded `stopped`, `escalated` or `started` is recorded `started` again, and the run goes on as a fresh one would, with the same scheduling and stop. Every open subtask with a checkpoint in this run continues from it; an escalated subtask continues at the phase that failed. A parked merged-base resolver continues from its own `base-<story id>` checkpoint, merged bases are built again (a tip already merged is skipped), and Integrate runs when every story finished clean. Pass your `--verify` commands again: the suite is not recorded, and it is what every subtask with no checkpoint, every merged base and Integrate run. The report has the shape of a fresh run's, plus `resumed: true`, and `completed` lists only what finished in this invocation. It exits 0 when the milestone finished and 1 when it escalated again.

A milestone resume refuses before anything is written and before git is fetched, with `{"ok": false, "error": {...}}` and exit code 3, when:

- the run is `done`. Start new work with `am run --milestone`.
- any open subtask, or any open `base-<story id>` resolver, has a checkpoint saved under a workflow that has changed since (its digest no longer matches). One stale checkpoint refuses the whole resume, and nothing is written. Relaunch with `am run --milestone` instead: a relaunch starts such a card again from its first phase rather than refusing.
- the run id's milestone is not on the board, or more than one root card has its short id, or the stories now have a blocker cycle.

On a `task` run (`am run --card`), `am resume <run-id>` continues one stopped (parked) or killed subtask from its newest checkpoint. It no longer refuses a stopped subtask. A checkpoint is saved before every phase runs, so the walk goes on at the interrupted phase, which runs again from its start, and nothing before that phase re-runs. One exception: a phase that finished just before the process was killed, before the next checkpoint was saved, runs again too, because phases are at-least-once. Attempts left recorded `started` with no terminal event are marked `harness_error` first. `data` names the phase the walk continued at as `resumed_from` and lists the marked attempts as `discarded_attempts`. A resumed walk that ends `done` or `stopped` exits 0, and one that escalates exits 1.

A `task` run's resume refuses before anything runs, with `{"ok": false, "error": {...}}` and exit code 3, when:

- the workflow changed since the checkpoint was saved (its digest no longer matches). Start a fresh `am run --card`.
- the subtask has no checkpoint (the run died before its first turn, or it predates checkpoints), its newest checkpoint is `done`, or its newest checkpoint was left by a phase escalation. An escalated subtask of a `task` run is never resumed.
- the run has no subtask recorded `started` or `stopped`, or more than one of them.

#### Not there yet

- `watch`, `retry` and `cancel` do not exist.
- There is no `--no-integrate` option: a milestone run that finishes clean always ends with Integrate.

See section 4 of the
[orchestration addendum](docs/superpowers/specs/2026-09-24-orchestration-design.md#4-deferred-to-the-follow-up-milestone-found-now-not-cut-yet),
section 5 of the
[parallel-stories addendum](docs/superpowers/specs/2026-09-24-parallel-stories-design.md#5-deferred),
section 6 of the
[Integrate addendum](docs/superpowers/specs/2026-09-25-integrate-design.md#6-deferred)
and section 10 of the
[supervisor-tree addendum](docs/superpowers/specs/2026-09-25-supervisor-tree-design.md#10-deferred)
for everything deferred.
```

- [ ] **Step 5: Confirm (GREEN)**

```bash
grep -nE 'not milestone-aware|have no effect: the checkpoint|a milestone-shaped run' README.md
grep -n 'on a milestone run continues that milestone under the same run id' README.md
grep -n 'Pass your `--verify` commands again' README.md
grep -n 'the run is `done`. Start new work with `am run --milestone`.' README.md
grep -n 'One stale checkpoint refuses the whole resume' README.md
grep -n 'a relaunch starts such a card again from its first phase' README.md
grep -n '`resumed`: `true`' README.md
grep -n "A \`task\` run's resume refuses" README.md
grep -n '2026-09-25-supervisor-tree-design.md#10-deferred' README.md
```

Expected: the first prints nothing; each other command prints exactly one line.

---

### Task 5: Design-spec pointer, full verification, commit

**Files:**
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` — add one line after line 434 (end of the section 11 "Status" paragraph).
- Test: the full default suite, `uv run pytest`.

**Interfaces:**
- Consumes: Tasks 1-4's README edits.
- Produces: the commit.

- [ ] **Step 1: Confirm the pointer is missing (RED)**

```bash
grep -n 'supervisor-tree' docs/superpowers/specs/2026-09-23-agent-manager-design.md
```

Expected: no output.

- [ ] **Step 2: Add the pointer line**

Old (lines 432-436):

```
`am run --milestone --max-concurrent N`). Levels are barriers: the next level
starts only after every story of the current one has finished. See the
parallel-stories addendum, `2026-09-24-parallel-stories-design.md`.

Stories within a dependency level run in parallel, bounded by
```

New:

```
`am run --milestone --max-concurrent N`). Levels are barriers: the next level
starts only after every story of the current one has finished. See the
parallel-stories addendum, `2026-09-24-parallel-stories-design.md`.

**Status (milestone 7):** superseded by the supervisor-tree addendum, `2026-09-25-supervisor-tree-design.md`: levels are no longer barriers (a story starts once its own blockers finish), a story with two or more blockers roots on a merged base, and `am resume` continues a milestone run.

Stories within a dependency level run in parallel, bounded by
```

This is the one added line. The older status paragraph stays as the milestone-4 record.

- [ ] **Step 3: Run the stale-text guard over README.md**

```bash
grep -nE 'barrier|not milestone-aware|two or more stories|level N\+1' README.md
grep -n '#multiple-blockers' README.md
git diff --stat
```

Expected: the first prints nothing. The second prints at least four lines (Milestone runs intro, Parallel runs, dry-run bullet, clean-run `bases`, escalation paragraph). `git diff --stat` lists exactly `README.md` and `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, nothing under `src/` or `tests/`.

- [ ] **Step 4: Run the full suite**

```bash
uv run pytest
```

Expected: every test passes, `tests/e2e` included, with the same count as before the change (no test reads README.md, so nothing should move).

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-23-agent-manager-design.md
git commit -F - <<'EOF'
docs: dataflow scheduling, merged bases and milestone resume

README: stories start as soon as their own blockers finish (levels stay
only as waves in --dry-run and reports); --dry-run documents merged_from
and no longer refuses two or more blockers; a new "Multiple blockers"
section covers <prefix>/base-<short id>, the resolver under "Merged
bases", MergeInProgressError, the one-blocker fast path, data.bases and
failed_phase "base"; am resume now continues a milestone run under the
same run id. Design spec section 11 points at the supervisor-tree
addendum.

Where the code and the addendum disagree, the README follows the code:
- A merged base merges its blockers' tips in the story's blocked_by
  order (dag.story_root, orchestrate.blocker_tips), not census order.
- After the stop, a story whose blockers all finished clean ends
  stopped when it takes its slot; only a story behind a failed or
  stopped blocker stays pending.
- On a milestone resume, --verify and --allow-no-verification are used
  for fresh subtasks, merged bases and Integrate, so README.md's Usage
  paragraph no longer says they have no effect.

The cli.dry_run_payload docstring still says the real run refuses a
multi-blocker story; that is a code change and out of scope here.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
```

Expected: one commit on `m7/task-document-dataflow-47c579e9` touching two files.
