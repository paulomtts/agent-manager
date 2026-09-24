<!-- task-pipeline: validated -->
# Subtask 43008688 — Implement the `rollup.set_status` step

Parent story: 360cd141 "Close the seams the wiring test found" (milestone 7aa00a90). Siblings f68d29e2 and be376902 are done; this card is the last of the three. This spec narrows the already-agreed milestone design (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`, plus the real-harness addendum `docs/superpowers/specs/2026-09-23-real-harness-design.md`) to one module and its tests. No design decisions are reopened here.

## Scope

Two source files and two test files.

1. NEW `src/agent_manager/steps/rollup.py` — a deterministic step that writes one card's board status through `board.set_status`.
2. `src/agent_manager/workflow/registry.py` — bind the name `rollup.set_status` to the real callable instead of the placeholder.
3. NEW `tests/steps/test_rollup.py` — Steps-tier tests against a real temporary brd board.
4. `tests/workflow/test_registry.py` — move `rollup.set_status` from the placeholder assertions to the real-callable assertions.

Out of scope, explicitly: ancestor roll-up (parent story/milestone status propagation — deferred, real-harness addendum §4), milestone orchestration, parallel stories, `integrate`, non-Claude harnesses, and anything owned by the two done siblings. Do **not** touch `workflow/builtin/task.yaml` (phase ordering is f68d29e2's), `results.py` or `dispatch.py` (be376902's). `critic_blockers_gate` is already a real reducer (`reducers.critic_blockers_gate`) and is not touched.

## The step

`rollup.set_status` is called by the two `best_effort: true` deterministic phases of `workflow/builtin/task.yaml`: `mark_in_progress` (`args: { status: in_progress }`) and the terminal `mark_done` (`args: { status: done }`). Everything else it needs comes from the engine context.

Observable behaviour:

- It delegates to `board.set_status(card_id, status, repo_dir=repo_dir)` (`src/agent_manager/board.py:198-216`), which runs `brd update <id> --status <status>` and returns a validated `models.Card`.
- It returns a small plain mapping describing what was written — the card id and the status brd reports back on the returned `Card` (not the status that was requested; the truth is what brd stored). A `dict[str, object]`, matching the shape the other deterministic steps return (`worktree.ensure`, `plan_check.find_validated_plan`). No pydantic model: the convention reserves those for process-boundary results, and this value never leaves the process as a schema.
- It is read-through-free and cacheless: no `board.show` before or after the write, no journaling, no ancestor lookup.
- Idempotency is inherited, not implemented. `brd update --status` stores the value it is given, so a repeated identical call is another successful write of the same value; resume re-runs whole phases and that must stay harmless. The step adds no "already in this status" short-circuit.

### Binding contract (the gotcha)

The engine binds step arguments **by parameter name** from the run context, overlaid with the document's `args` (`src/agent_manager/engine.py:153-202`, `bind_arguments`). The context key holding the card id is `card`, a bare id string (`engine.py:89-98`; the full `models.Card` lives under `card_details`). There is no `card_id` key, and `bind_arguments` raises `EngineError` for any declared parameter with no default that is not in the supplied mapping.

Therefore the step's first parameter must be named `card`, even though the card text writes the signature as `set_status(card_id, status, repo_dir)`. `status` arrives from the document's `args`; `repo_dir` from the context (`engine.py:96`). No positional-only parameters — `bind_arguments` rejects them outright. The `card_id` naming in the card text is descriptive of intent, not of the parameter name; the binding rule wins.

### Error paths

- `board.BoardError` — brd missing, brd non-zero with no envelope, an `{"ok": false}` envelope (including a nonexistent card id), non-JSON output, or a payload that fails `models.Card` validation. The step **does not catch it**. Tolerance is the engine's policy: both call sites are `best_effort: true`, so the engine records the warning and the run continues. Swallowing it here would make a board-write failure invisible.
- No other exception type is introduced by this module. It defines no error class of its own.

## Registry change

In `src/agent_manager/workflow/registry.py`:

- Add `rollup` to the steps import (line 19, currently `from agent_manager.steps import plan_check, reducers, verify, worktree`).
- Replace the `_placeholder("rollup.set_status", ...)` registration (lines 222-225) with `registry.register("rollup.set_status", rollup.set_status)`, alongside the other real steps, so `default_registry().resolve("rollup.set_status") is rollup.set_status`.
- `critic_blockers_gate` is already registered as the real reducer; leave it. After this change no placeholder registrations remain; leave the `_placeholder` helper (registry.py:151) in place, unused, rather than widening scope.
- Correct the now-stale prose: the `default_registry` docstring says "`steps/rollup.py` does not exist" and counts "four implemented steps" / "one name still has no implementation"; the section comment says "Owned by siblings" and the registration sits in a separate block. Update to the post-change reality (six reducers, five implemented steps, no placeholders) and move the rollup registration into the deterministic-steps block.
- `BUILTIN_FUNCTION_NAMES` is unchanged — the name was always listed.

## Tests

Tier assignment per the test-placement rule, design spec §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`): pure functions unit-tested; **Steps tested against temporary git repos and a temporary brd board, no network**; adapters via pure `build_command` and an injected launcher; engine via a fake adapter; one opt-in slow end-to-end. `rollup.set_status` is a deterministic Steps component whose entire behaviour is a brd write, so its tests are **Steps tier** and run against a real temporary brd board — brd is not mocked, and neither is `board.set_status`.

