# The pygents engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **In this repo** every task below is one `brd` subtask card, driven by the `task` workflow (`/task <card>` or `am run --milestone`), which writes its own per-subtask spec and TDD plan from the card and from this file. Task numbers here are `<story>.<subtask>`; each card's description names its Task. Milestone card: `84c3b532-50dd-4443-a0bf-5245cc19f847`.

**Goal:** Rebuild agent-manager's subtask engine on `pygents`, with checkpoint-based resume and critic revision loops, close the gaps between the `task.yaml` port and `leave-me-alone`'s `task.js`, then make it the only engine.

**Architecture:** A workflow is declared Python data (`workflow/phases.py`), compiled into two generic pygents tools (`runtime/compile.py`). One pygents `Agent` per subtask runs on one event loop, and all blocking work goes through `asyncio.to_thread` (`runtime/bridge.py`). The agent is checkpointed to SQLite at every `BEFORE_TURN`, and resume restores it. The old YAML engine runs side by side behind `--engine` until parity, then is deleted.

**Tech Stack:** Python 3.12, `pygents>=0.6.7`, Typer, Pydantic 2, SQLite (stdlib), pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (decisions G1–G10). Read it before any task.

## Global Constraints

- `pygents>=0.6.7` is the only new runtime dependency; nothing else is added.
- Only `src/agent_manager/runtime/` imports `pygents`. `workflow/phases.py` must not.
- The CLI's output envelope is unchanged: `{"ok": true, "data": ...}`, `--pretty` to indent.
- `SubtaskSummary`, the `phases`/`attempts` rows, the journal lines and every escalation payload are unchanged (G10).
- `--engine` defaults to `yaml` until Task 5.3 removes it.
- Loops: `validate_spec → spec` and `validate_plan → plan`, `max_loops=1`; review never loops (G4).
- Every `AgentPhase.timeout` is strictly greater than the runner's launcher timeout (G2).
- Checkpoints are written only at `BEFORE_TURN` and when `run()` ends; never at `AFTER_TURN` (spec §2).
- Never `break`/`return` out of `agent.run()`; always consume it to the end (spec §2).
- pygents hooks are module-level functions registered with `@hook(..., tags={"subtask"})`; never closures.
- Verification for every task: `uv run pytest` (whole suite green — each task lands green alone).
- Milestone branch prefix: `m6`. Base: `master` with milestone 5 (Integrate) merged.

## Review Focus

1. **Ctrl-C or a cancelled turn during `claude -p`.** A person expects no orphaned `claude` process. `to_thread` cannot be cancelled, so the bridge must kill the launched process tree itself. Test in Task 3.3.
2. **Two lanes compiling the same workflow at once.** `orchestrate.py` runs lanes on threads, and `ToolRegistry` refuses duplicate names. The compile cache must be locked, or the second lane crashes with `ValueError`. Test in Task 3.3.
3. **Results holding `Path` or `datetime` values.** `json.dumps` would fail at checkpoint time, deep into a paid run. The pool codec must round-trip them. Test in Task 3.1.
4. **Resuming in a process that already ran that subtask** (the tests, a relaunch in the same process). `Agent.from_dict` registers the agent's name, so a stale registration would crash. Test in Task 4.3.
5. **A `subtask`-tagged hook firing with no run in context** (an agent built outside the engine, as some tests do). The hook must do nothing rather than crash. Test in Task 4.2.

---

## File map

| File | Responsibility | Tasks |
|---|---|---|
| `src/agent_manager/workflow/phases.py` | phase dataclasses, `validate()`, `digest()`, `from_loader()` | 1.1, 1.2 |
| `src/agent_manager/workflow/task.py` | `TASK` | 1.3, 2.x, 5.1 |
| `src/agent_manager/workflow/integrate.py` | `INTEGRATE` | 1.3 |
| `src/agent_manager/steps/reducers.py` | + `review_blockers_gate`, `plan_hash_gate_adapter` (moved) | 1.2, 2.1 |
| `src/agent_manager/roles/bundles/{reviewer,spec_critic,plan_critic}/` | role prompts | 2.2, 2.3 |
| `src/agent_manager/steps/verify.py` | + typecheck and lint | 2.4 |
| `src/agent_manager/runtime/context.py` | pool codec, seed, binding table | 3.1 |
| `src/agent_manager/dispatch.py`, `prompt.py` | accept phase-model objects; `feedback` resolver | 3.2, 3.5 |
| `src/agent_manager/harness/launcher.py` | optional `on_spawn` callback | 3.3 |
| `src/agent_manager/runtime/state.py` | `RunDeps` + `current_run` context var | 3.3 |
| `src/agent_manager/runtime/bridge.py` | `to_thread` calls; kill-on-cancel | 3.3 |
| `src/agent_manager/runtime/compile.py` | `agent_phase`, `step_phase`, `Escalated`, cache | 3.3, 3.5 |
| `src/agent_manager/runtime/engine.py` | `run_subtask()` | 3.4, 4.3 |
| `src/agent_manager/store.py` | `checkpoints` table | 4.1 |
| `src/agent_manager/runtime/checkpoint.py` | hooks: checkpoint + stop bridge | 4.2 |
| `src/agent_manager/cli.py`, `orchestrate.py`, `integration.py` | `--engine`, resume | 4.4, 4.5, 5.3 |

---

## Story 1 — The declared phase model

### Task 1.1: Add the phase model with `validate()` and `digest()`

**Files:**
- Create: `src/agent_manager/workflow/phases.py`
- Modify: `src/agent_manager/prompt.py` (add the public `INPUT_NAMES`)
- Test: `tests/workflow/test_phases.py`

**Interfaces:**
- Produces: `Goto(phase: str, max_loops: int = 1)`, `Retry(max_attempts: int, on: tuple[str, ...])`, `Step(...)`, `AgentPhase(...)`, `Workflow(name, phases)` with `.phase(name)`, `.phase_names`, `.validate(*, launcher_timeout: timedelta, role_root: Path | None = None) -> None`, `.digest() -> str`; `WorkflowError(ValueError)` with `.phase: str | None`; `prompt.INPUT_NAMES: frozenset[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/workflow/test_phases.py
from datetime import timedelta
import pytest
from agent_manager.workflow.phases import AgentPhase, Goto, Retry, Step, Workflow, WorkflowError

LAUNCHER = timedelta(minutes=10)

def step_fn(worktree): return {"ok": True}
def gate_ok(result): return None
def other_gate(result): return None

def wf(*phases): return Workflow("t", tuple(phases))
def agent(name, **kw):
    kw.setdefault("role", "explorer"); kw.setdefault("inputs", ("card",)); kw.setdefault("result", None)
    return AgentPhase(name, **kw)

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
])
def test_validate_refuses(phases, needle):
    with pytest.raises(WorkflowError, match=needle):
        wf(*phases).validate(launcher_timeout=LAUNCHER)

def test_an_earlier_phase_name_is_a_valid_input():
    wf(agent("explore"), agent("spec", inputs=("explore",))).validate(launcher_timeout=LAUNCHER)

def test_digest_is_stable_and_sensitive():
    base = wf(Step("a", step_fn, gates=(gate_ok,)), agent("b"))
    assert base.digest() == wf(Step("a", step_fn, gates=(gate_ok,)), agent("b")).digest()
    for changed in (
        wf(agent("b"), Step("a", step_fn, gates=(gate_ok,))),              # reorder
        wf(Step("a2", step_fn, gates=(gate_ok,)), agent("b")),             # rename
        wf(Step("a", step_fn, gates=(other_gate,)), agent("b")),           # gate swap
        wf(Step("a", step_fn, gates=(gate_ok,)), agent("b", retry=Retry(2, ("gate_failed",)))),
    ):
        assert changed.digest() != base.digest()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/workflow/test_phases.py -v`
Expected: FAIL, `ModuleNotFoundError: agent_manager.workflow.phases`

- [ ] **Step 3: Implement**

In `prompt.py`, directly under the resolver table `_TABLE`:

```python
INPUT_NAMES: frozenset[str] = frozenset(_TABLE)
"""Every input name a phase may declare that a resolver provides (phases.validate)."""
```

`src/agent_manager/workflow/phases.py`:

