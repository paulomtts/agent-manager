"""The pieces of the subtask walk that live below pygents (design §6, G10).

`runtime/engine.py` walks one subtask on pygents. What that walk, the two
compiled tools (`runtime/compile.py`) and the agent-phase dispatcher
(`dispatch.py`) share lives here: the binding table (`subtask_context`,
`_document_paths`, `RESERVED_CONTEXT_KEYS`), binding by parameter name
(`bind_arguments`), one deterministic step run, judged and recorded
(`run_one_step`), and the summary and its subtask rows (`SubtaskSummary`,
`_escalate`, `_stop`, `_record_subtask_status`).

No pygents import here (rule 1): `dispatch.py` imports this module, and
dispatch must never load pygents. `runtime/__init__.py` imports nothing, so
importing this module loads nothing else from `runtime/`.

§6 says a step is called as `run(ctx) -> dict`, but the real steps take named
keyword arguments (`worktree.ensure(branch, base, worktree, repo_dir)`,
`verify.run_suite(commands, worktree)`, `plan_check.find_validated_plan(card)`).
Rather than rewrite four working steps, a step is bound by parameter name out
of a per-subtask context mapping overlaid with the phase's declared `args`, and
a binding failure raises `EngineError` naming phase, function and parameter
before the call -- a bare `TypeError` from a call site tells an operator
nothing about which phase is wrong.
"""

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent_manager import models, prompt
from agent_manager.runtime.errors import EngineError
from agent_manager.store import Store
from agent_manager.workflow import phases as phase_model

_EMPTY = inspect.Parameter.empty
_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)

RESERVED_CONTEXT_KEYS = (
    "card",
    "card_details",
    "parent_story_details",
    "branch",
    "base",
    "base_branch",
    "worktree",
    "repo_dir",
    "commands",
    "spec_path",
    "plan_path",
)
"""The context keys the engine owns, and no phase result may replace.

`subtask_context` always sets all but `spec_path` and `plan_path`; those two are
set by `_document_paths` only when some agent phase in the workflow declares
them as inputs. Reserved either way: a key the engine may set is a key a phase
result must never take over, whether this particular workflow made it appear or
not.

Named as a constant because phase results land in the same mapping under the
phase's name: a phase called `worktree` -- the shipped `task` workflow has
exactly one -- would otherwise overwrite the real worktree path every later
step binds from, and a phase called `spec_path` would overwrite the document
path `implement` and `review` both declare. `_bind_result` uses this to skip
writing such a result back into the table rather than refuse the phase
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
    binding is by name, and no step in `workflow.task.TASK` declares `args`
    that could bridge the difference. Hence `base` for `base_branch` and
    `worktree` for `worktree_path`.

    `base_branch` is that same string under a second key, because the two sides
    of the workflow disagree about the name: `worktree.ensure(branch, base,
    ...)` asks for `base`, and `reducers.review_gate(review, branch,
    base_branch)` asks for `base_branch`. A declared `args` entry cannot bridge
    it -- `args` are literals and the base branch is per-run -- and renaming
    either parameter would change a shipped step or a ported gate. Both keys are
    reserved, so no phase result can make them disagree.

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
        "base_branch": subtask.base_branch,
        "worktree": subtask.worktree_path,
        "repo_dir": repo_dir,
        "commands": list(commands),
    }


_DOCUMENT_INPUTS = {"spec_path": "spec", "plan_path": "plan"}
"""Which phase's `writes` template each §7 document-path input comes from.

Keyed on the phase *name*, not on a guess about the path: `TASK` names them
`spec` and `plan`, and matching on the template text would make a workflow
whose plan phase writes into `docs/specs/` resolve backwards.
"""


def _document_paths(
    workflow: phase_model.Workflow, card: models.Card | None
) -> dict[str, str]:
    """`spec_path` / `plan_path` for the whole subtask, computed once, from the workflow.

    Computed at subtask start rather than when the `spec` and `plan` phases run:
    `plan_check` may `skip_to` `docs_commit`, and `implement` still declares both
    inputs. §7 calls them "paths in the repo, already committed" -- the path is a
    property of the card and the workflow, not of a phase having executed.
    """
    declared = {
        name
        for phase in workflow.phases
        if isinstance(phase, phase_model.AgentPhase)
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


def _writing_phase(
    workflow: phase_model.Workflow, phase_name: str, input_name: str
) -> phase_model.AgentPhase:
    found = next((p for p in workflow.phases if p.name == phase_name), None)
    if not isinstance(found, phase_model.AgentPhase) or found.writes is None:
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
    [phase_model.AgentPhase, Mapping[str, Any], prompt.RenderedPrompt], Any
]
"""The seam `dispatch.AgentRunner` fills: `(phase, context, rendered) -> result`.

