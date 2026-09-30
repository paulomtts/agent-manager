<!-- task-pipeline: validated -->
# Point dispatch.py's AgentRunner at walk's evaluator (card 2c9171b0)

Parent: 223f9973 "One gate evaluator instead of two" (milestone 9c44c2fb). Spec of record: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, decision S3 (§3) and its §6 Testing bullet. This document narrows S3 to the dispatch half only.

## Base

Branch from `m13/task-add-gateverdict-and-the-4957ac74` (HEAD 02c2ff2), not master: only that branch has `GateVerdict`, `gate_values` and `evaluate_gates` in `src/agent_manager/runtime/walk.py`. This card consumes that API and does not modify `runtime/walk.py`.

## Scope

`src/agent_manager/dispatch.py`:

- Delete `gate_values`, `_render_verdict`, `evaluate_gates` and `_render_error` (currently :262-352).
- The import line `from agent_manager.runtime.walk import RESERVED_CONTEXT_KEYS, bind_arguments`
  (currently :35) becomes dead once those four functions are gone (nothing else in this file uses
  `RESERVED_CONTEXT_KEYS` or `bind_arguments`): replace it with `from agent_manager.runtime import
  walk`, which the new `walk.evaluate_gates(...)`/`walk.gate_values(...)` calls below need anyway.
