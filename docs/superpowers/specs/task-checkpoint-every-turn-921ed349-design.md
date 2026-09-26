# Checkpoint every turn and park on the run's stop (card 921ed349)

Subtask of story a6c7bff3 "Checkpoints and resume". Narrows plan Task 4.2 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1177-1251`) under the milestone spec `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` G5 (:91-96), G8 (:105-108), §6 (:262-300). The plan's reference implementation is prescriptive; follow it.

## Scope

In scope:

- New `src/agent_manager/runtime/checkpoint.py` with `Parked`, `save`, and the module-level `before_turn` hook.
- `src/agent_manager/runtime/engine.py`: import `checkpoint` so the hook registers; wire the `Parked` catch, the `done` save and the `escalated` saves into `_run`; update the `run_subtask` docstring, which currently says `should_stop` "is not yet read -- the stop bridge is plan Task 4.2".
- Tests `tests/runtime/test_checkpoint.py` and `tests/runtime/test_stop_bridge.py`.

Out of scope: resume from a checkpoint, `CheckpointMismatch`, `Agent.from_dict` (card 5698e4f6); the `--engine` flag and orchestrate/integration plumbing (7fdec762); `am resume --engine pygents`, orphan-attempt marking, digest-mismatch exit 3, milestone relaunch (02890d5d); the store's `checkpoints` table and methods, which are already on this branch at `store.py:842-906` (3d4947e8, done; do not touch); the supervisor-tree/`pause()` stop; exactly-once phases; upstream pygents fixes (spec §11).

## Observable behavior

`checkpoint.Parked(Exception)`: built with `before_phase: str`, exposes `.before_phase`, message `"stopped before <phase>"`.

`checkpoint.save(agent, reason: str) -> None`: reads `current_run.get(None)`. With no run set it returns and writes nothing. Otherwise it calls `deps.store.save_checkpoint(deps.subtask.card_id, workflow=deps.workflow.name, digest=deps.workflow.digest(), reason=reason, agent=agent.to_dict(), saved_at=deps.clock())`.

`checkpoint.before_turn(agent)`: a module-level `async` function registered with `@hook(AgentHook.BEFORE_TURN, tags={"subtask"})`. With no run set it does nothing: no `LookupError` and no row. Otherwise it takes the head phase name from `agent.to_dict()`, as `(snapshot["current_turn"] or snapshot["queue"][0])["kwargs"]["phase"]`. If `deps.should_stop` is set and returns true, it saves `parked` and raises `Parked(head)`. Otherwise it saves `turn`.

`engine._run` after `agent.run()`:
- Clean end: `_collect`, then `save(agent, "done")`, then the existing `_record_subtask_status`. The summary does not change.
- `checkpoint.Parked`: this except clause goes before the generic `except Exception`. It calls `_collect(agent, deps, summary)` and then returns `old._stop(summary, deps.store, deps.story_id, deps.subtask, parked.before_phase)`. The result is `status == "stopped"` and `detail == "stopped before <phase>"`. No further row is written, so the `parked` row the hook already saved stays the newest.
- `except C.Escalated` and the generic `except Exception`: call `save(agent, "escalated")` before `old._escalate(...)`, and leave everything else as it is.
- `except old.EngineError: raise` stays unchanged and writes no row. It is a re-raised wiring error, not an escalation branch. The plan says "both escalation branches", so this card follows the plan. Spec §6's wording "any other `Exception`" could be read to include it; that ambiguity is flagged, not resolved, here.
- A `BaseException` such as `KeyboardInterrupt` or `CancelledError` is never caught and writes nothing. The last `turn` row stands.
- `current_run` is still reset in `finally`. Every `save` in `_run` has to happen while `current_run` is still set, meaning inside the `try`, or it silently becomes a no-op. On the clean path that means the `done` save must also happen before the reset, so it cannot stay on the line after the `finally` where `_collect` currently runs.

## Binding constraints

- Only `src/agent_manager/runtime/` imports pygents; `workflow/phases.py` never does.
- Never break or return out of `agent.run()`. The stop ends the run only by `Parked` propagating out of it.
- Never checkpoint at AFTER_TURN. Hooks are module-level only, never closures (spec §11, the `HookRegistry` collision note).
- G10: `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads stay byte-for-byte the same. Checkpoints are only a side channel.
- `orchestrate.py`'s `RunStop` `threading.Event` is not redesigned. The hook only calls `should_stop`.
- A fake `claude` or fake step in a test knows only what its brief tells it.
- The whole default suite must stay green, including `tests/e2e`, on both engines.

## Error paths

- A hook firing with no run set (a stray agent tagged `subtask`) is a no-op.
- `save_checkpoint` failures, such as a store `CHECK` violation, are not caught. They propagate like any other store error: out of the hook, they end the run through the generic `Exception` branch. There is no special handling.
- A stop that is already set before the first turn parks before the first phase. This follows from the hook's rule and needs no extra code.

## Tests

Placement rule, from the findings: tests mirror the source path under `tests/`, and `tests/e2e` is reserved for full-pipeline harness wiring. All tests below exercise `src/agent_manager/runtime/` at the hook and engine level. They are driven through `runtime.engine.run_subtask` with fake steps and a real temporary `Store`, so they belong in the mirrored `tests/runtime/` tier and not in `tests/e2e`.

`tests/runtime/test_checkpoint.py` (mirrored tier `tests/runtime/`):
1. `test_every_turn_is_checkpointed_before_it_runs`: a 3-step workflow writes rows with seq 0, 1, 2, all with reason `turn`. Each row's stored agent has that phase at its queue head (`current_turn` or `queue[0]`). One final `done` row follows (seq 3).
2. `test_an_escalation_writes_an_escalated_row`: a fake step that escalates leaves the newest row with reason `escalated`. The summary and escalation payload are unchanged from today.
3. `test_hook_without_a_run_does_nothing`: a stray `Agent("stray", "t", [some_tool], tags=["subtask"])` with a queued `Turn` is run to the end with no `current_run` set. There is no `LookupError` and no checkpoint row.

`tests/runtime/test_stop_bridge.py` (mirrored tier `tests/runtime/`):
4. `test_stop_set_during_a_phase_parks_before_the_next`: step `a` calls `stop.set()` on a `threading.Event`, and `should_stop=stop.is_set`. Expected: `summary.status == "stopped"`, `summary.detail == "stopped before b"`, the newest checkpoint has reason `parked` with queue head `b`, and `b` never ran.

## Verification

`uv run pytest` must be fully green, per CLAUDE.md and plan Task 4.2 Step 4. The exploration findings were cut off at "VERIFICATION COMMANDS (given, returned exactly): ful". The upstream stage went over its 8000-character brief, and any further commands it listed are unknown. This spec uses only the command in CLAUDE.md.

Commit: `feat(runtime): checkpoint every turn and park on the run's stop`.
