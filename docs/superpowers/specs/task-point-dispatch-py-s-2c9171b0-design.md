# Point dispatch.py's AgentRunner at walk's evaluator (card 2c9171b0)

Parent: 223f9973 "One gate evaluator instead of two" (milestone 9c44c2fb). Spec of record: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, decision S3 (§3) and its §6 Testing bullet. This document narrows S3 to the dispatch half only.

## Base

Branch from `m13/task-add-gateverdict-and-the-4957ac74` (HEAD 02c2ff2), not master: only that branch has `GateVerdict`, `gate_values` and `evaluate_gates` in `src/agent_manager/runtime/walk.py`. This card consumes that API and does not modify `runtime/walk.py`.

## Scope

`src/agent_manager/dispatch.py`:

- Delete `gate_values`, `_render_verdict`, `evaluate_gates` and `_render_error` (currently :262-352).
- The import line `from agent_manager.runtime.walk import RESERVED_CONTEXT_KEYS, bind_arguments`
  (currently :35) becomes dead once those four functions are gone (nothing else in this file uses
  `RESERVED_CONTEXT_KEYS` or `bind_arguments`): replace it with `from agent_manager.runtime import
  walk`, which the new `walk.evaluate_gates(...)`/`walk.gate_values(...)` calls below need anyway.
- The one other caller of the deleted `_render_error` (the phase-`failed` record in the retry loop's `except Exception`, currently :466) switches to `walk._render_error`, which produces the identical `f"{type(error).__name__}: {error}"` string.
- The gate block after `classify` (currently :542-549) calls `walk.evaluate_gates(phase, walk.gate_values(context, phase.name, verdict.result), self.warnings)` and maps the returned `GateVerdict` to a `Verdict`:
  - `pass`: the `ok` verdict stands.
  - `warn`: the `ok` verdict stands. The warnings were already appended to `self.warnings` by `evaluate_gates`; dispatch must not append them again.
  - `fail`: `Verdict("gate_failed", detail=gv.detail["message"])`, not fatal, so `retry.on` still decides whether it re-dispatches.
  - `broken`: `Verdict("gate_failed", detail=<message>, fatal=True)`, so it is never retried whatever `retry.on` says. `<message>` is rebuilt by a small private helper in dispatch.py from `detail["gate"]`, `detail["reason"]`, `detail["error"]` and `detail["returned_type"]`, and matches the pre-S3 text exactly:
    - `reason == "raised"`: `phase {name!r} gate {gate!r} raised {ErrType}: {error}; a gate returns None to pass or a mapping verdict to fail, so this is a broken gate rather than a failed attempt`
    - `reason == "not_a_mapping"`: `phase {name!r} gate {gate!r} returned {returned_type}; a gate returns None to pass or a mapping verdict to fail, and anything else would be read as a pass by accident`
- `Verdict` (frozen dataclass, :158-169) is unchanged. Only how it gets constructed changes.
- Doc-only: the docstrings in `runtime/context.py:7` and `steps/reducers.py:383-384` that name `dispatch.gate_values` are updated to name `walk.gate_values`. No code changes in those files.

Out of scope: `runtime/walk.py` (the evaluator and `run_one_step`'s gate handling), `runtime/engine.py`, any of dispatch.py's execution logic outside the gate seam, and every other S-decision (S1, S2, S4-S11). Also out of scope, per spec §8: pygents' turn/phase model, the checkpoint format, the harness adapter contract, grafo, CI or typecheck gates, and the process-boundary monkeypatches.

## Observable behaviour

No CLI-observable change (spec §5). Envelopes, exit codes and `am status` shapes stay the same. So do the attempt statuses, the attempt and phase `detail` strings, the warning text and the retry counts for passing, warning, failing and broken gates on an agent phase.

## Error paths

- A gate whose parameters cannot be bound raises `EngineError` out of `evaluate_gates`. It still propagates through `AgentRunner`: the phase is recorded `failed` with the rendered error, then re-raised. It is not turned into a verdict.
- A `BaseException` raised by a gate (control-flow signals) propagates uncaught, as it does today.
- A broken gate is fatal even when `retry.on` lists `gate_failed`: one dispatch, then `AgentPhaseFailed(outcome="gate_failed")`.

## Tests

The placement rule is design spec §14: a pure function is tested beside its own logic, and the Engine is tested with a fake adapter or launcher and canned result files, never a real process. `tests/test_dispatch.py` is declared Engine tier. The shared evaluator's own behaviour, including the "gate that raises, tested once" case, is already covered in `tests/runtime/test_walk.py` on the base branch and is not duplicated here.

Remove from `tests/test_dispatch.py`. These call deleted functions, and their coverage now lives with the evaluator:
- `test_callable_gate_is_called_directly`
- `test_a_passing_callable_gate_sees_the_result`
- `test_a_callable_gate_that_raises_is_fatal_and_named_by_its_function_name`
- `test_a_callable_gate_returning_a_non_mapping_is_fatal_and_named_lambda`
- `test_a_callable_gate_with_an_unsupplied_parameter_is_a_named_engine_error`
- `test_a_callable_gate_warning_names_the_gate_by_its_function_name`
- `test_the_result_is_bound_under_both_result_and_the_phase_name`

Move, rewritten against `walk.gate_values` / `walk.evaluate_gates`, to `tests/runtime/test_walk.py`. This is the pure-function tier, beside the logic. These are additions only; the sibling's existing tests are left untouched:
- `test_a_reserved_key_is_not_overwritten_by_a_same_named_phase`
- `test_a_callable_gate_without_a_name_is_named_by_its_repr`: asserts `kind == "fail"` and the `repr`-named message.

Once both are gone, `tests/test_dispatch.py`'s `from agent_manager.runtime.walk import
RESERVED_CONTEXT_KEYS` (currently :33) and `import functools` (currently :12) are dead — remove
them if nothing else in the file still uses them.

Add to `tests/test_dispatch.py`, Engine tier, driven through the existing `_runner` / `FakeLauncher` helpers:
1. `test_agent_runner_maps_a_broken_gate_to_a_fatal_gate_failed`: parametrised over a raising gate and a non-mapping gate, with `retry.on` containing `gate_failed`. Asserts exactly one dispatch, `AgentPhaseFailed.outcome == "gate_failed"`, and a detail equal to the pre-S3 message above. This is S3 §6's thin test for the agent phase kind.
2. `test_a_warning_gate_passes_and_warns_exactly_once`: the phase result is returned, and `runner.warnings` holds the `phase 'explore' gate 'output_gate' warned: ...` line exactly once. This guards against appending the warning twice.
3. `test_a_failing_gate_records_the_rendered_message_as_the_detail`: the attempt and the `AgentPhaseFailed` detail equal `phase 'explore' gate '<lambda>' failed: blocked=x, detail=d`, and the failure is not fatal.
4. `test_an_unbindable_gate_parameter_propagates_as_engine_error`: `EngineError` escapes the runner with `.parameter`, `.function` and `.phase` intact, and the phase is recorded `failed`.

These existing Engine-tier tests must keep passing unedited: `test_a_valid_result_with_passing_gates_is_the_phase_result`, `test_a_retryable_gate_failure_re_dispatches_with_the_gate_detail`, `test_a_gate_failure_outside_retry_on_is_not_retried`, `test_a_gate_that_raises_stops_after_one_dispatch`, and the retry-loop tests further down the file.

## Verification

`uv run pytest` (full suite). There is no lint or typecheck step.
