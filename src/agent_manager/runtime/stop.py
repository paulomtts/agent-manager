"""The milestone's cooperative stop (supervisor-tree design T5).

One `StopSignal` per run, living on the run's one event loop, so it takes no
lock. `trigger` records the first caller as `primary` and pauses every
registered subtask agent; `register` pauses an agent at once if the signal
has already fired. A paused pygents agent fires `ON_PAUSE` before its next
turn, where `runtime/checkpoint.py`'s `on_pause` saves `parked` and raises
`Parked`.

Nothing here imports pygents: an agent is anything with a `.pause()`.
"""

from __future__ import annotations

from typing import Any


class StopSignal:
    def __init__(self) -> None:
        self.triggered = False
        self.primary: str | None = None
        self._agents: set[Any] = set()

    def trigger(self, story_id: str) -> bool:
        """Pause every registered agent; True only for the first caller, who becomes `primary`."""
        first = not self.triggered
        if first:
            self.triggered, self.primary = True, story_id
        for agent in list(self._agents):
            agent.pause()
        return first

    def register(self, agent: Any) -> None:
        """Track `agent`; pause it at once if the signal already fired."""
        self._agents.add(agent)
        if self.triggered:
            agent.pause()

    def unregister(self, agent: Any) -> None:
        """Stop tracking `agent`. A no-op for an agent never registered."""
        self._agents.discard(agent)
