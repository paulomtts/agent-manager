<!-- task-pipeline: validated -->
# Subtask 9b944409: Add an awaitable subtask driver

Parent story: e90a2247 ("Groundwork: the stop, an awaitable driver, multi-blocker roots"), milestone c2a981a3. This is Task 1.2 of `docs/superpowers/plans/2026-09-25-supervisor-tree.md`, narrowed from `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md` (decisions T2, T5). The supervisor's lanes will `await cli.drive_subtask_async(..., stop=stop, resume_from=...)`, so the driver must run on the caller's event loop and not open its own.

## Prerequisite

Blocked by 364babde (StopSignal and ON_PAUSE parking). That work must be present in this worktree: `src/agent_manager/runtime/stop.py` (`StopSignal`) and `runtime.engine.run_subtask_async(..., should_stop=None, stop=None, resume_from=None)`. Both are present in this worktree. This subtask only calls them. It does not modify `runtime/stop.py`, `runtime/checkpoint.py`, or `runtime/engine.py`.

## Scope

Only `src/agent_manager/cli.py` and the tests that stub the engine walk.

1. Add `async def drive_subtask_async(*, store, run_id, card, parent, subtask, repo_dir, commands=(), allow_no_verification=False, runner_factory=None, should_stop=None, stop: StopSignal | None = None, resume_from=None) -> SubtaskDrive`.
   - The body is today's `drive_subtask` body (cli.py:654-679), moved without changes. It builds the runner from `runner_factory`, or from `default_runner_factory` when that is `None`, passing `store`, `run_id`, `story_id=parent.id` and `card_id=card.id`. It builds the same `walk` kwargs dict: `story_id`, `subtask`, `repo_dir`, `commands`, `card`, `parent_story`, `extra_context=gate_context(...)`, `agent_runner` and `should_stop`, plus `stop=stop`. `resume_from` is added only when it is not `None`.
   - It then runs `summary = await runtime_engine.run_subtask_async(task_workflow.TASK, store, **walk)` and returns `SubtaskDrive(summary, list(summary.warnings) + list(getattr(runner, "warnings", [])))`.
   - `should_stop` appears in this signature only as a pass-through for the sync wrapper. The plan's Task 3.3 lists `drive_subtask_async` among the functions it removes `should_stop` from. `stop` is keyword-only, typed `StopSignal | None`, and imported from `agent_manager.runtime.stop`.
   - The driver stays a plain coroutine, not a pygents Agent (T2). It does not import `grafo`.
2. `drive_subtask` keeps its exact M6 signature, including `should_stop` and without `stop`, and its docstring contract. Its body becomes `return asyncio.run(drive_subtask_async(...))`, forwarding every argument.

## Observable behavior

- For the same inputs, `drive_subtask_async` and `drive_subtask` give an equal `SubtaskDrive`: the same summary status, the same results keys and the same merged warnings list.
- Awaiting `drive_subtask_async` inside a running event loop succeeds. It never hits `RuntimeError: asyncio.run() cannot be called from a running event loop`.
- A `stop` passed to `drive_subtask_async` reaches `run_subtask_async` as the same object. `should_stop` and `resume_from` reach it exactly as `drive_subtask` passes them today.
- A fresh walk, with `resume_from=None`, calls the engine without a `resume_from` key, as it does today.
- Error paths are unchanged. The driver catches nothing: escalation is `summary.status == "escalated"`, a stop is `"stopped"`, and engine exceptions (`EngineError`, `CheckpointMismatch`) propagate. Calling the sync `drive_subtask` from inside a running loop still raises `asyncio.run`'s `RuntimeError`, as expected. Callers inside a loop must use the async form.

## Test adaptation

The engine stubs that back `drive_subtask` now intercept `run_subtask_async`, not `run_subtask`, because `drive_subtask` reaches the engine only through `drive_subtask_async`. These stubs must become `async def` stubs patched onto `runtime_engine.run_subtask_async`, recording the same `(workflow, store, kwargs)` tuple:

- `_record_walks` at `tests/test_cli.py:1486`
- the `exploding` patch at `tests/test_cli.py:1935`

`tests/test_integration.py:612`'s `_stub_walk` (used only by `test_resolve_conflict_walks_integrate_with_the_same_arguments`) stays exactly as it is, still patching `runtime_engine.run_subtask`. It backs `integration._resolve_conflict`, which calls `runtime_engine.run_subtask` directly and is untouched by this subtask (`integration.py` is out of scope); it never goes through `drive_subtask` or `drive_subtask_async`. Patching `run_subtask_async` there instead would let the real `run_subtask` forward its full keyword set (`card`, `parent_story`, `clock`, `should_stop`, `stop`, `resume_from`) into the recorded kwargs, breaking that test's exact `kwargs == {...}` assertion.

These existing tests must keep passing with their assertions unchanged, apart from the stub target:

- `test_drive_subtask_walks_task_with_the_same_arguments`
- `test_drive_subtask_drives_two_subtasks_under_one_store_and_run`
- `test_drive_subtask_hands_should_stop_to_the_engine`
- `test_drive_subtask_hands_resume_from_to_the_pygents_walk`

The only allowed change to their expected kwargs is the new `stop: None` key.

## Tests

All new tests go in `tests/test_cli.py`. Under the design spec's §14 placement rule, this is CLI-level driver behavior exercised with a fake runner factory and a stubbed or fake-adapter engine. It uses no real harness and no network, so it does not belong in `tests/e2e` or the `e2e` marker. Tests use the existing `_drive_row`, `_record_walks` and `_recording_factory` helpers. No test sleeps.

1. `test_drive_subtask_async_runs_inside_a_running_loop` (tests/test_cli.py, CLI driver tier with a fake runner factory). The test computes the expected `SubtaskDrive` by calling the sync `cli.drive_subtask(...)` first, from the test's own top-level (no loop running yet). It then, inside a coroutine run by `asyncio.run`, awaits `cli.drive_subtask_async(...)` with a fake runner factory for the same inputs. It asserts that no `RuntimeError` is raised by the `await`, and that the awaited result equals the expected `SubtaskDrive`, comparing status, results keys and warnings, including runner out-of-band warnings. It never calls the sync `drive_subtask` from inside the coroutine: that would itself raise `asyncio.run`'s `RuntimeError`, which is a different failure than the one this test guards against.
2. `test_drive_subtask_async_hands_stop_to_the_engine` (tests/test_cli.py, CLI driver tier with the stubbed engine via `_record_walks`). It passes a `StopSignal()` and asserts that the recorded `run_subtask_async` call received that same object as `stop`, with workflow `task_workflow.TASK` and the other kwargs matching the existing same-arguments test.
3. The existing four `drive_subtask` tests listed above, adapted to the async stub (tests/test_cli.py, same tier). Adapting them proves that the sync wrapper still forwards `should_stop` and `resume_from`, and that it leaves out `resume_from` on a fresh walk.

## Out of scope

- Any change to `runtime/engine.py`, `runtime/stop.py` or `runtime/checkpoint.py` (owned by 364babde).
- Removing `should_stop` (Task 3.3).
- grafo, `dag.py` roots and dry-run `merged_from` (1693e86e).
- `orchestrate.py`.
- Verification discovery, live pause/cancel/watch/retry, running more than one `am` process per repo, a `max_workers` option, and leave-me-alone multi-blocker support.

## Verification

- Full suite: `uv run pytest` (the whole default suite must be green, including `tests/e2e` as collected by default).
- Typecheck: none.
- Lint: none.

---

# Awaitable Subtask Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `cli.drive_subtask_async`, a plain coroutine that walks one subtask on the caller's event loop via `runtime_engine.run_subtask_async` and forwards a `StopSignal`, and turn the sync `cli.drive_subtask` into an `asyncio.run` wrapper around it.

