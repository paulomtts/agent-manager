<!-- task-pipeline: validated -->
# Task 1.2 — Pin pygents' cancellation behaviour and drop pre-0.7 guards (card 0822234f)

Parent story: dd4a87d5 "Adopt pygents 0.7.0" (milestone b75742dd). Spec of record: `docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md`, decisions A3 and A5 (with A4 bounding what may be removed). Blocked by 59fd7f22 (Task 1.1); this worktree already carries its state: `engine.py`'s `_forget()` uses `contextlib.suppress(UnregisteredAgentError): AgentRegistry.unregister(name)`.

## Scope

1. New file `tests/runtime/test_cancellation.py`: pins, with real pygents `Agent` objects, a fake launcher and a temporary store, how the engine behaves when the task driving a subtask agent is cancelled (A3) and that engine-built agents carry no instance hooks (A5). Conventions follow `tests/runtime/test_resume.py` (agent_runner injection, `StopSignal` fixtures, its `_go`-style driver helper).
2. Remove from `src/agent_manager/runtime/engine.py` (and `src/agent_manager/orchestrate.py`, only if it holds any) code that exists solely to work around pre-0.7 pygents mishandling cancellation or early exit. Before editing, read the code as built and confirm each candidate. Expected result, based on locating:
   - `_run`'s `async for _ in agent.run(): pass` (engine.py ~197) is the normal loop. `checkpoint.Parked` already leaves via a raise, and no extra "keep consuming after Parked" loop exists. Keep it (A4). The module docstring (lines 1-7) and the inline comment "consumed to the end, always" stay too, because retiring the rule's wording belongs to Task 1.3's docs.
   - No `_is_running` manual reset exists in `src/`, so there is nothing to remove.
   - `_forget()` and its two call sites (before `Agent.from_dict` on resume, and in `run_subtask_async`'s `finally`) are A2's design, not a workaround. Keep them.
   - orchestrate.py's cancellation forwarding (grafo `run_until_killed` and the `BaseException` handling around lines 1100-1234) is genuine propagation design. Leave it untouched unless Step 1 shows a pygents-specific guard.
   If confirmation matches this, the source change is empty and the card's report must say so explicitly. It must not invent a removal.

## Observable behaviour pinned

- **Cancel mid-agent-phase** (the Ctrl-C path): `run_subtask_async` runs as an asyncio task. The fake launcher blocks inside an agent phase, and the test cancels the task. Afterwards:
  - `CancelledError` propagates to the caller.
  - The agent is not running, and its name is free in `AgentRegistry`: a fresh agent under the same name can be constructed or registered.
  - The turn in flight ended with stop reason `CANCELLED`.
  - The fake launcher's process was killed by M6's bridge (`runtime/bridge.py` kill_tree, unchanged).
  - The store's newest checkpoint for the card has `reason == "turn"` and no `done`/`escalated`/`parked` row after it.
  - `pending_phase(latest)` names the cancelled phase, and resuming from that checkpoint with a non-blocking fake launcher runs to `done`.
- **Cancel during a deterministic step phase**: the same assertions, except for the process kill, which applies only if the step spawns through the bridge. Specifically: the agent is not running, the name is free, the turn is `CANCELLED`, and the latest `turn` checkpoint is intact and resumable.
- **No closure hooks (A5)**: an agent built by `run_subtask_async` on the fresh path, and one rebuilt through the resume path (`Agent.from_dict` of a stored checkpoint), both have `hooks == []` and `turn_hooks == []`. `to_dict()` succeeds on both, without `UnserializableHookError`. If a hook is ever attached, the assertion message names it.
- **Early exit leaves the agent reusable**: this test is written only if Step 1 finds an engine path that breaks or returns out of `agent.run()`. Given the findings above, none is expected, so the test is omitted and the omission is noted.

## Error paths

- Cancellation writes no checkpoint row, because `_run`'s `finally` handles only `stop.unregister` and `current_run.reset`. The tests assert that no row appears after the last `turn` row.
- `StopSignal` registration is undone on cancel: `stop.unregister(agent)` ran, so a later trigger does not touch the dead agent.
- The name is freed even though `_run` exited by `BaseException`, through `run_subtask_async`'s `finally` → `_forget`.

## Constraints

- Do not touch `runtime/checkpoint.py` (the `Parked` class and the module-level `@hook(..., tags={"subtask"})` BEFORE_TURN/ON_PAUSE hooks) or `runtime/bridge.py`.
- Do not access `_registry` or `_items` on any pygents registry in `src/`. Tests may use registry `clear()` between cases.
- Stay out of sibling and milestone scope: the floor bump and unregister swap belong to 59fd7f22, the spec/CLAUDE.md rule retirement to 4f31e025, and exactly-once phases (232cbd44), live control (eb5db173) and multiple `am` processes (2db2a2ef) to their own issues. No pygents changes.

## Tests

All tests are Engine tier per `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14: engine driven with a fake launcher/adapter, no real harness, in the default suite. They live in `tests/runtime/test_cancellation.py`. None goes in `tests/e2e/`, which is reserved for the opt-in real-harness tests.

| Test | Tier |
|---|---|
| cancel mid-agent-phase: CancelledError propagates, agent not running / name free, turn `CANCELLED`, fake process killed, latest checkpoint `turn` and resumable to `done` | Engine (`tests/runtime/`) |
| cancel mid-step-phase: same, without the process-kill assertion unless the step spawns via the bridge | Engine (`tests/runtime/`) |
| cancel with a `StopSignal` registered: agent unregistered from the signal, no `parked` row | Engine (`tests/runtime/`) |
| fresh engine-built agent: `hooks == []`, `turn_hooks == []`, `to_dict()` succeeds | Engine (`tests/runtime/`) |
| resume-rebuilt agent (`Agent.from_dict`): `hooks == []`, `turn_hooks == []`, `to_dict()` succeeds | Engine (`tests/runtime/`) |
| early-exit path leaves agent reusable, only if Step 1 finds such a path (expected: omitted) | Engine (`tests/runtime/`) |

Verification: `uv run pytest` green, and `grep -rn "_registry\|_items" src/agent_manager | grep -i -E "AgentRegistry|ToolRegistry|HookRegistry"` returns nothing.

---

# Pin pygents' cancellation behaviour (card 0822234f) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tests/runtime/test_cancellation.py`, which pins how the pygents engine behaves when a subtask's asyncio task is cancelled mid-turn (A3) and that engine-built agents carry no instance hooks (A5), after first confirming that no pre-0.7 cancellation/early-exit guard remains in `src/` to remove.

**Architecture:** Engine-tier tests only. They drive `runtime.engine.run_subtask_async` as an asyncio task over real pygents `Agent`s, fake steps (plain functions gated by `threading.Event`s), a fake agent runner that launches a `sys.executable -c` sleeper through `harness.launcher.run_direct` and hands it to the bridge's spawn hook (the stand-in for `claude -p`), and a real temp store. The in-flight turn's stop reason is observed through a test-scoped global `TurnHook.ON_COMPLETE` hook that its fixture unregisters with pygents 0.7's `HookRegistry.unregister`. The expected source change is empty. Each test is a characterisation test of behaviour that already holds, so each task proves its tests can fail by temporarily sabotaging `engine.py`, watching the named assertion fail, and reverting with `git checkout`.

**Tech Stack:** Python 3.12, pytest with `asyncio_mode = "auto"` (pyproject.toml), pygents 0.7.x, `uv`.

**Spec:** `docs/superpowers/specs/task-pin-pygents-0822234f-design.md` (prepended above). Spec of record: `docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md` decisions A3, A4, A5.

**Branch / worktree:** `m8/task-pin-pygents-0822234f` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m8/task-pin-pygents-0822234f`, cut from `m8/task-raise-the-pygents-floor-59fd7f22`. All paths below are relative to that worktree. The only sibling code assumed is what that base branch already carries (Task 1.1's `unregister`-based `_forget` in `src/agent_manager/runtime/engine.py:255-264`, and `tests/runtime/test_engine_registry.py`). Nothing from Task 1.3 (4f31e025) is assumed.

## Global Constraints

- Do not modify `src/agent_manager/runtime/checkpoint.py` or `src/agent_manager/runtime/bridge.py` (M6's `kill_tree` and the module-level `@hook(..., tags={"subtask"})` hooks stay).
- No `_registry` or `_items` access on any pygents registry in `src/`. Tests may use `clear()` and the public `unregister()`.
- `agent.run()` consumed to the end stays the engine's normal loop, and hooks stay global/module-level (A4). Neither is a workaround to remove.
- Do not edit the module docstring (`engine.py:1-7`) or the "consumed to the end, always" comment; retiring that wording is Task 1.3's (4f31e025).
- No pygents changes; no work on exactly-once phases (232cbd44), live control (eb5db173) or multiple `am` processes (2db2a2ef); no floor bump or unregister swap (59fd7f22).
- Every new test is Engine tier and lives in `tests/runtime/test_cancellation.py`; nothing goes in `tests/e2e/`.
- No sleeps for ordering: use `threading.Event`s waited through `asyncio.to_thread`, as `tests/runtime/test_stop_bridge.py` and `tests/runtime/test_bridge.py` do.
- Temporary sabotage edits to `src/` exist only to watch a test fail. Revert each with `git checkout -- <file>` and confirm `git diff --exit-code -- src/` before committing.
- Verification: `uv run pytest`.

## Review Focus

1. The abandoned `to_thread` worker of a cancelled step finishes after the cancel. It must not add a checkpoint row after the last `turn` row, so the resume still starts at the cancelled phase. Covered in Task 2 (`test_cancelling_mid_step_phase_leaves_a_resumable_turn` waits for the worker before asserting rows).
2. A resumed run that is itself cancelled must leave its own newest `turn` row resumable. The name must be freed again, and a third run must finish `done` with no gap or duplicate in `seq`. Covered in Task 2 (`test_a_resumed_run_cancelled_again_resumes_from_its_own_turn`).
3. Cancelling one subtask while a sibling subtask runs concurrently on the same loop and store must leave the sibling untouched: it finishes `done` with its own rows, and only the cancelled card's name is freed. Covered in Task 3 (`test_cancelling_one_subtask_leaves_a_concurrent_one_running`).
4. A `StopSignal` triggered after a cancel must not pause the dead agent, and no `parked` row may appear. Covered in Task 3 (`test_a_cancel_unregisters_the_agent_from_the_stop_signal`).
5. The cancelled agent object stays serialisable and idle: `to_dict()` succeeds with no `current_turn`, and a property change is not refused as "running". Covered in Task 1 (`_assert_stopped`, used by every cancel test).

---

### Task 1: Confirm the code as built, and pin cancel mid-agent-phase

**Files:**
- Read only: `src/agent_manager/runtime/engine.py`, `src/agent_manager/orchestrate.py:1099-1239`
- Create: `tests/runtime/test_cancellation.py`

**Interfaces:**
- Consumes: `runtime_engine.run_subtask_async(workflow, store, *, story_id, subtask, repo_dir, commands=(), card=None, parent_story=None, extra_context=None, agent_runner=None, clock=..., stop=None, resume_from=None) -> walk.SubtaskSummary`; `runtime_engine.pending_phase(checkpoint) -> str | None`; `store.latest_checkpoint(card_id) -> Checkpoint | None`; `bridge.current_spawn_hook() -> SpawnHook | None`; `launcher.run_direct(argv, *, cwd, timeout, stdout_path, on_spawn=None) -> Outcome`; the autouse `fresh_pygents` fixture in `tests/runtime/conftest.py`.
- Produces (module-level in `tests/runtime/test_cancellation.py`, used by Tasks 2-4): constants `RUN_ID`, `STORY_ID`, `CARD_ID`, `OTHER_CARD`, `REPO`, `FIXED`, `AGENT_NAME`, `SLEEPER`; fixtures `store`, `completions` (yields `list[tuple[str, StopReason | None]]`); helpers `_subtask(card_id=CARD_ID)`, `_run(workflow, opened, card_id=CARD_ID, **kwargs)` (returns the `run_subtask_async` coroutine), `_go(workflow, opened, **kwargs)` (sync, `asyncio.run(_run(...))`), `_reasons(opened, card_id=CARD_ID) -> list[tuple[int, str]]`, `_stored_agents(opened, card_id=CARD_ID) -> list[dict]`, `_assert_free(name)`, `_assert_stopped(agent)`, `_with_agent_phase(ran) -> Workflow`, `_instant(phase, table, rendered) -> dict`; class `_Launcher(log_dir)` with `.spawned`, `.left` (`threading.Event`) and `.process` (`subprocess.Popen | None`).

- [ ] **Step 1: Confirm there is no pre-0.7 guard to remove**

Run each command from the worktree root and compare with the expected output:

```bash
grep -rn "_is_running" src/agent_manager
```
Expected: no output (there is no manual `_is_running` reset).

```bash
grep -rn "_registry\|_items" src/agent_manager | grep -i -E "AgentRegistry|ToolRegistry|HookRegistry"
```
Expected: no output.

```bash
grep -rn "in agent.run()\|\.aclose()\|agent\.run()\.__anext__" src/agent_manager
```
Expected: exactly one hit, `src/agent_manager/runtime/engine.py:197: async for _ in agent.run():  # consumed to the end, always`. Other mentions of `agent.run()` in docstrings (`engine.py:6`, `engine.py:50`, `checkpoint.py:11`, `state.py:7`) are prose, and this pattern does not match them.

```bash
grep -n "pygents" src/agent_manager/orchestrate.py
```
Expected: no output. orchestrate.py does not import pygents, so its `run_until_killed` / `BaseException` handling (lines 1099-1239) is about grafo dropping a lane's `BaseException` (its docstring says so), not a pygents workaround.

Then read `src/agent_manager/runtime/engine.py:177-242` and confirm:
- the body of `async for _ in agent.run():` is only `pass`, with no `break` or `return` inside it and no second loop that keeps draining `run()` after `checkpoint.Parked`;
- the `finally` of `_run` only calls `deps.stop.unregister(agent)` and `current_run.reset(token)`;
- `_forget` is called in exactly two places, before `Agent.from_dict` (line 167) and in `run_subtask_async`'s `finally` (line 184), and uses `AgentRegistry.unregister`.

If everything matches, the source change for this card is empty. Record that for Task 5's report. If any command shows a hit not listed above, stop: do not improvise a removal. Report the hit (file:line and code) back to the caller as a scope question, because the spec expects none.

- [ ] **Step 2: Write the test module and the mid-agent-phase cancel test**

Create `tests/runtime/test_cancellation.py`:

```python
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
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/runtime/test_cancellation.py::test_cancelling_mid_agent_phase_kills_the_process_and_leaves_a_resumable_turn -v`
Expected: PASS. This is a characterisation test of behaviour pygents 0.7 and M6 already provide. If it fails, stop and report the failing assertion. A failure here means pygents' cancellation differs from what A3 assumes, and fixing pygents is out of scope.

- [ ] **Step 4: Prove the test can fail (temporary sabotage)**

In `src/agent_manager/runtime/engine.py`, replace

```python
    finally:
        _forget(agent.name)
```

with

```python
    finally:
        pass
```

Run: `uv run pytest tests/runtime/test_cancellation.py::test_cancelling_mid_agent_phase_kills_the_process_and_leaves_a_resumable_turn -v`
Expected: FAIL inside `_assert_free` with `Failed: DID NOT RAISE <class 'pygents.errors.UnregisteredAgentError'>`.

- [ ] **Step 5: Revert the sabotage and re-run**

```bash
git checkout -- src/agent_manager/runtime/engine.py
git diff --exit-code -- src/
uv run pytest tests/runtime/test_cancellation.py -v
```
Expected: `git diff` prints nothing and exits 0; the test PASSES.

- [ ] **Step 6: Commit**

```bash
git add tests/runtime/test_cancellation.py
git commit -m "test: pin cancelling a subtask mid-agent-phase (A3)"
```

---

### Task 2: Pin cancel mid-step-phase, including a second cancel after a resume

**Files:**
- Modify: `tests/runtime/test_cancellation.py` (append after the Task 1 test)

**Interfaces:**
- Consumes (from Task 1): `store`, `completions`, `_run`, `_reasons`, `_assert_free`, `_assert_stopped`, `AGENT_NAME`, `CARD_ID`.
- Produces (used by Task 3): class `_Gate(*, opened=False)` with `.entered`, `.opened`, `.left` (`threading.Event`); `_gated(ran: list[str], gate: _Gate) -> Workflow` (steps `a`, `slow`, `b`, named `"gated"`); `async _cancel_in_slow(wf, gate, opened, **kwargs) -> Agent`.

A step goes through `bridge.call_step`, which spawns nothing the bridge tracks (`bridge.py:89-91`). So these tests make no process-kill assertion, as the spec allows.

- [ ] **Step 1: Write the step-phase tests**

Append to `tests/runtime/test_cancellation.py`:

```python
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
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/runtime/test_cancellation.py -k "step_phase or cancelled_again" -v`
Expected: 2 PASSED. If either fails, stop and report the failing assertion. Do not change `src/` to make it pass.

- [ ] **Step 3: Prove the tests can fail (temporary sabotage)**

In `src/agent_manager/runtime/engine.py`, replace

```python
    finally:
        _forget(agent.name)
```

with

```python
    finally:
        pass
```

Run: `uv run pytest tests/runtime/test_cancellation.py -k "step_phase or cancelled_again" -v`
Expected: both FAIL. `test_cancelling_mid_step_phase_leaves_a_resumable_turn` fails in `_assert_free` with `DID NOT RAISE <class 'pygents.errors.UnregisteredAgentError'>`. `test_a_resumed_run_cancelled_again_resumes_from_its_own_turn` fails because the second run's `AgentRegistry.get(AGENT_NAME)` in `_cancel_in_slow` finds a stale agent, or at `_assert_free` (either failure is acceptable, as long as it FAILS).

- [ ] **Step 4: Revert the sabotage and re-run**

```bash
git checkout -- src/agent_manager/runtime/engine.py
git diff --exit-code -- src/
uv run pytest tests/runtime/test_cancellation.py -v
```
Expected: `git diff` prints nothing and exits 0; 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_cancellation.py
git commit -m "test: pin cancelling a subtask mid-step-phase, twice over a resume (A3)"
```

---

### Task 3: Pin cancel with a StopSignal registered, and cancel beside a concurrent subtask

**Files:**
- Modify: `tests/runtime/test_cancellation.py` (append after the Task 2 tests)

**Interfaces:**
- Consumes (from Tasks 1-2): `store`, `completions`, `_run`, `_reasons`, `_assert_free`, `_Launcher`, `_with_agent_phase`, `_Gate`, `_gated`, `AGENT_NAME`, `OTHER_CARD`, `RUN_ID`; `StopSignal.register/unregister/trigger`.
- Produces: class `_Spy(StopSignal)` with `.calls: list[tuple[str, str]]`. Nothing later depends on it.

- [ ] **Step 1: Write the tests**

Append to `tests/runtime/test_cancellation.py`:

```python
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
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/runtime/test_cancellation.py -k "stop_signal or concurrent" -v`
Expected: 2 PASSED. If either fails, stop and report the failing assertion. Do not change `src/`.

- [ ] **Step 3: Prove the StopSignal test can fail (temporary sabotage)**

In `src/agent_manager/runtime/engine.py`, replace

```python
        if deps.stop is not None:
            deps.stop.unregister(agent)
        current_run.reset(token)
```

with

```python
        current_run.reset(token)
```

Run: `uv run pytest tests/runtime/test_cancellation.py::test_a_cancel_unregisters_the_agent_from_the_stop_signal -v`
Expected: FAIL at `assert spy.calls == [...]` (the right side has an `unregister` entry the left lacks).

Revert and apply the second sabotage. Replace

```python
    finally:
        _forget(agent.name)
```

with

```python
    finally:
        pass
```

Run: `uv run pytest tests/runtime/test_cancellation.py::test_cancelling_one_subtask_leaves_a_concurrent_one_running -v`
Expected: FAIL in `_assert_free` with `DID NOT RAISE <class 'pygents.errors.UnregisteredAgentError'>`.

- [ ] **Step 4: Revert the sabotage and re-run**

```bash
git checkout -- src/agent_manager/runtime/engine.py
git diff --exit-code -- src/
uv run pytest tests/runtime/test_cancellation.py -v
```
Expected: `git diff` prints nothing and exits 0; 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_cancellation.py
git commit -m "test: pin a cancel's StopSignal unregister and a concurrent subtask's survival (A3)"
```

---

### Task 4: Pin no instance hooks on engine-built agents (A5)

**Files:**
- Modify: `tests/runtime/test_cancellation.py` (append after the Task 3 tests)

**Interfaces:**
- Consumes (from Task 1): `store`, `_go`, `_stored_agents`, `AGENT_NAME`, `CARD_ID`; `Agent.hooks`, `Agent.turn_hooks`, `Agent.to_dict()` (raises `UnserializableHookError` on a hook that isn't the one registered under its name).
- Produces: `_Crash(BaseException)`, `_capturing(seen, crash_in) -> Workflow`, `_assert_no_instance_hooks(agent)`. Nothing later depends on them.

- [ ] **Step 1: Write the A5 tests**

Append to `tests/runtime/test_cancellation.py`:

```python
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
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/runtime/test_cancellation.py -k "instance_hooks" -v`
Expected: 2 PASSED. If either fails, stop and report the failing assertion. Do not change `src/`.

- [ ] **Step 3: Prove the tests can fail (temporary sabotage)**

In `src/agent_manager/runtime/engine.py`, replace

```python
            tags=["subtask"],
        )
    else:
```

with

```python
            tags=["subtask"],
        )

        async def _sabotage_hook(agent: Any) -> None:
            return None

        agent.before_turn(_sabotage_hook)
    else:
```

Run: `uv run pytest tests/runtime/test_cancellation.py -k "instance_hooks" -v`
Expected: both FAIL. The fresh test fails with `instance hooks attached: ['_sabotage_hook']`. The resume test fails either at the first `_go` with `UnserializableHookError` (a second closure under a name `HookRegistry` already holds cannot be saved) or at `_assert_no_instance_hooks` naming `_sabotage_hook`. Either failure is acceptable, as long as it FAILS.

- [ ] **Step 4: Revert the sabotage and re-run**

```bash
git checkout -- src/agent_manager/runtime/engine.py
git diff --exit-code -- src/
uv run pytest tests/runtime/test_cancellation.py -v
```
Expected: `git diff` prints nothing and exits 0; 7 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_cancellation.py
git commit -m "test: pin that engine-built agents carry no instance hooks (A5)"
```

---

### Task 5: Full verification and the card's report

**Files:**
- None modified.

**Interfaces:**
- Consumes: everything above. Produces: the card's report text.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: all tests pass (no failures, no errors). The opt-in real-harness tests in `tests/e2e/` stay deselected/skipped as before.

- [ ] **Step 2: Run the spec's registry grep**

Run: `grep -rn "_registry\|_items" src/agent_manager | grep -i -E "AgentRegistry|ToolRegistry|HookRegistry"`
Expected: no output.

- [ ] **Step 3: Confirm the source change is empty**

Run: `git diff --stat m8/task-raise-the-pygents-floor-59fd7f22 -- src/`
Expected: no output. `src/agent_manager/runtime/engine.py`, `src/agent_manager/orchestrate.py`, `src/agent_manager/runtime/checkpoint.py` and `src/agent_manager/runtime/bridge.py` are unchanged.

Run: `git diff --stat m8/task-raise-the-pygents-floor-59fd7f22`
Expected: `tests/runtime/test_cancellation.py` and this plan/spec under `docs/superpowers/` only.

- [ ] **Step 4: Write the card's report**

Include this statement, adjusted only if Task 1 Step 1 found otherwise (in which case the task would have stopped there):

> Source change: none. Task 1.2's "drop pre-0.7 guards" step found nothing to remove. `engine.py`'s `async for _ in agent.run(): pass` is the normal loop (A4). `Parked` already leaves it via a raise, and there is no extra drain loop. No `_is_running` reset exists in `src/`. `_forget` and its two call sites are A2's design, already on `AgentRegistry.unregister` from Task 1.1. orchestrate.py does not import pygents, and its `run_until_killed` / `BaseException` handling works around grafo dropping a lane's `BaseException`, not pygents. `checkpoint.py` and `bridge.py` are untouched. The early-exit reusability test is omitted because no engine path breaks or returns out of `agent.run()`. New: `tests/runtime/test_cancellation.py` (Engine tier, 7 tests) pins cancel mid-agent-phase (CancelledError propagates, turn CANCELLED, process SIGKILLed by the bridge, name free, agent idle, latest row `turn` and resumable to `done`), cancel mid-step-phase (including a second cancel after a resume, and the abandoned worker adding no row), cancel with a `StopSignal` registered (unregistered, no `parked` row, a later trigger pauses nothing), cancel beside a concurrent subtask (the sibling finishes `done`), and `hooks == []` / `turn_hooks == []` with a working `to_dict()` on fresh and resume-rebuilt agents.
