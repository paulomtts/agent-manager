"""Behaviour of the phase walk and deterministic phase execution (spec §6, §9, §12).

Engine tier per design §14 lines 477-492: canned fake functions in a hand-built
`FunctionRegistry` stand in for §14's fake adapter with canned result files, and
the store is a real temp SQLite projection plus a real temp JSONL journal. No
git, no `brd`, no harness process, nothing from `default_registry()` -- three of
its names are placeholders that raise `NotImplementedError`.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import engine, models, store as store_module
from agent_manager.errors import AgentPhaseFailed
from agent_manager.workflow.loader import AgentPhase, load_builtin, load_workflow
from agent_manager.workflow.registry import BUILTIN_FUNCTION_NAMES, FunctionRegistry

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
    context = engine.subtask_context(_subtask(), REPO, ["uv run pytest"])

    assert context == {
        "card": "ed77a917",
        "card_details": None,
        "parent_story_details": None,
        "branch": "m1/task-ed77a917",
        "base": "m1/story-base",
        "worktree": Path("/repo/.claude/worktrees/m1/task-ed77a917"),
        "repo_dir": REPO,
        "commands": ["uv run pytest"],
    }


def test_the_cards_land_under_the_details_keys_and_leave_the_id_string_alone():
    """§7's `card` input and the steps' `card` parameter are different things:
    `plan_check.find_validated_plan(card)` binds the bare id string, and turning
    that key into a `Card` would break every deterministic step at once.
    """
    context = engine.subtask_context(
        _subtask(), REPO, ["uv run pytest"], card=CARD, parent_story=PARENT
    )

    assert context["card"] == "ed77a917"
    assert context["card_details"] is CARD
    assert context["parent_story_details"] is PARENT


def test_the_new_context_keys_are_reserved_against_a_same_named_phase():
    for key in ("card_details", "parent_story_details", "spec_path", "plan_path"):
        assert key in engine.RESERVED_CONTEXT_KEYS


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


RUN_ID = "run-2026-09-23-01"
STORY_ID = "2143808b"


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A real temp projection plus a real temp journal, writing nowhere real."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


def _registry(functions: dict[str, Any]) -> FunctionRegistry:
    registry = FunctionRegistry()
    for name, fn in functions.items():
        registry.register(name, fn)
    return registry


def _workflow(document: str, functions: dict[str, Any]):
    return load_workflow(document, _registry(functions))


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


THREE_PHASES = """
name: three
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
  - name: beta
    kind: deterministic
    run: step.beta
  - name: gamma
    kind: deterministic
    run: step.gamma
"""


def test_phases_run_in_document_order(store):
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

    summary = engine.run_subtask(
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


def test_a_phase_result_is_bound_into_a_later_phase(store):
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

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen["alpha"] == {"plan": "docs/plan.md"}


def test_declared_args_reach_the_step_as_a_keyword(store):
    seen: list[str] = []

    def set_status(card: str, status: str) -> dict[str, Any]:
        seen.append(status)
        return {"status": status}

    document = """
name: board
phases:
  - name: mark_in_progress
    kind: deterministic
    run: rollup.set_status
    args: { status: in_progress }
"""
    workflow = _workflow(document, {"rollup.set_status": set_status})

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen == ["in_progress"]


def test_a_step_is_called_with_only_the_parameters_it_declares(store):
    seen: dict[str, Any] = {}

    def ensure(branch: str, base: str) -> dict[str, Any]:
        seen.update(branch=branch, base=base)
        return {}

    document = """
name: one
phases:
  - name: worktree
    kind: deterministic
    run: worktree.ensure
"""
    workflow = _workflow(document, {"worktree.ensure": ensure})

    engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        commands=["uv run pytest"],
    )

    assert seen == {"branch": "m1/task-ed77a917", "base": "m1/story-base"}


def test_every_state_edge_is_journalled_before_the_row_is_written(store):
    def step(card: str) -> dict[str, Any]:
        return {}

    document = """
name: two
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
  - name: beta
    kind: deterministic
    run: step.beta
