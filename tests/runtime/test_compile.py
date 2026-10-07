"""Compiling a `phases.Workflow` into two pygents tools (pygents-engine design G3, §5).

Unit tier: a real pygents `Agent` drives the compiled tools over fake steps and
a fake agent runner that knows only the arguments it is handed. The store is a
real temp SQLite projection plus a real temp JSONL journal, built as
tests/test_engine.py builds it. No git, no board, no harness process.
"""

import asyncio
import dataclasses
import itertools
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, ContextPool, ContextQueue, ToolRegistry

from agent_manager import models, store as store_module
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime import compile as C, context, state
from agent_manager.workflow.phases import AgentPhase, Goto, Step, Workflow, WorkflowError
from agent_manager.workflow.task import TASK

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


def _deps(workflow, store, runner=None, compiled=None) -> state.RunDeps:
    return state.RunDeps(
        workflow=workflow,
        store=store,
        story_id=STORY_ID,
        subtask=_subtask(),
        agent_runner=runner,
        clock=lambda: FIXED,
        compiled=compiled,
    )


async def _new_agent(compiled) -> Agent:
    agent = Agent(
        f"run:{next(_agent_names)}",
        "test run",
        [compiled.agent_phase, compiled.step_phase],
        context_pool=ContextPool(),
        context_queue=ContextQueue(limit=10),
        tags=["compile-unit"],
    )
    await agent.context_pool.add(context.seed_item({"worktree": "/w"}))
    await agent.put(compiled.first_turn())
    return agent


async def _consume(agent) -> None:
    async for _ in agent.run():
        pass


async def _drive(workflow, deps) -> Agent:
    agent = await _new_agent(C.compile_workflow(workflow))
    token = state.current_run.set(deps)
    try:
        await _consume(agent)
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


# D3: an agent phase's turn timeout is derived from the run's launcher timeout
# (G2) at compile time; the declared workflow and its digest never change.

DECLARED = timedelta(seconds=2100)  # TASK's floor, `workflow/task.py`
RUN_VALUES = (60.0, 1800.0, 3600.0, 86400.0)


def _step_and_agents() -> Workflow:
    return Workflow("derive", (
        Step("a", _noop),
        AgentPhase("b", "explorer", (), None, timeout=DECLARED),
        AgentPhase("c", "critic", (), None, timeout=DECLARED),
    ))


@pytest.mark.parametrize(
    ("launcher", "expected"),
    [(60.0, 2100), (1800.0, 2100), (3600.0, 3900), (86400.0, 86700)],
)
def test_launcher_timeout_derives_the_agent_turn_timeout(launcher, expected):
    asked: list[str] = []

    def launcher_timeout(name: str) -> float:
        asked.append(name)
        return launcher

    compiled = C.compile_workflow(_step_and_agents(), launcher_timeout=launcher_timeout)

    assert compiled.turn_for("b", 0).timeout == expected
    assert compiled.turn_for("a", 0).timeout == C.STEP_TIMEOUT == 3600
    assert compiled.first_turn().timeout == C.STEP_TIMEOUT
    assert "a" not in asked
    assert asked == ["b"]


def test_launcher_timeout_is_resolved_per_phase():
    compiled = C.compile_workflow(
        _step_and_agents(),
        launcher_timeout=lambda name: {"b": 7200.0}.get(name, 1800.0),
    )

    assert compiled.turn_for("b", 0).timeout == 7500
    assert compiled.turn_for("c", 0).timeout == 2100
    assert compiled.after("a", 0).timeout == 7500
    assert compiled.after("b", 0).timeout == 2100


def test_launcher_timeout_and_limit_wait_allowance_combine_in_the_turn_timeout():
    """The turn covers the larger of declared/launcher-derived, plus the wait allowance."""
    compiled = C.compile_workflow(
        _step_and_agents(),
        launcher_timeout=lambda name: {"b": 7200.0}.get(name, 60.0),
    )

    assert compiled.turn_for("b", 0, 3600.0).timeout == 7500 + 3600
    assert compiled.turn_for("c", 0, 3600.0).timeout == 2100 + 3600
    assert compiled.after("a", 0, 3600.0).timeout == 7500 + 3600
    assert compiled.after("b", 0, 3600.0).timeout == 2100 + 3600
    assert compiled.turn_for("b", 0).timeout == 7500
    assert compiled.turn_for("a", 0, 3600.0).timeout == C.STEP_TIMEOUT


