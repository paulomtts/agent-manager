<!-- task-pipeline: validated -->
# Give the spec phase a result — design

Card `be376902` (subtask of story `360cd141`, "Close the seams the wiring test found", milestone `7aa00a90`). Blocked by `f68d29e2` (done: the worktree phase now runs before any agent phase).

## Problem

The `spec` phase in `src/agent_manager/workflow/builtin/task.yaml` is the one agent phase with no `result:`. Two consequences, both found by the wiring test:

1. The spec author's brief carries no result contract at all (`prompt._result_contract` returns `None` when both `result_path` and `result_model` are absent), so the only evidence the phase produces is a file it claims to have written, judged by nothing.
2. The result-less path through `dispatch.classify` is not actually safe. `classify(outcome, result_path, model)` calls `result_path.is_file()` before it ever looks at `model`, so a phase with no result model is still required to leave a `result.json` behind or it is journalled `harness_error`. `AgentRunner._attempt` already passes `None` for the *brief's* result path (`dispatch.py:473`) while handing `classify` the concrete `dispatch_record.result_path` (`dispatch.py:508`) — the two halves of "this phase has no result" disagree.

## Scope

Three source changes and their tests. Everything else on the milestone is out: `steps/rollup.py` and its registry entry belong to sibling `43008688` and are not touched here; ancestor roll-up, milestone orchestration, parallel stories, `integrate`, non-Claude harnesses and addendum section 4 are all out of scope.

### 1. `SpecResult` in `src/agent_manager/results.py`

A new `SpecResult(_Result)` with exactly two fields, `path: str` and `note: str | None`, shaped after `PlanResult` (which already carries `path` and `note`) minus `self_reviewed`. As in `PlanResult`, `note` has no default: it is required but nullable, so the agent must write `"note": null` rather than omit it, and a result missing `note` is `schema_invalid` too. Nothing asks the spec author to self-review, so `self_reviewed` is dropped, because nothing asks the spec author to self-review. It inherits `_Result`'s `extra="forbid"`/`strict=True`, and needs no `serialization_alias` on either field: no reducer in `steps/reducers.py` reads a spec result, so there is no camelCase port to honour (contrast `ReviewResult.commit_count`).

Registered in `RESULT_MODELS` under the key `"SpecResult"`, keeping the table's rule that the key is the class name. The module docstring currently enumerates five names (`ExploreResult`, `CriticResult`, `PlanResult`, `ImplementResult`, `ReviewResult`); it becomes six, and the paragraph explaining that `Verification` is absent stays as it is.

`SpecResult.path` is informational. `engine._document_paths` derives the `spec_path` input from the phase's `writes:` template (`_DOCUMENT_INPUTS`), not from the result, so this card does **not** rewire `spec_path`; the field exists so the agent states where it wrote, and so `validate_spec` and any future cross-check have the claim on record.

### 2. `result: SpecResult` in `workflow/builtin/task.yaml`

Added to the `spec` phase, which today declares only `role: spec_author`, `inputs: [card, explore]` and `writes: docs/superpowers/specs/{stem}.md`. The `writes:` line stays — it is what `spec_path` resolves from. No `gates:` and no `retry:` are added: neither is in this card, and the phase's failure mode stays "schema_invalid or harness_error, one attempt".

After this, every agent phase in the shipped document has a `result:`, which is the property the tests below pin.

### 3. The result-less guard in `dispatch.classify`

Observable behaviour, in `src/agent_manager/dispatch.py`:

- Signature becomes `classify(outcome: Outcome, result_path: Path | None, model: type[BaseModel] | None) -> Verdict`.
- When `model is None`, the verdict is decided on exit status alone: a timeout is `harness_error` with the existing "timed out" detail, a non-zero exit is `harness_error` with the existing "exited N" detail, and a clean exit is `Verdict("ok", result=None)`. Nothing on disk is opened, `result_path` is never dereferenced, and the function must not raise for any combination of `result_path=None` and outcome.
- When `model` is not `None`, the order of §6 line 278 is unchanged and unconditional: timeout, then non-zero exit, then missing file (`harness_error`), then unreadable/non-UTF-8/non-JSON/invalid (`schema_invalid`), then `Verdict("ok", result=validated.model_dump(mode="json"))`. Gates still run after `ok`.

This replaces the current model-less branch, which reads the file and accepts any JSON object as the result. That branch's "must hold a JSON object" `schema_invalid` verdict disappears with it, because nothing is read any more; a phase that declares no model now has no result to be invalid.

`_attempt` is the call site that supplies the `None`: it passes `None if model is None else dispatch_record.result_path` to `classify`, mirroring what line 473 already does for `compose_brief`. `models.Dispatch.result_path` stays a required `Path` and `build_dispatch` keeps computing `attempt_dir / RESULT_NAME` — `harness/claude.py:138` dereferences `d.result_path.is_absolute()` when building the argv, and widening the model to `Path | None` would push a `None` check into every adapter for no gain. `models.Attempt.result_path` is already `Path | None`, so it needs no change either; the attempt row keeps recording the path the attempt directory would have used, which is what `cli.read_artifact` reads.

The guard is defensive: no phase in the shipped `task.yaml` is result-less once change 2 lands. It is therefore exercised with synthetic workflow documents, not the builtin one.

### Brief

