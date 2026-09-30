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
