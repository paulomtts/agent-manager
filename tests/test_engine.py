"""Behaviour of the subtask walk and deterministic phase execution (spec §6, §9, §12).

Engine tier per design §14 lines 477-492: canned fake callables in declared
phase-model workflows stand in for §14's fake adapter with canned result
files, and the store is a real temp SQLite projection plus a real temp JSONL
journal. No git, no `brd`, no harness process. Every walk is
`runtime.engine.run_subtask`, and each workflow below is the declared twin of
the YAML document the walk was first specified against, phase for phase.
"""

import asyncio
import dataclasses
import json
import threading
import typing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import dispatch, models, results
from agent_manager.store import db as store_db
from agent_manager.store import writer as store_writer
from agent_manager.runtime import walk
from agent_manager.errors import AgentPhaseFailed
from agent_manager.harness.base import Outcome
from agent_manager.runtime import bridge
from agent_manager.runtime import engine as new_engine
from agent_manager.errors import LimitWaitInterrupted
from agent_manager.runtime.stop import StopSignal
from agent_manager.steps import (
    docs_commit,
    integrate,
    plan_check,
    reducers,
    rollup,
    verify,
    worktree,
)
from agent_manager.workflow import phases as phase_model
from agent_manager.workflow import task as task_workflow

REPO = Path("/repo")


def _subtask(card: str = "ed77a917") -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card,
        branch=f"m1/task-{card}",
        base_branch="m1/story-base",
        status="started",
        worktree_path=Path(f"/repo/.claude/worktrees/m1/task-{card}"),
    )


CARD = models.Card(
    id="968fba15-0971-456a-ae9f-57ff2210f0ce",
    title="Resolve phase inputs",
    status="todo",
    parent_id="2143808b-b236-4cf9-b172-53809bdbc1a1",
)
PARENT = models.Card(
    id="2143808b-b236-4cf9-b172-53809bdbc1a1",
    title="The workflow document and the engine",
    status="in_progress",
)
SPEC_PATH = "docs/superpowers/specs/resolve-phase-inputs-968fba15.md"
PLAN_PATH = "docs/superpowers/plans/resolve-phase-inputs-968fba15.md"


def test_subtask_context_renames_the_model_fields_the_steps_ask_for():
    context = walk.subtask_context(_subtask(), REPO, ["uv run pytest"])

    assert context == {
        "card": "ed77a917",
        "card_details": None,
        "parent_story_details": None,
        "branch": "m1/task-ed77a917",
        "base": "m1/story-base",
        "base_branch": "m1/story-base",
        "worktree": Path("/repo/.claude/worktrees/m1/task-ed77a917"),
        "repo_dir": REPO,
        "commands": ["uv run pytest"],
    }


def test_the_cards_land_under_the_details_keys_and_leave_the_id_string_alone():
    """§7's `card` input and the steps' `card` parameter are different things:
    `plan_check.find_validated_plan(card)` binds the bare id string, and turning
    that key into a `Card` would break every deterministic step at once.
    """
    context = walk.subtask_context(
        _subtask(), REPO, ["uv run pytest"], card=CARD, parent_story=PARENT
    )

    assert context["card"] == "ed77a917"
    assert context["card_details"] is CARD
    assert context["parent_story_details"] is PARENT


def test_the_new_context_keys_are_reserved_against_a_same_named_phase():
    for key in (
        "card_details",
        "parent_story_details",
        "spec_path",
        "plan_path",
        "base_branch",
    ):
        assert key in walk.RESERVED_CONTEXT_KEYS


def test_the_base_branch_alias_is_the_same_string_the_steps_bind_as_base():
    """`review_gate(review, branch, base_branch)` binds by parameter name, and
    the deterministic steps bind the same value as `base`. One value, two keys,
    rather than a second source of truth for the base branch."""
    context = walk.subtask_context(_subtask(), REPO)
    assert context["base_branch"] == context["base"] == "m1/story-base"


def test_bind_arguments_passes_only_the_parameters_the_callable_declares():
    def step(branch: str, repo_dir: Path) -> dict[str, Any]:
        return {"branch": branch, "repo_dir": repo_dir}

    bound = walk.bind_arguments(
        step,
        walk.subtask_context(_subtask(), REPO, ["uv run pytest"]),
        phase="worktree",
        function="worktree.ensure",
    )

    assert bound == {"branch": "m1/task-ed77a917", "repo_dir": REPO}


