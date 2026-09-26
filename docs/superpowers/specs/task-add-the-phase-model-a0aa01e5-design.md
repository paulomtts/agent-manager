# Subtask a0aa01e5: Add the phase model with `validate()` and `digest()`

Parent story: 09e1183f "The declared phase model" (milestone 84c3b532). Narrows plan Task 1.1 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:65-264`) and milestone spec §5 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:175-217`), realizing decisions G2 and G3 (same spec, lines 64-72).

## Scope

- Create `src/agent_manager/workflow/phases.py`: the workflow as declared, frozen Python data. The plan's reference implementation (plan lines 141-264) is the agreed shape.
- Modify `src/agent_manager/prompt.py`: add `INPUT_NAMES: frozenset[str] = frozenset(_TABLE)` with a one-line docstring. Put it directly after `_TABLE` and its docstring (after line 288, before `INPUT_PRODUCERS`). Nothing else in `prompt.py` changes.
- Create `tests/workflow/test_phases.py`.

No other file changes. In particular, this subtask does not touch `workflow/registry.py`, `steps/reducers.py`, `workflow/builtin/*.yaml`, the engine, or `runtime/`.

## Public surface

- `WorkflowError(ValueError)`: constructed as `WorkflowError(message, *, phase=None)`. It exposes `.phase: str | None`. When a phase is given, `str()` is `"phase '<name>': <message>"`.
- `Goto(phase: str, max_loops: int = 1)`, a frozen dataclass.
- `Retry(max_attempts: int, on: tuple[str, ...])`, a frozen dataclass.
- `Step(name, run, args={}, gates=(), best_effort=False, when=None, skip_to=None)`, a frozen dataclass. `args` is a mapping (default factory `dict`). `gates` is a tuple of callables. `when` is a callable or `None`.
- `AgentPhase(name, role, inputs, result, gates=(), retry=None, writes=None, timeout=timedelta(minutes=30), on_fail=None)`, a frozen dataclass. `inputs` is a tuple of names. `result` is a pydantic `BaseModel` subclass or `None`. `on_fail` is a `Goto` or `None`.
- `Workflow(name, phases: tuple[Step | AgentPhase, ...])`, a frozen dataclass, with:
  - `.phase_names -> tuple[str, ...]`: the phase names in declared order.
  - `.phase(name)`: returns the phase with that name. An unknown name raises `WorkflowError` that lists the known phase names.
  - `.validate(*, launcher_timeout: timedelta, role_root: Path | None = None) -> None`
  - `.digest() -> str`
- `prompt.INPUT_NAMES: frozenset[str]`: exactly the keys of `_TABLE`.

## `validate()` behavior

`validate()` returns `None` when the workflow is valid. Otherwise it raises `WorkflowError` with `.phase` set to the phase that broke the rule. The message contains the listed keyword, which tests match by regex. Rules:

1. Duplicate phase name → "duplicate". This is checked across the whole workflow before any other rule.
2. On a `Step`, `when` without `skip_to`, or `skip_to` without `when` → a message naming both `when` and `skip_to`.
3. On a `Step`, `skip_to` names a phase that is not strictly later (itself, an earlier phase, or an unknown name) → "skip_to ... must name a later phase".
4. On an `AgentPhase`, the role does not load via `roles.loader.load_role(role, root=role_root)` → "role ... does not load: <cause>". The loader exception is chained as `__cause__`. The call must use exactly this signature (`loader.py:140`). `role_root=None` means the packaged bundles.
5. An `AgentPhase` input that is not in `prompt.INPUT_NAMES` and is not the name of a strictly earlier phase → "input ... has no resolver and no earlier phase".
6. `AgentPhase.timeout <= launcher_timeout` → "timeout ... must exceed the launcher timeout ...". The comparison is strict (G2): the launcher must kill `claude -p` before the turn times out, because cancellation cannot stop a `to_thread` worker.
7. On an `AgentPhase`, an `on_fail` with `max_loops < 1` → "Goto max_loops must be at least 1".
8. On an `AgentPhase`, an `on_fail` whose target is not strictly earlier (itself, a later phase, or an unknown name) → "Goto ... must name an earlier phase".

