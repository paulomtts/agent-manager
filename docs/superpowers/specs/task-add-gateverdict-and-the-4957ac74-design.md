# Add GateVerdict and the unified evaluator to runtime/walk.py (card 4957ac74)

Parent story: 223f9973, "One gate evaluator instead of two". Milestone: 9c44c2fb. Source of truth: `docs/superpowers/specs/2026-09-30-architecture-cleanup-design.md`, Decision S3 (lines 141-151), and §6 Testing, S3 bullet (line 298).

## Scope

This subtask covers `src/agent_manager/runtime/walk.py` and `tests/runtime/test_walk.py` only. Sibling card 2c9171b0 is blocked on this one. It deletes dispatch.py's `gate_values`/`evaluate_gates`/`_render_verdict`/`_render_error`, points `AgentRunner` at the evaluator added here, and maps `"broken"` to `Verdict("gate_failed", fatal=True)`. None of that work happens in this subtask. `dispatch.py` stays unchanged here.

1. Add `GateVerdict` to `runtime/walk.py`. It is a plain frozen `@dataclass`, not a Pydantic model: CLAUDE.md reserves Pydantic for process-boundary data, and this is internal runtime state. It has two fields:
   - `kind: Literal["pass", "warn", "fail", "broken"]`
   - `detail: dict | None`
2. Add one public evaluator to `walk.py`, `evaluate_gates(phase, values, warnings) -> GateVerdict`. It takes any phase object that has `.name` and `.gates`, so it works for both `phases.Step` and `phases.AgentPhase` without walk.py importing anything new. It follows the gate contract both copies share today:
   - It runs the gates in order and binds each one with `bind_arguments(gate, values, phase=..., function=_label(gate))`.
   - If a gate returns `None`, evaluation continues.
   - If a gate returns a mapping that contains `"warn"`, the evaluator appends the existing message (`phase {name!r} gate {gate!r} warned: {verdict['warn']}`) to `warnings` and continues.
   - If a gate returns any other mapping, the result is `kind="fail"`. Evaluation stops at the first one.
   - If a gate raises, or returns something that is not a mapping, the result is `kind="broken"`. Evaluation stops at the first one.
   - If no gate fails or breaks, the result is `kind="pass"`, or `kind="warn"` when at least one gate warned.
   - A binding failure is not `"broken"`. The `EngineError` from `bind_arguments` propagates out of the evaluator unchanged, because both callers treat it that way today: walk sends it to its catch-all, and dispatch lets it propagate.
3. `detail` is `None` for `"pass"`. For the other kinds it must carry enough for either caller to reproduce its current message without re-running the gate, under these exact keys (this dict is the contract sibling 2c9171b0 codes against, so the keys are fixed here rather than left for the implementer to invent):
   - `"fail"`: `{"gate": <name str>, "verdict": <raw mapping>, "message": <rendered "phase {name!r} gate {gate!r} failed: {k=v, ...sorted}">}`. That message is identical in both copies today.
   - `"broken"`: `{"gate": <name str>, "reason": "raised" | "not_a_mapping", "error": <exception>}`, plus `"returned_type": <type name str>` only when `reason == "not_a_mapping"`. For `"raised"`, `"error"` is the gate's own exception. For `"not_a_mapping"`, `"error"` is the `EngineError` that walk constructs today (the same message, `phase=`, and `function=`); `"returned_type"` is the returned value's `type(...).__name__`, kept as its own key (not just embedded in the `EngineError` message) so the sibling can read it without parsing text, to keep dispatch's wording.
   - `"warn"`: `{"warnings": <list[str], the collected warning messages>}`.
4. Replace the private `_evaluate_gates` (walk.py:302-328) and `_render_verdict` (330-331) with the new evaluator, and make `run_one_step` (364-416) call it. The binding-table builder becomes public as `gate_values` so the sibling can call it. `walk._gate_values` must stay importable under that name, because `tests/test_engine.py:225` calls it and existing tests must pass unchanged. `_label`, `_skip_target`, `_bind_result`, and `_render_error` stay. `_GateFailed` stays defined because Decision S7 (line 199) names it.
5. Do not add a pygents import. Rule 1 (walk.py:10-13) is enforced by `tests/runtime/test_walk.py`.