"""
    workflow = _workflow(document, {"step.alpha": step, "step.beta": step})

    engine.run_subtask(
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


def test_no_attempt_row_is_written_for_a_deterministic_phase(store):
    def step(card: str) -> dict[str, Any]:
        return {}

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": step})

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert store.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    assert not any(line.event == "attempt_upsert" for line in store.journal.read())


def test_a_phase_named_like_a_context_key_runs_but_never_clobbers_it(store):
    """The `worktree` phase of the shipped `builtin/task.yaml` names itself the
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

    document = """
name: collide
phases:
  - name: worktree
    kind: deterministic
    run: worktree.make
  - name: after
    kind: deterministic
    run: step.after
"""
    workflow = _workflow(
        document, {"worktree.make": make_worktree, "step.after": uses_worktree}
    )

    summary = engine.run_subtask(
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


def test_a_raising_step_escalates_and_the_exception_does_not_propagate(store):
    calls: list[str] = []

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        raise OSError("disk went away")

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        return {}

    document = """
name: two
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
  - name: beta
    kind: deterministic
    run: step.beta
"""
    workflow = _workflow(document, {"step.alpha": alpha, "step.beta": beta})

    summary = engine.run_subtask(
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


def test_a_binding_failure_escalates_without_calling_the_step(store):
    calls: list[str] = []

    def alpha(card: str, missing_thing: str) -> dict[str, Any]:
        calls.append("alpha")
        return {}

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == []
    assert summary.status == "escalated"
    assert "missing_thing" in summary.detail


@pytest.mark.parametrize("returned", [None, ["a", "list"], True, "a string"])
def test_a_non_mapping_step_result_escalates(store, returned):
    def alpha(card: str) -> Any:
        return returned

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "alpha"
    assert "mapping" in summary.detail
    assert _projected_phases(store) == [("alpha", "failed")]


def test_a_failed_phase_result_is_not_offered_to_later_phases(store):
    def alpha(card: str) -> dict[str, Any]:
        raise RuntimeError("nope")

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.results == {}


GATED = """
name: gated
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
    gates: [alpha_gate]
  - name: beta
    kind: deterministic
    run: step.beta
"""


def test_a_passing_gate_lets_the_walk_continue(store):
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

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen["result"] == {"passed": True}
    assert summary.status == "done"
    assert summary.warnings == []
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done")]


def test_a_warning_gate_continues_and_surfaces_the_warning(store):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"warn": "the suite reported no tests"}

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"
    assert len(summary.warnings) == 1
    assert "alpha_gate" in summary.warnings[0]
    assert "the suite reported no tests" in summary.warnings[0]
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done")]


def test_a_failing_gate_escalates_and_stops_the_walk(store):
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

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["alpha"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "alpha"
    assert "alpha_gate" in summary.detail
    assert "2 of 3 commands failed" in summary.detail
    assert _journalled_phases(store) == [("alpha", "started"), ("alpha", "failed")]
    assert store.journal.read()[-1].payload["status"] == "escalated"


def test_a_warning_before_a_failing_gate_survives_into_the_summary(store):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def first_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"warn": "looked thin"}

    def second_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"blocked": "review", "detail": "unresolved blocker"}

    document = """
name: gated
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
    gates: [first_gate, second_gate]
  - name: beta
    kind: deterministic
    run: step.beta
"""
    workflow = _workflow(
        document,
        {
            "step.alpha": alpha,
            "step.beta": beta,
            "first_gate": first_gate,
            "second_gate": second_gate,
        },
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert any("looked thin" in warning for warning in summary.warnings)


@pytest.mark.parametrize("verdict", [False, True, "blocked", 0])
def test_a_gate_returning_neither_none_nor_a_mapping_escalates(store, verdict):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(result: dict[str, Any]) -> Any:
        return verdict

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert "mapping" in summary.detail


def test_a_gate_binds_the_phase_result_under_the_phase_name_too(store):
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

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen == {"alpha": {"ok": 1}, "branch": "m1/task-ed77a917"}


SKIPPING = """
name: skipping
phases:
  - name: plan_check
    kind: deterministic
    run: plan_check.find
    when: plan_check.has
    skip_to: implement
  - name: spec
    kind: deterministic
    run: step.spec
  - name: plan
    kind: deterministic
    run: step.plan
  - name: implement
    kind: deterministic
    run: step.implement
"""


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


def test_a_truthy_when_jumps_to_skip_to(store):
    calls: list[str] = []

    def has(result: dict[str, Any]) -> bool:
        return bool(result.get("validated"))

    workflow = _skipping_workflow(has, calls)

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["plan_check", "implement"]
    assert summary.status == "done"
    assert summary.skipped == ["spec", "plan"]
    assert _projected_phases(store) == [("plan_check", "done"), ("implement", "done")]
    assert set(summary.results) == {"plan_check", "implement"}


def test_a_falsy_when_continues_to_the_next_phase(store):
    calls: list[str] = []

    def has(result: dict[str, Any]) -> bool:
        return False

    workflow = _skipping_workflow(has, calls)

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["plan_check", "spec", "plan", "implement"]
    assert summary.skipped == []
    assert summary.status == "done"


def test_a_raising_when_fails_its_phase_rather_than_not_skipping(store):
    calls: list[str] = []

    def has(result: dict[str, Any]) -> bool:
        raise ValueError("unreadable plan front matter")

    workflow = _skipping_workflow(has, calls)

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["plan_check"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "plan_check"
    assert "unreadable plan front matter" in summary.detail
    assert _projected_phases(store) == [("plan_check", "failed")]


BEST_EFFORT = """
name: board
phases:
  - name: mark_in_progress
    kind: deterministic
    run: rollup.set_status
    args: { status: in_progress }
    best_effort: true
  - name: work
    kind: deterministic
    run: step.work
  - name: mark_done
    kind: deterministic
    run: rollup.done
    args: { status: done }
    best_effort: true
    gates: [done_gate]
"""


def test_a_best_effort_failure_warns_and_does_not_sink_the_subtask(store):
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

    summary = engine.run_subtask(
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


def test_a_best_effort_phase_with_a_failing_gate_only_warns(store):
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

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"
    assert len(summary.warnings) == 1
    assert "card is still in_progress" in summary.warnings[0]
    assert _projected_phases(store)[-1] == ("mark_done", "failed")


def test_a_best_effort_binding_failure_only_warns(store):
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

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"
    assert "missing_thing" in summary.warnings[0]


def test_a_failed_best_effort_phase_contributes_no_result(store):
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

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert set(summary.results) == {"work", "mark_done"}


MIXED = """
name: mixed
phases:
  - name: explore
    kind: agent
    role: explorer
    result: ExploreResult
  - name: work
    kind: deterministic
    run: step.work
"""


def test_an_agent_phase_goes_to_the_injected_runner(store):
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

    summary = engine.run_subtask(
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


def test_an_agent_phase_with_no_runner_is_a_named_engine_error(store):
    def work(card: str) -> dict[str, Any]:
        return {}

    workflow = _workflow(MIXED, {"step.work": work})

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
        )

    assert caught.value.phase == "explore"
    assert "agent runner" in str(caught.value)


def test_starting_at_a_named_phase_runs_only_from_there(store):
    calls: list[str] = []

    def make(name: str):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            return {}

        return step

    workflow = _workflow(
        THREE_PHASES,
        {"step.alpha": make("alpha"), "step.beta": make("beta"), "step.gamma": make("gamma")},
    )

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        start_phase="beta",
    )

    assert calls == ["beta", "gamma"]
    assert summary.status == "done"
    assert _projected_phases(store) == [("beta", "done"), ("gamma", "done")]


def test_an_unknown_starting_phase_is_an_error_before_anything_is_recorded(store):
    calls: list[str] = []

    def step(card: str) -> dict[str, Any]:
        calls.append("ran")
        return {}

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": step, "step.beta": step, "step.gamma": step}
    )

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            start_phase="beeta",
        )

    assert calls == []
    assert "'beeta'" in str(caught.value)
    assert store.connection.execute("SELECT COUNT(*) FROM phases").fetchone()[0] == 0


