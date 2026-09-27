<!-- task-pipeline: validated -->
# Add StopSignal and park subtasks through ON_PAUSE (card 364babde)

Parent story: e90a2247 "Groundwork: the stop, an awaitable driver, multi-blocker roots". Plan: `docs/superpowers/plans/2026-09-25-supervisor-tree.md`, Task 1.1. Design: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`, Decision T5.

## Scope

In scope:

- New `src/agent_manager/runtime/stop.py` with `StopSignal`.
- `runtime/checkpoint.py`: a new module-level `ON_PAUSE` hook `on_pause`, tagged `subtask`, beside the existing `before_turn`.
- `runtime/engine.py`: rename `_drive` to public `run_subtask_async`, and add a `stop: StopSignal | None = None` keyword to it and to `run_subtask`. `run_subtask` stays the `asyncio.run(run_subtask_async(...))` wrapper. The agent is registered with `stop` before the run and unregistered in a `finally`.
- `runtime/state.py`: `RunDeps` gains `stop: StopSignal | None = None`.
- Tests: the new `tests/runtime/test_stop.py`, plus one test added to the existing `tests/runtime/test_stop_bridge.py`.

Out of scope. Each item belongs to a sibling card or a later task:

- `cli.py`, including `drive_subtask_async`. That is card 9b944409 (Task 1.2).
- `dag.py`, `orchestrate.py`, `pyproject.toml` and the grafo dependency. That is card 1693e86e (Task 1.3).
- Removing `should_stop`, the `BEFORE_TURN` stop branch, or `RunDeps.should_stop`. That is Task 3.3. All three stay and must keep working, so `orchestrate.py`'s thread runner is unaffected.
- Switching `_forget` to `AgentRegistry.unregister`. The pre-0.7.0 workaround stays as it is.

## Observable behavior

`StopSignal` is one per run and lives on the run's single event loop. It is a plain class with no locking.

- `triggered: bool` starts as `False`. `primary: str | None` starts as `None`.
- `trigger(story_id) -> bool`:
  - The first call sets `triggered = True` and `primary = story_id`, then returns `True`.
  - Later calls return `False` and leave `primary` unchanged.
  - Every call, first or not, calls `.pause()` on every currently registered agent. It iterates over a copy of the registered set.
- `register(agent)` adds the agent to the set. If the signal is already triggered, it calls `agent.pause()` straight away.
- `unregister(agent)` removes the agent. Calling it with an agent that is not registered is a no-op and does not raise.
- `stop.py` imports nothing from pygents. It depends only on the agent having a `.pause()` method, so any object with `.pause()` works (the tests use a `FakeAgent`).

`on_pause(agent)` is registered with `@hook(AgentHook.ON_PAUSE, tags={"subtask"})`. It is module-level, not a closure, because pygents' `HookRegistry` is keyed on the function name.

- If `current_run` is unset, it returns without doing anything.
- Otherwise it reads `head = agent.to_dict()["queue"][0]["kwargs"]["phase"]`, calls `save(agent, "parked")` and raises `Parked(head)`.
- The raise is what ends the run. On its own, pygents' `pause()` only clears the internal pause event, and `run()` would then wait forever on `_pause_event.wait()`.
- pygents fires `ON_PAUSE` at the top of its `while` loop, between turns, so a turn that is already in flight finishes first. At that point `current_turn` is `None` and the next phase is `queue[0]`.
- The loop condition is checked before the pause gate. So if a pause lands after the last phase has finished and the queue is empty, no `ON_PAUSE` fires and the subtask ends `done` as usual.

Engine:

- `run_subtask_async` has `_drive`'s signature and body, plus `stop`. `run_subtask` forwards `stop`.
- `stop` is passed into `RunDeps`. Add the field after `should_stop` so the existing positional construction still binds correctly.
- In `_run`, when `deps.stop` is set:
  - call `stop.register(agent)` immediately before `agent.run()` is consumed;
  - call `stop.unregister(agent)` in the `finally`, so it runs on every exit path (done, parked, escalated, EngineError, BaseException).
- The existing `except checkpoint.Parked` branch handles the pause path unchanged:
  - no extra checkpoint row is written, so `parked` stays the newest;
  - the summary's `status` is `"stopped"` and its `detail` is `"stopped before <phase>"`.
- `CheckpointMismatch`, the reserved `extra_context` refusal and `_forget` behave exactly as before. A mismatch is still raised before any agent exists and before any registration.

Without a `stop` argument, behavior is byte-identical to today. The `should_stop` path, with the `BEFORE_TURN` hook saving `parked` and raising `Parked`, keeps working.

## Error paths

- `trigger` or `register` with no agents registered: no error.
- `unregister` of an unknown agent: no error.
- `on_pause` with no `current_run` (an agent tagged `subtask` driven outside the engine): no-op. The agent then stays paused, which is the caller's concern.
- An exception or cancellation inside `_run` still unregisters the agent, so a later `trigger` never calls `.pause()` on an agent whose run has finished.

## Tests

Test-placement rule: this repo does not sort tests into unit and integration tiers. Tests mirror the source tree under `tests/<module>/` (CLAUDE.md). The only tier with its own name is `tests/e2e/`, which holds the slow opt-in real-harness test. These are runtime-module tests, so all of them go in `tests/runtime/`. None of them goes in `tests/e2e/`.

`tests/runtime/test_stop.py` is a new file. Its bodies are copied verbatim from the plan's Task 1.1 Step 1 and use a `FakeAgent` that counts `.pause()` calls:

1. `test_first_trigger_is_primary`: the first `trigger("A")` returns `True`, the second `trigger("B")` returns `False`, and afterwards `primary == "A"` and `triggered` is true.
2. `test_trigger_pauses_registered_agents_and_late_registrations`: an agent registered before the trigger is paused once, and an agent registered after the trigger is paused once when it registers.
3. `test_unregistered_agents_are_not_paused`: register, unregister, then trigger; the pause count is 0.

`tests/runtime/test_stop_bridge.py` gets one new test. The existing M6 `should_stop` tests stay as they are, because they cover the path that remains until Task 3.3:

4. Two subtasks, A and B, run concurrently on one loop:
   - Both use the existing fake runner/store fixtures and run `run_subtask_async(..., stop=stop)` with one shared `StopSignal`, gathered on the same loop.
   - A's fake step calls `stop.trigger("A")` while B's current phase is in flight.
   - Ordering comes from `threading.Event`s that the fake steps block on, never from sleeps. Steps run off the loop through `bridge.call_step`'s `asyncio.to_thread`, so the synchronization primitive must be thread-safe, not `asyncio.Event` (which cannot be waited on synchronously from the worker thread); this is the same pattern the existing `test_stop_bridge.py`, `test_bridge.py` and `test_compile.py` already use.
   - Assertions for B:
     - its newest checkpoint has reason `parked`;
     - that checkpoint's stored agent has B's next phase at `queue[0]`;
     - its `SubtaskSummary.status == "stopped"`;
     - its `detail == "stopped before <that phase>"`.

Exit criterion: `uv run pytest` passes, with the whole default suite green, including `tests/e2e`'s default-collected parts and the existing M6 stop-bridge tests.

Note: the upstream exploration summary was cut off at its 8000-character cap in the middle of the test-placement paragraph. That means the upstream stage over-ran its brief. The placement rule above was confirmed against CLAUDE.md and the plan, which names both test files. It was not taken from the missing text.

---

# StopSignal and ON_PAUSE parking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a loop-local `StopSignal` that pauses every registered subtask agent, and make a paused pygents subtask agent save a `parked` checkpoint and end `stopped before <phase>` through a module-level `ON_PAUSE` hook, with the engine driver made public as `run_subtask_async`.

**Architecture:** `runtime/stop.py` holds a plain `StopSignal` (no pygents import) that calls `.pause()` on registered agents. `runtime/engine.py` registers the subtask agent with the signal right before `agent.run()` is consumed and unregisters it in `_run`'s `finally`; pygents then fires `ON_PAUSE` at the top of its next loop iteration, where `runtime/checkpoint.py`'s new `on_pause` hook saves `parked` and raises the existing `Parked`, which `_run` already turns into a `stopped` summary. The M6 `should_stop` / `BEFORE_TURN` path is left untouched.

**Tech Stack:** Python 3.12, pygents 0.7.0 (installed; `Agent.pause()`/`resume()`, `AgentHook.ON_PAUSE`), pytest with `asyncio_mode = "auto"`, `uv`.

**Spec:** `docs/superpowers/specs/task-add-stopsignal-and-park-364babde-design.md` (prepended above). Parent: `docs/superpowers/plans/2026-09-25-supervisor-tree.md` Task 1.1, `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` Decision T5.

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m7/task-add-stopsignal-and-park-364babde` on `m7/task-add-stopsignal-and-park-364babde`, cut fresh from `master`. No sibling card's code (no `cli.drive_subtask_async`, no grafo, no `dag.RootPlan`) exists on this branch; nothing below uses it. All paths below are relative to the worktree root.

