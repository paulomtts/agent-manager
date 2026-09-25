<!-- task-pipeline: validated -->
# Declare TASK and INTEGRATE, pinned to the shipped YAML (card 04a5b91e)

Parent story: 09e1183f "The declared phase model". Plan: `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 1.3. Milestone spec: `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`.

## Scope

Two new modules that declare the builtin workflows as phase-model data, plus one pure-data test file. Nothing else changes.

- `src/agent_manager/workflow/task.py` exports `TASK: Workflow` and `LAUNCHER_TIMEOUT: timedelta`.
- `src/agent_manager/workflow/integrate.py` exports `INTEGRATE: Workflow`.
- `tests/workflow/test_declared.py`.

Consumed as-is (do not edit): `workflow/phases.py` (`Workflow`, `AgentPhase`, `Step`, `Retry`, `from_loader`, owned by siblings a0aa01e5 / 0326732a and already present in this worktree), `workflow/registry.py`, `workflow/loader.py`, `steps/reducers.py`, `steps/integrate.py`, the builtin YAML files, and the old-engine tests `tests/workflow/test_builtin_task.py` / `test_builtin_integrate.py`.

Out of scope: wiring `TASK`/`INTEGRATE` into any runner or the `--engine` switch, the supervisor tree, `Goto`/`on_fail` loops (the shipped YAML has none, so no phase sets `on_fail`), and anything under `runtime/`.

## Observable behaviour

`LAUNCHER_TIMEOUT = timedelta(seconds=dispatch.DEFAULT_TIMEOUT)` (1800 s, `src/agent_manager/dispatch.py:148`).

`TASK` is `Workflow("task", ...)` with these phases, in this order, mirroring `src/agent_manager/workflow/builtin/task.yaml` field for field. Callables are real function objects imported from their modules, never registry lookups, and each must be the same object `default_registry()` binds (`registry.py` lines 225-245), because `digest()` identifies callables by `module.qualname`:

| phase | kind | declaration |
|---|---|---|
| worktree | Step | run `worktree.ensure` |
| explore | AgentPhase | role `explorer`; inputs `(card, parent_story, repo_docs, verification)`; `results.ExploreResult`; gates `reducers.exploration_output_gate`, `reducers.verification_gate`; `Retry(2, ("schema_invalid", "gate_failed"))` |
| mark_in_progress | Step | run `rollup.set_status`, args `{"status": "in_progress"}`, `best_effort=True` |
| plan_check | Step | run `plan_check.find_validated_plan`, `when=plan_check.has_validated_plan`, `skip_to="docs_commit"` |
| spec | AgentPhase | role `spec_author`; inputs `(card, explore, spec_path)`; `results.SpecResult`; writes `docs/superpowers/specs/{stem}.md` |
| validate_spec | AgentPhase | role `critic`; inputs `(card, spec_path)`; `results.CriticResult`; gate `reducers.critic_blockers_gate` |
| plan | AgentPhase | role `planner`; inputs `(spec_path, plan_path)`; `results.PlanResult`; writes `docs/superpowers/plans/{stem}.md` |
| validate_plan | AgentPhase | role `critic`; inputs `(spec_path, plan_path)`; `results.CriticResult`; gate `reducers.critic_blockers_gate` |
| mark_validated | Step | run `plan_check.mark_validated` |
| docs_commit | Step | run `docs_commit.commit_documents` |
| implement | AgentPhase | role `coder`; inputs `(plan_path, spec_path, branch, base_branch, plan_hash)`; `results.ImplementResult`; gate `reducers.implement_blocked_gate` |
| review | AgentPhase | role `reviewer`; inputs `(branch, base_branch, plan_path)`; `results.ReviewResult`; gates `reducers.review_gate`, `reducers.plan_hash_gate_adapter` (in that order) |
| verify | Step | run `verify.run_suite`, gate `reducers.verification_passed_gate` |
| mark_done | Step | run `rollup.set_status`, args `{"status": "done"}`, `best_effort=True` |

`Retry.on` order must match the YAML's `on:` list exactly (the digest includes `repr(retry)`), as must gate order and input order.

`INTEGRATE` is `Workflow("integrate", ...)` mirroring `builtin/integrate.yaml`: `resolve` (AgentPhase, role `resolver`, inputs `(branch, base_branch, merge_tip, conflict_files, verification)`, `results.ResolveResult`, gate `steps.integrate.merge_completed_gate` (it lives in `steps/integrate.py`, not `reducers`; import it under an alias so it does not clash with the `workflow.integrate` module name), `Retry(2, ("gate_failed", "schema_invalid"))`), then `verify` (Step, run `verify.run_suite`, gate `reducers.verification_passed_gate`).

Per-phase timeouts (new data the YAML never had): the chosen values are explore 20 min, spec/plan 30 min, validate_spec/validate_plan 20 min, implement 90 min, review 45 min, resolve 30 min. `validate()` requires every agent timeout to be strictly greater than `LAUNCHER_TIMEOUT` (30 min), so any chosen value that is not above it is raised, never the launcher's lowered. Declare that floor once as a module constant, `LAUNCHER_TIMEOUT + timedelta(minutes=5)` (35 min), and give each agent phase `max(chosen, floor)`. That makes explore, spec, validate_spec, plan, validate_plan and resolve 35 min, implement 90 min and review 45 min. `integrate.py` imports `LAUNCHER_TIMEOUT` from `workflow.task` and does not define its own.

Neither module imports pygents (rule 1). Both are importable with no I/O. Building the objects touches no files, roles or git.

## Error paths

There are no runtime error paths of its own: both modules are constant data. Drift is caught by the tests. If a phase, field, order or callable disagrees with the shipped YAML, the digest assertion fails. If a role doesn't load, an input has no resolver or earlier phase, `when`/`skip_to` are inconsistent, or a timeout does not exceed the launcher's, `Workflow.validate()` raises `WorkflowError` naming the phase.

## Tests

All tests go in `tests/workflow/test_declared.py`, in the **pure-data tier** of the milestone spec §9 (`2026-09-25-pygents-engine-design.md`): no fakes, no git, no harness, next to `tests/workflow/test_phases.py`. None of them is a behavioural or engine-parity test, so none goes in `runtime/`, `steps/` or e2e. Follow the plan's skeleton (Task 1.3 Step 1) verbatim, including `_shipped()`, which copies each agent phase's timeout from the declared workflow onto the `from_loader` conversion before comparing digests.

1. `test_task_equals_the_shipped_yaml`: `TASK.digest() == _shipped("task", TASK).digest()`. Pure-data tier.
2. `test_integrate_equals_the_shipped_yaml`: `INTEGRATE.digest() == _shipped("integrate", INTEGRATE).digest()`. Pure-data tier.
3. `test_both_validate`: `TASK.validate(launcher_timeout=LAUNCHER_TIMEOUT)` and `INTEGRATE.validate(launcher_timeout=LAUNCHER_TIMEOUT)` both return without raising. Pure-data tier. Its only I/O is `validate()`'s role loading.

Acceptance: `uv run pytest` passes as a whole suite, including `tests/e2e` on both engines (rule 2).

---

# Declare TASK and INTEGRATE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare the builtin `task` and `integrate` workflows as phase-model data (`workflow/task.py`, `workflow/integrate.py`), pinned by digest to the shipped YAML and passing `Workflow.validate()`.

**Architecture:** Two constant-data modules build `Workflow` objects from `phases.AgentPhase`/`Step`/`Retry`, using the real step and gate function objects that `registry.default_registry()` binds. `task.py` also owns `LAUNCHER_TIMEOUT` and a single timeout floor (`LAUNCHER_TIMEOUT + 5 min`) plus a helper `agent_timeout(minutes)` that `integrate.py` reuses. One pure-data test file compares each declared digest against `from_loader(load_builtin(...))` with timeouts copied across, and runs `validate()`.

**Tech Stack:** Python 3, frozen dataclasses from `agent_manager.workflow.phases`, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-declare-task-and-04a5b91e/docs/superpowers/specs/task-declare-task-and-04a5b91e-design.md` (prepended above).