**Architecture:** The body of today's `drive_subtask` (`src/agent_manager/cli.py:654-679`) moves unchanged into `async def drive_subtask_async`, which awaits `runtime_engine.run_subtask_async(task_workflow.TASK, store, **walk)` with `stop=stop` added to the `walk` dict. `drive_subtask` keeps its M6 signature and becomes `return asyncio.run(drive_subtask_async(...))`. Tests that stub the engine behind `drive_subtask` move from patching `run_subtask` to patching `run_subtask_async` with `async def` stubs.

**Tech Stack:** Python 3.12, asyncio, pytest with pytest-asyncio (`asyncio_mode = "auto"` in `pyproject.toml`, so `async def test_...` functions run on a pytest-managed loop), `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m7/task-add-an-awaitable-9b944409/docs/superpowers/specs/task-add-an-awaitable-9b944409-design.md` (reproduced verbatim above).

## Global Constraints

- Only `src/agent_manager/cli.py` and `tests/test_cli.py` change. Do not modify `src/agent_manager/runtime/engine.py`, `src/agent_manager/runtime/stop.py`, `src/agent_manager/runtime/checkpoint.py`, `src/agent_manager/orchestrate.py` or `src/agent_manager/integration.py`.
- `tests/test_integration.py:612` (`_stub_walk`) stays exactly as it is, still patching `runtime_engine.run_subtask`.
- The driver stays a plain coroutine, not a pygents Agent (T2). `cli.py` must not import `grafo`.
- `stop` is keyword-only, typed `StopSignal | None`, imported from `agent_manager.runtime.stop`.
- `drive_subtask` keeps its exact M6 signature, including `should_stop` and without `stop`.
- `resume_from` joins the engine's keywords only when it is not `None`.
- The driver catches nothing: `EngineError` and `CheckpointMismatch` propagate; escalation and stop are summary statuses.
- New tests go in `tests/test_cli.py` (CLI driver tier, §14 of `docs/superpowers/specs/2026-09-23-agent-manager-design.md`), not in `tests/e2e`. No test sleeps.
- Verification: `uv run pytest` (full default suite green). No typecheck, no lint.

## Review Focus

1. Calling the sync `cli.drive_subtask` from inside a running event loop: a person expects the documented `RuntimeError` from `asyncio.run` ("cannot be called from a running event loop"), raised before the engine is reached, not a hang or a silent nested loop. Pinned by `test_drive_subtask_inside_a_running_loop_still_raises` in Task 1.
2. An `EngineError` raised by the engine while the async driver is awaited: a person expects it to propagate out of `await cli.drive_subtask_async(...)` unchanged, not be swallowed into a summary. Pinned by `test_drive_subtask_async_lets_an_engine_error_out` in Task 1.
3. Two `drive_subtask_async` calls gathered on one loop (what supervisor lanes will do), each with its own `StopSignal`: a person expects each walk to receive its own signal, never the other's. Pinned by `test_two_async_drives_on_one_loop_each_hand_their_own_stop` in Task 1.
4. The async driver given both `stop` and `resume_from`: a person expects both to reach the engine as the same objects. Pinned by `test_drive_subtask_async_hands_stop_and_resume_from_together` in Task 1.
5. An escalated summary carrying its own warnings plus runner out-of-band warnings, through the async driver: a person expects it returned (not raised) with warnings ordered summary-first, then runner. Pinned by `test_drive_subtask_async_runs_inside_a_running_loop` in Task 1 (its stub returns an escalated summary with warnings and its runner carries warnings).

---

## File Structure

- Modify: `src/agent_manager/cli.py` — add `import asyncio` and the `StopSignal` import; add `drive_subtask_async` directly above `drive_subtask`; replace `drive_subtask`'s body with the `asyncio.run` wrapper.
- Modify: `tests/test_cli.py` — imports; `_record_walks` (line 1486) and the `exploding` stub (line 1935) become async stubs on `run_subtask_async`; the same-arguments test's expected kwargs gain `"stop": None`; six new tests after `test_drive_subtask_walks_task_with_the_same_arguments` (which ends at line 1560, before `runner = CliRunner()` at line 1563).

This is one task: the change is a single function split, and every test below exercises the same seam. A reviewer could not meaningfully approve the async driver while rejecting the wrapper, or vice versa.

