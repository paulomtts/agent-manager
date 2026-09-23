"""Pure-functions tier (design spec §14): reads one packaged YAML file and
resolves names against the default registry. Nothing is executed."""

import pytest

from agent_manager.workflow import load_builtin
from agent_manager.workflow.loader import (
    AgentPhase,
    DeterministicPhase,
    builtin_path,
)
from agent_manager.workflow.registry import WorkflowLoadError, default_registry

# Design spec lines 146-225, in file order.
EXPECTED_PHASES = (
    ("explore", "agent"),
    ("mark_in_progress", "deterministic"),
    ("worktree", "deterministic"),
    ("plan_check", "deterministic"),
    ("spec", "agent"),
    ("validate_spec", "agent"),
    ("plan", "agent"),
    ("validate_plan", "agent"),
    ("implement", "agent"),
    ("review", "agent"),
    ("verify", "deterministic"),
    ("mark_done", "deterministic"),
)


def test_builtin_task_loads_against_the_default_registry() -> None:
    """The regression guard for the whole subtask: every name resolves."""
    workflow = load_builtin("task")
    assert workflow.name == "task"
    assert workflow.description


def test_builtin_task_has_the_twelve_phases_in_spec_order() -> None:
    workflow = load_builtin("task")
    assert tuple((phase.name, phase.kind) for phase in workflow.phases) == EXPECTED_PHASES


def test_plan_check_skips_forward_to_implement_when_a_plan_exists() -> None:
    phase = load_builtin("task").phase("plan_check")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "plan_check.find_validated_plan"
    assert phase.when == "plan_check.has_validated_plan"
    assert phase.skip_to == "implement"


@pytest.mark.parametrize(
    ("phase_name", "status"),
    [("mark_in_progress", "in_progress"), ("mark_done", "done")],
)
def test_the_rollup_phases_are_best_effort_with_their_status(
    phase_name: str, status: str
) -> None:
    phase = load_builtin("task").phase(phase_name)
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "rollup.set_status"
    assert phase.args == {"status": status}
    assert phase.best_effort is True


def test_explore_carries_both_gates_and_its_retry_policy() -> None:
    phase = load_builtin("task").phase("explore")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "explorer"
    assert phase.inputs == ["card", "parent_story", "repo_docs", "verification"]
    assert phase.result == "ExploreResult"
    assert phase.gates == ["exploration_output_gate", "verification_gate"]
    assert phase.retry is not None
    assert phase.retry.max_attempts == 2
    assert phase.retry.on == ["schema_invalid", "gate_failed"]


def test_review_carries_both_of_its_gates() -> None:
    phase = load_builtin("task").phase("review")
    assert isinstance(phase, AgentPhase)
    assert phase.gates == ["review_gate", "plan_hash_gate"]
    assert phase.inputs == ["branch", "base_branch", "plan_path"]


def test_every_resolved_function_is_the_registry_binding() -> None:
    workflow = load_builtin("task")
    registry = default_registry()
    assert sorted(workflow.functions) == sorted(registry.names())
    for name, fn in workflow.functions.items():
        assert fn is registry.resolve(name)


def test_an_unknown_builtin_name_raises() -> None:
    with pytest.raises(WorkflowLoadError) as caught:
        load_builtin("milestone")
    assert "milestone" in str(caught.value)


@pytest.mark.parametrize("name", ["../../etc/passwd", "a/b", "", ".", "..", "task/"])
def test_a_traversing_builtin_name_is_refused_before_any_read(name: str) -> None:
    """Review focus: nothing outside `workflow/builtin/` is ever opened."""
    with pytest.raises(WorkflowLoadError):
        builtin_path(name)


def test_builtin_path_stays_inside_the_builtin_directory() -> None:
    path = builtin_path("task")
    assert path.name == "task.yaml"
    assert path.parent.name == "builtin"
    assert path.is_file()
