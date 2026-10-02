# Comment on pause, cancel and `--card` runs (card 5d9a875f)

Subtask of story 77b0a064 "Outbox and wiring", milestone 21f4cf06. Narrows Task 2.3 of `docs/superpowers/plans/2026-09-29-board-comments.md`. Base: the state left by the done siblings b5b7b813 (outbox enqueue/flush) and 65ed3c70 (lane, base and clean/escalated run-end comments). Branch prefix `m12`.

## Scope

Wire the existing composers into the three run exits that still post nothing. Only two files change: `src/agent_manager/orchestrate.py` (`run_milestone`'s cancel and pause-only branches) and `src/agent_manager/cli.py` (`run_card`). `comments.py`, `board.py`, `store.py` and the outbox machinery are not touched; the composers (`compose_cancelled`, `compose_run_end`, `compose_done`, `compose_escalated`, `agent_reason`) and the `post_comment` helper are reused as built.

Out of scope (owned by 65ed3c70, already done): lane done/escalated comments, base-failed comments, run-end on clean, Integrate-escalated and lane-escalated exits (including escalated-with-pause), resume flushing of pending rows. Their tests are not duplicated.

## Observable behavior

All comments are posted only after the outcome they describe is recorded (B2), through `post_comment` (enqueue, then flush by card). Every flush warning is appended to the returned payload's `warnings`.

1. **Milestone cancel** (`stop.requested == "cancel"` in `run_milestone`, after `store.record_run(... "cancelled")`):
   - For each `LaneOutcome` with `kind == "stopped"` and a non-`None` `subtask`, in the order `outcomes` already has (wave order, census order within a wave): post `comments.compose_cancelled(run_id=run_id, card_id=outcome.subtask, before_phase=outcome.before_phase, branch=<that subtask's branch>, relaunch=f"am run --milestone {milestone_card.id}")`. The branch is the subtask's recorded branch, read off the `rows` mapping `record_plan` already built for this run: `rows[outcome.story][1][outcome.subtask].branch` (the `SubtaskRun` row's `branch` field — not a fresh `dag.subtask_branch(branch_prefix, subtask)` call, which takes a `SubtaskPlan`, not the `str` subtask id `outcome.subtask` carries).
   - Then one run-end comment on the milestone card via the existing `comment_run_end` closure, fed the report of `controlled_payload(run_id, "cancel", ...)`. That payload already carries `cancelled: True`, so `compose_run_end` reads outcome `cancelled` and suggests `am run --milestone <id>`.
   - No comment on pending stories, on story cards, or on subtasks already done. An escalated lane under a cancel already got its escalation comment from its lane; nothing extra is posted for it here.
2. **Milestone pause only** (`stop.requested == "pause"` with no escalated lane, after `record_run(... "stopped")`): exactly one comment, the run-end on the milestone card via `comment_run_end`. `controlled_payload(..., "pause", ...)` already sets `paused: True`, so no payload change is needed. No subtask comments.
3. **`run_card`** (after `card_run_status` and the three `store.record_*` calls, still inside `run_lease`, so the writes are fenced by the lease): post at most one comment on the subtask card itself, chosen by `summary.status`:
   - `done` -> `compose_done(run_id, card_id=card.id, summary, branch=branch, resumed_at=None)`.
   - `escalated` -> `compose_escalated(run_id, card_id=card.id, token=lease.token, failed_phase=summary.failed_phase, detail=summary.detail, reason=comments.agent_reason(summary.results, summary.failed_phase))`.
   - `stopped` with `stop.requested == "cancel"` -> `compose_cancelled(run_id, card_id=card.id, before_phase=orchestrate.stopped_before_phase(summary.detail), branch=branch, relaunch=f"am run --card {card.id}")`.
   - `stopped` under a pause -> no comment.
   - Never a milestone or story comment (`run_card` has no milestone). The flush warnings are appended to the returned `warnings` (`drive.warnings`).
   - `cli.py` reaches `post_comment` as `orchestrate.post_comment` (no new import cycle at module load; follow how `cli` already references `orchestrate`, otherwise import lazily inside the function).

## Error paths

- A `brd` failure, refusal or timeout during a flush is a warning in the payload only (B8): the run status, exit code, recorded rows and payload shape are unchanged, and the run is never escalated or parked because of it.
- A lost lease (`LeaseLostError` from a fenced store write) propagates as it does for any other fenced write; it is not swallowed.
- A crash between `record_run` and the flush leaves pending outbox rows that the existing resume/flush path posts later; keys are stable (`key(run_id, card_id, "cancelled")`, run-end keyed by lease token), so a replay never double-posts.
- Bodies reach `brd comment add` on stdin only, author `am`, last line `am-key: <key>` -- all guaranteed by the reused composers and `board.py`; this subtask adds no board calls of its own.

## Tests

All new tests follow the design spec's testing rule (step 14 of `docs/superpowers/specs/2026-09-23-agent-manager-design.md`): they are **Steps-tier** tests driven with a fake driver against a temporary git repo and temporary `brd` board (no network, no real harness), in the style of 65ed3c70's tests at `tests/test_orchestrate.py:5251+` (`project` fixture, `_comments`, `_comment_states`). No unit test is added for `comments.py` (unchanged) and no end-to-end test is added. `tests/conftest.py`'s data-directory isolation guard must stay green.

`tests/test_orchestrate.py` (Steps tier):
- Cancel with two parked (stopped) subtasks leaves one `cancelled` comment on each, naming `stopped before: <phase>`, the branch and `am run --milestone <id>`, and one `cancelled` run-end comment on the milestone; done subtasks get only their earlier done comment; story cards get none.
- A pause with no escalation leaves exactly one comment, a `paused` run-end on the milestone naming `am resume <run_id>`; no subtask card gets a new comment.
- A cancel whose run-end comment the board refuses still returns `cancelled: True` with the run recorded `cancelled`, and the refusal appears in `warnings`.

`tests/test_cli.py` (Steps tier, `run_card` with injected `runner_factory`/fake driver):
- `am run --card` that finishes `done` leaves one done comment on the card and none on its parent or any milestone.
- `am run --card` that escalates leaves one escalation comment on the card with the failed phase.
- `am run --card` cancelled mid-walk leaves one `cancelled` comment naming `am run --card <id>`; a paused one leaves none.
- A board refusal on the `--card` comment is a warning; `status` and the recorded rows are unchanged.

Verification: `uv run pytest` (no separate lint or typecheck, per CLAUDE.md).
