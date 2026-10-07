# agent-manager

A local-first CLI that drives a `brd` milestone to completion by dispatching AI
harness instances (Claude Code, Codex, Pi) as subprocesses.

The program owns the dependency graph, the git mechanics, the gates, the retries
and the persistence. A harness is launched only for the steps that genuinely need
a model; everything else is plain Python.

See `docs/superpowers/specs/2026-09-23-agent-manager-design.md` for the design.

## Install

```bash
uv tool install agent-manager     # or: pipx install agent-manager
```

To work on agent-manager itself, clone the repository and run `uv sync`.

## Requires

- `git`
- [`brd`](https://github.com/paulomtts/brd) — the board `am` drives
- `claude` (Claude Code) on `PATH` — the only harness wired up today; Codex and Pi are planned
- `bwrap` (bubblewrap) or `unshare` (util-linux), optional — `am run` starts every agent in a PID namespace of its own with one of them, so an agent cannot signal `am` (see [Isolating agents with `--isolation`](#isolating-agents-with---isolation)). Without either, runs go un-isolated with a warning.

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

Pick a run back up where it was interrupted: a `--card` run's one stopped or killed subtask, at the phase it was interrupted in, or a `--milestone` run's whole milestone, under the same run id. There is no `--base-branch`, no `--branch-prefix` and no `--max-concurrent` here: they were decided when the run started and are recorded on the run. So are the verification suite and the opt-out. With no `--verify`, the resume uses the recorded suite. A `--verify` passed now replaces the recorded suite for whatever starts afresh and is recorded in its place, and when it differs from the recorded one, `data.warnings` gains `verification: replaced in run record: [...]`, naming the suite it replaced. `--allow-no-verification` can only add the opt-out to a run that lacked it. A walk continued from a checkpoint keeps the suite and the opt-out the checkpoint was saved with, so on a `--card` run `--verify` and `--allow-no-verification` do not change that walk. The exception is a checkpoint declined because its worktree could not be kept (see [Resuming: what runs again](#resuming-what-runs-again)): that subtask is walked again from its first phase with the run's recorded suite (or the `--verify` passed now, which replaces it) and the run's opt-out, not the kept suite, and a `verification: kept from checkpoint: [...]` warning shown beside the decline warning is then stale. When `--verify` on a `--card` run differs from the kept suite, `data.warnings` names the kept suite as `verification: kept from checkpoint: [...]`. On a milestone run, the recorded suite (or the `--verify` passed now) is what every subtask with no checkpoint, every merged base and Integrate run. The run's isolation mode is restored as well. See [Relaunching resumes](#relaunching-resumes) for what a resume does and when it is refused.

```bash
am resume 20260923T140506Z-19efcddc
```

Every command prints one line of JSON — `{"ok": true, "data": ...}` on success,
`{"ok": false, "error": {...}}` on a refusal. Add `--pretty` to indent it.
The two exceptions are the streams. `am watch --follow` prints one JSON object per line until stopped (see [Watching a run](#watching-a-run)), and `am logs --follow` prints one JSON object per line until the attempt it follows is over (see [Reading an attempt's output](#reading-an-attempts-output)).

### Milestone runs

Drive every remaining subtask of one milestone. A story starts as soon as every story it is blocked by has finished clean, and up to `--max-concurrent` stories run at once. Inside a story, subtasks always run in order, each on its own local branch stacked on the branch before it:

```bash
am run --milestone "document milestone runs" \
  --branch-prefix m3 \
  --verify "uv run pytest" \
  [--max-concurrent N]
```

`--max-concurrent N` is how many stories run at once, across the whole milestone. It defaults to 4, and `--max-concurrent 1` runs one story at a time. See [Parallel runs](#parallel-runs) for what runs together and how a run stops, [Several am processes](#several-am-processes) for running more than one `am` at once and for the limits, and [Multiple blockers](#multiple-blockers) for a story with two or more blockers.

`--milestone` takes the milestone card's id, its exact title (case does not
matter), or a piece of its title that matches exactly one root card. A piece
that matches several root cards is refused with the list of matches, and one
that matches none is refused with the list of root cards. Both come back as
`{"ok": false, "error": {...}}` with exit code 3.

To drive one story of a milestone instead of the whole milestone, with no Integrate, use `--story`: see [Running one story with `--story`](#running-one-story-with---story).

`--branch-prefix` is required with `--card`, `--milestone` and `--story`, and optional with `--board` (see [Running every open milestone with `--board`](#running-every-open-milestone-with---board)). Every branch the run cuts is named
`<prefix>/task-<title slug>-<first 8 hex of the card id>`, and its worktree is
`<repo>/.claude/worktrees/<branch>`. `--verify` is repeatable, passed through as
written, and run in the order given. With no `--verify`, pass
`--allow-no-verification` to opt out on purpose; with neither, the verification
gate refuses to go on. `--repo-dir` defaults to `.` and `--base-branch` to
`master`.

Each `--verify` command runs with `AM_RUN_ID` (the run's id) and
`AM_CARD_ID` (the card being verified) added to its environment.
The base-branch and final integration checks run their commands with neither set.

Some combinations are refused before anything is read: `--card` together with `--milestone`, `--board` together with `--card` or with `--milestone`, `--story` together with `--card`, `--milestone` or `--board`, none of the four, a blank `--milestone`, a blank `--story`, a missing `--branch-prefix` with `--card`, `--milestone` or `--story`, a blank `--branch-prefix` with `--board`, `--dry-run` with `--card`, `--max-concurrent` with `--card` or `--story` (whatever its value), a `--max-concurrent` below 1 (with `--milestone` or `--board`), and `--detach` with `--dry-run`.
These are usage errors: Typer prints the message on stderr, nothing is printed on stdout, and the exit code is 2.

#### Running detached with `--detach`

```bash
am run --milestone "document milestone runs" --branch-prefix m3 --verify "uv run pytest" --detach
```

`--detach` works with `--card`, `--milestone` and `--board`. The command first does everything a foreground run does before its first subtask: it reads the board, makes every check, records the run and takes its lease. A refusal at that point comes back as the usual `{"ok": false, ...}` envelope with exit code 3, and nothing starts. Then the run moves to a background process in its own session, and the command prints one envelope and exits 0:

```json
{"ok":true,"data":{"detached":true,"log":"/home/me/.local/share/agent-manager/runs/20261004T090000Z-1a2b3c4d/run.log","pid":48213,"run_id":"20261004T090000Z-1a2b3c4d"}}
```

- `run_id` is the id `am runs`, `am status`, `am watch`, `am pause` and `am resume` take. `pid` is the background process. It holds the run's lease, and `am runs` and `am status` show it as the lease's `pid`.
- The background process writes its output to `run.log`. When the run ends it writes `report.json`. Both files are in `<data dir>/runs/<run-id>/` (`$XDG_DATA_HOME/agent-manager`, or `~/.local/share/agent-manager`) and both are mode 0600. `report.json` holds the envelope the same run would have printed in the foreground: `{"ok": true, "data": ...}` for a run that finished, escalated, stopped or was canceled, or `{"ok": false, "error": ...}` for a run the tool could not carry on. It is written in one step, so it is either absent or complete.
- A crash writes no `report.json`. Its traceback is in `run.log`, the lease and claims are released, and the run can be resumed like any crashed run.
- The exit code is 0 whenever the run was handed off, even if it later escalates. Read the outcome from `report.json` or `am status`.
- A missing verification command is not caught before the run starts. The verification gate runs during the explore phase, so with `--detach` it shows up in `report.json` and `am status`, not on your terminal. Pass `--verify` or `--allow-no-verification`.
- `--detach` with `--dry-run` is refused as a usage error (exit 2).
- With `--story` it behaves as with `--milestone`: the same pre-flight refusals before anything starts (`StoryBlockedError` included), the same envelope, and the same `run.log` and `report.json`.
- With `--board`, the command runs the board's whole pre-flight here: the argument checks, the board read, the cycle check, each milestone's prefix and base, and the up-front claim check. A refusal is the usual envelope with exit code 3, and nothing starts. Then the board run moves to the background process. The envelope's `data` has the keys `board`, `detached`, `pid`, `log`, `report` and `levels`, and no `run_id`: each milestone's run is created when that milestone starts, and `am runs` or `am watch --all` finds it. `log` is `<data dir>/boards/<stamp>-<digest>.log` and `report` is `<data dir>/boards/<stamp>-<digest>.report.json`, where `<digest>` is the repository's digest. Both are mode 0600. The report holds the envelope `am run --board` would have printed. A claim another run takes after the up-front check shows up there as an `escalated` milestone.
- Later versions may add keys to these envelopes. Ignore keys you do not know.

#### Isolating agents with `--isolation`

```bash
am run --milestone "document milestone runs" --branch-prefix m3 --verify "uv run pytest" --isolation bwrap
```

An agent that stops a stuck test with `pkill -f pytest`, `killall python` or `kill -9 -1` can hit `am` itself and end the whole run. `--isolation` starts every agent in a PID namespace of its own, where it sees and can signal only its own process tree. It takes `auto` (the default), `bwrap`, `unshare` or `none`, and works with every form of `am run`: `--card`, `--milestone`, `--story` and `--board`, with or without `--detach`. It is ignored with `--dry-run`: a preview launches nothing and probes nothing.

The mode is decided once, right after the argument checks and before the board is read, the run is recorded, its lease is taken or anything is forked:

- `none`: agents run un-isolated. Nothing is probed and there is no warning.
- `bwrap` or `unshare`: only that mode is probed, and there is no fallback. When it cannot start, the run is refused with `IsolationUnavailableError`, as `{"ok": false, "error": {"type": "IsolationUnavailableError", "message"}}`, and exit code 3, before anything is written: no run row, no run directory, no lease and no claim. The message is `isolation <mode> is unavailable: <reason> — pass --isolation none to run without it`. `<reason>` starts with the exact command the probe ran and ends with what went wrong (`exited 1`, `timed out after 10s`, or `could not start: ...`), so you can run that command by hand to see why.
- `auto`: `bwrap` if it starts, else `unshare` (still isolated, no warning), else the run goes on un-isolated with the warning `isolation: none (bwrap and unshare are unavailable): agents can signal the engine`. `auto` never refuses.

A probe runs the mode's command on `true`, with a 10-second timeout, at most once per process for each mode.

What each mode changes:

- `bwrap` starts each agent under `bwrap --bind / / --dev-bind /dev /dev --proc /proc --unshare-pid --die-with-parent --new-session`. The whole filesystem stays bound read-write, so the worktree, `~/.claude`, caches and tools are exactly what they are un-isolated, and the network is untouched: `bwrap` here is not a filesystem or network sandbox. Only the PID namespace is new (`--unshare-pid`), with a fresh `/proc`, so inside it `ps` and `pkill -f` see only the agent's own process tree.
- `unshare` starts each agent under `unshare --user --map-root-user --pid --fork --mount-proc`: a new user namespace and a new PID namespace. The user namespace is what lets an unprivileged user create the PID namespace, and it maps you to uid 0 inside, so file ownership looks different from inside the agent: your own files show as owned by `root`.

Where you see which mode a run got:

- The run records its mode as `config.launcher` (`direct` when un-isolated, else `bwrap` or `unshare`) and the `auto` warning as `config.isolation_warning` (`null` when there is none), for example in the journal head line's `payload.config`.
- The warning is appended once to the envelope's `data.warnings`, at the top level (on `--board`, never inside a milestone's entry). With `--detach` it is in the hand-off envelope.
- `am status <run-id>` always has a `warnings` key: `[]`, or a list holding the run's recorded isolation warning, so a detached run whose envelope is gone still says it is un-isolated.

Every agent's brief also carries a "Process safety" block telling it never to use `pkill -f`, `pkill` by name, `killall`, `kill -1` or `kill` with a pattern, and to stop a stuck test with `timeout` or by a PID it recorded. That is advice an agent can ignore; isolation is the guarantee. `am resume` restores the mode a run recorded (see [Relaunching resumes](#relaunching-resumes)).

#### Verification commands stay out of `ps`

An agent's `pkill -f "uv run pytest"` matches every process whose command line holds that text, and `am run --verify "uv run pytest"` used to be one of them. So when `am run` or `am resume` is given at least one `--verify X` or `--verify=X` (before any `--`), `am` re-execs itself at once with a neutral command line: every `--verify` is removed, the hidden flag `--verify-from-env` takes their place, and the commands travel in the environment variable `AM_VERIFY_JSON`, a JSON list in command-line order. `pkill -f` matches command lines, never environments, so it can no longer match `am`. Every other token stays, so identity options such as `--milestone`, `--story` and `--branch-prefix` remain visible in `ps`:

```text
/usr/bin/python3 /home/me/.local/bin/am run --verify-from-env --milestone document milestone runs --branch-prefix m3
```

This holds for a foreground run, for a `--detach` run (the background process is forked after the re-exec, so it has the neutral command line too) and for `am resume`. The run reads `AM_VERIFY_JSON` once, at start, and removes it from its own environment, so no agent, verification command or other child process inherits it. Each `--verify` command still runs exactly as written.

`--verify-from-env` is internal: it is what `ps` shows, not an option to type, and `--help` does not list it. Typed by hand it is a usage error (exit code 2, the message on stderr, the variable's value never echoed) when it is combined with `--verify`, when `AM_VERIFY_JSON` is unset, or when `AM_VERIFY_JSON` is not a JSON list of strings.

If the re-exec itself fails, the run goes on with its original command line, verification text included, and the ok envelope's `data.warnings` ends with `argv: verification commands visible in the process command line`.

#### Preview with `--dry-run`

```bash
am run --milestone "document milestone runs" --branch-prefix m3 --dry-run --pretty
```

The preview reads the board and writes nothing: no run directory, no branch, no
worktree, no board change. It exits 0. It takes `--max-concurrent` too, and
`data.max_concurrent` echoes it (4 when not given). `data.levels` groups the stories still to run into waves, as a list of `{"level", "concurrent", "stories"}`. It is a preview only: the real run starts each story as soon as its own blockers have finished, not when a whole level has. `concurrent` is how many of that level's stories could run at once, `min(stories in the level, max_concurrent)`. Each story is `{"story", "title", "root", "subtasks"}`, plus `merged_from` when its root is a merged base (see below), and each subtask is `{"id", "title", "status", "branch", "base"}`. Only the
subtasks still to run are listed. `data.already_done` lists what will not run:
`{"kind": "story", "id", "title"}` for a story with nothing left, and
`{"kind": "subtask", "id", "title", "story"}` for a done subtask of a story that
still has work.

Card statuses decide what counts as finished and what is in the plan at all.
`done` and `merged` (a human's step after integrating) both mean finished,
everywhere `am` asks whether a subtask, story or milestone is done, including
`already_done`. `canceled` and `archived` mean out of play: the card is left out
of the plan, is never run and is not counted as remaining work, and a `blocked_by`
edge pointing at such a card is ignored, as in leave-me-alone. Statuses are
read in any case. The status rollup never overwrites a `merged`, `canceled` or
`archived` card, ignores `canceled`/`archived` children and counts `merged`
children as `done`. `am` never writes `merged`, `canceled` or `archived` itself.

`data.integrate` is the plan for [Integrate](#integrate), the step that runs after every story has finished: `{"branch", "worktree", "order"}`. `branch` is `<prefix>-integrate`, `worktree` is its worktree, `<repo>/.claude/worktrees/<prefix>-integrate`, and `order` lists `{"story", "tip"}` in the order the tips will be merged. It names every story that has subtasks, done or not, because Integrate merges them all. The preview creates neither the branch nor the worktree.

Read the `base` column before a real run:

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

#### Running one story with `--story`

```bash
am run --story "<title piece>" --branch-prefix m3 --verify "uv run pytest"
```

Drive every remaining subtask of one story, on the story's own stack: in order, each on its own local branch stacked on the branch before it. A done subtask is skipped, but its branch still anchors the next one. The shape is `am run --story STORY --branch-prefix P [--base-branch B] [--verify CMD ...] [--allow-no-verification] [--dry-run] [--detach] [--repo-dir D]`. Pass the `--branch-prefix` the milestone's other stories run under: every branch is `<prefix>/task-<title slug>-<first 8 hex of the card id>`, and a story with a blocker roots on that blocker's branch, which exists only under the prefix its own run used.

How `STORY` is matched. A story is a child of a root card (a milestone), whatever its status. In this order:

1. An exact story id wins.
2. The exact id of a milestone or of a subtask is refused: it is not a story.
3. One exact title, in any case, wins, even when it is also a piece of another story's title. Two stories with that exact title are ambiguous.
4. Otherwise the piece must match exactly one story's title.

Milestone and subtask titles never match. Each refusal is `StoryNotFoundError`, printed as `{"ok": false, "error": {...}}` with exit code 3, and its message lists the stories, or the matches for an ambiguous piece.

Where the story starts. The run first checks the whole milestone for a blocker cycle, even one the story is not part of (`DependencyCycleError`, exit code 3). The story then roots as in a milestone run (see [Preview with `--dry-run`](#preview-with---dry-run)), counting only the blockers inside the milestone that it keeps: on `--base-branch` with none, on the kept blocker's tip with one, and on its own merged base `<prefix>/base-<short id>` with two or more (see [Multiple blockers](#multiple-blockers)). Each blocker, by its status, in any case:

- `merged`: dropped, so with no other blocker the story roots on `--base-branch`.
- `done`: kept when its tip, the branch of its last subtask, exists as a local branch, and the story roots on that tip. With no local tip, or no subtasks, it is dropped like a `merged` one, and the story roots on `--base-branch`.
- `canceled` or `archived`: ignored.
- anything else: open, and the run is refused (see below).

An open blocker refuses the run with `{"ok": false, "error": {"type": "StoryBlockedError", "message"}}` and exit code 3, before anything is written: no claim, no `git fetch`, no run row and no run directory. The message names every open blocker by title and id and ends `— run them first, or run the milestone`: run those stories first, or the whole milestone with `am run --milestone`. A story with no subtask left to run is not judged on its blockers.

No Integrate. A story run ends on the story's tip, the branch of its last subtask. It does not run [Integrate](#integrate): it creates no `<prefix>-integrate` branch and no integration worktree, the base branch does not move, and nothing is pushed. A clean run exits 0 with the report of [What a clean run leaves behind](#what-a-clean-run-leaves-behind) minus `integrated`: `done` is `true` without Integrate, and `tips` names this story alone. Cards roll up as in a milestone run: each finished subtask is `done`, then the story, and the milestone once all its stories are done. `am` never writes `merged`, `canceled` or `archived`. A story with no open subtask also exits 0 with that `done` report, with no level and nothing driven. Merging the tip is left to you, or to a later `am run --milestone`, whose Integrate merges every story's tip.

A story run is recorded as a `milestone` run of the story's parent milestone, driving one story at a time, with `story_id` naming the story (see [Listing runs](#listing-runs)).

A story run claims `card:<milestone>`, `card:<story>`, and the `card:<id>` and `branch:<branch>` of each remaining subtask, never `branch:<prefix>-integrate`. So it is refused with `ClaimedError` (exit code 3) beside a live run of its milestone, of one of its remaining subtasks (`--card`), or of the same story, and two story runs of one milestone are kept apart by the milestone's card. See [Several am processes](#several-am-processes).

Pause, resume, detach and preview:

- `am pause`, `am cancel` and the escalation report work as on a milestone run (see [Pausing and cancelling a run](#pausing-and-cancelling-a-run) and [What an escalation report contains](#what-an-escalation-report-contains)).
- `am resume <run-id>` continues that story alone, found from the run's recorded `config.story_id`, with no `--story`, `--branch-prefix` or `--base-branch`; the recorded suite is restored; a `--verify` passed now replaces it, as on a milestone run. It is refused with exit code 3, before anything is written, when the story is no longer a story of that milestone, and with `StoryBlockedError` when one of its blockers has re-opened. See [Relaunching resumes](#relaunching-resumes).
- `--detach` works with `--story` exactly as with `--milestone`: the same envelope, `run.log` and `report.json` (see [Running detached with `--detach`](#running-detached-with---detach)).
- `--dry-run` previews the story and writes nothing. Its `data` is `{"max_concurrent": 1, "levels", "already_done", "integrate": null}`: one level holding the story alone, with its remaining subtasks and their `branch` and `base`. A done blocker the story stacks on gets no row, and `already_done` lists only this story's entries. It refuses everything the run refuses except `ClaimedError`, since a preview checks no claim.

#### Running every open milestone with `--board`

```bash
am run --board --verify "uv run pytest" [--branch-prefix P] [--max-concurrent N]
am run --board --dry-run --pretty
```

`--board` drives every open milestone on the board in one command, as one dependency graph. Each milestone runs exactly as `am run --milestone` would run it: its stories, its [merged bases](#multiple-blockers) and its own [Integrate](#integrate) into its own `<prefix>-integrate`. Across milestones:

- Milestones are leveled by the `blocked_by` edges between them. A milestone that is marked done, or that has nothing open under it, drops out, and so does a blocker that is not an open milestone: it counts as satisfied for scheduling. Such a blocker can still be the milestone's base, when it is `done` but not `merged` and its `<prefix>-integrate` branch is still local (see Stacking below).
- A milestone starts once every open milestone blocking it has finished `done`.
- If a blocker ends in any other status (`escalated`, `stopped`, `canceled`, or `blocked` itself), the milestone is never started: no run, no branch, no worktree, no board change. It is reported `blocked`.
- A milestone whose run raises an error is reported `escalated`, and the other milestones carry on.
- A board with no open milestone is `ok`, runs nothing and exits 0.

Stacking. Each milestone starts from one branch, its base. Only the ids in its `blocked_by` that are milestone roots on the board count here; any other blocker id is ignored. A blocker's prefix is derived the same way as the milestone's own, even when the blocker is no longer open. A blocker counts as landed when its status is `merged`, `canceled` or `archived`, in any case.

| blockers of the milestone (its `blocked_by` milestone roots) | the milestone starts from |
|---|---|
| none, or every blocker is `merged`, `canceled` or `archived` | `--base-branch` |
| exactly one open blocker B | `<prefix of B>-integrate`, which B's own run in this board creates |
| exactly one blocker B that is not open and not landed (in practice `done` but not `merged`), whose `<prefix of B>-integrate` exists as a local branch | `<prefix of B>-integrate` |
| a blocker that is not open and not landed, with no local `<prefix>-integrate` branch | nothing: it is treated as landed and does not count |
| two or more blockers from the two `<prefix of B>-integrate` rows above | refused (`MilestoneBlockersError`, see Refusals below) |

A stacked milestone runs exactly as before from its base: its stories root on the blocker's `<prefix>-integrate` instead of `--base-branch`, and its own Integrate still merges into its own `<prefix>-integrate`. `am` never merges into `--base-branch`. Stacking changes where a milestone starts, not when. It still waits for every open blocker to finish `done`, and is reported `blocked` if one ends any other way.

Flags. Give exactly one of `--card`, `--milestone`, `--story` and `--board`. `--verify`, `--allow-no-verification`, `--base-branch` (default `master`) and `--repo-dir` apply to every milestone. `--branch-prefix` is optional with `--board`. Without it, each milestone's prefix is its own card stem, `<title slug>-<first 8 hex of the card id>`. With `--branch-prefix P`, each milestone's prefix is `P-<stem>`, never `P` itself, so milestone M's integration branch is `P-<stem of M>-integrate`. `--board` with `--card`, `--milestone` or `--story` and a blank `--branch-prefix` with `--board` are usage errors (exit 2).

Refusals. These come in this order, before anything is written. Each prints `{"ok": false, "error": {"type", "message"}}` and exits 3:

1. A blank `--base-branch`.
2. A blocker cycle between milestones (`DependencyCycleError`).
3. A blank prefix, or a prefix two milestones share.
4. A milestone with two or more blockers it could stack on: open, or not landed with a local `<prefix>-integrate` branch (`MilestoneBlockersError`). The message names the milestone and those blockers. A milestone stacks on at most one, so chain them (A ← B ← C): if C is blocked by both A and B, run `brd block B --by A`, then `brd unblock C --by A`, so that C is blocked by B only. One `am run --board` then runs the whole chain, each milestone starting from the previous one's `<prefix>-integrate`. When the message also says to mark blockers `merged`, their work may already have landed: instead of chaining, mark each such blocker `merged` with `brd update <id> --status merged`, a human's step `am` never takes. It then no longer counts.
5. A claim another live run holds, checked once over the claims of every open milestone together (`ClaimedError`, see [Several am processes](#several-am-processes)).

A refused board run leaves no run row, no run directory and no lease for any milestone. A board that cannot be read is refused the same way, as on a `--milestone` run.

The run's `data` is `{"ok", "board": true, "levels", "milestones"}`. It has no `run_id` of its own.

- `levels` is a list of `{"level", "milestones"}`, where `milestones` lists milestone ids. As with `--milestone`, levels are a way to read the plan: a milestone waits only for its own blockers.
- `milestones` has one entry per open milestone, in level order. Each entry has one of three shapes:
  - A milestone that ran: `{"milestone_id", "status", ...}`, followed by every key of that milestone's own `--milestone` report (see [What a clean run leaves behind](#what-a-clean-run-leaves-behind) and [What an escalation report contains](#what-an-escalation-report-contains)), its `run_id` included. `status` is `done`, `escalated`, `stopped` (a pause) or `canceled`.
  - A milestone that never started: `{"milestone_id", "status": "blocked", "blocked_by"}`. `blocked_by` lists the ids of its blockers that did not finish `done`.
  - A milestone whose run raised an error: `{"milestone_id", "status": "escalated", "error"}`, with `error` reading `"<Type>: <message>"`. It has no `run_id` key. One example is a claim that another run took after the board's up-front check.
- `ok` is `true` only when every entry is `done`.

The outer envelope's `ok` is `true` whatever the outcome, because the report itself is a true result. The exit code is 1 only when some entry is `escalated`. A board whose milestones are only `done`, `blocked`, `stopped` or `canceled` exits 0, even when `data.ok` is `false`. Read `data.ok`, not the exit code, to know whether everything finished.

`--dry-run` with `--board` is read-only. It opens no store, checks no claim, writes nothing, and exits 0. It still refuses a blocker cycle, a bad prefix and a board that cannot be read, the same way as above, and it refuses a milestone with two or more blocker milestones it could stack on (open, or unlanded with a local `<prefix>-integrate` branch) the same way as the real run (`MilestoneBlockersError`, exit 3). Its `data` is `{"board": true, "max_concurrent", "levels"}`, with no `ok` and no `run_id`. `levels` is a list of `{"level", "milestones"}`, and each milestone is `{"milestone_id", "title", "branch_prefix", "base_branch", "plan"}`. `branch_prefix` is the prefix that milestone will run under. `base_branch` is the branch the milestone would start from: the `--base-branch`, or its one blocker milestone's `<prefix>-integrate` when it stacks on it. `plan` is exactly that milestone's own `--milestone --dry-run` data (`max_concurrent`, `levels`, `already_done`, `integrate`), computed against `base_branch`, so the `base` column shows the stacking. Read each plan as described in [Preview with `--dry-run`](#preview-with---dry-run), `base` column included.

`--max-concurrent N` is one slot pool for the whole board, not N per milestone. Every story of every milestone takes a slot from the same N, so N caps the stories running at once across the board. It defaults to 4, as with `--milestone`, and `--max-concurrent 1` runs one story at a time on the whole board. Integrate merges do not take a slot (see [Integrate](#integrate)). Everything else in [Parallel runs](#parallel-runs) holds inside each milestone.

One run and one journal per milestone. A board run is not a run itself: nothing records it as a whole. Each milestone it starts is a run of its own, with its own run id, `<data dir>/runs/<run-id>/journal.jsonl` and lease, exactly as a `--milestone` run would be. To follow a board run, take each entry's `run_id` (or find the runs in `am runs`) and read each journal separately with `am watch <run-id>`, or read them all with `am watch --all`. In each journal:

- The first line is a `run_upsert` whose `payload.milestone_id` is that milestone's full card id. It is never `null` on a board run.
- Every line has that run's `run_id`.
- A `story_upsert` line has the story card id in `story`, and its `payload` has no milestone key. A story belongs to the milestone named on the first line of its journal.
- A real story id appears in only one milestone's journal. The synthetic ids `"integrate"`, `"bases"` and `"base-<story id>"` (see [Reading the stream safely](#reading-the-stream-safely)) are fixed names that may appear in several milestones' journals, so identify a story by `(run_id, story)`, never by `story` alone.

Recovery. Because nothing records the board run as a whole, there is nothing to resume at the board level. After a fix, either:

- run the same `am run --board` command again. Done milestones drop out, and each other open milestone starts again as a relaunch would (see [Relaunching resumes](#relaunching-resumes)), or
- continue one `stopped` or `escalated` milestone on its own with `am resume <run-id>`, using that entry's `run_id`.

A `blocked` milestone has no run to resume. It starts on a later `am run --board`, once its blockers are done.

Which base a milestone resumes from. A rerun of `am run --board` computes each milestone's base again from the current board and the current local branches. A blocker that finished `done` still stacks its dependents on its `<prefix>-integrate` while that branch is local and the blocker is not marked `merged`. Once you land it and mark it `merged`, they start from `--base-branch`. `am resume <run-id>` keeps the `base_branch` that run recorded and does not compute it again, so a stacked milestone does not move to a new base (see [Relaunching resumes](#relaunching-resumes)).

#### What a clean run leaves behind

Each story starts as soon as its blockers have finished clean, up to `--max-concurrent` stories run at once, and each story's subtasks run in order. Before the first subtask the run
does `git fetch origin` once (only when
a remote named `origin` exists) and `git worktree prune` once. Each finished
subtask is `done` on the board, and the rollup moves its story and the
milestone with it.

A clean run exits 0, and `data` holds:

- `done`: `true`, reported only once [Integrate](#integrate) has merged every tip and passed its final check.
- `run_id`: the run, for `am status <run_id>` and `am logs`.
- `levels`: the stories this run had work for, as `{"level", "stories"}` with
  story ids, grouped into waves the way `--dry-run` groups them. The grouping is for reading the report only: no story waited for the rest of its wave.
- `completed`: the subtask ids finished in this run, in order.
- `tips`: `{"story", "tip"}` for every story in the milestone that has
  subtasks, naming the branch its stack ends on. These are the branches
  Integrate merged.
- `warnings`: board writes that failed but did not stop the run, as text.
- `integrated`: `{"branch", "worktree", "merged", "resolved"}`. `branch` is `<prefix>-integrate` and `worktree` is its worktree. `merged` lists, in merge order, the story ids whose tip is in `branch`, including tips an earlier run already merged. `resolved` lists the story ids whose conflict a resolver fixed in this run.
- `bases`: `{"story", "branch", "blockers"}` for every merged base this run built, only when there is one. See [Multiple blockers](#multiple-blockers).
- `resumed`: `true`, only on a run continued by `am resume`, on every report shape. `completed` then lists only what finished in that invocation.

The tips are merged into `<prefix>-integrate` and nowhere else. The story branches stay local and stacked, the base branch does not move, and nothing is pushed. Merging the integration branch into the base branch is left to you (see [Integrate](#integrate)).

#### Integrate

After every story has finished, the run merges every story's tip into one local branch, `<prefix>-integrate`, and checks the result once. This step is Integrate. It runs only when every story finished clean: an escalation or a stop ends the run before it. An `am pause` or `am cancel` also keeps Integrate from running in that invocation, and Integrate itself cannot be paused or canceled: once the lanes have ended, the run no longer accepts either (see [Pausing and cancelling a run](#pausing-and-cancelling-a-run)). It also runs when there was nothing left to drive, so every relaunch of the milestone runs it again.

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

`--max-concurrent N` bounds how many stories run at once, across the whole milestone. It defaults to 4, and `--max-concurrent 1` runs one story at a time. The run records the value in its config as `max_concurrent_stories`.

What runs together:

- A story starts as soon as every story it is blocked by inside the milestone has finished clean. It does not wait for the rest of its level: with A blocking C and an unrelated B still running, C starts the moment A is done. Levels, or waves, remain only as a way to read the plan, in `--dry-run` and in the report's `levels`.
- A story that has started takes one of the `--max-concurrent` slots, and waits for one if all are taken. A story still waiting for its blockers holds no slot.
- Never two subtasks of one story. A story's subtasks run in order, each stacked on the branch before it.
- A story with two or more blockers first builds a merged base from their tips, after they have all finished clean. See [Multiple blockers](#multiple-blockers).

How a run stops. The first escalation in any lane, an exception raised inside a lane, or a merged base that fails, sets the run's stop. Every other lane checks the stop before each subtask and before it starts its next phase. A phase already running is never interrupted, so a stop waits for the running phase to finish: a lane in the middle of a long `implement` finishes it and then parks. The subtask that lane was on is recorded `stopped`, and so is its story. A lane that is between two subtasks, or that only gets its slot after the stop, ends `stopped` too, without driving anything more. A story whose blocker escalated or stopped never starts, and stays `pending`. Integrate does not run. `am pause` and `am cancel`, run from another terminal, set the same stop without anything failing; see [Pausing and cancelling a run](#pausing-and-cancelling-a-run).

`stopped` is not `escalated`:

- `escalated` is a failure. A gate gave up (for example `review`), or the lane
  raised an exception. Something needs fixing before you go on.
- `stopped` is a clean park between two phases. Nothing failed, and the work
  done so far is kept.

To continue, fix the escalation, then either relaunch the same `am run --milestone` command, which starts a new run, or run `am resume <run-id>`, which continues this run under the same run id (see [Relaunching resumes](#relaunching-resumes)). Either way the stopped subtask picks up where it parked, and every card already `done` on the board is skipped. After an `am pause` there is nothing to fix: run `am resume <run-id>`. A canceled run cannot be resumed, only relaunched (see [Pausing and cancelling a run](#pausing-and-cancelling-a-run)).

#### Several am processes

Several `am` processes may run on one repository at once, from different terminals, as long as their runs drive different cards and different branches. Each run claims, at its start, every card and branch it may drive, and holds those claims for as long as it holds its lease. A second process that needs any of them is refused before it writes anything. Nothing is queued: a refused command does not wait, so run it again once the other run has finished, or pause that run first.

What may run beside a live run:

| Beside a live … | `am run --milestone M2` | `am run --card X` | `am resume R` | `am status` / `am runs` / `am logs` / `am run --dry-run` |
|---|---|---|---|---|
| milestone run of `M1` | allowed when `M2` is not `M1` and the two `--branch-prefix` values differ | allowed unless `X` is a remaining subtask of `M1` | allowed unless `R` is that run or claims a key it holds | always |
| `--card` run of `Y` | allowed unless `Y` is a remaining subtask of `M2` | allowed when `X` is not `Y` | allowed unless `R` is that run or claims `Y` | always |

A claim is a key, `card:<card id>` or `branch:<branch name>`:

| Command | Claims |
|---|---|
| `am run --card X` | `card:X` |
| `am resume R` of a `--card` run | `card:<its one resumable subtask>` |
| `am run --milestone M` | `card:M`, `card:<id>` of every remaining subtask, and `branch:<prefix>-integrate` |
| `am resume R` of a milestone run | the same set, worked out again from the board with the run's recorded `--branch-prefix` |
| `am run --story S` | `card:<milestone of S>`, `card:S`, and `card:<id>` and `branch:<branch>` of every remaining subtask of `S`; no integration branch |
| `am resume R` of a story run | the same set, worked out again from the board for the run's recorded story and `--branch-prefix` |

A subtask already `done` on the board, and every subtask of a closed story, adds no key. `card:M` keeps two runs of one milestone apart whatever their prefixes, the subtask keys keep a `--card` run and a milestone run apart on a shared card in either order, and `branch:<prefix>-integrate` keeps two milestones with one `--branch-prefix` apart. A claim lives only as long as its run's lease: when the process dies, its claims die with it, and a later run takes them over.

Refusals. Each one prints `{"ok": false, "error": {"type", "message"}}`, exits 3, and comes before any write. A refused `am run --card`, `am run --milestone` or `am run --story` leaves no run directory and does no `git fetch` and no `git worktree prune`.

- `RunIsLiveError`: `am resume` of a run whose lease another process holds and is live, and the loser when two `am resume` race to take over the same dead run (exactly one wins). The message reads ``run <run-id> is still running in pid <pid> on <host> (heartbeat <n>s ago); wait for it to exit, or `am status <run-id>` ``.
- `ClaimedError`: a card or a branch this run needs is claimed by another run whose lease is live. Both kinds read the same way, with the key's kind and name: ``card <card id> is being driven by run <run-id> (pid <pid> on <host>, heartbeat <n>s ago); wait for it, or `am pause <run-id>` ``, or ``branch <prefix>-integrate is being driven by run <run-id> (…); wait for it, or `am pause <run-id>` ``. The JSON envelope has only `type` and `message`, and the message names both the key and the holding run; the `ClaimedError` exception itself also carries them as its `key` and `run_id` attributes.

A branch refusal means another milestone run uses the same `--branch-prefix`; picking another prefix avoids it.

Readers always work and take nothing. `am status`, `am runs`, `am logs` and `am run --dry-run` take no lease, no claim and no lock, and never write, so they work while any number of runs are going. Run against a repository `am` has never run in, they create nothing: no projection database, no `projects/` directory, no lock file. `am status <run-id>` shows the keys the run's live lease holds in `control.claims`.

`took_over`. `am resume` of a run whose process is dead takes its lease over. A lease is dead when its pid no longer exists on the same host, or when its heartbeat is more than 30 seconds old; from another host, the heartbeat is the only test. The resumed report then has `"took_over": {"pid", "host", "heartbeat_at"}`, naming the dead holder. On a milestone run it is on every report shape.

`LeaseLostError`. A process that was stuck rather than dead (stopped with SIGSTOP, or on a laptop that was suspended) may wake after another process has taken its run over. Every write of a run checks that this process still holds the lease, so the stuck process stops at its next store write: it writes nothing, not even the journal line, its lanes are cancelled as on a kill, and it exits 3 with `type: "LeaseLostError"` and the message `this process lost the lease of run '<run-id>': pid <pid> on <host> holds it now`. The new owner's rows are untouched. What this does not cover: a `git` or `brd` call the stuck process had already started finishes on its own. Such a call is bounded by one phase.

Locks. Board writes (a card status and its rollup) and git worktree operations (`git worktree add`, and a run's starting `git fetch` and `git worktree prune`) are serialised across processes by two lock files, `<data dir>/projects/<digest>.board.lock` and `<data dir>/projects/<digest>.git.lock`. `<data dir>` is `$XDG_DATA_HOME/agent-manager`, or `~/.local/share/agent-manager`, and `<digest>` is the same per-repository digest as the project database `<digest>.db` beside them, so the lock files are never inside the repository or a worktree. A process waits at most 600 seconds for one. Past that, the wait fails with `LockTimeoutError` and the message `timed out after 600.0s waiting for the lock <path>`. Inside a phase, the phase fails and the subtask escalates with that as its `detail`, prefixed `LockTimeoutError: `; run `am resume <run-id>` once the other process has let go. While a run is starting, the command instead exits 3 with `type: "LockTimeoutError"`. A holder that is killed, even with SIGKILL, releases its lock at once.

Limits, stated plainly:

- **Your test suite runs side by side.** Each lane's `verify` phase runs your
  `--verify` commands in its own worktree while other lanes do the same. Tests
  that use a fixed port, a shared file or a shared database will collide. Run
  such a repository with `--max-concurrent 1`.
- **One environment per lane.** `uv run pytest` builds
  a `.venv` in each worktree, which takes time and disk once per lane.
- **Machine load.** N lanes means up to N `claude -p` processes at once, and
  nothing rate-limits them.
- **`--max-concurrent` is per process.** Two `am` processes with `--max-concurrent 4` each can run 8 lanes, and 8 `claude -p` processes, at once. Nothing caps lanes across processes.
- **One data directory per machine.** Leases, claims and lock files live under the data directory. Two processes that see different data directories, for example through a different `XDG_DATA_HOME`, do not see each other's runs and are not kept apart.
- **Not over NFS.** The locks are `flock`s, which are not reliable on a network filesystem. Keep the data directory on a local disk.
- **POSIX only.** The locks use `fcntl`, so `am` does not run on Windows.
- **A repository is known by its resolved path.** A linked worktree of the repository given as `--repo-dir` is a different project, with its own database, leases and locks, so a run there is not kept apart from a run on the main checkout.

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

The first escalation stops the run: the other lanes park at their next phase boundary, no new story starts, and Integrate does not run (see [Parallel runs](#parallel-runs)). The run exits 1, and `data` holds `escalated` (`true`), `run_id`, `level`, `story`, `subtask`, `failed_phase`, `detail` and `warnings`. These top-level fields describe the first escalation. `level` is the story's wave, the level `--dry-run` lists it under; nothing waited on it.
`failed_phase` is the phase that gave up (for example `review`) and `detail`
says why. When the subtask's driver raised instead, `failed_phase` is `null`
and `detail` is `<ExceptionType>: <message>`. The escalated subtask, its story
and the run are recorded `escalated`, and `am status <run_id>` shows the whole
plan. A coder that reports `blocked` ends its subtask escalated at `implement`,
and review never runs.

A merged base that fails (see [Multiple blockers](#multiple-blockers)) escalates with `failed_phase` `"base"` and `subtask` `null`, because no subtask of the story ran. `detail` says why and names the base branch and its worktree. The story is recorded `escalated`. When that story has no subtasks of its own, `level` is `null` too. A merged base that raised an unexpected error instead has `failed_phase` `null` and `detail` `<ExceptionType>: <message>`.

When a critic, `validate_spec` or `validate_plan`, reports blockers, the subtask gets one revision: `spec` (or `plan`) runs again with the critic's reason as feedback, and then the critic runs again. A second block escalates at that critic's phase, so `failed_phase` is `validate_spec` or `validate_plan`. `review` has no revision loop. The report's shape is the same either way.

Three more keys appear only when they are not empty:

- `also_escalated`: a list of
  {"level", "story", "subtask", "failed_phase", "detail"}, one for each other
  lane that failed before it saw the stop.
- `stopped`: a list of {"story", "subtask", "before_phase"}, one for each lane
  the stop parked. `before_phase` is the phase it would have run next, or `null` for a lane that stopped before starting its subtask. `subtask` is `null` for a lane whose merged base's resolver was parked. A
  stopped subtask and its story are recorded `stopped`, not `escalated`.
- `bases`: the merged bases built before the run stopped, as on a clean run.

When an `am pause` was requested and a lane then escalated, the escalation wins and this report gains `control` (`"pause"`); it still exits 1. A cancel wins over an escalation instead, and gives the canceled report (see [Pausing and cancelling a run](#pausing-and-cancelling-a-run)).

An escalation at [Integrate](#integrate) has its own shape. The run exits 1, and `data` holds `escalated` (`true`), `phase` (`"integrate"`), `story`, `files`, `detail`, `run_id` and `warnings`. There is no `integrated` key.

- `story` is the story whose tip was being merged, or `null` when the final check of the integrated branch failed. When a merge was already in progress, it is the first story in the merge order, not necessarily the one whose merge is stuck.
- `files` lists the files that conflicted when that story's tip was merged, as they stood before the resolver ran. It is empty when a merge was already in progress in the integration worktree, and when the final check failed.
- `detail` says what went wrong and names the integration worktree.

The run is recorded `escalated`. The integration branch and its worktree are left exactly as Integrate left them, a merge still in progress included, so you can finish it there. See [Integrate](#integrate) for what to do next.

When the run cannot start at all (an unknown or ambiguous milestone, a blocker
cycle, a board error), it prints `{"ok": false, "error": {...}}` and exits 3.

#### Relaunching resumes

To go on after an escalation, a stopped lane, a paused or canceled run, or a killed run, fix the cause
and run the same `am run --milestone` command again. It starts a new run that
skips every card already `done` on the board. A subtask that was stopped or
killed part way picks up in its existing worktree and does not redo a plan
that already passed. Relaunching a finished milestone drives no subtask but still runs [Integrate](#integrate). With every tip already merged, it merges nothing and dispatches no agent, runs the final check again, and reports `done` with an empty `completed` and an `integrated` whose `resolved` is empty. Relaunching after an Integrate escalation runs Integrate again, so commit your fix in the integration worktree first. A relaunch after an `am cancel` or an `am reset` ignores the canceled run's checkpoints, so a subtask that run left parked starts again from its first phase.

`am resume <run-id>` on a milestone run continues that milestone under the same run id, instead of starting a new run. It finds the milestone from the run id, reads the board again and derives the plan exactly as a relaunch does (no story or milestone state is saved), and reuses the `branch_prefix`, `base_branch` and `max_concurrent_stories` the run recorded. Attempts left recorded `started` with no terminal event are marked `harness_error`, every open subtask recorded `stopped`, `escalated` or `started` is recorded `started` again, and the run goes on as a fresh one would, with the same scheduling and stop. Every open subtask with a checkpoint in this run continues from it; an escalated subtask continues at the phase that failed. A parked merged-base resolver continues from its own `base-<story id>` checkpoint, merged bases are built again (a tip already merged is skipped), and Integrate runs when every story finished clean. The run's recorded suite is restored, and it is what every subtask with no checkpoint, every merged base and Integrate run. A `--verify` passed now replaces it and is recorded in its place, and when it differs `data.warnings` gains `verification: replaced in run record: [...]`, naming the suite it replaced; `--allow-no-verification` can only add the opt-out. The report has the shape of a fresh run's, plus `resumed: true`, and `completed` lists only what finished in this invocation. It exits 0 when the milestone finished, was paused or was canceled, and 1 when it escalated again.

A story run relaunches the same way: running the same `am run --story` command again starts a new run of that story that skips every subtask already `done` on the board. `am resume <run-id>` of a story run continues that story alone under the same run id, found from the run's recorded `config.story_id`, with its plan, root and claims worked out again from the board as a fresh `am run --story` would. It refuses, with exit code 3 and nothing written, when the story is no longer a story of the recorded milestone (`NotResumableError`), or when one of its blockers has re-opened (`StoryBlockedError`). A story run never runs Integrate, on a resume either.

`am resume` restores the run's recorded isolation mode as well (see [Isolating agents with `--isolation`](#isolating-agents-with---isolation)). Its `--isolation` accepts only `none`; any other value is a usage error (exit 2). Omitted, a run recorded `bwrap` or `unshare` is probed again and refused with `IsolationUnavailableError` (exit code 3, nothing written) when this host can no longer start that mode, so a run recorded isolated never silently resumes un-isolated. A run recorded un-isolated (`direct`) stays un-isolated with no probe and keeps its recorded warning, if it has one, which `data.warnings` shows again. `--isolation none` runs the rest of the run un-isolated on purpose: nothing is probed, there is no warning, and the run record says so (`config.launcher` is `direct`, `config.isolation_warning` is `null`). The mode is decided after the canceled and live-lease refusals and before anything is written, and a carried warning comes in `data.warnings` before any `verification: replaced in run record: [...]` entry.

A milestone resume refuses before anything is written and before git is fetched, with `{"ok": false, "error": {...}}` and exit code 3, when:

- the run is `done`. Start new work with `am run --milestone`.
- the run was canceled (`NotResumableError`). Start new work with `am run --milestone`.
- the run is still live: another process holds its lease and its heartbeat is fresh (`RunIsLiveError`). Wait for that process to exit, or check `am status <run-id>`.
- any open subtask, or any open `base-<story id>` resolver, has a checkpoint saved under a workflow that has changed since (its digest no longer matches). One stale checkpoint refuses the whole resume, and nothing is written. Relaunch with `am run --milestone` instead: a relaunch starts such a card again from its first phase rather than refusing.
- the run id's milestone is not on the board, or more than one root card has its short id, or the stories now have a blocker cycle.
- the run was recorded `bwrap` or `unshare` and this host can no longer start that mode (`IsolationUnavailableError`). Pass `--isolation none` to resume it un-isolated on purpose (see [Isolating agents with `--isolation`](#isolating-agents-with---isolation)).

On a `task` run (`am run --card`), `am resume <run-id>` continues one stopped (parked) or killed subtask from its newest checkpoint. It no longer refuses a stopped subtask. A checkpoint is saved before every phase runs, so the walk goes on at the interrupted phase, which runs again from its start, and nothing before that phase re-runs. A phase that finished just before the process was killed, before the next checkpoint was saved, depends on its kind: an agent phase is adopted and not dispatched again, and a step runs again (see [Resuming: what runs again](#resuming-what-runs-again)). Attempts left recorded `started` with no terminal event are marked `harness_error` first. `data` names the phase the walk continued at as `resumed_from` (`null` when the checkpoint was declined and the walk started over, see [Resuming: what runs again](#resuming-what-runs-again)) and lists the marked attempts as `discarded_attempts`. A resumed walk that ends `done`, `stopped` or `canceled` exits 0, and one that escalates exits 1 (unless a cancel was requested, which wins).

A `task` run's resume refuses before anything runs, with `{"ok": false, "error": {...}}` and exit code 3, when:

- the workflow changed since the checkpoint was saved (its digest no longer matches). Start a fresh `am run --card`.
- the subtask has no checkpoint (the run died before its first turn, or it predates checkpoints), its newest checkpoint is `done`, or its newest checkpoint was left by a phase escalation. An escalated subtask of a `task` run is never resumed.
- the run has no subtask recorded `started` or `stopped`, or more than one of them.
- the run was canceled (`NotResumableError`). Start a fresh `am run --card`.
- the run is still live: another process holds its lease and its heartbeat is fresh (`RunIsLiveError`). Wait for that process to exit, or check `am status <run-id>`.
- the run was recorded `bwrap` or `unshare` and this host can no longer start that mode (`IsolationUnavailableError`). Pass `--isolation none` to resume it un-isolated on purpose (see [Isolating agents with `--isolation`](#isolating-agents-with---isolation)).

#### Pausing and cancelling a run

From another terminal, while a run is going, ask it to park (`pause`) or to stop for good (`cancel`):

```bash
am pause 20260930T101500Z-bdc5838b
am cancel 20260930T101500Z-bdc5838b
```

Both take `--repo-dir` (default `.`, the repository the run belongs to) and `--pretty`, and both work on a `--milestone` run and on a `--card` run. There is no `--wait`: the command records the request and returns at once. The run's own report, or `am status <run-id>`, shows when it has landed.

How the request reaches the run: it is a row in the repository's SQLite projection, the same database `am status` reads. There is no signal, socket or fifo. While a run is going, its process holds a lease on it, a row whose heartbeat it moves every 5 seconds, and it looks for new requests about once a second. A lease whose heartbeat is older than 30 seconds, or whose pid no longer exists on the same host, is dead, and a run with a dead lease cannot be asked anything.

A recorded request exits 0 and prints:

```json
{"ok": true, "data": {"run_id": "20260930T101500Z-bdc5838b", "command": "pause", "effective": "pause", "requested_at": "2026-09-30T10:20:03.412000+00:00", "already_requested": false, "message": "pause requested for run 20260930T101500Z-bdc5838b; it parks at its next phase boundary, and `am resume 20260930T101500Z-bdc5838b` continues it"}}
```

- `command` is what you asked for. `effective` is what the run will do: `cancel` once any cancel is recorded for this life of the run, otherwise `pause`.
- Asking again is a no-op that still exits 0, with `already_requested: true` and the `requested_at` of the earlier request that covers it. A pause is covered by any earlier pause or cancel. A cancel is covered only by an earlier cancel, so a cancel after a pause is recorded and upgrades the pause to a cancel.
- Requests count per life of the run. A request made before an `am resume` does not count for the resumed process, so pausing a resumed run records a new request.

What the run does with it:

- A phase already running is never interrupted. Every lane finishes its in-flight phase and parks before its next one, the same park an escalation's stop uses (see [Parallel runs](#parallel-runs)).
- No new story starts.
- Integrate does not run in this invocation.

A paused milestone run exits 0 (from `am run` and from `am resume` alike), and `data` holds:

- `paused`: `true`.
- `run_id`: the run.
- `stopped`: one `{"story", "subtask", "before_phase"}` per parked lane, the same rows as in an [escalation report](#what-an-escalation-report-contains).
- `completed`: the subtask ids finished in this invocation.
- `pending`: the ids of stories that never started.
- `warnings`: board writes that failed but did not stop the run, as text.
- `resume`: `"am resume <run-id>"`, with the run id filled in.
- `bases` and `resumed`, under the same rules as on every other report shape.

The run is recorded `stopped`. `am resume <run-id>` continues it: each parked subtask goes on from its checkpoint, and no finished phase runs again.

A canceled milestone run exits 0, and `data` holds `canceled` (`true`) and the same keys as a paused one except `resume`. When a lane also escalated, it adds `escalations`, a list of `{"level", "story", "subtask", "failed_phase", "detail"}` with the primary escalation first. The run is recorded `canceled`. Neither shape has an `escalated` key or a top-level `failed_phase`: a pause or a cancel is not a failure. A report written before this version of `am`, `report.json` included, carries `cancelled` (`true`) instead.

Which report you get when more than one thing happened:

1. A cancel always wins, even over an escalation: the canceled report, exit 0.
2. Otherwise an escalation wins: the ordinary [escalation report](#what-an-escalation-report-contains), exit 1, with `control: "pause"` added when a pause had also been requested.
3. Otherwise a pause gives the paused report, exit 0.

`am status <run-id>` always has a `control` key: `{"lease": {"pid", "host", "acquired_at", "heartbeat_at", "accepting", "live"} or null, "requests": [{"command", "requested_at", "handled_at"}], "claims": ["card:<id>", "branch:<name>", ...]}`. `claims` lists the keys the run's lease holds while it is live, and is empty otherwise (see [Several am processes](#several-am-processes)). `requests` lists the requests from every life of the run, in the order they were made, and `handled_at` is `null` until the run has acted on one. `live` is worked out when `am status` reads the lease; it is not stored. `accepting` turns `false` when the run is finishing.

`am status <run-id>` also always has an `integrity` key: `{"checked", "reason", "mismatches"}`. It compares the run's journal, which `am` appends before every write, with the projection `am status` reads. The journal rebuilds only the run's tree (`runs`, `stories`, `subtasks`, `phases`, `attempts`); the six row-only tables (`checkpoints`, `checkpoint_floors`, `run_controls`, `run_leases`, `run_claims`, `board_comments`) have no journal and are the projection's alone, so they are never compared. When no comparison was made, `checked` is `false` and `reason` says why: `"lease is live"` (a running process's writes in flight are not divergence), `"no journal"`, or `"journal unreadable: <error>"`. Otherwise `checked` is `true` and `reason` is `null`. Each entry of `mismatches` is `{"node", "field", "journal", "projection", "kind"}`: `node` is `{"story", "card", "phase", "attempt"}`, all `null` for the run itself; `field` is `"status"` when both sides have the node with different statuses and `null` when only one side has it; `journal` and `projection` are each side's status, `null` on the side that lacks the node. Only statuses and the tree's shape are compared. The check only reports: it writes nothing and never changes the exit code. A `stale` mismatch means the journal is ahead, and a resume or a rebuild moves the projection forward. A `foreign` mismatch means something other than `am` wrote this row.

`am status <run-id>` also always has a `warnings` key: `[]`, or a list holding the run's isolation warning, `isolation: none (bwrap and unshare are unavailable): agents can signal the engine`, when `--isolation auto` found neither `bwrap` nor `unshare` and the run went un-isolated (see [Isolating agents with `--isolation`](#isolating-agents-with---isolation)).

A request is refused, with `{"ok": false, "error": {"type", "message"}}`, exit code 3 and nothing recorded, in this order:

- `UnknownRunError`: the run id is not in the repository's projection. `am runs` lists the ones that are.
- `NotRunningError`: the run is not `started`. The message names its status. A stopped or escalated run wants `am resume`, and a finished one wants nothing.
- `DeadRunError`: the run is recorded `started`, but no process holds its lease, or the lease is dead. Nobody is left to act on a request. `am resume <run-id>` picks the run up, or `am reset <run-id>` closes it.
- `NotAcceptingError`: the run is finishing. A milestone run stops accepting requests once its lanes have ended, and a `--card` run once its walk has ended, so a milestone run in [Integrate](#integrate) cannot be paused or canceled.

Ctrl-C, pause and cancel are not the same:

- **Ctrl-C** kills the running harness processes. `am resume` then runs again every phase that was in flight.
- **Pause** kills nothing. Finished phases stand, and `am resume` continues each parked subtask from its checkpoint.
- **Cancel** parks the same way, but closes the run for good. `am resume` refuses it. A relaunch with `am run --milestone` ignores the canceled run's checkpoints, so a subtask the cancel parked starts again from its first phase. A cancel does not reset board cards: they keep whatever status the run left them in.

`am reset <run-id>` closes a run nobody is driving: one whose process crashed, or one that stopped or escalated and whose worktrees you then tore down by hand. It takes `--repo-dir` (default `.`) and `--pretty`, and records the run `canceled` exactly as a cancel would, so everything this section says about a canceled run applies to it: `am resume` refuses it, and a relaunch with `am run --milestone` starts its subtasks again from their first phase, `worktree`, which recreates a worktree directory that is gone. It writes no git and touches no board card. It exits 0 and prints `{"ok": true, "data": {"run_id", "previous_status", "status": "canceled", "already_canceled", "cards", "message"}}`, plus `took_over` (`{"pid", "host", "heartbeat_at"}`) when a dead process still held the run's lease. Resetting a run that is already canceled writes nothing and reports `already_canceled: true`. `cards` has one `{"card_id", "workflow", "open_in"}` per card the run saved a checkpoint for: `open_in` is `null` when a relaunch starts that card fresh, and names another run when that run holds the card's newest open checkpoint, so a relaunch would still continue the card from it. It refuses, with `{"ok": false, "error": {"type", "message"}}`, exit code 3 and nothing written, an unknown run (`UnknownRunError`), a run a live process still holds (`RunIsLiveError`, which points at `am cancel <run-id>` instead), and a run that finished `done` (`NotResettableError`).

`am resume` refuses, with exit code 3 and before anything is written, a run that was canceled (`NotResumableError`) and a run whose lease is still live in another process (`RunIsLiveError`: wait for that process to exit, or check `am status <run-id>`).

On a `--card` run, a pause parks the walk before its next phase. The report's `status` is `stopped`, it exits 0, and `am resume <run-id>` continues it. A cancel parks it the same way, and the report's `status` is `canceled`, exit 0, even when the walk escalated. In both cases the story and subtask rows keep the walk's own status.

#### Not there yet

- `retry` does not exist.
- `am pause --wait` does not exist, Integrate cannot be paused or canceled, and there is no way to pause a single story: a pause or cancel always applies to the whole run.
- There is no `--no-integrate` option: a milestone run that finishes clean always ends with Integrate. `am run --story` runs one story with no Integrate.

See section 4 of the
[orchestration addendum](docs/superpowers/specs/2026-09-24-orchestration-design.md#4-deferred-to-the-follow-up-milestone-found-now-not-cut-yet),
section 5 of the
[parallel-stories addendum](docs/superpowers/specs/2026-09-24-parallel-stories-design.md#5-deferred),
section 6 of the
[Integrate addendum](docs/superpowers/specs/2026-09-25-integrate-design.md#6-deferred)
and section 10 of the
[supervisor-tree addendum](docs/superpowers/specs/2026-09-25-supervisor-tree-design.md#10-deferred)
for everything deferred.

### Listing runs

`am runs` lists this repository's runs from its projection, newest first. Like `am status`, it takes no lease, no claim and no lock.

```bash
am runs --repo-dir . --pretty
```

`data.runs` is a list with one object per run. Each object has these keys:

- `id`, `workflow` (`milestone` or `task`), `repo_dir`, `base_branch`, `branch_prefix`, `status`, `started_at` (`null` if never recorded).
- `milestone_id`: the full id of the milestone card a milestone run drives. It is `null` on a `--card` run, and on a run recorded by an `am` too old to store it.
- `card_id`: the subtask card an `am run --card` run drives. It is `null` on a milestone run, and on a `--card` run whose subtask has not been recorded yet.
- `story_id`: the story card an `am run --story` run drives. It is `null` on any other run (a milestone, `--card` or `--board` run), and on a run recorded by an `am` too old to store it. A story run's `workflow` is `milestone`, its `card_id` is `null`, and its `milestone_id` is still the story's parent milestone, so a consumer that maps a run to its milestone keeps working.
- `lease`: the process holding the run, or `null` if no process has a lease row for it. When present it is `{live, pid, host, heartbeat_at, accepting}`, the same values `am status <run-id>` shows in `control.lease` (without `acquired_at`). `live` is worked out when you ask: the heartbeat is at most 30 seconds old, and the lease is on another host or its pid is alive here. `heartbeat_at` is an ISO 8601 string. `accepting` is `false` once the run's control window has closed.
- `progress`: how far the run has got, counted from its recorded tree: `{stories: {done, total}, subtasks: {done, total}, current}`. `done` counts only rows whose status is `done`; `failed`, `escalated`, `stopped` and `canceled` rows count toward `total` only. A milestone run that had to resolve a merge conflict also counts its synthetic `Integrate` story and that story's resolver subtasks, so it shows one story more than the milestone has. `current` is `{card, phase, attempt}` for the `started` phase that started most recently (`attempt` is that phase's highest attempt number, `null` before its first attempt), or `null` when no phase is started. It is read from the recorded rows, not from a live process: a run whose process died mid-phase still shows the phase it stopped in, so check `lease.live` to know whether anyone is still working on it. A run with nothing recorded below it shows `0` of `0` at both levels and `current: null`; `progress` itself is never `null`.

New keys are additive: a newer `am` may add keys to these objects, but never removes or renames one. Consumers should ignore any key they do not recognize.

### Watching a run

`am watch` prints a run's journal: the append-only log, one JSON object per line, that every run writes to `<data dir>/runs/<run-id>/journal.jsonl` (`<data dir>` is defined under [Several am processes](#several-am-processes)). It takes no lease, no claim and no lock, so it works beside any number of live runs, and since every run on the machine writes under the same `<data dir>/runs/`, one `am watch --all` sees the runs of every repository at once.

```bash
am watch 20260923T140506Z-19efcddc
am watch --all --since 40
am watch 20260923T140506Z-19efcddc --follow
am watch --all --follow --from-now
```

The shape is `am watch RUN_ID | --all [--since SEQ] [--follow [--from-now]]`:

- Give exactly one of `RUN_ID` and `--all`.
- `--all` reads every run under `<data dir>/runs/`. A run with no journal yet is skipped. A missing data directory, or a different one (for example under another `XDG_DATA_HOME`), gives no events, not an error.
- `--since SEQ` keeps only the lines whose `seq` is greater than `SEQ`. It filters each run by its own `seq`, so with `--all` the same `SEQ` applies to every run. It defaults to 0, every line.
- `--from-now` needs `--follow` and skips the backlog: the stream prints only lines appended after the command started. It cannot be combined with `--since`, whatever its value.

Without `--follow`, `am watch` prints one envelope and exits 0: `{"ok": true, "data": {"events": [...]}}`. Each event is one [journal line](#the-journal-line), and the list is ordered by `(run_id, seq)`.

These are refused with `{"ok": false, "error": {"type", "message"}}` and exit code 3:

- both `RUN_ID` and `--all`, or neither;
- a `--since` below 0;
- `--from-now` together with `--since`, any value, 0 included;
- `--from-now` without `--follow`;
- a run id with no journal, or one that is not a single directory name (`.`, `..`, or anything with a `/`), as `UnknownRunError`;
- a corrupt journal: a line that is not JSON (other than a final line still being written, see below), or a line that does not have the journal line's shape.

Watching a run id that does not exist creates no run directory.

#### Following with `--follow`

`--follow` turns the output into a stream. The first line is a hello line, the only line that is not a journal line:

```
{"am":"0.1.0","event":"watch","runs_dir":"/home/you/.local/share/agent-manager/runs","schema":2}
```

`am` is the version of `am` printing the stream, and `runs_dir` is the `<data dir>/runs` it reads. After the hello line comes every journal line above `--since` (the backlog), then each line as it is appended, one JSON object per line, until stopped. Each is a bare journal line with no envelope, flushed as soon as it is written. With `--all`, a run that starts after the stream began is picked up. Stream lines are always compact: `--pretty` only indents a refusal's envelope.

With `--from-now`, the hello line comes first as always, then no backlog: only lines appended after the command started. A line that was still being written when the command started is printed once it is complete. A run with no complete line yet when the command started, and with `--all` a run that starts later, is printed from its first line. The hello line is the same, `"schema":2`.

Every refusal listed above, a corrupt journal included, comes as the usual envelope with exit code 3 before any stream line is written. So the first line tells a stream from a refusal: only a refusal has an `"ok"` key, and only a stream starts with `"event": "watch"`.

Ctrl-C, or the reader closing the pipe, ends the stream with exit code 0 and nothing on stderr. A journal that turns corrupt after the stream has started cannot get an envelope, because every line after the hello line must be a journal line: `am watch` prints `am watch: <message>` on stderr and exits 3.

`am watch --follow` checks for new lines about every 250 ms. That interval is internal and is not part of the contract.

#### The journal line

Every event, in the envelope's `events` and on the stream, is one journal line (`JournalLine` in `src/agent_manager/store.py`):

```
{"attempt":null,"card":"<subtask-id>","event":"phase_upsert","payload":{"detail":null,"ended_at":null,"kind":"agent","name":"implement","started_at":"2026-10-02T14:03:11.410000Z","status":"started"},"phase":"implement","run_id":"20261002T140000Z-19efcddc","seq":17,"story":"<story-id>","ts":"2026-10-02T14:03:11.412000Z"}
```

| Field | What it holds |
|---|---|
| `seq` | the line's number in its run's journal, from 1, increasing |
| `ts` | when the line was written, ISO 8601 in UTC |
| `run_id` | the run |
| `event` | which kind of node the line records, one of the five below |
| `story` | the story id; `null` on a `run_upsert` |
| `card` | the subtask id on subtask, phase and attempt lines; otherwise `null` |
| `phase` | the phase name on phase and attempt lines; otherwise `null` |
| `attempt` | the attempt number on an attempt line; otherwise `null` |
| `payload` | the node itself, as the run recorded it, without its children |

Every line records one node of the run's tree. A status change is the same node recorded again with its new status; there is no separate transition event.

| `event` | Covers |
|---|---|
| `run_upsert` | the run starting and finishing: `started`, then `done`, `escalated`, `stopped` or `canceled` |
| `story_upsert` | a story's own progress: `pending`, `started`, `done`, `stopped`, `escalated` |
| `subtask_upsert` | a subtask's status: `pending`, `started`, `done`, `stopped`, `escalated` (recorded `started` again on a resume) |
| `phase_upsert` | a phase of a subtask: `started`, `done` or `failed`, with `detail` saying why a phase failed |
| `attempt_upsert` | one dispatch of a phase: `started`, then `ok`, `schema_invalid`, `gate_failed` or `harness_error`, with its `exit_code` and `duration` |

There is no separate "run finished" or "escalation" event. A run has finished when a `run_upsert` line's `payload.status` is `done`, `escalated`, `stopped` or `canceled`, and it escalated when that status is `escalated`.

Journals written before this version of `am` record a canceled run as `cancelled`, and those lines are never rewritten. Read both spellings as the same status. `am status` and `am runs` report such a run as `canceled`.

#### Reading the stream safely

The journal line is a public contract, version 1. A consumer that follows these rules keeps working across `am` versions:

- Cursor by `(run_id, seq)`, never by time or line count. To pick up where you left off, pass the highest `seq` you have seen as `--since`. The cursor survives a lease takeover: the process that takes a run over keeps appending to the same journal at a higher `seq`.
- Ignore any `event` value, and any `payload` key, you do not recognize. A newer `am` may write either.
- An unterminated final line is a write in flight, not a malformed file. `am watch` skips it, and emits it once it is complete.
- Know the synthetic ids. Story `"integrate"` is [Integrate](#integrate)'s resolver, story `"bases"` holds the [merged-base](#multiple-blockers) resolvers, and under it each resolver is subtask `"base-<story id>"`. A run's `repo_dir` and `milestone_id` (`null` on a `--card` run, the milestone's id on a `--milestone` or `--board` run, the parent milestone's id on a `--story` run) are in the `payload` of its first line, a `run_upsert`. So is `payload.config.story_id`, the story's id, which is `null` unless the run is an `am run --story` run. A `--board` run has no journal of its own: each milestone it starts is a run with its own journal, and the synthetic ids can recur across them, so key them by `(run_id, story)` (see [Running every open milestone with `--board`](#running-every-open-milestone-with---board)).
- The hello line's `schema` field is where a schema bump is signaled. It is `2` today. Schema 1 became 2 when `am` started writing a canceled run's status as `canceled` instead of `cancelled`; nothing else changed. Lines are replayed as stored, so a schema-2 stream still carries `cancelled` for a run canceled by an older `am`: accept both, whatever the schema.

### Reading an attempt's output

`am logs` prints what one attempt of one phase of one subtask was given and wrote. Like `am status` and `am runs`, it reads the projection and takes no lease, no claim and no lock, so it works beside any number of live runs.

```bash
am logs 20261002T140000Z-19efcddc <subtask-id> --phase implement
am logs 20261002T140000Z-19efcddc <subtask-id> --phase implement --follow
am logs 20261002T140000Z-19efcddc <subtask-id> --phase implement --follow --since-offset 20
```

The shape is `am logs RUN_ID CARD [--phase P] [--attempt N] [--follow] [--since-offset BYTES] [--repo-dir DIR]`:

- `--phase` defaults to the last phase with attempts, and `--attempt` to that phase's highest recorded attempt.
- Without `--follow`, `am logs` prints one envelope with the attempt's prompt, result and captured output, and exits 0.

#### Following an attempt with `--follow`

`--follow` turns the output into a stream of the attempt's stdout file, one JSON object per line:

```
{"event":"logs","offset":0,"path":"/home/you/.local/share/agent-manager/runs/20261002T140000Z-19efcddc/<subtask-id>/implement.1/stdout.log","schema":1}
{"offset":0,"text":"Reading the plan...\n"}
{"offset":20,"text":"Running uv run pytest\n"}
{"event":"end","status":"ok"}
```

- The first line is the hello line. `path` is the file being followed: the stdout file an agent attempt recorded (its stderr is merged into it), or `<phase>.N/stdout.log` for a deterministic phase such as `verify`. `offset` is the byte the stream starts at: 0, or the `--since-offset` you passed.
- Then come the file's bytes as `{"offset", "text"}` lines: what is already in the file first, then each append, flushed as soon as it is written. Chunks are contiguous, and each chunk's `offset` is the byte position of its first byte in the file. `text` is decoded as UTF-8. A character split across two reads is held back and arrives whole in the next chunk. A byte that is not valid UTF-8 comes out as U+FFFD.
- A file that is not written yet gives no chunk. The stream waits until it appears.
- Once the attempt has a terminal status and the file has stopped growing, the stream ends. A partial character still held back at the very end of the file comes out first, as one last chunk decoded with U+FFFD. Then the last line is `{"event":"end","status":S}`, and the exit code is 0. `S` is the attempt's status: `ok`, `schema_invalid`, `gate_failed` or `harness_error`, the vocabulary of `attempt_upsert` in the [journal](#the-journal-line). A deterministic phase has no attempt status of its own, so `S` is `ok` when the attempt is the phase's latest and the phase is `done`, and `gate_failed` when a later attempt superseded it or the phase failed, escalated, stopped or was canceled.

To pick up where you left off, pass `--since-offset B`, the way `--since` resumes `am watch`. `B` is the last chunk's `offset` plus the UTF-8 byte length of its `text`. That sum is exact for valid UTF-8. A U+FFFD stands for invalid bytes of the file but is 3 bytes in `text`, so after invalid bytes the sum can overcount. The next chunk's `offset` is always exact, so prefer it when you have one.

Ctrl-C, or the reader closing the pipe, ends the stream with exit code 0 and nothing on stderr. An error after the hello line cannot get an envelope, for example the run, card, phase or attempt no longer being in the projection. `am logs` then prints `am logs: <message>` on stderr and exits 3, as `am watch` does.

These are refused with the usual `{"ok": false, "error": {"type", "message"}}` envelope and exit code 3, before any stream line is written:

- an unknown run, card, phase or attempt, as for the one-shot;
- a `--since-offset` below 0;
- `--since-offset` without `--follow`, whatever its value, 0 included;
- an agent attempt that recorded no stdout path, since there is no file to follow.

So the first line tells a stream from a refusal: only a refusal has an `"ok"` key, and only a stream starts with `"event":"logs"`. Stream lines are always compact: `--pretty` only indents a refusal's envelope.

`am logs --follow` checks the file about every 250 ms. That interval is internal and is not part of the contract.

The hello line's `schema` is the stream's own version, `1` today. It is independent of the journal line's version and of the `am watch` hello line's `schema`, and a change to the chunk or end lines is signaled there.

New keys are additive: a newer `am` may add keys to the hello, chunk and end lines, but never removes or renames one. Consumers should ignore any key they do not recognize.

## Resuming: what runs again

`am resume <run-id>`, and a relaunch that continues an open checkpoint from an earlier run, go on at the turn the newest checkpoint saved, which is before the interrupted phase ran. Whether that phase runs again depends on its kind and on what was recorded before the process stopped:

| Phase kind, and what was recorded before the stop | On resume |
|---|---|
| Agent phase with an `ok` attempt recorded for this turn (it finished, but the next checkpoint was not saved) | Adopted, not dispatched again. Its result file is read and validated again, and the phase's gates run again against the resumed context. If either fails, the result is not reused and the phase is dispatched again. |
| Agent phase with no `ok` attempt, or only an attempt left `started` (marked `harness_error` on resume) | Dispatched again. An attempt that never finished is never adopted, even when its result file looks valid. |
| Step (`worktree`, `docs_commit`, `verify`, ...) | May run again. Steps are at-least-once, and every step must be idempotent. |

An adoption shows only as one line in the report's `warnings`:

```
phase 'implement' was not dispatched again: attempt 1 of run <run-id> had already succeeded (result reused)
```

A recorded result that no longer holds up is dispatched again, with one warning line `phase '<name>': attempt <n> of run <run-id> was not reused (<why>); dispatching again`. The envelope shape and the exit codes are the same as for any other resume.

Exactly-once covers am's dispatch of an agent phase, not what the harness did. The harness's own effects are never transactional: commits, files written in the worktree, or anything else an agent did before the kill stay as they are, whether the phase is then adopted or dispatched again. A phase that is dispatched again finds that work already in its worktree; `implement`, for example, resumes from git and the `Plan-Hash` trailers. The coder role is explicitly told never to rewrite, amend, squash or delete a commit; the `docs_commit` step does exactly that (rebuilding and moving the branch ref) to backfill a missing or stale `Plan-Hash` trailer, which is a deliberate asymmetry -- that rewrite is the engine's own, narrow and idempotent, done before an agent is ever dispatched into the worktree, not a license the coder shares.

`docs_commit` commits the spec and the plan in one commit with the `Plan-Hash` trailer when the repository tracks them. When the repository git-ignores both (for example `docs/superpowers/` in its `.gitignore`), it neither adds nor commits them: they stay in the worktree as ignored files, the plan is still hashed, and the backfill still stamps the trailer on every commit the branch already has. When git ignores only one of the two, the subtask escalates at `docs_commit`.

Before a checkpoint is continued, the subtask's worktree is checked. A worktree deleted outside `am` (an `rm -rf`, a `git worktree remove`) is a recovery, never a refusal:

| The subtask's worktree on resume | What happens | Warning | Where the walk goes on |
|---|---|---|---|
| Present (or the subtask has none) | Nothing runs: no git, no warning. | none | At the checkpoint's pending phase. |
| Missing, its branch still exists | The worktree is added again for the branch, which keeps its commits. | one, "added again" | At the checkpoint's pending phase. |
| Missing, and its branch is gone too, or adding it again fails | The checkpoint is not resumed. | one, "was not resumed" | From the subtask's first phase, `worktree`, as a fresh walk. When the branch was gone it is cut again from its base; a real git failure is reported by the `worktree` step as an ordinary escalation at `worktree`. |

The spec and the plan come back with a worktree added again only when they were committed. When the repository git-ignores them, they lived only in the deleted directory, so a walk resumed after the `spec` or `plan` phase finds neither file.

The three warning lines, in `data.warnings` on a `--card` run and in the run's warnings on a milestone run:

```
checkpoint #<seq> of run <run-id>: worktree <path> was missing and was added again for branch '<branch>'; resuming at '<phase>'
checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and branch '<branch>' no longer exists); starting from the first phase
checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and could not be added again: GitError: <message>); starting from the first phase
```

After a decline, `data.resumed_from` is `null` and a milestone run's `done` comment for that subtask carries no `(resumed at <phase>)` line: both report where the walk actually went on, not the checkpoint it was handed. The declined checkpoint row is not deleted or rewritten; the fresh walk's first checkpoint, saved at a higher seq, supersedes it.

## What the board records

`am` leaves a short comment on a `brd` card when something final happens to it: a subtask finishes, escalates or is canceled, a merged base fails, or a run ends. Code writes every comment, after the outcome is already recorded in the run's store. The board is a log for people to read; `am` never reads it back.

| Event | Card that gets the comment |
|---|---|
| A subtask is done | the subtask |
| A subtask escalates | the subtask |
| A cancel parks a subtask partway through a milestone run | that subtask |
| A merged base fails | the story the base roots |
| A milestone run ends: done, escalated, paused or canceled | the milestone card |
| A `--card` run ends | the subtask only, with the done, escalated or canceled comment above. There is no milestone card. |

Nothing else is posted: no comment when a run or phase starts, none for retries, revision loops or gate warnings, none for a subtask parked because another card escalated (the run-end comment lists it), and none for a pause on a subtask card, because a paused subtask is resumed, not closed. A lane stopped while its merged base was still being built names no subtask and gets no cancel comment. `am resume` of a `--card` run composes no outcome comment of its own: it only sends comments an earlier life left unsent.

### What each comment looks like

Every comment is plain text. The first line is `am · <outcome> · run <run-id>`, commands are in backticks, real card ids are `[[id]]` backlinks, and the last line is `am-key: <key>`.

A subtask that is done lists only facts. Each line is there only when the run recorded it:

```
am · done · run <run-id>
branch: m12/task-<slug>-<short-id>
commits: 3
Plan-Hash: <plan-hash>
spec: docs/superpowers/specs/<slug>-design.md
plan: docs/superpowers/plans/<slug>.md
verified: `uv run pytest`
review findings fixed: 2
am-key: <run-id>/<subtask-id>/done
```

When a milestone run picked the subtask up from a checkpoint, a second line `(resumed at <phase>)` follows the first. A `--card` run never adds it.

A subtask that escalated:

```
am · escalated · run <run-id>
phase: review
detail: review blockers: 1 unresolved
reason: "tests do not cover the empty list"
next: `am resume <run-id>`
why: `am logs <run-id> <subtask-id> --phase review`
am-key: <run-id>/<subtask-id>/escalated:<lease-token>
```

A subtask a cancel parked. On a `--card` run the relaunch is `am run --card <subtask-id>`:

```
am · canceled · run <run-id>
stopped before: implement
branch: m12/task-<slug>-<short-id>
relaunch: `am run --milestone <milestone-id>`
am-key: <run-id>/<subtask-id>/cancelled
```

A merged base that failed, on the story it roots:

```
am · base failed · run <run-id>
base branch: m12/base-<story-short-id>
detail: the merged base m12/base-<story-short-id> failed its verification in <worktree>: <what failed>
am-key: <run-id>/<story-id>/base-failed
```

The end of a milestone run, on the milestone card. A clean run:

```
am · done · run <run-id>
done: 5 of 5
integrated: m12-integrate
next: `git merge m12-integrate`
am-key: <run-id>/<milestone-id>/run-end:<lease-token>
```

An escalated run names the escalated card and phase and the parked cards instead:

```
am · escalated · run <run-id>
done: 2 of 5
escalated: [[<subtask-id>]] at review
parked: [[<other-subtask-id>]]
next: `am resume <run-id>`
am-key: <run-id>/<milestone-id>/run-end:<lease-token>
```

A paused run's `next:` line is `am resume <run-id>`. A canceled run's is `am run --milestone <milestone-id>`, and so is an [Integrate](#integrate) escalation's, which also adds an `integrate failed at …` line.

### Who writes them

The author is always `am`. Code builds every body from what the run recorded; no agent writes to the board. Agent text gets in only through three failure fields, and only on an escalation, as the `reason:` line:

- `validate_spec` and `validate_plan`: the critic's `reason`;
- `implement`: `blocked_reason`;
- `review`: `unresolved_blockers`, joined with `; `.

That text is in double quotes, and every `[[` in it becomes `[ [`, so it cannot create a backlink. A comment is at most 1,500 characters. When a body would be longer, the agent text is cut first and ends with a marker that names where to read the rest:

```
reason: "xxxx…xxxx" … (truncated; see `am logs <run-id> <subtask-id> --phase implement`)
```

Every other comment's marker says `am status <run-id>`. If cutting the agent text is not enough, or there is none, the field lines are cut instead. The first line, an escalation's `next:` and `why:` lines and the `am-key:` line are never cut.

### The `am-key` line

Every comment's last line is `am-key: <run-id>/<card-id>/<event>`, where the event is `done`, `escalated:<lease-token>`, `cancelled`, `base-failed` or `run-end:<lease-token>`. Before posting, `am` lists the card's comments and skips the post when one already ends with that line. So a crash between posting and recording, a replayed phase, or a resume never posts the same comment twice. Escalations and run ends carry the lease token of the process that ran them, so each life of a resumed milestone run posts its own escalation and its own run end. A subtask's `done` and `cancelled` happen once per run. The `cancelled` event keeps its old spelling while the comment's first line says `canceled`: the key is how `am` recognizes a comment it already posted, so a cancel comment an older `am` queued or posted still matches and is never posted twice.

### When the board is down

Comments are best-effort. Each one is queued in the run's store after the outcome is recorded, then posted with `brd comment add <card> - --author am`, the body on stdin. If `brd` fails or the board lock times out, nothing about the run changes: it does not escalate, park or change status. The run's report gets one warning and the comment stays queued:

```
board comment <key> on card <card-id> not posted (attempt 1 of 3), will retry: <error>
```

Queued comments are sent again right after the next comment on the same card is queued, at the start of every milestone run or `am resume` (every run's queued comments on the milestone's cards), and at the start of `am resume` of a `--card` run (that run's own). After 3 failed attempts a comment is given up, with one last warning:

```
board comment <key> on card <card-id> abandoned after 3 failed attempts: <error>
```

### Nothing is deleted

A card's thread only grows. An escalation comment stays after a later resume finishes the subtask and appends its `done` comment with `(resumed at <phase>)`. `am` never edits, replaces or deletes a comment, and never reads one back to decide anything: the run's store, not the board, is the record.

#### Not there yet

Comments from runs of leave-me-alone's orchestrator, feeding earlier comments into a later run's prompts, deleting or superseding outdated comments, and issues opened on escalation do not exist. See section 7 of the [board-comments addendum](docs/superpowers/specs/2026-09-29-board-comments-design.md#7-deferred) for everything deferred.

## Develop

```bash
uv sync
uv run pytest
```
