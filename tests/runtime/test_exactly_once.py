"""Exactly-once agent phases across a crash (exactly-once design E8, E9, E11; card 6ecbe6e2).

Engine tier (design §14): `runtime.engine.run_subtask` drives a real
`dispatch.AgentRunner` over a counting `FakeLauncher` that writes canned
result files, a real temp SQLite projection plus a real temp JSONL journal,
and real pygents agents. Nothing is ever spawned. The workflow is
`w` (step) -> `a` (agent) -> `b` (agent) -> `z` (step).

A crash is `_Crash`, a plain `BaseException`, as in tests/runtime/test_resume.py:
neither the engine, pygents nor `AgentRunner` may catch it. Each crash point is
armed by wrapping the call it fires after with `monkeypatch.setattr`, once.
No sleeps: the one stop is handed to the loop with `call_soon_threadsafe`.
Registry isolation between tests is the autouse `fresh_pygents` fixture.
"""

import asyncio
import dataclasses
import hashlib
import json
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from pygents import AgentRegistry, ToolRegistry

from agent_manager import cli, dispatch, models, paths, store as store_module
from agent_manager.harness.base import Outcome
from agent_manager.runtime import bridge, walk
from agent_manager.runtime import compile as compile_mod
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.store import checkpoints as store_checkpoints
from agent_manager.workflow.phases import AgentPhase, Goto, Step, Workflow

RUN_ID = "run-2026-10-01-01"
OTHER_RUN_ID = "run-2026-10-01-02"
STORY_ID = "fd673816"
CARD_ID = "6ecbe6e2"
REPO = Path("/repo")
FIXED = datetime(2026, 10, 1, tzinfo=timezone.utc)


class _Crash(BaseException):
    """A process death at a named point: not an `Exception`, so nothing may catch it."""


# ── result model, roles, adapter, launcher ──────────────────────────────────


class Found(BaseModel):
    """Both agent phases' result model."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    ok: bool = True


FOUND = {"summary": "found it", "ok": True}
FOUND_JSON = json.dumps(FOUND)
REJECTED_JSON = json.dumps({"summary": "not yet", "ok": False})


def critic_gate(result):
    """`b`'s gate: a result with `ok` false fails it."""
    return None if result["ok"] else {"approved": False}


POLICY = """\
allowed_tools = ["Read"]
max_attempts = 2
required_capabilities = []

[default_model]
claude = "sonnet"
fake = "fake-model"
"""

METHODOLOGY = """\
# Test-driven development

## the loop

Red, green, refactor. Never write implementation code before a failing test.
"""


def make_role(root: Path, name: str = "explorer") -> Path:
    """A synthetic role bundle, as tests/test_dispatch.py's `make_role` builds it."""
    directory = root / name
    (directory / "methodology").mkdir(parents=True, exist_ok=True)
    (directory / "system.md").write_text(
        f"Standing instructions for {name}.\n", encoding="utf-8"
    )
    (directory / "policy.toml").write_text(POLICY, encoding="utf-8")
    path = directory / "methodology" / "test-driven-development.md"
    path.write_text(METHODOLOGY, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / "VENDORED.lock").write_text(
        "[[vendored]]\n"
        'file = "test-driven-development.md"\n'
        'upstream = "skills/test-driven-development.md"\n'
        f'sha256 = "{digest}"\n',
        encoding="utf-8",
    )
    return directory


class FakeAdapter:
    """A `HarnessAdapter` by shape, whose argv names a program nothing runs."""

    name = "fake"
    capabilities = frozenset({"bash", "edit"})

    def build_command(self, d: models.Dispatch) -> list[str]:
        return [
            "fake-harness",
            "--model",
            d.model,
            "--prompt",
            str(d.prompt_path),
            "--result",
            str(d.result_path),
        ]