New file `tests/steps/test_rollup.py`, with a module docstring in the style of `tests/steps/test_verify.py` stating the placement and citing §14. `tests/steps/` has no `conftest.py` (`test_verify.py` defines its own `requires_git` marker locally), so lift the brd helpers from `tests/test_board.py` into this module: the `requires_brd` skipif marker (line 18), the `temp_board` fixture (lines 237-257: `XDG_DATA_HOME` pointed at `tmp_path`, `brd init --name temp-board` in a fresh directory), `_add_card` (line 260) and `_brd_json` (line 270).

Steps tier, `tests/steps/test_rollup.py`:

1. **The card's status really changes.** Add a card to the temp board, call the step with `status="in_progress"`, and assert the stored status via `brd show` JSON (`_brd_json`) — not only the returned mapping. Also assert the returned mapping reports the card id and the new status.
2. **A second identical call is a no-op that does not raise.** Call the step twice with the same status; the second call returns the same mapping and `brd show` still reports that status. This is the resume/`best_effort` guarantee the board module documents at `board.py:207-211`.
3. **A nonexistent card id raises `board.BoardError`.** `pytest.raises(board.BoardError)` around a call with an id that was never added, proving the step does not swallow the adapter's failure.

Unit tier (no board needed) — optional but worthwhile as the wiring-level check the card asks for, in the same file or in `tests/workflow/test_registry.py`:

4. **The engine can bind the document's arguments to this function.** Call `engine.bind_arguments(rollup.set_status, <a context containing `card` and `repo_dir`>, {"status": "done"}, ...)` and assert the bound keyword mapping is complete — this is the regression guard for the `card` vs `card_id` parameter-name trap, and it is a pure-function check on `bind_arguments`, so it needs no board.

Unit tier, `tests/workflow/test_registry.py`:

5. Extend `test_default_registry_resolves_implemented_steps_to_the_real_callables` (line 108) with `registry.resolve("rollup.set_status") is rollup.set_status`, importing `rollup`.
6. Delete `test_the_one_remaining_placeholder_resolves_and_raises_when_called` (lines 123-128): it asserts `rollup.set_status` raises `NotImplementedError`, which is no longer true, and no other placeholder remains. `critic_blockers_gate` is already covered as real code (lines 111 and 131) and needs no change.
7. The name list at lines 78-91 and `test_default_registry_holds_exactly_the_names_task_yaml_uses` are unchanged.

`tests/workflow/test_builtin_task.py:60` (asserting the rollup phases are `best_effort`) and `tests/test_engine.py` (which uses its own fake `set_status`) need no change.

## Verification

`uv run pytest` (full suite). No typecheck step, no lint step.

---

# `rollup.set_status` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the deterministic `rollup.set_status` step that writes one card's board status through `board.set_status`, and bind it in the workflow registry in place of the placeholder.

**Architecture:** A new one-function module `src/agent_manager/steps/rollup.py` delegates to the existing brd adapter and returns a plain `dict[str, object]` of the card id plus the status brd reports back; `BoardError` propagates so the engine's `best_effort` policy journals it. `src/agent_manager/workflow/registry.py` replaces its `_placeholder("rollup.set_status", ...)` binding with the real callable, leaving `_placeholder` itself in place unused.

**Tech Stack:** Python 3, pydantic (only indirectly, via `models.Card` returned by `board.set_status`), pytest, `uv run pytest`, the real `brd` CLI over `subprocess` for the Steps-tier tests.

**Spec:** prepended verbatim above; source file `docs/superpowers/specs/task-implement-the-rollup-43008688-design.md` in this worktree.

## Global Constraints

- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md).
- Full verification is `uv run pytest`. There is no lint step and no typecheck step.
- Pydantic models only for values validated at a process boundary; this step's return value is a plain `dict[str, object]`.
- Do not touch `workflow/builtin/task.yaml`, `src/agent_manager/results.py`, or `src/agent_manager/dispatch.py` — they belong to the two done sibling cards.
- `BUILTIN_FUNCTION_NAMES` and `TASK_YAML_NAMES` are unchanged: `rollup.set_status` was always listed.
- No ancestor roll-up, no `board.show` read-through, no journaling inside the step.
- The step's first parameter is named `card` (the context key), never `card_id`.

## Review Focus

- **A nonexistent card id** must raise `board.BoardError` rather than returning a cheerful mapping — covered by Task 1, Step 5.
- **An empty-string card id** (a context key that was never populated) must raise `board.BoardError`, not silently write nothing — Task 1, Step 7.
- **The returned mapping must report the status brd stored**, not the literal that was requested; a caller journaling the request would hide a board that disagreed — Task 1, Step 1 asserts the mapping against the `brd show` JSON, not against the argument.
- **A second call with a *different* status** (the real `in_progress` → `done` transition, and the resume case where `mark_in_progress` re-runs after `mark_done`) must overwrite cleanly — Task 1, Step 3.
- **A document that wrote `args: { card_id: ... }`** must fail loudly at bind time rather than binding the wrong parameter — Task 2, Step 1 pins `EngineError` for an `args` key the function does not take.

---

### Task 1: The `rollup.set_status` step

**Files:**
- Create: `src/agent_manager/steps/rollup.py`
- Test: `tests/steps/test_rollup.py` (new; Steps tier — `tests/steps/` is the Steps-tier directory, alongside `test_verify.py` and `test_plan_check.py`)

**Interfaces:**
- Consumes: `agent_manager.board.set_status(card_id: str, status: str, *, repo_dir: Path | None = None) -> models.Card` and `agent_manager.board.BoardError` (`src/agent_manager/board.py:32-54`, `198-216`).
- Produces: `agent_manager.steps.rollup.set_status(card: str, status: str, repo_dir: str | Path | None = None) -> dict[str, object]`, returning `{"card": <str id brd echoed>, "status": <str status brd stored>}`. Task 2 registers this exact callable.

- [ ] **Step 1: Write the failing Steps-tier tests**

Create `tests/steps/test_rollup.py` with the whole file below. The brd helpers are lifted from `tests/test_board.py` (marker line 18, fixture lines 237-257, `_add_card` line 260, `_brd_json` line 270) because `tests/steps/` has no `conftest.py`.

```python
"""Behaviour of the roll-up step (design §4 `steps/`, subtask card 43008688).

Placement follows design §14: `rollup.py` is a Steps component whose entire
behaviour is a brd write, so it is exercised against a real temporary brd board
over subprocess -- brd is not mocked, and neither is `board.set_status`.

`tests/steps/` has no `conftest.py` (`test_verify.py` defines its own
`requires_git` marker locally), so the brd helpers are lifted from
`tests/test_board.py`: the `requires_brd` marker, the `temp_board` fixture,
`_add_card` and `_brd_json`.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_manager import board
from agent_manager.steps import rollup

requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the roll-up step's steps-tier tests",
)


@pytest.fixture
def temp_board(tmp_path, monkeypatch):
    """A real, empty brd board in a throwaway directory.

    brd keys its SQLite files off XDG_DATA_HOME and resolves the board from the
    nearest `.brd` marker at or above its cwd, so pointing XDG_DATA_HOME at
    tmp_path and running in a fresh directory isolates these tests completely
    from the developer's own board. The subprocess inherits the patched
    environment.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "board-repo"
    root.mkdir()
    subprocess.run(
        ["brd", "init", "--name", "temp-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return root


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(
        argv, cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]["id"]


def _brd_json(root: Path, *args: str) -> object:
    completed = subprocess.run(
        ["brd", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]


@requires_brd
def test_set_status_really_changes_the_card_on_the_board(temp_board):
    milestone = _add_card(temp_board, "Milestone 2")
    story = _add_card(temp_board, "Close the seams the wiring test found", milestone)
    subtask = _add_card(temp_board, "Implement the rollup.set_status step", story)
    assert _brd_json(temp_board, "show", subtask)["status"] == "todo"

    result = rollup.set_status(subtask, "in_progress", repo_dir=temp_board)

    stored = _brd_json(temp_board, "show", subtask)
    assert stored["status"] == "in_progress"
    # The mapping reports what brd stored, not the literal that was requested.
    assert result == {"card": subtask, "status": stored["status"]}


@requires_brd
def test_a_second_identical_call_is_a_harmless_no_op(temp_board):
    subtask = _add_card(temp_board, "Implement the rollup.set_status step")

    first = rollup.set_status(subtask, "done", repo_dir=temp_board)
    second = rollup.set_status(subtask, "done", repo_dir=temp_board)

    assert second == first
    assert _brd_json(temp_board, "show", subtask)["status"] == "done"


@requires_brd
def test_a_later_call_with_a_different_status_overwrites(temp_board):
    subtask = _add_card(temp_board, "Implement the rollup.set_status step")

    rollup.set_status(subtask, "in_progress", repo_dir=temp_board)
    result = rollup.set_status(subtask, "done", repo_dir=temp_board)

    assert result == {"card": subtask, "status": "done"}
    assert _brd_json(temp_board, "show", subtask)["status"] == "done"


@requires_brd
def test_a_nonexistent_card_raises_board_error(temp_board):
    with pytest.raises(board.BoardError):
        rollup.set_status("deadbeef", "done", repo_dir=temp_board)


@requires_brd
def test_an_empty_card_id_raises_board_error(temp_board):
    # A context key that was never populated must fail loudly, not write nothing.
    with pytest.raises(board.BoardError):
        rollup.set_status("", "done", repo_dir=temp_board)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_rollup.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'agent_manager.steps.rollup'` (or `ImportError: cannot import name 'rollup'`).

