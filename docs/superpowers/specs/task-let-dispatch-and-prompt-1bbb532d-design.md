# Let dispatch and prompt take phase-model objects (subtask 1bbb532d)

Parent story: f9c19dc3 "Run a workflow on pygents" (Milestone 6: the pygents engine). Plan task: `docs/superpowers/plans/2026-09-25-pygents-engine.md`, Task 3.2. Milestone spec of record: `docs/superpowers/specs/2026-09-25-pygents-engine-design.md` (G1-G10), extending the 2026-09-23, 2026-09-24 and 2026-09-25-integrate specs.

## Goal

Widen `dispatch` and `prompt` so they accept both the YAML phase type (`workflow.loader.AgentPhase`, `result: str | None`, `gates: tuple[str, ...]`, `retry: RetryPolicy | None`) and the declared phase model (`workflow.phases.AgentPhase`, `result: type[BaseModel] | None`, `gates: tuple[Callable, ...]`, `retry: phases.Retry | None`). This is an accept-both widening only: the YAML engine's behaviour, messages and journal rows stay identical.

## Scope

In `src/agent_manager/dispatch.py`:

- `evaluate_gates(phase, workflow, values, warnings)` (the `for name in phase.gates` loop, ~line 300): each gate entry may be a `str` (resolved through `workflow.function(entry)`, as now) or a callable (used as-is; `workflow.function` is not called for it). The gate's display name used in `bind_arguments(..., function=name)` and in every verdict/warning message is the string itself for a `str`, else `getattr(entry, "__name__", repr(entry))`. All existing outcomes are unchanged: `None` passes, a mapping with `warn` appends a warning, any other mapping returns `gate_failed`, raising or returning a non-mapping returns a `fatal` `gate_failed`, a binding failure propagates as `EngineError`.
- `AgentRunner.__call__` model resolution (~lines 402-408): `phase.result is None` gives no model; `phase.result` that is a class (`isinstance(phase.result, type)`) is used directly, without consulting `self.result_models` or `results.resolve_result_model`; a `str` goes through `results.resolve_result_model(phase.result, self.result_models, phase=phase.name)` as now (including its existing error for an unknown name).
- The retry read (~lines 413-414) must work for both `loader.RetryPolicy` and `phases.Retry`. Both expose `max_attempts` and `on`, and the current code already duck-types (`phase.retry.max_attempts`, `tuple(phase.retry.on)`), so no branching is required; the requirement is that it is covered by a test.
- Type hints on `evaluate_gates`, `AgentRunner.__call__` and `_attempt` may be widened to admit either phase type; no runtime behaviour depends on the hint.

In `src/agent_manager/prompt.py`:

- `render_prompt`'s `phase` parameter, `_assemble`'s `phase` parameter and the `_Request.phase` field (currently typed `workflow.loader.AgentPhase`, imported at line 36) are retyped to a `typing.Protocol` exposing exactly `name: str`, `role: str`, `inputs` (a sequence of `str`) — the only attributes these code paths read. Both `AgentPhase` types satisfy it structurally. If nothing else in `prompt.py` needs the loader import, it is removed. No change to resolution, ordering, dedup, errors or text assembly.

## Out of scope

- The `feedback` input resolver, `Goto` revision loops and `agent_phase` (card b904b9e7), even though it also edits `prompt.py`.
- `runtime/compile.py`, `runtime/bridge.py`, `runtime/state.py`, `harness/launcher.py` on_spawn, `engine.run_one_step` (card 023d918e).
- `runtime.run_subtask` and parametrizing `tests/test_engine.py` over engines (card 2853e536).
- `engine.py`'s own deterministic-gate loop (`engine.py:298`) — not named by Task 3.2.
- Any `pygents` import: `dispatch.py` and `prompt.py` must not import it (rule 1). No new dependencies.
- Supervisor tree, exactly-once phases, prompt benchmarking, upstream pygents fixes.

## Invariants

- YAML-engine behaviour is byte-for-byte unchanged: same gate names in messages, same verdicts, same retry budget, same `_record_phase` journal rows (`started`/`done`/`failed` with the same fields) — `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads stay unchanged (rule 5 / G10).
- A fake `claude` in a test knows nothing beyond its brief (rule 4).
- The whole default suite, including `tests/e2e`, stays green (rule 2).

## Error paths

- Callable gate that raises or returns a non-mapping: `fatal` `gate_failed`, message naming the gate by `__name__` (a lambda shows as `<lambda>`).
- Callable gate whose parameters nothing supplies: `EngineError` from `engine.bind_arguments`, naming the gate by `__name__`.
- `str` gate with an unknown name: the loader's existing `UnknownFunctionError` from `workflow.function`, unchanged.
- `str` result with no model in `result_models`: `resolve_result_model`'s existing error, raised before any attempt is journalled, unchanged.

## Tests

Test-placement rule (CLAUDE.md "tests mirror it under `tests/`"; `tests/e2e/` is reserved for full-system wiring): all new tests are unit tests for `dispatch.py` and go in `tests/test_dispatch.py`, reusing its existing fake launcher/store/adapter fixtures. No e2e test is added.

- `test_callable_gate_is_called_directly` — tier: unit, `tests/test_dispatch.py`. A `phases.AgentPhase` with `gates=(lambda result: {"blocked": "x", "detail": "d"},)` evaluated against a workflow whose `function` raises; the verdict is `gate_failed` with detail containing `d`, and `workflow.function` is never called.
- `test_result_class_is_used_directly` — tier: unit, `tests/test_dispatch.py`. `phases.AgentPhase(..., result=results.CriticResult)` run by an `AgentRunner` whose `result_models` is empty (or lacks it); a valid result file validates against the class and the phase returns it.
- `test_phase_model_retry_is_honoured` — tier: unit, `tests/test_dispatch.py`. `retry=phases.Retry(2, ("schema_invalid",))` with an invalid first result and a valid second makes exactly two attempts and succeeds.
- Existing `tests/test_dispatch.py`, `tests/test_prompt.py`, `tests/test_engine.py` and `tests/e2e` tests pass unmodified, demonstrating the YAML path is unchanged.

## Verification

`uv run pytest` — whole suite green. No separate typecheck or lint command.
