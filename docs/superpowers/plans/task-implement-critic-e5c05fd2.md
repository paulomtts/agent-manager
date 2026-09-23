<!-- task-pipeline: validated -->
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

---

# `critic_blockers_gate` and Gate Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `critic_blockers_gate` a real, ported implementation and make every gate named in `builtin/task.yaml` bind and fire correctly against real, dumped result models.

**Architecture:** Three small, independent seams. The gate itself is one more pure function in `steps/reducers.py` registered as the real callable. Binding is fixed without touching reducer *semantics*: `engine.subtask_context` gains a `base_branch` alias (reserved, so no phase result can take it over), and `workflow/registry.py` gains one module-level adapter that turns the two phase results `implement` and `review` into `plan_hash_gate`'s two hash arguments. A read-compatibility shim in `reducers.py` lets the two JS-era camelCase field reads also see the snake_case keys a validated model dumps. A data-driven acceptance test in `tests/workflow/test_builtin_task.py` then binds and runs every gate of every phase against `model_dump(mode="json")` output, so a future unbindable gate fails in CI.

**Tech Stack:** Python 3, `uv`, pytest, Pydantic v2 (`strict=True`, `extra="forbid"`), PyYAML-backed workflow documents.

**Spec:** `docs/superpowers/specs/task-implement-critic-e5c05fd2-design.md` (prepended verbatim above).

## Global Constraints

- Verification is `uv run pytest` and nothing else. There is no separate lint or typecheck command (`CLAUDE.md`).
- Source lives under `src/agent_manager/`, tests mirror it under `tests/`. One pytest suite, no tiers, no markers.
- Gates are pure: no filesystem, network, model calls, or module-level mutable state. Malformed *content* produces a verdict, never an exception.
- A gate returns `None` to pass, a mapping verdict to fail, or `{"warn": ...}`; anything else is a broken gate.
- `BUILTIN_FUNCTION_NAMES` and `TASK_YAML_NAMES` are unchanged by this card: no new registered name is added or removed.
- Every existing assertion in `tests/steps/test_reducers.py` stays green, unmodified. That file is the ported gates' specification.
- Do not touch `src/agent_manager/results.py`, `results.RESULT_MODELS`, `dispatch.AgentRunner.result_models`, or the `model_dump(mode="json")` call at `dispatch.py:244`. Those belong to siblings c873fc52 and 29d51ff8.
- Do not edit `src/agent_manager/workflow/builtin/task.yaml`: yaml `args` are literals and cannot express any of the bindings this card needs.
- `resolve(name) is <a module-level callable>` must hold across two separate `default_registry()` calls for every registered name.
- Exact literal strings that must appear verbatim in verdicts: `"validation"`, `"spec has unresolvable blockers"`, `"the validator returned nothing"`.

## Review Focus

- A `blockers: true` critic result whose `reason` is a non-string (a dict or a list, which a language model does produce) must render as JS-style text in the detail, not as a Python `repr` and not as a `TypeError` — covered in Task 1.
- A resumed run whose `implement` phase never executed in-process leaves no `implement` key in the binding table; `plan_hash_gate` must still bind and pass rather than raising `EngineError` at the `review` phase — covered in Task 4.
- A review result that arrives spelled camelCase (a hand-written result file, or any future caller that dumps `by_alias=True`) must still be judged, not silently warned past — covered in Task 5 (the existing camelCase fixtures, kept green).
- A caller passing `base_branch` in `extra_context` must be refused rather than silently shadowing the engine's own alias with a branch the engine never derived — covered in Task 3.
- A gate added to `builtin/task.yaml` later, with a parameter nothing supplies, must fail at test time rather than at the first real run — covered in Task 6 (the data-driven parametrisation plus the guard asserting which gates the document names).

---

### Task 1: The `critic_blockers_gate` reducer

**Files:**
- Modify: `src/agent_manager/steps/reducers.py` (append after `verification_passed_gate`, which ends at line 326)
- Test: `tests/steps/test_reducers.py` (import list at lines 12-23; new tests appended at the end)

**Interfaces:**
- Consumes: `reducers._field(mapping, name) -> object` (returns `None` for a non-Mapping), `reducers._js_text(value) -> str`, `collections.abc.Mapping` (already imported at `reducers.py:20`).
- Produces: `reducers.critic_blockers_gate(result: object) -> dict[str, str] | None`. Task 2 registers this exact object under the name `critic_blockers_gate`; Task 6 binds it on `validate_spec` and `validate_plan`.

- [ ] **Step 1: Add `critic_blockers_gate` to the test module's import list**

In `tests/steps/test_reducers.py`, change the import block (lines 12-23) so the new name is imported alphabetically between `count_of` and `exploration_output_gate`:

```python
from agent_manager.steps.reducers import (
    _is_integer,
    _js_text,
    count_of,
    critic_blockers_gate,
    exploration_output_gate,
    is_plan_hash,
    plan_hash_gate,
    plan_hash_mismatch,
    review_gate,
    verification_gate,
    verification_passed_gate,
)
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `tests/steps/test_reducers.py`:

```python
# ── critic_blockers_gate ─────────────────────────────────────────────────────
# Ported from task.js lines 631-638 and 717-721. The critic REPORTS blockers on
# the spec or the plan and never acts on them; this gate is the stop, and it
# serves both `validate_spec` and `validate_plan` from one callable.

CRITIC_SUMMARY = (
    "the spec pins the blocked value and the two detail fallbacks, and both "
    "validation phases share this gate."
)


def test_a_critic_that_found_no_blockers_lets_the_run_continue():
    result = {"blockers": False, "reason": None, "summary": CRITIC_SUMMARY}
    assert critic_blockers_gate(result) is None


def test_blockers_stop_the_run_at_validation_and_carry_the_critics_reason():
    result = {
        "blockers": True,
        "reason": "the spec contradicts section 4 of the design",
        "summary": CRITIC_SUMMARY,
    }
    assert critic_blockers_gate(result) == {
        "blocked": "validation",
        "detail": "the spec contradicts section 4 of the design",
    }


