<!-- task-pipeline: validated -->
# Resume a subtask from its checkpoint (card 5698e4f6)

Parent story: a6c7bff3 "Checkpoints and resume" (milestone m6). Narrowed from plan Task 4.3 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1253-1280`) and pygents-engine design §6/§8. This subtask adds the engine-level resume primitive only.

## Scope

Modify `src/agent_manager/runtime/engine.py` only. No new source files. Add `tests/runtime/test_resume.py`.

Out of scope, owned by sibling cards:
- `store.Checkpoint`, `save_checkpoint`, `latest_checkpoint`, `latest_open_checkpoint` (3d4947e8, done). This card only consumes `store.Checkpoint`.
- `runtime/checkpoint.py` hooks, `Parked`, `save`, and the body of `_run` / `_collect` / `_forget` (921ed349, done). None of these change.
- `--engine` plumbing through cli/orchestrate/integration (7fdec762).
- `am resume --engine pygents`, exit code 3 on mismatch, orphan-attempt marking, milestone relaunch from open checkpoints (02890d5d).
- Switching `_forget` to `AgentRegistry.unregister` (pygents 0.7.0 adoption, decision A2). That has not landed on this base. `_forget` still pops `AgentRegistry._registry`, so this card uses the same pop-and-tolerate-missing style.

## Interface

- `runtime.engine.CheckpointMismatch(Exception)` is a new module-level exception. Its message names the checkpoint's digest and the workflow's digest.
- `runtime.engine.run_subtask(..., resume_from: store.Checkpoint | None = None)` is a new keyword-only parameter. It defaults to `None`, and it is passed through to `_drive`. Every other parameter and the `SubtaskSummary` return type stay the same. Update the `run_subtask` docstring, because its "No `start_phase`: resume belongs to the yaml engine" sentence will be wrong once this lands.

## Observable behavior

The `_drive` order is:
1. Build the binding and refuse reserved `extra_context` keys, unchanged.
2. Call `C.compile_workflow(workflow)`. This registers the digest-prefixed tools.
3. If `resume_from` is `None`, keep today's path: `Agent(...)`, `seed_item`, `first_turn`.
4. If `resume_from` is given:
   - If `resume_from.digest != workflow.digest()`, raise `CheckpointMismatch`. This happens before any agent is built and before `run()`, so no checkpoint row, phase row, attempt row or journal line is written.
   - Otherwise, drop any stale `AgentRegistry` entry under the checkpointed agent's name, tolerating a missing entry.
   - Build the agent with `Agent.from_dict(resume_from.agent)`.
   - Do not call `seed_item` or `first_turn`. The pool, which holds the seed and earlier phase results, and the queue, which holds the pending turn and its loop count, come from the checkpoint.
   - Compiling in step 2 before `from_dict` is what makes a stale or changed workflow fail early instead of binding the wrong tools.
5. Both paths then share the unchanged `RunDeps` + `_run` + `finally: _forget(agent.name)`. `agent.run()` is still consumed to the end. It is never broken or returned out of.

A resumed run produces the same shapes as a fresh run (G10): the same `SubtaskSummary` (results include the restored earlier phases, taken from the pool), the same phase and attempt rows for the phases it actually dispatches, and the same escalation payloads and final subtask status. Checkpoints keep being written by the existing hooks: `turn` at each BEFORE_TURN, and `done`/`escalated`/`parked` at the end.

Error paths:
- On a digest mismatch, `CheckpointMismatch` propagates to the caller and nothing is recorded. Mapping it to exit 3 is not this card's job.
- A `BaseException` during a resumed run passes through unchanged, exactly as in a fresh run. The last `turn` row stands, so the run can be resumed again.
- `EngineError` and the escalation branches behave as in `_run` today.

## Tests (`tests/runtime/test_resume.py`)

All five tests belong to the "Engine" tier from agent-manager design §14 and pygents-engine design §9: engine tests driven by a fake runner/adapter, with simulated mid-phase crashes. They live under `tests/runtime/` to mirror `runtime/engine.py`. They are not `e2e` and they run in the default suite. They rely on the existing autouse `fresh_pygents` fixture in `tests/runtime/conftest.py` for `ToolRegistry`/`AgentRegistry`/compile-cache cleanup and add no registry cleanup of their own.

1. `test_a_crash_resumes_at_the_phase_it_died_in`: the fake runner raises `KeyboardInterrupt` in phase `c` of a workflow a..e, and the call propagates it. The latest checkpoint has reason `turn` and its queue head is `c`. Resuming with `resume_from=` that row dispatches exactly `c, d, e`. The results for `a` and `b` are present in the summary, taken from the pool without being re-run. The status is done.
2. `test_loop_count_survives_a_resume`: the run crashes inside a looped phase on its second pass (loop=1). After resuming, the dispatched turn carries loop=1.
3. `test_a_changed_workflow_is_refused`: take a checkpoint saved under workflow W and call with W′, whose digest differs. `CheckpointMismatch` is raised, no new checkpoint row is written, and no phase is dispatched.
4. `test_a_parked_subtask_resumes`: `should_stop` parks before `b`. Resuming from the `parked` checkpoint runs `b..end`, and the subtask status is done.
5. `test_resuming_twice_in_one_process`: resume the same card twice in a row in one process with no `ValueError` from `AgentRegistry` (Review Focus 4).

## Verification

`uv run pytest` must pass for the whole default suite, including the non-e2e `tests/e2e` wiring tests. There is no `--engine` selector yet (7fdec762, not landed on this base): "the whole default suite" already covers both the yaml engine's tests and the pygents engine's tests as separate files, so nothing new needs to be added to exercise "both engines".

---

# Resume a Subtask from Its Checkpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `runtime.engine.run_subtask` continue a subtask from a saved `store.Checkpoint` (`resume_from=`), and refuse with `CheckpointMismatch` when the checkpoint was saved under a different workflow digest.

**Architecture:** `_drive` still builds the binding and compiles the workflow first. With `resume_from`, it checks the digest, frees the checkpointed agent's name in `AgentRegistry` with the existing `_forget`, and rebuilds the agent with `Agent.from_dict`, skipping the seed item and first turn. Both paths then go through the unchanged `RunDeps` + `_run` + `finally: _forget(agent.name)`, so a resumed run writes the same rows and returns the same `SubtaskSummary` shape as a fresh one.

**Tech Stack:** Python 3.12, pygents 0.7.0 (installed in `.venv`), pytest + pytest-asyncio (`asyncio_mode = "auto"`), `uv`.

**Spec:** `docs/superpowers/specs/task-resume-a-subtask-from-5698e4f6-design.md` (reproduced verbatim above).

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-resume-a-subtask-from-5698e4f6`, branch `m6/task-resume-a-subtask-from-5698e4f6`, cut from `m6/task-checkpoint-every-turn-921ed349`. Every path below is relative to that worktree. Only code already on that branch is assumed: `runtime/engine.py` with `_run`/`_collect`/`_forget`, `runtime/checkpoint.py`, `store.Checkpoint` and `Store.save_checkpoint`/`latest_checkpoint`, and `tests/runtime/conftest.py`.

