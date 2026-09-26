<!-- task-pipeline: validated -->
# Checkpoint every turn and park on the run's stop (card 921ed349)

Subtask of story a6c7bff3 "Checkpoints and resume". Narrows plan Task 4.2 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1177-1251`) under the milestone spec `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` G5 (:91-96), G8 (:105-108), §6 (:262-300). The plan's reference implementation is prescriptive; follow it.

## Scope

In scope:

- New `src/agent_manager/runtime/checkpoint.py` with `Parked`, `save`, and the module-level `before_turn` hook.
- `src/agent_manager/runtime/engine.py`: import `checkpoint` so the hook registers; wire the `Parked` catch, the `done` save and the `escalated` saves into `_run`; update the `run_subtask` docstring, which currently says `should_stop` "is not yet read -- the stop bridge is plan Task 4.2".
- Tests `tests/runtime/test_checkpoint.py` and `tests/runtime/test_stop_bridge.py`.

Out of scope: resume from a checkpoint, `CheckpointMismatch`, `Agent.from_dict` (card 5698e4f6); the `--engine` flag and orchestrate/integration plumbing (7fdec762); `am resume --engine pygents`, orphan-attempt marking, digest-mismatch exit 3, milestone relaunch (02890d5d); the store's `checkpoints` table and methods, which are already on this branch at `store.py:842-906` (3d4947e8, done; do not touch); the supervisor-tree/`pause()` stop; exactly-once phases; upstream pygents fixes (spec §11).

## Observable behavior

`checkpoint.Parked(Exception)`: built with `before_phase: str`, exposes `.before_phase`, message `"stopped before <phase>"`.

`checkpoint.save(agent, reason: str) -> None`: reads `current_run.get(None)`. With no run set it returns and writes nothing. Otherwise it calls `deps.store.save_checkpoint(deps.subtask.card_id, workflow=deps.workflow.name, digest=deps.workflow.digest(), reason=reason, agent=agent.to_dict(), saved_at=deps.clock())`.

`checkpoint.before_turn(agent)`: a module-level `async` function registered with `@hook(AgentHook.BEFORE_TURN, tags={"subtask"})`. With no run set it does nothing: no `LookupError` and no row. Otherwise it takes the head phase name from `agent.to_dict()`, as `(snapshot["current_turn"] or snapshot["queue"][0])["kwargs"]["phase"]`. If `deps.should_stop` is set and returns true, it saves `parked` and raises `Parked(head)`. Otherwise it saves `turn`.

`engine._run` after `agent.run()`:
- Clean end: `_collect`, then `save(agent, "done")`, then the existing `_record_subtask_status`. The summary does not change.
- `checkpoint.Parked`: this except clause goes before the generic `except Exception`. It calls `_collect(agent, deps, summary)` and then returns `old._stop(summary, deps.store, deps.story_id, deps.subtask, parked.before_phase)`. The result is `status == "stopped"` and `detail == "stopped before <phase>"`. No further row is written, so the `parked` row the hook already saved stays the newest.
- `except C.Escalated` and the generic `except Exception`: call `save(agent, "escalated")` before `old._escalate(...)`, and leave everything else as it is.
- `except old.EngineError: raise` stays unchanged and writes no row. It is a re-raised wiring error, not an escalation branch. The plan says "both escalation branches", so this card follows the plan. Spec §6's wording "any other `Exception`" could be read to include it; that ambiguity is flagged, not resolved, here.
- A `BaseException` such as `KeyboardInterrupt` or `CancelledError` is never caught and writes nothing. The last `turn` row stands.
- `current_run` is still reset in `finally`. Every `save` in `_run` has to happen while `current_run` is still set, meaning inside the `try`, or it silently becomes a no-op. On the clean path that means the `done` save must also happen before the reset, so it cannot stay on the line after the `finally` where `_collect` currently runs.

## Binding constraints

- Only `src/agent_manager/runtime/` imports pygents; `workflow/phases.py` never does.
- Never break or return out of `agent.run()`. The stop ends the run only by `Parked` propagating out of it.
- Never checkpoint at AFTER_TURN. Hooks are module-level only, never closures (spec §11, the `HookRegistry` collision note).
- G10: `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads stay byte-for-byte the same. Checkpoints are only a side channel.
- `orchestrate.py`'s `RunStop` `threading.Event` is not redesigned. The hook only calls `should_stop`.
- A fake `claude` or fake step in a test knows only what its brief tells it.
- The whole default suite must stay green, including `tests/e2e`, on both engines.