## Global Constraints

- Only `src/agent_manager/runtime/` imports pygents; `workflow/task.py` and `workflow/integrate.py` never do (rule 1).
- Every card must leave the WHOLE default suite green, including `tests/e2e`, on both engines while `--engine` exists (rule 2). Verification command: `uv run pytest`.
- `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads stay unchanged (G10) -- this card touches none of them.
- Do not edit `workflow/phases.py`, `workflow/registry.py`, `workflow/loader.py`, `steps/reducers.py`, `steps/integrate.py`, `builtin/task.yaml`, `builtin/integrate.yaml`, `tests/workflow/test_builtin_task.py`, `tests/workflow/test_builtin_integrate.py`.
- `LAUNCHER_TIMEOUT = timedelta(seconds=dispatch.DEFAULT_TIMEOUT)` (1800 s). Never lower it; raise a phase timeout instead.
- Timeout floor: `LAUNCHER_TIMEOUT + timedelta(minutes=5)` (35 min), declared once in `workflow/task.py`; every agent phase gets `max(chosen, floor)`. Chosen: explore 20, spec 30, validate_spec 20, plan 30, validate_plan 20, implement 90, review 45, resolve 30 (minutes).
- Callables are real function objects, never registry lookups; `Retry.on`, gate order and input order match the YAML exactly.
- `integrate.py` imports `LAUNCHER_TIMEOUT` machinery from `workflow.task`; it does not define its own. `merge_completed_gate` is imported from `agent_manager.steps.integrate` under a module alias (`integrate_steps`).
- Tests live in `tests/workflow/test_declared.py`, pure-data tier (spec §9): no fakes, no git, no harness.
- No `Goto`/`on_fail`, no runner or `--engine` wiring, nothing under `runtime/`.

## Review Focus

- Per-phase timeouts drifting from the chosen values: the digest test copies timeouts from the declared workflow onto the shipped one, so it can never catch a wrong timeout, and `validate()` only checks `> 30 min`. Expect each agent phase to carry exactly 35/90/45 min as specified. Pinned by `test_task_timeouts_are_the_chosen_values_floored_above_the_launcher` (Task 1) and `test_integrate_timeout_is_floored_above_the_launcher` (Task 2).
- A callable that has the right `module.qualname` but is not the object `default_registry()` binds (e.g. a wrapper or re-import): the digest cannot tell them apart. Expect `is`-identity with the registry's binding. Pinned by `test_task_callables_are_the_registry_bindings` (Task 1) and `test_integrate_callables_are_the_registry_bindings` (Task 2).
- `LAUNCHER_TIMEOUT` hard-coded to 1800 s instead of derived from `dispatch.DEFAULT_TIMEOUT`, so a later change to the launcher default silently makes `validate()` wrong. Expect equality with `dispatch.DEFAULT_TIMEOUT` seconds. Pinned by `test_launcher_timeout_is_the_dispatch_default` (Task 1).
- A pygents import sneaking into either declared module (rule 1). Expect no `pygents`/`pygents.*` import. Pinned by `test_declared_modules_never_import_pygents` (Task 1, extended in Task 2).
- `integrate.py` defining its own launcher timeout or floor, so the two workflows can disagree about G2. Expect `integrate.py` to reuse `workflow.task.agent_timeout`. Pinned by `test_integrate_reuses_the_task_timeout_floor` (Task 2).

---

## File Structure

- Create `src/agent_manager/workflow/task.py` -- `LAUNCHER_TIMEOUT`, `AGENT_TIMEOUT_FLOOR`, `agent_timeout(minutes)`, `TASK`.
- Create `src/agent_manager/workflow/integrate.py` -- `INTEGRATE`.
- Create `tests/workflow/test_declared.py` -- pure-data tests for both.

Neither new module is imported by `src/agent_manager/workflow/__init__.py`; leave that file alone. There is no import cycle: `task.py` imports `agent_manager.dispatch`, which imports `agent_manager.workflow.loader` (and so the `workflow` package `__init__`, which imports only `loader` and `registry`), none of which import `workflow.task`.

---

### Task 1: Declare `TASK` and `LAUNCHER_TIMEOUT`

**Files:**
- Create: `src/agent_manager/workflow/task.py`
- Test: `tests/workflow/test_declared.py`

**Interfaces:**
- Consumes: `agent_manager.workflow.phases.{AgentPhase, Retry, Step, Workflow, from_loader}` (frozen dataclasses; `AgentPhase(name, role, inputs, result, gates=(), retry=None, writes=None, timeout=timedelta(minutes=30), on_fail=None)`, `Step(name, run, args={}, gates=(), best_effort=False, when=None, skip_to=None)`, `Retry(max_attempts, on)`, `Workflow(name, phases)` with `.digest() -> str`, `.validate(*, launcher_timeout: timedelta, role_root=None) -> None`, `.phase(name)`, `.phase_names`); `agent_manager.workflow.loader.load_builtin(name, registry)`; `agent_manager.workflow.registry.default_registry()`; `agent_manager.dispatch.DEFAULT_TIMEOUT` (`1800.0`); real functions in `agent_manager.steps.{worktree, rollup, plan_check, docs_commit, verify, reducers}` and result classes in `agent_manager.results`.
- Produces: `agent_manager.workflow.task.LAUNCHER_TIMEOUT: timedelta` (30 min), `agent_manager.workflow.task.AGENT_TIMEOUT_FLOOR: timedelta` (35 min), `agent_manager.workflow.task.agent_timeout(minutes: int) -> timedelta` (returns `max(timedelta(minutes=minutes), AGENT_TIMEOUT_FLOOR)`), `agent_manager.workflow.task.TASK: Workflow` (name `"task"`, 14 phases).

- [ ] **Step 1: Write the failing tests**

Create `tests/workflow/test_declared.py`:

```python
"""Pure-data tier (spec §9): TASK and INTEGRATE are constant phase-model data,
so these are plain unit tests -- no fakes, no git, no harness. The only I/O is
load_builtin's read of the shipped YAML and validate()'s role loading."""