```python
"""The workflow as declared Python data (spec G3). No pygents import here."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import BaseModel


class WorkflowError(ValueError):
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


def _qual(fn: object) -> str:
    return f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', repr(fn))}"


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

    def validate(self, *, launcher_timeout: timedelta, role_root: Path | None = None) -> None:
        from agent_manager import prompt
        from agent_manager.roles.loader import load_role

        seen: list[str] = []
        order = {}
        for index, p in enumerate(self.phases):
            if p.name in order:
                raise WorkflowError("duplicate phase name", phase=p.name)
            order[p.name] = index
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
                        raise WorkflowError(f"input {name!r} has no resolver and no earlier phase", phase=p.name)
                if p.timeout <= launcher_timeout:
                    raise WorkflowError(
                        f"timeout {p.timeout} must exceed the launcher timeout {launcher_timeout}", phase=p.name)
                if p.on_fail is not None:
                    if p.on_fail.max_loops < 1:
                        raise WorkflowError("Goto max_loops must be at least 1", phase=p.name)
                    if order.get(p.on_fail.phase, index) >= index:
                        raise WorkflowError(f"Goto {p.on_fail.phase!r} must name an earlier phase", phase=p.name)
            seen.append(p.name)

    def digest(self) -> str:
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

Run: `uv run pytest tests/workflow/test_phases.py -v` → all PASS. Then `uv run pytest` → the whole suite is green.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/workflow/phases.py src/agent_manager/prompt.py tests/workflow/test_phases.py
git commit -m "feat(workflow): add the declared phase model with validate and digest"
```

### Task 1.2: Convert a loaded YAML workflow into the phase model

**Files:**
- Modify: `src/agent_manager/workflow/phases.py` (add `from_loader`)
- Modify: `src/agent_manager/steps/reducers.py` (receive `plan_hash_gate_adapter` and `_plan_hash_of`)
- Modify: `src/agent_manager/workflow/registry.py` (import them from reducers instead of defining them)
- Test: `tests/workflow/test_phases.py`, `tests/steps/test_reducers.py`

**Interfaces:**
- Consumes: `loader.Workflow` (`.phases`, `.function(name)`), `results.RESULT_MODELS`.
- Produces: `phases.from_loader(loaded: loader.Workflow, *, timeout: timedelta = timedelta(minutes=30)) -> Workflow`; `reducers.plan_hash_gate_adapter(implement=None, review=None)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/workflow/test_phases.py (append)
from agent_manager.steps import reducers
from agent_manager.workflow.loader import load_builtin
from agent_manager.workflow.phases import from_loader
from agent_manager.workflow.registry import default_registry
from agent_manager import results

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
    # the engine tests build workflows from fake registries; the conversion must carry the fakes
    loaded = load_builtin("integrate", default_registry())
    assert from_loader(loaded).phase("verify").run is loaded.function("verify.run_suite")
```

```python
# tests/steps/test_reducers.py (append)
from agent_manager.steps.reducers import plan_hash_gate_adapter

def test_plan_hash_gate_adapter_compares_the_two_results():
    assert plan_hash_gate_adapter({"plan_hash": "aaaaaaaa"}, {"plan_hash": "aaaaaaaa"}) is None
    assert plan_hash_gate_adapter({"plan_hash": "aaaaaaaa"}, {"plan_hash": "bbbbbbbb"}) is not None
    assert plan_hash_gate_adapter(None, None) is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/workflow/test_phases.py tests/steps/test_reducers.py -v`
Expected: FAIL, `ImportError: cannot import name 'from_loader'` / `'plan_hash_gate_adapter'`

- [ ] **Step 3: Implement**

Move `_plan_hash_of` and `plan_hash_gate_adapter`, together with their docstrings, verbatim from `workflow/registry.py:174-228` to the end of `steps/reducers.py`. In `registry.py`, replace them with `from agent_manager.steps.reducers import plan_hash_gate_adapter` and keep registering it under `"plan_hash_gate"`.

Append to `phases.py`:

```python
def from_loader(loaded: Any, *, timeout: timedelta = timedelta(minutes=30)) -> Workflow:
    """A `workflow.loader.Workflow` as phase-model data. Side by side only (G7)."""
    from agent_manager import results
    from agent_manager.workflow.loader import DeterministicPhase

    out: list[Step | AgentPhase] = []
    for p in loaded.phases:
        gates = tuple(loaded.function(name) for name in p.gates)
        if isinstance(p, DeterministicPhase):
            out.append(Step(p.name, loaded.function(p.run), dict(p.args or {}), gates,
                            p.best_effort, loaded.function(p.when) if p.when else None, p.skip_to))
        else:
            out.append(AgentPhase(
                p.name, p.role, tuple(p.inputs),
                results.RESULT_MODELS[p.result] if p.result else None, gates,
                Retry(p.retry.max_attempts, tuple(p.retry.on)) if p.retry else None,
                p.writes, timeout))
    return Workflow(loaded.name, tuple(out))
```

Check the attribute names against `workflow/loader.py`'s `DeterministicPhase`/`AgentPhase`/`RetryPolicy` before running. If `args`, `when` or `skip_to` are spelled differently there, use the loader's spelling.

- [ ] **Step 4: Run it and watch it pass:** `uv run pytest` → green.

- [ ] **Step 5: Commit**

```bash
git add -A src/agent_manager tests
git commit -m "feat(workflow): convert loaded YAML workflows into the phase model"
```

### Task 1.3: Declare `TASK` and `INTEGRATE`, pinned to the shipped YAML

**Files:**
- Create: `src/agent_manager/workflow/task.py`, `src/agent_manager/workflow/integrate.py`
- Test: `tests/workflow/test_declared.py`

**Interfaces:**
- Consumes: `phases.*`, `from_loader`, the real step and gate functions.
- Produces: `workflow.task.TASK: Workflow`, `workflow.integrate.INTEGRATE: Workflow`, `workflow.task.LAUNCHER_TIMEOUT: timedelta` (equal to `dispatch.DEFAULT_TIMEOUT` seconds).

- [ ] **Step 1: Write the failing tests**

```python
# tests/workflow/test_declared.py
from agent_manager.workflow.loader import load_builtin
from agent_manager.workflow.phases import from_loader
from agent_manager.workflow.registry import default_registry
from agent_manager.workflow.task import TASK, LAUNCHER_TIMEOUT
from agent_manager.workflow.integrate import INTEGRATE

def _shipped(name, like):
    converted = from_loader(load_builtin(name, default_registry()))
    # timeouts are new data the YAML never had: compare with TASK's own
    return type(converted)(converted.name, tuple(
        p if not hasattr(p, "timeout") else type(p)(**{**p.__dict__, "timeout": like.phase(p.name).timeout})
        for p in converted.phases))

def test_task_equals_the_shipped_yaml():
    assert TASK.digest() == _shipped("task", TASK).digest()

def test_integrate_equals_the_shipped_yaml():
    assert INTEGRATE.digest() == _shipped("integrate", INTEGRATE).digest()

def test_both_validate():
    TASK.validate(launcher_timeout=LAUNCHER_TIMEOUT)
    INTEGRATE.validate(launcher_timeout=LAUNCHER_TIMEOUT)
```

- [ ] **Step 2: Run it and watch it fail** (`ModuleNotFoundError: agent_manager.workflow.task`).

- [ ] **Step 3: Implement.** Write `TASK` phase by phase from `builtin/task.yaml`, in the same order, with the same roles, inputs, `result` classes, gates (as function objects: `reducers.exploration_output_gate`, `reducers.verification_gate`, `reducers.critic_blockers_gate`, `reducers.implement_blocked_gate`, `reducers.review_gate`, `reducers.plan_hash_gate_adapter`, `reducers.verification_passed_gate`), steps (`worktree.ensure`, `rollup.set_status` with `args={"status": "in_progress"}` / `{"status": "done"}`, `plan_check.find_validated_plan`, `plan_check.has_validated_plan`, `plan_check.mark_validated`, `docs_commit.commit_documents`, `verify.run_suite`), `writes`, and retry. Timeouts: `explore` 20 min, `spec`/`plan` 30 min, `validate_*` 20 min, `implement` 90 min, `review` 45 min. `LAUNCHER_TIMEOUT = timedelta(seconds=dispatch.DEFAULT_TIMEOUT)`; if any chosen timeout is not above it, raise that phase's timeout rather than lowering the launcher's. `INTEGRATE` is written the same way from `builtin/integrate.yaml` (`resolve`: role `resolver`, `results.ResolveResult`, `reducers.merge_completed_gate` (or wherever the milestone 5 code defines it), `Retry(2, ("gate_failed", "schema_invalid"))`, 30 min; then `verify`).

