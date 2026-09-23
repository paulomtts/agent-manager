"""Pure-functions tier (design spec §14): no filesystem, no network, no git."""

import pytest

from agent_manager.steps import plan_check, reducers, verify, worktree
from agent_manager.workflow.registry import (
    BUILTIN_FUNCTION_NAMES,
    DuplicateFunctionError,
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
    default_registry,
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
    "exploration_output_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
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


def test_default_registry_resolves_the_four_reducers_to_the_real_callables() -> None:
    registry = default_registry()
    assert registry.resolve("exploration_output_gate") is reducers.exploration_output_gate
    assert registry.resolve("verification_gate") is reducers.verification_gate
    assert registry.resolve("review_gate") is reducers.review_gate
    assert registry.resolve("plan_hash_gate") is reducers.plan_hash_gate
    assert registry.resolve("verification_passed_gate") is reducers.verification_passed_gate


def test_default_registry_resolves_implemented_steps_to_the_real_callables() -> None:
    """No placeholder may shadow a step that already exists on this branch."""
    registry = default_registry()
    assert registry.resolve("worktree.ensure") is worktree.ensure
    assert registry.resolve("verify.run_suite") is verify.run_suite
    assert registry.resolve("plan_check.find_validated_plan") is plan_check.find_validated_plan
    assert registry.resolve("plan_check.has_validated_plan") is plan_check.has_validated_plan


def test_placeholders_resolve_at_load_time_and_raise_when_called() -> None:
    registry = default_registry()
    for name in ("rollup.set_status", "critic_blockers_gate"):
        fn = registry.resolve(name)
        with pytest.raises(NotImplementedError) as caught:
            fn()
        assert name in str(caught.value)


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
