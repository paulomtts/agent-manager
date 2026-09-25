"""Pure-functions tier (design spec §14): the packaged `builtin/integrate.yaml`
loaded against the default registry (Integrate addendum I3, card b4bd3795).

Nothing outside this process is touched -- no git, no network, no filesystem
beyond the packaged YAML. `merge_completed_gate` is bound here but never
called: calling it runs git, and that is the engine tier's job
(`tests/test_integrate_workflow.py`)."""

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from agent_manager import dispatch, engine, models, prompt
from agent_manager.results import RESULT_MODELS, ResolveResult, resolve_result_model
from agent_manager.steps import integrate, reducers, verify
from agent_manager.workflow import load_builtin
from agent_manager.workflow.loader import AgentPhase, DeterministicPhase, builtin_path
from agent_manager.workflow.registry import default_registry

EXPECTED_PHASES = (("resolve", "agent"), ("verify", "deterministic"))
RESOLVE_INPUTS = ["branch", "base_branch", "merge_tip", "conflict_files", "verification"]
INTEGRATE_FUNCTIONS = {"merge_completed_gate", "verification_passed_gate", "verify.run_suite"}

REPO_DIR = Path("/repo")
SUITE = ["uv run pytest"]
INTEGRATION_BRANCH = "m5-integrate"
BASE_BRANCH = "main"
MERGE_TIP = "m5/story-the-resolver-5216cbee"
CONFLICT_FILES = ["src/agent_manager/prompt.py", "docs/notes with space.md"]

# Integrate addendum I3: card = the conflicting story's id, branch = the
# integration branch, base = the base branch, worktree = the integration worktree.
SUBTASK = models.SubtaskRun(
    card_id="5216cbee",
    branch=INTEGRATION_BRANCH,
    base_branch=BASE_BRANCH,
    status="started",
    worktree_path=Path("/repo/.claude/worktrees/m5-integrate"),
)
EXTRA_CONTEXT = {"merge_tip": MERGE_TIP, "conflict_files": list(CONFLICT_FILES)}


def test_builtin_integrate_loads_against_the_default_registry() -> None:
    workflow = load_builtin("integrate")
    assert workflow.name == "integrate"
    assert workflow.description
    assert builtin_path("integrate").is_file()


def test_builtin_integrate_is_resolve_then_verify_with_no_worktree_phase() -> None:
    """The integration worktree arrives through `SubtaskRun.worktree_path`
    (context key `worktree`); `merge_tip` already made it, so no phase here may
    create or re-point one."""
    workflow = load_builtin("integrate")
    assert tuple((phase.name, phase.kind) for phase in workflow.phases) == EXPECTED_PHASES
    assert workflow.phase_names == ("resolve", "verify")
    assert "worktree" not in workflow.phase_names


def test_the_task_document_is_unchanged_by_the_new_builtin() -> None:
    assert load_builtin("task").phase_names[0] == "worktree"
    assert len(load_builtin("task").phases) == 14


def test_resolve_is_the_resolver_judged_by_the_merge_gate_with_two_attempts() -> None:
    phase = load_builtin("integrate").phase("resolve")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "resolver"
    assert phase.inputs == RESOLVE_INPUTS
    assert phase.result == "ResolveResult"
    assert phase.gates == ["merge_completed_gate"]
    assert phase.writes is None
    assert phase.retry is not None
    assert phase.retry.max_attempts == 2
    assert phase.retry.on == ["gate_failed", "schema_invalid"]


def test_verify_is_shaped_exactly_like_the_task_documents_verify() -> None:
    phase = load_builtin("integrate").phase("verify")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "verify.run_suite"
    assert phase.gates == ["verification_passed_gate"]
    assert phase.args == {}
    assert phase.best_effort is False
    assert phase.when is None
    assert phase.skip_to is None
    assert phase == load_builtin("task").phase("verify")


def test_the_resolve_result_name_resolves_to_the_resolve_model() -> None:
    phase = load_builtin("integrate").phase("resolve")
    assert isinstance(phase, AgentPhase)
    model = resolve_result_model(phase.result, RESULT_MODELS, phase=phase.name)
    assert model is ResolveResult


def test_every_name_the_document_uses_is_a_registry_binding() -> None:
    workflow = load_builtin("integrate")
    registry = default_registry()
    assert set(workflow.functions) == INTEGRATE_FUNCTIONS
    assert set(workflow.functions) <= set(registry.names())
    for name, fn in workflow.functions.items():
        assert fn is registry.resolve(name)
    assert workflow.function("merge_completed_gate") is integrate.merge_completed_gate
    assert workflow.function("verification_passed_gate") is reducers.verification_passed_gate
    assert workflow.function("verify.run_suite") is verify.run_suite


