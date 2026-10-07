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
from pygents.errors import UnregisteredAgentError

from agent_manager import models, paths
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime import compile as compile_mod
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.runtime.state import Adoption, RunDeps, current_run
from agent_manager.steps.worktree import GitError
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.store import writer as store_writer
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
    opened = store_writer.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


WORKTREE: Path | None = None
"""The subtask's worktree path. The autouse `worktree_dir` fixture sets it to a
real `tmp_path` directory for every test, so a resume takes the engine's
no-git fast path; a test may `rmdir()` that directory, or set this to `None`."""


@pytest.fixture(autouse=True)
def worktree_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "worktree"
    path.mkdir()
    monkeypatch.setitem(globals(), "WORKTREE", path)
    return path


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=CARD_ID,
        branch=f"m6/task-resume-a-subtask-from-{CARD_ID}",
        base_branch="m6/story-base",
        status="started",
        worktree_path=WORKTREE,
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

    with pytest.raises(UnregisteredAgentError):
        AgentRegistry.get(parked.agent["name"])
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
    with pytest.raises(UnregisteredAgentError):
        AgentRegistry.get(crashed.agent["name"])


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
    checkpoint = store_checkpoints.Checkpoint(
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


def _checkpoint_with(agent: dict) -> store_checkpoints.Checkpoint:
    return store_checkpoints.Checkpoint(
        run_id=RUN_ID,
        card_id=CARD_ID,
        seq=0,
        workflow="task",
        digest="any",
        reason="turn",
        agent=agent,
        saved_at=FIXED,
    )


def _pool(*items: dict) -> dict:
    return {"context_pool": {"limit": None, "items": list(items), "hooks": {}, "tags": []}}


def test_kept_commands_reads_the_seeds_commands_in_stored_order():
    """Spec T1: the `"subtask"` seed item's `commands`, among other items."""
    checkpoint = _checkpoint_with(
        _pool(
            {"id": "explore", "description": "result", "content": {"commands": ["no"]}},
            {
                "id": "subtask",
                "description": "fixed subtask context",
                "content": {"card": CARD_ID, "commands": ["uv run pytest", "uv run ruff check"]},
            },
            {"id": "spec", "description": "result", "content": {"spec": "SPEC"}},
        )
    )

    assert runtime_engine.kept_commands(checkpoint) == ["uv run pytest", "uv run ruff check"]


@pytest.mark.parametrize(
    "agent",
    [
        pytest.param({}, id="no-context-pool"),
        pytest.param({"context_pool": None}, id="null-context-pool"),
        pytest.param({"context_pool": {}}, id="no-items"),
        pytest.param(
            _pool({"id": "spec", "description": "result", "content": {"commands": ["x"]}}),
            id="no-subtask-item",
        ),
        pytest.param(
            _pool({"id": "subtask", "description": "seed", "content": {"card": CARD_ID}}),
            id="no-commands-key",
        ),
        pytest.param(
            _pool({"id": "subtask", "description": "seed", "content": {"commands": "true"}}),
            id="commands-not-a-list",
        ),
        pytest.param(
            _pool({"id": "subtask", "description": "seed", "content": None}),
            id="content-not-a-dict",
        ),
    ],
)
def test_kept_commands_is_none_when_the_seed_does_not_say(agent):
    """Spec T2: unknown, never an exception -- a report must not break a resume."""
    assert runtime_engine.kept_commands(_checkpoint_with(agent)) is None


def test_kept_commands_is_empty_for_an_opted_out_seed():
    """Spec T3: a run started with `--allow-no-verification` kept `[]`, not "unknown"."""
    checkpoint = _checkpoint_with(
        _pool({"id": "subtask", "description": "seed", "content": {"commands": []}})
    )

    assert runtime_engine.kept_commands(checkpoint) == []


def test_kept_commands_reads_a_real_checkpoints_seed(store):
    """The shape above is the one the engine really saves."""
    ran: list[str] = []
    with pytest.raises(_Crash):
        _go(_five(ran, {"c"}), store, commands=["uv run pytest"])

    assert runtime_engine.kept_commands(store.latest_checkpoint(CARD_ID)) == ["uv run pytest"]


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


# ── the floor across a resume (exactly-once 1.2, card 94088f7e) ──────────────


def _crash_on_the_second_spec(names: list[str]):
    """`_loop_workflow`'s runner: the critic fails once, and the second pass of
    `spec` (loop 1) dies with `_Crash`."""

    def runner(phase, table, rendered):
        names.append(phase.name)
        if phase.name == "validate_spec":
            raise AgentPhaseFailed("validate_spec", outcome="gate_failed", detail="no error path")
        if phase.name == "spec" and names.count("spec") == 2:
            raise _Crash("killed on the second pass of spec")
        return {"ok": True}

    return runner


def _critic_fails_recording(seen: list[tuple[str, Adoption | None]]):
    """A resumed run's runner: records each phase with the run's `adopt`, and the
    critic fails again, which escalates (loop 1 already spent the Goto)."""

    def runner(phase, table, rendered):
        seen.append((phase.name, current_run.get().adopt))
        if phase.name == "validate_spec":
            raise AgentPhaseFailed(
                "validate_spec", outcome="gate_failed", detail="still no error path"
            )
        return {"ok": True}

    return runner


def _floors(opened) -> list[tuple[str, store_checkpoints.TurnFloor | None]]:
    """Every checkpoint row's reason and floor, oldest first."""
    rows = opened.connection.execute(
        "SELECT c.reason, f.phase, f.loop, f.source_run, f.floor"
        " FROM checkpoints c LEFT JOIN checkpoint_floors f"
        " ON f.run_id = c.run_id AND f.card_id = c.card_id AND f.seq = c.seq"
        " ORDER BY c.card_id, c.seq"
    ).fetchall()
    return [
        (row[0], None if row[1] is None else store_checkpoints.TurnFloor(row[1], row[2], row[3], row[4]))
        for row in rows
    ]


def _crash_in_the_loop(opened):
    names: list[str] = []
    wf = _loop_workflow()
    with pytest.raises(_Crash):
        _go(wf, opened, agent_runner=_crash_on_the_second_spec(names))
    assert names == ["spec", "validate_spec", "spec"]
    return wf, opened.latest_checkpoint(CARD_ID)


def test_a_carried_floor_survives_a_resume(store, monkeypatch):
    wf, crashed = _crash_in_the_loop(store)
    # The first run floored the turn it died in under its own id.
    assert crashed.reason == "turn"
    assert crashed.floor == store_checkpoints.TurnFloor("spec", 1, RUN_ID, 0)
    # As if that row had itself been carried from an earlier run: the resume
    # must keep the earlier run's id and number, not recompute its own.
    carried = store_checkpoints.TurnFloor("spec", 1, "run-earlier", 7)
    seen: list[tuple[str, Adoption | None]] = []
    taken: list[Adoption | None] = []
    take = RunDeps.take_adoption

    def recording_take(self, phase, loop):
        taken.append(take(self, phase, loop))
        return taken[-1]

    monkeypatch.setattr(RunDeps, "take_adoption", recording_take)

    summary = _go(
        wf,
        store,
        agent_runner=_critic_fails_recording(seen),
        resume_from=dataclasses.replace(crashed, floor=carried),
    )

    # The resumed head's tool takes the carried floor before it dispatches
    # (compile.agent_phase, exactly-once E8). This runner has no `adopt`, so
    # it dispatches as before -- and sees the adoption already consumed.
    assert taken[0] == Adoption("spec", 1, "run-earlier", 7)
    assert seen[0] == ("spec", None)
    assert summary.status == "escalated"
    assert _floors(store)[crashed.seq + 1:] == [
        ("turn", carried),
        ("turn", store_checkpoints.TurnFloor("validate_spec", 1, RUN_ID, 0)),
        ("escalated", None),
    ]


def test_a_floorless_row_resumes_with_a_fresh_floor(store):
    wf, crashed = _crash_in_the_loop(store)
    for attempt in (1, 2):
        paths.attempt_dir(RUN_ID, CARD_ID, "spec", attempt)
    seen: list[tuple[str, Adoption | None]] = []

    _go(
        wf,
        store,
        agent_runner=_critic_fails_recording(seen),
        resume_from=dataclasses.replace(crashed, floor=None),
    )

    assert seen[0] == ("spec", None)
    assert _floors(store)[crashed.seq + 1] == ("turn", store_checkpoints.TurnFloor("spec", 1, RUN_ID, 2))


# ── a resume re-ensures a missing worktree (card f76af5b2) ───────────────────

BRANCH = f"m6/task-resume-a-subtask-from-{CARD_ID}"
BASE_BRANCH = "m6/story-base"
KEPT = {"branch_existed": True, "worktree_existed": False, "created": True}
"""What `worktree.ensure` reports after re-adding a worktree whose branch survived."""
GONE = {"branch_existed": False, "worktree_existed": False, "created": True}
"""What `worktree.ensure` reports after cutting a branch that no longer existed."""


class _FakeEnsure:
    """A recording `ensure_worktree` that never runs git.

    Called with `worktree.ensure`'s four positional arguments `(branch, base,
    worktree, repo_dir)`; records each call, then raises `error` if one is set,
    else returns `result` filled out to `ensure`'s full shape. `error` may be
    set after construction, so one fake can succeed for a first run and fail
    for the resume that follows.
    """

    def __init__(
        self, result: dict[str, object] = KEPT, error: BaseException | None = None
    ) -> None:
        self.result = dict(result)
        self.error = error
        self.calls: list[tuple[str, str, Path | None, Path]] = []

    def __call__(self, branch, base, worktree, repo_dir) -> dict[str, object]:
        self.calls.append((branch, base, worktree, repo_dir))
        if self.error is not None:
            raise self.error
        return {"branch": branch, "worktree": str(worktree), **self.result, "commit_count": 0}


def _crash_in_c(opened) -> tuple[list[str], Workflow, Any]:
    """Run `_five` until it dies in `c`; what ran, the workflow and the `turn` row."""
    ran: list[str] = []
    wf = _five(ran, {"c"})
    with pytest.raises(_Crash):
        _go(wf, opened)
    crashed = opened.latest_checkpoint(CARD_ID)
    assert crashed.reason == "turn"
    assert _head(crashed.agent) == "c"
    return ran, wf, crashed


def test_a_fresh_walk_reports_no_resume_point(store):
    summary = _go(_five([], set()), store)

    assert summary.status == "done"
    assert summary.resumed_at is None


def test_an_intact_worktree_resumes_without_touching_git(store):
    """Spec test 1: the fast path -- the directory is there, the seam is never called."""
    ran, wf, crashed = _crash_in_c(store)
    fake = _FakeEnsure()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=fake)

    assert fake.calls == []
    assert ran == ["a", "b", "c", "c", "d", "e"]
    assert summary.status == "done"
    assert summary.warnings == []
    assert summary.resumed_at == "c"


