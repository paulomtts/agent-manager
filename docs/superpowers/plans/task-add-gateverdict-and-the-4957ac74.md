<!-- task-pipeline: validated -->
# Add GateVerdict and the unified evaluator to runtime/walk.py (card 4957ac74)

Parent story: 223f9973, "One gate evaluator instead of two". Milestone: 9c44c2fb. Source of truth: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, Decision S3 (lines 141-151), and §6 Testing, S3 bullet (line 298).

## Scope

This subtask covers `src/agent_manager/runtime/walk.py` and `tests/runtime/test_walk.py` only. Sibling card 2c9171b0 is blocked on this one. It deletes dispatch.py's `gate_values`/`evaluate_gates`/`_render_verdict`/`_render_error`, points `AgentRunner` at the evaluator added here, and maps `"broken"` to `Verdict("gate_failed", fatal=True)`. None of that work happens in this subtask. `dispatch.py` stays unchanged here.

1. Add `GateVerdict` to `runtime/walk.py`. It is a plain frozen `@dataclass`, not a Pydantic model: CLAUDE.md reserves Pydantic for process-boundary data, and this is internal runtime state. It has two fields:
   - `kind: Literal["pass", "warn", "fail", "broken"]`
   - `detail: dict | None`
2. Add one public evaluator to `walk.py`, `evaluate_gates(phase, values, warnings) -> GateVerdict`. It takes any phase object that has `.name` and `.gates`, so it works for both `phases.Step` and `phases.AgentPhase` without walk.py importing anything new. It follows the gate contract both copies share today:
   - It runs the gates in order and binds each one with `bind_arguments(gate, values, phase=..., function=_label(gate))`.
   - If a gate returns `None`, evaluation continues.
   - If a gate returns a mapping that contains `"warn"`, the evaluator appends the existing message (`phase {name!r} gate {gate!r} warned: {verdict['warn']}`) to `warnings` and continues.
   - If a gate returns any other mapping, the result is `kind="fail"`. Evaluation stops at the first one.
   - If a gate raises, or returns something that is not a mapping, the result is `kind="broken"`. Evaluation stops at the first one.
   - If no gate fails or breaks, the result is `kind="pass"`, or `kind="warn"` when at least one gate warned.
   - A binding failure is not `"broken"`. The `EngineError` from `bind_arguments` propagates out of the evaluator unchanged, because both callers treat it that way today: walk sends it to its catch-all, and dispatch lets it propagate.
3. `detail` is `None` for `"pass"`. For the other kinds it must carry enough for either caller to reproduce its current message without re-running the gate, under these exact keys (this dict is the contract sibling 2c9171b0 codes against, so the keys are fixed here rather than left for the implementer to invent):
   - `"fail"`: `{"gate": <name str>, "verdict": <raw mapping>, "message": <rendered "phase {name!r} gate {gate!r} failed: {k=v, ...sorted}">}`. That message is identical in both copies today.
   - `"broken"`: `{"gate": <name str>, "reason": "raised" | "not_a_mapping", "error": <exception>}`, plus `"returned_type": <type name str>` only when `reason == "not_a_mapping"`. For `"raised"`, `"error"` is the gate's own exception. For `"not_a_mapping"`, `"error"` is the `EngineError` that walk constructs today (the same message, `phase=`, and `function=`); `"returned_type"` is the returned value's `type(...).__name__`, kept as its own key (not just embedded in the `EngineError` message) so the sibling can read it without parsing text, to keep dispatch's wording.
   - `"warn"`: `{"warnings": <list[str], the collected warning messages>}`.
4. Replace the private `_evaluate_gates` (walk.py:302-328) and `_render_verdict` (330-331) with the new evaluator, and make `run_one_step` (364-416) call it. The binding-table builder becomes public as `gate_values` so the sibling can call it. `walk._gate_values` must stay importable under that name, because `tests/test_engine.py:225` calls it and existing tests must pass unchanged. `_label`, `_skip_target`, `_bind_result`, and `_render_error` stay. `_GateFailed` stays defined because Decision S7 (line 199) names it.
5. Do not add a pygents import. Rule 1 (walk.py:10-13) is enforced by `tests/runtime/test_walk.py`.

