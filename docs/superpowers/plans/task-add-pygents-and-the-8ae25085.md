<!-- task-pipeline: validated -->
# Task 3.1 — Add pygents and the pool codec (card 8ae25085)

Narrows `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 3.1 (lines 614-732) and `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` §3/§5/§9 to this one subtask. Parent story: f9c19dc3 "Run a workflow on pygents".

## Scope

In scope:

- `pyproject.toml`: add `pygents>=0.6.7` as a runtime dependency (the only new runtime dependency of the milestone), add `pytest-asyncio` to the `dev` dependency group, and add `asyncio_mode = "auto"` under `[tool.pytest.ini_options]`. The repo has no existing async-test convention, so this is the convention. Existing `addopts` (`--import-mode=importlib -m "not e2e"`), `testpaths` and `markers` stay unchanged.
- New package `src/agent_manager/runtime/` with an empty `__init__.py` and `context.py`.
- New test package `tests/runtime/` with `__init__.py` and `test_context.py`.

Out of scope (sibling cards own these; do not touch): `dispatch.py` and `prompt.py` (1bbb532d); `runtime/state.py`, `runtime/bridge.py`, `runtime/compile.py`, launcher `on_spawn`/`kill_tree`, and `engine.run_one_step` (023d918e); `runtime/engine.run_subtask` and the engine-parametrised `tests/test_engine.py` (2853e536); Goto loops and rendering of `feedback` in prompts (b904b9e7). `workflow/phases.py`, `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads are unchanged.

## Observable behaviour of `agent_manager.runtime.context`

The module docstring states the invariant: the pool holds JSON only, and this module is the single codec between the pool and typed Python.

- `SUBTASK = "subtask"` and `SKIPPED = "skipped"` are module constants (reserved pool item ids).
- `encode(value: Any) -> Any` returns a JSON-serialisable value. It checks in this order:
  1. pydantic `BaseModel`: `{"$model": "<module>:<qualname>", "data": value.model_dump(mode="json")}`. `by_alias` is not used, so field names are kept (for example `Verification.full_suite`, not `fullSuite`). Gates that need aliases still get them from `dispatch.gate_values`.
  2. `PurePath`: `{"$path": str(value)}`.
  3. `datetime`: `{"$dt": value.isoformat()}`.
  4. `Mapping`: a dict with `str` keys and encoded values.
  5. `list`/`tuple`: a list of encoded values.
  6. Anything else passes through unchanged.
- `decode(value: Any) -> Any` is the inverse. For a dict, it checks the tags in this order: `$model` imports the module with `importlib` and walks the dotted qualname with `getattr`, then calls `model_validate(data)`. `$path` becomes `Path`. `$dt` becomes `datetime.fromisoformat`. Any other dict is decoded key by key, lists element by element, and scalars pass through. Tuples come back as lists. That is accepted: the round-trip guarantee covers dicts, lists, scalars, paths, datetimes and models.
- `seed_item(binding: Mapping[str, Any]) -> ContextItem` returns `ContextItem(id=SUBTASK, description="fixed subtask context", content=encode(dict(binding)))`.
- `binding_table(pool: ContextPool, memory: ContextQueue, phase: str) -> dict[str, Any]` is synchronous. It builds the table in three steps:
  1. Start with the decoded seed content.
  2. For every pool item whose id is not `SUBTASK` or `SKIPPED`, set `table[item.id]` to the decoded content.
  3. Set `table["feedback"]` to a list of `dict` copies of the queue items whose content is a `Mapping` with `content["for"] == phase`, in queue order. The list is empty when nothing matches.

Within `src/agent_manager/`, only `src/agent_manager/runtime/` imports `pygents`; this card adds no pygents import to any other production module. `tests/runtime/test_context.py` does import `pygents` directly (`ContextPool`, `ContextQueue`, `ContextItem`) to exercise the codec against real containers, per the Tests section below.

## Error paths

No new error handling is added. Failures propagate as the underlying libraries raise them:

- A `$model` tag whose module or attribute cannot be resolved raises `ImportError` or `AttributeError`.
- Model data that no longer validates raises pydantic `ValidationError`.
- A malformed `$dt` raises `ValueError`.
- A pool with no `SUBTASK` seed fails with whatever `pool.get` raises.

