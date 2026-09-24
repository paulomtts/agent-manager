"""Pure-functions tier (design spec §14): reads one packaged YAML file and
resolves names against the default registry.

Nothing outside this process is touched -- no filesystem beyond the packaged
YAML, no network, no git. The acceptance tests at the bottom of the file do
*call* the resolved gates, against result models dumped in memory, because a
gate that binds and returns the wrong verdict is the failure this file exists
to catch."""

import inspect
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from agent_manager import cli, dispatch, engine, models
from agent_manager.results import (
    RESULT_MODELS,
    CriticResult,
    ExploreResult,
    ImplementResult,
    PlanResult,
    ReviewResult,
    SpecResult,
    Verification,
    resolve_result_model,
)
from agent_manager.workflow import load_builtin
from agent_manager.workflow.loader import (
    AgentPhase,
    DeterministicPhase,
    builtin_path,
)
from agent_manager.workflow.registry import (
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
    default_registry,
)

# Design spec lines 146-225, in file order.
EXPECTED_PHASES = (
    ("worktree", "deterministic"),
    ("explore", "agent"),
    ("mark_in_progress", "deterministic"),
    ("plan_check", "deterministic"),
    ("spec", "agent"),
    ("validate_spec", "agent"),
    ("plan", "agent"),
    ("validate_plan", "agent"),
    ("mark_validated", "deterministic"),
    ("docs_commit", "deterministic"),
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


def test_builtin_task_has_the_fourteen_phases_in_spec_order() -> None:
    workflow = load_builtin("task")
    assert tuple((phase.name, phase.kind) for phase in workflow.phases) == EXPECTED_PHASES


def test_no_agent_phase_precedes_the_worktree_phase() -> None:
    """R6: every agent phase is dispatched with the subtask's worktree as its
    cwd (`dispatch.AgentRunner._worktree`), so `worktree.ensure` has to have run
    before the first of them. Asserted against the loaded document rather than
    the YAML text, so any future reorder of the file fails right here."""
    phases = load_builtin("task").phases
    names = [phase.name for phase in phases]
    agent_indexes = [
        index for index, phase in enumerate(phases) if isinstance(phase, AgentPhase)
    ]

    # Non-vacuity: a loader change that stopped yielding `AgentPhase` instances,
    # or a rename of the worktree phase, must fail here and not pass emptily.
    assert "worktree" in names
    assert len(agent_indexes) == 7

    assert names.index("worktree") < min(agent_indexes)


def test_plan_check_skips_forward_to_docs_commit_when_a_plan_exists() -> None:
    phase = load_builtin("task").phase("plan_check")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "plan_check.find_validated_plan"
    assert phase.when == "plan_check.has_validated_plan"
    assert phase.skip_to == "docs_commit"


def test_docs_commit_sits_between_the_marker_and_the_coder() -> None:
    """The hash this phase stamps is the hash of the plan file WITH the
    validated marker on it, which is the hash `review` recomputes -- so it has
    to run after `mark_validated`. It must also run before `implement`, or the
    coder's own `git add` would sweep the documents into its commit and the
    docs commit would never exist."""
    workflow = load_builtin("task")
    phase = workflow.phase("docs_commit")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "docs_commit.commit_documents"
    # No args: `bind_arguments` takes card_details, spec_path, plan_path and
    # worktree from the context by parameter name. Not best-effort and not
    # gated: an uncommitted or untagged pair of documents must escalate.
    assert phase.args == {}
    assert phase.gates == []
    assert phase.best_effort is False
    assert phase.when is None
    assert phase.skip_to is None

    names = workflow.phase_names
    assert names.index("mark_validated") < names.index("docs_commit")
    assert names.index("docs_commit") < names.index("implement")


def test_mark_validated_stamps_the_plan_between_validation_and_implement() -> None:
    """Placement IS the guard: `validate_plan` is gated by
    `critic_blockers_gate`, and a non-retryable gate failure escalates the
    subtask out of the walk (engine.run_subtask) before this index is reached.
    So an unvalidated plan is never marked, with no extra logic here."""
    workflow = load_builtin("task")
    phase = workflow.phase("mark_validated")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "plan_check.mark_validated"
    # No args: `bind_arguments` takes `plan_path` and `worktree` from the
    # context by parameter name. No gates and not best-effort: an unmarked plan
    # makes the next run re-plan, so a failure here must escalate.
    assert phase.args == {}
    assert phase.gates == []
    assert phase.best_effort is False
    assert phase.when is None
    assert phase.skip_to is None

    names = workflow.phase_names
    assert names.index("validate_plan") < names.index("mark_validated")
    assert names.index("mark_validated") < names.index("implement")


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


def test_spec_declares_the_spec_result_and_still_writes_the_specs_document() -> None:
    phase = load_builtin("task").phase("spec")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "spec_author"
    # `spec_path` is declared as an input as well as a `writes:` template: the
    # template is how the engine derives the path, and the input is how the
    # agent is told it (nothing renders `writes:` into a brief).
    assert phase.inputs == ["card", "explore", "spec_path"]
    assert phase.result == "SpecResult"
    # `writes:` stays: engine._document_paths derives spec_path from this
    # template, not from the result's `path` field.
    assert phase.writes == "docs/superpowers/specs/{stem}.md"
    assert phase.gates == []
    assert phase.retry is None


def test_every_agent_phase_in_the_shipped_document_declares_a_result() -> None:
    """The seam this card closes: an agent phase with no `result:` produces no
    evidence a model ever validates."""
    agent_phases = [
        phase for phase in load_builtin("task").phases if isinstance(phase, AgentPhase)
    ]

    assert len(agent_phases) == 7  # non-vacuity
    assert [phase.name for phase in agent_phases if phase.result is None] == []


def test_review_carries_both_of_its_gates() -> None:
    phase = load_builtin("task").phase("review")
    assert isinstance(phase, AgentPhase)
    assert phase.gates == ["review_gate", "plan_hash_gate"]
    assert phase.inputs == ["branch", "base_branch", "plan_path"]
    # The reviewer recomputes the hash from the plan file; card f26b377d gives
    # the input to the coder only.
    assert "plan_hash" not in phase.inputs


def test_implement_is_handed_the_plan_hash_last_after_the_documents() -> None:
    """Card f26b377d: the coder cannot stamp a trailer it was never told. The
    hash renders last, after the documents and the branches, because that order
    is the document author's emphasis and `render_prompt` preserves it."""
    phase = load_builtin("task").phase("implement")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "coder"
    assert phase.result == "ImplementResult"
    assert phase.inputs == [
        "plan_path",
        "spec_path",
        "branch",
        "base_branch",
        "plan_hash",
    ]
    # Decision O7: a coder that reports `blocked: true` stops the subtask here.
    assert phase.gates == ["implement_blocked_gate"]
    # Not retryable on purpose: re-dispatching the same brief repeats the answer.
    assert phase.retry is None


def test_no_phase_declares_plan_hash_before_docs_commit_runs() -> None:
    """Review focus: `plan_hash` reads `context["docs_commit"]`, which the
    engine only binds once that phase has run. A phase declaring it earlier
    would raise at render time, mid-run. Driven from the loaded document, never
    a hardcoded list."""
    workflow = load_builtin("task")
    names = workflow.phase_names
    declaring = [
        phase.name
        for phase in workflow.phases
        if isinstance(phase, AgentPhase) and "plan_hash" in phase.inputs
    ]

    assert declaring == ["implement"]  # non-vacuity
    for name in declaring:
        assert names.index("docs_commit") < names.index(name)


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


def test_a_substituted_registry_is_the_one_actually_used() -> None:
    """`registry=` is the embedder's seam; if it were ignored, the default
    registry would resolve this document and nothing would ever notice."""
    with pytest.raises(UnknownFunctionError):
        load_builtin("task", FunctionRegistry())


def test_a_substituted_registry_supplies_the_resolved_callables() -> None:
    substitute = default_registry()
    workflow = load_builtin("task", substitute)
    for name, fn in workflow.functions.items():
        assert fn is substitute.resolve(name)


def test_every_declared_result_name_resolves_through_the_shipped_table() -> None:
    """Addendum §1 seam 1 closed: renaming a model, or a `result:` name in
    `task.yaml`, fails here rather than one second into a production run.

    Driven from the loaded phases, never a hardcoded list -- a hardcoded list
    would keep passing while the document drifted away from it."""
    checked = 0
    for phase in load_builtin("task").phases:
        if not isinstance(phase, AgentPhase) or phase.result is None:
            continue
        model = resolve_result_model(phase.result, RESULT_MODELS, phase=phase.name)
        assert issubclass(model, BaseModel)
        assert model.__name__ == phase.result
        checked += 1
    # Non-vacuity: a loader change that stopped yielding agent phases, or
    # stopped carrying `result`, would otherwise turn this into a no-op.
    assert checked == 7  # CriticResult is declared by two phases


def test_both_validation_phases_resolve_to_the_same_critic_model() -> None:
    """`CriticResult` is declared twice (`task.yaml` lines 39 and 53).
    Resolution is by name, so the duplicate is one shared class, not a clash."""
    workflow = load_builtin("task")
    spec_phase = workflow.phase("validate_spec")
    plan_phase = workflow.phase("validate_plan")
    assert isinstance(spec_phase, AgentPhase) and isinstance(plan_phase, AgentPhase)

    spec_model = resolve_result_model(
        spec_phase.result, RESULT_MODELS, phase="validate_spec"
    )
    plan_model = resolve_result_model(
        plan_phase.result, RESULT_MODELS, phase="validate_plan"
    )

    assert spec_model is plan_model
    assert spec_model is CriticResult


# ── acceptance #2: every gate binds against a real result object ─────────────
# Design §3: "Every gate named in builtin/task.yaml binds against a real result
# object." Real means a result MODEL instance dumped with
# `model_dump(mode="json")`, exactly as `dispatch.py:244` hands it to the gates
# -- not a hand-written dict, which is how the camelCase drift survived.

REPO_DIR = Path("/repo")
SUITE = ["uv run pytest"]
PLAN_HASH = "a1b2c3d4"
SPEC_PATH = "docs/superpowers/specs/implement-critic-e5c05fd2.md"
PLAN_PATH = "docs/superpowers/plans/implement-critic-e5c05fd2.md"
REAL_SUMMARY = (
    "engine.bind_arguments binds gate parameters by name out of the table "
    "engine._gate_values builds, so every gate in builtin/task.yaml has to name "
    "keys that table actually holds."
)

SUBTASK = models.SubtaskRun(
    card_id="e5c05fd2",
    branch="m2/task-implement-critic-e5c05fd2",
    base_branch="m2/story-result-contract",
    status="started",
    worktree_path=Path("/repo/.claude/worktrees/m2/task-implement-critic-e5c05fd2"),
)
SUBTASK_CARD = models.Card(
    id="6f1a2f2e-1f1c-4f0e-9a6d-0c2f3b4a5d6e",
    title="Implement critic_blockers_gate and bind every gate parameter",
    status="todo",
    parent_id="5cc741ec-2a3b-4c5d-8e9f-0a1b2c3d4e5f",
)
PARENT_CARD = models.Card(
    id="5cc741ec-2a3b-4c5d-8e9f-0a1b2c3d4e5f",
    title="The result contract: models, gates and bindings",
    status="in_progress",
)


def _explore_result(full_suite: list[str] | None = None) -> dict[str, Any]:
    return ExploreResult(
        refused=False,
        reason=None,
        summary=REAL_SUMMARY,
        verification=Verification(
            full_suite=list(SUITE if full_suite is None else full_suite),
            typecheck="",
            lint=[],
        ),
    ).model_dump(mode="json")


def _critic_result(blockers: bool = False) -> dict[str, Any]:
    return CriticResult(
        blockers=blockers,
        reason="the plan skips the read shim" if blockers else None,
        summary=REAL_SUMMARY,
    ).model_dump(mode="json")


def _spec_result() -> dict[str, Any]:
    return SpecResult(path=SPEC_PATH, note=None).model_dump(mode="json")


def _plan_result() -> dict[str, Any]:
    return PlanResult(path=PLAN_PATH, self_reviewed=True, note=None).model_dump(
        mode="json"
    )


def _implement_result() -> dict[str, Any]:
    return ImplementResult(
        blocked=False,
        blocked_reason=None,
        resumed=False,
        plan_hash=PLAN_HASH,
        report=REAL_SUMMARY,
    ).model_dump(mode="json")


def _review_result(commit_count: int = 3, tagged_count: int = 3) -> dict[str, Any]:
    return ReviewResult(
        findings=[],
        unresolved_blockers=[],
        fix_summary="nothing needed fixing",
        porcelain="",
        commit_count=commit_count,
        tagged_count=tagged_count,
        plan_hash=PLAN_HASH,
    ).model_dump(mode="json")


def _phase_results() -> dict[str, Any]:
    """What each phase leaves in the binding table on a healthy run.

    Agent phases leave their validated result's JSON dump (`dispatch.py:244`);
    deterministic phases leave whatever mapping their step returned
    (`engine._bind_result`), which is why those are plain dicts. `worktree` is
    absent on purpose: its name is reserved, so its result never reaches the
    table at all.
    """
    return {
        "explore": _explore_result(),
        "mark_in_progress": {"status": "in_progress"},
        "plan_check": {"found": False},
        "spec": _spec_result(),
        "validate_spec": _critic_result(),
        "plan": _plan_result(),
        "validate_plan": _critic_result(),
        "docs_commit": {"plan_hash": PLAN_HASH},
        "implement": _implement_result(),
        "review": _review_result(),
        "verify": {"passed": True, "detail": ""},
    }


def _values_for(phase_name: str, result: Any = None) -> dict[str, Any]:
    """The binding table this phase's gates really see, built as production does.

    `engine.subtask_context` plus the caller's own `cli.gate_context` plus the
    document paths, then every earlier phase's result under its own name, then
    `dispatch.gate_values`' `result` / `<phase name>` overlay --
    `engine._gate_values` builds the identical table for the deterministic
    `verify` phase.
    """
    results = _phase_results()
    context = engine.subtask_context(
        SUBTASK, REPO_DIR, SUITE, card=SUBTASK_CARD, parent_story=PARENT_CARD
    )
    context.update(cli.gate_context(SUITE, False))
    context.update({"spec_path": SPEC_PATH, "plan_path": PLAN_PATH})
    for name in load_builtin("task").phase_names:
        if name == phase_name:
            break
        if name in results and name not in engine.RESERVED_CONTEXT_KEYS:
            context[name] = results[name]
    return dispatch.gate_values(
        context, phase_name, results[phase_name] if result is None else result
    )


GATED_PHASES = [
    (phase.name, gate) for phase in load_builtin("task").phases for gate in phase.gates
]


def test_the_document_still_names_exactly_the_gates_this_suite_covers() -> None:
    """The parametrisation below is only a net if this list is the document's.
    A gate added to `task.yaml` must land here, and then in `_phase_results`."""
    assert GATED_PHASES == [
        ("explore", "exploration_output_gate"),
        ("explore", "verification_gate"),
        ("validate_spec", "critic_blockers_gate"),
        ("validate_plan", "critic_blockers_gate"),
        ("implement", "implement_blocked_gate"),
        ("review", "review_gate"),
        ("review", "plan_hash_gate"),
        ("verify", "verification_passed_gate"),
    ]


@pytest.mark.parametrize(("phase_name", "gate_name"), GATED_PHASES)
def test_every_gate_binds_every_parameter_against_real_results(
    phase_name: str, gate_name: str
) -> None:
    """Seam 3 closed: no gate in the shipped document has a parameter the gate
    table cannot supply. A future unbindable gate fails right here."""
    gate = load_builtin("task").function(gate_name)
    bound = engine.bind_arguments(
        gate, _values_for(phase_name), phase=phase_name, function=gate_name
    )
    assert set(bound) == set(inspect.signature(gate).parameters)


@pytest.mark.parametrize(("phase_name", "gate_name"), GATED_PHASES)
def test_every_gate_passes_on_a_healthy_run(phase_name: str, gate_name: str) -> None:
    gate = load_builtin("task").function(gate_name)
    bound = engine.bind_arguments(
        gate, _values_for(phase_name), phase=phase_name, function=gate_name
    )
    assert gate(**bound) is None


def test_review_gate_reads_a_real_dumped_zero_commit_count_and_blocks() -> None:
    """The assertion that would have caught the camelCase drift: binding alone
    would not, because a vacuous gate binds perfectly well and returns a warn."""
    gate = load_builtin("task").function("review_gate")
    values = _values_for("review", _review_result(commit_count=0, tagged_count=0))
    verdict = gate(
        **engine.bind_arguments(gate, values, phase="review", function="review_gate")
    )
    assert verdict["blocked"] == "implement"
    assert "no commits on top of m2/story-result-contract" in verdict["detail"]


def test_review_gate_reads_a_real_dumped_untagged_count_and_blocks() -> None:
    gate = load_builtin("task").function("review_gate")
    values = _values_for("review", _review_result(commit_count=3, tagged_count=1))
    verdict = gate(
        **engine.bind_arguments(gate, values, phase="review", function="review_gate")
    )
    assert verdict["blocked"] == "implement"
    assert "only 1 of 3 commits" in verdict["detail"]


def test_the_plan_hash_gate_compares_the_two_real_dumped_hashes() -> None:
    gate = load_builtin("task").function("plan_hash_gate")
    values = _values_for("review")
    values["implement"] = ImplementResult(
        blocked=False,
        blocked_reason=None,
        resumed=False,
        plan_hash="0badcafe",
        report=REAL_SUMMARY,
    ).model_dump(mode="json")
    verdict = gate(
        **engine.bind_arguments(gate, values, phase="review", function="plan_hash_gate")
    )
    assert "plan hash CHANGED mid-run" in verdict["detail"]


def test_exploration_output_gate_matches_a_caller_provided_suite() -> None:
    # `provided_verification` is the caller's own dict, not a result model, so
    # it keeps the harness JSON's `fullSuite` spelling; the explore side is the
    # snake_case dump of a real `ExploreResult`.
    gate = load_builtin("task").function("exploration_output_gate")
    values = _values_for("explore")
    values["provided_verification"] = {"fullSuite": list(SUITE)}
    bound = engine.bind_arguments(
        gate, values, phase="explore", function="exploration_output_gate"
    )
    assert gate(**bound) is None


def test_exploration_output_gate_still_catches_an_implausible_dumped_suite() -> None:
    gate = load_builtin("task").function("exploration_output_gate")
    values = _values_for("explore", _explore_result(full_suite=["a"]))
    bound = engine.bind_arguments(
        gate, values, phase="explore", function="exploration_output_gate"
    )
    assert "implausible command" in gate(**bound)["detail"]


@pytest.mark.parametrize("phase_name", ["validate_spec", "validate_plan"])
def test_critic_blockers_gate_fires_on_both_validation_phases(phase_name: str) -> None:
    gate = load_builtin("task").function("critic_blockers_gate")
    values = _values_for(phase_name, _critic_result(blockers=True))
    bound = engine.bind_arguments(
        gate, values, phase=phase_name, function="critic_blockers_gate"
    )
    assert gate(**bound) == {
        "blocked": "validation",
        "detail": "the plan skips the read shim",
    }


def test_verification_passed_gate_blocks_a_red_suite_on_the_verify_phase() -> None:
    gate = load_builtin("task").function("verification_passed_gate")
    values = _values_for("verify", {"passed": False, "detail": "2 failed, 0 passed"})
    bound = engine.bind_arguments(
        gate, values, phase="verify", function="verification_passed_gate"
    )
    assert gate(**bound) == {"blocked": "verification", "detail": "2 failed, 0 passed"}


def test_implement_blocked_gate_reads_a_real_dumped_blocked_result_and_blocks() -> None:
    """Binding alone would not catch a gate that reads the wrong key: this feeds
    it the snake_case dump of a real `ImplementResult`, as `dispatch.py` does."""
    gate = load_builtin("task").function("implement_blocked_gate")
    blocked = ImplementResult(
        blocked=True,
        blocked_reason="the baseline suite was already red: 3 failed",
        resumed=False,
        plan_hash=PLAN_HASH,
        report=REAL_SUMMARY,
    ).model_dump(mode="json")
    values = _values_for("implement", blocked)
    verdict = gate(
        **engine.bind_arguments(
            gate, values, phase="implement", function="implement_blocked_gate"
        )
    )
    assert verdict == {
        "blocked": "implement",
        "detail": "the baseline suite was already red: 3 failed",
    }