## Observable behaviour (unchanged on the deterministic-step path)

`run_one_step` maps each verdict kind to exactly what it records today:

- `"pass"` / `"warn"`: the result is the same as today. Warnings are collected in the same order with the same text. `skip_to` is still computed from the same binding table. The phase is recorded `done`, and the result is `_Outcome(ok=True, result=..., warnings=..., skip_to=...)`.
- `"fail"`: the phase is recorded `failed` with detail `phase 'x' gate 'g' failed: k=v`, and the result is `_Outcome(ok=False, detail=that, warnings=warnings)`. This is today's `_GateFailed` branch (walk.py:400-404).
- `"broken"`: this goes through today's catch-all path (walk.py:405-414). The phase is recorded `failed` with detail `_render_error(exc)`, which is `"{ExcType}: {exc}"` for the exception carried in `detail`. The result is `_Outcome(ok=False, ...)`. That is byte-for-byte the string recorded today, both for a gate that raises and for a gate that returns a non-mapping (the non-mapping case renders as `EngineError: ...`). A broken gate is never silently swallowed, and nothing escapes `run_one_step`.
- The following still go to the catch-all exactly as before: a binding failure (in a gate, in `when`, or in `run`), a non-mapping `run` result, or any exception from `run` or `when`.

## Error paths

- A gate raises any `Exception` subclass → `GateVerdict("broken", ...)`. `BaseException`s that are not `Exception` (control-flow signals) are not caught by the evaluator, matching dispatch's current `except Exception`.
- A gate returns a non-`None`, non-mapping value → `GateVerdict("broken", ...)`. It is never read as a pass.
- A gate's parameters cannot be bound → `EngineError` propagates out of the evaluator. It is not a verdict.

## Tests

The placement rule is design spec §14 (2026-09-23 design, lines 505-520). The evaluator and `GateVerdict` are pure and do no I/O, so they get unit tests in `tests/runtime/test_walk.py`, which mirrors `runtime/walk.py`. None of these tests go in `test_engine.py` or an integration fixture. The existing pygents-hygiene test in that file stays as it is.

1. **Unit, `tests/runtime/test_walk.py`:** all gates return `None` → `kind == "pass"`, `detail is None`, and `warnings` is untouched.
2. **Unit, `tests/runtime/test_walk.py`:** one gate warns and a later gate passes → `kind == "warn"`, and `warnings` holds the exact existing message.
3. **Unit, `tests/runtime/test_walk.py`:** a gate returns a failing mapping → `kind == "fail"`, the detail carries the rendered `phase ... gate ... failed: k=v` message, and gates after it are not called.
4. **Unit, `tests/runtime/test_walk.py`:** a gate that raises is tested once against the shared evaluator (S3 §6) → `kind == "broken"`, the detail carries the original exception and the gate name, and later gates are not called.
5. **Unit, `tests/runtime/test_walk.py`:** a gate returns a non-mapping → `kind == "broken"`, the detail carries the `EngineError` and the returned type name.
6. **Unit, `tests/runtime/test_walk.py`:** a gate with an unbindable parameter → `EngineError` propagates and no verdict is returned.
7. **Unit, `tests/runtime/test_walk.py`:** walk's thin "broken maps to its own outcome" test (S3 §6). It calls `run_one_step` with a `Step` whose gate raises and checks `_Outcome.ok is False` and that the phase row is recorded `failed` with detail `"{ExcType}: {msg}"`. It uses an in-memory or temporary `Store` and a fixed clock, which is the same lightweight setup existing `run_one_step` callers use, with no git and no harness. Dispatch's counterpart test belongs to sibling 2c9171b0.

These must pass unchanged: `tests/test_dispatch.py`, `tests/test_engine.py`, `tests/workflow/test_task.py`, `tests/workflow/test_integrate.py`, `tests/runtime/test_checkpoint.py`, and the existing pygents-hygiene test.

## Verification

`uv run pytest`. There is no typecheck or lint command (CLAUDE.md).

## Out of scope

- Any edit to `dispatch.py`, which belongs to sibling 2c9171b0.
- Changes to pygents' turn/phase model, the checkpoint format, or the harness adapter contract.
- Any typecheck or CI command.
- The legitimate monkeypatch calls.
- grafo.
- The `_Signal` marker base, which is S7.
