# `am watch` — design

Date: 2026-10-02
Status: approved design, pre-implementation

## 1. Purpose

brd issue 57f644bd (open on this board) asks for a way for Omarchy
widgets/plugins to react to `am`'s state changes — a run starting or
finishing, a subtask's status changing, a gate failing — without polling.
It frames this as an open design question: stdout NDJSON, a file/FIFO/socket,
or a notify hook.

It is not an open design question. `am` already has an event stream: every
state transition in a run is appended as sequence-numbered NDJSON to
`~/.local/share/agent-manager/runs/<run-id>/journal.jsonl`
(`Journal.append`, `store.py:356`), fsynced before the matching SQLite row is
written, as the one thing a replay can trust after a crash. The original
design spec even named the deferred consumer:
`agent-manager watch [<run-id>]  # live tail of the journal`
(`2026-09-23-agent-manager-design.md:402`, still listed deferred at line
428). This spec is "expose what exists, with a stated contract," not a new
subsystem.

## 2. Scope

**In scope.** A new `am watch` command that reads the journal (one run or
every run); a public v1 contract for the journal's line shape, so a reader
outside this codebase can depend on it; the one change needed to the journal
*reader* to make that contract viable going forward (tolerating an event
kind it doesn't recognize).

**Out of scope, deferred.** Journaling the transitions that are today
row-only — a pause/cancel *request* landing, lease liveness, checkpoint
turns (see 3.5); a board-level run record for `am run --board` (accepted
limitation of `2026-10-01-run-board-design.md` §3.7, not solved here);
`--events` on `am run` itself; any socket, FIFO, or notify-hook delivery
mechanism — all rejected below, not merely postponed.

**Never** (unchanged from the rest of `am`). Pushing to a remote, opening
PRs, touching `main`/`master`; nothing about push mechanics changes watching.

## 3. Decisions

### 3.1 What a widget can already see

Every line matches `JournalLine` (`store.py:251`): `{"seq", "ts", "run_id",
"event", "story", "card", "phase", "attempt", "payload"}`, where `event` is
one of five kinds (`EventKind`, `store.py:232`) and `payload` is the
affected row's own dump. A status transition is the same node recorded
again with a new status — there is no separate "transition" event.

| Event kind | Statuses observed | Covers |
|---|---|---|
| `run_upsert` | `started`; terminal `done`/`escalated`/`stopped`/`cancelled` | run started/finished |
| `story_upsert` | `pending`, `started`, `done`, `stopped`, `escalated` | a story's own progress; `story: "integrate"` and `story: "bases"` are the synthetic entries for Integrate and merged-base building |
| `subtask_upsert` | `pending`, `started`, `done`, `stopped`, `escalated` (re-stamped `started` on resume) | task status changes |
| `phase_upsert` | `started`, `done`, `failed`, with `detail` | a phase inside a subtask failing is a gate result |
| `attempt_upsert` | `started`, then `ok`/`schema_invalid`/`gate_failed`/`harness_error`, with cost/token/duration fields | one dispatch's own outcome |

This already answers the issue's wishlist (run started/finished, task
status changes, gate results) without inventing anything. Volume is
trivial: one clean subtask is roughly 50 lines over 30-90 minutes.

### 3.2 Delivery mechanism: tail the journal file, reject the issue's three candidates

**Rejected: NDJSON on `am run`'s own stdout (`--events`).** Nothing writes
to `am run`'s stdout mid-run today — harness output goes to `stdout.log`
(`harness/launcher.py:152`), and `brd`/`git`/verify calls are
`capture_output` throughout. Adding a stream there breaks the one-line
`{"ok": ..., "data": ...}` envelope every other command keeps, and only
reaches whoever happened to spawn that one process — not a widget watching
the whole machine, and not a run already in flight when the widget starts.

**Rejected: a FIFO or unix socket.** The live-control design already made
this call and wrote down why: "SQLite is the only channel: no socket, fifo
or signal handler" (`control.py:10`, decision C1). A FIFO with no reader
blocks the writer — a run must never stall because nothing is tailing it. A
socket needs something to own its lifecycle, and with several `am`
processes allowed per repository (README, "Several am processes") there is
no single process to host an endpoint.

