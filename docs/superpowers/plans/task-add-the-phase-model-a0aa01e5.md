<!-- task-pipeline: validated -->
# Subtask a0aa01e5: Add the phase model with `validate()` and `digest()`

Parent story: 09e1183f "The declared phase model" (milestone 84c3b532). Narrows plan Task 1.1 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:65-264`) and milestone spec §5 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:175-217`), realizing decisions G2 and G3 (same spec, lines 64-72).

## Scope

- Create `src/agent_manager/workflow/phases.py`: the workflow as declared, frozen Python data. The plan's reference implementation (plan lines 141-264) is the agreed shape.
- Modify `src/agent_manager/prompt.py`: add `INPUT_NAMES: frozenset[str] = frozenset(_TABLE)` with a one-line docstring. Put it directly after `_TABLE` and its docstring (after line 288, before `INPUT_PRODUCERS`). Nothing else in `prompt.py` changes.
- Create `tests/workflow/test_phases.py`.

No other file changes. In particular, this subtask does not touch `workflow/registry.py`, `steps/reducers.py`, `workflow/builtin/*.yaml`, the engine, or `runtime/`.

## Public surface

- `WorkflowError(ValueError)`: constructed as `WorkflowError(message, *, phase=None)`. It exposes `.phase: str | None`. When a phase is given, `str()` is `"phase '<name>': <message>"`.
- `Goto(phase: str, max_loops: int = 1)`, a frozen dataclass.
- `Retry(max_attempts: int, on: tuple[str, ...])`, a frozen dataclass.
- `Step(name, run, args={}, gates=(), best_effort=False, when=None, skip_to=None)`, a frozen dataclass. `args` is a mapping (default factory `dict`). `gates` is a tuple of callables. `when` is a callable or `None`.
- `AgentPhase(name, role, inputs, result, gates=(), retry=None, writes=None, timeout=timedelta(minutes=30), on_fail=None)`, a frozen dataclass. `inputs` is a tuple of names. `result` is a pydantic `BaseModel` subclass or `None`. `on_fail` is a `Goto` or `None`.
- `Workflow(name, phases: tuple[Step | AgentPhase, ...])`, a frozen dataclass, with:
  - `.phase_names -> tuple[str, ...]`: the phase names in declared order.
  - `.phase(name)`: returns the phase with that name. An unknown name raises `WorkflowError` that lists the known phase names.
  - `.validate(*, launcher_timeout: timedelta, role_root: Path | None = None) -> None`
  - `.digest() -> str`
- `prompt.INPUT_NAMES: frozenset[str]`: exactly the keys of `_TABLE`.

## `validate()` behavior

`validate()` returns `None` when the workflow is valid. Otherwise it raises `WorkflowError` with `.phase` set to the phase that broke the rule. The message contains the listed keyword, which tests match by regex. Rules:

1. Duplicate phase name → "duplicate". This is checked across the whole workflow before any other rule.
2. On a `Step`, `when` without `skip_to`, or `skip_to` without `when` → a message naming both `when` and `skip_to`.
3. On a `Step`, `skip_to` names a phase that is not strictly later (itself, an earlier phase, or an unknown name) → "skip_to ... must name a later phase".
4. On an `AgentPhase`, the role does not load via `roles.loader.load_role(role, root=role_root)` → "role ... does not load: <cause>". The loader exception is chained as `__cause__`. The call must use exactly this signature (`loader.py:140`). `role_root=None` means the packaged bundles.
5. An `AgentPhase` input that is not in `prompt.INPUT_NAMES` and is not the name of a strictly earlier phase → "input ... has no resolver and no earlier phase".
6. `AgentPhase.timeout <= launcher_timeout` → "timeout ... must exceed the launcher timeout ...". The comparison is strict (G2): the launcher must kill `claude -p` before the turn times out, because cancellation cannot stop a `to_thread` worker.
7. On an `AgentPhase`, an `on_fail` with `max_loops < 1` → "Goto max_loops must be at least 1".
8. On an `AgentPhase`, an `on_fail` whose target is not strictly earlier (itself, a later phase, or an unknown name) → "Goto ... must name an earlier phase".

`prompt` and `roles.loader` are imported inside `validate()`, so importing `phases` stays light. `validate()` does no I/O apart from loading roles.