---

### Task 1: Awaitable `drive_subtask_async` with a sync `asyncio.run` wrapper

**Files:**
- Modify: `src/agent_manager/cli.py:20-43` (imports), `src/agent_manager/cli.py:626-679` (`drive_subtask`)
- Test: `tests/test_cli.py:15-47` (imports), `tests/test_cli.py:1486-1499` (`_record_walks`), `tests/test_cli.py:1541-1551` (expected kwargs), new tests inserted after line 1560, `tests/test_cli.py:1929-1935` (`exploding`)

**Interfaces:**
- Consumes (already in this worktree, from 364babde; do not modify):
  - `agent_manager.runtime.stop.StopSignal` — `StopSignal()`, `.triggered`, `.primary`, `.trigger(story_id) -> bool`, `.register(agent)`, `.unregister(agent)`.
  - `agent_manager.runtime.engine.run_subtask_async(workflow, store, *, story_id, subtask, repo_dir, commands=(), card=None, parent_story=None, extra_context=None, agent_runner=None, clock=walk._utcnow, should_stop=None, stop: StopSignal | None = None, resume_from: Checkpoint | None = None) -> walk.SubtaskSummary` (async).
  - Existing in `cli.py`: `SubtaskDrive(summary: SubtaskSummary, warnings: list[str])` (frozen dataclass, line 615), `RunnerFactory`, `default_runner_factory`, `gate_context(commands, allow_no_verification)`.
- Produces:
  - `async def cli.drive_subtask_async(*, store: Store, run_id: str, card: models.Card, parent: models.Card, subtask: models.SubtaskRun, repo_dir: Path, commands: Sequence[str] = (), allow_no_verification: bool = False, runner_factory: RunnerFactory | None = None, should_stop: Callable[[], bool] | None = None, stop: StopSignal | None = None, resume_from: store_module.Checkpoint | None = None) -> SubtaskDrive`
  - `def cli.drive_subtask(...)` — unchanged M6 signature (no `stop`), now `asyncio.run(drive_subtask_async(...))`.

- [ ] **Step 1: Add the test imports**

In `tests/test_cli.py`, change the stdlib import block (lines 15-23) from:

```python
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
```

to:

```python
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
```

and change lines 46-47 from:

```python
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow import task as task_workflow
```

to:

```python
from agent_manager.runtime import engine as runtime_engine
from agent_manager.runtime.stop import StopSignal
from agent_manager.workflow import task as task_workflow
```

- [ ] **Step 2: Move `_record_walks` onto `run_subtask_async` as an async stub**

In `tests/test_cli.py`, replace `_record_walks` (lines 1486-1499):

```python
def _record_walks(monkeypatch) -> list[tuple[Any, Any, dict[str, Any]]]:
    """Stub `runtime.engine.run_subtask`; every call is recorded.

    Patched on the module itself, so the stub is what `drive_subtask` reaches
    through its `runtime_engine` alias.
    """
    walks: list[tuple[Any, Any, dict[str, Any]]] = []

    def run_subtask(workflow, store, **kwargs):
        walks.append((workflow, store, kwargs))
        return SubtaskSummary(status="done")

    monkeypatch.setattr(runtime_engine, "run_subtask", run_subtask)
    return walks
```

with:

```python
def _record_walks(monkeypatch) -> list[tuple[Any, Any, dict[str, Any]]]:
    """Stub `runtime.engine.run_subtask_async`; every call is recorded.

    Patched on the module itself, so the stub is what `drive_subtask_async`
    (and `drive_subtask`, through it) reaches via its `runtime_engine` alias.
    """
    walks: list[tuple[Any, Any, dict[str, Any]]] = []

    async def run_subtask_async(workflow, store, **kwargs):
        walks.append((workflow, store, kwargs))
        return SubtaskSummary(status="done")

    monkeypatch.setattr(runtime_engine, "run_subtask_async", run_subtask_async)
    return walks
```

- [ ] **Step 3: Add `stop: None` to the same-arguments test's expected kwargs**