## Global Constraints

- Do not touch `src/agent_manager/cli.py`, `src/agent_manager/dag.py`, `src/agent_manager/orchestrate.py` or `pyproject.toml`; do not add grafo.
- Keep `should_stop`, `RunDeps.should_stop` and the `BEFORE_TURN` stop branch in `checkpoint.before_turn` exactly working (removal is Task 3.3).
- Keep `_forget` (the pre-0.7.0 `AgentRegistry._registry.pop` workaround) and the `CheckpointMismatch` refusal unchanged; a mismatch is raised before any agent exists and before any registration.
- pygents hooks are module-level with `tags={"subtask"}`; never `break`/`return` out of `agent.run()`.
- `stop.py` imports nothing from pygents.
- `RunDeps.stop` is declared after `should_stop` so `RunDeps(workflow, store, story_id, subtask, agent_runner, clock, should_stop)` still binds positionally.
- No test sleeps to prove ordering; fake steps block on `threading.Event`s (steps run in `asyncio.to_thread` workers).
- All new tests live in `tests/runtime/` (module mirroring); none in `tests/e2e/`.
- Verification for every task: `uv run pytest` — the whole default suite green.
- Nothing is pushed.

## Review Focus

1. **Resuming a checkpoint parked by the `StopSignal`, with no stop this time.** pygents' `to_dict()` stores `"is_paused": True` for such a row and `Agent.from_dict` calls `agent.pause()` when it reads that flag, so without a fix the resumed agent hits `ON_PAUSE` at once and parks forever. Expected: it continues from the parked phase and ends `done`. Test in Task 3 (`tests/runtime/test_resume.py`).
2. **Resuming a `StopSignal`-parked checkpoint while the stop is still triggered.** Expected: it parks again before the same phase with a new `parked` row at `seq + 1` (the fix for item 1 must clear the stored pause before `_run` registers, not after). Test in Task 3.
3. **A stop already triggered before a subtask starts** (a lane that reaches its subtask after another lane stopped). Expected: `register` pauses at once, no phase runs, one `parked` row, `stopped before <first phase>`; also proves `run_subtask` forwards `stop`. Test in Task 2.
4. **Every exit path unregisters, and a refused resume never registers.** Done, escalated, `EngineError` and a `BaseException` crash each leave a `register`/`unregister` pair; a `CheckpointMismatch` leaves none. Expected: a later `trigger` never touches a finished agent. Test in Task 2.
5. **A stop triggered during the subtask's last phase.** The queue is empty when the loop re-checks, so no `ON_PAUSE` fires. Expected: the subtask ends `done` with a `done` row, not `stopped`. Test in Task 2.