## Error paths

- A hook firing with no run set (a stray agent tagged `subtask`) is a no-op.
- `save_checkpoint` failures, such as a store `CHECK` violation, are not caught. They propagate like any other store error: out of the hook, they end the run through the generic `Exception` branch. There is no special handling.
- A stop that is already set before the first turn parks before the first phase. This follows from the hook's rule and needs no extra code.

## Tests

Placement rule, from the findings: tests mirror the source path under `tests/`, and `tests/e2e` is reserved for full-pipeline harness wiring. All tests below exercise `src/agent_manager/runtime/` at the hook and engine level. They are driven through `runtime.engine.run_subtask` with fake steps and a real temporary `Store`, so they belong in the mirrored `tests/runtime/` tier and not in `tests/e2e`.

`tests/runtime/test_checkpoint.py` (mirrored tier `tests/runtime/`):
1. `test_every_turn_is_checkpointed_before_it_runs`: a 3-step workflow writes rows with seq 0, 1, 2, all with reason `turn`. Each row's stored agent has that phase at its queue head (`current_turn` or `queue[0]`). One final `done` row follows (seq 3).
2. `test_an_escalation_writes_an_escalated_row`: a fake step that escalates leaves the newest row with reason `escalated`. The summary and escalation payload are unchanged from today.
3. `test_hook_without_a_run_does_nothing`: a stray `Agent("stray", "t", [some_tool], tags=["subtask"])` with a queued `Turn` is run to the end with no `current_run` set. There is no `LookupError` and no checkpoint row.

`tests/runtime/test_stop_bridge.py` (mirrored tier `tests/runtime/`):
4. `test_stop_set_during_a_phase_parks_before_the_next`: step `a` calls `stop.set()` on a `threading.Event`, and `should_stop=stop.is_set`. Expected: `summary.status == "stopped"`, `summary.detail == "stopped before b"`, the newest checkpoint has reason `parked` with queue head `b`, and `b` never ran.

## Verification

`uv run pytest` must be fully green, per CLAUDE.md and plan Task 4.2 Step 4. The exploration findings were cut off at "VERIFICATION COMMANDS (given, returned exactly): ful". The upstream stage went over its 8000-character brief, and any further commands it listed are unknown. This spec uses only the command in CLAUDE.md.

Commit: `feat(runtime): checkpoint every turn and park on the run's stop`.

---

# Checkpoint Every Turn and Park on the Run's Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write an `Agent.to_dict()` checkpoint row before every turn of a pygents subtask run, a `done`/`escalated` row when the run ends, and turn the run's `should_stop` into a clean `parked` stop before the next phase.

**Architecture:** A new module `src/agent_manager/runtime/checkpoint.py` holds `Parked`, `save` and one module-level global `BEFORE_TURN` hook tagged `subtask`; it finds the run through `runtime.state.current_run`. `runtime/engine.py` imports it (which registers the hook) and, inside `_run`'s `try` while `current_run` is still set, catches `Parked` into `old._stop`, saves `escalated` in both escalation branches and saves `done` in a new `else:` clause.