`prompt` and `roles.loader` are imported inside `validate()`, so importing `phases` stays light. `validate()` does no I/O apart from loading roles.

## `digest()` behavior

`digest()` returns the sha256 hex digest of the following data:
- the workflow name;
- then one record per phase, in declared order;
- within a record, fields are joined by `\x1f`, and each record ends with `\x1e`.

Callables are rendered by a `_qual(fn)` helper as `module.qualname`. Record contents:
- Step: `"step"`, name, `_qual(run)`, `repr(sorted(args.items()))`, the gate qualnames, `best_effort`, the `when` qualname or `-`, and `skip_to` or `-`.
- AgentPhase: `"agent"`, name, role, comma-joined inputs, the result qualname or `-`, the gate qualnames, `repr(retry)`, `writes` or `-`, `str(timeout)`, and `repr(on_fail)`.

Two identical rebuilds must give the same digest. Any of the following must change the digest: reordering phases, renaming a phase, retargeting `skip_to` or `on_fail`, swapping a gate, or changing retry, timeout, role or inputs.

## Hard constraints

- `workflow/phases.py` never imports pygents. Only `src/agent_manager/runtime/` may (rule 1). The module docstring says so: "No pygents import here."
- The whole default suite, including `tests/e2e`, must stay green (rule 2, G7). This subtask adds modules and does not rewire any existing ones, so nothing that runs today should change.
- `SubtaskSummary`, journal lines, phase and attempt rows, and escalation payloads are not touched (rule 5, G10).

## Out of scope

- `phases.from_loader(...)` and moving `plan_hash_gate_adapter`/`_plan_hash_of` belong to sibling 0326732a.
- `workflow/task.py` (TASK, LAUNCHER_TIMEOUT), `workflow/integrate.py` (INTEGRATE) and the builtin YAML files belong to sibling 04a5b91e.
- Also out of scope: `runtime/compile.py` and dispatch, the supervisor tree, exactly-once phases, prompt benchmarking, and upstream pygents fixes.

## Tests

All tests go in `tests/workflow/test_phases.py`. The rule is that each module's tests mirror its path under `tests/` (spec §9). `phases.py` is pure data with no I/O, so these are plain unit tests: no fakes, no git repo, no harness. They do not go in `steps/`, `harness/` or `e2e/`.

The file content is the one given in plan lines 77-125. It uses `LAUNCHER = timedelta(minutes=10)` and module-level `step_fn`, `gate_ok` and `other_gate`, so the qualnames stay stable. The helper `agent()` defaults to role `explorer` (a shipped bundle) and inputs `("card",)` (a `_TABLE` key).

1. `test_valid_workflow_passes`: a Step followed by two AgentPhases, the last with `Goto` back to an earlier phase, validates cleanly.
2. `test_validate_refuses` is parametrized, and each case expects `WorkflowError` with a matching needle:
   - a duplicate name (`duplicate`);
   - `skip_to` pointing at itself (`skip_to`);
   - `when` without `skip_to` (`when`);
   - `skip_to` without `when` (`when`);
   - a forward `Goto` (`Goto`);
   - `max_loops=0` (`max_loops`);
   - an unknown role (`role`);
   - an unknown input (`input`);
   - a timeout equal to the launcher timeout (`timeout`).
3. `test_an_earlier_phase_name_is_a_valid_input`: a phase may list an earlier phase's name as an input.
4. `test_digest_is_stable_and_sensitive`: rebuilding the same workflow gives the same digest. A reorder, a rename, a gate swap and an added `Retry` each give a different digest.

Verification: `uv run pytest` (full suite). There is no typecheck or lint command.