- The one other caller of the deleted `_render_error` (the phase-`failed` record in the retry loop's `except Exception`, currently :466) switches to `walk._render_error`, which produces the identical `f"{type(error).__name__}: {error}"` string.
- The gate block after `classify` (currently :542-549) calls `walk.evaluate_gates(phase, walk.gate_values(context, phase.name, verdict.result), self.warnings)` and maps the returned `GateVerdict` to a `Verdict`:
  - `pass`: the `ok` verdict stands.
  - `warn`: the `ok` verdict stands. The warnings were already appended to `self.warnings` by `evaluate_gates`; dispatch must not append them again.
  - `fail`: `Verdict("gate_failed", detail=gv.detail["message"])`, not fatal, so `retry.on` still decides whether it re-dispatches.
  - `broken`: `Verdict("gate_failed", detail=<message>, fatal=True)`, so it is never retried whatever `retry.on` says. `<message>` is rebuilt by a small private helper in dispatch.py from `detail["gate"]`, `detail["reason"]`, `detail["error"]` and `detail["returned_type"]`, and matches the pre-S3 text exactly:
    - `reason == "raised"`: `phase {name!r} gate {gate!r} raised {ErrType}: {error}; a gate returns None to pass or a mapping verdict to fail, so this is a broken gate rather than a failed attempt`
    - `reason == "not_a_mapping"`: `phase {name!r} gate {gate!r} returned {returned_type}; a gate returns None to pass or a mapping verdict to fail, and anything else would be read as a pass by accident`
- `Verdict` (frozen dataclass, :158-169) is unchanged. Only how it gets constructed changes.
- Doc-only: the docstrings in `runtime/context.py:7` and `steps/reducers.py:383-384` that name `dispatch.gate_values` are updated to name `walk.gate_values`. No code changes in those files.

Out of scope: `runtime/walk.py` (the evaluator and `run_one_step`'s gate handling), `runtime/engine.py`, any of dispatch.py's execution logic outside the gate seam, and every other S-decision (S1, S2, S4-S11). Also out of scope, per spec §8: pygents' turn/phase model, the checkpoint format, the harness adapter contract, grafo, CI or typecheck gates, and the process-boundary monkeypatches.

## Observable behaviour

No CLI-observable change (spec §5). Envelopes, exit codes and `am status` shapes stay the same. So do the attempt statuses, the attempt and phase `detail` strings, the warning text and the retry counts for passing, warning, failing and broken gates on an agent phase.

## Error paths

- A gate whose parameters cannot be bound raises `EngineError` out of `evaluate_gates`. It still propagates through `AgentRunner`: the phase is recorded `failed` with the rendered error, then re-raised. It is not turned into a verdict.
- A `BaseException` raised by a gate (control-flow signals) propagates uncaught, as it does today.
- A broken gate is fatal even when `retry.on` lists `gate_failed`: one dispatch, then `AgentPhaseFailed(outcome="gate_failed")`.

## Tests

The placement rule is design spec §14: a pure function is tested beside its own logic, and the Engine is tested with a fake adapter or launcher and canned result files, never a real process. `tests/test_dispatch.py` is declared Engine tier. The shared evaluator's own behaviour, including the "gate that raises, tested once" case, is already covered in `tests/runtime/test_walk.py` on the base branch and is not duplicated here.

Remove from `tests/test_dispatch.py`. These call deleted functions, and their coverage now lives with the evaluator:
- `test_callable_gate_is_called_directly`
- `test_a_passing_callable_gate_sees_the_result`
- `test_a_callable_gate_that_raises_is_fatal_and_named_by_its_function_name`
- `test_a_callable_gate_returning_a_non_mapping_is_fatal_and_named_lambda`
- `test_a_callable_gate_with_an_unsupplied_parameter_is_a_named_engine_error`
- `test_a_callable_gate_warning_names_the_gate_by_its_function_name`
- `test_the_result_is_bound_under_both_result_and_the_phase_name`

Move, rewritten against `walk.gate_values` / `walk.evaluate_gates`, to `tests/runtime/test_walk.py`. This is the pure-function tier, beside the logic. These are additions only; the sibling's existing tests are left untouched:
- `test_a_reserved_key_is_not_overwritten_by_a_same_named_phase`
- `test_a_callable_gate_without_a_name_is_named_by_its_repr`: asserts `kind == "fail"` and the `repr`-named message.

Once both are gone, `tests/test_dispatch.py`'s `from agent_manager.runtime.walk import
RESERVED_CONTEXT_KEYS` (currently :33) and `import functools` (currently :12) are dead — remove
them if nothing else in the file still uses them.

Add to `tests/test_dispatch.py`, Engine tier, driven through the existing `_runner` / `FakeLauncher` helpers:
1. `test_agent_runner_maps_a_broken_gate_to_a_fatal_gate_failed`: parametrised over a raising gate and a non-mapping gate, with `retry.on` containing `gate_failed`. Asserts exactly one dispatch, `AgentPhaseFailed.outcome == "gate_failed"`, and a detail equal to the pre-S3 message above. This is S3 §6's thin test for the agent phase kind.
2. `test_a_warning_gate_passes_and_warns_exactly_once`: the phase result is returned, and `runner.warnings` holds the `phase 'explore' gate 'output_gate' warned: ...` line exactly once. This guards against appending the warning twice.
3. `test_a_failing_gate_records_the_rendered_message_as_the_detail`: the attempt and the `AgentPhaseFailed` detail equal `phase 'explore' gate '<lambda>' failed: blocked=x, detail=d`, and the failure is not fatal.
4. `test_an_unbindable_gate_parameter_propagates_as_engine_error`: `EngineError` escapes the runner with `.parameter`, `.function` and `.phase` intact, and the phase is recorded `failed`.

These existing Engine-tier tests must keep passing unedited: `test_a_valid_result_with_passing_gates_is_the_phase_result`, `test_a_retryable_gate_failure_re_dispatches_with_the_gate_detail`, `test_a_gate_failure_outside_retry_on_is_not_retried`, `test_a_gate_that_raises_stops_after_one_dispatch`, and the retry-loop tests further down the file.

## Verification

`uv run pytest` (full suite). There is no lint or typecheck step.

---

# Point dispatch.py's AgentRunner at walk's evaluator: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete dispatch.py's private copy of the gate evaluator and make `AgentRunner._attempt` map `runtime.walk.evaluate_gates`'s `GateVerdict` onto dispatch's `Verdict`, with no observable change.

**Architecture:** `walk.evaluate_gates` (already on the base branch) becomes the only gate evaluator. `AgentRunner._attempt` calls it with `walk.gate_values(...)` and translates the four kinds: `pass`/`warn` keep the `ok` verdict, `fail` becomes a non-fatal `gate_failed` carrying `detail["message"]`, `broken` becomes a fatal `gate_failed` whose detail is rebuilt, byte for byte, by a new private `_broken_gate_message` helper in dispatch.py. The work is pinned first with Engine-tier characterization tests that pass on today's code, then driven RED by two seam tests that patch `walk.evaluate_gates` and only pass once dispatch actually consults it.

**Tech Stack:** Python, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-point-dispatch-py-s-2c9171b0/docs/superpowers/specs/task-point-dispatch-py-s-2c9171b0-design.md` (prepended above). Spec of record: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, decision S3.

**Working directory for every command:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-point-dispatch-py-s-2c9171b0` (branch `m13/task-point-dispatch-py-s-2c9171b0`, cut from `m13/task-add-gateverdict-and-the-4957ac74`). Only code already on that base branch is assumed to exist: `walk.GateVerdict`, `walk.gate_values`, `walk.evaluate_gates`, `walk._render_error`, `walk.RESERVED_CONTEXT_KEYS`.

## Global Constraints

- Do not edit `src/agent_manager/runtime/walk.py` or `src/agent_manager/runtime/engine.py`.
- No CLI-observable change: envelopes, exit codes, `am status` shapes, attempt statuses, attempt and phase `detail` strings, warning text and retry counts all stay the same.
- `Verdict` (frozen dataclass in `src/agent_manager/dispatch.py`) is unchanged.
- A broken gate on an agent phase is `Verdict("gate_failed", fatal=True)`: never retried, whatever `retry.on` says.
- Broken-gate detail (`reason == "raised"`): `phase {name!r} gate {gate!r} raised {ErrType}: {error}; a gate returns None to pass or a mapping verdict to fail, so this is a broken gate rather than a failed attempt`
- Broken-gate detail (`reason == "not_a_mapping"`): `phase {name!r} gate {gate!r} returned {returned_type}; a gate returns None to pass or a mapping verdict to fail, and anything else would be read as a pass by accident`
- `dispatch.py` must stay pygents-free (checked by `tests/runtime/test_walk.py::test_importing_the_module_loads_no_pygents`).
- Test placement (design spec §14): pure-function tests in `tests/runtime/test_walk.py`, Engine-tier tests (fake launcher, canned result files) in `tests/test_dispatch.py`.
- Verification: `uv run pytest`. No lint, no typecheck.

## Notes for the executor

- **Spec gap, fixed in this plan (Task 3, Step 4):** the spec says the only edits outside dispatch.py are two docstrings, but two test helpers also call the deleted `dispatch.gate_values`: `tests/workflow/test_task.py:436` and `tests/workflow/test_integrate.py:128`. Deleting the function without repointing them breaks the full suite with `AttributeError`. They are repointed to `walk.gate_values` (both files already import `walk`), and their docstrings are updated. `walk.gate_values` builds the identical table, so their assertions do not change.
- **Orientation note:** the spec author's summary handed to the planner was truncated at 2000 characters mid-sentence (in its Tests list). This plan was written from the spec file on disk, not from that summary, so the truncation lost nothing here.
- **Why Task 1's tests pass immediately:** this card is a behaviour-preserving refactor. Task 1's tests are characterization tests. They pin today's observable behaviour so the rewire in Task 3 cannot drift, which means they pass before and after. The RED for the rewire is Task 3's seam tests, which fail until dispatch consults `walk.evaluate_gates`.

## Review Focus

1. A gate that returns a warning must produce exactly one warning line in `runner.warnings`. `walk.evaluate_gates` already appends it, so a second append in dispatch would double it (Task 1: `test_a_warning_gate_passes_and_warns_exactly_once`).
2. A gate that returns a falsy non-mapping (`[]`) must be a fatal broken gate, never a silent pass (Task 1: the `returns-empty-list` case of `test_agent_runner_maps_a_broken_gate_to_a_fatal_gate_failed`).
3. A warning from an earlier gate must survive a later gate failing in the same attempt (Task 1: `test_a_warning_before_a_failing_gate_is_kept_once`).
4. A phase with no result model still runs its gates, against `result=None`. `walk.gate_values` is annotated `Mapping` but has to accept `None` (Task 1: `test_a_result_less_phase_still_runs_its_gates_against_none`).
5. A `BaseException` control-flow signal raised by a gate must escape `AgentRunner` uncaught after one dispatch (Task 1: `test_a_control_flow_signal_from_a_gate_is_not_caught`).

---

### Task 1: Pin the agent-phase gate outcomes (Engine-tier characterization)

**Files:**
- Test: `tests/test_dispatch.py` (insert before `def test_a_phase_with_no_retry_block_dispatches_exactly_once`, currently line 880)

**Interfaces:**
- Consumes: existing helpers in `tests/test_dispatch.py`: `_agentic(*gates, **overrides) -> phases.Workflow`, `_runner(store, launcher, tmp_path, worktree, **overrides) -> (dispatch.AgentRunner, FakeAdapter)`, `FakeLauncher(results=[...])`, `_context(worktree)`, `_rendered()`, `_attempt_statuses(opened)`, `_phase_statuses(opened)`, fixtures `store`, `worktree`, constants `VALID_RESULT`, `RUN_ID`, `CARD`.
- Produces: helper `_last_phase_detail(opened) -> str | None` and module-level gates `raising_gate`, `chatty_gate`, `empty_list_gate`, class `_ControlSignal(BaseException)`. Task 3 reuses `_last_phase_detail`.

- [ ] **Step 1: Write the characterization tests**

In `tests/test_dispatch.py`, insert this block immediately before the line `def test_a_phase_with_no_retry_block_dispatches_exactly_once(store, tmp_path, worktree):`:

```python
# ── the gate seam: what AgentRunner does with a gate's answer ────────────────
# Engine tier (design §14): driven through `_runner` and `FakeLauncher` with
# canned result files, never a process. The evaluator's own behaviour is tested
# once, beside it, in tests/runtime/test_walk.py; these pin what an agent phase
# makes of each outcome, byte for byte, across the S3 rewire.

_BROKEN_RAISED = (
    "; a gate returns None to pass or a mapping verdict to fail, so this is a "
    "broken gate rather than a failed attempt"
)
_BROKEN_NOT_A_MAPPING = (
    "; a gate returns None to pass or a mapping verdict to fail, and anything "
    "else would be read as a pass by accident"
)


def raising_gate(result):
    raise RuntimeError("the gate itself is broken")


def chatty_gate(result):
    return "looks fine to me"


def empty_list_gate(result):
    return []


class _ControlSignal(BaseException):
    """Stands in for a pygents control-flow signal: a `BaseException`, not an `Exception`."""


def _last_phase_detail(opened) -> str | None:
    return [
        line.payload["detail"]
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ][-1]


@pytest.mark.parametrize(
    ("gate", "expected"),
    [
        (
            raising_gate,
            "phase 'explore' gate 'raising_gate' raised RuntimeError: "
            "the gate itself is broken" + _BROKEN_RAISED,
        ),
        (
            chatty_gate,
            "phase 'explore' gate 'chatty_gate' returned str" + _BROKEN_NOT_A_MAPPING,
        ),
        (
            empty_list_gate,
            "phase 'explore' gate 'empty_list_gate' returned list" + _BROKEN_NOT_A_MAPPING,
        ),
    ],
    ids=["raises", "returns-str", "returns-empty-list"],
)
def test_agent_runner_maps_a_broken_gate_to_a_fatal_gate_failed(
    store, tmp_path, worktree, gate, expected
):
    # S3 §6's thin test for the agent phase kind: retry.on lists gate_failed
    # and the budget is three, yet a broken gate is dispatched exactly once.
    workflow = _agentic(gate, retry=phases.Retry(3, ("schema_invalid", "gate_failed")))
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert caught.value.detail == expected
    assert len(launcher.calls) == 1
    assert _attempt_statuses(store) == [(1, "started"), (1, "gate_failed")]
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]
    assert _last_phase_detail(store) == expected


def test_a_warning_gate_passes_and_warns_exactly_once(store, tmp_path, worktree):
    def output_gate(result):
        return {"warn": "counts unusable"}

    workflow = _agentic(output_gate)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert result == {"summary": "explored the tree", "ok": True}
    assert runner.warnings == [
        "phase 'explore' gate 'output_gate' warned: counts unusable"
    ]
    assert len(launcher.calls) == 1
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]


