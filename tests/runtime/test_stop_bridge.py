"""The run's stop, bridged onto the pygents engine (pygents-engine design G8).

Runtime tier: `runtime.engine.run_subtask` over fake steps and a real temp
store. The stop is a plain `threading.Event`, as orchestrate.py's `RunStop`
is; the engine only ever calls its `is_set`.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import models, store as store_module
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow.phases import Step, Workflow

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


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=CARD_ID,
        branch=f"m6/task-checkpoint-every-turn-{CARD_ID}",
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