## `digest()` behavior

`digest()` returns the sha256 hex digest of the following data:
- the workflow name;
- then one record per phase, in declared order;
- within a record, fields are joined by `\x1f`, and each record ends with `\x1e`.

Callables are rendered by a `_qual(fn)` helper as `module.qualname`. Record contents:
- Step: `"step"`, name, `_qual(run)`, `repr(sorted(args.items()))`, the gate qualnames, `best_effort`, the `when` qualname or `-`, and `skip_to` or `-`.
- AgentPhase: `"agent"`, name, role, comma-joined inputs, the result qualname or `-`, the gate qualnames, `repr(retry)`, `writes` or `-`, `str(timeout)`, and `repr(on_fail)`.

Two identical rebuilds must give the same digest. Any of the following must change the digest: reordering phases, renaming a phase, retargeting `skip_to` or `on_fail`, swapping a gate, or changing retry, timeout, role or inputs.

## Hard constraints

- `workflow/phases.py` never imports pygents. Only `src/agent_manager/runtime/` may (rule 1). The module docstring says so: "No pygents import here."
- The whole default suite, including `tests/e2e`, must stay green (rule 2, G7). This subtask adds modules and does not rewire any existing ones, so nothing that runs today should change.
- `SubtaskSummary`, journal lines, phase and attempt rows, and escalation payloads are not touched (rule 5, G10).

## Out of scope

- `phases.from_loader(...)` and moving `plan_hash_gate_adapter`/`_plan_hash_of` belong to sibling 0326732a.
- `workflow/task.py` (TASK, LAUNCHER_TIMEOUT), `workflow/integrate.py` (INTEGRATE) and the builtin YAML files belong to sibling 04a5b91e.
- Also out of scope: `runtime/compile.py` and dispatch, the supervisor tree, exactly-once phases, prompt benchmarking, and upstream pygents fixes.

## Tests

All tests go in `tests/workflow/test_phases.py`. The rule is that each module's tests mirror its path under `tests/` (spec §9). `phases.py` is pure data with no I/O, so these are plain unit tests: no fakes, no git repo, no harness. They do not go in `steps/`, `harness/` or `e2e/`.

The file content is the one given in plan lines 77-125. It uses `LAUNCHER = timedelta(minutes=10)` and module-level `step_fn`, `gate_ok` and `other_gate`, so the qualnames stay stable. The helper `agent()` defaults to role `explorer` (a shipped bundle) and inputs `("card",)` (a `_TABLE` key).

1. `test_valid_workflow_passes`: a Step followed by two AgentPhases, the last with `Goto` back to an earlier phase, validates cleanly.
2. `test_validate_refuses` is parametrized, and each case expects `WorkflowError` with a matching needle:
   - a duplicate name (`duplicate`);
   - `skip_to` pointing at itself (`skip_to`);
   - `when` without `skip_to` (`when`);
   - `skip_to` without `when` (`when`);
   - a forward `Goto` (`Goto`);
   - `max_loops=0` (`max_loops`);
   - an unknown role (`role`);
   - an unknown input (`input`);
   - a timeout equal to the launcher timeout (`timeout`).
3. `test_an_earlier_phase_name_is_a_valid_input`: a phase may list an earlier phase's name as an input.
4. `test_digest_is_stable_and_sensitive`: rebuilding the same workflow gives the same digest. A reorder, a rename, a gate swap and an added `Retry` each give a different digest.

Verification: `uv run pytest` (full suite). There is no typecheck or lint command.

---

# Phase Model (`validate()` and `digest()`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/workflow/phases.py`, which holds the workflow as frozen Python data (`WorkflowError`, `Goto`, `Retry`, `Step`, `AgentPhase`, `Workflow`) with `.phase_names`, `.phase()`, `.validate()` and `.digest()`. Also add a public `prompt.INPUT_NAMES`.

**Architecture:** One pure-data module with no I/O at import time and no pygents import. `validate()` imports `agent_manager.prompt` and `agent_manager.roles.loader` lazily and raises `WorkflowError(..., phase=<name>)` on the first rule a phase breaks. `digest()` is a sha256 over the workflow name and one `\x1f`-joined, `\x1e`-terminated record per phase, with callables rendered as `module.qualname`. The work is split into three tasks: the data shapes and lookup, then `validate()` together with `INPUT_NAMES` (its only consumer), then `digest()`.

