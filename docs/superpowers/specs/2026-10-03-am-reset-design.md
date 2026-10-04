# `am reset` — design

Date: 2026-10-03
Status: approved design, pre-implementation

## 1. Purpose

There is no sanctioned way to close a run nobody is driving. `am cancel` is a
*request*: it inserts a `run_controls` row addressed to the live lease's token
and the running process honours it at its next poll (`cli.request_control`,
`cli.py:2220`; `control.apply_pending`, `control.py:180`). So it refuses, by
design, every run that has no live process to deliver to: `NotRunningError`
when the run is not `started`, `DeadRunError` when it is `started` but its
lease is gone or stale (`_controllable_lease`, `cli.py:2125`). The only other
command that writes to a run nobody is driving is `am resume`, and resuming is
the opposite of what an operator who has already torn the run down wants.

That gap was hit during a dogfood run of `am` on itself (2026-10-03). A
stale, superseded milestone run was terminal (not live), so `am cancel`
refused it; its branches and worktrees were removed by hand with
`git worktree remove` and `git branch -D`. Its `checkpoints` rows stayed
behind, open (`turn`/`parked`), pointing at worktrees that no longer existed.
The next `am run --milestone` relaunch did what relaunches do: it adopted the
newest open checkpoint of each card across every run
(`runs.continuable_checkpoint` → `Store.latest_open_checkpoint`,
`store.py:1555`) and continued the walk *after* the `worktree` step
(`workflow/task.py:44` is phase one; a resumed walk never runs it again), so
the first dispatch ran in a directory that was not there and crashed.

The fix applied in the moment was `UPDATE runs SET status='cancelled' WHERE
id=...` straight into the SQLite file. It worked, because
`latest_open_checkpoint` already treats a checkpoint of a cancelled run as
closed (live control C9). But it bypassed every safeguard a sanctioned write
has: no lease, no fence (`Store._fenced`, `store.py:1154`), and no journal
line — which means the projection and the journal now disagree, and a
`rebuild_from_journal` of that run (`store.py:1837`) would silently resurrect
the open checkpoints by putting the status back.

This spec adds the missing verb: `am reset <run-id>` closes a run nobody is
driving, through the store, under the lease, exactly as `am cancel` would
have closed it had the run still been live.

## 2. Scope

**In scope.** One new command, `am reset RUN_ID`, that records a non-live run
`cancelled` through `Store.record_run` under a `control.Lease`; its refusals;
its envelope; the README paragraphs that let it slot into the existing
`stopped`/`escalated`/`cancelled` vocabulary; and the tests in section 4.

**Out of scope, each a separate problem already identified.**

- *Resume re-ensures the worktree.* A resumed or adopted walk starts after
  the `worktree` step and so never recreates a worktree that has gone
  missing. Making the engine re-run `worktree.ensure` on resume would make
  the crash above impossible in the first place; it is
  `2026-10-03-resume-worktree-reensure-design.md`, not this command, and
  this command is still needed without it (an operator who tore a run down
  wants it closed, not revived).
- *Journal/projection divergence detection.* Noticing that a `runs` row no
  longer matches its journal (the state the hand edit left) is
  `2026-10-03-journal-db-divergence-design.md`, not this command. `am reset`
  only guarantees it never *causes* such a divergence.