def test_a_subtask_with_no_worktree_path_resumes_unchanged(store, monkeypatch):
    """Spec test 5: `worktree_path is None` is the fast path too."""
    monkeypatch.setitem(globals(), "WORKTREE", None)
    ran, wf, crashed = _crash_in_c(store)
    fake = _FakeEnsure()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=fake)

    assert fake.calls == []
    assert ran == ["a", "b", "c", "c", "d", "e"]
    assert summary.status == "done"
    assert summary.warnings == []
    assert summary.resumed_at == "c"


def test_resumed_at_is_reported_when_the_resumed_walk_parks_again(store):
    """Review Focus 5: a kept resume that stops still says where it continued."""
    ran, wf, _ = _park_after_a(store)
    parked = store.latest_checkpoint(CARD_ID)
    still = StopSignal()
    still.trigger("elsewhere")

    summary = _go(wf, store, resume_from=parked, stop=still, ensure_worktree=_FakeEnsure())

    assert summary.status == "stopped"
    assert summary.resumed_at == "b"


def _re_added(seq: int, path: Path, phase: str) -> str:
    return (
        f"checkpoint #{seq} of run {RUN_ID}: worktree {path} was missing and was"
        f" added again for branch '{BRANCH}'; resuming at '{phase}'"
    )


def _branch_gone(seq: int, path: Path) -> str:
    return (
        f"checkpoint #{seq} of run {RUN_ID} was not resumed (worktree {path} is"
        f" missing and branch '{BRANCH}' no longer exists); starting from the first phase"
    )