No change to `prompt.py` is needed. Once `spec` declares `SpecResult`, `_attempt` resolves the model, passes the attempt's `result.json` path and the model to `compose_brief`, and `_result_contract` emits the heading, the absolute path and the fenced `SpecResult.model_json_schema()` exactly as it does for every other phase. The work is in asserting it.

## Error paths

- A `result:` name with no entry in `RESULT_MODELS` is still an `EngineError` from `resolve_result_model`, raised during resolution before any attempt is journalled — a typo in `SpecResult` fails the document, it does not fail an attempt.
- A spec attempt that writes no `result.json` after a clean exit is now `harness_error` (this is a behaviour change for the `spec` phase, and the intended one).
- A spec attempt whose `result.json` is missing `path`, carries an extra key, or gives `path` a non-string under `strict=True` is `schema_invalid`. With no `retry:` block on the phase the budget is one attempt, so it surfaces as `AgentPhaseFailed`.
- `classify` with `result_path=None` and `model=None` never raises, for any outcome.

## Test list

Tiers are the design spec's §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md` lines 477-492).

**Pure-functions tier — `tests/test_results.py`**

1. `SpecResult` validates a well-formed `{"path": ..., "note": ...}` and round-trips through `model_dump(mode="json")` with snake_case keys and no aliases.
2. `SpecResult` rejects an unknown key (`extra="forbid"`) and rejects a non-string `path` (`strict=True`), matching the assertions the other result models already carry.
3. `RESULT_MODELS["SpecResult"] is SpecResult`, and the table now holds exactly the six expected names — adjust whatever existing test pins the table's contents rather than adding a parallel one.

**Pure-functions tier — `tests/test_prompt.py`**

4. Extend the brief's result-contract coverage (the block around lines 566-712) so the `spec` phase's contract is asserted the way `implement`'s is: the brief names an absolute `result.json` path and `_fenced_json(brief) == SpecResult.model_json_schema()`, with `path` present in the schema's `properties`. This can be a parametrisation over the result models rather than a hand-copied test.
5. Keep `test_a_phase_with_no_result_gets_no_contract_section` as-is — it drives `compose_brief` directly with a synthetic role, not the builtin document, so it stays valid and is now the only guard on the contract-less brief.

**Pure-functions tier — `tests/workflow/test_builtin_task.py`**

6. The `("spec", "agent")` entry in `EXPECTED_PHASES` (line 47) keeps its position; the phase-order test is unchanged.
7. A `spec`-phase test in the style of `test_explore_carries_both_gates_and_its_retry_policy`: `role == "spec_author"`, `inputs == ["card", "explore"]`, `result == "SpecResult"`, `writes` still the specs template, no gates, no retry.
8. `test_every_declared_result_name_resolves_through_the_shipped_table` (line 153-ish): the non-vacuity count goes from 6 to 7.
9. A new assertion that *every* `AgentPhase` in the loaded document declares a `result:` — this is the seam the card closes, and without it a future phase can be added result-less unnoticed.
10. `_phase_results()`' canned `"spec": {"path": SPEC_PATH}` (line 290-ish) becomes `SpecResult(path=SPEC_PATH, note=None).model_dump(mode="json")`, via a `_spec_result()` helper alongside `_plan_result()`, so the gate-binding acceptance tests bind against a real dumped model like every other phase.

**Pure-functions tier — `tests/test_dispatch.py` (`classify` is a pure function; these use `tmp_path` only for the stdout log the `Outcome` points at)**

11. `classify(clean_outcome, None, None)` is `Verdict("ok", result=None)` and raises nothing.
12. `classify(timeout_outcome, None, None)` is `harness_error` with the timeout detail; `classify(exit_2_outcome, None, None)` is `harness_error` with the exit detail.
13. A result-less phase with a `result.json` sitting on disk still classifies `ok` with `result is None` — the file is not read. This replaces `test_a_phase_with_no_result_model_takes_the_json_object_as_its_result` and `test_a_json_array_with_no_result_model_classifies_schema_invalid`, both of which encode the removed behaviour and should be deleted.
14. The model-bearing order tests (valid, schema-invalid, non-JSON, non-UTF-8, missing file, non-zero exit, timeout, "stdout is never the channel") are unchanged and must still pass — they are the regression net on §6 line 278.

**Engine tier — `tests/test_dispatch.py` (fake adapter, injected launcher, canned result files)**

15. The two existing runs of a synthetic result-less `spec` document (around lines 886 and 1114) now expect `runner(...)` to return `None`, with `FakeLauncher(results=[None])` writing no result file, and the second still asserting `prompt.RESULT_HEADING not in brief`. These are the end-to-end proof that the guard holds through `_attempt`.
16. A run of a synthetic `spec` phase that *does* declare `SpecResult`, with a canned valid result file, returns the JSON-mode dump `{"path": ..., "note": ...}` and journals the attempt `ok`; with a canned result missing `path`, the attempt is `schema_invalid` and the phase fails.

No steps-tier, adapters-tier or e2e test is added: nothing here touches git, a `brd` board, `build_command`'s argv or a real harness.

## Verification

`uv run pytest` (CLAUDE.md: the full suite; there is no separate lint or typecheck).

---

# Give the spec phase a result — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `spec` phase a validated result model, bind it in the shipped workflow document, and make `dispatch.classify` safe for a phase that declares no result model at all.