- [ ] **Step 3: Write the step**

Create `src/agent_manager/steps/rollup.py`:

```python
"""Write one card's board status (design §4 `steps/`, §6; subtask card 43008688).

A deterministic step: no model call, no network beyond whatever `brd` itself
does, no filesystem work of its own. The whole of it is one `board.set_status`
call, which runs `brd update <id> --status <status>`.

Ancestor roll-up -- propagating a subtask's completion up to its story and
milestone -- is deliberately not here: the real-harness addendum §4 defers it,
and this step writes exactly the one card the phase names.

Nothing is cached and nothing is read back: `board.show` before or after the
write would double the brd invocations for an answer the write already returns.

The first parameter is named `card`, not `card_id`, because the engine binds
arguments by parameter name out of the run context and the context key holding
the bare id string is `card` (`engine.py:98`, `bind_arguments` at
`engine.py:163-212`). There is no `card_id` key, so a parameter by that name
would fail to bind and the engine would report a missing required parameter.
"""

from pathlib import Path

from agent_manager import board


def set_status(
    card: str, status: str, repo_dir: str | Path | None = None
) -> dict[str, object]:
    """Set `card`'s board status to `status`, and report what brd stored.

    Called by the two `best_effort: true` phases of `builtin/task.yaml`
    (`mark_in_progress`, `mark_done`), which supply `status` through the
    document's `args`.

    Idempotency is inherited, not implemented: `brd update --status` stores the
    value it is given, so a repeated identical call is another successful write
    of the same value (`board.py:207-211`). There is no "already in this status"
    short-circuit, because resume re-runs whole phases and that must stay
    harmless.

    `board.BoardError` is not caught. Tolerance is the engine's policy -- both
    call sites are `best_effort`, so the engine journals the warning and the run
    continues; swallowing it here would make a failed board write invisible.

    The returned mapping reports the status on the `models.Card` brd answered
    with, not the requested literal: the board's copy is the truth. A plain
    `dict[str, object]`, like `worktree.ensure`'s result -- it crosses no
    process boundary, so it needs no pydantic model (`CLAUDE.md`).
    """
    written = board.set_status(
        card, status, repo_dir=Path(repo_dir) if repo_dir is not None else None
    )
    return {"card": written.id, "status": written.status}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_rollup.py -v`
