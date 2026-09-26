<!-- task-pipeline: validated -->
# Compile a workflow into `agent_phase` and `step_phase` (subtask 023d918e)

Task 3.3 of story f9c19dc3 "Run a workflow on pygents". This narrows plan `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 3.3 (lines 758-1001) and design `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (G2, G3, §4, §5, §9) to this card. The plan's code listing for `compile.py` is the reference implementation; this document fixes scope, behaviour and tests.

Note: the exploration summary handed to this stage was truncated at 8000 characters (it overran its brief). Anything below that was cut off was re-read from the plan, the design spec and the code, not guessed.

## Scope

In scope:

1. `src/agent_manager/harness/launcher.py`: `run_direct(argv, *, cwd, timeout, stdout_path, on_spawn=None)`. When `on_spawn` is given it is called with the `Popen` object right after `Popen(...)` returns, before anything waits on the process. The existing `_kill_tree` becomes public as `kill_tree(process)`, and `_kill_tree` stays as an alias so existing callers are unaffected. Callers that omit `on_spawn` see no change in behaviour.
2. `src/agent_manager/runtime/state.py`: the `RunDeps` dataclass (`workflow`, `store`, `story_id`, `subtask`, `agent_runner`, `clock`, `should_stop=None`, `warnings: list[str]`, `skipped: list[str]`, with list defaults via `default_factory`) and `current_run: ContextVar[RunDeps]`.
3. `src/agent_manager/runtime/bridge.py`:
   - `async call_agent(runner, phase, context, rendered)` runs `runner(phase, context, rendered)` through `asyncio.to_thread`. It keeps a per-call list of spawned processes, filled by an `on_spawn` hook held in a `threading.local` inside the worker thread. On `asyncio.CancelledError` it calls `launcher.kill_tree(p)` on every process recorded for that call, then re-raises. G2 applies here: `to_thread` cannot be cancelled, so killing the process is the bridge's job.
   - `current_spawn_hook()` returns the hook bound to the call running on the current thread, or `None` outside a call.
   - `async call_step(fn, kwargs)` runs `fn(**kwargs)` off the event loop and returns its result.
   - `last_spawned_for_tests()` is a test-only accessor for the most recently spawned process.
   - `AgentRunner` passes `on_spawn=bridge.current_spawn_hook()` to its launcher only when `inspect.signature(self.launcher).parameters` accepts `on_spawn`. This keeps the fake launchers in existing tests working. It is the only edit to `dispatch.py`.
4. `src/agent_manager/engine.py`: extract `run_one_step(*, phase, table, store, story_id, subtask, clock, workflow=None) -> _Outcome` from `_run_deterministic` and `_skip_target`. `_run_deterministic` then delegates to it. This must be a pure extraction: the old engine's phase rows, warnings, error text and outcomes stay byte-identical (G10). `phase` may be a loader `DeterministicPhase` or a `phases.Step`, and `run`, each gate and `when` are resolved as `entry if callable(entry) else workflow.function(entry)`, the pattern Task 3.2 uses in dispatch. The function label passed to `bind_arguments` and used in messages is the string name, or `__name__` for a callable.
   - Deviation from the plan's signature: the plan lists no `workflow` keyword. String entries on a loader phase cannot be resolved without one, and `phases.Workflow` has no `.function`. So `workflow` is an optional keyword that is only consulted for string entries. `compile.py` does not need to pass it.
5. `src/agent_manager/runtime/compile.py`:
   - `Escalated(Exception)` carries `.phase` and `.detail`. Its message is `"{phase}: {detail}"`.
   - `Compiled` is a frozen dataclass with `workflow`, `agent_phase` and `step_phase` fields and three methods:
     - `turn_for(name, loop)` returns a `Turn` whose kwargs are `{"phase", "loop"}`. Its timeout is the `AgentPhase.timeout` for agent phases and 3600s for steps.
     - `first_turn()`.
     - `after(name, loop)` returns the next phase's turn, or `None` after the last phase.
   - `compile_workflow(wf)` is cached per `(wf.name, wf.digest())` behind a module `threading.Lock`. Concurrent callers get the same `Compiled` object and never trip pygents' duplicate-name `ValueError`. `clear_cache()` is for tests.
   - The two tools are registered with `@tool` under `agent_phase_{wf.name}_{digest[:8]}` and `step_phase_{wf.name}_{digest[:8]}`. Implementation must first check whether pygents keys `ToolRegistry` on `__name__` or on `__qualname__`, and set both if needed.
   - `step_phase(phase, loop, pool)` does the following:
     - Builds the table with `context.binding_table`, then calls `bridge.call_step(engine.run_one_step, …)` with `deps` read from `current_run`.
     - Extends `deps.warnings` with the outcome's warnings.
     - On failure, a `best_effort` step appends `"best-effort phase {name!r} failed: {detail}"` to `deps.warnings` and falls through to the next phase. Any other step raises `Escalated(phase, detail)`. A gate verdict is a failure too.
     - On success, yields a `ContextItem(id=phase, content=context.encode(result))`.
     - If the step returns a `skip_to` (from `when`), extends `deps.skipped` with the phase names strictly between this phase and the target, and yields the target's turn. Otherwise it yields `after(...)`.
   - `agent_phase(phase, loop, pool, memory)` does the following:
     - Builds the table with `context.binding_table`, renders with `prompt.render_prompt`, and awaits `bridge.call_agent(deps.agent_runner, p, table, rendered)`.
     - On success, yields the result item and then the next turn.
     - On `AgentPhaseFailed`, if `on_fail` is set and `loop < on_fail.max_loops`, it yields a feedback `ContextItem` (`{"for", "from", "detail"}`) and then `turn_for(on_fail.phase, loop + 1)`. Otherwise it raises `Escalated(phase, failure.detail)`.

Out of scope:

- `runtime/engine.py`, `run_subtask`, `drive()` and parametrizing `tests/test_engine.py` belong to 2853e536.
- The `feedback` prompt resolver and the Goto loop's behavioural tests belong to b904b9e7. Only the loop mechanics above land here.
- `runtime/context.py` belongs to 8ae25085 and is consumed, not modified.
- `dispatch.evaluate_gates` and `prompt.py` belong to 1bbb532d and are not modified.
- Checkpointing, stop bridging and `should_stop` handling are not implemented here. The field exists only so later tasks can use it.

## Invariants

- Only `src/agent_manager/runtime/` imports pygents. `workflow/phases.py`, `task.py` and `integrate.py` stay pygents-free.
- Tools and hooks are module-level or registered once per compilation, never re-registered under an existing name. No pygents hook is registered as a closure (this task adds no hooks).
- Nothing here breaks or returns out of `agent.run()`. Tests consume `run()` to the end.
- The whole default suite stays green, including `tests/e2e` and the old engine's tests, which are unchanged.
- Fake runners in tests know only what their brief tells them.

## Error paths

- A step raises, returns a non-mapping, or gets a gate verdict. `run_one_step` records the phase row `failed` and returns `ok=False`. It never raises for a step's own exception, just as today. `step_phase` then either warns (`best_effort`) or raises `Escalated`.
- A gate `warn` verdict becomes a warning in `deps.warnings`, and the step continues.
- The agent runner raises `AgentPhaseFailed`. The phase loops through `Goto` if loops remain, and otherwise raises `Escalated(phase, detail)`.
- `call_agent` is cancelled. Every process spawned by that call is killed with `kill_tree` and `CancelledError` propagates. Processes spawned by other concurrent calls are untouched, because the hook is per-thread and per-call.
- `current_run` is unset when a tool runs: the `LookupError` propagates. This is a programming error, and `run_subtask` (3.4) always sets it.

## Tests

Test placement follows the project rule that tests mirror source under `tests/`. All of these run in the default suite. None needs the `e2e` marker, which pyproject reserves for the paid real-`claude` run, so none goes in `tests/e2e`. Parity parametrization over engines is 3.4's job, not this card's.

- `tests/runtime/conftest.py` (unit tier, `tests/runtime/`): an autouse fixture that clears `ToolRegistry` and `AgentRegistry` and calls `compile.clear_cache()` before and after each test.
- `tests/runtime/test_compile.py` (unit tier, mirrors `runtime/compile.py`). It drives a real pygents `Agent` with fake runners and a fake store:
  - `test_linear_flow_stores_every_result`: steps run in order, each binds the previous result, and the pool holds each result.
  - `test_skip_to_records_skipped_phases`: `when` is true, so execution jumps to `skip_to`, and the phases in between are recorded as skipped and never run.
  - `test_best_effort_failure_is_a_warning`: a `best_effort` step raises, the next phase still runs, and `deps.warnings` names the failure.
  - `test_step_gate_verdict_escalates`: a gate returns `{"blocked": …, "detail": "d"}`, which raises `Escalated` with `.phase == "a"` and `"d"` in `.detail`.
  - `test_agent_phase_failure_escalates`: the runner raises `AgentPhaseFailed` with no `on_fail`, which raises `Escalated(phase, detail)`.
  - `test_two_threads_compiling_at_once_share_one_compilation`: 8 threads compile the same workflow and get exactly one `Compiled` identity, with no `ValueError`.