---

## File map

| File | Change | Task |
|---|---|---|
| `src/agent_manager/runtime/stop.py` | Create: `StopSignal` | 1 |
| `tests/runtime/test_stop.py` | Create: the three verbatim `StopSignal` tests | 1 |
| `src/agent_manager/runtime/state.py` | Modify `RunDeps` (lines 18-32): add `stop` field after `should_stop` | 2 |
| `src/agent_manager/runtime/checkpoint.py` | Add module-level `on_pause` hook after `before_turn` (after line 64); docstring | 2 |
| `src/agent_manager/runtime/engine.py` | `_drive` (104-169) becomes `run_subtask_async` with defaults + `stop`; `run_subtask` (54-101) forwards `stop`; `_run` (172-219) registers/unregisters | 2 |
| `tests/runtime/test_stop_bridge.py` | Add the concurrent A/B test and Review Focus 3-5 tests | 2 |
| `tests/runtime/test_compile.py` | `_new_agent`'s `tags=["subtask"]` becomes `tags=["compile-unit"]` | 2 |
| `src/agent_manager/runtime/engine.py` | Resume branch (lines 156-161): `agent.resume()` after `Agent.from_dict` | 3 |
| `tests/runtime/test_resume.py` | Add Review Focus 1-2 tests | 3 |

---

### Task 1: `StopSignal`

**Files:**
- Create: `src/agent_manager/runtime/stop.py`
- Test: `tests/runtime/test_stop.py` (new; `tests/runtime/` mirrors `src/agent_manager/runtime/`)

**Interfaces:**
- Consumes: nothing.
- Produces: `agent_manager.runtime.stop.StopSignal` with `.triggered: bool` (initially `False`), `.primary: str | None` (initially `None`), `.trigger(story_id: str) -> bool`, `.register(agent: Any) -> None`, `.unregister(agent: Any) -> None`. Agents need only a `.pause()` method.

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime/test_stop.py` with exactly this content (verbatim from the parent plan's Task 1.1 Step 1):

```python
# tests/runtime/test_stop.py
import asyncio
from agent_manager.runtime.stop import StopSignal

class FakeAgent:
    def __init__(self): self.paused = 0
    def pause(self): self.paused += 1

def test_first_trigger_is_primary():
    stop = StopSignal()
    assert stop.trigger("A") is True and stop.trigger("B") is False
    assert stop.primary == "A" and stop.triggered

def test_trigger_pauses_registered_agents_and_late_registrations():
    stop, early, late = StopSignal(), FakeAgent(), FakeAgent()
    stop.register(early)
    stop.trigger("A")
    stop.register(late)
    assert early.paused == 1 and late.paused == 1

def test_unregistered_agents_are_not_paused():
    stop, agent = StopSignal(), FakeAgent()
    stop.register(agent); stop.unregister(agent); stop.trigger("A")
    assert agent.paused == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_stop.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'agent_manager.runtime.stop'`.

- [ ] **Step 3: Write the implementation**

Create `src/agent_manager/runtime/stop.py`:

```python
"""The milestone's cooperative stop (supervisor-tree design T5).

One `StopSignal` per run, living on the run's one event loop, so it takes no
lock. `trigger` records the first caller as `primary` and pauses every
registered subtask agent; `register` pauses an agent at once if the signal
has already fired. A paused pygents agent fires `ON_PAUSE` before its next
turn, where `runtime/checkpoint.py`'s `on_pause` saves `parked` and raises
`Parked`.

Nothing here imports pygents: an agent is anything with a `.pause()`.
"""

from __future__ import annotations

from typing import Any


