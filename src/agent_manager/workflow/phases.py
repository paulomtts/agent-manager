"""The workflow as declared Python data (spec G3). No pygents import here.

Only `agent_manager.runtime` may import pygents (rule 1). This module is frozen
data plus two pure checks -- `Workflow.validate()` and `Workflow.digest()` -- so
the compiler can trust what it is handed and a resumed run can tell whether the
workflow it checkpointed is the one it is about to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
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