Values that are not JSON-safe and not covered by a tag pass through `encode` unchanged. `json.dumps` rejects them at checkpoint time, which is the intended loud failure.

## Tests

Placement rule: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 places tests by subject, mirroring the source tree. Pure functions get unit tests beside their logic, and only the single real-harness test is `e2e` (opt-in). Pygents-engine spec §9 adds that no test calls a model. `context.py` is pure codec and data-shaping code over in-memory pygents containers, so both tests belong in the **default-suite unit tier** in `tests/runtime/test_context.py`: no git repo, no board, no fake adapter, no `e2e` marker.

1. `test_round_trip_through_json` (unit, default suite). Encodes a dict holding a `Path`, a timezone-aware `datetime` and an `ExploreResult` with a nested `Verification` (`src/agent_manager/results.py:39-53`), passes it through `json.dumps`/`json.loads`, decodes it, and asserts equality with the original. This proves plan Review Focus item 3: results holding `Path` or `datetime` survive checkpointing. It also proves that the `fullSuite` serialization alias does not break the round-trip.
2. `test_binding_table_rebuilds_seed_results_and_feedback` (unit, default suite, async via `asyncio_mode = "auto"`). Uses a real `pygents.ContextPool` and `ContextQueue(limit=10)`. It seeds `{"branch", "worktree": Path}`, adds a `spec` result item, and appends two feedback items (`for: spec` and `for: plan`). It asserts that `binding_table(..., "spec")` returns the decoded `Path`, the `spec` result, and only the `spec` feedback item.

The exact test bodies are given verbatim in the plan (lines 633-662).

The pygents-engine spec §9 also describes a test that `binding_table` matches the old engine's table per phase of TASK and INTEGRATE. That needs the compiler and `run_subtask`, so it is deferred to the sibling cards (023d918e and 2853e536) and not added here.

## Verification

`uv run pytest` must be fully green, including the existing yaml-engine suite. This card adds no engine-selectable behaviour.

## Note on inputs

The exploration findings given to this stage were cut off at 8000 characters, partway through the test-placement paragraph. The placement tier above comes from the cited §14 rule and pygents spec §9 directly, not from the missing text.

---

# Add pygents and the pool codec Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `pygents>=0.6.7` to the project and create `agent_manager.runtime.context`, the single JSON codec between the pygents context pool and typed Python (`encode`/`decode`, `SUBTASK`/`SKIPPED`, `seed_item`, `binding_table`).

**Architecture:** A new `src/agent_manager/runtime/` package holds the only production code allowed to import `pygents`. `context.py` tags pydantic models, paths and datetimes into JSON-safe dicts on the way into the pool and resolves them back on the way out; `binding_table` rebuilds the per-phase binding table (seed, phase results, addressed feedback) from a `ContextPool` and a `ContextQueue`. Nothing else in the codebase changes.

**Tech Stack:** Python 3.12+, pydantic v2, pygents (`ContextPool`, `ContextQueue`, `ContextItem` — verified against the pygents 0.7.0 source: `ContextPool.add` and `ContextQueue.append` are `async`, `ContextPool.get(id)` and the `.items` properties are sync, `ContextItem` is a frozen dataclass `ContextItem(content, description=None, id=None)`, and `pygents/__init__.py` re-exports all three), pytest + pytest-asyncio, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-add-pygents-and-the-8ae25085/docs/superpowers/specs/task-add-pygents-and-the-8ae25085-design.md` (prepended above), narrowing `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 3.1 and `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` §3/§5/§9.