## Observable behaviour (unchanged on the deterministic-step path)

`run_one_step` maps each verdict kind to exactly what it records today:

- `"pass"` / `"warn"`: the result is the same as today. Warnings are collected in the same order with the same text. `skip_to` is still computed from the same binding table. The phase is recorded `done`, and the result is `_Outcome(ok=True, result=..., warnings=..., skip_to=...)`.
- `"fail"`: the phase is recorded `failed` with detail `phase 'x' gate 'g' failed: k=v`, and the result is `_Outcome(ok=False, detail=that, warnings=warnings)`. This is today's `_GateFailed` branch (walk.py:400-404).
- `"broken"`: this goes through today's catch-all path (walk.py:405-414). The phase is recorded `failed` with detail `_render_error(exc)`, which is `"{ExcType}: {exc}"` for the exception carried in `detail`. The result is `_Outcome(ok=False, ...)`. That is byte-for-byte the string recorded today, both for a gate that raises and for a gate that returns a non-mapping (the non-mapping case renders as `EngineError: ...`). A broken gate is never silently swallowed, and nothing escapes `run_one_step`.
- The following still go to the catch-all exactly as before: a binding failure (in a gate, in `when`, or in `run`), a non-mapping `run` result, or any exception from `run` or `when`.

## Error paths

- A gate raises any `Exception` subclass → `GateVerdict("broken", ...)`. `BaseException`s that are not `Exception` (control-flow signals) are not caught by the evaluator, matching dispatch's current `except Exception`.
- A gate returns a non-`None`, non-mapping value → `GateVerdict("broken", ...)`. It is never read as a pass.
- A gate's parameters cannot be bound → `EngineError` propagates out of the evaluator. It is not a verdict.

## Tests

The placement rule is design spec §14 (2026-09-23 design, lines 505-520). The evaluator and `GateVerdict` are pure and do no I/O, so they get unit tests in `tests/runtime/test_walk.py`, which mirrors `runtime/walk.py`. None of these tests go in `test_engine.py` or an integration fixture. The existing pygents-hygiene test in that file stays as it is.

1. **Unit, `tests/runtime/test_walk.py`:** all gates return `None` → `kind == "pass"`, `detail is None`, and `warnings` is untouched.
2. **Unit, `tests/runtime/test_walk.py`:** one gate warns and a later gate passes → `kind == "warn"`, and `warnings` holds the exact existing message.
3. **Unit, `tests/runtime/test_walk.py`:** a gate returns a failing mapping → `kind == "fail"`, the detail carries the rendered `phase ... gate ... failed: k=v` message, and gates after it are not called.
4. **Unit, `tests/runtime/test_walk.py`:** a gate that raises is tested once against the shared evaluator (S3 §6) → `kind == "broken"`, the detail carries the original exception and the gate name, and later gates are not called.
5. **Unit, `tests/runtime/test_walk.py`:** a gate returns a non-mapping → `kind == "broken"`, the detail carries the `EngineError` and the returned type name.
6. **Unit, `tests/runtime/test_walk.py`:** a gate with an unbindable parameter → `EngineError` propagates and no verdict is returned.
7. **Unit, `tests/runtime/test_walk.py`:** walk's thin "broken maps to its own outcome" test (S3 §6). It calls `run_one_step` with a `Step` whose gate raises and checks `_Outcome.ok is False` and that the phase row is recorded `failed` with detail `"{ExcType}: {msg}"`. It uses an in-memory or temporary `Store` and a fixed clock, which is the same lightweight setup existing `run_one_step` callers use, with no git and no harness. Dispatch's counterpart test belongs to sibling 2c9171b0.

These must pass unchanged: `tests/test_dispatch.py`, `tests/test_engine.py`, `tests/workflow/test_task.py`, `tests/workflow/test_integrate.py`, `tests/runtime/test_checkpoint.py`, and the existing pygents-hygiene test.

## Verification

`uv run pytest`. There is no typecheck or lint command (CLAUDE.md).

## Out of scope

