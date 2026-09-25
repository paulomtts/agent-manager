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