@pytest.mark.parametrize("useless", [None, "", "   ", "\n\t ", False, 0])
def test_blockers_with_no_usable_reason_fall_back_to_the_js_wording(useless):
    # JS: `reason || 'spec has unresolvable blockers'`. A blank reason must not
    # produce an empty detail: the operator would have nothing to act on.
    assert critic_blockers_gate({"blockers": True, "reason": useless}) == {
        "blocked": "validation",
        "detail": "spec has unresolvable blockers",
    }


def test_a_result_with_no_reason_key_at_all_still_blocks():
    gate = critic_blockers_gate({"blockers": True})
    assert gate["detail"] == "spec has unresolvable blockers"


def test_a_dead_validator_is_itself_a_block():
    # Silence is not consent: a validation phase that produced no judgement has
    # not cleared anything, and reading that as a pass is how an unvalidated
    # plan reaches `implement`.
    assert critic_blockers_gate(None) == {
        "blocked": "validation",
        "detail": "the validator returned nothing",
    }


@pytest.mark.parametrize("dead", ["blockers", ["blockers"], 7, 0, True, object()])
def test_a_non_mapping_result_is_the_dead_validator_verdict(dead):
    assert critic_blockers_gate(dead) == {
        "blocked": "validation",
        "detail": "the validator returned nothing",
    }


@pytest.mark.parametrize("truthy", [1, "yes", ["one"], {"a": 1}, 0.5, -1])
def test_any_truthy_blockers_value_blocks_without_raising(truthy):
    gate = critic_blockers_gate({"blockers": truthy, "reason": None})
    assert gate["blocked"] == "validation"


@pytest.mark.parametrize("falsy", [False, 0, "", None, [], {}])
def test_any_falsy_blockers_value_passes(falsy):
    assert critic_blockers_gate({"blockers": falsy, "reason": "ignored"}) is None


def test_a_missing_blockers_key_passes_rather_than_blocking():
    # A mapping that reached the gate at all was schema-validated upstream; the
    # dead-validator branch is for no mapping, not for a thin one.
    assert critic_blockers_gate({"summary": CRITIC_SUMMARY}) is None


def test_a_non_string_reason_is_rendered_js_style_not_as_a_python_repr():
    gate = critic_blockers_gate({"blockers": True, "reason": {"missing": ["step 4"]}})
    assert gate["detail"] == '{"missing":["step 4"]}'


def test_a_boolean_reason_renders_as_json_not_as_python():
    assert critic_blockers_gate({"blockers": True, "reason": True})["detail"] == "true"
```

- [ ] **Step 3: Run the tests and verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -x -q`
Expected: collection error — `ImportError: cannot import name 'critic_blockers_gate' from 'agent_manager.steps.reducers'`.

- [ ] **Step 4: Write the gate**

Append to `src/agent_manager/steps/reducers.py`, after `verification_passed_gate` (the file currently ends at line 326):

```python


# The `validate_spec` / `validate_plan` gate (`builtin/task.yaml` lines 40 and
# 54), ported from task.js lines 631-638 and 717-721. The critic is asked to
# REPORT whether the spec or the plan has unresolvable blockers and never to
# decide what to do about them, for the reason `review_gate` exists: an agent
# that both measures and judges can talk itself out of the judgement. One
# callable serves both phases -- the engine's own failure message already names
# which one stopped (`phase 'validate_plan' gate 'critic_blockers_gate'
# failed: ...`), so a per-phase `blocked` value would only duplicate it.
def critic_blockers_gate(result: object) -> dict[str, str] | None:
    """``None`` when the critic found no blockers, else a blocked verdict.

    ``result`` is the critic phase's own result: both ``engine._gate_values``
    and ``dispatch.gate_values`` place it under exactly that key, which is why
    the parameter is not named after either phase.

    A dead validator -- ``None``, or anything that is not a ``Mapping`` -- is
    itself a block, checked before ``blockers`` rather than falling out of its
    falsiness. Silence is not consent: a validation phase that produced no
    judgement has not cleared the spec, and reading that as a pass is how an
    unvalidated plan reaches ``implement``.
    """
    if not isinstance(result, Mapping):
        return {"blocked": "validation", "detail": "the validator returned nothing"}
    if not _field(result, "blockers"):
        return None
    raw_reason = _field(result, "reason")
    # Mirrors JS `String(reason || '')`: any falsy value becomes the empty
    # string, and `_js_text` keeps a non-string readable as the harness JSON it
    # came from rather than as a Python repr.
    reason = ("" if not raw_reason else _js_text(raw_reason)).strip()
    return {
        "blocked": "validation",
        "detail": reason or "spec has unresolvable blockers",
    }
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -q`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat: port critic_blockers_gate from task.js"
```

---

### Task 2: Register the real `critic_blockers_gate`

**Files:**
- Modify: `src/agent_manager/workflow/registry.py` (the `default_registry` docstring at lines 199-205, the reducer block at lines 208-213, and the placeholder registration at lines 226-229)
- Test: `tests/workflow/test_registry.py` (lines 99-105 and 117-123)

**Interfaces:**
- Consumes: `reducers.critic_blockers_gate(result)` from Task 1.
- Produces: `default_registry().resolve("critic_blockers_gate") is reducers.critic_blockers_gate`. Task 6 relies on `workflow.function("critic_blockers_gate")` being the real, callable gate.

- [ ] **Step 1: Write the failing tests**

In `tests/workflow/test_registry.py`, add this assertion to `test_default_registry_resolves_the_five_reducers_to_the_real_callables` (line 99) and rename it, so the whole function reads:

