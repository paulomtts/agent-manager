<!-- task-pipeline: validated -->
<!-- SPEC (verbatim, prepended per the task workflow) -->

# Add the workflow loader and the function registry

Subtask `2399fdcc-760f-4628-bb45-fb07ccf4cfca`, under story `2143808b` ("The workflow document and the engine"). This narrows the agreed design in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §5, §6 and §14 to one module pair plus the builtin workflow document. No new design decisions are made here.

## 1. Scope

This subtask delivers exactly three source artifacts and their unit tests:

- `src/agent_manager/workflow/loader.py` — parse and validate a workflow YAML document into typed phase objects.
- `src/agent_manager/workflow/registry.py` — map the names appearing in `run`, `when` and `gate` positions to registered Python callables, and refuse to load a document that names a function nobody registered.
- `src/agent_manager/workflow/builtin/task.yaml` — the 12-phase task workflow shipped verbatim as written in the design spec, lines 139-225: `explore`, `mark_in_progress`, `worktree`, `plan_check`, `spec`, `validate_spec`, `plan`, `validate_plan`, `implement`, `review`, `verify`, `mark_done`.

The four reducer names that appear in `run`, `when` or `gate` positions in `task.yaml` — `exploration_output_gate`, `verification_gate`, `review_gate`, `plan_hash_gate` — already exist at `src/agent_manager/steps/reducers.py`, per the design's own package layout (§4, line 127: "`steps/reducers.py` the gates ported from task.js") and per siblings `ef33352b` (done: `exploration_output_gate`, `verification_gate`) and `5ee2ee50` (`review_gate`, `plan_hash_gate`, `count_of`). This subtask does not port or re-implement them; `default_registry()` **imports and registers** these four callables from `agent_manager.steps.reducers` as-is. `count_of`, `short_id` and `stem_of` are internal helpers, not names that appear in any `run`/`when`/`gate` position in `task.yaml` (they are consumed by `review_gate`/`plan_hash_gate` and by branch/artifact-path derivation respectively) — this subtask does not register them, does not port them, and does not create `src/agent_manager/workflow/reducers.py`. `short_id` and the stem naming already live in `src/agent_manager/dag.py` (sibling `01d725d6`, done) and are untouched here. `shell_quote` (`task.js:54`) does not port anywhere — nothing in this program hands a shell string to an agent to run verbatim.

Explicitly **not** in scope, owned by siblings or later milestones:

- `src/agent_manager/steps/reducers.py` and its tests — done, siblings `ef33352b` and `5ee2ee50`. This subtask only imports from it.
- `src/agent_manager/dag.py` (`short_id`, `task_stem`, branch naming) — done, sibling `01d725d6`. This subtask does not touch it and does not need it: no `dag.py` name appears in a `run`/`when`/`gate` position.

- `engine.py` in any form: phase walking, journal and store writes, gate evaluation at runtime, `skip_to`/`when` short-circuiting, `best_effort` semantics (sibling `ed77a917`).
- Input resolution and prompt rendering (§7, sibling `968fba15`).
- Agent dispatch, `result.json` validation, retry/escalate outcomes, fake-adapter engine tests (sibling `bf8e415b`).
- Milestone orchestration — census, levels, parallel stories, `integrate` — and non-Claude harnesses. Deferred to later milestones.
- Any expression language. `when`, `gate` and `run` are only ever names of registered functions; adding an expression language is out of bounds (spec:141-144).

The names appearing in `task.yaml` that belong to step modules a sibling subtask will implement (`rollup.set_status`, `worktree.ensure`, `plan_check.find_validated_plan`, `plan_check.has_validated_plan`, `verify.run_suite`, `critic_blockers_gate`, `verification_passed_gate`) are registered here as placeholders that resolve at load time and raise `NotImplementedError` when called. Resolution is this subtask's contract; execution is not. The placeholders are the seam the sibling replaces, not new behaviour.

## 2. Observable behaviour

**Loader.** `load_workflow(source, registry) -> Workflow` accepts a path or a YAML string and returns a Pydantic-validated `Workflow`: a `name`, a `description`, and an ordered list of phases. A phase is one of two shapes discriminated on `kind`:

- `kind: deterministic` — requires `run`; may carry `args` (a mapping), `best_effort` (bool, default false), `when` (a name), `skip_to` (the name of a later phase), `gates` (a list of names). It must not carry `role`, `inputs`, `result`, `writes` or `retry`.
- `kind: agent` — requires `role`; may carry `inputs` (list of strings), `result` (a model name), `writes` (a path template), `gates`, `retry` (`{max_attempts: int >= 1, on: [schema_invalid|gate_failed]}`), `when`, `skip_to`. It must not carry `run`, `args` or `best_effort`.

Phase names are unique within a document and preserve file order. `skip_to` must name a phase that exists and appears strictly later in the list. The returned object carries, for every name in a `run`, `when` or `gate` position, the resolved callable — so the caller never needs the registry again.

`load_builtin("task") -> Workflow` loads the shipped `workflow/builtin/task.yaml` against the default registry and is the canonical entry point for the engine.

**Registry.** A `FunctionRegistry` holds `name -> callable` entries. `register(name, fn)` rejects a duplicate name rather than silently overwriting it; `resolve(name)` returns the callable or raises `UnknownFunctionError`; `names()` lists what is registered, sorted, for use in error messages. A module-level `default_registry()` returns the registry populated with the four reducers imported from `agent_manager.steps.reducers` (`exploration_output_gate`, `verification_gate`, `review_gate`, `plan_hash_gate`) and the seven step placeholders (`rollup.set_status`, `worktree.ensure`, `plan_check.find_validated_plan`, `plan_check.has_validated_plan`, `verify.run_suite`, `critic_blockers_gate`, `verification_passed_gate`). Registration is explicit — no import-time scanning, no dynamic import of arbitrary dotted paths, so an unregistered name can never be reached by accident.

**The load-time invariant.** This is the point of the subtask: a workflow document naming a function nobody registered fails during `load_workflow`, before any phase runs, before any worktree exists, before any dispatch is billed. There is no lazy resolution and no fallback that defers the failure to execution.

## 3. Error paths

All loader failures raise `WorkflowLoadError` (or a subclass), carrying the workflow name where known, the offending phase name, and the field. Never a bare `KeyError`, `ValidationError` or `AttributeError` escaping to the caller.

- Malformed YAML, or YAML whose top level is not a mapping.
- Missing `name` or `phases`; `phases` empty or not a list.
- A phase missing `kind`, or `kind` not in `{deterministic, agent}`.
- A deterministic phase without `run`; an agent phase without `role`.
- A field belonging to the other kind (`run` on an agent phase, `role` on a deterministic phase, and so on).
- Duplicate phase name.
- `skip_to` naming an unknown phase, itself, or an earlier phase.
- `retry.max_attempts < 1`, or a `retry.on` entry outside the four known outcomes' retryable set (`schema_invalid`, `gate_failed`).
- **Unknown function name** in `run`, `when` or a `gates` entry — `UnknownFunctionError`, reported with the phase, the position (`run`/`when`/`gate`) and the registered names, so the fix is obvious from the message alone. Multiple unknown names in one document are reported together rather than one per load attempt.
- Duplicate registration of a name in the registry — `DuplicateFunctionError`.

## 4. Test list

Per the repo's own tiering (design spec §14, lines 477-492), every test below sits in the **pure functions** tier: deterministic, no network, no filesystem side effects beyond reading a YAML file, no git repository, no brd board, no fake adapter. None belongs to the steps, adapters, engine or end-to-end tiers — those tiers are owned by the sibling subtasks and later milestones. Tests mirror the source layout: `tests/workflow/test_loader.py`, `tests/workflow/test_registry.py`, `tests/workflow/test_builtin_task.py`. There is no `tests/workflow/test_reducers.py` in this subtask (see below).

`test_registry.py` (pure functions tier)
1. `register` then `resolve` returns the same callable.
2. `resolve` of an unregistered name raises `UnknownFunctionError` listing registered names.
3. Duplicate `register` raises `DuplicateFunctionError` and leaves the first binding intact.
4. `default_registry()` contains the four reducer names imported from `steps.reducers` and every name used by builtin `task.yaml` (the four reducers plus the seven step placeholders).
5. `default_registry()` does not contain `shell_quote`.

`test_loader.py` (pure functions tier)
6. A minimal valid document loads; phase order and names match the file.
7. A deterministic phase exposes its resolved `run` callable, its `args` and `best_effort`.
8. An agent phase exposes `role`, `inputs`, `result`, `writes`, `gates` and `retry`.
9. Unknown `run` name raises `UnknownFunctionError` at load time, naming the phase.
10. Unknown `when` name raises at load time.
11. Unknown `gate` name raises at load time.
12. Several unknown names are reported in one error.
13. Malformed YAML raises `WorkflowLoadError`, not a YAML library error.
14. Missing `name`/`phases`, empty `phases`, and a non-mapping top level each raise `WorkflowLoadError`.
15. Bad `kind`, deterministic-without-`run`, agent-without-`role` each raise with the phase named.
16. Cross-kind field (`run` on agent, `role` on deterministic) raises.
17. Duplicate phase name raises.
18. `skip_to` unknown / self / backwards each raise.
19. `retry.max_attempts = 0` and an unknown `retry.on` entry each raise.