## Global Constraints

- Source change is limited to `src/agent_manager/runtime/engine.py`; the only new file is `tests/runtime/test_resume.py`. No edits to `store.py`, `runtime/checkpoint.py`, `runtime/compile.py`, `cli.py`, `orchestrate.py` or `integration.py`.
- `agent.run()` is always consumed to the end (`async for _ in agent.run(): pass` inside the unchanged `_run`), never broken or returned out of.
- `C.compile_workflow(workflow)` runs before `Agent.from_dict` (tool names carry the digest prefix; `from_dict` resolves tools by name from `ToolRegistry`).
- The digest check happens before any agent is built and before `run()`: on refusal no checkpoint row, phase row, attempt row or journal line is written.
- Stale-agent cleanup uses the existing pop-and-tolerate-missing `_forget` (pre-0.7.0 style, `AgentRegistry._registry.pop(name, None)`); the `AgentRegistry.unregister` migration (decision A2) is not on this base and is not this card's job.
- `SubtaskSummary`, journal lines, phase/attempt rows, escalation payloads and final subtask status keep the fresh run's shapes (G10).
- Only `src/agent_manager/runtime/` imports pygents.
- No CLI command, no exit-code mapping, no `--engine` flag, no orphan-attempt marking here (siblings 7fdec762 and 02890d5d).
- Tests sit in the Engine tier under `tests/runtime/` (mirroring `runtime/engine.py`), are not marked `e2e`, and rely on the autouse `fresh_pygents` fixture in `tests/runtime/conftest.py` for isolation.
- Verification: `uv run pytest` (whole default suite green). There is no separate lint or typecheck command.

