"""Cancelling a subtask mid-turn, and no instance hooks on engine-built agents
(pygents-070 adoption design A3, A5; card 0822234f).

Engine tier (agent-manager design §14: the engine driven with fakes, no real
harness): `runtime.engine.run_subtask_async` runs as an asyncio task over real
pygents agents, fake steps, a fake agent runner that launches a
`sys.executable -c` sleeper through `harness.launcher.run_direct` and hands it
to the bridge's spawn hook (the stand-in for `claude -p`), and a real temp
store built as tests/runtime/test_resume.py builds it. The test cancels the
task the way Ctrl-C does. Workers run in `asyncio.to_thread`, so ordering uses
`threading.Event`s, never sleeps. Registry isolation between tests is the
autouse `fresh_pygents` fixture; the one global hook these tests add is
unregistered by its own fixture.

No engine path breaks or returns out of `agent.run()` (checked when this file
was written), so there is no early-exit reusability test.
"""

import asyncio
import json
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, AgentRegistry, HookRegistry, StopReason, TurnHook, hook
from pygents.errors import UnregisteredAgentError

from agent_manager import models, store as store_module
from agent_manager.harness import launcher
from agent_manager.runtime import bridge
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.workflow.phases import AgentPhase, Step, Workflow

RUN_ID = "run-2026-09-27-02"
STORY_ID = "dd4a87d5"
CARD_ID = "0822234f"
OTHER_CARD = "0822234e"
REPO = Path("/repo")
FIXED = datetime(2026, 9, 27, tzinfo=timezone.utc)
AGENT_NAME = f"{RUN_ID}:{CARD_ID}"
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


@pytest.fixture
def completions():
    """`(phase, stop reason)` of every turn that ends while the test runs.

    A global `ON_COMPLETE` hook with no tags, so it fires for every turn, in
    the order they end. Unregistered on the way out: it never outlives the
    test, and the `subtask` hooks of runtime/checkpoint.py are left alone.
    """
    seen: list[tuple[str, StopReason | None]] = []

    async def record_turn_completion(turn: Any, stop_reason: Any) -> None:
        seen.append((turn.kwargs["phase"], stop_reason))

    hook(TurnHook.ON_COMPLETE)(record_turn_completion)
    yield seen
    HookRegistry.unregister("record_turn_completion")


def _subtask(card_id: str = CARD_ID) -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card_id,
        branch=f"m8/task-pin-pygents-{card_id}",
        base_branch="m8/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _run(workflow: Workflow, opened, card_id: str = CARD_ID, **kwargs: Any):
    """`run_subtask_async`'s coroutine for `card_id`: await it, or wrap it in a task to cancel it."""
    return runtime_engine.run_subtask_async(
        workflow,
        opened,
        story_id=STORY_ID,
        subtask=_subtask(card_id),
        repo_dir=REPO,
        clock=lambda: FIXED,
        **kwargs,
    )


def _go(workflow: Workflow, opened, **kwargs: Any):
    return asyncio.run(_run(workflow, opened, **kwargs))


def _reasons(opened, card_id: str = CARD_ID) -> list[tuple[int, str]]:
    return [
        (row[0], row[1])
        for row in opened.connection.execute(
            "SELECT seq, reason FROM checkpoints WHERE card_id = ? ORDER BY seq", (card_id,)
        ).fetchall()
    ]


def _stored_agents(opened, card_id: str = CARD_ID) -> list[dict]:
    return [
        json.loads(row[0])
        for row in opened.connection.execute(
            "SELECT agent FROM checkpoints WHERE card_id = ? ORDER BY seq", (card_id,)
        ).fetchall()
    ]


def _assert_free(name: str) -> None:
    """`name` is not in `AgentRegistry`, and a new agent may take it."""
    with pytest.raises(UnregisteredAgentError):
        AgentRegistry.get(name)
    Agent(name, "a relaunch under the same name", [])
    AgentRegistry.unregister(name)


def _assert_stopped(agent: Agent) -> None:
    """The run is over. pygents refuses a property change on a running or a
    paused agent (`SafeExecutionError`), and clears the turn in flight when
    `run()` exits, so the agent still serialises."""
    assert agent.is_paused is False
    agent.description = "changed after the run ended"
    assert agent.to_dict()["current_turn"] is None


class _Launcher:
    """A fake agent runner standing in for `claude -p`.

    It launches `SLEEPER` with `launcher.run_direct` and hands the process to
    `bridge.current_spawn_hook()`, as `dispatch.py` hands a harness process to
    the bridge, then blocks until the process ends. `spawned` is set once the
    bridge knows the process; `left` once the worker thread is done with it.
    """

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.spawned = threading.Event()
        self.left = threading.Event()
        self.process = None

    def __call__(self, phase: Any, table: Any, rendered: Any) -> dict[str, Any]:
        record = bridge.current_spawn_hook()

        def on_spawn(process) -> None:
            self.process = process
            record(process)
            self.spawned.set()

        try:
            launcher.run_direct(
                SLEEPER,
                cwd=self.log_dir,
                timeout=120,
                stdout_path=self.log_dir / f"{phase.name}.log",
                on_spawn=on_spawn,
            )
        finally:
            self.left.set()
        return {"ok": True}


def _instant(phase: Any, table: Any, rendered: Any) -> dict[str, Any]:
    return {"ok": True}


def _with_agent_phase(ran: list[str]) -> Workflow:
    """Step `a`, agent phase `spec`, step `b`. Each step appends its name to `ran`."""

    def make(name: str):
        def run(card: str) -> dict[str, Any]:
            ran.append(name)
            return {name: name.upper()}

        return run

    return Workflow(
        "mixed",
        (
            Step("a", make("a")),
            AgentPhase("spec", "spec_author", (), None),
            Step("b", make("b")),
        ),
    )


# ── cancel mid-agent-phase (A3) ──────────────────────────────────────────────


async def test_cancelling_mid_agent_phase_kills_the_process_and_leaves_a_resumable_turn(
    store, tmp_path, completions
):
    ran: list[str] = []
    wf = _with_agent_phase(ran)
    claude = _Launcher(tmp_path)
    task = asyncio.create_task(_run(wf, store, agent_runner=claude))
    assert await asyncio.to_thread(claude.spawned.wait, 5)
    agent = AgentRegistry.get(AGENT_NAME)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # M6's bridge killed the process the cancelled turn launched.
    assert await asyncio.to_thread(claude.left.wait, 10)
    assert claude.process.returncode == -signal.SIGKILL
    # The turn in flight ended CANCELLED, and no later turn started.
    assert completions == [("a", StopReason.COMPLETED), ("spec", StopReason.CANCELLED)]
    _assert_stopped(agent)
    _assert_free(AGENT_NAME)
    # Cancellation writes no row: the `turn` row saved before `spec` is the newest.
    assert _reasons(store) == [(0, "turn"), (1, "turn")]
    latest = store.latest_checkpoint(CARD_ID)
    assert latest.reason == "turn"
    assert runtime_engine.pending_phase(latest) == "spec"

    ran.clear()
    summary = await _run(wf, store, agent_runner=_instant, resume_from=latest)

    assert ran == ["b"]
    assert summary.status == "done"
    assert set(summary.results) == {"a", "spec", "b"}
    assert _reasons(store)[2:] == [(2, "turn"), (3, "turn"), (4, "done")]
