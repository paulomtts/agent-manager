# e5c05fd2 — Implement `critic_blockers_gate` and bind every gate parameter

Parent story: 5cc741ec "The result contract: models, gates and bindings" (milestone 7aa00a90).
Source of truth: `docs/superpowers/specs/2026-09-23-real-harness-design.md` §1 seam 3, §2 R1, §3 acceptance #2.

## Scope

Close seam 3 of the real-harness design: "gates that cannot bind". Three deliverables, all in this repo:

1. A real `critic_blockers_gate` in `src/agent_manager/steps/reducers.py`, registered in `src/agent_manager/workflow/registry.py` in place of its `_placeholder(...)`.
2. Every gate named in `src/agent_manager/workflow/builtin/task.yaml` bindable through `engine.bind_arguments` against the table `engine._gate_values` / `dispatch.gate_values` actually build.
3. A test that loads the builtin document and binds every gate of every phase against real, dumped result objects, so a future unbindable gate fails at test time.

Out of scope, owned elsewhere: defining/registering the five result models and `results.RESULT_MODELS` (siblings c873fc52 and 29d51ff8), `AgentRunner.result_models` defaulting, the toy-repo smoke re-run, prompt briefs (next story), milestone orchestration, `rollup.*`, and everything in addendum §4.

## Precondition (verified)

