"""Parse and validate a workflow YAML document into typed phases (design §5).

A workflow document is text off disk, so it is validated with pydantic models
per `CLAUDE.md`, not read as loose dicts. Two phase kinds are discriminated on
`kind` (design §6): a deterministic phase the engine calls as `run(ctx)`, and an
agent phase the engine dispatches to a harness. `extra="forbid"` is the point of
validating at all -- a misspelled `best_effor` that is silently ignored ships a
workflow whose failure semantics are not what its author wrote.

Loading also *resolves*: every `run`, `when` and `gate` name is looked up in the
registry here, so a document naming a function nobody registered fails before
any phase runs, before a worktree exists and before a dispatch is billed. There
is no lazy resolution and no fallback that defers the failure to execution.
"""

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_manager.workflow.registry import (
    Function,
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
)


class _YamlLoader(yaml.SafeLoader):
    """`yaml.SafeLoader`, but `on`/`off`/`yes`/`no` are plain strings.

    PyYAML's default resolver follows YAML 1.1, which reads a bare `on` as the
    boolean `True` -- so `retry: { on: [schema_invalid, gate_failed] }`, taken
    verbatim from the design spec and shipped byte-for-byte as `builtin/task.yaml`,
    parses to a dict keyed by `True`, not `"on"`, and `RetryPolicy.on` is
    reported as "Field required" on every document that uses it, including the
    builtin one. Only `true`/`false` remain implicit booleans, which is all
    `best_effort` ever needs and matches YAML 1.2's narrower bool set.
    """


_YamlLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_YamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$", re.X),
    list("tTfF"),
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetryPolicy(_Model):
    """`retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }`.

    `on` is restricted to the two retryable outcomes of design §6 line 278:
    `ok` needs no retry and `harness_error` is not retried at this level.
    """

    max_attempts: int = Field(ge=1, strict=True)
    on: list[Literal["schema_invalid", "gate_failed"]] = Field(min_length=1)


class _PhaseBase(_Model):
    name: str = Field(min_length=1)
    when: str | None = None
    skip_to: str | None = None
    gates: list[str] = Field(default_factory=list)


class DeterministicPhase(_PhaseBase):
    """A phase the engine runs itself: no model, no network (design §6)."""

    kind: Literal["deterministic"]
    run: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    best_effort: bool = False


class AgentPhase(_PhaseBase):
    """A phase the engine dispatches to a harness (design §6).

    `result` is the *name* of a pydantic model and stays a string here: mapping
    it to a class is the agent-dispatch sibling's job, and this module must not
    import the model table to know a document is well formed.
    """

    kind: Literal["agent"]
    role: str = Field(min_length=1)
    inputs: list[str] = Field(default_factory=list)
    result: str | None = None
    writes: str | None = None
    retry: RetryPolicy | None = None


Phase = Annotated[DeterministicPhase | AgentPhase, Field(discriminator="kind")]


class Workflow(_Model):
    """One loaded, validated and fully resolved workflow document.

    `functions` is computed by `load_workflow`, never declared in YAML: it maps
    every name used in a `run`, `when` or `gate` position to the callable the
    registry bound it to, so the engine never needs the registry again.
    """

    name: str = Field(min_length=1)
    description: str = ""
    phases: list[Phase] = Field(min_length=1)
    functions: dict[str, Function] = Field(default_factory=dict)

    @property
    def phase_names(self) -> tuple[str, ...]:
        return tuple(phase.name for phase in self.phases)

    def phase(self, name: str) -> DeterministicPhase | AgentPhase:
        for phase in self.phases:
            if phase.name == name:
                return phase
        raise WorkflowLoadError(
            f"no phase named {name!r} (phases: {', '.join(self.phase_names)})",
            workflow=self.name,
        )

    def function(self, name: str) -> Function:
        try:
            return self.functions[name]
        except KeyError:
            raise UnknownFunctionError(
                f"{name!r} was not resolved when this workflow was loaded",
                workflow=self.name,
                unknown=(name,),
                registered=tuple(sorted(self.functions)),
            ) from None


