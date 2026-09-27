<!-- task-pipeline: validated -->
# Raise the pygents floor and free agent names with unregister (card 59fd7f22)

Parent story: dd4a87d5 "Adopt pygents 0.7.0" (milestone b75742dd). This subtask narrows decisions A1 and A2 of `docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md` (plan Task 1.1 in `docs/superpowers/plans/2026-09-25-pygents-070-adoption.md`). Nothing new is designed here.

## Scope

- **A1: the pygents floor.** `pyproject.toml` declares `"pygents>=0.6.7"`. Raise it to `"pygents>=0.7.0"` with `uv add "pygents>=0.7.0"` so that `pyproject.toml` and `uv.lock` both change. `uv.lock` already resolves pygents 0.7.0, so the only stale part is the declared floor. The resolved version must not change.
- **A2: public unregister.** In `src/agent_manager/runtime/engine.py`, change the body of `_forget(name)` from `AgentRegistry._registry.pop(name, None)` to `AgentRegistry.unregister(name)` wrapped in `contextlib.suppress(UnregisteredAgentError)`. `UnregisteredAgentError` is imported from `pygents.errors`. Suppressing the error is required because the name can legitimately be missing. One example is resuming a checkpoint whose agent was never registered in this process. Rewrite the docstring so it no longer describes the pre-0.7 workaround.
- Keep both call sites exactly as they are. The first, before `Agent.from_dict` on resume, clears a stale name that a run which died before its `finally` may have left behind. The second, in the `finally` after `_run`, frees the agent's own name for reuse.
- **`runtime/compile.py`: no change.** The card listed it only in case it drops tools from the registry. It does not. Nothing in it pops or deletes from `ToolRegistry`, and `clear_cache()` clears only the module's own `_CACHE`. Following the plan's Global Constraint, this subtask records that the workaround does not exist there and leaves the file untouched.
- **Test consistency.** Two assertions in `tests/runtime/test_resume.py`, at roughly lines 406 and 431, read `AgentRegistry._registry` directly (`... not in AgentRegistry._registry`). Change them to the public idiom `with pytest.raises(UnregisteredAgentError): AgentRegistry.get(name)`. Leave `test_resuming_twice_in_one_process` unchanged, and it must still pass. Tests that call a registry's public `.clear()` between cases stay as they are, as A2 allows (for example `tests/runtime/conftest.py`).

## Observable behavior

- After a run finishes, by any exit path that reaches the `finally`, its agent name is free, so a new `Agent` with the same name can be constructed or restored in the same process.
- Resuming a checkpoint whose agent name is not registered does not raise. Neither `_forget` call ever raises `UnregisteredAgentError`.
- Nothing under `src/agent_manager` touches private state on `AgentRegistry`, `ToolRegistry` or `HookRegistry`.

## Error paths

- `_forget` on an absent name: `UnregisteredAgentError`, a `KeyError` subclass, is suppressed. No other exception type is suppressed.

## Tests

All three new tests go in the new file `tests/runtime/test_engine_registry.py`. Under the placement rule in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14, these are **engine-tier tests: engine driven with a fake adapter or fake steps**, and the repo keeps that tier in `tests/runtime/`, mirroring `src/agent_manager/runtime/`. They do not belong in `tests/e2e/`, which is kept for the single real-harness opt-in test, or in top-level `tests/test_*.py`.

1. `test_a_finished_run_frees_its_agent_name` (engine tier, `tests/runtime/`): run a subtask to completion with fake steps. Afterwards `AgentRegistry.get(name)` raises `UnregisteredAgentError`, and a second run reusing the name succeeds.
2. `test_cleanup_of_an_agent_that_was_never_registered_does_not_raise` (engine tier, `tests/runtime/`; Review Focus 1): `_forget` on a name that is not registered returns without error. This covers the resume-from-checkpoint case.
3. `test_no_private_registry_access_in_src` (engine tier, `tests/runtime/`, a static guard over `src/`): the regex `(AgentRegistry|ToolRegistry|HookRegistry)\._` matches nothing under `src/agent_manager`.