**Tech Stack:** Python 3 dataclasses (frozen), pydantic `BaseModel` (type only), `hashlib`, pytest. Run everything with `uv run pytest`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-add-the-phase-model-a0aa01e5/docs/superpowers/specs/task-add-the-phase-model-a0aa01e5-design.md` (prepended above). Upstream: `docs/superpowers/plans/2026-09-25-pygents-engine.md:65-274` (Task 1.1 reference code) and `docs/superpowers/specs/2026-09-25-pygents-engine-design.md:64-72,175-217`.

**Working location:** worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-add-the-phase-model-a0aa01e5`, branch `m6/task-add-the-phase-model-a0aa01e5`. The branch was cut fresh from master. Assume nothing from sibling subtasks 0326732a or 04a5b91e exists: there is no `from_loader`, no `workflow/task.py` and no `workflow/integrate.py`. All paths below are relative to the worktree root.

## Global Constraints

- `src/agent_manager/workflow/phases.py` must never import `pygents`, directly or indirectly at module import. Its module docstring contains the sentence "No pygents import here."
- `validate()` imports `agent_manager.prompt` and `agent_manager.roles.loader` inside the method body, not at module top.
- `validate()` calls the role loader as exactly `load_role(p.role, root=role_root)` (signature `load_role(name: str, *, root: Path | None = None) -> RoleBundle`, `src/agent_manager/roles/loader.py:140`).
- Timeout rule G2: `AgentPhase.timeout` must be strictly greater than `launcher_timeout`, so equal is refused.
- `prompt.py` gains exactly one binding, `INPUT_NAMES: frozenset[str] = frozenset(_TABLE)`, plus a one-line docstring. It goes after the `_TABLE` docstring (ends at line 288) and before `INPUT_PRODUCERS` (line 291). Nothing else in `prompt.py` changes.
- No changes to `workflow/registry.py`, `steps/reducers.py`, `workflow/builtin/*.yaml`, `engine.py`, or anything under `runtime/`. `SubtaskSummary`, journal lines, phase and attempt rows, and escalation payloads stay unchanged.
- All tests live in `tests/workflow/test_phases.py`. `tests/` has no `__init__.py` files, and pytest runs with `--import-mode=importlib -m "not e2e"` (`pyproject.toml:28-38`), so no package marker file is needed.
- Verification: `uv run pytest` (full default suite, e2e deselected by addopts) must be green at the end of every task.

## Review Focus

The spec implies these cases, but its listed tests do not exercise them. Each one is pinned by a test in the task that owns the code:

1. An `AgentPhase` input that names a later phase, or the phase itself, must be refused as "input": only strictly earlier phases count. Pinned in Task 2 (`test_an_input_naming_a_later_or_same_phase_is_refused`).
2. `skip_to` pointing at an earlier or unknown phase, and `Goto` pointing at itself or an unknown phase, must be refused. The spec's listed cases only cover "self" for `skip_to` and "later" for `Goto`. Pinned in Task 2 (`test_validate_refuses` extra cases).
3. A role that fails to load must set `.phase` and chain the loader's `RoleBundleError` as `__cause__`, and `role_root` must actually reach the loader. An empty `role_root` must make even `explorer` fail. Pinned in Task 2 (`test_role_failure_is_chained_and_honours_role_root`).
4. A timeout one second above the launcher timeout must pass: the boundary is strict, not off by one. Pinned in Task 2 (`test_timeout_just_above_the_launcher_passes`).
5. `digest()` must not depend on the insertion order of `Step.args`, and it must change when timeout, role, inputs, `skip_to` or `on_fail` change. The spec requires these, but its listed test covers only reorder, rename, gate swap and retry. Pinned in Task 3 (`test_digest_ignores_args_order` and extra cases in `test_digest_is_stable_and_sensitive`).

---

### Task 1: The phase data shapes, `WorkflowError`, and `Workflow.phase_names` / `.phase()`

**Files:**
- Create: `src/agent_manager/workflow/phases.py`
- Test: `tests/workflow/test_phases.py` (new)

