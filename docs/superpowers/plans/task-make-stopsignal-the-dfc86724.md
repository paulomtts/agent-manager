<!-- task-pipeline: validated -->
# Make StopSignal the only stop (card dfc86724)

Parent story: 92c0ab94 "The supervisor". Blocked by 8eca88e2 (done). Narrows supervisor-tree design `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §T5 (lines 76-80) and its file-change table (line 132, "`stop: StopSignal | None` replaces `should_stop`") to one subtask. This is plan Task 3.3.

## Scope

Delete the M6 cooperative stop, `should_stop`, everywhere. After this card, the only way to stop a subtask is a `StopSignal` (`runtime/stop.py`), which pauses the agent, and the `ON_PAUSE` hook (`runtime/checkpoint.py:on_pause`), which saves `parked` and raises `Parked`. `runtime/stop.py` and `on_pause` are already done and stay as they are.

Source changes (all under `src/agent_manager/`):

- `runtime/engine.py`: drop the `should_stop` parameter from `run_subtask` and `run_subtask_async`. Drop it from the `RunDeps(...)` construction too; that call is positional (`workflow, store, story_id, subtask, agent_runner, clock, should_stop, stop=stop`), so check the remaining arguments still bind to the right fields. Remove the docstring line "`should_stop` (M6, until Task 3.3) is asked before every turn."
- `runtime/state.py`: remove the `RunDeps.should_stop` field. Rewrite the `stop` field's docstring so it no longer talks about `should_stop` or Task 3.3.
- `runtime/checkpoint.py`: in `before_turn`, remove only the `if deps.should_stop ...: save(agent, "parked"); raise Parked(head)` branch. `save(agent, "turn")` must still run on every turn. If `head` is no longer used after that, drop it, and drop `snapshot` too if nothing else in the function still reads it. Rewrite the module docstring so it describes one stop: the paragraph that begins "There are two stops until Task 3.3" goes, and so does the opening paragraph's claim that `before_turn` reads the stop. `ON_PAUSE` is the only parking point.
- `runtime/engine.py`: in `run_subtask_async`'s docstring, the sentence "Two stops park the subtask: ..." introduces the two bulleted paragraphs below it; once the `should_stop` bullet is removed (see below), rewrite that sentence too so it no longer says "Two stops" over what is now a single bulleted paragraph about `stop`.
- `cli.py`: remove `should_stop` from `drive_subtask_async` (the parameter and the `"should_stop"` key in the `walk` dict) and from `drive_subtask` (the parameter and where it is forwarded). Update both docstrings. The sync `drive_subtask` then has no stop at all. That is intended: the spec lists its parameters without adding a `stop`, and callers that need to stop await `drive_subtask_async(stop=...)`.
- `orchestrate.py`: remove `should_stop` from the `Driver` Protocol's `__call__` signature (around line 286), and remove the docstring sentence "`should_stop` stays until Task 3.3 deletes it; the lane never passes it." **Note for the planner:** the card's "Files" list leaves out `orchestrate.py`, but the code says this deletion belongs to Task 3.3. It only removes the parameter. Do not touch scheduling (owned by c0dbd454) or base-building (owned by 8eca88e2).
- Remove any `Callable` imports that no longer have a use.

Done when: `grep -rn "should_stop" src tests` finds nothing, and `uv run pytest` passes in full, including `tests/e2e`.

## Observable behaviour

- A stop still gives a `stopped` summary with the detail `"stopped before <next phase>"`, a `parked` checkpoint whose queue head is that phase, and a subtask row with status `stopped`. The difference is that the stop now arrives only through `StopSignal.trigger` or through a `register` on a signal that has already fired.
- Every turn still writes a `turn` checkpoint before it runs. A run with no `stop`, or with one that is never triggered, behaves exactly as today.
- Passing `should_stop=` to `run_subtask`, `run_subtask_async`, `drive_subtask`, `drive_subtask_async`, or `RunDeps` now raises `TypeError`, because the keyword no longer exists. No compatibility shim is kept.

## Error paths

- If a phase escalates in the same turn that a stop is triggered, the escalation still wins, as it does today with `should_stop`.
- An error raised outside a phase still writes an `escalated` checkpoint row through the engine's generic `except Exception` branch.
- No new errors are introduced.

## Tests

The tier rule comes from `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14. Engine- and runtime-level code is tested against temporary stores, repos and boards with canned fakes, in the default suite. Only a real harness goes in `tests/e2e/`. None of the tests below starts a real harness, so none of them belongs in `tests/e2e/`. Each change goes into the existing test file that already covers the code. Tests that set `should_stop=` either switch to a `StopSignal` or are deleted when a `StopSignal` test already covers the same thing. No test may sleep to get ordering: trigger the signal from inside a canned step or fake runner, or block on an `asyncio.Event`.

- `tests/test_engine.py` (engine tier, temp store): the stop block at lines 2318-2622.
  - Replace `_StopFlag` with a `StopSignal` that canned steps `trigger(...)`.
  - Port to it: `test_a_stop_requested_during_phase_three_stops_before_phase_four`, `test_a_stop_already_requested_runs_no_phase_at_all`, `test_a_stop_before_a_deterministic_phase_leaves_it_unstarted`, `test_a_stop_before_an_agent_phase_leaves_the_runner_uncalled`, `test_a_stop_before_an_agent_phase_wins_over_a_missing_runner`, `test_an_escalation_during_the_stop_request_wins_over_the_stop`. The last two may use a pre-triggered signal.
  - Rename `test_a_should_stop_that_never_fires_changes_nothing` so it uses an untriggered `StopSignal`.
  - Delete `test_should_stop_is_checked_only_at_visited_phases`. It tests the polling mechanism itself, which no longer exists.
- `tests/runtime/test_stop_bridge.py` (runtime tier, temp store): delete the two M6 tests at lines 70-126. `test_a_trigger_from_one_subtask_parks_another_mid_phase` and `test_a_stop_triggered_before_the_run_parks_before_the_first_phase` already cover the same things with `StopSignal`. Port the `should_stop=lambda: ran == ["a"]` call around line 351 to a `StopSignal` that step `a` triggers.
- `tests/runtime/test_checkpoint.py` (runtime tier):
  - Add a test that `before_turn` saves a `turn` row for every turn even when a `StopSignal` is triggered mid-run. The expected rows are `turn`, `turn`, …, then `parked` from `on_pause`, with no `parked` written by `before_turn`.
  - Rewrite `test_an_error_outside_a_phase_writes_an_escalated_row`. It currently makes the error by having `should_stop` raise. Raise it from another source that sits outside any phase (for example, a store wrapper whose second `save_checkpoint` raises). Keep its assertions: `ran == ["a"]`, `escalated`, and rows `(0, "turn"), (1, "escalated")`.