def test_without_a_launcher_timeout_the_declared_timeout_is_kept_as_is():
    # No hidden floor: a declared 60 s phase stays 60 s when no launcher is given.
    wf = Workflow("short", (AgentPhase("b", "explorer", (), None, timeout=timedelta(seconds=60)),))

    compiled = C.compile_workflow(wf)

    assert compiled.launcher_timeout is None
    assert compiled.turn_for("b", 0).timeout == 60


@pytest.mark.parametrize("value", RUN_VALUES)
def test_derived_turns_keep_g2_for_every_task_phase(value):
    compiled = C.compile_workflow(TASK, launcher_timeout=lambda _: value)
    derived = {
        p.name: compiled.turn_for(p.name, 0).timeout
        for p in TASK.phases
        if isinstance(p, AgentPhase)
    }

    assert all(timeout > value for timeout in derived.values()), derived
    # Built in the test only: the workflow of derived turn timeouts passes G2
    # at the run's value, which with a single value is also its largest.
    as_turns = dataclasses.replace(TASK, phases=tuple(
        dataclasses.replace(p, timeout=timedelta(seconds=derived[p.name]))
        if isinstance(p, AgentPhase) else p
        for p in TASK.phases
    ))
    as_turns.validate(launcher_timeout=timedelta(seconds=value))


def test_the_declared_timeouts_alone_do_not_carry_g2_for_a_raised_launcher():
    # Why the derivation, not the declaration, carries G2 for a run value
    # above the floor: TASK's declared timeouts fail it at 3600 s.
    with pytest.raises(WorkflowError):
        TASK.validate(launcher_timeout=timedelta(seconds=3600))


@pytest.mark.parametrize("value", RUN_VALUES)
def test_a_launcher_timeout_leaves_the_digest_and_tool_names_alone(value):
    before = TASK.digest()
    cached = C.compile_workflow(TASK)

    compiled = C.compile_workflow(TASK, launcher_timeout=lambda _: value)

    assert compiled is not cached
    assert compiled.workflow is TASK
    assert TASK.digest() == before
    assert compiled.agent_phase is cached.agent_phase
    assert compiled.step_phase is cached.step_phase
    assert compiled.agent_phase.__name__.endswith(before[:8])
    assert compiled.step_phase.__name__.endswith(before[:8])
    assert C.compile_workflow(TASK) is cached


def test_two_launcher_timeouts_never_leak_into_each_other():
    wf = _step_and_agents()

    a = C.compile_workflow(wf, launcher_timeout=lambda _: 3600.0)
    b = C.compile_workflow(wf, launcher_timeout=lambda _: 60.0)

    assert b.turn_for("b", 0).timeout == 2100
    assert a.turn_for("b", 0).timeout == 3900
    assert b.turn_for("b", 0).timeout == 2100
    assert a is not b
    plain = C.compile_workflow(wf)
    assert plain is not a and plain is not b
    assert plain.launcher_timeout is None
    assert plain.turn_for("b", 0).timeout == 2100


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


async def test_agent_phase_failure_escalates(store):
    def runner(phase, table, rendered):
        raise AgentPhaseFailed("review", outcome="gate_failed", detail="blocked by critic")

    wf = Workflow("t", (AgentPhase("review", "critic", (), None), Step("b", _noop)))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert info.value.phase == "review"
    assert info.value.detail == "blocked by critic"
    assert info.value.result is None


async def test_agent_phase_failure_s_result_reaches_escalated(store):
    # `comments.agent_reason` reads `unresolved_blockers` off exactly this
    # field, via `summary.results[failed_phase]` -- `_run` sets that from
    # `Escalated.result`, since a failed phase's own `ContextItem` is never
    # yielded.
    def runner(phase, table, rendered):
        raise AgentPhaseFailed(
            "review",
            outcome="gate_failed",
            detail="blocked by critic",
            result={"unresolved_blockers": ["the thing is broken"]},
        )

    wf = Workflow("t", (AgentPhase("review", "critic", (), None), Step("b", _noop)))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert info.value.result == {"unresolved_blockers": ["the thing is broken"]}


async def test_an_unexpected_runner_error_escalates_with_the_rendered_error(store):
    # Review Focus 5: the old engine escalates any runner exception as
    # "{Type}: {message}"; 3.4's parity run needs the same here.
    def runner(phase, table, rendered):
        raise EngineError("no worktree", phase="review")

    wf = Workflow("t", (AgentPhase("review", "critic", (), None),))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert info.value.phase == "review"
    assert info.value.detail == "EngineError: phase 'review': no worktree"


