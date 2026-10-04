# Journal / projection divergence detection — design

Date: 2026-10-03
Status: approved design, pre-implementation

## 1. Purpose

D5 says the SQLite projection is a cache and the journal is the truth: "The
DB is a projection and can be rebuilt from the journal; if the two disagree,
the journal wins" (`2026-09-23-agent-manager-design.md` §9, lines 365-368;
restated in the `store.py` module docstring, lines 1-7). Nothing in `am`
ever checks whether the two *do* agree. Every reader — `am status`,
`am resume`'s `cancelled` refusal (`cli.py:2030`), `latest_open_checkpoint`'s
"ignore cancelled runs" rule (`store.py:1555-1588`) — trusts the projection
row outright, and the one thing that would act on a disagreement,
`Store.rebuild_from_journal` (`store.py:1837`), has no caller anywhere in
`cli.py` (confirmed by grep: its callers are `tests/test_store.py`,
`tests/test_integration.py` and `tests/e2e/test_parallel_milestone.py`; the
two mentions in `bases.py:176` and `integration.py:14` are docstrings).

This is not hypothetical. On 2026-10-03, during a dogfood run, a real run's
projection row read `runs.status = 'cancelled'` while the newest
`run_upsert` line in its journal said `escalated`; `run_controls` had no
row for the run, so `am status` showed `requests: []`. The only code that
records `cancelled` does so through `record_run` after a cancel request has
been applied (`orchestrate.py:1930`, and `card_run_status` in `cli.py`),
which requires a `run_controls` row. The row had been changed by hand with
a raw `UPDATE runs SET status='cancelled'`, the only lever available in the
moment to make `latest_open_checkpoint` stop adopting that run's orphaned
checkpoints. Nothing noticed. Had anyone run `rebuild_from_journal` on that
run, it would have silently put `escalated` back and re-armed the stale
checkpoints — exactly the opposite of what the hand-fix was for.

Two companion fixes address *why* that hand-edit was ever needed (see §2).
This spec is the third, separate thing: make `am` able to say, cheaply and
on demand, "the journal and the projection disagree about this run, here is
how," so the next divergence — whatever causes it — surfaces instead of
silently steering `resume`, checkpoint adoption or a rebuild.

## 2. Scope

**In scope.** One read-only check, run on every `am status` call and
reported as a new key in its payload; a precise, narrow definition of what
"divergence" means for that check; a safety rail on
`Store.rebuild_from_journal` so it cannot silently overwrite a projection
value the journal never recorded; the docstring and README sentences that
today overstate what the journal can rebuild.

**Out of scope, handled elsewhere.** The root causes that made a hand-edit
tempting are two separate specs being written alongside this one:
`2026-10-03-resume-worktree-reensure-design.md` (resume re-ensures the
worktree, so an orphaned checkpoint no longer needs a cancelled run to be
ignored) and `2026-10-03-am-reset-design.md` (a sanctioned `am reset` that
does what the raw `UPDATE` did, through the store). Neither file exists on
disk at the time of writing; if their names change, this section is the
only place to update. This spec does not change how those commands write.

**Out of scope, deliberately.** Any repair. This check never writes a row,
never appends a journal line, never deletes anything (§3.4 is a hard
constraint, not a default). Journaling the row-only tables (`checkpoints`,
`checkpoint_floors`, `run_controls`, `run_leases`, `run_claims`,
`board_comments`) so they too could be rebuilt — the am-watch design (§3.5)
already deferred that and this spec does not reopen it. A standalone `am
check`/`am fsck` command: `am status` is where an operator already looks,
and a second command would be one more thing to remember to run. Comparing
anything other than status and shape (see §3.2 for why). Detecting
inconsistencies *within* the projection that have no journal counterpart —
a checkpoint row for a run that has no `runs` row, a lease row for a run
that is `done` — because those are row-only tables with nothing to compare
against; they are a different question ("is the projection self-consistent")
from this one ("do truth and cache agree").