**Interfaces:**
- Consumes: nothing from this subtask. Uses `pydantic.BaseModel` only as a type annotation.
- Produces: `WorkflowError(message: str, *, phase: str | None = None)` with `.phase`; `Goto(phase: str, max_loops: int = 1)`; `Retry(max_attempts: int, on: tuple[str, ...])`; `Step(name: str, run: Callable, args: Mapping[str, Any] = {}, gates: tuple[Callable, ...] = (), best_effort: bool = False, when: Callable | None = None, skip_to: str | None = None)`; `AgentPhase(name: str, role: str, inputs: tuple[str, ...], result: type[BaseModel] | None, gates: tuple[Callable, ...] = (), retry: Retry | None = None, writes: str | None = None, timeout: timedelta = timedelta(minutes=30), on_fail: Goto | None = None)`; `Workflow(name: str, phases: tuple[Step | AgentPhase, ...])` with `.phase_names -> tuple[str, ...]` and `.phase(name: str) -> Step | AgentPhase`.

- [ ] **Step 1: Write the failing tests**

Create `tests/workflow/test_phases.py` with exactly this content:

```python
"""Pure-data tier (spec §9): phases.py has no I/O beyond validate()'s role
loading, so these are plain unit tests -- no fakes, no git, no harness."""

import ast
import dataclasses
from datetime import timedelta
from pathlib import Path

import pytest

from agent_manager.workflow import phases as phases_module
from agent_manager.workflow.phases import (
    AgentPhase,
    Goto,
    Retry,
    Step,
    Workflow,
    WorkflowError,
)

LAUNCHER = timedelta(minutes=10)


def step_fn(worktree): return {"ok": True}
def gate_ok(result): return None
def other_gate(result): return None


def wf(*phases): return Workflow("t", tuple(phases))


def agent(name, **kw):
    kw.setdefault("role", "explorer")
    kw.setdefault("inputs", ("card",))
    kw.setdefault("result", None)
    return AgentPhase(name, **kw)


def test_phases_module_never_imports_pygents():
    tree = ast.parse(Path(phases_module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name == "pygents" or name.startswith("pygents.") for name in imported)
    assert "No pygents import here." in (phases_module.__doc__ or "")


def test_defaults_match_the_declared_shape():
    step = Step("a", step_fn)
    assert (step.args, step.gates, step.best_effort, step.when, step.skip_to) == ({}, (), False, None, None)
    phase = agent("b")
    assert phase.gates == () and phase.retry is None and phase.writes is None
    assert phase.timeout == timedelta(minutes=30) and phase.on_fail is None
    assert Goto("b").max_loops == 1
    assert Retry(2, ("gate_failed",)).on == ("gate_failed",)


def test_phase_model_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Step("a", step_fn).name = "b"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        agent("a").timeout = timedelta(0)  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        wf(Step("a", step_fn)).name = "u"  # type: ignore[misc]


def test_phase_names_and_lookup():
    a, b = Step("a", step_fn), agent("b")
    workflow = wf(a, b)
    assert workflow.phase_names == ("a", "b")
    assert workflow.phase("b") is b


def test_unknown_phase_lookup_lists_known_names():
    with pytest.raises(WorkflowError, match=r"no phase named 'zz'.*a, b") as info:
        wf(Step("a", step_fn), agent("b")).phase("zz")
    assert info.value.phase is None


def test_workflow_error_names_the_phase():
    error = WorkflowError("boom", phase="spec")
    assert isinstance(error, ValueError)
    assert error.phase == "spec"
    assert str(error) == "phase 'spec': boom"
    assert str(WorkflowError("boom")) == "boom"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/workflow/test_phases.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'agent_manager.workflow.phases'`.

- [ ] **Step 3: Implement the data shapes**

Create `src/agent_manager/workflow/phases.py` with exactly this content:

```python
"""The workflow as declared Python data (spec G3). No pygents import here.

Only `agent_manager.runtime` may import pygents (rule 1). This module is frozen
data plus two pure checks -- `Workflow.validate()` and `Workflow.digest()` -- so
the compiler can trust what it is handed and a resumed run can tell whether the
workflow it checkpointed is the one it is about to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Mapping

from pydantic import BaseModel


class WorkflowError(ValueError):
    """A declared workflow that must not run; `.phase` names the phase at fault."""

    def __init__(self, message: str, *, phase: str | None = None) -> None:
        self.phase = phase
        super().__init__(f"phase {phase!r}: {message}" if phase else message)


@dataclass(frozen=True)
class Goto:
    phase: str
    max_loops: int = 1


@dataclass(frozen=True)
class Retry:
    max_attempts: int
    on: tuple[str, ...]


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[..., Any]
    args: Mapping[str, Any] = field(default_factory=dict)
    gates: tuple[Callable[..., Any], ...] = ()
    best_effort: bool = False
    when: Callable[..., bool] | None = None
    skip_to: str | None = None


@dataclass(frozen=True)
class AgentPhase:
    name: str
    role: str
    inputs: tuple[str, ...]
    result: type[BaseModel] | None
    gates: tuple[Callable[..., Any], ...] = ()
    retry: Retry | None = None
    writes: str | None = None
    timeout: timedelta = timedelta(minutes=30)
    on_fail: Goto | None = None


@dataclass(frozen=True)
class Workflow:
    name: str
    phases: tuple[Step | AgentPhase, ...]

    @property
    def phase_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.phases)

    def phase(self, name: str) -> Step | AgentPhase:
        for p in self.phases:
            if p.name == name:
                return p
        raise WorkflowError(f"no phase named {name!r} (phases: {', '.join(self.phase_names)})")
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run pytest tests/workflow/test_phases.py -v`
Expected: 6 passed.

Then run: `uv run pytest`
Expected: the whole default suite is green (e2e deselected by addopts).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/workflow/phases.py tests/workflow/test_phases.py
git commit -m "feat(workflow): add the declared phase data model"
```

---

### Task 2: `prompt.INPUT_NAMES` and `Workflow.validate()`

**Files:**
- Modify: `src/agent_manager/prompt.py` (insert between line 288, the end of the `_TABLE` docstring, and line 291, `INPUT_PRODUCERS`)
- Modify: `src/agent_manager/workflow/phases.py` (add the `Path` import and the `validate()` method)
- Test: `tests/workflow/test_phases.py` (append)

**Interfaces:**
- Consumes: from Task 1, `Workflow`, `Step`, `AgentPhase`, `Goto` and `WorkflowError(message, *, phase=None)`. `agent_manager.prompt._TABLE: dict[str, Resolver]` (`prompt.py:267`) and `agent_manager.roles.loader.load_role(name, *, root=None)`, which raises `RoleBundleError` (a `RuntimeError`).
- Produces: `prompt.INPUT_NAMES: frozenset[str]` and `Workflow.validate(*, launcher_timeout: timedelta, role_root: Path | None = None) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/workflow/test_phases.py`:

```python
def test_input_names_are_exactly_the_resolver_table():
    from agent_manager import prompt

    assert isinstance(prompt.INPUT_NAMES, frozenset)
    assert prompt.INPUT_NAMES == frozenset(prompt._TABLE)
    assert "card" in prompt.INPUT_NAMES


def test_valid_workflow_passes():
    wf(Step("a", step_fn), agent("b"), agent("c", on_fail=Goto("b"))).validate(launcher_timeout=LAUNCHER)


@pytest.mark.parametrize("phases, needle", [
    ((Step("a", step_fn), Step("a", step_fn)), "duplicate"),
    ((Step("a", step_fn, skip_to="a", when=gate_ok),), "skip_to"),
    ((Step("a", step_fn, when=gate_ok),), "when"),
    ((Step("a", step_fn, skip_to="b"), Step("b", step_fn)), "when"),
    ((agent("a", on_fail=Goto("b")), agent("b")), "Goto"),
    ((agent("a"), agent("b", on_fail=Goto("a", max_loops=0))), "max_loops"),
    ((agent("a", role="no_such_role"),), "role"),
    ((agent("a", inputs=("nonsense",)),), "input"),
    ((agent("a", timeout=timedelta(minutes=10)),), "timeout"),
    # Review Focus 2: the other non-strict directions and unknown targets.
    ((Step("a", step_fn), Step("b", step_fn, when=gate_ok, skip_to="a")), "skip_to .* must name a later phase"),
    ((Step("a", step_fn, when=gate_ok, skip_to="nowhere"),), "skip_to .* must name a later phase"),
    ((agent("a", on_fail=Goto("a")),), "Goto .* must name an earlier phase"),
    ((agent("a"), agent("b", on_fail=Goto("nowhere"))), "Goto .* must name an earlier phase"),
])
def test_validate_refuses(phases, needle):
    with pytest.raises(WorkflowError, match=needle) as info:
        wf(*phases).validate(launcher_timeout=LAUNCHER)
    assert info.value.phase is not None


