# Subtask dd321e61: add `implement_blocked_gate`

Parent story: 8a174c56 ("Seam: a blocked coder must stop the subtask"). Source decision: `docs/superpowers/specs/2026-09-24-orchestration-design.md`, O7 (lines 103-110). This card narrows O7 and does not extend it.

## Problem

`ImplementResult.blocked` (`src/agent_manager/results.py:91-105`) is never read. When a coder reports that it cannot proceed, for example because the baseline suite was already red or the plan hash could not be computed, `review` runs anyway, and the subtask can end `done` on partial work.

## Scope

1. **Gate.** Add `implement_blocked_gate(result)` to `src/agent_manager/steps/reducers.py`. It is a pure function in the same style as `verification_passed_gate` (`reducers.py:322`).
   - The parameter must be named `result`, because the engine binds gate arguments by parameter name. The implement result arrives as a `model_dump(mode="json")` dict under both `result` and `implement` (`dispatch.gate_values`).
   - If `result` is a Mapping and `result.get("blocked") is True`, return `{"blocked": "implement", "detail": <blocked_reason>}`. The detail is `str(blocked_reason or "").strip()`. If that is empty, use a fallback detail saying the coder reported blocked but gave no reason.
   - If `result` is a Mapping and `blocked` is not identically `True`, return `None` to pass.
   - If `result` is not a Mapping, return a `{"blocked": "implement", "detail": ...}` verdict that says there was no implement result to judge. Do not raise. This follows the module docstring's rule that malformed content produces a verdict, not an exception.
2. **Registry.** In `src/agent_manager/workflow/registry.py`:
   - Add `"implement_blocked_gate"` to the sorted `BUILTIN_FUNCTION_NAMES` tuple (~line 210), between `"exploration_output_gate"` and the `"plan_check…"` entries.
   - Add `registry.register("implement_blocked_gate", reducers.implement_blocked_gate)` in `default_registry` (~line 240), next to the other reducers. Register the function directly, with no wrapper.
   - Updating the reducer counts in the docstrings is optional.
3. **Workflow.** In `src/agent_manager/workflow/builtin/task.yaml`, add `gates: [implement_blocked_gate]` to the `implement` phase. Do **not** add a `retry:` block. The phase stays non-retryable, because re-dispatching the same brief would repeat the same answer.

## Observable behaviour

- A healthy implement result (`blocked: false`) passes the gate, and the run proceeds to `review` exactly as it does today.
- A result with `blocked: true` produces a gate verdict. `implement` is an agent phase, so its gates run in `dispatch.evaluate_gates` (`dispatch.py:279`, called at ~line 512), not in `engine._evaluate_gates` (that one serves deterministic phases only and `_GateFailed` is not involved). The verdict becomes a `gate_failed` attempt whose detail is `phase 'implement' gate 'implement_blocked_gate' failed: blocked=implement, detail=<reason>`. With no `retry:` block the budget is 1 attempt (`dispatch.py:413`), so the failure is non-retryable and the runner raises `AgentPhaseFailed`, which the engine escalates. The subtask is escalated at phase `implement`, and the failure detail carries the coder's `blocked_reason`. `review` is never dispatched and no review attempt is recorded.

## Error paths

- `blocked: true` with a `blocked_reason` that is `None`, empty, or only whitespace still blocks, with the fallback "gave no reason" detail.
- A non-Mapping `result` blocks with a "no implement result to judge" detail. It never raises.
- Truthy values of `blocked` that are not `True`, such as `"true"` or `1`, pass. This matches the identity check in `verification_passed_gate`. Pydantic validation of `ImplementResult` upstream is what guarantees a real bool.

## Tests

Tier placement follows `docs/superpowers/specs/2026-09-23-agent-manager-design.md` section 14 (lines 477-492).

1. `tests/steps/test_reducers.py`: **pure-function unit tier**, because `steps/reducers.py` is a pure module. Add `implement_blocked_gate` to the imports at the top of the file.
   - A normal result (`blocked: false`) returns `None`.
   - `blocked: true` with a reason returns `{"blocked": "implement", "detail": <that reason>}`.
   - `blocked: true` with the reason missing, `None` or empty returns a `blocked: implement` verdict whose detail says the coder gave no reason.
2. `tests/workflow/test_builtin_task.py`: the existing **builtin-document tests**, which check the YAML against the registry (the pure/document level).
   - Change the assertion at ~line 225 in `test_implement_is_handed_the_plan_hash_last…` from `phase.gates == []` to `phase.gates == ["implement_blocked_gate"]`, and assert that the implement phase has no retry.
   - `test_the_document_still_names_exactly_the_gates_this_suite_covers` (~line 473) hard-codes the gate list; insert `("implement", "implement_blocked_gate")` there too, after the `validate_plan` entry and before `("review", "review_gate")`. `GATED_PHASES` itself (~line 465) is derived from the document and needs no edit.
   - The existing parametrised bind and healthy-run tests (~lines 258-280) pick up the new gate through `_implement_result()`, and must pass unchanged.
3. `tests/test_engine.py`: **Engine tier**, driven by a fake adapter that returns canned result files, one of them gate-failing. Load the real workflow with `load_builtin("task", _registry(...))`, as at lines 1181, 1647 and 1666, or use the FakeAdapter/FakeLauncher pattern from `tests/test_dispatch.py:173-265`. The canned implement result has `blocked: true` and a reason. Assert that:
   - `summary.status == "escalated"`.
   - The failure is at phase `implement` and its detail contains the reason.
   - The store has no attempt row for `review`.
   - The launcher never received a `review` dispatch.
   - The existing sync checks (`tests/workflow/test_registry.py:104` and `tests/test_engine.py:1175`, which compare `BUILTIN_FUNCTION_NAMES` with the names in task.yaml) must still pass.
4. **Production-wiring tier** (`tests/e2e`, fake `claude`). No new test is needed here. The healthy fake reports `blocked: false`, so the whole default suite, `tests/e2e` included, must stay green under `uv run pytest`. The fake `claude` must not learn anything beyond what the brief tells it.

## Out of scope

Everything in the addendum's section 4 that belongs to sibling cards: parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, review counts measured by git, and verification discovery. There is no retry block on `implement` and no change to the coder prompt, which already asks the coder to report `blocked: true` with a reason.