`test_builtin_task.py` (pure functions tier)
20. `load_builtin("task")` succeeds against `default_registry()` — the regression guard for the whole invariant.
21. The loaded document has exactly the 12 phases in spec order, with the expected `kind` per phase.
22. `plan_check` carries `when: plan_check.has_validated_plan` and `skip_to: implement`; `mark_in_progress` and `mark_done` are `best_effort` with their `status` args; `explore` carries both its gates and its retry policy.

There is no `test_reducers.py` in this subtask: `exploration_output_gate`, `verification_gate`, `review_gate` and `plan_hash_gate` are already implemented and unit-tested in `tests/steps/test_reducers.py` by siblings `ef33352b` and `5ee2ee50`, and are not re-tested here. This subtask's `test_registry.py` (test 4 above) covers the only thing that is this subtask's to prove: that `default_registry()` resolves those four names to the real, imported callables — not that the callables behave correctly, which is the sibling tests' job.

The `.test.mjs` companions named in §14 as the behavioural specification are not present in this checkout (they live with the plugin referenced by §15); this is background for how the reducer siblings ported their tests and does not bear on this subtask's own loader/registry/builtin tests, which have no JS counterpart.

## 5. Verification

```bash
uv run pytest
```

There is no separate lint or typecheck command (CLAUDE.md).

<!-- END SPEC -->

---

# Workflow loader and function registry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `workflow/loader.py`, `workflow/registry.py` and `workflow/builtin/task.yaml` so the 12-phase task workflow parses into typed phases whose every `run`/`when`/`gate` name is resolved to a registered Python callable at load time, or fails loudly before anything runs.

**Architecture:** `registry.py` is a plain explicit `name -> callable` table plus the module's error hierarchy (`WorkflowLoadError`, `UnknownFunctionError`, `DuplicateFunctionError`); it imports the already-shipped step and reducer callables and registers `NotImplementedError` placeholders for the names whose implementations a sibling subtask still owns. `loader.py` parses YAML with a `SafeLoader` subclass that keeps `on`/`off`/`yes`/`no` as plain strings (PyYAML's default YAML-1.1 resolver would otherwise turn the `retry.on` key into the boolean `True`), validates it into frozen Pydantic models discriminated on `kind`, applies the cross-field rules Pydantic cannot express (unique phase names, forward-only `skip_to`), then resolves every function name against the registry and returns a `Workflow` carrying the resolved callables. Dependency runs one way only: `loader` imports `registry`, never the reverse.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-add-the-workflow-loader-2399fdcc-design.md` (reproduced verbatim above). The parent design is `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §5 (lines 139-225), §6 (255-279), §14 (477-492).

## Global Constraints

- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md).
- Pydantic models for anything validated at a process boundary; plain dataclasses for internal-only state (CLAUDE.md). A workflow document is text off disk, so it is Pydantic.
- Full verification is exactly `uv run pytest`. There is no separate lint or typecheck command (CLAUDE.md).
- No expression language, ever: `when`, `gate` and `run` values are only ever names of registered Python functions (design spec lines 141-144).
- Registration is explicit: no import-time scanning, no dynamic import of a dotted path from the document.
- `shell_quote` does not port and must never be registered (design spec lines 252-253).
- This subtask touches **only** `pyproject.toml`, `src/agent_manager/workflow/**` and `tests/workflow/**`. `engine.py`, prompt rendering, dispatch/retry, `steps/reducers.py`, `steps/worktree.py`, `steps/verify.py`, `steps/plan_check.py` and `dag.py` are owned elsewhere and are imported, never edited.
- Branch `m1/task-add-the-workflow-loader-2399fdcc`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m1/task-add-the-workflow-loader-2399fdcc`. All paths below are relative to that worktree root.

### Deviation from the spec's §1/§2 placeholder list, with evidence

The spec says all seven step-owned names are registered as `NotImplementedError` placeholders. Four of those seven are **already implemented on this branch** and are registered as the real callables instead:

| name | real implementation on this branch | registered as |
|---|---|---|
| `worktree.ensure` | `src/agent_manager/steps/worktree.py:162` | the real function |
| `verify.run_suite` | `src/agent_manager/steps/verify.py:222` | the real function |
| `plan_check.find_validated_plan` | `src/agent_manager/steps/plan_check.py:115` | the real function |
| `plan_check.has_validated_plan` | `src/agent_manager/steps/plan_check.py:164` | the real function |
| `rollup.set_status` | no `steps/rollup.py` exists | placeholder |
| `critic_blockers_gate` | absent from `steps/reducers.py` | placeholder |
| `verification_passed_gate` | absent from `steps/reducers.py` | placeholder |

Registering a `NotImplementedError` placeholder over a working `worktree.ensure` would be a regression, not a seam. The spec's own §2 contract ("`default_registry()` returns the registry populated with …") and its test 4 ("contains … every name used by builtin `task.yaml`") are both satisfied: all eleven names resolve. Task 2 pins this with an explicit test that the four implemented names resolve to the imported functions themselves.

## Review Focus

Five input classes the spec implies but its own test list does not exercise. Each has a test in the task named.

- **A `str` that is a filesystem path, not YAML.** `load_workflow("workflow/builtin/task.yaml", registry)` must raise `WorkflowLoadError` ("top level must be a mapping"), never silently read a file the caller did not name as a `Path`. The `Path`-vs-`str` split is on type, never on what the string looks like. — Task 3.
- **`load_builtin` with a traversing or unknown name.** `load_builtin("../../etc/passwd")` and `load_builtin("milestone")` must raise `WorkflowLoadError` and read nothing outside `workflow/builtin/`. — Task 6.
- **`default_registry()` handing out shared state.** Two calls must return independent registries; registering into one must not raise `DuplicateFunctionError` on the next call or leak into it. A module-level singleton would make the registry poisonable by any caller. — Task 2. (The `_PLACEHOLDERS` name-to-function cache added to `_placeholder` is not this: it interns immutable, side-effect-free placeholder functions by name so their identity is stable across calls, the same way a real imported function already is; it holds no registrations and cannot leak a `register()` call between registries.)
- **`gates` given as a bare string** (`gates: exploration_output_gate` instead of a list). Must raise `WorkflowLoadError` naming the phase, not iterate the string character by character and report 24 unknown one-character function names. — Task 5.
- **An empty or comment-only YAML file.** `yaml.safe_load` returns `None`, which must become a `WorkflowLoadError`, not a `TypeError` from subscripting `None`. — Task 5.
- **The bare key `on`.** PyYAML's default (YAML 1.1) resolver reads an unquoted `on` as the boolean `True`, so `retry: { on: [schema_invalid, gate_failed] }` — written exactly this way in the design spec and shipped verbatim as `builtin/task.yaml` — parses to a dict keyed by `True`, and `RetryPolicy.on` fails validation with "Field required" on every retry-bearing document, including the builtin one. `_YamlLoader` (Task 3) disables implicit bool resolution for `on`/`off`/`yes`/`no`, keeping only `true`/`false`. — Task 3.

---

### Task 1: Project prerequisites and the `FunctionRegistry`

**Files:**
- Modify: `pyproject.toml` (lines 7-15 dependencies; new `[tool.pytest.ini_options]` section)
- Create: `src/agent_manager/workflow/__init__.py`
- Create: `src/agent_manager/workflow/registry.py`
- Test: `tests/workflow/test_registry.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `agent_manager.workflow.registry.Function = Callable[..., Any]`; `WorkflowLoadError(reason: str, *, workflow: str | None = None, phase: str | None = None, field: str | None = None)` with attributes `.reason/.workflow/.phase/.field`; `UnknownFunctionError(reason, *, workflow=None, phase=None, field=None, unknown: Sequence[str] = (), registered: Sequence[str] = ())` with `.unknown: tuple[str, ...]` and `.registered: tuple[str, ...]`; `DuplicateFunctionError(name: str)` with `.name`; `FunctionRegistry()` with `register(name: str, fn: Function) -> None`, `resolve(name: str) -> Function`, `names() -> tuple[str, ...]`, `__contains__(name: object) -> bool`.

- [ ] **Step 1: Add PyYAML and pin pytest's import mode**

Two prerequisites land before any code, because both change how the later steps' commands behave.

`yaml` is not a dependency yet (`pyproject.toml:7-10` lists only `typer` and `pydantic`), and the loader cannot parse a workflow without it.

pytest's default `prepend` import mode derives a test module's name from its basename, so `tests/workflow/test_loader.py` (Task 3) would collide with the existing `tests/roles/test_loader.py` and abort collection with "import file mismatch … unique basename". There are no `__init__.py` files under `tests/` and no pytest configuration at all today. `--import-mode=importlib` derives the module name from the rootdir-relative path instead, which makes the two files coexist without sprinkling `__init__.py` into four sibling-owned test directories.