```python
def test_default_registry_resolves_the_ported_reducers_to_the_real_callables() -> None:
    registry = default_registry()
    assert registry.resolve("exploration_output_gate") is reducers.exploration_output_gate
    assert registry.resolve("verification_gate") is reducers.verification_gate
    assert registry.resolve("review_gate") is reducers.review_gate
    assert registry.resolve("plan_hash_gate") is reducers.plan_hash_gate
    assert registry.resolve("verification_passed_gate") is reducers.verification_passed_gate
    assert registry.resolve("critic_blockers_gate") is reducers.critic_blockers_gate
```

Then replace `test_placeholders_resolve_at_load_time_and_raise_when_called` (lines 117-123) with:

```python
def test_the_one_remaining_placeholder_resolves_and_raises_when_called() -> None:
    """`steps/rollup.py` is still a sibling's; every other name is real code."""
    fn = default_registry().resolve("rollup.set_status")
    with pytest.raises(NotImplementedError) as caught:
        fn()
    assert "rollup.set_status" in str(caught.value)


def test_the_critic_gate_is_real_code_now_rather_than_a_placeholder() -> None:
    gate = default_registry().resolve("critic_blockers_gate")
    assert gate({"blockers": False, "reason": None, "summary": "explored"}) is None
    assert gate(None) == {
        "blocked": "validation",
        "detail": "the validator returned nothing",
    }
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/workflow/test_registry.py -x -q`
Expected: FAIL — `assert <function critic_blockers_gate at ...> is <function _unimplemented ...>` in the identity test, and `NotImplementedError` raised out of `test_the_critic_gate_is_real_code_now_rather_than_a_placeholder`.

- [ ] **Step 3: Register the real callable**

In `src/agent_manager/workflow/registry.py`, add a line to the reducer block so it reads:

```python
    # Gates ported from task.js -- siblings ef33352b and 5ee2ee50, done.
    registry.register("exploration_output_gate", reducers.exploration_output_gate)
    registry.register("verification_gate", reducers.verification_gate)
    registry.register("review_gate", reducers.review_gate)
    registry.register("plan_hash_gate", reducers.plan_hash_gate)
    registry.register("verification_passed_gate", reducers.verification_passed_gate)
    registry.register("critic_blockers_gate", reducers.critic_blockers_gate)
```

and delete the placeholder registration entirely (lines 226-229):

```python
    registry.register(
        "critic_blockers_gate",
        _placeholder("critic_blockers_gate", "the sibling subtask that adds the agent-phase gates"),
    )
```

- [ ] **Step 4: Correct the `default_registry` docstring**

In the same function's docstring, replace this paragraph:

```
    The five reducers and the four implemented steps are the real, imported
    callables -- not wrappers -- so `resolve(name) is the_function` holds and a
    sibling's bugfix reaches the engine without touching this table. The two
    remaining names have no implementation on this branch (`steps/rollup.py`
    does not exist; `critic_blockers_gate` is not in `steps/reducers.py`), so
    they resolve to placeholders.
```

with:

```
    The six reducers and the four implemented steps are the real, imported
    callables -- not wrappers -- so `resolve(name) is the_function` holds and a
    sibling's bugfix reaches the engine without touching this table. One name
    still has no implementation on this branch (`steps/rollup.py` does not
    exist), so `rollup.set_status` resolves to a placeholder.
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/workflow -q`
Expected: PASS (including `test_every_resolved_function_is_the_registry_binding` in `test_builtin_task.py`, which compares the document's resolved callables against the registry).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/workflow/registry.py tests/workflow/test_registry.py
git commit -m "feat: register the real critic_blockers_gate"
```

---

### Task 3: A `base_branch` alias in the engine's context

**Files:**
- Modify: `src/agent_manager/engine.py` (`RESERVED_CONTEXT_KEYS` at lines 37-48, `subtask_context` at lines 68-98)
- Test: `tests/test_engine.py` (lines 50-62, 79-81; new tests appended near them and at the end of the file)

**Interfaces:**
- Consumes: `models.SubtaskRun.base_branch: str`.
- Produces: `engine.subtask_context(...)` returns a dict with nine keys — the eight it has today plus `"base_branch"`, holding the same value as `"base"`. `RESERVED_CONTEXT_KEYS` grows to eleven names, gaining `"base_branch"`. Task 6 relies on `review_gate`'s `base_branch` parameter binding from this key.

- [ ] **Step 1: Write the failing tests**

In `tests/test_engine.py`, widen the exact-shape assertion in `test_subtask_context_renames_the_model_fields_the_steps_ask_for` (lines 50-62) to:

```python
def test_subtask_context_renames_the_model_fields_the_steps_ask_for():
    context = engine.subtask_context(_subtask(), REPO, ["uv run pytest"])

    assert context == {
        "card": "ed77a917",
        "card_details": None,
        "parent_story_details": None,
        "branch": "m1/task-ed77a917",
        "base": "m1/story-base",
        "base_branch": "m1/story-base",
        "worktree": Path("/repo/.claude/worktrees/m1/task-ed77a917"),
        "repo_dir": REPO,
        "commands": ["uv run pytest"],
    }
```

Widen the reserved-keys membership test (lines 79-81) to:

```python
def test_the_new_context_keys_are_reserved_against_a_same_named_phase():
    for key in (
        "card_details",
        "parent_story_details",
        "spec_path",
        "plan_path",
        "base_branch",
    ):
        assert key in engine.RESERVED_CONTEXT_KEYS
```

Add immediately after it:

```python
def test_the_base_branch_alias_is_the_same_string_the_steps_bind_as_base():
    """`review_gate(review, branch, base_branch)` binds by parameter name, and
    the deterministic steps bind the same value as `base`. One value, two keys,
    rather than a second source of truth for the base branch."""
    context = engine.subtask_context(_subtask(), REPO)
    assert context["base_branch"] == context["base"] == "m1/story-base"


def test_a_phase_named_base_branch_cannot_overwrite_the_alias():
    """Same rule as the `worktree` phase: a result must never replace a key a
    later gate binds from. `_bind_result` is called directly here because the
    rule is a property of that function, not of any particular document."""
    context = engine.subtask_context(_subtask(), REPO)
    engine._bind_result(context, "base_branch", {"branch": "somewhere/else"})
    assert context["base_branch"] == "m1/story-base"
```

And append at the end of the file, beside the other `extra_context` tests:

```python
def test_extra_context_may_not_redefine_the_base_branch_alias(tmp_path: Path):
    """A caller that could set `base_branch` would point `review_gate` at a base
    the engine never derived, while every step still used the real one."""
    registry = FunctionRegistry()
    registry.register("only.step", lambda: {"ok": True})
    document = tmp_path / "one.yaml"
    document.write_text(
        "name: one\n"
        "description: one deterministic phase\n"
        "phases:\n"
        "  - name: only\n"
        "    kind: deterministic\n"
        "    run: only.step\n",
        encoding="utf-8",
    )
    workflow = load_workflow(document, registry)
    store = store_module.Store.open(tmp_path, "run-extra-3")

    with pytest.raises(engine.EngineError) as caught:
        engine.run_subtask(
            workflow,
            store,
            story_id="story-1",
            subtask=_subtask(),
            repo_dir=REPO,
            extra_context={"base_branch": "somewhere/else"},
        )

    assert "base_branch" in str(caught.value)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/test_engine.py -x -q`
Expected: FAIL — the dict comparison reports a missing `'base_branch'` key, and `assert 'base_branch' in ('card', 'card_details', ...)` fails.

- [ ] **Step 3: Add the alias and reserve it**

In `src/agent_manager/engine.py`, add `"base_branch"` to `RESERVED_CONTEXT_KEYS` immediately after `"base"`:

```python
RESERVED_CONTEXT_KEYS = (
    "card",
    "card_details",
    "parent_story_details",
    "branch",
    "base",
    "base_branch",
    "worktree",
    "repo_dir",
    "commands",
    "spec_path",
    "plan_path",
)
```

and add the key to `subtask_context`'s returned dict, immediately after `"base"`:

```python
    return {
        "card": subtask.card_id,
        "card_details": card,
        "parent_story_details": parent_story,
        "branch": subtask.branch,
        "base": subtask.base_branch,
        "base_branch": subtask.base_branch,
        "worktree": subtask.worktree_path,
        "repo_dir": repo_dir,
        "commands": list(commands),
    }
```

- [ ] **Step 4: Document why the same value appears twice**

In `subtask_context`'s docstring, replace this sentence:

```
    binding is by name, and no deterministic phase in `builtin/task.yaml`
    declares `args` that could bridge the difference. Hence `base` for
    `base_branch` and `worktree` for `worktree_path`.
```

with:

```
    binding is by name, and no deterministic phase in `builtin/task.yaml`
    declares `args` that could bridge the difference. Hence `base` for
    `base_branch` and `worktree` for `worktree_path`.

    `base_branch` is that same string under a second key, because the two sides
    of the document disagree about the name: `worktree.ensure(branch, base,
    ...)` asks for `base`, and `reducers.review_gate(review, branch,
    base_branch)` asks for `base_branch`. An `args:` entry cannot bridge it --
    yaml `args` are literals and the base branch is per-run -- and renaming
    either parameter would change a shipped step or a ported gate. Both keys are
    reserved, so no phase result can make them disagree.
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest -q`
Expected: PASS. (`prompt._TABLE` resolves the `base_branch` *input* from the `base` key and is unaffected; `cli.resume_start_phase` filters `RESERVED_CONTEXT_KEYS` out of its producer search, and `base_branch` is not a phase name.)

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/engine.py tests/test_engine.py
git commit -m "feat: expose the base branch as base_branch so review_gate binds"
```

---

### Task 4: A registry adapter for `plan_hash_gate`

**Files:**
- Modify: `src/agent_manager/workflow/registry.py` (import line 16, a new module-level adapter above `BUILTIN_FUNCTION_NAMES` at line 174, the registration at line 212, and one docstring sentence)
- Test: `tests/workflow/test_registry.py` (the import block at lines 5-13, the reducer-identity test, and new tests)

**Interfaces:**
- Consumes: `reducers.plan_hash_gate(impl_hash: object, review_hash: object) -> dict[str, str] | None`.
- Produces: `registry.plan_hash_gate_adapter(implement: object = None, review: object = None) -> dict[str, str] | None`, a module-level function registered under the name `plan_hash_gate`. Task 6 binds it on the `review` phase from the `implement` and `review` keys of the gate table.

- [ ] **Step 1: Write the failing tests**

In `tests/workflow/test_registry.py`, extend the import block to bring in the adapter:

```python
from agent_manager.workflow.registry import (
    BUILTIN_FUNCTION_NAMES,
    DuplicateFunctionError,
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
    default_registry,
    plan_hash_gate_adapter,
)
```

Change the `plan_hash_gate` line of `test_default_registry_resolves_the_ported_reducers_to_the_real_callables` from

```python
    assert registry.resolve("plan_hash_gate") is reducers.plan_hash_gate
```

to

```python
    # The one name that is deliberately NOT the bare reducer: `plan_hash_gate`
    # compares two fields of two different phase results, and `bind_arguments`
    # binds whole values by parameter name only.
    assert registry.resolve("plan_hash_gate") is plan_hash_gate_adapter
    assert plan_hash_gate_adapter is not reducers.plan_hash_gate
```

Then append these tests to the file:

```python
def test_the_plan_hash_adapter_is_one_object_across_two_registries() -> None:
    """The same invariant `_PLACEHOLDERS` exists for: a closure built per call
    would make this the only name whose identity is unstable."""
    assert default_registry().resolve("plan_hash_gate") is default_registry().resolve(
        "plan_hash_gate"
    )


def test_the_plan_hash_adapter_compares_the_two_phases_plan_hash_fields() -> None:
    gate = plan_hash_gate_adapter(
        {"plan_hash": "a1b2c3d4", "report": "done"},
        {"plan_hash": "ffffffff", "porcelain": ""},
    )
    assert "plan hash CHANGED mid-run" in gate["detail"]
    assert "a1b2c3d4" in gate["detail"] and "ffffffff" in gate["detail"]
    assert "blocked" not in gate


def test_the_plan_hash_adapter_passes_when_the_two_hashes_match() -> None:
    assert (
        plan_hash_gate_adapter({"plan_hash": "a1b2c3d4"}, {"plan_hash": "a1b2c3d4"})
        is None
    )


@pytest.mark.parametrize("dead", [None, {}, "implement", 7, [{"plan_hash": "a1b2c3d4"}]])
def test_the_plan_hash_adapter_passes_when_either_phase_result_is_missing(dead) -> None:
    # A skipped or dead phase has no hash to compare; the reducer's own rule is
    # "nothing trustworthy to say" -> None.
    assert plan_hash_gate_adapter(dead, {"plan_hash": "a1b2c3d4"}) is None
    assert plan_hash_gate_adapter({"plan_hash": "a1b2c3d4"}, dead) is None


def test_the_plan_hash_adapter_binds_with_no_arguments_at_all() -> None:
    """Both parameters default to None so a run that skipped `implement` binds
    and passes, instead of `bind_arguments` reporting a required parameter."""
    assert plan_hash_gate_adapter() is None
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/workflow/test_registry.py -x -q`
Expected: collection error — `ImportError: cannot import name 'plan_hash_gate_adapter' from 'agent_manager.workflow.registry'`.

- [ ] **Step 3: Write the adapter**

In `src/agent_manager/workflow/registry.py`, extend the stdlib import on line 16:

```python
from collections.abc import Callable, Mapping, Sequence
```

and insert this immediately above `BUILTIN_FUNCTION_NAMES` (currently line 174):

```python
def _plan_hash_of(result: object) -> object:
    """The `plan_hash` field of a dumped phase result, or `None`.

    `None` rather than an error for a missing or non-mapping result: a phase
    that was skipped, escalated or returned nothing has no hash to compare, and
    `reducers.plan_hash_gate` already answers `None` (pass) for anything that is
    not an 8-hex-character string.
    """
    return result.get("plan_hash") if isinstance(result, Mapping) else None


def plan_hash_gate_adapter(
    implement: object = None, review: object = None
) -> dict[str, str] | None:
    """`reducers.plan_hash_gate` bound to the two phase results it compares.

    The document names this gate on the `review` phase, and
    `engine.bind_arguments` binds strictly by parameter name out of a table of
    whole values -- nothing in that table is called `impl_hash` or
    `review_hash`, and the yaml `args:` map holds literals, so neither could be
    written there either. This adapter takes the two names the table *does*
    hold, the phase names `implement` and `review`, and does the one field
    lookup the binder cannot do for itself. The reducer keeps its signature and
    its tests; nothing about the comparison moves.

    A module-level function rather than a closure built inside
    `default_registry()`: `resolve(name) is resolve(name)` must hold across two
    registries, which is the same invariant `_PLACEHOLDERS` exists to preserve.

    Both parameters default to `None` so a run where `implement` never executed
    in this process (a `plan_check` skip, or a resume started later) still binds
    and passes, rather than failing to bind and reading as a document bug.
    """
    return reducers.plan_hash_gate(_plan_hash_of(implement), _plan_hash_of(review))
```

- [ ] **Step 4: Register the adapter under the document's name**

In `default_registry()`, change

```python
    registry.register("plan_hash_gate", reducers.plan_hash_gate)
```

to

```python
    registry.register("plan_hash_gate", plan_hash_gate_adapter)
```

and append one sentence to the docstring paragraph Task 2 rewrote, so it ends:

```
    exist), so `rollup.set_status` resolves to a placeholder. `plan_hash_gate`
    is the single exception to the "no wrappers" rule: see
    `plan_hash_gate_adapter` above for why the binder cannot reach the two
    fields that gate compares.
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/workflow -q`
Expected: PASS, including `test_default_registry_holds_exactly_the_names_task_yaml_uses` (no name was added or removed) and `test_every_resolved_function_is_the_registry_binding`.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/workflow/registry.py tests/workflow/test_registry.py
git commit -m "feat: adapt plan_hash_gate to the implement/review phase results"
```

---

### Task 5: The camelCase/snake_case read shim

**Files:**
- Modify: `src/agent_manager/steps/reducers.py` (a new helper beside `_field` at lines 83-85; `review_gate` lines 187-188; `exploration_output_gate` line 272)
- Test: `tests/steps/test_reducers.py` (new tests appended; every existing assertion stays untouched)

**Interfaces:**
- Consumes: `reducers._field(mapping, name) -> object`.
- Produces: `reducers._either_field(mapping: object, snake: str, camel: str) -> object`. Private to the module; no signature of any gate changes.

- [ ] **Step 1: Confirm the drift is real before fixing it**

Run: `uv run python -c "from agent_manager.results import ReviewResult; print(sorted(ReviewResult(findings=[], unresolved_blockers=[], fix_summary='none', porcelain='', commit_count=3, tagged_count=3, plan_hash='a1b2c3d4').model_dump(mode='json')))"`
Expected: the printed list contains `commit_count` and `tagged_count` (snake_case), and no `commitCount`. If it prints camelCase instead, STOP: the spec's precondition is wrong and this task's premise with it.

- [ ] **Step 2: Write the failing tests**

Append to `tests/steps/test_reducers.py`:

```python
# ── snake_case results meet camelCase gates ──────────────────────────────────
# `results.ReviewResult` / `results.Verification` are snake_case and
# `dispatch.py` dumps them without `by_alias=True`, so a validated result
# reaches these gates spelled `commit_count` and `verification.full_suite`,
# while task.js wrote `commitCount` and `fullSuite`. Both spellings are read;
# no verdict, threshold or ordering depends on which one arrived.


def test_review_gate_reads_the_snake_case_counts_a_dumped_result_carries():
    assert (
        review_gate({"porcelain": "", "commit_count": 3, "tagged_count": 3}, BRANCH, BASE)
        is None
    )


def test_a_snake_case_zero_commit_count_blocks_instead_of_warning():
    # Before the shim this returned a warn: both camelCase reads were None, so
    # the no-commits stop could never fire against a real result.
    gate = review_gate({"porcelain": "", "commit_count": 0, "tagged_count": 0}, BRANCH, BASE)
    assert gate["blocked"] == "implement"
    assert "task-42 has no commits on top of main" in gate["detail"]


def test_snake_case_untagged_commits_still_block():
    gate = review_gate({"porcelain": "", "commit_count": 3, "tagged_count": 2}, BRANCH, BASE)
    assert gate["blocked"] == "implement"
    assert "only 2 of 3 commits" in gate["detail"]


def test_a_review_that_reports_neither_spelling_still_warns():
    gate = review_gate({"porcelain": ""}, BRANCH, BASE)
    assert gate["warn"]
    assert "blocked" not in gate


def test_exploration_output_gate_accepts_the_snake_case_suite_a_model_dumps():
    explore = {
        "summary": REAL_SUMMARY,
        "verification": {"full_suite": ["uv run pytest", "uv run ruff check ."]},
    }
    assert exploration_output_gate(explore, None) is None


@pytest.mark.parametrize("not_a_list", ["uv run pytest", {"0": "uv run pytest"}, 3, None])
def test_a_snake_case_suite_that_is_not_a_list_still_returns_the_array_verdict(not_a_list):
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"full_suite": not_a_list}}, None
    )
    assert gate["detail"] == "exploration did not return an array for verification.fullSuite"