The five result classes (`ExploreResult`, `CriticResult`, `PlanResult`, `ImplementResult`, `ReviewResult`, plus `Verification`) now exist in `src/agent_manager/results.py`; `RESULT_MODELS` is still an empty dict there (29d51ff8's card). Tests therefore import the real classes from `agent_manager.results` and build result objects from them, dumped with `model_dump(mode="json")` exactly as `dispatch.py` line 244 does. Do not use stand-in models, do not hand-write dicts, and do not populate `RESULT_MODELS`.

Important: `ReviewResult.commit_count`/`tagged_count` and `Verification.full_suite` carry `serialization_alias` camelCase names, but `dispatch.py` dumps WITHOUT `by_alias=True`, so the aliases are inert and the dumped mapping is snake_case (`commit_count`, `tagged_count`, `verification.full_suite`). The reducers read camelCase, so the §3 drift is real. The read shim in §3 is the fix; do not change `results.py` or the dump call. Confirm with a one-line check that `ReviewResult(...).model_dump(mode="json")` has `commit_count` before relying on this.

Model field sets (all fields required, `extra="forbid"`, `strict=True`): `ExploreResult{refused, reason, summary, verification{full_suite, typecheck, lint}}`, `CriticResult{blockers, reason, summary}`, `PlanResult{path, self_reviewed, note}`, `ImplementResult{blocked, blocked_reason, resumed, plan_hash, report}`, `ReviewResult{findings, unresolved_blockers, fix_summary, porcelain, commit_count, tagged_count, plan_hash}`. `reason`/`note`/`blocked_reason` are `str | None`; `full_suite` is `list[str]`, `typecheck` is `str`, `lint` is `list[str]`.

## 1. `critic_blockers_gate`

Ported from `task.js` lines 631-638 and 717-721 of `/home/paulomtts/Code/leave-me-alone/plugins/leave-me-alone/workflows/task.js`. Lives in `steps/reducers.py` beside the other ported gates, with the same house rules: pure, no I/O, no module-level mutable state, never raises on malformed *content*, uses the module's `_field` / `_js_text` helpers.

Signature: `critic_blockers_gate(result: object) -> dict[str, str] | None`. The parameter is named `result` because both `engine._gate_values` and `dispatch.gate_values` always place the phase's own result under that key, and the same callable must serve both `validate_spec` and `validate_plan`.

Observable behaviour:

- Result is a `Mapping` with a falsy `blockers` → returns `None` (pass).
- Result is a `Mapping` with a truthy `blockers` → returns `{"blocked": "validation", "detail": <reason>}` where `<reason>` is the result's `reason` when it is non-empty text, otherwise the JS fallback `"spec has unresolvable blockers"`.
- Result is `None`, or anything that is not a `Mapping` (a dead validator) → returns `{"blocked": "validation", "detail": "the validator returned nothing"}`. `_field` already returns `None` for a non-Mapping, so the dead-result branch is a `blockers`-independent check, not an accident of falsiness.
- Never raises, for any input, including objects whose `reason` is a non-string (render it through `_js_text`).

`"validation"` is the `blocked` value on purpose: `task.js` names the stopped stage (`blocked: 'validation'`), matching the convention of the other gates (`"tests"`, `"implement"`, `"verification"`). It is used for both `validate_spec` and `validate_plan` — the phase is identifiable from the engine's own message (`phase 'validate_plan' gate 'critic_blockers_gate' failed: ...`), so the gate does not need to differ. The chosen value is asserted by a unit test, not left implicit.

Registration: `registry.register("critic_blockers_gate", reducers.critic_blockers_gate)`, the real callable, so `default_registry().resolve("critic_blockers_gate") is reducers.critic_blockers_gate`. `BUILTIN_FUNCTION_NAMES` is unchanged (the name is already listed); the placeholder loop in `tests/workflow/test_registry.py` narrows to `rollup.set_status` only, and the reducer-identity test gains this gate.

## 2. Making every gate bindable

`engine.bind_arguments` binds by parameter name from `values` overlaid with the phase's literal yaml `args`, raising `EngineError` for an `args` key that is not a parameter and for a required parameter with no value. The table gates see is `{**subtask_context, **extra_context, <earlier phase names>: <dumped results>, "result": result, <this phase>: result}`. Constraint from the card: **change the registry or the yaml `args`, not the ported reducers' semantics** — `tests/steps/test_reducers.py` is their specification and every existing assertion in it stays green.

Per gate:

- `exploration_output_gate(explore, provided_verification)` — `explore` binds from the phase name; `provided_verification` comes from the caller's `extra_context`. Already bindable. But see §3: against a dumped `ExploreResult` it reads `verification.fullSuite` and finds nothing.
- `verification_gate(suite_cmds, allow_no_verification, caller_provided)` — all three from `extra_context`, per the `run_subtask` docstring. Already bindable.
- `critic_blockers_gate(result)` — binds from `result`. Bindable by construction.
- `review_gate(review, branch, base_branch)` — `review` and `branch` bind; `base_branch` does not, because `subtask_context` calls that key `base`. Fix: `subtask_context` additionally exposes the base branch under `base_branch`, and `base_branch` joins `RESERVED_CONTEXT_KEYS` (a phase result must not be able to take over a key gates bind from; `base` is already reserved for the same reason). The alias is the least invasive fix available and it preserves `resolve("review_gate") is reducers.review_gate`. Yaml `args` cannot help: `args` values are literals, and the base branch is per-run. Tests in `tests/test_engine.py` that pin the contents of `RESERVED_CONTEXT_KEYS` or the exact `subtask_context` dict are updated to match.
- `plan_hash_gate(impl_hash, review_hash)` — nothing supplies either name, and neither is expressible as a literal `args` value: they are the `plan_hash` fields of the `implement` and `review` results. Fix: a thin adapter defined in `workflow/registry.py` taking `implement` and `review` (the two phase names, which are in the table by the time `review`'s gates run) and delegating to `reducers.plan_hash_gate(_field(implement, "plan_hash"), _field(review, "plan_hash"))`. It must be a single module-level function object (not a closure built per `default_registry()` call) so `resolve(name) is resolve(name)` still holds across two registries, the invariant `_PLACEHOLDERS` exists to preserve. It must tolerate a missing or non-Mapping `implement`/`review` (a skipped or dead phase) by passing `None` through — `plan_hash_gate` already returns `None` for anything that is not an 8-hex-char string. `BUILTIN_FUNCTION_NAMES` is unchanged (same name); the identity assertion for `plan_hash_gate` in `tests/workflow/test_registry.py` changes from "is the reducer" to "is the registry adapter", with a comment saying why.
- `verification_passed_gate(result)` — already bindable.

`plan_hash_gate` is reachable only on the `review` phase, where `implement` has run. If `implement` was skipped, the adapter sees no `implement` key and `bind_arguments` would raise `EngineError` for a required parameter — so the adapter's parameters carry `= None` defaults, making a skipped phase a pass rather than a document bug.

## 3. Spelling reconciliation (the one place reducers change)

R1 says the models are snake_case "and spelled the way `steps/reducers.py` reads them", and that the implementer "reconciles the spelling in one place, with a test". Two reducers read camelCase keys inherited from `task.js`:

- `review_gate` reads `_field(review, "commitCount")` and `"taggedCount"`; `ReviewResult` dumps `commit_count` / `tagged_count`. Against a real dumped review result both counts read `None`, so the gate can only ever return its `warn` branch — silently vacuous, and the no-commits and untagged-commits stops never fire.
- `exploration_output_gate` reads `_field(_field(explore, "verification"), "fullSuite")`; `ExploreResult` dumps `verification.full_suite`. Against a real dumped explore result the gate always returns "did not return an array for verification.fullSuite" — a false block.

Resolution: the *field reads* accept both spellings (snake_case preferred, camelCase fallback), via one small helper in `reducers.py` used by both sites. No verdict text, threshold or ordering changes; every existing assertion in `tests/steps/test_reducers.py` (which feeds camelCase fixtures throughout) stays green unmodified. This is a read-compatibility shim, not a semantics change, and it is the narrowest reading of "don't change the reducers' semantics" that still lets acceptance #2 mean anything — binding alone would leave both gates vacuous against real results.

`unresolved_blockers` is read by no reducer. That is noted, not fixed here.

Flag to the story: this card touches `reducers.py` and `engine.subtask_context`/`RESERVED_CONTEXT_KEYS`, which the card text framed as registry/yaml-only work. The alternative (normalising inside registry adapters for `review_gate` and `exploration_output_gate`) would cost two more adapters and two more identity-assertion changes for the same effect; the shim was chosen. Say so in the card's completion note.

## Error paths

- A gate that cannot bind raises `EngineError` naming the phase, function and parameter — unchanged behaviour, now unreachable for the builtin document and proven so by the §4 test.
- A gate that raises comes back as a `fatal` `gate_failed` from `dispatch.evaluate_gates`. `critic_blockers_gate` must never take that path: no input shape makes it raise.
- A malformed critic result (missing `blockers`, `reason` of the wrong type, not a mapping at all) is a verdict, never an exception.
- `plan_hash_gate`'s adapter with a non-Mapping `implement` or `review` yields `None` (pass), matching the reducer's existing "nothing trustworthy to say" rule.

## Test list

Placement rule: per `CLAUDE.md`, "tests mirror [source] under `tests/`". There is a single pytest suite (`uv run pytest`, `testpaths = ["tests"]`, no markers, no tiers) — the only placement decision is which mirrored file a test belongs in.

`tests/steps/test_reducers.py` (mirrors `src/agent_manager/steps/reducers.py`):

1. `blockers: false` with a summary → gate returns `None`.
2. `blockers: true` with a `reason` → `{"blocked": "validation", "detail": <reason>}`; the `blocked` value is asserted literally.
3. `blockers: true` with no/empty/whitespace `reason` → detail is `"spec has unresolvable blockers"`.
4. `None` result → `{"blocked": "validation", "detail": "the validator returned nothing"}`.
5. Non-Mapping results (a string, a list, an `object()`, an integer) → the same dead-result verdict, parametrised; none raise.
6. Odd content (`blockers` truthy-but-not-bool, `reason` a non-string) → a verdict, no exception, detail rendered via `_js_text`.
7. `review_gate` reads snake_case counts: `commit_count=0` blocks with `blocked="implement"`; `tagged_count < commit_count` blocks; equal non-zero counts pass. Existing camelCase tests remain and must still pass.
8. `exploration_output_gate` accepts `verification.full_suite` as well as `verification.fullSuite` — a plausible snake_case explore result passes, and a snake_case non-list still returns the array verdict.

`tests/workflow/test_registry.py` (mirrors `src/agent_manager/workflow/registry.py`):

9. `resolve("critic_blockers_gate") is reducers.critic_blockers_gate` (added to the reducer-identity test).
10. The placeholder test covers only `rollup.set_status`; `critic_blockers_gate` no longer raises `NotImplementedError` when called.
11. `resolve("plan_hash_gate")` is the registry adapter, and is the *same object* across two `default_registry()` calls.
12. The adapter delegates: given `implement`/`review` mappings with differing valid plan hashes it returns the reducer's `{"detail": ...}`; with matching, missing or non-Mapping inputs it returns `None`.
13. `default_registry().names() == BUILTIN_FUNCTION_NAMES == TASK_YAML_NAMES` still holds (no new names).

`tests/workflow/test_builtin_task.py` (mirrors `workflow/builtin/task.yaml`) — the acceptance #2 test:

14. For every phase in `workflow.load_builtin("task")` and every gate name on it, `engine.bind_arguments(workflow.function(name), values, phase=..., function=...)` succeeds, where `values` is built exactly as production does: `engine.subtask_context(...)` plus the caller's `extra_context` (`provided_verification`, `suite_cmds`, `allow_no_verification`, `caller_provided`), plus each earlier phase's result stored under its phase name as `model_dump(mode="json")`, plus `dispatch.gate_values`' `result`/`<phase name>` overlay. Data-driven over `workflow.phases`, so a gate added later with an unbindable parameter fails here.
15. The bound gates actually *run* on those same real results and return `None` — a healthy run passes every gate end to end.
16. `review_gate` bound from a real dumped review result with `commit_count=0` returns the `blocked="implement"` verdict, not a `warn`. This is the assertion that would have caught the camelCase drift; binding alone would not.
17. `exploration_output_gate` bound from a real dumped explore result (with `provided_verification` matching the model's `full_suite`) returns `None`, and returns a verdict when the suite is implausible.
18. `critic_blockers_gate` binds and fires on both `validate_spec` and `validate_plan` from a real dumped critic result with `blockers=true`.

`tests/test_engine.py` (mirrors `src/agent_manager/engine.py`):

19. `subtask_context` exposes the base branch under both `base` and `base_branch`, with the same value; `base_branch` is in `RESERVED_CONTEXT_KEYS`, so `extra_context` supplying it is refused and a phase named `base_branch` does not overwrite it. Existing reserved-key and context-shape assertions updated accordingly.
