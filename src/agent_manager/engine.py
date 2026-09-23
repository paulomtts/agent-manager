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
from pathlib import Path
from typing import Any

from agent_manager import models

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