@dataclass
class FakeLauncher:
    """A counting `LauncherFn` double that writes canned result files.

    `calls` holds the phase of every dispatch, in order (read from the attempt
    directory `{phase}.{n}` the result path sits in). `results[phase][i]` is
    that phase's (i+1)th dispatch's `result.json` text, the last entry
    repeating; a phase with no entry gets `FOUND_JSON`. A `(phase, nth)` in
    `crash_on` writes its result file and then raises `_Crash`, once: the
    manager died mid-dispatch, after the harness wrote a valid file (W0).
    `on_call`, when set, is called with the phase before the file is written.
    """

    results: dict[str, list[str | None]] = field(default_factory=dict)
    crash_on: set[tuple[str, int]] = field(default_factory=set)
    on_call: Callable[[str], None] | None = None
    calls: list[str] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        result_path = Path(argv[argv.index("--result") + 1])
        phase = result_path.parent.name.rsplit(".", 1)[0]
        self.calls.append(phase)
        nth = self.calls.count(phase)
        if self.on_call is not None:
            self.on_call(phase)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("log\n", encoding="utf-8")
        script = self.results.get(phase, [FOUND_JSON])
        canned = script[min(nth - 1, len(script) - 1)]
        if canned is not None:
            result_path.write_text(canned, encoding="utf-8")
        if (phase, nth) in self.crash_on:
            self.crash_on.discard((phase, nth))
            raise _Crash(f"killed while {phase} dispatch {nth} was in flight")
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=1.0,
            stdout_path=stdout_path,
        )


def _dispatches(launcher: FakeLauncher) -> dict[str, int]:
    """How many times each phase reached the launcher, over every run."""
    return dict(Counter(launcher.calls))


# ── workflow, store, runner ─────────────────────────────────────────────────


def _workflow(ran: list[str], *, critic: bool = False) -> Workflow:
    """`w` -> `a` -> `b` -> `z`. The steps append their name to `ran`.
    With `critic`, a `b` whose gate fails loops back to `a` once."""

    def make(name: str):
        def run(card: str) -> dict[str, Any]:
            ran.append(name)
            return {name: name.upper()}

        return run

    return Workflow(
        "exactly_once",
        (
            Step("w", make("w")),
            AgentPhase("a", "explorer", (), Found),
            AgentPhase(
                "b",
                "explorer",
                (),
                Found,
                gates=(critic_gate,),
                on_fail=Goto("a", 1) if critic else None,
            ),
            Step("z", make("z")),
        ),
    )


def _seed(opened, run_id: str = RUN_ID) -> None:
    """The run, story and subtask lines `Store.replay_journal` needs above any phase line."""
    opened.record_run(
        models.Run(
            id=run_id,
            workflow="exactly_once",
            repo_dir=REPO,
            base_branch="master",
            branch_prefix="m11/",
        )
    )
    opened.record_story(models.StoryRun(card_id=STORY_ID, title="Adoption", level=0))
    opened.record_subtask(
        STORY_ID,
        models.SubtaskRun(card_id=CARD_ID, branch=f"m11/task-{CARD_ID}", base_branch="master"),
    )


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    _seed(opened)
    yield opened
    opened.close()


@pytest.fixture
def roles(tmp_path) -> Path:
    make_role(tmp_path / "bundles")
    return tmp_path / "bundles"


def _runner(opened, launcher: FakeLauncher, roles: Path) -> dispatch.AgentRunner:
    """A real `AgentRunner` for `opened`'s run. One per run: a new process
    starts with an empty `warnings` list."""
    adapter = FakeAdapter()
    return dispatch.AgentRunner(
        store=opened,
        launcher=launcher,
        run_id=opened.run_id,
        story_id=STORY_ID,
        card_id=CARD_ID,
        adapters={adapter.name: adapter},
        result_models={},
        harness_map={
            "explorer": models.HarnessAssignment(harness=adapter.name, model="fake-model")
        },
        role_root=roles,
        timeout=45.0,
    )


WORKTREE: Path | None = None
"""The subtask's worktree path. The autouse `worktree_dir` fixture sets it to a
real `tmp_path` directory, so a resume takes the engine's no-git fast path."""


@pytest.fixture(autouse=True)
def worktree_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "worktree"
    path.mkdir()
    monkeypatch.setitem(globals(), "WORKTREE", path)
    return path


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=CARD_ID,
        branch=f"m11/task-adopt-the-resumed-head-{CARD_ID}",
        base_branch="m11/story-base",
        status="started",
        worktree_path=WORKTREE,
    )


def _go(workflow: Workflow, opened, runner, **kwargs: Any):
    return runtime_engine.run_subtask(
        workflow,
        opened,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        agent_runner=runner,
        **kwargs,
    )


# ── reading what happened ───────────────────────────────────────────────────