import ast
from datetime import timedelta
from pathlib import Path

from agent_manager import dispatch
from agent_manager.workflow import task as task_module
from agent_manager.workflow.loader import load_builtin
from agent_manager.workflow.phases import AgentPhase, Step, from_loader
from agent_manager.workflow.registry import default_registry
from agent_manager.workflow.task import TASK, LAUNCHER_TIMEOUT


def _shipped(name, like):
    converted = from_loader(load_builtin(name, default_registry()))
    # timeouts are new data the YAML never had: compare with TASK's own
    return type(converted)(converted.name, tuple(
        p if not hasattr(p, "timeout") else type(p)(**{**p.__dict__, "timeout": like.phase(p.name).timeout})
        for p in converted.phases))


def _assert_same_callables(declared, name):
    """Identity, not just qualname: every callable is the object the registry binds."""
    converted = from_loader(load_builtin(name, default_registry()))
    assert declared.phase_names == converted.phase_names
    for mine, theirs in zip(declared.phases, converted.phases):
        assert type(mine) is type(theirs), mine.name
        assert len(mine.gates) == len(theirs.gates), mine.name
        for ours, registered in zip(mine.gates, theirs.gates):
            assert ours is registered, mine.name
        if isinstance(mine, Step):
            assert mine.run is theirs.run, mine.name
            assert mine.when is theirs.when, mine.name
        else:
            assert mine.result is theirs.result, mine.name


