# Stop on unresolved review blockers (card 468daea9)

Subtask of "Close the task.js gaps" (be007353), milestone 84c3b532. Narrows Task 2.1 of `docs/superpowers/plans/2026-09-25-pygents-engine.md` (lines 427-476) under `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`. This card lands first in its story. ee3cc742, c7bea6a2 and cf8b3888 wait on it.

## Scope

Today the reviewer's `unresolved_blockers` list (`ReviewResult.unresolved_blockers: list[str]`, `src/agent_manager/results.py:118`) is recorded but no gate reads it. A review can report open blockers and the task still goes on to `verify`. This card adds a pure gate that stops the `review` phase when the reviewer reports a blocker that is still open. The gate is wired into both the YAML and Python declarations of the `task` workflow.

In scope:

- `src/agent_manager/steps/reducers.py`: add `review_blockers_gate(result: object) -> dict[str, str] | None`.
- `src/agent_manager/workflow/registry.py`: add `registry.register("review_blockers_gate", reducers.review_blockers_gate)` to `default_registry()`, registering the bare function with no adapter because it takes only `result`. Add `"review_blockers_gate"` to `BUILTIN_FUNCTION_NAMES`, keeping the tuple sorted the way the test compares it.
- `src/agent_manager/workflow/builtin/task.yaml:77`: change `review`'s gates to `[review_blockers_gate, review_gate, plan_hash_gate]`.
- `src/agent_manager/workflow/task.py:102`: in this worktree the `TASK` constant already exists. Change `review`'s gates to `(reducers.review_blockers_gate, reducers.review_gate, reducers.plan_hash_gate_adapter)` so both engines and `test_declared.py` agree.
- Update the existing tests that pin the old gate list or the old registry contents (listed below).

Out of scope: reviewer role prompt and policy (ee3cc742), critic split and `validate_spec`/`validate_plan` (c7bea6a2), typecheck/lint in `verify.run_suite` (cf8b3888). Do not change `ReviewResult` itself, `SubtaskSummary`, journal lines, phase/attempt rows or the shape of the escalation payload (G10). No pygents import outside `runtime/`.

## Observable behavior

`review_blockers_gate(result)` is pure. It does no I/O and keeps no module state. Malformed input returns a verdict and never raises. It reads the field through the existing `_field(result, "unresolved_blockers")` helper, so key-lookup rules match the other gates.

| Input | Result |
|---|---|
| `result` is not a `Mapping`, e.g. `None` | `{"blocked": "review", "detail": "the review stage returned nothing"}` |
| Mapping whose `unresolved_blockers` is missing, `None` or `[]` | `None` (pass) |
| Mapping with N ≥ 1 blockers | `{"blocked": "review", "detail": f"review left {N} unresolved blocker(s): {'; '.join(map(str, blockers))}"}` |

The gate is listed first in `review`'s gates, so a review with open blockers escalates at `review` before `review_gate` and `plan_hash_gate` run. The engine's own detail then names `review_blockers_gate`. `verify` and `mark_done` never run. Reviews with an empty blocker list behave exactly as they do today.

## Tests

Tier placement follows `2026-09-23-agent-manager-design.md` §14.

Pure-function tier, in `tests/steps/test_reducers.py`, using the plan's exact assertions:
- `test_no_unresolved_blockers_passes`: `{"unresolved_blockers": []}` → `None`.
- `test_unresolved_blockers_block_review`: two blockers → the exact "review left 2 unresolved blocker(s): …; …" verdict.
- `test_a_dead_reviewer_blocks`: `None` → the "returned nothing" verdict.

Engine tier, in `tests/test_engine.py`, with a fake registry and fake agent runner and no real git or harness:
- New test: walk the shipped `task` workflow (`load_builtin("task", ...)`) using a `_builtin_functions` variant in which `review_blockers_gate` is the real reducer and the fake reviewer returns a `ReviewResult`-shaped payload with `unresolved_blockers=["x"]`. Assert `summary.status == "escalated"`, `summary.failed_phase == "review"`, and that no `verify` call appears in the recorded calls. The fake reviewer returns only what its brief allows (rule 4).
- Existing fixtures: add `"review_blockers_gate": agent_only_gate` to `_builtin_functions` (around line 1669) and to the similar dict near line 1199, so the existing walks still resolve every gate name.

Registry and workflow-declaration tests, under `tests/workflow/`, which pin declared structure:
- `tests/workflow/test_registry.py`: add `"review_blockers_gate"` to `TASK_YAML_NAMES`. Assert `registry.resolve("review_blockers_gate") is reducers.review_blockers_gate`.
- `tests/workflow/test_builtin_task.py:207`: expect `["review_blockers_gate", "review_gate", "plan_hash_gate"]`. Extend the `("review", "review_gate")` style table near line 488 if it lists every gate.
- `tests/workflow/test_phases.py:268`: also assert that `reducers.review_blockers_gate` is `review.gates[0]`.
- `tests/workflow/test_declared.py` must stay green. This proves the YAML and `TASK` agree.

Existing e2e (fake-claude) tier. This is not a new test, but the whole suite must stay green:
- `tests/e2e/fake_claude.py:596-607`: the review-fail branch already emits `unresolved_blockers=list(findings)`, so the new first gate now trips before `review_gate`. `tests/e2e/test_milestone_run.py:163` (`assert "review_gate" in stopped["detail"]`) will fail because `"review_blockers_gate"` does not contain that substring. Change the assertion to expect `review_blockers_gate`. Keep the `"review-fail marker"` detail assertion. Rewrite the now-stale comment in `fake_claude.py` that says "`unresolved_blockers` alone would fail nothing, because no gate reads it". Do not add new e2e tests.

The canned `"phase 'review' gate 'review_gate' failed"` payload in `tests/test_cli.py:2871` is a fixture, not a walk, so leave it unchanged.

## Verification

`uv run pytest` passes in full, including `tests/e2e` and both engines while `--engine` exists. Commit message: `feat(review): stop on unresolved review blockers`.