- `tests/runtime/test_bridge.py` (unit tier, mirrors `runtime/bridge.py`):
  - `test_cancelling_a_call_kills_its_process`: the runner uses `launcher.run_direct` with `on_spawn=bridge.current_spawn_hook()` on a sleeping child. After the task is cancelled, `CancelledError` propagates and `last_spawned_for_tests().poll()` is not `None`.
- `tests/harness/test_launcher.py` (unit tier, existing file; additions only):
  - `on_spawn` is called once with the live `Popen` before the process exits.
  - Omitting `on_spawn` leaves behaviour unchanged.
  - `kill_tree` is exported and `_kill_tree is kill_tree`.
- Existing `tests/test_engine.py` and the rest of the suite stay unchanged and green. Together they prove that the `run_one_step` extraction is pure.

---

# Compile a Workflow into `agent_phase` and `step_phase` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a declared `phases.Workflow` into two generic pygents tools (`agent_phase`, `step_phase`) that run each phase through a cancellable bridge, share one deterministic-step implementation with the old engine, and escalate through a typed `Escalated` error.

**Architecture:** The launcher gains an `on_spawn` hook and a public `kill_tree`. `runtime/bridge.py` runs blocking work through `asyncio.to_thread` and, on cancellation, kills every process its call spawned. `engine.run_one_step` is extracted from the old engine so both engines judge a step identically. `runtime/compile.py` builds the two tools once per `(name, digest)` behind a lock; the tools read the run's dependencies from `state.current_run` and the phase from the running workflow.