**Architecture:** Three source edits and their tests. `results.SpecResult` joins `RESULT_MODELS` under its class name; `workflow/builtin/task.yaml`'s `spec` phase declares `result: SpecResult` so every agent phase now carries one; `dispatch.classify` gains a `model is None` branch that decides on exit status alone, and `AgentRunner._attempt` passes `None` for the result path when there is no model, mirroring what it already does for `compose_brief`. No new module, no new seam: the brief's result contract falls out of `prompt._result_contract` unchanged.

**Tech Stack:** Python 3, pydantic v2 (`extra="forbid"`, `strict=True`), PyYAML-backed workflow loader, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-give-the-spec-phase-a-be376902/docs/superpowers/specs/task-give-the-spec-phase-a-be376902-design.md` (prepended verbatim above)

**Worktree / branch:** all work happens in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-give-the-spec-phase-a-be376902` on branch `m2/task-give-the-spec-phase-a-be376902`. Every path below is relative to that worktree root.

## Global Constraints

- The full suite is `uv run pytest`. There is no separate lint or typecheck command (CLAUDE.md).
- Source lives under `src/agent_manager/`; tests mirror it under `tests/`.
- Pydantic models only for process-boundary data (harness result files); internal-only state stays a dataclass.
- `_Result` is `extra="forbid"` and `strict=True`, model-wide. `SpecResult` inherits both and adds no `serialization_alias` on either field.
- `note` is required but nullable: no default, so an omitted `note` is `schema_invalid`.
- §6 line 278's order for model-bearing phases is unchanged and unconditional: timeout → non-zero exit → missing file (`harness_error`) → unreadable/non-UTF-8/non-JSON/invalid (`schema_invalid`) → `ok` with `model_dump(mode="json")`. Gates run after `ok`.
- `models.Dispatch.result_path` stays a required `Path`; `build_dispatch` keeps computing `attempt_dir / RESULT_NAME`. `models.py` is not edited by this plan.
- Out of scope, do not touch: `src/agent_manager/steps/rollup.py` and its registry entry (sibling card `43008688`), ancestor roll-up, milestone orchestration, `integrate`, non-Claude harnesses.

## Review Focus

- A result-less phase whose harness wrote a `result.json` anyway: the file must be ignored, not read back as the phase result — pinned in Task 3.
- `classify` on a timeout with `result_path=None`: must return `harness_error` without dereferencing the `None` — pinned in Task 3.
- A spec `result.json` that omits `note` entirely: must be `schema_invalid`, not silently `None` — pinned in Task 1.
- A spec `result.json` carrying an extra key or a non-string `path`: `schema_invalid`, and with no `retry:` block the phase fails after exactly one dispatch — pinned in Tasks 1 and 5.
- A result-less attempt row must still journal a `result_path` (that is what `cli.read_artifact` reads to find the attempt directory), even though `classify` was handed `None` — pinned in Task 3.

---

### Task 1: `SpecResult` and its registry entry

**Files:**
- Modify: `src/agent_manager/results.py:1-14` (module docstring), `src/agent_manager/results.py:54-72` (new class between `CriticResult` and `PlanResult`), `src/agent_manager/results.py:108-114` (`RESULT_MODELS`)
- Test: `tests/test_results.py`, `tests/test_dispatch.py` (one hardcoded table assertion, ~line 1055)

**Interfaces:**
- Consumes: `results._Result` (the `extra="forbid"`/`strict=True` base), `results.resolve_result_model(name, table, *, phase)`.
- Produces: `results.SpecResult(path: str, note: str | None)` and `results.RESULT_MODELS["SpecResult"] is results.SpecResult`. Tasks 2, 4 and 5 all depend on exactly these names.

- [ ] **Step 1: Write the failing model tests**

Insert this block in `tests/test_results.py` immediately above `def test_plan_result_accepts_a_full_payload():` (currently line 227):

```python
def test_spec_result_accepts_a_full_payload():
    spec = results.SpecResult(
        path="docs/superpowers/specs/task-give-the-spec-phase-a-be376902-design.md",
        note=None,
    )

    assert spec.path.endswith("be376902-design.md")
    assert spec.note is None


def test_spec_result_dumps_snake_case_keys_and_round_trips():
    # No serialization_alias on either field: no reducer reads a spec result, so
    # the dumped mapping and the validated mapping name one spelling each.
    spec = results.SpecResult(
        path="docs/superpowers/specs/s.md", note="narrowed the card to three edits"
    )

    dumped = spec.model_dump(mode="json")

    assert dumped == {
        "path": "docs/superpowers/specs/s.md",
        "note": "narrowed the card to three edits",
    }
    assert results.SpecResult(**dumped) == spec


def test_spec_result_rejects_a_missing_note():
    # `note` is nullable but required: the agent writes "note": null explicitly,
    # exactly as PlanResult and CriticResult already demand.
    with pytest.raises(ValidationError) as caught:
        results.SpecResult(path="docs/superpowers/specs/s.md")

    assert "note" in str(caught.value)


def test_spec_result_rejects_a_missing_path():
    with pytest.raises(ValidationError) as caught:
        results.SpecResult(note=None)

    assert "path" in str(caught.value)


def test_spec_result_does_not_coerce_a_non_string_path():
    with pytest.raises(ValidationError) as caught:
        results.SpecResult(path=["a", "b"], note=None)

    assert "path" in str(caught.value)


def test_spec_result_rejects_an_unknown_key():
    # PlanResult's self_reviewed is the likeliest stray key: nothing asks the
    # spec author to self-review, so it is an unknown key here.
    with pytest.raises(ValidationError) as caught:
        results.SpecResult(
            path="docs/superpowers/specs/s.md", note=None, self_reviewed=True
        )

    assert "self_reviewed" in str(caught.value)
    assert "self_reviewed" not in results.SpecResult.model_fields


def test_the_spec_phases_result_name_resolves_to_the_spec_result_class():
    assert results.RESULT_MODELS["SpecResult"] is results.SpecResult
    assert (
        results.resolve_result_model("SpecResult", results.RESULT_MODELS, phase="spec")
        is results.SpecResult
    )
```