Edit `pyproject.toml` so the dependency list and a new config section read:

```toml
dependencies = [
    "typer>=0.27.2",
    "pydantic>=2.9",
    "pyyaml>=6.0.2",
]

[dependency-groups]
dev = [
    "pytest>=9.1.1",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
# importlib mode, not the default prepend mode: tests/workflow/test_loader.py and
# tests/roles/test_loader.py share a basename, and prepend mode names test modules
# after their basename alone, so collecting both aborts with "import file mismatch".
addopts = "--import-mode=importlib"
```

- [ ] **Step 2: Sync and prove the existing suite is unharmed**

Run: `uv sync && uv run pytest`
Expected: PASS — every existing test still collected and green under the new import mode, and `uv run python -c "import yaml"` now works.

- [ ] **Step 3: Create the package directories**

```bash
mkdir -p src/agent_manager/workflow tests/workflow
```

Write `src/agent_manager/workflow/__init__.py`:

```python
"""The workflow document: its typed form, and the names it may reference."""
```

(Task 6 fills this with the public re-exports, once every name it exports exists.)

- [ ] **Step 4: Write the failing registry tests**

Create `tests/workflow/test_registry.py`:

```python
"""Pure-functions tier (design spec §14): no filesystem, no network, no git."""

import pytest

from agent_manager.workflow.registry import (
    DuplicateFunctionError,
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
)


def _first() -> str:
    return "first"


def _second() -> str:
    return "second"


def test_register_then_resolve_returns_the_same_callable() -> None:
    registry = FunctionRegistry()
    registry.register("demo.fn", _first)
    assert registry.resolve("demo.fn") is _first


def test_resolve_unknown_name_raises_and_lists_registered_names() -> None:
    registry = FunctionRegistry()
    registry.register("demo.fn", _first)

    with pytest.raises(UnknownFunctionError) as caught:
        registry.resolve("demo.missing")

    message = str(caught.value)
    assert "demo.missing" in message
    assert "demo.fn" in message
    assert caught.value.unknown == ("demo.missing",)
    assert caught.value.registered == ("demo.fn",)


def test_unknown_function_error_is_a_workflow_load_error() -> None:
    registry = FunctionRegistry()
    with pytest.raises(WorkflowLoadError):
        registry.resolve("demo.missing")


def test_duplicate_register_raises_and_keeps_the_first_binding() -> None:
    registry = FunctionRegistry()
    registry.register("demo.fn", _first)

    with pytest.raises(DuplicateFunctionError) as caught:
        registry.register("demo.fn", _second)

    assert caught.value.name == "demo.fn"
    assert registry.resolve("demo.fn") is _first


def test_registering_a_non_callable_raises() -> None:
    registry = FunctionRegistry()
    with pytest.raises(WorkflowLoadError):
        registry.register("demo.fn", "exploration_output_gate")  # type: ignore[arg-type]


def test_names_are_sorted_and_membership_is_cheap() -> None:
    registry = FunctionRegistry()
    registry.register("b.fn", _first)
    registry.register("a.fn", _second)

    assert registry.names() == ("a.fn", "b.fn")
    assert "a.fn" in registry
    assert "c.fn" not in registry
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_manager.workflow.registry'`.

- [ ] **Step 6: Write the registry**

Create `src/agent_manager/workflow/registry.py`:

```python
"""Name -> callable resolution for workflow documents (design §5, lines 141-144).

Every `run`, `when` and `gate` value in a workflow document is the name of a
function registered here. There is no expression language, and no dynamic
import of a dotted path out of the document: the only way a name becomes
reachable is that some line of Python called `register` with it, so a name
nobody registered can never be executed by accident -- `loader.load_workflow`
refuses the whole document instead, before any phase runs.

The error hierarchy lives in this module rather than in `loader.py` because the
dependency runs one way only (`loader` imports `registry`, never the reverse)
and both modules raise the same `WorkflowLoadError` base, so a caller catches
one type for "this workflow could not be loaded".
"""

from collections.abc import Callable, Sequence
from typing import Any

Function = Callable[..., Any]
"""What a registered name resolves to. The engine, not this module, knows what
arguments a given phase's function takes (design §6)."""


class WorkflowLoadError(RuntimeError):
    """Any failure to load, validate or resolve a workflow document.

    Carries the workflow name, the offending phase and the field where each is
    known: an operator reading one journal line has to be able to find the line
    of YAML that is wrong. A bare `KeyError`, `ValidationError` or
    `AttributeError` must never reach a caller of this package.
    """

    def __init__(
        self,
        reason: str,
        *,
        workflow: str | None = None,
        phase: str | None = None,
        field: str | None = None,
    ) -> None:
        self.reason = reason
        self.workflow = workflow
        self.phase = phase
        self.field = field
        parts = []
        if workflow is not None:
            parts.append(f"workflow {workflow!r}")
        if phase is not None:
            parts.append(f"phase {phase!r}")
        if field is not None:
            parts.append(f"field {field!r}")
        prefix = ", ".join(parts)
        super().__init__(f"{prefix}: {reason}" if prefix else reason)


class UnknownFunctionError(WorkflowLoadError):
    """A `run`, `when` or `gate` position names a function nobody registered.

    `unknown` holds every offending name from one document -- reporting them
    one per load attempt would make fixing a document an N-round trip.
    `registered` is what *was* available, so the fix is obvious from the
    message alone.
    """

    def __init__(
        self,
        reason: str,
        *,
        workflow: str | None = None,
        phase: str | None = None,
        field: str | None = None,
        unknown: Sequence[str] = (),
        registered: Sequence[str] = (),
    ) -> None:
        self.unknown = tuple(unknown)
        self.registered = tuple(registered)
        super().__init__(reason, workflow=workflow, phase=phase, field=field)


class DuplicateFunctionError(WorkflowLoadError):
    """A name was registered twice.

    Rejected rather than overwritten: silently rebinding `review_gate` to a
    second implementation is how a run ends up gated by code nobody meant to
    call, and the loser of the race is invisible.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"a function named {name!r} is already registered")


class FunctionRegistry:
    """An explicit `name -> callable` table.

    Deliberately not a decorator that populates a module-level singleton: the
    engine constructs a registry, hands it to the loader, and nothing else can
    mutate what a loaded workflow will call.
    """

    def __init__(self) -> None:
        self._functions: dict[str, Function] = {}

    def register(self, name: str, fn: Function) -> None:
        """Bind `name` to `fn`, refusing to shadow an existing binding."""
        if name in self._functions:
            raise DuplicateFunctionError(name)
        if not callable(fn):
            raise WorkflowLoadError(
                f"{name!r} was registered with {type(fn).__name__}, which is not callable"
            )
        self._functions[name] = fn

    def resolve(self, name: str) -> Function:
        """The callable bound to `name`, or `UnknownFunctionError`."""
        try:
            return self._functions[name]
        except KeyError:
            raise UnknownFunctionError(
                f"no function named {name!r} is registered "
                f"(registered: {', '.join(self.names()) or 'nothing'})",
                unknown=(name,),
                registered=self.names(),
            ) from None

    def names(self) -> tuple[str, ...]:
        """Everything registered, sorted, for error messages and tests."""
        return tuple(sorted(self._functions))

    def __contains__(self, name: object) -> bool:
        return name in self._functions
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_registry.py -v`
Expected: PASS (6 tests).

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock src/agent_manager/workflow/__init__.py src/agent_manager/workflow/registry.py tests/workflow/test_registry.py
git commit -m "feat(workflow): add the function registry and its error hierarchy"
```

---

### Task 2: `default_registry()`

**Files:**
- Modify: `src/agent_manager/workflow/registry.py` (append imports, `_placeholder`, `default_registry`)
- Test: `tests/workflow/test_registry.py` (append)

**Interfaces:**
- Consumes: `FunctionRegistry`, `Function`, `DuplicateFunctionError` from Task 1.
- Produces: `default_registry() -> FunctionRegistry`, a **fresh** registry per call holding exactly these eleven names: `exploration_output_gate`, `verification_gate`, `review_gate`, `plan_hash_gate`, `worktree.ensure`, `verify.run_suite`, `plan_check.find_validated_plan`, `plan_check.has_validated_plan`, `rollup.set_status`, `critic_blockers_gate`, `verification_passed_gate`. Also `BUILTIN_FUNCTION_NAMES: tuple[str, ...]` — the same eleven, sorted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/workflow/test_registry.py`:

```python
from agent_manager.steps import plan_check, reducers, verify, worktree
from agent_manager.workflow.registry import BUILTIN_FUNCTION_NAMES, default_registry

# Every name that appears in a run/when/gate position of builtin/task.yaml
# (design spec lines 146-225).
TASK_YAML_NAMES = (
    "critic_blockers_gate",
    "exploration_output_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_hash_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)


def test_default_registry_holds_exactly_the_names_task_yaml_uses() -> None:
    assert default_registry().names() == TASK_YAML_NAMES
    assert BUILTIN_FUNCTION_NAMES == TASK_YAML_NAMES


def test_default_registry_resolves_the_four_reducers_to_the_real_callables() -> None:
    registry = default_registry()
    assert registry.resolve("exploration_output_gate") is reducers.exploration_output_gate
    assert registry.resolve("verification_gate") is reducers.verification_gate
    assert registry.resolve("review_gate") is reducers.review_gate
    assert registry.resolve("plan_hash_gate") is reducers.plan_hash_gate


def test_default_registry_resolves_implemented_steps_to_the_real_callables() -> None:
    """No placeholder may shadow a step that already exists on this branch."""
    registry = default_registry()
    assert registry.resolve("worktree.ensure") is worktree.ensure
    assert registry.resolve("verify.run_suite") is verify.run_suite
    assert registry.resolve("plan_check.find_validated_plan") is plan_check.find_validated_plan
    assert registry.resolve("plan_check.has_validated_plan") is plan_check.has_validated_plan


def test_placeholders_resolve_at_load_time_and_raise_when_called() -> None:
    registry = default_registry()
    for name in ("rollup.set_status", "critic_blockers_gate", "verification_passed_gate"):
        fn = registry.resolve(name)
        with pytest.raises(NotImplementedError) as caught:
            fn()
        assert name in str(caught.value)


def test_default_registry_does_not_register_shell_quote() -> None:
    """`shell_quote` does not port (design spec lines 252-253)."""
    registry = default_registry()
    assert "shell_quote" not in registry
    with pytest.raises(UnknownFunctionError):
        registry.resolve("shell_quote")


def test_default_registry_returns_an_independent_registry_each_call() -> None:
    """Review focus: a shared singleton would be poisonable by any caller."""
    first = default_registry()
    first.register("test.only", _first)

    second = default_registry()
    assert "test.only" not in second
    assert second.names() == TASK_YAML_NAMES
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_registry.py -v`
Expected: FAIL at collection — `ImportError: cannot import name 'BUILTIN_FUNCTION_NAMES' from 'agent_manager.workflow.registry'`.

- [ ] **Step 3: Implement `default_registry`**

Append to `src/agent_manager/workflow/registry.py` (the module-level imports go with the existing ones at the top of the file):

```python
from agent_manager.steps import plan_check, reducers, verify, worktree
```

and, after `FunctionRegistry`:

```python
_PLACEHOLDERS: dict[str, Function] = {}
"""One function object per placeholder name, shared across every
`default_registry()` call.

`default_registry()` returns a fresh `FunctionRegistry` each time (Review
Focus: a singleton *registry* would be poisonable by any caller), but a
placeholder built fresh per call would mean `resolve(name) is resolve(name)`
holds across two `default_registry()` calls for every real, imported callable
and fails for every placeholder -- exactly the asymmetry
`test_every_resolved_function_is_the_registry_binding` (Task 6) checks for.
Interning by name gives a placeholder as stable an identity as a real import,
without making the registry itself shared, mutable state.
"""


def _placeholder(name: str, owner: str) -> Function:
    """A callable that resolves now and refuses to run.

    The subtask's contract is *resolution*, not execution: the builtin document
    must load whole today, and the step it names is a sibling's to write. The
    seam is deliberate -- the sibling replaces this line with a real import,
    and the name never has to be added to the document later.
    """
    if name in _PLACEHOLDERS:
        return _PLACEHOLDERS[name]

    def _unimplemented(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            f"{name} is registered so the workflow document resolves at load time, "
            f"but its implementation is owned by {owner}"
        )

    _unimplemented.__name__ = name.replace(".", "_")
    _unimplemented.__qualname__ = _unimplemented.__name__
    _PLACEHOLDERS[name] = _unimplemented
    return _unimplemented


BUILTIN_FUNCTION_NAMES = (
    "critic_blockers_gate",
    "exploration_output_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_hash_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)
"""Every name appearing in a run/when/gate position of `builtin/task.yaml`,
sorted. Kept here as data so a test can assert the registry and the document
have not drifted apart."""


def default_registry() -> FunctionRegistry:
    """A fresh registry holding every name `builtin/task.yaml` references.

    A new instance per call on purpose: a module-level singleton is mutable
    global state that any importer could rebind a gate in, and the second call
    would then fail on `DuplicateFunctionError`.

    The four reducers and the four implemented steps are the real, imported
    callables -- not wrappers -- so `resolve(name) is the_function` holds and a
    sibling's bugfix reaches the engine without touching this table. The three
    remaining names have no implementation on this branch (`steps/rollup.py`
    does not exist; `critic_blockers_gate` and `verification_passed_gate` are
    not in `steps/reducers.py`), so they resolve to placeholders.
    """
    registry = FunctionRegistry()

    # Gates ported from task.js -- siblings ef33352b and 5ee2ee50, done.
    registry.register("exploration_output_gate", reducers.exploration_output_gate)
    registry.register("verification_gate", reducers.verification_gate)
    registry.register("review_gate", reducers.review_gate)
    registry.register("plan_hash_gate", reducers.plan_hash_gate)

    # Deterministic steps that already ship on this branch.
    registry.register("worktree.ensure", worktree.ensure)
    registry.register("verify.run_suite", verify.run_suite)
    registry.register("plan_check.find_validated_plan", plan_check.find_validated_plan)
    registry.register("plan_check.has_validated_plan", plan_check.has_validated_plan)

    # Owned by siblings; registered so the document resolves, not so it runs.
    registry.register(
        "rollup.set_status",
        _placeholder("rollup.set_status", "the sibling subtask that adds steps/rollup.py"),
    )
    registry.register(
        "critic_blockers_gate",
        _placeholder("critic_blockers_gate", "the sibling subtask that adds the agent-phase gates"),
    )
    registry.register(
        "verification_passed_gate",
        _placeholder(
            "verification_passed_gate", "the sibling subtask that adds the agent-phase gates"
        ),
    )
    return registry
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_registry.py -v`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/workflow/registry.py tests/workflow/test_registry.py
git commit -m "feat(workflow): populate the default function registry"
```

---

### Task 3: The `Workflow` models and the loader happy path

**Files:**
- Create: `src/agent_manager/workflow/loader.py`
- Test: `tests/workflow/test_loader.py`

**Interfaces:**
- Consumes: `FunctionRegistry`, `Function`, `WorkflowLoadError`, `UnknownFunctionError` from Task 1.
- Produces: frozen Pydantic models `RetryPolicy(max_attempts: int, on: list[Literal["schema_invalid","gate_failed"]])`, `DeterministicPhase(name, kind="deterministic", run, args: dict[str, Any], best_effort: bool, when, skip_to, gates)`, `AgentPhase(name, kind="agent", role, inputs: list[str], result: str | None, writes: str | None, retry: RetryPolicy | None, when, skip_to, gates)`, `Workflow(name, description, phases: list[DeterministicPhase | AgentPhase], functions: dict[str, Function])` with `Workflow.phase(name) -> DeterministicPhase | AgentPhase`, `Workflow.function(name) -> Function` and `Workflow.phase_names -> tuple[str, ...]`; `load_workflow(source: str | Path, registry: FunctionRegistry) -> Workflow`.

- [ ] **Step 1: Write the failing happy-path tests**

Create `tests/workflow/test_loader.py`:

```python
"""Pure-functions tier (design spec §14): no network, no git, no brd board; the
only filesystem touch is reading a YAML file from `tmp_path`."""

from pathlib import Path

import pytest

from agent_manager.workflow.loader import (
    AgentPhase,
    DeterministicPhase,
    Workflow,
    load_workflow,
)
from agent_manager.workflow.registry import (
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
)


def fake_run(**kwargs: object) -> dict[str, object]:
    return {"ran": True}


def fake_when(result: object) -> bool:
    return True


def fake_gate(result: object) -> None:
    return None


def registry_with(*names: str) -> FunctionRegistry:
    """A registry binding every `name` to one of the three fakes above."""
    registry = FunctionRegistry()
    for name in names:
        if name.endswith("_gate"):
            registry.register(name, fake_gate)
        elif name.startswith("when"):
            registry.register(name, fake_when)
        else:
            registry.register(name, fake_run)
    return registry


MINIMAL = """
name: demo
description: two phases, one of each kind.
phases:
  - name: first
    kind: deterministic
    run: demo.run
  - name: second
    kind: agent
    role: coder
"""


def test_minimal_document_loads_with_file_order_preserved() -> None:
    workflow = load_workflow(MINIMAL, registry_with("demo.run"))

    assert isinstance(workflow, Workflow)
    assert workflow.name == "demo"
    assert workflow.description == "two phases, one of each kind."
    assert workflow.phase_names == ("first", "second")
    assert isinstance(workflow.phases[0], DeterministicPhase)
    assert isinstance(workflow.phases[1], AgentPhase)
    assert workflow.phase("second").role == "coder"


def test_deterministic_phase_exposes_its_resolved_run_args_and_best_effort() -> None:
    document = """
name: demo
phases:
  - name: mark_in_progress
    kind: deterministic
    run: rollup.set_status
    args: { status: in_progress }
    best_effort: true
"""
    workflow = load_workflow(document, registry_with("rollup.set_status"))

    phase = workflow.phase("mark_in_progress")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "rollup.set_status"
    assert workflow.function(phase.run) is fake_run
    assert phase.args == {"status": "in_progress"}
    assert phase.best_effort is True
    assert phase.when is None
    assert phase.skip_to is None
    assert phase.gates == []


