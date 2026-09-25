<!-- task-pipeline: validated -->
# Convert a loaded YAML workflow into the phase model (card 0326732a)

Parent story: 09e1183f "The declared phase model" (milestone 84c3b532, pygents-engine). Source of truth: `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 1.2 and `2026-09-25-pygents-engine-design.md` §5/§9. This narrows the agreed design to one subtask; it adds no new design.

## Base

Builds on the `phases.py` from sibling a0aa01e5 (branch `m6/task-add-the-phase-model-a0aa01e5`, already present in this worktree): `Goto`, `Retry`, `Step`, `AgentPhase`, `Workflow`, `WorkflowError`, `validate()`, `digest()`. This card must not change any of those, nor `validate()`/`digest()` logic.

## Scope

1. Add `from_loader(loaded: loader.Workflow, *, timeout: timedelta = timedelta(minutes=30)) -> Workflow` to `src/agent_manager/workflow/phases.py`.
2. Move `_plan_hash_of` and `plan_hash_gate_adapter` verbatim, docstrings included, from `src/agent_manager/workflow/registry.py` (lines 182-215 in this worktree; the plan's "174-228" is stale) to the end of `src/agent_manager/steps/reducers.py`. `registry.py` imports `plan_hash_gate_adapter` from `agent_manager.steps.reducers` and still registers it as `"plan_hash_gate"` in `default_registry()`. Signature stays `plan_hash_gate_adapter(implement: object = None, review: object = None)`. Any import the moved code needs (e.g. `Mapping`) goes to `reducers.py`. Any import in `registry.py` that is no longer used after the move is dropped.

Out of scope: declaring `TASK`/`INTEGRATE`, per-phase timeouts, and the digest-equality tests against the shipped YAML. Those belong to sibling 04a5b91e. No changes to the engine, the YAML, `dispatch.py`, `prompt.py`, or `tests/e2e`.

## Observable behavior of `from_loader`

- Returns `phases.Workflow(loaded.name, phases)`, with phases in the loaded order, so `phase_names` equals `tuple(p.name for p in loaded.phases)`.
- Every function name (`run`, `when`, and each `gates` entry) is resolved with `loaded.function(name)`, never through a registry. Whatever callable the loaded workflow holds, including a fake from a test registry, is carried through by identity.
- A deterministic loader phase (`kind == "deterministic"`) becomes `Step(name, run=<fn>, args=dict(args), gates=tuple(<fn>...), best_effort, when=<fn> or None, skip_to)`.
- An agent loader phase becomes `AgentPhase(name, role, inputs=tuple(inputs), result=results.RESULT_MODELS[result] or None, gates=tuple(<fn>...), retry=Retry(max_attempts, tuple(on)) or None, writes, timeout=timeout)`, with `on_fail=None`. The `timeout` keyword applies to every agent phase.
- Pure: the input is not mutated and no I/O happens. `phases.py` stays free of any pygents import (rule 1). `results` and `loader` are imported inside the function, as in the plan.
- Before writing the code, check the attribute names against `workflow/loader.py`. As of this worktree, `DeterministicPhase` has `run`, `args`, `best_effort`; `_PhaseBase` has `name`, `when`, `skip_to`, `gates`; `AgentPhase` has `role`, `inputs`, `result`, `writes`, `retry`; `RetryPolicy` has `max_attempts`, `on`.

## Error paths

- An unresolved function name raises the loader's `UnknownFunctionError` from `loaded.function`, unchanged.
- A `result:` name missing from `results.RESULT_MODELS` raises `WorkflowError(..., phase=<name>)`, not a bare `KeyError`.
- An agent loader phase with `when` or `skip_to` set raises `WorkflowError(..., phase=<name>)`. The loader's `_PhaseBase` allows these fields on agent phases, but `phases.AgentPhase` has no fields for them, so dropping them silently would change behavior. No shipped YAML uses them, so the shipped workflows still convert.

## Tests

The placement rule is design spec §14 plus pygents-engine design §9: pure functions get unit tests in the module under `tests/` that mirrors their `src/` module. Nothing for this card goes under `tests/e2e/`, which is reserved for the one opt-in real-harness run.

Append to `tests/workflow/test_phases.py` (unit tier, mirrors `workflow/phases.py`):
- `test_from_loader_resolves_every_name_of_the_shipped_task`: as in plan Task 1.2 Step 1. It covers the phase-name order, `review.result is results.ReviewResult`, `reducers.review_gate` and `reducers.plan_hash_gate_adapter` in `review.gates`, `explore.retry == Retry(2, ("schema_invalid", "gate_failed"))`, and `plan_check.skip_to == "docs_commit"` with a callable `when`.
- `test_from_loader_keeps_fakes_from_a_test_registry`: as in the plan. It checks that `from_loader(load_builtin("integrate", default_registry())).phase("verify").run is loaded.function("verify.run_suite")`.
- `test_from_loader_rejects_an_unknown_result_name` and `test_from_loader_rejects_when_on_an_agent_phase`: each builds a small `loader.Workflow` through a test registry and asserts that `WorkflowError` names the phase.

Append to `tests/steps/test_reducers.py` (unit tier, mirrors `steps/reducers.py`):
- `test_plan_hash_gate_adapter_compares_the_two_results`: as in the plan. Equal hashes give `None`, different hashes give a value that is not `None`, and `(None, None)` gives `None`.

Done means `uv run pytest` passes in full, with existing registry and loader tests unchanged. Rule 5/G10 applies: journal lines, `SubtaskSummary`, and phase/attempt rows are byte-for-byte unchanged.

## Note

The exploration findings given to this stage were cut off at 8000 characters, mid-sentence, in the test-tier paragraph. That is a sign the exploration stage over-ran its brief. The missing part looked like the end of the "no `tests/e2e`" point, which the design docs cover independently.

---

# Convert a Loaded YAML Workflow into the Phase Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `phases.from_loader()` to turn a loaded, resolved YAML `loader.Workflow` into the declared phase model, and move `plan_hash_gate_adapter` (with `_plan_hash_of`) from `workflow/registry.py` into `steps/reducers.py`.

**Architecture:** `from_loader` is a pure function appended to `src/agent_manager/workflow/phases.py`. It walks `loaded.phases` in order, resolves every function name through `loaded.function(name)` (never a registry), maps `result:` names through `results.RESULT_MODELS`, and builds frozen `Step`/`AgentPhase` dataclasses. The adapter move is a verbatim relocation; `registry.py` imports it back and keeps registering it under `"plan_hash_gate"`.

**Tech Stack:** Python 3.12, Pydantic 2 (loader models), stdlib dataclasses, pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-convert-a-loaded-yaml-0326732a-design.md` (prepended above). Parent plan: `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 1.2.

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-convert-a-loaded-yaml-0326732a`, branch `m6/task-convert-a-loaded-yaml-0326732a`, cut from `m6/task-add-the-phase-model-a0aa01e5`. The only prior-subtask code assumed present is that branch's `workflow/phases.py` (`Goto`, `Retry`, `Step`, `AgentPhase`, `Workflow`, `WorkflowError`, `validate()`, `digest()`), which has been read and is in the worktree. All paths below are relative to the worktree root.