**Tech Stack:** Python 3.12, pygents 0.7.0 (`Agent`, `hook`, `AgentHook`, `HookRegistry`), SQLite via `agent_manager.store.Store`, pytest with `pytest-asyncio` in `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/task-checkpoint-every-turn-921ed349-design.md` (prepended verbatim above), under milestone spec `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` §6 and plan `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 4.2.

**Upstream truncation notice:** both the spec author's summary and the exploration findings handed to this stage were truncated (2224 > 2000 and 8054 > 8000 characters). The exploration findings were cut off inside "VERIFICATION COMMANDS (given, returned exactly): ful". That truncation is itself evidence the upstream stage over-ran its brief; this plan does not guess the missing text and uses only `uv run pytest` (CLAUDE.md, and the command the task prompt gave).

## Deviation from the spec (read before Task 1)

The spec says `save` passes `saved_at=deps.clock()`. This plan passes `saved_at=old._utcnow()` (the wall clock, `src/agent_manager/engine.py:230-231`) instead. Reason: `deps.clock` is the *injected* clock the old engine uses to stamp phase rows, and `tests/test_engine.py:1458-1507` (`test_a_phase_is_timed_with_the_injected_clock`, which runs today on both the `yaml` and `pygents` params) hands in an iterator of exactly four ticks and asserts the phase rows get them in order. A `BEFORE_TURN` save that reads `deps.clock()` would consume tick 0 before `alpha` starts, shift every phase stamp and exhaust the iterator, so that test's `[pygents]` param would fail. That violates two binding rules of the same spec at once: G10 (phase rows byte-identical across engines) and "the whole default suite must stay green". The milestone spec §6 does not prescribe the source of `saved_at`, so reading the wall clock is within it; `saved_at` is used only to order rows across runs in `latest_open_checkpoint`. Task 2 adds a guard test (`test_a_checkpoint_never_reads_the_injected_clock`) that pins this. If a reviewer rejects the deviation, the alternative is changing that existing test, which would break G10 parity, so do not do that silently.

## Global Constraints

- Only `src/agent_manager/runtime/` imports pygents; `workflow/phases.py` never does.
- Never break or return out of `agent.run()`; the stop ends the run only by `Parked` propagating out of it.
- Never checkpoint at `AFTER_TURN`. Hooks are module-level only, never closures (milestone spec §11, `HookRegistry` collision note).
- G10: `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads stay byte-for-byte the same. Checkpoints are only a side channel.
- `orchestrate.py`'s `RunStop` `threading.Event` is not redesigned; the hook only calls `should_stop`.
- A fake step in a test knows only what its brief tells it.
- `except old.EngineError: raise` writes no row. A `BaseException` writes nothing.
- Do not touch `store.py` (3d4947e8), and do not implement resume, `CheckpointMismatch`, `Agent.from_dict`, `--engine` or `am resume` (sibling cards 5698e4f6, 7fdec762, 02890d5d).
- Leave the `should_stop` skip in `tests/test_engine.py:256-259` in place: `test_an_exception_from_should_stop_propagates_and_records_nothing` (`tests/test_engine.py:2775`) expects the yaml engine's propagate-and-record-nothing behavior, which the pygents engine does not have (an exception from the hook escalates), so un-skipping belongs to whichever card decides that parity.
- Verification: `uv run pytest` fully green.
- Final commit message: `feat(runtime): checkpoint every turn and park on the run's stop`.

## Review Focus

