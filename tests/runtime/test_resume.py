"""Resuming a subtask from its checkpoint (pygents-engine design §6, §8; card 5698e4f6).

Engine tier: `runtime.engine.run_subtask` drives fake steps and a fake agent
runner that know only the arguments they are bound, over a real temp SQLite
projection plus a real temp JSONL journal, built as
tests/runtime/test_checkpoint.py builds it. A crash is `_Crash`, a plain
`BaseException` (not `KeyboardInterrupt`, which asyncio re-raises out of the
event loop from the producer task before the engine unwinds); like
tests/test_engine.py's `_Abort`, neither `_run` nor pygents may catch it.
Registry isolation between tests is the autouse `fresh_pygents` fixture.
"""

import asyncio
import dataclasses
import json
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, AgentRegistry, ToolRegistry

from agent_manager import models, store as store_module
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime import compile as compile_mod
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.workflow.phases import AgentPhase, Goto, Step, Workflow

RUN_ID = "run-2026-09-26-04"
STORY_ID = "a6c7bff3"
CARD_ID = "5698e4f6"
REPO = Path("/repo")
FIXED = datetime(2026, 9, 26, tzinfo=timezone.utc)
FIVE = ("a", "b", "c", "d", "e")
ALL_RESULTS = {name: {name: name.upper()} for name in FIVE}


class _Crash(BaseException):
    """A process death mid-phase: not an `Exception`, so nothing may catch it."""


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
        branch=f"m6/task-resume-a-subtask-from-{CARD_ID}",
        base_branch="m6/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _go(workflow: Workflow, opened, **kwargs: Any):
    return runtime_engine.run_subtask(
        workflow,
        opened,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        **kwargs,
    )


def _five(
    ran: list[str], crash_in: set[str], after_a: Callable[[], None] | None = None
) -> Workflow:
    """Steps a..e. Each appends its name to `ran`; a step named in `crash_in`
    raises `_Crash` once (the name is discarded), so a resume runs it cleanly.
    `after_a`, when given, is called by step `a` just before it returns."""

    def make(name: str):
        def run(card: str) -> dict[str, Any]:
            ran.append(name)
            if name in crash_in:
                crash_in.discard(name)
                raise _Crash(f"killed in {name}")
            if name == "a" and after_a is not None:
                after_a()
            return {name: name.upper()}

        return run

    return Workflow("five", tuple(Step(name, make(name)) for name in FIVE))


class _StopAfterA:
    """A `StopSignal` that step `a` triggers, once.

    Steps run in `asyncio.to_thread` workers and the signal lives on the loop,
    so the trigger is handed to the loop with `call_soon_threadsafe` and the
    step blocks on a `threading.Event` until it has run -- no sleeps. Only the
    first call fires: a later fresh run of the same workflow, on a new loop,
    passes straight through.
    """

    def __init__(self) -> None:
        self.signal = StopSignal()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._fired = threading.Event()

    def __call__(self) -> None:
        if self._fired.is_set():
            return
        assert self.loop is not None, "armed by _park_after_a before the run"

        def trigger() -> None:
            self.signal.trigger(STORY_ID)
            self._fired.set()

        self.loop.call_soon_threadsafe(trigger)
        if not self._fired.wait(5):
            raise RuntimeError("the stop was never triggered")


def _park_after_a(opened) -> tuple[list[str], Workflow, Any]:
    """Run `_five` under a `StopSignal` that step `a` triggers, so the subtask
    parks before `b`. Returns what ran, the workflow (for a resume) and the
    summary."""
    ran: list[str] = []
    stop = _StopAfterA()
    wf = _five(ran, set(), after_a=stop)

    async def go():
        stop.loop = asyncio.get_running_loop()
        return await runtime_engine.run_subtask_async(
            wf,
            opened,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            clock=lambda: FIXED,
            stop=stop.signal,
        )

    return ran, wf, asyncio.run(go())


def _rows(opened) -> list[tuple[int, str, dict]]:
    """Every checkpoint row in the store, oldest first, agent decoded."""
    return [
        (row[0], row[1], json.loads(row[2]))
        for row in opened.connection.execute(
            "SELECT seq, reason, agent FROM checkpoints ORDER BY card_id, seq"
        ).fetchall()
    ]


def _reasons(opened) -> list[tuple[int, str]]:
    return [(seq, reason) for seq, reason, _ in _rows(opened)]


def _next_turn(agent: dict) -> dict:
    """The turn a stored agent would run next: its current turn, else its queue head."""
    return agent["current_turn"] or agent["queue"][0]