## Global Constraints

- Only `src/agent_manager/runtime/` imports `pygents`. `workflow/phases.py` must not (rule 1); `tests/workflow/test_phases.py::test_phases_module_never_imports_pygents` already enforces this.
- Do not change `Goto`, `Retry`, `Step`, `AgentPhase`, `Workflow`, `WorkflowError`, `validate()` or `digest()` in `phases.py`.
- `plan_hash_gate_adapter(implement: object = None, review: object = None) -> dict[str, str] | None` keeps its signature and docstring, and is still registered as `"plan_hash_gate"` in `default_registry()`.
- Do not declare `TASK`/`INTEGRATE`, per-phase timeouts, or digest-equality tests (sibling 04a5b91e). Do not touch the engine, the YAML files, `dispatch.py`, `prompt.py`, or `tests/e2e/`.
- `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads stay byte-for-byte unchanged (rule 5 / G10).
- Verification for every task: `uv run pytest` (whole suite green).

## Review Focus

1. **The `timeout` keyword is ignored or only the default is used.** A caller passing `timeout=timedelta(minutes=45)` expects every agent phase to carry 45 minutes and no step to be affected. Test added in Task 2 (`test_from_loader_puts_the_timeout_on_every_agent_phase`).
2. **An agent phase carrying `skip_to` without `when` (or `when` alone).** The loader accepts either field alone on an agent phase; a person expects a `WorkflowError` naming the phase, not a silent drop. Test added in Task 2 (parametrized `test_from_loader_rejects_when_on_an_agent_phase` covers `when` only, `skip_to` only, and both).
3. **A name the loaded workflow never resolved.** A person expects the loader's own `UnknownFunctionError`, not a `KeyError` or a re-wrapped `WorkflowError`. Test added in Task 2 (`test_from_loader_lets_an_unresolved_name_raise_unknown_function_error`).
4. **The converted `Step.args` aliasing the loader model's dict.** Mutating one would leak into the other, breaking "pure, input not mutated". A person expects a copy. Test added in Task 2 (`test_from_loader_maps_a_deterministic_phase_field_by_field`).
5. **The digest of a converted workflow is not reproducible across two loads.** Sibling 04a5b91e compares digests of `from_loader(shipped YAML)`; two independent loads must give the same digest, and the moved adapter's `__module__` must now be `agent_manager.steps.reducers`, since `digest()` hashes `module.qualname`. Tests added in Task 1 (`__module__` assertion) and Task 2 (`test_from_loader_digest_is_reproducible_across_loads`).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/agent_manager/steps/reducers.py` | Modify (append at end) | Receives `_plan_hash_of` and `plan_hash_gate_adapter`. `Mapping` is already imported on line 20. |
| `src/agent_manager/workflow/registry.py` | Modify | Delete lines 182-215, import `plan_hash_gate_adapter` from `agent_manager.steps.reducers`, drop the now-unused `Mapping` import, keep the `"plan_hash_gate"` registration on line 264. |
| `src/agent_manager/workflow/phases.py` | Modify (append at end) | Adds `from_loader`. |
| `tests/steps/test_reducers.py` | Modify (import block + append) | Unit test for the moved adapter. |
| `tests/workflow/test_phases.py` | Modify (import block + append) | Unit tests for `from_loader`. |