def test_duplicate_is_checked_before_any_other_rule():
    # The first "a" has a bad role, but the duplicate is reported first.
    with pytest.raises(WorkflowError, match="duplicate") as info:
        wf(agent("a", role="no_such_role"), Step("a", step_fn)).validate(launcher_timeout=LAUNCHER)
    assert info.value.phase == "a"


def test_when_without_skip_to_names_both_fields():
    with pytest.raises(WorkflowError, match=r"when.*skip_to") as info:
        wf(Step("a", step_fn, when=gate_ok), Step("b", step_fn)).validate(launcher_timeout=LAUNCHER)
    assert info.value.phase == "a"


def test_an_earlier_phase_name_is_a_valid_input():
    wf(agent("explore"), agent("spec", inputs=("explore",))).validate(launcher_timeout=LAUNCHER)


def test_an_earlier_phase_name_outside_the_resolver_table_is_a_valid_input():
    # "explore" above is also a _TABLE key; "design" is not, so only the
    # earlier-phase rule can accept it.
    wf(agent("design"), agent("impl", inputs=("card", "design"))).validate(launcher_timeout=LAUNCHER)


def test_an_input_naming_a_later_or_same_phase_is_refused():
    # Review Focus 1: only a strictly earlier phase can feed an input.
    with pytest.raises(WorkflowError, match="input 'b' has no resolver and no earlier phase") as info:
        wf(agent("a", inputs=("b",)), agent("b")).validate(launcher_timeout=LAUNCHER)
    assert info.value.phase == "a"
    with pytest.raises(WorkflowError, match="input 'a'"):
        wf(agent("a", inputs=("a",))).validate(launcher_timeout=LAUNCHER)


def test_role_failure_is_chained_and_honours_role_root(tmp_path):
    # Review Focus 3: an empty role_root means even a shipped role cannot load.
    from agent_manager.roles.loader import RoleBundleError

    with pytest.raises(WorkflowError, match="role 'explorer' does not load") as info:
        wf(agent("a")).validate(launcher_timeout=LAUNCHER, role_root=tmp_path)
    assert info.value.phase == "a"
    assert isinstance(info.value.__cause__, RoleBundleError)


def test_timeout_just_above_the_launcher_passes():
    # Review Focus 4: G2 is strict, so one second over is enough.
    wf(agent("a", timeout=LAUNCHER + timedelta(seconds=1))).validate(launcher_timeout=LAUNCHER)
    with pytest.raises(WorkflowError, match="timeout .* must exceed the launcher timeout"):
        wf(agent("a", timeout=LAUNCHER - timedelta(seconds=1))).validate(launcher_timeout=LAUNCHER)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/workflow/test_phases.py -v`
Expected: the 6 Task 1 tests pass. `test_input_names_are_exactly_the_resolver_table` FAILs with `AttributeError: module 'agent_manager.prompt' has no attribute 'INPUT_NAMES'`. Every validate test FAILs with `AttributeError: 'Workflow' object has no attribute 'validate'`.

- [ ] **Step 3a: Add `INPUT_NAMES` to `prompt.py`**

In `src/agent_manager/prompt.py`, replace this block:

```python
the caller supplies both through `engine.run_subtask(extra_context=...)`.
"""


INPUT_PRODUCERS: dict[str, str] = {
```

with:

```python
the caller supplies both through `engine.run_subtask(extra_context=...)`.
"""


INPUT_NAMES: frozenset[str] = frozenset(_TABLE)
"""Every input name a phase may declare that a resolver provides (phases.validate)."""