class StopSignal:
    def __init__(self) -> None:
        self.triggered = False
        self.primary: str | None = None
        self._agents: set[Any] = set()

    def trigger(self, story_id: str) -> bool:
        """Pause every registered agent; True only for the first caller, who becomes `primary`."""
        first = not self.triggered
        if first:
            self.triggered, self.primary = True, story_id
        for agent in list(self._agents):
            agent.pause()
        return first

    def register(self, agent: Any) -> None:
        """Track `agent`; pause it at once if the signal already fired."""
        self._agents.add(agent)
        if self.triggered:
            agent.pause()

    def unregister(self, agent: Any) -> None:
        """Stop tracking `agent`. A no-op for an agent never registered."""
        self._agents.discard(agent)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_stop.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all passed (nothing else imports `stop.py` yet).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/runtime/stop.py tests/runtime/test_stop.py
git commit -m "$(cat <<'EOF'
feat(runtime): add StopSignal

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
)"
```

---

### Task 2: Park subtask agents through `ON_PAUSE`; public `run_subtask_async` with `stop`

**Files:**
- Modify: `src/agent_manager/runtime/state.py:11-32`
- Modify: `src/agent_manager/runtime/checkpoint.py:1-19` (docstring), append after line 64
- Modify: `src/agent_manager/runtime/engine.py:12-23` (imports), `:54-169` (`run_subtask`, `_drive`), `:172-219` (`_run`)
- Test: `tests/runtime/test_stop_bridge.py` (existing; extend, do not replace the two M6 tests)
- Modify: `tests/runtime/test_compile.py` (`_new_agent`'s tag; see Step 6a below)

**Interfaces:**
- Consumes: `StopSignal` from Task 1 (`trigger`, `register`, `unregister`, `triggered`, `primary`).
- Produces:
  - `agent_manager.runtime.engine.run_subtask_async(workflow, store, *, story_id, subtask, repo_dir, commands=(), card=None, parent_story=None, extra_context=None, agent_runner=None, clock=walk._utcnow, should_stop=None, stop: StopSignal | None = None, resume_from=None) -> walk.SubtaskSummary` (async; public; same defaults as `run_subtask` so card 9b944409 can call it with keywords only).
  - `agent_manager.runtime.engine.run_subtask(..., stop: StopSignal | None = None, ...)` = `asyncio.run(run_subtask_async(...))`.
  - `RunDeps.stop: StopSignal | None = None`, declared right after `should_stop`.
  - `agent_manager.runtime.checkpoint.on_pause(agent) -> None` (async, module-level `ON_PAUSE` hook, `tags={"subtask"}`).
  - `engine._drive` no longer exists (no caller in `src/` or `tests/` uses it; `tests/runtime/test_compile.py` has its own unrelated local `_drive` helper).

- [ ] **Step 1: Write the failing tests**

In `tests/runtime/test_stop_bridge.py`, replace the module docstring and imports (lines 1-18) with:

```python
"""The run's stop, bridged onto the pygents engine (pygents-engine design G8;
supervisor-tree design T5).

Runtime tier: `runtime.engine.run_subtask` / `run_subtask_async` over fake
steps and a real temp store. Two stops are covered. The M6 one is a plain
`threading.Event`, as orchestrate.py's `RunStop` is; the engine only ever
calls its `is_set` (kept until Task 3.3). The M7 one is a `StopSignal`, which
pauses the registered pygents agent; its `ON_PAUSE` hook parks the subtask.

Steps run off the loop in `asyncio.to_thread` workers, so a step that fires
the `StopSignal` hands `trigger` to the loop with `call_soon_threadsafe`
(the signal lives on the loop), and ordering between steps uses
`threading.Event`s, never sleeps.
"""

import asyncio
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import models, store as store_module
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.stop import StopSignal
from agent_manager.workflow.phases import AgentPhase, Step, Workflow
```

Replace the `_subtask` helper (lines 36-43) with a version taking an optional card id (the existing tests call it with no argument and are unaffected):

```python
def _subtask(card_id: str = CARD_ID) -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card_id,
        branch=f"m6/task-checkpoint-every-turn-{card_id}",
        base_branch="m6/story-base",
        status="started",
        worktree_path=Path("/w"),
    )
```

Then append at the end of the file:

```python
# ── StopSignal (supervisor-tree T5, card 364babde) ───────────────────────────

A_CARD = "aaaa0001"
B_CARD = "bbbb0002"
AGENT_NAME = f"{RUN_ID}:{CARD_ID}"


def _async_go(workflow: Workflow, opened, card_id: str, stop: StopSignal):
    return runtime_engine.run_subtask_async(
        workflow,
        opened,
        story_id=STORY_ID,
        subtask=_subtask(card_id),
        repo_dir=REPO,
        clock=lambda: FIXED,
        stop=stop,
    )


