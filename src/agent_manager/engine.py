"""Walk one subtask's phases and execute the deterministic ones (design §6).

The walk is the only thing here. The engine resolves each agent phase's declared
`inputs` and renders its prompt (§6 step 2, `prompt.py`), then hands phase,
context and prompt to the injected runner: everything past that call --
the attempt directory, the dispatch, the result file, the retry -- belongs to a
sibling, and this module treats it as an opaque call.

§6 says the engine calls `run(ctx) -> dict`, but the real steps take named
keyword arguments (`worktree.ensure(branch, base, worktree, repo_dir)`,
`verify.run_suite(commands, worktree)`, `plan_check.find_validated_plan(card)`).
Rather than rewrite four working steps, the engine binds by parameter name out
of a per-subtask context mapping overlaid with the phase's declared `args`, and
raises its own error naming phase, function and parameter before the call -- a
bare `TypeError` from a call site tells an operator nothing about which line of
YAML is wrong.
"""

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent_manager import models, prompt
from agent_manager.errors import EngineError
from agent_manager.store import Store
from agent_manager.workflow.loader import AgentPhase, DeterministicPhase, Workflow

_EMPTY = inspect.Parameter.empty
_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)

# `EngineError` is imported, not defined, so `prompt.py` can raise it without
# importing this module back. `engine.EngineError` is still the public name.

RESERVED_CONTEXT_KEYS = (
    "card",
    "card_details",
    "parent_story_details",
    "branch",
    "base",
    "worktree",
    "repo_dir",
    "commands",
    "spec_path",
    "plan_path",
)
"""The context keys the engine itself sets, and no phase result may replace.

Named as a constant because phase results land in the same mapping under the
phase's name: a phase called `worktree` -- the shipped `builtin/task.yaml`
has exactly one -- would otherwise overwrite the real worktree path every
later step binds from, and a phase called `spec_path` would overwrite the
document path `implement` and `review` both declare. `_bind_result` uses this
to skip writing such a result back into the table rather than refuse the phase
outright.
"""


def subtask_context(
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    *,
    card: models.Card | None = None,
    parent_story: models.Card | None = None,
) -> dict[str, Any]:
    """The starting binding table for one subtask's phases.

    The keys are the *callables'* parameter names, not the model's field names:
    binding is by name, and no deterministic phase in `builtin/task.yaml`
    declares `args` that could bridge the difference. Hence `base` for
    `base_branch` and `worktree` for `worktree_path`.

    `card` stays the bare id string every deterministic step binds by that name
    (`plan_check.find_validated_plan(card)`). The full cards the §7 `card` and
    `parent_story` *inputs* render live beside it under `card_details` and
    `parent_story_details`, supplied by the caller exactly as `commands` is --
    nothing here reads the board.
    """
    return {
        "card": subtask.card_id,
        "card_details": card,
        "parent_story_details": parent_story,
        "branch": subtask.branch,
        "base": subtask.base_branch,
        "worktree": subtask.worktree_path,
        "repo_dir": repo_dir,
        "commands": list(commands),
    }


_DOCUMENT_INPUTS = {"spec_path": "spec", "plan_path": "plan"}
"""Which phase's `writes:` template each §7 document-path input comes from.

Keyed on the phase *name*, not on a guess about the path: `builtin/task.yaml`
names them `spec` and `plan`, and matching on the template text would make a
document whose plan phase writes into `docs/specs/` resolve backwards.
"""


def _document_paths(workflow: Workflow, card: models.Card | None) -> dict[str, str]:
    """`spec_path` / `plan_path` for the whole subtask, computed once, from the document.

    Computed at subtask start rather than when the `spec` and `plan` phases run:
    `plan_check` may `skip_to: implement`, and `implement` still declares both
    inputs. §7 calls them "paths in the repo, already committed" -- the path is a
    property of the card and the document, not of a phase having executed.
    """
    declared = {
        name
        for phase in workflow.phases
        if isinstance(phase, AgentPhase)
        for name in phase.inputs
        if name in _DOCUMENT_INPUTS
    }
    paths: dict[str, str] = {}
    for name in sorted(declared):
        source = _writing_phase(workflow, _DOCUMENT_INPUTS[name], name)
        if card is None:
            raise EngineError(
                f"is declared as an input, but no card was supplied to expand "
                f"{source.writes!r} (the stem comes from the card's id and title)",
                phase=source.name,
                parameter=name,
            )
        paths[name] = prompt.expand_writes(
            source.writes, card, phase=source.name, input_name=name
        )
    return paths


