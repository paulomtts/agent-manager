"""Compiling a `phases.Workflow` into two pygents tools (pygents-engine design G3, §5).

Unit tier: a real pygents `Agent` drives the compiled tools over fake steps and
a fake agent runner that knows only the arguments it is handed. The store is a
real temp SQLite projection plus a real temp JSONL journal, built as
tests/test_engine.py builds it. No git, no board, no harness process.
"""

import itertools
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, ContextPool, ContextQueue, ToolRegistry

from agent_manager import models, store as store_module
from agent_manager.runtime import compile as C, context, state
from agent_manager.workflow.phases import AgentPhase, Step, Workflow

RUN_ID = "run-2026-09-26-01"
STORY_ID = "f9c19dc3"
FIXED = datetime(2026, 9, 26, tzinfo=timezone.utc)
_agent_names = itertools.count()


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


def _noop() -> dict[str, Any]:
    return {}


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id="023d918e",
        branch="m6/task-compile-a-workflow-into-023d918e",
        base_branch="m6/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _deps(workflow, store, runner=None) -> state.RunDeps:
    return state.RunDeps(
        workflow=workflow,
        store=store,
        story_id=STORY_ID,
        subtask=_subtask(),
        agent_runner=runner,
        clock=lambda: FIXED,
    )


async def _drive(workflow, deps) -> Agent:
    compiled = C.compile_workflow(workflow)
    agent = Agent(
        f"run:{next(_agent_names)}",
        "test run",
        [compiled.agent_phase, compiled.step_phase],
        context_pool=ContextPool(),
        context_queue=ContextQueue(limit=10),
        tags=["subtask"],
    )
    await agent.context_pool.add(context.seed_item({"worktree": "/w"}))
    token = state.current_run.set(deps)
    try:
        await agent.put(compiled.first_turn())
        async for _ in agent.run():
            pass
    finally:
        state.current_run.reset(token)
    return agent


def _phase_rows(opened) -> list[tuple[str | None, str]]:
    return [
        (line.phase, line.payload["status"])
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ]


def _pool_ids(agent) -> list[str]:
    return [item.id for item in agent.context_pool.items]


async def test_linear_flow_stores_every_result(store):
    ran: list[Any] = []
    wf = Workflow("t", (
        Step("a", lambda worktree: ran.append(("a", worktree)) or {"a": 1}),
        Step("b", lambda a: ran.append(("b", a)) or {"b": 2}),
    ))
    deps = _deps(wf, store)

    agent = await _drive(wf, deps)

    assert ran == [("a", "/w"), ("b", {"a": 1})]
    assert agent.context_pool.get("a").content == {"a": 1}
    assert agent.context_pool.get("b").content == {"b": 2}
    assert _phase_rows(store) == [
        ("a", "started"), ("a", "done"), ("b", "started"), ("b", "done"),
    ]
    assert deps.warnings == []
    assert deps.skipped == []


async def test_an_agent_result_is_pooled_and_bound_into_the_next_step(store):
    briefs: list[Any] = []
    seen: list[Any] = []

    def runner(phase, table, rendered):
        briefs.append((phase.name, table["worktree"], rendered.phase))
        return {"summary": "explored"}

    wf = Workflow("t", (
        AgentPhase("explore", "explorer", (), None),
        Step("use", lambda explore: seen.append(explore) or {}),
    ))

    agent = await _drive(wf, _deps(wf, store, runner))

    assert briefs == [("explore", "/w", "explore")]
    assert seen == [{"summary": "explored"}]
    assert agent.context_pool.get("explore").content == {"summary": "explored"}


async def test_skip_to_records_skipped_phases(store):
    ran: list[str] = []
    wf = Workflow("t", (
        Step("a", lambda: {"done": True}, when=lambda result: result["done"], skip_to="d"),
        Step("b", lambda: ran.append("b") or {}),
        Step("c", lambda: ran.append("c") or {}),
        Step("d", lambda: ran.append("d") or {}),
    ))
    deps = _deps(wf, store)

    agent = await _drive(wf, deps)

    assert ran == ["d"]
    assert deps.skipped == ["b", "c"]
    assert _pool_ids(agent) == ["subtask", "a", "d"]