def test_a_failing_gate_records_the_rendered_message_as_the_detail(
    store, tmp_path, worktree
):
    expected = "phase 'explore' gate '<lambda>' failed: blocked=x, detail=d"
    workflow = _agentic(lambda result: {"blocked": "x", "detail": "d"})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert caught.value.detail == expected
    # Not fatal: retry.on lists gate_failed, so the whole budget of two is spent
    # and the second prompt carries the rendered message as feedback.
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "gate_failed"), (2, "started"), (2, "gate_failed")
    ]
    assert _last_phase_detail(store) == expected
    second = (paths.attempt_dir(RUN_ID, CARD, "explore", 2) / "prompt.txt").read_text(
        encoding="utf-8"
    )
    assert expected in second.split(dispatch.FEEDBACK_HEADING, 1)[1]


def test_an_unbindable_gate_parameter_propagates_as_engine_error(
    store, tmp_path, worktree
):
    def output_gate(result, provided_verification):
        return None

    workflow = _agentic(output_gate)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(EngineError) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.parameter == "provided_verification"
    assert caught.value.function == "output_gate"
    assert caught.value.phase == "explore"
    assert len(launcher.calls) == 1
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]
    assert _last_phase_detail(store).startswith(
        "EngineError: phase 'explore', function 'output_gate', "
        "parameter 'provided_verification': no value for a required parameter"
    )


