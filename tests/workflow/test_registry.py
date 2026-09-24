"""Pure-functions tier (design spec §14): no filesystem, no network, no git."""

from pathlib import Path

import pytest

from agent_manager import models
from agent_manager.engine import bind_arguments
from agent_manager.errors import EngineError
from agent_manager.steps import docs_commit, plan_check, reducers, rollup, verify, worktree
from agent_manager.workflow.registry import (
    BUILTIN_FUNCTION_NAMES,
    DuplicateFunctionError,
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
    default_registry,
    plan_hash_gate_adapter,
)


def _first() -> str:
    return "first"


def _second() -> str:
    return "second"


def test_register_then_resolve_returns_the_same_callable() -> None:
    registry = FunctionRegistry()
    registry.register("demo.fn", _first)
    assert registry.resolve("demo.fn") is _first


def test_resolve_unknown_name_raises_and_lists_registered_names() -> None:
    registry = FunctionRegistry()
    registry.register("demo.fn", _first)

    with pytest.raises(UnknownFunctionError) as caught:
        registry.resolve("demo.missing")

    message = str(caught.value)
    assert "demo.missing" in message
    assert "demo.fn" in message
    assert caught.value.unknown == ("demo.missing",)
    assert caught.value.registered == ("demo.fn",)


def test_unknown_function_error_is_a_workflow_load_error() -> None:
    registry = FunctionRegistry()
    with pytest.raises(WorkflowLoadError):
        registry.resolve("demo.missing")


def test_duplicate_register_raises_and_keeps_the_first_binding() -> None:
    registry = FunctionRegistry()
    registry.register("demo.fn", _first)

    with pytest.raises(DuplicateFunctionError) as caught:
        registry.register("demo.fn", _second)

    assert caught.value.name == "demo.fn"
    assert registry.resolve("demo.fn") is _first


def test_registering_a_non_callable_raises() -> None:
    registry = FunctionRegistry()
    with pytest.raises(WorkflowLoadError):
        registry.register("demo.fn", "exploration_output_gate")  # type: ignore[arg-type]


def test_names_are_sorted_and_membership_is_cheap() -> None:
    registry = FunctionRegistry()
    registry.register("b.fn", _first)
    registry.register("a.fn", _second)

    assert registry.names() == ("a.fn", "b.fn")
    assert "a.fn" in registry
    assert "c.fn" not in registry