**Working directory for every command:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-add-pygents-and-the-8ae25085` (branch `m6/task-add-pygents-and-the-8ae25085`). Do not assume any sibling card's code (`runtime/state.py`, `runtime/bridge.py`, `runtime/compile.py`, `runtime/engine.py`, dispatch/prompt changes) exists on this branch — it does not.

**Deviation from the spec, on purpose:** the spec lists `tests/runtime/__init__.py`. No test directory in this repo has an `__init__.py` (`tests/steps/`, `tests/workflow/`, `tests/harness/`, `tests/roles/`, `tests/e2e/` all lack one), because `addopts` uses `--import-mode=importlib`, which does not need packages. This plan follows the sibling tier convention and creates `tests/runtime/test_context.py` only. The basename `test_context.py` is unique under `tests/`, so there is no module-name collision. `src/agent_manager/runtime/__init__.py` is still created, since source subpackages all have one.

## Global Constraints

- `pygents>=0.6.7` is the only new runtime dependency this milestone adds.
- `pytest-asyncio` goes in the `dev` dependency group only; `asyncio_mode = "auto"` goes under `[tool.pytest.ini_options]`; `addopts`, `testpaths` and `markers` stay unchanged.
- Within `src/agent_manager/`, only `src/agent_manager/runtime/` imports `pygents`.
- The pool holds JSON only; `runtime/context.py` is the single codec between the pool and typed Python.
- `encode` uses `model_dump(mode="json")` without `by_alias`; field names, not aliases, go into the pool.
- No new error handling: `ImportError`/`AttributeError`/`ValidationError`/`ValueError`/`KeyError` propagate as the libraries raise them.
- Do not touch `dispatch.py`, `prompt.py`, `engine.py`, `workflow/phases.py`, `harness/launcher.py`, `tests/test_engine.py`, or create `runtime/state.py`, `runtime/bridge.py`, `runtime/compile.py`, `runtime/engine.py`.
- `uv run pytest` must be fully green at the end of every task.

## Review Focus

1. A phase result holding a `Path` or `datetime` (directly or inside a nested model) is checkpointed with `json.dumps` — it must survive the round-trip unchanged (Task 1, `test_round_trip_through_json`).
2. A model with a `serialization_alias` (`Verification.full_suite` → `fullSuite`) goes through the pool — it must decode back to an equal model, not fail strict validation on the alias (Task 1, same test).
3. The memory queue holds items whose content is not a mapping (a plain string note) — `binding_table` must skip them rather than raise on `.get` (Task 2, `test_binding_table_ignores_non_mapping_queue_items`).
4. The pool holds the reserved `SKIPPED` item — it must not leak into the binding table as a phase named `skipped` (Task 2, `test_binding_table_excludes_the_skipped_item`).
5. A later edit adds `import pygents` to some module outside `runtime/` — the boundary must be caught by the suite, not by review alone (Task 2, `test_only_runtime_imports_pygents`).

---

### Task 1: The `encode`/`decode` codec

**Files:**
- Create: `src/agent_manager/runtime/__init__.py`
- Create: `src/agent_manager/runtime/context.py`
- Test: `tests/runtime/test_context.py`

**Interfaces:**
- Consumes: `agent_manager.results.ExploreResult`, `agent_manager.results.Verification` (`src/agent_manager/results.py:39-53`; both `strict=True`, `extra="forbid"`; `Verification.full_suite` has `serialization_alias="fullSuite"`).
- Produces:
  - `agent_manager.runtime.context.SUBTASK: str = "subtask"`
  - `agent_manager.runtime.context.SKIPPED: str = "skipped"`
  - `agent_manager.runtime.context.encode(value: Any) -> Any`
  - `agent_manager.runtime.context.decode(value: Any) -> Any`

- [ ] **Step 1: Write the failing round-trip test**

Create `tests/runtime/test_context.py` (no `__init__.py`, matching every other test directory):

```python
"""The pool codec (pygents-engine design §5, §9).

Unit tier per design §14: pure codec and data-shaping code over in-memory
pygents containers -- no git repo, no board, no fake adapter, no model call.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_manager import results
from agent_manager.runtime import context


def test_round_trip_through_json():
    value = {
        "worktree": Path("/r/.claude/worktrees/m6/x"),
        "at": datetime(2026, 9, 25, tzinfo=timezone.utc),
        "explore": results.ExploreResult(
            refused=False,
            reason=None,
            summary="s" * 80,
            verification=results.Verification(
                full_suite=["uv run pytest"], typecheck="", lint=[]
            ),
        ),
    }

    back = context.decode(json.loads(json.dumps(context.encode(value))))

    assert back == value
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/runtime/test_context.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'agent_manager.runtime'`.

- [ ] **Step 3: Create the package**

Create `src/agent_manager/runtime/__init__.py`:

```python
"""The pygents engine: the only package allowed to import pygents."""
```

- [ ] **Step 4: Write the codec**

Create `src/agent_manager/runtime/context.py`:

```python
"""The pool holds JSON only (spec §5); this is the one codec between it and the engine.