- `tests/runtime/test_resume.py` (runtime tier): the six `should_stop=` calls (lines 211, 301, 304, 322, 345, 393) switch to a `StopSignal` triggered by step `a`, or already triggered for the resume at line 304. The parked checkpoint and resume assertions stay as they are.
- `tests/test_cli.py` (CLI tier: temp git repo and brd board, fake runner):
  - `test_drive_subtask_hands_should_stop_to_the_engine` becomes a test that `drive_subtask_async` hands a pre-triggered `StopSignal` to the engine. Driven through `asyncio.run`, it keeps the result "stopped before worktree", with no runner call and no worktree created.
  - `test_drive_subtask_walks_task_with_the_same_arguments` drops the `should_stop` argument and the `"should_stop"` expected key, keeping `"stop": None`.
  - The expected kwargs around line 1679 drop `"should_stop": None`.
  - The `_park_pygents` helper (around line 4405; despite the similarly named `_crash_pygents` nearby, this is the one that calls `drive_subtask` with `should_stop=`) switches to `drive_subtask_async` with a `StopSignal` that the fake runner triggers once it has seen `spec`. It still asserts "stopped before validate_spec".
- `tests/test_orchestrate.py` (orchestrate tier, fake drivers):
  - Fake drivers drop their `should_stop=None` parameter and the recorded `"should_stop"` key (around lines 664, 680, 994).
  - The test around line 1193 stops asserting `calls[0]["should_stop"] is None`. It asserts instead that the lane passes no `should_stop` keyword, and its docstring stops mentioning Task 3.3.

Out of scope: `runtime/stop.py`, `on_pause`, the scheduling and base-building logic in `orchestrate.py` and `bases.py`, and everything in the addendum's §10 (live `am pause/cancel/watch/retry`, verification discovery, grafo `max_workers`, a676f178).

---

# Make StopSignal the only stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the M6 `should_stop` cooperative stop from the engine, `RunDeps`, the `BEFORE_TURN` hook, both CLI drivers and the orchestrator's `Driver` protocol, so a `StopSignal` plus `ON_PAUSE` is the only way a subtask stops.