- A phase result that is not JSON-able after `context.encode` (a `set`, a custom object): `save_checkpoint`'s `json.dumps` raises `TypeError` at the next `BEFORE_TURN`, the generic branch then calls `save(agent, "escalated")`, which raises the same `TypeError` from inside the `except` clause and escapes `run_subtask` with the subtask left `started`. The spec says store failures get no special handling, so this plan does not add any; the reviewer should confirm that is acceptable and that no test in the suite returns such a result (Task 2 Step 7 checks the full suite for exactly this failure).
- An injected clock must not be read by checkpoints (see the deviation above); pinned by `test_a_checkpoint_never_reads_the_injected_clock` in Task 2.
- A stop already set before the first turn parks before the first phase, with a single `parked` row at seq 0 and no phase run; pinned by `test_a_stop_set_before_the_run_parks_before_the_first_phase` in Task 2.
- An exception from the generic branch (not `C.Escalated`) still writes an `escalated` row before `_escalate`; pinned by `test_an_error_outside_a_phase_writes_an_escalated_row` in Task 2 (triggered by a `should_stop` that raises on its second call).
- The global hook fires for every agent tagged `subtask` in the process, including the ones `tests/runtime/test_compile.py` drives directly with `current_run` set, so those tests now write `turn` rows to their own temp stores; they must still pass unchanged (Task 2 Step 7 runs them).

---

### Task 1: The checkpoint module and its no-run guard

**Files:**
- Create: `src/agent_manager/runtime/checkpoint.py`
- Test: `tests/runtime/test_checkpoint.py` (new; mirrored tier `tests/runtime/`, next to `tests/runtime/test_compile.py`, and covered by the autouse `fresh_pygents` fixture in `tests/runtime/conftest.py`)

**Interfaces:**
- Consumes: `agent_manager.runtime.state.current_run: ContextVar[RunDeps]` and `RunDeps` fields `workflow` (`.name`, `.digest()`), `store` (`.save_checkpoint(card_id, *, workflow, digest, reason, agent, saved_at) -> Checkpoint`), `subtask` (`.card_id`), `should_stop: Callable[[], bool] | None`; `agent_manager.engine._utcnow() -> datetime`; pygents `hook`, `AgentHook.BEFORE_TURN`.
- Produces: `checkpoint.Parked(Exception)` with `__init__(self, before_phase: str)`, attribute `.before_phase: str`, `str(exc) == f"stopped before {before_phase}"`; `checkpoint.save(agent: Any, reason: str) -> None`; `checkpoint.before_turn` (the global `Hook` object wrapping `async def before_turn(agent) -> None`).

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime/test_checkpoint.py` with this full content (the imports and helpers also serve Task 2's tests, which are appended to this file):

```python
"""Checkpoints written by the pygents engine (pygents-engine design G5, §6).

Runtime tier: `runtime.engine.run_subtask` drives fake steps that know only
the arguments they are bound, over a real temp SQLite projection plus a real
temp JSONL journal, built as tests/runtime/test_compile.py builds it. The
checkpoint rows are read straight from the store's `checkpoints` table.
"""

import itertools
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, Turn, tool

from agent_manager import models, store as store_module
from agent_manager.runtime import checkpoint
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow.phases import Step, Workflow

RUN_ID = "run-2026-09-26-02"
STORY_ID = "a6c7bff3"
CARD_ID = "921ed349"
REPO = Path("/repo")
FIXED = datetime(2026, 9, 26, tzinfo=timezone.utc)


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
        branch=f"m6/task-checkpoint-every-turn-{CARD_ID}",
        base_branch="m6/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _rows(opened) -> list[tuple[int, str, dict]]:
    """Every checkpoint row in the store, oldest first, agent decoded."""
    return [
        (row[0], row[1], json.loads(row[2]))
        for row in opened.connection.execute(
            "SELECT seq, reason, agent FROM checkpoints ORDER BY card_id, seq"
        ).fetchall()
    ]


def _head(agent: dict) -> str:
    """The phase a stored agent would run next: its current turn, else its queue head."""
    return (agent["current_turn"] or agent["queue"][0])["kwargs"]["phase"]


def test_parked_names_the_phase_it_stopped_before():
    parked = checkpoint.Parked("b")

    assert isinstance(parked, Exception)
    assert parked.before_phase == "b"
    assert str(parked) == "stopped before b"


