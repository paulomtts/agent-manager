# Let the engine stop between phases on request (card 0d8b7c9a)

Subtask of story 9bfb5ac2 "Stop cleanly: a stopped status and a cooperative stop" (addendum decision P4, `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` lines 66-75). This narrows P4 to the engine's side of the stop: a check before each phase. It extends `2026-09-24-orchestration-design.md` and `2026-09-23-agent-manager-design.md` and changes nothing they decided.

## Precondition

Sibling a3dd82f4 "Add the stopped status" must be present. This worktree already has it: `models.Status` includes `"stopped"` (models.py:25) and `SubtaskSummary.status` is `Literal["done", "escalated", "stopped"]` (engine.py:244). `master` does not; if this card is ever rebased onto a base without that work, stop and merge the sibling branch first rather than re-adding `stopped` here. The `am status` rows, `select_resumable` refusal text, exit code and envelope handling of `stopped`, `rebuild_from_journal` and their round-trip tests belong to that sibling and are not touched.

## Scope

1. `engine.run_subtask` gains a keyword `should_stop: Callable[[], bool] | None = None`.
2. At the top of every iteration of the phase loop (`while index < len(workflow.phases)`, engine.py:385), before anything about the phase runs, the engine calls `should_stop()` if it is not None. The check comes before the `agent_runner is None` `EngineError`, before `prompt.render_prompt`, and before `_run_deterministic` records the phase `started`. So it applies to deterministic and agent phases alike, and nothing about the phase runs.
3. When the check returns true, the engine records the subtask `stopped` via `_record_subtask_status(store, story_id, subtask, "stopped")`, which writes both the store row and the journal line through `store.record_subtask`. It sets `summary.status = "stopped"` and `summary.detail = "stopped before <phase.name>"`, leaves `summary.failed_phase` as `None`, and returns the summary immediately. Results, warnings and skipped phases gathered so far stay on the summary, as with `_escalate`. A small helper next to `_escalate` is fine, but it must not share `_escalate`'s `failed_phase` assignment: `stopped` is not `failed`.
4. The engine never checks during a phase. A phase already started, including an agent phase in flight, finishes and is recorded normally (done, failed or escalated). A stop that turns true during phase N shows up at the check before phase N+1. If phase N escalates, the escalation wins and there is no later check. If the walk finishes all phases, the subtask is recorded `done` as today, with no check after the last phase.
5. The engine checks only the loop indices it actually visits. Phases jumped over by a `when` `skip_to` are never visited, so they are never checked.
6. `cli.drive_subtask` (cli.py:679) gains an optional keyword `should_stop: Callable[[], bool] | None = None` and passes it straight to `engine.run_subtask`. `orchestrate.Driver` (orchestrate.py:46-66) gains the same keyword with default `None`, so `cli.drive_subtask` still satisfies the protocol.
7. Existing callers keep their current code. `orchestrate.run_milestone`'s `drive(...)` call (orchestrate.py:301), `run_card`, `resume`, and every fake driver in the tests stay as they are and pass no `should_stop`. Wiring a real shared `threading.Event` into those callers is other stories' work (P5/P6/P7). So is handling a `stopped` summary in orchestrate's `status != "done"` branch (orchestrate.py:322-331), which cannot happen until someone passes `should_stop`.

## Observable behaviour

- `should_stop=None`, or a callable that never returns true, gives exactly today's behaviour. That means the same phases, store rows, journal lines and summary. `--max-concurrent 1` and the sequential runner are unaffected.
- On a stop, the subtask row and its journal record read `stopped`. The summary reads `status="stopped"`, `failed_phase=None`, `detail="stopped before <name>"`. The engine starts no later phase: no `started` phase row, no agent-runner call, and no deterministic function call.
- Re-entry: the store has no CHECK on status, so there is no schema change. A `stopped` subtask can be driven again by a fresh `run_subtask` call over the same store. It continues through the same idempotence, `start_phase`, and Plan-Hash re-entrancy that resume already relies on, and it finishes `done`.

## Error paths

- `should_stop` raising is not caught. It is the caller's own callable (a flag read), and the engine does not turn it into an escalation. It propagates the same way a bad `start_phase` or a reserved `extra_context` key does.
- An agent phase with no injected runner still raises `EngineError`, but only if the walk actually reaches that phase without stopping first.

## Tests

All engine tests are in the engine tier (design spec section 14: engine driven with a fake runner or canned functions). They go in `tests/test_engine.py` and use its hand-built `FunctionRegistry`, the real temp SQLite and JSONL `store` fixture, and its existing helpers (`_registry`, `_workflow`, `_recording_runner`, `_journalled_phases`, `_projected_phases`, `_journalled_details`). None of these tests touch `tests/e2e` or call a real `claude`, and no new tier is added.

1. Stop during phase 3 (engine tier): `should_stop` becomes true while phase 3 runs (for example, set by phase 3's canned function or runner). Phases 1-3 are recorded as normal. The call log shows no phase 4 or later. The store row and journal show the subtask `stopped`, and the summary reads `status="stopped"`, `failed_phase is None`, `detail == "stopped before <phase 4 name>"`.
2. Already stopped (engine tier): `should_stop` is true from the start. No phase is recorded, no function or runner is called, and the subtask is `stopped` with `detail == "stopped before <phase 1 name>"`.
3. Both phase kinds are checked (engine tier): in a workflow that mixes deterministic and agent phases, a stop set just before a deterministic phase leaves it with no `started` row and its function uncalled. A stop set just before an agent phase leaves the recording runner uncalled. The agent case includes a walk with `agent_runner=None` that stops before its agent phase and returns `stopped` rather than raising `EngineError`.
4. Never fires (engine tier): with a `should_stop` that always returns false, the walk's summary, phase rows and journal match a walk without it. The existing engine suite passes unchanged.
5. Re-drive after a stop (engine tier): a first `run_subtask` stops before phase N. A second `run_subtask` over the same store, with the same subtask and `start_phase=<phase N>` and no stop, runs only phase N onward according to the call log. It finishes with the subtask recorded `done` in both the store row and the journal.
6. Pass-through (CLI tier, `tests/test_cli.py`, only if cheap with the existing temp git/brd fixtures and a fake `runner_factory`): `cli.drive_subtask(..., should_stop=lambda: True)` returns a summary with `status == "stopped"` and never calls the fake runner.
7. Driver compatibility (`tests/test_orchestrate.py`): add this only if the existing orchestrate suite does not already show that fake drivers without a `should_stop` parameter still work. Fake drivers are not modified.

Card rules still hold. Any fake `claude` knows only what the brief says: it does not compute the plan hash, commit the spec or plan, or learn the result path from anything other than the prompt text. The whole default suite, including `tests/e2e`, stays green. Everything runs in one process, with no support for two `am` processes on one repo or run.