The walk resolves the phase's declared `inputs` and renders the prompt before
the call, because that is exactly where §6 puts step 2 -- and because the runner
cannot dispatch without a prompt it can write to the attempt directory first.
Everything past this call -- that directory, dispatch, schema validation, retry,
its gates -- belongs to the runner. The walk only takes the returned result
into the pool under the phase's name.
"""


@dataclass
class SubtaskSummary:
    """What the walk did to one subtask.

    Returned rather than raised: a caller must be able to tell a clean `done`
    from a `done` whose board write silently failed (§12), and an exception
    carries neither the results nor the warnings.
    """

    status: Literal["done", "escalated", "stopped"] = "done"
    results: dict[str, Any] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed_phase: str | None = None
    detail: str | None = None
    before_phase: str | None = None
    """The phase a `stopped` subtask would have run next; None unless `_stop` ran."""


@dataclass
class _Outcome:
    """One deterministic phase's verdict, already recorded."""

    ok: bool
    result: Any = None
    detail: str | None = None
    warnings: list[str] = field(default_factory=list)
    skip_to: str | None = None


class _GateFailed(Exception):
    """A gate returned a verdict. Private: it never leaves `run_one_step`."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class GateVerdict:
    """What one pass over a phase's gates concluded (architecture-cleanup S3).

    A plain dataclass rather than a Pydantic model: it never crosses a process
    boundary. `detail` is `None` for `pass`; otherwise it carries enough for
    either caller -- `run_one_step` here, `dispatch.AgentRunner` -- to render
    its own message without re-running a gate:

    - `fail`: `gate`, `verdict` (the raw mapping), `message` (the rendered
      `phase ... gate ... failed: k=v` line).
    - `broken`: `gate`, `reason` (`"raised"` or `"not_a_mapping"`), `error`
      (the gate's exception, or the `EngineError` built for a non-mapping),
      plus `returned_type` for `not_a_mapping`.
    - `warn`: `warnings`, the messages this evaluation appended.
    """

    kind: Literal["pass", "warn", "fail", "broken"]
    detail: dict[str, Any] | None


def gate_values(
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


_gate_values = gate_values
"""The pre-S3 private name, kept because `tests/test_engine.py` binds through it."""


def _label(fn: Callable[..., Any]) -> str:
    """How messages name a callable: its `__name__`, or its `repr` when it has none."""
    return getattr(fn, "__name__", repr(fn))


def evaluate_gates(
    phase: phase_model.Step | phase_model.AgentPhase,
    values: Mapping[str, Any],
    warnings: list[str],
) -> GateVerdict:
    """Run `phase`'s gates in order and say what they concluded.

    The one gate contract both phase kinds share: `None` passes, a mapping
    holding `warn` appends a warning and continues, any other mapping fails.
    A gate that raises an `Exception`, or returns anything that is not a
    mapping, is `broken` -- never read as a pass. Evaluation stops at the first
    `fail` or `broken`.

    A gate whose parameters cannot be bound is a workflow wiring bug, not a
    verdict: the `EngineError` from `bind_arguments` propagates unchanged.
    """
    warned: list[str] = []
    for gate in phase.gates:
        name = _label(gate)
        kwargs = bind_arguments(gate, values, phase=phase.name, function=name)
        try:
            verdict = gate(**kwargs)
        except Exception as error:
            return GateVerdict(
                "broken", {"gate": name, "reason": "raised", "error": error}
            )
        if verdict is None:
            continue
        if not isinstance(verdict, Mapping):
            returned_type = type(verdict).__name__
            error = EngineError(
                f"gate returned {returned_type}; a gate returns None to pass "
                "or a mapping verdict to fail, and anything else would be read as a "
                "pass by accident",
                phase=phase.name,
                function=name,
            )
            return GateVerdict(
                "broken",
                {
                    "gate": name,
                    "reason": "not_a_mapping",
                    "error": error,
                    "returned_type": returned_type,
                },
            )
        if "warn" in verdict:
            message = f"phase {phase.name!r} gate {name!r} warned: {verdict['warn']}"
            warnings.append(message)
            warned.append(message)
            continue
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(verdict.items()))
        return GateVerdict(
            "fail",
            {
                "gate": name,
                "verdict": verdict,
                "message": f"phase {phase.name!r} gate {name!r} failed: {rendered}",
            },
        )
    if warned:
        return GateVerdict("warn", {"warnings": warned})
    return GateVerdict("pass", None)


def _skip_target(phase: phase_model.Step, values: Mapping[str, Any]) -> str | None:
    """The phase to jump to, or `None` to fall through to the next one.

    Both `when` and `skip_to` are required for a jump: `when` alone has nowhere
    to go, and `skip_to` alone would be an unconditional jump the workflow
    author did not write.
    """
    if phase.when is None or phase.skip_to is None:
        return None
    kwargs = bind_arguments(
        phase.when, values, phase=phase.name, function=_label(phase.when)
    )
    return phase.skip_to if phase.when(**kwargs) else None


def _bind_result(context: dict[str, Any], phase_name: str, result: Any) -> None:
    """Fold one phase's result into the binding table under its own name.

    `TASK` names its worktree-setup phase `worktree`, exactly the key
    `subtask_context` binds the real worktree path under. The result is still
    recorded and returned in the summary either way; it is just never written
    back here, so the reserved value survives for every later phase that binds
    `worktree` (or any other reserved key) by name, instead of being silently
    replaced by a same-named phase's own result. `runtime/context.py`'s
    `binding_table` applies the same rule to the pygents pool.
    """
    if phase_name not in RESERVED_CONTEXT_KEYS:
        context[phase_name] = result


def run_one_step(
    *,
    phase: phase_model.Step,
    table: Mapping[str, Any],
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    clock: Clock,
) -> _Outcome:
    """One deterministic phase, run, judged and recorded.

    The pygents engine's `step_phase` tool calls this through
    `bridge.call_step`: binding, the mapping check, gates, `when` and
    `skip_to`, and the `started`/`done`/`failed` phase rows. `best_effort` is
    the caller's to apply -- this returns the verdict, not the walk's reaction
    to it.
    """
    started_at = clock()
    _record_phase(store, story_id, subtask, phase, "started", started_at, None)
    warnings: list[str] = []
    try:
        label = _label(phase.run)
        kwargs = bind_arguments(
            phase.run, table, phase.args, phase=phase.name, function=label
        )
        result = phase.run(**kwargs)
        if not isinstance(result, Mapping):
            raise EngineError(
                f"returned {type(result).__name__}, but a deterministic phase must "
                "return a mapping: a gate or a later `when` would read anything else "
                "as closed and the run would branch wrongly",
                phase=phase.name,
                function=label,
            )
        verdict = evaluate_gates(phase, gate_values(table, phase.name, result), warnings)
        if verdict.kind == "fail":
            raise _GateFailed(verdict.detail["message"])
        if verdict.kind == "broken":
            # Re-raised into the catch-all below on purpose: it records
            # `_render_error(error)`, the exact string a broken gate recorded
            # before the evaluator was shared.
            raise verdict.detail["error"]
        skip_to = _skip_target(phase, gate_values(table, phase.name, result))
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
    phase: phase_model.Step,
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


def _escalate(
    summary: SubtaskSummary,
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    phase_name: str,
    detail: str | None,
) -> SubtaskSummary:
    """Record the subtask `escalated` and hand the walk's summary back.

    One helper for both phase kinds, because §12's "escalation stops the run" is
    one rule: the summary is returned rather than raised so the caller can still
    read the results and warnings of everything that ran before it.
    """
    summary.status = "escalated"
    summary.failed_phase = phase_name
    summary.detail = detail
    _record_subtask_status(store, story_id, subtask, "escalated")
    return summary


def _stop(
    summary: SubtaskSummary,
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    phase_name: str,
) -> SubtaskSummary:
    """Record the subtask `stopped` before `phase_name` and hand the summary back.

    Kept apart from `_escalate` on purpose: `stopped` is not `failed`, so
    `failed_phase` stays `None`. Results, warnings and skips gathered so far
    stay on the summary.
    """
    summary.status = "stopped"
    summary.before_phase = phase_name
    summary.detail = f"stopped before {phase_name}"
    _record_subtask_status(store, story_id, subtask, "stopped")
    return summary
