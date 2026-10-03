"""Pure-functions tier (design spec §14): `workflow.integrate.INTEGRATE`, the
declared resolver workflow (Integrate addendum I3, card b4bd3795).

Ported from `tests/workflow/test_builtin_integrate.py`, which asserted this
against `builtin/integrate.yaml` and was deleted with it (card 7a744199).

Nothing outside this process is touched -- no git, no network.
`merge_completed_gate` is bound here but never called: calling it runs git,
and that is the engine tier's job (`tests/test_integrate_workflow.py`)."""

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from agent_manager import dispatch, models, prompt
from agent_manager.results import RESULT_MODELS, ResolveResult
from agent_manager.runtime import walk
from agent_manager.steps import integrate, reducers, verify
from agent_manager.workflow.integrate import INTEGRATE
from agent_manager.workflow.phases import AgentPhase, Retry, Step
from agent_manager.workflow.task import TASK

RESOLVE_INPUTS = ("branch", "base_branch", "merge_tip", "conflict_files", "verification")

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


def test_integrate_is_resolve_then_verify_with_no_worktree_phase() -> None:
    """The integration worktree arrives through `SubtaskRun.worktree_path`
    (context key `worktree`); `merge_tip` already made it, so no phase here may
    create or re-point one."""
    assert INTEGRATE.name == "integrate"
    assert tuple((phase.name, type(phase)) for phase in INTEGRATE.phases) == (
        ("resolve", AgentPhase),
        ("verify", Step),
    )
    assert INTEGRATE.phase_names == ("resolve", "verify")
    assert "worktree" not in INTEGRATE.phase_names


def test_resolve_is_the_resolver_judged_by_the_merge_gate_with_two_attempts() -> None:
    phase = INTEGRATE.phase("resolve")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "resolver"
    assert phase.inputs == RESOLVE_INPUTS
    assert phase.result is ResolveResult
    assert RESULT_MODELS["ResolveResult"] is ResolveResult
    assert phase.gates == (integrate.merge_completed_gate,)
    assert phase.writes is None
    assert phase.retry == Retry(2, ("gate_failed", "schema_invalid"))


def test_verify_is_shaped_exactly_like_the_task_workflows_verify() -> None:
    phase = INTEGRATE.phase("verify")
    assert isinstance(phase, Step)
    assert phase.run is verify.run_suite
    assert phase.gates == (reducers.verification_passed_gate,)
    assert dict(phase.args) == {}
    assert phase.best_effort is False
    assert phase.when is None
    assert phase.skip_to is None
    assert phase == TASK.phase("verify")


def test_no_resolve_input_is_produced_by_another_phase_or_reserved() -> None:
    """Both resolver-only inputs come from the caller's `extra_context`: none
    is read out of a phase result and none is a key the engine owns (so
    `run_subtask` accepts them)."""
    assert not set(RESOLVE_INPUTS) & set(prompt.INPUT_PRODUCERS)
    for key in EXTRA_CONTEXT:
        assert key not in walk.RESERVED_CONTEXT_KEYS


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
    """`walk.subtask_context` plus the caller's `extra_context`, as
    `runtime.engine.run_subtask` builds the binding."""
    context = walk.subtask_context(SUBTASK, REPO_DIR, SUITE)
    context.update(EXTRA_CONTEXT)
    return context


def _values_for(phase_name: str) -> dict[str, Any]:
    """The binding table this phase's gates really see: the context, every
    earlier phase's result under its own name, then `walk.gate_values`'
    `result` / `<phase name>` overlay (the one table both the `resolve` agent
    phase and the `verify` step bind gates against)."""
    results = _phase_results()
    context = _context()
    for name in INTEGRATE.phase_names:
        if name == phase_name:
            break
        if name not in walk.RESERVED_CONTEXT_KEYS:
            context[name] = results[name]
    return walk.gate_values(context, phase_name, results[phase_name])


GATED_PHASES = [
    ("resolve", integrate.merge_completed_gate),
    ("verify", reducers.verification_passed_gate),
]


def test_the_workflow_still_holds_exactly_the_gates_this_suite_covers() -> None:
    assert [(p.name, g) for p in INTEGRATE.phases for g in p.gates] == GATED_PHASES


def _required(fn: Any) -> set[str]:
    return {
        p.name
        for p in inspect.signature(fn).parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }


@pytest.mark.parametrize(
    ("phase_name", "gate"), GATED_PHASES, ids=[g.__name__ for _p, g in GATED_PHASES]
)
def test_every_gate_binds_every_required_parameter_against_real_results(
    phase_name: str, gate: Any
) -> None:
    bound = walk.bind_arguments(
        gate, _values_for(phase_name), phase=phase_name, function=gate.__name__
    )
    assert set(bound) == _required(gate)


def test_the_merge_gate_binds_the_integration_worktree_and_never_the_git_runner() -> None:
    gate = integrate.merge_completed_gate
    bound = walk.bind_arguments(
        gate, _values_for("resolve"), phase="resolve", function="merge_completed_gate"
    )
    assert bound["worktree"] == SUBTASK.worktree_path
    assert bound["result"] == _phase_results()["resolve"]
    assert "git_runner" not in bound


def test_the_verification_gate_passes_a_green_suite_on_the_verify_phase() -> None:
    gate = reducers.verification_passed_gate
    bound = walk.bind_arguments(
        gate, _values_for("verify"), phase="verify", function="verification_passed_gate"
    )
    assert gate(**bound) is None


def test_every_resolve_input_renders_through_the_shipped_table() -> None:
    phase = INTEGRATE.phase("resolve")
    assert isinstance(phase, AgentPhase)
    rendered = prompt.render_prompt(phase, _context())

    assert rendered.inputs == RESOLVE_INPUTS
    assert rendered.text.startswith("# phase: resolve\n# role: resolver\n")
    bodies = dict(rendered.sections)
    assert bodies["branch"] == INTEGRATION_BRANCH
    assert bodies["base_branch"] == BASE_BRANCH
    assert bodies["merge_tip"] == MERGE_TIP
    assert json.loads(bodies["conflict_files"]) == CONFLICT_FILES
    assert json.loads(bodies["verification"]) == SUITE


def test_integrates_verify_binds_the_synthetic_subtasks_card_id():
    """Spec e2efd21d B3: the integrate walk's verify step gets `card` from the
    table -- the synthetic subtask's `card_id` -- and exports it as
    `AM_CARD_ID`. `run_id` is not in the table; the engine injects it."""
    phase = INTEGRATE.phase("verify")

    kwargs = walk.bind_arguments(
        phase.run, _context(), phase.args, phase="verify", function="verify.run_suite"
    )

    assert kwargs["card"] == SUBTASK.card_id
    assert "run_id" not in kwargs