Both test files are the existing unit-tier modules that mirror their `src/` modules (design spec §14, pygents-engine design §9). Nothing goes under `tests/e2e/`.

---

### Task 1: Move `plan_hash_gate_adapter` into `steps/reducers.py`

**Files:**
- Modify: `src/agent_manager/steps/reducers.py` (append after line 405, the end of `critic_blockers_gate`)
- Modify: `src/agent_manager/workflow/registry.py:16`, `:19-27`, `:182-215`, `:254-256`
- Test: `tests/steps/test_reducers.py`

**Interfaces:**
- Consumes: `reducers.plan_hash_gate(impl_hash: object, review_hash: object) -> dict[str, str] | None` (already in `reducers.py`, line 261).
- Produces: `agent_manager.steps.reducers.plan_hash_gate_adapter(implement: object = None, review: object = None) -> dict[str, str] | None`, and `agent_manager.steps.reducers._plan_hash_of(result: object) -> object`. `agent_manager.workflow.registry.plan_hash_gate_adapter` remains importable (the same object, re-imported), so `tests/workflow/test_registry.py` is unchanged.

- [ ] **Step 1: Write the failing test**

In `tests/steps/test_reducers.py`, add `plan_hash_gate_adapter` to the existing import block (lines 12-25), keeping it alphabetical:

```python
from agent_manager.steps.reducers import (
    _is_integer,
    _js_text,
    count_of,
    critic_blockers_gate,
    exploration_output_gate,
    implement_blocked_gate,
    is_plan_hash,
    plan_hash_gate,
    plan_hash_gate_adapter,
    plan_hash_mismatch,
    review_gate,
    verification_gate,
    verification_passed_gate,
)
```

Then append at the end of the file:

