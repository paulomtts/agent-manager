<!-- task-pipeline: validated -->
# Spec (verbatim)

<!-- Copied verbatim from docs/superpowers/specs/task-run-deterministic-ed77a917-design.md -->

# ed77a917 — Run deterministic phases in the engine

Date: 2026-09-23
Status: spec, pre-plan
Card: ed77a917 (subtask of story 2143808b, milestone 352e955b)
Source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§6 phase contract, §9 write ordering and resume, §12 escalation and best-effort, §14 testing)

## Scope

This subtask adds `src/agent_manager/engine.py`: the walk over one subtask's phases, and the execution of the `kind: deterministic` ones. It is the first code in the engine module; everything it needs already exists in this worktree (`workflow/loader.py`, `workflow/registry.py`, `workflow/builtin/task.yaml`, `models.py`, `store.py`, `paths.py`, `board.py`, `dag.py`, `steps/{worktree,verify,plan_check,reducers}.py` from dependency 2399fdcc and its predecessors), so no integration work is part of this card.

In scope:

- Walking `Workflow.phases` in document order for one subtask.
- Executing a `DeterministicPhase` by calling the callable the loader already bound in `Workflow.functions[phase.run]`, with arguments reconciled against the phase's declared `args` and the subtask's context.
- Recording every state edge through `Store.record_phase`, which already performs the §9 journal-before-DB ordering.
- Evaluating the phase's `gates` over the phase result; a failing gate escalates the subtask and stops the walk.
- Honouring `when` / `skip_to` (the `plan_check` short-circuit to `implement`).
- Honouring `best_effort`: a failed board write is recorded and surfaced in the returned summary, and never sinks an otherwise-sound subtask.

Explicitly not in scope, owned by siblings: input resolution and prompt rendering (968fba15), and agent-phase dispatch, schema validation, retry and escalation (bf8e415b). When the walk reaches a `kind: agent` phase it calls an injected callable — the seam bf8e415b fills — and does nothing else with it. Journal reading, replay and `resume <run-id>` reconstruction are also out of scope; the walk only accepts an optional starting phase name so a later resume subtask can re-enter it.

## The deterministic-phase contract, reconciled

§6 states the engine calls `run(ctx) -> dict`. The real step functions take named keyword arguments (`worktree.ensure(branch, base, worktree, repo_dir, git_runner=...)`, `verify.run_suite(commands, worktree, *, runner=...)`, `plan_check.find_validated_plan(card, plans_dir=None, *, repo_dir=None, ...)`), not a single `ctx`. The engine reconciles the two rather than rewriting the steps: it holds a context mapping for the subtask, overlays the phase's declared `args` from the document, and binds by parameter name against the callable's signature. Only parameters the callable actually declares are passed; a parameter that is required and cannot be supplied from `args` or the context is an engine error raised before the call, naming the phase, the function and the missing parameter — never a bare `TypeError` from the call site.

