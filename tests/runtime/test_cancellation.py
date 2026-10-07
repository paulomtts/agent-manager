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

from agent_manager import models
from agent_manager.store import writer as store_writer
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
    opened = store_writer.Store.open(tmp_path / "repo", RUN_ID)
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


WORKTREE: Path | None = None
"""The subtask's worktree path. The autouse `worktree_dir` fixture sets it to a
real `tmp_path` directory, so a resume takes the engine's no-git fast path."""


@pytest.fixture(autouse=True)
def worktree_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "worktree"
    path.mkdir()
    monkeypatch.setitem(globals(), "WORKTREE", path)
    return path


def _subtask(card_id: str = CARD_ID) -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card_id,
        branch=f"m8/task-pin-pygents-{card_id}",
        base_branch="m8/story-base",
        status="started",
        worktree_path=WORKTREE,
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


# ── cancel mid-step-phase (A3) ───────────────────────────────────────────────


class _Gate:
    """Holds step `slow` until `opened`. `entered` and `left` bracket its worker."""

    def __init__(self, *, opened: bool = False) -> None:
        self.entered = threading.Event()
        self.opened = threading.Event()
        self.left = threading.Event()
        if opened:
            self.opened.set()


def _gated(ran: list[str], gate: _Gate) -> Workflow:
    """Steps `a`, `slow`, `b`. Each appends its name to `ran`; `slow` waits on
    `gate`. Every call builds closures with the same qualified names, so every
    `_gated` workflow has the same digest and resumes another's checkpoint."""

    def make(name: str):
        def run(card: str) -> dict[str, Any]:
            ran.append(name)
            if name == "slow":
                gate.entered.set()
                try:
                    if not gate.opened.wait(10):
                        raise RuntimeError("the gate was never opened")
                finally:
                    gate.left.set()
            return {name: name.upper()}

        return run

    return Workflow("gated", tuple(Step(n, make(n)) for n in ("a", "slow", "b")))


async def _cancel_in_slow(wf: Workflow, gate: _Gate, opened, **kwargs: Any) -> Agent:
    """Run `wf` as a task, cancel it while `slow` is in flight, and let the
    abandoned worker finish. Returns the cancelled run's agent."""
    task = asyncio.create_task(_run(wf, opened, **kwargs))
    assert await asyncio.to_thread(gate.entered.wait, 5)
    agent = AgentRegistry.get(AGENT_NAME)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # A `to_thread` worker cannot be cancelled, only abandoned: release it and
    # wait it out, so whatever it does after the cancel has been done.
    gate.opened.set()
    assert await asyncio.to_thread(gate.left.wait, 5)
    return agent


async def test_cancelling_mid_step_phase_leaves_a_resumable_turn(store, completions):
    ran: list[str] = []
    gate = _Gate()

    agent = await _cancel_in_slow(_gated(ran, gate), gate, store)

    assert completions == [("a", StopReason.COMPLETED), ("slow", StopReason.CANCELLED)]
    _assert_stopped(agent)
    _assert_free(AGENT_NAME)
    # Review Focus 1: the abandoned worker has finished, and still no row
    # follows the `turn` row saved before `slow`.
    assert _reasons(store) == [(0, "turn"), (1, "turn")]
    latest = store.latest_checkpoint(CARD_ID)
    assert latest.reason == "turn"
    assert runtime_engine.pending_phase(latest) == "slow"

    ran.clear()
    rebuilt = _gated(ran, _Gate(opened=True))
    assert rebuilt.digest() == latest.digest
    summary = await _run(rebuilt, store, resume_from=latest)

    assert ran == ["slow", "b"]
    assert summary.status == "done"
    assert summary.results == {"a": {"a": "A"}, "slow": {"slow": "SLOW"}, "b": {"b": "B"}}
    assert _reasons(store)[2:] == [(2, "turn"), (3, "turn"), (4, "done")]


async def test_a_resumed_run_cancelled_again_resumes_from_its_own_turn(store, completions):
    # Review Focus 2.
    ran: list[str] = []
    first_gate = _Gate()
    await _cancel_in_slow(_gated(ran, first_gate), first_gate, store)
    first = store.latest_checkpoint(CARD_ID)

    second_gate = _Gate()
    await _cancel_in_slow(_gated(ran, second_gate), second_gate, store, resume_from=first)

    _assert_free(AGENT_NAME)
    assert completions == [
        ("a", StopReason.COMPLETED),
        ("slow", StopReason.CANCELLED),
        ("slow", StopReason.CANCELLED),
    ]
    assert _reasons(store) == [(0, "turn"), (1, "turn"), (2, "turn")]
    second = store.latest_checkpoint(CARD_ID)
    assert runtime_engine.pending_phase(second) == "slow"

    ran.clear()
    summary = await _run(_gated(ran, _Gate(opened=True)), store, resume_from=second)

    assert ran == ["slow", "b"]
    assert summary.status == "done"
    assert _reasons(store)[3:] == [(3, "turn"), (4, "turn"), (5, "done")]