def test_agent_phase_exposes_role_inputs_result_writes_gates_and_retry() -> None:
    document = """
name: demo
phases:
  - name: explore
    kind: agent
    role: explorer
    inputs: [card, parent_story]
    result: ExploreResult
    writes: docs/superpowers/specs/{stem}.md
    gates: [exploration_output_gate, verification_gate]
    retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }
"""
    workflow = load_workflow(
        document, registry_with("exploration_output_gate", "verification_gate")
    )

    phase = workflow.phase("explore")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "explorer"
    assert phase.inputs == ["card", "parent_story"]
    assert phase.result == "ExploreResult"
    assert phase.writes == "docs/superpowers/specs/{stem}.md"
    assert phase.gates == ["exploration_output_gate", "verification_gate"]
    assert phase.retry is not None
    assert phase.retry.max_attempts == 2
    assert phase.retry.on == ["schema_invalid", "gate_failed"]
    assert workflow.function("exploration_output_gate") is fake_gate


def test_the_bare_word_on_survives_as_a_string_key_not_a_bool() -> None:
    """Review focus: PyYAML's YAML-1.1 resolver reads a bare `on` as `True`.

    `retry.on` is spelled exactly this way in the design spec and in
    `builtin/task.yaml`; if this regresses, every retry-bearing document,
    including the shipped one, fails validation with "Field required"."""
    document = """
name: demo
phases:
  - name: explore
    kind: agent
    role: explorer
    retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }
"""
    workflow = load_workflow(document, registry_with())
    assert workflow.phase("explore").retry.on == ["schema_invalid", "gate_failed"]


def test_when_and_skip_to_resolve_and_survive_the_round_trip() -> None:
    document = """
name: demo
phases:
  - name: plan_check
    kind: deterministic
    run: demo.run
    skip_to: implement
    when: when.has_plan
  - name: implement
    kind: agent
    role: coder
"""
    workflow = load_workflow(document, registry_with("demo.run", "when.has_plan"))

    phase = workflow.phase("plan_check")
    assert phase.when == "when.has_plan"
    assert phase.skip_to == "implement"
    assert workflow.function("when.has_plan") is fake_when


def test_a_path_argument_is_read_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text(MINIMAL, encoding="utf-8")

    workflow = load_workflow(path, registry_with("demo.run"))

    assert workflow.phase_names == ("first", "second")


def test_a_missing_path_raises_a_workflow_load_error(tmp_path: Path) -> None:
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow(tmp_path / "absent.yaml", registry_with())
    assert "absent.yaml" in str(caught.value)


def test_a_string_that_looks_like_a_path_is_parsed_as_yaml_not_read() -> None:
    """Review focus: the Path/str split is on type, never on what a string
    looks like -- guessing would read a file the caller never named."""
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow("src/agent_manager/workflow/builtin/task.yaml", registry_with())
    assert "mapping" in str(caught.value)


def test_a_phase_that_does_not_exist_raises_rather_than_key_error() -> None:
    workflow = load_workflow(MINIMAL, registry_with("demo.run"))
    with pytest.raises(WorkflowLoadError):
        workflow.phase("nope")
    with pytest.raises(UnknownFunctionError):
        workflow.function("nope")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_manager.workflow.loader'`.

- [ ] **Step 3: Write the loader**

Create `src/agent_manager/workflow/loader.py`:

```python
"""Parse and validate a workflow YAML document into typed phases (design §5).

A workflow document is text off disk, so it is validated with pydantic models
per `CLAUDE.md`, not read as loose dicts. Two phase kinds are discriminated on
`kind` (design §6): a deterministic phase the engine calls as `run(ctx)`, and an
agent phase the engine dispatches to a harness. `extra="forbid"` is the point of
validating at all -- a misspelled `best_effor` that is silently ignored ships a
workflow whose failure semantics are not what its author wrote.

Loading also *resolves*: every `run`, `when` and `gate` name is looked up in the
registry here, so a document naming a function nobody registered fails before
any phase runs, before a worktree exists and before a dispatch is billed. There
is no lazy resolution and no fallback that defers the failure to execution.
"""

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_manager.workflow.registry import (
    Function,
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
)


class _YamlLoader(yaml.SafeLoader):
    """`yaml.SafeLoader`, but `on`/`off`/`yes`/`no` are plain strings.

    PyYAML's default resolver follows YAML 1.1, which reads a bare `on` as the
    boolean `True` -- so `retry: { on: [schema_invalid, gate_failed] }`, taken
    verbatim from the design spec and shipped byte-for-byte as `builtin/task.yaml`,
    parses to a dict keyed by `True`, not `"on"`, and `RetryPolicy.on` is
    reported as "Field required" on every document that uses it, including the
    builtin one. Only `true`/`false` remain implicit booleans, which is all
    `best_effort` ever needs and matches YAML 1.2's narrower bool set.
    """


_YamlLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_YamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$", re.X),
    list("tTfF"),
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetryPolicy(_Model):
    """`retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }`.

    `on` is restricted to the two retryable outcomes of design §6 line 278:
    `ok` needs no retry and `harness_error` is not retried at this level.
    """

    max_attempts: int = Field(ge=1, strict=True)
    on: list[Literal["schema_invalid", "gate_failed"]] = Field(min_length=1)


class _PhaseBase(_Model):
    name: str = Field(min_length=1)
    when: str | None = None
    skip_to: str | None = None
    gates: list[str] = Field(default_factory=list)


class DeterministicPhase(_PhaseBase):
    """A phase the engine runs itself: no model, no network (design §6)."""

    kind: Literal["deterministic"]
    run: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    best_effort: bool = False


class AgentPhase(_PhaseBase):
    """A phase the engine dispatches to a harness (design §6).

    `result` is the *name* of a pydantic model and stays a string here: mapping
    it to a class is the agent-dispatch sibling's job, and this module must not
    import the model table to know a document is well formed.
    """

    kind: Literal["agent"]
    role: str = Field(min_length=1)
    inputs: list[str] = Field(default_factory=list)
    result: str | None = None
    writes: str | None = None
    retry: RetryPolicy | None = None


Phase = Annotated[DeterministicPhase | AgentPhase, Field(discriminator="kind")]


class Workflow(_Model):
    """One loaded, validated and fully resolved workflow document.

    `functions` is computed by `load_workflow`, never declared in YAML: it maps
    every name used in a `run`, `when` or `gate` position to the callable the
    registry bound it to, so the engine never needs the registry again.
    """

    name: str = Field(min_length=1)
    description: str = ""
    phases: list[Phase] = Field(min_length=1)
    functions: dict[str, Function] = Field(default_factory=dict)

    @property
    def phase_names(self) -> tuple[str, ...]:
        return tuple(phase.name for phase in self.phases)

    def phase(self, name: str) -> DeterministicPhase | AgentPhase:
        for phase in self.phases:
            if phase.name == name:
                return phase
        raise WorkflowLoadError(
            f"no phase named {name!r} (phases: {', '.join(self.phase_names)})",
            workflow=self.name,
        )

    def function(self, name: str) -> Function:
        try:
            return self.functions[name]
        except KeyError:
            raise UnknownFunctionError(
                f"{name!r} was not resolved when this workflow was loaded",
                workflow=self.name,
                unknown=(name,),
                registered=tuple(sorted(self.functions)),
            ) from None


def load_workflow(source: str | Path, registry: FunctionRegistry) -> Workflow:
    """Parse, validate and resolve one workflow document.

    A `Path` is read from disk; a `str` *is* the YAML document. The split is on
    type and never on what the string looks like: guessing would make
    `load_workflow("name: task")` and `load_workflow("workflows/task.yaml")`
    two spellings of one argument, and a wrong guess reads a file the caller
    never named.
    """
    if isinstance(source, Path):
        text = _read(source)
        origin = str(source)
    else:
        text = source
        origin = "<string>"
    data = _parse(text, origin)
    workflow = _validate(data, origin)
    return _resolve(workflow, registry)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkflowLoadError(f"no such workflow document: {path}") from exc
    except UnicodeDecodeError as exc:
        raise WorkflowLoadError(f"{path} is not valid UTF-8") from exc
    except OSError as exc:
        raise WorkflowLoadError(f"{path} is unreadable: {exc.strerror}") from exc


def _parse(text: str, origin: str) -> Mapping[str, Any]:
    try:
        data = yaml.load(text, Loader=_YamlLoader)
    except yaml.YAMLError as exc:
        # The YAML library's own exception must not escape: callers catch
        # WorkflowLoadError, and a ScannerError says nothing about workflows.
        raise WorkflowLoadError(f"{origin} is not valid YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        # An empty or comment-only document parses to None, which would
        # otherwise fail much later with a TypeError.
        raise WorkflowLoadError(
            f"{origin}: the top level of a workflow document must be a mapping, "
            f"got {type(data).__name__}"
        )
    return data


def _validate(data: Mapping[str, Any], origin: str) -> Workflow:
    try:
        return Workflow.model_validate(dict(data))
    except ValidationError as exc:
        raise _load_error_from(exc, origin, data) from exc


def _load_error_from(
    exc: ValidationError, origin: str, data: Mapping[str, Any]
) -> WorkflowLoadError:
    """Turn pydantic's first complaint into an operator-readable failure."""
    first = exc.errors()[0]
    loc = tuple(first.get("loc", ()))
    return WorkflowLoadError(
        f"{origin}: {first.get('msg', 'did not validate')}"
        + (f" ({exc.error_count()} validation errors in total)" if exc.error_count() > 1 else ""),
        workflow=data.get("name") if isinstance(data.get("name"), str) else None,
        phase=_phase_name_at(loc, data),
        field=".".join(str(part) for part in loc) or None,
    )


def _phase_name_at(loc: tuple[Any, ...], data: Mapping[str, Any]) -> str | None:
    """The `name:` of the phase a pydantic error location points into.

    The index alone (`phases.3`) sends a reader counting list entries; the name
    is what they search the file for. A phase with no usable name falls back to
    its position, which is all there is to say about it.
    """
    if len(loc) < 2 or loc[0] != "phases" or not isinstance(loc[1], int):
        return None
    phases = data.get("phases")
    if not isinstance(phases, list) or loc[1] >= len(phases):
        return None
    entry = phases[loc[1]]
    name = entry.get("name") if isinstance(entry, Mapping) else None
    return name if isinstance(name, str) and name else f"#{loc[1]}"


def _function_names(phase: DeterministicPhase | AgentPhase) -> Iterator[tuple[str, str]]:
    """Every `(position, name)` this phase references."""
    if isinstance(phase, DeterministicPhase):
        yield "run", phase.run
    if phase.when is not None:
        yield "when", phase.when
    for gate in phase.gates:
        yield "gate", gate


def _resolve(workflow: Workflow, registry: FunctionRegistry) -> Workflow:
    functions: dict[str, Function] = {}
    for phase in workflow.phases:
        for _position, name in _function_names(phase):
            if name not in functions:
                functions[name] = registry.resolve(name)
    return workflow.model_copy(update={"functions": functions})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_loader.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS — in particular `tests/roles/test_loader.py` and `tests/workflow/test_loader.py` are both collected, proving the import-mode change from Task 1.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/workflow/loader.py tests/workflow/test_loader.py
git commit -m "feat(workflow): parse and validate a workflow document into typed phases"
```