INPUT_PRODUCERS: dict[str, str] = {
```

- [ ] **Step 3b: Add the `Path` import to `phases.py`**

In `src/agent_manager/workflow/phases.py`, replace:

```python
from datetime import timedelta
from typing import Any, Callable, Mapping
```

with:

```python
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
```

- [ ] **Step 3c: Add `validate()` to `Workflow`**

In `src/agent_manager/workflow/phases.py`, replace:

```python
        raise WorkflowError(f"no phase named {name!r} (phases: {', '.join(self.phase_names)})")
```

with:

```python
        raise WorkflowError(f"no phase named {name!r} (phases: {', '.join(self.phase_names)})")

    def validate(self, *, launcher_timeout: timedelta, role_root: Path | None = None) -> None:
        """Refuse a workflow that must not run, naming the phase at fault.

        `launcher_timeout` is G2: every agent phase's turn timeout must exceed
        it strictly, because the launcher has to kill `claude -p` before the
        turn is cancelled -- cancellation cannot stop a `to_thread` worker.
        """
        from agent_manager import prompt
        from agent_manager.roles.loader import load_role

        order: dict[str, int] = {}
        for index, p in enumerate(self.phases):
            if p.name in order:
                raise WorkflowError("duplicate phase name", phase=p.name)
            order[p.name] = index

        seen: list[str] = []
        for index, p in enumerate(self.phases):
            if isinstance(p, Step):
                if (p.when is None) != (p.skip_to is None):
                    raise WorkflowError("`when` and `skip_to` must be given together", phase=p.name)
                if p.skip_to is not None and order.get(p.skip_to, -1) <= index:
                    raise WorkflowError(f"skip_to {p.skip_to!r} must name a later phase", phase=p.name)
            else:
                try:
                    load_role(p.role, root=role_root)
                except Exception as error:
                    raise WorkflowError(f"role {p.role!r} does not load: {error}", phase=p.name) from error
                for name in p.inputs:
                    if name not in prompt.INPUT_NAMES and name not in seen:
                        raise WorkflowError(
                            f"input {name!r} has no resolver and no earlier phase", phase=p.name)
                if p.timeout <= launcher_timeout:
                    raise WorkflowError(
                        f"timeout {p.timeout} must exceed the launcher timeout {launcher_timeout}",
                        phase=p.name,
                    )
                if p.on_fail is not None:
                    if p.on_fail.max_loops < 1:
                        raise WorkflowError("Goto max_loops must be at least 1", phase=p.name)
                    if order.get(p.on_fail.phase, index) >= index:
                        raise WorkflowError(
                            f"Goto {p.on_fail.phase!r} must name an earlier phase", phase=p.name)
            seen.append(p.name)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run pytest tests/workflow/test_phases.py -v`
Expected: all tests pass (6 from Task 1, plus 1 + 1 + 13 parametrized + 7 from this task).

Then run: `uv run pytest`
Expected: the whole default suite is green. `prompt.py` only gained a binding, so every existing prompt and engine test is unaffected.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/prompt.py src/agent_manager/workflow/phases.py tests/workflow/test_phases.py
git commit -m "feat(workflow): validate declared phases and expose prompt.INPUT_NAMES"
```

---

### Task 3: `Workflow.digest()`

**Files:**
- Modify: `src/agent_manager/workflow/phases.py` (add `import hashlib`, the `_qual` helper, and the `digest()` method)
- Test: `tests/workflow/test_phases.py` (append)

**Interfaces:**
- Consumes: from Task 1, `Workflow`, `Step`, `AgentPhase`, `Goto` and `Retry`.
- Produces: `Workflow.digest() -> str` (a 64-character lowercase sha256 hex string) and the module-private `_qual(fn: object) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/workflow/test_phases.py`:

```python
def test_digest_is_stable_and_sensitive():
    base = wf(Step("a", step_fn, gates=(gate_ok,)), agent("b"))
    assert base.digest() == wf(Step("a", step_fn, gates=(gate_ok,)), agent("b")).digest()
    for changed in (
        wf(agent("b"), Step("a", step_fn, gates=(gate_ok,))),              # reorder
        wf(Step("a2", step_fn, gates=(gate_ok,)), agent("b")),             # rename
        wf(Step("a", step_fn, gates=(other_gate,)), agent("b")),           # gate swap
        wf(Step("a", step_fn, gates=(gate_ok,)), agent("b", retry=Retry(2, ("gate_failed",)))),
        # Review Focus 5: the rest of the spec's must-change list.
        wf(Step("a", step_fn, gates=(gate_ok,)), agent("b", timeout=timedelta(minutes=31))),
        wf(Step("a", step_fn, gates=(gate_ok,)), agent("b", role="planner")),
        wf(Step("a", step_fn, gates=(gate_ok,)), agent("b", inputs=("card", "branch"))),
    ):
        assert changed.digest() != base.digest()


def test_digest_is_a_sha256_hex_string():
    digest = wf(Step("a", step_fn)).digest()
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_digest_changes_when_skip_to_or_on_fail_is_retargeted():
    def skipping(target):
        return wf(Step("a", step_fn, when=gate_ok, skip_to=target), Step("b", step_fn), Step("c", step_fn))

    assert skipping("b").digest() != skipping("c").digest()

    def looping(target):
        return wf(agent("a"), agent("b"), agent("c", on_fail=Goto(target)))

    assert looping("a").digest() != looping("b").digest()
    assert looping("a").digest() != wf(agent("a"), agent("b"), agent("c", on_fail=Goto("a", max_loops=2))).digest()


def test_digest_ignores_args_order():
    # Review Focus 5: args are sorted, so insertion order is not identity.
    first = wf(Step("a", step_fn, args={"x": 1, "y": 2}))
    second = wf(Step("a", step_fn, args={"y": 2, "x": 1}))
    assert first.digest() == second.digest()
    assert first.digest() != wf(Step("a", step_fn, args={"x": 1, "y": 3})).digest()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/workflow/test_phases.py -v -k digest`
Expected: 4 FAIL with `AttributeError: 'Workflow' object has no attribute 'digest'`.

- [ ] **Step 3a: Add the `hashlib` import**

In `src/agent_manager/workflow/phases.py`, replace:

```python
from __future__ import annotations

from dataclasses import dataclass, field
```

with:

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
```

- [ ] **Step 3b: Add the `_qual` helper**

In `src/agent_manager/workflow/phases.py`, replace:

```python
    on_fail: Goto | None = None


@dataclass(frozen=True)
class Workflow:
```

with:

```python
    on_fail: Goto | None = None


def _qual(fn: object) -> str:
    """A callable's stable identity for the digest: `module.qualname`."""
    return f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', repr(fn))}"


@dataclass(frozen=True)
class Workflow:
```

- [ ] **Step 3c: Add `digest()` to `Workflow`**

In `src/agent_manager/workflow/phases.py`, replace:

```python
                        raise WorkflowError(
                            f"Goto {p.on_fail.phase!r} must name an earlier phase", phase=p.name)
            seen.append(p.name)
```

with:

```python
                        raise WorkflowError(
                            f"Goto {p.on_fail.phase!r} must name an earlier phase", phase=p.name)
            seen.append(p.name)

    def digest(self) -> str:
        """sha256 over the name and one record per phase, in declared order.

        Fields are joined by `\\x1f` and each record ends with `\\x1e`, so no
        two different workflows can concatenate to the same bytes by shifting
        a value across a field boundary.
        """
        h = hashlib.sha256(self.name.encode())
        for p in self.phases:
            if isinstance(p, Step):
                parts = ["step", p.name, _qual(p.run), repr(sorted(p.args.items())),
                         *map(_qual, p.gates), str(p.best_effort),
                         _qual(p.when) if p.when else "-", p.skip_to or "-"]
            else:
                parts = ["agent", p.name, p.role, ",".join(p.inputs),
                         _qual(p.result) if p.result else "-", *map(_qual, p.gates),
                         repr(p.retry), p.writes or "-", str(p.timeout), repr(p.on_fail)]
            h.update("\x1f".join(parts).encode() + b"\x1e")
        return h.hexdigest()
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run pytest tests/workflow/test_phases.py -v`
Expected: every test in the file passes.

Then run: `uv run pytest`
Expected: the whole default suite is green.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/workflow/phases.py tests/workflow/test_phases.py
git commit -m "feat(workflow): add a stable, order-sensitive Workflow.digest()"
```