def test_an_implausible_snake_case_command_is_still_caught():
    gate = exploration_output_gate(
        {"summary": REAL_SUMMARY, "verification": {"full_suite": ["a"]}}, None
    )
    assert "implausible command" in gate["detail"]
```

- [ ] **Step 3: Run the tests and verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -x -q`
Expected: FAIL — `test_review_gate_reads_the_snake_case_counts_a_dumped_result_carries` gets a `{"warn": ...}` dict instead of `None`, and the exploration snake_case test gets the "did not return an array" verdict.

- [ ] **Step 4: Add the helper**

In `src/agent_manager/steps/reducers.py`, immediately after `_field` (lines 83-85), insert:

```python


# These gates were ported from task.js and read the camelCase keys the JS
# harness wrote. `results.py`'s models are snake_case, and `dispatch.py` dumps
# them without `by_alias=True`, so BOTH spellings genuinely reach a gate: a
# validated model dump is snake_case, and a hand-written result file (or the
# ported fixtures in `tests/steps/test_reducers.py`) is camelCase. Reading both
# is a compatibility shim on the read, not a second rule -- no verdict text,
# threshold or ordering below depends on which spelling arrived. Doing it here,
# in one helper, is what keeps the alternative from happening: two registry
# wrappers that would quietly become a second place gate semantics live.
def _either_field(mapping: object, snake: str, camel: str) -> object:
    """``snake``'s value if it has one, else ``camel``'s, else ``None``."""
    value = _field(mapping, snake)
    return _field(mapping, camel) if value is None else value
```