def load_workflow(source: str | Path, registry: FunctionRegistry) -> Workflow:
    """Parse, validate and resolve one workflow document.

    A `Path` is read from disk; a `str` *is* the YAML document. The split is on
    type and never on what the string looks like: guessing would make
    `load_workflow("name: task")` and `load_workflow("workflows/task.yaml")`
    two spellings of one argument, and a wrong guess reads a file the caller
    never named.
    """
    if isinstance(source, Path):
        text = _read(source)
        origin = str(source)
    else:
        text = source
        origin = "<string>"
    data = _parse(text, origin)
    workflow = _validate(data, origin)
    return _resolve(workflow, registry)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkflowLoadError(f"no such workflow document: {path}") from exc
    except UnicodeDecodeError as exc:
        raise WorkflowLoadError(f"{path} is not valid UTF-8") from exc
    except OSError as exc:
        raise WorkflowLoadError(f"{path} is unreadable: {exc.strerror}") from exc


def _parse(text: str, origin: str) -> Mapping[str, Any]:
    try:
        data = yaml.load(text, Loader=_YamlLoader)
    except yaml.YAMLError as exc:
        # The YAML library's own exception must not escape: callers catch
        # WorkflowLoadError, and a ScannerError says nothing about workflows.
        raise WorkflowLoadError(f"{origin} is not valid YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        # An empty or comment-only document parses to None, which would
        # otherwise fail much later with a TypeError.
        raise WorkflowLoadError(
            f"{origin}: the top level of a workflow document must be a mapping, "
            f"got {type(data).__name__}"
        )
    return data


def _validate(data: Mapping[str, Any], origin: str) -> Workflow:
    try:
        return Workflow.model_validate(dict(data))
    except ValidationError as exc:
        raise _load_error_from(exc, origin, data) from exc


def _load_error_from(
    exc: ValidationError, origin: str, data: Mapping[str, Any]
) -> WorkflowLoadError:
    """Turn pydantic's first complaint into an operator-readable failure."""
    first = exc.errors()[0]
    loc = tuple(first.get("loc", ()))
    return WorkflowLoadError(
        f"{origin}: {first.get('msg', 'did not validate')}"
        + (f" ({exc.error_count()} validation errors in total)" if exc.error_count() > 1 else ""),
        workflow=data.get("name") if isinstance(data.get("name"), str) else None,
        phase=_phase_name_at(loc, data),
        field=".".join(str(part) for part in loc) or None,
    )


def _phase_name_at(loc: tuple[Any, ...], data: Mapping[str, Any]) -> str | None:
    """The `name:` of the phase a pydantic error location points into.

    The index alone (`phases.3`) sends a reader counting list entries; the name
    is what they search the file for. A phase with no usable name falls back to
    its position, which is all there is to say about it.
    """
    if len(loc) < 2 or loc[0] != "phases" or not isinstance(loc[1], int):
        return None
    phases = data.get("phases")
    if not isinstance(phases, list) or loc[1] >= len(phases):
        return None
    entry = phases[loc[1]]
    name = entry.get("name") if isinstance(entry, Mapping) else None
    return name if isinstance(name, str) and name else f"#{loc[1]}"


def _function_names(phase: DeterministicPhase | AgentPhase) -> Iterator[tuple[str, str]]:
    """Every `(position, name)` this phase references."""
    if isinstance(phase, DeterministicPhase):
        yield "run", phase.run
    if phase.when is not None:
        yield "when", phase.when
    for gate in phase.gates:
        yield "gate", gate


def _resolve(workflow: Workflow, registry: FunctionRegistry) -> Workflow:
    """Bind every referenced name, or refuse the whole document.

    Every offender in the document is collected before raising: reporting one
    per load attempt turns fixing a workflow into an N-round trip, and the
    engine only ever loads once, at the top of a run.
    """
    functions: dict[str, Function] = {}
    missing: list[tuple[str, str, str]] = []
    for phase in workflow.phases:
        for position, name in _function_names(phase):
            if name in functions:
                continue
            if name in registry:
                functions[name] = registry.resolve(name)
            else:
                missing.append((phase.name, position, name))
    if missing:
        detail = "; ".join(
            f"phase {phase!r} {position} {name!r}" for phase, position, name in missing
        )
        unknown = tuple(dict.fromkeys(name for _phase, _position, name in missing))
        raise UnknownFunctionError(
            f"names {len(unknown)} function(s) nobody registered: {detail} "
            f"(registered: {', '.join(registry.names()) or 'nothing'})",
            workflow=workflow.name,
            phase=missing[0][0],
            field=missing[0][1],
            unknown=unknown,
            registered=registry.names(),
        )
    return workflow.model_copy(update={"functions": functions})
