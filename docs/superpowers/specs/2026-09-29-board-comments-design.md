# Outcome comments on brd cards — design addendum

Date: 2026-09-29
Extends: `2026-09-23-agent-manager-design.md` (D5, amended here), `2026-09-25-supervisor-tree-design.md`
(lanes, run end), `2026-09-27-live-control-design.md` (C1 row-only tables, C4 lease token, C9 cancel),
`2026-09-27-multi-process-design.md` (X4 fenced writes, X7 board `ProcessLock`),
`2026-09-27-exactly-once-design.md` (replayed phases)
Status: milestone 12 scope; decisions B1–B10. Builds on milestones 9, 10 and 11 merged.

## 1. Why

During a run the board only ever sees `todo → in_progress → done` (D5). A card left `in_progress` by an
escalation or a cancel says nothing about why, where its work lives, or how to continue; that lives in
`am`'s store, reachable only through `am status`/`am logs` with the right run id. The board is what a
human opens afterwards. This milestone makes `am` leave a short, factual record of each **outcome** on the
card where it happened.

Agreed with the user (brainstorm, 2026-09-29):
- The board is **a record read afterwards**, not a live dashboard; `am status` stays the live view.
- **Outcomes only**: failures and stops on the card they hit, one "done" comment per subtask, one run-end
  comment on the milestone card. No "started" comments, no per-phase notes.
- **Agent text only to explain failures** (capped): the critic's reason, the coder's `blocked_reason`,
  review's unresolved blockers. Done comments are pure facts.
- **On resume, keep the old comment and append the sequel**; nothing is deleted.
- **Cancel** comments on each card it left `in_progress`; **pause** comments only on the milestone card.

## 2. What was found

- `brd comment` has `add <id> <body|-> [--author]`, `list <id>` (oldest first) and `delete <comment-id>`;
  there is **no edit**, and brd mints the comment id (a uuid4), so a caller cannot choose it
  (`~/Code/brd/src/brd/cli/comments.py`, `src/brd/comments.py`; the brd spec lists editing as a non-goal).
  Fields: id, entity_id, author, body, created_at. No metadata column: an idempotency key can only live in
  the body. No size limit (argv, or stdin with `-`). Markdown is stored raw, not rendered. `[[id]]` links
  outside code spans are indexed as refs (backlinks). Comments cascade-delete with their card, are
  included in `brd show` JSON and `--pretty`, `brd export`, and every card in `brd list`/`next` JSON.
- Cards and issues are commentable; documents are not. Synthetic ids `am` uses (`integrate`, `bases`,
  `base-<story>`) are not board cards: their events must land on the milestone or story card.
- `am`'s only board writes today are `board.set_status` and `steps/rollup.py`, under `board.WRITE_LOCK`,
  best-effort (a failure becomes a report warning). M10 wraps that lock in a cross-process board
  `ProcessLock` (X7).
- An escalation skips the remaining TASK steps (`walk._escalate`), so a comment step inside TASK (like
  `mark_done`) would never fire on the path that needs it most, and would change the workflow digest.
  Outcome sites are in code: `orchestrate.lane` (subtask done/escalated/stopped rows, ~l.1053-1083),
  `bases.build`'s failure path, `orchestrate.run_milestone`'s end (done / escalated / Integrate), M9's
  paused/cancelled outcomes (C6), and `cli.run_card`'s end.
- leave-me-alone's orchestrator does not comment on cards (its old GitHub-issue comments were removed when
  it moved to brd), so there is no parity constraint.

## 3. Decisions

**B1 — D5 is amended.** "brd receives status transitions only" becomes: "brd receives status transitions
and a bounded set of code-authored, append-only, idempotent outcome comments." `am` never reads comments
back to decide anything: the board is not a second source of truth.

**B2 — Which events, which card.**

| Event | Card | Content |
|---|---|---|
| subtask done | the subtask | outcome, `(resumed at <phase>)` when this life resumed it, branch, commit count, Plan-Hash, spec and plan paths, verify commands passed, review findings fixed |
| subtask escalated | the subtask | phase, gate/detail, the capped agent reason when one exists, `next:` resume command, `why:` `am logs` command |
| subtask cancelled (M9) | each subtask the cancel left `in_progress` | phase it stopped before, the branch keeping its work, the relaunch command |
| merged base failed | the story the base roots | base branch, detail |
| run end | the milestone card | outcome (done / escalated / paused / cancelled), run id, done N of M, the escalated card and phase, parked cards, integrated branch or Integrate failure, the next command |
| `--card` run end | the subtask only | the subtask comments above; there is no milestone card |

Not posted: started, per-phase, retries, revision loops, gate warnings, subtasks parked because another
card escalated (the run-end comment lists them), pause on subtask cards.

**B3 — Code composes every comment.** `comments.compose_*` are pure functions from outcome data
(`LaneOutcome`, `SubtaskSummary.results`, the run payload) to a body. Agent text enters only through the
failure fields named in §1, quoted and capped. No role gets a brd tool.

**B4 — Format.** Author `am`. Plain text. First line `am · <outcome> · run <run-id>`. Commands in
backticks. Real card ids as `[[id]]` (backlinks); agent text inside double quotes with any `[[` rewritten
to `[ [` so it cannot create refs. Hard cap 1,500 characters per comment: agent text is cut first, then
`… (truncated; see \`am logs …\`)`. Last line `am-key: <key>` (B5).

**B5 — Keys.** Every comment has a key `<run-id>/<card-id>/<event>`, where event is `done`,
`escalated:<lease-token>`, `cancelled`, `base-failed`, or `run-end:<lease-token>`. Lease-token-scoped events
(M9 C4) let each life of a resumed run report its own escalation and its own run end; `done` and
`cancelled` happen once per run and card.

