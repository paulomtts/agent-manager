"""The workflow as declared Python data (spec G3). No pygents import here.

Only `agent_manager.runtime` may import pygents (rule 1). This module is frozen
data plus two pure checks -- `Workflow.validate()` and `Workflow.digest()` -- so
the compiler can trust what it is handed and a resumed run can tell whether the
workflow it checkpointed is the one it is about to run.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import BaseModel


class WorkflowError(ValueError):
    """A declared workflow that must not run; `.phase` names the phase at fault."""

    def __init__(self, message: str, *, phase: str | None = None) -> None:
        self.phase = phase
        super().__init__(f"phase {phase!r}: {message}" if phase else message)


@dataclass(frozen=True)
class Goto:
    phase: str
    max_loops: int = 1


@dataclass(frozen=True)
class Retry:
    max_attempts: int
    on: tuple[str, ...]


@dataclass(frozen=True)
class Step:
    """A step may run more than once for one walk (a resume re-runs a step whose completion was not checkpointed); it must be idempotent."""

    name: str
    run: Callable[..., Any]
    args: Mapping[str, Any] = field(default_factory=dict)
    gates: tuple[Callable[..., Any], ...] = ()
    best_effort: bool = False
    when: Callable[..., bool] | None = None
    skip_to: str | None = None


@dataclass(frozen=True)
class AgentPhase:
    name: str
    role: str
    inputs: tuple[str, ...]
    result: type[BaseModel] | None
    gates: tuple[Callable[..., Any], ...] = ()
    retry: Retry | None = None
    writes: str | None = None
    timeout: timedelta = timedelta(minutes=30)
    on_fail: Goto | None = None


def _qual(fn: object) -> str:
    """A callable's stable identity for the digest: `module.qualname`."""
    return f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', repr(fn))}"


@dataclass(frozen=True)
class Workflow:
    name: str
    phases: tuple[Step | AgentPhase, ...]

    @property
    def phase_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.phases)

    def phase(self, name: str) -> Step | AgentPhase:
        for p in self.phases:
            if p.name == name:
                return p
        raise WorkflowError(f"no phase named {name!r} (phases: {', '.join(self.phase_names)})")

    def validate(self, *, launcher_timeout: timedelta, role_root: Path | None = None) -> None:
        """Refuse a workflow that must not run, naming the phase at fault.

        `launcher_timeout` is G2: every agent phase's turn timeout must exceed
        it strictly, because the launcher has to kill `claude -p` before the
        turn is cancelled -- cancellation cannot stop a `to_thread` worker.
        """
        from agent_manager import prompt
        from agent_manager.roles.loader import load_role

        order: dict[str, int] = {}
        for index, p in enumerate(self.phases):
            if p.name in order:
                raise WorkflowError("duplicate phase name", phase=p.name)
            order[p.name] = index

        seen: list[str] = []
        for index, p in enumerate(self.phases):
            if isinstance(p, Step):
                if (p.when is None) != (p.skip_to is None):
                    raise WorkflowError("`when` and `skip_to` must be given together", phase=p.name)
                if p.skip_to is not None and order.get(p.skip_to, -1) <= index:
                    raise WorkflowError(f"skip_to {p.skip_to!r} must name a later phase", phase=p.name)
            else:
                try:
                    load_role(p.role, root=role_root)
                except Exception as error:
                    raise WorkflowError(f"role {p.role!r} does not load: {error}", phase=p.name) from error
                for name in p.inputs:
                    if name not in prompt.INPUT_NAMES and name not in seen:
                        raise WorkflowError(
                            f"input {name!r} has no resolver and no earlier phase", phase=p.name)
                if p.timeout <= launcher_timeout:
                    raise WorkflowError(
                        f"timeout {p.timeout} must exceed the launcher timeout {launcher_timeout}",
                        phase=p.name,
                    )
                if p.on_fail is not None:
                    if p.on_fail.max_loops < 1:
                        raise WorkflowError("Goto max_loops must be at least 1", phase=p.name)
                    if order.get(p.on_fail.phase, index) >= index:
                        raise WorkflowError(
                            f"Goto {p.on_fail.phase!r} must name an earlier phase", phase=p.name)
            seen.append(p.name)

    def digest(self) -> str:
        """sha256 over the name and one record per phase, in declared order.

        Fields are joined by `\\x1f` and each record ends with `\\x1e`, so no
        two different workflows can concatenate to the same bytes by shifting
        a value across a field boundary.
        """
        h = hashlib.sha256(self.name.encode())
        for p in self.phases:
            if isinstance(p, Step):
                parts = ["step", p.name, _qual(p.run), repr(sorted(p.args.items())),
                         *map(_qual, p.gates), str(p.best_effort),
                         _qual(p.when) if p.when else "-", p.skip_to or "-"]
            else:
                parts = ["agent", p.name, p.role, ",".join(p.inputs),
                         _qual(p.result) if p.result else "-", *map(_qual, p.gates),
                         repr(p.retry), p.writes or "-", str(p.timeout), repr(p.on_fail)]
            h.update("\x1f".join(parts).encode() + b"\x1e")
        return h.hexdigest()