async def test_on_fail_loops_back_with_feedback_then_escalates_when_loops_run_out(store):
    # Mechanics only: the feedback prompt resolver and the full loop behaviour
    # belong to b904b9e7.
    calls: list[Any] = []

    def runner(phase, table, rendered):
        calls.append((phase.name, [f["detail"] for f in table["feedback"]]))
        if phase.name == "review":
            raise AgentPhaseFailed("review", outcome="gate_failed", detail=f"blocker {len(calls)}")
        return {"path": "docs/s.md"}

    wf = Workflow("t", (
        AgentPhase("spec", "writer", (), None),
        AgentPhase("review", "critic", (), None, on_fail=Goto("spec", 1)),
    ))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert calls == [
        ("spec", []),
        ("review", []),
        ("spec", ["blocker 2"]),
        ("review", []),
    ]
    assert info.value.phase == "review"
    assert info.value.detail == "blocker 4"


async def test_an_agent_phase_with_no_runner_injected_raises_the_old_engines_error(store):
    # A missing runner is a wiring bug, not a phase failure: the old engine
    # raises it to the caller before anything runs, and so must the tool.
    wf = Workflow("t", (AgentPhase("review", "critic", (), None),))

    with pytest.raises(EngineError) as info:
        await _drive(wf, _deps(wf, store, runner=None))

    assert str(info.value) == (
        "phase 'review': is an agent phase, but no agent runner was injected"
    )


# Goto revision loops end to end (pygents-engine design G4, §5; card b904b9e7).
# A critic (`validate_spec`) loops back to the phase it reviews (`spec`) at most
# once, and a successor (`plan`) only runs once the critic passes -- the shape
# the built-in task workflow will take.

FEEDBACK_ITEM = {"for": "spec", "from": "validate_spec", "detail": "no error path"}
LOOP_ORDER = ["spec", "validate_spec", "spec", "validate_spec", "plan"]


def _loop_workflow() -> Workflow:
    return Workflow("loops", (
        AgentPhase("spec", "spec_author", ("feedback",), None),
        AgentPhase("validate_spec", "critic", (), None, on_fail=Goto("spec", 1)),
        AgentPhase("plan", "planner", (), None),
    ))


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
    wf = _loop_workflow()

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
    wf = _loop_workflow()

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

    wf = _loop_workflow()
    compiled = C.compile_workflow(wf)
    agent = await _new_agent(compiled)
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


# G4 says each critic loops at most once: a loop spent by one critic must not
# use up the next critic's. The built-in task workflow has two critics in a row.

def _two_critics() -> Workflow:
    return Workflow("two-critics", (
        AgentPhase("spec", "spec_author", ("feedback",), None),
        AgentPhase("validate_spec", "critic", (), None, on_fail=Goto("spec", 1)),
        AgentPhase("plan", "planner", ("feedback",), None),
        AgentPhase("validate_plan", "critic", (), None, on_fail=Goto("plan", 1)),
        AgentPhase("review", "reviewer", (), None),
    ))


def _critics_fail(budget: dict[str, int]):
    """A fake runner whose critics fail as many times as `budget` says, then pass."""
    names: list[str] = []
    left = dict(budget)

    def runner(phase, ctx, rendered):
        names.append(phase.name)
        if left.get(phase.name, 0) > 0:
            left[phase.name] -= 1
            raise AgentPhaseFailed(phase.name, outcome="gate_failed", detail=f"{phase.name} blocks")
        return {"ok": True}

    return runner, names


async def test_each_critic_gets_its_own_loop(store):
    runner, names = _critics_fail({"validate_spec": 1, "validate_plan": 1})
    wf = _two_critics()

    await _drive(wf, _deps(wf, store, runner))

    assert names == [
        "spec", "validate_spec", "spec", "validate_spec",
        "plan", "validate_plan", "plan", "validate_plan", "review",
    ]


async def test_a_later_critic_still_escalates_on_its_second_failure(store):
    runner, names = _critics_fail({"validate_spec": 1, "validate_plan": 2})
    wf = _two_critics()

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert info.value.phase == "validate_plan"
    assert info.value.detail == "validate_plan blocks"
    assert names == [
        "spec", "validate_spec", "spec", "validate_spec",
        "plan", "validate_plan", "plan", "validate_plan",
    ]


