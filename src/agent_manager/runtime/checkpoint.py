"""Checkpoint hooks (pygents-engine design G5, G8, §6; supervisor-tree T5).

Every turn of a subtask's agent is saved by `before_turn` as
`Agent.to_dict()` in the store's `checkpoints` table *before* it runs. The
after-run rows (`done`, `escalated`) are written by `runtime/engine.py`
through `save`.

The run's one stop is a `StopSignal` (`runtime/stop.py`) that pauses the
agent; pygents then fires `ON_PAUSE` at the top of its loop, between turns,
and `on_pause` saves `parked` and raises `Parked`, which propagates out of
`agent.run()` and ends it cleanly -- nothing breaks or returns out of the
loop. The raise is what ends the run: `pause()` alone would leave `run()`
waiting forever for a `resume()`.

The hooks are module-level on purpose: pygents' `HookRegistry` is process-wide
and keyed on the function's name, and closures from one factory collide in
it (design §11). They are global and tagged `subtask`, so they fire for every
agent tagged `subtask`; they find their run through `state.current_run` and
do nothing when no run is set.

`saved_at` is read from the wall clock, never from the run's injected
`clock`: that clock stamps phase rows, and a checkpoint reading it would
shift every phase-row stamp after it (G10).
"""

from __future__ import annotations

from typing import Any

from pygents import AgentHook, hook

from agent_manager import paths
from agent_manager.runtime import walk
from agent_manager.runtime.state import RunDeps, current_run
from agent_manager.store import TurnFloor
from agent_manager.workflow.phases import AgentPhase

_FLOORED = ("turn", "parked")
"""The reasons whose row names a turn still to run, so it carries that turn's floor."""


class Parked(Exception):
    """The run's `StopSignal` parked the subtask before `.before_phase`."""

    def __init__(self, before_phase: str) -> None:
        self.before_phase = before_phase
        super().__init__(f"stopped before {before_phase}")


def save(agent: Any, reason: str) -> None:
    """Write `agent` as the next checkpoint of the running subtask, or nothing with no run."""
    deps = current_run.get(None)
    if deps is None:
        return
    deps.store.save_checkpoint(
        deps.subtask.card_id,
        workflow=deps.workflow.name,
        digest=deps.workflow.digest(),
        reason=reason,
        agent=agent.to_dict(),
        saved_at=walk._utcnow(),
        floor=_floor(deps, agent, reason),
    )


def _floor(deps: RunDeps, agent: Any, reason: str) -> TurnFloor | None:
    """The floor of the agent-phase turn `agent` would run next, or `None` (exactly-once 1.2).

    Only `turn` and `parked` rows name a turn still to run. The next turn is
    the one in flight, else the queue head; a step (E9), a phase the workflow
    does not have, or no turn at all gets no floor, and neither does a store
    with no run. A carried adoption for the same `(phase, loop)` is written
    unchanged, so the floor survives a chain of resumes; otherwise the floor
    is the highest attempt this run's directory holds for the phase.
    """
    if reason not in _FLOORED:
        return None
    state = agent.to_dict()
    turn = state.get("current_turn") or next(iter(state.get("queue") or ()), None)
    if turn is None:
        return None
    phase, loop = turn["kwargs"]["phase"], turn["kwargs"]["loop"]
    if not any(
        isinstance(p, AgentPhase) and p.name == phase for p in deps.workflow.phases
    ):
        return None
    run_id = getattr(deps.store, "run_id", None)
    if not run_id:
        return None
    carried = deps.adopt
    if carried is not None and (carried.phase, carried.loop) == (phase, loop):
        return TurnFloor(**vars(carried))
    return TurnFloor(
        phase, loop, run_id, paths.highest_attempt(run_id, deps.subtask.card_id, phase)
    )


@hook(AgentHook.BEFORE_TURN, tags={"subtask"})
async def before_turn(agent: Any) -> None:
    """Save the turn about to run. `save` writes nothing with no run set."""
    save(agent, "turn")


@hook(AgentHook.ON_PAUSE, tags={"subtask"})
async def on_pause(agent: Any) -> None:
    deps = current_run.get(None)
    if deps is None:
        return
    # ON_PAUSE fires between turns, so no turn is in flight: the next phase
    # is the queue head.
    head = agent.to_dict()["queue"][0]["kwargs"]["phase"]
    save(agent, "parked")
    raise Parked(head)