async def test_a_trigger_from_one_subtask_parks_another_mid_phase(store):
    loop = asyncio.get_running_loop()
    stop = StopSignal()
    b_in, fired = threading.Event(), threading.Event()
    ran: list[str] = []

    def fire() -> None:
        # On the loop, where the signal lives.
        stop.trigger("A")
        fired.set()

    def a1(card: str) -> dict[str, Any]:
        ran.append("a1")
        if not b_in.wait(5):
            raise RuntimeError("B's first phase never started")
        loop.call_soon_threadsafe(fire)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")
        return {"a1": 1}

    def a2(card: str) -> dict[str, Any]:
        ran.append("a2")
        return {"a2": 2}

    def b1(card: str) -> dict[str, Any]:
        ran.append("b1")
        b_in.set()
        # B's phase stays in flight until A has triggered the stop.
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")
        return {"b1": 1}

    def b2(card: str) -> dict[str, Any]:
        ran.append("b2")
        return {"b2": 2}

    a_summary, b_summary = await asyncio.gather(
        _async_go(Workflow("stop_a", (Step("a1", a1), Step("a2", a2))), store, A_CARD, stop),
        _async_go(Workflow("stop_b", (Step("b1", b1), Step("b2", b2))), store, B_CARD, stop),
    )

    assert stop.primary == "A"
    assert sorted(ran) == ["a1", "b1"]
    # B: its in-flight phase finished, then it parked before the next one.
    assert b_summary.status == "stopped"
    assert b_summary.detail == "stopped before b2"
    assert b_summary.results == {"b1": {"b1": 1}}
    newest = store.latest_checkpoint(B_CARD)
    assert newest.reason == "parked"
    assert newest.agent["current_turn"] is None
    assert newest.agent["queue"][0]["kwargs"]["phase"] == "b2"
    # A was registered too, so the trigger parks it as well.
    assert a_summary.status == "stopped"
    assert a_summary.detail == "stopped before a2"
    assert store.latest_checkpoint(A_CARD).reason == "parked"


def test_a_stop_triggered_before_the_run_parks_before_the_first_phase(store):
    # Review Focus 3; also proves `run_subtask` forwards `stop`.
    stop = StopSignal()
    stop.trigger("elsewhere")
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    summary = runtime_engine.run_subtask(
        Workflow("late", (Step("a", a),)),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        stop=stop,
    )

    assert ran == []
    assert summary.status == "stopped"
    assert summary.detail == "stopped before a"
    assert summary.results == {}
    assert _reasons(store) == [(0, "parked")]
    newest = store.latest_checkpoint(CARD_ID)
    assert newest.agent["queue"][0]["kwargs"]["phase"] == "a"
    assert stop.primary == "elsewhere"


async def test_a_stop_triggered_in_the_last_phase_still_ends_done(store):
    # Review Focus 5: the queue is empty when the loop re-checks, so no ON_PAUSE.
    loop = asyncio.get_running_loop()
    stop = StopSignal()
    fired = threading.Event()

    def fire() -> None:
        stop.trigger("A")
        fired.set()

    def a(card: str) -> dict[str, Any]:
        return {"a": 1}

    def last(card: str) -> dict[str, Any]:
        loop.call_soon_threadsafe(fire)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")
        return {"last": 2}

    summary = await _async_go(
        Workflow("ends", (Step("a", a), Step("last", last))), store, CARD_ID, stop
    )

    assert stop.triggered
    assert summary.status == "done"
    assert summary.results == {"a": {"a": 1}, "last": {"last": 2}}
    assert _reasons(store) == [(0, "turn"), (1, "turn"), (2, "done")]


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


class _Crash(BaseException):
    """A process death mid-phase: not an `Exception`, so nothing may catch it."""


def _spy_go(workflow: Workflow, opened, spy: _Spy, **kwargs: Any):
    return runtime_engine.run_subtask(
        workflow,
        opened,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        stop=spy,
        **kwargs,
    )


PAIR = [("register", AGENT_NAME), ("unregister", AGENT_NAME)]


def _ok(card: str) -> dict[str, Any]:
    return {"ok": 1}


def _boom(card: str) -> dict[str, Any]:
    raise RuntimeError("boom")


def _die(card: str) -> dict[str, Any]:
    raise _Crash("killed")


def test_a_done_run_unregisters_its_agent(store):
    # Review Focus 4.
    spy = _Spy()
    assert _spy_go(Workflow("fine", (Step("a", _ok),)), store, spy).status == "done"
    assert spy.calls == PAIR


def test_an_escalated_run_unregisters_its_agent(store):
    spy = _Spy()
    summary = _spy_go(Workflow("fails", (Step("a", _boom),)), store, spy)
    assert summary.status == "escalated"
    assert spy.calls == PAIR


def test_an_engine_error_unregisters_its_agent(store):
    # An agent phase with no injected runner is a wiring bug raised as EngineError.
    spy = _Spy()
    with pytest.raises(EngineError):
        _spy_go(Workflow("unwired", (AgentPhase("spec", "spec_author", (), None),)), store, spy)
    assert spy.calls == PAIR


def test_a_crash_unregisters_its_agent(store):
    spy = _Spy()
    with pytest.raises(_Crash):
        _spy_go(Workflow("dies", (Step("a", _die),)), store, spy)
    assert spy.calls == PAIR


