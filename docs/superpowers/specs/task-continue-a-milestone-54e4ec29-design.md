# Continue a milestone run with `am resume` (card 54e4ec29)

Narrows plan Task 4.1 (`docs/superpowers/plans/2026-09-25-supervisor-tree.md`, lines 337-345) and addendum decision T8 / sections 6, 8 and 9 (`docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`) to this one subtask. Parent story: 84802b0b "Milestone-wide resume".

## Prerequisites

This card assumes plan Tasks 1.1-1.3, 2.1-2.2 and 3.1-3.3 are merged: `StopSignal`, `cli.drive_subtask_async`, grafo-based dataflow roots, `bases.build`, and the grafo-tree `run_milestone` (`supervise` / `lane`). If they are missing from the working tree, the card is blocked. Do not re-implement them here.

## Scope

Files: `src/agent_manager/cli.py` (`resume_run`), `src/agent_manager/orchestrate.py` (`run_milestone`), and `src/agent_manager/bases.py` (a `resume_from: Checkpoint | None = None` keyword-only parameter threaded through `build` and `_resolve_conflict` into `runtime_engine.run_subtask_async`, which already accepts it — needed because point 2 below requires each base resolver's checkpoint to reach `bases.build`). Tests go in `tests/test_cli.py`, `tests/test_orchestrate.py`, and `tests/test_bases.py` if the resolver-resume case needs its own fixture.

1. `cli.resume_run(run_id, ...)` reads `run.workflow` and dispatches on it. `"task"` keeps the current `_resume_from_checkpoint` path exactly as it is. `"milestone"` calls `orchestrate.run_milestone(..., resume_run_id=run_id)`.
2. `orchestrate.run_milestone(..., resume_run_id: str | None = None)` gets a new keyword-only parameter. When it is given:
   - Reuse that run id and its recorded `branch_prefix`, `base_branch` and `max_concurrent_stories`. Do not create a new run.
   - Refresh git the same way a fresh run does: fetch origin when present, then worktree prune.
   - Re-derive the plan fresh from the board: a fresh census, with done cards skipped. No story-level or milestone-level state is checkpointed or read back.
   - For every open subtask of this run and every `base-<story>` resolver of this run, load `Store.latest_checkpoint(card_id)`. `Store.latest_open_checkpoint` may be used to decide which ones are open.
   - Validate all checkpoints before writing anything (see Error paths).
   - Mark orphan attempts `harness_error`, reusing `cli.orphan_attempts` and the existing marking pattern in `_resume_from_checkpoint` at `cli.py` around lines 1328-1341.
   - Row transitions: subtask/resolver rows that are stopped, escalated or started become `started`. The run row becomes `started`.
   - Run `supervise(...)` as a fresh run under the same run id. Each subtask with an open checkpoint gets it passed as `resume_from` to `drive_subtask_async`, and each base resolver's checkpoint goes to `bases.build`.
   - Bases re-derive and do not merge again. This relies on the existing `already_merged` short-circuit in `merge_tip`.
   - Integrate runs when all stories finish, the same as in a fresh run.
3. Report: the same shape as a fresh milestone run's report, plus `resumed: true`. `completed` lists only what finished during this invocation.

Resume is strict and relaunch is lenient. `am run --milestone` keeps its per-card leniency, and the two paths must not share the strict refusal.

## Error paths

All of these exit with code 3 and write nothing: no row changes, no orphan marking, no git mutation beyond the refresh.

- **Digest mismatch.** The newest turn/parked/escalated checkpoint of any open subtask has a digest different from the current TASK digest, or any `base-<story>` resolver's checkpoint differs from the current INTEGRATE digest. The whole resume is refused with "workflow changed since checkpoint". The message follows the wording of the single-subtask check at `cli.py:497-502` and names the card and both digests.
- **Run is `done`.** Refused with "run <id> finished; start new work with am run --milestone".
- `am resume` on a `task` run behaves exactly as it does today, including its own refusals.

## Invariants (milestone-wide rules)

- Only `orchestrate.py` imports grafo, and every grafo Node is built with `timeout=None`.
- Only the subtask is a pygents Agent.
- No test sleeps to prove ordering. Fake drivers block on `asyncio.Event`s.
- The milestone's base branch never moves, and nothing is pushed.
- The whole default suite stays green, including `tests/e2e`.

## Out of scope

- The fake-claude kill-and-resume end-to-end proof. That is `tests/e2e/test_milestone_resume.py` plus any kill switch in `tests/e2e/fake_claude.py`, and it belongs to sibling card 949d51a0.
- Verification discovery.
- Live pause, cancel, watch or retry.
- More than one `am` process per repo.
- A grafo `max_workers` option.
- Multi-blocker support for leave-me-alone.

## Tests

Placement rule (design spec section 14 and addendum section 9): engine and orchestrator mechanics are driven with a fake runner or fake drivers in `tests/test_orchestrate.py` / `tests/test_cli.py`, with temporary git repos and a temporary brd board and no real harness process. Only `tests/e2e/*` runs the fake `claude` binary as a subprocess, and this card adds nothing there.

| # | Test | Tier / file |
|---|---|---|
| 1 | A milestone run stops on an escalation, then the fake runner is fixed to succeed, then `am resume <run-id>`. Checks: the same run id is reused; the escalated subtask resumes at its failed phase and earlier phases are not re-dispatched; the parked subtask continues from its checkpoint; done cards are not driven; Integrate runs; the report has `resumed: true` and `completed` holds only this invocation's work. | fake-runner, `tests/test_cli.py` |
| 2 | `run_milestone(resume_run_id=...)` reuses the recorded `branch_prefix`, `base_branch` and `max_concurrent_stories`, and passes each open checkpoint as `resume_from` to the fake `drive_subtask_async` / `bases.build`. | fake-driver, `tests/test_orchestrate.py` |
| 3 | A stale digest on one open subtask gives exit 3 "workflow changed since checkpoint", and no rows or attempts change. | fake-runner, `tests/test_cli.py` |
| 4 | A stale digest on a `base-<story>` resolver checkpoint gives exit 3, and nothing is written. | fake-driver, `tests/test_orchestrate.py` |
| 5 | Resuming a `done` milestone run gives exit 3 "run <id> finished; start new work with am run --milestone", and nothing is written. | fake-runner, `tests/test_cli.py` |
| 6 | An orphan attempt (still `started` from the interrupted run) is marked `harness_error` on resume. | fake-runner, `tests/test_cli.py` |
| 7 | A merged base from the interrupted run is reused and not merged again (`already_merged`), and the milestone base branch has not moved. | temp git repo + fake driver, `tests/test_orchestrate.py` |
| 8 | `am resume` on a `task` run behaves as before: the existing task-resume tests pass unchanged, plus one assertion that dispatch routes to `_resume_from_checkpoint`. | fake-runner, `tests/test_cli.py` |
