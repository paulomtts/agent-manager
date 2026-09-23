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

from agent_manager import engine, models, store as store_module
from agent_manager.workflow.loader import load_workflow
from agent_manager.workflow.registry import FunctionRegistry

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
