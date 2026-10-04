# am interfaces for the Omarchy plugin (S4) — design

Status: proposed. The agent-manager half of a four-part effort; the plugin side is
three specs in `omarchy-project-manager/docs/superpowers/specs/`:
`2026-10-03-am-run-monitor-design.md` (S1), `…-am-run-controls-design.md` (S2),
`…-am-run-dispatch-design.md` (S3).

## Problem

The plugin can monitor, control and start `am` runs using only what `am` offers
today, but five gaps force workarounds that are slower, racier or coupled to
details `am` has not promised:

1. `am runs` lists identity only, so the plugin calls `am status` once per run to
   learn the milestone, liveness and progress.
2. `am run` prints its run id only in its final report, so a launcher cannot
   learn the id of a run it just started.
3. There is no way to follow an attempt's output; `am logs` is a whole-file
   snapshot.
4. `am watch --all --follow` replays every journal line of every run first, so a
   monitor that only wants *new* activity must read and discard the history.
5. `am run --board` exists and is exposed in `--help` but is not in the README, and
   what its run records as `milestone_id` and in the journal is unstated.

(An earlier idea, to add escalation detail to the journal, is **not** needed:
`phase_upsert` already carries `detail` for a failed phase, and `am` already posts
the reason to the card as a brd comment.)

## Goal

Each gap gets a small, documented, tested addition that is backwards compatible
(only new fields, new flags, new documented behaviour), so the plugin can drop its
workarounds without changing its UI. None of these is a prerequisite for S1–S3,
which work against `am` as it is.

## Non-goals

- No `am retry`, per-story pause, or Integrate stop (the README lists these as not
  there; nothing in the plugin needs them).
- No daemon, server or socket. `am` stays a CLI over a journal and a store.
- No change to the lease, claim, control-request or journal-line contracts.
- Cross-repo `am runs` is out of scope (the plugin is scoped to one project).

## Changes

### 1. Richer `am runs`

`RunSummary` (`src/agent_manager/store.py`, `extra="forbid"`) gains optional
fields, all in `data.runs[]`:

```
milestone_id   str | null     null on a --card run (same value as the first run_upsert)
card_id        str | null     the subtask id on a --card run, else null
lease          { live: bool, pid: int | null, host: str | null,
                 heartbeat_at: str | null, accepting: bool } | null
progress       { stories: {done,total}, subtasks: {done,total},
                 current: { card, phase, attempt } | null }
```

`lease` uses the same computation as `am status`'s `control.lease`; `progress` is
counted from the projection rows already in the store. A run with no lease row
has `lease: null`. Documented in the README's `am runs` section and pinned by a
shape test. Old consumers ignore the new keys.

### 2. Run id at start: `am run --detach`

`am run … --detach` performs the whole pre-flight in the foreground (every
refusal today: claims, lease, cycles, ambiguous milestone, missing verification,
exit 3 with the usual envelope), creates the run and takes its lease, then hands
the engine to a detached child (own session, output to
`<data dir>/runs/<run-id>/run.log`, mode 0600) and prints one envelope
`{"ok":true,"data":{"run_id","pid","log","detached":true}}` and exits 0. The
final report is written to `<data dir>/runs/<run-id>/report.json` when the run
ends, which `am status` already summarises; nothing about a non-detached run
changes. `--dry-run` with `--detach` is refused.

The plugin's launcher (S3) uses this and drops its `am runs` polling; a terminal
user gets a safe way to start a run and close the terminal.

### 3. Following output: `am logs … --follow`

`am logs RUN CARD [--phase P] [--attempt N] [--follow] [--since-offset B]`:

- Without `--follow`: unchanged (one envelope, whole file).
- With `--follow`: a hello line `{"event":"logs","schema":1,"path":…,"offset":B}`,
  then one JSON object per chunk `{"offset":B,"text":"…"}` from the attempt's
  stdout, flushed as written, until the attempt reaches a terminal status and the
  file stops growing, then `{"event":"end","status":"ok|…"}`. `--since-offset`
  resumes from a byte offset, like `am watch --since`. Ctrl-C ends with exit 0.
- Every refusal comes as the usual envelope with exit 3 before the first stream
  line, as `am watch` does.

The plugin's output pane (S1) switches from snapshot-and-refresh to this and
loses the "snapshot Ns ago" label.

### 4. `am watch --from-now`

`am watch [RUN | --all] --follow --from-now` skips the backlog: the hello line,
then only lines appended after the command started. It is exclusive with
`--since` (refused with exit 3 when both are given). `--from-now` without
`--follow` is refused.

The plugin's watch helper (S1) drops its timestamp-based backlog filter.

### 5. Document `--board`

README section for `am run --board` (and `--board --dry-run`): what a board run
is, the plan payload shape, `max_concurrent` semantics, the run's `milestone_id`
(`null`, or a documented stand-in), and how its journal represents multiple
milestones (which `story_upsert` ids, whether synthetic ids appear). If the
journal cannot tell the plugin which milestone a story belongs to, add that
field to the `run_upsert`/`story_upsert` payload (a new key, which consumers are
already told to tolerate). Add the same shape tests as for `--milestone`.

## Compatibility and versioning

- Every change adds a field, a flag or documentation. No key is removed or
  renamed, so the journal stays **schema 1** and the watch hello stays `1`.
- New hello lines (`logs`) carry their own `schema`.
- The README's "Reading the stream safely" rules already say to ignore unknown
  keys; add the same sentence to the `runs` and `logs` sections.

## Testing

- Unit: `RunSummary` with and without lease/progress; progress counts on a
  fixture run tree; `--from-now` skips backlog and is refused with `--since`;
  `logs --follow` chunking, offsets, resume, end event, refusals.
- `--detach`: refusals happen before detaching (same envelopes as foreground);
  the run id printed matches `am runs`; the child survives the parent exiting;
  `report.json` appears at the end; `--dry-run --detach` refused.
- Contract tests pin the new JSON shapes next to the existing ones.
- One `e2e_fake` tier test (fake harness, no real agents) that starts a detached
  milestone run, follows its logs and watch stream, pauses it with `am pause`
  and resumes it, asserting the same sequence the plugin relies on.
- README examples are executed or shape-checked by the existing docs test if one
  exists; otherwise add the commands to the contract tests.

## Order and sizing

Each item is independent and ships alone. Suggested order by value to the
plugin: 2 (`--detach`), 1 (`am runs`), 4 (`--from-now`), 3 (`logs --follow`),
then 5 (`--board` docs, which should happen before S3 exposes board dispatch).
Items 1, 4 and 5 are small; 2 and 3 are medium.

## Open questions

- `--detach` versus a simpler `--print-run-id-first` that keeps the process in the
  foreground. This spec chooses `--detach` because it also fixes the "closing the
  terminal kills the run" footgun, but it makes `am` own a detached process model
  it does not have today. Say if you would rather keep `am` foreground-only.
- Is `lease` on `am runs` acceptable cost? It reads one row per run; assumed yes.