- [ ] **Step 4: Run it and watch it pass:** `uv run pytest` → green.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/workflow/task.py src/agent_manager/workflow/integrate.py tests/workflow/test_declared.py
git commit -m "feat(workflow): declare TASK and INTEGRATE, pinned to the shipped YAML"
```

---

## Story 2 — Close the task.js gaps (on both engines)

Each task in this story edits `builtin/task.yaml` **and** `workflow/task.py` identically, so `test_declared.py` stays green and both engines get the fix.

### Task 2.1: Stop on unresolved review blockers

**Files:**
- Modify: `src/agent_manager/steps/reducers.py`, `src/agent_manager/workflow/registry.py`, `src/agent_manager/workflow/builtin/task.yaml`, `src/agent_manager/workflow/task.py`
- Test: `tests/steps/test_reducers.py`, `tests/test_engine.py`

**Interfaces:**
- Produces: `reducers.review_blockers_gate(result: object) -> dict[str, str] | None`, registered as `"review_blockers_gate"` and listed **first** in `review`'s gates.

- [ ] **Step 1: Write the failing tests**

```python
# tests/steps/test_reducers.py (append)
from agent_manager.steps.reducers import review_blockers_gate

def test_no_unresolved_blockers_passes():
    assert review_blockers_gate({"unresolved_blockers": []}) is None

def test_unresolved_blockers_block_review():
    verdict = review_blockers_gate({"unresolved_blockers": ["tests assert the mock", "no error path"]})
    assert verdict == {"blocked": "review",
                       "detail": "review left 2 unresolved blocker(s): tests assert the mock; no error path"}

def test_a_dead_reviewer_blocks():
    assert review_blockers_gate(None) == {"blocked": "review", "detail": "the review stage returned nothing"}
```

In `tests/test_engine.py`, add one walk of the shipped `task` workflow (use the existing `_builtin_functions` helper) where the fake reviewer returns `unresolved_blockers=["x"]`. Assert `summary.status == "escalated"`, `summary.failed_phase == "review"`, and that `verify` never ran.

- [ ] **Step 2: Run them and watch them fail.**

- [ ] **Step 3: Implement**

```python
def review_blockers_gate(result: object) -> dict[str, str] | None:
    """task.js:842-855: only what the reviewer says is STILL standing gates done."""
    if not isinstance(result, Mapping):
        return {"blocked": "review", "detail": "the review stage returned nothing"}
    blockers = list(_field(result, "unresolved_blockers") or [])
    if not blockers:
        return None
    return {"blocked": "review",
            "detail": f"review left {len(blockers)} unresolved blocker(s): {'; '.join(map(str, blockers))}"}
```

Register it in `default_registry()`, and add it to `BUILTIN_FUNCTION_NAMES` if that tuple is checked. Change `review`'s gates in `task.yaml` to `[review_blockers_gate, review_gate, plan_hash_gate]`, and make the same change in `TASK`.

- [ ] **Step 4: Run it and watch it pass:** `uv run pytest` → green.

- [ ] **Step 5: Commit** — `git commit -m "feat(review): stop on unresolved review blockers"`

### Task 2.2: Rewrite the reviewer role from task.js's Review stage

**Files:**
- Modify: `src/agent_manager/roles/bundles/reviewer/system.md`, `src/agent_manager/roles/bundles/reviewer/policy.toml`
- Test: `tests/roles/test_reviewer_brief.py`

**Interfaces:**
- Consumes: `roles.loader.load_role`, `prompt.compose_brief`, `results.ReviewResult` (unchanged).

- [ ] **Step 1: Write the failing test**

```python
# tests/roles/test_reviewer_brief.py
from agent_manager.roles.loader import load_role

def test_reviewer_brief_carries_the_task_js_contract():
    role = load_role("reviewer")
    text = role.system
    for needle in (
        "git status --porcelain",
        "git rev-list --count",
        'grep -c "^Plan-Hash: $PLAN_HASH"',
        "Co-Authored-By:",
        "Plan-Hash: $PLAN_HASH",
        "sha256sum",
        "unresolved_blockers",
        "Never weaken, skip, xfail, or delete a test",
        "report their output verbatim",
    ):
        assert needle in text, needle
    assert {"Edit", "Write"} <= set(role.policy.allowed_tools)
```

Check the `RoleBundle` attribute names (`system`, `policy.allowed_tools`) in `roles/loader.py` and use them.

- [ ] **Step 2: Run it and watch it fail.**

- [ ] **Step 3: Implement.** Rewrite `system.md` from `task.js`'s Review prompt (`leave-me-alone/plugins/leave-me-alone/workflows/task.js:790-826`), adapted to agent-manager as follows:
  - the worktree is the cwd, so no `-C`;
  - `<base>` and `<plan>` refer to the brief's `base_branch` and `plan_path` inputs;
  - the result is written to the result file with snake_case field names (`findings`, `unresolved_blockers`, `fix_summary`, `porcelain`, `commit_count`, `tagged_count`, `plan_hash`);
  - the trailers are `Co-Authored-By: Claude <noreply@anthropic.com>` and `Plan-Hash: $PLAN_HASH`.

  Keep all of `task.js`'s instructions:
  - read the full diff against the plan and spec;
  - apply the test-integrity gate;
  - fix confirmed findings with TDD and commit them granularly;
  - run lint and format and commit their fixes;
  - skip wrong findings and note why in `fix_summary`;
  - list in `unresolved_blockers` only the blockers still standing;
  - finally, run the three commands and report their output verbatim without acting on it.

  In `policy.toml`, set `allowed_tools = ["Read", "Grep", "Glob", "Bash", "Edit", "Write"]` and leave `max_attempts` and the model unchanged.

- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(roles): the reviewer fixes, commits and reports task.js's three git facts"`

### Task 2.3: Split the critic into `spec_critic` and `plan_critic`

**Files:**
- Create: `src/agent_manager/roles/bundles/spec_critic/{system.md,policy.toml,VENDORED.lock}`, `src/agent_manager/roles/bundles/plan_critic/{system.md,policy.toml,VENDORED.lock}`
- Delete: `src/agent_manager/roles/bundles/critic/`
- Modify: `builtin/task.yaml`, `workflow/task.py` (`validate_spec.role = "spec_critic"`, `validate_plan.role = "plan_critic"`)
- Test: `tests/roles/test_critic_briefs.py`; update any test naming role `critic`.

- [ ] **Step 1: Write the failing test**

```python
# tests/roles/test_critic_briefs.py
import pytest
from agent_manager.roles.loader import load_role

@pytest.mark.parametrize("role, criteria", [
    ("spec_critic", ["completeness", "consistency", "clarity", "scope", "YAGNI", "sibling subtasks"]),
    ("plan_critic", ["completeness", "spec alignment", "decomposition", "buildability",
                     "traces back to the SPEC"]),
])
def test_critic_brief(role, criteria):
    text = load_role(role).system
    for needle in criteria + ["Fold every CONFIRMED fix", "blockers=true only", "Verify every suspicion"]:
        assert needle in text, needle

def test_the_generic_critic_is_gone():
    with pytest.raises(Exception):
        load_role("critic")
```

- [ ] **Step 2: Run it and watch it fail.**
- [ ] **Step 3: Implement.** Write `spec_critic/system.md` from `task.js:614-629` and `plan_critic/system.md` from `task.js:700-715`, keeping each prompt's criteria, calibration paragraph and "fold fixes" instruction. For `plan_critic`, leave out the `<!-- task-pipeline: validated -->` line: `mark_validated` writes that marker. Copy `critic/policy.toml` into both bundles and delete `critic/`. Then run `grep -rn '"critic"\|role: critic' src tests` and update every hit.
- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(roles): split the critic into spec_critic and plan_critic"`

### Task 2.4: Verify also runs Explore's typecheck and lint

**Files:**
- Modify: `src/agent_manager/steps/verify.py`
- Test: `tests/steps/test_verify.py`

**Interfaces:**
- Produces: `verify.run_suite(commands, worktree, explore=None, *, runner=run_command)`. When `explore` is a mapping with `verification.typecheck` (non-empty) and/or `verification.lint`, those commands run after `commands`, in that order. The engine binds `explore` by parameter name from the context, so no workflow edit is needed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/steps/test_verify.py (append)
def test_typecheck_and_lint_run_after_the_suite(tmp_path):
    ran = []
    def runner(command, cwd):
        ran.append(command)
        return _ok(command)          # reuse this file's existing fake-result helper
    explore = {"verification": {"fullSuite": ["uv run pytest"], "typecheck": "uv run mypy",
                                 "lint": ["uv run ruff check"]}}
    run_suite(["uv run pytest"], tmp_path, explore, runner=runner)
    assert ran == ["uv run pytest", "uv run mypy", "uv run ruff check"]

