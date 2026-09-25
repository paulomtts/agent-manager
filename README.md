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

- There is no Integrate step: nothing merges the story tips into one branch.
- `am resume` is not milestone-aware.
- `watch`, `retry` and `cancel` do not exist.

See section 4 of the
[orchestration addendum](docs/superpowers/specs/2026-09-24-orchestration-design.md#4-deferred-to-the-follow-up-milestone-found-now-not-cut-yet).

## Develop

```bash
uv sync
uv run pytest
```
