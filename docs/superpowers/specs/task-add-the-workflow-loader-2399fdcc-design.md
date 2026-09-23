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