- [ ] **Step 2: Update the two existing table tests in the same file**

Replace `tests/test_results.py:36-45` (`test_the_shipped_table_holds_exactly_the_five_declared_names`) with:

```python
def test_the_shipped_table_holds_exactly_the_six_declared_names():
    # An equality, not a superset: a stray seventh key is a name the engine would
    # happily resolve for a phase that has no business declaring it.
    assert set(results.RESULT_MODELS) == {
        "ExploreResult",
        "CriticResult",
        "SpecResult",
        "PlanResult",
        "ImplementResult",
        "ReviewResult",
    }
```

Replace `tests/test_results.py:64-76` (`test_an_unknown_name_now_lists_the_five_registered_names`) with the version below. `"SpecResult"` can no longer serve as the unknown name — it resolves now — so the probe becomes `"VerifyResult"`, a name no phase declares:

```python
def test_an_unknown_name_now_lists_the_six_registered_names():
    # The addendum's opening failure ends "(registered: nothing)". That exact
    # phrasing must be gone, and the six sorted names must be what it offers.
    with pytest.raises(EngineError) as caught:
        results.resolve_result_model("VerifyResult", results.RESULT_MODELS, phase="verify")

    message = str(caught.value)
    assert caught.value.phase == "verify"
    assert "registered: nothing" not in message
    assert (
        "CriticResult, ExploreResult, ImplementResult, PlanResult, ReviewResult, "
        "SpecResult" in message
    )
```

- [ ] **Step 3: Add the schema assertion to the existing schema test**

In `tests/test_results.py`, inside `test_the_embedded_json_schema_names_snake_case_only`, immediately after the `PlanResult` block (currently lines 560-564), add:

```python
    spec_schema = results.SpecResult.model_json_schema()
    assert set(spec_schema["required"]) == {"path", "note"}
    assert set(spec_schema["properties"]) == {"path", "note"}
    assert spec_schema["additionalProperties"] is False
```

- [ ] **Step 3b: Update the hardcoded table assertion in `tests/test_dispatch.py`**

In `test_the_production_runner_factory_carries_the_shipped_table` (~line 1055), add `"SpecResult",` to the literal set of five names (it becomes six). Without this the test fails once Task 1 registers the model.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_results.py -v`
Expected: FAIL — the new tests raise `AttributeError: module 'agent_manager.results' has no attribute 'SpecResult'`, and `test_the_shipped_table_holds_exactly_the_six_declared_names` fails on the missing `"SpecResult"` key.

- [ ] **Step 5: Add the model**

In `src/agent_manager/results.py`, insert between `CriticResult` (ends line 59) and `PlanResult` (starts line 62):

```python
class SpecResult(_Result):
    """The `spec` phase's result file (`builtin/task.yaml`, the `spec` phase).

    `PlanResult`'s shape minus `self_reviewed`: nothing asks the spec author to
    self-review, so a result claiming it is an unknown key. `path` is what the
    agent says it wrote; `engine._document_paths` still derives `spec_path` from
    the phase's `writes:` template, so this field is the agent's claim on record
    rather than the engine's input. No `serialization_alias` on either field: no
    reducer in `steps/reducers.py` reads a spec result, so there is no camelCase
    port to honour.
    """

    path: str
    note: str | None
```

- [ ] **Step 6: Register it and update the module docstring**

In `src/agent_manager/results.py`, change the `RESULT_MODELS` literal (lines 108-114) to:

```python
RESULT_MODELS: dict[str, type[BaseModel]] = {
    "ExploreResult": ExploreResult,
    "CriticResult": CriticResult,
    "SpecResult": SpecResult,
    "PlanResult": PlanResult,
    "ImplementResult": ImplementResult,
    "ReviewResult": ReviewResult,
}
```

And change the docstring's enumeration (lines 7-9) from the five names to the six:

```python
`RESULT_MODELS` is that mapping: every `result:` name `builtin/task.yaml`
declares -- `ExploreResult`, `CriticResult`, `SpecResult`, `PlanResult`,
`ImplementResult`, `ReviewResult` -- against the model that validates that
phase's `result.json`.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_results.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: PASS. (`test_the_production_runner_factory_carries_the_shipped_table` pins the five names literally and was updated in Step 3b.)

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/results.py tests/test_results.py tests/test_dispatch.py
git commit -m "feat: add SpecResult and register it in RESULT_MODELS"
```

---

### Task 2: Bind `result: SpecResult` on the shipped `spec` phase

**Files:**
- Modify: `src/agent_manager/workflow/builtin/task.yaml:29-33` (the `spec` phase)
- Test: `tests/workflow/test_builtin_task.py`

**Interfaces:**
- Consumes: `results.SpecResult` and `RESULT_MODELS["SpecResult"]` from Task 1; `workflow.load_builtin("task")`, `loader.AgentPhase`, `results.resolve_result_model`.
- Produces: `load_builtin("task").phase("spec").result == "SpecResult"`, and the invariant that every `AgentPhase` in the shipped document has a non-`None` `result`.

- [ ] **Step 1: Write the failing document tests**

In `tests/workflow/test_builtin_task.py`, add these two tests immediately after `test_explore_carries_both_gates_and_its_retry_policy` (which ends at line 120):

```python
def test_spec_declares_the_spec_result_and_still_writes_the_specs_document():
    phase = load_builtin("task").phase("spec")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "spec_author"
    assert phase.inputs == ["card", "explore"]
    assert phase.result == "SpecResult"
    # `writes:` stays: engine._document_paths derives spec_path from this
    # template, not from the result's `path` field.
    assert phase.writes == "docs/superpowers/specs/{stem}.md"
    assert phase.gates == []
    assert phase.retry is None