def test_a_warning_before_a_failing_gate_is_kept_once(store, tmp_path, worktree):
    def cautious(result):
        return {"warn": "coverage dipped"}

    def blocking(result):
        return {"blocked": "x"}

    workflow = _agentic(cautious, blocking, retry=None)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.detail == "phase 'explore' gate 'blocking' failed: blocked=x"
    assert runner.warnings == ["phase 'explore' gate 'cautious' warned: coverage dipped"]


def test_a_result_less_phase_still_runs_its_gates_against_none(store, tmp_path, worktree):
    seen: list[object] = []

    def output_gate(result):
        seen.append(result)

    workflow = _agentic(
        output_gate,
        name="spec",
        result=None,
        retry=None,
        writes="docs/superpowers/specs/{stem}.md",
    )
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert result is None
    assert seen == [None]
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]


def test_a_control_flow_signal_from_a_gate_is_not_caught(store, tmp_path, worktree):
    def output_gate(result):
        raise _ControlSignal()

    workflow = _agentic(output_gate)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(_ControlSignal):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert len(launcher.calls) == 1


```

- [ ] **Step 2: Run them against today's code**

Run: `uv run pytest tests/test_dispatch.py -v -k "broken_gate_to_a_fatal or warns_exactly_once or rendered_message_as_the_detail or unbindable_gate_parameter or warning_before_a_failing or result_less_phase_still_runs or control_flow_signal"`
Expected: 9 passed (3 parametrised cases plus 6 tests). These are characterization tests: they pin pre-S3 behaviour, so they pass now. If any fails, the expected string in the test is wrong. Fix the test to match today's output, never the source, before moving on.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dispatch.py
git commit -m "test: pin agent-phase gate outcomes before the S3 rewire"
```