def test_no_explore_means_only_the_suite(tmp_path):
    ran = []
    run_suite(["uv run pytest"], tmp_path, runner=lambda c, cwd: (ran.append(c), _ok(c))[1])
    assert ran == ["uv run pytest"]

def test_an_empty_typecheck_is_skipped(tmp_path):
    ran = []
    explore = {"verification": {"typecheck": "", "lint": []}}
    run_suite(["x"], tmp_path, explore, runner=lambda c, cwd: (ran.append(c), _ok(c))[1])
    assert ran == ["x"]
```

Match `_ok` and the runner's call signature to what `test_verify.py` already uses.

- [ ] **Step 2: Watch them fail.**
- [ ] **Step 3: Implement.** Add the `explore: object = None` parameter. Read `verification` with the same `_field` style used elsewhere, accepting both `typecheck` and `lint` as they come from `ExploreResult`'s dump, and append the extra commands before running. A failing extra command fails the suite exactly as a failing `--verify` command does.
- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(verify): also run Explore's typecheck and lint"`

---

## Story 3 — Run a workflow on pygents

### Task 3.1: Add pygents and the pool codec

**Files:**
- Modify: `pyproject.toml` (`uv add "pygents>=0.6.7"`)
- Create: `src/agent_manager/runtime/__init__.py`, `src/agent_manager/runtime/context.py`
- Test: `tests/runtime/__init__.py`, `tests/runtime/test_context.py`