async def test_hook_without_a_run_does_nothing(store):
    ran: list[str] = []

    async def some_tool() -> str:
        ran.append("some_tool")
        return "ok"

    some_tool = tool(some_tool)
    agent = Agent("stray", "t", [some_tool], tags=["subtask"])
    await agent.put(Turn(some_tool))

    async for _ in agent.run():  # no LookupError from current_run
        pass

    assert ran == ["some_tool"]
    assert _rows(store) == []


def test_save_without_a_run_does_nothing(store):
    class Unreadable:
        def to_dict(self) -> dict:
            raise AssertionError("save read the agent with no run set")

    checkpoint.save(Unreadable(), "turn")

    assert _rows(store) == []
```

Note on `itertools`, `Any`, `Step`, `Workflow`, `runtime_engine` and `FIXED`: they are used by Task 2's tests appended to this file. Leave them imported now.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_checkpoint.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'checkpoint' from 'agent_manager.runtime'`.

- [ ] **Step 3: Write the module**

Create `src/agent_manager/runtime/checkpoint.py`:

```python
"""Checkpoint hooks (pygents-engine design G5, G8, §6).

Every turn of a subtask's agent is saved as `Agent.to_dict()` in the store's
`checkpoints` table *before* it runs, and the run's cooperative stop is read
at the same moment: a set stop saves `parked` and raises `Parked`, which
propagates out of `agent.run()` and ends it cleanly -- nothing breaks or
returns out of the loop. The after-run rows (`done`, `escalated`) are
written by `runtime/engine.py` through `save`.

The hook is module-level on purpose: pygents' `HookRegistry` is process-wide
and keyed on the function's name, and closures from one factory collide in
it (design §11). It is global and tagged `subtask`, so it fires for every
agent tagged `subtask`; it finds its run through `state.current_run` and
does nothing when no run is set.

`saved_at` is read from the wall clock, never from the run's injected
`clock`: that clock stamps phase rows, and a checkpoint reading it would
shift every stamp the old engine would have written (G10).
"""

from __future__ import annotations

from typing import Any

from pygents import AgentHook, hook

from agent_manager import engine as old
from agent_manager.runtime.state import current_run


class Parked(Exception):
    """The run's stop was set: the subtask stopped before `.before_phase`."""

    def __init__(self, before_phase: str) -> None:
        self.before_phase = before_phase
        super().__init__(f"stopped before {before_phase}")


def save(agent: Any, reason: str) -> None:
    """Write `agent` as the next checkpoint of the running subtask, or nothing with no run."""
    deps = current_run.get(None)
    if deps is None:
        return
    deps.store.save_checkpoint(
        deps.subtask.card_id,
        workflow=deps.workflow.name,
        digest=deps.workflow.digest(),
        reason=reason,
        agent=agent.to_dict(),
        saved_at=old._utcnow(),
    )


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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_checkpoint.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: all green. Importing `checkpoint` from the test file registers the global hook for the whole session, so the `[pygents]` params in `tests/test_engine.py` and the agents in `tests/runtime/test_compile.py` already write `turn` rows here; they must not change outcome. If any fails with `TypeError: Object of type ... is not JSON serializable` or a shifted phase timestamp, stop and report it (see Review Focus) rather than editing that test.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/runtime/checkpoint.py tests/runtime/test_checkpoint.py
git commit -m "feat(runtime): add the BEFORE_TURN checkpoint hook and Parked"
```

---

### Task 2: Wire checkpoints and the stop into the engine

**Files:**
- Modify: `src/agent_manager/runtime/engine.py:20-24` (imports), `:42-46` (`run_subtask` docstring), `:114-145` (`_run`)
- Test: `tests/runtime/test_checkpoint.py` (append), `tests/runtime/test_stop_bridge.py` (new; mirrored tier `tests/runtime/`)

