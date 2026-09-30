"""Checkpoints written by the pygents engine (pygents-engine design G5, §6).

Runtime tier: `runtime.engine.run_subtask` drives fake steps that know only
the arguments they are bound, over a real temp SQLite projection plus a real
temp JSONL journal, built as tests/runtime/test_compile.py builds it. The
checkpoint rows are read straight from the store's `checkpoints` table.
"""

import asyncio
import itertools
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, Turn, tool

from agent_manager import models, store as store_module
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime import checkpoint
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.workflow.phases import AgentPhase, Step, Workflow

RUN_ID = "run-2026-09-26-02"
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


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=CARD_ID,
        branch=f"m6/task-checkpoint-every-turn-{CARD_ID}",
        base_branch="m6/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _rows(opened) -> list[tuple[int, str, dict]]:
    """Every checkpoint row in the store, oldest first, agent decoded."""
    return [
        (row[0], row[1], json.loads(row[2]))
        for row in opened.connection.execute(
            "SELECT seq, reason, agent FROM checkpoints ORDER BY card_id, seq"
        ).fetchall()
    ]


def _head(agent: dict) -> str:
    """The phase a stored agent would run next: its current turn, else its queue head."""
    return (agent["current_turn"] or agent["queue"][0])["kwargs"]["phase"]


def test_parked_names_the_phase_it_stopped_before():
    parked = checkpoint.Parked("b")

    assert isinstance(parked, Exception)
    assert parked.before_phase == "b"
    assert str(parked) == "stopped before b"


async def test_hook_without_a_run_does_nothing(store):
    ran: list[str] = []

    async def some_tool() -> str:
        ran.append("some_tool")
        return "ok"

    some_tool = tool(some_tool)
    agent = Agent("stray", "t", [some_tool], tags=["subtask"])
    await agent.put(Turn(some_tool))

    async for _ in agent.run():  # no LookupError from current_run
        pass

    assert ran == ["some_tool"]
    assert _rows(store) == []


def test_save_without_a_run_does_nothing(store):
    class Unreadable:
        def to_dict(self) -> dict:
            raise AssertionError("save read the agent with no run set")

    checkpoint.save(Unreadable(), "turn")

    assert _rows(store) == []


def _three_steps(ran: list[str]) -> Workflow:
    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        return {"b": 2}

    def c(card: str) -> dict[str, Any]:
        ran.append("c")
        return {"c": 3}

    return Workflow("three", (Step("a", a), Step("b", b), Step("c", c)))


def test_every_turn_is_checkpointed_before_it_runs(store):
    ran: list[str] = []

    summary = runtime_engine.run_subtask(
        _three_steps(ran),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
    )

    assert ran == ["a", "b", "c"]
    assert summary.status == "done"
    assert summary.results == {"a": {"a": 1}, "b": {"b": 2}, "c": {"c": 3}}
    rows = _rows(store)
    assert [(seq, reason) for seq, reason, _ in rows] == [
        (0, "turn"), (1, "turn"), (2, "turn"), (3, "done"),
    ]
    assert [_head(agent) for _, _, agent in rows[:3]] == ["a", "b", "c"]
    done = rows[3][2]
    assert done["current_turn"] is None
    assert done["queue"] == []
    newest = store.latest_checkpoint(CARD_ID)
    assert (newest.seq, newest.reason, newest.workflow) == (3, "done", "three")


def test_an_escalation_writes_an_escalated_row(store):
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        raise RuntimeError("boom")

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        return {}

    summary = runtime_engine.run_subtask(
        Workflow("escalates", (Step("a", a), Step("b", b))),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
    )

    assert ran == ["a"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "a"
    assert "boom" in summary.detail
    assert [(seq, reason) for seq, reason, _ in _rows(store)] == [
        (0, "turn"), (1, "escalated"),
    ]
    assert store.latest_checkpoint(CARD_ID).reason == "escalated"


class _SecondSaveBreaks:
    """The run's store, except that its second `save_checkpoint` raises.

    The first save is `a`'s `turn` row; the second is the `turn` row
    `before_turn` writes for `b`, so the error comes from the `BEFORE_TURN`
    hook, outside any phase. Every other attribute is the real store's.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._saves = itertools.count()

    def save_checkpoint(self, *args: Any, **kwargs: Any) -> Any:
        if next(self._saves) == 1:
            raise RuntimeError("checkpoint write broke")
        return self._inner.save_checkpoint(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_an_error_outside_a_phase_writes_an_escalated_row(store):
    """The generic `except Exception` branch, not `C.Escalated`: an error raised
    by the hook itself (here the store refusing `b`'s `turn` row) still leaves
    an `escalated` row before the subtask is escalated."""
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        return {"b": 2}

    summary = runtime_engine.run_subtask(
        Workflow("breaks", (Step("a", a), Step("b", b))),
        _SecondSaveBreaks(store),
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
    )

    assert ran == ["a"]
    assert summary.status == "escalated"
    assert "checkpoint write broke" in summary.detail
    assert summary.before_phase is None
    assert [(seq, reason) for seq, reason, _ in _rows(store)] == [
        (0, "turn"), (1, "escalated"),
    ]


async def test_before_turn_saves_every_turn_and_only_on_pause_parks(store):
    """`before_turn` saves `turn` for every turn, a triggered `StopSignal`
    included; the one `parked` row is `on_pause`'s, written before `c`, the
    turn that never ran. Passes before and after the M6 stop is deleted."""
    loop = asyncio.get_running_loop()
    stop = StopSignal()
    fired = threading.Event()
    ran: list[str] = []

    def fire() -> None:
        stop.trigger(STORY_ID)
        fired.set()

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        loop.call_soon_threadsafe(fire)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")
        return {"b": 2}

    def c(card: str) -> dict[str, Any]:
        ran.append("c")
        return {"c": 3}

    summary = await runtime_engine.run_subtask_async(
        Workflow("parks", (Step("a", a), Step("b", b), Step("c", c))),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        stop=stop,
    )

    assert ran == ["a", "b"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before c"
    assert summary.before_phase == "c"
    rows = _rows(store)
    assert [(seq, reason) for seq, reason, _ in rows] == [
        (0, "turn"), (1, "turn"), (2, "parked"),
    ]
    assert [_head(agent) for _, _, agent in rows] == ["a", "b", "c"]


def test_a_checkpoint_never_reads_the_injected_clock(store):
    """Guard for the plan's `saved_at` deviation: the injected clock stamps phase
    rows only -- twice per step phase (runtime/walk.py `run_one_step`) -- so a
    checkpoint must not consume it (G10). Passes before and after Task 2."""
    calls: list[datetime] = []

    def clock() -> datetime:
        calls.append(FIXED)
        return FIXED

    summary = runtime_engine.run_subtask(
        _three_steps([]),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=clock,
    )

    assert summary.status == "done"
    assert len(calls) == 6
    assert len(_rows(store)) >= 3



def test_an_engine_error_writes_no_after_run_row(store):
    """A wiring bug (`old.EngineError`, here an agent phase with no agent
    runner injected) is raised to the caller, not escalated: the `turn` row
    written before the phase stays the newest, and no `escalated` or `done`
    row follows it."""
    with pytest.raises(EngineError) as caught:
        runtime_engine.run_subtask(
            Workflow("miswired", (AgentPhase("explore", "explorer", (), None),)),
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            clock=lambda: FIXED,
        )

    assert caught.value.phase == "explore"
    assert [(seq, reason) for seq, reason, _ in _rows(store)] == [(0, "turn")]
