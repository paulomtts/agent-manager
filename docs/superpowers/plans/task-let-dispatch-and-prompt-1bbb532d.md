<!-- task-pipeline: validated -->
# Let dispatch and prompt take phase-model objects (subtask 1bbb532d)

Parent story: f9c19dc3 "Run a workflow on pygents" (Milestone 6: the pygents engine). Plan task: `docs/superpowers/plans/2026-09-25-pygents-engine.md`, Task 3.2. Milestone spec of record: `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (G1-G10), extending the 2026-09-23, 2026-09-24 and 2026-09-25-integrate specs.

## Goal

Widen `dispatch` and `prompt` so they accept both the YAML phase type (`workflow.loader.AgentPhase`, `result: str | None`, `gates: tuple[str, ...]`, `retry: RetryPolicy | None`) and the declared phase model (`workflow.phases.AgentPhase`, `result: type[BaseModel] | None`, `gates: tuple[Callable, ...]`, `retry: phases.Retry | None`). This is an accept-both widening only: the YAML engine's behaviour, messages and journal rows stay identical.

## Scope

In `src/agent_manager/dispatch.py`:

- `evaluate_gates(phase, workflow, values, warnings)` (the `for name in phase.gates` loop, ~line 300): each gate entry may be a `str` (resolved through `workflow.function(entry)`, as now) or a callable (used as-is; `workflow.function` is not called for it). The gate's display name used in `bind_arguments(..., function=name)` and in every verdict/warning message is the string itself for a `str`, else `getattr(entry, "__name__", repr(entry))`. All existing outcomes are unchanged: `None` passes, a mapping with `warn` appends a warning, any other mapping returns `gate_failed`, raising or returning a non-mapping returns a `fatal` `gate_failed`, a binding failure propagates as `EngineError`.
- `AgentRunner.__call__` model resolution (~lines 402-408): `phase.result is None` gives no model; `phase.result` that is a class (`isinstance(phase.result, type)`) is used directly, without consulting `self.result_models` or `results.resolve_result_model`; a `str` goes through `results.resolve_result_model(phase.result, self.result_models, phase=phase.name)` as now (including its existing error for an unknown name).
- The retry read (~lines 413-414) must work for both `loader.RetryPolicy` and `phases.Retry`. Both expose `max_attempts` and `on`, and the current code already duck-types (`phase.retry.max_attempts`, `tuple(phase.retry.on)`), so no branching is required; the requirement is that it is covered by a test.
- Type hints on `evaluate_gates`, `AgentRunner.__call__` and `_attempt` may be widened to admit either phase type; no runtime behaviour depends on the hint.

In `src/agent_manager/prompt.py`:

- `render_prompt`'s `phase` parameter, `_assemble`'s `phase` parameter and the `_Request.phase` field (currently typed `workflow.loader.AgentPhase`, imported at line 36) are retyped to a `typing.Protocol` exposing exactly `name: str`, `role: str`, `inputs` (a sequence of `str`) — the only attributes these code paths read. Both `AgentPhase` types satisfy it structurally. If nothing else in `prompt.py` needs the loader import, it is removed. No change to resolution, ordering, dedup, errors or text assembly.

## Out of scope

- The `feedback` input resolver, `Goto` revision loops and `agent_phase` (card b904b9e7), even though it also edits `prompt.py`.
- `runtime/compile.py`, `runtime/bridge.py`, `runtime/state.py`, `harness/launcher.py` on_spawn, `engine.run_one_step` (card 023d918e).
- `runtime.run_subtask` and parametrizing `tests/test_engine.py` over engines (card 2853e536).
- `engine.py`'s own deterministic-gate loop (`engine.py:298`) — not named by Task 3.2.
- Any `pygents` import: `dispatch.py` and `prompt.py` must not import it (rule 1). No new dependencies.
- Supervisor tree, exactly-once phases, prompt benchmarking, upstream pygents fixes.

## Invariants

- YAML-engine behaviour is byte-for-byte unchanged: same gate names in messages, same verdicts, same retry budget, same `_record_phase` journal rows (`started`/`done`/`failed` with the same fields) — `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads stay unchanged (rule 5 / G10).
- A fake `claude` in a test knows nothing beyond its brief (rule 4).
- The whole default suite, including `tests/e2e`, stays green (rule 2).