def test_the_builtin_task_document_walks_against_a_fake_registry(store):
    calls: list[str] = []

    def set_status(card: str, status: str) -> dict[str, Any]:
        calls.append(f"rollup.set_status:{status}")
        return {"card": card, "status": status}

    def ensure(branch: str, base: str, worktree: Any, repo_dir: Any) -> dict[str, Any]:
        calls.append("worktree.ensure")
        return {"created": True}

    def find_plan(card: str) -> dict[str, Any]:
        calls.append("plan_check.find_validated_plan")
        return {"found": False, "validated": False}

    def has_plan(result: dict[str, Any]) -> bool:
        return bool(result.get("validated"))

    def run_suite(commands: list[str], worktree: Any) -> dict[str, Any]:
        calls.append("verify.run_suite")
        return {"passed": True}

    def verification_passed_gate(result: dict[str, Any]) -> None:
        calls.append("verification_passed_gate")
        return None

    def agent_only_gate(**kwargs: Any) -> None:
        raise AssertionError("an agent phase's gate is the agent runner's business")

    functions: dict[str, Any] = {
        "rollup.set_status": set_status,
        "worktree.ensure": ensure,
        "plan_check.find_validated_plan": find_plan,
        "plan_check.has_validated_plan": has_plan,
        "verify.run_suite": run_suite,
        "verification_passed_gate": verification_passed_gate,
        "critic_blockers_gate": agent_only_gate,
        "exploration_output_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
        "verification_gate": agent_only_gate,
    }
    assert sorted(functions) == sorted(BUILTIN_FUNCTION_NAMES)

    def agent_runner(phase: AgentPhase, context: dict[str, Any], rendered) -> dict[str, Any]:
        calls.append(f"agent:{phase.name}")
        return {"role": phase.role}

    workflow = load_builtin("task", _registry(functions))

    summary = engine.run_subtask(
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
        "agent:explore",
        "rollup.set_status:in_progress",
        "worktree.ensure",
        "plan_check.find_validated_plan",
        "agent:spec",
        "agent:validate_spec",
        "agent:plan",
        "agent:validate_plan",
        "agent:implement",
        "agent:review",
        "verify.run_suite",
        "verification_passed_gate",
        "rollup.set_status:done",
    ]
    assert summary.status == "done"
    assert summary.warnings == []
    assert [name for name, _status in _projected_phases(store)] == [
        "mark_in_progress",
        "worktree",
        "plan_check",
        "verify",
        "mark_done",
    ]


