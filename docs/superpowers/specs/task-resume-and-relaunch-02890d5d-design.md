# Resume and relaunch from checkpoints on the pygents engine (card 02890d5d)

Subtask of a6c7bff3 "Checkpoints and resume" (milestone 84c3b532). Blocked by 7fdec762 (done). Narrows plan Task 4.5 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1297-1316`) and milestone spec §6/§8/§9 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:262-401`).

Note on inputs: the exploration summary this spec was written from was cut off at 8000 characters, partway through its file:line list. The missing part probably held the rest of the references and the test-placement paragraph. The line references and tier assignments below come from reading this worktree's code directly and from design §14 of `2026-09-23-agent-manager-design.md`. They do not come from the missing text.

## Scope

In scope, all in this worktree:

- `src/agent_manager/cli.py`:
  - `resume_run` gets an `engine` parameter, and the Typer `resume` command gets `--engine {yaml,pygents}` (default `yaml`).
  - `select_resumable` accepts a `stopped` subtask on pygents only.
  - `drive_subtask` gains `resume_from`.
- `src/agent_manager/orchestrate.py`:
  - On relaunch under `--engine pygents`, look up each card's open checkpoint before driving it.
  - The `Driver` protocol gains `resume_from`.
- Tests in `tests/test_cli.py`, `tests/test_orchestrate.py` and `tests/e2e/test_milestone_run.py`, plus whatever `tests/e2e/conftest.py`/`fake_claude.py` knob the kill scenario needs.

Consumed, not redefined:

- `Store.latest_checkpoint(card_id)` and `Store.latest_open_checkpoint(card_id, workflow)` (`store.py:896-928`)
- `runtime_engine.run_subtask(..., resume_from=...)` and `CheckpointMismatch` (`runtime/engine.py:33-154`)
- the checkpoint hook (`runtime/checkpoint.py`)

Out of scope:

- `store.py`, `runtime/checkpoint.py` and the resume mechanics in `runtime/engine.py`
- `integration.py` (Integrate's own path)
- the yaml helpers `interrupted_phase`, `resume_start_phase` and `_skipped_origin`
- `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads (G10)

## Rules carried from the story

1. Only `runtime/` imports pygents. `cli.py` and `orchestrate.py` reach it only through `agent_manager.runtime.engine` (`runtime_engine`). A digest comes from `workflow.task.TASK.digest()` (`workflow/phases.py`, which has no pygents import).
2. The whole default suite stays green, `tests/e2e` included, on both engines.
3. Never break or return out of `agent.run()`. Never checkpoint at `AFTER_TURN`. Hooks are module-level only. This card adds no hooks.
4. A fake `claude` knows only what its brief and the test's env knobs tell it. It is never told run or checkpoint state.
5. G10: payload shapes, journal lines and rows stay as they are.

## Observable behavior

### `am resume <run-id> --engine yaml` (the default)

Byte-for-byte today's behavior. It still uses `select_resumable`'s current refusals, including the stopped-subtask remedy text, plus `interrupted_phase`, `resume_start_phase` and `yaml_engine.run_subtask(start_phase=...)`.

### `am resume <run-id> --engine pygents`

1. The same pre-`Store.open` refusals as yaml: unknown run, repo dir, board, and workflow load.
2. `select_resumable` on pygents returns the run's single subtask recorded `started` or `stopped`.
   - Zero candidates is refused with the existing "nothing in flight" wording, minus the relaunch remedy, which does not apply here.
   - More than one candidate is refused as today.
   - An `escalated` subtask is still refused. `retry` owns escalations; see the open question below.
3. Open the store and take `store.latest_checkpoint(subtask.card_id)`. Two cases are refused with `NotResumableError` (exit 3) before anything is written:
   - There is no row. The run was driven on yaml, or it died before its first `BEFORE_TURN`. The message says so.
   - The newest row is `done`. Only the final status write was lost, the same situation as the yaml "every phase done" refusal.
4. If the checkpoint's digest differs from `TASK.digest()`, the command prints `{"ok": false, "error": {...}}` whose message contains `workflow changed since checkpoint` and exits 3. It writes nothing: no orphan re-marking and no run/story/subtask row changes. `CheckpointMismatch` is the type this path raises or catches. The CLI must refuse before its own writes, not rely on `run_subtask` raising after they happen. The envelope's `type` is `CheckpointMismatch`, or a `CliError` wrapping it. Either way it rides `HANDLED` to exit 3.
5. Otherwise it marks orphan attempts `harness_error` exactly as today (`orphan_attempts` and `record_attempt`), records run, story and subtask `started`, and calls `runtime_engine.run_subtask(TASK, store, ..., resume_from=checkpoint)` with the same walk kwargs `drive_subtask` uses. It never calls `interrupted_phase` or `resume_start_phase`.
6. The agent is rebuilt from the checkpoint, so the pending turn and its loop count are the checkpoint's. Resuming from an `escalated` row re-runs the failed phase with the loop count it had. Resuming from a `parked` row continues the phase it parked before.
7. The payload keeps today's key set: `run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status`, `failed_phase`, `detail`, `skipped`, `warnings`, `resumed_from` and `discarded_attempts`. `resumed_from` names the phase the checkpoint resumes at. If that cannot be read without `cli.py` touching pygents structures, the plan may add a read-only accessor inside `runtime/`; it must not change resume mechanics.
8. Exit codes are as today: 0 for done or stopped, 1 for escalated, and 3 for a refusal or mismatch. `--engine bogus` is a usage error: exit 2 and nothing on stdout.

### `am run --milestone --engine pygents` relaunch

- `done` cards are still skipped, as today.
- For every subtask a lane is about to drive, the lane calls `store.latest_open_checkpoint(card.id, TASK.name)`.
  - If that returns a row whose digest equals `TASK.digest()`, the driver gets `resume_from=<row>`, and the card continues from it inside the new run. New checkpoints are saved under the new run id.
  - If it returns `None` (no row, or the card's newest row is `done`) or the digest differs, the driver gets `resume_from=None` and the card starts fresh. No error, no warning is required, and no exit 3.
- `plan_check` still reuses a validated plan on a fresh start.
- On `--engine yaml` no lookup happens, and the driver gets no checkpoint.

`drive_subtask(..., resume_from=None)` passes `resume_from` to `runtime_engine.run_subtask`. Passing a non-`None` `resume_from` with `engine="yaml"` is refused with `ValueError`, before a runner is built. The `Driver` protocol grows the same keyword. Existing test drivers must keep working, which means either:

- the lane passes `resume_from` only when it is non-`None`, or
- the fakes are updated.

The plan picks one. No test assertion is loosened.

## Error paths

| Situation | Result |
|---|---|
| pygents, digest mismatch on `am resume` | `ok: false`, message contains "workflow changed since checkpoint", exit 3, nothing written |
| pygents, no checkpoint row for the in-flight card | `NotResumableError`, exit 3, nothing written |
| pygents, newest row `done` | `NotResumableError`, exit 3, nothing written |
| pygents, 0 or more than 1 `started`/`stopped` subtasks, or only `escalated` | `NotResumableError`, exit 3 (pre-`Store.open`) |
| pygents relaunch, mismatch or no open checkpoint | card starts fresh, no error |
| yaml, any of the above | unchanged from today |
| `--engine` not in `ENGINES` | exit 2, nothing on stdout |

## Open question, flagged rather than invented

The milestone spec says "after an escalation it re-runs the failed phase with the loop count it had". The findings only relax the `stopped` refusal. This spec therefore keeps `escalated` subtasks refused on `resume`. The escalated-row path is reached only when a `started` subtask's newest checkpoint is `escalated`, for example after a crash between the checkpoint save and the row write. If the spec critic reads §6 as making `escalated` subtasks resumable on pygents, the change is local:

- add `escalated` to step 2's candidate statuses, and
- add a test.

## Tests

Tier placement follows design §14 (`2026-09-23-agent-manager-design.md:497-512`) as applied in this repo:

- Pure: functions over hand-built models.
- Steps: a real temporary git repo and brd board, `XDG_DATA_HOME` under `tmp_path`, and the harness replaced at the `runner_factory`/`driver` seams.
- Fake-claude e2e: `tests/e2e`, the default suite, running the real `cli.app` with a fake `claude` on `PATH`.

| # | Test | File | Tier |
|---|---|---|---|
| 1 | `select_resumable` on pygents returns a lone `stopped` subtask; still refuses zero, more than one, and a lone `escalated` subtask | `tests/test_cli.py` | Pure |
| 2 | `select_resumable` on yaml (default) keeps every existing refusal and its wording (existing tests at 4494-4540 untouched) | `tests/test_cli.py` | Pure (existing) |
| 3 | A pygents subtask killed by a `BaseException` from the runner in `plan` resumes via `resume_run(engine="pygents")`. The runner sees `plan` next, not `explore` or `spec`, and the walk ends `done` | `tests/test_cli.py` | Steps |
| 4 | A stopped (parked) milestone subtask resumed with `am resume --engine pygents` continues to `done` instead of being refused | `tests/test_cli.py` | Steps |
| 5 | Orphan `started` attempts are recorded `harness_error` on the pygents branch and listed in `discarded_attempts` | `tests/test_cli.py` | Steps |
| 6 | A digest mismatch exits 3 with an `ok: false` envelope containing "workflow changed since checkpoint"; rows, attempts and checkpoints are unchanged afterwards | `tests/test_cli.py` | Steps |
| 7 | No checkpoint for the in-flight card (a yaml-driven run), and newest row `done`: each exits 3 and writes nothing | `tests/test_cli.py` | Steps |
| 8 | A `started` subtask whose newest row is `escalated` resumes at the failed phase with its loop count (the runner sees that phase next, with the recorded loop) | `tests/test_cli.py` | Steps |
| 9 | `am resume --engine bogus` exits 2 with nothing on stdout | `tests/test_cli.py` | Steps (CliRunner, as the existing `--engine bogus` test) |
| 10 | Relaunch on pygents: a card parked in run 1 gets `resume_from` equal to that row in run 2 (driver seam); a digest mismatch gets `None` and the run does not raise; a card whose newest row is `done` gets `None`; on yaml no `resume_from` is passed | `tests/test_orchestrate.py` | Steps |
| 11 | `drive_subtask` refuses `resume_from` with `engine="yaml"`, and hands it through on pygents | `tests/test_cli.py` | Steps |
| 12 | Under the fake `claude`, a pygents run killed in `plan` and then resumed with `am resume --engine pygents` does not re-dispatch `explore` or `spec`. The fake's log shows each once in total and `plan` twice. How the kill happens is a test env knob chosen in the plan; the fake is told nothing about run state | `tests/e2e/test_milestone_run.py` | Fake-claude e2e |
| 13 | A pygents milestone relaunch after a stop continues the parked card (fake log shows no re-dispatch of its finished phases) | `tests/e2e/test_milestone_run.py` | Fake-claude e2e |
| 14 | All existing resume and milestone tests pass unchanged on `--engine yaml`, and the parametrised e2e parity stays green on both engines | as existing | existing tiers |

Verification: `uv run pytest`, the whole default suite, green.