In `test_drive_subtask_walks_task_with_the_same_arguments`, change the docstring first line and the expected dict (lines 1514 and 1541-1551). Replace:

```python
    """Spec test 2: the walk is `runtime.engine.run_subtask` over `TASK`. The
    keywords are compared whole, so a `start_phase` or a `resume_from`
    sneaking into the call fails here."""
```

with:

```python
    """Spec test 2: the walk is `runtime.engine.run_subtask_async` over `TASK`.
    The keywords are compared whole, so a `start_phase` or a `resume_from`
    sneaking into the call fails here. The sync driver has no `stop`, so it
    forwards `stop=None`."""
```

and replace:

```python
        "agent_runner": runner,
        "should_stop": stop,
    }
    assert drive.summary.status == "done"
```

with:

```python
        "agent_runner": runner,
        "should_stop": stop,
        "stop": None,
    }
    assert drive.summary.status == "done"
```

- [ ] **Step 4: Move the `exploding` stub onto `run_subtask_async`**

In `test_an_engine_error_escaping_the_walk_reaches_the_operator` (lines 1928-1935), replace:

```python
    """`run_subtask` deliberately lets `EngineError` out rather than journalling
    it as a phase failure: an unbindable gate is a document bug, not an attempt."""

    def exploding(*args, **kwargs):
        raise EngineError("no value for a required parameter", phase="explore")

    monkeypatch.setattr(cli.runtime_engine, "run_subtask", exploding)
```

with:

```python
    """`run_subtask_async` deliberately lets `EngineError` out rather than
    journalling it as a phase failure: an unbindable gate is a document bug,
    not an attempt."""

    async def exploding(*args, **kwargs):
        raise EngineError("no value for a required parameter", phase="explore")

    monkeypatch.setattr(cli.runtime_engine, "run_subtask_async", exploding)
```

- [ ] **Step 5: Write the new failing tests**

In `tests/test_cli.py`, insert the following directly after `test_drive_subtask_walks_task_with_the_same_arguments` (after its last line, `    }` closing the `factory_call` dict at line 1560) and before `runner = CliRunner()`:

```python
# ── drive_subtask_async (card 9b944409) ──────────────────────────────────────


def test_drive_subtask_async_runs_inside_a_running_loop(monkeypatch):
    """T2: a supervisor lane awaits the driver on its own loop, so the driver
    must not open one. The sync form is the oracle, computed first with no loop
    running; the async form, awaited inside a running loop, must return the
    same drive and reach the engine on that caller's loop. The canned summary
    escalates and carries a warning, and the runner carries an out-of-band
    one, so the merge order is checked too."""
    loops: list[asyncio.AbstractEventLoop] = []

    async def run_subtask_async(workflow, store, **kwargs):
        loops.append(asyncio.get_running_loop())
        return SubtaskSummary(
            status="escalated",
            results={"explore": {"ok": True}},
            warnings=["summary warning"],
            failed_phase="validate_spec",
            detail="canned escalation",
        )

    monkeypatch.setattr(runtime_engine, "run_subtask_async", run_subtask_async)

    def factory(**kwargs: Any) -> Any:
        return SimpleNamespace(warnings=["runner warning"])

    drive_args: dict[str, Any] = {
        "store": object(),
        "run_id": DRIVE_RUN_ID,
        "card": DRIVE_CARD,
        "parent": DRIVE_PARENT,
        "subtask": _drive_row(),
        "repo_dir": DRIVE_REPO,
        "commands": ["uv run pytest"],
        "runner_factory": factory,
    }

    expected = cli.drive_subtask(**drive_args)

    async def inside() -> tuple[cli.SubtaskDrive, asyncio.AbstractEventLoop]:
        drive = await cli.drive_subtask_async(**drive_args)
        return drive, asyncio.get_running_loop()

    drive, outer = asyncio.run(inside())

    assert len(loops) == 2
    assert loops[1] is outer
    assert drive.summary.status == expected.summary.status == "escalated"
    assert drive.summary.results.keys() == expected.summary.results.keys() == {"explore"}
    assert drive.warnings == expected.warnings == ["summary warning", "runner warning"]
    assert drive == expected


def test_drive_subtask_async_hands_stop_to_the_engine(monkeypatch):
    """T5: the lane's `StopSignal` reaches the pygents walk as the same object,
    and every other keyword is what the sync driver sends today."""
    walks = _record_walks(monkeypatch)
    seen: list[dict[str, Any]] = []
    factory, runner = _recording_factory(seen)
    store = object()
    subtask = _drive_row()
    stop = StopSignal()

    drive = asyncio.run(
        cli.drive_subtask_async(
            store=store,
            run_id=DRIVE_RUN_ID,
            card=DRIVE_CARD,
            parent=DRIVE_PARENT,
            subtask=subtask,
            repo_dir=DRIVE_REPO,
            commands=["uv run pytest"],
            runner_factory=factory,
            stop=stop,
        )
    )

    ((workflow, passed_store, kwargs),) = walks
    assert workflow is task_workflow.TASK
    assert passed_store is store
    assert kwargs["stop"] is stop
    assert kwargs == {
        "story_id": DRIVE_PARENT.id,
        "subtask": subtask,
        "repo_dir": DRIVE_REPO,
        "commands": ["uv run pytest"],
        "card": DRIVE_CARD,
        "parent_story": DRIVE_PARENT,
        "extra_context": cli.gate_context(["uv run pytest"], False),
        "agent_runner": runner,
        "should_stop": None,
        "stop": stop,
    }
    assert drive.summary.status == "done"
    assert drive.warnings == []
    (factory_call,) = seen
    assert factory_call == {
        "store": store,
        "run_id": DRIVE_RUN_ID,
        "story_id": DRIVE_PARENT.id,
        "card_id": DRIVE_CARD.id,
    }


async def test_drive_subtask_async_hands_stop_and_resume_from_together(monkeypatch):
    """A lane relaunching a parked subtask passes both; both arrive untouched."""
    walks = _record_walks(monkeypatch)
    factory, _runner = _recording_factory([])
    stop = StopSignal()
    checkpoint = _checkpoint("parked", queue=("plan",))

    await cli.drive_subtask_async(
        store=object(),
        run_id=DRIVE_RUN_ID,
        card=DRIVE_CARD,
        parent=DRIVE_PARENT,
        subtask=_drive_row(),
        repo_dir=DRIVE_REPO,
        runner_factory=factory,
        stop=stop,
        resume_from=checkpoint,
    )

    ((_workflow, _store, kwargs),) = walks
    assert kwargs["stop"] is stop
    assert kwargs["resume_from"] is checkpoint


async def test_two_async_drives_on_one_loop_each_hand_their_own_stop(monkeypatch):
    """Supervisor lanes share one loop; each walk must get its own signal."""
    walks = _record_walks(monkeypatch)
    factory, _runner = _recording_factory([])
    first_row, second_row = _drive_row(), _drive_row()
    first_stop, second_stop = StopSignal(), StopSignal()

    def drive(row: models.SubtaskRun, stop: StopSignal):
        return cli.drive_subtask_async(
            store=object(),
            run_id=DRIVE_RUN_ID,
            card=DRIVE_CARD,
            parent=DRIVE_PARENT,
            subtask=row,
            repo_dir=DRIVE_REPO,
            runner_factory=factory,
            stop=stop,
        )

    drives = await asyncio.gather(drive(first_row, first_stop), drive(second_row, second_stop))

    assert [d.summary.status for d in drives] == ["done", "done"]
    stops = {id(kwargs["subtask"]): kwargs["stop"] for _w, _s, kwargs in walks}
    assert len(stops) == 2
    assert stops[id(first_row)] is first_stop
    assert stops[id(second_row)] is second_stop


async def test_drive_subtask_async_lets_an_engine_error_out(monkeypatch):
    """The driver catches nothing: an engine error escapes the `await` as is."""

    async def exploding(*args, **kwargs):
        raise EngineError("no value for a required parameter", phase="explore")

    monkeypatch.setattr(runtime_engine, "run_subtask_async", exploding)
    factory, _runner = _recording_factory([])

    with pytest.raises(EngineError, match="explore"):
        await cli.drive_subtask_async(
            store=object(),
            run_id=DRIVE_RUN_ID,
            card=DRIVE_CARD,
            parent=DRIVE_PARENT,
            subtask=_drive_row(),
            repo_dir=DRIVE_REPO,
            runner_factory=factory,
        )


@pytest.mark.filterwarnings("ignore:coroutine .* was never awaited:RuntimeWarning")
async def test_drive_subtask_inside_a_running_loop_still_raises(monkeypatch):
    """The sync form is `asyncio.run` and stays so: inside a loop it refuses
    before the engine is reached. Callers inside a loop use the async form.
    A characterization pin: it passes before and after this card."""
    walks = _record_walks(monkeypatch)
    factory, _runner = _recording_factory([])

    with pytest.raises(RuntimeError, match="cannot be called from a running event loop"):
        cli.drive_subtask(
            store=object(),
            run_id=DRIVE_RUN_ID,
            card=DRIVE_CARD,
            parent=DRIVE_PARENT,
            subtask=_drive_row(),
            repo_dir=DRIVE_REPO,
            runner_factory=factory,
        )

    assert walks == []
```