The context mapping's keys are the callables' exact parameter names, not the model's field names, because binding is by name and none of the builtin document's deterministic phases declares `args` to bridge a difference: `card`, `branch`, `repo_dir`, and each completed phase's result under the phase's name, plus two required renames off `SubtaskRun` — `base` (the model's `base_branch`) for `worktree.ensure`'s `base` parameter, and `worktree` (the model's `worktree_path`) for `worktree.ensure`'s and `verify.run_suite`'s `worktree` parameter — and `commands` (the run's configured verification commands) for `verify.run_suite`'s `commands` parameter. Without these renames the builtin `worktree` phase, which carries no `args`, would fail to bind `base` and `worktree` and the engine would raise its own "missing required parameter" error on the first deterministic phase of every run.

The walk's entry point also needs `story_id`, alongside the `SubtaskRun` (or its card id, branch and base) and the workflow: `Store.record_phase(story_id, card_id, phase)` and `Store.record_subtask(story_id, subtask)` both require it, and nothing else in this subtask's inputs supplies it. `story_id` is passed in by the caller (the run-level scheduler that owns story and subtask sequencing, out of scope here) and is not part of the per-subtask context mapping above, since none of the registered deterministic functions take it as a parameter.

The return value is the phase result. It is recorded and becomes available to later phases and to gates under the phase name, per §6. A deterministic step that returns something other than a mapping is an engine error, for the same reason: a later `when` or gate would read it as closed or empty and the run would take a silently wrong branch.

`DeterministicPhase` has no `retry` field. A deterministic phase therefore has exactly one attempt, and any gate failure on it is terminal.

## Observable behaviour

For each phase, in order:

1. **Agent phase** — delegate to the injected agent-phase runner and take its result as the phase result. If no runner was injected, that is an engine error naming the phase (this card does not stub agent behaviour).
2. **Deterministic phase** — record the phase as `started` (`PhaseRun(name, kind="deterministic", status="started", started_at=...)`) through `Store.record_phase`, bind arguments, call the function, then record the terminal status. No `Attempt` row is written: `models.Attempt` requires a `Dispatch` (harness, model, role, cwd, prompt_path, result_path), none of which exists for a function call, and fabricating one would put fiction in the journal. `PhaseRun` alone carries the whole story of a deterministic phase.
3. **Gates** — every name in `phase.gates` is resolved from `Workflow.functions` and called with arguments bound the same way, with the phase result in scope. A gate returns `None` to pass, `{"warn": ...}` to pass with a warning (recorded and surfaced in the summary, the walk continues), or a verdict dict to fail.
4. **`when` / `skip_to`** — if the phase declares both, the `when` function is called with the phase result; when it returns truthy, the walk jumps to the phase named by `skip_to` (the loader has already proved that target exists and is later in the document). A `when` that returns falsy continues to the next phase. `plan_check` with a validated plan on disk therefore skips `spec`, `validate_spec`, `plan` and `validate_plan` and resumes at `implement`; those skipped phases are not recorded as run.
5. The walk ends when the phases are exhausted, or when an escalation stops it.

The return value is a summary object for one subtask: the terminal subtask status (`done` or `escalated`), the phase results by name, the names of phases skipped by `skip_to`, and the list of warnings — best-effort failures and gate warnings — so a run never reports success while the board silently never moved (§12).

## Error paths

- **Gate failure on a non-best-effort deterministic phase.** The phase is recorded `failed`, the subtask is recorded `escalated` (both through `Store.record_phase` / `Store.record_subtask`, journal first), the walk stops, and the summary carries the gate name and the verdict detail. There is no retry, because `DeterministicPhase` has no `retry` field.
- **The step function raises.** Any exception from the callable — `steps.worktree.GitError`, an `OSError`, anything — is caught, the phase is recorded `failed` with the exception rendered into the journal payload, and the subtask escalates exactly as for a gate failure. The exception does not propagate out of the walk.
- **Best-effort phase fails**, whether by raising or by a failing gate: the phase is recorded `failed`, a warning is appended to the summary, the subtask does *not* escalate, and the walk continues with the next phase. This is the `mark_in_progress` / `mark_done` board write of §12.
- **Argument binding failure** (a required parameter with no value, or an `args` key the callable does not declare): an engine error naming phase, function and parameter, raised before the call; if the phase is best-effort it is treated as a best-effort failure like any other.
- **Non-mapping step result**: engine error, phase `failed`, escalation (or warning if best-effort).
- **A `when` function that raises**: treated as a failure of the phase it belongs to — never as "do not skip", because a silently-not-skipped `plan_check` re-plans work that was already validated.
- **Unknown starting phase name** passed to the walk: an engine error before anything is recorded.

Registry note: `registry.default_registry()` still binds `rollup.set_status`, `critic_blockers_gate` and `verification_passed_gate` to placeholders that raise `NotImplementedError`. Engine tests must construct their own `FunctionRegistry` with canned functions and load a workflow document against it; they must not drive the default registry.

## Tests

Tier per `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 (lines 477-492). The Pure tier there names `dag.py` and `steps/reducers.py`; the Steps tier names work against temp git repos and a temp `brd` board; the Adapters tier names `build_command`. None of those describe loop logic, so every test below is **Engine tier** — driven with canned fakes (here, a fake registry of deterministic functions standing in for §14's fake adapter with canned result files), against a temp store and journal, with no git, no `brd` and no harness. They land in `tests/test_engine.py`, mirroring `src/agent_manager/engine.py` per `CLAUDE.md`.

1. **Phases run in document order** — a workflow of three canned deterministic phases; the call order and the recorded phase order both match the document. *Engine tier.*
2. **A phase result is recorded and reaches a later phase** — phase B's bound argument is phase A's returned dict. *Engine tier.*
3. **Declared `args` are passed and override context** — `args: {status: in_progress}` arrives as the `status` keyword. *Engine tier.*
4. **Only declared parameters are bound** — a step taking two parameters is called with exactly those, from a context holding many more. *Engine tier.*
5. **A missing required parameter is an engine error naming phase, function and parameter**, raised without calling the step. *Engine tier.*
6. **Every state edge is journalled before the row is written** — the journal holds `started` then the terminal status for each phase, in sequence order, and the SQLite projection agrees. *Engine tier.*
7. **A passing gate lets the walk continue**; a gate returning `{"warn": ...}` also continues and the warning is in the summary. *Engine tier.*
8. **A failing gate on a deterministic phase escalates and stops** — phase `failed`, subtask `escalated`, no later phase runs, the verdict detail is in the journal and in the summary. *Engine tier.*
9. **A raising step escalates the same way** and the exception does not propagate. *Engine tier.*
10. **`when` truthy jumps to `skip_to`** — the `plan_check` shape: the intervening phases never run and are absent from the recorded phases, and the walk resumes at the target. *Engine tier.*
11. **`when` falsy continues to the next phase.** *Engine tier.*
12. **A best-effort failure does not sink the subtask** — the board-write phase raises, the phase is `failed`, the walk continues, the subtask ends `done`, and the failure is in the summary's warnings. *Engine tier.*
13. **A best-effort phase whose gate fails behaves identically** — warning, no escalation. *Engine tier.*
14. **The builtin `task.yaml` walks against a fake registry** — loading the shipped document with every name bound to a canned function drives the full twelve-phase order, with the agent phases going to the injected agent runner seam and the deterministic ones executing. *Engine tier.*
15. **Re-entering the walk at a named later phase** runs only from that phase onward; an unknown name is an engine error before anything is recorded. *Engine tier.*

Verification: `uv run pytest`. No separate lint or typecheck command.

---

# Deterministic Phase Execution in the Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/engine.py` so one subtask's loaded workflow phases are walked in document order, with every `kind: deterministic` phase executed, gated, journalled and recorded, and every failure resolved into either an escalation or a best-effort warning.

**Architecture:** A single public entry point, `engine.run_subtask(...)`, walks `Workflow.phases` from an optional starting index. Each deterministic phase is recorded `started` through `Store.record_phase` (which already does the §9 journal-before-DB write), then its callable — already bound by the loader in `Workflow.functions` — is called with keyword arguments bound by parameter name from a per-subtask context mapping overlaid with the phase's declared `args`. Gates and the `when` predicate are bound and called the same way, with the phase result in scope. The walk returns a mutable `SubtaskSummary` dataclass (status, results, skipped, warnings, failed phase, detail) rather than raising, so a caller always learns what happened. Agent phases are delegated wholesale to an injected `agent_runner` callable — the seam sibling bf8e415b fills — and nothing else about them (dispatch, schema validation, retry, their gates) is this module's business.

**Tech Stack:** Python 3, `inspect.signature` for argument binding, `dataclasses`, pydantic models from `agent_manager.models`, `agent_manager.store.Store`, `agent_manager.workflow.loader`, pytest with `tmp_path`/`monkeypatch`. Packaged with `uv`.

**Spec:** `docs/superpowers/specs/task-run-deterministic-ed77a917-design.md` (reproduced verbatim above). Its own source of truth is `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §6, §9, §12, §14.

## Global Constraints

- Source lives under `src/agent_manager/`, tests mirror it under `tests/` — so the only two files this plan touches are `src/agent_manager/engine.py` and `tests/test_engine.py`.
- Verification is `uv run pytest`. There is no separate lint or typecheck command.
- Every state edge goes through `Store.record_phase` / `Store.record_subtask`; this module never writes a row or a journal line itself (§9 journal-before-DB ordering already lives inside those methods).
- No `Attempt` and no `Dispatch` is ever constructed for a deterministic phase: `models.Attempt.dispatch` is required and would be fiction.
- Tests must build their own `FunctionRegistry` with canned callables. `registry.default_registry()` binds `rollup.set_status`, `critic_blockers_gate` and `verification_passed_gate` to placeholders that raise `NotImplementedError`; no test in this plan may drive it.
- Out of scope and not to be written here: input resolution and prompt rendering (sibling 968fba15), agent-phase dispatch/validation/retry/escalation (sibling bf8e415b), journal replay and `resume` reconstruction.
- Branch `m1/task-run-deterministic-ed77a917`, cut from `origin/m1/task-add-the-workflow-loader-2399fdcc`. Assume nothing from any other sibling subtask exists.
- No exception ever escapes `run_subtask` except `EngineError` raised *before* any recording (unknown start phase).

## Review Focus

- **A deterministic step returns something that is not a mapping** (a `list`, `None`, a `bool`) — the spec's error paths require an engine error, phase `failed`, escalation; without it a later `when` reads the value as closed and the run silently re-plans validated work. Test in Task 3.
- **A `when` predicate raises** — must fail its phase, never be read as "do not skip". Test in Task 5.
- **An agent phase is reached with no `agent_runner` injected** — must be a named engine error, not an `AttributeError` or a silently skipped phase. Test in Task 7.
- **A gate returns something that is neither `None` nor a mapping** (a bare `False`, a string) — must be an engine error, not silently treated as passing. Test in Task 4.
- **A phase whose name collides with a reserved context key** (`card`, `branch`, `base`, `worktree`, `repo_dir`, `commands`) — the shipped `builtin/task.yaml` names its worktree-setup phase `worktree`, exactly the key `subtask_context` binds the real worktree path under, so this is not a hypothetical document error to refuse but a real, load-bearing document the engine must run. A colliding phase still runs and its result is recorded and returned in the summary; it is simply never folded back into the binding table, so the reserved value (the real worktree path, in the shipped case) survives for every later phase that binds `worktree` by name, instead of being silently replaced by the `worktree` phase's own `{"created": ...}` result. Test in Task 2.

---

## File Structure

- **Create `src/agent_manager/engine.py`** — the whole deliverable. Holds `EngineError`, `RESERVED_CONTEXT_KEYS`, `subtask_context`, `bind_arguments`, `SubtaskSummary`, `AgentPhaseRunner`, and `run_subtask` plus its private helpers (`_run_deterministic`, `_evaluate_gates`, `_skip_target`, `_bind_result`, `_record_phase`, `_record_subtask_status`).
- **Create `tests/test_engine.py`** — every test in this plan. Engine tier per §14: canned fake functions in a hand-built `FunctionRegistry`, a temp `Store` (SQLite + JSONL journal under a redirected `XDG_DATA_HOME`), no git, no `brd`, no harness. Sits directly under `tests/` mirroring `src/agent_manager/engine.py`, alongside `tests/test_store.py` and `tests/test_dag.py`.

---

## Task 1: Engine errors, the subtask context, and argument binding

**Files:**
- Create: `src/agent_manager/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `agent_manager.models.SubtaskRun` (fields `card_id`, `branch`, `base_branch`, `worktree_path`).
- Produces:
  - `EngineError(reason: str, *, phase: str | None = None, function: str | None = None, parameter: str | None = None)` — a `RuntimeError` subclass with those four attributes.
  - `RESERVED_CONTEXT_KEYS: tuple[str, ...]`.
  - `subtask_context(subtask: models.SubtaskRun, repo_dir: Path, commands: Sequence[str] = ()) -> dict[str, Any]`.
  - `bind_arguments(fn: Callable[..., Any], values: Mapping[str, Any], args: Mapping[str, Any] | None = None, *, phase: str, function: str) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing tests for the context mapping and the binder**

Create `tests/test_engine.py` with exactly this content:

```python
"""Behaviour of the phase walk and deterministic phase execution (spec §6, §9, §12).

Engine tier per design §14 lines 477-492: canned fake functions in a hand-built
`FunctionRegistry` stand in for §14's fake adapter with canned result files, and
the store is a real temp SQLite projection plus a real temp JSONL journal. No
git, no `brd`, no harness process, nothing from `default_registry()` — three of
its names are placeholders that raise `NotImplementedError`.
"""

from pathlib import Path
from typing import Any

import pytest

from agent_manager import engine, models

REPO = Path("/repo")


def _subtask(card: str = "ed77a917") -> models.SubtaskRun:
    return models.SubtaskRun(
        card_id=card,
        branch=f"m1/task-{card}",
        base_branch="m1/story-base",
        status="started",
        worktree_path=Path(f"/repo/.claude/worktrees/m1/task-{card}"),
    )


def test_subtask_context_renames_the_model_fields_the_steps_ask_for():
    context = engine.subtask_context(_subtask(), REPO, ["uv run pytest"])

    assert context == {
        "card": "ed77a917",
        "branch": "m1/task-ed77a917",
        "base": "m1/story-base",
        "worktree": Path("/repo/.claude/worktrees/m1/task-ed77a917"),
        "repo_dir": REPO,
        "commands": ["uv run pytest"],
    }


def test_bind_arguments_passes_only_the_parameters_the_callable_declares():
    def step(branch: str, repo_dir: Path) -> dict[str, Any]:
        return {"branch": branch, "repo_dir": repo_dir}

    bound = engine.bind_arguments(
        step,
        engine.subtask_context(_subtask(), REPO, ["uv run pytest"]),
        phase="worktree",
        function="worktree.ensure",
    )

    assert bound == {"branch": "m1/task-ed77a917", "repo_dir": REPO}


def test_bind_arguments_lets_declared_args_override_the_context():
    def step(card: str, status: str) -> dict[str, Any]:
        return {"card": card, "status": status}

    bound = engine.bind_arguments(
        step,
        {"card": "ed77a917", "status": "from-context"},
        {"status": "in_progress"},
        phase="mark_in_progress",
        function="rollup.set_status",
    )

    assert bound == {"card": "ed77a917", "status": "in_progress"}


def test_bind_arguments_skips_optional_parameters_nothing_supplies():
    def step(card: str, plans_dir: str | None = None) -> dict[str, Any]:
        return {"card": card, "plans_dir": plans_dir}

    bound = engine.bind_arguments(
        step, {"card": "ed77a917"}, phase="plan_check", function="plan_check.find"
    )

    assert bound == {"card": "ed77a917"}


def test_bind_arguments_names_phase_function_and_parameter_for_a_missing_required():
    called: list[str] = []

    def step(card: str, commands: list[str]) -> dict[str, Any]:
        called.append(card)
        return {}

    with pytest.raises(engine.EngineError) as caught:
        engine.bind_arguments(
            step, {"card": "ed77a917"}, phase="verify", function="verify.run_suite"
        )

    assert called == []
    assert caught.value.phase == "verify"
    assert caught.value.function == "verify.run_suite"
    assert caught.value.parameter == "commands"
    message = str(caught.value)
    assert "'verify'" in message
    assert "'verify.run_suite'" in message
    assert "'commands'" in message


def test_bind_arguments_refuses_an_args_key_the_callable_does_not_declare():
    def step(card: str) -> dict[str, Any]:
        return {}

    with pytest.raises(engine.EngineError) as caught:
        engine.bind_arguments(
            step,
            {"card": "ed77a917"},
            {"stauts": "done"},
            phase="mark_done",
            function="rollup.set_status",
        )

    assert caught.value.parameter == "stauts"
    assert "does not take" in str(caught.value)


def test_bind_arguments_refuses_a_positional_only_parameter():
    def step(card: str, /) -> dict[str, Any]:
        return {}

    with pytest.raises(engine.EngineError) as caught:
        engine.bind_arguments(
            step, {"card": "ed77a917"}, phase="plan_check", function="plan_check.find"
        )

    assert caught.value.parameter == "card"
    assert "positional-only" in str(caught.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: collection error — `ImportError: cannot import name 'engine' from 'agent_manager'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/engine.py`:

```python
"""Walk one subtask's phases and execute the deterministic ones (design §6).

The walk is the only thing here. Input resolution and prompt rendering belong to
a sibling, and so does everything about an agent phase past handing it to the
injected runner: this module treats a `kind: agent` phase as an opaque call.

§6 says the engine calls `run(ctx) -> dict`, but the real steps take named
keyword arguments (`worktree.ensure(branch, base, worktree, repo_dir)`,
`verify.run_suite(commands, worktree)`, `plan_check.find_validated_plan(card)`).
Rather than rewrite four working steps, the engine binds by parameter name out
of a per-subtask context mapping overlaid with the phase's declared `args`, and
raises its own error naming phase, function and parameter before the call — a
bare `TypeError` from a call site tells an operator nothing about which line of
YAML is wrong.
"""

import inspect
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_manager import models

_EMPTY = inspect.Parameter.empty
_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)


class EngineError(RuntimeError):
    """The engine refused to run, or could not make sense of, a phase.

    Carries the coordinates an operator needs to find the offending line of the
    workflow document: which phase, which registered function, which parameter.
    """

    def __init__(
        self,
        reason: str,
        *,
        phase: str | None = None,
        function: str | None = None,
        parameter: str | None = None,
    ) -> None:
        self.reason = reason
        self.phase = phase
        self.function = function
        self.parameter = parameter
        parts = []
        if phase is not None:
            parts.append(f"phase {phase!r}")
        if function is not None:
            parts.append(f"function {function!r}")
        if parameter is not None:
            parts.append(f"parameter {parameter!r}")
        prefix = ", ".join(parts)
        super().__init__(f"{prefix}: {reason}" if prefix else reason)


RESERVED_CONTEXT_KEYS = ("card", "branch", "base", "worktree", "repo_dir", "commands")
"""The context keys `subtask_context` always sets.

Named as a constant because phase results land in the same mapping under the
phase's name: a phase called `worktree` -- the shipped `builtin/task.yaml`
has exactly one -- would otherwise overwrite the real worktree path every
later step binds from. `_bind_result` uses this to skip writing such a
result back into the table rather than refuse the phase outright.
"""


def subtask_context(
    subtask: models.SubtaskRun, repo_dir: Path, commands: Sequence[str] = ()
) -> dict[str, Any]:
    """The starting binding table for one subtask's phases.

    The keys are the *callables'* parameter names, not the model's field names:
    binding is by name, and no deterministic phase in `builtin/task.yaml`
    declares `args` that could bridge the difference. Hence `base` for
    `base_branch` and `worktree` for `worktree_path`.
    """
    return {
        "card": subtask.card_id,
        "branch": subtask.branch,
        "base": subtask.base_branch,
        "worktree": subtask.worktree_path,
        "repo_dir": repo_dir,
        "commands": list(commands),
    }


def bind_arguments(
    fn: Callable[..., Any],
    values: Mapping[str, Any],
    args: Mapping[str, Any] | None = None,
    *,
    phase: str,
    function: str,
) -> dict[str, Any]:
    """Keyword arguments for `fn`, taken by name from `values` overlaid with `args`.

    Only parameters `fn` actually declares are passed, so a context holding
    twenty keys still calls a two-parameter step with two. `*args`/`**kwargs`
    are ignored rather than fed: a step that declares `**kwargs` has not asked
    for the whole context.
    """
    args = {} if args is None else args
    parameters = inspect.signature(fn).parameters
    for key in args:
        if key not in parameters:
            raise EngineError(
                f"the document declares args key {key!r}, which this function does not "
                f"take (it takes: {', '.join(parameters) or 'nothing'})",
                phase=phase,
                function=function,
                parameter=key,
            )
    supplied = {**values, **args}
    bound: dict[str, Any] = {}
    for parameter in parameters.values():
        if parameter.kind in _VARIADIC:
            continue
        if parameter.name not in supplied:
            if parameter.default is _EMPTY:
                raise EngineError(
                    "no value for a required parameter "
                    f"(available: {', '.join(sorted(supplied)) or 'nothing'})",
                    phase=phase,
                    function=function,
                    parameter=parameter.name,
                )
            continue
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise EngineError(
                "is positional-only, and the engine binds every argument by name",
                phase=phase,
                function=function,
                parameter=parameter.name,
            )
        bound[parameter.name] = supplied[parameter.name]
    return bound
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): bind step arguments by name from a subtask context"
```

---

## Task 2: Walk the deterministic phases and record every edge

**Files:**
- Modify: `src/agent_manager/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `bind_arguments`, `subtask_context`, `EngineError`, `RESERVED_CONTEXT_KEYS` from Task 1; `agent_manager.store.Store` (`record_phase(story_id, card_id, PhaseRun)`, `record_subtask(story_id, SubtaskRun)`, `.journal`, `.connection`); `agent_manager.workflow.loader.Workflow` (`.phases`, `.phase_names`, `.function(name)`), `DeterministicPhase`.
- Produces:
  - `SubtaskSummary` dataclass: `status: Literal["done", "escalated"]`, `results: dict[str, Any]`, `skipped: list[str]`, `warnings: list[str]`, `failed_phase: str | None`, `detail: str | None`.
  - `run_subtask(workflow, store, *, story_id, subtask, repo_dir, commands=(), start_phase=None, clock=_utcnow) -> SubtaskSummary`. Later tasks add the `agent_runner` keyword.

  A phase named the same as a reserved context key (`worktree`, notably: the shipped `builtin/task.yaml` names its worktree-setup phase exactly that) always runs and is always recorded and returned in `results`; its result is just never folded back into the binding table under that name, so the reserved value survives for every later phase that binds it by name.

- [ ] **Step 1: Write the failing tests for order, result flow and write ordering**

Append to `tests/test_engine.py` (and extend the import line at the top from `from agent_manager import engine, models` to `from agent_manager import engine, models, store as store_module`):

```python
from agent_manager.workflow.loader import load_workflow
from agent_manager.workflow.registry import FunctionRegistry

RUN_ID = "run-2026-09-23-01"
STORY_ID = "2143808b"


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A real temp projection plus a real temp journal, writing nowhere real."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    opened = store_module.Store.open(tmp_path / "repo", RUN_ID)
    yield opened
    opened.close()


def _registry(functions: dict[str, Any]) -> FunctionRegistry:
    registry = FunctionRegistry()
    for name, fn in functions.items():
        registry.register(name, fn)
    return registry


def _workflow(document: str, functions: dict[str, Any]):
    return load_workflow(document, _registry(functions))


def _journalled_phases(opened) -> list[tuple[str | None, str]]:
    return [
        (line.phase, line.payload["status"])
        for line in opened.journal.read()
        if line.event == "phase_upsert"
    ]


def _projected_phases(opened) -> list[tuple[str, str]]:
    return [
        (row["name"], row["status"])
        for row in opened.connection.execute(
            "SELECT name, status FROM phases ORDER BY position"
        ).fetchall()
    ]


THREE_PHASES = """
name: three
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
  - name: beta
    kind: deterministic
    run: step.beta
  - name: gamma
    kind: deterministic
    run: step.gamma
"""


def test_phases_run_in_document_order(store):
    calls: list[str] = []

    def make(name: str):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            return {"phase": name}

        return step

    workflow = _workflow(
        THREE_PHASES,
        {"step.alpha": make("alpha"), "step.beta": make("beta"), "step.gamma": make("gamma")},
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["alpha", "beta", "gamma"]
    assert summary.status == "done"
    assert summary.results == {
        "alpha": {"phase": "alpha"},
        "beta": {"phase": "beta"},
        "gamma": {"phase": "gamma"},
    }
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done"), ("gamma", "done")]


def test_a_phase_result_is_bound_into_a_later_phase(store):
    seen: dict[str, Any] = {}

    def alpha(card: str) -> dict[str, Any]:
        return {"plan": "docs/plan.md"}

    def beta(alpha: dict[str, Any]) -> dict[str, Any]:
        seen["alpha"] = alpha
        return {}

    def gamma(card: str) -> dict[str, Any]:
        return {}

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": alpha, "step.beta": beta, "step.gamma": gamma}
    )

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen["alpha"] == {"plan": "docs/plan.md"}


def test_declared_args_reach_the_step_as_a_keyword(store):
    seen: list[str] = []

    def set_status(card: str, status: str) -> dict[str, Any]:
        seen.append(status)
        return {"status": status}

    document = """
name: board
phases:
  - name: mark_in_progress
    kind: deterministic
    run: rollup.set_status
    args: { status: in_progress }
"""
    workflow = _workflow(document, {"rollup.set_status": set_status})

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen == ["in_progress"]


def test_a_step_is_called_with_only_the_parameters_it_declares(store):
    seen: dict[str, Any] = {}

    def ensure(branch: str, base: str) -> dict[str, Any]:
        seen.update(branch=branch, base=base)
        return {}

    document = """
name: one
phases:
  - name: worktree
    kind: deterministic
    run: worktree.ensure
"""
    workflow = _workflow(document, {"worktree.ensure": ensure})

    engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        commands=["uv run pytest"],
    )

    assert seen == {"branch": "m1/task-ed77a917", "base": "m1/story-base"}


def test_every_state_edge_is_journalled_before_the_row_is_written(store):
    def step(card: str) -> dict[str, Any]:
        return {}

    document = """
name: two
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
  - name: beta
    kind: deterministic
    run: step.beta
"""
    workflow = _workflow(document, {"step.alpha": step, "step.beta": step})

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert _journalled_phases(store) == [
        ("alpha", "started"),
        ("alpha", "done"),
        ("beta", "started"),
        ("beta", "done"),
    ]
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done")]
    assert [line.event for line in store.journal.read()][-1] == "subtask_upsert"
    assert store.journal.read()[-1].payload["status"] == "done"


def test_no_attempt_row_is_written_for_a_deterministic_phase(store):
    def step(card: str) -> dict[str, Any]:
        return {}

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": step})

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert store.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    assert not any(line.event == "attempt_upsert" for line in store.journal.read())


def test_a_phase_named_like_a_context_key_runs_but_never_clobbers_it(store):
    """The `worktree` phase of the shipped `builtin/task.yaml` names itself the
    same as the context key `subtask_context` binds the real worktree path
    under. It must still run and record normally; its own result must simply
    never overwrite the context key later phases bind `worktree` from, or the
    real `verify` phase would receive `{"created": True}` where it needs a
    filesystem path.
    """
    seen: dict[str, Any] = {}

    def make_worktree(card: str, worktree: Any) -> dict[str, Any]:
        return {"created": True, "original_worktree_arg": worktree}

    def uses_worktree(worktree: Any) -> dict[str, Any]:
        seen["worktree"] = worktree
        return {}

    document = """
name: collide
phases:
  - name: worktree
    kind: deterministic
    run: worktree.make
  - name: after
    kind: deterministic
    run: step.after
"""
    workflow = _workflow(
        document, {"worktree.make": make_worktree, "step.after": uses_worktree}
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    real_worktree = _subtask().worktree_path
    assert summary.status == "done"
    assert summary.results["worktree"] == {
        "created": True,
        "original_worktree_arg": real_worktree,
    }
    assert seen["worktree"] == real_worktree
    assert _projected_phases(store) == [("worktree", "done"), ("after", "done")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: the seven new tests FAIL with `AttributeError: module 'agent_manager.engine' has no attribute 'run_subtask'`; Task 1's seven still pass.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/engine.py`, extend the imports and append the walk:

```python
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent_manager import models
from agent_manager.store import Store
from agent_manager.workflow.loader import DeterministicPhase, Workflow
```

```python
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


Clock = Callable[[], datetime]


@dataclass
class SubtaskSummary:
    """What the walk did to one subtask.

    Returned rather than raised: a caller must be able to tell a clean `done`
    from a `done` whose board write silently failed (§12), and an exception
    carries neither the results nor the warnings.
    """

    status: Literal["done", "escalated"] = "done"
    results: dict[str, Any] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed_phase: str | None = None
    detail: str | None = None


@dataclass
class _Outcome:
    """One deterministic phase's verdict, already recorded."""

    ok: bool
    result: Any = None
    detail: str | None = None


def run_subtask(
    workflow: Workflow,
    store: Store,
    *,
    story_id: str,
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    start_phase: str | None = None,
    clock: Clock = _utcnow,
) -> SubtaskSummary:
    """Walk `workflow`'s phases for one subtask, running the deterministic ones.

    `story_id` is the caller's: `Store.record_phase` and `Store.record_subtask`
    are both keyed by it, and nothing in a subtask knows its story.
    """
    index = _start_index(workflow, start_phase)
    context = subtask_context(subtask, repo_dir, commands)
    summary = SubtaskSummary()

    while index < len(workflow.phases):
        phase = workflow.phases[index]
        if not isinstance(phase, DeterministicPhase):
            raise EngineError(
                "is an agent phase, but no agent runner was injected",
                phase=phase.name,
            )
        outcome = _run_deterministic(
            phase, workflow, store, story_id, subtask, context, clock
        )
        _bind_result(context, phase.name, outcome.result)
        summary.results[phase.name] = outcome.result
        index += 1

    _record_subtask_status(store, story_id, subtask, summary.status)
    return summary


def _bind_result(context: dict[str, Any], phase_name: str, result: Any) -> None:
    """Fold one phase's result into the binding table under its own name.

    The shipped `builtin/task.yaml` names its worktree-setup phase `worktree`,
    exactly the key `subtask_context` binds the real worktree path under. Its
    result is still recorded and returned in the summary either way (see
    `run_subtask`); it is just never written back here, so the reserved value
    survives for every later phase that binds `worktree` (or any other
    reserved key) by name, instead of being silently replaced by a same-named
    phase's own result.
    """
    if phase_name not in RESERVED_CONTEXT_KEYS:
        context[phase_name] = result


def _start_index(workflow: Workflow, start_phase: str | None) -> int:
    if start_phase is None:
        return 0
    for index, phase in enumerate(workflow.phases):
        if phase.name == start_phase:
            return index
    raise EngineError(
        f"cannot start at {start_phase!r}: workflow {workflow.name!r} has no such phase "
        f"(phases: {', '.join(workflow.phase_names)})"
    )


def _run_deterministic(
    phase: DeterministicPhase,
    workflow: Workflow,
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    context: Mapping[str, Any],
    clock: Clock,
) -> _Outcome:
    started_at = clock()
    _record_phase(store, story_id, subtask, phase, "started", started_at, None)
    function = workflow.function(phase.run)
    kwargs = bind_arguments(
        function, context, phase.args, phase=phase.name, function=phase.run
    )
    result = function(**kwargs)
    _record_phase(store, story_id, subtask, phase, "done", started_at, clock())
    return _Outcome(ok=True, result=result)


def _record_phase(
    store: Store,
    story_id: str,
    subtask: models.SubtaskRun,
    phase: DeterministicPhase,
    status: models.Status,
    started_at: datetime,
    ended_at: datetime | None,
) -> None:
    store.record_phase(
        story_id,
        subtask.card_id,
        models.PhaseRun(
            name=phase.name,
            kind="deterministic",
            status=status,
            started_at=started_at,
            ended_at=ended_at,
        ),
    )


def _record_subtask_status(
    store: Store, story_id: str, subtask: models.SubtaskRun, status: models.Status
) -> None:
    store.record_subtask(story_id, subtask.model_copy(update={"status": status}))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): walk a subtask's deterministic phases, recording every edge"
```

---

## Task 3: Failure handling — a raising step, a non-mapping result, escalation

**Files:**
- Modify: `src/agent_manager/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `_run_deterministic`, `_Outcome`, `SubtaskSummary`, `_record_phase`, `_record_subtask_status` from Task 2.
- Produces: `_Outcome.detail` populated on failure; `run_subtask` returns `SubtaskSummary(status="escalated", failed_phase=..., detail=...)` instead of propagating; `_render_error(error) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
def test_a_raising_step_escalates_and_the_exception_does_not_propagate(store):
    calls: list[str] = []

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        raise OSError("disk went away")

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        return {}

    document = """
name: two
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
  - name: beta
    kind: deterministic
    run: step.beta
"""
    workflow = _workflow(document, {"step.alpha": alpha, "step.beta": beta})

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["alpha"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "alpha"
    assert "disk went away" in summary.detail
    assert _journalled_phases(store) == [("alpha", "started"), ("alpha", "failed")]
    assert _projected_phases(store) == [("alpha", "failed")]
    assert store.journal.read()[-1].event == "subtask_upsert"
    assert store.journal.read()[-1].payload["status"] == "escalated"


def test_a_binding_failure_escalates_without_calling_the_step(store):
    calls: list[str] = []

    def alpha(card: str, missing_thing: str) -> dict[str, Any]:
        calls.append("alpha")
        return {}

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == []
    assert summary.status == "escalated"
    assert "missing_thing" in summary.detail


@pytest.mark.parametrize("returned", [None, ["a", "list"], True, "a string"])
def test_a_non_mapping_step_result_escalates(store, returned):
    def alpha(card: str) -> Any:
        return returned

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "alpha"
    assert "mapping" in summary.detail
    assert _projected_phases(store) == [("alpha", "failed")]


def test_a_failed_phase_result_is_not_offered_to_later_phases(store):
    def alpha(card: str) -> dict[str, Any]:
        raise RuntimeError("nope")

    document = """
name: one
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
"""
    workflow = _workflow(document, {"step.alpha": alpha})

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.results == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: the new tests FAIL — `OSError: disk went away` / `EngineError` propagate out of `run_subtask`, and the non-mapping cases pass a `None` straight through into `summary.results`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/engine.py`, replace `_run_deterministic` with the failure-aware version and add `_render_error`:

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
    started_at = clock()
    _record_phase(store, story_id, subtask, phase, "started", started_at, None)
    try:
        function = workflow.function(phase.run)
        kwargs = bind_arguments(
            function, context, phase.args, phase=phase.name, function=phase.run
        )
        result = function(**kwargs)
        if not isinstance(result, Mapping):
            raise EngineError(
                f"returned {type(result).__name__}, but a deterministic phase must "
                "return a mapping: a gate or a later `when` would read anything else "
                "as closed and the run would branch wrongly",
                phase=phase.name,
                function=phase.run,
            )
    except Exception as error:
        # Deliberately total. A step is other people's code -- GitError, OSError,
        # anything -- and an exception escaping the walk would leave the subtask
        # recorded `started` forever, which is exactly what resume mistakes for
        # work in flight.
        _record_phase(store, story_id, subtask, phase, "failed", started_at, clock())
        return _Outcome(ok=False, detail=_render_error(error))
    _record_phase(store, story_id, subtask, phase, "done", started_at, clock())
    return _Outcome(ok=True, result=result)


def _render_error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"
```

and replace the body of the `while` loop in `run_subtask` (everything from `outcome = _run_deterministic(...)` to `index += 1`) with:

```python
        outcome = _run_deterministic(
            phase, workflow, store, story_id, subtask, context, clock
        )
        if not outcome.ok:
            summary.status = "escalated"
            summary.failed_phase = phase.name
            summary.detail = outcome.detail
            _record_subtask_status(store, story_id, subtask, "escalated")
            return summary
        _bind_result(context, phase.name, outcome.result)
        summary.results[phase.name] = outcome.result
        index += 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: 21 passed (the non-mapping test is parametrized four ways).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): escalate a subtask when a deterministic phase fails"
```

---

## Task 4: Gates — pass, warn, fail

**Files:**
- Modify: `src/agent_manager/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `_run_deterministic`, `_Outcome`, `bind_arguments`, `EngineError` from Tasks 1-3.
- Produces: `_Outcome.warnings: list[str]`; `_GateFailed(detail: str)` private exception; `_evaluate_gates(phase, workflow, values, warnings) -> None`; `_gate_values(context, phase_name, result) -> dict[str, Any]`. `SubtaskSummary.warnings` is populated from `_Outcome.warnings`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
GATED = """
name: gated
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
    gates: [alpha_gate]
  - name: beta
    kind: deterministic
    run: step.beta
"""


def test_a_passing_gate_lets_the_walk_continue(store):
    seen: dict[str, Any] = {}

    def alpha(card: str) -> dict[str, Any]:
        return {"passed": True}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(result: dict[str, Any]) -> dict[str, str] | None:
        seen["result"] = result
        return None

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen["result"] == {"passed": True}
    assert summary.status == "done"
    assert summary.warnings == []
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done")]


def test_a_warning_gate_continues_and_surfaces_the_warning(store):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"warn": "the suite reported no tests"}

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"
    assert len(summary.warnings) == 1
    assert "alpha_gate" in summary.warnings[0]
    assert "the suite reported no tests" in summary.warnings[0]
    assert _projected_phases(store) == [("alpha", "done"), ("beta", "done")]


def test_a_failing_gate_escalates_and_stops_the_walk(store):
    calls: list[str] = []

    def alpha(card: str) -> dict[str, Any]:
        calls.append("alpha")
        return {"passed": False}

    def beta(card: str) -> dict[str, Any]:
        calls.append("beta")
        return {}

    def alpha_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"blocked": "verification", "detail": "2 of 3 commands failed"}

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["alpha"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "alpha"
    assert "alpha_gate" in summary.detail
    assert "2 of 3 commands failed" in summary.detail
    assert _journalled_phases(store) == [("alpha", "started"), ("alpha", "failed")]
    assert store.journal.read()[-1].payload["status"] == "escalated"


def test_a_warning_before_a_failing_gate_survives_into_the_summary(store):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def first_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"warn": "looked thin"}

    def second_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"blocked": "review", "detail": "unresolved blocker"}

    document = """
name: gated
phases:
  - name: alpha
    kind: deterministic
    run: step.alpha
    gates: [first_gate, second_gate]
  - name: beta
    kind: deterministic
    run: step.beta
"""
    workflow = _workflow(
        document,
        {
            "step.alpha": alpha,
            "step.beta": beta,
            "first_gate": first_gate,
            "second_gate": second_gate,
        },
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert any("looked thin" in warning for warning in summary.warnings)


@pytest.mark.parametrize("verdict", [False, True, "blocked", 0])
def test_a_gate_returning_neither_none_nor_a_mapping_escalates(store, verdict):
    def alpha(card: str) -> dict[str, Any]:
        return {}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(result: dict[str, Any]) -> Any:
        return verdict

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "escalated"
    assert "mapping" in summary.detail


def test_a_gate_binds_the_phase_result_under_the_phase_name_too(store):
    seen: dict[str, Any] = {}

    def alpha(card: str) -> dict[str, Any]:
        return {"ok": 1}

    def beta(card: str) -> dict[str, Any]:
        return {}

    def alpha_gate(alpha: dict[str, Any], branch: str) -> None:
        seen.update(alpha=alpha, branch=branch)
        return None

    workflow = _workflow(
        GATED, {"step.alpha": alpha, "step.beta": beta, "alpha_gate": alpha_gate}
    )

    engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert seen == {"alpha": {"ok": 1}, "branch": "m1/task-ed77a917"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: the new tests FAIL — gates are never called, so `seen` stays empty and every gated walk reports `done`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/engine.py`, add the warnings field to `_Outcome`, add the gate machinery, and call it from `_run_deterministic`:

```python
@dataclass
class _Outcome:
    """One deterministic phase's verdict, already recorded."""

    ok: bool
    result: Any = None
    detail: str | None = None
    warnings: list[str] = field(default_factory=list)


class _GateFailed(Exception):
    """A gate returned a verdict. Private: it never leaves `_run_deterministic`."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def _gate_values(
    context: Mapping[str, Any], phase_name: str, result: Mapping[str, Any]
) -> dict[str, Any]:
    """The binding table a gate or a `when` predicate sees.

    The result appears twice on purpose: under the phase's name, which is how
    §6 says later phases read it, and under `result`, which is the parameter
    name `plan_check.has_validated_plan(result)` and the shipped gates use.
    """
    return {**context, phase_name: result, "result": result}


def _evaluate_gates(
    phase: DeterministicPhase,
    workflow: Workflow,
    values: Mapping[str, Any],
    warnings: list[str],
) -> None:
    """Run every gate in order; append warnings, raise `_GateFailed` on a verdict."""
    for name in phase.gates:
        gate = workflow.function(name)
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
```

Then rewrite `_run_deterministic`'s body so gates run inside the guarded region and warnings survive every exit:

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
    started_at = clock()
    _record_phase(store, story_id, subtask, phase, "started", started_at, None)
    warnings: list[str] = []
    try:
        function = workflow.function(phase.run)
        kwargs = bind_arguments(
            function, context, phase.args, phase=phase.name, function=phase.run
        )
        result = function(**kwargs)
        if not isinstance(result, Mapping):
            raise EngineError(
                f"returned {type(result).__name__}, but a deterministic phase must "
                "return a mapping: a gate or a later `when` would read anything else "
                "as closed and the run would branch wrongly",
                phase=phase.name,
                function=phase.run,
            )
        _evaluate_gates(phase, workflow, _gate_values(context, phase.name, result), warnings)
    except _GateFailed as failure:
        _record_phase(store, story_id, subtask, phase, "failed", started_at, clock())
        return _Outcome(ok=False, detail=failure.detail, warnings=warnings)
    except Exception as error:
        # Deliberately total. A step is other people's code -- GitError, OSError,
        # anything -- and an exception escaping the walk would leave the subtask
        # recorded `started` forever, which is exactly what resume mistakes for
        # work in flight.
        _record_phase(store, story_id, subtask, phase, "failed", started_at, clock())
        return _Outcome(ok=False, detail=_render_error(error), warnings=warnings)
    _record_phase(store, story_id, subtask, phase, "done", started_at, clock())
    return _Outcome(ok=True, result=result, warnings=warnings)
```

Finally, in `run_subtask`'s loop, collect the warnings before the failure check:

```python
        outcome = _run_deterministic(
            phase, workflow, store, story_id, subtask, context, clock
        )
        summary.warnings.extend(outcome.warnings)
        if not outcome.ok:
            summary.status = "escalated"
            summary.failed_phase = phase.name
            summary.detail = outcome.detail
            _record_subtask_status(store, story_id, subtask, "escalated")
            return summary
        _bind_result(context, phase.name, outcome.result)
        summary.results[phase.name] = outcome.result
        index += 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: 30 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): evaluate deterministic phase gates, warn or escalate"
```

---

## Task 5: `when` / `skip_to` — the plan_check short circuit

**Files:**
- Modify: `src/agent_manager/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `_run_deterministic`, `_Outcome`, `_gate_values`, `bind_arguments` from Tasks 1-4.
- Produces: `_Outcome.skip_to: str | None`; `_skip_target(phase, workflow, values) -> str | None`; `SubtaskSummary.skipped` populated with the names jumped over.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
SKIPPING = """
name: skipping
phases:
  - name: plan_check
    kind: deterministic
    run: plan_check.find
    when: plan_check.has
    skip_to: implement
  - name: spec
    kind: deterministic
    run: step.spec
  - name: plan
    kind: deterministic
    run: step.plan
  - name: implement
    kind: deterministic
    run: step.implement
"""


def _skipping_workflow(has: Any, calls: list[str]):
    def find(card: str) -> dict[str, Any]:
        calls.append("plan_check")
        return {"found": True, "validated": True}

    def make(name: str):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            return {}

        return step

    return _workflow(
        SKIPPING,
        {
            "plan_check.find": find,
            "plan_check.has": has,
            "step.spec": make("spec"),
            "step.plan": make("plan"),
            "step.implement": make("implement"),
        },
    )


def test_a_truthy_when_jumps_to_skip_to(store):
    calls: list[str] = []

    def has(result: dict[str, Any]) -> bool:
        return bool(result.get("validated"))

    workflow = _skipping_workflow(has, calls)

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["plan_check", "implement"]
    assert summary.status == "done"
    assert summary.skipped == ["spec", "plan"]
    assert _projected_phases(store) == [("plan_check", "done"), ("implement", "done")]
    assert set(summary.results) == {"plan_check", "implement"}


def test_a_falsy_when_continues_to_the_next_phase(store):
    calls: list[str] = []

    def has(result: dict[str, Any]) -> bool:
        return False

    workflow = _skipping_workflow(has, calls)

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["plan_check", "spec", "plan", "implement"]
    assert summary.skipped == []
    assert summary.status == "done"


def test_a_raising_when_fails_its_phase_rather_than_not_skipping(store):
    calls: list[str] = []

    def has(result: dict[str, Any]) -> bool:
        raise ValueError("unreadable plan front matter")

    workflow = _skipping_workflow(has, calls)

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["plan_check"]
    assert summary.status == "escalated"
    assert summary.failed_phase == "plan_check"
    assert "unreadable plan front matter" in summary.detail
    assert _projected_phases(store) == [("plan_check", "failed")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: the three new tests FAIL — every phase runs regardless (`calls == ["plan_check", "spec", "plan", "implement"]`), `summary.skipped` is `[]`, and the raising `when` is never called so no escalation happens.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/engine.py`, add `skip_to` to `_Outcome`, add `_skip_target`, evaluate it inside `_run_deterministic`, and act on it in the loop:

```python
@dataclass
class _Outcome:
    """One deterministic phase's verdict, already recorded."""

    ok: bool
    result: Any = None
    detail: str | None = None
    warnings: list[str] = field(default_factory=list)
    skip_to: str | None = None
```

```python
def _skip_target(
    phase: DeterministicPhase, workflow: Workflow, values: Mapping[str, Any]
) -> str | None:
    """The phase to jump to, or `None` to fall through to the next one.

    Both `when` and `skip_to` are required for a jump: `when` alone has nowhere
    to go, and `skip_to` alone would be an unconditional jump the document
    author did not write.
    """
    if phase.when is None or phase.skip_to is None:
        return None
    predicate = workflow.function(phase.when)
    kwargs = bind_arguments(predicate, values, phase=phase.name, function=phase.when)
    return phase.skip_to if predicate(**kwargs) else None
```

Inside `_run_deterministic`, extend the guarded region (immediately after the `_evaluate_gates` call) and carry the target out:

```python
        _evaluate_gates(phase, workflow, _gate_values(context, phase.name, result), warnings)
        skip_to = _skip_target(phase, workflow, _gate_values(context, phase.name, result))
```

and change the success return to:

```python
    _record_phase(store, story_id, subtask, phase, "done", started_at, clock())
    return _Outcome(ok=True, result=result, warnings=warnings, skip_to=skip_to)
```

A `when` that raises is caught by the existing `except Exception` clause — which is the point: the phase is recorded `failed` and the subtask escalates, rather than the walk quietly deciding not to skip and re-planning validated work.

In `run_subtask`'s loop, replace the trailing `index += 1` with the jump:

```python
        _bind_result(context, phase.name, outcome.result)
        summary.results[phase.name] = outcome.result
        if outcome.skip_to is None:
            index += 1
            continue
        target = workflow.phase_names.index(outcome.skip_to)
        summary.skipped.extend(workflow.phase_names[index + 1 : target])
        index = target
```

`workflow.phase_names.index` is safe without a guard: `loader._check_skip_to` already proved at load time that every `skip_to` names a phase strictly later in the document.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: 33 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): honour when/skip_to so a validated plan short-circuits"
```

---

## Task 6: `best_effort` — a warning, never an escalation

**Files:**
- Modify: `src/agent_manager/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `_Outcome`, `SubtaskSummary`, `DeterministicPhase.best_effort` from Tasks 1-5.
- Produces: the `phase.best_effort` branch in `run_subtask`'s failure handling. No new public names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
BEST_EFFORT = """
name: board
phases:
  - name: mark_in_progress
    kind: deterministic
    run: rollup.set_status
    args: { status: in_progress }
    best_effort: true
  - name: work
    kind: deterministic
    run: step.work
  - name: mark_done
    kind: deterministic
    run: rollup.done
    args: { status: done }
    best_effort: true
    gates: [done_gate]
"""


def test_a_best_effort_failure_warns_and_does_not_sink_the_subtask(store):
    calls: list[str] = []

    def set_status(card: str, status: str) -> dict[str, Any]:
        calls.append(f"mark:{status}")
        raise RuntimeError("brd exited 1: board is locked")

    def work(card: str) -> dict[str, Any]:
        calls.append("work")
        return {}

    def done(card: str, status: str) -> dict[str, Any]:
        calls.append(f"done:{status}")
        return {}

    def done_gate(result: dict[str, Any]) -> None:
        return None

    workflow = _workflow(
        BEST_EFFORT,
        {
            "rollup.set_status": set_status,
            "step.work": work,
            "rollup.done": done,
            "done_gate": done_gate,
        },
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert calls == ["mark:in_progress", "work", "done:done"]
    assert summary.status == "done"
    assert summary.failed_phase is None
    assert len(summary.warnings) == 1
    assert "mark_in_progress" in summary.warnings[0]
    assert "board is locked" in summary.warnings[0]
    assert _projected_phases(store) == [
        ("mark_in_progress", "failed"),
        ("work", "done"),
        ("mark_done", "done"),
    ]
    assert store.journal.read()[-1].payload["status"] == "done"


def test_a_best_effort_phase_with_a_failing_gate_only_warns(store):
    def set_status(card: str, status: str) -> dict[str, Any]:
        return {}

    def work(card: str) -> dict[str, Any]:
        return {}

    def done(card: str, status: str) -> dict[str, Any]:
        return {"moved": False}

    def done_gate(result: dict[str, Any]) -> dict[str, str]:
        return {"blocked": "board", "detail": "card is still in_progress"}

    workflow = _workflow(
        BEST_EFFORT,
        {
            "rollup.set_status": set_status,
            "step.work": work,
            "rollup.done": done,
            "done_gate": done_gate,
        },
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"
    assert len(summary.warnings) == 1
    assert "card is still in_progress" in summary.warnings[0]
    assert _projected_phases(store)[-1] == ("mark_done", "failed")


def test_a_best_effort_binding_failure_only_warns(store):
    def set_status(card: str, status: str, missing_thing: str) -> dict[str, Any]:
        return {}

    def work(card: str) -> dict[str, Any]:
        return {}

    def done(card: str, status: str) -> dict[str, Any]:
        return {}

    def done_gate(result: dict[str, Any]) -> None:
        return None

    workflow = _workflow(
        BEST_EFFORT,
        {
            "rollup.set_status": set_status,
            "step.work": work,
            "rollup.done": done,
            "done_gate": done_gate,
        },
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert summary.status == "done"
    assert "missing_thing" in summary.warnings[0]


def test_a_failed_best_effort_phase_contributes_no_result(store):
    def set_status(card: str, status: str) -> dict[str, Any]:
        raise RuntimeError("board is locked")

    def work(card: str) -> dict[str, Any]:
        return {}

    def done(card: str, status: str) -> dict[str, Any]:
        return {}

    def done_gate(result: dict[str, Any]) -> None:
        return None

    workflow = _workflow(
        BEST_EFFORT,
        {
            "rollup.set_status": set_status,
            "step.work": work,
            "rollup.done": done,
            "done_gate": done_gate,
        },
    )

    summary = engine.run_subtask(
        workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
    )

    assert set(summary.results) == {"work", "mark_done"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: the four new tests FAIL — the first best-effort failure escalates and stops the walk, so `summary.status == "escalated"` and `calls == ["mark:in_progress"]`.

- [ ] **Step 3: Write the minimal implementation**

In `run_subtask`, replace the failure branch with one that forks on `phase.best_effort`:

```python
        if not outcome.ok:
            if phase.best_effort:
                # §12: the board write is the one thing allowed to fail quietly.
                # Quietly in the *run*, not in the report -- a run that says
                # `done` while the card never moved is the failure mode this
                # warning exists to prevent.
                summary.warnings.append(
                    f"best-effort phase {phase.name!r} failed: {outcome.detail}"
                )
                index += 1
                continue
            summary.status = "escalated"
            summary.failed_phase = phase.name
            summary.detail = outcome.detail
            _record_subtask_status(store, story_id, subtask, "escalated")
            return summary
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: 37 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): treat a best-effort phase failure as a warning"
```

---

## Task 7: The agent-phase seam, re-entry, and the builtin document

**Files:**
- Modify: `src/agent_manager/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6; `agent_manager.workflow.loader.load_builtin`, `AgentPhase`; `agent_manager.workflow.registry.BUILTIN_FUNCTION_NAMES`.
- Produces:
  - `AgentPhaseRunner = Callable[[AgentPhase, Mapping[str, Any]], Any]`.
  - `run_subtask(..., agent_runner: AgentPhaseRunner | None = None, start_phase: str | None = None, ...)` — the final public signature: `run_subtask(workflow, store, *, story_id, subtask, repo_dir, commands=(), agent_runner=None, start_phase=None, clock=_utcnow) -> SubtaskSummary`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
from agent_manager.workflow.loader import AgentPhase, load_builtin
from agent_manager.workflow.registry import BUILTIN_FUNCTION_NAMES

MIXED = """
name: mixed
phases:
  - name: explore
    kind: agent
    role: explorer
    result: ExploreResult
  - name: work
    kind: deterministic
    run: step.work
"""


def test_an_agent_phase_goes_to_the_injected_runner(store):
    seen: list[tuple[str, str]] = []

    def agent_runner(phase, context):
        seen.append((phase.name, phase.role))
        return {"summary": "explored"}

    def work(card: str, explore: dict[str, Any]) -> dict[str, Any]:
        seen.append(("work", explore["summary"]))
        return {}

    workflow = _workflow(MIXED, {"step.work": work})

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        agent_runner=agent_runner,
    )

    assert seen == [("explore", "explorer"), ("work", "explored")]
    assert summary.results["explore"] == {"summary": "explored"}
    assert _projected_phases(store) == [("work", "done")]


def test_an_agent_phase_with_no_runner_is_a_named_engine_error(store):
    def work(card: str) -> dict[str, Any]:
        return {}

    workflow = _workflow(MIXED, {"step.work": work})

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow, store, story_id=STORY_ID, subtask=_subtask(), repo_dir=REPO
        )

    assert caught.value.phase == "explore"
    assert "agent runner" in str(caught.value)


def test_starting_at_a_named_phase_runs_only_from_there(store):
    calls: list[str] = []

    def make(name: str):
        def step(card: str) -> dict[str, Any]:
            calls.append(name)
            return {}

        return step

    workflow = _workflow(
        THREE_PHASES,
        {"step.alpha": make("alpha"), "step.beta": make("beta"), "step.gamma": make("gamma")},
    )

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        start_phase="beta",
    )

    assert calls == ["beta", "gamma"]
    assert summary.status == "done"
    assert _projected_phases(store) == [("beta", "done"), ("gamma", "done")]