---

### Task 2: Move the two pure-function gate tests beside walk's evaluator

**Files:**
- Modify: `tests/runtime/test_walk.py` (append at end of file; add `import functools` to the imports)
- Modify: `tests/test_dispatch.py:483-497` (remove the two tests that move or are dropped)

**Interfaces:**
- Consumes: `walk.gate_values(context, phase_name, result) -> dict[str, Any]`, `walk.evaluate_gates(phase, values, warnings) -> walk.GateVerdict`, `walk.RESERVED_CONTEXT_KEYS`, `AgentPhase` (already imported in `tests/runtime/test_walk.py` from `agent_manager.workflow.phases`).
- Produces: nothing later tasks use.

- [ ] **Step 1: Add the moved tests to the pure-function tier**

In `tests/runtime/test_walk.py`, change the import block's first lines from:

```python
import subprocess
import sys
```

to:

```python
import functools
import subprocess
import sys
```

Then append at the end of the file:

```python


# ── moved from tests/test_dispatch.py when dispatch's copy was deleted (S3) ──


def test_a_reserved_key_is_not_overwritten_by_a_same_named_phase():
    values = walk.gate_values({"worktree": Path("/repo/wt")}, "worktree", {"created": True})

    assert values["worktree"] == Path("/repo/wt")
    assert values["result"] == {"created": True}
    assert "worktree" in walk.RESERVED_CONTEXT_KEYS


def _blocking_gate(result, blocked):
    return {"blocked": blocked}


def test_a_callable_gate_without_a_name_is_named_by_its_repr():
    # A functools.partial has no __name__; the display name falls back to repr.
    gate = functools.partial(_blocking_gate, blocked="x")
    name = repr(gate)
    phase = AgentPhase("explore", "explorer", (), None, gates=(gate,))

    verdict = walk.evaluate_gates(
        phase, walk.gate_values({}, "explore", {"summary": "ok"}), []
    )

    assert verdict.kind == "fail"
    assert verdict.detail["message"] == f"phase 'explore' gate {name!r} failed: blocked=x"
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/runtime/test_walk.py -v -k "reserved_key_is_not_overwritten or named_by_its_repr"`
Expected: 2 passed. `walk`'s evaluator already has this behaviour. These tests move to the tier that owns the logic, so they pass on arrival.

- [ ] **Step 3: Remove the moved and dropped tests from the Engine tier**

In `tests/test_dispatch.py`, delete this block (currently lines 483-497, between `_workflow` and `_model_phase`):

```python
def test_the_result_is_bound_under_both_result_and_the_phase_name():
    values = dispatch.gate_values({"card": CARD}, "explore", {"summary": "ok"})

    assert values["result"] == {"summary": "ok"}
    assert values["explore"] == {"summary": "ok"}
    assert values["card"] == CARD


def test_a_reserved_key_is_not_overwritten_by_a_same_named_phase():
    values = dispatch.gate_values({"worktree": Path("/repo/wt")}, "worktree", {"created": True})

    assert values["worktree"] == Path("/repo/wt")
    assert values["result"] == {"created": True}
    assert "worktree" in RESERVED_CONTEXT_KEYS


```

After the deletion, `def _workflow(...)`'s `return document(functions)` is followed by two blank lines and then `def _model_phase(*gates, **overrides) -> phases.AgentPhase:`.

Then delete the now-unused import line (currently line 33):

```python
from agent_manager.runtime.walk import RESERVED_CONTEXT_KEYS
```

- [ ] **Step 4: Run both files**

