# Declare TASK and INTEGRATE, pinned to the shipped YAML (card 04a5b91e)

Parent story: 09e1183f "The declared phase model". Plan: `docs/superpowers/plans/2026-09-25-pygents-engine.md` Task 1.3. Milestone spec: `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`.

## Scope

Two new modules that declare the builtin workflows as phase-model data, plus one pure-data test file. Nothing else changes.

- `src/agent_manager/workflow/task.py` exports `TASK: Workflow` and `LAUNCHER_TIMEOUT: timedelta`.
- `src/agent_manager/workflow/integrate.py` exports `INTEGRATE: Workflow`.
- `tests/workflow/test_declared.py`.

Consumed as-is (do not edit): `workflow/phases.py` (`Workflow`, `AgentPhase`, `Step`, `Retry`, `from_loader`, owned by siblings a0aa01e5 / 0326732a and already present in this worktree), `workflow/registry.py`, `workflow/loader.py`, `steps/reducers.py`, `steps/integrate.py`, the builtin YAML files, and the old-engine tests `tests/workflow/test_builtin_task.py` / `test_builtin_integrate.py`.

Out of scope: wiring `TASK`/`INTEGRATE` into any runner or the `--engine` switch, the supervisor tree, `Goto`/`on_fail` loops (the shipped YAML has none, so no phase sets `on_fail`), and anything under `runtime/`.

## Observable behaviour

`LAUNCHER_TIMEOUT = timedelta(seconds=dispatch.DEFAULT_TIMEOUT)` (1800 s, `src/agent_manager/dispatch.py:148`).

`TASK` is `Workflow("task", ...)` with these phases, in this order, mirroring `src/agent_manager/workflow/builtin/task.yaml` field for field. Callables are real function objects imported from their modules, never registry lookups, and each must be the same object `default_registry()` binds (`registry.py` lines 225-245), because `digest()` identifies callables by `module.qualname`:

| phase | kind | declaration |
|---|---|---|
| worktree | Step | run `worktree.ensure` |
| explore | AgentPhase | role `explorer`; inputs `(card, parent_story, repo_docs, verification)`; `results.ExploreResult`; gates `reducers.exploration_output_gate`, `reducers.verification_gate`; `Retry(2, ("schema_invalid", "gate_failed"))` |
| mark_in_progress | Step | run `rollup.set_status`, args `{"status": "in_progress"}`, `best_effort=True` |
| plan_check | Step | run `plan_check.find_validated_plan`, `when=plan_check.has_validated_plan`, `skip_to="docs_commit"` |
| spec | AgentPhase | role `spec_author`; inputs `(card, explore, spec_path)`; `results.SpecResult`; writes `docs/superpowers/specs/{stem}.md` |
| validate_spec | AgentPhase | role `critic`; inputs `(card, spec_path)`; `results.CriticResult`; gate `reducers.critic_blockers_gate` |
| plan | AgentPhase | role `planner`; inputs `(spec_path, plan_path)`; `results.PlanResult`; writes `docs/superpowers/plans/{stem}.md` |
| validate_plan | AgentPhase | role `critic`; inputs `(spec_path, plan_path)`; `results.CriticResult`; gate `reducers.critic_blockers_gate` |
| mark_validated | Step | run `plan_check.mark_validated` |
| docs_commit | Step | run `docs_commit.commit_documents` |
| implement | AgentPhase | role `coder`; inputs `(plan_path, spec_path, branch, base_branch, plan_hash)`; `results.ImplementResult`; gate `reducers.implement_blocked_gate` |
| review | AgentPhase | role `reviewer`; inputs `(branch, base_branch, plan_path)`; `results.ReviewResult`; gates `reducers.review_gate`, `reducers.plan_hash_gate_adapter` (in that order) |
| verify | Step | run `verify.run_suite`, gate `reducers.verification_passed_gate` |
| mark_done | Step | run `rollup.set_status`, args `{"status": "done"}`, `best_effort=True` |

`Retry.on` order must match the YAML's `on:` list exactly (the digest includes `repr(retry)`), as must gate order and input order.

`INTEGRATE` is `Workflow("integrate", ...)` mirroring `builtin/integrate.yaml`: `resolve` (AgentPhase, role `resolver`, inputs `(branch, base_branch, merge_tip, conflict_files, verification)`, `results.ResolveResult`, gate `steps.integrate.merge_completed_gate` (it lives in `steps/integrate.py`, not `reducers`; import it under an alias so it does not clash with the `workflow.integrate` module name), `Retry(2, ("gate_failed", "schema_invalid"))`), then `verify` (Step, run `verify.run_suite`, gate `reducers.verification_passed_gate`).

Per-phase timeouts (new data the YAML never had): the chosen values are explore 20 min, spec/plan 30 min, validate_spec/validate_plan 20 min, implement 90 min, review 45 min, resolve 30 min. `validate()` requires every agent timeout to be strictly greater than `LAUNCHER_TIMEOUT` (30 min), so any chosen value that is not above it is raised, never the launcher's lowered. Declare that floor once as a module constant, `LAUNCHER_TIMEOUT + timedelta(minutes=5)` (35 min), and give each agent phase `max(chosen, floor)`. That makes explore, spec, validate_spec, plan, validate_plan and resolve 35 min, implement 90 min and review 45 min. `integrate.py` imports `LAUNCHER_TIMEOUT` from `workflow.task` and does not define its own.

Neither module imports pygents (rule 1). Both are importable with no I/O. Building the objects touches no files, roles or git.

## Error paths

There are no runtime error paths of its own: both modules are constant data. Drift is caught by the tests. If a phase, field, order or callable disagrees with the shipped YAML, the digest assertion fails. If a role doesn't load, an input has no resolver or earlier phase, `when`/`skip_to` are inconsistent, or a timeout does not exceed the launcher's, `Workflow.validate()` raises `WorkflowError` naming the phase.

## Tests

All tests go in `tests/workflow/test_declared.py`, in the **pure-data tier** of the milestone spec §9 (`2026-09-25-pygents-engine-design.md`): no fakes, no git, no harness, next to `tests/workflow/test_phases.py`. None of them is a behavioural or engine-parity test, so none goes in `runtime/`, `steps/` or e2e. Follow the plan's skeleton (Task 1.3 Step 1) verbatim, including `_shipped()`, which copies each agent phase's timeout from the declared workflow onto the `from_loader` conversion before comparing digests.

1. `test_task_equals_the_shipped_yaml`: `TASK.digest() == _shipped("task", TASK).digest()`. Pure-data tier.
2. `test_integrate_equals_the_shipped_yaml`: `INTEGRATE.digest() == _shipped("integrate", INTEGRATE).digest()`. Pure-data tier.
3. `test_both_validate`: `TASK.validate(launcher_timeout=LAUNCHER_TIMEOUT)` and `INTEGRATE.validate(launcher_timeout=LAUNCHER_TIMEOUT)` both return without raising. Pure-data tier. Its only I/O is `validate()`'s role loading.

Acceptance: `uv run pytest` passes as a whole suite, including `tests/e2e` on both engines (rule 2).