def _reused(phase: str, n: int, source_run: str) -> str:
    """The one warning line an adoption writes (`AgentRunner.adopt`)."""
    return (
        f"phase {phase!r} was not dispatched again: attempt {n} of run {source_run} "
        "had already succeeded (result reused)"
    )


def _phase_rows(opened, phase: str) -> list[str]:
    """Every `phase_upsert` status of `phase` in `opened`'s own journal, in order."""
    return [
        line.payload["status"]
        for line in opened.journal.read()
        if line.event == "phase_upsert" and line.phase == phase
    ]


def _attempt_statuses(opened, phase: str) -> list[tuple[int, str]]:
    """`phase`'s attempts as the journal last records them: `(n, status)`."""
    run = opened.replay_journal(opened.run_id)
    return [
        (attempt.n, attempt.status)
        for story in run.stories
        for subtask in story.subtasks
        if subtask.card_id == CARD_ID
        for recorded in subtask.phases
        if recorded.name == phase
        for attempt in recorded.attempts
    ]


def _next_turn(agent: dict) -> dict:
    """The turn a stored agent would run next: its current turn, else its queue head."""
    return agent["current_turn"] or agent["queue"][0]


def _head(agent: dict) -> str:
    return _next_turn(agent)["kwargs"]["phase"]


def _mark_orphans(opened) -> None:
    """What a real resume does first (cli.py, before `drive_subtask_async`):
    every attempt left `started` is recorded `harness_error`."""
    run = opened.replay_journal(opened.run_id)
    for story in run.stories:
        for subtask in story.subtasks:
            for phase, attempt in cli.orphan_attempts(subtask):
                opened.record_attempt(
                    story.card_id,
                    subtask.card_id,
                    phase.name,
                    attempt.model_copy(update={"status": "harness_error"}),
                )


def _new_process() -> None:
    """A restart: every pygents registry and the compile cache start empty."""
    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()


# ── crash points ────────────────────────────────────────────────────────────


def _crash_after_call_agent(monkeypatch, name: str) -> None:
    """W2: `name`'s dispatch returned its result, and the process died before
    the next checkpoint. Once."""
    armed = {name}
    real = bridge.call_agent

    async def call_agent(runner, phase, context, rendered):
        result = await real(runner, phase, context, rendered)
        if phase.name in armed:
            armed.discard(phase.name)
            raise _Crash(f"killed after {phase.name}'s dispatch returned")
        return result

    monkeypatch.setattr(bridge, "call_agent", call_agent)


def _crash_after_done_row(monkeypatch, name: str) -> None:
    """W1: `name`'s `done` row is in the journal, and the process died. Once."""
    armed = {name}
    real = dispatch.AgentRunner._record_phase

    def record_phase(self, phase, status, *args):
        real(self, phase, status, *args)
        if status == "done" and phase.name in armed:
            armed.discard(phase.name)
            raise _Crash(f"killed after {phase.name}'s done row")

    monkeypatch.setattr(dispatch.AgentRunner, "_record_phase", record_phase)


def _crash_after_adopt(monkeypatch) -> None:
    """The process died right after an adoption returned, before its turn ended. Once."""
    armed = [True]
    real = dispatch.AgentRunner.adopt

    def adopt(self, phase, context, *, source_run, floor):
        adopted = real(self, phase, context, source_run=source_run, floor=floor)
        if armed:
            armed.clear()
            raise _Crash(f"killed after {phase.name} was adopted")
        return adopted

    monkeypatch.setattr(dispatch.AgentRunner, "adopt", adopt)


def _crash_after_step(monkeypatch, name: str) -> None:
    """Step `name` ran and recorded `done`, and the process died before the
    next checkpoint. Once."""
    armed = {name}
    real = walk.run_one_step

    def run_one_step(**kwargs):
        outcome = real(**kwargs)
        if kwargs["phase"].name in armed:
            armed.discard(kwargs["phase"].name)
            raise _Crash(f"killed after step {kwargs['phase'].name}")
        return outcome

    monkeypatch.setattr(walk, "run_one_step", run_one_step)