def _head(agent: dict) -> str:
    return _next_turn(agent)["kwargs"]["phase"]


def _phase_rows(opened) -> list[tuple[str | None, str]]:
    return [
        (line.phase, line.payload["status"])
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ]


def test_a_crash_resumes_at_the_phase_it_died_in(store):
    ran: list[str] = []
    wf = _five(ran, {"c"})

    with pytest.raises(_Crash):
        _go(wf, store)

    assert ran == ["a", "b", "c"]
    crashed = store.latest_checkpoint(CARD_ID)
    assert crashed.reason == "turn"
    assert _head(crashed.agent) == "c"

    ran.clear()
    summary = _go(wf, store, resume_from=crashed)

    assert ran == ["c", "d", "e"]
    assert summary.status == "done"
    assert summary.failed_phase is None
    assert summary.results == ALL_RESULTS
    assert _reasons(store) == [
        (0, "turn"), (1, "turn"), (2, "turn"),
        (3, "turn"), (4, "turn"), (5, "turn"), (6, "done"),
    ]
    assert [_head(agent) for _, _, agent in _rows(store)[3:6]] == ["c", "d", "e"]
    assert _phase_rows(store) == [
        ("a", "started"), ("a", "done"),
        ("b", "started"), ("b", "done"),
        ("c", "started"),
        ("c", "started"), ("c", "done"),
        ("d", "started"), ("d", "done"),
        ("e", "started"), ("e", "done"),
    ]


def _loop_workflow() -> Workflow:
    return Workflow("loops", (
        AgentPhase("spec", "spec_author", (), None),
        AgentPhase("validate_spec", "critic", (), None, on_fail=Goto("spec", 1)),
        AgentPhase("plan", "planner", (), None),
    ))