def test_a_refused_resume_never_registers(store):
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    wf = Workflow("refused", (Step("a", a), Step("b", _ok)))
    runtime_engine.run_subtask(
        wf, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO,
        clock=lambda: FIXED, should_stop=lambda: ran == ["a"],
    )
    parked = store.latest_checkpoint(CARD_ID)
    changed = Workflow("refused", wf.phases + (Step("c", _ok),))
    spy = _Spy()

    with pytest.raises(runtime_engine.CheckpointMismatch):
        _spy_go(changed, store, spy, resume_from=parked)

    assert spy.calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_stop_bridge.py -v`
Expected: the two existing M6 tests PASS; every new test FAILS — the async ones with `AttributeError: module 'agent_manager.runtime.engine' has no attribute 'run_subtask_async'`, the sync ones with `TypeError: run_subtask() got an unexpected keyword argument 'stop'`.

- [ ] **Step 3: Add `RunDeps.stop`**

In `src/agent_manager/runtime/state.py`, replace lines 11-32 (from `from __future__ import annotations` through the end of the `RunDeps` class) with:

```python
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_manager.runtime.stop import StopSignal


@dataclass
class RunDeps:
    workflow: Any
    store: Any
    story_id: str
    subtask: Any
    agent_runner: Callable[..., Any] | None
    clock: Callable[[], Any]
    should_stop: Callable[[], bool] | None = None
    stop: StopSignal | None = None
    """The milestone's `StopSignal` (supervisor-tree T5). Declared after
    `should_stop` so the positional construction still binds; `should_stop`
    stays until Task 3.3."""
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    running: str | None = None
    """The phase whose tool was entered last. pygents clears the agent's
    `current_turn` before an error leaves `run()`, so the engine reads the
    phase an unexpected error escaped from here instead."""
```

- [ ] **Step 4: Add the `on_pause` hook**

In `src/agent_manager/runtime/checkpoint.py`, replace the module docstring (lines 1-19) with:

```python
"""Checkpoint hooks (pygents-engine design G5, G8, §6; supervisor-tree T5).

Every turn of a subtask's agent is saved as `Agent.to_dict()` in the store's
`checkpoints` table *before* it runs, and the run's cooperative stop is read
at the same moment: a set stop saves `parked` and raises `Parked`, which
propagates out of `agent.run()` and ends it cleanly -- nothing breaks or
returns out of the loop. The after-run rows (`done`, `escalated`) are
written by `runtime/engine.py` through `save`.

There are two stops until Task 3.3. The M6 one is `RunDeps.should_stop`,
read by `before_turn`. The M7 one is a `StopSignal` (`runtime/stop.py`) that
pauses the agent; pygents then fires `ON_PAUSE` at the top of its loop,
between turns, and `on_pause` saves `parked` and raises `Parked`. The raise
is what ends the run: `pause()` alone would leave `run()` waiting forever
for a `resume()`.

The hooks are module-level on purpose: pygents' `HookRegistry` is process-wide
and keyed on the function's name, and closures from one factory collide in
it (design §11). They are global and tagged `subtask`, so they fire for every
agent tagged `subtask`; they find their run through `state.current_run` and
do nothing when no run is set.

`saved_at` is read from the wall clock, never from the run's injected
`clock`: that clock stamps phase rows, and a checkpoint reading it would
shift every phase-row stamp after it (G10).
"""
```

Then append after `before_turn` (after line 64):

```python


@hook(AgentHook.ON_PAUSE, tags={"subtask"})
async def on_pause(agent: Any) -> None:
    deps = current_run.get(None)
    if deps is None:
        return
    # ON_PAUSE fires between turns, so no turn is in flight: the next phase
    # is the queue head.
    head = agent.to_dict()["queue"][0]["kwargs"]["phase"]
    save(agent, "parked")
    raise Parked(head)