```python
def test_plan_hash_gate_adapter_compares_the_two_results():
    assert plan_hash_gate_adapter({"plan_hash": "aaaaaaaa"}, {"plan_hash": "aaaaaaaa"}) is None
    assert plan_hash_gate_adapter({"plan_hash": "aaaaaaaa"}, {"plan_hash": "bbbbbbbb"}) is not None
    assert plan_hash_gate_adapter(None, None) is None
    # digest() hashes `module.qualname`, so the adapter's home is part of the
    # declared workflow's identity (Review Focus 5).
    assert plan_hash_gate_adapter.__module__ == "agent_manager.steps.reducers"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'plan_hash_gate_adapter' from 'agent_manager.steps.reducers'`.

- [ ] **Step 3: Append the moved code to `reducers.py`**

Append to the end of `src/agent_manager/steps/reducers.py` (after `critic_blockers_gate`). This is `registry.py` lines 182-215 verbatim, docstrings included, with one unavoidable change: the final line calls `plan_hash_gate(...)` directly instead of `reducers.plan_hash_gate(...)`, because the module cannot refer to itself as `reducers`. `Mapping` is already imported at line 20 of `reducers.py`, so no import is added.

```python


def _plan_hash_of(result: object) -> object:
    """The `plan_hash` field of a dumped phase result, or `None`.

    `None` rather than an error for a missing or non-mapping result: a phase
    that was skipped, escalated or returned nothing has no hash to compare, and
    `reducers.plan_hash_gate` already answers `None` (pass) for anything that is
    not an 8-hex-character string.
    """
    return result.get("plan_hash") if isinstance(result, Mapping) else None


def plan_hash_gate_adapter(
    implement: object = None, review: object = None
) -> dict[str, str] | None:
    """`reducers.plan_hash_gate` bound to the two phase results it compares.

    The document names this gate on the `review` phase, and
    `engine.bind_arguments` binds strictly by parameter name out of a table of
    whole values -- nothing in that table is called `impl_hash` or
    `review_hash`, and the yaml `args:` map holds literals, so neither could be
    written there either. This adapter takes the two names the table *does*
    hold, the phase names `implement` and `review`, and does the one field
    lookup the binder cannot do for itself. The reducer keeps its signature and
    its tests; nothing about the comparison moves.

    A module-level function rather than a closure built inside
    `default_registry()`: `resolve(name) is resolve(name)` must hold across two
    registries, which is the same invariant `_PLACEHOLDERS` exists to preserve.

    Both parameters default to `None` so a run where `implement` never executed
    in this process (a `plan_check` skip, or a resume started later) still binds
    and passes, rather than failing to bind and reading as a document bug.
    """
    return plan_hash_gate(_plan_hash_of(implement), _plan_hash_of(review))
```

- [ ] **Step 4: Delete the originals from `registry.py`**

In `src/agent_manager/workflow/registry.py`, delete lines 182-215 (from `def _plan_hash_of(result: object) -> object:` through `return reducers.plan_hash_gate(_plan_hash_of(implement), _plan_hash_of(review))`) together with the two blank lines that follow them, so `_placeholder` is followed by two blank lines and then `BUILTIN_FUNCTION_NAMES = (`.

- [ ] **Step 5: Import the adapter back and drop the unused `Mapping`**

In `src/agent_manager/workflow/registry.py`, replace line 16:

```python
from collections.abc import Callable, Mapping, Sequence
```

with:

```python
from collections.abc import Callable, Sequence
```

and replace the steps import block (lines 19-27):

```python
from agent_manager.steps import (
    docs_commit,
    integrate,
    plan_check,
    reducers,
    rollup,
    verify,
    worktree,
)
```

with:

```python
from agent_manager.steps import (
    docs_commit,
    integrate,
    plan_check,
    reducers,
    rollup,
    verify,
    worktree,
)
from agent_manager.steps.reducers import plan_hash_gate_adapter
```

`reducers` stays imported: `default_registry()` still registers six other reducers from it. The line `registry.register("plan_hash_gate", plan_hash_gate_adapter)` in `default_registry()` is left exactly as it is.