def _imported_modules(module):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_task_equals_the_shipped_yaml():
    assert TASK.digest() == _shipped("task", TASK).digest()


def test_both_validate():
    TASK.validate(launcher_timeout=LAUNCHER_TIMEOUT)


def test_task_callables_are_the_registry_bindings():
    _assert_same_callables(TASK, "task")


def test_launcher_timeout_is_the_dispatch_default():
    assert LAUNCHER_TIMEOUT == timedelta(seconds=dispatch.DEFAULT_TIMEOUT)
    assert LAUNCHER_TIMEOUT == timedelta(minutes=30)


def test_task_timeouts_are_the_chosen_values_floored_above_the_launcher():
    timeouts = {p.name: p.timeout for p in TASK.phases if isinstance(p, AgentPhase)}
    assert timeouts == {
        "explore": timedelta(minutes=35),
        "spec": timedelta(minutes=35),
        "validate_spec": timedelta(minutes=35),
        "plan": timedelta(minutes=35),
        "validate_plan": timedelta(minutes=35),
        "implement": timedelta(minutes=90),
        "review": timedelta(minutes=45),
    }
    assert task_module.AGENT_TIMEOUT_FLOOR == LAUNCHER_TIMEOUT + timedelta(minutes=5)
    assert task_module.agent_timeout(20) == task_module.AGENT_TIMEOUT_FLOOR
    assert task_module.agent_timeout(90) == timedelta(minutes=90)