Run: `uv run pytest tests/test_dispatch.py tests/runtime/test_walk.py -q`
Expected: all pass. `grep -n RESERVED_CONTEXT_KEYS tests/test_dispatch.py` should now print nothing.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_walk.py tests/test_dispatch.py
git commit -m "test: move dispatch's pure gate-table tests beside walk's evaluator"
```

---

### Task 3: Rewire AgentRunner to walk.evaluate_gates and delete dispatch's copy

**Files:**
- Modify: `src/agent_manager/dispatch.py:35` (import), `:262-352` (delete four functions, add `_broken_gate_message`), `:466` (`_render_error` caller), `:542-549` (gate block)
- Modify: `tests/test_dispatch.py:11-13` (drop `functools`), `:36` (import `walk`), `:512-617` (delete seven evaluator-level tests and `_blocking_gate`), plus two new seam tests
- Modify: `tests/workflow/test_task.py:417-438`, `tests/workflow/test_integrate.py:116-128` (repoint `dispatch.gate_values` to `walk.gate_values`)
- Modify (docstrings only): `src/agent_manager/runtime/context.py:7`, `src/agent_manager/steps/reducers.py:383-385`

**Interfaces:**
- Consumes: `walk.evaluate_gates(phase, values, warnings) -> walk.GateVerdict`; `walk.GateVerdict(kind: Literal["pass","warn","fail","broken"], detail: dict[str, Any] | None)`. Detail shapes: `fail` gives `{"gate", "verdict", "message"}`; `broken` gives `{"gate", "reason": "raised" | "not_a_mapping", "error", "returned_type" (not_a_mapping only)}`. Also `walk.gate_values(context, phase_name, result) -> dict[str, Any]`, `walk._render_error(error: BaseException) -> str`, and `_last_phase_detail` from Task 1.
- Produces: `dispatch._broken_gate_message(phase_name: str, detail: Mapping[str, Any]) -> str` (private). `dispatch.gate_values`, `dispatch.evaluate_gates`, `dispatch._render_verdict` and `dispatch._render_error` no longer exist.

- [ ] **Step 1: Write the failing seam tests**

In `tests/test_dispatch.py`, change the import line (currently line 36):

```python
from agent_manager.runtime import bridge
```

to:

```python
from agent_manager.runtime import bridge, walk
```

Then insert this block immediately after `test_a_control_flow_signal_from_a_gate_is_not_caught` (added in Task 1) and before `def test_a_phase_with_no_retry_block_dispatches_exactly_once`:

```python
def test_agent_runner_takes_a_failing_verdict_from_the_shared_evaluator(
    store, tmp_path, worktree, monkeypatch
):
    # The seam itself (S3): the gate below would pass, so only a runner that
    # asks `walk.evaluate_gates` can see this `fail`.
    calls: list[tuple[str, object, object]] = []

    def shared(phase, values, warnings):
        calls.append((phase.name, values["result"], values["explore"]))
        return walk.GateVerdict(
            "fail",
            {"gate": "g", "verdict": {"k": "v"}, "message": "from the shared evaluator"},
        )

    monkeypatch.setattr(walk, "evaluate_gates", shared)
    workflow = _agentic(lambda result: None, retry=None)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    produced = {"summary": "explored the tree", "ok": True}
    assert caught.value.outcome == "gate_failed"
    assert caught.value.detail == "from the shared evaluator"
    assert calls == [("explore", produced, produced)]
    assert _last_phase_detail(store) == "from the shared evaluator"


def test_agent_runner_maps_a_broken_verdict_from_the_shared_evaluator_to_fatal(
    store, tmp_path, worktree, monkeypatch
):
    monkeypatch.setattr(
        walk,
        "evaluate_gates",
        lambda phase, values, warnings: walk.GateVerdict(
            "broken", {"gate": "g", "reason": "raised", "error": KeyError("gone")}
        ),
    )
    # retry.on lists gate_failed with a budget of two: only `fatal` stops it.
    workflow = _agentic(lambda result: None)
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert caught.value.detail == (
        "phase 'explore' gate 'g' raised KeyError: 'gone'" + _BROKEN_RAISED
    )
    assert len(launcher.calls) == 1


```

- [ ] **Step 2: Run the seam tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v -k "from_the_shared_evaluator"`
Expected: 2 failed, each with `Failed: DID NOT RAISE <class 'agent_manager.errors.AgentPhaseFailed'>`. Dispatch still runs its own evaluator, which never calls the patched `walk.evaluate_gates`, and the real gate passes.

- [ ] **Step 3: Delete the evaluator-level tests whose functions are about to go**