**Tech Stack:** Python 3.12, pygents (installed in `.venv`, `pygents>=0.6.7`), pytest with `pytest-asyncio` in `asyncio_mode = "auto"`, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-compile-a-workflow-into-023d918e/docs/superpowers/specs/task-compile-a-workflow-into-023d918e-design.md` (reproduced verbatim above). The parent plan listing is `docs/superpowers/plans/2026-09-25-pygents-engine.md` lines 758-1001.

Upstream note: both the spec author's summary and the exploration summary handed to this stage were truncated (2393 > 2000 and 10652 > 8000 characters), which means those stages over-ran their briefs. Nothing here relies on the truncated text: the spec was read from disk, and every file this plan touches was read before its step was written.

Pygents fact checked for the spec's "check first" item: `ToolRegistry._key_attr = "__name__"` (`.venv/lib/python3.12/site-packages/pygents/registry.py:65`), and `BaseTool.__init__` copies `__name__`/`__qualname__` from the wrapped function via `functools.update_wrapper` (`pygents/tool.py:270`). Renaming `fn.__name__` before `tool(fn)` is therefore enough; this plan sets `__qualname__` too so reprs agree. Context injection of `pool: ContextPool` / `memory: ContextQueue` is by type hint (`pygents/utils.py:92-120`, `get_type_hints` against the function's module globals), and async-generator tools run in a producer task created inside `agent.run()` (`pygents/turn.py:313`), so a `ContextVar` set before `agent.run()` is visible inside the tools.

## Global Constraints

- Only `src/agent_manager/runtime/` imports pygents (rule 1); `tests/runtime/test_context.py::test_only_runtime_imports_pygents` already enforces it statically, and `bridge.py` must not import pygents because `dispatch.py` imports it.
- Tools are registered once per compilation under `agent_phase_{wf.name}_{digest[:8]}` and `step_phase_{wf.name}_{digest[:8]}`; no pygents hook is added by this card.
- Never break or return out of `agent.run()`; tests consume it with `async for _ in agent.run(): pass`.
- The old engine's phase rows, warnings, error text and outcomes stay byte-identical (G10); no existing test in `tests/test_engine.py` is edited.
- `run_direct` callers that omit `on_spawn` see no change; `_kill_tree` stays as an alias of `kill_tree`.
- All new tests are unit tier in the default suite (`tests/runtime/`, `tests/harness/test_launcher.py`, `tests/test_dispatch.py`, `tests/test_engine.py`); nothing is marked `e2e`.
- `runtime/engine.py`, `run_subtask`, `runtime/context.py`, `dispatch.evaluate_gates` and `prompt.py` are not created or modified.
- Verification command: `uv run pytest` (no separate lint or typecheck).

## Review Focus

1. A cancellation that lands before the worker thread has spawned its process: the process must still die, the moment it is spawned, not after the launcher's own timeout. Pinned in Task 2 (`test_a_process_spawned_after_the_call_was_cancelled_is_killed_at_once`).
2. Two lanes compiling the same workflow at once, and two versions of one workflow compiled in one process: one shared compilation for the first, two coexisting tool pairs for the second, never pygents' duplicate-name `ValueError`. Pinned in Task 5 (`test_two_threads_compiling_at_once_share_one_compilation`, `test_two_versions_of_one_workflow_compile_side_by_side`).
3. Two workflow objects with the same name and digest but different callables (a factory's closures share `module.qualname`, which is all the digest sees): a run must call its own workflow's callables, not the ones the cached compilation was built from. The tools therefore read the phase from `deps.workflow`. Pinned in Task 5 (`test_a_run_calls_its_own_workflows_callables_when_the_compilation_is_shared`).
4. An `on_spawn` hook that raises: the child it was handed must not be leaked for the whole launcher timeout. `run_direct` kills it and re-raises. Pinned in Task 1 (`test_an_on_spawn_that_raises_kills_the_child_and_propagates`).
5. An agent runner that raises something other than `AgentPhaseFailed` (an `EngineError`, an `OSError`): the old engine escalates it with `"{Type}: {message}"`, so the new tool must too, or 3.4's parity run diverges. Pinned in Task 6 (`test_an_unexpected_runner_error_escalates_with_the_rendered_error`).

Cross-card risk recorded for 2853e536, not fixed here (it lives in `runtime/context.py`, owned by 8ae25085): `context.binding_table` lets a pool item named after a reserved key (the shipped `builtin/task.yaml` has a phase named `worktree`) overwrite the seed value, which the old engine's `_bind_result` refuses to do.

---

## File Structure

- Modify `src/agent_manager/harness/launcher.py`: `kill_tree` public (alias `_kill_tree`), `run_direct(..., on_spawn=None)`.
- Create `src/agent_manager/runtime/bridge.py`: `call_agent`, `call_step`, `current_spawn_hook`, `last_spawned_for_tests`. No pygents import.
- Modify `src/agent_manager/dispatch.py`: `_spawn_kwargs(launcher)` and its use at the launcher call in `AgentRunner._attempt` (currently lines 519-521).
- Modify `src/agent_manager/engine.py`: `run_one_step`, `_resolve`, `_label`; `_evaluate_gates` and `_skip_target` take `workflow | None` and entries that may be callables; `_run_deterministic` delegates.
- Create `src/agent_manager/runtime/state.py`: `RunDeps`, `current_run`.
- Create `src/agent_manager/runtime/compile.py`: `Escalated`, `Compiled`, `compile_workflow`, `clear_cache`, `STEP_TIMEOUT`.
- Create `tests/runtime/conftest.py`, `tests/runtime/test_bridge.py`, `tests/runtime/test_compile.py`.
- Modify (additions only) `tests/harness/test_launcher.py`, `tests/test_dispatch.py`, `tests/test_engine.py`.

---

### Task 1: Launcher `on_spawn` hook and public `kill_tree`

**Files:**
- Modify: `src/agent_manager/harness/launcher.py:22-27` (imports), `:66-91` (`_kill_tree`), `:94-148` (`run_direct`)
- Test: `tests/harness/test_launcher.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `launcher.kill_tree(process: subprocess.Popen[bytes]) -> None`; `launcher._kill_tree is launcher.kill_tree`; `launcher.run_direct(argv, *, cwd, timeout, stdout_path, on_spawn: Callable[[subprocess.Popen[bytes]], None] | None = None) -> Outcome`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/test_launcher.py`:

```python
def test_on_spawn_is_called_once_with_the_live_process(tmp_path):
    # The bridge records the process through this hook so a cancelled turn can
    # kill it; a hook that ran after the wait would record a corpse.
    seen = []

    def hook(process):
        seen.append((process, process.poll()))

    outcome = launcher.run_direct(
        [sys.executable, "-c", "import time; time.sleep(0.3); print('done')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=tmp_path / "stdout.log",
        on_spawn=hook,
    )
    assert len(seen) == 1
    process, polled = seen[0]
    assert polled is None
    assert process.args[0] == sys.executable
    assert process.returncode == outcome.exit_code == 0


def test_passing_no_on_spawn_behaves_as_before(tmp_path):
    log = tmp_path / "stdout.log"
    outcome = launcher.run_direct(
        [sys.executable, "-c", "print('plain')"],
        cwd=tmp_path,
        timeout=30.0,
        stdout_path=log,
        on_spawn=None,
    )
    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert "plain" in log.read_text()


def test_an_on_spawn_that_raises_kills_the_child_and_propagates(tmp_path):
    # Review Focus 4: the hook failing must not leave a harness running for the
    # whole launcher timeout with nobody holding its handle.
    spawned = []

    def hook(process):
        spawned.append(process)
        raise RuntimeError("hook broke")

    with pytest.raises(RuntimeError, match="hook broke"):
        launcher.run_direct(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            timeout=120.0,
            stdout_path=tmp_path / "stdout.log",
            on_spawn=hook,
        )
    assert len(spawned) == 1
    assert spawned[0].poll() is not None


def test_kill_tree_is_public_and_the_old_name_is_an_alias():
    assert launcher._kill_tree is launcher.kill_tree
    fake = _FakeProcess(os.getpid())
    launcher.kill_tree(fake)
    assert fake.killed is True
    assert fake.waited is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/harness/test_launcher.py -k "on_spawn or kill_tree" -v`
Expected: FAIL. The three `run_direct` tests fail with `TypeError: run_direct() got an unexpected keyword argument 'on_spawn'`, and `test_kill_tree_is_public_and_the_old_name_is_an_alias` fails with `AttributeError: module 'agent_manager.harness.launcher' has no attribute 'kill_tree'`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/harness/launcher.py`, add `from collections.abc import Callable` to the imports so the block reads:

```python
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
```

Rename `def _kill_tree(process: subprocess.Popen[bytes]) -> None:` to `def kill_tree(process: subprocess.Popen[bytes]) -> None:` (body and docstring unchanged), then append a paragraph to the end of its docstring and add the alias directly after the function:

```python
    The bridge (`runtime/bridge.py`) calls this too, on every process a
    cancelled agent turn spawned: a `to_thread` worker cannot be cancelled, so
    the process it started has to be.
    """
    try:
        group = os.getpgid(process.pid)
    except (ProcessLookupError, PermissionError):
        group = None
    if group is not None and group != os.getpgid(0):
        os.killpg(group, signal.SIGKILL)
    else:
        process.kill()
    process.wait()


_kill_tree = kill_tree
"""The name this was private under; kept so existing callers are unaffected."""
```

Replace the `run_direct` signature and the `with` block with:

```python
def run_direct(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    stdout_path: Path,
    on_spawn: Callable[[subprocess.Popen[bytes]], None] | None = None,
) -> Outcome:
```

Append to the end of `run_direct`'s docstring (before the closing `"""`):

```python
    `on_spawn`, when given, is called with the live `Popen` right after it is
    created and before anything waits on it -- the bridge's way of learning
    which process a cancelled turn must kill. If it raises, the child is killed
    and the error propagates: nobody else holds its handle.
```

and replace the body from `with stdout_path.open("wb") as log, ...` down to the end of the `except subprocess.TimeoutExpired:` block with:

```python
    with stdout_path.open("wb") as log, open(os.devnull, "rb") as devnull:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=devnull,
            stdout=log,
            stderr=subprocess.STDOUT,
            # Its own session, so the timeout can kill the whole tree below it
            # and not just the process we spawned.
            start_new_session=True,
        )
        if on_spawn is not None:
            try:
                on_spawn(process)
            except BaseException:
                kill_tree(process)
                raise
        try:
            exit_code: int | None = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            kill_tree(process)
            exit_code = None
            timed_out = True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_launcher.py -v`
Expected: PASS, including the pre-existing `test_the_timeout_kill_never_signals_the_managers_own_process_group` (which still calls `launcher._kill_tree`).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/harness/launcher.py tests/harness/test_launcher.py
git commit -F - <<'EOF'
feat(launcher): add an on_spawn hook and make kill_tree public

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
```

---

### Task 2: The bridge (`runtime/bridge.py`)

**Files:**
- Create: `src/agent_manager/runtime/bridge.py`
- Test: `tests/runtime/test_bridge.py` (new, unit tier, mirrors `runtime/bridge.py`)

**Interfaces:**
- Consumes: `launcher.kill_tree(process)`, `launcher.run_direct(..., on_spawn=...)` from Task 1.
- Produces:
  - `bridge.SpawnHook = Callable[[subprocess.Popen], None]`
  - `async bridge.call_agent(runner: Callable[[Any, Any, Any], Any], phase: Any, context: Any, rendered: Any) -> Any`
  - `async bridge.call_step(fn: Callable[..., Any], kwargs: Mapping[str, Any]) -> Any`
  - `bridge.current_spawn_hook() -> SpawnHook | None`
  - `bridge.last_spawned_for_tests() -> subprocess.Popen[bytes] | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime/test_bridge.py`:

```python
"""The bridge between pygents' event loop and blocking engine code (pygents-engine design G2).

Unit tier: the children are `sys.executable -c` sleepers, as in
tests/harness/test_launcher.py -- never a harness. The runners are plain
functions that know only the arguments they are handed.
"""

import asyncio
import sys
import threading

import pytest

from agent_manager.errors import AgentPhaseFailed
from agent_manager.harness import launcher
from agent_manager.runtime import bridge

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


async def test_cancelling_a_call_kills_its_process(tmp_path):
    # Review Focus 1 of the parent plan: `to_thread` cannot be cancelled, so the
    # bridge has to kill what the abandoned thread started.
    started = threading.Event()

    def runner(phase, context, rendered):
        hook = bridge.current_spawn_hook()

        def on_spawn(process):
            hook(process)
            started.set()

        return launcher.run_direct(
            SLEEPER, cwd=tmp_path, timeout=120, stdout_path=tmp_path / "out",
            on_spawn=on_spawn,
        )

    task = asyncio.create_task(bridge.call_agent(runner, None, {}, None))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bridge.last_spawned_for_tests().poll() is not None


async def test_cancelling_one_call_leaves_another_calls_process_alone(tmp_path):
    events = {"a": threading.Event(), "b": threading.Event()}
    procs = {}

    def make_runner(key):
        def runner(phase, context, rendered):
            hook = bridge.current_spawn_hook()

            def on_spawn(process):
                procs[key] = process
                hook(process)
                events[key].set()

            return launcher.run_direct(
                SLEEPER, cwd=tmp_path, timeout=120,
                stdout_path=tmp_path / f"{key}.log", on_spawn=on_spawn,
            )

        return runner

    a = asyncio.create_task(bridge.call_agent(make_runner("a"), None, {}, None))
    b = asyncio.create_task(bridge.call_agent(make_runner("b"), None, {}, None))
    assert await asyncio.to_thread(events["a"].wait, 5)
    assert await asyncio.to_thread(events["b"].wait, 5)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    try:
        assert procs["a"].poll() is not None
        assert procs["b"].poll() is None
    finally:
        launcher.kill_tree(procs["b"])
        await b


async def test_a_process_spawned_after_the_call_was_cancelled_is_killed_at_once(tmp_path):
    # Review Focus 1: the cancel lands while the worker is still before Popen.
    go, finished = threading.Event(), threading.Event()
    seen = []

    def runner(phase, context, rendered):
        go.wait(5)
        hook = bridge.current_spawn_hook()
        try:
            return launcher.run_direct(
                SLEEPER, cwd=tmp_path, timeout=120, stdout_path=tmp_path / "out",
                on_spawn=lambda process: (seen.append(process), hook(process)),
            )
        finally:
            finished.set()

    task = asyncio.create_task(bridge.call_agent(runner, None, {}, None))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    go.set()
    assert await asyncio.to_thread(finished.wait, 10)
    assert len(seen) == 1
    assert seen[0].poll() is not None


async def test_a_call_returns_the_runners_result_and_binds_a_hook_only_inside_it():
    seen = []

    def runner(phase, context, rendered):
        seen.append(bridge.current_spawn_hook())
        return {"phase": phase, "context": context, "rendered": rendered}

    result = await bridge.call_agent(runner, "explore", {"k": 1}, "brief")

    assert result == {"phase": "explore", "context": {"k": 1}, "rendered": "brief"}
    assert callable(seen[0])
    assert bridge.current_spawn_hook() is None
    assert await asyncio.to_thread(bridge.current_spawn_hook) is None


async def test_a_runner_exception_propagates_unchanged():
    failure = AgentPhaseFailed("review", outcome="gate_failed", detail="blocked")

    def runner(phase, context, rendered):
        raise failure

    with pytest.raises(AgentPhaseFailed) as caught:
        await bridge.call_agent(runner, None, {}, None)
    assert caught.value is failure


async def test_call_step_runs_the_function_off_the_event_loop():
    loop_thread = threading.get_ident()

    def fn(x):
        return x, threading.get_ident()

    value, thread = await bridge.call_step(fn, {"x": 1})

    assert value == 1
    assert thread != loop_thread
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_bridge.py -v`
Expected: FAIL at collection with `ImportError: cannot import name 'bridge' from 'agent_manager.runtime'`.

- [ ] **Step 3: Write the implementation**

Create `src/agent_manager/runtime/bridge.py`:

```python
"""The one door between pygents' event loop and blocking engine code (pygents-engine design G2).

An agent phase blocks for as long as `claude -p` runs, and a step blocks on
git, a test suite or the board, so both run through `asyncio.to_thread`. A
`to_thread` worker cannot be cancelled: cancelling the awaiting task abandons
the thread, it does not stop it. So `call_agent` stops what the thread started.
Each call keeps its own record of the processes it spawned, reached from inside
the worker thread through `current_spawn_hook()`; on `CancelledError` every one
of them is killed with `launcher.kill_tree` before the error propagates, and a
process the worker spawns after the cancel is killed the moment it appears.

The hook is per thread and per call, so cancelling one call never touches a
process another concurrent call spawned. Nothing here imports pygents:
`dispatch.py` imports this module, and rule 1 keeps pygents inside the runtime
modules that need it.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
from collections.abc import Callable, Mapping
from typing import Any

from agent_manager.harness import launcher

SpawnHook = Callable[[subprocess.Popen], None]

_local = threading.local()
_last_spawned: subprocess.Popen[bytes] | None = None


class _Call:
    """The processes one `call_agent` spawned, and whether it was cancelled."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spawned: list[subprocess.Popen[bytes]] = []
        self._cancelled = False

    def on_spawn(self, process: subprocess.Popen[bytes]) -> None:
        global _last_spawned
        _last_spawned = process
        with self._lock:
            self._spawned.append(process)
            cancelled = self._cancelled
        if cancelled:
            launcher.kill_tree(process)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            spawned = list(self._spawned)
        for process in spawned:
            launcher.kill_tree(process)


def current_spawn_hook() -> SpawnHook | None:
    """The `on_spawn` bound to the `call_agent` running on this thread, else `None`."""
    return getattr(_local, "hook", None)


def last_spawned_for_tests() -> subprocess.Popen[bytes] | None:
    """The most recently spawned process of any call. Test-only."""
    return _last_spawned


async def call_agent(
    runner: Callable[[Any, Any, Any], Any], phase: Any, context: Any, rendered: Any
) -> Any:
    """`runner(phase, context, rendered)` off the loop; kill its processes if cancelled."""
    call = _Call()

    def work() -> Any:
        _local.hook = call.on_spawn
        try:
            return runner(phase, context, rendered)
        finally:
            _local.hook = None

    try:
        return await asyncio.to_thread(work)
    except asyncio.CancelledError:
        call.cancel()
        raise


async def call_step(fn: Callable[..., Any], kwargs: Mapping[str, Any]) -> Any:
    """`fn(**kwargs)` off the loop. A step spawns nothing the bridge must track."""
    return await asyncio.to_thread(lambda: fn(**kwargs))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_bridge.py tests/runtime/test_context.py -v`
Expected: PASS (six bridge tests; `test_only_runtime_imports_pygents` still passes).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runtime/bridge.py tests/runtime/test_bridge.py
git commit -F - <<'EOF'
feat(runtime): add the bridge that kills a cancelled call's processes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
```

---

### Task 3: `AgentRunner` hands the bridge hook to a launcher that accepts it

**Files:**
- Modify: `src/agent_manager/dispatch.py:22-39` (imports), add `_spawn_kwargs` after `_utcnow` (line ~363), launcher call at `:519-521`
- Test: `tests/test_dispatch.py` (append; add one import)

**Interfaces:**
- Consumes: `bridge.current_spawn_hook()` and `bridge.call_agent(...)` from Task 2.
- Produces: `dispatch._spawn_kwargs(launcher) -> dict[str, Any]` (private); `AgentRunner` passes `on_spawn=` only to launchers whose signature declares `on_spawn`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_dispatch.py`, add to the import block (after `from agent_manager.roles.loader import load_role`):

```python
from agent_manager.runtime import bridge
```

Append to the end of `tests/test_dispatch.py`:

```python
# ── the bridge's spawn hook reaches a launcher that declares it ──────────────


@dataclass
class SpawnAwareLauncher(FakeLauncher):
    """A `FakeLauncher` that declares `on_spawn`, as `launcher.run_direct` does."""

    hooks: list[object] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path, on_spawn=None) -> Outcome:
        self.hooks.append(on_spawn)
        return super().__call__(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path
        )


