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


@dataclass
class RunDeps:
    workflow: Any
    store: Any
    story_id: str
    subtask: Any
    agent_runner: Callable[..., Any] | None
    clock: Callable[[], Any]
    should_stop: Callable[[], bool] | None = None
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


current_run: ContextVar[RunDeps] = ContextVar("current_run")
