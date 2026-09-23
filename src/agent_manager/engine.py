"""Walk one subtask's phases and execute the deterministic ones (design §6).

The walk is the only thing here. Input resolution and prompt rendering belong to
a sibling, and so does everything about an agent phase past handing it to the
injected runner: this module treats a `kind: agent` phase as an opaque call.

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

from agent_manager import models
from agent_manager.store import Store
from agent_manager.workflow.loader import DeterministicPhase, Workflow

_EMPTY = inspect.Parameter.empty
_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)


class EngineError(RuntimeError):
    """The engine refused to run, or could not make sense of, a phase.

    Carries the coordinates an operator needs to find the offending line of the
    workflow document: which phase, which registered function, which parameter.
    """

    def __init__(
        self,
        reason: str,
        *,
        phase: str | None = None,
        function: str | None = None,
        parameter: str | None = None,
    ) -> None:
        self.reason = reason
        self.phase = phase
        self.function = function
        self.parameter = parameter
        parts = []
        if phase is not None:
            parts.append(f"phase {phase!r}")
        if function is not None:
            parts.append(f"function {function!r}")
        if parameter is not None:
            parts.append(f"parameter {parameter!r}")
        prefix = ", ".join(parts)
        super().__init__(f"{prefix}: {reason}" if prefix else reason)


RESERVED_CONTEXT_KEYS = ("card", "branch", "base", "worktree", "repo_dir", "commands")
"""The context keys `subtask_context` always sets.

Named as a constant because phase results land in the same mapping under the
phase's name: a phase called `worktree` -- the shipped `builtin/task.yaml`
has exactly one -- would otherwise overwrite the real worktree path every
later step binds from. `_bind_result` uses this to skip writing such a
result back into the table rather than refuse the phase outright.
"""


def subtask_context(
    subtask: models.SubtaskRun, repo_dir: Path, commands: Sequence[str] = ()
) -> dict[str, Any]:
    """The starting binding table for one subtask's phases.

    The keys are the *callables'* parameter names, not the model's field names:
    binding is by name, and no deterministic phase in `builtin/task.yaml`
    declares `args` that could bridge the difference. Hence `base` for
    `base_branch` and `worktree` for `worktree_path`.
    """
    return {
        "card": subtask.card_id,
        "branch": subtask.branch,
        "base": subtask.base_branch,
        "worktree": subtask.worktree_path,
        "repo_dir": repo_dir,
        "commands": list(commands),
    }


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


def run_subtask(
    workflow: Workflow,
    store: Store,
    *,
    story_id: str,
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    start_phase: str | None = None,
    clock: Clock = _utcnow,
) -> SubtaskSummary:
    """Walk `workflow`'s phases for one subtask, running the deterministic ones.

    `story_id` is the caller's: `Store.record_phase` and `Store.record_subtask`
    are both keyed by it, and nothing in a subtask knows its story.
    """
    index = _start_index(workflow, start_phase)
    context = subtask_context(subtask, repo_dir, commands)
    summary = SubtaskSummary()

    while index < len(workflow.phases):
        phase = workflow.phases[index]
        if not isinstance(phase, DeterministicPhase):
            raise EngineError(
                "is an agent phase, but no agent runner was injected",
                phase=phase.name,
            )
        outcome = _run_deterministic(
            phase, workflow, store, story_id, subtask, context, clock
        )
        _bind_result(context, phase.name, outcome.result)
        summary.results[phase.name] = outcome.result
        index += 1

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
    function = workflow.function(phase.run)
    kwargs = bind_arguments(
        function, context, phase.args, phase=phase.name, function=phase.run
    )
    result = function(**kwargs)
    _record_phase(store, story_id, subtask, phase, "done", started_at, clock())
    return _Outcome(ok=True, result=result)


def _record_phase(
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    phase: DeterministicPhase,
    status: models.Status,
    started_at: datetime,
    ended_at: datetime | None,
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
        ),
    )


def _record_subtask_status(
    store: Store, story_id: str, subtask: models.SubtaskRun, status: models.Status
) -> None:
    store.record_subtask(story_id, subtask.model_copy(update={"status": status}))