- Any edit to `dispatch.py`, which belongs to sibling 2c9171b0.
- Changes to pygents' turn/phase model, the checkpoint format, or the harness adapter contract.
- Any typecheck or CI command.
- The legitimate monkeypatch calls.
- grafo.
- The `_Signal` marker base, which is S7.

---

# GateVerdict and the Unified Gate Evaluator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `GateVerdict` dataclass and one public `evaluate_gates` function to `src/agent_manager/runtime/walk.py`, and repoint `run_one_step` at them without changing a single recorded outcome.

**Architecture:** `evaluate_gates` runs a phase's gates in order and returns a `GateVerdict` (`pass`/`warn`/`fail`/`broken`) instead of raising. The one thing it still lets escape is a binding failure (`EngineError`). `run_one_step` turns `fail` into today's `_GateFailed` raise. It turns `broken` into a re-raise of the carried exception, so both go down the same `except` branches that record today's strings. `gate_values` becomes the public name of the binding-table builder, and `_gate_values` stays as an alias.

**Tech Stack:** Python 3, stdlib `dataclasses`, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m13/task-add-gateverdict-and-the-4957ac74/docs/superpowers/specs/task-add-gateverdict-and-the-4957ac74-design.md` (the spec is reproduced verbatim above). Upstream note: the spec author's orientation summary that came with this task was cut off at its 2000-character cap. That means the spec stage over-ran its brief. This plan was written from the spec file on disk, not from that summary, so nothing in it depends on the missing text.

## Global Constraints

- Work only in `src/agent_manager/runtime/walk.py` and `tests/runtime/test_walk.py`. Do not edit `dispatch.py` or any other file.
- `runtime/walk.py` must not import pygents, directly or transitively (Rule 1). The existing parametrized `test_importing_the_module_loads_no_pygents` enforces this.
- `GateVerdict` is a plain `@dataclass(frozen=True)`, not Pydantic.
- `GateVerdict.kind: Literal["pass", "warn", "fail", "broken"]`, `GateVerdict.detail: dict | None`.
- Detail keys are fixed exactly: fail → `gate`, `verdict`, `message`. broken → `gate`, `reason` (`"raised"` | `"not_a_mapping"`), `error`, plus `returned_type` only for `not_a_mapping`. warn → `warnings`. pass → `None`.
- `walk._gate_values` must remain importable (`tests/test_engine.py:225`).
- `_GateFailed`, `_label`, `_skip_target`, `_bind_result`, and `_render_error` stay.
- Existing suites pass unchanged. Verification is `uv run pytest`. There is no lint or typecheck command.
- The branch is `m13/task-add-gateverdict-and-the-4957ac74`, cut fresh from master. Do not assume any sibling code exists.

## Review Focus

1. A gate that returns a non-mapping (for example an `int`) during `run_one_step`: the operator expects the recorded detail to be byte-for-byte today's `EngineError: phase 'a', function 'chatty': gate returned int; ...` string, not a new wording. Pinned in Task 1.
2. Warnings emitted by earlier gates before a later gate breaks: the operator expects them to stay on the `_Outcome`, in order, exactly as today. Pinned in Task 1.
3. A gate returning an empty mapping `{}`: the operator expects a `fail` (today it fails with an empty `failed: ` tail), not a pass or a warn. Pinned in Task 2.
4. A gate returning a mapping that has `"warn"` alongside other keys: the operator expects `warn` to win, as today, not a fail. Pinned in Task 2.
5. A gate raising a `BaseException` that is not an `Exception` (a control-flow signal): the operator expects it to escape the evaluator rather than be downgraded to `broken`. Pinned in Task 2.

---

### Task 1: Pin run_one_step's current gate outcomes (characterization)

These tests describe behaviour that exists today, so they PASS on the current code. That is deliberate. They are the guard the refactor in Task 3 must keep green, and the only honest RED for a no-behaviour-change refactor is the one in Task 3. Spec test 7 lives here.

**Files:**
- Modify: `tests/runtime/test_walk.py` (append after line 40; extend the imports at lines 11-14)

**Interfaces:**
- Consumes: existing `walk.run_one_step(*, phase, table, store, story_id, subtask, clock) -> _Outcome`, `store_module.Store.open(repo_dir, run_id)`, `Store.journal.read()` (lines with `.event`, `.phase`, `.payload`).
- Produces: the module-level test helpers `RUN_ID`, `STORY_ID`, `CARD_ID`, `FIXED`, the `store` fixture, `_subtask()`, and `_phase_rows(opened)`. Tasks 2 and 3 reuse them.

- [ ] **Step 1: Replace the import block and add the shared fixtures**

Replace lines 11-14 of `tests/runtime/test_walk.py`:

```python
import subprocess
import sys