```

- [ ] **Step 5: Make the driver public with `stop`, and register around the run**

In `src/agent_manager/runtime/engine.py`:

(a) Replace the import block lines 19-23 with:

```python
from agent_manager.runtime import walk
from agent_manager.runtime import checkpoint  # registers the BEFORE_TURN and ON_PAUSE hooks
from agent_manager.runtime import compile as C
from agent_manager.runtime import context
from agent_manager.runtime.state import RunDeps, current_run
from agent_manager.runtime.stop import StopSignal
from agent_manager.workflow.phases import Workflow
```

(b) Replace `run_subtask` and `_drive` (lines 54-169, from `def run_subtask(` through `        _forget(agent.name)`) with:

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
    clock: Callable[[], Any] = walk._utcnow,
    should_stop: Callable[[], bool] | None = None,
    stop: StopSignal | None = None,
    resume_from: Checkpoint | None = None,
) -> walk.SubtaskSummary:
    """Walk `workflow`'s phases for one subtask on pygents. One `asyncio.run`
    around `run_subtask_async`, which documents the parameters."""
    return asyncio.run(
        run_subtask_async(
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
            stop=stop,
            resume_from=resume_from,
        )
    )


async def run_subtask_async(
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
    clock: Callable[[], Any] = walk._utcnow,
    should_stop: Callable[[], bool] | None = None,
    stop: StopSignal | None = None,
    resume_from: Checkpoint | None = None,
) -> walk.SubtaskSummary:
    """Walk `workflow`'s phases for one subtask on the running event loop.

    Every turn is saved as a `turn` checkpoint before it runs, and the run ends
    with a `done` or `escalated` one (`runtime/checkpoint.py`). Two stops park
    the subtask: a `parked` checkpoint is saved, the next phase is not
    started, and the subtask is recorded `stopped before <phase>`.

    - `should_stop` (M6, until Task 3.3) is asked before every turn.
    - `stop`, a `StopSignal`, has the agent registered for the run and
      unregistered on every exit. A trigger pauses it; the turn in flight
      finishes and the agent parks before the next one. A trigger after the
      last phase finished changes nothing: the subtask ends `done`.

    `resume_from` continues from a saved checkpoint instead of the first phase:
    the agent is rebuilt from it, so the pool (seed and earlier results) and
    the queue (the pending turn and its loop count) are the checkpoint's, and
    no seed or first turn is added. A checkpoint saved under another workflow
    digest is refused with `CheckpointMismatch` before anything runs or is
    recorded.
    """
    # The binding, built and refused before any agent exists, so a refusal
    # records nothing.
    binding = walk.subtask_context(
        subtask, repo_dir, commands, card=card, parent_story=parent_story
    )
    if extra_context:
        reserved = sorted(set(extra_context) & set(walk.RESERVED_CONTEXT_KEYS))
        if reserved:
            raise walk.EngineError(
                "extra_context supplies "
                f"{', '.join(repr(key) for key in reserved)}, which the engine owns "
                f"(reserved: {', '.join(walk.RESERVED_CONTEXT_KEYS)})"
            )
        binding.update(extra_context)
    binding.update(walk._document_paths(workflow, card))

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
    try:
        if resume_from is None:
            await agent.context_pool.add(context.seed_item(binding))
            await agent.put(compiled.first_turn())
        deps = RunDeps(
            workflow, store, story_id, subtask, agent_runner, clock, should_stop, stop=stop
        )
        return await _run(agent, deps)
    finally:
        _forget(agent.name)
```

(c) In `_run` (formerly lines 172-219), replace the lines from `    token = current_run.set(deps)` through `    try:` / `        async for _ in agent.run():  # consumed to the end, always` with:

```python
    token = current_run.set(deps)
    # Every `checkpoint.save` below runs inside this `try`, while `current_run`
    # is still set: after the `finally` resets it, `save` is a silent no-op.
    try:
        if deps.stop is not None:
            # Registered for exactly the life of `run()`: a signal that has
            # already fired pauses the agent here, before its first turn.
            deps.stop.register(agent)
        async for _ in agent.run():  # consumed to the end, always
```

replace the `except checkpoint.Parked as parked:` comment lines

```python
        # The stop, raised by the BEFORE_TURN hook after it saved `parked`:
        # no further row, so that one stays the newest.
```

with

```python
        # The stop, raised by the BEFORE_TURN or ON_PAUSE hook after it saved
        # `parked`: no further row, so that one stays the newest.
```

and replace the `finally:` block at the end of `_run`'s `try`

```python
    finally:
        # A `BaseException` (cancellation, KeyboardInterrupt) passes straight
        # through here and writes nothing: the last `turn` row stands.
        current_run.reset(token)
```

with

```python
    finally:
        # A `BaseException` (cancellation, KeyboardInterrupt) passes straight
        # through here and writes nothing: the last `turn` row stands.
        # Unregistered on every exit, so a later trigger never pauses an
        # agent whose run is over.
        if deps.stop is not None:
            deps.stop.unregister(agent)
        current_run.reset(token)
```

- [ ] **Step 6a: Untag `test_compile.py`'s own test agents so the new global `ON_PAUSE` hook does not see them**

`checkpoint.on_pause` (Step 4 above) is module-level and tagged `subtask`, so
pygents' process-wide `HookRegistry` fires it for *every* agent tagged
`subtask` in the test session, not only agents driven through
`engine.run_subtask_async`. `tests/runtime/test_compile.py::test_loop_count_is_in_the_queued_turn`
builds its own agent tagged `subtask` (`_new_agent`) and calls `agent.pause()`
directly, with `state.current_run` set to its own `RunDeps`, purely to freeze
the queue between turns so it can inspect it — it has no relation to
`StopSignal` and asserts nothing about checkpoints. Once `on_pause` exists,
that direct `.pause()` now finds `current_run` set and raises `Parked`
instead of the loop simply waiting on `_pause_event`, which turns the test's
`await run` (at the end of the `try/finally`) into an unhandled-exception
failure. This only reproduces when the two test modules share one pytest
process (`test_compile.py` alone stays green), so it is easy to miss running
one file at a time, but it fails the Step 7 full-suite run.

In `tests/runtime/test_compile.py`, in `_new_agent`, change

```python
        tags=["subtask"],
```

to

```python
        tags=["compile-unit"],
```

This module's tests assert nothing about checkpoints (no `latest_checkpoint`
call anywhere in the file), so detaching it from the `subtask`-tagged hooks
(`before_turn` and now `on_pause`) changes nothing else it checks.

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/runtime/test_stop_bridge.py tests/runtime/test_stop.py tests/runtime/test_compile.py -v`
Expected: all passed (2 existing M6 tests, 8 new stop-bridge tests, 3 `StopSignal` tests, 22 `test_compile.py` tests including `test_loop_count_is_in_the_queued_turn`).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: all passed, including `tests/runtime/test_resume.py`, `tests/runtime/test_compile.py` (its `RunDeps(...)` is keyword-built, and its test agents no longer carry the `subtask` tag) and every `should_stop` test.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/runtime/state.py src/agent_manager/runtime/checkpoint.py src/agent_manager/runtime/engine.py tests/runtime/test_stop_bridge.py tests/runtime/test_compile.py
git commit -m "$(cat <<'EOF'
feat(runtime): StopSignal parks subtask agents through ON_PAUSE

run_subtask_async is the public awaitable driver; run_subtask wraps it.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
)"
```

