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


@dataclass(frozen=True)
class Adoption:
    """A turn a resumed run carries from its checkpoint's floor (exactly-once E4/E5).

    Same fields as `store_checkpoints.TurnFloor`, so `Adoption(**vars(floor))` builds one.
    Plain values only: nothing here imports the store or pygents.
    """

    phase: str
    loop: int
    source_run: str
    floor: int


@dataclass
class RunDeps:
    workflow: Any
    store: Any
    story_id: str
    subtask: Any
    agent_runner: Callable[..., Any] | None
    clock: Callable[[], Any]
    stop: StopSignal | None = None
    """The milestone's `StopSignal` (supervisor-tree T5), the run's only stop.
    `engine._run` registers the agent with it for the life of `run()`."""
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    running: str | None = None
    """The phase whose tool was entered last. pygents clears the agent's
    `current_turn` before an error leaves `run()`, so the engine reads the
    phase an unexpected error escaped from here instead."""
    adopt: Adoption | None = None
    """The floor carried from the resume checkpoint, until `take_adoption`
    consumes it. `checkpoint.save` reads it but never clears it."""
    compiled: Any = None
    """The run's own `compile.Compiled`, whose turns carry the run's derived
    timeouts (G2); the tools build every turn they yield from it. `None` -- a
    hand-built `RunDeps` -- falls back to the workflow's shared compilation."""

    def take_adoption(self, phase: str, loop: int) -> Adoption | None:
        """The carried adoption if it is for `(phase, loop)`, else `None`.

        Always clears `adopt`, on a mismatch too, so a second call returns `None`.
        """
        carried, self.adopt = self.adopt, None
        if carried is not None and (carried.phase, carried.loop) == (phase, loop):
            return carried
        return None


current_run: ContextVar[RunDeps] = ContextVar("current_run")
