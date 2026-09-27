"""Checkpoint hooks (pygents-engine design G5, G8, §6; supervisor-tree T5).

Every turn of a subtask's agent is saved as `Agent.to_dict()` in the store's
`checkpoints` table *before* it runs, and the run's cooperative stop is read
at the same moment: a set stop saves `parked` and raises `Parked`, which
propagates out of `agent.run()` and ends it cleanly -- nothing breaks or
returns out of the loop. The after-run rows (`done`, `escalated`) are
written by `runtime/engine.py` through `save`.

There are two stops until Task 3.3. The M6 one is `RunDeps.should_stop`,
read by `before_turn`. The M7 one is a `StopSignal` (`runtime/stop.py`) that
pauses the agent; pygents then fires `ON_PAUSE` at the top of its loop,
between turns, and `on_pause` saves `parked` and raises `Parked`. The raise
is what ends the run: `pause()` alone would leave `run()` waiting forever
for a `resume()`.

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

from agent_manager.runtime import walk
from agent_manager.runtime.state import current_run


class Parked(Exception):
    """The run's stop was set: the subtask stopped before `.before_phase`."""

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
    )


@hook(AgentHook.BEFORE_TURN, tags={"subtask"})
async def before_turn(agent: Any) -> None:
    deps = current_run.get(None)
    if deps is None:
        return
    snapshot = agent.to_dict()
    head = (snapshot["current_turn"] or snapshot["queue"][0])["kwargs"]["phase"]
    if deps.should_stop is not None and deps.should_stop():
        save(agent, "parked")
        raise Parked(head)
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