**Never** (unchanged from the rest of `am`). Pushing, opening PRs, touching
`main`/`master`.

## 3. Decisions

### 3.1 What the projection can and cannot be rebuilt from the journal

Read precisely, `rebuild_from_journal` (`store.py:1837-1878`) replays the
journal into a `models.Run`, calls `_delete_run` (`store.py:1897-1903`),
which deletes this run's rows from exactly five tables — `attempts`,
`phases`, `subtasks`, `stories`, `runs` — and rewrites those five from the
replayed tree. The other six tables are not touched, and `Store`'s own
docstring says so (`store.py:1104-1113`): `checkpoints`, `checkpoint_floors`,
`run_controls`, `run_leases`, `run_claims` and `board_comments` are row-only,
"outside the journal," and "`rebuild_from_journal` leaves those rows alone."

So the D5 sentence is true of the §9 *tree* (run, stories, subtasks, phases,
attempts) and false of everything that drives recovery: a projection that is
lost loses every resumable checkpoint and every pause/cancel request, and a
rebuild cannot bring them back. This spec does not change that; it stops the
code from claiming otherwise. The module docstring (`store.py:1-7`), the
§9 sentence in the design spec, and the README's description of the
projection gain one clause each: the journal rebuilds the run's tree; the
row-only tables have no journal and are the projection's alone.

This boundary is also what makes the check below well-defined: "divergence"
is a question that can only be asked of the five tree tables, because only
they have a journal to disagree with.

### 3.2 What counts as divergence: status and shape of the tree, nothing else

The check compares the §9 tree as the journal replays it
(`store.replay(Journal.read(...))`) against the tree as the projection loads
it (`store.load_run(conn, run_id)`), node by node, and reports exactly two
kinds of difference:

- **A status mismatch.** A node present in both trees whose `status` differs.
  Nodes are matched by their identity: the run by itself; a story by
  `card_id`; a subtask by `(story card_id, card_id)`; a phase by `name`; an
  attempt by `n`. Every level is compared, because it is one recursive walk
  and leaving a level out would be an arbitrary line to draw — but *only*
  `status` is compared. The real incident was a status mismatch at the run,
  and status is the one field at every level that the program branches on:
  `resume` refuses on `runs.status == 'cancelled'`, `latest_open_checkpoint`
  filters on it, the milestone resume decides what is in flight from subtask
  and phase status. Titles, branches, worktree paths, timestamps, costs and
  `config` steer nothing on their own and are left alone.
- **A shape mismatch.** A node the journal replays that has no projection
  row, or a projection row that no journal line created.

Each mismatch is also classified, cheaply, as one of two kinds — and this
classification is what the rest of the design hangs on:

- **`stale`**: the projection's value is one the journal *did* record for
  that node at some earlier `seq`, or the projection simply lacks a node the
  journal has. This is the divergence D5 was designed for: a crash between
  the journal append and the row commit (the case
  `test_rebuild_picks_up_a_journal_line_whose_row_never_landed` pins down,
  `tests/test_store.py:965`). The journal is ahead; a rebuild would move the
  projection forward to where the journal already is.
- **`foreign`**: the projection's status is a value *no* journal line ever
  recorded for that node, or the projection has a row for a node the journal
  never created. Nothing in `am` can produce this: every tree row is written
  by a `record_*` that appended the same value to the journal first, inside
  one lock (`store.py:1187-1261`). A foreign value was written outside the
  store — by hand, by a raw SQL statement, by a future bug — and it is
  precisely the value a rebuild would silently destroy. The 2026-10-03
  incident is a `foreign` status mismatch at the run node: `cancelled`
  appears in no line of that journal.

The classification needs the journal *lines*, not only the replayed tree:
one extra pass over the lines collects, per node, the set of statuses ever
recorded. Both passes are linear in the journal's length (roughly fifty
lines per clean subtask, per am-watch §3.1), and `am status` already loads
the whole tree from the projection, so the check costs about as much as the
load it sits next to. That is cheap enough to run every time; no caching, no
flag to turn it off.