**Rejected (for now): a notify-hook subprocess.** ~50 events per subtask,
times however many lanes are concurrent, is a process-spawn storm if each
one shells out a hook; every hook's own failure/timeout would need the same
best-effort isolation board comments already needed (B8). It is also
redundant with the recommendation below: a hook is
`am watch --follow | while read line; do ...; done` with no further
plumbing, so nothing stops a widget from building its own if it wants one —
`am` does not need to.

**Chosen: `am watch`, reading the journal file(s) directly.** No new write
path — the journal is already the thing a crash-safe replay trusts
(`replay_journal`, `store.py:1789`, used by resume's adoption check). It is
cross-process for free: the README's "several `am` processes" scenario is N
processes each owning one journal file under one shared
`<data dir>/runs/`, so a watcher over that one directory sees every run on
the machine, across every repository, with no per-project SQLite digest to
resolve first (the first line of each file carries `repo_dir` and
`milestone_id`, from the run's own `payload`). A journal a dead process's
lease was taken over from is not a special case: the new owner calls
`reseek` (`store.py:310`) and keeps appending to the same file at a higher
`seq`, so a watcher's `(run_id, seq)` cursor already survives a takeover
without knowing one happened. A torn last line — a write in flight —
already has a defined reading: `Journal.read(ignore_torn_tail=True)`
(`store.py:320`, `_for_reading`, `store.py:288`), built for exactly this
cross-process read.

This also satisfies "without polling" at the point that matters: a file in
a directory is inotify-able. `am watch --follow` can start with a cheap
stat-poll (see 3.6) and move to inotify later without changing its output
contract — the widget never polls either way, because it reads a stream
`am watch` produces, not a file it watches itself.

### 3.3 The public contract is `JournalLine` v1, not a new shape

The journal's own line shape becomes the documented public contract,
instead of inventing a second vocabulary a widget would have to learn on
top of it:

```json
{"seq":17,"ts":"2026-10-02T14:03:11.412000+00:00","run_id":"20261002T140000Z-19efcddc",
 "event":"phase_upsert","story":"<story-id>","card":"<subtask-id>","phase":"implement","attempt":null,
 "payload":{"name":"implement","kind":"agent","status":"started","started_at":"…","ended_at":null,"detail":null}}
```

Consumer contract (this is what "versioning" means here, since the shape
itself is already fixed by `JournalLine`):

- Cursor by `(run_id, seq)`, not by time or line count.
- Ignore any `event` value you don't recognize, and any `payload` key you
  don't recognize. Section 3.4 is what makes that safe to rely on.
- An unterminated final line is a write in flight, not a malformed file —
  this is exactly what `ignore_torn_tail` already encodes.
- Synthetic ids to know about: story `"integrate"`, story `"bases"`,
  subtask `"base-<story id>"` (`bases.py:45`, `integration.py:41`). A
  run's own `repo_dir` and `milestone_id` are in its first
  (`run_upsert`) line.

`am watch --follow` emits one `watch` line first — `{"event": "watch",
"schema": 1, "am": "<version>", "runs_dir": "..."}` — and that is where a
future schema bump is signaled; the file format underneath stays an
implementation detail `am watch` is free to normalize.

No higher-level synthetic events in v1 ("escalation", "run finished"): a
widget derives those in a few lines (`event == "run_upsert" and
payload["status"] in {"done", "escalated", "stopped", "cancelled"}`).
Inventing a second vocabulary for the same facts is exactly the kind of
drift that makes two schemas instead of one.

### 3.4 The one change this requires: tolerant reading

`JournalLine` is `extra="forbid"` and `event` is a closed `Literal`
(`store.py:258`, `232`) — deliberately, so an internal replay fails loudly
on an envelope it cannot interpret. But an external, long-lived consumer
needs the opposite property: an older `am watch` must not crash on a
journal line a newer `am` wrote with a kind it does not yet know. Before
`am watch` ships, loosen *reading* only — `Journal.read`/`replay_journal`
skip an unrecognized `event` (and ignore unknown `payload` keys) rather
than raising — while `Journal.append` keeps writing strictly. This is a
small, separate change, gated on the existing store test suite, and lands
first.

### 3.5 Deferred: row-only transitions get no event in v1

Three transitions a widget would plausibly want are recorded only as rows,
never journaled, and stay that way for v1:

- **Control requests** (`run_controls`, `control.add_control` /
  `apply_pending`): a widget sees nothing between "pause requested" and the
  park actually landing as `stopped`. Journaling this (a `control_upsert`
  kind) is the natural next addition, but only after 3.4, since it is a new
  `event` kind.
- **Lease liveness** (`run_leases`): deliberately read-time, never stored
  (`control.lease_is_live`). This should stay that way — a widget judges
  staleness from the newest line's `ts` plus the same 30-second rule `am`
  itself uses, rather than `am` emitting a liveness event that could itself
  go stale.
- **Checkpoints** (`turn`/`parked`/`done`/`escalated` per agent turn): the
  finest-grained signal in the system, deliberately kept out of the
  journal today. Left alone.

### 3.6 Command shape

```
am watch [RUN_ID | --all] [--since SEQ] [--follow]
```

- No `--follow`: terminating, normal envelope —
  `{"ok": true, "data": {"events": [...]}}`.
- `--follow`: streaming — bare `JournalLine`-shaped objects, one per line,
  until interrupted. A refusal (unknown run id, no data directory) still
  prints the `{"ok": false, "error": {...}}` envelope and exits 3 *before*
  any streaming line is written, so a consumer can tell "this is a stream"
  from "this is a refusal" by inspecting only the first line.
- `--all` walks every run directory under `<data dir>/runs/` rather than
  one `run_id`; `--since SEQ` resumes a consumer's own cursor.
- `--follow` polls each watched file on a short interval (around 250ms) in
  v1 — no new dependency, and the widget itself still does no polling, since
  it only ever reads `am watch`'s stream. Moving the poll to inotify later
  is an internal change to `am watch`, not to its output contract.

This is the one place the README's rule needs a word added: every
terminating command prints one line of JSON; `am watch --follow` prints one
JSON object per line until stopped.

### 3.7 Constraints this design must respect

- **`XDG_DATA_HOME`.** A watcher must resolve the data directory exactly as
  `paths.data_dir()` does. Two processes under different data directories
  are already a documented limit (README, "Several am processes") and stay
  one here: a widget watching the wrong data directory sees nothing, not an
  error.
- **No directory side effect on a bad id.** `Journal(run_id)`'s normal
  constructor creates the run's directory as a side effect
  (`paths.run_dir`). `am watch` must open journals the way `_for_reading`
  does (`store.py:288`) so watching a run that does not exist, or has not
  started yet, never creates an empty run directory.
- **Multi-process is solved by the file, not by `am watch`.** This is the
  reason 3.2 rejects a socket or FIFO outright rather than trying to make
  one multi-process-safe: the lease/claims machinery exists precisely
  because there is no single process to own such an endpoint, and a plain
  directory of files sidesteps the question entirely.

## 4. Testing

- The tolerant-reading change (3.4): a unit test round-tripping a journal
  containing one line with an `event` value the current `EventKind` does
  not list, confirming `read`/`replay_journal` skip it rather than raising,
  and that `append` still refuses to *write* one.
- `am watch`: unit tests for `--since`, `--all`, the no-such-run refusal
  (confirmed to create no run directory), and the torn-tail case already
  covered by `ignore_torn_tail`. An e2e test driving a small milestone and
  confirming `am watch --follow` observes every expected `event`/`status`
  pair in order, including a takeover (`reseek`) continuing the same
  `run_id`/`seq` sequence a watcher was already following.

## 5. Risks

- Making `JournalLine` a public contract is a one-way door: once an
  external consumer depends on its field names, renaming one is a breaking
  change for every widget, not just internal callers. 3.4's tolerant
  reading covers *new* event kinds, not a renamed field on an existing one
  — a future field rename still needs its own compatibility story, not
  addressed here.
- `am watch --follow`'s stat-poll is a known stopgap (3.6); if watch
  latency or CPU usage from several followed runs turns out to matter in
  practice, moving to inotify is the natural next step, deferred here only
  because nothing today demonstrates it is needed.