def _re_add_failed(seq: int, path: Path, rendered: str) -> str:
    return (
        f"checkpoint #{seq} of run {RUN_ID} was not resumed (worktree {path} is"
        f" missing and could not be added again: {rendered}); starting from the first phase"
    )


def _record_adoptions(monkeypatch) -> list[tuple[str, Adoption | None, Adoption | None]]:
    """Wrap `RunDeps.take_adoption`: `(phase, adopt before the take, what it returned)` per call."""
    seen: list[tuple[str, Adoption | None, Adoption | None]] = []
    take = RunDeps.take_adoption

    def recording_take(self, phase, loop):
        before = self.adopt
        taken = take(self, phase, loop)
        seen.append((phase, before, taken))
        return taken

    monkeypatch.setattr(RunDeps, "take_adoption", recording_take)
    return seen


def test_a_deleted_worktree_whose_branch_survives_is_re_added_and_resumed(
    store, worktree_dir, monkeypatch
):
    """Spec test 2: the checkpoint is kept, and so is its carried floor."""
    ran, wf, crashed = _crash_in_c(store)
    assert _next_turn(crashed.agent)["kwargs"] == {"phase": "c", "loop": 0}
    carried = store_checkpoints.TurnFloor("c", 0, "run-earlier", 7)
    worktree_dir.rmdir()
    fake = _FakeEnsure(KEPT)
    adoptions = _record_adoptions(monkeypatch)
    ran.clear()

    summary = _go(
        wf,
        store,
        resume_from=dataclasses.replace(crashed, floor=carried),
        ensure_worktree=fake,
    )

    assert fake.calls == [(BRANCH, BASE_BRANCH, worktree_dir, REPO)]
    assert ran == ["c", "d", "e"]
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS
    assert summary.warnings == [_re_added(crashed.seq, worktree_dir, "c")]
    assert summary.resumed_at == "c"
    # Same shape as test_a_carried_floor_survives_a_resume: the resumed head
    # takes the carried floor, unchanged.
    assert adoptions[0][2] == Adoption("c", 0, "run-earlier", 7)


