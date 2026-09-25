# agent-manager

A local-first CLI that drives a `brd` milestone to completion by dispatching AI
harness instances (Claude Code, Codex, Pi) as subprocesses.

The program owns the dependency graph, the git mechanics, the gates, the retries
and the persistence. A harness is launched only for the steps that genuinely need
a model; everything else is plain Python.

See `docs/superpowers/specs/2026-09-23-agent-manager-design.md` for the design.

## Usage

Drive one subtask card end to end. `--branch-prefix` is required — it is the
milestone prefix of the branch the run cuts — and `--verify` is repeatable, one
whole verification command per occurrence, run in the order given:

```bash
am run --card 19efcddc-0000-0000-0000-000000000000 \
  --branch-prefix m2 \
  --verify "uv run pytest" \
  --verify "uv run ruff check"
```

Pick a killed run back up at the phase it died in. There is no `--base-branch`
and no `--branch-prefix` here: both were decided when the run started and are
recorded on the run. The verification suite is not recorded, so a resume is told
it the same way a fresh run was:

```bash
am resume 20260923T140506Z-19efcddc --verify "uv run pytest"
```

Every command prints one line of JSON — `{"ok": true, "data": ...}` on success,
`{"ok": false, "error": {...}}` on a refusal. Add `--pretty` to indent it.

### Milestone runs

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
`--milestone`, neither of them, a blank `--milestone`, `--dry-run` with
`--card`, `--max-concurrent` with `--card`, and a `--max-concurrent` below 1.
These are usage errors, like a missing `--branch-prefix`: Typer prints the
message on stderr, nothing is printed on stdout, and the exit code is 2.

#### Preview with `--dry-run`

```bash
am run --milestone "document milestone runs" --branch-prefix m3 --dry-run --pretty
```

The preview reads the board and writes nothing: no run directory, no branch, no
worktree, no board change. It exits 0. It takes `--max-concurrent` too, and
`data.max_concurrent` echoes it (4 when not given). `data.levels` is a list of
`{"level", "concurrent", "stories"}`, where `concurrent` is how many of that
level's stories would run at once. Each story is `{"story", "title", "root", "subtasks"}`,
and each subtask is `{"id", "title", "status", "branch", "base"}`. Only the
subtasks still to run are listed. `data.already_done` lists what will not run:
`{"kind": "story", "id", "title"}` for a story with nothing left, and
`{"kind": "subtask", "id", "title", "story"}` for a done subtask of a story that
still has work.

`data.integrate` is the plan for [Integrate](#integrate), the step that runs after the last level: `{"branch", "worktree", "order"}`. `branch` is `<prefix>-integrate`, `worktree` is its worktree, `<repo>/.claude/worktrees/<prefix>-integrate`, and `order` lists `{"story", "tip"}` in the order the tips will be merged. It names every story that has subtasks, done or not, because Integrate merges them all. The preview creates neither the branch nor the worktree.

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

Levels run one after another, a level's stories run on up to `--max-concurrent`
lanes, and each story's subtasks run in order. Before the first subtask the run
does `git fetch origin` once (only when
a remote named `origin` exists) and `git worktree prune` once. Each finished
subtask is `done` on the board, and the rollup moves its story and the
milestone with it.

A clean run exits 0, and `data` holds:

- `done`: `true`, reported only once [Integrate](#integrate) has merged every tip and passed its final check.
- `run_id`: the run, for `am status <run_id>` and `am logs`.
- `levels`: the stories this run had work for, as `{"level", "stories"}` with
  story ids.
- `completed`: the subtask ids finished in this run, in order.
- `tips`: `{"story", "tip"}` for every story in the milestone that has
  subtasks, naming the branch its stack ends on. These are the branches
  Integrate merged.
- `warnings`: board writes that failed but did not stop the run, as text.
- `integrated`: `{"branch", "worktree", "merged", "resolved"}`. `branch` is `<prefix>-integrate` and `worktree` is its worktree. `merged` lists, in merge order, the story ids whose tip is in `branch`, including tips an earlier run already merged. `resolved` lists the story ids whose conflict a resolver fixed in this run.

The tips are merged into `<prefix>-integrate` and nowhere else. The story branches stay local and stacked, the base branch does not move, and nothing is pushed. Merging the integration branch into the base branch is left to you (see [Integrate](#integrate)).

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

To continue, fix the escalation and relaunch the same `am run --milestone`
command (see [Relaunching resumes](#relaunching-resumes)). The stopped subtask
picks up where it parked, and every card already `done` on the board is
skipped. `am resume <run-id>` does not continue stopped work: on a run with a
stopped subtask it is refused with `{"ok": false, "error": {...}}` and exit
code 3, and the message names the stopped subtasks and says to relaunch the
milestone command.

Run one `am` process per repository. Two `am` processes on the same repository
or on the same run are not supported.

Limits, stated plainly:

- **Your test suite runs side by side.** Each lane's `verify` phase runs your
  `--verify` commands in its own worktree while other lanes do the same. Tests
  that use a fixed port, a shared file or a shared database will collide. Run
  such a repository with `--max-concurrent 1`.
- **One environment per lane.** `uv run pytest` builds
  a `.venv` in each worktree, which takes time and disk once per lane.
- **Machine load.** N lanes means up to N `claude -p` processes at once, and
  nothing rate-limits them.

#### What an escalation report contains

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
  {"level", "story", "subtask", "failed_phase", "detail"}, one for each other
  lane that failed before it saw the stop.
- `stopped`: a list of {"story", "subtask", "before_phase"}, one for each lane
  the stop parked. `before_phase` is the phase it would have run next. A
  stopped subtask and its story are recorded `stopped`, not `escalated`.

An escalation at [Integrate](#integrate) has its own shape. The run exits 1, and `data` holds `escalated` (`true`), `phase` (`"integrate"`), `story`, `files`, `detail`, `run_id` and `warnings`. There is no `integrated` key.

- `story` is the story whose tip was being merged, or `null` when the final check of the integrated branch failed.
- `files` lists the conflicting files the resolver did not finish. It is empty when a merge was already in progress in the integration worktree, and when the final check failed.
- `detail` says what went wrong and names the integration worktree.

The run is recorded `escalated`. The integration branch and its worktree are left exactly as Integrate left them, a merge still in progress included, so you can finish it there. See [Integrate](#integrate) for what to do next.

When the run cannot start at all (an unknown or ambiguous milestone, a blocker
cycle, a board error), it prints `{"ok": false, "error": {...}}` and exits 3.

#### Relaunching resumes

To go on after an escalation, a stopped lane or a killed run, fix the cause
and run the same `am run --milestone` command again. It starts a new run that
skips every card already `done` on the board. A subtask that was stopped or
killed part way picks up in its existing worktree and does not redo a plan
that already passed. Relaunching a finished milestone drives no subtask but still runs [Integrate](#integrate). With every tip already merged, it merges nothing and dispatches no agent, runs the final check again, and reports `done` with an empty `completed` and an `integrated` whose `resolved` is empty. Relaunching after an Integrate escalation runs Integrate again, so commit your fix in the integration worktree first.

`am resume <run-id>` is not milestone-aware and does not continue a milestone.
On a run with a stopped subtask it is refused with exit code 3, and its message
says to relaunch. Relaunch the `am run --milestone` command instead.

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

## Develop

```bash
uv sync
uv run pytest
```