def test_every_agent_phase_in_the_shipped_document_declares_a_result():
    """The seam this card closes: an agent phase with no `result:` produces no
    evidence a model ever validates. Asserted over the loaded document so a
    future result-less phase fails here rather than in production."""
    agent_phases = [
        phase for phase in load_builtin("task").phases if isinstance(phase, AgentPhase)
    ]

    # Non-vacuity: seven agent phases, the same count the worktree-order test pins.
    assert len(agent_phases) == 7
    assert [phase.name for phase in agent_phases if phase.result is None] == []
```

- [ ] **Step 2: Raise the non-vacuity count in the resolution test**

In `tests/workflow/test_builtin_task.py`, change the last line of `test_every_declared_result_name_resolves_through_the_shipped_table` (line 188) from:

```python
    assert checked == 6  # CriticResult is declared by two phases
```

to:

```python
    assert checked == 7  # CriticResult is declared by two phases
```

- [ ] **Step 3: Make the canned `spec` binding a real dumped model**

In `tests/workflow/test_builtin_task.py`, add `SpecResult` to the imports from `agent_manager.results` (lines 18-27), keeping the list alphabetical-ish as it already is:

```python
from agent_manager.results import (
    RESULT_MODELS,
    CriticResult,
    ExploreResult,
    ImplementResult,
    PlanResult,
    ReviewResult,
    SpecResult,
    Verification,
    resolve_result_model,
)
```

Add a helper immediately above `_plan_result` (line 268):

```python
def _spec_result() -> dict[str, Any]:
    return SpecResult(path=SPEC_PATH, note=None).model_dump(mode="json")
```

And in `_phase_results` change line 309 from `"spec": {"path": SPEC_PATH},` to:

```python
        "spec": _spec_result(),
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_builtin_task.py -v`
Expected: FAIL — `test_spec_declares_the_spec_result_and_still_writes_the_specs_document` asserts `"SpecResult" == None`, `test_every_agent_phase_in_the_shipped_document_declares_a_result` reports `['spec']`, and the resolution test still counts 6.

- [ ] **Step 5: Add the `result:` line to the document**

In `src/agent_manager/workflow/builtin/task.yaml`, change the `spec` phase (lines 29-33) to:

```yaml
  - name: spec
    kind: agent
    role: spec_author
    inputs: [card, explore]
    result: SpecResult
    writes: docs/superpowers/specs/{stem}.md
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_builtin_task.py -v`
Expected: PASS, all tests in the file, including the unchanged `test_builtin_task_has_the_twelve_phases_in_spec_order` and `test_no_agent_phase_precedes_the_worktree_phase`.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS. `tests/test_cli.py` replaces `dispatch.AgentRunner` wholesale, so no CLI test dispatches this phase for real.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/workflow/builtin/task.yaml tests/workflow/test_builtin_task.py
git commit -m "feat: declare result: SpecResult on the builtin spec phase"
```

---

### Task 3: The result-less guard in `classify`

**Files:**
- Modify: `src/agent_manager/dispatch.py:198-259` (`classify`), `src/agent_manager/dispatch.py:508` (the `_attempt` call site)
- Test: `tests/test_dispatch.py` (pure `classify` tests around lines 337-441; the two engine-tier result-less runs at lines 880-897 and 1107-1131)

**Interfaces:**
- Consumes: `dispatch.Verdict(status, result=None, detail=None, fatal=False)`, `harness.base.Outcome`, `models.Dispatch.result_path` (still a required `Path`).
- Produces: `dispatch.classify(outcome: Outcome, result_path: Path | None, model: type[BaseModel] | None) -> Verdict`. Task 5's engine-tier tests rely on the model-bearing half of this being untouched.

- [ ] **Step 1: Write the failing pure `classify` tests and delete the two obsolete ones**

In `tests/test_dispatch.py`, delete `test_a_phase_with_no_result_model_takes_the_json_object_as_its_result` (lines 378-385) and `test_a_json_array_with_no_result_model_classifies_schema_invalid` (lines 388-397) — both encode the behaviour this task removes — and put these in their place:

```python
def test_a_result_less_phase_is_ok_with_no_result_at_all(tmp_path):
    # The guard: no model means nothing is read and nothing is returned. A
    # result-less phase's evidence is its exit status, and that is all.
    verdict = dispatch.classify(_outcome(tmp_path), None, None)

    assert verdict == dispatch.Verdict("ok", result=None)
    assert verdict.detail is None
    assert verdict.fatal is False


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "fragment"),
    [(None, True, "timed out"), (2, False, "exited 2")],
)
def test_a_result_less_phase_judges_a_bad_exit_on_status_alone(
    tmp_path, exit_code, timed_out, fragment
):
    # Review Focus: result_path is None here, so a guard placed after the
    # is_file() call would raise AttributeError instead of classifying.
    outcome = _outcome(tmp_path, exit_code=exit_code, timed_out=timed_out)

    verdict = dispatch.classify(outcome, None, None)

    assert verdict.status == "harness_error"
    assert fragment in verdict.detail
    assert verdict.result is None


def test_a_result_less_phase_never_reads_a_result_file_that_is_there(tmp_path):
    # Review Focus: a harness that writes result.json anyway must not have it
    # adopted as the phase result -- a phase with no model has no result.
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"wrote": "docs/spec.md"}), encoding="utf-8")

    verdict = dispatch.classify(_outcome(tmp_path), result, None)

    assert verdict.status == "ok"
    assert verdict.result is None


def test_a_result_less_phase_with_no_file_on_disk_is_still_ok(tmp_path):
    # The missing-file harness_error belongs to model-bearing phases only.
    verdict = dispatch.classify(_outcome(tmp_path), tmp_path / "absent.json", None)

    assert verdict.status == "ok"
    assert verdict.result is None
```

- [ ] **Step 2: Rewrite the two engine-tier result-less runs to expect `None`**

In `tests/test_dispatch.py`, replace `test_a_phase_with_no_result_model_declared_needs_no_table_entry` (lines 880-897) with:

```python
def test_a_phase_with_no_result_model_declared_needs_no_table_entry(
    store, tmp_path, worktree
):
    document = """
name: agentic
phases:
  - name: spec
    kind: agent
    role: explorer
    writes: docs/superpowers/specs/{stem}.md
"""
    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert result is None
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]
    # Review Focus: the attempt row still records the path the attempt directory
    # would have used -- cli.read_artifact reads it -- even though classify was
    # handed None.
    terminal = [
        line.payload
        for line in store.journal.read()
        if line.event == "attempt_upsert" and line.payload["status"] == "ok"
    ][0]
    assert terminal["result_path"].endswith("spec.1/result.json")
```

And replace `test_a_phase_with_no_declared_result_gets_no_result_contract` (lines 1107-1131) with:

```python
def test_a_phase_with_no_declared_result_gets_no_result_contract(
    store, tmp_path, worktree
):
    # Spec test 6 / Review Focus 4: no contract at all, not a half-contract error,
    # and no result file is asked for or read.
    document = """
name: agentic
phases:
  - name: spec
    kind: agent
    role: explorer
    writes: docs/superpowers/specs/{stem}.md
"""
    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[None])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    brief = launcher.prompts[0]
    assert result is None
    assert prompt.RESULT_HEADING not in brief
    assert "Standing instructions for explorer." in brief
    assert f"{prompt.METHODOLOGY_HEADING_PREFIX}test-driven-development.md" in brief
    assert "# phase: explore" in brief
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v -k "result_less or no_result_model_declared or no_declared_result"`
Expected: FAIL — `test_a_result_less_phase_is_ok_with_no_result_at_all` and the parametrised exit test raise `AttributeError: 'NoneType' object has no attribute 'is_file'`; `test_a_result_less_phase_never_reads_a_result_file_that_is_there` returns `{"wrote": "docs/spec.md"}` instead of `None`; both runner tests fail with `AgentPhaseFailed`/`harness_error` because no result file was written.

- [ ] **Step 4: Move the guard ahead of the file read in `classify`**

In `src/agent_manager/dispatch.py`, replace `classify` (lines 198-259) with:

```python
def classify(
    outcome: Outcome, result_path: Path | None, model: type[BaseModel] | None
) -> Verdict:
    """One attempt's outcome, from the launcher's report and the result file.

    The order is §6 line 278's, and it is load-bearing: a timeout or a non-zero
    exit is a `harness_error` whatever is on disk, and a missing file after a
    clean exit is a `harness_error` too -- nothing is parsed in either case.
    Only past those does the file get read, and from there every failure is
    `schema_invalid`, because a file that exists and cannot be validated is
    exactly what re-dispatching with the validator's text can fix.

    A phase that declares no result model is judged on exit status alone: there
    is no contract in its brief, so there is nothing to read and nothing to
    validate, and a file the harness wrote anyway is not adopted as a result.
    `result_path` is never dereferenced on that path, so `None` is accepted for
    any outcome and this function never raises.

    A verdict of `ok` here means "the result file is good"; the gates run after
    and may still turn it into `gate_failed`.

    A validated result is returned as its JSON-mode dump rather than as the
    model instance: the ported gates read it with `Mapping.get`
    (`steps/reducers.py`), and §7 inlines it into a later phase's prompt as
    JSON. Handing them a `BaseModel` would make every gate silently read `None`.
    """
    if outcome.timed_out:
        return Verdict(
            "harness_error",
            detail=f"the harness timed out and was killed after {outcome.duration:.1f}s",
        )
    if outcome.exit_code != 0:
        return Verdict("harness_error", detail=f"the harness exited {outcome.exit_code}")
    if model is None:
        return Verdict("ok", result=None)
    if result_path is None or not result_path.is_file():
        return Verdict(
            "harness_error", detail=f"the harness wrote no result file at {result_path}"
        )
    try:
        text = result_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return Verdict(
            "schema_invalid", detail=f"{result_path} is not valid UTF-8 text: {error}"
        )
    except OSError as error:
        return Verdict(
            "schema_invalid",
            detail=f"{result_path} cannot be read: {type(error).__name__}: {error}",
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return Verdict("schema_invalid", detail=f"{result_path} is not valid JSON: {error}")
    try:
        validated = model.model_validate(data)
    except ValidationError as error:
        return Verdict("schema_invalid", detail=str(error))
    return Verdict("ok", result=validated.model_dump(mode="json"))
```