---

### Task 4: Load-time failure on unknown function names

This is the crux of the subtask. Task 3 resolves names one at a time and lets the registry's own error escape, which says nothing about *which phase* named the function and reports only the first offender. Both are fixed here.

**Files:**
- Modify: `src/agent_manager/workflow/loader.py` (replace `_resolve`)
- Test: `tests/workflow/test_loader.py` (append)

**Interfaces:**
- Consumes: `_function_names`, `Workflow`, `UnknownFunctionError` from Task 3.
- Produces: no new public names. `load_workflow` now raises a single `UnknownFunctionError` whose `.unknown` is every offending name in document order (deduplicated) and whose message names each `(phase, position, name)` triple.

- [ ] **Step 1: Write the failing tests**

Append to `tests/workflow/test_loader.py`:

```python
def test_unknown_run_name_fails_at_load_time_naming_the_phase() -> None:
    document = """
name: demo
phases:
  - name: worktree
    kind: deterministic
    run: worktree.ensure
"""
    with pytest.raises(UnknownFunctionError) as caught:
        load_workflow(document, registry_with("demo.run"))

    message = str(caught.value)
    assert "worktree" in message
    assert "worktree.ensure" in message
    assert "run" in message
    assert "demo.run" in message  # what WAS registered
    assert caught.value.unknown == ("worktree.ensure",)


def test_unknown_when_name_fails_at_load_time() -> None:
    document = """
name: demo
phases:
  - name: plan_check
    kind: deterministic
    run: demo.run
    when: plan_check.has_validated_plan
    skip_to: implement
  - name: implement
    kind: agent
    role: coder
"""
    with pytest.raises(UnknownFunctionError) as caught:
        load_workflow(document, registry_with("demo.run"))

    assert caught.value.unknown == ("plan_check.has_validated_plan",)
    assert "when" in str(caught.value)
    assert "plan_check" in str(caught.value)


def test_unknown_gate_name_fails_at_load_time() -> None:
    document = """
name: demo
phases:
  - name: review
    kind: agent
    role: reviewer
    gates: [review_gate, plan_hash_gate]
"""
    with pytest.raises(UnknownFunctionError) as caught:
        load_workflow(document, registry_with("review_gate"))

    assert caught.value.unknown == ("plan_hash_gate",)
    assert "gate" in str(caught.value)
    assert "review" in str(caught.value)


def test_every_unknown_name_is_reported_in_one_error() -> None:
    document = """
name: demo
phases:
  - name: first
    kind: deterministic
    run: missing.run
    when: missing.when
  - name: second
    kind: agent
    role: coder
    gates: [missing_gate, missing_gate]
"""
    with pytest.raises(UnknownFunctionError) as caught:
        load_workflow(document, registry_with())

    assert caught.value.unknown == ("missing.run", "missing.when", "missing_gate")
    message = str(caught.value)
    assert "'first'" in message
    assert "'second'" in message
    assert caught.value.workflow == "demo"


def test_resolution_happens_before_any_function_is_called() -> None:
    """The invariant: nothing the document names runs during a load."""
    calls: list[str] = []

    def spy(*args: object, **kwargs: object) -> None:
        calls.append("called")

    registry = FunctionRegistry()
    registry.register("demo.run", spy)
    load_workflow(MINIMAL, registry)

    assert calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_loader.py -v`
Expected: FAIL — `test_unknown_run_name_fails_at_load_time_naming_the_phase` fails on `assert "run" in message`/the phase name (the registry's message carries neither), and `test_every_unknown_name_is_reported_in_one_error` fails because `.unknown` holds only `("missing.run",)`.

- [ ] **Step 3: Replace `_resolve` with the aggregating version**

In `src/agent_manager/workflow/loader.py`, replace the `_resolve` function written in Task 3 with:

```python
def _resolve(workflow: Workflow, registry: FunctionRegistry) -> Workflow:
    """Bind every referenced name, or refuse the whole document.

    Every offender in the document is collected before raising: reporting one
    per load attempt turns fixing a workflow into an N-round trip, and the
    engine only ever loads once, at the top of a run.
    """
    functions: dict[str, Function] = {}
    missing: list[tuple[str, str, str]] = []
    for phase in workflow.phases:
        for position, name in _function_names(phase):
            if name in functions:
                continue
            if name in registry:
                functions[name] = registry.resolve(name)
            else:
                missing.append((phase.name, position, name))
    if missing:
        detail = "; ".join(
            f"phase {phase!r} {position} {name!r}" for phase, position, name in missing
        )
        unknown = tuple(dict.fromkeys(name for _phase, _position, name in missing))
        raise UnknownFunctionError(
            f"names {len(unknown)} function(s) nobody registered: {detail} "
            f"(registered: {', '.join(registry.names()) or 'nothing'})",
            workflow=workflow.name,
            phase=missing[0][0],
            field=missing[0][1],
            unknown=unknown,
            registered=registry.names(),
        )
    return workflow.model_copy(update={"functions": functions})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_loader.py -v`
Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/workflow/loader.py tests/workflow/test_loader.py
git commit -m "feat(workflow): fail at load time on every unknown function name"
```

---

### Task 5: The structural error paths

**Files:**
- Modify: `src/agent_manager/workflow/loader.py` (add `_check_phase_names`, `_check_skip_to`, call both from `_validate`)
- Test: `tests/workflow/test_loader.py` (append)

**Interfaces:**
- Consumes: `Workflow`, `WorkflowLoadError`, `_load_error_from` from Task 3.
- Produces: no new public names. `load_workflow` now raises `WorkflowLoadError` for duplicate phase names and for a `skip_to` that is unknown, self-referential or backwards.

- [ ] **Step 1: Write the failing tests**

Append to `tests/workflow/test_loader.py`:

```python
def test_malformed_yaml_raises_a_workflow_load_error_not_a_yaml_error() -> None:
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow("name: demo\nphases: [ - broken", registry_with())
    assert "not valid YAML" in str(caught.value)


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("phases:\n  - name: a\n    kind: agent\n    role: coder\n", id="no_name"),
        pytest.param("name: demo\n", id="no_phases"),
        pytest.param("name: demo\nphases: []\n", id="empty_phases"),
        pytest.param("name: demo\nphases: not-a-list\n", id="phases_not_a_list"),
        pytest.param("- name: demo\n", id="top_level_list"),
        pytest.param("just a string\n", id="top_level_scalar"),
        pytest.param("", id="empty_document"),
        pytest.param("# only a comment\n", id="comment_only"),
    ],
)
def test_structurally_broken_documents_raise(document: str) -> None:
    """The last three are the review-focus case: safe_load returns None."""
    with pytest.raises(WorkflowLoadError):
        load_workflow(document, registry_with())


