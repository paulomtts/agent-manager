"""The milestone's `StopSignal` on its own (supervisor-tree design T5).

Runtime tier: a fake agent that only counts `pause()` calls, since the signal
knows nothing of pygents.
"""

from agent_manager.runtime.stop import StopSignal


class FakeAgent:
    def __init__(self) -> None:
        self.paused = 0

    def pause(self) -> None:
        self.paused += 1


def test_first_trigger_is_primary():
    stop = StopSignal()
    assert stop.trigger("A") is True and stop.trigger("B") is False
    assert stop.primary == "A" and stop.triggered


def test_trigger_pauses_registered_agents_and_late_registrations():
    stop, early, late = StopSignal(), FakeAgent(), FakeAgent()
    stop.register(early)
    stop.trigger("A")
    stop.register(late)
    assert early.paused == 1 and late.paused == 1


def test_unregistered_agents_are_not_paused():
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent)
    stop.unregister(agent)
    stop.trigger("A")
    assert agent.paused == 0