async def test_a_launcher_that_declares_on_spawn_gets_the_bridge_calls_hook(
    store, tmp_path, worktree
):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = SpawnAwareLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = await bridge.call_agent(
        runner, workflow.phase("explore"), _context(worktree), _rendered()
    )

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.hooks) == 1
    assert callable(launcher.hooks[0])


def test_a_launcher_that_declares_on_spawn_gets_none_outside_a_bridge_call(
    store, tmp_path, worktree
):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = SpawnAwareLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert launcher.hooks == [None]


async def test_a_launcher_without_on_spawn_still_works_inside_a_bridge_call(
    store, tmp_path, worktree
):
    workflow = _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[VALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = await bridge.call_agent(
        runner, workflow.phase("explore"), _context(worktree), _rendered()
    )

    assert result == {"summary": "explored the tree", "ok": True}
    assert len(launcher.calls) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -k "on_spawn" -v`
Expected: `test_a_launcher_that_declares_on_spawn_gets_the_bridge_calls_hook` FAILS with `assert callable(None)` (the runner never passes the hook, so the default `None` is recorded). The other two already pass; they pin the no-hook path and the four-keyword fake launchers, and must stay green.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/dispatch.py`, change the top of the import block to:

```python
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
```

and add, after `from agent_manager.roles.loader import RoleBundle, load_role`:

```python
from agent_manager.runtime import bridge
```

After `def _utcnow() -> datetime: ...` (before `Clock = Callable[[], datetime]`), add:

```python
def _spawn_kwargs(launcher: LauncherFn) -> dict[str, Any]:
    """`on_spawn` for a launcher that declares it, bound to the bridge call in flight.

    Inside `bridge.call_agent` the hook records every process this attempt
    starts, so a cancelled turn can kill it (pygents-engine design G2);
    anywhere else it is `None`, which `run_direct` treats as absent. A launcher
    that does not declare the keyword -- every fake launcher in the tests --
    is called exactly as before.
    """
    try:
        parameters = inspect.signature(launcher).parameters
    except (TypeError, ValueError):
        return {}
    if "on_spawn" not in parameters:
        return {}
    return {"on_spawn": bridge.current_spawn_hook()}
```

In `AgentRunner._attempt`, replace:

```python
        outcome = self.launcher(
            argv, cwd=cwd, timeout=self.timeout, stdout_path=stdout_path
        )
```

with:

```python
        outcome = self.launcher(
            argv,
            cwd=cwd,
            timeout=self.timeout,
            stdout_path=stdout_path,
            **_spawn_kwargs(self.launcher),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py tests/runtime/test_context.py -v`
Expected: PASS, including every pre-existing dispatch test and `test_only_runtime_imports_pygents`.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -F - <<'EOF'
feat(dispatch): pass the bridge's spawn hook to launchers that accept it

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
```

---

### Task 4: Extract `engine.run_one_step`

**Files:**
- Modify: `src/agent_manager/engine.py:26-29` (imports), `:291-337` (`_evaluate_gates`, `_render_verdict`, `_skip_target`), `:487-531` (`_run_deterministic`), `:538-547` (`_record_phase` annotation)
- Test: `tests/test_engine.py` (append a new section; add one import; no existing test edited)

**Interfaces:**
- Consumes: `phases.Step` (existing, `src/agent_manager/workflow/phases.py:43-51`), loader `DeterministicPhase`/`Workflow.function`.
- Produces: `engine.run_one_step(*, phase: DeterministicPhase | phases.Step, table: Mapping[str, Any], store: Store, story_id: str, subtask: models.SubtaskRun, clock: Clock, workflow: Workflow | None = None) -> engine._Outcome` where `_Outcome` has `ok: bool`, `result`, `detail: str | None`, `warnings: list[str]`, `skip_to: str | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_engine.py`, add to the import block (after `from agent_manager.steps import integrate, reducers`):

```python
from agent_manager.workflow import phases as phase_model
```

Append to the end of `tests/test_engine.py`:

```python
# ── run_one_step: one deterministic phase, either phase type ────────────────

FIXED = datetime(2026, 9, 26, tzinfo=timezone.utc)


def _step_functions() -> dict[str, Any]:
    return {
        "step.alpha": lambda card: {"card": card},
        "step.beta": lambda card: {},
        "step.gamma": lambda card: {},
    }


def test_run_one_step_calls_a_phase_models_callables_directly(store):
    seen: dict[str, Any] = {}

    def build(card):
        return {"built": card}

    def ok_gate(result):
        seen["gate"] = result

    step = phase_model.Step(
        "a",
        build,
        gates=(ok_gate,),
        when=lambda result: result["built"] == "c1",
        skip_to="z",
    )

    outcome = engine.run_one_step(
        phase=step, table={"card": "c1"}, store=store, story_id=STORY_ID,
        subtask=_subtask(), clock=lambda: FIXED,
    )

    assert outcome.ok is True
    assert outcome.result == {"built": "c1"}
    assert outcome.skip_to == "z"
    assert outcome.warnings == []
    assert seen["gate"] == {"built": "c1"}
    assert _journalled_phases(store) == [("a", "started"), ("a", "done")]


def test_run_one_step_names_a_callable_gate_by_its_function_name(store):
    def blocking(result):
        return {"blocked": "x"}

    step = phase_model.Step("a", lambda: {}, gates=(blocking,))

    outcome = engine.run_one_step(
        phase=step, table={}, store=store, story_id=STORY_ID,
        subtask=_subtask(), clock=lambda: FIXED,
    )

    assert outcome.ok is False
    assert outcome.detail == "phase 'a' gate 'blocking' failed: blocked=x"
    assert _journalled_phases(store) == [("a", "started"), ("a", "failed")]


def test_run_one_step_resolves_a_loader_phases_names_through_the_workflow(store):
    loaded = _workflow(THREE_PHASES, _step_functions())

    outcome = engine.run_one_step(
        phase=loaded.phases[0], table={"card": "c1"}, store=store,
        story_id=STORY_ID, subtask=_subtask(), clock=lambda: FIXED,
        workflow=loaded,
    )

    assert outcome.ok is True
    assert outcome.result == {"card": "c1"}


def test_run_one_step_fails_a_named_function_it_has_no_workflow_to_resolve(store):
    loaded = _workflow(THREE_PHASES, _step_functions())

    outcome = engine.run_one_step(
        phase=loaded.phases[0], table={"card": "c1"}, store=store,
        story_id=STORY_ID, subtask=_subtask(), clock=lambda: FIXED,
    )

    assert outcome.ok is False
    assert outcome.detail.startswith("EngineError: ")
    assert "'step.alpha'" in outcome.detail
    assert _journalled_phases(store) == [("alpha", "started"), ("alpha", "failed")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -k run_one_step -v`
Expected: FAIL with `AttributeError: module 'agent_manager.engine' has no attribute 'run_one_step'`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/engine.py`, add after `from agent_manager.store import Store`:

```python
from agent_manager.workflow import phases as phase_model
```

and after `from agent_manager.workflow.loader import AgentPhase, DeterministicPhase, Workflow`:

```python
AnyStep = DeterministicPhase | phase_model.Step
"""Either deterministic-phase type: the YAML one (`run`, gates and `when` as
names) or the declared phase model (the callables themselves)."""
```

Replace `_evaluate_gates`, `_render_verdict` and `_skip_target` (lines 291-337) with:

```python
def _resolve(entry: Any, workflow: Workflow | None) -> Callable[..., Any]:
    """A phase's `run`, gate or `when` entry as the callable to call.

    A `phases.Step` holds the callable itself; a loader phase holds a name,
    which only the workflow that loaded it can resolve.
    """
    if callable(entry):
        return entry
    if workflow is None:
        raise EngineError(
            f"names function {entry!r}, but no workflow was given to resolve it"
        )
    return workflow.function(entry)


def _label(entry: Any) -> str:
    """How messages name an entry: the name as written, or a callable's `__name__`."""
    return entry if isinstance(entry, str) else getattr(entry, "__name__", repr(entry))


def _evaluate_gates(
    phase: AnyStep,
    workflow: Workflow | None,
    values: Mapping[str, Any],
    warnings: list[str],
) -> None:
    """Run every gate in order; append warnings, raise `_GateFailed` on a verdict."""
    for entry in phase.gates:
        gate = _resolve(entry, workflow)
        name = _label(entry)
        kwargs = bind_arguments(gate, values, phase=phase.name, function=name)
        verdict = gate(**kwargs)
        if verdict is None:
            continue
        if not isinstance(verdict, Mapping):
            raise EngineError(
                f"gate returned {type(verdict).__name__}; a gate returns None to pass "
                "or a mapping verdict to fail, and anything else would be read as a "
                "pass by accident",
                phase=phase.name,
                function=name,
            )
        if "warn" in verdict:
            warnings.append(
                f"phase {phase.name!r} gate {name!r} warned: {verdict['warn']}"
            )
            continue
        raise _GateFailed(f"phase {phase.name!r} gate {name!r} failed: {_render_verdict(verdict)}")


def _render_verdict(verdict: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(verdict.items()))


def _skip_target(
    phase: AnyStep, workflow: Workflow | None, values: Mapping[str, Any]
) -> str | None:
    """The phase to jump to, or `None` to fall through to the next one.

    Both `when` and `skip_to` are required for a jump: `when` alone has nowhere
    to go, and `skip_to` alone would be an unconditional jump the document
    author did not write.
    """
    if phase.when is None or phase.skip_to is None:
        return None
    predicate = _resolve(phase.when, workflow)
    kwargs = bind_arguments(
        predicate, values, phase=phase.name, function=_label(phase.when)
    )
    return phase.skip_to if predicate(**kwargs) else None
```

Replace `_run_deterministic` (lines 487-531) with:

```python
def _run_deterministic(
    phase: DeterministicPhase,
    workflow: Workflow,
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    context: Mapping[str, Any],
    clock: Clock,
) -> _Outcome:
    return run_one_step(
        phase=phase,
        table=context,
        store=store,
        story_id=story_id,
        subtask=subtask,
        clock=clock,
        workflow=workflow,
    )


def run_one_step(
    *,
    phase: AnyStep,
    table: Mapping[str, Any],
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    clock: Clock,
    workflow: Workflow | None = None,
) -> _Outcome:
    """One deterministic phase, run, judged and recorded.

    The old engine's walk and the pygents engine's `step_phase` both call this,
    so both judge a step identically: binding, the mapping check, gates, `when`
    and `skip_to`, and the `started`/`done`/`failed` phase rows. `workflow` is
    needed only to resolve a loader phase's names; a `phases.Step` carries its
    callables. `best_effort` is the caller's to apply -- this returns the
    verdict, not the walk's reaction to it.
    """
    started_at = clock()
    _record_phase(store, story_id, subtask, phase, "started", started_at, None)
    warnings: list[str] = []
    try:
        function = _resolve(phase.run, workflow)
        label = _label(phase.run)
        kwargs = bind_arguments(
            function, table, phase.args, phase=phase.name, function=label
        )
        result = function(**kwargs)
        if not isinstance(result, Mapping):
            raise EngineError(
                f"returned {type(result).__name__}, but a deterministic phase must "
                "return a mapping: a gate or a later `when` would read anything else "
                "as closed and the run would branch wrongly",
                phase=phase.name,
                function=label,
            )
        _evaluate_gates(phase, workflow, _gate_values(table, phase.name, result), warnings)
        skip_to = _skip_target(phase, workflow, _gate_values(table, phase.name, result))
    except _GateFailed as failure:
        _record_phase(
            store, story_id, subtask, phase, "failed", started_at, clock(), failure.detail
        )
        return _Outcome(ok=False, detail=failure.detail, warnings=warnings)
    except Exception as error:
        # Deliberately total. A step is other people's code -- GitError, OSError,
        # anything -- and an exception escaping the walk would leave the subtask
        # recorded `started` forever, which is exactly what resume mistakes for
        # work in flight.
        detail = _render_error(error)
        _record_phase(
            store, story_id, subtask, phase, "failed", started_at, clock(), detail
        )
        return _Outcome(ok=False, detail=detail, warnings=warnings)
    _record_phase(store, story_id, subtask, phase, "done", started_at, clock())
    return _Outcome(ok=True, result=result, warnings=warnings, skip_to=skip_to)
```

In `_record_phase`, change the annotation `phase: DeterministicPhase,` to `phase: AnyStep,` (only `.name` is read).

Also update the `_GateFailed` docstring to `"""A gate returned a verdict. Private: it never leaves `run_one_step`."""`.

- [ ] **Step 4: Run the tests to verify they pass, and that the extraction is pure**

Run: `uv run pytest tests/test_engine.py -v`
Expected: PASS: the four new `run_one_step` tests and every pre-existing test unchanged.

Run: `uv run pytest`
Expected: PASS (whole default suite; the old engine's behaviour is byte-identical).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -F - <<'EOF'
refactor(engine): extract run_one_step for both engines

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
```

---

### Task 5: `RunDeps`, and compiling a workflow (steps, cache, names, agent success)

**Files:**
- Create: `src/agent_manager/runtime/state.py`, `src/agent_manager/runtime/compile.py`, `tests/runtime/conftest.py`
- Test: `tests/runtime/test_compile.py` (new, unit tier, mirrors `runtime/compile.py`)

**Interfaces:**
- Consumes: `bridge.call_agent`, `bridge.call_step` (Task 2); `engine.run_one_step` (Task 4); `context.binding_table(pool, memory, phase)`, `context.encode(value)`, `context.seed_item(binding)` (existing, `src/agent_manager/runtime/context.py`); `prompt.render_prompt(phase, context)`; `phases.Workflow.phase(name)`, `.phase_names`, `.phases`, `.digest()`.
- Produces:
  - `state.RunDeps(workflow, store, story_id, subtask, agent_runner, clock, should_stop=None, warnings=[], skipped=[])`, `state.current_run: ContextVar[RunDeps]`
  - `compile.Escalated(phase: str, detail: str | None)` with `.phase`, `.detail`, `str(e) == f"{phase}: {detail}"`
  - `compile.Compiled(workflow, agent_phase, step_phase)` with `.turn_for(name, loop) -> Turn`, `.first_turn() -> Turn`, `.after(name, loop) -> Turn | None`
  - `compile.compile_workflow(wf: phases.Workflow) -> Compiled`, `compile.clear_cache() -> None`, `compile.STEP_TIMEOUT = 3600.0`

- [ ] **Step 1: Write the conftest and the failing tests**

Create `tests/runtime/conftest.py`:

```python
"""Fresh pygents registries and compile cache around every runtime test.

pygents' `ToolRegistry` and `AgentRegistry` are process-wide and refuse a
second entry under a name they hold, and `compile_workflow` caches per
`(name, digest)`. Clearing all three before and after each test keeps one
test's compilation or agent from colliding with the next.
"""

import pytest
from pygents import AgentRegistry, ToolRegistry

from agent_manager.runtime import compile as compile_mod


@pytest.fixture(autouse=True)
def fresh_pygents():
    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()
    yield
    ToolRegistry.clear()
    AgentRegistry.clear()
    compile_mod.clear_cache()
```

Create `tests/runtime/test_compile.py`:

```python
"""Compiling a `phases.Workflow` into two pygents tools (pygents-engine design G3, §5).

Unit tier: a real pygents `Agent` drives the compiled tools over fake steps and
a fake agent runner that knows only the arguments it is handed. The store is a
real temp SQLite projection plus a real temp JSONL journal, built as
tests/test_engine.py builds it. No git, no board, no harness process.
"""

import itertools
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, ContextPool, ContextQueue, ToolRegistry

from agent_manager import models, store as store_module
from agent_manager.runtime import compile as C, context, state
from agent_manager.workflow.phases import AgentPhase, Step, Workflow

RUN_ID = "run-2026-09-26-01"
STORY_ID = "f9c19dc3"
FIXED = datetime(2026, 9, 26, tzinfo=timezone.utc)
_agent_names = itertools.count()


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


def _noop() -> dict[str, Any]:
    return {}


def _subtask() -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id="023d918e",
        branch="m6/task-compile-a-workflow-into-023d918e",
        base_branch="m6/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _deps(workflow, store, runner=None) -> state.RunDeps:
    return state.RunDeps(
        workflow=workflow,
        store=store,
        story_id=STORY_ID,
        subtask=_subtask(),
        agent_runner=runner,
        clock=lambda: FIXED,
    )


async def _drive(workflow, deps) -> Agent:
    compiled = C.compile_workflow(workflow)
    agent = Agent(
        f"run:{next(_agent_names)}",
        "test run",
        [compiled.agent_phase, compiled.step_phase],
        context_pool=ContextPool(),
        context_queue=ContextQueue(limit=10),
        tags=["subtask"],
    )
    await agent.context_pool.add(context.seed_item({"worktree": "/w"}))
    token = state.current_run.set(deps)
    try:
        await agent.put(compiled.first_turn())
        async for _ in agent.run():
            pass
    finally:
        state.current_run.reset(token)
    return agent


def _phase_rows(opened) -> list[tuple[str | None, str]]:
    return [
        (line.phase, line.payload["status"])
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ]


def _pool_ids(agent) -> list[str]:
    return [item.id for item in agent.context_pool.items]


async def test_linear_flow_stores_every_result(store):
    ran: list[Any] = []
    wf = Workflow("t", (
        Step("a", lambda worktree: ran.append(("a", worktree)) or {"a": 1}),
        Step("b", lambda a: ran.append(("b", a)) or {"b": 2}),
    ))
    deps = _deps(wf, store)

    agent = await _drive(wf, deps)

    assert ran == [("a", "/w"), ("b", {"a": 1})]
    assert agent.context_pool.get("a").content == {"a": 1}
    assert agent.context_pool.get("b").content == {"b": 2}
    assert _phase_rows(store) == [
        ("a", "started"), ("a", "done"), ("b", "started"), ("b", "done"),
    ]
    assert deps.warnings == []
    assert deps.skipped == []


async def test_an_agent_result_is_pooled_and_bound_into_the_next_step(store):
    briefs: list[Any] = []
    seen: list[Any] = []

    def runner(phase, table, rendered):
        briefs.append((phase.name, table["worktree"], rendered.phase))
        return {"summary": "explored"}

    wf = Workflow("t", (
        AgentPhase("explore", "explorer", (), None),
        Step("use", lambda explore: seen.append(explore) or {}),
    ))

    agent = await _drive(wf, _deps(wf, store, runner))

    assert briefs == [("explore", "/w", "explore")]
    assert seen == [{"summary": "explored"}]
    assert agent.context_pool.get("explore").content == {"summary": "explored"}


async def test_skip_to_records_skipped_phases(store):
    ran: list[str] = []
    wf = Workflow("t", (
        Step("a", lambda: {"done": True}, when=lambda result: result["done"], skip_to="d"),
        Step("b", lambda: ran.append("b") or {}),
        Step("c", lambda: ran.append("c") or {}),
        Step("d", lambda: ran.append("d") or {}),
    ))
    deps = _deps(wf, store)

    agent = await _drive(wf, deps)

    assert ran == ["d"]
    assert deps.skipped == ["b", "c"]
    assert _pool_ids(agent) == ["subtask", "a", "d"]


async def test_best_effort_failure_is_a_warning(store):
    ran: list[str] = []

    def board_move():
        raise RuntimeError("board down")

    wf = Workflow("t", (
        Step("move", board_move, best_effort=True),
        Step("b", lambda: ran.append("b") or {}),
    ))
    deps = _deps(wf, store)

    agent = await _drive(wf, deps)

    assert ran == ["b"]
    assert deps.warnings == ["best-effort phase 'move' failed: RuntimeError: board down"]
    assert "move" not in _pool_ids(agent)
    assert _phase_rows(store)[:2] == [("move", "started"), ("move", "failed")]


async def test_a_gate_warning_lands_in_the_run_warnings(store):
    def slow_gate(result):
        return {"warn": "slow"}

    wf = Workflow("t", (Step("a", _noop, gates=(slow_gate,)),))
    deps = _deps(wf, store)

    await _drive(wf, deps)

    assert deps.warnings == ["phase 'a' gate 'slow_gate' warned: slow"]


async def test_step_gate_verdict_escalates(store):
    ran: list[str] = []
    wf = Workflow("t", (
        Step("a", _noop, gates=(lambda result: {"blocked": "x", "detail": "d"},)),
        Step("b", lambda: ran.append("b") or {}),
    ))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store))

    assert info.value.phase == "a"
    assert "d" in info.value.detail
    assert ran == []
    assert _phase_rows(store) == [("a", "started"), ("a", "failed")]


async def test_a_raising_step_escalates_with_the_rendered_error(store):
    def boom():
        raise ValueError("bad input")

    wf = Workflow("t", (Step("a", boom), Step("b", _noop)))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store))

    assert info.value.phase == "a"
    assert info.value.detail == "ValueError: bad input"
    assert str(info.value) == "a: ValueError: bad input"