## Error paths

- Callable gate that raises or returns a non-mapping: `fatal` `gate_failed`, message naming the gate by `__name__` (a lambda shows as `<lambda>`).
- Callable gate whose parameters nothing supplies: `EngineError` from `engine.bind_arguments`, naming the gate by `__name__`.
- `str` gate with an unknown name: the loader's existing `UnknownFunctionError` from `workflow.function`, unchanged.
- `str` result with no model in `result_models`: `resolve_result_model`'s existing error, raised before any attempt is journalled, unchanged.

## Tests

Test-placement rule (CLAUDE.md "tests mirror it under `tests/`"; `tests/e2e/` is reserved for full-system wiring): all new tests are unit tests for `dispatch.py` and go in `tests/test_dispatch.py`, reusing its existing fake launcher/store/adapter fixtures. No e2e test is added.

- `test_callable_gate_is_called_directly` — tier: unit, `tests/test_dispatch.py`. A `phases.AgentPhase` with `gates=(lambda result: {"blocked": "x", "detail": "d"},)` evaluated against a workflow whose `function` raises; the verdict is `gate_failed` with detail containing `d`, and `workflow.function` is never called.
- `test_result_class_is_used_directly` — tier: unit, `tests/test_dispatch.py`. `phases.AgentPhase(..., result=results.CriticResult)` run by an `AgentRunner` whose `result_models` is empty (or lacks it); a valid result file validates against the class and the phase returns it.
- `test_phase_model_retry_is_honoured` — tier: unit, `tests/test_dispatch.py`. `retry=phases.Retry(2, ("schema_invalid",))` with an invalid first result and a valid second makes exactly two attempts and succeeds.
- Existing `tests/test_dispatch.py`, `tests/test_prompt.py`, `tests/test_engine.py` and `tests/e2e` tests pass unmodified, demonstrating the YAML path is unchanged.

## Verification

`uv run pytest` — whole suite green. No separate typecheck or lint command.

---

# Let dispatch and prompt take phase-model objects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `dispatch.evaluate_gates`, `dispatch.AgentRunner` and `prompt.render_prompt` accept both the YAML `workflow.loader.AgentPhase` and the declared `workflow.phases.AgentPhase`, with the YAML path unchanged.

**Architecture:** Two small runtime branches in `dispatch.py` (a gate entry is a name or a callable; a result is a name or a class), a type alias `AnyAgentPhase` for the widened hints, and a structural `PromptPhase` Protocol in `prompt.py` replacing its import of the YAML type. The retry read already duck-types and only gets a pinning test.

**Tech Stack:** Python 3.12, pydantic v2, pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-let-dispatch-and-prompt-1bbb532d-design.md` (prepended above).

**Branch / worktree:** `m6/task-let-dispatch-and-prompt-1bbb532d` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-let-dispatch-and-prompt-1bbb532d`, cut from `m6/task-add-pygents-and-the-8ae25085`. `src/agent_manager/workflow/phases.py` (with `AgentPhase`, `Retry`) already exists on this branch. Nothing from cards 023d918e, 2853e536 or b904b9e7 exists; do not assume it. All paths below are relative to the worktree root.

## Global Constraints