def _journalled_details(opened) -> list[tuple[str | None, str, str | None]]:
    return [
        (line.phase, line.payload["status"], line.payload["detail"])
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ]


def test_a_raising_step_writes_why_it_failed_into_the_journal(store):
    """§9 makes the journal the truth the projection is rebuilt from, so the
    reason a phase failed has to be *in* it. The returned summary is in-memory
    only: an operator reading the audit trail after the process is gone would
    otherwise see `failed` with no cause at all.
    """

    def alpha(card: str) -> dict[str, Any]:
        raise OSError("disk went away")

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    started, failed = _journalled_details(store)
    assert started == ("alpha", "started", None)
    assert failed[:2] == ("alpha", "failed")
    assert "disk went away" in failed[2]
    assert failed[2] == summary.detail


def test_a_failing_gate_writes_its_verdict_into_the_journal(store):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"blocked": "verification", "detail": "2 of 3 commands failed"}

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    _started, failed = _journalled_details(store)
    assert failed[:2] == ("alpha", "failed")
    assert "alpha_gate" in failed[2]
    assert "2 of 3 commands failed" in failed[2]
    assert failed[2] == summary.detail


def test_a_best_effort_failure_is_journalled_with_its_reason_too(store):
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

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    details = dict(
        (phase, detail)
        for phase, status, detail in _journalled_details(store)
        if status == "failed"
    )
    assert "board is locked" in details["mark_in_progress"]


def test_a_phase_that_succeeds_journals_no_failure_detail(store):
    def step(card: str) -> dict[str, Any]:
        return {}

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": step})

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert _journalled_details(store) == [
        ("alpha", "started", None),
        ("alpha", "done", None),
    ]


def test_a_gate_on_a_phase_named_like_a_context_key_still_sees_the_real_value(store):
    """The mirror of the `_bind_result` guard, for the phase's own gate and
    `when`. The shipped `worktree` phase carries neither today, but a gate
    added to it that asks for `worktree` wants the path `worktree.ensure` was
    pointed at, not that call's return value -- exactly what the reserved-key
    guard protects for every *later* phase. The result stays reachable under
    `result`, which is the name the shipped gates bind by anyway.
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

    document = """
name: collide
phases:
  - name: worktree
    kind: deterministic
    run: worktree.ensure
    gates: [worktree_gate]
    when: when_worktree
    skip_to: after
  - name: after
    kind: deterministic
    run: step.after