Known limit, accepted: a hand-edit that sets a node back to a status the
journal recorded earlier (`escalated` reverted to `started`) is
indistinguishable from a crash and classifies as `stale`. A rebuild restoring
the journal's newest value is then the documented contract, not a surprise;
and `am status` still reports the mismatch, so it is not silent.

### 3.3 Where it lives: a new `integrity` key on `am status`

The check runs inside `status_for` (`cli.py:1416-1460`), the read-only
function behind `am status [RUN_ID]`, after the tree and the control view
are loaded over the same connection. No new command; no change to `am runs`
(a listing that would have to open every journal to say anything) or to
`am watch` (which prints the journal and has no projection to compare).

The payload gains one top-level key beside `run`, `stories`, `rows` and
`control`, built by an `integrity_view` function with the same role
`control_view` (`cli.py:247`) has for `control`:

```json
"integrity": {
  "checked": true,
  "reason": null,
  "mismatches": [
    {
      "node": {"story": null, "card": null, "phase": null, "attempt": null},
      "field": "status",
      "journal": "escalated",
      "projection": "cancelled",
      "kind": "foreign"
    }
  ]
}
```

- `checked` is `false`, `reason` says why, and `mismatches` is `[]` when the
  comparison could not be made. The three reasons: `"lease is live"` (§3.5),
  `"no journal"` (there is no `journal.jsonl` for the run under the data
  directory — a projection row without a journal is itself worth seeing,
  and the reason says so without failing the command), and
  `"journal unreadable: <message>"` (a `JournalError` or a pydantic
  `ValidationError` from `replay`, the same errors adoption already turns
  into a declined warning at `dispatch.py:645-650`). None of these is an
  error envelope: `am status` of a run whose rows exist must keep exiting 0
  exactly as it does today, because that is what every planted-projection
  test in `tests/test_cli.py` asserts and what an operator debugging a
  broken run needs most.
- `node` uses the journal's own coordinate names (`story`, `card`, `phase`,
  `attempt`, as `JournalLine` and the am-watch v1 contract spell them), all
  `null` for the run itself, so the vocabulary is the one `am watch` already
  prints rather than a third one.
- `field` is `"status"` for a status mismatch and `null` for a shape
  mismatch, where `journal`/`projection` are the status on the side that has
  the node and `null` on the side that lacks it.
- `kind` is `"stale"` or `"foreign"` as §3.2 defines them.
- Order: the §9 tree walk order (run, then stories by position, …), so two
  runs of `am status` print the same list.

A clean run is `{"checked": true, "reason": null, "mismatches": []}`. The
key is always present, like `control` (C12), so a consumer never has to
test for its absence. The exit code is unchanged: `am status` is a report,
and a run with mismatches is still a run whose status was reported. A
script that wants to gate on it reads `data.integrity.mismatches`.

The comparison itself is a pure function in `store.py` —
`diverging(lines: list[JournalLine], projection: models.Run) -> list[Mismatch]`,
with `Mismatch` a frozen dataclass carrying the five fields above — so the
CLI, the rebuild rail (§3.6) and the tests share one definition of
"disagree." `status_for` opens the journal through
`Journal._for_reading(run_id).read(ignore_torn_tail=True)`, the same way
`_journal_events` does for `am watch` (`cli.py:1626-1635`), and for the same
two reasons: the normal `Journal(run_id)` constructor creates the run
directory (`paths.run_dir`), which a reader must never do, and another
process may be appending to the file right now.

### 3.4 Hard constraint: report, never repair

The check writes nothing: no row, no journal line, no delete, no
`rebuild_from_journal`. This is the point of the spec, not a convenience.
The one real divergence on record is one where the journal's newest value is
*wrong* for the operator's purposes and the projection's hand-written value
is right; an auto-repair in either direction would have made that run worse.
"The journal wins" is a rule about which store to trust when rebuilding; it
is not a mandate to rebuild. `status_for` keeps its documented property
("Read-only: no `record_*` is called") and the README's "Readers always work
and take nothing" sentence stays true word for word.