def test_turns_carry_the_phase_the_loop_and_a_timeout():
    wf = Workflow("t", (
        Step("a", _noop),
        AgentPhase("b", "explorer", (), None, timeout=timedelta(minutes=40)),
    ))
    compiled = C.compile_workflow(wf)

    first = compiled.first_turn()
    nxt = compiled.after("a", 2)

    assert first.tool is compiled.step_phase
    assert first.kwargs == {"phase": "a", "loop": 0}
    assert first.timeout == C.STEP_TIMEOUT == 3600
    assert nxt.tool is compiled.agent_phase
    assert nxt.kwargs == {"phase": "b", "loop": 2}
    assert nxt.timeout == 2400
    assert compiled.after("b", 0) is None


def test_the_tools_are_registered_under_digest_suffixed_names():
    wf = Workflow("t", (Step("a", _noop),))

    compiled = C.compile_workflow(wf)

    suffix = wf.digest()[:8]
    assert compiled.agent_phase.__name__ == f"agent_phase_t_{suffix}"
    assert ToolRegistry.get(f"agent_phase_t_{suffix}") is compiled.agent_phase
    assert ToolRegistry.get(f"step_phase_t_{suffix}") is compiled.step_phase
    assert C.compile_workflow(wf) is compiled


def test_two_versions_of_one_workflow_compile_side_by_side():
    # Review Focus 2: an edited workflow keeps its name but not its digest.
    v1 = Workflow("t", (Step("a", _noop),))
    v2 = Workflow("t", (Step("a", _noop), Step("b", _noop)))

    first, second = C.compile_workflow(v1), C.compile_workflow(v2)

    assert first is not second
    assert first.step_phase.__name__ != second.step_phase.__name__