- Only `src/agent_manager/runtime/` may import `pygents`; `dispatch.py` and `prompt.py` must not (rule 1).
- The whole default suite, including `tests/e2e`, stays green: `uv run pytest` (rule 2).
- `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads stay unchanged (rule 5 / G10).
- A fake `claude` in a test never knows more than its brief tells it (rule 4).
- No new dependencies.
- YAML-engine messages stay byte-identical: a `str` gate is still named by its string in every message.
- Tests for `src/agent_manager/<module>.py` live in `tests/test_<module>.py`; nothing is added under `tests/e2e/`.

## Review Focus

1. A callable gate that raises or returns a non-mapping: must still be a `fatal` `gate_failed` naming the gate by `__name__`, never an `AttributeError` or a silent pass. Pinned in Task 1 (`test_a_callable_gate_that_raises_is_fatal_and_named_by_its_function_name`, `test_a_callable_gate_returning_a_non_mapping_is_fatal_and_named_lambda`).
2. A callable gate whose parameters nothing supplies: must be an `EngineError` whose `.function` is the gate's `__name__`. Pinned in Task 1 (`test_a_callable_gate_with_an_unsupplied_parameter_is_a_named_engine_error`).
3. A callable with no `__name__` (a `functools.partial`): must be named by `repr` rather than crashing on the name lookup. Pinned in Task 1 (`test_a_callable_gate_without_a_name_is_named_by_its_repr`).
4. A callable gate that warns: the warning line names the gate by `__name__` in the same format YAML gates use. Pinned in Task 1 (`test_a_callable_gate_warning_names_the_gate_by_its_function_name`).
5. A result class whose `__name__` collides with a different model in `result_models`: the class must win, the table must be ignored. Pinned in Task 2 (`test_a_result_class_wins_over_a_same_named_table_entry`).

---

### Task 1: Callable gates in `dispatch.evaluate_gates`

**Files:**
- Modify: `src/agent_manager/dispatch.py:22-38` (imports, new `AnyAgentPhase` alias), `src/agent_manager/dispatch.py:279-340` (`evaluate_gates`)
- Test: `tests/test_dispatch.py` (imports at lines 11-34; new tests appended after `test_a_gate_whose_parameter_nothing_supplies_is_a_named_engine_error`, which ends at line 613)

**Interfaces:**
- Consumes: `phases.AgentPhase(name, role, inputs, result, gates=(), retry=None, ...)` from `src/agent_manager/workflow/phases.py`; `engine.bind_arguments(fn, values, *, phase, function)`.
- Produces: `dispatch.AnyAgentPhase = AgentPhase | phases.AgentPhase` (used by Task 2); `evaluate_gates(phase: AnyAgentPhase, workflow: Workflow, values, warnings) -> Verdict | None`. Test helpers `_NoLookupWorkflow` and `_model_phase(*gates, **overrides) -> phases.AgentPhase` in `tests/test_dispatch.py` (used by Tasks 2 and 3).

- [ ] **Step 1: Add the test imports**

In `tests/test_dispatch.py`, change the import block at the top. Replace:

```python
import hashlib
import json
import subprocess
```

with:

```python
import functools
import hashlib
import json
import subprocess
```

and replace:

```python
from agent_manager.workflow.loader import load_workflow
from agent_manager.workflow.registry import FunctionRegistry
```

with:

```python
from agent_manager.workflow import phases
from agent_manager.workflow.loader import load_workflow
from agent_manager.workflow.registry import FunctionRegistry
```

- [ ] **Step 2: Write the failing tests**

Append directly after `test_a_gate_whose_parameter_nothing_supplies_is_a_named_engine_error` (before `STORY_ID = "2143808b"`) in `tests/test_dispatch.py`:

```python
# ── phase-model phases (workflow.phases.AgentPhase) ──────────────────────────


class _NoLookupWorkflow:
    """A workflow whose name table must never be consulted.

    A phase-model phase carries its gates as callables, so nothing about it
    should reach `workflow.function`; any call is recorded and fails loudly.
    """

    def __init__(self) -> None:
        self.looked_up: list[object] = []

    def function(self, name):
        self.looked_up.append(name)
        raise AssertionError(f"workflow.function({name!r}) was called for a callable gate")


def _model_phase(*gates, **overrides) -> phases.AgentPhase:
    """A declared `phases.AgentPhase` shaped like AGENT_DOCUMENT's explore phase."""
    fields = {
        "name": "explore",
        "role": "explorer",
        "inputs": (),
        "result": FakeResult,
        "gates": tuple(gates),
    }
    fields.update(overrides)
    return phases.AgentPhase(**fields)