def _writing_phase(workflow: Workflow, phase_name: str, input_name: str) -> AgentPhase:
    found = next((p for p in workflow.phases if p.name == phase_name), None)
    if not isinstance(found, AgentPhase) or found.writes is None:
        raise EngineError(
            f"is declared as an input, but this workflow has no agent phase named "
            f"{phase_name!r} with a `writes:` template to take the path from "
            f"(phases: {', '.join(workflow.phase_names)})",
            parameter=input_name,
        )
    return found


def bind_arguments(
    fn: Callable[..., Any],
    values: Mapping[str, Any],
    args: Mapping[str, Any] | None = None,
    *,
    phase: str,
    function: str,
) -> dict[str, Any]:
    """Keyword arguments for `fn`, taken by name from `values` overlaid with `args`.

    Only parameters `fn` actually declares are passed, so a context holding
    twenty keys still calls a two-parameter step with two. `*args`/`**kwargs`
    are ignored rather than fed: a step that declares `**kwargs` has not asked
    for the whole context.
    """
    args = {} if args is None else args
    parameters = inspect.signature(fn).parameters
    for key in args:
        if key not in parameters:
            raise EngineError(
                f"the document declares args key {key!r}, which this function does not "
                f"take (it takes: {', '.join(parameters) or 'nothing'})",
                phase=phase,
                function=function,
                parameter=key,
            )
    supplied = {**values, **args}
    bound: dict[str, Any] = {}
    for parameter in parameters.values():
        if parameter.kind in _VARIADIC:
            continue
        if parameter.name not in supplied:
            if parameter.default is _EMPTY:
                raise EngineError(
                    "no value for a required parameter "
                    f"(available: {', '.join(sorted(supplied)) or 'nothing'})",
                    phase=phase,
                    function=function,
                    parameter=parameter.name,
                )
            continue
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise EngineError(
                "is positional-only, and the engine binds every argument by name",
                phase=phase,
                function=function,
                parameter=parameter.name,
            )
        bound[parameter.name] = supplied[parameter.name]
    return bound


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


Clock = Callable[[], datetime]

AgentPhaseRunner = Callable[
    ["AgentPhase", Mapping[str, Any], prompt.RenderedPrompt], Any
]
"""The seam sibling bf8e415b fills: `(phase, context, rendered) -> result`.

The engine resolves the phase's declared `inputs` and renders the prompt before
the call, because that is exactly where §6 puts step 2 -- and because the runner
cannot dispatch without a prompt it can write to the attempt directory first.
Everything past this call -- that directory, dispatch, schema validation, retry,
its gates -- belongs to that subtask, not here. This module only takes the
returned result into the context under the phase's name.
"""


@dataclass
class SubtaskSummary:
    """What the walk did to one subtask.

    Returned rather than raised: a caller must be able to tell a clean `done`
    from a `done` whose board write silently failed (§12), and an exception
    carries neither the results nor the warnings.
    """

    status: Literal["done", "escalated"] = "done"
    results: dict[str, Any] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed_phase: str | None = None
    detail: str | None = None


@dataclass
class _Outcome:
    """One deterministic phase's verdict, already recorded."""

    ok: bool
    result: Any = None
    detail: str | None = None
    warnings: list[str] = field(default_factory=list)
    skip_to: str | None = None