async def test_a_run_calls_its_own_workflows_callables_when_the_compilation_is_shared(store):
    # Review Focus 3: the digest keys callables on module.qualname, which two
    # closures from one factory share.
    calls: list[int] = []

    def make(tag):
        def step():
            calls.append(tag)
            return {}

        return step

    first = Workflow("t", (Step("a", make(1)),))
    second = Workflow("t", (Step("a", make(2)),))
    assert first.digest() == second.digest()

    await _drive(first, _deps(first, store))
    await _drive(second, _deps(second, store))

    assert calls == [1, 2]


def test_two_threads_compiling_at_once_share_one_compilation():
    # Review Focus 2: two lanes, one workflow, no duplicate-name ValueError.
    wf = Workflow("t", (Step("a", _noop),))
    out: list[Any] = []
    errors: list[BaseException] = []

    def work():
        try:
            out.append(C.compile_workflow(wf))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(out) == 8
    assert len({id(c) for c in out}) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_compile.py -v`
Expected: FAIL at collection. `tests/runtime/conftest.py` raises `ImportError: cannot import name 'compile' from 'agent_manager.runtime'` (this also errors `tests/runtime/test_bridge.py` and `test_context.py` until Step 3 lands, since they share the conftest).

- [ ] **Step 3: Write the implementation**

Create `src/agent_manager/runtime/state.py`:

```python
"""What a running subtask's tools need, reachable without passing it through pygents.

A pygents turn carries only JSON-able kwargs (`{"phase", "loop"}`), and a
checkpoint must not hold a store or a runner. So the run's dependencies live in
one `RunDeps`, set in `current_run` by whoever drives the agent (Task 3.4's
`run_subtask`), and read by the compiled tools. The tools run in a task
pygents creates inside `agent.run()`, which copies the context, so the value
set before the run is the value they see.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RunDeps:
    workflow: Any
    store: Any
    story_id: str
    subtask: Any
    agent_runner: Callable[..., Any] | None
    clock: Callable[[], Any]
    should_stop: Callable[[], bool] | None = None
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


current_run: ContextVar[RunDeps] = ContextVar("current_run")
```

Create `src/agent_manager/runtime/compile.py`:

```python
"""A `phases.Workflow` as two generic pygents tools (pygents-engine design G3, §5).

