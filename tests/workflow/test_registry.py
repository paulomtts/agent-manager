"""Pure-functions tier (design spec §14): no filesystem, no network, no git."""

import pytest

from agent_manager.workflow.registry import (
    DuplicateFunctionError,
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
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