"""
    workflow = _workflow(
        document,
        {
            "worktree.ensure": ensure,
            "worktree_gate": worktree_gate,
            "when_worktree": when_worktree,
            "step.after": lambda card: {},
        },
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    real_worktree = _subtask().worktree_path
    assert summary.status == "done"
    assert seen["worktree"] == real_worktree
    assert seen["when_worktree"] == real_worktree
    assert seen["result"] == {"created": True}


def test_a_phase_is_timed_with_the_injected_clock(store):
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

    document = """
name: two
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
  - name: beta
    kind: deterministic
    run: step.beta
"""
    workflow = _workflow(document, {"step.alpha": alpha, "step.beta": beta})

    engine.run_subtask(
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


def test_the_default_clock_stamps_an_aware_utc_time(store):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": alpha})
    before = datetime.now(timezone.utc)

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    after = datetime.now(timezone.utc)
    done = store.journal.read()[-2]
    assert done.payload["status"] == "done"
    started_at = datetime.fromisoformat(done.payload["started_at"])
    ended_at = datetime.fromisoformat(done.payload["ended_at"])
    assert started_at.tzinfo is not None
    assert before <= started_at <= ended_at <= after


DOCUMENT_PATHS = """
name: paths
phases:
  - name: spec
    kind: agent
    role: spec_author
    writes: docs/superpowers/specs/{stem}.md
  - name: plan
    kind: agent
    role: planner
    writes: docs/superpowers/plans/{stem}.md
  - name: implement
    kind: agent
    role: coder
    inputs: [spec_path, plan_path]
  - name: after
    kind: deterministic
    run: step.after
"""


def test_document_paths_are_bound_from_the_writes_templates(store):
    seen: dict[str, Any] = {}

    def after(spec_path: str, plan_path: str) -> dict[str, Any]:
        seen.update(spec_path=spec_path, plan_path=plan_path)
        return {}

    workflow = _workflow(DOCUMENT_PATHS, {"step.after": after})

    engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        card=CARD,
        agent_runner=lambda phase, context, rendered: {},
    )

    assert seen == {"spec_path": SPEC_PATH, "plan_path": PLAN_PATH}


def test_a_document_path_input_with_no_writing_phase_is_a_named_error(store):
    document = """
name: orphan
phases:
  - name: implement
    kind: agent
    role: coder
    inputs: [plan_path]
