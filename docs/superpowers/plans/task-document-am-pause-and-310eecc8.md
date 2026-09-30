<!-- task-pipeline: validated -->
# Document `am pause` and `am cancel` — subtask design

Card: `310eecc8-9f74-4610-86d0-5b0e1907228f`, story `49e7aa15` ("Prove and document live control"), milestone `bdc5838b`. Plan: Task 3.2 of `docs/superpowers/plans/2026-09-27-live-control.md`. Decisions: C1–C12 of `docs/superpowers/specs/2026-09-27-live-control-design.md`.

Documentation only. No source file and no test file changes. Every statement in the README is taken from the code in this worktree, not from the addendum; where the two differ (for example the `NotAcceptingError` message), the code wins.

## Scope

Three files change:

1. `README.md` — new section, edits to existing sections.
2. `docs/superpowers/specs/2026-09-23-agent-manager-design.md` — a pointer to the live-control addendum.
3. `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` — a "Superseded by" note in §10.

Not touched: `tests/e2e/test_live_control.py` (sibling d147d97a owns it), anything under `src/`.

Note on the addendum file: `2026-09-27-live-control-design.md` and the plan are not in this worktree's tree (they live on the `docs-live-control` worktree). The pointers name the file by path, as the existing pointers do; they resolve once that branch is merged. Do not copy the addendum into this branch.

Names moved since `6d69e3a` as listed on the card (report this in the result): `request_control` is at `cli.py:1776`, `_controllable_lease` at `cli.py:1681`, the `pause`/`cancel` commands at `cli.py:1833-1854`, the resume refusals at `cli.py:1579-1590`, `card_run_status` at `cli.py:793`. `controlled_payload` (`orchestrate.py:160`) and the outcome precedence (`orchestrate.py:1468-1483`) are where the card says.

## README: new section "Pausing and cancelling a run"

A `####` section under "Milestone runs" (Usage), placed after "Relaunching resumes" and before "Not there yet". It must state, from the code:

- **Commands.** `am pause <run-id>` and `am cancel <run-id>`, each with `--repo-dir` (default `.`) and `--pretty`, run from another terminal while the run is going. There is no `--wait`: the command only records the request and returns.
- **Channel.** The request is a row in the repository's SQLite projection; the running process polls for it (about once a second) and holds a lease with a heartbeat (every 5 s). A lease whose heartbeat is older than 30 s, or whose pid is gone on the same host, is dead. No signal, socket or fifo.
- **Success envelope, exit 0.** `data` is `{"run_id", "command", "effective", "requested_at", "already_requested", "message"}`. Give one example (a pause). Repeating a request is a no-op that still exits 0, with `already_requested: true` and the earlier `requested_at`: a pause is covered by any earlier pause or cancel, a cancel only by an earlier cancel, so a cancel after a pause is recorded and upgrades it. `effective` is `cancel` once any cancel is recorded for this life of the run, else `pause`. A request made to an earlier life (before a resume) does not count.
- **What happens.** A phase already running is never interrupted: every lane finishes its in-flight phase and parks before its next one, the same park the escalation stop uses. No new story starts. Integrate does not run in this invocation.
- **Paused report**, `am run`/`am resume` exit 0: `paused: true`, `run_id`, `stopped` (rows `{"story", "subtask", "before_phase"}`, as in the escalation report), `completed`, `pending` (ids of stories that never started), `warnings`, `resume: "am resume <run-id>"`; plus `bases` and `resumed` under the same rules as every other shape. The run is recorded `stopped`; `am resume <run-id>` continues it and redoes no finished phase.
- **Cancelled report**, exit 0: `cancelled: true` with the same keys minus `resume`, plus `escalations` (escalation rows, primary first) only when a lane also escalated. The run is recorded `cancelled`. Neither shape has an `escalated` key.
- **Precedence.** A cancel always wins, even over an escalation. Otherwise an escalation wins: the ordinary escalated report, exit 1, with `control: "pause"` added when a pause had also been requested. Otherwise a pause gives the paused report.
- **`am status`'s `control` key**, always present: `{"lease": {"pid", "host", "acquired_at", "heartbeat_at", "accepting", "live"} | null, "requests": [{"command", "requested_at", "handled_at"}]}`, requests from every life of the run in order. `live` is computed when read.
- **Refusals**, each `{"ok": false, "error": {"type", "message"}}` at exit 3 with nothing recorded, checked in this order: `UnknownRunError` (run not in the projection); `NotRunningError` (run not `started`; the message names its status); `DeadRunError` (recorded `started` but no lease, or a dead lease; `am resume` picks it up); `NotAcceptingError` (the run is finishing: its window closes once the lanes or the card walk end, so a milestone run in Integrate cannot be paused or cancelled).
- **Ctrl-C vs pause vs cancel.** Ctrl-C kills the running harness processes, and `am resume` redoes every phase that was in flight. Pause redoes nothing: finished phases stand, and `am resume` continues each parked subtask from its checkpoint. Cancel parks the same way but closes the run for good: `am resume` refuses it, and a relaunch (`am run --milestone`) ignores the cancelled run's checkpoints, so a card it parked starts again from its first phase. Cancel does not reset board cards.
- **`am resume` refusals**, exit 3, before anything is written: a `cancelled` run gives `NotResumableError`; a run whose lease is still live gives `RunIsLiveError` (wait for it to exit, or check `am status`).
- **`--card` runs too.** A pause parks the walk before its next phase; the report's `status` is `stopped` (exit 0), and it can be resumed. A cancel parks it the same way and the report's `status` is `cancelled` (exit 0), even when the walk escalated; the story and subtask rows keep the walk's own status.