@pytest.mark.parametrize(
    "phase_body",
    [
        pytest.param("kind: wizard\n    run: demo.run", id="unknown_kind"),
        pytest.param("run: demo.run", id="no_kind"),
        pytest.param("kind: deterministic", id="deterministic_without_run"),
        pytest.param("kind: agent", id="agent_without_role"),
        pytest.param("kind: agent\n    role: coder\n    run: demo.run", id="run_on_agent"),
        pytest.param("kind: agent\n    role: coder\n    args: { a: 1 }", id="args_on_agent"),
        pytest.param(
            "kind: agent\n    role: coder\n    best_effort: true", id="best_effort_on_agent"
        ),
        pytest.param("kind: deterministic\n    run: demo.run\n    role: coder", id="role_on_det"),
        pytest.param(
            "kind: deterministic\n    run: demo.run\n    inputs: [card]", id="inputs_on_det"
        ),
        pytest.param(
            "kind: deterministic\n    run: demo.run\n    result: PlanResult", id="result_on_det"
        ),
        pytest.param(
            "kind: deterministic\n    run: demo.run\n    writes: docs/x.md", id="writes_on_det"
        ),
        pytest.param(
            "kind: deterministic\n    run: demo.run\n    retry: { max_attempts: 2, on: [gate_failed] }",
            id="retry_on_det",
        ),
        pytest.param("kind: deterministic\n    run: demo.run\n    typo: 1", id="unknown_field"),
        pytest.param(
            "kind: agent\n    role: coder\n    gates: exploration_output_gate", id="gates_bare_str"
        ),
        pytest.param(
            "kind: agent\n    role: coder\n    retry: { max_attempts: 0, on: [gate_failed] }",
            id="zero_attempts",
        ),
        pytest.param(
            "kind: agent\n    role: coder\n    retry: { max_attempts: 2, on: [harness_error] }",
            id="unretryable_outcome",
        ),
        pytest.param(
            "kind: agent\n    role: coder\n    retry: { max_attempts: 2, on: [] }",
            id="empty_retry_on",
        ),
    ],
)
def test_broken_phases_raise_with_the_phase_named(phase_body: str) -> None:
    """`gates_bare_str` is the review-focus case: a bare string must be
    rejected, not iterated character by character."""
    document = f"name: demo\nphases:\n  - name: broken\n    {phase_body}\n"
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow(document, registry_with("demo.run", "exploration_output_gate"))
    assert caught.value.phase == "broken"
    assert "broken" in str(caught.value)