"""
    workflow = _workflow(document, {})

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
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


def test_a_document_path_input_with_no_card_is_a_named_error(store):
    workflow = _workflow(DOCUMENT_PATHS, {"step.after": lambda spec_path, plan_path: {}})

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            agent_runner=lambda phase, context, rendered: {},
        )

    assert caught.value.parameter in {"plan_path", "spec_path"}
    assert "no card was supplied" in str(caught.value)


def test_a_document_with_no_path_inputs_needs_no_card(store):
    """Every existing walk in this file passes no card; none may start failing."""
    workflow = _workflow(THREE_PHASES, {
        "step.alpha": lambda card: {},
        "step.beta": lambda card: {},
        "step.gamma": lambda card: {},
    })

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"


def _recording_runner(recorded: dict[str, Any]):
    def agent_runner(phase, context, rendered):
        recorded[phase.name] = rendered
        return {"role": phase.role}

    return agent_runner


def _builtin_functions(calls: list[str], *, validated: bool) -> dict[str, Any]:
    """The fake registry `builtin/task.yaml` needs, with no git, brd or harness."""

    def set_status(card: str, status: str) -> dict[str, Any]:
        calls.append(f"rollup.set_status:{status}")
        return {"card": card, "status": status}

    def ensure(branch: str, base: str, worktree: Any, repo_dir: Any) -> dict[str, Any]:
        calls.append("worktree.ensure")
        return {"created": True}

    def find_plan(card: str) -> dict[str, Any]:
        calls.append("plan_check.find_validated_plan")
        return {"found": validated, "validated": validated}

    def has_plan(result: dict[str, Any]) -> bool:
        return bool(result.get("validated"))

    def run_suite(commands: list[str], worktree: Any) -> dict[str, Any]:
        calls.append("verify.run_suite")
        return {"passed": True}

    def passed(result: dict[str, Any]) -> None:
        return None

    def agent_only_gate(**kwargs: Any) -> None:
        raise AssertionError("an agent phase's gate is the agent runner's business")

    return {
        "rollup.set_status": set_status,
        "worktree.ensure": ensure,
        "plan_check.find_validated_plan": find_plan,
        "plan_check.has_validated_plan": has_plan,
        "verify.run_suite": run_suite,
        "verification_passed_gate": passed,
        "critic_blockers_gate": agent_only_gate,
        "exploration_output_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
        "verification_gate": agent_only_gate,
    }


def _walk_builtin(store, recorded: dict[str, Any], *, validated: bool) -> Any:
    calls: list[str] = []
    workflow = load_builtin("task", _registry(_builtin_functions(calls, validated=validated)))
    return engine.run_subtask(
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


def test_every_document_path_input_renders_the_expanded_writes_template(store):
    recorded: dict[str, Any] = {}

    _walk_builtin(store, recorded, validated=False)

    for phase_name in ("validate_spec", "plan", "validate_plan", "implement"):
        assert dict(recorded[phase_name].sections)["spec_path"] == SPEC_PATH
    for phase_name in ("validate_plan", "implement", "review"):
        assert dict(recorded[phase_name].sections)["plan_path"] == PLAN_PATH


def test_implement_gets_both_paths_even_when_plan_check_skipped_spec_and_plan(store):
    recorded: dict[str, Any] = {}

    summary = _walk_builtin(store, recorded, validated=True)

    assert summary.skipped == ["spec", "validate_spec", "plan", "validate_plan"]
    assert set(recorded) == {"explore", "implement", "review"}
    sections = dict(recorded["implement"].sections)
    assert sections["spec_path"] == SPEC_PATH
    assert sections["plan_path"] == PLAN_PATH


def test_each_agent_phase_receives_exactly_the_inputs_it_declares(store):
    """§13: a phase receives its declared inputs and nothing else."""
    recorded: dict[str, Any] = {}

    _walk_builtin(store, recorded, validated=False)

    assert {name: rendered.inputs for name, rendered in recorded.items()} == {
        "explore": ("card", "parent_story", "repo_docs", "verification"),
        "spec": ("card", "explore"),
        "validate_spec": ("card", "spec_path"),
        "plan": ("spec_path",),
        "validate_plan": ("spec_path", "plan_path"),
        "implement": ("plan_path", "spec_path", "branch", "base_branch"),
        "review": ("branch", "base_branch", "plan_path"),
    }


def test_the_explore_prompt_reads_the_cards_not_the_reserved_card_key(store):
    recorded: dict[str, Any] = {}

    _walk_builtin(store, recorded, validated=False)

    sections = dict(recorded["explore"].sections)
    assert json.loads(sections["card"])["title"] == "Resolve phase inputs"
    assert json.loads(sections["parent_story"])["title"] == (
        "The workflow document and the engine"
    )
    assert json.loads(sections["verification"]) == ["uv run pytest"]


def test_an_unresolvable_input_raises_out_of_the_walk_before_the_runner(store):
    """The walk does not wrap the runner call, so resolution failures propagate.
    Journalling them as an outcome is sibling bf8e415b's choice, not this one's.
    """
    called: list[str] = []

    def agent_runner(phase, context, rendered):
        called.append(phase.name)
        return {}

    document = """
name: early
phases:
  - name: spec
    kind: agent
    role: spec_author
    inputs: [explore]
"""
    workflow = _workflow(document, {})

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
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


def test_a_phase_named_spec_path_never_clobbers_the_document_path(store):
    seen: dict[str, Any] = {}

    def collide(card: str) -> dict[str, Any]:
        return {"not": "a path"}

    def after(spec_path: str) -> dict[str, Any]:
        seen["spec_path"] = spec_path
        return {}

    document = """
name: reserved
phases:
  - name: spec
    kind: agent
    role: spec_author
    writes: docs/superpowers/specs/{stem}.md
  - name: spec_path
    kind: deterministic
    run: step.collide
  - name: implement
    kind: agent
    role: coder
    inputs: [spec_path]
  - name: after
    kind: deterministic
    run: step.after
