"""The run's stop, bridged onto the pygents engine (pygents-engine design G8;
supervisor-tree design T5).

Runtime tier: `runtime.engine.run_subtask` / `run_subtask_async` over fake
steps and a real temp store. Two stops are covered. The M6 one is a plain
`threading.Event`, as orchestrate.py's `RunStop` is; the engine only ever
calls its `is_set` (kept until Task 3.3). The M7 one is a `StopSignal`, which
pauses the registered pygents agent; its `ON_PAUSE` hook parks the subtask.

Steps run off the loop in `asyncio.to_thread` workers, so a step that fires
the `StopSignal` hands `trigger` to the loop with `call_soon_threadsafe`
(the signal lives on the loop), and ordering between steps uses
`threading.Event`s, never sleeps.
"""

import asyncio
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import models, store as store_module
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.stop import StopSignal
from agent_manager.workflow.phases import AgentPhase, Step, Workflow

RUN_ID = "run-2026-09-26-03"
STORY_ID = "a6c7bff3"
CARD_ID = "921ed349"
REPO = Path("/repo")
FIXED = datetime(2026, 9, 26, tzinfo=timezone.utc)


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


def _subtask(card_id: str = CARD_ID) -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card_id,
        branch=f"m6/task-checkpoint-every-turn-{card_id}",
        base_branch="m6/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _reasons(opened) -> list[tuple[int, str]]:
    return [
        (row[0], row[1])
        for row in opened.connection.execute(
            "SELECT seq, reason FROM checkpoints ORDER BY seq"
        ).fetchall()
    ]


def _head(agent: dict) -> str:
    return (agent["current_turn"] or agent["queue"][0])["kwargs"]["phase"]