Note: `_checkpoint` is a module-level helper defined further down `tests/test_cli.py` (above `test_drive_subtask_hands_resume_from_to_the_pygents_walk`, line 3866). It is resolved at call time, so using it from a test defined earlier in the file is fine.

- [ ] **Step 6: Run the driver tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "drive_subtask or two_async_drives" -v`

Expected:
- FAIL `test_drive_subtask_walks_task_with_the_same_arguments`: the unpatched sync `runtime_engine.run_subtask` forwards its full keyword set into the stubbed `run_subtask_async`, so the recorded kwargs also hold `clock` and `resume_from` and the whole-dict comparison fails.
- FAIL `test_drive_subtask_async_runs_inside_a_running_loop`, `test_drive_subtask_async_hands_stop_to_the_engine`, `test_drive_subtask_async_hands_stop_and_resume_from_together`, `test_two_async_drives_on_one_loop_each_hand_their_own_stop`, `test_drive_subtask_async_lets_an_engine_error_out` with `AttributeError: module 'agent_manager.cli' has no attribute 'drive_subtask_async'`.
- PASS `test_drive_subtask_inside_a_running_loop_still_raises` (characterization: today the nested `asyncio.run` in `runtime_engine.run_subtask` raises the same error), `test_drive_subtask_hands_resume_from_to_the_pygents_walk`, and the git/brd tests `test_drive_subtask_drives_two_subtasks_under_one_store_and_run` / `test_drive_subtask_hands_should_stop_to_the_engine` (they drive the real engine; skipped if git or brd is absent).

- [ ] **Step 7: Add the `cli.py` imports**

In `src/agent_manager/cli.py`, change line 20 from:

```python
import json
```

to:

```python
import asyncio
import json
```

and change line 38 from:

```python
from agent_manager.runtime.errors import EngineError
```

to:

```python
from agent_manager.runtime.errors import EngineError
from agent_manager.runtime.stop import StopSignal
```

- [ ] **Step 8: Split `drive_subtask` into `drive_subtask_async` plus the sync wrapper**

In `src/agent_manager/cli.py`, replace the whole of `drive_subtask` (lines 626-679, from `def drive_subtask(` through `    return SubtaskDrive(summary=summary, warnings=warnings)`) with:

```python
async def drive_subtask_async(
    *,
    store: Store,
    run_id: str,
    card: models.Card,
    parent: models.Card,
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: RunnerFactory | None = None,
    should_stop: Callable[[], bool] | None = None,
    stop: StopSignal | None = None,
    resume_from: store_module.Checkpoint | None = None,
) -> SubtaskDrive:
    """Walk one subtask through `workflow.task.TASK` on the caller's event loop.

    The awaitable form of `drive_subtask` (supervisor-tree T2): a supervisor
    lane awaits it, so it must not open a loop of its own. A plain coroutine,
    not a pygents Agent. The contract is `drive_subtask`'s: the caller owns the
    store, the run id and every row around the walk, and this function catches
    nothing -- an escalation is `summary.status == "escalated"`, a stop is
    `"stopped"`, and engine errors propagate.

    `stop` (T5) is the run's `StopSignal`, handed to the engine as is.
    `should_stop` is here only so `drive_subtask` can forward it; Task 3.3
    removes it. `resume_from` joins the walk's keywords only when given, so a
    fresh walk is called exactly as before.
    """
    factory = default_runner_factory if runner_factory is None else runner_factory
    runner = factory(
        store=store,
        run_id=run_id,
        story_id=parent.id,
        card_id=card.id,
    )
    walk: dict[str, Any] = {
        "story_id": parent.id,
        "subtask": subtask,
        "repo_dir": repo_dir,
        "commands": commands,
        "card": card,
        "parent_story": parent,
        "extra_context": gate_context(commands, allow_no_verification),
        "agent_runner": runner,
        "should_stop": should_stop,
        "stop": stop,
    }
    if resume_from is not None:
        walk["resume_from"] = resume_from
    summary = await runtime_engine.run_subtask_async(task_workflow.TASK, store, **walk)
    # `AgentRunner` collects gate warnings out of band (dispatch.py:375):
    # its signature returns a result, so a warning has nowhere else to go,
    # and dropping them is the §12 failure this whole list exists to prevent.
    warnings = list(summary.warnings) + list(getattr(runner, "warnings", []))
    return SubtaskDrive(summary=summary, warnings=warnings)


def drive_subtask(
    *,
    store: Store,
    run_id: str,
    card: models.Card,
    parent: models.Card,
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: RunnerFactory | None = None,
    should_stop: Callable[[], bool] | None = None,
    resume_from: store_module.Checkpoint | None = None,
) -> SubtaskDrive:
    """Walk one subtask through `workflow.task.TASK` under a store the caller owns.

    Addendum O4's shared driver. `run_card` calls it once, and a milestone runner
    calls it once per subtask against one store and one run id. The caller owns
    everything around the walk: the board reads, the run id, opening and
    closing the store, and the run/story/subtask rows. This function catches
    nothing. An escalation is `summary.status == "escalated"`, not an exception.
    `should_stop` goes straight to the engine; a stop is
    `summary.status == "stopped"`.

    One `asyncio.run` around `drive_subtask_async`, whose walk is
    `runtime.engine.run_subtask_async` over `TASK`. `resume_from` (card
    02890d5d) continues it from a saved checkpoint; it joins the walk's
    keywords only when given, so a fresh walk is called exactly as before.
    Being `asyncio.run`, it raises `RuntimeError` inside a running loop;
    callers there await `drive_subtask_async` instead.
    """
    return asyncio.run(
        drive_subtask_async(
            store=store,
            run_id=run_id,
            card=card,
            parent=parent,
            subtask=subtask,
            repo_dir=repo_dir,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            should_stop=should_stop,
            resume_from=resume_from,
        )
    )
```

- [ ] **Step 9: Run the driver tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "drive_subtask or two_async_drives or engine_error_escaping" -v`

Expected: PASS for every selected test, including the six new ones and the four adapted `drive_subtask` tests (`test_an_engine_error_escaping_the_walk_reaches_the_operator` and the two git/brd driver tests are skipped only if git or brd is absent).

- [ ] **Step 10: Run the full suite**

Run: `uv run pytest`

Expected: all tests pass (the default `-m "not e2e"` selection). In particular `tests/test_integration.py::test_resolve_conflict_walks_integrate_with_the_same_arguments` still passes with its untouched `run_subtask` stub, and `tests/test_orchestrate.py` still passes (its lane pool calls `cli.drive_subtask` from worker threads, where no loop runs, so the `asyncio.run` wrapper is safe there).

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add awaitable drive_subtask_async; drive_subtask wraps it in asyncio.run

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K"
```