---

### Task 3: A `StopSignal`-parked checkpoint resumes

**Files:**
- Modify: `src/agent_manager/runtime/engine.py` (resume branch of `run_subtask_async`, the `agent = Agent.from_dict(resume_from.agent)` line)
- Test: `tests/runtime/test_resume.py` (existing; append)

**Interfaces:**
- Consumes: `run_subtask(..., stop=..., resume_from=...)` and `StopSignal` from Tasks 1-2; pygents `Agent.resume()` (idempotent; sets the pause event).
- Produces: no new names. Behavior: a resumed agent never inherits the stored `is_paused` flag; only the current run's `stop` can pause it.

Why: `on_pause` saves `agent.to_dict()` while the agent is paused, so a `StopSignal` `parked` row stores `"is_paused": True`, and `Agent.from_dict` (pygents `agent.py`, `from_dict`) calls `agent.pause()` when it reads that flag. An M6 `should_stop` parked row stores `False`, which is why the existing `test_a_parked_subtask_resumes` never saw this.

- [ ] **Step 1: Write the tests**

In `tests/runtime/test_resume.py`, add after the existing imports (after line 25, `from agent_manager.workflow.phases import AgentPhase, Goto, Step, Workflow`):

```python
from agent_manager.runtime.stop import StopSignal
```

Append at the end of the file:

```python
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
```

- [ ] **Step 2: Run the tests to verify the first fails**

Run: `uv run pytest tests/runtime/test_resume.py -v -k "stop_signal"`
Expected: `test_a_subtask_parked_by_the_stop_signal_resumes` FAILS at `assert ran == list(FIVE)` (`ran == []`: the resumed agent re-parked, `summary.status == "stopped"`). `test_a_stop_signal_parked_subtask_resumed_under_a_triggered_stop_parks_again` PASSES already; it is the regression guard that pins the fix's ordering in Step 3.

- [ ] **Step 3: Clear the stored pause on resume**

In `src/agent_manager/runtime/engine.py`, in `run_subtask_async`'s `else:` (resume) branch, replace

```python
        # The pool (seed, earlier results) and the queue (pending turn, loop
        # count) come from the checkpoint: no seed item, no first turn.
        agent = Agent.from_dict(resume_from.agent)
```

with

```python
        # The pool (seed, earlier results) and the queue (pending turn, loop
        # count) come from the checkpoint: no seed item, no first turn.
        agent = Agent.from_dict(resume_from.agent)
        # A row parked by a `StopSignal` was saved while its agent was paused,
        # and `from_dict` restores that pause; left in place, ON_PAUSE would
        # park the resumed agent again before it ran anything. That pause
        # belonged to the stopped run: only this run's `stop`, registered in
        # `_run` after this line, may pause the agent now.
        agent.resume()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: all passed, including every pre-existing resume test (an M6-parked or crashed row stores `is_paused: False`, so `resume()` is a no-op there).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/runtime/engine.py tests/runtime/test_resume.py
git commit -m "$(cat <<'EOF'
fix(runtime): a StopSignal-parked checkpoint resumes instead of re-parking

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
)"
```

---

## Notes for the implementer

- The spec summary handed to the plan writer was truncated at its 2000-character cap, and the exploration findings at their 8000-character cap (mid test-placement paragraph). Both are upstream stages over-running their briefs. This plan was written from the spec file on disk and the source, not from the truncated text.
- Deviations from the spec's literal wording, both additive: (1) `run_subtask_async` takes `run_subtask`'s defaults (`commands=()`, `card=None`, ..., `clock=walk._utcnow`) rather than `_drive`'s all-required keywords, because it is now public and the parent plan's interface lists it with defaults; (2) the concurrent test's step hands `stop.trigger("A")` to the loop through `loop.call_soon_threadsafe` because steps run in `to_thread` workers and the spec places the signal on the run's loop. Task 3's `agent.resume()` fills a gap the spec is silent on (Review Focus 1).
- Task 2's Step 6a is not in the spec either. The spec's new `on_pause` hook is module-level and tagged `subtask` (as it must be, per pygents' process-wide `HookRegistry`), which means it fires for any agent tagged `subtask` in the whole test process, not only ones driven through `run_subtask_async`. `tests/runtime/test_compile.py` has its own agents tagged `subtask` (unrelated to the engine) and one test pauses one directly to inspect its queue; verified by actually running `uv run pytest` end to end, that test only fails when run in the same session as a test that imports `runtime.engine`/`runtime.checkpoint` (i.e. the default full-suite run), not standalone. Retagging those test agents `compile-unit` is the fix; it changes nothing that module's tests assert.