Every phase runs through one of two tools: `agent_phase` for an `AgentPhase`,
`step_phase` for a `Step`. A turn names the tool and carries only
`{"phase", "loop"}`; everything else is read at run time -- the binding table
from the pool, the run's dependencies from `state.current_run`.

pygents' `ToolRegistry` is process-wide, keyed on the tool's `__name__`, and
refuses a second tool under a name it holds. So the tools are named after the
workflow and the first eight hex digits of its digest, and a compilation is
built once per `(name, digest)` behind a lock: two lanes compiling one workflow
share one compilation, and two versions of a workflow get two.

The tools read each phase from the running workflow (`deps.workflow`), not the
compiled one. Two workflow objects can share a name and a digest -- the digest
keys a callable on `module.qualname`, which a factory's closures share -- while
holding different callables, and a shared compilation must still call the
running workflow's own. Everything the compilation itself reads (phase order,
kinds, timeouts) is covered by the digest, so it cannot differ between them.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from pygents import ContextItem, ContextPool, ContextQueue, Turn, tool

from agent_manager import engine as old_engine
from agent_manager import prompt
from agent_manager.runtime import bridge, context
from agent_manager.runtime.state import current_run
from agent_manager.workflow.phases import AgentPhase, Workflow

STEP_TIMEOUT = 3600.0
"""A step's turn timeout, in seconds. Steps have no declared timeout; an hour
bounds a hung git or test-suite call without cutting a slow suite short."""


class Escalated(Exception):
    """A phase ended the subtask: `.phase` names it, `.detail` says why."""

    def __init__(self, phase: str, detail: str | None) -> None:
        self.phase = phase
        self.detail = detail
        super().__init__(f"{phase}: {detail}")


@dataclass(frozen=True)
class Compiled:
    """One workflow's two registered tools, and the turns that drive them."""

    workflow: Workflow
    agent_phase: Any
    step_phase: Any

    def turn_for(self, name: str, loop: int) -> Turn:
        p = self.workflow.phase(name)
        kwargs = {"phase": name, "loop": loop}
        if isinstance(p, AgentPhase):
            return Turn(self.agent_phase, timeout=p.timeout.total_seconds(), kwargs=kwargs)
        return Turn(self.step_phase, timeout=STEP_TIMEOUT, kwargs=kwargs)

    def first_turn(self) -> Turn:
        return self.turn_for(self.workflow.phases[0].name, 0)

    def after(self, name: str, loop: int) -> Turn | None:
        names = self.workflow.phase_names
        i = names.index(name)
        return self.turn_for(names[i + 1], loop) if i + 1 < len(names) else None


_CACHE: dict[tuple[str, str], Compiled] = {}
_LOCK = threading.Lock()


def clear_cache() -> None:
    """Forget every compilation. For tests, which clear `ToolRegistry` alongside:
    recompiling while the old tools are still registered would be refused."""
    with _LOCK:
        _CACHE.clear()


def compile_workflow(wf: Workflow) -> Compiled:
    key = (wf.name, wf.digest())
    with _LOCK:
        compiled = _CACHE.get(key)
        if compiled is None:
            compiled = _CACHE[key] = _build(wf, suffix=key[1][:8])
        return compiled


def _rename(fn: Any, name: str) -> None:
    # `ToolRegistry` keys on `__name__`, which `tool()` copies from the function;
    # `__qualname__` is set too so the tool's repr names what it is registered as.
    fn.__name__ = name
    fn.__qualname__ = name


def _build(wf: Workflow, *, suffix: str) -> Compiled:
    holder: dict[str, Compiled] = {}

    async def agent_phase(phase: str, loop: int, pool: ContextPool, memory: ContextQueue):
        deps = current_run.get()
        p = deps.workflow.phase(phase)
        table = context.binding_table(pool, memory, phase)
        rendered = prompt.render_prompt(p, table)
        result = await bridge.call_agent(deps.agent_runner, p, table, rendered)
        yield ContextItem(id=phase, description=f"{phase} result", content=context.encode(result))
        nxt = holder["compiled"].after(phase, loop)
        if nxt is not None:
            yield nxt

    async def step_phase(phase: str, loop: int, pool: ContextPool):
        deps = current_run.get()
        p = deps.workflow.phase(phase)
        # A step never reads feedback, so it gets an empty memory window.
        table = context.binding_table(pool, ContextQueue(limit=1), phase)
        outcome = await bridge.call_step(
            old_engine.run_one_step,
            {
                "phase": p,
                "table": table,
                "store": deps.store,
                "story_id": deps.story_id,
                "subtask": deps.subtask,
                "clock": deps.clock,
            },
        )
        deps.warnings.extend(outcome.warnings)
        if not outcome.ok:
            if not p.best_effort:
                raise Escalated(phase, outcome.detail)
            deps.warnings.append(f"best-effort phase {phase!r} failed: {outcome.detail}")
        else:
            yield ContextItem(
                id=phase, description=f"{phase} result", content=context.encode(outcome.result)
            )
            if outcome.skip_to is not None:
                names = holder["compiled"].workflow.phase_names
                deps.skipped.extend(names[names.index(phase) + 1 : names.index(outcome.skip_to)])
                yield holder["compiled"].turn_for(outcome.skip_to, loop)
                return
        nxt = holder["compiled"].after(phase, loop)
        if nxt is not None:
            yield nxt

    _rename(agent_phase, f"agent_phase_{wf.name}_{suffix}")
    _rename(step_phase, f"step_phase_{wf.name}_{suffix}")
    compiled = Compiled(wf, tool(agent_phase), tool(step_phase))
    holder["compiled"] = compiled
    return compiled
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime -v`
Expected: PASS: every test in `test_compile.py`, `test_bridge.py` and `test_context.py` (including `test_only_runtime_imports_pygents`).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runtime/state.py src/agent_manager/runtime/compile.py tests/runtime/conftest.py tests/runtime/test_compile.py
git commit -F - <<'EOF'
feat(runtime): compile a workflow into agent_phase and step_phase tools

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
```

