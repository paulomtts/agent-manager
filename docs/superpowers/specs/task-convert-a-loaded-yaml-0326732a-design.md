# Convert a loaded YAML workflow into the phase model (card 0326732a)

Parent story: 09e1183f "The declared phase model" (milestone 84c3b532, pygents-engine). Source of truth: `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 1.2 and `2026-09-25-pygents-engine-design.md` §5/§9. This narrows the agreed design to one subtask; it adds no new design.

## Base

Builds on the `phases.py` from sibling a0aa01e5 (branch `m6/task-add-the-phase-model-a0aa01e5`, already present in this worktree): `Goto`, `Retry`, `Step`, `AgentPhase`, `Workflow`, `WorkflowError`, `validate()`, `digest()`. This card must not change any of those, nor `validate()`/`digest()` logic.

## Scope

1. Add `from_loader(loaded: loader.Workflow, *, timeout: timedelta = timedelta(minutes=30)) -> Workflow` to `src/agent_manager/workflow/phases.py`.
2. Move `_plan_hash_of` and `plan_hash_gate_adapter` verbatim, docstrings included, from `src/agent_manager/workflow/registry.py` (lines 182-215 in this worktree; the plan's "174-228" is stale) to the end of `src/agent_manager/steps/reducers.py`. `registry.py` imports `plan_hash_gate_adapter` from `agent_manager.steps.reducers` and still registers it as `"plan_hash_gate"` in `default_registry()`. Signature stays `plan_hash_gate_adapter(implement: object = None, review: object = None)`. Any import the moved code needs (e.g. `Mapping`) goes to `reducers.py`. Any import in `registry.py` that is no longer used after the move is dropped.

Out of scope: declaring `TASK`/`INTEGRATE`, per-phase timeouts, and the digest-equality tests against the shipped YAML. Those belong to sibling 04a5b91e. No changes to the engine, the YAML, `dispatch.py`, `prompt.py`, or `tests/e2e`.

## Observable behavior of `from_loader`

- Returns `phases.Workflow(loaded.name, phases)`, with phases in the loaded order, so `phase_names` equals `tuple(p.name for p in loaded.phases)`.
- Every function name (`run`, `when`, and each `gates` entry) is resolved with `loaded.function(name)`, never through a registry. Whatever callable the loaded workflow holds, including a fake from a test registry, is carried through by identity.
- A deterministic loader phase (`kind == "deterministic"`) becomes `Step(name, run=<fn>, args=dict(args), gates=tuple(<fn>...), best_effort, when=<fn> or None, skip_to)`.
- An agent loader phase becomes `AgentPhase(name, role, inputs=tuple(inputs), result=results.RESULT_MODELS[result] or None, gates=tuple(<fn>...), retry=Retry(max_attempts, tuple(on)) or None, writes, timeout=timeout)`, with `on_fail=None`. The `timeout` keyword applies to every agent phase.
- Pure: the input is not mutated and no I/O happens. `phases.py` stays free of any pygents import (rule 1). `results` and `loader` are imported inside the function, as in the plan.
- Before writing the code, check the attribute names against `workflow/loader.py`. As of this worktree, `DeterministicPhase` has `run`, `args`, `best_effort`; `_PhaseBase` has `name`, `when`, `skip_to`, `gates`; `AgentPhase` has `role`, `inputs`, `result`, `writes`, `retry`; `RetryPolicy` has `max_attempts`, `on`.

## Error paths

- An unresolved function name raises the loader's `UnknownFunctionError` from `loaded.function`, unchanged.
- A `result:` name missing from `results.RESULT_MODELS` raises `WorkflowError(..., phase=<name>)`, not a bare `KeyError`.
- An agent loader phase with `when` or `skip_to` set raises `WorkflowError(..., phase=<name>)`. The loader's `_PhaseBase` allows these fields on agent phases, but `phases.AgentPhase` has no fields for them, so dropping them silently would change behavior. No shipped YAML uses them, so the shipped workflows still convert.

## Tests

The placement rule is design spec §14 plus pygents-engine design §9: pure functions get unit tests in the module under `tests/` that mirrors their `src/` module. Nothing for this card goes under `tests/e2e/`, which is reserved for the one opt-in real-harness run.

Append to `tests/workflow/test_phases.py` (unit tier, mirrors `workflow/phases.py`):
- `test_from_loader_resolves_every_name_of_the_shipped_task`: as in plan Task 1.2 Step 1. It covers the phase-name order, `review.result is results.ReviewResult`, `reducers.review_gate` and `reducers.plan_hash_gate_adapter` in `review.gates`, `explore.retry == Retry(2, ("schema_invalid", "gate_failed"))`, and `plan_check.skip_to == "docs_commit"` with a callable `when`.
- `test_from_loader_keeps_fakes_from_a_test_registry`: as in the plan. It checks that `from_loader(load_builtin("integrate", default_registry())).phase("verify").run is loaded.function("verify.run_suite")`.
- `test_from_loader_rejects_an_unknown_result_name` and `test_from_loader_rejects_when_on_an_agent_phase`: each builds a small `loader.Workflow` through a test registry and asserts that `WorkflowError` names the phase.

Append to `tests/steps/test_reducers.py` (unit tier, mirrors `steps/reducers.py`):
- `test_plan_hash_gate_adapter_compares_the_two_results`: as in the plan. Equal hashes give `None`, different hashes give a value that is not `None`, and `(None, None)` gives `None`.

Done means `uv run pytest` passes in full, with existing registry and loader tests unchanged. Rule 5/G10 applies: journal lines, `SubtaskSummary`, and phase/attempt rows are byte-for-byte unchanged.

## Note

The exploration findings given to this stage were cut off at 8000 characters, mid-sentence, in the test-tier paragraph. That is a sign the exploration stage over-ran its brief. The missing part looked like the end of the "no `tests/e2e`" point, which the design docs cover independently.