- [ ] **Step 5: Pass `None` from the call site**

In `src/agent_manager/dispatch.py`, replace line 508 inside `_attempt`:

```python
        verdict = classify(outcome, dispatch_record.result_path, model)
```

with:

```python
        # The same `None if model is None` the brief above uses (line 473): the
        # two halves of "this phase has no result" have to agree. The dispatch
        # and the journalled attempt keep the concrete path -- `harness/claude.py`
        # dereferences it to build the argv, and `cli.read_artifact` reads it.
        verdict = classify(
            outcome, None if model is None else dispatch_record.result_path, model
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: PASS, all tests in the file — including the untouched §6 line 278 regression net (`test_a_valid_result_file_classifies_ok`, `test_a_result_that_fails_the_model_classifies_schema_invalid`, `test_a_non_json_result_classifies_schema_invalid`, `test_a_result_file_that_is_not_utf8_classifies_schema_invalid`, `test_a_missing_result_file_after_exit_zero_classifies_harness_error`, `test_a_non_zero_exit_classifies_harness_error`, `test_a_timeout_classifies_harness_error_and_never_reads_the_result`, `test_stdout_is_never_the_channel`).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "fix: classify a result-less phase on exit status alone"
```

---

### Task 4: The spec author's brief carries the `SpecResult` contract

**Files:**
- Test: `tests/test_prompt.py` (imports at lines 16-19; new tests after `test_the_contract_states_the_absolute_path_and_embeds_the_real_schema`, line 579)

**Interfaces:**
- Consumes: `results.SpecResult` and `results.RESULT_MODELS` from Task 1; `prompt.compose_brief(role, rendered, *, result_path=None, result_model=None, feedback=None)`, `prompt.RESULT_HEADING`, the file's own `_role`, `_rendered` and `_fenced_json` helpers.
- Produces: nothing new — this task is the assertion that `prompt._result_contract` needs no change to serve the `spec` phase.

- [ ] **Step 1: Write the failing brief tests**

In `tests/test_prompt.py`, extend the import on line 16 to:

```python
from agent_manager import dag, models, prompt, results
```

Then add, immediately after `test_the_contract_states_the_absolute_path_and_embeds_the_real_schema` (ends line 578):

```python
def test_the_spec_authors_contract_names_its_path_and_embeds_the_real_schema():
    # The seam card be376902 closes: before SpecResult existed the spec phase
    # composed a brief with no contract section at all.
    brief = prompt.compose_brief(
        _role("spec_author"),
        _rendered(),
        result_path=Path("/var/agent-manager/runs/r1/card/spec.1/result.json"),
        result_model=results.SpecResult,
    )

    assert "\n## Result contract\n" in brief
    assert "/var/agent-manager/runs/r1/card/spec.1/result.json" in brief
    assert _fenced_json(brief) == results.SpecResult.model_json_schema()
    assert set(_fenced_json(brief)["properties"]) == {"path", "note"}


@pytest.mark.parametrize("name", sorted(results.RESULT_MODELS))
def test_every_shipped_result_model_embeds_its_own_schema_in_the_contract(name):
    # One parametrisation instead of six hand-copied tests: the brief's schema
    # is the model the engine validates against, for every phase that has one.
    model = results.RESULT_MODELS[name]
    result_path = f"/var/agent-manager/runs/r1/card/{name}.1/result.json"

    brief = prompt.compose_brief(
        _role(), _rendered(), result_path=Path(result_path), result_model=model
    )

    assert result_path in brief
    assert _fenced_json(brief) == model.model_json_schema()
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v -k "spec_authors_contract or every_shipped_result_model"`
Expected: PASS. These are characterisation tests over Task 1's model and the unchanged `prompt._result_contract`; there is no red step to force here, and `test_a_phase_with_no_result_gets_no_contract_section` (line 605) stays exactly as it is — it drives `compose_brief` with a synthetic role and is now the only guard on the contract-less brief.

- [ ] **Step 3: Prove the parametrisation is not vacuous**