---

### Task 6: Agent-phase failures escalate, or loop back through `Goto`

**Files:**
- Modify: `src/agent_manager/runtime/compile.py` (imports; the `agent_phase` body inside `_build`)
- Test: `tests/runtime/test_compile.py` (append; extend imports)

**Interfaces:**
- Consumes: `errors.AgentPhaseFailed(phase, *, outcome, detail)` with `.detail`; `phases.Goto(phase, max_loops=1)`; `old_engine._render_error(error) -> str` (`"{Type}: {message}"`, `src/agent_manager/engine.py`).
- Produces: `agent_phase` raises `Escalated(phase, detail)` on a terminal failure; with `on_fail` and loops left it yields a queue item `{"for": on_fail.phase, "from": phase, "detail": detail}` and `turn_for(on_fail.phase, loop + 1)`. The feedback resolver and the loop's behavioural tests stay with b904b9e7.

- [ ] **Step 1: Write the failing tests**

In `tests/runtime/test_compile.py`, add to the imports:

```python
from agent_manager.errors import AgentPhaseFailed, EngineError
```

and change the phases import to:

```python
from agent_manager.workflow.phases import AgentPhase, Goto, Step, Workflow
```

Append:

```python
async def test_agent_phase_failure_escalates(store):
    def runner(phase, table, rendered):
        raise AgentPhaseFailed("review", outcome="gate_failed", detail="blocked by critic")

    wf = Workflow("t", (AgentPhase("review", "critic", (), None), Step("b", _noop)))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert info.value.phase == "review"
    assert info.value.detail == "blocked by critic"


async def test_an_unexpected_runner_error_escalates_with_the_rendered_error(store):
    # Review Focus 5: the old engine escalates any runner exception as
    # "{Type}: {message}"; 3.4's parity run needs the same here.
    def runner(phase, table, rendered):
        raise EngineError("no worktree", phase="review")

    wf = Workflow("t", (AgentPhase("review", "critic", (), None),))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert info.value.phase == "review"
    assert info.value.detail == "EngineError: phase 'review': no worktree"


async def test_on_fail_loops_back_with_feedback_then_escalates_when_loops_run_out(store):
    # Mechanics only: the feedback prompt resolver and the full loop behaviour
    # belong to b904b9e7.
    calls: list[Any] = []

    def runner(phase, table, rendered):
        calls.append((phase.name, [f["detail"] for f in table["feedback"]]))
        if phase.name == "review":
            raise AgentPhaseFailed("review", outcome="gate_failed", detail=f"blocker {len(calls)}")
        return {"path": "docs/s.md"}

    wf = Workflow("t", (
        AgentPhase("spec", "writer", (), None),
        AgentPhase("review", "critic", (), None, on_fail=Goto("spec", 1)),
    ))

    with pytest.raises(C.Escalated) as info:
        await _drive(wf, _deps(wf, store, runner))

    assert calls == [
        ("spec", []),
        ("review", []),
        ("spec", ["blocker 2"]),
        ("review", []),
    ]
    assert info.value.phase == "review"
    assert info.value.detail == "blocker 4"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_compile.py -k "agent_phase_failure or unexpected_runner or on_fail" -v`
Expected: FAIL. `test_agent_phase_failure_escalates` and the `on_fail` test fail with `AgentPhaseFailed: phase 'review' ended gate_failed: ...` escaping instead of `Escalated`; the unexpected-error test fails with `EngineError` escaping instead of `Escalated`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/runtime/compile.py`, add to the imports (after `from agent_manager import prompt`):

```python
from agent_manager.errors import AgentPhaseFailed
```

Replace the `agent_phase` function inside `_build` with:

```python
    async def agent_phase(phase: str, loop: int, pool: ContextPool, memory: ContextQueue):
        deps = current_run.get()
        p = deps.workflow.phase(phase)
        table = context.binding_table(pool, memory, phase)
        # Outside the try, as in the old engine: an input no resolver provides
        # is a workflow bug and its `EngineError` must reach the caller as is.
        rendered = prompt.render_prompt(p, table)
        try:
            result = await bridge.call_agent(deps.agent_runner, p, table, rendered)
        except AgentPhaseFailed as failure:
            if p.on_fail is not None and loop < p.on_fail.max_loops:
                yield ContextItem(
                    content={"for": p.on_fail.phase, "from": phase, "detail": failure.detail}
                )
                yield holder["compiled"].turn_for(p.on_fail.phase, loop + 1)
                return
            raise Escalated(phase, failure.detail) from failure
        except Exception as error:
            # Total, as the old engine's agent branch is: an exception escaping
            # the walk would leave the subtask recorded `started` forever.
            raise Escalated(phase, old_engine._render_error(error)) from error
        yield ContextItem(id=phase, description=f"{phase} result", content=context.encode(result))
        nxt = holder["compiled"].after(phase, loop)
        if nxt is not None:
            yield nxt
```

(`asyncio.CancelledError` is a `BaseException`, so a cancelled turn is not turned into an escalation; the bridge has already killed its processes.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime -v`
Expected: PASS (all compile, bridge and context tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runtime/compile.py tests/runtime/test_compile.py
git commit -F - <<'EOF'
feat(runtime): escalate agent-phase failures or loop back through Goto

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WcLCFHbyPbpCmG8mdjqd2K
EOF
```

---

### Task 7: Full verification

**Files:** none modified.

**Interfaces:**
- Consumes: everything above.
- Produces: a green default suite on this branch.

- [ ] **Step 1: Run the whole default suite**

Run: `uv run pytest`
Expected: PASS. The paid `e2e` test stays deselected by `addopts = -m "not e2e"`; every other test, including `tests/e2e` collection, `tests/test_engine.py` (unchanged tests plus the four `run_one_step` additions), `tests/test_dispatch.py`, `tests/harness/test_launcher.py` and `tests/runtime/`, passes.

- [ ] **Step 2: Confirm the scope boundaries held**

Run: `git diff --stat m6/task-let-dispatch-and-prompt-1bbb532d...HEAD`
Expected: only these paths appear: `src/agent_manager/harness/launcher.py`, `src/agent_manager/dispatch.py`, `src/agent_manager/engine.py`, `src/agent_manager/runtime/bridge.py`, `src/agent_manager/runtime/state.py`, `src/agent_manager/runtime/compile.py`, `tests/harness/test_launcher.py`, `tests/test_dispatch.py`, `tests/test_engine.py`, `tests/runtime/conftest.py`, `tests/runtime/test_bridge.py`, `tests/runtime/test_compile.py`, and this plan. No `runtime/engine.py`, `runtime/context.py` or `prompt.py`. If anything else appears, revert it before finishing.

---

## Self-Review

- Spec coverage: scope item 1 is Task 1; item 2 (`RunDeps`, `current_run`) is Task 5; item 3 (bridge plus the `AgentRunner` edit) is Tasks 2 and 3; item 4 (`run_one_step`, optional `workflow`, callable-or-name resolution, labels) is Task 4; item 5 (`Escalated`, `Compiled`, cache and lock, digest-suffixed names, `step_phase`, `agent_phase` with `Goto`) is Tasks 5 and 6. Every spec test is present under its spec name: the conftest, the six `test_compile.py` tests, `test_cancelling_a_call_kills_its_process`, and the three launcher additions. Error paths: step raise, non-mapping and gate verdict (Tasks 4 and 5), gate `warn` (Task 5), `AgentPhaseFailed` with and without loops (Task 6), per-call cancellation (Task 2), unset `current_run` (left to propagate as `LookupError`, as the spec says, with no code needed).
- Deliberate additions beyond the spec, each with a test: the kill-on-spawn-after-cancel path and the per-call isolation test (Task 2); `run_direct` killing the child when `on_spawn` raises (Task 1); tools reading the phase from `deps.workflow` (Task 5); escalating non-`AgentPhaseFailed` runner errors (Task 6); direct `run_one_step` tests appended to `tests/test_engine.py` without editing any existing test (Task 4).
- Placeholder scan: every code step has complete code; no TBD, no "similar to".
- Type consistency: `run_one_step(*, phase, table, store, story_id, subtask, clock, workflow=None)` matches between Task 4 and its call in Task 5; `bridge.call_agent(runner, phase, context, rendered)` and `bridge.call_step(fn, kwargs)` match between Tasks 2, 3 and 5; `Escalated(phase, detail)`, `Compiled.turn_for/first_turn/after`, `STEP_TIMEOUT` and `RunDeps` fields are used as defined; `holder["compiled"]` is the one key used in both tool bodies.