**Architecture:** Tests are moved off `should_stop` first, onto a `StopSignal` that a canned step or fake runner triggers (the port passes before and after the source change). Then the parameter is removed layer by layer from the outside in (CLI drivers, then the orchestrator's `Driver` protocol, then the engine, `RunDeps` and the hook), so the default suite stays green after every task. Each removal is driven by a RED test that pins the exact parameter list or positional binding.

**Tech Stack:** Python 3, pytest with `asyncio_mode = "auto"` (see `pyproject.toml`), pygents `Agent` hooks, `uv`.

**Spec:** `docs/superpowers/specs/task-make-stopsignal-the-dfc86724-design.md` (prepended above). Parent decision: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` §T5.

**Working directory:** every path below is relative to the worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m7/task-make-stopsignal-the-dfc86724` (branch `m7/task-make-stopsignal-the-dfc86724`, cut from `m7/task-root-multi-blocker-8eca88e2`). Run every command from there.

## Global Constraints

- Only `orchestrate.py` imports `grafo`; every grafo `Node` is built with `timeout=None`. This card adds no grafo code.
- Only the subtask is a pygents Agent; the supervisor and lanes are plain async functions.
- The WHOLE default suite stays green, `tests/e2e` included: `uv run pytest`.
- No test sleeps to get ordering. Steps and agent runners run in `asyncio.to_thread` workers (`src/agent_manager/runtime/bridge.py:83,91`), and the `StopSignal` lives on the loop, so a step triggers it with `loop.call_soon_threadsafe(...)` and blocks on a `threading.Event` until the trigger has run (the pattern already used in `tests/runtime/test_stop_bridge.py:148-202`).
- A fake `claude` never knows more than its brief.
- The milestone's base branch never moves; nothing is pushed.
- Do not touch `src/agent_manager/runtime/stop.py`, `on_pause` in `src/agent_manager/runtime/checkpoint.py`, or the scheduling and base-building logic in `src/agent_manager/orchestrate.py` / `src/agent_manager/bases.py`.
- No compatibility shim: the old keyword simply no longer exists.
- Done when `grep -rn "should_stop" src tests` prints nothing and `uv run pytest` passes.
- `Callable` stays imported in `runtime/engine.py`, `runtime/state.py`, `cli.py` and `orchestrate.py`: each still uses it (`clock`, `agent_runner`, `node_coroutine`, `run_card`'s `clock`). Only remove an import if the removal leaves it unused.

## Review Focus

1. A stop triggered from a step or runner in a `to_thread` worker: expected to pause the agent after that phase finishes and park before the next. Pinned by the ported `tests/test_engine.py` tests (Task 2) and `tests/runtime/test_checkpoint.py::test_before_turn_saves_every_turn_and_only_on_pause_parks` (Task 1).
2. A `parked` row written by the deleted M6 stop (agent never paused, `is_paused` false) still sitting in an existing store: expected to resume like any other parked row. Pinned by `tests/runtime/test_resume.py::test_a_row_parked_without_a_pause_still_resumes` (Task 1).
3. A caller still passing the old keyword: expected `TypeError`, no silent ignore. Pinned by the parameter-list tests in Tasks 3, 4 and 5.
4. A phase that triggers the stop and then fails in the same turn: expected `escalated`, not `stopped`. Pinned by `tests/test_engine.py::test_an_escalation_during_the_stop_request_wins_over_the_stop` (Task 2).
5. The positional `RunDeps(...)` call in the engine after the field is gone: the seventh positional argument must bind to `stop`, not shift anything. Pinned by `tests/runtime/test_stop_bridge.py::test_run_deps_binds_the_stop_signal_seventh` (Task 5).

---

### Task 1: Move the runtime-tier tests onto `StopSignal`

Test-only. Every changed test passes before and after the source change, because `StopSignal` already works; this task removes the tests' dependence on `should_stop` so Task 5 can delete it.

**Files:**
- Modify: `tests/runtime/test_stop_bridge.py:1-14` (module docstring), `:70-126` (delete two M6 tests), `:341-360` (`test_a_refused_resume_never_registers`)
- Modify: `tests/runtime/test_checkpoint.py:1-22` (imports), `:174-209` (rewrite `test_an_error_outside_a_phase_writes_an_escalated_row`), plus one new test
- Modify: `tests/runtime/test_resume.py:13-26` (imports), `:72-86` (`_five`), `:207-225`, `:297-312`, `:319-337`, `:340-358`, `:391-397`, plus two new helpers and one new test

**Interfaces:**
- Consumes: `agent_manager.runtime.stop.StopSignal` (`trigger(story_id) -> bool`, `register`, `unregister`, `.triggered`), `runtime_engine.run_subtask_async(..., stop=StopSignal | None, resume_from=...)`.
- Produces: nothing other tasks import.

- [ ] **Step 1: Rewrite `tests/runtime/test_stop_bridge.py`'s module docstring**

Replace lines 1-14 with:

```python
"""The run's stop, bridged onto the pygents engine (pygents-engine design G8;
supervisor-tree design T5).

Runtime tier: `runtime.engine.run_subtask` / `run_subtask_async` over fake
steps and a real temp store. The one stop is a `StopSignal`, which pauses the
registered pygents agent; its `ON_PAUSE` hook parks the subtask.

Steps run off the loop in `asyncio.to_thread` workers, so a step that fires
the `StopSignal` hands `trigger` to the loop with `call_soon_threadsafe`
(the signal lives on the loop), and ordering between steps uses
`threading.Event`s, never sleeps.
"""
```

- [ ] **Step 2: Delete the two M6 tests in `tests/runtime/test_stop_bridge.py`**

Delete `test_stop_set_during_a_phase_parks_before_the_next` and `test_a_stop_set_before_the_run_parks_before_the_first_phase` (lines 70-126, from `def test_stop_set_during_a_phase_parks_before_the_next(store):` through the blank lines before `# ── StopSignal (supervisor-tree T5, card 364babde)`). `test_a_trigger_from_one_subtask_parks_another_mid_phase` and `test_a_stop_triggered_before_the_run_parks_before_the_first_phase` cover the same behaviour. `_head` (line 66) becomes unused; delete it too.

- [ ] **Step 3: Port `test_a_refused_resume_never_registers` in `tests/runtime/test_stop_bridge.py`**

Replace the whole function (starting `def test_a_refused_resume_never_registers(store):`) with:

```python
async def test_a_refused_resume_never_registers(store):
    loop = asyncio.get_running_loop()
    stop = StopSignal()
    fired = threading.Event()
    ran: list[str] = []

    def fire() -> None:
        stop.trigger("A")
        fired.set()

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        loop.call_soon_threadsafe(fire)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")
        return {"a": 1}

    wf = Workflow("refused", (Step("a", a), Step("b", _ok)))
    await _async_go(wf, store, CARD_ID, stop)
    parked = store.latest_checkpoint(CARD_ID)
    assert parked.reason == "parked"
    changed = Workflow("refused", wf.phases + (Step("c", _ok),))
    spy = _Spy()

    with pytest.raises(runtime_engine.CheckpointMismatch):
        await runtime_engine.run_subtask_async(
            changed,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            clock=lambda: FIXED,
            stop=spy,
            resume_from=parked,
        )

    assert ran == ["a"]
    assert spy.calls == []
```

(`_spy_go` is not used here: it calls the sync `run_subtask`, whose `asyncio.run` cannot run inside this test's loop.)

- [ ] **Step 4: Add imports to `tests/runtime/test_checkpoint.py`**

Replace lines 9-22 (the import block) with:

```python
import asyncio
import itertools
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, Turn, tool

from agent_manager import models, store as store_module
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime import checkpoint
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.workflow.phases import AgentPhase, Step, Workflow
```

- [ ] **Step 5: Rewrite `test_an_error_outside_a_phase_writes_an_escalated_row` in `tests/runtime/test_checkpoint.py`**

Replace the whole function (lines 174-209) with:

```python
class _SecondSaveBreaks:
    """The run's store, except that its second `save_checkpoint` raises.

    The first save is `a`'s `turn` row; the second is the `turn` row
    `before_turn` writes for `b`, so the error comes from the `BEFORE_TURN`
    hook, outside any phase. Every other attribute is the real store's.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._saves = itertools.count()

    def save_checkpoint(self, *args: Any, **kwargs: Any) -> Any:
        if next(self._saves) == 1:
            raise RuntimeError("checkpoint write broke")
        return self._inner.save_checkpoint(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_an_error_outside_a_phase_writes_an_escalated_row(store):
    """The generic `except Exception` branch, not `C.Escalated`: an error raised
    by the hook itself (here the store refusing `b`'s `turn` row) still leaves
    an `escalated` row before the subtask is escalated."""
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        return {"b": 2}

    summary = runtime_engine.run_subtask(
        Workflow("breaks", (Step("a", a), Step("b", b))),
        _SecondSaveBreaks(store),
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
    )

    assert ran == ["a"]
    assert summary.status == "escalated"
    assert "checkpoint write broke" in summary.detail
    assert [(seq, reason) for seq, reason, _ in _rows(store)] == [
        (0, "turn"), (1, "escalated"),
    ]
```

- [ ] **Step 6: Add the `before_turn` guard test to `tests/runtime/test_checkpoint.py`**

Insert directly after the function from Step 5:

```python
async def test_before_turn_saves_every_turn_and_only_on_pause_parks(store):
    """`before_turn` saves `turn` for every turn, a triggered `StopSignal`
    included; the one `parked` row is `on_pause`'s, written before `c`, the
    turn that never ran. Passes before and after the M6 stop is deleted."""
    loop = asyncio.get_running_loop()
    stop = StopSignal()
    fired = threading.Event()
    ran: list[str] = []

    def fire() -> None:
        stop.trigger(STORY_ID)
        fired.set()

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        loop.call_soon_threadsafe(fire)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")
        return {"b": 2}

    def c(card: str) -> dict[str, Any]:
        ran.append("c")
        return {"c": 3}

    summary = await runtime_engine.run_subtask_async(
        Workflow("parks", (Step("a", a), Step("b", b), Step("c", c))),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        stop=stop,
    )

    assert ran == ["a", "b"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before c"
    rows = _rows(store)
    assert [(seq, reason) for seq, reason, _ in rows] == [
        (0, "turn"), (1, "turn"), (2, "parked"),
    ]
    assert [_head(agent) for _, _, agent in rows] == ["a", "b", "c"]
```

- [ ] **Step 7: Add imports to `tests/runtime/test_resume.py`**

Replace lines 13-26 (the import block) with:

```python
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
```

- [ ] **Step 8: Give `_five` an `after_a` hook and add the park helpers in `tests/runtime/test_resume.py`**

Replace `_five` (lines 72-86) with the following, which also adds `_StopAfterA` and `_park_after_a` right after it:

```python
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
```

- [ ] **Step 9: Port `test_a_parked_subtask_resumes` in `tests/runtime/test_resume.py`**

Replace its first three body lines:

```python
    ran: list[str] = []
    wf = _five(ran, set())

    parked_summary = _go(wf, store, should_stop=lambda: ran == ["a"])
```

with:

```python
    ran, wf, parked_summary = _park_after_a(store)
```

Everything after it stays as is.

- [ ] **Step 10: Port `test_a_resume_with_the_stop_still_set_parks_again` in `tests/runtime/test_resume.py`**

Replace its body up to and including `summary = _go(wf, store, resume_from=parked, should_stop=lambda: True)` with:

```python
    # Review Focus 5.
    ran, wf, _ = _park_after_a(store)
    parked = store.latest_checkpoint(CARD_ID)
    still = StopSignal()
    still.trigger("elsewhere")

    summary = _go(wf, store, resume_from=parked, stop=still)
```

The assertions after it stay as they are.

- [ ] **Step 11: Port `test_a_changed_workflow_is_refused` and `test_a_refused_resume_leaves_the_card_runnable` in `tests/runtime/test_resume.py`**

In both functions replace:

```python
    ran: list[str] = []
    wf = _five(ran, set())
    _go(wf, store, should_stop=lambda: ran == ["a"])
```

with:

```python
    ran, wf, _ = _park_after_a(store)
```

(In the second test the later fresh `_go(changed, store)` runs step `a` again; `_StopAfterA` has already fired, so it returns at once and the run ends `done`, as asserted.)

- [ ] **Step 12: Port `test_pending_phase_reads_a_parked_checkpoint` in `tests/runtime/test_resume.py`**

Replace:

```python
    ran: list[str] = []
    _go(_five(ran, set()), store, should_stop=lambda: ran == ["a"])
```

with:

```python
    _park_after_a(store)
```

- [ ] **Step 13: Add the legacy-parked-row guard to `tests/runtime/test_resume.py`**

Append at the end of the file (after `test_a_stop_signal_parked_subtask_resumed_under_a_triggered_stop_parks_again`):

```python
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
```

- [ ] **Step 14: Run the three files**

Run: `uv run pytest tests/runtime/test_stop_bridge.py tests/runtime/test_checkpoint.py tests/runtime/test_resume.py -v`
Expected: all PASS (the source still accepts `should_stop`; nothing here uses it any more).

- [ ] **Step 15: Confirm no hit is left in these files**

Run: `grep -n "should_stop" tests/runtime/test_stop_bridge.py tests/runtime/test_checkpoint.py tests/runtime/test_resume.py`
Expected: no output.

- [ ] **Step 16: Commit**

```bash
git add tests/runtime/test_stop_bridge.py tests/runtime/test_checkpoint.py tests/runtime/test_resume.py
git commit -m "test(runtime): stop subtasks with StopSignal, not should_stop" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```

---

### Task 2: Move `tests/test_engine.py`'s stop block onto `StopSignal`

Test-only; passes before and after the source change.

**Files:**
- Modify: `tests/test_engine.py:11-16` (imports), `:20-36` (imports), `:2318-2622` (the stop block)

**Interfaces:**
- Consumes: `new_engine.run_subtask_async` (the module is imported as `new_engine`), the file's `store` fixture, `run_subtask` fixture (sync `runtime.engine.run_subtask`), `STORY_ID`, `REPO`, `_subtask`, `_workflow`, `FOUR_PHASES`, `THREE_PHASES`, `STOP_MIXED`, `_recording_runner`, `_projected_phases`, `_journalled_phases`, `_subtask_journal_statuses`, `_projected_subtask_status`, `walk`.
- Produces: nothing other tasks import.

- [ ] **Step 1: Add imports**

In `tests/test_engine.py` replace lines 11-16:

```python
import dataclasses
import json
import typing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
```

with:

```python
import asyncio
import dataclasses
import json
import threading
import typing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
```

and after the line `from agent_manager.runtime import engine as new_engine` add:

```python
from agent_manager.runtime.stop import StopSignal
```

- [ ] **Step 2: Replace the stop block**

Replace everything from `class _StopFlag:` (line 2318) through the end of `test_an_escalation_during_the_stop_request_wins_over_the_stop` (line 2621, the last line before `# ── run_one_step: one deterministic phase, run, judged and recorded`) with:

```python
class _StepStop:
    """A `StopSignal` a canned step or fake runner triggers mid-walk.

    Steps and agent runners run in `asyncio.to_thread` workers, and the signal
    lives on the loop, so `fire` hands `trigger` to the loop with
    `call_soon_threadsafe` and blocks on a `threading.Event` until it has run:
    the agent is paused before the phase returns. No sleeps.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.signal = StopSignal()
        self._loop = loop

    def fire(self) -> None:
        fired = threading.Event()

        def trigger() -> None:
            self.signal.trigger(STORY_ID)
            fired.set()

        self._loop.call_soon_threadsafe(trigger)
        if not fired.wait(5):
            raise RuntimeError("the stop was never triggered")


async def test_a_stop_requested_during_phase_three_stops_before_phase_four(store):
    calls: list[str] = []
    stop = _StepStop(asyncio.get_running_loop())

    def make(name: str, *, fire: bool = False):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            if fire:
                stop.fire()
            return {"phase": name}

        return step

    workflow = _workflow(
        FOUR_PHASES,
        {
            "step.alpha": make("alpha"),
            "step.beta": make("beta"),
            "step.gamma": make("gamma", fire=True),
            "step.delta": make("delta"),
        },
    )

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop.signal,
    )

    assert calls == ["alpha", "beta", "gamma"]
    assert summary.status == "stopped"
    assert summary.failed_phase is None
    assert summary.detail == "stopped before delta"
    assert set(summary.results) == {"alpha", "beta", "gamma"}
    assert _projected_phases(store) == [
        ("alpha", "done"),
        ("beta", "done"),
        ("gamma", "done"),
    ]
    assert ("delta", "started") not in _journalled_phases(store)
    assert _subtask_journal_statuses(store) == ["stopped"]
    assert _projected_subtask_status(store) == "stopped"


def test_a_stop_already_requested_runs_no_phase_at_all(store, run_subtask):
    calls: list[str] = []
    stop = StopSignal()
    stop.trigger(STORY_ID)

    def step(card: str) -> dict[str, Any]:
        calls.append("ran")
        return {}

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": step, "step.beta": step, "step.gamma": step}
    )

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop,
    )

    assert calls == []
    assert summary.status == "stopped"
    assert summary.failed_phase is None
    assert summary.detail == "stopped before alpha"
    assert summary.results == {}
    assert _journalled_phases(store) == []
    assert store.connection.execute("SELECT COUNT(*) FROM phases").fetchone()[0] == 0
    assert _subtask_journal_statuses(store) == ["stopped"]
    assert _projected_subtask_status(store) == "stopped"


async def test_a_stop_before_a_deterministic_phase_leaves_it_unstarted(store):
    calls: list[str] = []
    stop = _StepStop(asyncio.get_running_loop())

    def prepare(card: str) -> dict[str, Any]:
        calls.append("prepare")
        return {}

    def finish(card: str) -> dict[str, Any]:
        calls.append("finish")
        return {}

    def agent_runner(phase, context, rendered):
        calls.append(f"agent:{phase.name}")
        stop.fire()
        return {"summary": "explored"}

    workflow = _workflow(STOP_MIXED, {"step.prepare": prepare, "step.finish": finish})

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
        stop=stop.signal,
    )

    assert calls == ["prepare", "agent:explore"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before finish"
    assert summary.results["explore"] == {"summary": "explored"}
    assert _journalled_phases(store) == [("prepare", "started"), ("prepare", "done")]
    assert _projected_subtask_status(store) == "stopped"


async def test_a_stop_before_an_agent_phase_leaves_the_runner_uncalled(store):
    recorded: dict[str, Any] = {}
    stop = _StepStop(asyncio.get_running_loop())

    def prepare(card: str) -> dict[str, Any]:
        stop.fire()
        return {}

    def finish(card: str) -> dict[str, Any]:
        raise AssertionError("no phase after the stop may start")

    workflow = _workflow(STOP_MIXED, {"step.prepare": prepare, "step.finish": finish})

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=_recording_runner(recorded),
        stop=stop.signal,
    )

    assert recorded == {}
    assert summary.status == "stopped"
    assert summary.failed_phase is None
    assert summary.detail == "stopped before explore"
    assert _projected_phases(store) == [("prepare", "done")]
    assert _subtask_journal_statuses(store) == ["stopped"]


async def test_a_stop_before_an_agent_phase_wins_over_a_missing_runner(store):
    """The stop parks the agent before the `explore` turn, and the
    `agent_runner is None` error is raised only inside that turn: a walk that
    stops before its agent phase never reaches it, so it has nothing to
    complain about."""
    stop = _StepStop(asyncio.get_running_loop())

    def prepare(card: str) -> dict[str, Any]:
        stop.fire()
        return {}

    def finish(card: str) -> dict[str, Any]:
        raise AssertionError("no phase after the stop may start")

    workflow = _workflow(STOP_MIXED, {"step.prepare": prepare, "step.finish": finish})

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop.signal,
    )

    assert summary.status == "stopped"
    assert summary.detail == "stopped before explore"
    assert _projected_subtask_status(store) == "stopped"


def test_a_stop_signal_that_never_fires_changes_nothing(store, run_subtask):
    calls: list[str] = []
    stop = StopSignal()

    def make(name: str):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            return {"phase": name}

        return step

    workflow = _workflow(
        THREE_PHASES,
        {"step.alpha": make("alpha"), "step.beta": make("beta"), "step.gamma": make("gamma")},
    )

    summary = run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop,
    )

    assert not stop.triggered
    assert calls == ["alpha", "beta", "gamma"]
    assert summary == walk.SubtaskSummary(
        status="done",
        results={
            "alpha": {"phase": "alpha"},
            "beta": {"phase": "beta"},
            "gamma": {"phase": "gamma"},
        },
    )
    assert _journalled_phases(store) == [
        ("alpha", "started"),
        ("alpha", "done"),
        ("beta", "started"),
        ("beta", "done"),
        ("gamma", "started"),
        ("gamma", "done"),
    ]
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done"), ("gamma", "done")]
    assert _subtask_journal_statuses(store) == ["done"]
    assert _projected_subtask_status(store) == "done"


async def test_an_escalation_during_the_stop_request_wins_over_the_stop(store):
    calls: list[str] = []
    stop = _StepStop(asyncio.get_running_loop())

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        return {}

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        stop.fire()
        raise OSError("disk went away")

    def gamma(card: str) -> dict[str, Any]:
        calls.append("gamma")
        return {}

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": alpha, "step.beta": beta, "step.gamma": gamma}
    )

    summary = await new_engine.run_subtask_async(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        stop=stop.signal,
    )

    assert stop.signal.triggered
    assert calls == ["alpha", "beta"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "beta"
    assert "disk went away" in summary.detail
    assert _subtask_journal_statuses(store) == ["escalated"]
    assert _projected_subtask_status(store) == "escalated"
```

`test_should_stop_is_checked_only_at_visited_phases` is deleted by this replacement: it tested the polling itself, which is gone. `_skipping_workflow` stays; three other tests (around lines 869, 888, 905) still use it.

- [ ] **Step 3: Run the ported tests**

Run: `uv run pytest tests/test_engine.py -k "stop" -v`
Expected: the seven tests above PASS.

- [ ] **Step 4: Run the whole file and confirm no hit is left**

Run: `uv run pytest tests/test_engine.py -q && grep -n "should_stop\|_StopFlag" tests/test_engine.py`
Expected: all PASS, then no grep output.

- [ ] **Step 5: Commit**

```bash
git add tests/test_engine.py
git commit -m "test(engine): stop the walk with StopSignal, not should_stop" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```

---

### Task 3: Drop `should_stop` from the CLI drivers

**Files:**
- Modify: `src/agent_manager/cli.py:628-731` (`drive_subtask_async`, `drive_subtask`)
- Test: `tests/test_cli.py:15-25` (imports), `:1413-1479`, `:1536-1585`, `:1642-1690`, `:4358-4412`, plus one new test

**Interfaces:**
- Consumes: `cli.drive_subtask_async(..., stop: StopSignal | None = None, resume_from=None)`; `runtime_engine.run_subtask_async` still accepts `should_stop` until Task 5, but the CLI stops sending it here.
- Produces: `cli.drive_subtask(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, resume_from=None) -> SubtaskDrive` and `cli.drive_subtask_async(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, stop=None, resume_from=None) -> SubtaskDrive`. Task 4's protocol test compares against `drive_subtask_async`'s parameter list.

- [ ] **Step 1: Add imports to `tests/test_cli.py`**

After `import asyncio` (line 15) add `import inspect`; after `import sys` (line 21) add `import threading`, so the block reads:

```python
import asyncio
import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timezone
```

- [ ] **Step 2: Port `test_drive_subtask_hands_should_stop_to_the_engine` (test-only, passes today)**

Rename it and replace its docstring and the driver call. The new head of the function:

```python
@requires_git
@requires_brd
def test_drive_subtask_async_hands_a_triggered_stop_to_the_engine(project, cards):
    """Addendum P4, on the one stop: the driver passes the run's `StopSignal`
    straight through. With the signal already triggered, the first phase of
    `TASK` never starts, so the fake runner is never called and no worktree is
    made."""
```

Directly after `seen: list[tuple[str, dict[str, Any]]] = []` add:

```python
    stop = StopSignal()
    stop.trigger(parent.id)
```

and replace the `drive = cli.drive_subtask(...)` call with:

```python
        drive = asyncio.run(
            cli.drive_subtask_async(
                store=store,
                run_id=run_id,
                card=card,
                parent=parent,
                subtask=subtask,
                repo_dir=root,
                runner_factory=lambda **kwargs: fake_runner(seen),
                stop=stop,
            )
        )
```

The setup and every assertion (`"stopped"`, `failed_phase is None`, `"stopped before worktree"`, `seen == []`, no worktree, subtask row `["stopped"]`) stay unchanged.

- [ ] **Step 3: Port `_park_pygents` (test-only, passes today)**

Change its docstring's first line to:

```python
    """A milestone run whose one subtask the run's `StopSignal` parked on pygents after `spec`.
```

After the existing `seen: list[str] = []` line add:

```python
    stop = StopSignal()
    record = _resume_factory(seen)

    def stopping_factory(**kwargs: Any):
        # Called by `drive_subtask_async` on its loop, where the signal lives.
        loop = asyncio.get_running_loop()
        run = record(**kwargs)

        def runner(phase, context, rendered):
            result = run(phase, context, rendered)
            if phase.name == "spec":
                # The runner is in a `to_thread` worker: hand `trigger` to the
                # loop and wait until it has run, so the agent is paused
                # before this phase returns.
                fired = threading.Event()

                def fire() -> None:
                    stop.trigger(parent.id)
                    fired.set()

                loop.call_soon_threadsafe(fire)
                if not fired.wait(5):
                    raise RuntimeError("the stop was never triggered")
            return result

        return runner
```

and replace the `drive = cli.drive_subtask(...)` call with:

```python
        drive = asyncio.run(
            cli.drive_subtask_async(
                store=opened,
                run_id=run_id,
                card=card,
                parent=parent,
                subtask=subtask,
                repo_dir=root,
                runner_factory=stopping_factory,
                stop=stop,
            )
        )
```

The two assertions after it (`"stopped"`, `"stopped before validate_spec"`) stay.

- [ ] **Step 4: Run the two ported tests to confirm they still pass on today's source**

Run: `uv run pytest tests/test_cli.py -k "hands_a_triggered_stop or parked_milestone_subtask_resumes" -v`
Expected: PASS (both need `git` and `brd`; if they are skipped, note it and rely on Step 9's full run on a machine with both).

- [ ] **Step 5: Write the failing tests**

In `test_drive_subtask_walks_task_with_the_same_arguments` delete:

```python
    def stop() -> bool:
        return False

```

delete the `should_stop=stop,` argument, and delete `"should_stop": stop,` from the expected `kwargs` dict (keep `"stop": None`).

In `test_drive_subtask_async_hands_stop_to_the_engine` delete `"should_stop": None,` from the expected `kwargs` dict.

Directly after `test_drive_subtask_walks_task_with_the_same_arguments` add:

```python
DRIVER_KEYWORDS = [
    "store",
    "run_id",
    "card",
    "parent",
    "subtask",
    "repo_dir",
    "commands",
    "allow_no_verification",
    "runner_factory",
]


def test_the_drivers_take_a_stop_signal_and_no_other_stop():
    """T5: the `StopSignal` is the only stop. The sync driver takes none; a
    caller that must stop awaits `drive_subtask_async(stop=...)`. Any other
    keyword is a `TypeError`."""
    assert list(inspect.signature(cli.drive_subtask).parameters) == [
        *DRIVER_KEYWORDS,
        "resume_from",
    ]
    assert list(inspect.signature(cli.drive_subtask_async).parameters) == [
        *DRIVER_KEYWORDS,
        "stop",
        "resume_from",
    ]
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "walks_task_with_the_same_arguments or hands_stop_to_the_engine or no_other_stop" -v`
Expected: 3 FAIL: the two dict comparisons show an extra `'should_stop'` key, and the signature test shows `'should_stop'` in both parameter lists.

- [ ] **Step 7: Remove the parameter from `drive_subtask_async` in `src/agent_manager/cli.py`**

Delete the line `    should_stop: Callable[[], bool] | None = None,` from its signature. Replace the docstring paragraph

```
    `stop` (T5) is the run's `StopSignal`, handed to the engine as is.
    `should_stop` is here only so `drive_subtask` can forward it; Task 3.3
    removes it. `resume_from` joins the walk's keywords only when given, so a
    fresh walk is called exactly as before.
```

with:

```
    `stop` (T5) is the run's `StopSignal`, handed to the engine as is; it is
    the only stop. `resume_from` joins the walk's keywords only when given, so
    a fresh walk is called exactly as before.
```

and delete the line `        "should_stop": should_stop,` from the `walk` dict.

- [ ] **Step 8: Remove the parameter from `drive_subtask` in `src/agent_manager/cli.py`**

Delete the line `    should_stop: Callable[[], bool] | None = None,` from its signature and the line `            should_stop=should_stop,` from the `drive_subtask_async(...)` call inside it. Replace the docstring sentences

```
    nothing. An escalation is `summary.status == "escalated"`, not an exception.
    `should_stop` goes straight to the engine; a stop is
    `summary.status == "stopped"`.
```

with:

```
    nothing. An escalation is `summary.status == "escalated"`, not an exception.
    It takes no stop: a caller that must stop awaits
    `drive_subtask_async(stop=...)`, where a stop is `summary.status == "stopped"`.
```

`Callable` stays imported: `run_card`'s `clock: Callable[[], datetime]` still uses it.

- [ ] **Step 9: Run the CLI tests**

Run: `uv run pytest tests/test_cli.py -q`
Expected: all PASS.

- [ ] **Step 10: Confirm no hit is left**

Run: `grep -n "should_stop" src/agent_manager/cli.py tests/test_cli.py`
Expected: no output.

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): the drivers take StopSignal as their only stop" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```

---

### Task 4: Drop `should_stop` from the orchestrator's `Driver` protocol

Only the protocol's parameter and its docstring sentence change; scheduling and base-building code is not touched.

**Files:**
- Modify: `src/agent_manager/orchestrate.py:261-289` (`Driver`)
- Test: `tests/test_orchestrate.py:652-683` (`FakeDriver.__call__`), `:982-996` (`GatedDriver.__call__`), `:1188-1204`, plus one new test

**Interfaces:**
- Consumes: `cli.drive_subtask_async`'s parameter list from Task 3.
- Produces: `orchestrate.Driver.__call__(self, *, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, stop=None, resume_from=None) -> cli.SubtaskDrive`.

- [ ] **Step 1: Drop the parameter from the fake drivers (passes today; the lane never passes it)**

In `FakeDriver.__call__` delete the line `        should_stop=None,` and the line `                "should_stop": should_stop,` from the recorded dict. In `GatedDriver.__call__` delete the line `        should_stop=None,`.

- [ ] **Step 2: Rewrite the lane test around line 1190**

Replace `test_no_driver_resolves_to_cli_drive_subtask_async_at_call_time` with:

```python
@requires_git
@requires_brd
def test_no_driver_resolves_to_cli_drive_subtask_async_at_call_time(project, monkeypatch):
    """The default driver is the awaitable one (T3), read off `cli` when the run
    starts, never bound at import. The lane hands it the run's StopSignal and
    no other stop: `FakeDriver`'s keywords are closed, so any other stop
    keyword would be a `TypeError` and the run would not finish `done`."""
    shape = _milestone(project, {"A": 1})
    (a1,) = shape["subtasks"]["A"]
    fake = FakeDriver()
    monkeypatch.setattr(cli, "drive_subtask_async", fake)

    result = _run(project, shape["milestone"], None)

    assert [call["card"] for call in fake.calls] == [a1]
    assert isinstance(fake.calls[0]["stop"], StopSignal)
    assert result["done"] is True
```

- [ ] **Step 3: Write the failing test**

Directly after that test add:

```python
def test_the_driver_protocol_mirrors_drive_subtask_async():
    """`Driver` is `cli.drive_subtask_async`'s keyword signature, so the one
    stop either takes is the `StopSignal`."""
    protocol = list(inspect.signature(orchestrate.Driver.__call__).parameters)

    assert protocol[0] == "self"
    assert protocol[1:] == list(inspect.signature(cli.drive_subtask_async).parameters)
```

- [ ] **Step 4: Run it to verify it fails**

Run: `uv run pytest tests/test_orchestrate.py::test_the_driver_protocol_mirrors_drive_subtask_async -v`
Expected: FAIL, the protocol's list has an extra `'should_stop'` between `'runner_factory'` and `'stop'`.

- [ ] **Step 5: Edit `Driver` in `src/agent_manager/orchestrate.py`**

Replace the docstring paragraph

```
    `stop` is the run's `StopSignal`, passed on every call. `should_stop` stays
    until Task 3.3 deletes it; the lane never passes it. `resume_from` (card
    02890d5d) is passed only when a relaunch found a checkpoint to continue,
    so a driver written before it keeps working.
```

with:

```
    `stop` is the run's `StopSignal`, passed on every call; it is the only
    stop. `resume_from` (card 02890d5d) is passed only when a relaunch found a
    checkpoint to continue, so a driver written before it keeps working.
```

and delete the line `        should_stop: Callable[[], bool] | None = None,` from `__call__`. `Callable` stays imported (`node_coroutine` and `run_milestone`'s `clock` use it).

- [ ] **Step 6: Run the orchestrate tests**

Run: `uv run pytest tests/test_orchestrate.py -q`
Expected: all PASS.

- [ ] **Step 7: Confirm no hit is left**

Run: `grep -n "should_stop" src/agent_manager/orchestrate.py tests/test_orchestrate.py`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat(orchestrate): Driver takes StopSignal as its only stop" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```

---

### Task 5: Delete `should_stop` from the engine, `RunDeps` and `before_turn`

**Files:**
- Modify: `src/agent_manager/runtime/engine.py:39-52` (`pending_phase` docstring), `:55-91` (`run_subtask`), `:94-130` (`run_subtask_async` signature and docstring), `:183-185` (`RunDeps(...)`), `:203-205` (comment in `_run`)
- Modify: `src/agent_manager/runtime/state.py:20-32` (`RunDeps`)
- Modify: `src/agent_manager/runtime/checkpoint.py:1-15` (module docstring), `:38-43` (`Parked` docstring), `:61-71` (`before_turn`)
- Test: `tests/runtime/test_stop_bridge.py` (imports and two new tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: `run_subtask(workflow, store, *, story_id, subtask, repo_dir, commands=(), card=None, parent_story=None, extra_context=None, agent_runner=None, clock=walk._utcnow, stop=None, resume_from=None)` and the same list for `run_subtask_async`; `RunDeps(workflow, store, story_id, subtask, agent_runner, clock, stop=None, warnings=[], skipped=[], running=None)`.

- [ ] **Step 1: Write the failing tests**

In `tests/runtime/test_stop_bridge.py` add `import inspect` after `import asyncio`, and after `from agent_manager.runtime.errors import EngineError` add:

```python
from agent_manager.runtime.state import RunDeps
```

Then add, directly before the `# ── StopSignal (supervisor-tree T5, card 364babde)` section header:

```python
WALK_PARAMETERS = [
    "workflow",
    "store",
    "story_id",
    "subtask",
    "repo_dir",
    "commands",
    "card",
    "parent_story",
    "extra_context",
    "agent_runner",
    "clock",
    "stop",
    "resume_from",
]


@pytest.mark.parametrize(
    "walk",
    [runtime_engine.run_subtask, runtime_engine.run_subtask_async],
    ids=["sync", "async"],
)
def test_the_walk_takes_a_stop_signal_and_no_other_stop(walk):
    """T5: `stop` is the only stop; any other stop keyword is a `TypeError`."""
    assert list(inspect.signature(walk).parameters) == WALK_PARAMETERS


def test_run_deps_binds_the_stop_signal_seventh():
    """The engine builds `RunDeps` positionally up to `clock`; the seventh
    field must be `stop`, so nothing after `clock` shifts."""
    stop = StopSignal()

    deps = RunDeps("workflow", "store", STORY_ID, "subtask", None, lambda: FIXED, stop)

    assert deps.stop is stop
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/runtime/test_stop_bridge.py -k "no_other_stop or seventh" -v`
Expected: 3 FAIL: both signature lists contain `'should_stop'` before `'stop'`, and `deps.stop` is `None` (the seventh positional argument binds to `should_stop` today).

- [ ] **Step 3: Remove the field from `RunDeps` in `src/agent_manager/runtime/state.py`**

Replace

```python
    clock: Callable[[], Any]
    should_stop: Callable[[], bool] | None = None
    stop: StopSignal | None = None
    """The milestone's `StopSignal` (supervisor-tree T5). Declared after
    `should_stop` so the positional construction still binds; `should_stop`
    stays until Task 3.3."""
```

with:

```python
    clock: Callable[[], Any]
    stop: StopSignal | None = None
    """The milestone's `StopSignal` (supervisor-tree T5), the run's only stop.
    `engine._run` registers the agent with it for the life of `run()`."""
```

`Callable` stays imported (`agent_runner`, `clock`).

- [ ] **Step 4: Remove the parameter from `src/agent_manager/runtime/engine.py`**

In `run_subtask` delete the line `    should_stop: Callable[[], bool] | None = None,` from the signature and the line `            should_stop=should_stop,` from the `run_subtask_async(...)` call.

In `run_subtask_async` delete the line `    should_stop: Callable[[], bool] | None = None,` from the signature, and replace the docstring's first two paragraphs

```
    Every turn is saved as a `turn` checkpoint before it runs, and the run ends
    with a `done` or `escalated` one (`runtime/checkpoint.py`). Two stops park
    the subtask: a `parked` checkpoint is saved, the next phase is not
    started, and the subtask is recorded `stopped before <phase>`.

    - `should_stop` (M6, until Task 3.3) is asked before every turn.
    - `stop`, a `StopSignal`, has the agent registered for the run and
      unregistered on every exit. A trigger pauses it; the turn in flight
      finishes and the agent parks before the next one. A trigger after the
      last phase finished changes nothing: the subtask ends `done`.
```

with:

```
    Every turn is saved as a `turn` checkpoint before it runs, and the run ends
    with a `done` or `escalated` one (`runtime/checkpoint.py`).

    `stop`, a `StopSignal`, is the only stop. The agent is registered with it
    for the run and unregistered on every exit. A trigger pauses the agent:
    the turn in flight finishes, a `parked` checkpoint is saved, the next
    phase is not started, and the subtask is recorded `stopped before
    <phase>`. A trigger after the last phase finished changes nothing: the
    subtask ends `done`.
```

Replace the `RunDeps` construction

```python
        deps = RunDeps(
            workflow, store, story_id, subtask, agent_runner, clock, should_stop, stop=stop
        )
```

with:

```python
        deps = RunDeps(workflow, store, story_id, subtask, agent_runner, clock, stop=stop)
```

In `_run` replace the comment

```python
        # The stop, raised by the BEFORE_TURN or ON_PAUSE hook after it saved
        # `parked`: no further row, so that one stays the newest.
```

with:

```python
        # The stop, raised by the ON_PAUSE hook after it saved `parked`: no
        # further row, so that one stays the newest.
```

In `pending_phase`'s docstring replace

```
    touching a pygents structure itself (card 02890d5d). The next turn is the
    turn in flight if there was one, else the queue head -- the reading the
    `BEFORE_TURN` hook makes. A `done` row holds no turn, and neither does an
```

with:

```
    touching a pygents structure itself (card 02890d5d). The next turn is the
    turn in flight if there was one, else the queue head. A `done` row holds
    no turn, and neither does an
```

`Callable` stays imported (`clock`).

- [ ] **Step 5: Remove the stop branch from `before_turn` in `src/agent_manager/runtime/checkpoint.py`**

Replace the module docstring's first two paragraphs (lines 1-15)

```
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
```

with:

```
"""Checkpoint hooks (pygents-engine design G5, G8, §6; supervisor-tree T5).

Every turn of a subtask's agent is saved by `before_turn` as
`Agent.to_dict()` in the store's `checkpoints` table *before* it runs. The
after-run rows (`done`, `escalated`) are written by `runtime/engine.py`
through `save`.

The run's one stop is a `StopSignal` (`runtime/stop.py`) that pauses the
agent; pygents then fires `ON_PAUSE` at the top of its loop, between turns,
and `on_pause` saves `parked` and raises `Parked`, which propagates out of
`agent.run()` and ends it cleanly -- nothing breaks or returns out of the
loop. The raise is what ends the run: `pause()` alone would leave `run()`
waiting forever for a `resume()`.
```

Replace the `Parked` docstring line

```python
    """The run's stop was set: the subtask stopped before `.before_phase`."""
```

with:

```python
    """The run's `StopSignal` parked the subtask before `.before_phase`."""
```

Replace `before_turn`

```python
@hook(AgentHook.BEFORE_TURN, tags={"subtask"})
async def before_turn(agent: Any) -> None:
    deps = current_run.get(None)
    if deps is None:
        return
    snapshot = agent.to_dict()
    head = (snapshot["current_turn"] or snapshot["queue"][0])["kwargs"]["phase"]
    if deps.should_stop is not None and deps.should_stop():
        save(agent, "parked")
        raise Parked(head)
    save(agent, "turn")
```

with:

```python
@hook(AgentHook.BEFORE_TURN, tags={"subtask"})
async def before_turn(agent: Any) -> None:
    """Save the turn about to run. `save` writes nothing with no run set."""
    save(agent, "turn")
```

`current_run` stays imported: `save` and `on_pause` use it. `on_pause` is not touched.

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/runtime/test_stop_bridge.py -k "no_other_stop or seventh" -v`
Expected: 3 PASS.

- [ ] **Step 7: Run the runtime and engine tiers**

Run: `uv run pytest tests/runtime tests/test_engine.py -q`
Expected: all PASS, including `test_hook_without_a_run_does_nothing`, `test_every_turn_is_checkpointed_before_it_runs`, `test_before_turn_saves_every_turn_and_only_on_pause_parks` and `test_an_error_outside_a_phase_writes_an_escalated_row`.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/runtime/engine.py src/agent_manager/runtime/state.py src/agent_manager/runtime/checkpoint.py tests/runtime/test_stop_bridge.py
git commit -m "feat(runtime): StopSignal is the only stop; delete should_stop" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```

---

### Task 6: Prove it is gone and the whole suite is green

**Files:**
- None, unless a step below finds a hit.

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: the card's done condition.

- [ ] **Step 1: Grep the source and tests**

Run: `grep -rn "should_stop" src tests`
Expected: no output. If anything prints, it is a hit Tasks 1-5 missed: switch it to a `StopSignal` the way the task that owns its file did, or delete it, then re-run this step.

- [ ] **Step 2: Check the M6 references in comments are gone too**

Run: `grep -rn "Task 3.3\|_StopFlag\|BEFORE_TURN stop" src tests`
Expected: no output. A remaining `Task 3.3` is a stale comment about this deletion; reword it to describe the single `StopSignal` stop.

- [ ] **Step 3: Run the full default suite**

Run: `uv run pytest`
Expected: all PASS (the `e2e`-marked real-harness test is deselected by `addopts`, as always; every other test under `tests/e2e/` runs).

- [ ] **Step 4: Commit any fix-ups from Steps 1-2**

Only if Steps 1-2 changed a file:

```bash
git add -A src tests
git commit -m "chore: drop the last should_stop references" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```