def test_declared_modules_never_import_pygents():
    for module in (task_module,):
        imported = _imported_modules(module)
        assert not any(n == "pygents" or n.startswith("pygents.") for n in imported), module.__name__
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_declared.py -v`
Expected: collection ERROR with `ModuleNotFoundError: No module named 'agent_manager.workflow.task'`.

- [ ] **Step 3: Write the implementation**

Create `src/agent_manager/workflow/task.py`:

```python
"""The builtin `task` workflow as declared phase-model data (spec G3).

Pinned to `builtin/task.yaml`: `tests/workflow/test_declared.py` asserts that
`TASK.digest()` equals the digest of the shipped YAML run through
`phases.from_loader`, so the two cannot drift apart silently. Every callable
is the real function object `registry.default_registry()` binds -- never a
registry lookup -- because the digest names callables by `module.qualname`.

Timeouts are the one thing the YAML never had. G2 requires every agent turn
timeout to exceed the launcher's strictly (the launcher must kill `claude -p`
before the turn is cancelled), so each phase gets `max(chosen, floor)` with the
floor five minutes above the launcher timeout. The launcher's is never lowered.

No pygents import here (rule 1).
"""

from __future__ import annotations

from datetime import timedelta

from agent_manager import dispatch, results
from agent_manager.steps import docs_commit, plan_check, reducers, rollup, verify, worktree
from agent_manager.workflow.phases import AgentPhase, Retry, Step, Workflow

LAUNCHER_TIMEOUT = timedelta(seconds=dispatch.DEFAULT_TIMEOUT)
"""How long the launcher lets one `claude -p` run before killing it (1800 s)."""

AGENT_TIMEOUT_FLOOR = LAUNCHER_TIMEOUT + timedelta(minutes=5)
"""The lowest agent-phase timeout: strictly above `LAUNCHER_TIMEOUT` (G2)."""


def agent_timeout(minutes: int) -> timedelta:
    """The chosen timeout, raised to `AGENT_TIMEOUT_FLOOR` if it is not above it."""
    return max(timedelta(minutes=minutes), AGENT_TIMEOUT_FLOOR)


TASK = Workflow("task", (
    Step("worktree", worktree.ensure),
    AgentPhase(
        "explore",
        role="explorer",
        inputs=("card", "parent_story", "repo_docs", "verification"),
        result=results.ExploreResult,
        gates=(reducers.exploration_output_gate, reducers.verification_gate),
        retry=Retry(2, ("schema_invalid", "gate_failed")),
        timeout=agent_timeout(20),
    ),
    Step("mark_in_progress", rollup.set_status, args={"status": "in_progress"}, best_effort=True),
    Step(
        "plan_check",
        plan_check.find_validated_plan,
        when=plan_check.has_validated_plan,
        skip_to="docs_commit",
    ),
    AgentPhase(
        "spec",
        role="spec_author",
        inputs=("card", "explore", "spec_path"),
        result=results.SpecResult,
        writes="docs/superpowers/specs/{stem}.md",
        timeout=agent_timeout(30),
    ),
    AgentPhase(
        "validate_spec",
        role="critic",
        inputs=("card", "spec_path"),
        result=results.CriticResult,
        gates=(reducers.critic_blockers_gate,),
        timeout=agent_timeout(20),
    ),
    AgentPhase(
        "plan",
        role="planner",
        inputs=("spec_path", "plan_path"),
        result=results.PlanResult,
        writes="docs/superpowers/plans/{stem}.md",
        timeout=agent_timeout(30),
    ),
    AgentPhase(
        "validate_plan",
        role="critic",
        inputs=("spec_path", "plan_path"),
        result=results.CriticResult,
        gates=(reducers.critic_blockers_gate,),
        timeout=agent_timeout(20),
    ),
    Step("mark_validated", plan_check.mark_validated),
    Step("docs_commit", docs_commit.commit_documents),
    AgentPhase(
        "implement",
        role="coder",
        inputs=("plan_path", "spec_path", "branch", "base_branch", "plan_hash"),
        result=results.ImplementResult,
        gates=(reducers.implement_blocked_gate,),
        timeout=agent_timeout(90),
    ),
    AgentPhase(
        "review",
        role="reviewer",
        inputs=("branch", "base_branch", "plan_path"),
        result=results.ReviewResult,
        gates=(reducers.review_gate, reducers.plan_hash_gate_adapter),
        timeout=agent_timeout(45),
    ),
    Step("verify", verify.run_suite, gates=(reducers.verification_passed_gate,)),
    Step("mark_done", rollup.set_status, args={"status": "done"}, best_effort=True),
))
"""`builtin/task.yaml`, phase for phase."""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_declared.py -v`
Expected: 6 passed. If `test_task_equals_the_shipped_yaml` fails, diff `TASK.phases` against `from_loader(load_builtin("task", default_registry())).phases` field by field (gate order, `Retry.on` order, `args`, `writes`) and fix `task.py` -- never the YAML or the test.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all green, including `tests/e2e`.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/workflow/task.py tests/workflow/test_declared.py
git commit -m "feat(workflow): declare TASK, pinned to the shipped task.yaml"
```