`encode` tags the three non-JSON types a phase result can carry -- pydantic
models, paths and datetimes -- so a checkpoint's `json.dumps` never meets
them; `decode` turns the tags back into the typed values. Models are dumped
by field name, not alias: gates that need aliases get them from
`dispatch.gate_values`. Tuples come back as lists.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

from pydantic import BaseModel

SUBTASK = "subtask"
SKIPPED = "skipped"


def encode(value: Any) -> Any:
    if isinstance(value, BaseModel):
        cls = type(value)
        return {
            "$model": f"{cls.__module__}:{cls.__qualname__}",
            "data": value.model_dump(mode="json"),
        }
    if isinstance(value, PurePath):
        return {"$path": str(value)}
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "$model" in value:
            module, qualname = value["$model"].split(":")
            cls: Any = importlib.import_module(module)
            for part in qualname.split("."):
                cls = getattr(cls, part)
            return cls.model_validate(value["data"])
        if "$path" in value:
            return Path(value["$path"])
        if "$dt" in value:
            return datetime.fromisoformat(value["$dt"])
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/runtime/test_context.py -v`
Expected: `test_round_trip_through_json PASSED`.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all tests pass (the `e2e`-marked tests stay deselected).

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/runtime/__init__.py src/agent_manager/runtime/context.py tests/runtime/test_context.py
git commit -m "feat(runtime): add the pool JSON codec"
```

---

### Task 2: Add pygents, `seed_item` and `binding_table`

**Files:**
- Modify: `pyproject.toml` (`dependencies` list lines 7-11, `[dependency-groups] dev` lines 13-16, `[tool.pytest.ini_options]` lines 28-38)
- Modify: `uv.lock` (regenerated by `uv add`)
- Modify: `src/agent_manager/runtime/context.py`
- Test: `tests/runtime/test_context.py`

**Interfaces:**
- Consumes: `context.encode`, `context.decode`, `context.SUBTASK`, `context.SKIPPED` from Task 1; `pygents.ContextPool` (`async add(item)`, `get(id) -> ContextItem`, `items -> list[ContextItem]`), `pygents.ContextQueue(limit: int)` (`async append(*items)`, `items -> list[ContextItem]`), `pygents.ContextItem(content, description=None, id=None)` (frozen dataclass; the pool requires both `id` and `description`).
- Produces (what sibling cards 023d918e, 2853e536 and b904b9e7 rely on):
  - `agent_manager.runtime.context.seed_item(binding: Mapping[str, Any]) -> ContextItem`
  - `agent_manager.runtime.context.binding_table(pool: ContextPool, memory: ContextQueue, phase: str) -> dict[str, Any]` — synchronous; the decoded seed, plus `table[item.id] = decode(item.content)` for every pool item other than `SUBTASK`/`SKIPPED`, plus `table["feedback"]: list[dict]` of the queue items whose content is a `Mapping` with `content["for"] == phase`, in queue order.

- [ ] **Step 1: Add the dependencies**

Run:

```bash
uv add "pygents>=0.6.7"
uv add --dev pytest-asyncio
```

Expected: `pyproject.toml`'s `dependencies` gains `"pygents>=0.6.7"`, the `dev` group gains a `pytest-asyncio>=...` entry, and `uv.lock` is updated. If `uv add` rewrote the pygents specifier to something else, edit it back to exactly `"pygents>=0.6.7"` and run `uv lock`.

- [ ] **Step 2: Turn on auto async mode**

In `pyproject.toml`, add the `asyncio_mode` line directly under `testpaths`, leaving `markers`, the comment block and `addopts` exactly as they are:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
```

- [ ] **Step 3: Write the failing binding-table tests**

In `tests/runtime/test_context.py`, add these imports below the existing `from pathlib import Path` line and above `from agent_manager import results`:

```python
from pygents import ContextItem, ContextPool, ContextQueue
```

Then append these tests to the end of the file:

```python
async def test_binding_table_rebuilds_seed_results_and_feedback():
    pool, memory = ContextPool(), ContextQueue(limit=10)
    await pool.add(context.seed_item({"branch": "m6/task-x-1234abcd", "worktree": Path("/w")}))
    await pool.add(
        ContextItem(
            id="spec",
            description="spec result",
            content=context.encode({"path": "docs/s.md", "note": None}),
        )
    )
    await memory.append(
        ContextItem(content={"for": "spec", "from": "validate_spec", "detail": "no error path"})
    )
    await memory.append(
        ContextItem(content={"for": "plan", "from": "validate_plan", "detail": "other"})
    )

    table = context.binding_table(pool, memory, "spec")

    assert table["branch"] == "m6/task-x-1234abcd"
    assert table["worktree"] == Path("/w")
    assert table["spec"] == {"path": "docs/s.md", "note": None}
    assert table["feedback"] == [
        {"for": "spec", "from": "validate_spec", "detail": "no error path"}
    ]