## README: edits to existing sections

- **Parallel runs**, the "How a run stops" paragraph (line 174) and the "To continue" paragraph (line 183): add `am pause`/`am cancel` as another way to set the stop, with a link to the new section. Leave the escalation wording as it is.
- **Integrate** (line 125): "an escalation or a stop ends the run before it" can stay. Add that a pause or cancel also keeps Integrate from running, and that Integrate itself cannot be paused.
- **What an escalation report contains**: mention the `control: "pause"` key.
- **Relaunching resumes** (lines 258-278): the opening sentence adds a paused run. Both refusal lists, milestone and `task`, gain "the run was cancelled" (`NotResumableError`) and "the run is still live in another process" (`RunIsLiveError`). Note that a relaunch after a cancel starts its parked cards afresh.
- **Not there yet** (line 282): `cancel` now exists, so the line becomes "`watch` and `retry` do not exist." Add a line saying `am pause --wait`, pausing Integrate, and pausing a single story do not exist.

## Spec pointers

- `2026-09-23-agent-manager-design.md`: after the "Status (milestone 3)" paragraph under the CLI synopsis (ends line 426), add a "**Status (milestone 9):**" paragraph in the style of line 436: `pause` and `cancel` exist as the live-control addendum, `2026-09-27-live-control-design.md`, specifies; `watch` and `retry` remain deferred. The synopsis at line 406 lists `cancel` but not `pause`; the pointer names both, and the synopsis stays as it is.
- `2026-09-25-supervisor-tree-design.md` line 250: after the live `am pause`/`am cancel` item, add "(superseded by `2026-09-27-live-control-design.md`)". The other items on the line stay.

## Error paths

None at runtime; this is prose. The failure mode to guard against is inaccuracy: saying Integrate can be paused, inventing `--wait`, saying pause or cancel exits nonzero, giving a control an `escalated` key or a `failed_phase`, or suggesting a cancel resets board cards.

## Tests

No new tests. §14 "Testing" of the agent-manager design sorts tests into pure-function unit tests, step tests against temp git repos and a temp board, adapter `build_command` purity tests, fake-adapter engine tests, and one opt-in real-harness end-to-end test. The repo also has a fake-claude e2e tier under `tests/e2e/`. Documentation fits none of them, and no existing test reads `README.md` or the spec files. The e2e proof of the behaviour documented here is sibling d147d97a's `tests/e2e/test_live_control.py` (fake-claude e2e tier), not this card's.

Verification: `uv run pytest` passes with the same test count. Commit: `git commit -m "docs: am pause and am cancel"`.