---

### Task 2: Declare `INTEGRATE`

**Files:**
- Create: `src/agent_manager/workflow/integrate.py`
- Modify: `tests/workflow/test_declared.py` (full replacement shown below)

**Interfaces:**
- Consumes: from Task 1, `agent_manager.workflow.task.agent_timeout(minutes: int) -> timedelta`, `LAUNCHER_TIMEOUT`, `AGENT_TIMEOUT_FLOOR`; `agent_manager.steps.integrate.merge_completed_gate`; `agent_manager.steps.reducers.verification_passed_gate`; `agent_manager.steps.verify.run_suite`; `agent_manager.results.ResolveResult`; `phases.{AgentPhase, Retry, Step, Workflow}`.
- Produces: `agent_manager.workflow.integrate.INTEGRATE: Workflow` (name `"integrate"`, phases `resolve`, `verify`).

- [ ] **Step 1: Write the failing tests**

Replace the whole of `tests/workflow/test_declared.py` with:

```python
"""Pure-data tier (spec §9): TASK and INTEGRATE are constant phase-model data,
so these are plain unit tests -- no fakes, no git, no harness. The only I/O is
load_builtin's read of the shipped YAML and validate()'s role loading."""

import ast
from datetime import timedelta
from pathlib import Path

from agent_manager import dispatch
from agent_manager.workflow import integrate as integrate_module
from agent_manager.workflow import task as task_module
from agent_manager.workflow.loader import load_builtin
from agent_manager.workflow.phases import AgentPhase, Step, from_loader
from agent_manager.workflow.registry import default_registry
from agent_manager.workflow.task import TASK, LAUNCHER_TIMEOUT
from agent_manager.workflow.integrate import INTEGRATE


def _shipped(name, like):
    converted = from_loader(load_builtin(name, default_registry()))
    # timeouts are new data the YAML never had: compare with TASK's own
    return type(converted)(converted.name, tuple(
        p if not hasattr(p, "timeout") else type(p)(**{**p.__dict__, "timeout": like.phase(p.name).timeout})
        for p in converted.phases))


def _assert_same_callables(declared, name):
    """Identity, not just qualname: every callable is the object the registry binds."""
    converted = from_loader(load_builtin(name, default_registry()))
    assert declared.phase_names == converted.phase_names
    for mine, theirs in zip(declared.phases, converted.phases):
        assert type(mine) is type(theirs), mine.name
        assert len(mine.gates) == len(theirs.gates), mine.name
        for ours, registered in zip(mine.gates, theirs.gates):
            assert ours is registered, mine.name
        if isinstance(mine, Step):
            assert mine.run is theirs.run, mine.name
            assert mine.when is theirs.when, mine.name
        else:
            assert mine.result is theirs.result, mine.name


def _imported_modules(module):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_task_equals_the_shipped_yaml():
    assert TASK.digest() == _shipped("task", TASK).digest()


def test_integrate_equals_the_shipped_yaml():
    assert INTEGRATE.digest() == _shipped("integrate", INTEGRATE).digest()


def test_both_validate():
    TASK.validate(launcher_timeout=LAUNCHER_TIMEOUT)
    INTEGRATE.validate(launcher_timeout=LAUNCHER_TIMEOUT)


def test_task_callables_are_the_registry_bindings():
    _assert_same_callables(TASK, "task")


def test_integrate_callables_are_the_registry_bindings():
    _assert_same_callables(INTEGRATE, "integrate")


def test_launcher_timeout_is_the_dispatch_default():
    assert LAUNCHER_TIMEOUT == timedelta(seconds=dispatch.DEFAULT_TIMEOUT)
    assert LAUNCHER_TIMEOUT == timedelta(minutes=30)


def test_task_timeouts_are_the_chosen_values_floored_above_the_launcher():
    timeouts = {p.name: p.timeout for p in TASK.phases if isinstance(p, AgentPhase)}
    assert timeouts == {
        "explore": timedelta(minutes=35),
        "spec": timedelta(minutes=35),
        "validate_spec": timedelta(minutes=35),
        "plan": timedelta(minutes=35),
        "validate_plan": timedelta(minutes=35),
        "implement": timedelta(minutes=90),
        "review": timedelta(minutes=45),
    }
    assert task_module.AGENT_TIMEOUT_FLOOR == LAUNCHER_TIMEOUT + timedelta(minutes=5)
    assert task_module.agent_timeout(20) == task_module.AGENT_TIMEOUT_FLOOR
    assert task_module.agent_timeout(90) == timedelta(minutes=90)


def test_integrate_timeout_is_floored_above_the_launcher():
    timeouts = {p.name: p.timeout for p in INTEGRATE.phases if isinstance(p, AgentPhase)}
    assert timeouts == {"resolve": timedelta(minutes=35)}


def test_integrate_reuses_the_task_timeout_floor():
    source = Path(integrate_module.__file__).read_text(encoding="utf-8")
    assert "agent_manager.workflow.task" in _imported_modules(integrate_module)
    assert "DEFAULT_TIMEOUT" not in source
    assert "LAUNCHER_TIMEOUT =" not in source
    assert "AGENT_TIMEOUT_FLOOR =" not in source


def test_declared_modules_never_import_pygents():
    for module in (task_module, integrate_module):
        imported = _imported_modules(module)
        assert not any(n == "pygents" or n.startswith("pygents.") for n in imported), module.__name__
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_declared.py -v`
Expected: collection ERROR with `ModuleNotFoundError: No module named 'agent_manager.workflow.integrate'`.