class _StopDuring:
    """A `StopSignal` the launcher triggers while `phase` is dispatching, once.

    tests/runtime/test_resume.py's `_StopAfterA`, moved into the launcher: the
    launcher runs in a `to_thread` worker and the signal lives on the loop, so
    the trigger is handed over with `call_soon_threadsafe` and the worker
    blocks on a `threading.Event` until it has run -- no sleeps.
    """

    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.signal = StopSignal()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._fired = threading.Event()

    def __call__(self, phase: str) -> None:
        if phase != self.phase or self._fired.is_set():
            return
        assert self.loop is not None, "set before the run"

        def trigger() -> None:
            self.signal.trigger(STORY_ID)
            self._fired.set()

        self.loop.call_soon_threadsafe(trigger)
        if not self._fired.wait(5):
            raise RuntimeError("the stop was never triggered")


# ── adoption: the windows where the head already succeeded ──────────────────


def test_w2_a_crash_after_the_dispatch_returned_adopts_on_resume(store, roles, monkeypatch):
    launcher = FakeLauncher()
    ran: list[str] = []
    wf = _workflow(ran)
    _crash_after_call_agent(monkeypatch, "a")

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    crashed = store.latest_checkpoint(CARD_ID)
    assert (crashed.reason, _head(crashed.agent)) == ("turn", "a")
    assert crashed.floor == store_checkpoints.TurnFloor("a", 0, RUN_ID, 0)
    assert _dispatches(launcher) == {"a": 1}

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == [_reused("a", 1, RUN_ID)]
    assert summary.results["a"] == FOUND
    assert ran == ["w", "z"]
    assert _attempt_statuses(store, "a") == [(1, "ok")]


def test_w1_a_crash_after_the_done_row_does_not_dispatch_again(store, roles, monkeypatch):
    launcher = FakeLauncher()
    ran: list[str] = []
    wf = _workflow(ran)
    _crash_after_done_row(monkeypatch, "a")

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    assert _phase_rows(store, "a") == ["started", "done"]
    crashed = store.latest_checkpoint(CARD_ID)
    assert _head(crashed.agent) == "a"

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == [_reused("a", 1, RUN_ID)]


def test_w2_at_the_last_agent_phase_adopts_and_the_subtask_completes(
    store, roles, monkeypatch
):
    launcher = FakeLauncher()
    ran: list[str] = []
    wf = _workflow(ran)
    _crash_after_call_agent(monkeypatch, "b")

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    crashed = store.latest_checkpoint(CARD_ID)
    assert _head(crashed.agent) == "b"
    assert crashed.floor == store_checkpoints.TurnFloor("b", 0, RUN_ID, 0)
    assert ran == ["w"]

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == [_reused("b", 1, RUN_ID)]
    assert summary.results["b"] == FOUND
    assert ran == ["w", "z"]
    assert store.latest_checkpoint(CARD_ID).reason == "done"


def test_a_second_crash_before_the_adopted_phase_finishes_still_adopts(
    store, roles, monkeypatch
):
    launcher = FakeLauncher()
    ran: list[str] = []
    wf = _workflow(ran)
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)

    _crash_after_adopt(monkeypatch)
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles), resume_from=crashed)

    second = store.latest_checkpoint(CARD_ID)
    assert second.seq > crashed.seq
    assert (second.reason, _head(second.agent)) == ("turn", "a")
    # The resumed BEFORE_TURN re-saved the carried floor unchanged.
    assert second.floor == store_checkpoints.TurnFloor("a", 0, RUN_ID, 0)

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=second)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == [_reused("a", 1, RUN_ID)]


def test_a_relaunch_adopts_once_from_the_earlier_run(store, roles, monkeypatch, tmp_path):
    launcher = FakeLauncher()
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(_workflow([]), store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)

    _new_process()
    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        _seed(other, OTHER_RUN_ID)
        runner = _runner(other, launcher, roles)
        ran: list[str] = []
        summary = _go(_workflow(ran), other, runner, resume_from=crashed)
        a_rows = _phase_rows(other, "a")
        newest = other.latest_checkpoint(CARD_ID)
    finally:
        other.close()

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == [_reused("a", 1, RUN_ID)]
    assert RUN_ID in runner.warnings[0]
    # The adoption is recorded in the new run only, as one `done` row.
    assert a_rows == ["done"]
    assert newest.reason == "done"
    assert ran == ["z"]