Upstream note: the exploration summary was cut off at 8000 characters partway through the test-placement rule. The tiers above come from reading §14 directly, not from filling in the missing text.

---

# Document `am pause` and `am cancel` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document the live-control commands `am pause` and `am cancel` in `README.md`, and add pointers to the live-control addendum in the two design specs, without touching any source or test file.

**Architecture:** Documentation only. Task 1 adds the new README section "Pausing and cancelling a run". Task 2 updates the README sections that currently say only Ctrl-C or an escalation stops a run. Task 3 adds the two spec pointers, runs the full suite and makes the card's single commit. There is no code, so each task's "RED" step is a `grep` that shows the required text is missing, and its "GREEN" step is the same `grep` finding it.

**Tech Stack:** Markdown; `uv run pytest` (Python 3, pytest) as the regression check; `git`.

**Spec:** `docs/superpowers/specs/task-document-am-pause-and-310eecc8-design.md` (reproduced above).

## Global Constraints

- Work in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m9/task-document-am-pause-and-310eecc8` on branch `m9/task-document-am-pause-and-310eecc8`. Every relative path below is relative to that worktree. Nothing is pushed, and the base branch never moves.
- Only three files change: `README.md`, `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` (plus this plan file, already written). Nothing under `src/` or `tests/`. Do not create or touch `tests/e2e/test_live_control.py` (sibling d147d97a owns it).
- Do not copy `2026-09-27-live-control-design.md` into this branch. The pointers name it by path; it resolves once the `docs-live-control` branch is merged.
- Every README statement comes from the code in this worktree, not the addendum. Where they differ, the code wins.
- The README must not claim Integrate can be paused or cancelled, must not document a `--wait` flag as existing, must not say `am pause`/`am cancel` or a paused/cancelled report exits nonzero, must not give a controlled report an `escalated` key or a top-level `failed_phase`, and must not say a cancel resets board cards.
- The README describes SQLite as the only channel: no signal, socket or fifo.
- Timings, verbatim from `src/agent_manager/control.py`: `CONTROL_POLL_SECONDS = 1.0`, `HEARTBEAT_SECONDS = 5.0`, `LEASE_STALE_SECONDS = 30.0`.
- Exit codes, verbatim from `src/agent_manager/cli.py`: every refusal is `EXIT_ERROR = 3`; `EXIT_ESCALATED = 1` only when the payload is `escalated`; everything else 0.
- One commit for the whole card, at the end of Task 3, with exactly: `git commit -m "docs: am pause and am cancel"`. Tasks 1 and 2 do not commit.
- `uv run pytest` must pass with the same number of tests as before any edit (recorded in Task 1 Step 1).
- No hard-wrapped prose in new text: each new paragraph or bullet is one line, as the newer README paragraphs already are.
- Names moved since `6d69e3a` (report in the result): `request_control` is at `cli.py:1776`, `_controllable_lease` at `cli.py:1681`, `pause`/`cancel` at `cli.py:1833-1854`, the resume refusals at `cli.py:1579-1590`, `card_run_status` at `cli.py:793`. `controlled_payload` (`orchestrate.py:160`) and the outcome precedence (`orchestrate.py:1468-1483`) are where the card says.

## Review Focus

- **A reader tries to pause a run that is in Integrate.** They should read that it is refused with `NotAcceptingError` and that Integrate cannot be paused or cancelled; nothing may suggest otherwise. Pinned by Task 1 Step 5 and Task 2 Step 5 (`grep` for the Integrate sentences).
- **A script waits for the pause to land using `--wait`.** The README must say there is no `--wait` and list it under "Not there yet"; `--wait` must appear nowhere else. Pinned by Task 2 Step 5 (`grep -n -- '--wait' README.md` shows exactly two lines).
- **A script branches on the exit code of a paused or cancelled run.** It must read exit 0 for both reports and for the commands, exit 1 only for the escalated report, and exit 3 for refusals. Pinned by Task 1 Step 5 (`grep` for the exit-code sentences).
- **A reader cancels expecting the board to be reset.** The README must say a cancel does not reset board cards and that a relaunch starts the parked card from its first phase. Pinned by Task 1 Step 5.
- **The new cross-links are broken.** Every `(#pausing-and-cancelling-a-run)` link must match the heading `#### Pausing and cancelling a run`. Pinned by Task 2 Step 5 (link count and heading count).

---

### Task 1: New README section "Pausing and cancelling a run"

**Files:**
- Modify: `README.md:280` (insert a new `####` section immediately before the existing `#### Not there yet` heading, i.e. after "Relaunching resumes")

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the heading `#### Pausing and cancelling a run`, whose GitHub anchor is `#pausing-and-cancelling-a-run`. Task 2 links to that anchor from four places.

- [ ] **Step 1: Record the baseline test count**

Run: `uv run pytest -q 2>&1 | tail -n 1`
Expected: a single summary line such as `N passed in ...s` (possibly with `M skipped`). Write down N (and M). Task 3 checks that the same numbers come back.

- [ ] **Step 2: Show the section is missing (RED)**

Run: `grep -c '^#### Pausing and cancelling a run$' README.md`
Expected: `0` (grep exits 1).

- [ ] **Step 3: Insert the section before "Not there yet"**

Use Edit on `README.md`. `old_string` (unique; it is the heading at line 280):

```markdown
#### Not there yet
```

`new_string` (the whole new section followed by the unchanged heading):

````markdown
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

A cancelled milestone run exits 0, and `data` holds `cancelled` (`true`) and the same keys as a paused one except `resume`. When a lane also escalated, it adds `escalations`, a list of `{"level", "story", "subtask", "failed_phase", "detail"}` with the primary escalation first. The run is recorded `cancelled`. Neither shape has an `escalated` key or a top-level `failed_phase`: a pause or a cancel is not a failure.

Which report you get when more than one thing happened:

1. A cancel always wins, even over an escalation: the cancelled report, exit 0.
2. Otherwise an escalation wins: the ordinary [escalation report](#what-an-escalation-report-contains), exit 1, with `control: "pause"` added when a pause had also been requested.
3. Otherwise a pause gives the paused report, exit 0.

`am status <run-id>` always has a `control` key: `{"lease": {"pid", "host", "acquired_at", "heartbeat_at", "accepting", "live"} or null, "requests": [{"command", "requested_at", "handled_at"}]}`. `requests` lists the requests from every life of the run, in the order they were made, and `handled_at` is `null` until the run has acted on one. `live` is worked out when `am status` reads the lease; it is not stored. `accepting` turns `false` when the run is finishing.

A request is refused, with `{"ok": false, "error": {"type", "message"}}`, exit code 3 and nothing recorded, in this order:

- `UnknownRunError`: the run id is not in the repository's projection. `am runs` lists the ones that are.
- `NotRunningError`: the run is not `started`. The message names its status. A stopped or escalated run wants `am resume`, and a finished one wants nothing.
- `DeadRunError`: the run is recorded `started`, but no process holds its lease, or the lease is dead. Nobody is left to act on a request. `am resume <run-id>` picks the run up.
- `NotAcceptingError`: the run is finishing. A milestone run stops accepting requests once its lanes have ended, and a `--card` run once its walk has ended, so a milestone run in [Integrate](#integrate) cannot be paused or cancelled.

Ctrl-C, pause and cancel are not the same:

- **Ctrl-C** kills the running harness processes. `am resume` then runs again every phase that was in flight.
- **Pause** kills nothing. Finished phases stand, and `am resume` continues each parked subtask from its checkpoint.
- **Cancel** parks the same way, but closes the run for good. `am resume` refuses it. A relaunch with `am run --milestone` ignores the cancelled run's checkpoints, so a subtask the cancel parked starts again from its first phase. A cancel does not reset board cards: they keep whatever status the run left them in.

`am resume` refuses, with exit code 3 and before anything is written, a run that was cancelled (`NotResumableError`) and a run whose lease is still live in another process (`RunIsLiveError`: wait for that process to exit, or check `am status <run-id>`).

On a `--card` run, a pause parks the walk before its next phase. The report's `status` is `stopped`, it exits 0, and `am resume <run-id>` continues it. A cancel parks it the same way, and the report's `status` is `cancelled`, exit 0, even when the walk escalated. In both cases the story and subtask rows keep the walk's own status.

#### Not there yet
````

- [ ] **Step 4: Show the section is there (GREEN)**

Run: `grep -c '^#### Pausing and cancelling a run$' README.md`
Expected: `1`.

- [ ] **Step 5: Check the accuracy guards in the new section**

Run each command; each must print the expected result.

```bash
grep -c 'There is no `--wait`' README.md
grep -c 'so a milestone run in \[Integrate\](#integrate) cannot be paused or cancelled' README.md
grep -c 'A cancel does not reset board cards' README.md
grep -c 'A paused milestone run exits 0' README.md
grep -c 'A cancelled milestone run exits 0' README.md
grep -c 'Neither shape has an `escalated` key or a top-level `failed_phase`' README.md
grep -c 'There is no signal, socket or fifo' README.md
grep -c 'every 5 seconds' README.md
grep -c 'older than 30 seconds' README.md
```

Expected: `1` for every line.

Also confirm the section sits between "Relaunching resumes" and "Not there yet":

Run: `grep -n '^#### ' README.md`
Expected: `#### Relaunching resumes`, then `#### Pausing and cancelling a run`, then `#### Not there yet`, in that order and adjacent in the list.

No commit here (Global Constraints: one commit at the end of Task 3).

---

### Task 2: Update the existing README sections

**Files:**
- Modify: `README.md:125` (Integrate)
- Modify: `README.md:174` and `README.md:183` (Parallel runs)
- Modify: `README.md:243` (What an escalation report contains, after the `bases` bullet)
- Modify: `README.md:258-278` (Relaunching resumes)
- Modify: `README.md:282` (Not there yet)

Line numbers are before Task 1's insertion; Task 1 only inserts text below line 279, so lines 125-278 are unchanged and line 282 has moved down. Every Edit below matches on text, not line number.

**Interfaces:**
- Consumes: the anchor `#pausing-and-cancelling-a-run` from Task 1.
- Produces: nothing later tasks rely on.

- [ ] **Step 1: Show the old wording is still there (RED)**

```bash
grep -c '`watch`, `retry` and `cancel` do not exist.' README.md
grep -c '(#pausing-and-cancelling-a-run)' README.md
```

Expected: `1`, then `0`.

- [ ] **Step 2: Edit Integrate and Parallel runs**

Edit 1, `README.md` Integrate. `old_string`:

```markdown
It runs only when every story finished clean: an escalation or a stop ends the run before it.
```

`new_string`:

```markdown
It runs only when every story finished clean: an escalation or a stop ends the run before it. An `am pause` or `am cancel` also keeps Integrate from running in that invocation, and Integrate itself cannot be paused or cancelled: once the lanes have ended, the run no longer accepts either (see [Pausing and cancelling a run](#pausing-and-cancelling-a-run)).
```

Edit 2, `README.md` Parallel runs, "How a run stops". `old_string`:

```markdown
A story whose blocker escalated or stopped never starts, and stays `pending`. Integrate does not run.
```

`new_string`:

```markdown
A story whose blocker escalated or stopped never starts, and stays `pending`. Integrate does not run. `am pause` and `am cancel`, run from another terminal, set the same stop without anything failing; see [Pausing and cancelling a run](#pausing-and-cancelling-a-run).
```

Edit 3, `README.md` Parallel runs, "To continue". `old_string`:

```markdown
Either way the stopped subtask picks up where it parked, and every card already `done` on the board is skipped.
```

`new_string`:

```markdown
Either way the stopped subtask picks up where it parked, and every card already `done` on the board is skipped. After an `am pause` there is nothing to fix: run `am resume <run-id>`. A cancelled run cannot be resumed, only relaunched (see [Pausing and cancelling a run](#pausing-and-cancelling-a-run)).
```

- [ ] **Step 3: Edit the escalation report and Relaunching resumes**

Edit 4, `README.md` What an escalation report contains. `old_string`:

```markdown
- `bases`: the merged bases built before the run stopped, as on a clean run.
```

`new_string`:

```markdown
- `bases`: the merged bases built before the run stopped, as on a clean run.

When an `am pause` was requested and a lane then escalated, the escalation wins and this report gains `control` (`"pause"`); it still exits 1. A cancel wins over an escalation instead, and gives the cancelled report (see [Pausing and cancelling a run](#pausing-and-cancelling-a-run)).
```

Edit 5, `README.md` Relaunching resumes, opening sentence. `old_string`:

```markdown
To go on after an escalation, a stopped lane or a killed run, fix the cause
```

`new_string`:

```markdown
To go on after an escalation, a stopped lane, a paused or cancelled run, or a killed run, fix the cause
```

Edit 6, `README.md` Relaunching resumes, end of the first paragraph. `old_string`:

```markdown
Relaunching after an Integrate escalation runs Integrate again, so commit your fix in the integration worktree first.
```

`new_string`:

```markdown
Relaunching after an Integrate escalation runs Integrate again, so commit your fix in the integration worktree first. A relaunch after an `am cancel` ignores the cancelled run's checkpoints, so a subtask the cancel parked starts again from its first phase.
```

Edit 7, `README.md` milestone resume exit codes. `old_string`:

```markdown
It exits 0 when the milestone finished and 1 when it escalated again.
```

`new_string`:

```markdown
It exits 0 when the milestone finished, was paused or was cancelled, and 1 when it escalated again.
```

Edit 8, `README.md` milestone resume refusals. `old_string`:

```markdown
- the run is `done`. Start new work with `am run --milestone`.
```

`new_string`:

```markdown
- the run is `done`. Start new work with `am run --milestone`.
- the run was cancelled (`NotResumableError`). Start new work with `am run --milestone`.
- the run is still live: another process holds its lease and its heartbeat is fresh (`RunIsLiveError`). Wait for that process to exit, or check `am status <run-id>`.
```

Edit 9, `README.md` `task` resume exit codes. `old_string`:

```markdown
A resumed walk that ends `done` or `stopped` exits 0, and one that escalates exits 1.
```

`new_string`:

```markdown
A resumed walk that ends `done`, `stopped` or `cancelled` exits 0, and one that escalates exits 1 (unless a cancel was requested, which wins).
```

Edit 10, `README.md` `task` resume refusals. `old_string`:

```markdown
- the run has no subtask recorded `started` or `stopped`, or more than one of them.
```

`new_string`:

```markdown
- the run has no subtask recorded `started` or `stopped`, or more than one of them.
- the run was cancelled (`NotResumableError`). Start a fresh `am run --card`.
- the run is still live: another process holds its lease and its heartbeat is fresh (`RunIsLiveError`). Wait for that process to exit, or check `am status <run-id>`.
```

- [ ] **Step 4: Edit Not there yet**

Edit 11, `README.md`. `old_string`:

```markdown
- `watch`, `retry` and `cancel` do not exist.
```

`new_string`:

```markdown
- `watch` and `retry` do not exist.
- `am pause --wait` does not exist, Integrate cannot be paused or cancelled, and there is no way to pause a single story: a pause or cancel always applies to the whole run.
```

- [ ] **Step 5: Show the new wording is there (GREEN) and the links resolve**

```bash
grep -c '`watch`, `retry` and `cancel` do not exist.' README.md
grep -c '`watch` and `retry` do not exist.' README.md
grep -o '(#pausing-and-cancelling-a-run)' README.md | wc -l
grep -c '^#### Pausing and cancelling a run$' README.md
grep -c 'NotResumableError' README.md
grep -c 'RunIsLiveError' README.md
grep -n -- '--wait' README.md
grep -c 'Integrate itself cannot be paused or cancelled' README.md
```

Expected, in order:

1. `0` (the old "Not there yet" line is gone).
2. `1`.
3. `4`: Edits 1, 2, 3 and 4 each add one `(#pausing-and-cancelling-a-run)` link, and Task 1 adds none (it links to `#parallel-runs`, `#what-an-escalation-report-contains` and `#integrate`).
4. `1` (the anchor's heading exists exactly once).
5. `3`: Task 1's `am resume` refusal sentence, plus Edits 8 and 10.
6. `3`: the same three places.
7. Exactly two lines: Task 1's "There is no `--wait`" paragraph and the "Not there yet" bullet from Edit 11.
8. `1` (Edit 1).

No commit here (Global Constraints).

---

### Task 3: Spec pointers, full verification and the commit

**Files:**
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:424-426` (insert a paragraph after the "Status (milestone 3)" paragraph)
- Modify: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md:250`

**Interfaces:**
- Consumes: Tasks 1 and 2's `README.md` edits (committed together here).
- Produces: the card's single commit.

- [ ] **Step 1: Show the pointers are missing (RED)**

```bash
grep -c 'Status (milestone 9)' docs/superpowers/specs/2026-09-23-agent-manager-design.md
grep -c 'superseded by `2026-09-27-live-control-design.md`' docs/superpowers/specs/2026-09-25-supervisor-tree-design.md
```

Expected: `0`, then `0`.

- [ ] **Step 2: Add the "Status (milestone 9)" paragraph**

Edit `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. `old_string` (lines 424-426, unique):

```markdown
`--max-concurrent` lanes (default 4); see P1 of the parallel-stories addendum,
`2026-09-24-parallel-stories-design.md`. Deferred: `watch`, `retry`, `cancel`,
`--workflow`, `--harness`, and a milestone-aware `resume`. See section 4 of the orchestration addendum,
`2026-09-24-orchestration-design.md`.
```

`new_string`:

```markdown
`--max-concurrent` lanes (default 4); see P1 of the parallel-stories addendum,
`2026-09-24-parallel-stories-design.md`. Deferred: `watch`, `retry`, `cancel`,
`--workflow`, `--harness`, and a milestone-aware `resume`. See section 4 of the orchestration addendum,
`2026-09-24-orchestration-design.md`.

**Status (milestone 9):** `pause` and `cancel` exist, as the live-control addendum, `2026-09-27-live-control-design.md`, specifies: `am pause <run-id>` parks a running run at its next phase boundary for `am resume`, and `am cancel <run-id>` stops it there and closes it for good. The synopsis above lists `cancel` but not `pause`; both exist. `watch` and `retry` remain deferred.
```

- [ ] **Step 3: Add the supersession note in the supervisor-tree addendum**

Edit `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`. `old_string` (line 250):

```markdown
- Verification discovery (orchestrator.js's Detect); live `am pause`/`am cancel`; `watch`/`retry`.
```

`new_string`:

```markdown
- Verification discovery (orchestrator.js's Detect); live `am pause`/`am cancel` (superseded by `2026-09-27-live-control-design.md`); `watch`/`retry`.
```

- [ ] **Step 4: Show the pointers are there (GREEN)**

```bash
grep -c 'Status (milestone 9)' docs/superpowers/specs/2026-09-23-agent-manager-design.md
grep -c 'superseded by `2026-09-27-live-control-design.md`' docs/superpowers/specs/2026-09-25-supervisor-tree-design.md
```

Expected: `1`, then `1`.

- [ ] **Step 5: Confirm only the three files (and this plan) changed**

Run: `git status --porcelain`
Expected: modified `README.md`, `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`, plus the untracked spec and plan under `docs/superpowers/specs/task-document-am-pause-and-310eecc8-design.md` and `docs/superpowers/plans/task-document-am-pause-and-310eecc8.md` if they are not yet committed. Nothing under `src/` or `tests/`. No `docs/superpowers/specs/2026-09-27-live-control-design.md`.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q 2>&1 | tail -n 1`
Expected: the same `N passed` (and `M skipped`) as Task 1 Step 1, no failures, no errors.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-23-agent-manager-design.md docs/superpowers/specs/2026-09-25-supervisor-tree-design.md docs/superpowers/specs/task-document-am-pause-and-310eecc8-design.md docs/superpowers/plans/task-document-am-pause-and-310eecc8.md
git commit -m "docs: am pause and am cancel"
```

Expected: one commit on `m9/task-document-am-pause-and-310eecc8`. Do not push.