async def test_best_effort_failure_is_a_warning(store):
    ran: list[str] = []

    def board_move():
        raise RuntimeError("board down")

    wf = Workflow("t", (
        Step("move", board_move, best_effort=True),
        Step("b", lambda: ran.append("b") or {}),
    ))
    deps = _deps(wf, store)

    agent = await _drive(wf, deps)

    assert ran == ["b"]
    assert deps.warnings == ["best-effort phase 'move' failed: RuntimeError: board down"]
    assert "move" not in _pool_ids(agent)
    assert _phase_rows(store)[:2] == [("move", "started"), ("move", "failed")]


async def test_a_gate_warning_lands_in_the_run_warnings(store):
    def slow_gate(result):
        return {"warn": "slow"}

    wf = Workflow("t", (Step("a", _noop, gates=(slow_gate,)),))
    deps = _deps(wf, store)

    await _drive(wf, deps)

    assert deps.warnings == ["phase 'a' gate 'slow_gate' warned: slow"]


async def test_step_gate_verdict_escalates(store):
    ran: list[str] = []
    wf = Workflow("t", (
        Step("a", _noop, gates=(lambda result: {"blocked": "x", "detail": "d"},)),
        Step("b", lambda: ran.append("b") or {}),
    ))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store))

    assert info.value.phase == "a"
    assert "d" in info.value.detail
    assert ran == []
    assert _phase_rows(store) == [("a", "started"), ("a", "failed")]


async def test_a_raising_step_escalates_with_the_rendered_error(store):
    def boom():
        raise ValueError("bad input")

    wf = Workflow("t", (Step("a", boom), Step("b", _noop)))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store))

    assert info.value.phase == "a"
    assert info.value.detail == "ValueError: bad input"
    assert str(info.value) == "a: ValueError: bad input"


def test_turns_carry_the_phase_the_loop_and_a_timeout():
    wf = Workflow("t", (
        Step("a", _noop),
        AgentPhase("b", "explorer", (), None, timeout=timedelta(minutes=40)),
    ))
    compiled = C.compile_workflow(wf)

    first = compiled.first_turn()
    nxt = compiled.after("a", 2)

    assert first.tool is compiled.step_phase
    assert first.kwargs == {"phase": "a", "loop": 0}
    assert first.timeout == C.STEP_TIMEOUT == 3600
    assert nxt.tool is compiled.agent_phase
    assert nxt.kwargs == {"phase": "b", "loop": 2}
    assert nxt.timeout == 2400
    assert compiled.after("b", 0) is None


def test_the_tools_are_registered_under_digest_suffixed_names():
    wf = Workflow("t", (Step("a", _noop),))

    compiled = C.compile_workflow(wf)

    suffix = wf.digest()[:8]
    assert compiled.agent_phase.__name__ == f"agent_phase_t_{suffix}"
    assert ToolRegistry.get(f"agent_phase_t_{suffix}") is compiled.agent_phase
    assert ToolRegistry.get(f"step_phase_t_{suffix}") is compiled.step_phase
    assert C.compile_workflow(wf) is compiled


def test_two_versions_of_one_workflow_compile_side_by_side():
    # Review Focus 2: an edited workflow keeps its name but not its digest.
    v1 = Workflow("t", (Step("a", _noop),))
    v2 = Workflow("t", (Step("a", _noop), Step("b", _noop)))

    first, second = C.compile_workflow(v1), C.compile_workflow(v2)

    assert first is not second
    assert first.step_phase.__name__ != second.step_phase.__name__


async def test_a_run_calls_its_own_workflows_callables_when_the_compilation_is_shared(store):
    # Review Focus 3: the digest keys callables on module.qualname, which two
    # closures from one factory share.
    calls: list[int] = []

    def make(tag):
        def step():
            calls.append(tag)
            return {}

        return step

    first = Workflow("t", (Step("a", make(1)),))
    second = Workflow("t", (Step("a", make(2)),))
    assert first.digest() == second.digest()

    await _drive(first, _deps(first, store))
    await _drive(second, _deps(second, store))

    assert calls == [1, 2]


def test_two_threads_compiling_at_once_share_one_compilation():
    # Review Focus 2: two lanes, one workflow, no duplicate-name ValueError.
    wf = Workflow("t", (Step("a", _noop),))
    out: list[Any] = []
    errors: list[BaseException] = []

    def work():
        try:
            out.append(C.compile_workflow(wf))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(out) == 8
    assert len({id(c) for c in out}) == 1