**Interfaces:**
- Consumes (from Task 1): `checkpoint.Parked` with `.before_phase: str`; `checkpoint.save(agent, reason: str) -> None`. From the old engine: `old._stop(summary, store, story_id, subtask, phase_name) -> SubtaskSummary` (sets `status="stopped"`, `detail=f"stopped before {phase_name}"`, records the subtask `stopped`); `old._escalate(summary, store, story_id, subtask, phase_name, detail)`; `old._record_subtask_status(store, story_id, subtask, status)`. From the store: `Store.latest_checkpoint(card_id) -> Checkpoint | None` with `.seq`, `.reason`, `.agent: dict`.
- Produces: `runtime.engine.run_subtask(...)` unchanged in signature; it now honours `should_stop` (returns a `stopped` summary) and leaves one checkpoint row per turn plus a final `done`/`escalated` row, or a final `parked` row on a stop.

- [ ] **Step 1: Append the failing engine-level tests to `tests/runtime/test_checkpoint.py`**

These call the synchronous `runtime_engine.run_subtask` (it runs its own `asyncio.run`), so they are plain `def` tests.

```python
def _three_steps(ran: list[str]) -> Workflow:
    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        return {"b": 2}

    def c(card: str) -> dict[str, Any]:
        ran.append("c")
        return {"c": 3}

    return Workflow("three", (Step("a", a), Step("b", b), Step("c", c)))


def test_every_turn_is_checkpointed_before_it_runs(store):
    ran: list[str] = []

    summary = runtime_engine.run_subtask(
        _three_steps(ran),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
    )

    assert ran == ["a", "b", "c"]
    assert summary.status == "done"
    assert summary.results == {"a": {"a": 1}, "b": {"b": 2}, "c": {"c": 3}}
    rows = _rows(store)
    assert [(seq, reason) for seq, reason, _ in rows] == [
        (0, "turn"), (1, "turn"), (2, "turn"), (3, "done"),
    ]
    assert [_head(agent) for _, _, agent in rows[:3]] == ["a", "b", "c"]
    done = rows[3][2]
    assert done["current_turn"] is None
    assert done["queue"] == []
    newest = store.latest_checkpoint(CARD_ID)
    assert (newest.seq, newest.reason, newest.workflow) == (3, "done", "three")


def test_an_escalation_writes_an_escalated_row(store):
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        raise RuntimeError("boom")

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        return {}

    summary = runtime_engine.run_subtask(
        Workflow("escalates", (Step("a", a), Step("b", b))),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
    )

    assert ran == ["a"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "a"
    assert "boom" in summary.detail
    assert [(seq, reason) for seq, reason, _ in _rows(store)] == [
        (0, "turn"), (1, "escalated"),
    ]
    assert store.latest_checkpoint(CARD_ID).reason == "escalated"


def test_an_error_outside_a_phase_writes_an_escalated_row(store):
    """The generic `except Exception` branch, not `C.Escalated`: an error raised
    by the hook itself (here a `should_stop` that breaks on its second call)
    still leaves an `escalated` row before the subtask is escalated."""
    ran: list[str] = []
    asked = itertools.count()

    def should_stop() -> bool:
        if next(asked) == 1:
            raise RuntimeError("stop check broke")
        return False

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        return {"b": 2}

    summary = runtime_engine.run_subtask(
        Workflow("breaks", (Step("a", a), Step("b", b))),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        should_stop=should_stop,
    )

    assert ran == ["a"]
    assert summary.status == "escalated"
    assert "stop check broke" in summary.detail
    assert [(seq, reason) for seq, reason, _ in _rows(store)] == [
        (0, "turn"), (1, "escalated"),
    ]


def test_a_checkpoint_never_reads_the_injected_clock(store):
    """Guard for the plan's `saved_at` deviation: the injected clock stamps phase
    rows only -- twice per step phase (engine.py `run_one_step`) -- so a
    checkpoint must not consume it (G10). Passes before and after Task 2."""
    calls: list[datetime] = []

    def clock() -> datetime:
        calls.append(FIXED)
        return FIXED

    summary = runtime_engine.run_subtask(
        _three_steps([]),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=clock,
    )

    assert summary.status == "done"
    assert len(calls) == 6
    assert len(_rows(store)) >= 3
```