def test_an_adopt_that_raises_escalates_like_a_dispatch_error(store, roles, monkeypatch):
    launcher = FakeLauncher()
    wf = _workflow([])
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)

    def adopt(self, phase, context, *, source_run, floor):
        raise RuntimeError("journal vanished")

    monkeypatch.setattr(dispatch.AgentRunner, "adopt", adopt)
    summary = _go(wf, store, _runner(store, launcher, roles), resume_from=crashed)

    assert summary.status == "escalated"
    assert summary.failed_phase == "a"
    assert summary.detail == "RuntimeError: journal vanished"
    assert _dispatches(launcher) == {"a": 1}
    assert store.latest_checkpoint(CARD_ID).reason == "escalated"


def test_a_damaged_result_is_declined_and_dispatched_again(store, roles, monkeypatch):
    launcher = FakeLauncher()
    wf = _workflow([])
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)
    result_file = paths.attempt_dir(RUN_ID, CARD_ID, "a", 1) / dispatch.RESULT_NAME
    result_file.write_text("I could not produce JSON, sorry.", encoding="utf-8")

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 2, "b": 1}
    [warning] = runner.warnings
    assert warning.startswith(f"phase 'a': attempt 1 of run {RUN_ID} was not reused (")
    assert "is not valid JSON" in warning
    assert warning.endswith("); dispatching again")
    assert _attempt_statuses(store, "a") == [(1, "ok"), (2, "ok")]


# ── steps are never adopted (E9) ────────────────────────────────────────────


def test_a_step_caught_in_the_window_runs_again(store, roles, monkeypatch):
    launcher = FakeLauncher()
    ran: list[str] = []
    wf = _workflow(ran)
    _crash_after_step(monkeypatch, "w")

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    assert ran == ["w"]
    assert _phase_rows(store, "w") == ["started", "done"]
    crashed = store.latest_checkpoint(CARD_ID)
    assert _head(crashed.agent) == "w"
    assert crashed.floor is None  # a step head carries no floor

    summary = _go(wf, store, _runner(store, launcher, roles), resume_from=crashed)

    assert summary.status == "done"
    assert ran == ["w", "w", "z"]  # at-least-once: the step ran again
    assert _dispatches(launcher) == {"a": 1, "b": 1}


def test_a_carried_adoption_does_not_outlive_a_step_head(store, roles, monkeypatch, tmp_path):
    launcher = FakeLauncher()
    ran: list[str] = []
    # Run 1 completes: `a` attempt 1 is `ok` in RUN_ID's journal.
    assert _go(_workflow(ran), store, _runner(store, launcher, roles)).status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}

    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        _seed(other, OTHER_RUN_ID)
        _crash_after_step(monkeypatch, "w")
        with pytest.raises(_Crash):
            _go(_workflow(ran), other, _runner(other, launcher, roles))
        crashed = other.latest_checkpoint(CARD_ID)
        assert _head(crashed.agent) == "w"
        # A floor naming `a`, carried into a resume whose head is the step
        # `w`: the step's turn is the first after resume, so it must end the
        # adoption, and `a` must dispatch rather than reuse run 1's result.
        forged = dataclasses.replace(crashed, floor=store_checkpoints.TurnFloor("a", 0, RUN_ID, 0))
        runner = _runner(other, launcher, roles)
        summary = _go(_workflow(ran), other, runner, resume_from=forged)
    finally:
        other.close()

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 2, "b": 2}
    assert runner.warnings == []


# ── the windows that must dispatch again ────────────────────────────────────


def test_w0_a_crash_mid_dispatch_dispatches_again(store, roles):
    # The harness wrote a valid result.json, but the attempt was never judged:
    # it stays `started`, a resume marks it `harness_error`, and an attempt
    # that is not `ok` is never adopted.
    launcher = FakeLauncher(crash_on={("a", 1)})
    wf = _workflow([])

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    assert (paths.attempt_dir(RUN_ID, CARD_ID, "a", 1) / dispatch.RESULT_NAME).is_file()
    crashed = store.latest_checkpoint(CARD_ID)
    assert crashed.floor == store_checkpoints.TurnFloor("a", 0, RUN_ID, 0)
    _mark_orphans(store)

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 2, "b": 1}
    assert _attempt_statuses(store, "a") == [(1, "harness_error"), (2, "ok")]
    assert runner.warnings == []