"""
    recorded: dict[str, Any] = {}
    workflow = _workflow(document, {"step.collide": collide, "step.after": after})

    summary = engine.run_subtask(
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


RESERVED_DETAILS = """
name: reserved-details
phases:
  - name: card_details
    kind: deterministic
    run: step.collide
  - name: parent_story_details
    kind: deterministic
    run: step.collide
  - name: explore
    kind: agent
    role: explorer
    inputs: [card, parent_story]
"""


def test_a_phase_named_card_details_never_clobbers_the_cards_the_prompt_renders(store):
    """The membership check above is only a constant; this is the behaviour it buys.

    A document is free to name a phase `card_details`, and its result must not
    become what the next phase's `card` input renders.
    """
    recorded: dict[str, Any] = {}
    workflow = _workflow(RESERVED_DETAILS, {"step.collide": lambda card: {"not": "a card"}})

    summary = engine.run_subtask(
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



def test_a_failed_agent_phase_escalates_the_subtask_and_stops(store):
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

    summary = engine.run_subtask(
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


def test_an_unexpected_error_from_the_agent_runner_escalates_rather_than_crashing(store):
    # Symmetric with `_run_deterministic`'s deliberately total except: an
    # exception escaping the walk would leave the subtask recorded `started`
    # forever, which is exactly what resume mistakes for work in flight.
    def work(card: str) -> dict[str, Any]:
        raise AssertionError("no phase after the failed one may start")

    def agent_runner(phase, context, rendered):
        raise OSError("the run directory went away")

    workflow = _workflow(MIXED, {"step.work": work})

    summary = engine.run_subtask(
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


def test_a_successful_agent_phase_still_advances_the_walk(store):
    """The existing happy path must not change shape under the new try/except."""
    def work(card: str, explore: dict[str, Any]) -> dict[str, Any]:
        return {"saw": explore["summary"]}

    workflow = _workflow(MIXED, {"step.work": work})

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=lambda phase, context, rendered: {"summary": "explored"},
    )

    assert summary.status == "done"
    assert summary.results["work"] == {"saw": "explored"}


def test_extra_context_reaches_a_deterministic_phase_binding(tmp_path: Path):
    """The §12 escape hatch's parameters have to arrive somehow: `subtask_context`
    is a fixed table, and `builtin/task.yaml`'s gates bind names it does not hold.
    """
    seen: dict[str, Any] = {}

    def step(suite_cmds: list[str], allow_no_verification: bool) -> dict[str, Any]:
        seen["suite_cmds"] = suite_cmds
        seen["allow_no_verification"] = allow_no_verification
        return {"ok": True}

    registry = FunctionRegistry()
    registry.register("only.step", step)
    document = tmp_path / "one.yaml"
    document.write_text(
        "name: one\n"
        "description: one deterministic phase\n"
        "phases:\n"
        "  - name: only\n"
        "    kind: deterministic\n"
        "    run: only.step\n",
        encoding="utf-8",
    )
    workflow = load_workflow(document, registry)
    store = store_module.Store.open(tmp_path, "run-extra-1")

    summary = engine.run_subtask(
        workflow,
        store,
        story_id="story-1",
        subtask=_subtask(),
        repo_dir=REPO,
        extra_context={"suite_cmds": [], "allow_no_verification": True},
    )

    assert summary.status == "done"
    assert seen == {"suite_cmds": [], "allow_no_verification": True}


def test_extra_context_may_not_redefine_a_reserved_key(tmp_path: Path):
    """`worktree`, `card` and friends are the engine's own: letting a caller
    overwrite one would point every later step at a path the engine never chose.
    """
    registry = FunctionRegistry()
    registry.register("only.step", lambda: {"ok": True})
    document = tmp_path / "one.yaml"
    document.write_text(
        "name: one\n"
        "description: one deterministic phase\n"
        "phases:\n"
        "  - name: only\n"
        "    kind: deterministic\n"
        "    run: only.step\n",
        encoding="utf-8",
    )
    workflow = load_workflow(document, registry)
    store = store_module.Store.open(tmp_path, "run-extra-2")

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow,
            store,
            story_id="story-1",
            subtask=_subtask(),
            repo_dir=REPO,
            extra_context={"worktree": "/somewhere/else"},
        )

    assert "worktree" in str(caught.value)