- [ ] **Step 6: Fix the stale pointer in `default_registry()`'s docstring**

In the same file, the docstring of `default_registry()` says the adapter is "above". Replace:

```python
    implementation. `plan_hash_gate` is the single exception to the "no
    wrappers" rule: see `plan_hash_gate_adapter` above for why the binder cannot
    reach the two fields that gate compares.
```

with:

```python
    implementation. `plan_hash_gate` is the single exception to the "no
    wrappers" rule: see `steps.reducers.plan_hash_gate_adapter` for why the
    binder cannot reach the two fields that gate compares.
```

- [ ] **Step 7: Run the reducer and registry tests and watch them pass**

Run: `uv run pytest tests/steps/test_reducers.py tests/workflow/test_registry.py -v`
Expected: PASS. In particular `test_plan_hash_gate_adapter_compares_the_two_results` passes, and the existing `tests/workflow/test_registry.py` assertions (`registry.resolve("plan_hash_gate") is plan_hash_gate_adapter`, `plan_hash_gate_adapter is not reducers.plan_hash_gate`, and the adapter behaviour tests around line 289-316) pass unchanged.

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/steps/reducers.py src/agent_manager/workflow/registry.py tests/steps/test_reducers.py
git commit -m "refactor(steps): move plan_hash_gate_adapter into reducers"
```

---

### Task 2: Add `phases.from_loader`

**Files:**
- Modify: `src/agent_manager/workflow/phases.py` (lines 9-17 import block; append after line 147, the end of `digest()`)
- Test: `tests/workflow/test_phases.py` (lines 4-20 import block; append at end)

**Interfaces:**
- Consumes: `loader.Workflow` (`.name: str`, `.phases: list[DeterministicPhase | AgentPhase]`, `.function(name: str) -> Callable`, which raises `registry.UnknownFunctionError` for an unresolved name); `loader.DeterministicPhase` (`name`, `run: str`, `args: dict[str, Any]`, `best_effort: bool`, `when: str | None`, `skip_to: str | None`, `gates: list[str]`); `loader.AgentPhase` (`name`, `role: str`, `inputs: list[str]`, `result: str | None`, `writes: str | None`, `retry: RetryPolicy | None`, `when`, `skip_to`, `gates`); `loader.RetryPolicy` (`max_attempts: int`, `on: list[str]`); `results.RESULT_MODELS: dict[str, type[BaseModel]]`; `reducers.plan_hash_gate_adapter` from Task 1.
- Produces: `agent_manager.workflow.phases.from_loader(loaded: loader.Workflow, *, timeout: timedelta = timedelta(minutes=30)) -> Workflow`. Sibling 04a5b91e uses it to pin `TASK`/`INTEGRATE` digests to the shipped YAML.

- [ ] **Step 1: Extend the test module's imports**

In `tests/workflow/test_phases.py`, replace the import block (lines 4-20):

```python
import ast
import dataclasses
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from agent_manager.workflow import phases as phases_module
from agent_manager.workflow.phases import (
    AgentPhase,
    Goto,
    Retry,
    Step,
    Workflow,
    WorkflowError,
)
```

with:

```python
import ast
import dataclasses
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from agent_manager import results
from agent_manager.steps import reducers, rollup
from agent_manager.workflow import phases as phases_module
from agent_manager.workflow.loader import load_builtin, load_workflow
from agent_manager.workflow.phases import (
    AgentPhase,
    Goto,
    Retry,
    Step,
    Workflow,
    WorkflowError,
    from_loader,
)
from agent_manager.workflow.registry import (
    FunctionRegistry,
    UnknownFunctionError,
    default_registry,
)
```

- [ ] **Step 2: Append the failing tests**

Append at the end of `tests/workflow/test_phases.py`:

```python
# --- from_loader (card 0326732a) -------------------------------------------


def fake_run_suite(worktree): return {"passed": True}
def fake_verification_passed_gate(result): return None
def fake_merge_completed_gate(result): return None
def fake_when(state): return True
def fake_run(worktree): return {"ok": True}