Edited tests in `tests/runtime/test_resume.py` (engine tier, `tests/runtime/`):

- The two `._registry` membership assertions switch to the `pytest.raises(UnregisteredAgentError)` / `AgentRegistry.get` idiom.
- `test_resuming_twice_in_one_process` must keep passing unmodified.

## Out of scope

- The run-loop cancellation and early-exit guards, `tests/runtime/test_cancellation.py`, and the no-instance-hooks test (A3/A5). These belong to sibling 0822234f.
- Edits to the spec and addendum docs and to `CLAUDE.md` (A4). These belong to sibling 4f31e025.
- The M6 process-tree kill in `runtime/bridge.py`, the global `@hook(..., tags={"subtask"})` hooks in `runtime/checkpoint.py`, exactly-once phases, live control, several `am` processes, and any change to pygents itself.

## Verification

- Full suite: `uv run pytest`
- Typecheck: none
- Lint: none

---

# Raise the pygents floor and free agent names with unregister Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare `pygents>=0.7.0` and replace the engine's private-dict agent-name cleanup with pygents' public `AgentRegistry.unregister`, guarded by tests.

**Architecture:** Two independent changes. A1 is a dependency-floor bump done through `uv add`, which rewrites `pyproject.toml` and the `specifier` line in `uv.lock` without changing the resolved 0.7.0. A2 changes only the body and docstring of `_forget` in `src/agent_manager/runtime/engine.py`. Its two call sites (engine.py:165 before `Agent.from_dict`, engine.py:182 in the `finally`) are not touched. New engine-tier tests in `tests/runtime/test_engine_registry.py` pin the lifecycle and add a static guard against private registry access in `src/`.

**Tech Stack:** Python 3.12, `uv`, pytest (with `asyncio_mode = "auto"`, `--import-mode=importlib`), pygents 0.7.0 (`AgentRegistry.unregister`, `pygents.errors.UnregisteredAgentError`, a `KeyError` subclass).

**Spec:** `docs/superpowers/specs/task-raise-the-pygents-floor-59fd7f22-design.md` (reproduced verbatim above).

## Global Constraints

- The pygents floor is `"pygents>=0.7.0"`, set with `uv add "pygents>=0.7.0"`. The resolved pygents version in `uv.lock` stays `0.7.0`.
- `_forget` uses `AgentRegistry.unregister(name)` inside `contextlib.suppress(UnregisteredAgentError)`, with `UnregisteredAgentError` imported from `pygents.errors`. No other exception type is suppressed.
- Both `_forget` call sites in `run_subtask_async` stay exactly where and as they are.
- Nothing under `src/agent_manager` matches the regex `(AgentRegistry|ToolRegistry|HookRegistry)\._`. This includes docstrings and comments, since the guard test is a plain text scan.
- `src/agent_manager/runtime/compile.py` is not changed: it never removes tools from `ToolRegistry`, so the workaround does not exist there.
- `test_resuming_twice_in_one_process` in `tests/runtime/test_resume.py` is not edited and must still pass.
- Public `.clear()` calls on registries in tests stay (for example `tests/runtime/conftest.py`).
- Out of bounds on this branch: cancellation/early-exit guards and `tests/runtime/test_cancellation.py` (sibling 0822234f), spec/addendum docs and `CLAUDE.md` (sibling 4f31e025), `runtime/bridge.py`, the global hooks in `runtime/checkpoint.py`, and pygents itself.
- New tests live in `tests/runtime/` (engine tier, fake steps), never `tests/e2e/` or top-level `tests/test_*.py`.
- Verification is `uv run pytest`. There is no typecheck or lint command.

## Review Focus