def test_the_two_builtins_together_use_every_registered_name() -> None:
    """The registry pins exactly the names the builtin documents use
    (`tests/workflow/test_registry.py`); with this document shipped, that
    claim is now checkable against the documents themselves."""
    used = set(load_builtin("task").functions) | set(load_builtin("integrate").functions)
    assert used == set(default_registry().names())


def test_no_resolve_input_is_produced_by_another_phase_or_reserved() -> None:
    """Both new inputs come from the caller's `extra_context`: none is read out
    of a phase result (so resume needs no back-off) and none is a key the
    engine owns (so `run_subtask` accepts them)."""
    assert not set(RESOLVE_INPUTS) & set(prompt.INPUT_PRODUCERS)
    for key in EXTRA_CONTEXT:
        assert key not in engine.RESERVED_CONTEXT_KEYS


# ── every gate binds against real results ────────────────────────────────────


def _phase_results() -> dict[str, Any]:
    """What each phase leaves in the binding table on a healthy run: the JSON
    dump of a real `ResolveResult` (as `dispatch.classify` hands it on) and the
    mapping `verify.run_suite` returns."""
    return {
        "resolve": ResolveResult(
            resolved=True, summary="kept both stories' edits in both files"
        ).model_dump(mode="json"),
        "verify": {"passed": True, "verified": [], "detail": ""},
    }


def _context() -> dict[str, Any]:
    """`engine.subtask_context` plus the caller's `extra_context`, as
    `engine.run_subtask` builds it."""
    context = engine.subtask_context(SUBTASK, REPO_DIR, SUITE)
    context.update(EXTRA_CONTEXT)
    return context


def _values_for(phase_name: str) -> dict[str, Any]:
    """The binding table this phase's gates really see: the context, every
    earlier phase's result under its own name, then `dispatch.gate_values`'
    `result` / `<phase name>` overlay (`engine._gate_values` builds the same
    table for the deterministic `verify`)."""
    results = _phase_results()
    context = _context()
    for name in load_builtin("integrate").phase_names:
        if name == phase_name:
            break
        if name not in engine.RESERVED_CONTEXT_KEYS:
            context[name] = results[name]
    return dispatch.gate_values(context, phase_name, results[phase_name])


GATED_PHASES = [
    ("resolve", "merge_completed_gate"),
    ("verify", "verification_passed_gate"),
]


def test_the_document_still_names_exactly_the_gates_this_suite_covers() -> None:
    workflow = load_builtin("integrate")
    assert [(p.name, g) for p in workflow.phases for g in p.gates] == GATED_PHASES


def _required(fn: Any) -> set[str]:
    return {
        p.name
        for p in inspect.signature(fn).parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }


@pytest.mark.parametrize(("phase_name", "gate_name"), GATED_PHASES)
def test_every_gate_binds_every_required_parameter_against_real_results(
    phase_name: str, gate_name: str
) -> None:
    gate = load_builtin("integrate").function(gate_name)
    bound = engine.bind_arguments(
        gate, _values_for(phase_name), phase=phase_name, function=gate_name
    )
    assert set(bound) == _required(gate)


def test_the_merge_gate_binds_the_integration_worktree_and_never_the_git_runner() -> None:
    gate = load_builtin("integrate").function("merge_completed_gate")
    bound = engine.bind_arguments(
        gate, _values_for("resolve"), phase="resolve", function="merge_completed_gate"
    )
    assert bound["worktree"] == SUBTASK.worktree_path
    assert bound["result"] == _phase_results()["resolve"]
    assert "git_runner" not in bound


def test_the_verification_gate_passes_a_green_suite_on_the_verify_phase() -> None:
    gate = load_builtin("integrate").function("verification_passed_gate")
    bound = engine.bind_arguments(
        gate, _values_for("verify"), phase="verify", function="verification_passed_gate"
    )
    assert gate(**bound) is None


def test_every_resolve_input_renders_through_the_shipped_table() -> None:
    phase = load_builtin("integrate").phase("resolve")
    assert isinstance(phase, AgentPhase)
    rendered = prompt.render_prompt(phase, _context())

    assert rendered.inputs == tuple(RESOLVE_INPUTS)
    assert rendered.text.startswith("# phase: resolve\n# role: resolver\n")
    bodies = dict(rendered.sections)
    assert bodies["branch"] == INTEGRATION_BRANCH
    assert bodies["base_branch"] == BASE_BRANCH
    assert bodies["merge_tip"] == MERGE_TIP
    assert json.loads(bodies["conflict_files"]) == CONFLICT_FILES
    assert json.loads(bodies["verification"]) == SUITE