# ── cancel with a StopSignal registered, and beside another subtask (A3) ─────


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


async def test_a_cancel_unregisters_the_agent_from_the_stop_signal(store, tmp_path):
    spy = _Spy()
    claude = _Launcher(tmp_path)
    task = asyncio.create_task(
        _run(_with_agent_phase([]), store, agent_runner=claude, stop=spy)
    )
    assert await asyncio.to_thread(claude.spawned.wait, 5)
    agent = AgentRegistry.get(AGENT_NAME)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(claude.left.wait, 10)

    assert spy.calls == [("register", AGENT_NAME), ("unregister", AGENT_NAME)]
    # Review Focus 4: a trigger after the cancel reaches no dead agent.
    assert spy.trigger("late") is True
    assert agent.is_paused is False
    assert _reasons(store) == [(0, "turn"), (1, "turn")]
    _assert_free(AGENT_NAME)


async def test_cancelling_one_subtask_leaves_a_concurrent_one_running(
    store, tmp_path, completions
):
    # Review Focus 3.
    claude = _Launcher(tmp_path)
    cancelled = asyncio.create_task(_run(_with_agent_phase([]), store, agent_runner=claude))
    gate = _Gate()
    other_ran: list[str] = []
    other = asyncio.create_task(_run(_gated(other_ran, gate), store, card_id=OTHER_CARD))
    assert await asyncio.to_thread(claude.spawned.wait, 5)
    assert await asyncio.to_thread(gate.entered.wait, 5)

    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    # The other subtask is still in `slow`, and its agent is still registered.
    assert not other.done()
    AgentRegistry.get(f"{RUN_ID}:{OTHER_CARD}")
    gate.opened.set()
    summary = await other

    assert await asyncio.to_thread(claude.left.wait, 10)
    assert summary.status == "done"
    assert other_ran == ["a", "slow", "b"]
    assert ("spec", StopReason.CANCELLED) in completions
    assert ("slow", StopReason.COMPLETED) in completions
    assert _reasons(store) == [(0, "turn"), (1, "turn")]
    assert _reasons(store, OTHER_CARD) == [(0, "turn"), (1, "turn"), (2, "turn"), (3, "done")]
    _assert_free(AGENT_NAME)
    _assert_free(f"{RUN_ID}:{OTHER_CARD}")


# ── no instance hooks on engine-built agents (A5) ────────────────────────────


class _Crash(BaseException):
    """A process death mid-phase: not an `Exception`, so nothing may catch it."""


def _capturing(seen: list[Agent], crash_in: set[str]) -> Workflow:
    """Steps `a` and `b`. Each appends the agent running it to `seen`, looked
    up by name while the run holds it. A step named in `crash_in` raises
    `_Crash` once (the name is discarded), so a resume runs it cleanly."""

    def make(name: str):
        def run(card: str) -> dict[str, Any]:
            seen.append(AgentRegistry.get(AGENT_NAME))
            if name in crash_in:
                crash_in.discard(name)
                raise _Crash(f"killed in {name}")
            return {name: name.upper()}

        return run

    return Workflow("captures", (Step("a", make("a")), Step("b", make("b"))))


def _assert_no_instance_hooks(agent: Agent) -> None:
    assert agent.hooks == [], (
        f"instance hooks attached: {[h.__name__ for h in agent.hooks]}"
    )
    assert agent.turn_hooks == [], (
        f"instance turn hooks attached: {[h.__name__ for h in agent.turn_hooks]}"
    )
    # Raises `UnserializableHookError` if a closure hook slipped in.
    data = agent.to_dict()
    assert data["hooks"] == {}
    assert data["turn_hooks"] == {}


def test_a_fresh_engine_built_agent_has_no_instance_hooks(store):
    seen: list[Agent] = []

    summary = _go(_capturing(seen, set()), store)

    assert summary.status == "done"
    assert len(seen) == 2
    assert seen[0] is seen[1]
    _assert_no_instance_hooks(seen[0])
    stored = _stored_agents(store)
    assert [(a["hooks"], a["turn_hooks"]) for a in stored] == [({}, {})] * len(stored)


def test_an_agent_rebuilt_on_resume_has_no_instance_hooks(store):
    crashed_seen: list[Agent] = []
    with pytest.raises(_Crash):
        _go(_capturing(crashed_seen, {"b"}), store)
    crashed = store.latest_checkpoint(CARD_ID)
    assert crashed.reason == "turn"
    assert runtime_engine.pending_phase(crashed) == "b"

    seen: list[Agent] = []
    summary = _go(_capturing(seen, set()), store, resume_from=crashed)

    assert summary.status == "done"
    assert len(seen) == 1
    assert seen[0] is not crashed_seen[0]
    _assert_no_instance_hooks(seen[0])
    stored = _stored_agents(store)
    assert [(a["hooks"], a["turn_hooks"]) for a in stored] == [({}, {})] * len(stored)