**Deviation from the spec's test 1 wording, on purpose:** the spec says the fake runner raises `KeyboardInterrupt`. The compiled phase tools are async generators, and pygents runs them in a producer `asyncio` task (`pygents/turn.py:302-313`). asyncio re-raises `KeyboardInterrupt`/`SystemExit` straight out of the event loop from whichever task raised it (`Task.__step`), before the consuming task unwinds. At that point the test would be exercising asyncio's shutdown path instead of the engine. The tests therefore raise `_Crash(BaseException)`, a plain `BaseException` subclass. It is still not an `Exception`, so neither `_run` nor pygents catches it, and it is the same stand-in that `tests/test_engine.py:2101` (`_Abort`) already uses for the pygents engine. The spec's requirement is that a `BaseException` passes through and leaves the last `turn` row standing, and that requirement is what the tests check.

## Review Focus

1. A resume in a new process: `ToolRegistry`, `AgentRegistry` and the compile cache are all empty, and the workflow object is rebuilt with fresh closures (same digest). The resume should still work, because the compile runs before `from_dict` and the rebuilt workflow's own callables are the ones called. Pinned by `test_a_resume_in_a_fresh_process_uses_the_rebuilt_workflow` (Task 1).
2. A stale `AgentRegistry` entry under the checkpointed agent's name, left by a process that died before its `finally` ran. The resume should drop it and proceed, with no `ValueError`. Pinned by `test_a_stale_registry_entry_does_not_block_a_resume` (Task 2).
3. A refused resume should leave the card runnable. After `CheckpointMismatch`, a fresh run of the same card in the same process should succeed with no `ValueError`, because the refusal happens before any agent is registered. Pinned by `test_a_refused_resume_leaves_the_card_runnable` (Task 2).
4. Resuming from a `done` checkpoint (empty queue, no current turn). Nothing should be re-dispatched, and the summary should be `done` with the pooled results. Pinned by `test_resuming_a_done_checkpoint_dispatches_nothing` (Task 1).
5. Resuming a parked checkpoint while the run's stop is still set. It should park again before the same phase and dispatch nothing, with status `stopped` and a new `parked` row. Pinned by `test_a_resume_with_the_stop_still_set_parks_again` (Task 1).

Out of scope, noted for sibling card 02890d5d: an `escalated` checkpoint is saved after the failing turn has already left the queue, so resuming one as-is would dispatch nothing and finish `done`. Choosing which checkpoint `am resume` feeds in belongs to 02890d5d. This card does not change it.

---

### Task 1: Resume path in `_drive` (`resume_from`)

**Files:**
- Modify: `src/agent_manager/runtime/engine.py:11-115` (imports, `run_subtask` signature and docstring, `_drive` signature and body)
- Test: `tests/runtime/test_resume.py` (new)