def test_bind_arguments_lets_declared_args_override_the_context():
    def step(card: str, status: str) -> dict[str, Any]:
        return {"card": card, "status": status}

    bound = walk.bind_arguments(
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

    bound = walk.bind_arguments(
        step, {"card": "ed77a917"}, phase="plan_check", function="plan_check.find"
    )

    assert bound == {"card": "ed77a917"}


def test_bind_arguments_names_phase_function_and_parameter_for_a_missing_required():
    called: list[str] = []

    def step(card: str, commands: list[str]) -> dict[str, Any]:
        called.append(card)
        return {}

    with pytest.raises(walk.EngineError) as caught:
        walk.bind_arguments(
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

    with pytest.raises(walk.EngineError) as caught:
        walk.bind_arguments(
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

    with pytest.raises(walk.EngineError) as caught:
        walk.bind_arguments(
            step, {"card": "ed77a917"}, phase="plan_check", function="plan_check.find"
        )

    assert caught.value.parameter == "card"
    assert "positional-only" in str(caught.value)


def test_bind_arguments_binds_the_merge_completed_gate_from_a_gate_table():
    """The gate takes `result` and `worktree` by name out of exactly the table
    `_gate_values` builds for a deterministic phase; `git_runner` is keyword-only
    with a default and must never be required or bound from the context."""
    result = {"resolved": True, "files": ["a.js"], "summary": "kept both sides"}
    context = walk.subtask_context(_subtask(), REPO, ["uv run pytest"])
    values = walk._gate_values(context, "merge", result)

    bound = walk.bind_arguments(
        integrate.merge_completed_gate,
        values,
        phase="merge",
        function="merge_completed_gate",
    )

    assert bound == {
        "result": result,
        "worktree": Path("/repo/.claude/worktrees/m1/task-ed77a917"),
    }


def test_the_engine_can_bind_mark_validated_out_of_the_subtask_context():
    """The phase carries no `args`, so both parameters have to come from the
    context by name -- `plan_path` from `walk._document_paths` and `worktree`
    from `walk.subtask_context`. If either name drifted, the phase would die
    at runtime while every unit test still passed."""
    bound = walk.bind_arguments(
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


def test_the_engine_can_bind_the_docs_commit_step_out_of_the_subtask_context():
    """The phase carries no `args`, so all five parameters have to come from the
    context by name -- `card_details`, `worktree` and `base_branch` from
    `walk.subtask_context`, `spec_path` and `plan_path` from
    `walk._document_paths`. `git_runner` has a default and must NOT be bound
    out of a context that happens to hold no such key."""
    card = models.Card(
        id="6f1a2f2e-1f1c-4f0e-9a6d-0c2f3b4a5d6e",
        title="Commit the spec and plan with the Plan-Hash trailer",
        status="todo",
    )
    bound = walk.bind_arguments(
        docs_commit.commit_documents,
        {
            "card": "ba15da20",
            "card_details": card,
            "worktree": Path("/repo/.claude/worktrees/m2/task-docs-ba15da20"),
            "base_branch": "m2/story-docs",
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
        "base_branch": "m2/story-docs",
    }


def test_the_engine_can_bind_the_workflows_args_to_the_rollup_step():
    """The `card` vs `card_id` trap: the context key is `card`, a bare id."""
    bound = walk.bind_arguments(
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


def test_binding_rejects_an_args_key_the_rollup_step_does_not_take():
    """A step declared with `args={"card_id": ...}` must fail loudly."""
    with pytest.raises(walk.EngineError):
        walk.bind_arguments(
            rollup.set_status,
            {"card": "43008688", "repo_dir": Path("/repo")},
            {"card_id": "43008688", "status": "done"},
            phase="mark_done",
            function="rollup.set_status",
        )


RUN_ID = "run-2026-09-23-01"
STORY_ID = "2143808b"


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A real temp projection plus a real temp journal, writing nowhere real."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_writer.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


@pytest.fixture
def run_subtask():
    """`runtime.engine.run_subtask`, the one walk (pygents-engine design G7)."""
    return new_engine.run_subtask


Step = phase_model.Step


def _agent(name, role, *, inputs=(), result=None, writes=None) -> phase_model.AgentPhase:
    """A bare declared agent phase: no gates, no retry, the thirty-minute
    default timeout."""
    return phase_model.AgentPhase(
        name, role=role, inputs=tuple(inputs), result=result, writes=writes
    )


def _workflow(document, functions: dict[str, Any]) -> phase_model.Workflow:
    """`document` built over `functions`, keyed by the names its steps call."""
    return document(functions)


def _journalled_phases(opened) -> list[tuple[str | None, str]]:
    return [
        (line.phase, line.payload["status"])
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ]


def _projected_phases(opened) -> list[tuple[str, str]]:
    return [
        (row["name"], row["status"])
        for row in opened.connection.execute(
            "SELECT name, status FROM phases ORDER BY position"
        ).fetchall()
    ]


def THREE_PHASES(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("three", (
        Step("alpha", fn["step.alpha"]),
        Step("beta", fn["step.beta"]),
        Step("gamma", fn["step.gamma"]),
    ))


def ONE_PHASE(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("one", (Step("alpha", fn["step.alpha"]),))


def TWO_PHASES(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("two", (
        Step("alpha", fn["step.alpha"]),
        Step("beta", fn["step.beta"]),
    ))


def test_phases_run_in_document_order(store, run_subtask):
    calls: list[str] = []

    def make(name: str):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            return {"phase": name}

        return step

    workflow = _workflow(
        THREE_PHASES,
        {"step.alpha": make("alpha"), "step.beta": make("beta"), "step.gamma": make("gamma")},
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["alpha", "beta", "gamma"]
    assert summary.status == "done"
    assert summary.results == {
        "alpha": {"phase": "alpha"},
        "beta": {"phase": "beta"},
        "gamma": {"phase": "gamma"},
    }
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done"), ("gamma", "done")]


def test_a_phase_result_is_bound_into_a_later_phase(store, run_subtask):
    seen: dict[str, Any] = {}

    def alpha(card: str) -> dict[str, Any]:
        return {"plan": "docs/plan.md"}

    def beta(alpha: dict[str, Any]) -> dict[str, Any]:
        seen["alpha"] = alpha
        return {}

    def gamma(card: str) -> dict[str, Any]:
        return {}

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": alpha, "step.beta": beta, "step.gamma": gamma}
    )

    run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen["alpha"] == {"plan": "docs/plan.md"}


def test_declared_args_reach_the_step_as_a_keyword(store, run_subtask):
    seen: list[str] = []

    def set_status(card: str, status: str) -> dict[str, Any]:
        seen.append(status)
        return {"status": status}

    def document(fn):
        return phase_model.Workflow("board", (
            Step("mark_in_progress", fn["rollup.set_status"], args={"status": "in_progress"}),
        ))

    workflow = _workflow(document, {"rollup.set_status": set_status})

    run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen == ["in_progress"]


def test_a_step_is_called_with_only_the_parameters_it_declares(store, run_subtask):
    seen: dict[str, Any] = {}

    def ensure(branch: str, base: str) -> dict[str, Any]:
        seen.update(branch=branch, base=base)
        return {}

    def document(fn):
        return phase_model.Workflow("one", (Step("worktree", fn["worktree.ensure"]),))

    workflow = _workflow(document, {"worktree.ensure": ensure})

    run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        commands=["uv run pytest"],
    )

    assert seen == {"branch": "m1/task-ed77a917", "base": "m1/story-base"}


def test_every_state_edge_is_journalled_before_the_row_is_written(store, run_subtask):
    def step(card: str) -> dict[str, Any]:
        return {}

    document = TWO_PHASES
    workflow = _workflow(document, {"step.alpha": step, "step.beta": step})

    run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert _journalled_phases(store) == [
        ("alpha", "started"),
        ("alpha", "done"),
        ("beta", "started"),
        ("beta", "done"),
    ]
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done")]
    assert [line.event for line in store.journal.read()][-1] == "subtask_upsert"
    assert store.journal.read()[-1].payload["status"] == "done"


def test_no_attempt_row_is_written_for_a_deterministic_phase(store, run_subtask):
    def step(card: str) -> dict[str, Any]:
        return {}

    document = ONE_PHASE
    workflow = _workflow(document, {"step.alpha": step})

    run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert store.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    assert not any(line.event == "attempt_upsert" for line in store.journal.read())


def test_a_phase_named_like_a_context_key_runs_but_never_clobbers_it(store, run_subtask):
    """The `worktree` phase of the shipped `TASK` names itself the
    same as the context key `subtask_context` binds the real worktree path
    under. It must still run and record normally; its own result must simply
    never overwrite the context key later phases bind `worktree` from, or the
    real `verify` phase would receive `{"created": True}` where it needs a
    filesystem path.
    """
    seen: dict[str, Any] = {}

    def make_worktree(card: str, worktree: Any) -> dict[str, Any]:
        return {"created": True, "original_worktree_arg": worktree}

    def uses_worktree(worktree: Any) -> dict[str, Any]:
        seen["worktree"] = worktree
        return {}

    def document(fn):
        return phase_model.Workflow("collide", (
            Step("worktree", fn["worktree.make"]),
            Step("after", fn["step.after"]),
        ))

    workflow = _workflow(
        document, {"worktree.make": make_worktree, "step.after": uses_worktree}
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    real_worktree = _subtask().worktree_path
    assert summary.status == "done"
    assert summary.results["worktree"] == {
        "created": True,
        "original_worktree_arg": real_worktree,
    }
    assert seen["worktree"] == real_worktree
    assert _projected_phases(store) == [("worktree", "done"), ("after", "done")]


def test_a_raising_step_escalates_and_the_exception_does_not_propagate(store, run_subtask):
    calls: list[str] = []

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        raise OSError("disk went away")

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        return {}

    document = TWO_PHASES
    workflow = _workflow(document, {"step.alpha": alpha, "step.beta": beta})

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["alpha"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "alpha"
    assert "disk went away" in summary.detail
    assert _journalled_phases(store) == [("alpha", "started"), ("alpha", "failed")]
    assert _projected_phases(store) == [("alpha", "failed")]
    assert store.journal.read()[-1].event == "subtask_upsert"
    assert store.journal.read()[-1].payload["status"] == "escalated"


def test_a_binding_failure_escalates_without_calling_the_step(store, run_subtask):
    calls: list[str] = []

    def alpha(card: str, missing_thing: str) -> dict[str, Any]:
        calls.append("alpha")
        return {}

    document = ONE_PHASE
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == []
    assert summary.status == "escalated"
    assert "missing_thing" in summary.detail


@pytest.mark.parametrize("returned", [None, ["a", "list"], True, "a string"])
def test_a_non_mapping_step_result_escalates(store, returned, run_subtask):
    def alpha(card: str) -> Any:
        return returned

    document = ONE_PHASE
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "alpha"
    assert "mapping" in summary.detail
    assert _projected_phases(store) == [("alpha", "failed")]


def test_a_failed_phase_result_is_not_offered_to_later_phases(store, run_subtask):
    def alpha(card: str) -> dict[str, Any]:
        raise RuntimeError("nope")

    document = ONE_PHASE
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.results == {}


def GATED(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("gated", (
        Step("alpha", fn["step.alpha"], gates=(fn["alpha_gate"],)),
        Step("beta", fn["step.beta"]),
    ))


def test_a_passing_gate_lets_the_walk_continue(store, run_subtask):
    seen: dict[str, Any] = {}

    def alpha(card: str) -> dict[str, Any]:
        return {"passed": True}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(result: dict[str, Any]) -> dict[str, str] | None:
        seen["result"] = result
        return None

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen["result"] == {"passed": True}
    assert summary.status == "done"
    assert summary.warnings == []
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done")]


def test_a_warning_gate_continues_and_surfaces_the_warning(store, run_subtask):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"warn": "the suite reported no tests"}

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"
    assert len(summary.warnings) == 1
    assert "alpha_gate" in summary.warnings[0]
    assert "the suite reported no tests" in summary.warnings[0]
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done")]


def test_a_failing_gate_escalates_and_stops_the_walk(store, run_subtask):
    calls: list[str] = []

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        return {"passed": False}

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        return {}

    def alpha_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"blocked": "verification", "detail": "2 of 3 commands failed"}

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["alpha"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "alpha"
    assert "alpha_gate" in summary.detail
    assert "2 of 3 commands failed" in summary.detail
    assert _journalled_phases(store) == [("alpha", "started"), ("alpha", "failed")]
    assert store.journal.read()[-1].payload["status"] == "escalated"


def test_a_warning_before_a_failing_gate_survives_into_the_summary(store, run_subtask):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def first_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"warn": "looked thin"}

    def second_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"blocked": "review", "detail": "unresolved blocker"}

    def document(fn):
        return phase_model.Workflow("gated", (
            Step("alpha", fn["step.alpha"], gates=(fn["first_gate"], fn["second_gate"])),
            Step("beta", fn["step.beta"]),
        ))

    workflow = _workflow(
        document,
        {
            "step.alpha": alpha,
            "step.beta": beta,
            "first_gate": first_gate,
            "second_gate": second_gate,
        },
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert any("looked thin" in warning for warning in summary.warnings)


@pytest.mark.parametrize("verdict", [False, True, "blocked", 0])
def test_a_gate_returning_neither_none_nor_a_mapping_escalates(store, verdict, run_subtask):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(result: dict[str, Any]) -> Any:
        return verdict

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert "mapping" in summary.detail


def test_a_gate_binds_the_phase_result_under_the_phase_name_too(store, run_subtask):
    seen: dict[str, Any] = {}

    def alpha(card: str) -> dict[str, Any]:
        return {"ok": 1}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(alpha: dict[str, Any], branch: str) -> None:
        seen.update(alpha=alpha, branch=branch)
        return None

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen == {"alpha": {"ok": 1}, "branch": "m1/task-ed77a917"}


def SKIPPING(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("skipping", (
        Step("plan_check", fn["plan_check.find"], when=fn["plan_check.has"], skip_to="implement"),
        Step("spec", fn["step.spec"]),
        Step("plan", fn["step.plan"]),
        Step("implement", fn["step.implement"]),
    ))


def _skipping_workflow(has: Any, calls: list[str]):
    def find(card: str) -> dict[str, Any]:
        calls.append("plan_check")
        return {"found": True, "validated": True}

    def make(name: str):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            return {}

        return step

    return _workflow(
        SKIPPING,
        {
            "plan_check.find": find,
            "plan_check.has": has,
            "step.spec": make("spec"),
            "step.plan": make("plan"),
            "step.implement": make("implement"),
        },
    )


def test_a_truthy_when_jumps_to_skip_to(store, run_subtask):
    calls: list[str] = []

    def has(result: dict[str, Any]) -> bool:
        return bool(result.get("validated"))

    workflow = _skipping_workflow(has, calls)

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["plan_check", "implement"]
    assert summary.status == "done"
    assert summary.skipped == ["spec", "plan"]
    assert _projected_phases(store) == [("plan_check", "done"), ("implement", "done")]
    assert set(summary.results) == {"plan_check", "implement"}


def test_a_falsy_when_continues_to_the_next_phase(store, run_subtask):
    calls: list[str] = []

    def has(result: dict[str, Any]) -> bool:
        return False

    workflow = _skipping_workflow(has, calls)

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["plan_check", "spec", "plan", "implement"]
    assert summary.skipped == []
    assert summary.status == "done"


def test_a_raising_when_fails_its_phase_rather_than_not_skipping(store, run_subtask):
    calls: list[str] = []

    def has(result: dict[str, Any]) -> bool:
        raise ValueError("unreadable plan front matter")

    workflow = _skipping_workflow(has, calls)

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["plan_check"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "plan_check"
    assert "unreadable plan front matter" in summary.detail
    assert _projected_phases(store) == [("plan_check", "failed")]


def BEST_EFFORT(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("board", (
        Step(
            "mark_in_progress",
            fn["rollup.set_status"],
            args={"status": "in_progress"},
            best_effort=True,
        ),
        Step("work", fn["step.work"]),
        Step(
            "mark_done",
            fn["rollup.done"],
            args={"status": "done"},
            gates=(fn["done_gate"],),
            best_effort=True,
        ),
    ))


def test_a_best_effort_failure_warns_and_does_not_sink_the_subtask(store, run_subtask):
    calls: list[str] = []

    def set_status(card: str, status: str) -> dict[str, Any]:
        calls.append(f"mark:{status}")
        raise RuntimeError("brd exited 1: board is locked")

    def work(card: str) -> dict[str, Any]:
        calls.append("work")
        return {}

    def done(card: str, status: str) -> dict[str, Any]:
        calls.append(f"done:{status}")
        return {}

    def done_gate(result: dict[str, Any]) -> None:
        return None

    workflow = _workflow(
        BEST_EFFORT,
        {
            "rollup.set_status": set_status,
            "step.work": work,
            "rollup.done": done,
            "done_gate": done_gate,
        },
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["mark:in_progress", "work", "done:done"]
    assert summary.status == "done"
    assert summary.failed_phase is None
    assert len(summary.warnings) == 1
    assert "mark_in_progress" in summary.warnings[0]
    assert "board is locked" in summary.warnings[0]
    assert _projected_phases(store) == [
        ("mark_in_progress", "failed"),
        ("work", "done"),
        ("mark_done", "done"),
    ]
    assert store.journal.read()[-1].payload["status"] == "done"


def test_a_best_effort_phase_with_a_failing_gate_only_warns(store, run_subtask):
    def set_status(card: str, status: str) -> dict[str, Any]:
        return {}

    def work(card: str) -> dict[str, Any]:
        return {}

    def done(card: str, status: str) -> dict[str, Any]:
        return {"moved": False}

    def done_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"blocked": "board", "detail": "card is still in_progress"}

    workflow = _workflow(
        BEST_EFFORT,
        {
            "rollup.set_status": set_status,
            "step.work": work,
            "rollup.done": done,
            "done_gate": done_gate,
        },
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"
    assert len(summary.warnings) == 1
    assert "card is still in_progress" in summary.warnings[0]
    assert _projected_phases(store)[-1] == ("mark_done", "failed")


def test_a_best_effort_binding_failure_only_warns(store, run_subtask):
    def set_status(card: str, status: str, missing_thing: str) -> dict[str, Any]:
        return {}

    def work(card: str) -> dict[str, Any]:
        return {}

    def done(card: str, status: str) -> dict[str, Any]:
        return {}

    def done_gate(result: dict[str, Any]) -> None:
        return None

    workflow = _workflow(
        BEST_EFFORT,
        {
            "rollup.set_status": set_status,
            "step.work": work,
            "rollup.done": done,
            "done_gate": done_gate,
        },
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"
    assert "missing_thing" in summary.warnings[0]


def test_a_failed_best_effort_phase_contributes_no_result(store, run_subtask):
    def set_status(card: str, status: str) -> dict[str, Any]:
        raise RuntimeError("board is locked")

    def work(card: str) -> dict[str, Any]:
        return {}

    def done(card: str, status: str) -> dict[str, Any]:
        return {}

    def done_gate(result: dict[str, Any]) -> None:
        return None

    workflow = _workflow(
        BEST_EFFORT,
        {
            "rollup.set_status": set_status,
            "step.work": work,
            "rollup.done": done,
            "done_gate": done_gate,
        },
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert set(summary.results) == {"work", "mark_done"}


def MIXED(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("mixed", (
        _agent("explore", "explorer", result=results.ExploreResult),
        Step("work", fn["step.work"]),
    ))


def test_an_agent_phase_goes_to_the_injected_runner(store, run_subtask):
    seen: list[tuple[str, str]] = []

    def agent_runner(phase, context, rendered):
        seen.append((phase.name, phase.role))
        assert rendered.phase == "explore"
        assert rendered.inputs == ()
        return {"summary": "explored"}

    def work(card: str, explore: dict[str, Any]) -> dict[str, Any]:
        seen.append(("work", explore["summary"]))
        return {}

    workflow = _workflow(MIXED, {"step.work": work})

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
    )

    assert seen == [("explore", "explorer"), ("work", "explored")]
    assert summary.results["explore"] == {"summary": "explored"}
    assert _projected_phases(store) == [("work", "done")]


def test_an_agent_phase_with_no_runner_is_a_named_engine_error(store, run_subtask):
    def work(card: str) -> dict[str, Any]:
        return {}

    workflow = _workflow(MIXED, {"step.work": work})

    with pytest.raises(walk.EngineError) as caught:
        run_subtask(
            workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
        )

    assert caught.value.phase == "explore"
    assert "agent runner" in str(caught.value)


def test_the_builtin_task_document_walks_against_a_fake_registry(store, run_subtask):
    calls: list[str] = []

    def set_status(card: str, status: str) -> dict[str, Any]:
        calls.append(f"rollup.set_status:{status}")
        return {"card": card, "status": status}

    def ensure(
        branch: str, base: str, worktree: Any, repo_dir: Any, fast_forward: bool = False
    ) -> dict[str, Any]:
        calls.append("worktree.ensure")
        return {"created": True}

    def find_plan(card: str) -> dict[str, Any]:
        calls.append("plan_check.find_validated_plan")
        return {"found": False, "validated": False}

    def has_plan(result: dict[str, Any]) -> bool:
        return bool(result.get("validated"))

    def mark_validated(plan_path: str, worktree: Any) -> dict[str, Any]:
        calls.append("plan_check.mark_validated")
        return {"path": plan_path, "appended": True}

    def commit_documents(
        card_details: Any, spec_path: str, plan_path: str, worktree: Any
    ) -> dict[str, Any]:
        calls.append("docs_commit.commit_documents")
        return {"plan_hash": "a1b2c3d4"}

    def run_suite(commands: list[str], worktree: Any) -> dict[str, Any]:
        calls.append("verify.run_suite")
        return {"passed": True}

    def stale_branch_gate(result: dict[str, Any]) -> None:
        return None

    def verification_passed_gate(result: dict[str, Any]) -> None:
        calls.append("verification_passed_gate")
        return None

    def agent_only_gate(**kwargs: Any) -> None:
        raise AssertionError("an agent phase's gate is the agent runner's business")

    def integrate_only_gate(**kwargs: Any) -> None:
        raise AssertionError("TASK never references an integrate-only gate")

    functions: dict[str, Any] = {
        "rollup.set_status": set_status,
        "worktree.ensure": ensure,
        "plan_check.find_validated_plan": find_plan,
        "plan_check.has_validated_plan": has_plan,
        "plan_check.mark_validated": mark_validated,
        "docs_commit.commit_documents": commit_documents,
        "verify.run_suite": run_suite,
        "verification_passed_gate": verification_passed_gate,
        "critic_blockers_gate": agent_only_gate,
        "exploration_output_gate": agent_only_gate,
        "implement_blocked_gate": agent_only_gate,
        "review_blockers_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
        "stale_branch_gate": stale_branch_gate,
        "verification_gate": agent_only_gate,
        "merge_completed_gate": integrate_only_gate,
    }

    def agent_runner(phase: phase_model.AgentPhase, context: dict[str, Any], rendered) -> dict[str, Any]:
        calls.append(f"agent:{phase.name}")
        return {"role": phase.role}

    workflow = _fake_task(functions)

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        commands=["uv run pytest"],
        card=CARD,
        parent_story=PARENT,
        agent_runner=agent_runner,
    )

    assert calls == [
        "worktree.ensure",
        "agent:explore",
        "rollup.set_status:in_progress",
        "plan_check.find_validated_plan",
        "agent:spec",
        "agent:validate_spec",
        "agent:plan",
        "agent:validate_plan",
        "plan_check.mark_validated",
        "docs_commit.commit_documents",
        "agent:implement",
        "agent:review",
        "verify.run_suite",
        "verification_passed_gate",
        "rollup.set_status:done",
    ]
    assert summary.status == "done"
    assert summary.warnings == []
    assert [name for name, _status in _projected_phases(store)] == [
        "worktree",
        "mark_in_progress",
        "plan_check",
        "mark_validated",
        "docs_commit",
        "verify",
        "mark_done",
    ]


def _journalled_details(opened) -> list[tuple[str | None, str, str | None]]:
    return [
        (line.phase, line.payload["status"], line.payload["detail"])
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ]


def test_a_raising_step_writes_why_it_failed_into_the_journal(store, run_subtask):
    """§9 makes the journal the truth the projection is rebuilt from, so the
    reason a phase failed has to be *in* it. The returned summary is in-memory
    only: an operator reading the audit trail after the process is gone would
    otherwise see `failed` with no cause at all.
    """

    def alpha(card: str) -> dict[str, Any]:
        raise OSError("disk went away")

    document = ONE_PHASE
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    started, failed = _journalled_details(store)
    assert started == ("alpha", "started", None)
    assert failed[:2] == ("alpha", "failed")
    assert "disk went away" in failed[2]
    assert failed[2] == summary.detail


def test_a_failing_gate_writes_its_verdict_into_the_journal(store, run_subtask):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"blocked": "verification", "detail": "2 of 3 commands failed"}

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    _started, failed = _journalled_details(store)
    assert failed[:2] == ("alpha", "failed")
    assert "alpha_gate" in failed[2]
    assert "2 of 3 commands failed" in failed[2]
    assert failed[2] == summary.detail


def test_a_best_effort_failure_is_journalled_with_its_reason_too(store, run_subtask):
    def set_status(card: str, status: str) -> dict[str, Any]:
        raise RuntimeError("brd exited 1: board is locked")

    def work(card: str) -> dict[str, Any]:
        return {}

    def done(card: str, status: str) -> dict[str, Any]:
        return {}

    def done_gate(result: dict[str, Any]) -> None:
        return None

    workflow = _workflow(
        BEST_EFFORT,
        {
            "rollup.set_status": set_status,
            "step.work": work,
            "rollup.done": done,
            "done_gate": done_gate,
        },
    )

    run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    details = dict(
        (phase, detail)
        for phase, status, detail in _journalled_details(store)
        if status == "failed"
    )
    assert "board is locked" in details["mark_in_progress"]


def test_a_phase_that_succeeds_journals_no_failure_detail(store, run_subtask):
    def step(card: str) -> dict[str, Any]:
        return {}

    document = ONE_PHASE
    workflow = _workflow(document, {"step.alpha": step})

    run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert _journalled_details(store) == [
        ("alpha", "started", None),
        ("alpha", "done", None),
    ]


def test_a_gate_on_a_phase_named_like_a_context_key_still_sees_the_real_value(store, run_subtask):
    """A phase named after a reserved context key (e.g. `worktree`) still
    gets the real engine-set value when its own gate or `when` binds by
    that name, not that phase's own return value. The shipped `worktree`
    phase carries neither a gate nor a `when` today, but a gate added to it
    that asks for `worktree` wants the path `worktree.ensure` was pointed
    at -- exactly what the reserved-key guard protects for every *later*
    phase too. The result stays reachable under `result`, which is the
    name the shipped gates bind by anyway.
    """
    seen: dict[str, Any] = {}

    def ensure(card: str) -> dict[str, Any]:
        return {"created": True}

    def worktree_gate(worktree: Any, result: dict[str, Any]) -> None:
        seen.update(worktree=worktree, result=result)
        return None

    def when_worktree(worktree: Any) -> bool:
        seen["when_worktree"] = worktree
        return False

    def document(fn):
        return phase_model.Workflow("collide", (
            Step(
                "worktree",
                fn["worktree.ensure"],
                gates=(fn["worktree_gate"],),
                when=fn["when_worktree"],
                skip_to="after",
            ),
            Step("after", fn["step.after"]),
        ))

    workflow = _workflow(
        document,
        {
            "worktree.ensure": ensure,
            "worktree_gate": worktree_gate,
            "when_worktree": when_worktree,
            "step.after": lambda card: {},
        },
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    real_worktree = _subtask().worktree_path
    assert summary.status == "done"
    assert seen["worktree"] == real_worktree
    assert seen["when_worktree"] == real_worktree
    assert seen["result"] == {"created": True}


def test_a_phase_is_timed_with_the_injected_clock(store, run_subtask):
    """`started_at` is read once, before the call, and reused on the terminal
    record so the row keeps the moment work began rather than the moment it
    ended. Both edges come from the injected clock, never from the wall.
    """
    ticks = [
        datetime(2026, 9, 23, 10, minute, tzinfo=timezone.utc)
        for minute in (0, 5, 9, 30)
    ]
    clock = iter(ticks).__next__

    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        raise OSError("gone")

    document = TWO_PHASES
    workflow = _workflow(document, {"step.alpha": alpha, "step.beta": beta})

    run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=clock,
    )

    stamped = [
        (line.phase, line.payload["status"], line.payload["started_at"], line.payload["ended_at"])
        for line in store.journal.read()
        if line.event == "phase_upsert"
    ]
    assert stamped == [
        ("alpha", "started", "2026-09-23T10:00:00Z", None),
        ("alpha", "done", "2026-09-23T10:00:00Z", "2026-09-23T10:05:00Z"),
        ("beta", "started", "2026-09-23T10:09:00Z", None),
        ("beta", "failed", "2026-09-23T10:09:00Z", "2026-09-23T10:30:00Z"),
    ]
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "failed")]


def test_the_default_clock_stamps_an_aware_utc_time(store, run_subtask):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    document = ONE_PHASE
    workflow = _workflow(document, {"step.alpha": alpha})
    before = datetime.now(timezone.utc)

    run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    after = datetime.now(timezone.utc)
    done = store.journal.read()[-2]
    assert done.payload["status"] == "done"
    started_at = datetime.fromisoformat(done.payload["started_at"])
    ended_at = datetime.fromisoformat(done.payload["ended_at"])
    assert started_at.tzinfo is not None
    assert before <= started_at <= ended_at <= after


def DOCUMENT_PATHS(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("paths", (
        _agent("spec", "spec_author", writes="docs/superpowers/specs/{stem}.md"),
        _agent("plan", "planner", writes="docs/superpowers/plans/{stem}.md"),
        _agent("implement", "coder", inputs=("spec_path", "plan_path")),
        Step("after", fn["step.after"]),
    ))


def test_document_paths_are_bound_from_the_writes_templates(store, run_subtask):
    seen: dict[str, Any] = {}

    def after(spec_path: str, plan_path: str) -> dict[str, Any]:
        seen.update(spec_path=spec_path, plan_path=plan_path)
        return {}

    workflow = _workflow(DOCUMENT_PATHS, {"step.after": after})

    run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        card=CARD,
        agent_runner=lambda phase, context, rendered: {},
    )

    assert seen == {"spec_path": SPEC_PATH, "plan_path": PLAN_PATH}


def test_a_document_path_input_with_no_writing_phase_is_a_named_error(store, run_subtask):
    def document(fn):
        return phase_model.Workflow("orphan", (
            _agent("implement", "coder", inputs=("plan_path",)),
        ))

    workflow = _workflow(document, {})

    with pytest.raises(walk.EngineError) as caught:
        run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            card=CARD,
            agent_runner=lambda phase, context, rendered: {},
        )

    assert caught.value.parameter == "plan_path"
    assert "'plan'" in str(caught.value)
    assert "writes" in str(caught.value)


def test_a_document_path_input_with_no_card_is_a_named_error(store, run_subtask):
    workflow = _workflow(DOCUMENT_PATHS, {"step.after": lambda spec_path, plan_path: {}})

    with pytest.raises(walk.EngineError) as caught:
        run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            agent_runner=lambda phase, context, rendered: {},
        )

    assert caught.value.parameter in {"plan_path", "spec_path"}
    assert "no card was supplied" in str(caught.value)


def test_a_document_with_no_path_inputs_needs_no_card(store, run_subtask):
    """Every existing walk in this file passes no card; none may start failing."""
    workflow = _workflow(THREE_PHASES, {
        "step.alpha": lambda card: {},
        "step.beta": lambda card: {},
        "step.gamma": lambda card: {},
    })

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"


def _recording_runner(recorded: dict[str, Any]):
    def agent_runner(phase, context, rendered):
        recorded[phase.name] = rendered
        return {"role": phase.role}

    return agent_runner


_TASK_FUNCTION_NAMES: dict[Any, str] = {
    worktree.ensure: "worktree.ensure",
    rollup.set_status: "rollup.set_status",
    plan_check.find_validated_plan: "plan_check.find_validated_plan",
    plan_check.has_validated_plan: "plan_check.has_validated_plan",
    plan_check.mark_validated: "plan_check.mark_validated",
    docs_commit.commit_documents: "docs_commit.commit_documents",
    verify.run_suite: "verify.run_suite",
    reducers.verification_passed_gate: "verification_passed_gate",
    reducers.exploration_output_gate: "exploration_output_gate",
    reducers.verification_gate: "verification_gate",
    reducers.critic_blockers_gate: "critic_blockers_gate",
    reducers.implement_blocked_gate: "implement_blocked_gate",
    reducers.review_blockers_gate: "review_blockers_gate",
    reducers.review_gate: "review_gate",
    reducers.plan_hash_gate_adapter: "plan_hash_gate",
    reducers.stale_branch_gate: "stale_branch_gate",
}
"""Every callable `TASK` holds, under the name the fake tables below use for it."""


def _fake_task(functions: dict[str, Any]) -> phase_model.Workflow:
    """`workflow.task.TASK` with every callable swapped for the fake of the same name.

    Phase order, inputs, `writes`, `skip_to` and the critics' `on_fail` are
    TASK's own; only what runs is fake. A callable `TASK` holds that `functions`
    does not name raises `KeyError` here, before anything is walked.
    """

    def fake(fn: Any) -> Any:
        return functions[_TASK_FUNCTION_NAMES[fn]]

    swapped: list[phase_model.Step | phase_model.AgentPhase] = []
    for phase in task_workflow.TASK.phases:
        if isinstance(phase, phase_model.Step):
            swapped.append(
                dataclasses.replace(
                    phase,
                    run=fake(phase.run),
                    gates=tuple(fake(gate) for gate in phase.gates),
                    when=None if phase.when is None else fake(phase.when),
                )
            )
        else:
            swapped.append(
                dataclasses.replace(phase, gates=tuple(fake(gate) for gate in phase.gates))
            )
    return phase_model.Workflow(task_workflow.TASK.name, tuple(swapped))


def _builtin_functions(calls: list[str], *, validated: bool) -> dict[str, Any]:
    """The fake callables `TASK` needs, with no git, brd or harness."""

    def set_status(card: str, status: str) -> dict[str, Any]:
        calls.append(f"rollup.set_status:{status}")
        return {"card": card, "status": status}

    def ensure(
        branch: str, base: str, worktree: Any, repo_dir: Any, fast_forward: bool = False
    ) -> dict[str, Any]:
        calls.append("worktree.ensure")
        return {"created": True}

    def find_plan(card: str) -> dict[str, Any]:
        calls.append("plan_check.find_validated_plan")
        return {"found": validated, "validated": validated}

    def has_plan(result: dict[str, Any]) -> bool:
        return bool(result.get("validated"))

    def mark_validated(plan_path: str, worktree: Any) -> dict[str, Any]:
        calls.append("plan_check.mark_validated")
        return {"path": plan_path, "appended": True}

    def commit_documents(
        card_details: Any, spec_path: str, plan_path: str, worktree: Any
    ) -> dict[str, Any]:
        calls.append("docs_commit.commit_documents")
        return {"plan_hash": "a1b2c3d4"}

    def run_suite(commands: list[str], worktree: Any) -> dict[str, Any]:
        calls.append("verify.run_suite")
        return {"passed": True}

    def stale_branch_gate(result: dict[str, Any]) -> None:
        return None

    def passed(result: dict[str, Any]) -> None:
        return None

    def agent_only_gate(**kwargs: Any) -> None:
        raise AssertionError("an agent phase's gate is the agent runner's business")

    return {
        "rollup.set_status": set_status,
        "worktree.ensure": ensure,
        "plan_check.find_validated_plan": find_plan,
        "plan_check.has_validated_plan": has_plan,
        "plan_check.mark_validated": mark_validated,
        "docs_commit.commit_documents": commit_documents,
        "verify.run_suite": run_suite,
        "verification_passed_gate": passed,
        "critic_blockers_gate": agent_only_gate,
        "exploration_output_gate": agent_only_gate,
        "implement_blocked_gate": agent_only_gate,
        "review_blockers_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
        "stale_branch_gate": stale_branch_gate,
        "verification_gate": agent_only_gate,
    }


def _walk_builtin(run_subtask, store, recorded: dict[str, Any], *, validated: bool) -> Any:
    calls: list[str] = []
    workflow = _fake_task(_builtin_functions(calls, validated=validated))
    return run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        commands=["uv run pytest"],
        card=CARD,
        parent_story=PARENT,
        agent_runner=_recording_runner(recorded),
    )


def test_every_document_path_input_renders_the_expanded_writes_template(store, run_subtask):
    recorded: dict[str, Any] = {}

    _walk_builtin(run_subtask, store, recorded, validated=False)

    for phase_name in ("validate_spec", "plan", "validate_plan", "implement"):
        assert dict(recorded[phase_name].sections)["spec_path"] == SPEC_PATH
    for phase_name in ("validate_plan", "implement", "review"):
        assert dict(recorded[phase_name].sections)["plan_path"] == PLAN_PATH


def test_implement_gets_both_paths_even_when_plan_check_skipped_spec_and_plan(
    store, run_subtask
):
    recorded: dict[str, Any] = {}

    summary = _walk_builtin(run_subtask, store, recorded, validated=True)

    assert summary.skipped == [
        "spec",
        "validate_spec",
        "plan",
        "validate_plan",
        "mark_validated",
    ]
    assert set(recorded) == {"explore", "implement", "review"}
    sections = dict(recorded["implement"].sections)
    assert sections["spec_path"] == SPEC_PATH
    assert sections["plan_path"] == PLAN_PATH


def test_each_agent_phase_receives_exactly_the_inputs_it_declares(store, run_subtask):
    """§13: a phase receives its declared inputs and nothing else."""
    recorded: dict[str, Any] = {}

    _walk_builtin(run_subtask, store, recorded, validated=False)

    assert {name: rendered.inputs for name, rendered in recorded.items()} == {
        "explore": ("card", "parent_story", "repo_docs", "verification"),
        "spec": ("card", "explore", "spec_path"),
        "validate_spec": ("card", "spec_path"),
        "plan": ("spec_path", "plan_path"),
        "validate_plan": ("spec_path", "plan_path"),
        "implement": ("plan_path", "spec_path", "branch", "base_branch", "plan_hash"),
        "review": ("branch", "base_branch", "plan_path", "plan_hash"),
    }


def test_the_explore_prompt_reads_the_cards_not_the_reserved_card_key(store, run_subtask):
    recorded: dict[str, Any] = {}

    _walk_builtin(run_subtask, store, recorded, validated=False)

    sections = dict(recorded["explore"].sections)
    assert json.loads(sections["card"])["title"] == "Resolve phase inputs"
    assert json.loads(sections["parent_story"])["title"] == (
        "The workflow document and the engine"
    )
    assert json.loads(sections["verification"]) == ["uv run pytest"]


def test_an_unresolvable_input_raises_out_of_the_walk_before_the_runner(store, run_subtask):
    """The walk does not wrap the runner call, so resolution failures propagate.
    Journalling them as an outcome is sibling bf8e415b's choice, not this one's.
    """
    called: list[str] = []

    def agent_runner(phase, context, rendered):
        called.append(phase.name)
        return {}

    def document(fn):
        return phase_model.Workflow("early", (
            _agent("spec", "spec_author", inputs=("explore",)),
        ))

    workflow = _workflow(document, {})

    with pytest.raises(walk.EngineError) as caught:
        run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            card=CARD,
            agent_runner=agent_runner,
        )

    assert called == []
    assert caught.value.phase == "spec"
    assert caught.value.parameter == "explore"


def test_a_phase_named_spec_path_never_clobbers_the_document_path(store, run_subtask):
    seen: dict[str, Any] = {}

    def collide(card: str) -> dict[str, Any]:
        return {"not": "a path"}

    def after(spec_path: str) -> dict[str, Any]:
        seen["spec_path"] = spec_path
        return {}

    def document(fn):
        return phase_model.Workflow("reserved", (
            _agent("spec", "spec_author", writes="docs/superpowers/specs/{stem}.md"),
            Step("spec_path", fn["step.collide"]),
            _agent("implement", "coder", inputs=("spec_path",)),
            Step("after", fn["step.after"]),
        ))

    recorded: dict[str, Any] = {}
    workflow = _workflow(document, {"step.collide": collide, "step.after": after})

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        card=CARD,
        agent_runner=_recording_runner(recorded),
    )

    assert summary.status == "done"
    assert summary.results["spec_path"] == {"not": "a path"}
    assert dict(recorded["implement"].sections)["spec_path"] == SPEC_PATH
    assert seen["spec_path"] == SPEC_PATH


def RESERVED_DETAILS(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("reserved-details", (
        Step("card_details", fn["step.collide"]),
        Step("parent_story_details", fn["step.collide"]),
        _agent("explore", "explorer", inputs=("card", "parent_story")),
    ))


def test_a_phase_named_card_details_never_clobbers_the_cards_the_prompt_renders(
    store, run_subtask
):
    """The membership check above is only a constant; this is the behaviour it buys.

    A document is free to name a phase `card_details`, and its result must not
    become what the next phase's `card` input renders.
    """
    recorded: dict[str, Any] = {}
    workflow = _workflow(RESERVED_DETAILS, {"step.collide": lambda card: {"not": "a card"}})

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        card=CARD,
        parent_story=PARENT,
        agent_runner=_recording_runner(recorded),
    )

    assert summary.results["card_details"] == {"not": "a card"}
    sections = dict(recorded["explore"].sections)
    assert json.loads(sections["card"])["title"] == "Resolve phase inputs"
    assert json.loads(sections["parent_story"])["title"] == (
        "The workflow document and the engine"
    )



def test_a_failed_agent_phase_escalates_the_subtask_and_stops(store, run_subtask):
    # Spec test 14 (§12 line 429: escalation stops the run -- no later phase is
    # started, and the subtask is recorded escalated).
    calls: list[str] = []

    def work(card: str) -> dict[str, Any]:
        calls.append("work")
        return {}

    def agent_runner(phase, context, rendered):
        calls.append(f"agent:{phase.name}")
        raise AgentPhaseFailed(
            phase.name,
            outcome="gate_failed",
            detail="phase 'explore' gate 'exploration_output_gate' failed: blocked=exploration",
        )

    workflow = _workflow(MIXED, {"step.work": work})

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
    )

    assert calls == ["agent:explore"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "explore"
    assert "blocked=exploration" in summary.detail
    assert summary.results == {}
    subtasks = [
        line.payload["status"]
        for line in store.journal.read()
        if line.event == "subtask_upsert"
    ]
    assert subtasks == ["escalated"]


def test_a_gate_failure_s_result_reaches_summary_results_for_the_failed_phase(
    store, run_subtask
):
    # The bug milestone 12's board-comment proof found: a gate-failed phase's
    # `ContextItem` is never yielded (only a successful phase's is), so
    # `comments.agent_reason` always read `None` for a real escalation unless
    # `AgentPhaseFailed.result` is threaded through `Escalated` into
    # `summary.results[failed_phase]` here.
    def agent_runner(phase, context, rendered):
        raise AgentPhaseFailed(
            phase.name,
            outcome="gate_failed",
            detail="phase 'explore' gate 'exploration_output_gate' failed: blocked=exploration",
            result={"unresolved_blockers": ["the thing is broken"]},
        )

    workflow = _workflow(MIXED, {"step.work": lambda card: {}})

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "explore"
    assert summary.results == {
        "explore": {"unresolved_blockers": ["the thing is broken"]}
    }


def test_an_unexpected_error_from_the_agent_runner_escalates_rather_than_crashing(store, run_subtask):
    # Symmetric with `_run_deterministic`'s deliberately total except: an
    # exception escaping the walk would leave the subtask recorded `started`
    # forever, which is exactly what resume mistakes for work in flight.
    def work(card: str) -> dict[str, Any]:
        raise AssertionError("no phase after the failed one may start")

    def agent_runner(phase, context, rendered):
        raise OSError("the run directory went away")

    workflow = _workflow(MIXED, {"step.work": work})

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "explore"
    assert "OSError: the run directory went away" in summary.detail


def test_a_successful_agent_phase_still_advances_the_walk(store, run_subtask):
    """The existing happy path must not change shape under the new try/except."""
    def work(card: str, explore: dict[str, Any]) -> dict[str, Any]:
        return {"saw": explore["summary"]}

    workflow = _workflow(MIXED, {"step.work": work})

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=lambda phase, context, rendered: {"summary": "explored"},
    )

    assert summary.status == "done"
    assert summary.results["work"] == {"saw": "explored"}


def test_an_escalation_keeps_the_results_and_warnings_gathered_before_it(
    store, run_subtask
):
    """Review Focus 3: the failure path collects too. A phase that finished,
    and a gate warning it raised, must survive into an escalated summary."""

    def alpha(card: str) -> dict[str, Any]:
        return {"phase": "alpha"}

    def beta(card: str) -> dict[str, Any]:
        raise OSError("disk went away")

    def alpha_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"warn": "looked thin"}

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "beta"
    assert summary.results == {"alpha": {"phase": "alpha"}}
    assert summary.warnings == ["phase 'alpha' gate 'alpha_gate' warned: looked thin"]


def test_a_run_that_raised_leaves_its_agent_name_free_for_the_next_run(
    store, run_subtask
):
    """Review Focus 1: pygents' AgentRegistry is process-wide and refuses a
    second agent under a name it holds. Both runs use the same run id and
    card id, so the second can only start if the first freed the name, even
    though it ended by raising."""
    workflow = _workflow(MIXED, {"step.work": lambda card, explore: {}})

    with pytest.raises(walk.EngineError):
        run_subtask(
            workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
        )

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=lambda phase, context, rendered: {"summary": "explored"},
    )

    assert summary.status == "done"


class _Abort(BaseException):
    """Not an `Exception`: neither engine may catch it."""


def test_a_base_exception_propagates_and_records_no_outcome(store, run_subtask):
    """Review Focus 2 and the last row of the spec's error table: a
    BaseException propagates, nothing past the phase's own `started` row is
    written, and the agent name is still freed for the next run."""

    def alpha(card: str) -> dict[str, Any]:
        raise _Abort("operator pulled the plug")

    aborting = _workflow(
        THREE_PHASES,
        {"step.alpha": alpha, "step.beta": lambda card: {}, "step.gamma": lambda card: {}},
    )

    with pytest.raises(_Abort):
        run_subtask(
            aborting, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
        )

    assert _journalled_phases(store) == [("alpha", "started")]
    assert _projected_subtask_status(store) is None

    finishing = _workflow(
        THREE_PHASES,
        {"step.alpha": lambda card: {}, "step.beta": lambda card: {}, "step.gamma": lambda card: {}},
    )
    summary = run_subtask(
        finishing, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )
    assert summary.status == "done"


def test_extra_context_reaches_a_deterministic_phase_binding(tmp_path: Path, run_subtask):
    """The §12 escape hatch's parameters have to arrive somehow: `subtask_context`
    is a fixed table, and `TASK`'s gates bind names it does not hold.
    """
    seen: dict[str, Any] = {}

    def step(suite_cmds: list[str], allow_no_verification: bool) -> dict[str, Any]:
        seen["suite_cmds"] = suite_cmds
        seen["allow_no_verification"] = allow_no_verification
        return {"ok": True}

    workflow = phase_model.Workflow("one", (Step("only", step),))
    store = store_writer.Store.open(tmp_path, "run-extra-1")

    summary = run_subtask(
        workflow,
        store,
        story_id="story-1",
        subtask=_subtask(),
        repo_dir=REPO,
        extra_context={"suite_cmds": [], "allow_no_verification": True},
    )

    assert summary.status == "done"
    assert seen == {"suite_cmds": [], "allow_no_verification": True}


def test_extra_context_may_not_redefine_a_reserved_key(tmp_path: Path, run_subtask):
    """`worktree`, `card` and friends are the engine's own: letting a caller
    overwrite one would point every later step at a path the engine never chose.
    """
    workflow = phase_model.Workflow("one", (Step("only", lambda: {"ok": True}),))
    store = store_writer.Store.open(tmp_path, "run-extra-2")

    with pytest.raises(walk.EngineError) as caught:
        run_subtask(
            workflow,
            store,
            story_id="story-1",
            subtask=_subtask(),
            repo_dir=REPO,
            extra_context={"worktree": "/somewhere/else"},
        )

    assert "worktree" in str(caught.value)


def test_extra_context_may_not_redefine_the_base_branch_alias(tmp_path: Path, run_subtask):
    """A caller that could set `base_branch` would point `review_gate` at a base
    the engine never derived, while every step still used the real one."""
    workflow = phase_model.Workflow("one", (Step("only", lambda: {"ok": True}),))
    store = store_writer.Store.open(tmp_path, "run-extra-3")

    with pytest.raises(walk.EngineError) as caught:
        run_subtask(
            workflow,
            store,
            story_id="story-1",
            subtask=_subtask(),
            repo_dir=REPO,
            extra_context={"base_branch": "somewhere/else"},
        )

    assert "base_branch" in str(caught.value)


# ── decision O7: a blocked coder stops the subtask ───────────────────────────
# Engine tier per design 14: the real `TASK`, the real gate, the real
# `dispatch.AgentRunner` for the two phases under test, and a fake adapter plus
# a fake launcher that writes a canned result file. No process is started.


class _FakeAdapter:
    """A `HarnessAdapter` by shape, whose argv names a program nothing runs."""

    name = "fake"
    capabilities = frozenset({"bash", "edit"})

    def build_command(self, d: models.Dispatch) -> list[str]:
        return ["fake-harness", "--role", d.role, "--result", str(d.result_path)]


class _CannedLauncher:
    """A `LauncherFn` double: writes `results[role]` as the result file and
    records every role it was asked to run."""

    def __init__(self, results: dict[str, str]) -> None:
        self.results = results
        self.roles: list[str] = []

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        role = argv[argv.index("--role") + 1]
        self.roles.append(role)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        canned = self.results.get(role)
        if canned is not None:
            Path(argv[argv.index("--result") + 1]).write_text(canned, encoding="utf-8")
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=0.5,
            stdout_path=stdout_path,
        )


BLOCKED_REASON = "the baseline suite was already red: 3 failed before any change"
BLOCKED_IMPLEMENT = json.dumps(
    {
        "blocked": True,
        "blocked_reason": BLOCKED_REASON,
        "resumed": False,
        "plan_hash": "a1b2c3d4",
        "report": "stopped before writing any code",
    }
)


def test_a_blocked_coder_escalates_the_subtask_at_implement_and_review_never_runs(
    store, run_subtask
):
    calls: list[str] = []
    functions = _builtin_functions(calls, validated=True)
    functions["implement_blocked_gate"] = reducers.implement_blocked_gate
    workflow = _fake_task(functions)

    launcher = _CannedLauncher({"coder": BLOCKED_IMPLEMENT})
    adapter = _FakeAdapter()
    subtask = _subtask()
    dispatching = dispatch.AgentRunner(
        store=store,
        launcher=launcher,
        run_id=RUN_ID,
        story_id=STORY_ID,
        card_id=subtask.card_id,
        adapters={adapter.name: adapter},
        harness_map={
            "coder": models.HarnessAssignment(harness=adapter.name, model="fake-model"),
            "reviewer": models.HarnessAssignment(harness=adapter.name, model="fake-model"),
        },
    )

    def agent_runner(phase, context, rendered):
        if phase.name in ("implement", "review"):
            return dispatching(phase, context, rendered)
        calls.append(f"agent:{phase.name}")
        return {"role": phase.role}

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=subtask,
        repo_dir=REPO,
        commands=["uv run pytest"],
        card=CARD,
        parent_story=PARENT,
        agent_runner=agent_runner,
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "implement"
    assert BLOCKED_REASON in summary.detail
    assert "implement_blocked_gate" in summary.detail
    assert "blocked=implement" in summary.detail
    assert launcher.roles == ["coder"]
    attempts = store.connection.execute(
        "SELECT phase, status FROM attempts ORDER BY phase, n"
    ).fetchall()
    assert [tuple(row) for row in attempts] == [("implement", "gate_failed")]
    assert "verify.run_suite" not in calls
    assert "rollup.set_status:done" not in calls


# A `ReviewResult` whose reviewer left one blocker standing. Clean in every
# other respect -- empty porcelain, one tagged commit, a well-formed hash -- so
# the only thing that can stop `review` is the blocker.
BLOCKED_REVIEW = json.dumps(
    {
        "findings": ["x"],
        "unresolved_blockers": ["x"],
        "fix_summary": "one finding is still open",
        "porcelain": "",
        "commit_count": 1,
        "tagged_count": 1,
        "plan_hash": "a1b2c3d4",
    }
)


def test_a_reviewer_reporting_a_blocker_escalates_at_review_and_verify_never_runs(
    store, run_subtask
):
    calls: list[str] = []
    functions = _builtin_functions(calls, validated=True)
    # The real gate under test. `review_gate` and `plan_hash_gate` stay
    # `agent_only_gate`, which raises if called: listed first, the blockers
    # gate must stop the phase before either of them runs.
    functions["review_blockers_gate"] = reducers.review_blockers_gate
    workflow = _fake_task(functions)

    launcher = _CannedLauncher({"reviewer": BLOCKED_REVIEW})
    adapter = _FakeAdapter()
    subtask = _subtask()
    dispatching = dispatch.AgentRunner(
        store=store,
        launcher=launcher,
        run_id=RUN_ID,
        story_id=STORY_ID,
        card_id=subtask.card_id,
        adapters={adapter.name: adapter},
        harness_map={
            "reviewer": models.HarnessAssignment(harness=adapter.name, model="fake-model"),
        },
    )

    def agent_runner(phase, context, rendered):
        if phase.name == "review":
            return dispatching(phase, context, rendered)
        calls.append(f"agent:{phase.name}")
        return {"role": phase.role}

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=subtask,
        repo_dir=REPO,
        commands=["uv run pytest"],
        card=CARD,
        parent_story=PARENT,
        agent_runner=agent_runner,
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "review"
    assert "review_blockers_gate" in summary.detail
    assert "blocked=review" in summary.detail
    assert "review left 1 unresolved blocker(s): x" in summary.detail
    assert launcher.roles == ["reviewer"]
    attempts = store.connection.execute(
        "SELECT phase, status FROM attempts ORDER BY phase, n"
    ).fetchall()
    assert [tuple(row) for row in attempts] == [("review", "gate_failed")]
    assert "agent:implement" in calls
    assert "verify.run_suite" not in calls
    assert "rollup.set_status:done" not in calls


def test_a_subtask_summary_may_report_stopped():
    hints = typing.get_type_hints(walk.SubtaskSummary)
    assert typing.get_args(hints["status"]) == ("done", "escalated", "stopped")


def FOUR_PHASES(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("four", (
        Step("alpha", fn["step.alpha"]),
        Step("beta", fn["step.beta"]),
        Step("gamma", fn["step.gamma"]),
        Step("delta", fn["step.delta"]),
    ))


def STOP_MIXED(fn: dict[str, Any]) -> phase_model.Workflow:
    return phase_model.Workflow("stop_mixed", (
        Step("prepare", fn["step.prepare"]),
        _agent("explore", "explorer", result=results.ExploreResult),
        Step("finish", fn["step.finish"]),
    ))


def _subtask_journal_statuses(opened) -> list[str]:
    return [
        line.payload["status"]
        for line in opened.journal.read()
        if line.event == "subtask_upsert"
    ]


def _projected_subtask_status(opened, card: str = "ed77a917") -> str | None:
    row = opened.connection.execute(
        "SELECT status FROM subtasks WHERE card_id = ?", (card,)
    ).fetchone()
    return None if row is None else row[0]


class _StepStop:
    """A `StopSignal` a canned step or fake runner triggers mid-walk.

    Steps and agent runners run in `asyncio.to_thread` workers, and the signal
    lives on the loop, so `fire` hands `trigger` to the loop with
    `call_soon_threadsafe` and blocks on a `threading.Event` until it has run:
    the agent is paused before the phase returns. No sleeps.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.signal = StopSignal()
        self._loop = loop

    def fire(self) -> None:
        fired = threading.Event()

        def trigger() -> None:
            self.signal.trigger(STORY_ID)
            fired.set()

        self._loop.call_soon_threadsafe(trigger)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")


async def test_a_stop_requested_during_phase_three_stops_before_phase_four(store):
    calls: list[str] = []
    stop = _StepStop(asyncio.get_running_loop())

    def make(name: str, *, fire: bool = False):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            if fire:
                stop.fire()
            return {"phase": name}

        return step

    workflow = _workflow(
        FOUR_PHASES,
        {
            "step.alpha": make("alpha"),
            "step.beta": make("beta"),
            "step.gamma": make("gamma", fire=True),
            "step.delta": make("delta"),
        },
    )

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop.signal,
    )

    assert calls == ["alpha", "beta", "gamma"]
    assert summary.status == "stopped"
    assert summary.failed_phase is None
    assert summary.detail == "stopped before delta"
    assert set(summary.results) == {"alpha", "beta", "gamma"}
    assert _projected_phases(store) == [
        ("alpha", "done"),
        ("beta", "done"),
        ("gamma", "done"),
    ]
    assert ("delta", "started") not in _journalled_phases(store)
    assert _subtask_journal_statuses(store) == ["stopped"]
    assert _projected_subtask_status(store) == "stopped"


def test_a_stop_already_requested_runs_no_phase_at_all(store, run_subtask):
    calls: list[str] = []
    stop = StopSignal()
    stop.trigger(STORY_ID)

    def step(card: str) -> dict[str, Any]:
        calls.append("ran")
        return {}

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": step, "step.beta": step, "step.gamma": step}
    )

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop,
    )

    assert calls == []
    assert summary.status == "stopped"
    assert summary.failed_phase is None
    assert summary.detail == "stopped before alpha"
    assert summary.results == {}
    assert _journalled_phases(store) == []
    assert store.connection.execute("SELECT COUNT(*) FROM phases").fetchone()[0] == 0
    assert _subtask_journal_statuses(store) == ["stopped"]
    assert _projected_subtask_status(store) == "stopped"


async def test_a_stop_before_a_deterministic_phase_leaves_it_unstarted(store):
    calls: list[str] = []
    stop = _StepStop(asyncio.get_running_loop())

    def prepare(card: str) -> dict[str, Any]:
        calls.append("prepare")
        return {}

    def finish(card: str) -> dict[str, Any]:
        calls.append("finish")
        return {}

    def agent_runner(phase, context, rendered):
        calls.append(f"agent:{phase.name}")
        stop.fire()
        return {"summary": "explored"}

    workflow = _workflow(STOP_MIXED, {"step.prepare": prepare, "step.finish": finish})

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
        stop=stop.signal,
    )

    assert calls == ["prepare", "agent:explore"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before finish"
    assert summary.results["explore"] == {"summary": "explored"}
    assert _journalled_phases(store) == [("prepare", "started"), ("prepare", "done")]
    assert _projected_subtask_status(store) == "stopped"


async def test_a_stop_before_an_agent_phase_leaves_the_runner_uncalled(store):
    recorded: dict[str, Any] = {}
    stop = _StepStop(asyncio.get_running_loop())

    def prepare(card: str) -> dict[str, Any]:
        stop.fire()
        return {}

    def finish(card: str) -> dict[str, Any]:
        raise AssertionError("no phase after the stop may start")

    workflow = _workflow(STOP_MIXED, {"step.prepare": prepare, "step.finish": finish})

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=_recording_runner(recorded),
        stop=stop.signal,
    )

    assert recorded == {}
    assert summary.status == "stopped"
    assert summary.failed_phase is None
    assert summary.detail == "stopped before explore"
    assert _projected_phases(store) == [("prepare", "done")]
    assert _subtask_journal_statuses(store) == ["stopped"]


async def test_a_stop_before_an_agent_phase_wins_over_a_missing_runner(store):
    """The stop parks the agent before the `explore` turn, and the
    `agent_runner is None` error is raised only inside that turn: a walk that
    stops before its agent phase never reaches it, so it has nothing to
    complain about."""
    stop = _StepStop(asyncio.get_running_loop())

    def prepare(card: str) -> dict[str, Any]:
        stop.fire()
        return {}

    def finish(card: str) -> dict[str, Any]:
        raise AssertionError("no phase after the stop may start")

    workflow = _workflow(STOP_MIXED, {"step.prepare": prepare, "step.finish": finish})

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop.signal,
    )

    assert summary.status == "stopped"
    assert summary.detail == "stopped before explore"
    assert _projected_subtask_status(store) == "stopped"


def test_a_stop_signal_that_never_fires_changes_nothing(store, run_subtask):
    calls: list[str] = []
    stop = StopSignal()

    def make(name: str):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            return {"phase": name}

        return step

    workflow = _workflow(
        THREE_PHASES,
        {"step.alpha": make("alpha"), "step.beta": make("beta"), "step.gamma": make("gamma")},
    )

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop,
    )

    assert not stop.triggered
    assert calls == ["alpha", "beta", "gamma"]
    assert summary == walk.SubtaskSummary(
        status="done",
        results={
            "alpha": {"phase": "alpha"},
            "beta": {"phase": "beta"},
            "gamma": {"phase": "gamma"},
        },
    )
    assert _journalled_phases(store) == [
        ("alpha", "started"),
        ("alpha", "done"),
        ("beta", "started"),
        ("beta", "done"),
        ("gamma", "started"),
        ("gamma", "done"),
    ]
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done"), ("gamma", "done")]
    assert _subtask_journal_statuses(store) == ["done"]
    assert _projected_subtask_status(store) == "done"


async def test_an_escalation_during_the_stop_request_wins_over_the_stop(store):
    calls: list[str] = []
    stop = _StepStop(asyncio.get_running_loop())

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        return {}

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        stop.fire()
        raise OSError("disk went away")

    def gamma(card: str) -> dict[str, Any]:
        calls.append("gamma")
        return {}

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": alpha, "step.beta": beta, "step.gamma": gamma}
    )

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop.signal,
    )

    assert stop.signal.triggered
    assert calls == ["alpha", "beta"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "beta"
    assert "disk went away" in summary.detail
    assert _subtask_journal_statuses(store) == ["escalated"]
    assert _projected_subtask_status(store) == "escalated"


# ── run_one_step: one deterministic phase, run, judged and recorded ─────────

FIXED = datetime(2026, 9, 26, tzinfo=timezone.utc)


def test_run_one_step_calls_a_phase_models_callables_directly(store):
    seen: dict[str, Any] = {}

    def build(card):
        return {"built": card}

    def ok_gate(result):
        seen["gate"] = result

    step = phase_model.Step(
        "a",
        build,
        gates=(ok_gate,),
        when=lambda result: result["built"] == "c1",
        skip_to="z",
    )

    outcome = walk.run_one_step(
        phase=step, table={"card": "c1"}, store=store, story_id=STORY_ID,
        subtask=_subtask(), clock=lambda: FIXED,
    )

    assert outcome.ok is True
    assert outcome.result == {"built": "c1"}
    assert outcome.skip_to == "z"
    assert outcome.warnings == []
    assert seen["gate"] == {"built": "c1"}
    assert _journalled_phases(store) == [("a", "started"), ("a", "done")]


def test_run_one_step_names_a_callable_gate_by_its_function_name(store):
    def blocking(result):
        return {"blocked": "x"}

    step = phase_model.Step("a", lambda: {}, gates=(blocking,))

    outcome = walk.run_one_step(
        phase=step, table={}, store=store, story_id=STORY_ID,
        subtask=_subtask(), clock=lambda: FIXED,
    )

    assert outcome.ok is False
    assert outcome.detail == "phase 'a' gate 'blocking' failed: blocked=x"
    assert _journalled_phases(store) == [("a", "started"), ("a", "failed")]


def test_an_error_no_phase_handles_escalates_at_the_phase_that_was_running(
    store, monkeypatch
):
    """Review Focus 5. An
    `Exception` that is neither `Escalated` nor `EngineError` -- here the
    bridge itself failing under `beta` -- must end the subtask escalated at
    `beta`, in the old engine's `{Type}: {message}` form, not propagate."""
    real_call_step = bridge.call_step

    async def call_step(fn, kwargs):
        if kwargs["phase"].name == "beta":
            raise RuntimeError("the worker pool is gone")
        return await real_call_step(fn, kwargs)

    monkeypatch.setattr(bridge, "call_step", call_step)
    workflow = _workflow(
        THREE_PHASES,
        {
            "step.alpha": lambda card: {"phase": "alpha"},
            "step.beta": lambda card: {},
            "step.gamma": lambda card: {},
        },
    )

    summary = new_engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "beta"
    assert summary.detail == "RuntimeError: the worker pool is gone"
    assert summary.results == {"alpha": {"phase": "alpha"}}
    assert _projected_phases(store) == [("alpha", "done")]
    assert _subtask_journal_statuses(store) == ["escalated"]


async def test_a_stop_during_a_usage_limit_wait_parks_before_the_same_phase(store):
    """The runner reports an interrupted wait: the phase is neither escalated nor
    done, it is queued again, and the paused agent parks before it."""
    stop = _StepStop(asyncio.get_running_loop())
    calls: list[str] = []

    def prepare(card: str) -> dict[str, Any]:
        return {}

    def finish(card: str) -> dict[str, Any]:
        raise AssertionError("no phase after the stop may start")

    def agent_runner(phase, context, rendered):
        calls.append(phase.name)
        stop.fire()
        raise LimitWaitInterrupted(phase.name)

    workflow = _workflow(STOP_MIXED, {"step.prepare": prepare, "step.finish": finish})

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
        stop=stop.signal,
    )

    assert calls == ["explore"]
    assert summary.status == "stopped"
    assert summary.failed_phase is None
    assert summary.detail == "stopped before explore"
    assert _projected_subtask_status(store) == "stopped"


def test_an_agent_turn_timeout_grows_by_the_runners_limit_wait_allowance():
    from agent_manager.runtime import compile as turns

    workflow = _workflow(STOP_MIXED, {"step.prepare": print, "step.finish": print})
    compiled = turns.compile_workflow(workflow)

    class Waiting:
        turn_allowance = 7200.0

    assert turns.turn_allowance(Waiting()) == 7200.0
    assert turns.turn_allowance(lambda *args: None) == 0.0
    assert turns.turn_allowance(None) == 0.0
    plain = compiled.turn_for("explore", 0).timeout
    assert compiled.turn_for("explore", 0, 7200.0).timeout == plain + 7200.0
    assert compiled.turn_for("prepare", 0, 7200.0).timeout == turns.STEP_TIMEOUT


# ── a store write that gave up stops the walk with no further write ─────────


def _checkpoint_reasons(opened) -> list[str]:
    return [
        row[0]
        for row in opened.connection.execute(
            "SELECT reason FROM checkpoints ORDER BY card_id, seq"
        ).fetchall()
    ]


def _busy(operation: str = "record_phase") -> store_db.StoreBusyError:
    return store_db.StoreBusyError(operation, 5, 10.0)


def test_a_busy_store_error_from_an_agent_phase_stops_the_walk_without_writing(
    store, run_subtask
):
    busy = _busy()
    calls: list[str] = []

    def a(card: str) -> dict[str, Any]:
        calls.append("a")
        return {}

    def b(card: str) -> dict[str, Any]:
        calls.append("b")
        return {}

    def agent_runner(phase, context, rendered):
        calls.append(phase.name)
        raise busy

    workflow = phase_model.Workflow(
        "busy_agent", (Step("a", a), _agent("explore", "explorer"), Step("b", b))
    )
    stop = StopSignal()

    with pytest.raises(store_db.StoreBusyError) as caught:
        run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            agent_runner=agent_runner,
            stop=stop,
        )

    # pygents and the bridge hand a tool's exception back as the same object.
    assert caught.value is busy
    assert calls == ["a", "explore"]
    assert stop.triggered is True
    assert stop.primary == STORY_ID
    assert _checkpoint_reasons(store) == ["turn", "turn"]
    newest = store.latest_checkpoint(_subtask().card_id)
    assert newest.reason == "turn"
    assert new_engine.pending_phase(newest) == "explore"
    assert _journalled_phases(store) == [("a", "started"), ("a", "done")]
    assert _subtask_journal_statuses(store) == []
    assert _projected_subtask_status(store) is None


def test_a_busy_store_error_from_a_critic_never_loops_back(store, run_subtask):
    busy = _busy()
    calls: list[str] = []

    def agent_runner(phase, context, rendered):
        calls.append(phase.name)
        if phase.name == "review":
            raise busy
        return {"ok": True}

    workflow = phase_model.Workflow(
        "busy_critic",
        (
            _agent("implement", "coder"),
            phase_model.AgentPhase(
                "review",
                role="reviewer",
                inputs=(),
                result=None,
                on_fail=phase_model.Goto("implement"),
            ),
        ),
    )

    with pytest.raises(store_db.StoreBusyError) as caught:
        run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            agent_runner=agent_runner,
        )

    assert caught.value is busy
    assert calls == ["implement", "review"]
    assert _checkpoint_reasons(store) == ["turn", "turn"]
    assert _projected_subtask_status(store) is None