def test_from_loader_resolves_every_name_of_the_shipped_task():
    loaded = load_builtin("task", default_registry())
    converted = from_loader(loaded)
    assert converted.phase_names == tuple(p.name for p in loaded.phases)
    review = converted.phase("review")
    assert review.result is results.ReviewResult
    assert reducers.review_gate in review.gates
    assert reducers.plan_hash_gate_adapter in review.gates
    assert converted.phase("explore").retry == Retry(2, ("schema_invalid", "gate_failed"))
    plan_check = converted.phase("plan_check")
    assert plan_check.skip_to == "docs_commit" and callable(plan_check.when)


def test_from_loader_keeps_fakes_from_a_test_registry():
    # The engine tests build workflows from fake registries; the conversion
    # must carry whatever callable the loaded workflow holds, by identity.
    loaded = load_builtin("integrate", default_registry())
    assert from_loader(loaded).phase("verify").run is loaded.function("verify.run_suite")

    fakes = FunctionRegistry()
    fakes.register("merge_completed_gate", fake_merge_completed_gate)
    fakes.register("verify.run_suite", fake_run_suite)
    fakes.register("verification_passed_gate", fake_verification_passed_gate)
    converted = from_loader(load_builtin("integrate", fakes))
    assert converted.phase("verify").run is fake_run_suite
    assert converted.phase("verify").gates == (fake_verification_passed_gate,)
    assert converted.phase("resolve").gates == (fake_merge_completed_gate,)


def test_from_loader_maps_a_deterministic_phase_field_by_field():
    loaded = load_builtin("task", default_registry())
    step = from_loader(loaded).phase("mark_in_progress")
    assert isinstance(step, Step)
    assert step.run is rollup.set_status
    assert step.args == {"status": "in_progress"}
    # A copy, not the loader model's own dict (Review Focus 4).
    assert step.args is not loaded.phase("mark_in_progress").args
    assert step.best_effort is True
    assert step.when is None and step.skip_to is None and step.gates == ()


def test_from_loader_maps_an_agent_phase_field_by_field():
    converted = from_loader(load_builtin("task", default_registry()))
    spec = converted.phase("spec")
    assert isinstance(spec, AgentPhase)
    assert spec.role == "spec_author"
    assert spec.inputs == ("card", "explore", "spec_path")
    assert spec.result is results.SpecResult
    assert spec.writes == "docs/superpowers/specs/{stem}.md"
    assert spec.retry is None and spec.gates == ()
    assert all(p.on_fail is None for p in converted.phases if isinstance(p, AgentPhase))


def test_from_loader_puts_the_timeout_on_every_agent_phase():
    loaded = load_builtin("task", default_registry())
    agents = [p for p in from_loader(loaded).phases if isinstance(p, AgentPhase)]
    assert agents and all(p.timeout == timedelta(minutes=30) for p in agents)
    longer = from_loader(loaded, timeout=timedelta(minutes=45))
    assert all(p.timeout == timedelta(minutes=45)
               for p in longer.phases if isinstance(p, AgentPhase))


def test_from_loader_digest_is_reproducible_across_loads():
    # Review Focus 5: sibling 04a5b91e pins declared digests to this value.
    first = from_loader(load_builtin("task", default_registry())).digest()
    second = from_loader(load_builtin("task", default_registry())).digest()
    assert first == second


def _loaded_with_agent(agent_extra: str):
    """A two-phase `loader.Workflow`: an agent phase `ask` with `agent_extra`
    YAML lines appended, then a deterministic phase `later`."""
    registry = FunctionRegistry()
    registry.register("fake_when", fake_when)
    registry.register("fake_run", fake_run)
    document = (
        "name: t\n"
        "phases:\n"
        "  - name: ask\n"
        "    kind: agent\n"
        "    role: explorer\n"
        f"{agent_extra}"
        "  - name: later\n"
        "    kind: deterministic\n"
        "    run: fake_run\n"
    )
    return load_workflow(document, registry)