1. `_forget` on a name that was never registered (a resume of a checkpoint from another process): it must return quietly, since the resume path calls it before `from_dict`. Pinned by `test_cleanup_of_an_agent_that_was_never_registered_does_not_raise` in Task 2.
2. A non-`UnregisteredAgentError` failure out of `unregister`, including a bare `KeyError`: it must propagate. An over-broad `suppress(KeyError)` or `suppress(Exception)` would silently hide a real registry fault. Pinned by `test_forget_lets_other_errors_through` in Task 2.
3. A run that ends escalated rather than done: the name must still be freed, because the `finally` runs on every exit and a relaunch of an escalated card in the same process is the normal next step. Pinned by `test_an_escalated_run_frees_its_agent_name` in Task 2.
4. The rewritten `_forget` docstring or a comment mentioning `AgentRegistry._registry`: the static guard scans text, so describing the old workaround in prose would fail the suite. Pinned by `test_no_private_registry_access_in_src` in Task 2. The docstring in Step 5 is written to avoid it.
5. The guard test scanning an empty or wrong directory (a path mistake makes it pass vacuously): it must prove it saw real source files. Pinned by the `assert sources` line in `test_no_private_registry_access_in_src` in Task 2.

---

### Task 1: Raise the pygents floor (A1)

**Files:**
- Modify: `pyproject.toml:10` (via `uv add`)
- Modify: `uv.lock:26` (via `uv add`)

**Interfaces:**
- Consumes: nothing.
- Produces: declared dependency `pygents>=0.7.0`. Task 2 depends on the 0.7.0 API (`AgentRegistry.unregister`), which the lock already resolves.

This task has no new test: it changes packaging metadata only, and the resolved version is unchanged. Its check is that the two files show the new floor, the lock still resolves 0.7.0, and the suite still passes.

- [ ] **Step 1: Confirm the current state**

Run: `grep -n 'pygents' pyproject.toml uv.lock`
Expected: `pyproject.toml:10:    "pygents>=0.6.7",`, `uv.lock:26:    { name = "pygents", specifier = ">=0.6.7" },`, and `uv.lock:211:name = "pygents"` followed by `version = "0.7.0"` on line 212.

- [ ] **Step 2: Raise the floor with uv**

Run: `uv add "pygents>=0.7.0"`
Expected: exits 0. `pyproject.toml` line 10 now reads:

```toml
    "pygents>=0.7.0",
```

and `uv.lock` line 26 now reads:

```toml
    { name = "pygents", specifier = ">=0.7.0" },
```

- [ ] **Step 3: Confirm the resolved version did not move**

Run: `grep -n -A1 '^name = "pygents"' uv.lock`
Expected:

```
211:name = "pygents"
212-version = "0.7.0"
```