In `tests/test_dispatch.py`, delete everything from the line `def test_callable_gate_is_called_directly():` through the end of `test_a_callable_gate_without_a_name_is_named_by_its_repr` (currently lines 512-618), up to but not including `STORY_ID = "2143808b"`. The deleted block is exactly these seven tests plus the helper:

- `test_callable_gate_is_called_directly`
- `test_a_passing_callable_gate_sees_the_result`
- `test_a_callable_gate_that_raises_is_fatal_and_named_by_its_function_name`
- `test_a_callable_gate_returning_a_non_mapping_is_fatal_and_named_lambda`
- `test_a_callable_gate_with_an_unsupplied_parameter_is_a_named_engine_error`
- `test_a_callable_gate_warning_names_the_gate_by_its_function_name`
- `def _blocking_gate(result, blocked):` (helper, used only by the next test)
- `test_a_callable_gate_without_a_name_is_named_by_its_repr`

Keep `_model_phase` (just above them), because `_agentic` uses it. After the deletion, `_model_phase`'s `return phases.AgentPhase(**fields)` is followed by two blank lines and then `STORY_ID = "2143808b"`.

Then change the top-of-file imports from:

```python
import dataclasses
import functools
import hashlib
```

to:

```python
import dataclasses
import hashlib
```

Check: `grep -n "functools\|dispatch.gate_values\|dispatch.evaluate_gates" tests/test_dispatch.py` prints nothing.

- [ ] **Step 4: Repoint the two workflow-tier helpers at walk.gate_values**

In `tests/workflow/test_task.py`, replace:

```python
    `walk.subtask_context` plus the caller's own `cli.gate_context` plus the
    document paths, then every earlier phase's result under its own name, then
    `dispatch.gate_values`' `result` / `<phase name>` overlay --
    `walk._gate_values` builds the identical table for the `verify` step.
    """
```

with:

```python
    `walk.subtask_context` plus the caller's own `cli.gate_context` plus the
    document paths, then every earlier phase's result under its own name, then
    `walk.gate_values`' `result` / `<phase name>` overlay -- the one table both
    agent phases and the `verify` step bind gates against.
    """
```

and replace:

```python
    return dispatch.gate_values(
        context, phase_name, results[phase_name] if result is None else result
    )
```

with:

```python
    return walk.gate_values(
        context, phase_name, results[phase_name] if result is None else result
    )
```

(`dispatch` stays imported there because other docstrings mention it. It is now unused as code, which is harmless: there is no lint step. Leave the import alone.)

In `tests/workflow/test_integrate.py`, replace:

```python
    """The binding table this phase's gates really see: the context, every
    earlier phase's result under its own name, then `dispatch.gate_values`'
    `result` / `<phase name>` overlay (`walk._gate_values` builds the same
    table for the `verify` step)."""
```

with:

```python
    """The binding table this phase's gates really see: the context, every
    earlier phase's result under its own name, then `walk.gate_values`'
    `result` / `<phase name>` overlay (the one table both the `resolve` agent
    phase and the `verify` step bind gates against)."""
```

and replace:

```python
    return dispatch.gate_values(context, phase_name, results[phase_name])
```

with:

```python
    return walk.gate_values(context, phase_name, results[phase_name])
```

(`dispatch` stays imported there because other docstrings mention it. It is unused as code, which is harmless: there is no lint step.)

Run: `uv run pytest tests/workflow/test_task.py tests/workflow/test_integrate.py -q`
Expected: all pass. The table is identical, and `dispatch.gate_values` still exists at this point.

- [ ] **Step 5: Rewire dispatch.py: the import**

In `src/agent_manager/dispatch.py`, replace line 35:

```python
from agent_manager.runtime.walk import RESERVED_CONTEXT_KEYS, bind_arguments
```

with:

```python
from agent_manager.runtime import walk
```

- [ ] **Step 6: Rewire dispatch.py: replace the four functions with the broken-gate helper**

In `src/agent_manager/dispatch.py`, delete everything from `def gate_values(` (currently line 262) through the end of `_render_error`:

```python
def _render_error(error: BaseException) -> str:
    """`walk._render_error`'s format, so both phase kinds fail the same way."""
    return f"{type(error).__name__}: {error}"
```

That removes `gate_values`, `_render_verdict`, `evaluate_gates` and `_render_error` (currently lines 262-351). In their place, between `classify` and `def _utcnow() -> datetime:`, put:

