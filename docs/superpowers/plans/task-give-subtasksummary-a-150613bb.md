<!-- task-pipeline: validated -->
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

---

# Give SubtaskSummary a real before_phase field Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry the stop phase on `SubtaskSummary.before_phase` directly, so `orchestrate` no longer parses it back out of `summary.detail`.

**Architecture:** `walk._stop` already receives the exact phase name from the engine (`parked.before_phase`); it now also stores it on a new dataclass field. `lane`'s stop branch reads that field and the string-parsing helper `orchestrate.stopped_before_phase` plus `STOPPED_PREFIX` are deleted. No outward (JSON, journal, store) change.

**Tech Stack:** Python 3, dataclasses, pytest (async tests via the repo's existing pytest config), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-give-subtasksummary-a-150613bb/docs/superpowers/specs/task-give-subtasksummary-a-150613bb-design.md` (prepended above).

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-give-subtasksummary-a-150613bb` on branch `m13/task-give-subtasksummary-a-150613bb`, cut from `origin/m13/task-refactor-base-only-lane-8fc3628c`. All paths below are relative to that worktree root. Run every command from that root.

## Global Constraints

- `SubtaskSummary` stays a plain `@dataclass` in `src/agent_manager/runtime/walk.py`; do not move it to `models.py` and do not convert it to Pydantic.
- New field exactly: `before_phase: str | None = None`, declared last (after `detail`) so every existing positional/keyword construction keeps working.
- `walk._stop` keeps writing `summary.detail = f"stopped before {phase_name}"` unchanged.
- No CLI-observable change: run result JSON, `am status` JSON, journal format, and store rows stay byte-identical.
- Do not touch `checkpoint.Parked`, `runtime/engine.py`, or any `StoryRecorder` method other than the one `recorder.stopped(...)` call in `lane`'s stop branch.
- No tests in `tests/e2e/`.
- Verification: `uv run pytest` (no lint, no typecheck).

## Review Focus

1. An escalated subtask (never reached `_stop`) must report `before_phase is None`, not a stale or parsed value — pinned in Task 1 by extending `test_an_error_outside_a_phase_writes_an_escalated_row` in `tests/runtime/test_checkpoint.py`.
2. A stop requested while a phase is running must name the next unrun phase on the field, not the phase that was running — pinned in Task 1 by `summary.before_phase == "c"` in the parked-stop checkpoint test (the stop fires during `b`); `tests/test_engine.py::test_a_stop_requested_during_phase_three_stops_before_phase_four` stays unchanged per the spec.
3. A stop before any subtask started must still report `"before_phase": None` in the run JSON — covered unchanged by the existing oracles in `tests/test_orchestrate.py` (~2094, ~3276, ~3316); Task 2 must not touch the literal-`None` `recorder.stopped(...)` calls.
4. A stale reference to the deleted helper anywhere in `src/` or `tests/` would break import of `orchestrate` — Task 2 Step 5 greps for it.
5. `detail` text must remain `"stopped before <phase>"` for anything that still displays it — pinned by the existing `detail == "stopped before c"` / `"stopped before delta"` assertions left in place.

---

### Task 1: `SubtaskSummary.before_phase` set by `walk._stop`

**Files:**
- Modify: `src/agent_manager/runtime/walk.py:241-255` (`SubtaskSummary`)
- Modify: `src/agent_manager/runtime/walk.py:474-490` (`_stop`)
- Test: `tests/runtime/test_checkpoint.py:267-269` and `:221-223`
- Must still pass unchanged: `tests/test_engine.py::test_a_stop_requested_during_phase_three_stops_before_phase_four`

**Interfaces:**
- Consumes: `walk._stop(summary, store, story_id, subtask, phase_name)` — already called from `runtime/engine.py:203-204` with `parked.before_phase`.
- Produces: `SubtaskSummary.before_phase: str | None` — equals `phase_name` when `status == "stopped"`, otherwise `None`. Task 2 reads it.

- [ ] **Step 1: Write the failing assertions**

In `tests/runtime/test_checkpoint.py`, inside `test_before_turn_saves_every_turn_and_only_on_pause_parks`, replace:

```python
    assert ran == ["a", "b"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before c"
```

with:

```python
    assert ran == ["a", "b"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before c"
    assert summary.before_phase == "c"
```

In the same file, inside `test_an_error_outside_a_phase_writes_an_escalated_row`, replace:

```python
    assert ran == ["a"]
    assert summary.status == "escalated"
    assert "checkpoint write broke" in summary.detail
```

with:

```python
    assert ran == ["a"]
    assert summary.status == "escalated"
    assert "checkpoint write broke" in summary.detail
    assert summary.before_phase is None
```

Leave `tests/test_engine.py` untouched: the spec lists `test_a_stop_requested_during_phase_three_stops_before_phase_four` as "unchanged, must still pass".

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_checkpoint.py::test_before_turn_saves_every_turn_and_only_on_pause_parks tests/runtime/test_checkpoint.py::test_an_error_outside_a_phase_writes_an_escalated_row -v`
Expected: 2 FAILED, each with `AttributeError: 'SubtaskSummary' object has no attribute 'before_phase'`.

- [ ] **Step 3: Add the field**

In `src/agent_manager/runtime/walk.py`, replace:

```python
    failed_phase: str | None = None
    detail: str | None = None


@dataclass
class _Outcome:
```

with:

```python
    failed_phase: str | None = None
    detail: str | None = None
    before_phase: str | None = None
    """The phase a `stopped` subtask would have run next; None unless `_stop` ran."""


@dataclass
class _Outcome:
```

- [ ] **Step 4: Set it in `_stop`**

In `src/agent_manager/runtime/walk.py`, replace:

```python
    summary.status = "stopped"
    summary.detail = f"stopped before {phase_name}"
    _record_subtask_status(store, story_id, subtask, "stopped")
    return summary
```

with:

```python
    summary.status = "stopped"
    summary.before_phase = phase_name
    summary.detail = f"stopped before {phase_name}"
    _record_subtask_status(store, story_id, subtask, "stopped")
    return summary
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_checkpoint.py tests/test_engine.py::test_a_stop_requested_during_phase_three_stops_before_phase_four -v`
Expected: all PASSED (the two extended checkpoint tests plus the unchanged engine stop test).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/runtime/walk.py tests/runtime/test_checkpoint.py
git commit -m "feat(walk): give SubtaskSummary a before_phase field set by _stop (S11)"
```

---

### Task 2: Read `summary.before_phase` in `lane` and delete the parsing helper

**Files:**
- Modify: `src/agent_manager/orchestrate.py:73-86` (delete `STOPPED_PREFIX` and `stopped_before_phase`)
- Modify: `src/agent_manager/orchestrate.py:1226-1233` (`lane`'s stop branch)
- Test: `tests/test_orchestrate.py:172-177` (delete the helper's unit test)

**Interfaces:**
- Consumes: `SubtaskSummary.before_phase: str | None` from Task 1; `StoryRecorder.stopped(subtask_id, before_phase, *, subtask_row=...)` (existing, unchanged, orchestrate.py ~944-953).
- Produces: nothing new; `orchestrate.stopped_before_phase` and `orchestrate.STOPPED_PREFIX` no longer exist.

This task is a behaviour-preserving refactor: its RED is removing the test for the helper being deleted, and its GREEN oracle is the unchanged lane stop tests in `tests/test_orchestrate.py` (e.g. ~2059, ~2094, ~2839, ~4652) that assert `before_phase` in the run JSON.

- [ ] **Step 1: Delete the helper's unit test**

In `tests/test_orchestrate.py`, delete this whole function (and the two blank lines after it):

```python
def test_the_before_phase_is_read_out_of_a_stopped_detail():
    """`walk._stop` writes "stopped before <phase>"; the summary has no field
    of its own for that phase, so the helper reads it out of `detail`."""
    assert orchestrate.stopped_before_phase("stopped before implement") == "implement"
    assert orchestrate.stopped_before_phase("reviewer found a blocker") is None
    assert orchestrate.stopped_before_phase(None) is None


```

- [ ] **Step 2: Switch the call site to the field**

In `src/agent_manager/orchestrate.py`, inside `lane`, replace:

```python
                if summary.status == "stopped":
                    raise LaneStopped(
                        recorder.stopped(
                            subtask.id,
                            stopped_before_phase(summary.detail),
                            subtask_row="started",
                        )
                    )
```

with:

```python
                if summary.status == "stopped":
                    raise LaneStopped(
                        recorder.stopped(
                            subtask.id,
                            summary.before_phase,
                            subtask_row="started",
                        )
                    )
```

- [ ] **Step 3: Delete the helper and its constant**

In `src/agent_manager/orchestrate.py`, delete this block (keeping one pair of blank lines between `GRAFO_LOGGER`'s docstring and `LaneKind`):

```python
STOPPED_PREFIX = "stopped before "
"""How `walk._stop` opens a stopped subtask's `detail` (addendum P4)."""


def stopped_before_phase(detail: str | None) -> str | None:
    """The phase a stopped subtask would have run next, read out of its detail.

    `walk._stop` writes `"stopped before <phase>"` and the summary has no
    field of its own for the phase, so this strips the prefix. A detail without
    the prefix, or no detail at all, gives None.
    """
    if detail is None or not detail.startswith(STOPPED_PREFIX):
        return None
    return detail[len(STOPPED_PREFIX):]


```

After the edit the surrounding text reads:

```python
GRAFO_LOGGER = "grafo"
"""grafo's logger. It logs every failing node with a traceback on its own
handler; `supervise` silences it so stdout stays one JSON line (T6)."""


LaneKind = Literal["done", "escalated", "stopped", "pending"]
```

- [ ] **Step 4: Run the orchestrator tests**

Run: `uv run pytest tests/test_orchestrate.py -v`
Expected: all PASS, including the lane stop oracles asserting `"before_phase": "implement"`, `"b_second"`, `"second"` and `None`.

- [ ] **Step 5: Confirm no stale references remain**

Run: `grep -rn "stopped_before_phase\|STOPPED_PREFIX" src tests`
Expected: no output (exit status 1).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all PASS (the opt-in e2e tests stay deselected by the default config).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "refactor(orchestrate): read SubtaskSummary.before_phase instead of parsing detail (S11)"
```