def test_a_goto_looped_phase_dispatches_every_iteration_and_is_never_adopted(store, roles):
    # `b` rejects the first `a`, so `a` runs again at loop 1 and dies there
    # mid-dispatch. Its loop-0 attempt 1 is `ok` but sits at the loop-1 floor,
    # so the resume must dispatch `a` a third time rather than reuse it.
    launcher = FakeLauncher(
        results={"b": [REJECTED_JSON, FOUND_JSON]}, crash_on={("a", 2)}
    )
    wf = _workflow([], critic=True)

    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))

    assert _dispatches(launcher) == {"a": 2, "b": 1}
    crashed = store.latest_checkpoint(CARD_ID)
    assert _next_turn(crashed.agent)["kwargs"] == {"phase": "a", "loop": 1}
    assert crashed.floor == store_checkpoints.TurnFloor("a", 1, RUN_ID, 1)
    _mark_orphans(store)

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 3, "b": 2}
    assert runner.warnings == []
    assert _attempt_statuses(store, "a") == [(1, "ok"), (2, "harness_error"), (3, "ok")]


def test_a_checkpoint_with_no_floor_row_dispatches_again(store, roles, monkeypatch):
    # E11: a row saved before checkpoint_floors existed has no floor; the
    # resume degrades to at-least-once, with no error.
    launcher = FakeLauncher()
    wf = _workflow([])
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))
    store.connection.execute("DELETE FROM checkpoint_floors")
    store.connection.commit()
    crashed = store.latest_checkpoint(CARD_ID)
    assert crashed.floor is None

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=crashed)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 2, "b": 1}
    assert runner.warnings == []


def test_a_parked_subtask_dispatches_the_next_phase_once(store, roles):
    stop = _StopDuring("a")
    launcher = FakeLauncher(on_call=stop)
    ran: list[str] = []
    wf = _workflow(ran)

    async def go():
        stop.loop = asyncio.get_running_loop()
        return await runtime_engine.run_subtask_async(
            wf,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            clock=lambda: FIXED,
            agent_runner=_runner(store, launcher, roles),
            stop=stop.signal,
        )

    parked_summary = asyncio.run(go())

    assert parked_summary.status == "stopped"
    assert parked_summary.detail == "stopped before b"
    parked = store.latest_checkpoint(CARD_ID)
    assert parked.reason == "parked"
    assert parked.floor == store_checkpoints.TurnFloor("b", 0, RUN_ID, 0)
    assert _dispatches(launcher) == {"a": 1}

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=parked)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 1}
    assert runner.warnings == []
    assert ran == ["w", "z"]


def test_a_mismatched_adoption_is_discarded(store, roles, monkeypatch):
    # `b` succeeded and the process died; the row's floor is forged to name
    # `a`. `take_adoption("b", 0)` discards it, so `b` dispatches again.
    launcher = FakeLauncher()
    wf = _workflow([])
    _crash_after_call_agent(monkeypatch, "b")
    with pytest.raises(_Crash):
        _go(wf, store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)
    assert _head(crashed.agent) == "b"
    forged = dataclasses.replace(crashed, floor=store_checkpoints.TurnFloor("a", 0, RUN_ID, 0))

    runner = _runner(store, launcher, roles)
    summary = _go(wf, store, runner, resume_from=forged)

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 1, "b": 2}
    assert runner.warnings == []


def test_a_relaunch_whose_source_journal_is_gone_dispatches_again(
    store, roles, monkeypatch, tmp_path
):
    launcher = FakeLauncher()
    _crash_after_call_agent(monkeypatch, "a")
    with pytest.raises(_Crash):
        _go(_workflow([]), store, _runner(store, launcher, roles))
    crashed = store.latest_checkpoint(CARD_ID)
    store.journal.path.unlink()

    _new_process()
    other = store_module.Store.open(tmp_path / "repo", OTHER_RUN_ID)
    try:
        _seed(other, OTHER_RUN_ID)
        runner = _runner(other, launcher, roles)
        summary = _go(_workflow([]), other, runner, resume_from=crashed)
    finally:
        other.close()

    assert summary.status == "done"
    assert _dispatches(launcher) == {"a": 2, "b": 1}
    [warning] = runner.warnings
    assert warning.startswith(
        f"phase 'a': attempt ? of run {RUN_ID} was not reused (its journal cannot be read: "
    )
    assert "MissingJournalError" in warning
    assert warning.endswith("); dispatching again")
