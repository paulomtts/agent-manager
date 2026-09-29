"""The milestone's `StopSignal` on its own (supervisor-tree design T5).

Runtime tier: a fake agent that only counts `pause()` calls, since the signal
knows nothing of pygents.
"""

import pytest

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


def test_request_pauses_registered_agents_and_leaves_primary_unset():
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent)
    stop.request("pause")
    assert agent.paused == 1 and stop.triggered
    assert stop.primary is None and stop.requested == "pause"
    stop.request("pause")
    assert agent.paused == 2


def test_request_returns_whether_requested_changed():
    stop = StopSignal()
    assert stop.request("pause") is True
    assert stop.request("pause") is False
    assert stop.request("cancel") is True
    assert stop.request("pause") is False
    assert stop.request("cancel") is False
    assert stop.requested == "cancel"


def test_cancel_overrides_pause_and_pause_after_cancel_is_a_noop():
    paused_first = StopSignal()
    paused_first.request("pause")
    paused_first.request("cancel")
    assert paused_first.requested == "cancel"

    cancelled_first = StopSignal()
    assert cancelled_first.request("cancel") is True
    assert cancelled_first.request("pause") is False
    assert cancelled_first.requested == "cancel"


def test_agent_registered_after_request_is_paused_at_once():
    stop, late = StopSignal(), FakeAgent()
    stop.request("pause")
    stop.register(late)
    assert late.paused == 1


def test_first_trigger_after_a_request_becomes_primary():
    stop = StopSignal()
    stop.request("pause")
    assert stop.trigger("A") is True and stop.trigger("B") is False
    assert stop.primary == "A" and stop.requested == "pause" and stop.triggered


def test_an_unknown_command_is_refused_before_any_effect():
    # Review Focus 1.
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent)
    with pytest.raises(ValueError, match="stop"):
        stop.request("stop")  # type: ignore[arg-type]
    assert agent.paused == 0 and not stop.triggered and stop.requested is None