async def test_a_critic_looping_back_past_an_earlier_critic_shares_its_loop(store):
    # Termination: were the counter reset when `validate_spec` passes, a
    # `validate_plan` that loops back to `spec` would re-earn its loop on every
    # pass and never escalate. Such a critic keeps the one shared counter.
    runner, names = _critics_fail({"validate_plan": 5})
    wf = Workflow("overlapping", (
        AgentPhase("spec", "spec_author", ("feedback",), None),
        AgentPhase("validate_spec", "critic", (), None, on_fail=Goto("spec", 1)),
        AgentPhase("plan", "planner", (), None),
        AgentPhase("validate_plan", "critic", (), None, on_fail=Goto("spec", 1)),
    ))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert info.value.phase == "validate_plan"
    assert names == [
        "spec", "validate_spec", "plan", "validate_plan",
        "spec", "validate_spec", "plan", "validate_plan",
    ]


# D3 (T7): every turn a tool yields -- `after()`, a critic's `Goto`, a step's
# `skip_to` -- comes from the run's own compilation, `deps.compiled`.


def _timeout_recorder(fail: dict[str, int] | None = None):
    """A fake runner that records each agent phase with its running turn's
    timeout, read off the agent (`agents[0]`) the test drives; phases named in
    `fail` fail that many times first."""
    seen: list[tuple[str, float]] = []
    agents: list[Agent] = []
    left = dict(fail or {})

    def runner(phase, table, rendered):
        seen.append((phase.name, agents[0].to_dict()["current_turn"]["timeout"]))
        if left.get(phase.name, 0) > 0:
            left[phase.name] -= 1
            raise AgentPhaseFailed(phase.name, outcome="gate_failed", detail=f"{phase.name} blocks")
        return {"ok": True}

    return runner, seen, agents


async def _drive_compiled(compiled, deps, agents: list[Agent]) -> Agent:
    agent = await _new_agent(compiled)
    agents.append(agent)
    token = state.current_run.set(deps)
    try:
        await _consume(agent)
    finally:
        state.current_run.reset(token)
    return agent


def _step_then_critic_loop() -> Workflow:
    return Workflow("derived-loop", (
        Step("a", _noop),
        AgentPhase("spec", "spec_author", ("feedback",), None, timeout=DECLARED),
        AgentPhase("validate_spec", "critic", (), None, timeout=DECLARED, on_fail=Goto("spec", 1)),
    ))


async def test_after_and_goto_turns_carry_the_derived_timeout(store):
    runner, seen, agents = _timeout_recorder({"validate_spec": 1})
    wf = _step_then_critic_loop()
    compiled = C.compile_workflow(wf, launcher_timeout=lambda _: 3600.0)

    await _drive_compiled(compiled, _deps(wf, store, runner, compiled=compiled), agents)

    # spec via after() from the step, validate_spec via after(), spec again via
    # the critic's Goto, validate_spec via after().
    assert seen == [
        ("spec", 3900), ("validate_spec", 3900), ("spec", 3900), ("validate_spec", 3900),
    ]


async def test_a_skip_to_turn_carries_the_derived_timeout(store):
    runner, seen, agents = _timeout_recorder()
    wf = Workflow("derived-skip", (
        Step("a", _noop, when=lambda result: True, skip_to="c"),
        Step("b", _noop),
        AgentPhase("c", "explorer", (), None, timeout=DECLARED),
    ))
    compiled = C.compile_workflow(wf, launcher_timeout=lambda _: 3600.0)
    deps = _deps(wf, store, runner, compiled=compiled)

    await _drive_compiled(compiled, deps, agents)

    assert deps.skipped == ["b"]
    assert seen == [("c", 3900)]


async def test_tools_without_a_run_compilation_keep_the_declared_timeout(store):
    # A hand-built `RunDeps` (no `compiled`) falls back to the shared,
    # launcher-less compilation, even when the first turn came from a derived one.
    runner, seen, agents = _timeout_recorder({"validate_spec": 1})
    wf = _step_then_critic_loop()
    compiled = C.compile_workflow(wf, launcher_timeout=lambda _: 3600.0)

    await _drive_compiled(compiled, _deps(wf, store, runner), agents)

    assert seen == [
        ("spec", 2100), ("validate_spec", 2100), ("spec", 2100), ("validate_spec", 2100),
    ]