import pytest
```

with:

```python
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_manager import models, store as store_module
from agent_manager.runtime import walk
from agent_manager.runtime.errors import EngineError
from agent_manager.workflow.phases import AgentPhase, Step
```

Then append this to the end of the file, after `test_importing_the_module_loads_no_pygents`:

```python


# ── gate evaluation (architecture-cleanup S3) ────────────────────────────────
#
# Unit tier per design §14: the evaluator and GateVerdict are pure. The
# run_one_step tests use a real temp Store and a fixed clock -- the same
# lightweight setup tests/test_engine.py's run_one_step tests use -- with no
# git and no harness.

RUN_ID = "run-2026-09-30-01"
STORY_ID = "223f9973"
CARD_ID = "4957ac74"
FIXED = datetime(2026, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=CARD_ID,
        branch=f"m13/task-add-gateverdict-and-the-{CARD_ID}",
        base_branch="m13/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _phase_rows(opened) -> list[tuple[str | None, str, str | None]]:
    return [
        (line.phase, line.payload["status"], line.payload["detail"])
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ]


def _run(store, step: Step, table=None):
    return walk.run_one_step(
        phase=step,
        table={} if table is None else table,
        store=store,
        story_id=STORY_ID,
        subtask=_subtask(),
        clock=lambda: FIXED,
    )
```

- [ ] **Step 2: Write the characterization tests (spec test 7 plus Review Focus 1 and 2)**

Append to `tests/runtime/test_walk.py`:

```python


def test_run_one_step_records_a_raising_gate_as_failed_with_the_error_text(store):
    """Spec test 7: walk's own outcome for a broken gate (S3 §6)."""
    later_calls: list[object] = []

    def exploding(result):
        raise ValueError("gate blew up")

    def later(result):
        later_calls.append(result)

    outcome = _run(store, Step("verify", lambda: {"ok": True}, gates=(exploding, later)))

    assert outcome.ok is False
    assert outcome.detail == "ValueError: gate blew up"
    assert later_calls == []
    assert _phase_rows(store) == [
        ("verify", "started", None),
        ("verify", "failed", "ValueError: gate blew up"),
    ]


def test_run_one_step_records_a_non_mapping_gate_as_failed_engine_error(store):
    """Review Focus 1: the recorded text is today's, byte for byte."""

    def chatty(result):
        return 3

    outcome = _run(store, Step("a", lambda: {}, gates=(chatty,)))

    expected = (
        "EngineError: phase 'a', function 'chatty': gate returned int; a gate "
        "returns None to pass or a mapping verdict to fail, and anything else "
        "would be read as a pass by accident"
    )
    assert outcome.ok is False
    assert outcome.detail == expected
    assert _phase_rows(store)[-1] == ("a", "failed", expected)


def test_run_one_step_keeps_earlier_warnings_when_a_later_gate_breaks(store):
    """Review Focus 2: warnings gathered before the break stay on the outcome."""

    def first(result):
        return {"warn": "one"}

    def second(result):
        return {"warn": "two"}

    def exploding(result):
        raise OSError("disk went away")

    outcome = _run(store, Step("a", lambda: {}, gates=(first, second, exploding)))

    assert outcome.ok is False
    assert outcome.detail == "OSError: disk went away"
    assert outcome.warnings == [
        "phase 'a' gate 'first' warned: one",
        "phase 'a' gate 'second' warned: two",
    ]
```

- [ ] **Step 3: Run the new tests and confirm they PASS on current code**

Run: `uv run pytest tests/runtime/test_walk.py -v`
Expected: all PASS, including the seven parametrized pygents-hygiene cases. If one of the three new tests fails, the test is wrong about today's behaviour. Fix the test, not `walk.py`.

- [ ] **Step 4: Commit**

```bash
git add tests/runtime/test_walk.py
git commit -m "Pin run_one_step's broken-gate outcomes before consolidating the evaluator"
```

---

### Task 2: GateVerdict, public gate_values, and evaluate_gates

This adds the new type and function next to the old private helpers. `run_one_step` is not touched yet, so the suite stays green throughout.

**Files:**
- Modify: `src/agent_manager/runtime/walk.py:269-331` (add `GateVerdict` after `_GateFailed`; rename `_gate_values` to `gate_values` with an alias; add `evaluate_gates` after `_label`)
- Test: `tests/runtime/test_walk.py`

**Interfaces:**
- Consumes: `bind_arguments(fn, values, args=None, *, phase, function) -> dict`, `_label(fn) -> str`, `EngineError(reason, *, phase=None, function=None, parameter=None)`, and the Task 1 test helpers.
- Produces:
  - `walk.GateVerdict(kind: Literal["pass","warn","fail","broken"], detail: dict | None)`, a frozen dataclass with positional fields.
  - `walk.gate_values(context: Mapping[str, Any], phase_name: str, result: Mapping[str, Any]) -> dict[str, Any]`, with `walk._gate_values is walk.gate_values`.
  - `walk.evaluate_gates(phase: phase_model.Step | phase_model.AgentPhase, values: Mapping[str, Any], warnings: list[str]) -> GateVerdict`

- [ ] **Step 1: Write the failing evaluator tests (spec tests 1-6, Review Focus 3-5, gate_values alias)**

Append to `tests/runtime/test_walk.py`:

```python


def _step(*gates) -> Step:
    return Step("verify", lambda: {}, gates=tuple(gates))


def test_gate_values_is_public_and_the_private_name_still_resolves():
    context = {"card": "c1", "worktree": "/w"}

    values = walk.gate_values(context, "build", {"x": 1})

    assert values == {"card": "c1", "worktree": "/w", "result": {"x": 1}, "build": {"x": 1}}
    assert walk._gate_values is walk.gate_values


def test_evaluate_gates_passes_when_every_gate_returns_none():
    """Spec test 1."""
    calls: list[str] = []

    def first(result):
        calls.append("first")

    def second(card):
        calls.append("second")

    warnings: list[str] = []

    verdict = walk.evaluate_gates(_step(first, second), {"result": {}, "card": "c1"}, warnings)

    assert verdict == walk.GateVerdict("pass", None)
    assert verdict.detail is None
    assert calls == ["first", "second"]
    assert warnings == []


def test_evaluate_gates_warns_and_keeps_going_after_a_warning_gate():
    """Spec test 2."""
    calls: list[str] = []

    def cautious(result):
        return {"warn": "coverage dipped"}

    def fine(result):
        calls.append("fine")

    warnings = ["earlier"]

    verdict = walk.evaluate_gates(_step(cautious, fine), {"result": {}}, warnings)

    message = "phase 'verify' gate 'cautious' warned: coverage dipped"
    assert verdict.kind == "warn"
    assert verdict.detail == {"warnings": [message]}
    assert warnings == ["earlier", message]
    assert calls == ["fine"]


def test_evaluate_gates_fails_at_the_first_failing_mapping_and_stops():
    """Spec test 3, driven through an AgentPhase to show both phase kinds work."""
    later_calls: list[str] = []

    def blocking(result):
        return {"reason": "red", "count": 2}

    def later(result):
        later_calls.append("later")

    phase = AgentPhase("review", "critic", (), None, gates=(blocking, later))

    verdict = walk.evaluate_gates(phase, {"result": {}}, [])

    assert verdict.kind == "fail"
    assert verdict.detail == {
        "gate": "blocking",
        "verdict": {"reason": "red", "count": 2},
        "message": "phase 'review' gate 'blocking' failed: count=2, reason=red",
    }
    assert later_calls == []


def test_evaluate_gates_reports_a_raising_gate_as_broken():
    """Spec test 4: the one "raises" test against the shared evaluator (S3 §6)."""
    boom = ValueError("gate blew up")
    later_calls: list[str] = []

    def exploding(result):
        raise boom

    def later(result):
        later_calls.append("later")

    verdict = walk.evaluate_gates(_step(exploding, later), {"result": {}}, [])

    assert verdict.kind == "broken"
    assert verdict.detail == {"gate": "exploding", "reason": "raised", "error": boom}
    assert verdict.detail["error"] is boom
    assert later_calls == []


def test_evaluate_gates_reports_a_non_mapping_gate_as_broken():
    """Spec test 5."""

    def chatty(result):
        return 3

    verdict = walk.evaluate_gates(_step(chatty), {"result": {}}, [])

    assert verdict.kind == "broken"
    assert set(verdict.detail) == {"gate", "reason", "error", "returned_type"}
    assert verdict.detail["gate"] == "chatty"
    assert verdict.detail["reason"] == "not_a_mapping"
    assert verdict.detail["returned_type"] == "int"
    error = verdict.detail["error"]
    assert isinstance(error, EngineError)
    assert error.phase == "verify"
    assert error.function == "chatty"
    assert str(error) == (
        "phase 'verify', function 'chatty': gate returned int; a gate returns "
        "None to pass or a mapping verdict to fail, and anything else would be "
        "read as a pass by accident"
    )


def test_evaluate_gates_lets_a_binding_failure_propagate():
    """Spec test 6: a binding failure is not a verdict."""

    def needs(missing):
        return None

    with pytest.raises(EngineError) as info:
        walk.evaluate_gates(_step(needs), {"result": {}}, [])

    assert info.value.phase == "verify"
    assert info.value.function == "needs"
    assert info.value.parameter == "missing"


def test_evaluate_gates_fails_an_empty_mapping():
    """Review Focus 3: `{}` is a failing verdict today, not a pass."""

    def empty(result):
        return {}

    verdict = walk.evaluate_gates(_step(empty), {"result": {}}, [])

    assert verdict.kind == "fail"
    assert verdict.detail["message"] == "phase 'verify' gate 'empty' failed: "


def test_evaluate_gates_lets_warn_win_over_other_keys():
    """Review Focus 4: a mapping holding `warn` warns, whatever else it holds."""

    def mixed(result):
        return {"warn": "soft", "blocked": "x"}

    warnings: list[str] = []

    verdict = walk.evaluate_gates(_step(mixed), {"result": {}}, warnings)

    assert verdict.kind == "warn"
    assert warnings == ["phase 'verify' gate 'mixed' warned: soft"]


def test_evaluate_gates_does_not_swallow_a_base_exception():
    """Review Focus 5: control-flow signals are not `broken`."""

    class _Signal(BaseException):
        pass

    def signalling(result):
        raise _Signal()

    with pytest.raises(_Signal):
        walk.evaluate_gates(_step(signalling), {"result": {}}, [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_walk.py -v`
Expected: the ten new tests FAIL with `AttributeError: module 'agent_manager.runtime.walk' has no attribute 'gate_values'` / `'evaluate_gates'` / `'GateVerdict'`. The Task 1 tests and the hygiene tests still PASS.

- [ ] **Step 3: Add GateVerdict after `_GateFailed`**

In `src/agent_manager/runtime/walk.py`, directly after the `_GateFailed` class (after line 274, `super().__init__(detail)`), insert:

```python


@dataclass(frozen=True)
class GateVerdict:
    """What one pass over a phase's gates concluded (architecture-cleanup S3).

    A plain dataclass rather than a Pydantic model: it never crosses a process
    boundary. `detail` is `None` for `pass`; otherwise it carries enough for
    either caller -- `run_one_step` here, `dispatch.AgentRunner` -- to render
    its own message without re-running a gate:

    - `fail`: `gate`, `verdict` (the raw mapping), `message` (the rendered
      `phase ... gate ... failed: k=v` line).
    - `broken`: `gate`, `reason` (`"raised"` or `"not_a_mapping"`), `error`
      (the gate's exception, or the `EngineError` built for a non-mapping),
      plus `returned_type` for `not_a_mapping`.
    - `warn`: `warnings`, the messages this evaluation appended.
    """

    kind: Literal["pass", "warn", "fail", "broken"]
    detail: dict[str, Any] | None
```

- [ ] **Step 4: Make the binding-table builder public, keeping the old name**

In `src/agent_manager/runtime/walk.py`, change the definition line

```python
def _gate_values(
    context: Mapping[str, Any], phase_name: str, result: Mapping[str, Any]
) -> dict[str, Any]:
```

to

```python
def gate_values(
    context: Mapping[str, Any], phase_name: str, result: Mapping[str, Any]
) -> dict[str, Any]:
```

and directly after that function's final `return values` line, insert:

```python


_gate_values = gate_values
"""The pre-S3 private name, kept because `tests/test_engine.py` binds through it."""
```

Leave the two `_gate_values(...)` calls inside `run_one_step` as they are for now (the alias keeps them working). Task 3 repoints them.

- [ ] **Step 5: Add `evaluate_gates` after `_label`**

In `src/agent_manager/runtime/walk.py`, directly after the `_label` function (after `return getattr(fn, "__name__", repr(fn))`) and before `def _evaluate_gates(`, insert:

```python


def evaluate_gates(
    phase: phase_model.Step | phase_model.AgentPhase,
    values: Mapping[str, Any],
    warnings: list[str],
) -> GateVerdict:
    """Run `phase`'s gates in order and say what they concluded.

    The one gate contract both phase kinds share: `None` passes, a mapping
    holding `warn` appends a warning and continues, any other mapping fails.
    A gate that raises an `Exception`, or returns anything that is not a
    mapping, is `broken` -- never read as a pass. Evaluation stops at the first
    `fail` or `broken`.

    A gate whose parameters cannot be bound is a workflow wiring bug, not a
    verdict: the `EngineError` from `bind_arguments` propagates unchanged.
    """
    warned: list[str] = []
    for gate in phase.gates:
        name = _label(gate)
        kwargs = bind_arguments(gate, values, phase=phase.name, function=name)
        try:
            verdict = gate(**kwargs)
        except Exception as error:
            return GateVerdict(
                "broken", {"gate": name, "reason": "raised", "error": error}
            )
        if verdict is None:
            continue
        if not isinstance(verdict, Mapping):
            returned_type = type(verdict).__name__
            error = EngineError(
                f"gate returned {returned_type}; a gate returns None to pass "
                "or a mapping verdict to fail, and anything else would be read as a "
                "pass by accident",
                phase=phase.name,
                function=name,
            )
            return GateVerdict(
                "broken",
                {
                    "gate": name,
                    "reason": "not_a_mapping",
                    "error": error,
                    "returned_type": returned_type,
                },
            )
        if "warn" in verdict:
            message = f"phase {phase.name!r} gate {name!r} warned: {verdict['warn']}"
            warnings.append(message)
            warned.append(message)
            continue
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(verdict.items()))
        return GateVerdict(
            "fail",
            {
                "gate": name,
                "verdict": verdict,
                "message": f"phase {phase.name!r} gate {name!r} failed: {rendered}",
            },
        )
    if warned:
        return GateVerdict("warn", {"warnings": warned})
    return GateVerdict("pass", None)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_walk.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/runtime/walk.py tests/runtime/test_walk.py
git commit -m "Add GateVerdict and the shared evaluate_gates to runtime/walk.py"
```

---

### Task 3: Point run_one_step at evaluate_gates and delete the old private helpers

**Files:**
- Modify: `src/agent_manager/runtime/walk.py` (the `_evaluate_gates` and `_render_verdict` functions, originally lines 302-331; the gate call at original line 398-399 inside `run_one_step`)
- Test: `tests/runtime/test_walk.py`

**Interfaces:**
- Consumes: `walk.evaluate_gates`, `walk.GateVerdict`, and `walk.gate_values` from Task 2, `_GateFailed(detail: str)`, and the Task 1 helpers `_run`, `_phase_rows`, `store`.
- Produces: `run_one_step` whose gate handling is `evaluate_gates` → `fail` raises `_GateFailed(detail["message"])`, `broken` re-raises `detail["error"]` into the existing catch-all. No new public names.

- [ ] **Step 1: Write the failing tests that run_one_step honours the verdict it is given**

Append to `tests/runtime/test_walk.py`:

```python


def test_run_one_step_records_a_fail_verdict_with_its_message(store, monkeypatch):
    """run_one_step takes its gate outcome from evaluate_gates, not a private copy."""
    monkeypatch.setattr(
        walk,
        "evaluate_gates",
        lambda phase, values, warnings: walk.GateVerdict(
            "fail", {"gate": "g", "verdict": {"k": "v"}, "message": "from the verdict"}
        ),
    )

    outcome = _run(store, Step("a", lambda: {}))

    assert outcome.ok is False
    assert outcome.detail == "from the verdict"
    assert _phase_rows(store)[-1] == ("a", "failed", "from the verdict")


def test_run_one_step_sends_a_broken_verdict_through_the_catch_all(store, monkeypatch):
    monkeypatch.setattr(
        walk,
        "evaluate_gates",
        lambda phase, values, warnings: walk.GateVerdict(
            "broken", {"gate": "g", "reason": "raised", "error": KeyError("gone")}
        ),
    )

    outcome = _run(store, Step("a", lambda: {}))

    assert outcome.ok is False
    assert outcome.detail == "KeyError: 'gone'"
    assert _phase_rows(store)[-1] == ("a", "failed", "KeyError: 'gone'")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_walk.py -k "fail_verdict or broken_verdict" -v`
Expected: both FAIL on `assert outcome.ok is False` (`assert True is False`), because `run_one_step` still calls the private `_evaluate_gates`, and with no gates on the step that records `done`.

- [ ] **Step 3: Repoint run_one_step**

In `src/agent_manager/runtime/walk.py`, inside `run_one_step`, replace

```python
        _evaluate_gates(phase, _gate_values(table, phase.name, result), warnings)
        skip_to = _skip_target(phase, _gate_values(table, phase.name, result))
```

with

```python
        verdict = evaluate_gates(phase, gate_values(table, phase.name, result), warnings)
        if verdict.kind == "fail":
            raise _GateFailed(verdict.detail["message"])
        if verdict.kind == "broken":
            # Re-raised into the catch-all below on purpose: it records
            # `_render_error(error)`, the exact string a broken gate recorded
            # before the evaluator was shared.
            raise verdict.detail["error"]
        skip_to = _skip_target(phase, gate_values(table, phase.name, result))
```

- [ ] **Step 4: Delete the replaced private helpers**

In `src/agent_manager/runtime/walk.py`, delete the whole `_evaluate_gates` function (from `def _evaluate_gates(` through its last line `raise _GateFailed(f"phase {phase.name!r} gate {name!r} failed: {_render_verdict(verdict)}")`) and the whole `_render_verdict` function:

```python
def _render_verdict(verdict: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(verdict.items()))
```

Keep `_GateFailed`, `_label`, `_skip_target`, `_bind_result`, `_render_error`, and the `_gate_values` alias.

- [ ] **Step 5: Run the walk tests to verify they pass**

Run: `uv run pytest tests/runtime/test_walk.py -v`
Expected: all PASS, including the Task 1 characterization tests (unchanged recorded strings) and the pygents-hygiene cases.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all PASS with no test file other than `tests/runtime/test_walk.py` modified. In particular `tests/test_engine.py` (including `walk._gate_values` at line 225 and `test_run_one_step_names_a_callable_gate_by_its_function_name`), `tests/test_dispatch.py`, `tests/workflow/test_task.py`, `tests/workflow/test_integrate.py`, and `tests/runtime/test_checkpoint.py` must pass.

- [ ] **Step 7: Confirm scope**

Run: `git diff --stat master`
Expected: only `src/agent_manager/runtime/walk.py` and `tests/runtime/test_walk.py` (plus the spec and plan docs already on the branch). `src/agent_manager/dispatch.py` must not appear.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/runtime/walk.py tests/runtime/test_walk.py
git commit -m "Route run_one_step's gates through evaluate_gates; drop the private copies"
```