- [ ] **Step 3: Write the implementation**

Create `src/agent_manager/workflow/integrate.py`:

```python
"""The builtin `integrate` workflow as declared phase-model data (spec G3).

Pinned to `builtin/integrate.yaml` by `tests/workflow/test_declared.py`, the
same way `workflow.task.TASK` is pinned to `task.yaml`. The timeout floor is
`workflow.task`'s -- one launcher timeout for every declared workflow.

`merge_completed_gate` lives in `steps/integrate.py`; that module is imported
as `integrate_steps` so it is never confused with this one.

No pygents import here (rule 1).
"""

from __future__ import annotations

from agent_manager import results
from agent_manager.steps import integrate as integrate_steps
from agent_manager.steps import reducers, verify
from agent_manager.workflow.phases import AgentPhase, Retry, Step, Workflow
from agent_manager.workflow.task import agent_timeout

INTEGRATE = Workflow("integrate", (
    AgentPhase(
        "resolve",
        role="resolver",
        inputs=("branch", "base_branch", "merge_tip", "conflict_files", "verification"),
        result=results.ResolveResult,
        gates=(integrate_steps.merge_completed_gate,),
        retry=Retry(2, ("gate_failed", "schema_invalid")),
        timeout=agent_timeout(30),
    ),
    Step("verify", verify.run_suite, gates=(reducers.verification_passed_gate,)),
))
"""`builtin/integrate.yaml`, phase for phase."""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_declared.py -v`
Expected: 10 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all green, including `tests/e2e`.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/workflow/integrate.py tests/workflow/test_declared.py
git commit -m "feat(workflow): declare INTEGRATE, pinned to the shipped integrate.yaml"
```