def test_from_loader_rejects_an_unknown_result_name():
    loaded = _loaded_with_agent("    result: NoSuchResult\n")
    with pytest.raises(WorkflowError, match="NoSuchResult") as info:
        from_loader(loaded)
    assert info.value.phase == "ask"


@pytest.mark.parametrize("agent_extra", [
    "    when: fake_when\n",
    "    skip_to: later\n",
    "    when: fake_when\n    skip_to: later\n",
], ids=["when", "skip_to", "both"])
def test_from_loader_rejects_when_on_an_agent_phase(agent_extra):
    loaded = _loaded_with_agent(agent_extra)
    with pytest.raises(WorkflowError, match=r"when.*skip_to") as info:
        from_loader(loaded)
    assert info.value.phase == "ask"


def test_from_loader_lets_an_unresolved_name_raise_unknown_function_error():
    # Review Focus 3: the loader's own error, not a KeyError or a WorkflowError.
    stripped = _loaded_with_agent("").model_copy(update={"functions": {}})
    with pytest.raises(UnknownFunctionError, match="fake_run"):
        from_loader(stripped)
```

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run pytest tests/workflow/test_phases.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'from_loader' from 'agent_manager.workflow.phases'`.

- [ ] **Step 4: Confirm the loader's attribute names**

Open `src/agent_manager/workflow/loader.py` and confirm, before writing Step 5's code, that `_PhaseBase` (line 72) still declares `name`, `when`, `skip_to`, `gates`; `DeterministicPhase` (line 79) declares `run`, `args`, `best_effort`; `AgentPhase` (line 88) declares `role`, `inputs`, `result`, `writes`, `retry`; `RetryPolicy` (line 61) declares `max_attempts`, `on`; and `Workflow.function` (line 133) raises `UnknownFunctionError`. As of this worktree they all do. If any is spelled differently, use the loader's spelling in Step 5.

- [ ] **Step 5: Add a type-only import of the loader to `phases.py`**

In `src/agent_manager/workflow/phases.py`, replace line 15:

```python
from typing import Any, Callable, Mapping
```

with:

```python
from typing import TYPE_CHECKING, Any, Callable, Mapping
```

and, directly after line 17 (`from pydantic import BaseModel`), add:

```python

if TYPE_CHECKING:
    from agent_manager.workflow import loader
```

The module already has `from __future__ import annotations` (line 9), so the `loader.Workflow` annotation is never evaluated at runtime and `phases.py` gains no import-time dependency on the loader, registry or steps.

- [ ] **Step 6: Append `from_loader` to `phases.py`**

Append at the end of `src/agent_manager/workflow/phases.py`, after `Workflow.digest()`:

```python


def from_loader(loaded: loader.Workflow, *, timeout: timedelta = timedelta(minutes=30)) -> Workflow:
    """A resolved `workflow.loader.Workflow` as phase-model data. Side by side only (G7).

    Pure: nothing is read or written, and `loaded` is left as it was. Every
    `run`, `when` and gate name goes through `loaded.function(name)`, never a
    registry, so whatever callable the loaded workflow holds -- a fake from a
    test registry included -- is carried through by identity, and a name it
    never resolved raises the loader's own `UnknownFunctionError`. `timeout`
    is put on every agent phase.

    Two things the loader accepts cannot be expressed here and are refused
    with a `WorkflowError` naming the phase rather than silently dropped: a
    `result:` name with no model in `results.RESULT_MODELS`, and `when` or
    `skip_to` on an agent phase (`AgentPhase` has no field for either).
    """
    from agent_manager import results
    from agent_manager.workflow.loader import DeterministicPhase

    out: list[Step | AgentPhase] = []
    for p in loaded.phases:
        if isinstance(p, DeterministicPhase):
            out.append(Step(
                p.name,
                loaded.function(p.run),
                dict(p.args),
                tuple(loaded.function(name) for name in p.gates),
                p.best_effort,
                loaded.function(p.when) if p.when is not None else None,
                p.skip_to,
            ))
            continue
        if p.when is not None or p.skip_to is not None:
            raise WorkflowError(
                "an agent phase cannot carry `when` or `skip_to`; "
                "only a deterministic step can skip",
                phase=p.name,
            )
        if p.result is not None and p.result not in results.RESULT_MODELS:
            raise WorkflowError(
                f"result {p.result!r} is not a known result model "
                f"(known: {', '.join(sorted(results.RESULT_MODELS))})",
                phase=p.name,
            )
        out.append(AgentPhase(
            p.name,
            p.role,
            tuple(p.inputs),
            results.RESULT_MODELS[p.result] if p.result is not None else None,
            tuple(loaded.function(name) for name in p.gates),
            Retry(p.retry.max_attempts, tuple(p.retry.on)) if p.retry is not None else None,
            p.writes,
            timeout,
        ))
    return Workflow(loaded.name, tuple(out))
```