async def test_binding_table_excludes_the_skipped_item():
    pool, memory = ContextPool(), ContextQueue(limit=10)
    await pool.add(context.seed_item({"branch": "b"}))
    await pool.add(
        ContextItem(id=context.SKIPPED, description="skipped phases", content=["critic"])
    )

    table = context.binding_table(pool, memory, "plan")

    assert table == {"branch": "b", "feedback": []}


async def test_binding_table_ignores_non_mapping_queue_items():
    pool, memory = ContextPool(), ContextQueue(limit=10)
    await pool.add(context.seed_item({"branch": "b"}))
    await memory.append(ContextItem(content="a free-text note"))
    await memory.append(ContextItem(content={"for": "plan", "from": "review", "detail": "d"}))

    table = context.binding_table(pool, memory, "plan")

    assert table["feedback"] == [{"for": "plan", "from": "review", "detail": "d"}]


def test_seed_item_is_the_reserved_subtask_item():
    item = context.seed_item({"worktree": Path("/w")})

    assert item.id == context.SUBTASK == "subtask"
    assert item.description == "fixed subtask context"
    assert item.content == {"worktree": {"$path": "/w"}}
```

- [ ] **Step 4: Run them to verify they fail**

Run: `uv run pytest tests/runtime/test_context.py -v`
Expected: `test_round_trip_through_json` PASSES; the four new tests FAIL with `AttributeError: module 'agent_manager.runtime.context' has no attribute 'seed_item'`. (If instead they fail with "async def functions are not natively supported", Step 2's `asyncio_mode` line is missing — fix that first.)

- [ ] **Step 5: Implement `seed_item` and `binding_table`**

In `src/agent_manager/runtime/context.py`, add the pygents import after `from pydantic import BaseModel`:

```python
from pygents import ContextItem, ContextPool, ContextQueue
```

Then append to the end of the file:

```python
def seed_item(binding: Mapping[str, Any]) -> ContextItem:
    return ContextItem(
        id=SUBTASK, description="fixed subtask context", content=encode(dict(binding))
    )


def binding_table(pool: ContextPool, memory: ContextQueue, phase: str) -> dict[str, Any]:
    table: dict[str, Any] = dict(decode(pool.get(SUBTASK).content))
    for item in pool.items:
        if item.id not in (SUBTASK, SKIPPED):
            table[item.id] = decode(item.content)
    table["feedback"] = [
        dict(i.content)
        for i in memory.items
        if isinstance(i.content, Mapping) and i.content.get("for") == phase
    ]
    return table
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_context.py -v`
Expected: all five tests PASS.

- [ ] **Step 7: Add the import-boundary guard**

Append to `tests/runtime/test_context.py`:

```python
def test_only_runtime_imports_pygents():
    import re

    import agent_manager

    src = Path(agent_manager.__file__).parent
    runtime = src / "runtime"
    pattern = re.compile(r"^\s*(from|import)\s+pygents\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if runtime not in path.parents and pattern.search(path.read_text())
    ]

    assert offenders == []
```

This is a regression guard for a Global Constraint, so it passes on first run; confirm it bites by temporarily adding `import pygents` as the last line of `src/agent_manager/results.py`, running `uv run pytest tests/runtime/test_context.py::test_only_runtime_imports_pygents -v` (expected: FAIL listing `results.py`), then removing that line again.

- [ ] **Step 8: Run the guard**

Run: `uv run pytest tests/runtime/test_context.py::test_only_runtime_imports_pygents -v`
Expected: PASS (with `results.py` restored).

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest`
Expected: all tests pass, `e2e` tests deselected. `git diff --stat` shows only `pyproject.toml`, `uv.lock`, `src/agent_manager/runtime/context.py` and `tests/runtime/test_context.py`.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock src/agent_manager/runtime/context.py tests/runtime/test_context.py
git commit -m "feat(runtime): add pygents and the pool binding table"
```
