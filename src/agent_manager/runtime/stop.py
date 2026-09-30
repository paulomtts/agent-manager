"""The milestone's cooperative stop (supervisor-tree design T5, live control C3).

One `StopSignal` per run, living on the run's one event loop, so it takes no
lock. Two things fire it, and both pause every registered subtask agent:

- `trigger(story_id)` is an escalation. The first escalating story becomes
  `primary`, even when a control request fired the signal before it.
- `request(command)` is a live control (`am pause`/`am cancel`). It records
  `requested` and never touches `primary`. `cancel` overrides `pause`, and
  `pause` after `cancel` changes nothing.

`register` pauses an agent at once if the signal has already fired. A paused
pygents agent fires `ON_PAUSE` before its next turn, where
`runtime/checkpoint.py`'s `on_pause` saves `parked` and raises `Parked`.

Nothing here imports pygents: an agent is anything with a `.pause()`.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

Command = Literal["pause", "cancel"]


class StopSignal:
    def __init__(self) -> None:
        self.triggered = False
        self.primary: str | None = None
        self.requested: Command | None = None
        self._agents: set[Any] = set()

    def trigger(self, story_id: str) -> bool:
        """Pause every registered agent; True only for the first escalation, which becomes `primary`."""
        first = self.primary is None
        self.triggered = True
        if first:
            self.primary = story_id
        self._pause_all()
        return first

    def request(self, command: Command) -> bool:
        """Pause every registered agent for a control; True iff `requested` changed.

        `cancel` overrides `pause`; `pause` after `cancel` and a repeated
        command leave `requested` as it was. `primary` is never touched.
        """
        if command not in get_args(Command):
            raise ValueError(f"unknown control command: {command!r}")
        self.triggered = True
        self._pause_all()
        if self.requested == "cancel" or self.requested == command:
            return False
        self.requested = command
        return True

    def register(self, agent: Any) -> None:
        """Track `agent`; pause it at once if the signal already fired."""
        self._agents.add(agent)
        if self.triggered:
            agent.pause()

    def unregister(self, agent: Any) -> None:
        """Stop tracking `agent`. A no-op for an agent never registered."""
        self._agents.discard(agent)

    def _pause_all(self) -> None:
        for agent in list(self._agents):
            agent.pause()