- [ ] **Step 5: Use it at the two drifted reads**

In `review_gate`, replace lines 187-188:

```python
    raw_commit = _field(review, "commitCount")
    raw_tagged = _field(review, "taggedCount")
```

with:

```python
    raw_commit = _either_field(review, "commit_count", "commitCount")
    raw_tagged = _either_field(review, "tagged_count", "taggedCount")
```

In `exploration_output_gate`, replace line 272:

```python
    full_suite = _field(_field(explore, "verification"), "fullSuite")
```

with:

```python
    full_suite = _either_field(
        _field(explore, "verification"), "full_suite", "fullSuite"
    )
```

Leave the `provided_verification` read on line 280 (`_field(provided_verification, "fullSuite")`) alone: that value is the caller's own dict, not a validated result model — `cli.gate_context` passes `None` and the orchestrator's shape is the JS `verification` object. The verdict text keeps saying `verification.fullSuite` for the same reason: it is the name the agent's prompt and the harness JSON use, so changing it would send an operator looking for a key nobody wrote.

- [ ] **Step 6: Run the tests and verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -q`
Expected: PASS — the new tests plus every pre-existing camelCase assertion in the file, unmodified.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "fix: read both spellings of the counts and the full suite"
```

---

### Task 6: Acceptance #2 — every gate binds and fires against real results

**Files:**
- Modify: `tests/workflow/test_builtin_task.py` (imports at lines 4-17; everything else appended at the end)

**Interfaces:**
- Consumes: `workflow.load_builtin("task")` → `Workflow` with `.phases`, `.phase_names`, `.function(name)`; `engine.subtask_context(subtask, repo_dir, commands, *, card, parent_story)`; `engine.bind_arguments(fn, values, args=None, *, phase, function)`; `engine.RESERVED_CONTEXT_KEYS`; `dispatch.gate_values(context, phase_name, result)`; `cli.gate_context(commands, allow_no_verification)`; the six result classes in `agent_manager.results`; `models.SubtaskRun`, `models.Card`.
- Produces: no source change. This task adds the regression net only.

- [ ] **Step 1: Write the failing test module additions**

Replace the import block at the top of `tests/workflow/test_builtin_task.py` (lines 4-17) with:

```python
import inspect
from pathlib import Path
from typing import Any

import pytest

from agent_manager import cli, dispatch, engine, models
from agent_manager.results import (
    CriticResult,
    ExploreResult,
    ImplementResult,
    PlanResult,
    ReviewResult,
    Verification,
)
from agent_manager.workflow import load_builtin
from agent_manager.workflow.loader import (
    AgentPhase,
    DeterministicPhase,
    builtin_path,
)
from agent_manager.workflow.registry import (
    FunctionRegistry,
    UnknownFunctionError,
    WorkflowLoadError,
    default_registry,
)
```