**B6 — An outbox in the store.** A new row-only table (M9 C1 pattern) `board_comments(run_id, card_id,
key PRIMARY KEY, body, state CHECK (state IN ('pending','posted','abandoned')), comment_id, failed_attempts
INTEGER NOT NULL DEFAULT 0, created_at, posted_at)`.
`comments.enqueue(store, ...)` inserts `pending` rows (`INSERT OR IGNORE` on the key) inside
`store.immediate`, fenced by the run's lease (M10 X4) — after the outcome itself is recorded.

**B7 — Flush.** `comments.flush(store, root)` posts every `pending` row of the run, oldest first, each under
the board `ProcessLock` (M10 X7; never while a store transaction is open, X8):
1. `brd comment list <card>`: if a comment's body ends with `am-key: <key>`, mark the row posted with that
   comment id (a crash after posting and before marking cannot double-post);
2. else `brd comment add <card> - --author am` with the body on stdin; mark posted with the returned id.
A brd failure or timeout leaves the row `pending` and adds one report warning. Flush runs right after each
enqueue, at run end, and at the start of `am resume` and of a relaunch (which flushes every run's pending
rows for the milestone's cards).

**B8 — Best-effort, after the fact.** Enqueue happens after the outcome is recorded; flush failures never
escalate, park or change a run's status. A board that is down only delays comments.

**B9 — Replays and resumes post nothing twice.** A phase replayed by M11 or a subtask resumed by `am
resume` reaches the same outcome sites with the same keys (B5) and is absorbed by `INSERT OR IGNORE`
and by the key check in B7.

**B10 — `brd` access stays in `board.py`.** New `board.comment_add(card_id, body, *, author, repo_dir)` →
comment id, and `board.comment_list(card_id, *, repo_dir)` → list of `(id, body)`, alongside `set_status`,
same `BoardError` handling.

## 4. Architecture

```
 outcome site (lane / bases / run_milestone / cancel / run_card)
   │ outcome recorded in the store (unchanged)
   ▼
 comments.compose_<event>(...)  ── pure ──► (key, card_id, body)
   ▼
 comments.enqueue  ── store.immediate, fenced (M10 X4) ──► board_comments row 'pending' (INSERT OR IGNORE)
   ▼
 comments.flush(store, root)   for each pending row, oldest first, under the board ProcessLock:
   board.comment_list(card) ── body ends "am-key: <key>"? ── yes ─► mark posted (id from brd)
                            └─ no ─► board.comment_add(card, body, author="am") ─► mark posted
   BoardError / timeout ─► stays pending, one warning in the report
```

| File | Change |
|---|---|
| `src/agent_manager/board.py` | `comment_add`, `comment_list` (B10) |
| `src/agent_manager/comments.py` | NEW: `compose_*`, `enqueue`, `flush`, caps, escaping, keys |
| `src/agent_manager/store.py` | `board_comments` table; `enqueue_comment`, `pending_comments`, `mark_comment_posted` |
| `src/agent_manager/orchestrate.py` | enqueue + flush at lane outcomes, run end, cancel; flush at resume/relaunch start |
| `src/agent_manager/bases.py` | enqueue on base failure (story card) |
| `src/agent_manager/cli.py` | `run_card` end; `resume_run` flushes first |
| `docs/superpowers/specs/2026-09-23-agent-manager-design.md` | D5 amended (B1) |
| `README.md` | what the board records |

## 5. Error cases

| Case | Behaviour |
|---|---|
| `brd` missing, failing, or timing out | row stays `pending`; one warning per flush; the run is unaffected |
| card deleted from the board | `comment add` fails → `pending` + warning; never retried forever: each failed post increments `failed_attempts`; at 3 the row becomes `abandoned` and one warning names it |
| crash between `comment add` and marking posted | next flush finds `am-key` on the card and marks it; no duplicate |
| crash before enqueue | the outcome is recorded; on resume the outcome site does not re-run, so that comment is lost; accepted (the report and store keep the outcome) |
| lease lost (M10) | enqueue raises `LeaseLostError` like any fenced write; the new owner's flush posts what was enqueued |
| agent text containing `[[…]]`, backticks or secrets-looking strings | `[[` escaped; quoted; capped; only the named failure fields are ever included, never stdout |

## 6. Testing

- `comments.compose_*`: one test per event with golden bodies; the 1,500-char cap cuts agent text first; `[[` escaped; key format per event; `(resumed at <phase>)` present only when resumed.
- Outbox and flush with a fake board: enqueue is idempotent by key; flush posts pending rows in order; a crash injected after `comment_add` and before marking → the next flush finds the key and does not post again; brd down → rows stay pending with a warning, then post on the next flush; a row failing 3 flushes becomes `abandoned` with a warning.
- Orchestrate tests (fake drivers): a clean run → one done comment per subtask and one run-end comment; an escalation → the escalated card and the milestone get comments, parked siblings do not; resume → the old escalation stays and a `done (resumed at <phase>)` is appended; cancel (M9) → each parked card gets a cancel comment; pause → only the milestone card.
- e2e under the fake claude against a real temporary board: `brd comment list` after a clean run, after escalation + resume, and after a cancel.
- Board-down e2e: brd made to fail (a scaffolding env var in the fake's PATH), the run still ends `done` with warnings.

## 7. Deferred

- Comments from runs of leave-me-alone's orchestrator.
- Feeding earlier comments into a later run's prompts.
- Deleting or superseding outdated comments (brd has no edit; the thread is append-only by choice).
- Issues auto-opened on escalation.
