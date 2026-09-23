"""Pure-functions tier (design spec §14): no network, no git, no brd board; the
only filesystem touch is reading a YAML file from `tmp_path`."""

from pathlib import Path

import pytest

from agent_manager.workflow.loader import (
    AgentPhase,
    DeterministicPhase,
    Workflow,
    load_workflow,
)
from agent_manager.workflow.registry import (
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
)


def fake_run(**kwargs: object) -> dict[str, object]:
    return {"ran": True}


def fake_when(result: object) -> bool:
    return True


def fake_gate(result: object) -> None:
    return None


def registry_with(*names: str) -> FunctionRegistry:
    """A registry binding every `name` to one of the three fakes above."""
    registry = FunctionRegistry()
    for name in names:
        if name.endswith("_gate"):
            registry.register(name, fake_gate)
        elif name.startswith("when"):
            registry.register(name, fake_when)
        else:
            registry.register(name, fake_run)
    return registry


MINIMAL = """
name: demo
description: two phases, one of each kind.
phases:
  - name: first
    kind: deterministic
    run: demo.run
  - name: second
    kind: agent
    role: coder
"""


def test_minimal_document_loads_with_file_order_preserved() -> None:
    workflow = load_workflow(MINIMAL, registry_with("demo.run"))

    assert isinstance(workflow, Workflow)
    assert workflow.name == "demo"
    assert workflow.description == "two phases, one of each kind."
    assert workflow.phase_names == ("first", "second")
    assert isinstance(workflow.phases[0], DeterministicPhase)
    assert isinstance(workflow.phases[1], AgentPhase)
    assert workflow.phase("second").role == "coder"


def test_deterministic_phase_exposes_its_resolved_run_args_and_best_effort() -> None:
    document = """
name: demo
phases:
  - name: mark_in_progress
    kind: deterministic
    run: rollup.set_status
    args: { status: in_progress }
    best_effort: true
"""
    workflow = load_workflow(document, registry_with("rollup.set_status"))

    phase = workflow.phase("mark_in_progress")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "rollup.set_status"
    assert workflow.function(phase.run) is fake_run
    assert phase.args == {"status": "in_progress"}
    assert phase.best_effort is True
    assert phase.when is None
    assert phase.skip_to is None
    assert phase.gates == []


def test_agent_phase_exposes_role_inputs_result_writes_gates_and_retry() -> None:
    document = """
name: demo
phases:
  - name: explore
    kind: agent
    role: explorer
    inputs: [card, parent_story]
    result: ExploreResult
    writes: docs/superpowers/specs/{stem}.md
    gates: [exploration_output_gate, verification_gate]
    retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }
"""
    workflow = load_workflow(
        document, registry_with("exploration_output_gate", "verification_gate")
    )

    phase = workflow.phase("explore")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "explorer"
    assert phase.inputs == ["card", "parent_story"]
    assert phase.result == "ExploreResult"
    assert phase.writes == "docs/superpowers/specs/{stem}.md"
    assert phase.gates == ["exploration_output_gate", "verification_gate"]
    assert phase.retry is not None
    assert phase.retry.max_attempts == 2
    assert phase.retry.on == ["schema_invalid", "gate_failed"]
    assert workflow.function("exploration_output_gate") is fake_gate


def test_the_bare_word_on_survives_as_a_string_key_not_a_bool() -> None:
    """Review focus: PyYAML's YAML-1.1 resolver reads a bare `on` as `True`.

    `retry.on` is spelled exactly this way in the design spec and in
    `builtin/task.yaml`; if this regresses, every retry-bearing document,
    including the shipped one, fails validation with "Field required"."""
    document = """
name: demo
phases:
  - name: explore
    kind: agent
    role: explorer
    retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }
"""
    workflow = load_workflow(document, registry_with())
    assert workflow.phase("explore").retry.on == ["schema_invalid", "gate_failed"]


def test_when_and_skip_to_resolve_and_survive_the_round_trip() -> None:
    document = """
name: demo
phases:
  - name: plan_check
    kind: deterministic
    run: demo.run
    skip_to: implement
    when: when.has_plan
  - name: implement
    kind: agent
    role: coder
"""
    workflow = load_workflow(document, registry_with("demo.run", "when.has_plan"))

    phase = workflow.phase("plan_check")
    assert phase.when == "when.has_plan"
    assert phase.skip_to == "implement"
    assert workflow.function("when.has_plan") is fake_when


def test_a_path_argument_is_read_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text(MINIMAL, encoding="utf-8")

    workflow = load_workflow(path, registry_with("demo.run"))

    assert workflow.phase_names == ("first", "second")


def test_a_missing_path_raises_a_workflow_load_error(tmp_path: Path) -> None:
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow(tmp_path / "absent.yaml", registry_with())
    assert "absent.yaml" in str(caught.value)


def test_a_string_that_looks_like_a_path_is_parsed_as_yaml_not_read() -> None:
    """Review focus: the Path/str split is on type, never on what a string
    looks like -- guessing would read a file the caller never named."""
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow("src/agent_manager/workflow/builtin/task.yaml", registry_with())
    assert "mapping" in str(caught.value)


def test_a_phase_that_does_not_exist_raises_rather_than_key_error() -> None:
    workflow = load_workflow(MINIMAL, registry_with("demo.run"))
    with pytest.raises(WorkflowLoadError):
        workflow.phase("nope")
    with pytest.raises(UnknownFunctionError):
        workflow.function("nope")