Run: `git diff --stat`
Expected: only `pyproject.toml` and `uv.lock` changed, one line each (no other package versions touched in `uv.lock`). If `uv.lock` shows any other version change, run `git checkout uv.lock pyproject.toml` and stop: that is outside this card's scope.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS (same count as before the change).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: raise the pygents floor to 0.7.0 (A1)"
```

---

### Task 2: Free agent names with the public unregister (A2)

**Files:**
- Create: `tests/runtime/test_engine_registry.py`
- Modify: `src/agent_manager/runtime/engine.py:10-17` (imports)
- Modify: `src/agent_manager/runtime/engine.py:253-262` (`_forget`)
- Not modified: `src/agent_manager/runtime/compile.py` (no tool removal exists there, so nothing to replace)

**Interfaces:**
- Consumes: `pygents.AgentRegistry.unregister(name: str) -> None` (raises `UnregisteredAgentError` when absent), `pygents.AgentRegistry.get(name: str)` (raises `UnregisteredAgentError` when absent), `pygents.errors.UnregisteredAgentError(KeyError)`. Existing `agent_manager.runtime.engine.run_subtask(workflow, store, *, story_id, subtask, repo_dir, clock=..., ...) -> walk.SubtaskSummary`, whose agent is named `f"{store.run_id}:{subtask.card_id}"`. Existing `agent_manager.store.Store.open(repo_dir, run_id)`, `agent_manager.models.SubtaskRun`, `agent_manager.workflow.phases.Step(name, fn)` / `Workflow(name, phases)`. The autouse `fresh_pygents` fixture in `tests/runtime/conftest.py` clears the registries around each test.
- Produces: `agent_manager.runtime.engine._forget(name: str) -> None` with the same signature as today: removes `name` from `AgentRegistry` and returns quietly if it is absent. Only `UnregisteredAgentError` is suppressed.

- [ ] **Step 1: Write the tests**

Create `tests/runtime/test_engine_registry.py`:

```python
"""Freeing agent names with pygents' public `AgentRegistry.unregister` (card 59fd7f22, decision A2).

Engine tier (agent-manager design §14: the engine driven with fake steps):
`runtime.engine.run_subtask` runs plain-function steps over a real temp store,
built as tests/runtime/test_resume.py builds it. Registry isolation between
tests is the autouse `fresh_pygents` fixture in tests/runtime/conftest.py.
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pygents import Agent, AgentRegistry
from pygents.errors import UnregisteredAgentError

from agent_manager import models, store as store_module
from agent_manager.runtime import engine as runtime_engine
from agent_manager.workflow.phases import Step, Workflow

RUN_ID = "run-2026-09-27-01"
STORY_ID = "dd4a87d5"
CARD_ID = "59fd7f22"
REPO = Path("/repo")
FIXED = datetime(2026, 9, 27, tzinfo=timezone.utc)
AGENT_NAME = f"{RUN_ID}:{CARD_ID}"
SRC = Path(__file__).resolve().parents[2] / "src" / "agent_manager"
PRIVATE_REGISTRY_ACCESS = re.compile(r"(AgentRegistry|ToolRegistry|HookRegistry)\._")


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
        branch=f"m8/task-raise-the-pygents-floor-{CARD_ID}",
        base_branch="m8/story-base",
        status="started",
        worktree_path=Path("/w"),
    )


def _go(workflow: Workflow, opened):
    return runtime_engine.run_subtask(
        workflow,
        opened,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        clock=lambda: FIXED,
    )


def _one(card: str) -> dict[str, Any]:
    return {"one": "ONE"}


def _boom(card: str) -> dict[str, Any]:
    raise RuntimeError("boom")


def _assert_free(name: str) -> None:
    with pytest.raises(UnregisteredAgentError):
        AgentRegistry.get(name)


def test_a_finished_run_frees_its_agent_name(store):
    wf = Workflow("single", (Step("one", _one),))

    first = _go(wf, store)

    assert first.status == "done"
    _assert_free(AGENT_NAME)
    second = _go(wf, store)
    assert second.status == "done"
    assert second.results == {"one": {"one": "ONE"}}
    _assert_free(AGENT_NAME)


def test_an_escalated_run_frees_its_agent_name(store):
    # Review Focus 3: the `finally` frees the name on an escalation too.
    summary = _go(Workflow("escalates", (Step("boom", _boom),)), store)

    assert summary.status == "escalated"
    _assert_free(AGENT_NAME)
    Agent(AGENT_NAME, "a relaunch of the same card", [])


def test_cleanup_of_an_agent_that_was_never_registered_does_not_raise():
    # Review Focus 1: the resume path forgets a checkpointed name that this
    # process never registered.
    _assert_free("never-registered")

    runtime_engine._forget("never-registered")

    _assert_free("never-registered")


def test_forget_frees_a_registered_name():
    Agent("left-behind", "a stale registration", [])

    runtime_engine._forget("left-behind")

    _assert_free("left-behind")
    Agent("left-behind", "the name is reusable", [])


def test_forget_lets_other_errors_through(monkeypatch):
    # Review Focus 2: only `UnregisteredAgentError` is suppressed; a bare
    # `KeyError` (its base class) is a real fault and must surface.
    def broken(name: str) -> None:
        raise KeyError(name)

    monkeypatch.setattr(AgentRegistry, "unregister", broken)

    with pytest.raises(KeyError) as caught:
        runtime_engine._forget("anything")

    assert type(caught.value) is KeyError


def test_no_private_registry_access_in_src():
    sources = sorted(SRC.rglob("*.py"))
    # Review Focus 5: a wrong path must not make this pass vacuously.
    assert sources, f"no Python sources found under {SRC}"

    hits = [
        f"{path.relative_to(SRC.parent)}:{number}: {line.strip()}"
        for path in sources
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if PRIVATE_REGISTRY_ACCESS.search(line)
    ]

    assert hits == []
```

- [ ] **Step 2: Run the tests and watch the right ones fail**

Run: `uv run pytest tests/runtime/test_engine_registry.py -v`
Expected:
- `test_no_private_registry_access_in_src` FAILS. The assertion shows `['agent_manager/runtime/engine.py:262: AgentRegistry._registry.pop(name, None)']` (plus a docstring line if the old docstring matches; it does not today).
- `test_forget_lets_other_errors_through` FAILS with `Failed: DID NOT RAISE <class 'KeyError'>`, because today's `_forget` never calls `unregister`.
- `test_a_finished_run_frees_its_agent_name`, `test_an_escalated_run_frees_its_agent_name`, `test_cleanup_of_an_agent_that_was_never_registered_does_not_raise` and `test_forget_frees_a_registered_name` PASS. They pin behaviour the old pop already had, which must survive the switch to `unregister`.

If either of the two expected failures passes, or any of the four pins fails, stop and investigate before changing `engine.py`.

- [ ] **Step 3: Add the imports to engine.py**

In `src/agent_manager/runtime/engine.py`, replace:

```python
import asyncio
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pygents import Agent, AgentRegistry, ContextPool, ContextQueue
```

with:

```python
import asyncio
import contextlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pygents import Agent, AgentRegistry, ContextPool, ContextQueue
from pygents.errors import UnregisteredAgentError
```

- [ ] **Step 4: Confirm the call sites are untouched**

Run: `grep -n '_forget(' src/agent_manager/runtime/engine.py`
Expected, exactly three lines (two calls and the definition):

```
167:        _forget(resume_from.agent["name"])
184:        _forget(agent.name)
255:def _forget(name: str) -> None:
```

(Each line is two further down than in the original file (165, 182, 253) because Step 3 added two import lines.) Do not edit the resume call site or the `finally` call site.

- [ ] **Step 5: Rewrite `_forget`**

In `src/agent_manager/runtime/engine.py`, replace:

```python
def _forget(name: str) -> None:
    """Free `name` in pygents' process-wide `AgentRegistry` for the next run.

    The installed pygents 0.7.0 has `AgentRegistry.unregister`, but switching
    to it is decision A2 of
    docs/superpowers/specs/2026-09-25-pygents-070-adoption-design.md, a card of
    its own. Until then this is the pre-0.7.0 workaround the milestone spec
    (§11) describes: pop the registry's dict, tolerating a name already gone.
    """
    AgentRegistry._registry.pop(name, None)
```

with:

```python
def _forget(name: str) -> None:
    """Free `name` in pygents' process-wide `AgentRegistry` so it can be reused.

    The name may legitimately be absent: a resumed checkpoint's agent was
    never registered in this process, or a run's own name is already gone.
    Only `UnregisteredAgentError` is tolerated; any other error is a real
    fault and propagates.
    """
    with contextlib.suppress(UnregisteredAgentError):
        AgentRegistry.unregister(name)
```

- [ ] **Step 6: Run the new tests and watch them pass**

Run: `uv run pytest tests/runtime/test_engine_registry.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 7: Run the resume tests that exercise both call sites**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: all PASS, including `test_resuming_twice_in_one_process`, `test_a_stale_registry_entry_does_not_block_a_resume` (call site 1 removing a real stale entry) and `test_a_resume_in_a_fresh_process_uses_the_rebuilt_workflow` (call site 1 on a name that is absent).

- [ ] **Step 8: Confirm compile.py needs nothing**

Run: `grep -nE 'ToolRegistry|\.pop\(|del ' src/agent_manager/runtime/compile.py`
Expected: exactly three hits (lines 8, 80 and 96), all prose in a docstring or comment that mentions `ToolRegistry`. No line removes an entry from `ToolRegistry`. Do not edit `compile.py`. If a `ToolRegistry` removal does appear, stop and report it: the spec says there is none.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/runtime/engine.py tests/runtime/test_engine_registry.py
git commit -m "fix: free agent names with AgentRegistry.unregister (A2)"
```

---

### Task 3: Move test_resume.py off the private registry dict

**Files:**
- Modify: `tests/runtime/test_resume.py:22-23` (imports)
- Modify: `tests/runtime/test_resume.py:406` (`test_a_refused_resume_leaves_the_card_runnable`)
- Modify: `tests/runtime/test_resume.py:431` (`test_a_stale_registry_entry_does_not_block_a_resume`)

**Interfaces:**
- Consumes: `pygents.AgentRegistry.get(name)` raising `pygents.errors.UnregisteredAgentError` when the name is absent.
- Produces: nothing new. `test_resuming_twice_in_one_process` (line 286) is not touched.

This is a test-only refactor: both assertions pass before and after, because they check the same fact through the public API. There is no RED step. The check is that the file still passes and no longer reads `AgentRegistry._registry`.

- [ ] **Step 1: Confirm the two private reads**

Run: `grep -n 'AgentRegistry\._' tests/runtime/test_resume.py`
Expected, exactly:

```
406:    assert parked.agent["name"] not in AgentRegistry._registry
431:    assert crashed.agent["name"] not in AgentRegistry._registry
```

- [ ] **Step 2: Add the import**

In `tests/runtime/test_resume.py`, replace:

```python
import pytest
from pygents import Agent, AgentRegistry, ToolRegistry
```

with:

```python
import pytest
from pygents import Agent, AgentRegistry, ToolRegistry
from pygents.errors import UnregisteredAgentError
```

- [ ] **Step 3: Replace the read in `test_a_refused_resume_leaves_the_card_runnable`**

Replace:

```python
    assert parked.agent["name"] not in AgentRegistry._registry
    ran.clear()
    summary = _go(changed, store)
```

with:

```python
    with pytest.raises(UnregisteredAgentError):
        AgentRegistry.get(parked.agent["name"])
    ran.clear()
    summary = _go(changed, store)
```

- [ ] **Step 4: Replace the read in `test_a_stale_registry_entry_does_not_block_a_resume`**

Replace:

```python
    assert summary.results == ALL_RESULTS
    assert crashed.agent["name"] not in AgentRegistry._registry
```

with:

```python
    assert summary.results == ALL_RESULTS
    with pytest.raises(UnregisteredAgentError):
        AgentRegistry.get(crashed.agent["name"])
```

(`assert summary.results == ALL_RESULTS` appears several times in the file; this `old_string` is unique only because it includes the `crashed.agent["name"]` line that follows it.)

- [ ] **Step 5: Confirm no private read remains**

Run: `grep -n 'AgentRegistry\._' tests/runtime/test_resume.py`
Expected: no output.

Run: `git diff tests/runtime/test_resume.py`
Expected: the diff touches only the import block and the two assertions. No line inside `test_resuming_twice_in_one_process` changes.

- [ ] **Step 6: Run the resume tests**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: all PASS, including `test_resuming_twice_in_one_process`, `test_a_refused_resume_leaves_the_card_runnable` and `test_a_stale_registry_entry_does_not_block_a_resume`.

- [ ] **Step 7: Commit**

```bash
git add tests/runtime/test_resume.py
git commit -m "test: read agent registration through AgentRegistry.get in resume tests"
```

---

### Task 4: Full verification

**Files:** none changed.

**Interfaces:**
- Consumes: the results of Tasks 1-3.
- Produces: a verified branch.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with no failures and no errors. The count is the pre-card count plus the 6 new tests in `tests/runtime/test_engine_registry.py`.

- [ ] **Step 2: Check the scope of the branch diff**

Run: `git diff --stat master...HEAD`
Expected: exactly these files: `pyproject.toml`, `uv.lock`, `src/agent_manager/runtime/engine.py`, `tests/runtime/test_engine_registry.py`, `tests/runtime/test_resume.py`, plus the spec and plan under `docs/superpowers/` if they are committed on this branch. None of `src/agent_manager/runtime/compile.py`, `runtime/bridge.py`, `runtime/checkpoint.py`, `CLAUDE.md`, `tests/runtime/test_cancellation.py` or the addendum specs appear.