```python
def _broken_gate_message(phase_name: str, detail: Mapping[str, Any]) -> str:
    """The detail a broken gate on an agent phase records (architecture-cleanup S3).

    `walk.evaluate_gates` reports a gate that raised, or returned something
    other than `None` or a mapping, as `broken` and leaves the wording to each
    caller. This is the text dispatch has always journalled for one, rebuilt
    from the verdict's `gate`, `reason`, `error` and `returned_type`, so the
    attempt and phase rows an operator reads do not change with the evaluator.
    """
    gate = detail["gate"]
    if detail["reason"] == "raised":
        error = detail["error"]
        return (
            f"phase {phase_name!r} gate {gate!r} raised "
            f"{type(error).__name__}: {error}; a gate returns None to pass or "
            "a mapping verdict to fail, so this is a broken gate rather than a "
            "failed attempt"
        )
    return (
        f"phase {phase_name!r} gate {gate!r} returned "
        f"{detail['returned_type']}; a gate returns None to pass or a "
        "mapping verdict to fail, and anything else would be read as a "
        "pass by accident"
    )
```

- [ ] **Step 7: Rewire dispatch.py: the phase-`failed` record**

In `AgentRunner.__call__`'s `except Exception as error:` block, replace:

```python
            self._record_phase(
                phase, "failed", started_at, self.clock(), _render_error(error)
            )
```

with:

```python
            self._record_phase(
                phase, "failed", started_at, self.clock(), walk._render_error(error)
            )
```

- [ ] **Step 8: Rewire dispatch.py: the gate block in `_attempt`**

In `AgentRunner._attempt`, replace:

```python
        if verdict.status == "ok":
            failure = evaluate_gates(
                phase,
                gate_values(context, phase.name, verdict.result),
                self.warnings,
            )
            if failure is not None:
                verdict = failure
```

with:

```python
        if verdict.status == "ok":
            # The one evaluator both phase kinds share (S3). `pass` and `warn`
            # keep the `ok` verdict -- `evaluate_gates` has already appended any
            # warning to `self.warnings`, so nothing is appended here. `fail` is
            # retryable if `retry.on` says so; `broken` never is.
            gates = walk.evaluate_gates(
                phase,
                walk.gate_values(context, phase.name, verdict.result),
                self.warnings,
            )
            if gates.kind == "fail":
                verdict = Verdict("gate_failed", detail=gates.detail["message"])
            elif gates.kind == "broken":
                verdict = Verdict(
                    "gate_failed",
                    detail=_broken_gate_message(phase.name, gates.detail),
                    fatal=True,
                )
```

Check: `grep -n "RESERVED_CONTEXT_KEYS\|bind_arguments\|_render_verdict\|def evaluate_gates\|def gate_values\|def _render_error" src/agent_manager/dispatch.py` prints nothing.

- [ ] **Step 9: Run the seam tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v -k "from_the_shared_evaluator"`
Expected: 2 passed.

- [ ] **Step 10: Run the gate-related Engine and pure tiers**

Run: `uv run pytest tests/test_dispatch.py tests/runtime/test_walk.py tests/workflow/test_task.py tests/workflow/test_integrate.py -q`
Expected: all pass. That includes Task 1's characterization tests, the unedited `test_a_valid_result_with_passing_gates_is_the_phase_result`, `test_a_retryable_gate_failure_re_dispatches_with_the_gate_detail`, `test_a_gate_failure_outside_retry_on_is_not_retried` and `test_a_gate_that_raises_stops_after_one_dispatch`, and `test_importing_the_module_loads_no_pygents[agent_manager.dispatch]`.

- [ ] **Step 11: Update the two source docstrings that name the deleted function**

In `src/agent_manager/runtime/context.py`, replace:

```python
by field name, not alias: gates that need aliases get them from
`dispatch.gate_values`. Tuples come back as lists.
```

with:

```python
by field name, not alias: gates that need aliases get them from
`walk.gate_values`. Tuples come back as lists.
```

In `src/agent_manager/steps/reducers.py`, replace:

```python
    ``result`` is the critic phase's own result: both ``walk._gate_values``
    and ``dispatch.gate_values`` place it under exactly that key, which is why
    the parameter is not named after either phase.
```

with:

```python
    ``result`` is the critic phase's own result: ``walk.gate_values``, which
    builds the gate table for both phase kinds, places it under exactly that
    key, which is why the parameter is not named after either phase.
```

Check: `grep -rn "dispatch.gate_values\|dispatch.evaluate_gates" src tests` prints nothing.

- [ ] **Step 12: Run the full suite**

Run: `uv run pytest`
Expected: all pass, with no failures or errors.

- [ ] **Step 13: Commit**

```bash
git add src/agent_manager/dispatch.py src/agent_manager/runtime/context.py src/agent_manager/steps/reducers.py tests/test_dispatch.py tests/workflow/test_task.py tests/workflow/test_integrate.py
git commit -m "refactor: point AgentRunner at walk's shared gate evaluator (S3)"
```