@pytest.mark.parametrize("with_stop", [True, False], ids=["stop", "no-stop"])
def test_a_busy_done_record_of_a_step_stops_the_walk_without_writing(
    store, run_subtask, monkeypatch, with_stop
):
    busy = _busy()
    attempted: list[tuple[str, str]] = []
    real_record_phase = store.record_phase

    def record_phase(story_id, card_id, phase):
        attempted.append((phase.name, phase.status))
        if (phase.name, phase.status) == ("alpha", "done"):
            raise busy
        return real_record_phase(story_id, card_id, phase)

    monkeypatch.setattr(store, "record_phase", record_phase)
    calls: list[str] = []

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        return {}

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        return {}

    workflow = _workflow(TWO_PHASES, {"step.alpha": alpha, "step.beta": beta})
    stop = StopSignal() if with_stop else None

    with pytest.raises(store_db.StoreBusyError) as caught:
        run_subtask(
            workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO, stop=stop
        )

    assert caught.value is busy
    assert calls == ["alpha"]
    assert attempted == [("alpha", "started"), ("alpha", "done")]
    assert _checkpoint_reasons(store) == ["turn"]
    assert _subtask_journal_statuses(store) == []
    if stop is not None:
        assert stop.primary == STORY_ID


def test_a_busy_turn_checkpoint_stops_the_walk_without_writing(
    store, run_subtask, monkeypatch
):
    busy = _busy("save_checkpoint")
    saves: list[str] = []
    real_save = store.save_checkpoint

    def save_checkpoint(card_id, **kwargs):
        saves.append(kwargs["reason"])
        if len(saves) == 2:
            # The `BEFORE_TURN` save of `beta`'s turn, outside any phase.
            raise busy
        return real_save(card_id, **kwargs)

    monkeypatch.setattr(store, "save_checkpoint", save_checkpoint)
    calls: list[str] = []

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        return {}

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        return {}

    workflow = _workflow(TWO_PHASES, {"step.alpha": alpha, "step.beta": beta})
    stop = StopSignal()

    with pytest.raises(store_db.StoreBusyError) as caught:
        run_subtask(
            workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO, stop=stop
        )

    assert caught.value is busy
    assert calls == ["alpha"]
    assert saves == ["turn", "turn"]
    assert _checkpoint_reasons(store) == ["turn"]
    assert _journalled_phases(store) == [("alpha", "started"), ("alpha", "done")]
    assert _subtask_journal_statuses(store) == []
    assert stop.primary == STORY_ID


