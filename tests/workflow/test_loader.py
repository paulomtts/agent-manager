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


def test_unknown_run_name_fails_at_load_time_naming_the_phase() -> None:
    document = """
name: demo
phases:
  - name: worktree
    kind: deterministic
    run: worktree.ensure
"""
    with pytest.raises(UnknownFunctionError) as caught:
        load_workflow(document, registry_with("demo.run"))

    message = str(caught.value)
    # Substring checks on bare words like "run" or "worktree" would be satisfied
    # by the registered name "demo.run" and by "worktree.ensure" itself, so the
    # phase and the position are pinned as whole fragments and as attributes.
    assert "phase 'worktree' run 'worktree.ensure'" in message
    assert "demo.run" in message  # what WAS registered
    assert caught.value.phase == "worktree"
    assert caught.value.field == "run"
    assert caught.value.unknown == ("worktree.ensure",)


def test_unknown_when_name_fails_at_load_time() -> None:
    document = """
name: demo
phases:
  - name: plan_check
    kind: deterministic
    run: demo.run
    when: plan_check.has_validated_plan
    skip_to: implement
  - name: implement
    kind: agent
    role: coder
"""
    with pytest.raises(UnknownFunctionError) as caught:
        load_workflow(document, registry_with("demo.run"))

    assert caught.value.unknown == ("plan_check.has_validated_plan",)
    assert "phase 'plan_check' when 'plan_check.has_validated_plan'" in str(caught.value)
    assert caught.value.phase == "plan_check"
    assert caught.value.field == "when"


def test_unknown_gate_name_fails_at_load_time() -> None:
    document = """
name: demo
phases:
  - name: review
    kind: agent
    role: reviewer
    gates: [review_gate, plan_hash_gate]
"""
    with pytest.raises(UnknownFunctionError) as caught:
        load_workflow(document, registry_with("review_gate"))

    assert caught.value.unknown == ("plan_hash_gate",)
    assert "phase 'review' gate 'plan_hash_gate'" in str(caught.value)
    assert caught.value.phase == "review"
    assert caught.value.field == "gate"


def test_every_unknown_name_is_reported_in_one_error() -> None:
    document = """
name: demo
phases:
  - name: first
    kind: deterministic
    run: missing.run
    when: missing.when
  - name: second
    kind: agent
    role: coder
    gates: [missing_gate, missing_gate]
"""
    with pytest.raises(UnknownFunctionError) as caught:
        load_workflow(document, registry_with())

    assert caught.value.unknown == ("missing.run", "missing.when", "missing_gate")
    message = str(caught.value)
    assert "phase 'first' run 'missing.run'" in message
    assert "phase 'first' when 'missing.when'" in message
    assert "phase 'second' gate 'missing_gate'" in message
    assert caught.value.workflow == "demo"


def test_resolution_happens_before_any_function_is_called() -> None:
    """The invariant: nothing the document names runs during a load."""
    calls: list[str] = []

    def spy(*args: object, **kwargs: object) -> None:
        calls.append("called")

    registry = FunctionRegistry()
    registry.register("demo.run", spy)
    load_workflow(MINIMAL, registry)

    assert calls == []


def test_malformed_yaml_raises_a_workflow_load_error_not_a_yaml_error() -> None:
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow("name: demo\nphases: [ - broken", registry_with())
    assert "not valid YAML" in str(caught.value)


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("phases:\n  - name: a\n    kind: agent\n    role: coder\n", id="no_name"),
        pytest.param("name: demo\n", id="no_phases"),
        pytest.param("name: demo\nphases: []\n", id="empty_phases"),
        pytest.param("name: demo\nphases: not-a-list\n", id="phases_not_a_list"),
        pytest.param("- name: demo\n", id="top_level_list"),
        pytest.param("just a string\n", id="top_level_scalar"),
        pytest.param("", id="empty_document"),
        pytest.param("# only a comment\n", id="comment_only"),
    ],
)
def test_structurally_broken_documents_raise(document: str) -> None:
    """The last three are the review-focus case: safe_load returns None."""
    with pytest.raises(WorkflowLoadError):
        load_workflow(document, registry_with())


@pytest.mark.parametrize(
    "phase_body",
    [
        pytest.param("kind: wizard\n    run: demo.run", id="unknown_kind"),
        pytest.param("run: demo.run", id="no_kind"),
        pytest.param("kind: deterministic", id="deterministic_without_run"),
        pytest.param("kind: agent", id="agent_without_role"),
        pytest.param("kind: agent\n    role: coder\n    run: demo.run", id="run_on_agent"),
        pytest.param("kind: agent\n    role: coder\n    args: { a: 1 }", id="args_on_agent"),
        pytest.param(
            "kind: agent\n    role: coder\n    best_effort: true", id="best_effort_on_agent"
        ),
        pytest.param("kind: deterministic\n    run: demo.run\n    role: coder", id="role_on_det"),
        pytest.param(
            "kind: deterministic\n    run: demo.run\n    inputs: [card]", id="inputs_on_det"
        ),
        pytest.param(
            "kind: deterministic\n    run: demo.run\n    result: PlanResult", id="result_on_det"
        ),
        pytest.param(
            "kind: deterministic\n    run: demo.run\n    writes: docs/x.md", id="writes_on_det"
        ),
        pytest.param(
            "kind: deterministic\n    run: demo.run\n    retry: { max_attempts: 2, on: [gate_failed] }",
            id="retry_on_det",
        ),
        pytest.param("kind: deterministic\n    run: demo.run\n    typo: 1", id="unknown_field"),
        pytest.param(
            "kind: agent\n    role: coder\n    gates: exploration_output_gate", id="gates_bare_str"
        ),
        pytest.param(
            "kind: agent\n    role: coder\n    retry: { max_attempts: 0, on: [gate_failed] }",
            id="zero_attempts",
        ),
        pytest.param(
            "kind: agent\n    role: coder\n    retry: { max_attempts: 2, on: [harness_error] }",
            id="unretryable_outcome",
        ),
        pytest.param(
            "kind: agent\n    role: coder\n    retry: { max_attempts: 2, on: [] }",
            id="empty_retry_on",
        ),
    ],
)
def test_broken_phases_raise_with_the_phase_named(phase_body: str) -> None:
    """`gates_bare_str` is the review-focus case: a bare string must be
    rejected, not iterated character by character."""
    document = f"name: demo\nphases:\n  - name: broken\n    {phase_body}\n"
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow(document, registry_with("demo.run", "exploration_output_gate"))
    assert caught.value.phase == "broken"
    assert "broken" in str(caught.value)


def test_duplicate_phase_name_raises() -> None:
    document = """
name: demo
phases:
  - name: twice
    kind: deterministic
    run: demo.run
  - name: twice
    kind: agent
    role: coder
"""
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow(document, registry_with("demo.run"))
    assert caught.value.phase == "twice"
    assert "duplicate" in str(caught.value)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        pytest.param("nowhere", "not a phase", id="unknown"),
        pytest.param("second", "itself", id="self"),
        pytest.param("first", "earlier", id="backwards"),
    ],
)
def test_bad_skip_to_raises(target: str, expected: str) -> None:
    document = f"""
name: demo
phases:
  - name: first
    kind: deterministic
    run: demo.run
  - name: second
    kind: deterministic
    run: demo.run
    skip_to: {target}
"""
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow(document, registry_with("demo.run"))
    assert caught.value.phase == "second"
    assert caught.value.field == "skip_to"
    assert expected in str(caught.value)

