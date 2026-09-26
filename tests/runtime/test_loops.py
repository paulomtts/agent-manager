"""Goto revision loops, end to end through the compiled tools (pygents-engine design G4, §5).

Runtime tier, as tests/runtime/test_compile.py: a real pygents `Agent` drives the
compiled tools, the agent runner is a fake that reads only its
`(phase, context, rendered)` arguments, and the store is a real temp SQLite
projection plus JSONL journal. No git, no board, no harness process.

The workflow is the shape the built-in task workflow will take: a critic
(`validate_spec`) that loops back to the phase it reviews (`spec`) at most once,
and a successor (`plan`) that only runs once the critic passes.
"""

import asyncio
import itertools
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, ContextPool, ContextQueue

from agent_manager import models, store as store_module
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime import compile as C, context, state
from agent_manager.workflow.phases import AgentPhase, Goto, Workflow

RUN_ID = "run-2026-09-26-01"
STORY_ID = "f9c19dc3"
FIXED = datetime(2026, 9, 26, tzinfo=timezone.utc)
FEEDBACK_ITEM = {"for": "spec", "from": "validate_spec", "detail": "no error path"}
LOOP_ORDER = ["spec", "validate_spec", "spec", "validate_spec", "plan"]
_agent_names = itertools.count()


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id="b904b9e7",
        branch="m6/task-add-goto-revision-loops-b904b9e7",
        base_branch="m6/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _deps(workflow, store, runner) -> state.RunDeps:
    return state.RunDeps(
        workflow=workflow,
        store=store,
        story_id=STORY_ID,
        subtask=_subtask(),
        agent_runner=runner,
        clock=lambda: FIXED,
    )


def _workflow() -> Workflow:
    return Workflow("loops", (
        AgentPhase("spec", "spec_author", ("feedback",), None),
        AgentPhase("validate_spec", "critic", (), None, on_fail=Goto("spec", 1)),
        AgentPhase("plan", "planner", (), None),
    ))


async def _agent(compiled) -> Agent:
    agent = Agent(
        f"loops:{next(_agent_names)}",
        "loop test run",
        [compiled.agent_phase, compiled.step_phase],
        context_pool=ContextPool(),
        context_queue=ContextQueue(limit=10),
        tags=["subtask"],
    )
    await agent.context_pool.add(context.seed_item({"worktree": "/w"}))
    await agent.put(compiled.first_turn())
    return agent


async def _consume(agent) -> None:
    async for _ in agent.run():
        pass


async def _drive(workflow, deps) -> Agent:
    agent = await _agent(C.compile_workflow(workflow))
    token = state.current_run.set(deps)
    try:
        await _consume(agent)
    finally:
        state.current_run.reset(token)
    return agent


def _critic_fails(*details: str):
    """A fake runner whose `validate_spec` fails with each detail in turn, then passes."""
    calls: list[tuple[str, Any, str]] = []
    remaining = list(details)

    def runner(phase, ctx, rendered):
        calls.append((phase.name, ctx.get("feedback"), rendered.text))
        if phase.name == "validate_spec" and remaining:
            raise AgentPhaseFailed(
                "validate_spec", outcome="gate_failed", detail=remaining.pop(0)
            )
        return {"ok": True}

    return runner, calls


async def test_blockers_loop_back_once_then_pass(store):
    runner, calls = _critic_fails("no error path")
    wf = _workflow()

    agent = await _drive(wf, _deps(wf, store, runner))

    assert [name for name, _, _ in calls] == LOOP_ORDER
    # Review Focus: the critic's re-run and the successor see no feedback meant for spec.
    assert [feedback for _, feedback, _ in calls] == [[], [], [FEEDBACK_ITEM], [], []]
    assert calls[0][2] == "# phase: spec\n# role: spec_author\n"
    assert calls[2][2] == (
        "# phase: spec\n"
        "# role: spec_author\n"
        "\n"
        "## feedback\n"
        "Feedback from review\n"
        "- validate_spec: no error path\n"
    )
    assert agent.context_pool.get("plan").content == {"ok": True}


async def test_second_failure_escalates_validation(store):
    runner, calls = _critic_fails("no error path", "still no error path")
    wf = _workflow()

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert info.value.phase == "validate_spec"
    assert info.value.detail == "still no error path"
    assert [name for name, _, _ in calls] == LOOP_ORDER[:4]
    assert "plan" not in [name for name, _, _ in calls]


async def _first_queued(agent, timeout: float = 5.0) -> list[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        queue = agent.to_dict()["queue"]
        if queue:
            return queue
        assert loop.time() < deadline, "no turn was queued after the critic failed"
        await asyncio.sleep(0.01)


async def test_loop_count_is_in_the_queued_turn(store):
    in_critic = threading.Event()
    release = threading.Event()
    names: list[str] = []

    def runner(phase, ctx, rendered):
        names.append(phase.name)
        if phase.name == "validate_spec" and names.count("validate_spec") == 1:
            in_critic.set()
            assert release.wait(5), "the test never released the critic"
            raise AgentPhaseFailed("validate_spec", outcome="gate_failed", detail="no error path")
        return {"ok": True}

    wf = _workflow()
    compiled = C.compile_workflow(wf)
    agent = await _agent(compiled)
    token = state.current_run.set(_deps(wf, store, runner))
    run = asyncio.create_task(_consume(agent))  # copies current_run into the task
    state.current_run.reset(token)
    try:
        assert await asyncio.to_thread(in_critic.wait, 5), "the critic was never dispatched"
        agent.pause()  # hold the next turn in the queue once the critic's turn ends
        release.set()
        queue = await _first_queued(agent)
    finally:
        release.set()
        agent.resume()
        await run

    assert queue[0]["kwargs"] == {"phase": "spec", "loop": 1}
    assert queue[0]["tool_name"] == compiled.agent_phase.__name__
    assert names == LOOP_ORDER