- [ ] **Step 2: Create the failing stop-bridge tests**

Create `tests/runtime/test_stop_bridge.py`:

```python
"""The run's stop, bridged onto the pygents engine (pygents-engine design G8).

Runtime tier: `runtime.engine.run_subtask` over fake steps and a real temp
store. The stop is a plain `threading.Event`, as orchestrate.py's `RunStop`
is; the engine only ever calls its `is_set`.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_manager import models, store as store_module
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow.phases import Step, Workflow

RUN_ID = "run-2026-09-26-03"
STORY_ID = "a6c7bff3"
CARD_ID = "921ed349"
REPO = Path("/repo")
FIXED = datetime(2026, 9, 26, tzinfo=timezone.utc)


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
        branch=f"m6/task-checkpoint-every-turn-{CARD_ID}",
        base_branch="m6/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _reasons(opened) -> list[tuple[int, str]]:
    return [
        (row[0], row[1])
        for row in opened.connection.execute(
            "SELECT seq, reason FROM checkpoints ORDER BY seq"
        ).fetchall()
    ]


def _head(agent: dict) -> str:
    return (agent["current_turn"] or agent["queue"][0])["kwargs"]["phase"]


def test_stop_set_during_a_phase_parks_before_the_next(store):
    stop = threading.Event()
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        stop.set()
        return {"a": 1}

    def b(card: str) -> dict[str, Any]:
        ran.append("b")
        return {"b": 2}

    summary = runtime_engine.run_subtask(
        Workflow("stops", (Step("a", a), Step("b", b))),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        should_stop=stop.is_set,
    )

    assert ran == ["a"]
    assert summary.status == "stopped"
    assert summary.detail == "stopped before b"
    assert summary.failed_phase is None
    assert summary.results == {"a": {"a": 1}}
    assert _reasons(store) == [(0, "turn"), (1, "parked")]
    newest = store.latest_checkpoint(CARD_ID)
    assert newest.reason == "parked"
    assert _head(newest.agent) == "b"


def test_a_stop_set_before_the_run_parks_before_the_first_phase(store):
    ran: list[str] = []

    def a(card: str) -> dict[str, Any]:
        ran.append("a")
        return {"a": 1}

    summary = runtime_engine.run_subtask(
        Workflow("never", (Step("a", a),)),
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
        should_stop=lambda: True,
    )

    assert ran == []
    assert summary.status == "stopped"
    assert summary.detail == "stopped before a"
    assert summary.results == {}
    assert _reasons(store) == [(0, "parked")]
    assert _head(store.latest_checkpoint(CARD_ID).agent) == "a"
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/runtime/test_checkpoint.py tests/runtime/test_stop_bridge.py -v`
Expected:
- `test_every_turn_is_checkpointed_before_it_runs` FAILS: rows end at `(2, "turn")`, no `(3, "done")`.
- `test_an_escalation_writes_an_escalated_row` FAILS: rows are `[(0, "turn")]`, no `escalated`.
- `test_an_error_outside_a_phase_writes_an_escalated_row` FAILS: rows are `[(0, "turn")]`.
- Both stop-bridge tests FAIL: `summary.status == "escalated"` (the `Parked` from the hook falls into the generic `except Exception`).
- `test_a_checkpoint_never_reads_the_injected_clock` and the three Task 1 tests PASS (the clock test is a guard, not a RED test).

- [ ] **Step 4: Import `checkpoint` in the engine**

In `src/agent_manager/runtime/engine.py`, replace lines 20-24:

```python
from agent_manager import engine as old
from agent_manager.runtime import compile as C
from agent_manager.runtime import context
from agent_manager.runtime.state import RunDeps, current_run
from agent_manager.workflow.phases import Workflow
```

with:

```python
from agent_manager import engine as old
from agent_manager.runtime import checkpoint  # registers the BEFORE_TURN hook
from agent_manager.runtime import compile as C
from agent_manager.runtime import context
from agent_manager.runtime.state import RunDeps, current_run
from agent_manager.workflow.phases import Workflow
```

- [ ] **Step 5: Rewrite `_run` and the `run_subtask` docstring**

In `src/agent_manager/runtime/engine.py`, replace the `run_subtask` docstring (lines 42-46):

```python
    """Walk `workflow`'s phases for one subtask on pygents. One `asyncio.run`.

    No `start_phase`: resume belongs to the yaml engine. `should_stop` is kept
    in `RunDeps` and not yet read -- the stop bridge is plan Task 4.2.
    """
```

with:

```python
    """Walk `workflow`'s phases for one subtask on pygents. One `asyncio.run`.

    No `start_phase`: resume belongs to the yaml engine. Every turn is saved as
    a `turn` checkpoint before it runs, and the run ends with a `done` or
    `escalated` one (`runtime/checkpoint.py`). `should_stop` is asked before
    every turn: once it answers true, a `parked` checkpoint is saved, the next
    phase is not started, and the subtask is recorded `stopped before <phase>`.
    """
```

Then replace the whole `_run` function (lines 114-145) with:

```python
async def _run(agent: Agent, deps: RunDeps) -> old.SubtaskSummary:
    summary = old.SubtaskSummary()
    token = current_run.set(deps)
    # Every `checkpoint.save` below runs inside this `try`, while `current_run`
    # is still set: after the `finally` resets it, `save` is a silent no-op.
    try:
        async for _ in agent.run():  # consumed to the end, always
            pass
    except checkpoint.Parked as parked:
        # The stop, raised by the BEFORE_TURN hook after it saved `parked`:
        # no further row, so that one stays the newest.
        _collect(agent, deps, summary)
        return old._stop(
            summary, deps.store, deps.story_id, deps.subtask, parked.before_phase
        )
    except C.Escalated as esc:
        _collect(agent, deps, summary)
        checkpoint.save(agent, "escalated")
        return old._escalate(
            summary, deps.store, deps.story_id, deps.subtask, esc.phase, esc.detail
        )
    except old.EngineError:
        # A missing runner or an unresolvable input: a wiring or document bug
        # the old engine raises to its caller, `.phase`/`.parameter` intact.
        # Not an escalation, so no checkpoint row.
        raise
    except Exception as error:
        _collect(agent, deps, summary)
        checkpoint.save(agent, "escalated")
        # `deps.running` is only `None` if the error came before any tool was
        # entered; there is no phase to name then.
        return old._escalate(
            summary,
            deps.store,
            deps.story_id,
            deps.subtask,
            deps.running or "?",
            old._render_error(error),
        )
    else:
        _collect(agent, deps, summary)
        checkpoint.save(agent, "done")
    finally:
        # A `BaseException` (cancellation, KeyboardInterrupt) passes straight
        # through here and writes nothing: the last `turn` row stands.
        current_run.reset(token)
    old._record_subtask_status(deps.store, deps.story_id, deps.subtask, summary.status)
    return summary
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/runtime/test_checkpoint.py tests/runtime/test_stop_bridge.py -v`
Expected: 9 passed.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: all green, including `tests/test_engine.py::test_a_phase_is_timed_with_the_injected_clock[pygents]`, every other `[pygents]` param in `tests/test_engine.py`, `tests/runtime/test_compile.py` and `tests/e2e`. The `should_stop` tests in `tests/test_engine.py` stay skipped on `[pygents]` (see Global Constraints). If a `[pygents]` test fails with `TypeError: Object of type ... is not JSON serializable`, stop and report it against the Review Focus item rather than changing that test.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/runtime/engine.py tests/runtime/test_checkpoint.py tests/runtime/test_stop_bridge.py
git commit -m "feat(runtime): checkpoint every turn and park on the run's stop"
```