def test_an_unknown_starting_phase_is_an_error_before_anything_is_recorded(store):
    calls: list[str] = []

    def step(card: str) -> dict[str, Any]:
        calls.append("ran")
        return {}

    workflow = _workflow(
        THREE_PHASES, {"step.alpha": step, "step.beta": step, "step.gamma": step}
    )

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=_subtask(),
            repo_dir=REPO,
            start_phase="beeta",
        )

    assert calls == []
    assert "'beeta'" in str(caught.value)
    assert store.connection.execute("SELECT COUNT(*) FROM phases").fetchone()[0] == 0


def test_the_builtin_task_document_walks_against_a_fake_registry(store):
    calls: list[str] = []

    def set_status(card: str, status: str) -> dict[str, Any]:
        calls.append(f"rollup.set_status:{status}")
        return {"card": card, "status": status}

    def ensure(branch: str, base: str, worktree: Any, repo_dir: Any) -> dict[str, Any]:
        calls.append("worktree.ensure")
        return {"created": True}

    def find_plan(card: str) -> dict[str, Any]:
        calls.append("plan_check.find_validated_plan")
        return {"found": False, "validated": False}

    def has_plan(result: dict[str, Any]) -> bool:
        return bool(result.get("validated"))

    def run_suite(commands: list[str], worktree: Any) -> dict[str, Any]:
        calls.append("verify.run_suite")
        return {"passed": True}

    def verification_passed_gate(result: dict[str, Any]) -> None:
        calls.append("verification_passed_gate")
        return None

    def agent_only_gate(**kwargs: Any) -> None:
        raise AssertionError("an agent phase's gate is the agent runner's business")

    functions: dict[str, Any] = {
        "rollup.set_status": set_status,
        "worktree.ensure": ensure,
        "plan_check.find_validated_plan": find_plan,
        "plan_check.has_validated_plan": has_plan,
        "verify.run_suite": run_suite,
        "verification_passed_gate": verification_passed_gate,
        "critic_blockers_gate": agent_only_gate,
        "exploration_output_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
        "verification_gate": agent_only_gate,
    }
    assert sorted(functions) == sorted(BUILTIN_FUNCTION_NAMES)

    def agent_runner(phase: AgentPhase, context: dict[str, Any]) -> dict[str, Any]:
        calls.append(f"agent:{phase.name}")
        return {"role": phase.role}

    workflow = load_builtin("task", _registry(functions))

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        commands=["uv run pytest"],
        agent_runner=agent_runner,
    )

    assert calls == [
        "agent:explore",
        "rollup.set_status:in_progress",
        "worktree.ensure",
        "plan_check.find_validated_plan",
        "agent:spec",
        "agent:validate_spec",
        "agent:plan",
        "agent:validate_plan",
        "agent:implement",
        "agent:review",
        "verify.run_suite",
        "verification_passed_gate",
        "rollup.set_status:done",
    ]
    assert summary.status == "done"
    assert summary.warnings == []
    assert [name for name, _status in _projected_phases(store)] == [
        "mark_in_progress",
        "worktree",
        "plan_check",
        "verify",
        "mark_done",
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: the five new tests FAIL — `run_subtask() got an unexpected keyword argument 'agent_runner'` for three of them; the unknown-start-phase test already passes and the missing-runner test already passes against Task 2's placeholder-free error, which is fine.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/engine.py`, import `AgentPhase` alongside the rest of the loader names:

```python
from agent_manager.workflow.loader import AgentPhase, DeterministicPhase, Workflow
```

Declare the seam next to `Clock`:

```python
AgentPhaseRunner = Callable[["AgentPhase", Mapping[str, Any]], Any]
"""The seam sibling bf8e415b fills: `(phase, context) -> result`.

Everything about an agent phase past this call -- prompt rendering, dispatch,
schema validation, retry, its gates -- belongs to that subtask, not here. This
module only takes the returned result into the context under the phase's name.
"""
```

Add the parameter to `run_subtask`'s signature (between `commands` and `start_phase`):

```python
def run_subtask(
    workflow: Workflow,
    store: Store,
    *,
    story_id: str,
    subtask: models.SubtaskRun,
    repo_dir: Path,
    commands: Sequence[str] = (),
    agent_runner: AgentPhaseRunner | None = None,
    start_phase: str | None = None,
    clock: Clock = _utcnow,
) -> SubtaskSummary:
```

and replace the `if not isinstance(phase, DeterministicPhase):` guard at the top of the loop with the delegation:

```python
        if not isinstance(phase, DeterministicPhase):
            if agent_runner is None:
                raise EngineError(
                    "is an agent phase, but no agent runner was injected",
                    phase=phase.name,
                )
            result = agent_runner(phase, dict(context))
            _bind_result(context, phase.name, result)
            summary.results[phase.name] = result
            index += 1
            continue
```

Note what is deliberately absent: no `record_phase` for an agent phase and no gate evaluation for one. Both are bf8e415b's, which owns the attempt, the retry counter and the `gate_failed` outcome that drives them. The same `_bind_result` guard from Task 2 applies here too: no phase in the shipped document happens to collide, but an agent phase's result is no more entitled to clobber a reserved context key than a deterministic one's.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: 42 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS — every pre-existing test plus the 42 engine tests.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat(engine): delegate agent phases to an injected runner and re-enter at a phase"
```
