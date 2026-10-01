# Give SubtaskSummary a real before_phase field (subtask 150613bb)

Parent story: 0f2b1491 "One StoryRecorder instead of nine inlined state transitions" (milestone 9c44c2fb). Source decision: architecture cleanup addendum `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md` §3, S11 (Housekeeping) — "add `before_phase: str | None` to `SubtaskSummary` and stop parsing it out of `detail` text", riding along with S5's `orchestrate.py` work.

## Base branch

Build on `m13/task-refactor-base-only-lane-8fc3628c` (blocker 8fc3628c, done), not `master`. Only that branch has `StoryRecorder` (`orchestrate.py` ~865+, `.stopped(subtask_id, before_phase, *, subtask_row=...)` ~944-953) and the target call site (~1228-1231). This worktree already sits on that code.

Note on the source decision's module-graph table: the addendum's §4 table lists this field under
`models.py` (`SubtaskSummary.before_phase (S11)`), but `SubtaskSummary` is not a `models.py`
Pydantic type today — it is, and stays, the plain dataclass at `runtime/walk.py:242`, per
CLAUDE.md's rule that internal-only state uses a dataclass, not a process-boundary model. The
table row is a slip in the umbrella doc; this subtask edits `runtime/walk.py`, not `models.py`.

## Scope

1. `src/agent_manager/runtime/walk.py` — `SubtaskSummary` (dataclass, ~242-255) gains `before_phase: str | None = None`. It stays a plain dataclass (internal-only state per CLAUDE.md).
2. `walk._stop` (~474-490) sets `summary.before_phase = phase_name` in addition to what it does today. `summary.detail = f"stopped before {phase_name}"` is kept unchanged, as are `status = "stopped"`, `failed_phase = None`, and the `_record_subtask_status(..., "stopped")` write.
3. `src/agent_manager/orchestrate.py` — delete `STOPPED_PREFIX` (~73-75) and `stopped_before_phase` (~77-86).
4. The one real caller, inside `lane`'s stop-handling branch (~1228-1231), becomes `recorder.stopped(subtask.id, summary.before_phase, subtask_row="started")`. Every other `recorder.stopped(...)` call already passes a literal `None` (a stop with no subtask started) and is left alone.

The engine does not change: `runtime/engine.py` (~203-204) already passes `parked.before_phase` into `_stop`, which is the exact phase name.

## Observable behaviour

None changes (§5 Compatibility). `LaneOutcome.before_phase`, the `stopped` entries in the run result and `am status` JSON, the journal format, and the subtask/story rows written to the store all stay byte-identical. `SubtaskSummary` is never persisted, so the new field changes no stored format. For a subtask that stops, `summary.before_phase` holds the same value that `stopped_before_phase(summary.detail)` returned before. For a summary that did not stop (`done`/`escalated`), it stays `None`.

## Error paths

No new ones. The old helper returned `None` when `detail` was missing or did not start with the prefix. The new field returns `None` whenever `_stop` did not run, which covers the same cases for every summary that reaches the stop branch. The stop branch only runs for `status == "stopped"`, and that status is set only by `_stop`.

## Out of scope

- Other `StoryRecorder` methods (`started`, `subtask_done`, `escalated`) and `lane`/`base_only_lane` control flow outside the one stop call site. Those belong to siblings 7640024e and 8fc3628c, both done.
- `checkpoint.Parked` (checkpoint.py:37-41), which is read only. Its shape is the checkpoint format.
- From §8: the pygents turn/phase model, the harness adapter contract, adding a typecheck/CI gate, rewriting the process-boundary monkeypatches, and replacing grafo.

## Tests

Tier rule, per the design spec §14 "Testing": pure functions get direct unit tests; the engine is driven with fake steps/adapters; only the opt-in e2e test uses a real harness. None of this subtask's coverage goes in `tests/e2e/`.

- **Remove** `test_the_before_phase_is_read_out_of_a_stopped_detail` (tests/test_orchestrate.py:172-177). This is a pure-function unit test tier. It tests the helper being deleted, and nothing replaces it.
- **Extend** the parked-stop test in `tests/runtime/test_checkpoint.py` (asserts at ~268-269) with `assert summary.before_phase == "c"`, next to the existing `detail == "stopped before c"` assertion. This is the fake-driven engine tier: it runs `run_subtask_async` with in-process fake steps and reaches `walk._stop` through the engine's parked-checkpoint path.
- **Unchanged, must still pass** (orchestrator driven with fakes, default suite): the existing lane stop oracles in `tests/test_orchestrate.py` that assert `"before_phase": "implement"` / `"b_second"` / `"second"` for a started subtask and `None` for unstarted ones (e.g. ~2059, ~2094, ~2839, ~4652). These confirm that the value now comes from `summary.before_phase` with the outward shape untouched. Also `tests/test_engine.py::test_a_stop_requested_during_phase_three_stops_before_phase_four`.

## Verification

`uv run pytest` (full suite; no lint or typecheck command exists).