- *Board cards.* Like `am cancel`, a reset does not touch `brd`: cards keep
  whatever status the run left them in (README, "Cancel ... does not reset
  board cards").
- *Git.* `am reset` removes no worktree and deletes no branch. The operator
  already did that, or will; this command only makes the store agree.

**Never** (unchanged). No raw SQL against the projection from a command, no
write outside `_fenced()` once a token is bound, no push, no `main`/`master`.

## 3. Decisions

### 3.1 It targets a run, not a card

The finding proposed `am reset --card <id>`. The command takes a run id
instead, like `cancel`, `pause` and `resume`:

```
am reset RUN_ID [--repo-dir DIR] [--pretty]
```

Three reasons, in order of weight.

**The unit the operator tore down is a run.** Worktrees and branches belong
to a run's subtask rows (`subtasks.worktree_path`), and the lease's claims are
the run's (`card:<id>`, `branch:<name>`, `control.card_claim`). Every
checkpoint the hand cleanup orphaned was left by one run; the incident
removed *that run's* branches, not one card's.

**"Reset this card" is not well-defined on its own.** `latest_open_checkpoint`
is "newest across every run": the card's newest row in *any* run decides
first (`done`, or in a cancelled run, closes the card), and only then does it
pick the newest open row of the workflow outside cancelled runs
(`store.py:1567-1588`). A card can hold open rows in several runs — a
relaunch that adopted run Y's checkpoint writes its own `turn` rows under run
X on the next `BEFORE_TURN`, so Y's rows stay open underneath. "Reset the
card" would then have to choose between closing one run's rows (and exposing
Y's, which point at the same worktree path, since `runs.worktree_for` is a
function of the branch) or cancelling every non-live run that mentions the
card (closing other cards' progress in those runs as a side effect). Neither
is what the finding meant. Targeting the run makes the write exact and lets
the report say what is left (3.5).

**The post-condition is a run-level fact.** The acceptance criterion is "a
relaunch re-dispatches the card from phase one *and* `am resume <old-run-id>`
refuses as it refuses a cancelled run". The second half is a property of the
run's status (`resume_run`, `cli.py:2030`), and no per-card row can provide
it.

Rejected: `am cancel RUN_ID --force`/`--dead`. `cancel` has one meaning the
README states carefully — it records a request and returns at once; the run
itself does the stopping. A flag that sometimes writes the terminal status
directly would give one verb two write paths. A second verb keeps each
command's contract one sentence long, and `cancel`'s `DeadRunError` message
gains a pointer to it.

### 3.2 It writes `runs.status = 'cancelled'`, not a checkpoint row

The write is one `Store.record_run(run.model_copy(update={"status":
"cancelled"}))` on the run as `load_run` returned it: one `run_upsert` journal
line, then the `runs` row, in one fenced transaction (`store.py:1196`). Story,
subtask, phase and attempt rows are untouched, as they are after `am cancel`
(README: "the story and subtask rows keep the walk's own status"). No
`checkpoints` row is written or deleted.

Why this and not a checkpoint row. The three readers that decide what a
checkpoint means were each checked against the alternatives:

| Reader | `done` row under the run | new `abandoned` reason | `runs.status = 'cancelled'` |
|---|---|---|---|
| relaunch: `latest_open_checkpoint` (`store.py:1555`) | closes the card (newest row `done`) — only if written under the run holding the card's newest row | needs the CHECK at `store.py:105` changed and both queries taught the new reason | closes the card: newest row's run is cancelled, and the second query excludes cancelled runs — already implemented |
| milestone resume: `orchestrate.resume_point` (`orchestrate.py:684`) | `done` means "start the card fresh *in this run*": the resume proceeds and re-dispatches under the old run id, which is not a refusal | needs its own branch | never reached: `resume_run` refuses a cancelled run first (`cli.py:2030`) |
| task resume: `cli.checkpoint_resume_phase` (`cli.py:505`) | refuses with "only the final status write was lost", a false explanation | needs its own branch | never reached: same C9 refusal |

A `done` row lies — the walk did not finish — and gives the wrong answer on
a milestone resume. A new reason costs a CHECK-constraint migration (SQLite
cannot alter a CHECK in place; `open_db` has only `_ADDED_COLUMNS` as its
migration mechanism, `store.py:207`) plus three readers, to express a state
the run status already expresses. And `cancelled` is already the documented
word for exactly this: "a run closed for good ... its checkpoints are never
continued and it is never resumed" (`models.Status`, `models.py:25`; README,
"Pausing and cancelling a run"). Reusing it means `am status`, `am runs`,
`am watch`'s `run_upsert` table and every README sentence about cancelled
runs are already correct for a reset run.

Both at once ("for safety") is rejected: the `done` row adds nothing the
status does not, and would put a false `done` into the row `am status` and a
future `am logs` reader see as the card's last checkpoint.