Run: `uv run pytest tests/test_prompt.py -v -k every_shipped_result_model --collect-only`
Expected: six collected cases, one per name — `CriticResult`, `ExploreResult`, `ImplementResult`, `PlanResult`, `ReviewResult`, `SpecResult`. If `SpecResult` is missing, Task 1's registration did not land.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_prompt.py
git commit -m "test: pin the spec author's brief to the SpecResult schema"
```

---

### Task 5: End-to-end proof through `AgentRunner` for a `SpecResult` phase

**Files:**
- Test: `tests/test_dispatch.py` (new engine-tier tests after `test_a_phase_with_no_result_model_declared_needs_no_table_entry`, which Task 3 left ending around line 905)

**Interfaces:**
- Consumes: `results.SpecResult` (Task 1), the `classify` signature from Task 3, and this file's engine-tier fixtures — `store`, `worktree`, `_workflow(document, functions)`, `_runner(store, workflow, launcher, tmp_path, worktree, **overrides)`, `FakeLauncher(results=[...])`, `FakeAdapter`, `_context(worktree)`, `_rendered()`, `_attempt_statuses(opened)`.
- Produces: nothing new — this is the proof that the model, the contract and the guard hold together through one real `AgentRunner` dispatch.

- [ ] **Step 1: Write the failing engine-tier tests**

In `tests/test_dispatch.py`, add immediately after `test_a_phase_with_no_result_model_declared_needs_no_table_entry`:

```python
SPEC_DOCUMENT = """
name: agentic
phases:
  - name: spec
    kind: agent
    role: explorer
    result: SpecResult
    writes: docs/superpowers/specs/{stem}.md
"""

VALID_SPEC_RESULT = json.dumps({"path": "docs/superpowers/specs/task-x.md", "note": None})
SPEC_RESULT_WITHOUT_PATH = json.dumps({"note": None})


def _spec_runner(store, workflow, launcher, tmp_path, worktree):
    """A runner whose table holds the real SpecResult beside the canned model."""
    return _runner(
        store,
        workflow,
        launcher,
        tmp_path,
        worktree,
        result_models={"FakeResult": FakeResult, "SpecResult": results.SpecResult},
    )


def test_a_spec_phase_validates_its_result_and_returns_the_json_dump(
    store, tmp_path, worktree
):
    workflow = _workflow(SPEC_DOCUMENT, {})
    launcher = FakeLauncher(results=[VALID_SPEC_RESULT])
    runner, _ = _spec_runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert result == {"path": "docs/superpowers/specs/task-x.md", "note": None}
    assert _attempt_statuses(store) == [(1, "started"), (1, "ok")]
    # The brief the launcher was handed carries the contract the model defines.
    contract = launcher.prompts[0].split(prompt.RESULT_HEADING, 1)[1]
    assert '"path"' in contract
    assert '"note"' in contract
    assert '"additionalProperties": false' in contract


def test_a_spec_result_missing_path_is_schema_invalid_and_fails_the_phase(
    store, tmp_path, worktree
):
    # Review Focus: no retry: block on the shipped spec phase, so one dispatch
    # and then AgentPhaseFailed -- not a silent pass on an unvalidated file.
    workflow = _workflow(SPEC_DOCUMENT, {})
    launcher = FakeLauncher(results=[SPEC_RESULT_WITHOUT_PATH])
    runner, _ = _spec_runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert caught.value.phase == "spec"
    assert caught.value.outcome == "schema_invalid"
    assert "path" in caught.value.detail
    assert len(launcher.calls) == 1
    assert _attempt_statuses(store) == [(1, "started"), (1, "schema_invalid")]


def test_a_spec_attempt_that_writes_no_result_file_is_a_harness_error(
    store, tmp_path, worktree
):
    # The behaviour change this card makes deliberate: once spec declares a
    # model, a clean exit with no result.json is harness_error, not ok.
    workflow = _workflow(SPEC_DOCUMENT, {})
    launcher = FakeLauncher(results=[None])
    runner, _ = _spec_runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed) as caught:
        runner(workflow.phase("spec"), _context(worktree), _rendered())

    assert caught.value.outcome == "harness_error"
    assert "no result file" in caught.value.detail
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v -k spec_`
Expected: PASS. These exercise Tasks 1 and 3 together through `AgentRunner`; if `SpecResult` were still unregistered the helper's explicit table entry would fail with `AttributeError`, and if `classify`'s model-bearing order had drifted the third test would report `ok` instead of `harness_error`.

- [ ] **Step 3: Confirm the failure modes are really distinct**

Run: `uv run pytest tests/test_dispatch.py -v -k "spec_ or all_four_outcome_names"`
Expected: PASS, and `test_all_four_outcome_names_are_journalled_as_distinct_values` still sees `["ok", "schema_invalid", "gate_failed", "harness_error"]` — that phase declares `FakeResult`, so Task 3's guard does not touch it.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_dispatch.py
git commit -m "test: run a SpecResult-bearing spec phase through AgentRunner"
```

---

## Final verification

- [ ] **Run the full suite one last time**

Run: `uv run pytest`
Expected: PASS, no skips beyond the pre-existing `-m e2e` exclusion.

- [ ] **Confirm the three source changes are all present**

Run: `git diff --stat m2/task-create-the-worktree-f68d29e2..HEAD`
Expected: exactly these seven files — `src/agent_manager/results.py`, `src/agent_manager/workflow/builtin/task.yaml`, `src/agent_manager/dispatch.py`, `tests/test_results.py`, `tests/test_prompt.py`, `tests/test_dispatch.py`, `tests/workflow/test_builtin_task.py`. `src/agent_manager/models.py`, `src/agent_manager/prompt.py` and `src/agent_manager/steps/rollup.py` must not appear.