def test_callable_gate_is_called_directly():
    workflow = _NoLookupWorkflow()

    verdict = dispatch.evaluate_gates(
        _model_phase(lambda result: {"blocked": "x", "detail": "d"}),
        workflow,
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is False
    assert verdict.detail == "phase 'explore' gate '<lambda>' failed: blocked=x, detail=d"
    assert workflow.looked_up == []


def test_a_passing_callable_gate_sees_the_result():
    seen: list[object] = []

    def output_gate(result):
        seen.append(result)
        return None

    verdict = dispatch.evaluate_gates(
        _model_phase(output_gate),
        _NoLookupWorkflow(),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict is None
    assert seen == [{"summary": "ok"}]


def test_a_callable_gate_that_raises_is_fatal_and_named_by_its_function_name():
    def output_gate(result):
        raise RuntimeError("the gate itself is broken")

    verdict = dispatch.evaluate_gates(
        _model_phase(output_gate),
        _NoLookupWorkflow(),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is True
    assert "gate 'output_gate' raised RuntimeError: the gate itself is broken" in verdict.detail


def test_a_callable_gate_returning_a_non_mapping_is_fatal_and_named_lambda():
    verdict = dispatch.evaluate_gates(
        _model_phase(lambda result: "looks fine to me"),
        _NoLookupWorkflow(),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is True
    assert "gate '<lambda>' returned str" in verdict.detail


def test_a_callable_gate_with_an_unsupplied_parameter_is_a_named_engine_error():
    def output_gate(result, provided_verification):
        return None

    with pytest.raises(EngineError) as caught:
        dispatch.evaluate_gates(
            _model_phase(output_gate),
            _NoLookupWorkflow(),
            dispatch.gate_values({}, "explore", {"summary": "ok"}),
            [],
        )

    assert caught.value.parameter == "provided_verification"
    assert caught.value.function == "output_gate"
    assert caught.value.phase == "explore"


def test_a_callable_gate_warning_names_the_gate_by_its_function_name():
    def output_gate(result):
        return {"warn": "counts unusable"}

    warnings: list[str] = []

    verdict = dispatch.evaluate_gates(
        _model_phase(output_gate),
        _NoLookupWorkflow(),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        warnings,
    )

    assert verdict is None
    assert warnings == ["phase 'explore' gate 'output_gate' warned: counts unusable"]


def _blocking_gate(result, blocked):
    return {"blocked": blocked}


def test_a_callable_gate_without_a_name_is_named_by_its_repr():
    # A functools.partial has no __name__; the display name falls back to repr.
    gate = functools.partial(_blocking_gate, blocked="x")
    name = repr(gate)

    verdict = dispatch.evaluate_gates(
        _model_phase(gate),
        _NoLookupWorkflow(),
        dispatch.gate_values({}, "explore", {"summary": "ok"}),
        [],
    )

    assert verdict.status == "gate_failed"
    assert verdict.fatal is False
    assert verdict.detail == f"phase 'explore' gate {name!r} failed: blocked=x"
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -k "callable_gate" -v`
Expected: all seven FAIL with `AssertionError: workflow.function(<function ...>) was called for a callable gate` (raised by `_NoLookupWorkflow.function`, because `evaluate_gates` still calls `workflow.function(name)` for every entry).

- [ ] **Step 4: Add the `AnyAgentPhase` alias in `dispatch.py`**

In `src/agent_manager/dispatch.py`, replace:

```python
from agent_manager.store import Store
from agent_manager.workflow.loader import AgentPhase, Workflow

RESULT_NAME = "result.json"
```

with:

```python
from agent_manager.store import Store
from agent_manager.workflow import phases as phase_model
from agent_manager.workflow.loader import AgentPhase, Workflow

AnyAgentPhase = AgentPhase | phase_model.AgentPhase
"""Either agent-phase type: the YAML one (`result` and gates as names) or the
declared phase model (`result` a class, gates callables). Dispatch accepts both
while the YAML engine exists; nothing here imports pygents (rule 1)."""

RESULT_NAME = "result.json"
```

- [ ] **Step 5: Implement callable gates in `evaluate_gates`**

In `src/agent_manager/dispatch.py`, replace the signature and the head of the loop:

```python
def evaluate_gates(
    phase: AgentPhase,
    workflow: Workflow,
    values: Mapping[str, Any],
    warnings: list[str],
) -> Verdict | None:
```

with:

```python
def evaluate_gates(
    phase: AnyAgentPhase,
    workflow: Workflow,
    values: Mapping[str, Any],
    warnings: list[str],
) -> Verdict | None:
```

Then append this paragraph to the end of the `evaluate_gates` docstring (just before its closing `"""`):

```python

    A gate entry is either a name, resolved through `workflow.function` as the
    YAML document declares it, or -- on a declared `phases.AgentPhase` -- the
    callable itself, used as-is and named by its `__name__` (its `repr` when it
    has none) in every message.
```

And replace:

```python
    for name in phase.gates:
        gate = workflow.function(name)
        kwargs = engine.bind_arguments(gate, values, phase=phase.name, function=name)
```

with:

```python
    for entry in phase.gates:
        if isinstance(entry, str):
            name, gate = entry, workflow.function(entry)
        else:
            name, gate = getattr(entry, "__name__", repr(entry)), entry
        kwargs = engine.bind_arguments(gate, values, phase=phase.name, function=name)
```

Leave the rest of the loop body (which already uses `name` and `gate`) untouched.

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -k "callable_gate" -v`
Expected: 7 passed.

- [ ] **Step 7: Run the whole dispatch file to confirm the YAML gate path is unchanged**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: all pass, including the pre-existing `test_a_failing_gate_returns_a_retryable_gate_failed_verdict`, `test_a_warning_gate_is_recorded_and_does_not_fail_the_attempt` and `test_a_gate_whose_parameter_nothing_supplies_is_a_named_engine_error` (their messages still name `'output_gate'`).

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "Let dispatch.evaluate_gates take callable gates from phase-model phases"
```

---

### Task 2: A result class is used directly by `AgentRunner`

**Files:**
- Modify: `src/agent_manager/dispatch.py` — `AgentRunner.__call__` model resolution (the `model = (...)` expression, ~lines 402-408 before Task 1's edit) and the `phase: AgentPhase` hints on `__call__`, `_attempt`, `_record_phase`, `_record_attempt`
- Test: `tests/test_dispatch.py` (append at the very end of the file: these tests need the `store`/`worktree` fixtures and `_runner`, which are defined after `STORY_ID = "2143808b"`)

**Interfaces:**
- Consumes: `dispatch.AnyAgentPhase` (Task 1); test helper `_model_phase(*gates, **overrides)` (Task 1); existing `_runner`, `_context`, `_rendered`, `_phase_statuses`, `_attempt_statuses`, `FakeLauncher`, `_workflow`, `AGENT_DOCUMENT`, `VALID_RESULT` in `tests/test_dispatch.py`; `results.CriticResult` (`blockers: bool`, `reason: str | None`, `summary: str`, strict, extra forbidden).
- Produces: `AgentRunner.__call__(phase: AnyAgentPhase, context, rendered)`; test constant `CRITIC_RESULT` (used by Task 3).

- [ ] **Step 1: Write the failing tests**

Append at the very end of `tests/test_dispatch.py`:

```python
# ── phase-model phases through the runner ────────────────────────────────────

CRITIC_RESULT = json.dumps({"blockers": False, "reason": None, "summary": "no blockers"})


def test_result_class_is_used_directly(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[CRITIC_RESULT])
    runner, _ = _runner(
        store, workflow, launcher, tmp_path, worktree, result_models={}
    )

    result = runner(
        _model_phase(result=results.CriticResult), _context(worktree), _rendered()
    )

    assert result == {"blockers": False, "reason": None, "summary": "no blockers"}
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "done")]
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]
    contract = launcher.prompts[0].split(prompt.RESULT_HEADING, 1)[1]
    assert '"blockers"' in contract


def test_a_result_class_wins_over_a_same_named_table_entry(store, tmp_path, worktree):
    # Review Focus 5: the table maps "CriticResult" to a different model; the
    # class on the phase is what the result file is validated against.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(
        store,
        workflow,
        launcher,
        tmp_path,
        worktree,
        result_models={"CriticResult": FakeResult},
    )

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(
            _model_phase(result=results.CriticResult), _context(worktree), _rendered()
        )

    assert caught.value.outcome == "schema_invalid"
    assert "blockers" in caught.value.detail
    assert len(launcher.calls) == 1
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -k "result_class" -v`
Expected: both FAIL with `EngineError` "... declares result <class 'agent_manager.results.CriticResult'>, which no result model is registered for ..." (the class is still looked up in `result_models` by `results.resolve_result_model`).

- [ ] **Step 3: Implement class-or-name result resolution**

In `src/agent_manager/dispatch.py`, inside `AgentRunner.__call__`, replace:

```python
        model = (
            None
            if phase.result is None
            else results.resolve_result_model(
                phase.result, self.result_models, phase=phase.name
            )
        )
```

with:

```python
        # A declared phase-model phase carries its result model as the class
        # itself, which is used as-is; a YAML phase carries a name, looked up
        # in the table exactly as before.
        if phase.result is None:
            model = None
        elif isinstance(phase.result, type):
            model = phase.result
        else:
            model = results.resolve_result_model(
                phase.result, self.result_models, phase=phase.name
            )
```

- [ ] **Step 4: Widen the phase hints on the runner**

In `src/agent_manager/dispatch.py`, in class `AgentRunner`, change the `phase` annotation from `AgentPhase` to `AnyAgentPhase` in exactly these four signatures (no other change to them):

```python
    def __call__(
        self,
        phase: AnyAgentPhase,
        context: Mapping[str, Any],
        rendered: prompt.RenderedPrompt,
    ) -> Any:
```

```python
    def _attempt(
        self,
        phase: AnyAgentPhase,
        context: Mapping[str, Any],
        rendered: prompt.RenderedPrompt,
        feedback: Sequence[str],
        target: Target,
        role: RoleBundle,
        cwd: Path,
        model: type[BaseModel] | None,
    ) -> Verdict:
```

```python
    def _record_phase(
        self,
        phase: AnyAgentPhase,
        status: models.Status,
        started_at: datetime,
        ended_at: datetime | None,
        detail: str | None,
    ) -> None:
```

```python
    def _record_attempt(self, phase: AnyAgentPhase, attempt: models.Attempt) -> None:
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -k "result_class" -v`
Expected: 2 passed.

- [ ] **Step 6: Run the whole dispatch file to confirm the name path is unchanged**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: all pass, including `test_an_unregistered_result_model_is_a_named_engine_error` (a `str` result with an empty table still raises `EngineError` before any launch).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "Let AgentRunner use a phase-model result class directly"
```

---

### Task 3: Pin the retry read for `phases.Retry`

The retry read (`phase.retry.max_attempts`, `tuple(phase.retry.on)`) already duck-types over `loader.RetryPolicy` and `phases.Retry`; the spec asks only that this is covered by a test. This task therefore adds a pinning test that is expected to PASS on its first run once Task 2 is in (before Task 2 it would fail on result resolution, not on retry). No production code changes.

**Files:**
- Test: `tests/test_dispatch.py` (append at the very end, after Task 2's tests)

**Interfaces:**
- Consumes: `_model_phase` (Task 1), `CRITIC_RESULT` (Task 2), `phases.Retry(max_attempts: int, on: tuple[str, ...])`, existing `INVALID_RESULT`, `_runner`, `_context`, `_rendered`, `_attempt_statuses`, `_phase_statuses`.
- Produces: nothing later tasks use.

- [ ] **Step 1: Write the pinning tests**

Append at the very end of `tests/test_dispatch.py`:

```python
def test_phase_model_retry_is_honoured(store, tmp_path, worktree):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[INVALID_RESULT, CRITIC_RESULT])
    runner, _ = _runner(
        store, workflow, launcher, tmp_path, worktree, result_models={}
    )
    phase = _model_phase(
        result=results.CriticResult, retry=phases.Retry(2, ("schema_invalid",))
    )

    result = runner(phase, _context(worktree), _rendered())

    assert result == {"blockers": False, "reason": None, "summary": "no blockers"}
    assert len(launcher.calls) == 2
    assert _attempt_statuses(store) == [
        (1, "started"), (1, "schema_invalid"), (2, "started"), (2, "ok")
    ]
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "done")]


def test_phase_model_retry_does_not_retry_an_outcome_outside_on(store, tmp_path, worktree):
    # The `on` half of the duck-typed read: gate_failed is not in `on`, so one
    # dispatch only, even with attempts left.
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[CRITIC_RESULT])
    runner, _ = _runner(
        store, workflow, launcher, tmp_path, worktree, result_models={}
    )
    phase = _model_phase(
        lambda result: {"blocked": "critic"},
        result=results.CriticResult,
        retry=phases.Retry(3, ("schema_invalid",)),
    )

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(phase, _context(worktree), _rendered())

    assert caught.value.outcome == "gate_failed"
    assert len(launcher.calls) == 1
    assert _phase_statuses(store)[-1] == ("explore", "failed")
```

- [ ] **Step 2: Run the pinning tests**

Run: `uv run pytest tests/test_dispatch.py -k "phase_model_retry" -v`
Expected: 2 passed on first run (the read already duck-types; this is a characterization test, per the spec's "the requirement is that it is covered by a test"). If either fails, stop: the retry read is not duck-typing as the spec assumes, and `src/agent_manager/dispatch.py`'s `budget = ...` / `retry_on = ...` lines need inspecting before going further.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dispatch.py
git commit -m "Pin AgentRunner's retry read for phases.Retry"
```

---

### Task 4: `prompt.render_prompt` takes a structural `PromptPhase`

**Files:**
- Modify: `src/agent_manager/prompt.py:24-36` (imports), `src/agent_manager/prompt.py:88-94` (`_Request`), `src/agent_manager/prompt.py:310` (`render_prompt` signature), `src/agent_manager/prompt.py:338` (`_assemble` signature)
- Test: `tests/test_prompt.py` (import block at lines 10-19; new tests appended at the end of the file). This is the mirrored unit file for `prompt.py` per CLAUDE.md; the spec's test list only names dispatch tests, and these two are added so the prompt change has its own RED/GREEN cycle.

**Interfaces:**
- Consumes: `phases.AgentPhase(name, role, inputs, result, ...)`; existing `_phase(inputs, *, name, role, **extra)` and `_context(**overrides)` helpers in `tests/test_prompt.py`.
- Produces: `prompt.PromptPhase` (a `typing.Protocol` with read-only `name: str`, `role: str`, `inputs: Sequence[str]`); `render_prompt(phase: PromptPhase, context: Mapping[str, Any]) -> RenderedPrompt`.

- [ ] **Step 1: Add the test imports**

In `tests/test_prompt.py`, replace:

```python
import json
from pathlib import Path
```

with:

```python
import inspect
import json
from pathlib import Path
```

and replace:

```python
from agent_manager.workflow.loader import AgentPhase
```

with:

```python
from agent_manager.workflow import phases
from agent_manager.workflow.loader import AgentPhase
```

- [ ] **Step 2: Write the failing tests**

Append at the end of `tests/test_prompt.py`:

```python
def test_prompt_reads_phases_through_a_protocol_not_the_yaml_type():
    # Spec: render_prompt, _assemble and _Request are retyped to a Protocol
    # carrying name, role and inputs, and the loader import is dropped.
    assert "AgentPhase" not in vars(prompt)
    assert inspect.signature(prompt.render_prompt).parameters["phase"].annotation is (
        prompt.PromptPhase
    )
    assert all(
        isinstance(getattr(prompt.PromptPhase, attr), property)
        for attr in ("name", "role", "inputs")
    )


def test_a_phase_model_agent_phase_renders_exactly_like_the_yaml_one():
    declared = phases.AgentPhase("implement", "coder", ("branch", "base_branch"), None)

    rendered = prompt.render_prompt(declared, _context())

    assert rendered == prompt.render_prompt(_phase(["branch", "base_branch"]), _context())
    assert rendered.text.startswith("# phase: implement\n# role: coder\n")
```

- [ ] **Step 3: Run the new tests to verify the first fails**

Run: `uv run pytest tests/test_prompt.py -k "protocol_not_the_yaml_type or phase_model_agent_phase" -v`
Expected: `test_prompt_reads_phases_through_a_protocol_not_the_yaml_type` FAILS on `assert "AgentPhase" not in vars(prompt)` (prompt.py still imports the YAML type). `test_a_phase_model_agent_phase_renders_exactly_like_the_yaml_one` PASSES already (render_prompt reads only `name`, `role`, `inputs` at runtime); it pins that behaviour through the retype.

- [ ] **Step 4: Replace the loader import with the `PromptPhase` Protocol**

In `src/agent_manager/prompt.py`, replace:

```python
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel

from agent_manager import dag, models
from agent_manager.errors import EngineError
from agent_manager.roles.loader import RoleBundle
from agent_manager.workflow.loader import AgentPhase

_MISSING = object()
```

with:

```python
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from pydantic import BaseModel

from agent_manager import dag, models
from agent_manager.errors import EngineError
from agent_manager.roles.loader import RoleBundle


class PromptPhase(Protocol):
    """The three things rendering reads from an agent phase, and nothing else.

    Structural, so both the YAML `workflow.loader.AgentPhase` and the declared
    `workflow.phases.AgentPhase` satisfy it without either being imported here.
    Read-only properties, because a frozen dataclass and a frozen pydantic model
    both expose these as attributes that must not be assigned.
    """

    @property
    def name(self) -> str: ...

    @property
    def role(self) -> str: ...

    @property
    def inputs(self) -> Sequence[str]: ...


_MISSING = object()
```

- [ ] **Step 5: Retype `_Request.phase`**

In `src/agent_manager/prompt.py`, replace:

```python
    name: str
    phase: AgentPhase
    context: Mapping[str, Any]
```

with:

```python
    name: str
    phase: PromptPhase
    context: Mapping[str, Any]
```

- [ ] **Step 6: Retype `render_prompt` and `_assemble`**

In `src/agent_manager/prompt.py`, replace:

```python
def render_prompt(phase: AgentPhase, context: Mapping[str, Any]) -> RenderedPrompt:
```

with:

```python
def render_prompt(phase: PromptPhase, context: Mapping[str, Any]) -> RenderedPrompt:
```

and replace:

```python
def _assemble(phase: AgentPhase, sections: list[tuple[str, str]]) -> str:
```

with:

```python
def _assemble(phase: PromptPhase, sections: list[tuple[str, str]]) -> str:
```

No other line in `prompt.py` changes.

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -k "protocol_not_the_yaml_type or phase_model_agent_phase" -v`
Expected: 2 passed.

- [ ] **Step 8: Run the prompt and phases tests to confirm nothing else moved**

Run: `uv run pytest tests/test_prompt.py tests/workflow/test_phases.py -v`
Expected: all pass (`phases.Workflow.validate` imports `prompt` lazily and reads `prompt.INPUT_NAMES`, which is untouched).

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py
git commit -m "Type prompt.render_prompt's phase as a structural PromptPhase"
```

---

### Task 5: Whole-suite verification and rule-1 check

**Files:**
- None modified.

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Confirm neither widened module imports pygents (rule 1)**

Run: `grep -n "pygents" src/agent_manager/dispatch.py src/agent_manager/prompt.py src/agent_manager/workflow/phases.py`
Expected: no output from `dispatch.py` or `prompt.py`. (`phases.py` mentions pygents only in its docstring, which is fine; it must have no `import pygents` line.)

- [ ] **Step 2: Run the whole default suite (rule 2)**

Run: `uv run pytest`
Expected: all tests pass, including `tests/test_engine.py` and `tests/e2e/` unmodified, showing the YAML path is unchanged.

- [ ] **Step 3: Confirm the working tree is clean**

Run: `git status --short`
Expected: no output (everything was committed in Tasks 1-4).
