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

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent_manager.steps import plan_check, reducers, rollup, verify, worktree

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


_PLACEHOLDERS: dict[str, Function] = {}
"""One function object per placeholder name, shared across every
`default_registry()` call.

`default_registry()` returns a fresh `FunctionRegistry` each time (Review
Focus: a singleton *registry* would be poisonable by any caller), but a
placeholder built fresh per call would mean `resolve(name) is resolve(name)`
holds across two `default_registry()` calls for every real, imported callable
and fails for every placeholder -- exactly the asymmetry
`test_every_resolved_function_is_the_registry_binding` (Task 6) checks for.
Interning by name gives a placeholder as stable an identity as a real import,
without making the registry itself shared, mutable state.
"""


def _placeholder(name: str, owner: str) -> Function:
    """A callable that resolves now and refuses to run.

    The subtask's contract is *resolution*, not execution: the builtin document
    must load whole today, and the step it names is a sibling's to write. The
    seam is deliberate -- the sibling replaces this line with a real import,
    and the name never has to be added to the document later.
    """
    if name in _PLACEHOLDERS:
        return _PLACEHOLDERS[name]

    def _unimplemented(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            f"{name} is registered so the workflow document resolves at load time, "
            f"but its implementation is owned by {owner}"
        )

    _unimplemented.__name__ = name.replace(".", "_")
    _unimplemented.__qualname__ = _unimplemented.__name__
    _PLACEHOLDERS[name] = _unimplemented
    return _unimplemented


def _plan_hash_of(result: object) -> object:
    """The `plan_hash` field of a dumped phase result, or `None`.

    `None` rather than an error for a missing or non-mapping result: a phase
    that was skipped, escalated or returned nothing has no hash to compare, and
    `reducers.plan_hash_gate` already answers `None` (pass) for anything that is
    not an 8-hex-character string.
    """
    return result.get("plan_hash") if isinstance(result, Mapping) else None


def plan_hash_gate_adapter(
    implement: object = None, review: object = None
) -> dict[str, str] | None:
    """`reducers.plan_hash_gate` bound to the two phase results it compares.

    The document names this gate on the `review` phase, and
    `engine.bind_arguments` binds strictly by parameter name out of a table of
    whole values -- nothing in that table is called `impl_hash` or
    `review_hash`, and the yaml `args:` map holds literals, so neither could be
    written there either. This adapter takes the two names the table *does*
    hold, the phase names `implement` and `review`, and does the one field
    lookup the binder cannot do for itself. The reducer keeps its signature and
    its tests; nothing about the comparison moves.

    A module-level function rather than a closure built inside
    `default_registry()`: `resolve(name) is resolve(name)` must hold across two
    registries, which is the same invariant `_PLACEHOLDERS` exists to preserve.

    Both parameters default to `None` so a run where `implement` never executed
    in this process (a `plan_check` skip, or a resume started later) still binds
    and passes, rather than failing to bind and reading as a document bug.
    """
    return reducers.plan_hash_gate(_plan_hash_of(implement), _plan_hash_of(review))


BUILTIN_FUNCTION_NAMES = (
    "critic_blockers_gate",
    "exploration_output_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_check.mark_validated",
    "plan_hash_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)
"""Every name appearing in a run/when/gate position of `builtin/task.yaml`,
sorted. Kept here as data so a test can assert the registry and the document
have not drifted apart."""


def default_registry() -> FunctionRegistry:
    """A fresh registry holding every name `builtin/task.yaml` references.

    A new instance per call on purpose: a module-level singleton is mutable
    global state that any importer could rebind a gate in, and the second call
    would then fail on `DuplicateFunctionError`.

    The six reducers and the five implemented steps are the real, imported
    callables -- not wrappers -- so `resolve(name) is the_function` holds and a
    sibling's bugfix reaches the engine without touching this table. No
    placeholder registrations remain: every name the document uses now has an
    implementation. `plan_hash_gate` is the single exception to the "no
    wrappers" rule: see `plan_hash_gate_adapter` above for why the binder cannot
    reach the two fields that gate compares.
    """
    registry = FunctionRegistry()

    # Gates ported from task.js -- siblings ef33352b and 5ee2ee50, done.
    registry.register("exploration_output_gate", reducers.exploration_output_gate)
    registry.register("verification_gate", reducers.verification_gate)
    registry.register("review_gate", reducers.review_gate)
    registry.register("plan_hash_gate", plan_hash_gate_adapter)
    registry.register("verification_passed_gate", reducers.verification_passed_gate)
    registry.register("critic_blockers_gate", reducers.critic_blockers_gate)

    # Deterministic steps.
    registry.register("worktree.ensure", worktree.ensure)
    registry.register("verify.run_suite", verify.run_suite)
    registry.register("plan_check.find_validated_plan", plan_check.find_validated_plan)
    registry.register("plan_check.has_validated_plan", plan_check.has_validated_plan)
    registry.register("plan_check.mark_validated", plan_check.mark_validated)
    registry.register("rollup.set_status", rollup.set_status)
    return registry