def test_duplicate_phase_name_raises() -> None:
    document = """
name: demo
phases:
  - name: twice
    kind: deterministic
    run: demo.run
  - name: twice
    kind: agent
    role: coder
"""
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow(document, registry_with("demo.run"))
    assert caught.value.phase == "twice"
    assert "duplicate" in str(caught.value)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        pytest.param("nowhere", "not a phase", id="unknown"),
        pytest.param("second", "itself", id="self"),
        pytest.param("first", "earlier", id="backwards"),
    ],
)
def test_bad_skip_to_raises(target: str, expected: str) -> None:
    document = f"""
name: demo
phases:
  - name: first
    kind: deterministic
    run: demo.run
  - name: second
    kind: deterministic
    run: demo.run
    skip_to: {target}
"""
    with pytest.raises(WorkflowLoadError) as caught:
        load_workflow(document, registry_with("demo.run"))
    assert caught.value.phase == "second"
    assert caught.value.field == "skip_to"
    assert expected in str(caught.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_loader.py -v`
Expected: FAIL — `test_duplicate_phase_name_raises` and all three `test_bad_skip_to_raises` cases fail (no error is raised at all: pydantic cannot express either rule). The parametrized pydantic cases should already pass.

- [ ] **Step 3: Add the cross-field checks**

In `src/agent_manager/workflow/loader.py`, replace `_validate` with:

```python
def _validate(data: Mapping[str, Any], origin: str) -> Workflow:
    try:
        workflow = Workflow.model_validate(dict(data))
    except ValidationError as exc:
        raise _load_error_from(exc, origin, data) from exc
    _check_phase_names(workflow)
    _check_skip_to(workflow)
    return workflow
```

and add, after `_phase_name_at`:

```python
def _check_phase_names(workflow: Workflow) -> None:
    """Phase names are the document's only identifiers.

    Two phases sharing one name makes `skip_to`, the journal and every recorded
    phase result ambiguous, and the duplicate would silently win or lose
    depending on which lookup ran.
    """
    seen: set[str] = set()
    for phase in workflow.phases:
        if phase.name in seen:
            raise WorkflowLoadError(
                "duplicate phase name", workflow=workflow.name, phase=phase.name, field="name"
            )
        seen.add(phase.name)


def _check_skip_to(workflow: Workflow) -> None:
    """`skip_to` may only jump forward.

    A backwards or self jump is a loop the engine would walk forever, and an
    unknown target is a typo that would otherwise only surface at the moment
    the `when` gate first opens -- possibly hours into a run.
    """
    order = {phase.name: index for index, phase in enumerate(workflow.phases)}
    for index, phase in enumerate(workflow.phases):
        target = phase.skip_to
        if target is None:
            continue
        if target not in order:
            raise WorkflowLoadError(
                f"skip_to names {target!r}, which is not a phase in this workflow "
                f"(phases: {', '.join(workflow.phase_names)})",
                workflow=workflow.name,
                phase=phase.name,
                field="skip_to",
            )
        if order[target] <= index:
            where = "itself" if target == phase.name else "earlier in the document"
            raise WorkflowLoadError(
                f"skip_to must name a later phase, but {target!r} is {where}",
                workflow=workflow.name,
                phase=phase.name,
                field="skip_to",
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_loader.py -v`
Expected: PASS (all cases, 44 tests in this file: 14 from Tasks 3-4 plus the 30 added here).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/workflow/loader.py tests/workflow/test_loader.py
git commit -m "feat(workflow): reject duplicate phase names and non-forward skip_to"
```

---

### Task 6: Ship `builtin/task.yaml` and `load_builtin`

**Files:**
- Create: `src/agent_manager/workflow/builtin/task.yaml`
- Modify: `src/agent_manager/workflow/loader.py` (add `BUILTIN_DIR`, `builtin_path`, `load_builtin`)
- Modify: `src/agent_manager/workflow/__init__.py` (public re-exports)
- Test: `tests/workflow/test_builtin_task.py`

**Interfaces:**
- Consumes: `load_workflow` (Tasks 3-5), `default_registry` (Task 2).
- Produces: `BUILTIN_DIR: Path`; `builtin_path(name: str) -> Path`; `load_builtin(name: str, registry: FunctionRegistry | None = None) -> Workflow` (a `None` registry means `default_registry()`); and `agent_manager.workflow` re-exporting `AgentPhase`, `DeterministicPhase`, `DuplicateFunctionError`, `FunctionRegistry`, `RetryPolicy`, `UnknownFunctionError`, `Workflow`, `WorkflowLoadError`, `default_registry`, `load_builtin`, `load_workflow`.

- [ ] **Step 1: Write the failing builtin tests**

Create `tests/workflow/test_builtin_task.py`:

```python
"""Pure-functions tier (design spec §14): reads one packaged YAML file and
resolves names against the default registry. Nothing is executed."""

import pytest

from agent_manager.workflow import load_builtin
from agent_manager.workflow.loader import (
    AgentPhase,
    DeterministicPhase,
    builtin_path,
)
from agent_manager.workflow.registry import WorkflowLoadError, default_registry

# Design spec lines 146-225, in file order.
EXPECTED_PHASES = (
    ("explore", "agent"),
    ("mark_in_progress", "deterministic"),
    ("worktree", "deterministic"),
    ("plan_check", "deterministic"),
    ("spec", "agent"),
    ("validate_spec", "agent"),
    ("plan", "agent"),
    ("validate_plan", "agent"),
    ("implement", "agent"),
    ("review", "agent"),
    ("verify", "deterministic"),
    ("mark_done", "deterministic"),
)


def test_builtin_task_loads_against_the_default_registry() -> None:
    """The regression guard for the whole subtask: every name resolves."""
    workflow = load_builtin("task")
    assert workflow.name == "task"
    assert workflow.description


def test_builtin_task_has_the_twelve_phases_in_spec_order() -> None:
    workflow = load_builtin("task")
    assert tuple((phase.name, phase.kind) for phase in workflow.phases) == EXPECTED_PHASES


def test_plan_check_skips_forward_to_implement_when_a_plan_exists() -> None:
    phase = load_builtin("task").phase("plan_check")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "plan_check.find_validated_plan"
    assert phase.when == "plan_check.has_validated_plan"
    assert phase.skip_to == "implement"


@pytest.mark.parametrize(
    ("phase_name", "status"),
    [("mark_in_progress", "in_progress"), ("mark_done", "done")],
)
def test_the_rollup_phases_are_best_effort_with_their_status(
    phase_name: str, status: str
) -> None:
    phase = load_builtin("task").phase(phase_name)
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "rollup.set_status"
    assert phase.args == {"status": status}
    assert phase.best_effort is True


def test_explore_carries_both_gates_and_its_retry_policy() -> None:
    phase = load_builtin("task").phase("explore")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "explorer"
    assert phase.inputs == ["card", "parent_story", "repo_docs", "verification"]
    assert phase.result == "ExploreResult"
    assert phase.gates == ["exploration_output_gate", "verification_gate"]
    assert phase.retry is not None
    assert phase.retry.max_attempts == 2
    assert phase.retry.on == ["schema_invalid", "gate_failed"]


def test_review_carries_both_of_its_gates() -> None:
    phase = load_builtin("task").phase("review")
    assert isinstance(phase, AgentPhase)
    assert phase.gates == ["review_gate", "plan_hash_gate"]
    assert phase.inputs == ["branch", "base_branch", "plan_path"]


def test_every_resolved_function_is_the_registry_binding() -> None:
    workflow = load_builtin("task")
    registry = default_registry()
    assert sorted(workflow.functions) == sorted(registry.names())
    for name, fn in workflow.functions.items():
        assert fn is registry.resolve(name)


def test_an_unknown_builtin_name_raises() -> None:
    with pytest.raises(WorkflowLoadError) as caught:
        load_builtin("milestone")
    assert "milestone" in str(caught.value)


@pytest.mark.parametrize("name", ["../../etc/passwd", "a/b", "", ".", "..", "task/"])
def test_a_traversing_builtin_name_is_refused_before_any_read(name: str) -> None:
    """Review focus: nothing outside `workflow/builtin/` is ever opened."""
    with pytest.raises(WorkflowLoadError):
        builtin_path(name)


def test_builtin_path_stays_inside_the_builtin_directory() -> None:
    path = builtin_path("task")
    assert path.name == "task.yaml"
    assert path.parent.name == "builtin"
    assert path.is_file()
```

Note: `test_every_resolved_function_is_the_registry_binding` asserts the document uses *all eleven* registered names — true for `task.yaml`, and the guard that keeps `default_registry()` from accumulating names nothing references.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_builtin_task.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_builtin' from 'agent_manager.workflow'`.

- [ ] **Step 3: Ship `task.yaml` verbatim**

```bash
mkdir -p src/agent_manager/workflow/builtin
```

Create `src/agent_manager/workflow/builtin/task.yaml` — the document from design spec lines 146-225, byte for byte:

```yaml
name: task
description: Drive one subtask card end to end in its own worktree.

phases:
  - name: explore
    kind: agent
    role: explorer
    inputs: [card, parent_story, repo_docs, verification]
    result: ExploreResult
    gates: [exploration_output_gate, verification_gate]
    retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }

  - name: mark_in_progress
    kind: deterministic
    run: rollup.set_status
    args: { status: in_progress }
    best_effort: true

  - name: worktree
    kind: deterministic
    run: worktree.ensure

  - name: plan_check
    kind: deterministic
    run: plan_check.find_validated_plan
    skip_to: implement
    when: plan_check.has_validated_plan

  - name: spec
    kind: agent
    role: spec_author
    inputs: [card, explore]
    writes: docs/superpowers/specs/{stem}.md

  - name: validate_spec
    kind: agent
    role: critic
    inputs: [card, spec_path]
    result: CriticResult
    gates: [critic_blockers_gate]

  - name: plan
    kind: agent
    role: planner
    inputs: [spec_path]
    result: PlanResult
    writes: docs/superpowers/plans/{stem}.md

  - name: validate_plan
    kind: agent
    role: critic
    inputs: [spec_path, plan_path]
    result: CriticResult
    gates: [critic_blockers_gate]

  - name: implement
    kind: agent
    role: coder
    inputs: [plan_path, spec_path, branch, base_branch]
    result: ImplementResult

  - name: review
    kind: agent
    role: reviewer
    inputs: [branch, base_branch, plan_path]
    result: ReviewResult
    gates: [review_gate, plan_hash_gate]

  - name: verify
    kind: deterministic
    run: verify.run_suite
    gates: [verification_passed_gate]

  - name: mark_done
    kind: deterministic
    run: rollup.set_status
    args: { status: done }
    best_effort: true
```

- [ ] **Step 4: Add `load_builtin`**

Append to `src/agent_manager/workflow/loader.py`:

```python
BUILTIN_DIR = Path(__file__).parent / "builtin"
"""The packaged workflow documents, resolved relative to this module the same
way `roles.loader.bundles_dir` resolves role bundles."""


def builtin_path(name: str) -> Path:
    """The path of a shipped workflow document, refusing anything but a name.

    Validated *before* it is joined: `"../coder"` names a real, loadable file
    one level up on plenty of layouts, and joining first would hand the engine
    a workflow from outside the package.
    """
    if name in {"", ".", ".."} or name != Path(name).name:
        raise WorkflowLoadError(
            f"{name!r} is not a builtin workflow name "
            "(a builtin is named by a plain name, not a path)"
        )
    return BUILTIN_DIR / f"{name}.yaml"


def load_builtin(name: str, registry: FunctionRegistry | None = None) -> Workflow:
    """Load a shipped workflow against the default registry.

    The canonical entry point for the engine: `load_builtin("task")` is the
    12-phase task workflow of design §5, fully resolved. `registry` exists so a
    test or an embedder can substitute its own table.
    """
    path = builtin_path(name)
    if not path.is_file():
        raise WorkflowLoadError(
            f"no builtin workflow named {name!r} ({path}); "
            f"builtins: {', '.join(sorted(p.stem for p in BUILTIN_DIR.glob('*.yaml'))) or 'none'}"
        )
    return load_workflow(path, default_registry() if registry is None else registry)
```

and extend the existing registry import at the top of `loader.py` to bring in `default_registry`:

```python
from agent_manager.workflow.registry import (
    Function,
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
    default_registry,
)
```

- [ ] **Step 5: Re-export the public surface**

Replace `src/agent_manager/workflow/__init__.py` with:

```python
"""The workflow document: its typed form, and the names it may reference.

`load_builtin("task")` is the engine's entry point (design §5). Nothing here
executes a phase -- the engine, the prompt renderer and the harness dispatch
are sibling modules that consume what this package returns.
"""

from agent_manager.workflow.loader import (
    AgentPhase,
    DeterministicPhase,
    RetryPolicy,
    Workflow,
    builtin_path,
    load_builtin,
    load_workflow,
)
from agent_manager.workflow.registry import (
    DuplicateFunctionError,
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
    default_registry,
)

__all__ = [
    "AgentPhase",
    "DeterministicPhase",
    "DuplicateFunctionError",
    "FunctionRegistry",
    "RetryPolicy",
    "UnknownFunctionError",
    "Workflow",
    "WorkflowLoadError",
    "builtin_path",
    "default_registry",
    "load_builtin",
    "load_workflow",
]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_builtin_task.py -v`
Expected: PASS (16 tests).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 8: Confirm the YAML ships in the wheel**

Run: `uv build && uv run python -c "import zipfile,glob; print([n for n in zipfile.ZipFile(sorted(glob.glob('dist/*.whl'))[-1]).namelist() if n.endswith('task.yaml')])"`
Expected: `['agent_manager/workflow/builtin/task.yaml']` — hatchling includes package data by default, and a wheel without it would make `load_builtin` fail only once installed.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/workflow/builtin/task.yaml src/agent_manager/workflow/loader.py src/agent_manager/workflow/__init__.py tests/workflow/test_builtin_task.py
git commit -m "feat(workflow): ship builtin/task.yaml and load_builtin"
```

```bash
rm -rf dist
```

(Step 8 writes `dist/`; it is build output, not source, and must not be committed.)

---

## Spec coverage map

| Spec item | Task |
|---|---|
| §1 `workflow/loader.py` | 3, 4, 5, 6 |
| §1 `workflow/registry.py` | 1, 2 |
| §1 `workflow/builtin/task.yaml` verbatim | 6 |
| §1 four reducers imported, not re-implemented | 2 |
| §1 no `workflow/reducers.py`, no `count_of`/`short_id`/`stem_of` registration | 2 (registry holds exactly the eleven names) |
| §1 `shell_quote` never registered | 2 |
| §1 step-owned names resolve but do not run | 2 (three placeholders; four real, per the deviation table) |
| §2 loader: path or YAML string, typed phases, order, uniqueness, forward `skip_to`, resolved callables | 3, 5 |
| §2 registry: `register`/`resolve`/`names`/`default_registry` | 1, 2 |
| §2 the load-time invariant | 4 |
| §3 every listed error path | 3 (read/parse), 4 (unknown names), 5 (structure/retry) |
| §4 tests 1-5 | 1, 2 |
| §4 tests 6-8 | 3 |
| §4 tests 9-12 | 4 |
| §4 tests 13-19 | 5 |
| §4 tests 20-22 | 6 |
| §5 `uv run pytest` | every task's final step |