How a reset is told apart from an `am cancel` afterwards, for whoever reads
the run later: a run cancelled by request has a `cancel` row in
`run_controls` (`am status`'s `control.requests`); a reset run has none, and
its final `run_upsert` line is stamped well after the previous terminal one.
No new column or event is added to say "reset"; the distinction is
recoverable and nothing reads it.

### 3.3 Fencing: the resume's takeover path, with nothing driven

"Nothing is live, so nothing needs protecting" is the wrong reading. The
fence is what turns a write into a sanctioned one: with no token bound,
`_fenced()` is a no-op and `record_run` writes unfenced (`store.py:1165`),
which is the M9 behaviour the lease was added to end. So `am reset` does what
`_resume_from_checkpoint` does up to the first write (`cli.py:1885-1916`),
and then stops:

1. `resolve_repo_dir`, `open_db`, `load_run` — read-only; `UnknownRunError`
   as `resume_run` raises it. Then the read-only refusals of 3.4, on that same
   connection, so a refusal leaves no run directory, lease row or journal line
   behind (the order `resume_run` keeps for the same reason).
2. `Store.open(root, run_id)` and `cli.run_lease(store)` with **no claims**.
   `control.Lease.__enter__` calls `take_lease`, which in one `BEGIN
   IMMEDIATE` refuses a live holder (`LeaseHeldError` → `RunIsLiveError`),
   takes over a dead one (reported as `displaced`), binds the token and
   `reseek`s the journal so this line is numbered after anything a stuck
   previous owner appended (`store.py:1711-1773`). The heartbeat thread runs
   for the few milliseconds the block lasts, which is what makes a concurrent
   `am resume <run-id>` refuse with `RunIsLiveError` rather than race the
   write.
3. Inside the block: `store.record_run(cancelled)`. `_fenced()` re-checks the
   token, appends the journal line and writes the row in one transaction.
4. `Lease.__exit__` releases the lease row and unbinds the token; the store
   closes.

No claims, because reset drives no card and owns no branch: claiming
`card:<id>` for the run's subtasks would make it refuse whenever a *relaunch*
is already live on one of them, and that relaunch is unaffected by the reset
(it read the checkpoint at dispatch and writes its own rows now). Taking the
lease of the dead run is the whole concurrency story: two resets of one run
serialise on `take_lease`, and the second sees `cancelled` and is a no-op
(3.4).

A lighter path — `immediate(conn)`, check status, `UPDATE runs` — is exactly
the hand edit with a transaction around it: no journal line, no fence. It is
rejected for the reason this spec exists.

### 3.4 Refusals and the no-op

In order, all read-only and before `Store.open`, each an `{"ok": false,
"error": {"type", "message"}}` envelope at exit 3 via `HANDLED`:

| Condition | Type | Message says |
|---|---|---|
| run id not in the projection | `UnknownRunError` (existing) | as `resume_run`'s: `am runs` lists the ones that are |
| lease row present and `control.lease_is_live` | `RunIsLiveError` (existing) | the pid, host and heartbeat age, and: it is running, so `am cancel <run-id>` is the command; `am reset` is for a run nobody is driving |
| `status == "done"` | `NotResettableError` (new, a `CliError`) | the run finished; every card's newest checkpoint is already `done`, so there is nothing to close; start new work with `am run` |

`take_lease` re-checks liveness atomically after the read-only check, so the
race between them resolves to the same `RunIsLiveError` (through
`run_lease`'s translation, whose generic wording is acceptable for the race
only).

`status == "cancelled"` is **not** a refusal. Like a repeated `am cancel`
(`already_requested: true`), it exits 0 with `already_cancelled: true` and
writes nothing — no journal line, no row. The lease is still taken and
released around the check, which is harmless (lease rows are row-only, outside
the journal) and keeps one code path.

Every other recorded status is resettable: `stopped` and `escalated` (the
terminal states a relaunch adopts from), and `started` with no lease or a
dead lease (the crash case `DeadRunError` names today — this is the run a
crashed process left recorded in flight). `started` with a dead lease is the
takeover, and the payload reports it under `took_over` with the same shape
`resume` uses (`cli.py:1970-1976`).

A run with no checkpoints at all — one that died before its first turn — is
reset like any other, with an empty `cards` list (3.5). The run is still the
thing being closed: `am resume` of it must refuse afterwards, and the status
write is what makes it. There is no "nothing to reset" refusal for a
checkpoint-less run; `done` is refused because a finished run is not stale,
not because it has no rows.

`am cancel`'s `DeadRunError` message, which today ends "`am resume <run-id>`
picks it up", gains "or `am reset <run-id>` closes it", in both of its
wordings (`cli.py:2148-2157`). That is the only change to an existing
command.

### 3.5 Envelope

```json
{"ok": true, "data": {
  "run_id": "20261003T101500Z-bdc5838b",
  "previous_status": "stopped",
  "status": "cancelled",
  "already_cancelled": false,
  "cards": [
    {"card_id": "<subtask-id>", "workflow": "task", "open_in": null},
    {"card_id": "base-<story-id>", "workflow": "bases", "open_in": "20261001T090000Z-19efcddc"}
  ],
  "message": "run 20261003T101500Z-bdc5838b is cancelled; am resume refuses it, and a relaunch starts each card above from its first phase"
}}
```

`took_over: {"pid", "host", "heartbeat_at"}` is added only when a dead lease
was displaced, as on `resume`.

`cards` is the one piece of information the run status alone cannot give,
and it is why the command reports anything beyond the status. For every
distinct `(card_id, workflow)` with a `checkpoints` row under this run — a
new read-only `Store.checkpoint_cards(run_id)` — the command calls
`store.latest_open_checkpoint(card_id, workflow)` *after* the write and
reports the `run_id` of the row still adoptable as `open_in`, or `null`. It
is `null` in the ordinary case (the card's newest row was this run's, so the
newest-row rule closes it). It names another run exactly in the chain case
3.1 describes — this run adopted that run's checkpoint and crashed before its
first `BEFORE_TURN` wrote a newer row — so the operator sees which run to
reset next instead of discovering it on the relaunch. `open_in` is the run
the *next relaunch* would continue from, not a promise that its worktree
exists.

### 3.6 What a relaunch and a resume see afterwards

This is the acceptance criterion, traced through the code as it is:

- **Relaunch** (`am run --milestone`, a new run id). For each remaining
  subtask, `runs.continuable_checkpoint` → `latest_open_checkpoint`: the
  card's newest row belongs to the reset run, whose status is now
  `cancelled`, so the first query returns `None` before the second runs
  (`store.py:1574-1579`). The lane passes no `resume_from`
  (`orchestrate.py:1287-1289`), `run_subtask_async` builds a fresh agent, and
  the walk starts at `worktree` (`task.py:44`): `worktree.ensure` checks the
  surviving branch out into a fresh worktree, or re-cuts the branch from its
  base when `git branch -D` removed it (`steps/worktree.py:265-282`). Nothing
  about the walk is told it was ever attempted. A `base-<story>` resolver
  checkpoint is closed the same way (its rows are in the same table under the
  same run).
- **Resume** (`am resume <reset-run-id>`). `resume_run` loads the run, sees
  `status == "cancelled"` and raises `NotResumableError("run <id> was
  cancelled; start new work with `am run --milestone`")` before `Store.open`
  (`cli.py:2030-2033`) — the identical refusal, message included, that an
  `am cancel`led run gets. Task and milestone runs alike, since the check
  precedes the workflow branch.
- **`am status`/`am runs`/`am watch`.** Show `cancelled`; the journal carries
  the `run_upsert` line that says so, in sequence, so `rebuild_from_journal`
  reproduces the status rather than reverting it.

### 3.7 Documentation

README, "Pausing and cancelling a run": after the Ctrl-C/pause/cancel list,
one paragraph introducing `am reset <run-id>` — a run nobody is driving
(crashed, or terminal and torn down by hand) is closed with it; it records
the run `cancelled` exactly as a cancel would have, so everything the section
says about a cancelled run applies; it writes no git and touches no card;
`already_cancelled` on repeat; the three refusals; `cards`/`open_in`. The
`DeadRunError` bullet in the refusal list gains the pointer from 3.4. The
"Relaunching resumes" paragraph that says "A relaunch after an `am cancel`
ignores the cancelled run's checkpoints" gains "or an `am reset`". The watch
section's `run_upsert` row needs no change: `cancelled` is already listed.

## 4. Testing

All `unit` tier unless marked: driven through the `projection` fixture
(`tests/test_cli.py:3901`) and the store, with `brd`/`git`/`claude` stubbed
off `PATH`. Named up front, so the implementation plan can take them as
given:

1. **Reset of a `stopped` run with an open `parked` checkpoint.** The run row
   reads `cancelled`; the journal gained exactly one `run_upsert` line with
   `payload.status == "cancelled"` and the next `seq`; `rebuild_from_journal`
   of the run yields `cancelled` (the property the hand edit lacked);
   `latest_open_checkpoint` for the card returns `None`; envelope has
   `previous_status: "stopped"`, `already_cancelled: false`, the card in
   `cards` with `open_in: null`; exit 0. Run twice: the worktree directory
   present, and removed — the store write is identical and the test asserts
   it does not look at the filesystem.
2. **Refused: the run is live.** A lease row with a fresh heartbeat and this
   process's pid: `RunIsLiveError`, exit 3, message names `am cancel`;
   status, journal length and lease row unchanged.
3. **Started run with a dead lease (the crash case).** A lease row with a
   stale heartbeat or a dead pid: reset succeeds, `took_over` names the
   displaced holder, and no `run_leases` row for the run remains afterwards.
4. **Already cancelled.** Exit 0, `already_cancelled: true`, journal length
   unchanged.
5. **Refused: `done`.** `NotResettableError`, exit 3, nothing written.
6. **Refused: unknown run.** `UnknownRunError`, exit 3, and no run directory
   is created (every refusal precedes `Store.open`, 3.3 step 1).
7. **Run with no checkpoints.** Reset succeeds with `cards: []`; `am resume`
   of it then refuses (test 9).
8. **A relaunch after reset starts fresh.** Through `orchestrate` with the
   fake driver: a card with an open checkpoint under the reset run is
   dispatched with no `resume_from`. Twice: the checkpoint's worktree
   directory present, and absent — both start at phase one. One
   `@pytest.mark.git` companion in `tests/steps/` proving `worktree.ensure`
   recreates a removed worktree for a surviving branch, and re-cuts one for a
   deleted branch, since that is what "from phase one" relies on.
9. **`am resume` of the reset run refuses.** Task and milestone workflows:
   `NotResumableError`, the cancelled message, exit 3, nothing written —
   reusing the assertions of
   `test_resume_writes_nothing_when_it_refuses`.
10. **The chain case.** Run Y with an open row for card c, run X with a newer
    open row for c: reset X → `open_in: null` (newest-row rule), and
    `continuable_checkpoint(c)` is `None` even though Y's row is still open.
    Variant: X holds a `turn` row for c under the `task` workflow but Y's
    newer row for c is under `bases` (a resolver), so after resetting X the
    newest row is Y's and still open — `cards` lists c with `open_in: Y`,
    and after resetting Y too it is `null`.
11. **Concurrency** (`unit`, two stores on one database, as
    `tests/test_control.py` does): a reset while another process holds the
    run's lease live is refused at `take_lease` even when the read-only check
    passed (monkeypatched `lease_is_live`), and two resets of one run leave
    one journal line.
12. **`e2e_fake`, one scenario:** a milestone paused under the fake `claude`,
    its subtask worktree removed by hand, `am reset`, then the relaunch
    drives the card from `worktree` to `done`. This is the incident, replayed.

## 5. Risks

- **`cancelled` now has two causes.** A reader of `am runs` cannot tell a
  requested cancel from a reset without looking at `control.requests` (3.2).
  Accepted: the vocabulary gain outweighs it, and nothing in `am` branches on
  the difference. If a future need appears, a journaled `control_upsert`
  kind (deferred in `2026-10-02-am-watch-design.md` §3.5) is the right place
  to record "reset by operator", not a new run status.
- **The reset does not prove the worktree is gone.** An operator can reset a
  run whose worktrees still hold uncommitted work; the relaunch then re-adds
  the worktree for the surviving branch (commits kept) but starts the walk
  over, re-running phases that passed. This is the same trade `am cancel`
  makes ("a subtask the cancel parked starts again from its first phase"),
  and the message in 3.5 says so.
- **The chain case is reported, not resolved.** `open_in` tells the operator
  which run to reset next rather than cancelling it for them (3.1's second
  rejected option). If this turns out to be the common case in practice, a
  `--cascade` that resets every run `open_in` names, each under its own
  lease, is a contained follow-up.
- **A corrupt journal refuses the reset.** `Store.open` constructs
  `Journal(run_id)`, whose `last_seq` scans with the strict reader
  (`store.py:339`, `358`); a crashed run whose own journal has a torn final
  line raises `CorruptJournalError` before the lease is taken. `am resume`
  has the same exposure today. A reset's line *must* be numbered after a
  torn one, so tolerating it here means truncating or re-numbering, which is
  the divergence-repair work this spec keeps out of scope. `JournalError` is
  not in `HANDLED` (`cli.py:1142`), so today it would surface as a traceback;
  adding it to the tuple, so the refusal is an envelope at exit 3 naming the
  file and line, is part of this work.