Expected: all five PASS (or all skipped if `brd` is not on PATH — if they skip, install brd before continuing, since these are the only tests proving the step works).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/rollup.py tests/steps/test_rollup.py
git commit -m "feat: add the rollup.set_status deterministic step"
```

---

### Task 2: Register `rollup.set_status` as the real callable

**Files:**
- Modify: `src/agent_manager/workflow/registry.py:19` (import), `:228-265` (`default_registry` docstring, comment and registrations)
- Test: `tests/workflow/test_registry.py` (existing; unit/pure-functions tier — its own docstring says "Pure-functions tier (design spec §14): no filesystem, no network, no git")

**Interfaces:**
- Consumes: `agent_manager.steps.rollup.set_status(card, status, repo_dir=None) -> dict[str, object]` from Task 1; `agent_manager.engine.bind_arguments(fn, values, args=None, *, phase, function) -> dict[str, Any]`; `agent_manager.errors.EngineError`.
- Produces: `default_registry().resolve("rollup.set_status") is rollup.set_status`. No new names; `BUILTIN_FUNCTION_NAMES` is untouched.

- [ ] **Step 1: Write the failing unit-tier tests**

In `tests/workflow/test_registry.py`, extend the import block (lines 3-14) and the two tests below.

Add to the imports, replacing line 5:

```python
from agent_manager.engine import bind_arguments
from agent_manager.errors import EngineError
from agent_manager.steps import plan_check, reducers, rollup, verify, worktree
```

Append to `test_default_registry_resolves_implemented_steps_to_the_real_callables` (after line 120, `plan_check.has_validated_plan`):

```python
    assert registry.resolve("rollup.set_status") is rollup.set_status
```

Replace `test_the_one_remaining_placeholder_resolves_and_raises_when_called` (lines 123-128) entirely with these two tests:

```python
def test_the_engine_can_bind_the_documents_args_to_the_rollup_step() -> None:
    """The `card` vs `card_id` trap: the context key is `card`, a bare id."""
    bound = bind_arguments(
        rollup.set_status,
        {
            "card": "43008688",
            "card_details": None,
            "branch": "m2/task-implement-the-rollup-43008688",
            "repo_dir": Path("/repo"),
        },
        {"status": "done"},
        phase="mark_done",
        function="rollup.set_status",
    )
    assert bound == {
        "card": "43008688",
        "status": "done",
        "repo_dir": Path("/repo"),
    }


def test_binding_rejects_an_args_key_the_rollup_step_does_not_take() -> None:
    """A document that wrote `args: { card_id: ... }` must fail loudly."""
    with pytest.raises(EngineError):
        bind_arguments(
            rollup.set_status,
            {"card": "43008688", "repo_dir": Path("/repo")},
            {"card_id": "43008688", "status": "done"},
            phase="mark_done",
            function="rollup.set_status",
        )
```

Add `from pathlib import Path` to the module's imports (above `import pytest`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_registry.py -v`
Expected: `test_default_registry_resolves_implemented_steps_to_the_real_callables` FAILS on the new assertion (the registry still resolves the `_unimplemented` placeholder, not `rollup.set_status`). The two binding tests PASS already — they exercise Task 1's function directly, not the registry, and are the regression guard for the parameter name.

- [ ] **Step 3: Bind the real callable in the registry**

In `src/agent_manager/workflow/registry.py`, change line 19 to:

```python
from agent_manager.steps import plan_check, reducers, rollup, verify, worktree
```

Replace the third paragraph of the `default_registry` docstring (lines 236-242) with:

```python
    The six reducers and the five implemented steps are the real, imported
    callables -- not wrappers -- so `resolve(name) is the_function` holds and a
    sibling's bugfix reaches the engine without touching this table. No
    placeholder registrations remain: every name the document uses now has an
    implementation. `plan_hash_gate` is the single exception to the "no
    wrappers" rule: see `plan_hash_gate_adapter` above for why the binder cannot
    reach the two fields that gate compares.
```

Then replace the deterministic-steps block and the placeholder block (lines 254-264) with:

```python
    # Deterministic steps.
    registry.register("worktree.ensure", worktree.ensure)
    registry.register("verify.run_suite", verify.run_suite)
    registry.register("plan_check.find_validated_plan", plan_check.find_validated_plan)
    registry.register("plan_check.has_validated_plan", plan_check.has_validated_plan)
    registry.register("rollup.set_status", rollup.set_status)
    return registry
```

Leave `_placeholder` (line 151) and `_PLACEHOLDERS` (line 136) in place, unused: removing them is a wider change than this card owns.

- [ ] **Step 4: Run the registry tests to verify they pass**

Run: `uv run pytest tests/workflow/test_registry.py -v`
Expected: every test PASSES, and no test named `test_the_one_remaining_placeholder_resolves_and_raises_when_called` is collected.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS. `tests/workflow/test_builtin_task.py` (the rollup phases are `best_effort`) and `tests/test_engine.py` (its own fake `set_status`) are unaffected; `tests/workflow/test_loader.py` and the `default_registry` name-list tests still see the same eleven names.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/workflow/registry.py tests/workflow/test_registry.py
git commit -m "feat: bind rollup.set_status to the real step in the registry"
```
