"""Behaviour of the phase walk and deterministic phase execution (spec §6, §9, §12).

Engine tier per design §14 lines 477-492: canned fake functions in a hand-built
`FunctionRegistry` stand in for §14's fake adapter with canned result files, and
the store is a real temp SQLite projection plus a real temp JSONL journal. No
git, no `brd`, no harness process, nothing from `default_registry()` -- three of
its names are placeholders that raise `NotImplementedError`.
"""

from pathlib import Path
from typing import Any

import pytest

from agent_manager import engine, models

REPO = Path("/repo")


def _subtask(card: str = "ed77a917") -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card,
        branch=f"m1/task-{card}",
        base_branch="m1/story-base",
        status="started",
        worktree_path=Path(f"/repo/.claude/worktrees/m1/task-{card}"),
    )


def test_subtask_context_renames_the_model_fields_the_steps_ask_for():
    context = engine.subtask_context(_subtask(), REPO, ["uv run pytest"])

    assert context == {
        "card": "ed77a917",
        "branch": "m1/task-ed77a917",
        "base": "m1/story-base",
        "worktree": Path("/repo/.claude/worktrees/m1/task-ed77a917"),
        "repo_dir": REPO,
        "commands": ["uv run pytest"],
    }


def test_bind_arguments_passes_only_the_parameters_the_callable_declares():
    def step(branch: str, repo_dir: Path) -> dict[str, Any]:
        return {"branch": branch, "repo_dir": repo_dir}

    bound = engine.bind_arguments(
        step,
        engine.subtask_context(_subtask(), REPO, ["uv run pytest"]),
        phase="worktree",
        function="worktree.ensure",
    )

    assert bound == {"branch": "m1/task-ed77a917", "repo_dir": REPO}


def test_bind_arguments_lets_declared_args_override_the_context():
    def step(card: str, status: str) -> dict[str, Any]:
        return {"card": card, "status": status}

    bound = engine.bind_arguments(
        step,
        {"card": "ed77a917", "status": "from-context"},
        {"status": "in_progress"},
        phase="mark_in_progress",
        function="rollup.set_status",
    )

    assert bound == {"card": "ed77a917", "status": "in_progress"}


def test_bind_arguments_skips_optional_parameters_nothing_supplies():
    def step(card: str, plans_dir: str | None = None) -> dict[str, Any]:
        return {"card": card, "plans_dir": plans_dir}

    bound = engine.bind_arguments(
        step, {"card": "ed77a917"}, phase="plan_check", function="plan_check.find"
    )

    assert bound == {"card": "ed77a917"}


def test_bind_arguments_names_phase_function_and_parameter_for_a_missing_required():
    called: list[str] = []

    def step(card: str, commands: list[str]) -> dict[str, Any]:
        called.append(card)
        return {}

    with pytest.raises(engine.EngineError) as caught:
        engine.bind_arguments(
            step, {"card": "ed77a917"}, phase="verify", function="verify.run_suite"
        )

    assert called == []
    assert caught.value.phase == "verify"
    assert caught.value.function == "verify.run_suite"
    assert caught.value.parameter == "commands"
    message = str(caught.value)
    assert "'verify'" in message
    assert "'verify.run_suite'" in message
    assert "'commands'" in message


def test_bind_arguments_refuses_an_args_key_the_callable_does_not_declare():
    def step(card: str) -> dict[str, Any]:
        return {}

    with pytest.raises(engine.EngineError) as caught:
        engine.bind_arguments(
            step,
            {"card": "ed77a917"},
            {"stauts": "done"},
            phase="mark_done",
            function="rollup.set_status",
        )

    assert caught.value.parameter == "stauts"
    assert "does not take" in str(caught.value)


def test_bind_arguments_refuses_a_positional_only_parameter():
    def step(card: str, /) -> dict[str, Any]:
        return {}

    with pytest.raises(engine.EngineError) as caught:
        engine.bind_arguments(
            step, {"card": "ed77a917"}, phase="plan_check", function="plan_check.find"
        )

    assert caught.value.parameter == "card"
    assert "positional-only" in str(caught.value)
