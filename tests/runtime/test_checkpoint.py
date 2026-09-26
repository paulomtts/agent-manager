"""Checkpoints written by the pygents engine (pygents-engine design G5, §6).

Runtime tier: `runtime.engine.run_subtask` drives fake steps that know only
the arguments they are bound, over a real temp SQLite projection plus a real
temp JSONL journal, built as tests/runtime/test_compile.py builds it. The
checkpoint rows are read straight from the store's `checkpoints` table.
"""

import itertools
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, Turn, tool

from agent_manager import models, store as store_module
from agent_manager.runtime import checkpoint
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow.phases import Step, Workflow

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