def test_loop_count_survives_a_resume(store):
    names: list[str] = []

    def crashing(phase, table, rendered):
        names.append(phase.name)
        if phase.name == "validate_spec":
            raise AgentPhaseFailed("validate_spec", outcome="gate_failed", detail="no error path")
        if phase.name == "spec" and names.count("spec") == 2:
            raise _Crash("killed on the second pass of spec")
        return {"ok": True}

    wf = _loop_workflow()

    with pytest.raises(_Crash):
        _go(wf, store, agent_runner=crashing)

    assert names == ["spec", "validate_spec", "spec"]
    crashed = store.latest_checkpoint(CARD_ID)
    assert crashed.reason == "turn"
    assert _next_turn(crashed.agent)["kwargs"] == {"phase": "spec", "loop": 1}

    resumed: list[str] = []

    def critic_fails_again(phase, table, rendered):
        resumed.append(phase.name)
        if phase.name == "validate_spec":
            raise AgentPhaseFailed(
                "validate_spec", outcome="gate_failed", detail="still no error path"
            )
        return {"ok": True}

    summary = _go(wf, store, agent_runner=critic_fails_again, resume_from=crashed)

    first_resumed = _rows(store)[crashed.seq + 1]
    assert first_resumed[1] == "turn"
    assert _next_turn(first_resumed[2])["kwargs"] == {"phase": "spec", "loop": 1}
    # loop=1 already spent the one Goto: a second critic failure escalates
    # instead of looping back again, which it would do had loop reset to 0.
    assert resumed == ["spec", "validate_spec"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "validate_spec"
    assert summary.detail == "still no error path"
    assert store.latest_checkpoint(CARD_ID).reason == "escalated"


def test_a_parked_subtask_resumes(store):
    ran, wf, parked_summary = _park_after_a(store)

    assert parked_summary.status == "stopped"
    assert parked_summary.detail == "stopped before b"
    parked = store.latest_checkpoint(CARD_ID)
    assert parked.reason == "parked"
    assert _head(parked.agent) == "b"

    ran.clear()
    summary = _go(wf, store, resume_from=parked)

    assert ran == ["b", "c", "d", "e"]
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS
    assert store.latest_checkpoint(CARD_ID).reason == "done"


def test_resuming_twice_in_one_process(store):
    ran: list[str] = []
    wf = _five(ran, {"b", "d"})

    with pytest.raises(_Crash):
        _go(wf, store)
    first = store.latest_checkpoint(CARD_ID)
    assert _head(first.agent) == "b"

    with pytest.raises(_Crash):
        _go(wf, store, resume_from=first)
    second = store.latest_checkpoint(CARD_ID)
    assert second.reason == "turn"
    assert _head(second.agent) == "d"

    summary = _go(wf, store, resume_from=second)

    assert ran == ["a", "b", "b", "c", "d", "d", "e"]
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS


def _new_process() -> None:
    """Simulate a restart: a new process starts with every pygents registry and
    the compile cache empty. Not test isolation (the autouse fixture does
    that); this is the condition under test."""
    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()


def test_a_resume_in_a_fresh_process_uses_the_rebuilt_workflow(store):
    # Review Focus 1: compile must run before `from_dict`, and the rebuilt
    # workflow's own callables must be the ones called.
    before: list[str] = []
    with pytest.raises(_Crash):
        _go(_five(before, {"c"}), store)
    crashed = store.latest_checkpoint(CARD_ID)

    _new_process()
    after: list[str] = []
    rebuilt = _five(after, set())
    assert rebuilt.digest() == crashed.digest

    summary = _go(rebuilt, store, resume_from=crashed)

    assert before == ["a", "b", "c"]
    assert after == ["c", "d", "e"]
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS


def test_resuming_a_done_checkpoint_dispatches_nothing(store):
    # Review Focus 4.
    ran: list[str] = []
    wf = _five(ran, set())
    assert _go(wf, store).status == "done"
    done = store.latest_checkpoint(CARD_ID)
    assert done.reason == "done"

    ran.clear()
    summary = _go(wf, store, resume_from=done)

    assert ran == []
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS
    assert store.latest_checkpoint(CARD_ID).reason == "done"


def test_a_resume_with_the_stop_still_set_parks_again(store):
    # Review Focus 5.
    ran, wf, _ = _park_after_a(store)
    parked = store.latest_checkpoint(CARD_ID)
    still = StopSignal()
    still.trigger("elsewhere")

    summary = _go(wf, store, resume_from=parked, stop=still)

    assert ran == ["a"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before b"
    assert summary.results == {"a": {"a": "A"}}
    newest = store.latest_checkpoint(CARD_ID)
    assert (newest.seq, newest.reason) == (parked.seq + 1, "parked")
    assert _head(newest.agent) == "b"


def _extra(card: str) -> dict[str, Any]:
    return {"f": "F"}


def test_a_changed_workflow_is_refused(store):
    ran, wf, _ = _park_after_a(store)
    parked = store.latest_checkpoint(CARD_ID)
    changed = Workflow("five", wf.phases + (Step("f", _extra),))
    assert changed.digest() != wf.digest()
    rows_before = _reasons(store)
    journal_before = len(store.journal.read())

    with pytest.raises(runtime_engine.CheckpointMismatch) as caught:
        _go(changed, store, resume_from=parked)

    assert isinstance(caught.value, Exception)
    assert parked.digest in str(caught.value)
    assert changed.digest() in str(caught.value)
    assert ran == ["a"]
    assert _reasons(store) == rows_before
    assert len(store.journal.read()) == journal_before


def test_a_refused_resume_leaves_the_card_runnable(store):
    # Review Focus 3: the refusal comes before any agent is registered, so a
    # fresh run of the same card in the same process is not refused a name.
    ran, wf, _ = _park_after_a(store)
    parked = store.latest_checkpoint(CARD_ID)
    changed = Workflow("five", wf.phases + (Step("f", _extra),))

    with pytest.raises(runtime_engine.CheckpointMismatch):
        _go(changed, store, resume_from=parked)

    assert parked.agent["name"] not in AgentRegistry._registry
    ran.clear()
    summary = _go(changed, store)

    assert ran == ["a", "b", "c", "d", "e"]
    assert summary.status == "done"
    assert summary.results == {**ALL_RESULTS, "f": {"f": "F"}}


def test_a_stale_registry_entry_does_not_block_a_resume(store):
    # Review Focus 2: a process that died before its `finally` left the agent
    # registered under the checkpointed name.
    ran: list[str] = []
    wf = _five(ran, {"c"})
    with pytest.raises(_Crash):
        _go(wf, store)
    crashed = store.latest_checkpoint(CARD_ID)
    Agent(crashed.agent["name"], "left behind by a dead run", [])

    ran.clear()
    summary = _go(wf, store, resume_from=crashed)

    assert ran == ["c", "d", "e"]
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS
    assert crashed.agent["name"] not in AgentRegistry._registry


# ── pending_phase (card 02890d5d) ────────────────────────────────────────────


def test_pending_phase_reads_the_turn_a_crashed_checkpoint_would_run_next(store):
    ran: list[str] = []
    with pytest.raises(_Crash):
        _go(_five(ran, {"c"}), store)

    assert runtime_engine.pending_phase(store.latest_checkpoint(CARD_ID)) == "c"


def test_pending_phase_reads_a_parked_checkpoint(store):
    _park_after_a(store)
    parked = store.latest_checkpoint(CARD_ID)

    assert parked.reason == "parked"
    assert runtime_engine.pending_phase(parked) == "b"


def test_pending_phase_prefers_the_turn_in_flight_over_the_queue():
    checkpoint = store_module.Checkpoint(
        run_id=RUN_ID,
        card_id=CARD_ID,
        seq=0,
        workflow="five",
        digest="any",
        reason="turn",
        agent={
            "current_turn": {"kwargs": {"phase": "c", "loop": 1}},
            "queue": [{"kwargs": {"phase": "d", "loop": 0}}],
        },
        saved_at=FIXED,
    )

    assert runtime_engine.pending_phase(checkpoint) == "c"


def test_a_done_checkpoint_has_no_pending_phase(store):
    _go(_five([], set()), store)
    done = store.latest_checkpoint(CARD_ID)

    assert done.reason == "done"
    assert runtime_engine.pending_phase(done) is None


def _boom(card: str) -> dict[str, Any]:
    raise RuntimeError("boom")


def test_a_phase_escalation_leaves_an_escalated_row_with_no_pending_phase(store):
    """Why card 02890d5d refuses to resume such a row and relaunches it fresh:
    the failed turn was consumed, `Escalated` enqueued nothing, and
    `agent.run()` cleared `current_turn` on its way out, so the row holds no
    turn. Continuing from it would run nothing and record the subtask `done`."""
    summary = _go(Workflow("escalates", (Step("a", _boom), Step("b", _extra))), store)

    assert summary.status == "escalated"
    escalated = store.latest_checkpoint(CARD_ID)
    assert escalated.reason == "escalated"
    assert escalated.agent["current_turn"] is None
    assert escalated.agent["queue"] == []
    assert runtime_engine.pending_phase(escalated) is None


# ── a StopSignal-parked checkpoint (card 364babde) ───────────────────────────


def _park_with_a_triggered_stop(wf: Workflow, opened):
    """Park `wf`'s subtask through the StopSignal path: a signal already
    triggered pauses the agent before its first turn, and ON_PAUSE parks it."""
    stop = StopSignal()
    stop.trigger("elsewhere")
    summary = _go(wf, opened, stop=stop)
    assert summary.status == "stopped"
    assert summary.detail == "stopped before a"
    parked = opened.latest_checkpoint(CARD_ID)
    assert parked.reason == "parked"
    # pygents stores the pause itself in the row.
    assert parked.agent["is_paused"] is True
    return parked


def test_a_subtask_parked_by_the_stop_signal_resumes(store):
    # Review Focus 1: the stored pause must not re-park the resumed agent.
    ran: list[str] = []
    wf = _five(ran, set())
    parked = _park_with_a_triggered_stop(wf, store)
    assert ran == []

    summary = _go(wf, store, resume_from=parked)

    assert ran == list(FIVE)
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS
    assert store.latest_checkpoint(CARD_ID).reason == "done"


def test_a_stop_signal_parked_subtask_resumed_under_a_triggered_stop_parks_again(store):
    # Review Focus 2: clearing the stored pause must come before the run's
    # own stop registers the agent, or this resume would run.
    ran: list[str] = []
    wf = _five(ran, set())
    parked = _park_with_a_triggered_stop(wf, store)
    again = StopSignal()
    again.trigger("elsewhere")

    summary = _go(wf, store, resume_from=parked, stop=again)

    assert ran == []
    assert summary.status == "stopped"
    assert summary.detail == "stopped before a"
    newest = store.latest_checkpoint(CARD_ID)
    assert (newest.seq, newest.reason) == (parked.seq + 1, "parked")
    assert _head(newest.agent) == "a"


def test_a_row_parked_without_a_pause_still_resumes(store):
    """Rows `parked` by the deleted M6 stop were saved from an agent that was
    never paused. One still sitting in an older store resumes like any other."""
    ran: list[str] = []
    wf = _five(ran, set())
    parked = _park_with_a_triggered_stop(wf, store)
    legacy = dataclasses.replace(parked, agent={**parked.agent, "is_paused": False})

    summary = _go(wf, store, resume_from=legacy)

    assert ran == list(FIVE)
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS
