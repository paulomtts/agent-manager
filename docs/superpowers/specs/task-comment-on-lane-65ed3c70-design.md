# Comment on lane outcomes, merged bases and run end — subtask design

Card `65ed3c70-b4e3-473e-a634-f64688ff0ba5`, story `77b0a064` ("Outbox and wiring"), plan Task 2.2 of `docs/superpowers/plans/2026-09-29-board-comments.md`. This narrows the board-comments addendum (`docs/superpowers/specs/2026-09-29-board-comments-design.md`, B2, B5, B7, B8, B9) to the call sites below. Commit: `feat(orchestrate): comment outcomes on the board`.

## Scope

This branch is based on `m12/task-enqueue-and-flush-b5b7b813`, and that base already has `comments.compose_*`, `comments.agent_reason`, `comments.enqueue`, `comments.flush`, `board.comment_add`/`comment_list` and the store's `board_comments` outbox. This subtask only **calls** those functions, at the outcome sites. It changes nothing in `comments.py`, `board.py` or `store.py`.

Every site follows one pattern. First the outcome is recorded exactly as it is today. Then `comments.enqueue(store, comment, run_id=run_id, now=<utc now>)` runs, then `comments.flush(...)`. Each warning that `flush` returns is added to the warnings that reach the run's report. A flush never raises for a board failure (B8), so no outcome, status or return value changes because of the board.

### Sites (in `src/agent_manager/orchestrate.py` unless noted)

1. **Subtask done (`lane`).** This site comes right after `store.record_subtask(story.id, row… "done")`. It calls `compose_done(run_id=…, card_id=subtask.id, summary=result.summary, branch=row's branch, resumed_at=…)`, then enqueues and flushes. `resumed_at` is `runtime.engine.pending_phase(checkpoint)` when the lane passed a checkpoint to the driver as `resume_from`, and `None` otherwise. Flush is a blocking subprocess, so inside the async lane it runs off the event loop, the same way `board.show` does (`asyncio.to_thread`). Its warnings join the lane's `warnings`, so `LaneOutcome.warnings` carries them to the report.
2. **Subtask escalated (`lane`, non-`done`/non-`stopped` summary branch).** This site comes after the subtask and story are recorded `escalated` and before `LaneEscalated` is raised. It calls `compose_escalated(run_id, card_id=subtask.id, token=<this life's lease token>, failed_phase=summary.failed_phase, detail=summary.detail, reason=agent_reason(summary.results, summary.failed_phase))`, then enqueues and flushes. The flush warnings must still reach the report through the raised outcome's `warnings`. `lane` has no lease token today: `run_milestone` holds it (`lease.token` from `cli.run_lease`), so the token has to be threaded down through `supervise`/`SupervisorPlan` to `lane`. The plan stage decides how. The `escalated:<token>` key is what lets a second life that escalates at the same phase post a second comment (Review Focus 3).
3. **Merged base failed.** This covers both `except bases.BaseFailed` sites: the one in `lane` and the one in `base_only_lane`. It applies only when `error.stopped` is false. It runs after the escalation is recorded and before `LaneEscalated` is raised. It calls `compose_base_failed(run_id, story_id=story.id, base_branch=root_plan.branch, detail=error.detail)` on the **story** card, then enqueues and flushes. In `base_only_lane` there is no store row, but the comment still goes on the story card. The site lives in orchestrate rather than inside `bases.build` for three reasons: orchestrate is where `stopped` versus escalated is decided, both lanes are covered by one pattern, and `tests/test_orchestrate.py`'s `fake_bases` fixture replaces `bases.build` wholesale. `bases.py` is therefore expected to stay unchanged. If the implementer finds a reason to touch it after all, the matching test goes in `tests/test_bases.py`.
4. **Run end (`run_milestone`).** Three exits get a comment:
   - the lane-escalation branch (`status="escalated"`, including when `control: "pause"` is added);
   - the Integrate-escalation branch (`integrate_escalated_payload`);
   - the `done` branch.

   In each case the site comes after `store.record_run(...)`. It builds the report payload and calls `compose_run_end(run_id, milestone_id=milestone_card.id, token=lease.token, payload=<that payload plus "total": the milestone's subtask count>)`. `total` goes only into the dict handed to `compose_run_end` and must not change the report's shape. The site then enqueues, flushes, and adds the flush warnings to the returned payload's `warnings` before returning.

   Three exits get no run-end comment here:
   - **Cancel.** This branch belongs to sibling `5d9a875f`.
   - **Pure pause.** This branch also belongs to sibling `5d9a875f`.
   - **An exception from Integrate.** The run is never recorded, so it stays unrecorded and uncommented.
5. **Flush at start (`run_milestone`, fresh and resume).** This runs once the lease is held (so the fenced `mark_comment_posted` writes are allowed) and before `supervise` drives anything. It calls `flush(store, root, card_ids=<milestone card id + every story id + every subtask id of plan.stories>)`, which posts any run's leftover pending rows for this milestone's cards (B7, relaunch). Its warnings start the run's `warnings` list. Every refusal still happens before `Store.open`, exactly as today.
6. **`cli.resume_run`.** A milestone resume already goes through `run_milestone(resume_run_id=…)` and gets the flush from item 5. A `task`-workflow resume (`_resume_from_checkpoint`) flushes that run's pending rows (`run_id=run.id`) once its lease is held and before the walk continues. In both paths, refusals (unknown, cancelled or live run) stay before any store write and are unchanged.