- [ ] **Step 7: Run the phase tests and watch them pass**

Run: `uv run pytest tests/workflow/test_phases.py -v`
Expected: PASS, including the pre-existing `test_phases_module_never_imports_pygents` (the new imports are `agent_manager.results` and `agent_manager.workflow.loader`, neither of which is pygents).

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest`
Expected: all green, with `tests/workflow/test_registry.py`, `tests/workflow/test_loader.py` and `tests/steps/test_reducers.py` unchanged apart from Task 1's addition.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/workflow/phases.py tests/workflow/test_phases.py
git commit -m "feat(workflow): convert loaded YAML workflows into the phase model"
```

---

## Self-Review

- **Spec coverage.** Scope 1 (`from_loader`) is Task 2 Step 6. Scope 2 (verbatim move, re-import, still registered as `"plan_hash_gate"`, `Mapping` handled, unused import dropped) is Task 1 Steps 3-5. Observable behavior: order and identity (`test_from_loader_resolves_every_name_of_the_shipped_task`, `test_from_loader_keeps_fakes_from_a_test_registry`), the Step mapping (`..._maps_a_deterministic_phase_field_by_field`), the AgentPhase mapping with `on_fail=None` (`..._maps_an_agent_phase_field_by_field`), `timeout` (`..._puts_the_timeout_on_every_agent_phase`), purity and no pygents (`args` copy test plus the existing AST test). Error paths: `UnknownFunctionError` unchanged, unknown `result` raises `WorkflowError`, and agent `when`/`skip_to` raises `WorkflowError`, each with a test. Tests land only in `tests/workflow/test_phases.py` and `tests/steps/test_reducers.py`, not in `tests/e2e/`. Out-of-scope items (`TASK`/`INTEGRATE`, per-phase timeouts, digest equality against declared workflows) are not touched.
- **Placeholder scan.** Every code step has complete code. No TBD, no "similar to", and no undefined names.
- **Type consistency.** `from_loader(loaded: loader.Workflow, *, timeout: timedelta = timedelta(minutes=30)) -> Workflow` is the same in Interfaces, Step 6 and the tests. `plan_hash_gate_adapter(implement: object = None, review: object = None)` is the same in Task 1 and in Task 2's consumed interfaces. Positional `Step(...)`/`AgentPhase(...)` arguments follow the field order in `phases.py` lines 41-61 (`Step`: name, run, args, gates, best_effort, when, skip_to; `AgentPhase`: name, role, inputs, result, gates, retry, writes, timeout).
- **Deviations from the parent plan's sample code, made on purpose:** `from_loader` annotates `loaded: loader.Workflow` under `TYPE_CHECKING` rather than `Any`, matching the spec's signature. It checks `is not None` rather than truthiness, so an empty-string `when` could never slip past as "no when"; the loader's `when: str | None` makes the two equivalent for real documents. It adds the two `WorkflowError` paths the spec requires. The moved adapter calls `plan_hash_gate` rather than `reducers.plan_hash_gate`, because it now lives inside `reducers`. The fake-registry test adds a real fake registry on top of the plan's default-registry assertion, so its name matches what it checks.
- **Upstream note.** The exploration findings handed to the spec and plan stages were cut off at 8000 characters, mid-sentence, in the test-tier paragraph. Treat that as evidence the exploration stage over-ran its brief. This plan does not guess at the missing text; the placement rule it follows comes from the spec and the design docs directly.