def test_stop_set_during_a_phase_parks_before_the_next(store):
    stop = threading.Event()
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        stop.set()
        return {"a": 1}

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        return {"b": 2}

    summary = runtime_engine.run_subtask(
        Workflow("stops", (Step("a", a), Step("b", b))),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        should_stop=stop.is_set,
    )

    assert ran == ["a"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before b"
    assert summary.failed_phase is None
    assert summary.results == {"a": {"a": 1}}
    assert _reasons(store) == [(0, "turn"), (1, "parked")]
    newest = store.latest_checkpoint(CARD_ID)
    assert newest.reason == "parked"
    assert _head(newest.agent) == "b"


def test_a_stop_set_before_the_run_parks_before_the_first_phase(store):
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    summary = runtime_engine.run_subtask(
        Workflow("never", (Step("a", a),)),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        should_stop=lambda: True,
    )

    assert ran == []
    assert summary.status == "stopped"
    assert summary.detail == "stopped before a"
    assert summary.results == {}
    assert _reasons(store) == [(0, "parked")]
    assert _head(store.latest_checkpoint(CARD_ID).agent) == "a"


# ── StopSignal (supervisor-tree T5, card 364babde) ───────────────────────────

A_CARD = "aaaa0001"
B_CARD = "bbbb0002"
AGENT_NAME = f"{RUN_ID}:{CARD_ID}"


def _async_go(workflow: Workflow, opened, card_id: str, stop: StopSignal):
    return runtime_engine.run_subtask_async(
        workflow,
        opened,
        story_id=STORY_ID,
        subtask=_subtask(card_id),
        repo_dir=REPO,
        clock=lambda: FIXED,
        stop=stop,
    )


async def test_a_trigger_from_one_subtask_parks_another_mid_phase(store):
    loop = asyncio.get_running_loop()
    stop = StopSignal()
    b_in, fired = threading.Event(), threading.Event()
    ran: list[str] = []

    def fire() -> None:
        # On the loop, where the signal lives.
        stop.trigger("A")
        fired.set()

    def a1(card: str) -> dict[str, Any]:
        ran.append("a1")
        if not b_in.wait(5):
            raise RuntimeError("B's first phase never started")
        loop.call_soon_threadsafe(fire)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")
        return {"a1": 1}

    def a2(card: str) -> dict[str, Any]:
        ran.append("a2")
        return {"a2": 2}

    def b1(card: str) -> dict[str, Any]:
        ran.append("b1")
        b_in.set()
        # B's phase stays in flight until A has triggered the stop.
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")
        return {"b1": 1}

    def b2(card: str) -> dict[str, Any]:
        ran.append("b2")
        return {"b2": 2}

    a_summary, b_summary = await asyncio.gather(
        _async_go(Workflow("stop_a", (Step("a1", a1), Step("a2", a2))), store, A_CARD, stop),
        _async_go(Workflow("stop_b", (Step("b1", b1), Step("b2", b2))), store, B_CARD, stop),
    )

    assert stop.primary == "A"
    assert sorted(ran) == ["a1", "b1"]
    # B: its in-flight phase finished, then it parked before the next one.
    assert b_summary.status == "stopped"
    assert b_summary.detail == "stopped before b2"
    assert b_summary.results == {"b1": {"b1": 1}}
    newest = store.latest_checkpoint(B_CARD)
    assert newest.reason == "parked"
    assert newest.agent["current_turn"] is None
    assert newest.agent["queue"][0]["kwargs"]["phase"] == "b2"
    # A was registered too, so the trigger parks it as well.
    assert a_summary.status == "stopped"
    assert a_summary.detail == "stopped before a2"
    assert store.latest_checkpoint(A_CARD).reason == "parked"


def test_a_stop_triggered_before_the_run_parks_before_the_first_phase(store):
    # Review Focus 3; also proves `run_subtask` forwards `stop`.
    stop = StopSignal()
    stop.trigger("elsewhere")
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    summary = runtime_engine.run_subtask(
        Workflow("late", (Step("a", a),)),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        stop=stop,
    )

    assert ran == []
    assert summary.status == "stopped"
    assert summary.detail == "stopped before a"
    assert summary.results == {}
    assert _reasons(store) == [(0, "parked")]
    newest = store.latest_checkpoint(CARD_ID)
    assert newest.agent["queue"][0]["kwargs"]["phase"] == "a"
    assert stop.primary == "elsewhere"


async def test_a_stop_triggered_in_the_last_phase_still_ends_done(store):
    # Review Focus 5: the queue is empty when the loop re-checks, so no ON_PAUSE.
    loop = asyncio.get_running_loop()
    stop = StopSignal()
    fired = threading.Event()

    def fire() -> None:
        stop.trigger("A")
        fired.set()

    def a(card: str) -> dict[str, Any]:
        return {"a": 1}

    def last(card: str) -> dict[str, Any]:
        loop.call_soon_threadsafe(fire)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")
        return {"last": 2}

    summary = await _async_go(
        Workflow("ends", (Step("a", a), Step("last", last))), store, CARD_ID, stop
    )

    assert stop.triggered
    assert summary.status == "done"
    assert summary.results == {"a": {"a": 1}, "last": {"last": 2}}
    assert _reasons(store) == [(0, "turn"), (1, "turn"), (2, "done")]


class _Spy(StopSignal):
    """A `StopSignal` that records every register/unregister by agent name."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    def register(self, agent: Any) -> None:
        self.calls.append(("register", agent.name))
        super().register(agent)

    def unregister(self, agent: Any) -> None:
        self.calls.append(("unregister", agent.name))
        super().unregister(agent)


class _Crash(BaseException):
    """A process death mid-phase: not an `Exception`, so nothing may catch it."""


def _spy_go(workflow: Workflow, opened, spy: _Spy, **kwargs: Any):
    return runtime_engine.run_subtask(
        workflow,
        opened,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        stop=spy,
        **kwargs,
    )


PAIR = [("register", AGENT_NAME), ("unregister", AGENT_NAME)]


def _ok(card: str) -> dict[str, Any]:
    return {"ok": 1}


def _boom(card: str) -> dict[str, Any]:
    raise RuntimeError("boom")


def _die(card: str) -> dict[str, Any]:
    raise _Crash("killed")


def test_a_done_run_unregisters_its_agent(store):
    # Review Focus 4.
    spy = _Spy()
    assert _spy_go(Workflow("fine", (Step("a", _ok),)), store, spy).status == "done"
    assert spy.calls == PAIR


def test_an_escalated_run_unregisters_its_agent(store):
    spy = _Spy()
    summary = _spy_go(Workflow("fails", (Step("a", _boom),)), store, spy)
    assert summary.status == "escalated"
    assert spy.calls == PAIR


def test_an_engine_error_unregisters_its_agent(store):
    # An agent phase with no injected runner is a wiring bug raised as EngineError.
    spy = _Spy()
    with pytest.raises(EngineError):
        _spy_go(Workflow("unwired", (AgentPhase("spec", "spec_author", (), None),)), store, spy)
    assert spy.calls == PAIR


def test_a_crash_unregisters_its_agent(store):
    spy = _Spy()
    with pytest.raises(_Crash):
        _spy_go(Workflow("dies", (Step("a", _die),)), store, spy)
    assert spy.calls == PAIR


def test_a_refused_resume_never_registers(store):
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    wf = Workflow("refused", (Step("a", a), Step("b", _ok)))
    runtime_engine.run_subtask(
        wf, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO,
        clock=lambda: FIXED, should_stop=lambda: ran == ["a"],
    )
    parked = store.latest_checkpoint(CARD_ID)
    changed = Workflow("refused", wf.phases + (Step("c", _ok),))
    spy = _Spy()

    with pytest.raises(runtime_engine.CheckpointMismatch):
        _spy_go(changed, store, spy, resume_from=parked)

    assert spy.calls == []
