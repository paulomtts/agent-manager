# Parallel stories — design addendum

Date: 2026-09-24
Extends: `2026-09-24-orchestration-design.md` (O6 "sequential runner") and
`2026-09-23-agent-manager-design.md` (§11 concurrency, §12 escalation)
Status: milestone 4 scope; decisions P1–P7. Integrate is milestone 5.

## 1. Why this shape

`am run --milestone` runs one story at a time. The main spec's §11 says stories
in the same dependency level run in parallel, bounded by `max_concurrent_stories`
(default 4); today the field exists, defaults to 1, and nothing reads it.

Parallelism does not save tokens, so it does not serve the first motivation
(cost). It serves latency, and it is what makes the second half of the design
(Integrate, which exists to merge parallel stacks) worth building.

Every seam below was found by reading the code first, because milestone 2 taught
that seams between cards only show up when something runs:

- `store.open_db` calls `sqlite3.connect` with the default `check_same_thread`,
  so a lane thread using the shared connection raises `ProgrammingError`.
- `Journal.append` computes the next sequence number with `last_seq()`, which
  re-reads the whole journal file on every line: quadratic in run length, and two
  threads reading at once take the same number.
- `worktree.ensure` runs `git worktree add` with no coordination, and
  `steps/rollup.py` reads siblings then writes the parent: a read-modify-write
  that two lanes finishing together can interleave, leaving a milestone `in
  progress` after every story is `done`.
- `engine.run_subtask` has no way to be told to stop, and `models.Status` has no
  state for "stopped between phases".

What is already safe: `cli.drive_subtask` loads its workflow and builds its runner
per call, `AgentRunner.warnings` is per instance, `dispatch.next_attempt` scans
per card, and `paths` creates directories with `exist_ok=True`. Nothing at module
level is mutated.

## 2. Decisions

**P1 — Threads, one lane per story, bounded.** A level's stories run on a thread
pool of `max_concurrent_stories` workers (default 4, per the main spec). Subtasks
inside a story stay strictly sequential. Levels stay barriers: level N+1 starts
when every story of level N has finished. A story could start the moment its own
blocker finishes, which would be more parallel, but it is a second scheduling
rule, and the barrier is the old orchestrator's behaviour. Per-story readiness is
deferred. `--max-concurrent N` sets the bound and is recorded in the run's
config. `--max-concurrent 1` behaves exactly as the sequential runner does today,
and is the escape hatch for a repo whose tests cannot run side by side.

**P2 — One store, one lock.** A single SQLite connection opened with
`check_same_thread=False` and a `busy_timeout`, plus one reentrant lock making
"append the journal line, then write the row" a single critical section in every
`record_*`, so journal order and row order agree. The journal reads the highest
sequence number from disk **once, at open**, and increments an in-memory counter
under the lock. One process writes a given run: two processes on one run are not
supported and no attempt is made to support them.

**P3 — Shared git and board resources are serialised in-process.**
`git worktree add` runs under a per-repository lock. Every `board.set_status` and
the *whole* rollup walk run under one board lock, since a rollup is
read-modify-write. The lock does not replace a deliberate test of `brd` itself
(main spec §17): a stress test bypasses the lock and hammers `brd update` from
threads. If `brd` fails under it, `board._run` gets a bounded retry on lock
errors. Two `am` processes on one repository are not supported.

**P4 — Stop cooperatively, between phases, never inside one.** The first
escalation (or a lane's uncaught exception) sets a shared `threading.Event`.
`engine.run_subtask` takes a `should_stop` callable and checks it **before each
phase**. A phase already running is never interrupted: an agent phase mid-flight
finishes, so its work is not lost and its record stays consistent. When the check
fires, the subtask is recorded `stopped` and the walk returns. A new `stopped`
status is added to `models.Status` and to the summary; the store has no `CHECK` on
status, so no schema change is needed. `stopped` is not `failed`: relaunching the
same command continues a stopped subtask from where it parked, through the same
idempotence and Plan-Hash re-entrancy that resume already relies on.

**P5 — The escalation report stays compatible and gets richer.** `escalated:
true` and its existing fields (`level, story, subtask, failed_phase, detail`)
describe the *first* escalation detected, so callers keep working. New keys:
`also_escalated`, other lanes that failed before they saw the stop, and
`stopped`, each parked lane as `{story, subtask, before_phase}`. "First" means
first to take the lock, which is well defined even when two fail together.

**P6 — A lane's exception is an escalation, never a silent loss.** The old
`mapWithConcurrency` turned a dying lane into `null` and relied on the level loop
to halt. Here a lane's uncaught exception is caught at the lane boundary, recorded
as an escalation with the exception's type and message, and sets the stop event.
`BaseException` (Ctrl-C) is not caught.

**P7 — Parallelism is proven, not sampled.** A test that asserts two stories
overlapped because their timestamps happen to overlap is flaky. The fake
`claude`'s implement phase instead **rendezvouses**: each lane writes a marker file
and waits (with a timeout) for the other lane's marker. If the lanes were
sequential the wait would time out and the test would fail, so passing proves
overlap. The rule from milestone 2 stands: the fake must never know more than the
brief tells it. The rendezvous directory comes from the test's environment, not
from the brief, because it is test scaffolding and not something a real agent
would be told. One opt-in test (`pytest -m e2e`) runs two independent stories
against a real `claude -p`; it is a human step.

## 3. Acceptance

1. Under the fake `claude`, two independent stories in one level run at the same
   time (rendezvous), and with `--max-concurrent 1` they do not.
2. No more than `max_concurrent_stories` lanes are in flight at once.
3. One story escalating stops the other lanes at their next phase boundary:
   the other lane's subtask is `stopped`, no phase starts after the stop, and no
   story of a later level starts.
4. Relaunching after that continues the stopped subtask and completes the
   milestone, skipping what is done.
5. After a parallel run the journal has unique, contiguous sequence numbers, and
   the projection rebuilt from the journal equals the database.
6. After a parallel run every finished story's card, and the milestone card, is
   `done` on the board, however the lanes interleaved.
7. By hand, after the milestone: two independent stories against a real harness
   both reach `done`, and their phases overlapped in time.

## 4. Limits stated plainly

- **Your test suite runs side by side.** Each lane's `verify` phase runs the
  repository's tests in its own worktree while other lanes do the same. Tests that
  use a fixed port, a shared file or a shared database will collide.
  `--max-concurrent 1` is the answer. `uv run pytest` builds a `.venv` per
  worktree, which costs time and disk once per lane.
- **Machine load.** Four `claude -p` processes at once is four times the request
  rate. Nothing here rate-limits them.
- **A stop waits for the running phase.** After an escalation, a lane in the
  middle of a long `implement` finishes that phase before parking.

## 5. Deferred

- **Integrate** is milestone 5. Its cards are cut after this milestone merges,
  because they must be written against `run_milestone` as it will then be.
- Per-story readiness in place of the level barrier.
- A milestone-aware `resume`, plus `watch`, `retry` and `cancel`.
- **Cost capture.** Real `claude -p` attempts record a duration but no tokens or
  cost, because the adapter reads counts from plain-text stdout that carries none.
- **`am`'s reviewer brief** never mentions `Plan-Hash`, and the `review` phase is
  not given the hash as an input. The first real review that commits a fix will
  trip `review_gate`. The old pipeline had the same bug and was fixed in
  leave-me-alone 4.1.5.
- Handling Ctrl-C by stopping lanes cleanly.