def test_a_deleted_worktree_whose_branch_is_gone_starts_over(store, worktree_dir, monkeypatch):
    """Spec test 3: the checkpoint is declined and the walk runs from the first phase."""
    ran, wf, crashed = _crash_in_c(store)
    worktree_dir.rmdir()
    fake = _FakeEnsure(GONE)
    adoptions = _record_adoptions(monkeypatch)
    ran.clear()

    summary = _go(
        wf,
        store,
        # A floor on the declined row must not be carried into the fresh walk.
        resume_from=dataclasses.replace(crashed, floor=store_checkpoints.TurnFloor("c", 0, "run-earlier", 7)),
        ensure_worktree=fake,
    )

    assert fake.calls == [(BRANCH, BASE_BRANCH, worktree_dir, REPO)]
    assert ran == list(FIVE)
    assert summary.status == "done"
    assert summary.results == ALL_RESULTS
    assert summary.warnings == [_branch_gone(crashed.seq, worktree_dir)]
    assert summary.resumed_at is None
    # deps.adopt is None: the fresh walk's first take sees no carried floor.
    assert adoptions[0][:2] == ("a", None)
    # The declined row stays, superseded by the fresh walk's newer rows.
    assert store.latest_checkpoint(CARD_ID).seq > crashed.seq
    reopened = store.latest_open_checkpoint(CARD_ID, wf.name)
    assert reopened is None or reopened.seq != crashed.seq


