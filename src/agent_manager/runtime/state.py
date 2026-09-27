"""What a running subtask's tools need, reachable without passing it through pygents.

A pygents turn carries only JSON-able kwargs (`{"phase", "loop"}`), and a
checkpoint must not hold a store or a runner. So the run's dependencies live in
one `RunDeps`, set in `current_run` by whoever drives the agent (Task 3.4's
`run_subtask`), and read by the compiled tools. The tools run in a task
pygents creates inside `agent.run()`, which copies the context, so the value
set before the run is the value they see.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_manager.runtime.stop import StopSignal


@dataclass
class RunDeps:
    workflow: Any
    store: Any
    story_id: str
    subtask: Any
    agent_runner: Callable[..., Any] | None
    clock: Callable[[], Any]
    should_stop: Callable[[], bool] | None = None
    stop: StopSignal | None = None
    """The milestone's `StopSignal` (supervisor-tree T5). Declared after
    `should_stop` so the positional construction still binds; `should_stop`
    stays until Task 3.3."""
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    running: str | None = None
    """The phase whose tool was entered last. pygents clears the agent's
    `current_turn` before an error leaves `run()`, so the engine reads the
    phase an unexpected error escaped from here instead."""


current_run: ContextVar[RunDeps] = ContextVar("current_run")