# Every name that appears in a run/when/gate position of builtin/task.yaml
# (design spec lines 146-225).
TASK_YAML_NAMES = (
    "critic_blockers_gate",
    "docs_commit.commit_documents",
    "exploration_output_gate",
    "implement_blocked_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_check.mark_validated",
    "plan_hash_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)


def test_default_registry_holds_exactly_the_names_task_yaml_uses() -> None:
    assert default_registry().names() == TASK_YAML_NAMES
    assert BUILTIN_FUNCTION_NAMES == TASK_YAML_NAMES


def test_default_registry_resolves_the_ported_reducers_to_the_real_callables() -> None:
    registry = default_registry()
    assert registry.resolve("exploration_output_gate") is reducers.exploration_output_gate
    assert registry.resolve("verification_gate") is reducers.verification_gate
    assert registry.resolve("review_gate") is reducers.review_gate
    # The one name that is deliberately NOT the bare reducer: `plan_hash_gate`
    # compares two fields of two different phase results, and `bind_arguments`
    # binds whole values by parameter name only.
    assert registry.resolve("plan_hash_gate") is plan_hash_gate_adapter
    assert plan_hash_gate_adapter is not reducers.plan_hash_gate
    assert registry.resolve("verification_passed_gate") is reducers.verification_passed_gate
    assert registry.resolve("critic_blockers_gate") is reducers.critic_blockers_gate
    assert registry.resolve("implement_blocked_gate") is reducers.implement_blocked_gate


def test_default_registry_resolves_implemented_steps_to_the_real_callables() -> None:
    """No placeholder may shadow a step that already exists on this branch."""
    registry = default_registry()
    assert registry.resolve("worktree.ensure") is worktree.ensure
    assert registry.resolve("verify.run_suite") is verify.run_suite
    assert registry.resolve("plan_check.find_validated_plan") is plan_check.find_validated_plan
    assert registry.resolve("plan_check.has_validated_plan") is plan_check.has_validated_plan
    assert registry.resolve("plan_check.mark_validated") is plan_check.mark_validated
    assert registry.resolve("docs_commit.commit_documents") is docs_commit.commit_documents
    assert registry.resolve("rollup.set_status") is rollup.set_status


def test_the_engine_can_bind_mark_validated_out_of_the_subtask_context() -> None:
    """Review focus: the phase carries no `args:`, so both parameters have to
    come from the context by name -- `plan_path` from `engine._document_paths`
    and `worktree` from `engine.subtask_context`. If either name drifted, the
    phase would die at runtime while every unit test still passed."""
    bound = bind_arguments(
        plan_check.mark_validated,
        {
            "card": "a32af745",
            "worktree": Path("/repo/.claude/worktrees/m2/task-rows-a32af745"),
            "plan_path": "docs/superpowers/plans/task-rows-a32af745.md",
            "spec_path": "docs/superpowers/specs/task-rows-a32af745.md",
            "commands": ["uv run pytest"],
        },
        None,
        phase="mark_validated",
        function="plan_check.mark_validated",
    )

    assert bound == {
        "plan_path": "docs/superpowers/plans/task-rows-a32af745.md",
        "worktree": Path("/repo/.claude/worktrees/m2/task-rows-a32af745"),
    }


def test_the_engine_can_bind_the_docs_commit_step_out_of_the_subtask_context() -> None:
    """The phase carries no `args:`, so all four parameters have to come from the
    context by name -- `card_details` and `worktree` from
    `engine.subtask_context`, `spec_path` and `plan_path` from
    `engine._document_paths`. `git_runner` has a default and must NOT be bound
    out of a context that happens to hold no such key."""
    card = models.Card(
        id="6f1a2f2e-1f1c-4f0e-9a6d-0c2f3b4a5d6e",
        title="Commit the spec and plan with the Plan-Hash trailer",
        status="todo",
    )
    bound = bind_arguments(
        docs_commit.commit_documents,
        {
            "card": "ba15da20",
            "card_details": card,
            "worktree": Path("/repo/.claude/worktrees/m2/task-docs-ba15da20"),
            "plan_path": "docs/superpowers/plans/task-docs-ba15da20.md",
            "spec_path": "docs/superpowers/specs/task-docs-ba15da20.md",
            "commands": ["uv run pytest"],
        },
        None,
        phase="docs_commit",
        function="docs_commit.commit_documents",
    )

    assert bound == {
        "card_details": card,
        "spec_path": "docs/superpowers/specs/task-docs-ba15da20.md",
        "plan_path": "docs/superpowers/plans/task-docs-ba15da20.md",
        "worktree": Path("/repo/.claude/worktrees/m2/task-docs-ba15da20"),
    }


def test_the_engine_can_bind_the_documents_args_to_the_rollup_step() -> None:
    """The `card` vs `card_id` trap: the context key is `card`, a bare id."""
    bound = bind_arguments(
        rollup.set_status,
        {
            "card": "43008688",
            "card_details": None,
            "branch": "m2/task-implement-the-rollup-43008688",
            "repo_dir": Path("/repo"),
        },
        {"status": "done"},
        phase="mark_done",
        function="rollup.set_status",
    )
    assert bound == {
        "card": "43008688",
        "status": "done",
        "repo_dir": Path("/repo"),
    }


def test_binding_rejects_an_args_key_the_rollup_step_does_not_take() -> None:
    """A document that wrote `args: { card_id: ... }` must fail loudly."""
    with pytest.raises(EngineError):
        bind_arguments(
            rollup.set_status,
            {"card": "43008688", "repo_dir": Path("/repo")},
            {"card_id": "43008688", "status": "done"},
            phase="mark_done",
            function="rollup.set_status",
        )


def test_the_critic_gate_is_real_code_now_rather_than_a_placeholder() -> None:
    gate = default_registry().resolve("critic_blockers_gate")
    assert gate({"blockers": False, "reason": None, "summary": "explored"}) is None
    assert gate(None) == {
        "blocked": "validation",
        "detail": "the validator returned nothing",
    }


def test_default_registry_does_not_register_shell_quote() -> None:
    """`shell_quote` does not port (design spec lines 252-253)."""
    registry = default_registry()
    assert "shell_quote" not in registry
    with pytest.raises(UnknownFunctionError):
        registry.resolve("shell_quote")


def test_default_registry_returns_an_independent_registry_each_call() -> None:
    """Review focus: a shared singleton would be poisonable by any caller."""
    first = default_registry()
    first.register("test.only", _first)

    second = default_registry()
    assert "test.only" not in second
    assert second.names() == TASK_YAML_NAMES


def test_the_plan_hash_adapter_is_one_object_across_two_registries() -> None:
    """The same invariant `_PLACEHOLDERS` exists for: a closure built per call
    would make this the only name whose identity is unstable."""
    assert default_registry().resolve("plan_hash_gate") is default_registry().resolve(
        "plan_hash_gate"
    )


def test_the_plan_hash_adapter_compares_the_two_phases_plan_hash_fields() -> None:
    gate = plan_hash_gate_adapter(
        {"plan_hash": "a1b2c3d4", "report": "done"},
        {"plan_hash": "ffffffff", "porcelain": ""},
    )
    assert "plan hash CHANGED mid-run" in gate["detail"]
    assert "a1b2c3d4" in gate["detail"] and "ffffffff" in gate["detail"]
    assert "blocked" not in gate


def test_the_plan_hash_adapter_passes_when_the_two_hashes_match() -> None:
    assert (
        plan_hash_gate_adapter({"plan_hash": "a1b2c3d4"}, {"plan_hash": "a1b2c3d4"})
        is None
    )


@pytest.mark.parametrize("dead", [None, {}, "implement", 7, [{"plan_hash": "a1b2c3d4"}]])
def test_the_plan_hash_adapter_passes_when_either_phase_result_is_missing(dead) -> None:
    # A skipped or dead phase has no hash to compare; the reducer's own rule is
    # "nothing trustworthy to say" -> None.
    assert plan_hash_gate_adapter(dead, {"plan_hash": "a1b2c3d4"}) is None
    assert plan_hash_gate_adapter({"plan_hash": "a1b2c3d4"}, dead) is None


def test_the_plan_hash_adapter_binds_with_no_arguments_at_all() -> None:
    """Both parameters default to None so a run that skipped `implement` binds
    and passes, instead of `bind_arguments` reporting a required parameter."""
    assert plan_hash_gate_adapter() is None