def test_a_failed_re_add_starts_over_and_phase_0_escalates(store, worktree_dir):
    """Spec test 4: the engine declines and warns, it does not escalate itself;
    phase 0 of the fresh walk runs the same `ensure` and fails as an ordinary step."""
    fake = _FakeEnsure(KEPT)
    ran: list[str] = []

    def ensure_step(branch: str, base: str, worktree: Path, repo_dir: Path) -> dict[str, Any]:
        ran.append("worktree")
        return fake(branch, base, worktree, repo_dir)

    wf = Workflow("guarded", (Step("worktree", ensure_step),) + _five(ran, {"c"}).phases)
    with pytest.raises(_Crash):
        _go(wf, store)
    crashed = store.latest_checkpoint(CARD_ID)
    assert _head(crashed.agent) == "c"
    worktree_dir.rmdir()
    error = GitError("fatal: could not create work tree dir", argv=["worktree", "add"], exit_code=128)
    fake.error = error
    fake.calls.clear()
    ran.clear()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=fake)

    # Once for the re-check, once as phase 0 of the fresh walk.
    assert fake.calls == [(BRANCH, BASE_BRANCH, worktree_dir, REPO)] * 2
    assert ran == ["worktree"]
    assert summary.warnings == [_re_add_failed(crashed.seq, worktree_dir, f"GitError: {error}")]
    assert summary.status == "escalated"
    assert summary.failed_phase == "worktree"
    assert summary.detail.startswith("GitError:")
    assert summary.resumed_at is None
    assert _phase_rows(store)[-2:] == [("worktree", "started"), ("worktree", "failed")]


def test_the_digest_check_runs_before_the_worktree_re_check(store, worktree_dir):
    """Spec test 6: a changed workflow is refused before the seam is ever called."""
    _, wf, crashed = _crash_in_c(store)
    changed = Workflow("five", wf.phases + (Step("f", _extra),))
    worktree_dir.rmdir()
    fake = _FakeEnsure(KEPT)

    with pytest.raises(runtime_engine.CheckpointMismatch):
        _go(changed, store, resume_from=crashed, ensure_worktree=fake)

    assert fake.calls == []


def test_a_checkpoint_parked_before_the_worktree_existed_walks_from_the_start(
    store, worktree_dir
):
    """Review Focus 1: parked before phase 0, no worktree and no branch were ever made."""
    ran: list[str] = []
    wf = _five(ran, set())
    parked = _park_with_a_triggered_stop(wf, store)
    worktree_dir.rmdir()
    fake = _FakeEnsure(GONE)

    summary = _go(wf, store, resume_from=parked, ensure_worktree=fake)

    assert len(fake.calls) == 1
    assert ran == list(FIVE)
    assert summary.status == "done"
    assert summary.warnings == [_branch_gone(parked.seq, worktree_dir)]
    assert summary.resumed_at is None


def test_any_error_from_the_reensure_declines_and_never_escapes(store, worktree_dir):
    """Review Focus 2: not only `GitError` -- `ensure`'s own `ValueError`, a lock timeout."""
    ran, wf, crashed = _crash_in_c(store)
    worktree_dir.rmdir()
    error = ValueError("worktree.ensure needs a non-empty base name, got ''")
    fake = _FakeEnsure(error=error)
    ran.clear()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=fake)

    assert ran == list(FIVE)
    assert summary.status == "done"
    assert summary.warnings == [_re_add_failed(crashed.seq, worktree_dir, f"ValueError: {error}")]
    assert summary.resumed_at is None


def test_a_stale_registry_entry_does_not_block_a_declined_resume(store, worktree_dir):
    """Review Focus 3: the decline path frees the checkpoint's agent name too."""
    ran, wf, crashed = _crash_in_c(store)
    Agent(crashed.agent["name"], "left behind by a dead run", [])
    worktree_dir.rmdir()
    ran.clear()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=_FakeEnsure(GONE))

    assert ran == list(FIVE)
    assert summary.status == "done"
    with pytest.raises(UnregisteredAgentError):
        AgentRegistry.get(crashed.agent["name"])


def test_a_file_where_the_worktree_should_be_is_not_the_fast_path(store, worktree_dir):
    """Review Focus 4: only a directory counts as an intact worktree."""
    ran, wf, crashed = _crash_in_c(store)
    worktree_dir.rmdir()
    worktree_dir.write_text("not a worktree\n", encoding="utf-8")
    fake = _FakeEnsure(KEPT)
    ran.clear()

    summary = _go(wf, store, resume_from=crashed, ensure_worktree=fake)

    assert fake.calls == [(BRANCH, BASE_BRANCH, worktree_dir, REPO)]
    assert ran == ["c", "d", "e"]
    assert summary.warnings == [_re_added(crashed.seq, worktree_dir, "c")]