class _GateFailed(Exception):
    """A gate returned a verdict. Private: it never leaves `_run_deterministic`."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def _gate_values(
    context: Mapping[str, Any], phase_name: str, result: Mapping[str, Any]
) -> dict[str, Any]:
    """The binding table a gate or a `when` predicate sees.

    The result appears twice on purpose: under the phase's name, which is how
    §6 says later phases read it, and under `result`, which is the parameter
    name `plan_check.has_validated_plan(result)` and the shipped gates use.

    A phase named after a reserved key is the one exception, for the same
    reason `_bind_result` is: a gate on the `worktree` phase that binds
    `worktree` wants the path the phase was pointed at, not that phase's
    return value. `result` still reaches it either way.
    """
    values = {**context, "result": result}
    if phase_name not in RESERVED_CONTEXT_KEYS:
        values[phase_name] = result
    return values


def _evaluate_gates(
    phase: DeterministicPhase,
    workflow: Workflow,
    values: Mapping[str, Any],
    warnings: list[str],
) -> None:
    """Run every gate in order; append warnings, raise `_GateFailed` on a verdict."""
    for name in phase.gates:
        gate = workflow.function(name)
        kwargs = bind_arguments(gate, values, phase=phase.name, function=name)
        verdict = gate(**kwargs)
        if verdict is None:
            continue
        if not isinstance(verdict, Mapping):
            raise EngineError(
                f"gate returned {type(verdict).__name__}; a gate returns None to pass "
                "or a mapping verdict to fail, and anything else would be read as a "
                "pass by accident",
                phase=phase.name,
                function=name,
            )
        if "warn" in verdict:
            warnings.append(
                f"phase {phase.name!r} gate {name!r} warned: {verdict['warn']}"
            )
            continue
        raise _GateFailed(f"phase {phase.name!r} gate {name!r} failed: {_render_verdict(verdict)}")


def _render_verdict(verdict: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(verdict.items()))


def _skip_target(
    phase: DeterministicPhase, workflow: Workflow, values: Mapping[str, Any]
) -> str | None:
    """The phase to jump to, or `None` to fall through to the next one.

    Both `when` and `skip_to` are required for a jump: `when` alone has nowhere
    to go, and `skip_to` alone would be an unconditional jump the document
    author did not write.
    """
    if phase.when is None or phase.skip_to is None:
        return None
    predicate = workflow.function(phase.when)
    kwargs = bind_arguments(predicate, values, phase=phase.name, function=phase.when)
    return phase.skip_to if predicate(**kwargs) else None


def run_subtask(
    workflow: Workflow,
    store: Store,
    *,
    story_id: str,
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    card: models.Card | None = None,
    parent_story: models.Card | None = None,
    agent_runner: AgentPhaseRunner | None = None,
    start_phase: str | None = None,
    clock: Clock = _utcnow,
) -> SubtaskSummary:
    """Walk `workflow`'s phases for one subtask, running the deterministic ones.

    `story_id` is the caller's: `Store.record_phase` and `Store.record_subtask`
    are both keyed by it, and nothing in a subtask knows its story.
    """
    index = _start_index(workflow, start_phase)
    context = subtask_context(
        subtask, repo_dir, commands, card=card, parent_story=parent_story
    )
    context.update(_document_paths(workflow, card))
    summary = SubtaskSummary()

    while index < len(workflow.phases):
        phase = workflow.phases[index]
        if not isinstance(phase, DeterministicPhase):
            if agent_runner is None:
                raise EngineError(
                    "is an agent phase, but no agent runner was injected",
                    phase=phase.name,
                )
            rendered = prompt.render_prompt(phase, context)
            result = agent_runner(phase, dict(context), rendered)
            _bind_result(context, phase.name, result)
            summary.results[phase.name] = result
            index += 1
            continue
        outcome = _run_deterministic(
            phase, workflow, store, story_id, subtask, context, clock
        )
        summary.warnings.extend(outcome.warnings)
        if not outcome.ok:
            if phase.best_effort:
                # §12: the board write is the one thing allowed to fail quietly.
                # Quietly in the *run*, not in the report -- a run that says
                # `done` while the card never moved is the failure mode this
                # warning exists to prevent.
                summary.warnings.append(
                    f"best-effort phase {phase.name!r} failed: {outcome.detail}"
                )
                index += 1
                continue
            summary.status = "escalated"
            summary.failed_phase = phase.name
            summary.detail = outcome.detail
            _record_subtask_status(store, story_id, subtask, "escalated")
            return summary
        _bind_result(context, phase.name, outcome.result)
        summary.results[phase.name] = outcome.result
        if outcome.skip_to is None:
            index += 1
            continue
        target = workflow.phase_names.index(outcome.skip_to)
        summary.skipped.extend(workflow.phase_names[index + 1 : target])
        index = target

    _record_subtask_status(store, story_id, subtask, summary.status)
    return summary


def _bind_result(context: dict[str, Any], phase_name: str, result: Any) -> None:
    """Fold one phase's result into the binding table under its own name.

    The shipped `builtin/task.yaml` names its worktree-setup phase `worktree`,
    exactly the key `subtask_context` binds the real worktree path under. Its
    result is still recorded and returned in the summary either way (see
    `run_subtask`); it is just never written back here, so the reserved value
    survives for every later phase that binds `worktree` (or any other
    reserved key) by name, instead of being silently replaced by a same-named
    phase's own result.
    """
    if phase_name not in RESERVED_CONTEXT_KEYS:
        context[phase_name] = result


def _start_index(workflow: Workflow, start_phase: str | None) -> int:
    if start_phase is None:
        return 0
    for index, phase in enumerate(workflow.phases):
        if phase.name == start_phase:
            return index
    raise EngineError(
        f"cannot start at {start_phase!r}: workflow {workflow.name!r} has no such phase "
        f"(phases: {', '.join(workflow.phase_names)})"
    )


def _run_deterministic(
    phase: DeterministicPhase,
    workflow: Workflow,
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    context: Mapping[str, Any],
    clock: Clock,
) -> _Outcome:
    started_at = clock()
    _record_phase(store, story_id, subtask, phase, "started", started_at, None)
    warnings: list[str] = []
    try:
        function = workflow.function(phase.run)
        kwargs = bind_arguments(
            function, context, phase.args, phase=phase.name, function=phase.run
        )
        result = function(**kwargs)
        if not isinstance(result, Mapping):
            raise EngineError(
                f"returned {type(result).__name__}, but a deterministic phase must "
                "return a mapping: a gate or a later `when` would read anything else "
                "as closed and the run would branch wrongly",
                phase=phase.name,
                function=phase.run,
            )
        _evaluate_gates(phase, workflow, _gate_values(context, phase.name, result), warnings)
        skip_to = _skip_target(phase, workflow, _gate_values(context, phase.name, result))
    except _GateFailed as failure:
        _record_phase(
            store, story_id, subtask, phase, "failed", started_at, clock(), failure.detail
        )
        return _Outcome(ok=False, detail=failure.detail, warnings=warnings)
    except Exception as error:
        # Deliberately total. A step is other people's code -- GitError, OSError,
        # anything -- and an exception escaping the walk would leave the subtask
        # recorded `started` forever, which is exactly what resume mistakes for
        # work in flight.
        detail = _render_error(error)
        _record_phase(
            store, story_id, subtask, phase, "failed", started_at, clock(), detail
        )
        return _Outcome(ok=False, detail=detail, warnings=warnings)
    _record_phase(store, story_id, subtask, phase, "done", started_at, clock())
    return _Outcome(ok=True, result=result, warnings=warnings, skip_to=skip_to)


def _render_error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _record_phase(
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    phase: DeterministicPhase,
    status: models.Status,
    started_at: datetime,
    ended_at: datetime | None,
    detail: str | None = None,
) -> None:
    store.record_phase(
        story_id,
        subtask.card_id,
        models.PhaseRun(
            name=phase.name,
            kind="deterministic",
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            detail=detail,
        ),
    )


def _record_subtask_status(
    store: Store, story_id: str, subtask: models.SubtaskRun, status: models.Status
) -> None:
    store.record_subtask(story_id, subtask.model_copy(update={"status": status}))