def test_a_busy_parked_checkpoint_stops_the_walk_without_writing(
    store, run_subtask, monkeypatch
):
    # Review Focus 4: a run already paused by a control when its `parked`
    # save is the write that stays busy.
    busy = _busy("save_checkpoint")
    real_save = store.save_checkpoint

    def save_checkpoint(card_id, **kwargs):
        if kwargs["reason"] == "parked":
            raise busy
        return real_save(card_id, **kwargs)

    monkeypatch.setattr(store, "save_checkpoint", save_checkpoint)
    calls: list[str] = []

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        return {}

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        return {}

    workflow = _workflow(TWO_PHASES, {"step.alpha": alpha, "step.beta": beta})
    stop = StopSignal()
    stop.request("pause")

    with pytest.raises(store_db.StoreBusyError) as caught:
        run_subtask(
            workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO, stop=stop
        )

    assert caught.value is busy
    assert calls == []
    assert _checkpoint_reasons(store) == []
    assert _projected_subtask_status(store) is None
    assert stop.primary == STORY_ID


def test_an_ordinary_step_error_still_escalates_and_never_triggers_the_stop(
    store, run_subtask
):
    # Regression guard: only a `StoreBusyError` takes the new path.
    def alpha(card: str) -> dict[str, Any]:
        raise RuntimeError("not the store")

    def beta(card: str) -> dict[str, Any]:
        return {}

    stop = StopSignal()
    summary = run_subtask(
        _workflow(TWO_PHASES, {"step.alpha": alpha, "step.beta": beta}),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop,
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "alpha"
    assert "RuntimeError: not the store" in summary.detail
    assert _checkpoint_reasons(store) == ["turn", "escalated"]
    assert _subtask_journal_statuses(store) == ["escalated"]
    assert stop.triggered is False
