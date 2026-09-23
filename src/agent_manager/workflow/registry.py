"""Name -> callable resolution for workflow documents (design §5, lines 141-144).

Every `run`, `when` and `gate` value in a workflow document is the name of a
function registered here. There is no expression language, and no dynamic
import of a dotted path out of the document: the only way a name becomes
reachable is that some line of Python called `register` with it, so a name
nobody registered can never be executed by accident -- `loader.load_workflow`
refuses the whole document instead, before any phase runs.

The error hierarchy lives in this module rather than in `loader.py` because the
dependency runs one way only (`loader` imports `registry`, never the reverse)
and both modules raise the same `WorkflowLoadError` base, so a caller catches
one type for "this workflow could not be loaded".
"""

from collections.abc import Callable, Sequence
from typing import Any

Function = Callable[..., Any]
"""What a registered name resolves to. The engine, not this module, knows what
arguments a given phase's function takes (design §6)."""


class WorkflowLoadError(RuntimeError):
    """Any failure to load, validate or resolve a workflow document.

    Carries the workflow name, the offending phase and the field where each is
    known: an operator reading one journal line has to be able to find the line
    of YAML that is wrong. A bare `KeyError`, `ValidationError` or
    `AttributeError` must never reach a caller of this package.
    """

    def __init__(
        self,
        reason: str,
        *,
        workflow: str | None = None,
        phase: str | None = None,
        field: str | None = None,
    ) -> None:
        self.reason = reason
        self.workflow = workflow
        self.phase = phase
        self.field = field
        parts = []
        if workflow is not None:
            parts.append(f"workflow {workflow!r}")
        if phase is not None:
            parts.append(f"phase {phase!r}")
        if field is not None:
            parts.append(f"field {field!r}")
        prefix = ", ".join(parts)
        super().__init__(f"{prefix}: {reason}" if prefix else reason)


class UnknownFunctionError(WorkflowLoadError):
    """A `run`, `when` or `gate` position names a function nobody registered.

    `unknown` holds every offending name from one document -- reporting them
    one per load attempt would make fixing a document an N-round trip.
    `registered` is what *was* available, so the fix is obvious from the
    message alone.
    """

    def __init__(
        self,
        reason: str,
        *,
        workflow: str | None = None,
        phase: str | None = None,
        field: str | None = None,
        unknown: Sequence[str] = (),
        registered: Sequence[str] = (),
    ) -> None:
        self.unknown = tuple(unknown)
        self.registered = tuple(registered)
        super().__init__(reason, workflow=workflow, phase=phase, field=field)


class DuplicateFunctionError(WorkflowLoadError):
    """A name was registered twice.

    Rejected rather than overwritten: silently rebinding `review_gate` to a
    second implementation is how a run ends up gated by code nobody meant to
    call, and the loser of the race is invisible.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"a function named {name!r} is already registered")


class FunctionRegistry:
    """An explicit `name -> callable` table.

    Deliberately not a decorator that populates a module-level singleton: the
    engine constructs a registry, hands it to the loader, and nothing else can
    mutate what a loaded workflow will call.
    """

    def __init__(self) -> None:
        self._functions: dict[str, Function] = {}

    def register(self, name: str, fn: Function) -> None:
        """Bind `name` to `fn`, refusing to shadow an existing binding."""
        if name in self._functions:
            raise DuplicateFunctionError(name)
        if not callable(fn):
            raise WorkflowLoadError(
                f"{name!r} was registered with {type(fn).__name__}, which is not callable"
            )
        self._functions[name] = fn

    def resolve(self, name: str) -> Function:
        """The callable bound to `name`, or `UnknownFunctionError`."""
        try:
            return self._functions[name]
        except KeyError:
            raise UnknownFunctionError(
                f"no function named {name!r} is registered "
                f"(registered: {', '.join(self.names()) or 'nothing'})",
                unknown=(name,),
                registered=self.names(),
            ) from None

    def names(self) -> tuple[str, ...]:
        """Everything registered, sorted, for error messages and tests."""
        return tuple(sorted(self._functions))

    def __contains__(self, name: object) -> bool:
        return name in self._functions