Then append to the end of the file:

```python
# ── acceptance #2: every gate binds against a real result object ─────────────
# Design §3: "Every gate named in builtin/task.yaml binds against a real result
# object." Real means a result MODEL instance dumped with
# `model_dump(mode="json")`, exactly as `dispatch.py:244` hands it to the gates
# -- not a hand-written dict, which is how the camelCase drift survived.

REPO_DIR = Path("/repo")
SUITE = ["uv run pytest"]
PLAN_HASH = "a1b2c3d4"
SPEC_PATH = "docs/superpowers/specs/implement-critic-e5c05fd2.md"
PLAN_PATH = "docs/superpowers/plans/implement-critic-e5c05fd2.md"
REAL_SUMMARY = (
    "engine.bind_arguments binds gate parameters by name out of the table "
    "engine._gate_values builds, so every gate in builtin/task.yaml has to name "
    "keys that table actually holds."
)

SUBTASK = models.SubtaskRun(
    card_id="e5c05fd2",
    branch="m2/task-implement-critic-e5c05fd2",
    base_branch="m2/story-result-contract",
    status="started",
    worktree_path=Path("/repo/.claude/worktrees/m2/task-implement-critic-e5c05fd2"),
)
SUBTASK_CARD = models.Card(
    id="6f1a2f2e-1f1c-4f0e-9a6d-0c2f3b4a5d6e",
    title="Implement critic_blockers_gate and bind every gate parameter",
    status="todo",
    parent_id="5cc741ec-2a3b-4c5d-8e9f-0a1b2c3d4e5f",
)
PARENT_CARD = models.Card(
    id="5cc741ec-2a3b-4c5d-8e9f-0a1b2c3d4e5f",
    title="The result contract: models, gates and bindings",
    status="in_progress",
)


def _explore_result(full_suite: list[str] | None = None) -> dict[str, Any]:
    return ExploreResult(
        refused=False,
        reason=None,
        summary=REAL_SUMMARY,
        verification=Verification(
            full_suite=list(SUITE if full_suite is None else full_suite),
            typecheck="",
            lint=[],
        ),
    ).model_dump(mode="json")


def _critic_result(blockers: bool = False) -> dict[str, Any]:
    return CriticResult(
        blockers=blockers,
        reason="the plan skips the read shim" if blockers else None,
        summary=REAL_SUMMARY,
    ).model_dump(mode="json")


def _plan_result() -> dict[str, Any]:
    return PlanResult(path=PLAN_PATH, self_reviewed=True, note=None).model_dump(
        mode="json"
    )


def _implement_result() -> dict[str, Any]:
    return ImplementResult(
        blocked=False,
        blocked_reason=None,
        resumed=False,
        plan_hash=PLAN_HASH,
        report=REAL_SUMMARY,
    ).model_dump(mode="json")


def _review_result(commit_count: int = 3, tagged_count: int = 3) -> dict[str, Any]:
    return ReviewResult(
        findings=[],
        unresolved_blockers=[],
        fix_summary="nothing needed fixing",
        porcelain="",
        commit_count=commit_count,
        tagged_count=tagged_count,
        plan_hash=PLAN_HASH,
    ).model_dump(mode="json")


def _phase_results() -> dict[str, Any]:
    """What each phase leaves in the binding table on a healthy run.

    Agent phases leave their validated result's JSON dump (`dispatch.py:244`);
    deterministic phases leave whatever mapping their step returned
    (`engine._bind_result`), which is why those are plain dicts. `worktree` is
    absent on purpose: its name is reserved, so its result never reaches the
    table at all.
    """
    return {
        "explore": _explore_result(),
        "mark_in_progress": {"status": "in_progress"},
        "plan_check": {"found": False},
        "spec": {"path": SPEC_PATH},
        "validate_spec": _critic_result(),
        "plan": _plan_result(),
        "validate_plan": _critic_result(),
        "implement": _implement_result(),
        "review": _review_result(),
        "verify": {"passed": True, "detail": ""},
    }


def _values_for(phase_name: str, result: Any = None) -> dict[str, Any]:
    """The binding table this phase's gates really see, built as production does.

    `engine.subtask_context` plus the caller's own `cli.gate_context` plus the
    document paths, then every earlier phase's result under its own name, then
    `dispatch.gate_values`' `result` / `<phase name>` overlay --
    `engine._gate_values` builds the identical table for the deterministic
    `verify` phase.
    """
    results = _phase_results()
    context = engine.subtask_context(
        SUBTASK, REPO_DIR, SUITE, card=SUBTASK_CARD, parent_story=PARENT_CARD
    )
    context.update(cli.gate_context(SUITE, False))
    context.update({"spec_path": SPEC_PATH, "plan_path": PLAN_PATH})
    for name in load_builtin("task").phase_names:
        if name == phase_name:
            break
        if name in results and name not in engine.RESERVED_CONTEXT_KEYS:
            context[name] = results[name]
    return dispatch.gate_values(
        context, phase_name, results[phase_name] if result is None else result
    )


GATED_PHASES = [
    (phase.name, gate) for phase in load_builtin("task").phases for gate in phase.gates
]


def test_the_document_still_names_exactly_the_gates_this_suite_covers() -> None:
    """The parametrisation below is only a net if this list is the document's.
    A gate added to `task.yaml` must land here, and then in `_phase_results`."""
    assert GATED_PHASES == [
        ("explore", "exploration_output_gate"),
        ("explore", "verification_gate"),
        ("validate_spec", "critic_blockers_gate"),
        ("validate_plan", "critic_blockers_gate"),
        ("review", "review_gate"),
        ("review", "plan_hash_gate"),
        ("verify", "verification_passed_gate"),
    ]


@pytest.mark.parametrize(("phase_name", "gate_name"), GATED_PHASES)
def test_every_gate_binds_every_parameter_against_real_results(
    phase_name: str, gate_name: str
) -> None:
    """Seam 3 closed: no gate in the shipped document has a parameter the gate
    table cannot supply. A future unbindable gate fails right here."""
    gate = load_builtin("task").function(gate_name)
    bound = engine.bind_arguments(
        gate, _values_for(phase_name), phase=phase_name, function=gate_name
    )
    assert set(bound) == set(inspect.signature(gate).parameters)


@pytest.mark.parametrize(("phase_name", "gate_name"), GATED_PHASES)
def test_every_gate_passes_on_a_healthy_run(phase_name: str, gate_name: str) -> None:
    gate = load_builtin("task").function(gate_name)
    bound = engine.bind_arguments(
        gate, _values_for(phase_name), phase=phase_name, function=gate_name
    )
    assert gate(**bound) is None


def test_review_gate_reads_a_real_dumped_zero_commit_count_and_blocks() -> None:
    """The assertion that would have caught the camelCase drift: binding alone
    would not, because a vacuous gate binds perfectly well and returns a warn."""
    gate = load_builtin("task").function("review_gate")
    values = _values_for("review", _review_result(commit_count=0, tagged_count=0))
    verdict = gate(
        **engine.bind_arguments(gate, values, phase="review", function="review_gate")
    )
    assert verdict["blocked"] == "implement"
    assert "no commits on top of m2/story-result-contract" in verdict["detail"]


def test_review_gate_reads_a_real_dumped_untagged_count_and_blocks() -> None:
    gate = load_builtin("task").function("review_gate")
    values = _values_for("review", _review_result(commit_count=3, tagged_count=1))
    verdict = gate(
        **engine.bind_arguments(gate, values, phase="review", function="review_gate")
    )
    assert verdict["blocked"] == "implement"
    assert "only 1 of 3 commits" in verdict["detail"]


def test_the_plan_hash_gate_compares_the_two_real_dumped_hashes() -> None:
    gate = load_builtin("task").function("plan_hash_gate")
    values = _values_for("review")
    values["implement"] = ImplementResult(
        blocked=False,
        blocked_reason=None,
        resumed=False,
        plan_hash="0badcafe",
        report=REAL_SUMMARY,
    ).model_dump(mode="json")
    verdict = gate(
        **engine.bind_arguments(gate, values, phase="review", function="plan_hash_gate")
    )
    assert "plan hash CHANGED mid-run" in verdict["detail"]


def test_exploration_output_gate_matches_a_caller_provided_suite() -> None:
    # `provided_verification` is the caller's own dict, not a result model, so
    # it keeps the harness JSON's `fullSuite` spelling; the explore side is the
    # snake_case dump of a real `ExploreResult`.
    gate = load_builtin("task").function("exploration_output_gate")
    values = _values_for("explore")
    values["provided_verification"] = {"fullSuite": list(SUITE)}
    bound = engine.bind_arguments(
        gate, values, phase="explore", function="exploration_output_gate"
    )
    assert gate(**bound) is None


def test_exploration_output_gate_still_catches_an_implausible_dumped_suite() -> None:
    gate = load_builtin("task").function("exploration_output_gate")
    values = _values_for("explore", _explore_result(full_suite=["a"]))
    bound = engine.bind_arguments(
        gate, values, phase="explore", function="exploration_output_gate"
    )
    assert "implausible command" in gate(**bound)["detail"]


@pytest.mark.parametrize("phase_name", ["validate_spec", "validate_plan"])
def test_critic_blockers_gate_fires_on_both_validation_phases(phase_name: str) -> None:
    gate = load_builtin("task").function("critic_blockers_gate")
    values = _values_for(phase_name, _critic_result(blockers=True))
    bound = engine.bind_arguments(
        gate, values, phase=phase_name, function="critic_blockers_gate"
    )
    assert gate(**bound) == {
        "blocked": "validation",
        "detail": "the plan skips the read shim",
    }


def test_verification_passed_gate_blocks_a_red_suite_on_the_verify_phase() -> None:
    gate = load_builtin("task").function("verification_passed_gate")
    values = _values_for("verify", {"passed": False, "detail": "2 failed, 0 passed"})
    bound = engine.bind_arguments(
        gate, values, phase="verify", function="verification_passed_gate"
    )
    assert gate(**bound) == {"blocked": "verification", "detail": "2 failed, 0 passed"}
```