**Interfaces:**
- Produces:
  - `context.encode(value: Any) -> Any` (JSON-safe; tags `Path` as `{"$path": str}`, `datetime` as `{"$dt": iso}`, and pydantic models as `{"$model": "<module>:<qualname>", "data": model_dump(mode="json")}`);
  - `context.decode(value: Any) -> Any`;
  - `context.SUBTASK = "subtask"`, `context.SKIPPED = "skipped"`;
  - `context.seed_item(binding: Mapping[str, Any]) -> ContextItem`;
  - `context.binding_table(pool: ContextPool, memory: ContextQueue, phase: str) -> dict[str, Any]`, which returns the seed, plus every phase result by phase name, plus `feedback: list[dict]` (the queue items whose `for == phase`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/test_context.py
import json
from datetime import datetime, timezone
from pathlib import Path
from pygents import ContextPool, ContextQueue
from pygents.context import ContextItem
from agent_manager import models, results
from agent_manager.runtime import context

def test_round_trip_through_json():
    value = {"worktree": Path("/r/.claude/worktrees/m6/x"),
             "at": datetime(2026, 9, 25, tzinfo=timezone.utc),
             "explore": results.ExploreResult(refused=False, reason=None, summary="s" * 80,
                 verification=results.Verification(full_suite=["uv run pytest"], typecheck="", lint=[]))}
    back = context.decode(json.loads(json.dumps(context.encode(value))))
    assert back == value

async def test_binding_table_rebuilds_seed_results_and_feedback():
    pool, memory = ContextPool(), ContextQueue(limit=10)
    await pool.add(context.seed_item({"branch": "m6/task-x-1234abcd", "worktree": Path("/w")}))
    await pool.add(ContextItem(id="spec", description="spec result",
                               content=context.encode({"path": "docs/s.md", "note": None})))
    await memory.append(ContextItem(content={"for": "spec", "from": "validate_spec", "detail": "no error path"}))
    await memory.append(ContextItem(content={"for": "plan", "from": "validate_plan", "detail": "other"}))
    table = context.binding_table(pool, memory, "spec")
    assert table["worktree"] == Path("/w")
    assert table["spec"] == {"path": "docs/s.md", "note": None}
    assert table["feedback"] == [{"for": "spec", "from": "validate_spec", "detail": "no error path"}]
```

Add `asyncio_mode = "auto"` under `[tool.pytest.ini_options]` in `pyproject.toml`, together with `pytest-asyncio` as a dev dependency (`uv add --dev pytest-asyncio`). If the repo already has an async test convention, use that instead.

- [ ] **Step 2: Watch it fail.**
- [ ] **Step 3: Implement**

```python
"""The pool holds JSON only (spec §2); this is the one codec between it and the engine."""
from __future__ import annotations
import importlib
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any, Mapping
from pydantic import BaseModel
from pygents import ContextPool, ContextQueue
from pygents.context import ContextItem

SUBTASK = "subtask"
SKIPPED = "skipped"

def encode(value: Any) -> Any:
    if isinstance(value, BaseModel):
        cls = type(value)
        return {"$model": f"{cls.__module__}:{cls.__qualname__}", "data": value.model_dump(mode="json")}
    if isinstance(value, PurePath):
        return {"$path": str(value)}
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value

def decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "$model" in value:
            module, qualname = value["$model"].split(":")
            cls = importlib.import_module(module)
            for part in qualname.split("."):
                cls = getattr(cls, part)
            return cls.model_validate(value["data"])
        if "$path" in value:
            return Path(value["$path"])
        if "$dt" in value:
            return datetime.fromisoformat(value["$dt"])
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value

def seed_item(binding: Mapping[str, Any]) -> ContextItem:
    return ContextItem(id=SUBTASK, description="fixed subtask context", content=encode(dict(binding)))

def binding_table(pool: ContextPool, memory: ContextQueue, phase: str) -> dict[str, Any]:
    table: dict[str, Any] = dict(decode(pool.get(SUBTASK).content))
    for item in pool.items:
        if item.id not in (SUBTASK, SKIPPED):
            table[item.id] = decode(item.content)
    table["feedback"] = [dict(i.content) for i in memory.items
                         if isinstance(i.content, Mapping) and i.content.get("for") == phase]
    return table
```

Note: `ExploreResult`'s `Verification` uses a `serialization_alias` (`fullSuite`), so `model_dump(mode="json")` without `by_alias` keeps the field names and round-trips through `model_validate`. Gates that need aliases get them from `dispatch.gate_values`, as today.

- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(runtime): add pygents and the pool codec"`

### Task 3.2: Let `dispatch` and `prompt` take phase-model objects

**Files:**
- Modify: `src/agent_manager/dispatch.py` (`evaluate_gates`, the `result` resolution in `AgentRunner.__call__`, the retry read), `src/agent_manager/prompt.py` (`render_prompt` type hint → a `Protocol`)
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Produces: `dispatch.evaluate_gates(phase, workflow, values, warnings)`. A gate entry may be a `str` (resolved through `workflow.function`, as now) or a callable (used as is, named by `__name__` in messages). `AgentRunner` accepts `phase.result` as a `str` (looked up in `result_models`) or as a `type[BaseModel]`. `phase.retry` may be a `RetryPolicy` or a `phases.Retry`: both expose `max_attempts` and `on`.

- [ ] **Step 1: Write the failing tests** (follow the existing `tests/test_dispatch.py` fixtures for a fake launcher and store):
  - `test_callable_gate_is_called_directly`: an `AgentPhase` (phase model) whose `gates=(lambda result: {"blocked": "x", "detail": "d"},)`; the runner returns a verdict of `gate_failed` with detail containing `d`, and `workflow.function` is never called (pass a workflow whose `function` raises).
  - `test_result_class_is_used_directly`: `phases.AgentPhase(..., result=results.CriticResult)`; a valid result file validates against it without consulting `result_models`.
  - `test_phase_model_retry_is_honoured`: `retry=phases.Retry(2, ("schema_invalid",))` with an invalid first result makes two attempts.
- [ ] **Step 2: Watch them fail.**
- [ ] **Step 3: Implement.** In `evaluate_gates`, change `for name in phase.gates: gate = workflow.function(name)` to:

```python
    for entry in phase.gates:
        gate = entry if callable(entry) else workflow.function(entry)
        name = entry if isinstance(entry, str) else getattr(entry, "__name__", repr(entry))
```

In `AgentRunner.__call__`, change the model resolution to `phase.result if isinstance(phase.result, type) else results.resolve_result_model(...)`. In `prompt.py`, type `render_prompt`'s `phase` as a `Protocol` with `name`, `role` and `inputs`.
- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "refactor(dispatch): accept phase-model gates and result classes"`

### Task 3.3: Compile a workflow into `agent_phase` and `step_phase`

**Files:**
- Modify: `src/agent_manager/harness/launcher.py` (optional `on_spawn`)
- Create: `src/agent_manager/runtime/state.py`, `src/agent_manager/runtime/bridge.py`, `src/agent_manager/runtime/compile.py`
- Test: `tests/runtime/conftest.py`, `tests/runtime/test_compile.py`, `tests/runtime/test_bridge.py`, `tests/harness/test_launcher.py`

**Interfaces:**
- Produces:
  - `launcher.run_direct(argv, *, cwd, timeout, stdout_path, on_spawn: Callable[[subprocess.Popen], None] | None = None)`, plus a public `launcher.kill_tree(process)` (the existing `_kill_tree`, renamed with an alias kept).
  - `state.RunDeps` (a dataclass holding `workflow`, `store`, `story_id`, `subtask`, `agent_runner`, `clock`, `should_stop`, `warnings: list[str]`, `skipped: list[str]`) and `state.current_run: ContextVar[RunDeps]`.
  - `bridge.call_agent(runner, phase, context, rendered)`, which awaits `to_thread` and, on cancellation, kills any process spawned by that call.
  - `bridge.call_step(fn, kwargs)`.
  - `compile.Escalated(Exception)` with `.phase` and `.detail`.
  - `compile.compile_workflow(workflow) -> Compiled`, where `Compiled` has `.agent_phase`, `.step_phase` and `.first_turn() -> Turn`. Compilation is cached per `(name, digest)` behind a `threading.Lock`.
  - `compile.clear_cache()` (for tests).

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/conftest.py
import pytest
from pygents import AgentRegistry, ToolRegistry
from agent_manager.runtime import compile as compile_mod

@pytest.fixture(autouse=True)
def fresh_pygents():
    ToolRegistry.clear(); AgentRegistry.clear(); compile_mod.clear_cache()
    yield
    ToolRegistry.clear(); AgentRegistry.clear(); compile_mod.clear_cache()
```

```python
# tests/runtime/test_compile.py
import threading
from datetime import timedelta
import pytest
from pygents import Agent, ContextPool, ContextQueue
from agent_manager.runtime import compile as C, context, state
from agent_manager.workflow.phases import AgentPhase, Step, Workflow

def _deps(workflow, runner, calls):  # a RunDeps with a fake store that records phase rows
    ...  # build with the same fake Store helper tests/test_engine.py uses

async def _drive(workflow, deps):
    compiled = C.compile_workflow(workflow)
    agent = Agent("run:card", "t", [compiled.agent_phase, compiled.step_phase],
                  context_pool=ContextPool(), context_queue=ContextQueue(limit=10), tags=["subtask"])
    await agent.context_pool.add(context.seed_item({"worktree": "/w"}))
    token = state.current_run.set(deps)
    try:
        await agent.put(compiled.first_turn())
        async for _ in agent.run():
            pass
    finally:
        state.current_run.reset(token)
    return agent

async def test_linear_flow_stores_every_result():
    ran = []
    wf = Workflow("t", (Step("a", lambda worktree: ran.append("a") or {"a": 1}),
                        Step("b", lambda a: ran.append(("b", a)) or {"b": 2})))
    agent = await _drive(wf, _deps(wf, None, ran))
    assert ran == ["a", ("b", {"a": 1})]
    assert agent.context_pool.get("b").content == {"b": 2}

async def test_skip_to_records_skipped_phases(): ...     # when → True jumps; pool item "skipped" lists them
async def test_best_effort_failure_is_a_warning(): ...   # step raises; next phase still runs; deps.warnings has it
async def test_step_gate_verdict_escalates():            # gate returns {"blocked": ...}
    wf = Workflow("t", (Step("a", lambda: {}, gates=(lambda result: {"blocked": "x", "detail": "d"},)),))
    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, None, []))
    assert info.value.phase == "a" and "d" in info.value.detail

async def test_agent_phase_failure_escalates(): ...      # runner raises AgentPhaseFailed → Escalated(phase, detail)

def test_two_threads_compiling_at_once_share_one_compilation():
    wf = Workflow("t", (Step("a", lambda: {}),))
    out = []
    threads = [threading.Thread(target=lambda: out.append(C.compile_workflow(wf))) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len({id(c) for c in out}) == 1                # Review Focus 2
```

Fill in the three `...` tests in the same style as the two written out: one assertion block each, as the comments describe.

```python
# tests/runtime/test_bridge.py — Review Focus 1
import asyncio, subprocess, sys
from agent_manager.harness import launcher
from agent_manager.runtime import bridge

async def test_cancelling_a_call_kills_its_process(tmp_path):
    spawned = []
    def runner(phase, context, rendered):
        return launcher.run_direct([sys.executable, "-c", "import time; time.sleep(60)"],
                                   cwd=tmp_path, timeout=120, stdout_path=tmp_path / "out",
                                   on_spawn=bridge.current_spawn_hook())
    task = asyncio.create_task(bridge.call_agent(runner, None, {}, None))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.2)
    assert bridge.last_spawned_for_tests().poll() is not None   # the child is dead
```

`bridge.current_spawn_hook()` returns the `on_spawn` callback bound to the call in progress, which `AgentRunner`'s launcher call must pass through (see the implementation below). Keep `last_spawned_for_tests` a test-only accessor.

- [ ] **Step 2: Watch them fail.**
- [ ] **Step 3: Implement**

`launcher.run_direct` gets the extra keyword `on_spawn=None` and calls `on_spawn(process)` right after `Popen`. `_kill_tree` is exposed as `kill_tree`.

`runtime/state.py`:

```python
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

@dataclass
class RunDeps:
    workflow: Any
    store: Any
    story_id: str
    subtask: Any
    agent_runner: Callable[..., Any] | None
    clock: Callable[[], Any]
    should_stop: Callable[[], bool] | None = None
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

current_run: ContextVar[RunDeps] = ContextVar("current_run")
```

`runtime/bridge.py`: `call_agent` creates a per-call list and a `threading.local`-scoped `on_spawn` that appends to it. It then runs `runner(phase, context, rendered)` via `asyncio.to_thread`. On `asyncio.CancelledError` it calls `launcher.kill_tree(p)` for every recorded process and re-raises. `current_spawn_hook()` reads the thread-local. `AgentRunner` passes `on_spawn=bridge.current_spawn_hook()` to its launcher call only when the launcher accepts it, so fake launchers in existing tests keep working: check with `inspect.signature(self.launcher).parameters`.

`runtime/compile.py`:

```python
"""A phases.Workflow as two generic pygents tools (spec G3, §5)."""
from __future__ import annotations
import threading
from dataclasses import dataclass
from pygents import ContextPool, ContextQueue, Turn, tool
from pygents.context import ContextItem
from agent_manager import engine as old_engine, prompt
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime import bridge, context
from agent_manager.runtime.state import current_run
from agent_manager.workflow.phases import AgentPhase, Step, Workflow

class Escalated(Exception):
    def __init__(self, phase: str, detail: str | None) -> None:
        self.phase, self.detail = phase, detail
        super().__init__(f"{phase}: {detail}")

@dataclass(frozen=True)
class Compiled:
    workflow: Workflow
    agent_phase: object
    step_phase: object
    def turn_for(self, name: str, loop: int) -> Turn:
        p = self.workflow.phase(name)
        fn = self.agent_phase if isinstance(p, AgentPhase) else self.step_phase
        timeout = p.timeout.total_seconds() if isinstance(p, AgentPhase) else 3600
        return Turn(fn, kwargs={"phase": name, "loop": loop}, timeout=timeout)
    def first_turn(self) -> Turn:
        return self.turn_for(self.workflow.phases[0].name, 0)
    def after(self, name: str, loop: int) -> Turn | None:
        names = self.workflow.phase_names
        i = names.index(name)
        return self.turn_for(names[i + 1], loop) if i + 1 < len(names) else None

_CACHE: dict[tuple[str, str], Compiled] = {}
_LOCK = threading.Lock()

def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()

def compile_workflow(wf: Workflow) -> Compiled:
    key = (wf.name, wf.digest())
    with _LOCK:
        if key not in _CACHE:
            _CACHE[key] = _build(wf, suffix=key[1][:8])
        return _CACHE[key]

def _build(wf: Workflow, suffix: str) -> Compiled:
    holder: dict[str, Compiled] = {}

    async def agent_phase(phase: str, loop: int, pool: ContextPool, memory: ContextQueue):
        deps, p = current_run.get(), wf.phase(phase)
        table = context.binding_table(pool, memory, phase)
        rendered = prompt.render_prompt(p, table)
        try:
            result = await bridge.call_agent(deps.agent_runner, p, table, rendered)
        except AgentPhaseFailed as failure:
            if p.on_fail is not None and loop < p.on_fail.max_loops:      # Task 3.5 adds the test
                yield ContextItem(content={"for": p.on_fail.phase, "from": phase, "detail": failure.detail})
                yield holder["c"].turn_for(p.on_fail.phase, loop + 1)
                return
            raise Escalated(phase, failure.detail) from failure
        yield ContextItem(id=phase, description=f"{phase} result", content=context.encode(result))
        if (nxt := holder["c"].after(phase, loop)) is not None:
            yield nxt

    async def step_phase(phase: str, loop: int, pool: ContextPool):
        deps, p = current_run.get(), wf.phase(phase)
        # reuse the old engine's deterministic walk for one phase: binding, gates, best_effort, when/skip_to,
        # and the phase row -- the same function, so both engines judge a step identically
        table = context.binding_table(pool, ContextQueue(limit=1), phase)
        outcome = await bridge.call_step(old_engine.run_one_step, dict(
            phase=p, table=table, store=deps.store, story_id=deps.story_id,
            subtask=deps.subtask, clock=deps.clock))
        deps.warnings.extend(outcome.warnings)
        if not outcome.ok:
            if p.best_effort:
                deps.warnings.append(f"best-effort phase {phase!r} failed: {outcome.detail}")
            else:
                raise Escalated(phase, outcome.detail)
        else:
            yield ContextItem(id=phase, description=f"{phase} result", content=context.encode(outcome.result))
        if outcome.ok and outcome.skip_to is not None:
            names = wf.phase_names
            deps.skipped.extend(names[names.index(phase) + 1 : names.index(outcome.skip_to)])
            yield holder["c"].turn_for(outcome.skip_to, loop)
            return
        if (nxt := holder["c"].after(phase, loop)) is not None:
            yield nxt

    agent_phase.__name__ = f"agent_phase_{wf.name}_{suffix}"
    step_phase.__name__ = f"step_phase_{wf.name}_{suffix}"
    compiled = Compiled(wf, tool()(agent_phase), tool()(step_phase))
    holder["c"] = compiled
    return compiled
```

This needs `engine.run_one_step(*, phase, table, store, story_id, subtask, clock) -> _Outcome`. Extract it from `engine._run_deterministic` plus `_skip_target`, and have `_run_deterministic` call it, so the old engine's behaviour is unchanged. Its phase argument may be either a loader `DeterministicPhase` or a `phases.Step`: resolve `run`, `gates` and `when` with `entry if callable(entry) else workflow.function(entry)`, as in Task 3.2. **Check first** that pygents registers a tool under `fn.__name__` after the rename (`ToolRegistry.get(agent_phase.__name__)`). If it reads `__qualname__` instead, set that too.

- [ ] **Step 4:** `uv run pytest` → green, including the old engine's tests (`run_one_step` is a pure extraction).
- [ ] **Step 5: Commit** — `git commit -m "feat(runtime): compile a workflow into two pygents tools"`

### Task 3.4: Add `runtime.run_subtask` and run the engine tests on both engines

**Files:**
- Create: `src/agent_manager/runtime/engine.py`
- Modify: `tests/test_engine.py` (add an `engine` fixture; parametrize the behavioural tests)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Compiled`, `RunDeps`, `current_run`, `context.seed_item`, `engine.subtask_context`, `engine._document_paths`, `engine._escalate`, `engine._stop`, `engine._record_subtask_status`, `engine.SubtaskSummary`, `phases.from_loader`.
- Produces: `runtime.engine.run_subtask(workflow: phases.Workflow, store, *, story_id, subtask, repo_dir, commands=(), card=None, parent_story=None, extra_context=None, agent_runner=None, clock=..., should_stop=None) -> SubtaskSummary`, a synchronous function that calls `asyncio.run` once.

- [ ] **Step 1: Write the failing test harness**

At the top of `tests/test_engine.py`:

```python
from agent_manager.runtime import engine as new_engine
from agent_manager.workflow.phases import from_loader

@pytest.fixture(params=["yaml", "pygents"])
def run_subtask(request):
    if request.param == "yaml":
        return engine.run_subtask
    def run(workflow, store, **kw):
        kw.pop("start_phase", None) and pytest.skip("start_phase is the yaml engine's resume")
        return new_engine.run_subtask(from_loader(workflow), store, **kw)
    return run
```

Replace every direct `engine.run_subtask(` call in a **behavioural** test with the `run_subtask` fixture (add the parameter). Leave tests of old-engine internals (`bind_arguments`, `_bind_result`, `start_phase`, the loader) on `engine.*`. Also add:

```python
def test_new_engine_returns_same_summary_for_the_shipped_task(tmp_path):  # one explicit parity check
    ...  # walk load_builtin("task", fake registry) on both engines; assert equal status, results keys,
         # skipped, warnings, and identical phase rows in the store
```

- [ ] **Step 2: Watch the pygents half fail** (`ModuleNotFoundError: agent_manager.runtime.engine`).
- [ ] **Step 3: Implement**

```python
"""The pygents subtask engine (spec §4). Synchronous facade over one event loop."""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from pygents import Agent, ContextPool, ContextQueue
from agent_manager import engine as old
from agent_manager.runtime import compile as C, context
from agent_manager.runtime.state import RunDeps, current_run
from agent_manager.workflow.phases import Workflow

def run_subtask(workflow: Workflow, store, *, story_id: str, subtask, repo_dir: Path,
                commands: Sequence[str] = (), card=None, parent_story=None,
                extra_context: Mapping[str, Any] | None = None, agent_runner=None,
                clock: Callable = old._utcnow, should_stop: Callable[[], bool] | None = None,
                ) -> old.SubtaskSummary:
    return asyncio.run(_drive(workflow, store, story_id=story_id, subtask=subtask, repo_dir=repo_dir,
        commands=commands, card=card, parent_story=parent_story, extra_context=extra_context,
        agent_runner=agent_runner, clock=clock, should_stop=should_stop))

async def _drive(workflow, store, *, story_id, subtask, repo_dir, commands, card, parent_story,
                 extra_context, agent_runner, clock, should_stop):
    binding = old.subtask_context(subtask, repo_dir, commands, card=card, parent_story=parent_story)
    if extra_context:
        reserved = sorted(set(extra_context) & set(old.RESERVED_CONTEXT_KEYS))
        if reserved:
            raise old.EngineError(f"extra_context supplies {', '.join(map(repr, reserved))}, which the engine owns")
        binding.update(extra_context)
    binding.update(old._document_paths(workflow, card))
    compiled = C.compile_workflow(workflow)
    agent = Agent(f"{getattr(store, 'run_id', 'run')}:{subtask.card_id}", workflow.name,
                  [compiled.agent_phase, compiled.step_phase],
                  context_pool=ContextPool(), context_queue=ContextQueue(limit=10), tags=["subtask"])
    await agent.context_pool.add(context.seed_item(binding))
    await agent.put(compiled.first_turn())
    deps = RunDeps(workflow, store, story_id, subtask, agent_runner, clock, should_stop)
    return await _run(agent, deps)

async def _run(agent: Agent, deps: RunDeps) -> old.SubtaskSummary:
    summary = old.SubtaskSummary()
    token = current_run.set(deps)
    try:
        async for _ in agent.run():          # consumed to the end, always
            pass
    except C.Escalated as esc:
        _collect(agent, deps, summary)
        return old._escalate(summary, deps.store, deps.story_id, deps.subtask, esc.phase, esc.detail)
    except Exception as error:
        _collect(agent, deps, summary)
        running = agent.to_dict()["current_turn"]
        phase = running["kwargs"]["phase"] if running else "?"
        return old._escalate(summary, deps.store, deps.story_id, deps.subtask, phase,
                             f"{type(error).__name__}: {error}")
    finally:
        current_run.reset(token)
        from pygents import AgentRegistry
        AgentRegistry._items.pop(agent.name, None)   # see Review Focus 4; use the registry's real removal API
    _collect(agent, deps, summary)
    old._record_subtask_status(deps.store, deps.story_id, deps.subtask, summary.status)
    return summary

def _collect(agent, deps, summary):
    summary.results = {i.id: context.decode(i.content) for i in agent.context_pool.items
                       if i.id not in (context.SUBTASK, context.SKIPPED)}
    summary.skipped = list(deps.skipped)
    summary.warnings = list(deps.warnings)
```

`agent.to_dict()["current_turn"]` is read only after `run()` has ended; the generator's own `finally` clears `current_turn`. If it's already `None` by then, record the running phase from a `BEFORE_TURN`-free path instead: `agent_phase`/`step_phase` set `deps.running = phase` on entry. Use whichever the test proves works, and delete the other. For the registry removal, check `pygents.registry.AgentRegistry` for a removal method. If there isn't one, add a small `_forget(name)` helper in `runtime/engine.py` that edits the registry's dict, with a comment pointing at the upstream fix (spec §11).

- [ ] **Step 4:** `uv run pytest` → green on both engines.
- [ ] **Step 5: Commit** — `git commit -m "feat(runtime): run a subtask on pygents; engine tests run on both engines"`

### Task 3.5: Add `Goto` revision loops and the `feedback` input

**Files:**
- Modify: `src/agent_manager/prompt.py` (`feedback` resolver), `src/agent_manager/runtime/compile.py` (loop branch already sketched in 3.3; this task adds its tests and any fixes)
- Test: `tests/runtime/test_loops.py`, `tests/test_prompt.py`

**Interfaces:**
- Produces: `prompt._TABLE["feedback"]`, which renders the `feedback` list as a section titled "Feedback from review" with one bullet per item (`from`: `detail`), and renders nothing when the list is empty or missing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/test_loops.py — uses the fixtures/_drive pattern of test_compile.py
async def test_blockers_loop_back_once_then_pass():
    # fake runner: validate_spec fails (AgentPhaseFailed) the first time, passes the second
    # expect dispatch order: spec, validate_spec, spec, validate_spec, plan
    # expect the second `spec` call's context["feedback"] == [{"for": "spec", "from": "validate_spec", "detail": ...}]

async def test_second_failure_escalates_validation():
    # validate_spec fails twice → Escalated(phase="validate_spec"); `plan` never dispatched

async def test_loop_count_is_in_the_queued_turn():
    # after the first failure, agent.to_dict()["queue"][0]["kwargs"] == {"phase": "spec", "loop": 1}
```

Write each as a full test using `_drive`, `AgentPhaseFailed("validate_spec", outcome="gate_failed", detail="no error path")` and a fake `agent_runner(phase, context, rendered)` that records `(phase.name, context.get("feedback"))`.

```python
# tests/test_prompt.py (append)
def test_feedback_renders_each_item_and_nothing_when_empty():
    phase = SimpleNamespace(name="spec", role="spec_author", inputs=("feedback",))
    text = render_prompt(phase, {"feedback": [{"for": "spec", "from": "validate_spec", "detail": "no error path"}]}).text
    assert "Feedback from review" in text and "validate_spec: no error path" in text
    assert "Feedback from review" not in render_prompt(phase, {"feedback": []}).text
```

- [ ] **Step 2: Watch them fail.**
- [ ] **Step 3: Implement** the resolver with the same `Resolver` shape as `_verbatim`, and fix `agent_phase`'s loop branch until the tests pass.
- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(runtime): revision loops with critic feedback"`

---

## Story 4 — Checkpoints and resume

### Task 4.1: Add the `checkpoints` table to the store

**Files:**
- Modify: `src/agent_manager/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `store.Checkpoint` (a frozen dataclass with `run_id`, `card_id`, `seq`, `workflow`, `digest`, `reason`, `agent: dict`, `saved_at: datetime`); `Store.save_checkpoint(card_id: str, *, workflow: str, digest: str, reason: str, agent: dict, saved_at: datetime) -> Checkpoint` (it assigns `seq`); `Store.latest_checkpoint(card_id: str) -> Checkpoint | None` (for this store's run); `Store.latest_open_checkpoint(card_id: str, workflow: str) -> Checkpoint | None` (across runs; `reason in ('turn','parked','escalated')` and the newest row is not terminal).

- [ ] **Step 1: Write the failing tests** — seq increments per card from 0; `latest_checkpoint` returns the highest seq; a `CHECK` violation (`reason="bogus"`) raises; `latest_open_checkpoint` finds an older run's `parked` row, and returns `None` when that card's newest row across runs is `done`; the `agent` dict round-trips byte-equal through JSON.
- [ ] **Step 2: Watch them fail.**
- [ ] **Step 3: Implement.** Append the spec's `CREATE TABLE IF NOT EXISTS checkpoints` (spec §6) to `_SCHEMA`, and write the three methods with the store's existing connection and transaction helpers. `agent` is stored as `json.dumps(agent, sort_keys=True)`.
- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(store): add the checkpoints table"`

### Task 4.2: Checkpoint every turn and bridge the run's stop

**Files:**
- Create: `src/agent_manager/runtime/checkpoint.py`
- Modify: `src/agent_manager/runtime/engine.py` (import `checkpoint` so the hooks register; final save; `Parked` handling)
- Test: `tests/runtime/test_checkpoint.py`, `tests/runtime/test_stop_bridge.py`

**Interfaces:**
- Produces: `checkpoint.Parked(Exception)` with `.before_phase: str`; the module-level hook `checkpoint.before_turn(agent)`, registered with `@hook(AgentHook.BEFORE_TURN, tags={"subtask"})`; `checkpoint.save(agent, reason: str) -> None`, which reads `current_run` and is a no-op when no run is set.

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/test_checkpoint.py
async def test_every_turn_is_checkpointed_before_it_runs(): ...
    # 3-step workflow → rows seq 0,1,2 reason 'turn', each queue head = that phase; then one 'done' row

async def test_an_escalation_writes_an_escalated_row(): ...

async def test_hook_without_a_run_does_nothing():                 # Review Focus 5
    agent = Agent("stray", "t", [some_tool], tags=["subtask"])
    await agent.put(Turn(some_tool))
    async for _ in agent.run():
        pass                                                      # no LookupError, no row
```

```python
# tests/runtime/test_stop_bridge.py
async def test_stop_set_during_a_phase_parks_before_the_next():
    stop = threading.Event()
    # step "a" sets stop.set() while running; should_stop=stop.is_set
    # expect: summary.status == "stopped", summary.detail == "stopped before b",
    #         newest checkpoint reason 'parked' with queue head "b", and "b" never ran
```

Write the `...` bodies fully, using `runtime.engine.run_subtask` with fake steps and a real temporary `Store`.

- [ ] **Step 2: Watch them fail.**
- [ ] **Step 3: Implement**

```python
"""Checkpoint hooks (spec §6). Module-level on purpose: see spec §2 on closure hooks."""
import json
from pygents import AgentHook, hook
from agent_manager.runtime.state import current_run

class Parked(Exception):
    def __init__(self, before_phase: str) -> None:
        self.before_phase = before_phase
        super().__init__(f"stopped before {before_phase}")

def save(agent, reason: str) -> None:
    deps = current_run.get(None)
    if deps is None:
        return
    deps.store.save_checkpoint(deps.subtask.card_id, workflow=deps.workflow.name,
        digest=deps.workflow.digest(), reason=reason, agent=agent.to_dict(), saved_at=deps.clock())

@hook(AgentHook.BEFORE_TURN, tags={"subtask"})
async def before_turn(agent) -> None:
    deps = current_run.get(None)
    if deps is None:
        return
    snapshot = agent.to_dict()
    head = (snapshot["current_turn"] or snapshot["queue"][0])["kwargs"]["phase"]
    if deps.should_stop is not None and deps.should_stop():
        save(agent, "parked")
        raise Parked(head)
    save(agent, "turn")
```

In `runtime/engine._run`: `except checkpoint.Parked as parked: _collect(...); return old._stop(summary, ..., parked.before_phase)`. On a clean end, call `save(agent, "done")`. In both escalation branches, call `save(agent, "escalated")` before `_escalate`.

- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(runtime): checkpoint every turn and park on the run's stop"`

### Task 4.3: Resume a subtask from its checkpoint

**Files:**
- Modify: `src/agent_manager/runtime/engine.py`
- Test: `tests/runtime/test_resume.py`

**Interfaces:**
- Produces: `runtime.engine.run_subtask(..., resume_from: store.Checkpoint | None = None)` (when it's given, the seed and first turn are skipped and the agent is `Agent.from_dict(resume_from.agent)`); `runtime.engine.CheckpointMismatch(Exception)`, raised when `resume_from.digest != workflow.digest()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/test_resume.py
def test_a_crash_resumes_at_the_phase_it_died_in(tmp_path):
    # runner raises KeyboardInterrupt (a BaseException) in phase "c" of a..e; the call propagates it
    # latest checkpoint: reason 'turn', queue head "c"
    # resume_from=that row: dispatched phases are exactly ["c", "d", "e"]; a and b results come from the pool

def test_loop_count_survives_a_resume(tmp_path): ...    # crash in the looped `spec` (loop=1); resumed turn has loop=1
def test_a_changed_workflow_is_refused(tmp_path): ...   # resume_from built from W; call with W' → CheckpointMismatch
def test_a_parked_subtask_resumes(tmp_path): ...        # park before "b", resume → b..end run, status done
def test_resuming_twice_in_one_process(tmp_path): ...   # Review Focus 4: same card resumed twice in a row, no ValueError
```

- [ ] **Step 2: Watch them fail.**
- [ ] **Step 3: Implement.** In `_drive`: if `resume_from` is given, check the digest, then `agent = Agent.from_dict(resume_from.agent)`, forgetting any stale `AgentRegistry` entry under that name first, and skip `seed_item` and `first_turn`. `from_dict` restores tools from `ToolRegistry` by name, so call `compile_workflow(workflow)` **before** `from_dict`: the tool names include the digest prefix, which is what makes a changed workflow fail early instead of resolving the wrong tool.
- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(runtime): resume a subtask from its checkpoint"`

### Task 4.4: Select the engine with `--engine` through run, milestone and Integrate

**Files:**
- Modify: `src/agent_manager/cli.py` (`Engine = Literal["yaml", "pygents"]`; `drive_subtask(..., engine: Engine = "yaml")`; `run_card(..., engine)`; the Typer `run` command gets `--engine`), `src/agent_manager/orchestrate.py` (`run_milestone(..., engine)` passed to the driver), `src/agent_manager/integration.py` (same)
- Test: `tests/test_cli.py`, `tests/e2e/test_production_wiring.py`, `tests/e2e/test_milestone_run.py`, `tests/e2e/test_parallel_milestone.py`

**Interfaces:**
- Produces: in `drive_subtask`, `engine == "pygents"` → `runtime.engine.run_subtask(workflow.task.TASK, ...)` (Integrate: `workflow.integrate.INTEGRATE`) with the same arguments, minus `start_phase`; `engine == "yaml"` → unchanged.

- [ ] **Step 1: Write the failing tests** — parametrize the fake-claude e2e tests (`test_production_wiring`, `test_milestone_run`, `test_parallel_milestone`) over `engine ∈ {yaml, pygents}` by passing `--engine`, and assert identical JSON `data` (ignoring `run_id` and timestamps). Add `test_cli`: `--engine bogus` is a usage error (exit 2, nothing on stdout).
- [ ] **Step 2: Watch them fail.**
- [ ] **Step 3: Implement** the plumbing. `RunnerFactory` still receives a `workflow`: for pygents, pass the loader workflow the old code already loads, since `AgentRunner` only uses it for by-name gates, which the phase model never produces.
- [ ] **Step 4:** `uv run pytest` → green on both engines, parallel milestone included (each lane thread runs its own `asyncio.run`).
- [ ] **Step 5: Commit** — `git commit -m "feat(cli): --engine selects the YAML or the pygents engine"`

### Task 4.5: `am resume` and milestone relaunch continue from checkpoints

**Files:**
- Modify: `src/agent_manager/cli.py` (`resume_run`, the Typer `resume` command gets `--engine`), `src/agent_manager/orchestrate.py` (relaunch: look up open checkpoints)
- Test: `tests/test_cli.py`, `tests/e2e/test_milestone_run.py`

**Interfaces:**
- Consumes: `Store.latest_checkpoint`, `Store.latest_open_checkpoint`, `run_subtask(resume_from=...)`, `CheckpointMismatch`.
- Produces: `am resume <run-id> --engine pygents`. It resumes from the newest `turn`/`parked`/`escalated` row, including a stopped subtask; if the workflow changed it prints `{"ok": false, "error": ...}` and exits 3; orphan attempts are still marked `harness_error`. On relaunch with `--engine pygents`, a card with an open checkpoint and a matching digest continues from it; with a different digest it starts fresh and no error is raised.

- [ ] **Step 1: Write the failing tests**:
  - under a fake claude, kill a run in `plan`, resume, and assert that `explore` and `spec` were not re-dispatched (count the fake's invocations per phase);
  - a stopped milestone lane, resumed with `am resume`, continues instead of being refused;
  - a digest mismatch exits 3 with the message "workflow changed since checkpoint";
  - a relaunch after a stop continues the parked card;
  - with `--engine yaml`, the existing resume tests are unchanged.
- [ ] **Step 2: Watch them fail.**
- [ ] **Step 3: Implement.** Branch `resume_run` on the engine. The yaml branch is untouched; the pygents branch uses checkpoints and never calls `interrupted_phase` or `resume_start_phase`.
- [ ] **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(cli): resume and relaunch from checkpoints on the pygents engine"`

---

## Story 5 — Switch over

### Task 5.1: Turn on the critic loops in `TASK`

**Files:**
- Modify: `src/agent_manager/workflow/task.py` (`validate_spec.on_fail = Goto("spec")`, `validate_plan.on_fail = Goto("plan")`, `"feedback"` added to the `spec` and `plan` inputs), `tests/workflow/test_declared.py` (compare against the YAML with `on_fail` and the `feedback` input stripped), `tests/e2e/fake_claude.py` (a mode where the critic blocks once)
- Test: `tests/e2e/test_production_wiring.py`

- [ ] **Step 1: Write the failing tests** — under a fake claude with `--engine pygents`: when `spec_critic` blocks once, the run ends `done`, `spec` was dispatched twice, and the second brief contains the critic's reason. When it blocks twice, the run exits 1 with `failed_phase == "validate_spec"`. The same test on `--engine yaml` escalates on the first block, which is documented behaviour and asserted explicitly.
- [ ] **Step 2: Watch them fail.**  **Step 3: Implement.**  **Step 4:** `uv run pytest` → green.
- [ ] **Step 5: Commit** — `git commit -m "feat(workflow): critics loop back once before escalating"`

### Task 5.2: Prove the pygents engine against a real harness

**Files:**
- Modify: `tests/e2e/test_real_harness.py` (parametrize the existing opt-in test over `engine`)

- [ ] **Step 1:** Parametrize the opt-in real-harness subtask test over `engine ∈ {yaml, pygents}`, keeping its existing opt-in guard (environment variable) and assertions.
- [ ] **Step 2:** Run it for real: `AM_REAL_HARNESS=1 uv run pytest tests/e2e/test_real_harness.py -k pygents -v` (use the guard variable that file actually checks). Expected: PASS. Record the run id in the commit message.
- [ ] **Step 3:** `uv run pytest` → green, with the opt-in test skipped by default.
- [ ] **Step 4: Commit** — `git commit -m "test(e2e): prove the pygents engine against a real harness"`

### Task 5.3: Make pygents the only engine

**Files:**
- Delete: `src/agent_manager/engine.py` (after moving `SubtaskSummary`, `subtask_context`, `bind_arguments`, `run_one_step`, `_document_paths`, `_escalate`, `_stop`, `_record_subtask_status`, `RESERVED_CONTEXT_KEYS` and `EngineError` to `runtime/`), `workflow/loader.py`, `workflow/registry.py`, `workflow/builtin/`, `phases.from_loader`, `tests/workflow/test_loader.py`, `tests/workflow/test_registry.py`, `tests/workflow/test_builtin_task.py`, `tests/workflow/test_declared.py`
- Modify: `cli.py` (delete `interrupted_phase`, `resume_start_phase`, `_skipped_origin` and `--engine`), `dispatch.py` and `prompt.py` (delete the by-name paths from Task 3.2), `orchestrate.py`, `integration.py`, `pyproject.toml` (drop `pyyaml` if nothing else imports it: `grep -rn "import yaml" src`), `tests/test_engine.py` (drop the `yaml` param)

- [ ] **Step 1:** Run `grep -rn "workflow.loader\|workflow.registry\|load_builtin\|from agent_manager import engine\|agent_manager.engine" src tests`. Every hit is either moved or deleted in this task.
- [ ] **Step 2:** Move the helpers, update the imports, and delete the files and paths listed above.
- [ ] **Step 3:** `uv run pytest` → green. `am run --help` shows no `--engine`.
- [ ] **Step 4: Commit** — `git commit -m "refactor: make pygents the only engine; delete the YAML engine"`

### Task 5.4: Document the pygents engine

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (a one-paragraph pointer in §6/§9 to the addendum)

- [ ] **Step 1:** Read `runtime/engine.py`, `runtime/checkpoint.py`, `workflow/task.py` and `cli.resume_run` as they actually are. Document from the code, not from the spec.
- [ ] **Step 2:** Update the README:
  - **Relaunching resumes:** `am resume` continues stopped subtasks now, the digest refusal (exit 3), and the fact that nothing before the interrupted phase re-runs;
  - **What an escalation report contains:** a critic's blockers get one revision before escalating;
  - **Not there yet:** remove "`am resume` is not milestone-aware" if Task 4.5 made it so; otherwise leave it.
- [ ] **Step 3:** `uv run pytest` → green.
- [ ] **Step 4: Commit** — `git commit -m "docs: document the pygents engine, checkpoints and critic loops"`