**Interfaces:**
- Consumes: `store.Checkpoint` (dataclass in `src/agent_manager/store.py:507`, fields `run_id, card_id, seq, workflow, digest, reason, agent: dict, saved_at`), `Store.latest_checkpoint(card_id) -> Checkpoint | None`, `pygents.Agent.from_dict(data: dict) -> Agent`, `C.compile_workflow(wf) -> Compiled`, the unchanged `_run(agent, deps)` and `_forget(name)`.
- Produces: `run_subtask(workflow, store, *, story_id, subtask, repo_dir, commands=(), card=None, parent_story=None, extra_context=None, agent_runner=None, clock=old._utcnow, should_stop=None, resume_from: Checkpoint | None = None) -> old.SubtaskSummary`; `_drive(..., should_stop, resume_from)` with the same keyword. Task 2 edits the `else` branch of `_drive` that this task adds.

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime/test_resume.py`:

```python
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

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import AgentRegistry, ToolRegistry

from agent_manager import models, store as store_module
from agent_manager.errors import AgentPhaseFailed
from agent_manager.runtime import compile as compile_mod
from agent_manager.runtime import engine as runtime_engine
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


def _five(ran: list[str], crash_in: set[str]) -> Workflow:
    """Steps a..e. Each appends its name to `ran`; a step named in `crash_in`
    raises `_Crash` once (the name is discarded), so a resume runs it cleanly."""

    def make(name: str):
        def run(card: str) -> dict[str, Any]:
            ran.append(name)
            if name in crash_in:
                crash_in.discard(name)
                raise _Crash(f"killed in {name}")
            return {name: name.upper()}

        return run

    return Workflow("five", tuple(Step(name, make(name)) for name in FIVE))


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
    ran: list[str] = []
    wf = _five(ran, set())

    parked_summary = _go(wf, store, should_stop=lambda: ran == ["a"])

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
    ran: list[str] = []
    wf = _five(ran, set())
    _go(wf, store, should_stop=lambda: ran == ["a"])
    parked = store.latest_checkpoint(CARD_ID)

    summary = _go(wf, store, resume_from=parked, should_stop=lambda: True)

    assert ran == ["a"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before b"
    assert summary.results == {"a": {"a": "A"}}
    newest = store.latest_checkpoint(CARD_ID)
    assert (newest.seq, newest.reason) == (parked.seq + 1, "parked")
    assert _head(newest.agent) == "b"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: every test that passes `resume_from=` FAILs with `TypeError: run_subtask() got an unexpected keyword argument 'resume_from'`. That is all seven tests, since each one resumes.

- [ ] **Step 3: Add the `TYPE_CHECKING` import for `Checkpoint`**

In `src/agent_manager/runtime/engine.py`, replace:

```python
from pathlib import Path
from typing import Any

from pygents import Agent, AgentRegistry, ContextPool, ContextQueue
```

with:

```python
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pygents import Agent, AgentRegistry, ContextPool, ContextQueue
```

and replace:

```python
from agent_manager.workflow.phases import Workflow
```

with:

```python
from agent_manager.workflow.phases import Workflow

if TYPE_CHECKING:
    # Annotation only (the module has `from __future__ import annotations`);
    # the name `store` is taken by `run_subtask`'s parameter.
    from agent_manager.store import Checkpoint
```

- [ ] **Step 4: Add `resume_from` to `run_subtask` and rewrite its docstring**

Replace the whole `run_subtask` function (lines 28-66) with:

```python
def run_subtask(
    workflow: Workflow,
    store: Any,
    *,
    story_id: str,
    subtask: Any,
    repo_dir: Path,
    commands: Sequence[str] = (),
    card: Any = None,
    parent_story: Any = None,
    extra_context: Mapping[str, Any] | None = None,
    agent_runner: Any = None,
    clock: Callable[[], Any] = old._utcnow,
    should_stop: Callable[[], bool] | None = None,
    resume_from: Checkpoint | None = None,
) -> old.SubtaskSummary:
    """Walk `workflow`'s phases for one subtask on pygents. One `asyncio.run`.

    Every turn is saved as a `turn` checkpoint before it runs, and the run ends
    with a `done` or `escalated` one (`runtime/checkpoint.py`). `should_stop` is
    asked before every turn: once it answers true, a `parked` checkpoint is
    saved, the next phase is not started, and the subtask is recorded
    `stopped before <phase>`.

    `resume_from` continues from a saved checkpoint instead of the first phase:
    the agent is rebuilt from it, so the pool (seed and earlier results) and
    the queue (the pending turn and its loop count) are the checkpoint's, and
    no seed or first turn is added. A checkpoint saved under another workflow
    digest is refused with `CheckpointMismatch` before anything runs or is
    recorded.
    """
    return asyncio.run(
        _drive(
            workflow,
            store,
            story_id=story_id,
            subtask=subtask,
            repo_dir=repo_dir,
            commands=commands,
            card=card,
            parent_story=parent_story,
            extra_context=extra_context,
            agent_runner=agent_runner,
            clock=clock,
            should_stop=should_stop,
            resume_from=resume_from,
        )
    )
```

- [ ] **Step 5: Build the agent from the checkpoint in `_drive`**

In `_drive`'s signature, replace:

```python
    clock: Callable[[], Any],
    should_stop: Callable[[], bool] | None,
) -> old.SubtaskSummary:
    # The binding, built and refused exactly as the old engine builds it:
```

with:

```python
    clock: Callable[[], Any],
    should_stop: Callable[[], bool] | None,
    resume_from: Checkpoint | None,
) -> old.SubtaskSummary:
    # The binding, built and refused exactly as the old engine builds it:
```

Then replace the tail of `_drive`:

```python
    compiled = C.compile_workflow(workflow)
    agent = Agent(
        f"{getattr(store, 'run_id', 'run')}:{subtask.card_id}",
        workflow.name,
        [compiled.agent_phase, compiled.step_phase],
        context_pool=ContextPool(),
        context_queue=ContextQueue(limit=10),
        tags=["subtask"],
    )
    try:
        await agent.context_pool.add(context.seed_item(binding))
        await agent.put(compiled.first_turn())
        deps = RunDeps(workflow, store, story_id, subtask, agent_runner, clock, should_stop)
        return await _run(agent, deps)
    finally:
        _forget(agent.name)
```

with:

```python
    # Compiled first on both paths: it registers the digest-prefixed tools
    # that `Agent.from_dict` below resolves by name from `ToolRegistry`.
    compiled = C.compile_workflow(workflow)
    if resume_from is None:
        agent = Agent(
            f"{getattr(store, 'run_id', 'run')}:{subtask.card_id}",
            workflow.name,
            [compiled.agent_phase, compiled.step_phase],
            context_pool=ContextPool(),
            context_queue=ContextQueue(limit=10),
            tags=["subtask"],
        )
    else:
        # The pool (seed, earlier results) and the queue (pending turn, loop
        # count) come from the checkpoint: no seed item, no first turn.
        agent = Agent.from_dict(resume_from.agent)
    try:
        if resume_from is None:
            await agent.context_pool.add(context.seed_item(binding))
            await agent.put(compiled.first_turn())
        deps = RunDeps(workflow, store, story_id, subtask, agent_runner, clock, should_stop)
        return await _run(agent, deps)
    finally:
        _forget(agent.name)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 7: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS, with no failures (the non-e2e `tests/e2e` wiring tests included).

- [ ] **Step 8: Commit**

```bash
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-resume-a-subtask-from-5698e4f6 add src/agent_manager/runtime/engine.py tests/runtime/test_resume.py
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-resume-a-subtask-from-5698e4f6 commit -m "feat(runtime): resume a subtask from its checkpoint

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```

---

### Task 2: Refuse a changed workflow and drop a stale agent entry

**Files:**
- Modify: `src/agent_manager/runtime/engine.py` (new `CheckpointMismatch` class after the imports; the `else` branch Task 1 added to `_drive`)
- Test: `tests/runtime/test_resume.py` (append three tests and one import)

**Interfaces:**
- Consumes: from Task 1, `run_subtask(..., resume_from=...)` and `_drive`'s `else:` branch, which currently is:
  ```python
      else:
          # The pool (seed, earlier results) and the queue (pending turn, loop
          # count) come from the checkpoint: no seed item, no first turn.
          agent = Agent.from_dict(resume_from.agent)
  ```
  Also the existing `_forget(name: str) -> None` (pops `AgentRegistry._registry`, tolerating a missing name) and `Workflow.digest() -> str`.
- Produces: `runtime.engine.CheckpointMismatch(Exception)`, raised by `run_subtask` when `resume_from.digest != workflow.digest()`. Its message contains both digests in full. Sibling card 02890d5d maps it to exit 3.

- [ ] **Step 1: Write the failing tests**

In `tests/runtime/test_resume.py`, replace:

```python
from pygents import AgentRegistry, ToolRegistry
```

with:

```python
from pygents import Agent, AgentRegistry, ToolRegistry
```

Then append to the end of the file:

```python
def _extra(card: str) -> dict[str, Any]:
    return {"f": "F"}


def test_a_changed_workflow_is_refused(store):
    ran: list[str] = []
    wf = _five(ran, set())
    _go(wf, store, should_stop=lambda: ran == ["a"])
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
    ran: list[str] = []
    wf = _five(ran, set())
    _go(wf, store, should_stop=lambda: ran == ["a"])
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: the 7 Task 1 tests PASS. `test_a_changed_workflow_is_refused` and `test_a_refused_resume_leaves_the_card_runnable` FAIL with `AttributeError: module 'agent_manager.runtime.engine' has no attribute 'CheckpointMismatch'`. `test_a_stale_registry_entry_does_not_block_a_resume` FAILs with `ValueError: 'run-2026-09-26-04:5698e4f6' already registered`.

- [ ] **Step 3: Add `CheckpointMismatch`**

In `src/agent_manager/runtime/engine.py`, replace:

```python
if TYPE_CHECKING:
    # Annotation only (the module has `from __future__ import annotations`);
    # the name `store` is taken by `run_subtask`'s parameter.
    from agent_manager.store import Checkpoint
```

with:

```python
if TYPE_CHECKING:
    # Annotation only (the module has `from __future__ import annotations`);
    # the name `store` is taken by `run_subtask`'s parameter.
    from agent_manager.store import Checkpoint


class CheckpointMismatch(Exception):
    """A checkpoint saved under another version of the workflow: its digest is
    not the digest of the workflow asked to resume it. Raised before any agent
    is built, so nothing is run or recorded."""
```

- [ ] **Step 4: Check the digest and drop a stale entry before `from_dict`**

In `_drive`, replace:

```python
    else:
        # The pool (seed, earlier results) and the queue (pending turn, loop
        # count) come from the checkpoint: no seed item, no first turn.
        agent = Agent.from_dict(resume_from.agent)
```

with:

```python
    else:
        digest = workflow.digest()
        if resume_from.digest != digest:
            raise CheckpointMismatch(
                f"checkpoint {resume_from.card_id}#{resume_from.seq} was saved under "
                f"digest {resume_from.digest}, but workflow {workflow.name!r} "
                f"has digest {digest}"
            )
        # A run that died before its `finally` may have left its agent
        # registered under this name; `from_dict` would be refused it.
        _forget(resume_from.agent["name"])
        # The pool (seed, earlier results) and the queue (pending turn, loop
        # count) come from the checkpoint: no seed item, no first turn.
        agent = Agent.from_dict(resume_from.agent)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 6: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS, with no failures (the non-e2e `tests/e2e` wiring tests included).

- [ ] **Step 7: Commit**

```bash
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-resume-a-subtask-from-5698e4f6 add src/agent_manager/runtime/engine.py tests/runtime/test_resume.py
git -C /home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-resume-a-subtask-from-5698e4f6 commit -m "feat(runtime): refuse a checkpoint from another workflow digest

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```