### Explicitly not commented

- Lanes that are parked or `stopped` because another card escalated. The run-end comment lists them.
- Started events, per-phase events, retries, revision loops and gate warnings.
- The `lane` catch-all escalation (a lane bug with no `SubtaskSummary` and no phase). The run-end comment still records it.
- Cancel, pause-only and `run_card` outcomes, which belong to `5d9a875f`.
- Out-of-scope items from addendum §7.

## Observable behavior

- A clean run leaves exactly one `am · done` comment on each subtask it drove, and one `am · done · run <id>` run-end comment on the milestone card. Every body is authored `am` and ends `am-key: <key>`.
- When a run escalates, the escalated subtask gets an `am · escalated` comment and the milestone gets an escalated run-end comment. Parked siblings and their stories get nothing.
- Resuming after the fix leaves the first life's escalation comment in place. It adds `done` on the subtask with `(resumed at review)`, plus the second life's run-end comment, which has a distinct `run-end:<token>` key.
- A replay or resume that reaches a site with the same key posts nothing a second time (B9; handled by the outbox).
- When the board is down, the outcome, status and return value stay as they would be without comments. Only `warnings` gains the flush's warnings.

## Error paths

- A `BoardError` or lock timeout during a flush is turned into a warning inside `flush`, and the run carries on.
- `LeaseLostError` from `enqueue` or `flush` propagates like any fenced write (it is a `BaseException` and is never caught by the lane catch-all). Existing lease-loss behavior is unchanged.
- An unknown or partial payload in `compose_run_end` reads as `ended` and never raises (already in `comments.py`).
- `summary.failed_phase` is never `None` on the non-`done`/non-`stopped` branch: `runtime/walk.py`'s `_escalate` is the only path that sets a summary's status to `escalated`, and it always sets `failed_phase` to the phase name in the same call. So `compose_escalated`/`agent_reason` (both take a `str`) always get one. The lane's own catch-all escalation (no `SubtaskSummary`, Exception branch) stays uncommented, as already stated above. The rule is "no crash, no agent text beyond `_REASON_FIELDS`".

## Tests

Test placement: addendum §6 places these scenarios in **orchestrate tests driven by fake drivers**. Design §14 says "Engine — driven with a fake adapter" and "Steps — against … a temporary `brd` board; no network". So every test below goes in **`tests/test_orchestrate.py`**, at the existing fake-driver tier, on that module's temporary `brd` board (skipped when `brd` is absent, as today). Comments are read back with `board.comment_list` or from the store outbox. A fake `board_api` (or monkeypatched `board.comment_add`/`comment_list`/`write_lock`) is used only where a board failure is injected. The `tests/e2e` real-board proofs belong to Task 3.1 and are not written here. `tests/conftest.py`'s data-directory isolation guard must stay green.

1. A clean two-story run gives one done comment per subtask, each keyed `<run>/<subtask>/done`, plus exactly one run-end comment on the milestone card. No other card gets a comment. (`tests/test_orchestrate.py`, fake-driver tier.)
2. In an escalation with a parked sibling, comments appear only on the escalated subtask (`escalated:<token>` key, `phase:` line, the agent's `_REASON_FIELDS` reason quoted) and the milestone card (escalated run-end). The parked sibling and its story have none. (`tests/test_orchestrate.py`, fake-driver tier.)
3. Resume after the fix: the escalation comment is still there, and a done comment containing `(resumed at review)` is added. (`tests/test_orchestrate.py`, fake-driver tier with a checkpoint whose pending phase is `review`.)
4. A second life that escalates at the same phase gives two escalation comments on the subtask, with distinct token-scoped keys (Review Focus 3). (`tests/test_orchestrate.py`, fake-driver tier.)
5. A failed merged base (`fake_bases` returns `BaseFailed("…")`, not stopped) gives a `base-failed` comment on the story card, in both the `lane` and the `base_only_lane` cases. `BaseFailed(stopped=True)` gives none. (`tests/test_orchestrate.py`, `fake_bases` tier.)
6. An Integrate escalation gives a milestone run-end comment carrying `integrate failed at …`. (`tests/test_orchestrate.py`, fake-driver tier with `integration.integrate_milestone` replaced.)
7. With the board down for the run, the run still ends `done`, its status and return value match a board-up run except that `warnings`, and the rows stay pending. A later `run_milestone`/`am resume` start flush posts them. (`tests/test_orchestrate.py`, fake-driver tier with a failing fake board.)

Existing tests that assert exact `warnings` lists while the board is faked to fail, or with no real board, may now see flush warnings. Those assertions get updated only where the warnings genuinely come from the new flushes. No behavior assertion is loosened. `uv run pytest`, the whole suite including `tests/e2e`, must be green.