Any future command that acts on a reported mismatch — `am reset`, a
hypothetical `am rebuild` — is a separate spec that must name which kind it
acts on and how it asks first.

### 3.5 A live run is not checked

While the run's lease is live (`control.lease_is_live`, the same `live` the
`control` key already reports), the comparison is skipped with
`reason: "lease is live"`. Between a `record_*`'s journal append and its row
commit there is a window in which a reader sees the journal one line ahead
of the projection; reading the two in either order cannot close it from the
outside, and reporting that as a mismatch would be noise on every status
call against a running milestone. Nothing acts on a live run's projection
from outside anyway: `resume` refuses it (C10) and a rebuild of a run
another process is writing is already forbidden by the lease. A run that is
dead, finished, parked or cancelled — every run anyone would resume or
rebuild — is checked.

### 3.6 `rebuild_from_journal` gains a rail against `foreign` values

Decision: yes, a small one, in the method itself, because the method is the
only writer in the codebase that can overwrite a projection value the
journal never recorded, and a rail that lives in a CLI wrapper would protect
only callers that go through that wrapper. `rebuild_from_journal` already
refuses two kinds of bad input (a journal naming another run's id,
`store.py:1855-1859`; a phase whose subtask no line created, through
`replay`'s `_find`), so a third refusal is the same shape of guard.

Signature: `rebuild_from_journal(run_id, *, force: bool = False)`. Before
`_delete_run`, with the store lock and fence already held, the method loads
the projection's tree over its own connection and runs `diverging` on the
journal lines it just read. If any mismatch is `foreign` and `force` is
false, it raises `ProjectionDivergedError` (a new `RuntimeError` subclass —
not a `JournalError`, since the journal is fine) whose message lists the
foreign mismatches, and touches no row. `stale` mismatches never refuse:
moving a projection forward to where the journal already is is what the
method exists for. With `force=True` the rebuild proceeds as today.

Every existing caller keeps working without `force`, checked against each
test: a truncated or emptied projection has no rows, so nothing is foreign
(`tests/test_store.py:895-910`, `930-945`); rebuilding a run recorded only
through `record_*` is an exact match (`917`, `1136`, `1168`, `1220`,
`1535`, `tests/test_integration.py:587`,
`tests/e2e/test_parallel_milestone.py:201`); the journal-ahead case is
`stale` (`965`). No production code calls it at all.

The method's docstring sentence "the result is the same whether the
projection was stale, truncated or already correct" stays true and gains
the one exception: a projection that holds a value the journal never did is
refused unless the caller says it knows.

### 3.7 Constraints this design must respect

- **No directory side effect.** Opening the journal for the check must go
  through `Journal._for_reading`, never `Journal(run_id)` or
  `Store.open`; `am status` of a run whose directory is gone must not
  recreate it (am-watch §3.7, `load_run`'s docstring at `store.py:637-647`).
- **`XDG_DATA_HOME`.** The journal is resolved through `paths.data_dir()`,
  the projection through `paths.project_db_path(root)`. A projection in one
  data directory and a journal in another report `"no journal"`, not an
  error; that is already a documented limit (README, "Several am
  processes").
- **Torn tail.** Another process may be appending; `ignore_torn_tail=True`
  is mandatory, as for `am watch`. A newline-terminated non-JSON line is
  still `CorruptJournalError` and reports as `"journal unreadable: …"`.
- **Unknown event kinds.** `Journal.read` already skips lines a newer `am`
  wrote (am-watch §3.4); the check inherits that and never raises on one.
- **One definition.** `am status` and the rebuild rail call the same
  `diverging`; neither reimplements the walk.

## 4. Testing

Tier placement follows CLAUDE.md: everything below is `unit` — it drives
`Store`, `replay` and `status_for` against `tmp_path` with a planted
projection and journal, no subprocess of any kind.

`store.diverging` (pure, `tests/test_store.py`):

- A run recorded only through `record_*` and loaded back reports no
  mismatches, at every level, including a run with a resumed subtask
  re-stamped `started` and an attempt re-recorded `harness_error`.
- The 2026-10-03 case: record a run whose newest `run_upsert` is
  `escalated`, then `UPDATE runs SET status='cancelled'` over a raw
  connection. One mismatch: run node, `field: "status"`, `journal:
  "escalated"`, `projection: "cancelled"`, `kind: "foreign"`.
- The crash case: a journal line whose row never landed (the setup of
  `tests/test_store.py:965`). One shape mismatch, `projection: null`,
  `kind: "stale"`.
- A status set back to an earlier journaled value is `stale`, not
  `foreign` (the limit in §3.2, pinned so it is a decision and not an
  accident).
- A row inserted by hand for a subtask no journal line created is a shape
  mismatch, `journal: null`, `kind: "foreign"`.
- Mismatches come out in tree order.

`am status` (`tests/test_cli.py`, the `projection` fixture):

- A clean run's payload has `integrity: {"checked": true, "reason": null,
  "mismatches": []}` and every other key is byte-for-byte what it was.
- The hand-edited `cancelled` run is reported with the one `foreign`
  mismatch, exit code 0, `control.requests` still `[]`.
- The check is read-only: snapshot every table and the journal file's bytes
  before, run `am status` on the diverged run, and assert both are
  unchanged after — the same pattern
  `test_status_shows_the_lease_and_every_lifes_requests_in_seq_order`
  already uses for `control`.
- A run with a live lease reports `checked: false, reason: "lease is live"`
  even when its projection has been edited; the same run with a stale
  heartbeat is checked and reports the mismatch.
- Rows planted with no journal file report `checked: false, reason: "no
  journal"` at exit 0, and no run directory is created under the data
  directory by the call.
- A journal whose last line is torn is checked (the torn line ignored); a
  journal with a newline-terminated non-JSON line reports `"journal
  unreadable: …"` at exit 0.

`rebuild_from_journal` (`tests/test_store.py`):

- The hand-edited `cancelled` run: `rebuild_from_journal(RUN_ID)` raises
  `ProjectionDivergedError` naming the run node and both values, and
  afterwards `runs.status` is still `cancelled` and every other row is
  untouched. With `force=True` it rebuilds and `runs.status` is
  `escalated`.
- The journal-ahead case still rebuilds without `force` (the existing test
  at `965` is the assertion; it must keep passing unchanged).
- Every existing `rebuild_from_journal` test passes unchanged with the
  default `force=False`.

## 5. Risks

- **Scope creep into reconciliation.** The temptation, once a diff exists,
  is to compare every column and then to offer a `--fix`. Section 3.2's
  "status and shape only" and §3.4's "never repair" are the lines; a future
  need to compare more should be its own spec with its own incident behind
  it.
- **Noise on live runs** is handled by §3.5, but a run whose process died
  *inside* a `record_*` (line appended, row never committed) will report a
  `stale` mismatch on every `am status` until it is resumed or rebuilt.
  That is correct — it is exactly the divergence D5 anticipates — but an
  operator who has never seen the key may read `stale` as damage. The
  README paragraph for `integrity` must say in one sentence that `stale`
  means "the journal is ahead, a resume or rebuild moves the projection
  forward" and `foreign` means "something other than `am` wrote this row."
- **The `stale`/`foreign` rule is only as good as the journal.** A journal
  that was itself edited by hand to contain the value makes a foreign edit
  look stale. Nothing here defends against that; the journal is the stated
  truth and this spec takes it at its word.
- **`rebuild_from_journal` has no production caller today**, so the rail
  in §3.6 protects a path only tests and a future command exercise. It is
  still worth adding now, because the first production caller — most likely
  `am reset` or a rebuild option on it — would otherwise inherit the
  un-railed method and the exact failure mode in §1.