- [ ] **Step 2: Run the tests and verify they pass**

Run: `uv run pytest tests/workflow/test_builtin_task.py -q`
Expected: PASS. Every one of these depends on Tasks 1-5: without Task 3 the `review_gate` binding raises `EngineError` for `base_branch`, without Task 4 it raises for `impl_hash`, without Task 2 `critic_blockers_gate` raises `NotImplementedError`, and without Task 5 the `review_gate` and `exploration_output_gate` behaviour assertions fail.

- [ ] **Step 3: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, no skips introduced, no warnings about unknown marks.

- [ ] **Step 4: Commit**

```bash
git add tests/workflow/test_builtin_task.py
git commit -m "test: bind and run every builtin gate against real dumped results"
```

---

## Completion note for the card

Record on card e5c05fd2, per the spec's "Flag to the story":

> This card touched `steps/reducers.py` (one private `_either_field` read shim, no verdict/threshold/ordering change — every pre-existing assertion in `tests/steps/test_reducers.py` is untouched and green) and `engine.subtask_context`/`RESERVED_CONTEXT_KEYS` (a `base_branch` alias for the same value as `base`), which the card text framed as registry/yaml-only work. Yaml `args` are literals and could not express either binding. The alternative to the shim — registry wrappers normalising the input of `review_gate` and `exploration_output_gate` — would have cost two more adapters and two more identity assertions for the same effect, and would have put gate semantics in two places. `results.py`, `RESULT_MODELS` and the `dispatch.py` dump call were not touched; `unresolved_blockers` is still read by no reducer, noted and not fixed here.
