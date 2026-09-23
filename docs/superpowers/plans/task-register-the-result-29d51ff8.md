<!-- task-pipeline: validated -->
# Spec (verbatim)

<!-- Copied verbatim from docs/superpowers/specs/task-register-the-result-29d51ff8-design.md -->

# Register the result models with the agent runner — design

Card: 29d51ff8 (subtask of story 5cc741ec "The result contract: models, gates and bindings", milestone 7aa00a90)
Amends nothing. Narrows: `docs/superpowers/specs/2026-09-23-real-harness-design.md` §1 seam 1 and R1, over `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §6 step 5 and §14.
Worktree: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8`

## State of the tree (resolved)

The exploration findings flagged that the five models were missing. They are missing from the *main checkout* (`/home/paulomtts/Code/agent-manager/src/agent_manager/results.py` has only `RESULT_MODELS` and `resolve_result_model`), but they are present in **this card's own worktree**: `.../task-register-the-result-29d51ff8/src/agent_manager/results.py` defines `_Result` (line 25, `ConfigDict(extra="forbid", strict=True)`), `Verification`, `ExploreResult`, `CriticResult`, `PlanResult`, `ImplementResult`, `ReviewResult` (lines 39-107), exactly the R1 field sets, plus `serialization_alias` on `Verification.full_suite`, `ReviewResult.commit_count` and `ReviewResult.tagged_count`. Sibling c873fc52's work has landed on the branch this card builds on. All work for this card happens in this worktree; the main checkout is not to be edited. No model class, field, alias or docstring of the sibling's is to be changed — this card only registers what already exists.

## Scope

Registration and nothing else:

1. `RESULT_MODELS` in `src/agent_manager/results.py` gains the five entries, keyed by class name: `"ExploreResult"`, `"CriticResult"`, `"PlanResult"`, `"ImplementResult"`, `"ReviewResult"`. `Verification` is **not** registered — no `result:` name refers to it; it is reachable only as `ExploreResult.verification`.
2. The two docstrings that assert the table ships empty are corrected: the module docstring (lines 7-12, "The table ships empty, and that is deliberate … wiring them into the table is a separate card's decision") and the `RESULT_MODELS` docstring (line 22, "Empty on this branch"). The replacement text says what the table is (every `result:` name `builtin/task.yaml` declares, mapped to the model that validates that phase's `result.json`) and keeps the standing rule that an unregistered name fails loudly at dispatch rather than validating nothing.
3. `dispatch.AgentRunner.result_models` (`src/agent_manager/dispatch.py:368-370`) is **verified, not rewritten**: `field(default_factory=lambda: dict(results.RESULT_MODELS))` already copies the table per runner, so filling the table satisfies the requirement. `cli.default_runner_factory` (`src/agent_manager/cli.py:588-610`) already omits `result_models` and must stay that way — no wiring is added to `cli.py`.

Out of scope: defining or editing the models (c873fc52); `critic_blockers_gate`, the gate bindings and `workflow/registry.py` (e5c05fd2); the composed brief and the embedded JSON Schema (R2, the next story); `--verify` / `--branch-prefix` (R3); the fake-claude production-wiring test (R4); milestone orchestration, parallel stories, integrate, non-Claude harnesses (addendum §4).

## Observable behaviour

- `results.RESULT_MODELS` is a 5-entry mapping; `results.resolve_result_model(name, results.RESULT_MODELS, phase=p)` returns the class for each of the five names declared in `workflow/builtin/task.yaml` (`ExploreResult` line 9, `CriticResult` lines 39 and 53, `PlanResult` line 46, `ImplementResult` line 60, `ReviewResult` line 66).
- A default-constructed `AgentRunner` (no `result_models=` argument) resolves those same five names. Each runner holds its own `dict` copy, so mutating one runner's table cannot corrupt the module-level one.
- `am run --card …` with production wiring gets past `explore`'s model lookup, which is the failure the addendum opens with.

## Error paths

- `resolve_result_model` keeps its current behaviour verbatim: an unknown name raises `EngineError`, carrying `phase=`, with the message listing the registered names (now the five, sorted, instead of `nothing`). No signature, message-shape or exception-type change.
- Registration performs no I/O and no validation; importing `results` still reads no file. A typo in a key surfaces as the same `EngineError` at dispatch time, not at import.
- `CriticResult` is registered once and shared by both `validate_spec` and `validate_plan`; resolution is by name, so the duplicate `result:` in the YAML is not an error.

## Tests

Tiers per design §14 (lines 477-492), as read by the placement rule in the exploration findings.

1. **The table holds the five names, and each resolves to its class** — replaces `tests/test_results.py::test_the_shipped_table_is_empty_and_says_why` (lines 36-40), which asserts `RESULT_MODELS == {}` and must fail once the table is filled. Assert `set(results.RESULT_MODELS) == {"ExploreResult", "CriticResult", "PlanResult", "ImplementResult", "ReviewResult"}` (an equality, so registering a stray key such as `Verification` fails) and that each value is the class of that name. *Engine tier* — `tests/test_results.py`'s own docstring places the result-name table in the engine tier: it is the table the engine's validation step reads, exercised without a harness. The other two tests in that file use a canned table and stay as they are.
2. **Every `result:` name in `builtin/task.yaml` resolves through the shipped table** — the regression guard for a renamed model. Load the workflow with `load_builtin("task")`, iterate its phases, and for each `AgentPhase` with a non-`None` `result`, assert `results.resolve_result_model(phase.result, results.RESULT_MODELS, phase=phase.name)` returns a `BaseModel` subclass named `phase.result`. Driven from the loaded phases, never a hardcoded list, so renaming a model or a YAML name fails here. Assert at least one phase was checked, so a loader change that empties the iteration cannot make the test vacuous. *Pure-functions tier* — it reads one packaged YAML file and resolves names against a module-level table, with no repo, no board and no harness; prior specs put the builtin-YAML tests in that tier, in `tests/workflow/test_builtin_task.py`, which is where this one goes (its module docstring already reads "reads one packaged YAML file and resolves names against the default registry").
3. **A default `AgentRunner` carries the shipped table** — construct `dispatch.AgentRunner(...)` directly with only the required fields (`workflow`, `store`, `launcher`, `run_id`, `story_id`, `card_id`) and no `result_models=` argument. Do not use `_runner` (`tests/test_dispatch.py` line 553): it always injects `result_models={"FakeResult": FakeResult}` and `overrides` can only replace that key, never omit it. Assert its `result_models` equals `results.RESULT_MODELS` and is a distinct object from it (the `default_factory` copy). Optional but cheap; it is the only assertion that the production default is the filled table. *Engine tier* — `tests/test_dispatch.py` is engine tier, using the fake adapter and injected launcher; the existing helper's `result_models={"FakeResult": FakeResult}` override stays for every other test there.

No end-to-end, real-harness or fake-claude test is written for this card.

## Verification beyond the suite

`uv run pytest` (the whole suite; there is no lint or typecheck) must be green from this worktree.

Then re-run the addendum §1 smoke: a `run_card` call with production wiring (`cli.default_runner_factory`, real adapters, direct launcher) against a throwaway toy repo and board in the scratchpad. The run is **expected to fail** — the brief/prompt composition (R2) is the next story, so nothing yet tells the agent what to write or where. The evidence this card worked is that the failure is no longer `EngineError: phase 'explore': declares result 'ExploreResult', which no result model is registered for (registered: nothing)`. Record the new error verbatim — phase, exception type and message — in the card's completion note; a failure that is still the old `EngineError` means the card did not land.

---

# Register the result models with the agent runner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill `results.RESULT_MODELS` with the five phase result models so a production `AgentRunner` can resolve every `result:` name `builtin/task.yaml` declares.

**Architecture:** Registration only. The five models already exist in this worktree (`src/agent_manager/results.py:39-107`, sibling c873fc52) and are not touched. The `RESULT_MODELS` assignment moves below the class definitions — it currently sits at line 21, above them, and cannot name them from there — and gains five entries keyed by class name. `dispatch.AgentRunner.result_models` already reads that table through `default_factory=lambda: dict(results.RESULT_MODELS)`, and `cli.default_runner_factory` already omits the argument, so both are pinned with tests rather than edited.

**Tech Stack:** Python 3, pydantic v2, pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-register-the-result-29d51ff8-design.md` (prepended verbatim above)

## Global Constraints

- All work happens in the worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8`, on branch `m2/task-register-the-result-29d51ff8`. The main checkout at `/home/paulomtts/Code/agent-manager` is never edited.
- No model class, field, alias or docstring defined by sibling c873fc52 (`src/agent_manager/results.py` lines 25-107) is changed. This card only registers what already exists.
- `Verification` is **not** registered — no `result:` name refers to it; it is reachable only as `ExploreResult.verification`.
- `resolve_result_model(name, table, *, phase)` keeps its behaviour verbatim: unknown name → `EngineError` carrying `phase=`, same message shape. No signature, message-shape or exception-type change.
- No wiring is added to `src/agent_manager/cli.py`. `default_runner_factory` (lines 588-610) keeps omitting `result_models`.
- `src/agent_manager/dispatch.py:368-370` is verified, not rewritten (apart from one temporary, reverted mutation used to prove a test red).
- Verification command for this repo: `uv run pytest`. There is no lint and no typecheck.
- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md).
- Out of scope: defining/editing the models (c873fc52); gates, bindings and `workflow/registry.py` (e5c05fd2); the composed brief and embedded JSON Schema (R2); `--verify` / `--branch-prefix` (R3); the fake-claude production-wiring test (R4); milestone orchestration, parallel stories, integrate, non-Claude harnesses.

## Review Focus

- **A stray key such as `Verification` in the table.** Nothing in the engine would notice a sixth entry, and it would let a future `result: Verification` silently "validate". Pinned by the set-equality assertion in Task 1, Step 1.
- **The `EngineError` message still reading `registered: nothing`.** That exact string is the addendum §1 failure; if the table were populated but shadowed, the message would still say it. Pinned by `test_an_unknown_name_now_lists_the_five_registered_names` in Task 1, Step 1.
- **One runner mutating the module-level table.** `result_models` is a plain `dict` copy per runner; if the `default_factory` ever became `lambda: results.RESULT_MODELS`, a runner popping a key would corrupt every later runner in the process. Pinned by the identity-and-mutation assertions in Task 2, Step 1.
- **`cli.default_runner_factory` drifting into passing its own table.** The production path is the one the smoke exercises and the one no engine-tier test covers today. Pinned by `test_the_production_runner_factory_carries_the_shipped_table` in Task 2, Step 1.
- **`CriticResult` declared twice in the YAML (lines 39 and 53).** Resolution is by name, so the duplicate must resolve to one shared class rather than being treated as a conflict. Pinned by `test_both_validation_phases_resolve_to_the_same_critic_model` in Task 1, Step 2.

## File Structure

- Modify `src/agent_manager/results.py` — move the `RESULT_MODELS` assignment below the five model classes and populate it; rewrite the module docstring's "ships empty" paragraph and the `RESULT_MODELS` docstring. Sole source change in this card.
- Modify `tests/test_results.py` (engine tier) — replace `test_the_shipped_table_is_empty_and_says_why` (lines 36-40) with four assertions about the shipped table.
- Modify `tests/workflow/test_builtin_task.py` (pure-functions tier) — add the `task.yaml`-driven name-resolution guard and the shared-`CriticResult` check.
- Modify `tests/test_dispatch.py` (engine tier) — add two construction-only tests for the production default; the existing `_runner` helper and its `result_models={"FakeResult": FakeResult}` override are untouched.
- No file is created. `src/agent_manager/dispatch.py` and `src/agent_manager/cli.py` end the card byte-identical to how they started.

---

### Task 1: Populate the result-model table

**Files:**
- Modify: `src/agent_manager/results.py:1-22` (docstrings), `src/agent_manager/results.py:21-22` (move the assignment below line 107)
- Test: `tests/test_results.py:36-40` (replace), `tests/workflow/test_builtin_task.py:17-24` (imports) and end of file

**Interfaces:**
- Consumes: `results.resolve_result_model(name: str, table: Mapping[str, type[BaseModel]], *, phase: str) -> type[BaseModel]`, unchanged; the five classes `ExploreResult`, `CriticResult`, `PlanResult`, `ImplementResult`, `ReviewResult` from `src/agent_manager/results.py:47-107`; `agent_manager.workflow.load_builtin(name)`; `agent_manager.workflow.loader.AgentPhase` with attributes `.name: str` and `.result: str | None`.
- Produces: `results.RESULT_MODELS: dict[str, type[BaseModel]]` holding exactly five entries, each key equal to its value's `__name__`. Task 2 compares a runner's `result_models` against this object.

- [ ] **Step 1: Replace the empty-table test in `tests/test_results.py`**

Delete lines 36-40 of `tests/test_results.py`:

```python
def test_the_shipped_table_is_empty_and_says_why():
    # The five names in builtin/task.yaml now have models below, but putting
    # them in the table is a sibling card's decision, not this one's.
    # Validating nothing would be worse -- an unknown name fails loudly instead.
    assert results.RESULT_MODELS == {}
```

and put these four tests in their place (same position, right after `test_an_unknown_result_name_is_a_named_engine_error`):

```python
def test_the_shipped_table_holds_exactly_the_five_declared_names():
    # An equality, not a superset: a stray sixth key is a name the engine would
    # happily resolve for a phase that has no business declaring it.
    assert set(results.RESULT_MODELS) == {
        "ExploreResult",
        "CriticResult",
        "PlanResult",
        "ImplementResult",
        "ReviewResult",
    }


def test_every_entry_is_the_class_its_key_names():
    assert results.RESULT_MODELS  # an empty table would make the loop vacuous
    for name, model in results.RESULT_MODELS.items():
        assert model.__name__ == name
        assert (
            results.resolve_result_model(name, results.RESULT_MODELS, phase="explore")
            is model
        )


def test_verification_is_not_registered():
    # No `result:` name refers to it; it is reachable only as
    # ExploreResult.verification, so registering it would be dead surface.
    assert "Verification" not in results.RESULT_MODELS


def test_an_unknown_name_now_lists_the_five_registered_names():
    # The addendum's opening failure ends "(registered: nothing)". That exact
    # phrasing must be gone, and the five sorted names must be what it offers.
    with pytest.raises(EngineError) as caught:
        results.resolve_result_model("SpecResult", results.RESULT_MODELS, phase="spec")

    message = str(caught.value)
    assert caught.value.phase == "spec"
    assert "registered: nothing" not in message
    assert (
        "CriticResult, ExploreResult, ImplementResult, PlanResult, ReviewResult"
        in message
    )
```

- [ ] **Step 2: Add the `task.yaml` name-resolution guard to `tests/workflow/test_builtin_task.py`**

First extend the imports. Replace lines 17-24:

```python
from agent_manager.results import (
    CriticResult,
    ExploreResult,
    ImplementResult,
    PlanResult,
    ReviewResult,
    Verification,
)
```

with:

```python
from agent_manager.results import (
    RESULT_MODELS,
    CriticResult,
    ExploreResult,
    ImplementResult,
    PlanResult,
    ReviewResult,
    Verification,
    resolve_result_model,
)
```

and add `from pydantic import BaseModel` directly below `import pytest` (line 14).

Then append these two tests immediately after `test_a_substituted_registry_supplies_the_resolved_callables` (line 147), above the `── acceptance #2` banner comment:

```python
def test_every_declared_result_name_resolves_through_the_shipped_table() -> None:
    """Addendum §1 seam 1 closed: renaming a model, or a `result:` name in
    `task.yaml`, fails here rather than one second into a production run.

    Driven from the loaded phases, never a hardcoded list -- a hardcoded list
    would keep passing while the document drifted away from it."""
    checked = 0
    for phase in load_builtin("task").phases:
        if not isinstance(phase, AgentPhase) or phase.result is None:
            continue
        model = resolve_result_model(phase.result, RESULT_MODELS, phase=phase.name)
        assert issubclass(model, BaseModel)
        assert model.__name__ == phase.result
        checked += 1
    # Non-vacuity: a loader change that stopped yielding agent phases, or
    # stopped carrying `result`, would otherwise turn this into a no-op.
    assert checked == 5


def test_both_validation_phases_resolve_to_the_same_critic_model() -> None:
    """`CriticResult` is declared twice (`task.yaml` lines 39 and 53).
    Resolution is by name, so the duplicate is one shared class, not a clash."""
    workflow = load_builtin("task")
    spec_phase = workflow.phase("validate_spec")
    plan_phase = workflow.phase("validate_plan")
    assert isinstance(spec_phase, AgentPhase) and isinstance(plan_phase, AgentPhase)

    spec_model = resolve_result_model(
        spec_phase.result, RESULT_MODELS, phase="validate_spec"
    )
    plan_model = resolve_result_model(
        plan_phase.result, RESULT_MODELS, phase="validate_plan"
    )

    assert spec_model is plan_model
    assert spec_model is CriticResult
```

- [ ] **Step 3: Run the new tests and watch them fail**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
uv run pytest tests/test_results.py -k "shipped_table or every_entry or verification_is_not_registered or unknown_name_now_lists" -v
uv run pytest tests/workflow/test_builtin_task.py -k "declared_result_name or same_critic_model" -v
```

(Two invocations, not one: a second `-k` on the same command line replaces the first rather than adding to it.)

Expected: all six FAIL. The `test_results.py` ones fail on the empty set / the non-empty assertion / `registered: nothing` still being in the message; the two in `test_builtin_task.py` fail with `EngineError: phase 'explore': declares result 'ExploreResult', which no result model is registered for (registered: nothing)` — the addendum's opening error, reproduced by the suite.

- [ ] **Step 4: Move and populate `RESULT_MODELS` in `src/agent_manager/results.py`**

Delete lines 21-22 (the assignment and its docstring, which sit above the class definitions and so cannot name them):

```python
RESULT_MODELS: dict[str, type[BaseModel]] = {}
"""Every `result:` name with a model behind it. Empty on this branch."""
```

so the module goes straight from the `from agent_manager.errors import EngineError` import to `class _Result(BaseModel):`, and insert the populated table after `ReviewResult` ends (current line 107, the `plan_hash: str` line) and before `def resolve_result_model(`:

```python
RESULT_MODELS: dict[str, type[BaseModel]] = {
    "ExploreResult": ExploreResult,
    "CriticResult": CriticResult,
    "PlanResult": PlanResult,
    "ImplementResult": ImplementResult,
    "ReviewResult": ReviewResult,
}
"""Every `result:` name `builtin/task.yaml` declares, keyed by class name.

`Verification` is absent on purpose: no phase declares it, and it is reachable
only as `ExploreResult.verification`.
"""
```

Keep two blank lines above and below the block, as the rest of the module does.

- [ ] **Step 5: Correct the module docstring**

In `src/agent_manager/results.py`, replace the second paragraph of the module docstring (lines 7-12):

```
The table ships empty, and that is deliberate. `builtin/task.yaml` names five
result models -- `ExploreResult`, `CriticResult`, `PlanResult`,
`ImplementResult`, `ReviewResult` -- and they are defined below, but wiring
them into the table is a separate card's decision, not this one's. An
unresolved name fails loudly at dispatch time instead, which is strictly
better than validating nothing and calling the result `ok`.
```

with:

```
`RESULT_MODELS` is that mapping: every `result:` name `builtin/task.yaml`
declares -- `ExploreResult`, `CriticResult`, `PlanResult`, `ImplementResult`,
`ReviewResult` -- against the model that validates that phase's `result.json`.
`Verification` is not in it; no phase declares it, and it is reachable only as
`ExploreResult.verification`. A name with no entry still fails loudly at
dispatch time, which is strictly better than validating nothing and calling
the result `ok`.
```

Leave lines 1-5 (the summary line and the `workflow/loader.py` paragraph) exactly as they are.

- [ ] **Step 6: Run the six tests and watch them pass**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
uv run pytest tests/test_results.py tests/workflow/test_builtin_task.py -v
```

Expected: PASS, both files entire — the pre-existing model, gate and schema tests included, since no model was touched.

- [ ] **Step 7: Run the whole suite**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
uv run pytest
```

Expected: green. If `tests/test_dispatch.py::test_an_unregistered_result_model_is_a_named_engine_error` fails, read it before changing anything: it passes `result_models={}` explicitly (line 841) and so is independent of the module table — a failure there means the override seam broke, not the registration.

- [ ] **Step 8: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
git add src/agent_manager/results.py tests/test_results.py tests/workflow/test_builtin_task.py
git commit -m "feat: register the five phase result models in RESULT_MODELS"
```

---

### Task 2: Pin the production default to the shipped table

**Files:**
- Test: `tests/test_dispatch.py:19` (imports) and end of file
- Verify only (no lasting change): `src/agent_manager/dispatch.py:368-370`, `src/agent_manager/cli.py:588-610`

**Interfaces:**
- Consumes: `results.RESULT_MODELS` from Task 1; `dispatch.AgentRunner(workflow, store, launcher, run_id, story_id, card_id, ...)`; `cli.default_runner_factory(*, workflow, store, run_id, story_id, card_id) -> engine.AgentPhaseRunner`; the module-level helpers already in `tests/test_dispatch.py` — `_workflow(document, functions)` (line 393), `AGENT_DOCUMENT` (line 381), `FakeLauncher` (line 207), `VALID_RESULT` (line 202), `RUN_ID` (line 26), `CARD` (line 28), `STORY_ID` (line 535), and the `store` fixture (line 538).
- Produces: nothing other tasks consume.

- [ ] **Step 1: Write the two failing tests in `tests/test_dispatch.py`**

Extend the import on line 19 from:

```python
from agent_manager import dispatch, engine, models, paths, prompt, store as store_module
```

to:

```python
from agent_manager import (
    cli,
    dispatch,
    engine,
    models,
    paths,
    prompt,
    results,
    store as store_module,
)
```

Then append to the end of the file:

```python
# ── the production default, which no other test in this file can see ─────────
# `_runner` (line 553) always injects `result_models={"FakeResult": FakeResult}`
# and `overrides` can only replace that key, never omit it -- so these two
# construct their runners directly. Neither dispatches: construction is the
# whole assertion.


def test_a_default_runner_carries_the_shipped_result_model_table(store):
    runner = dispatch.AgentRunner(
        workflow=_workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None}),
        store=store,
        launcher=FakeLauncher(results=[VALID_RESULT]),
        run_id=RUN_ID,
        story_id=STORY_ID,
        card_id=CARD,
    )

    assert runner.result_models == results.RESULT_MODELS
    # A copy, not the module object: one runner must not be able to corrupt the
    # table every later runner in this process will be built from.
    assert runner.result_models is not results.RESULT_MODELS
    assert isinstance(runner.result_models, dict)
    runner.result_models.pop("ExploreResult")
    assert "ExploreResult" in results.RESULT_MODELS


def test_the_production_runner_factory_carries_the_shipped_table(store):
    # `cli.default_runner_factory` omits `result_models` on purpose (cli.py
    # lines 598-601). This is the path the addendum §1 smoke takes, and the
    # only test that walks it.
    runner = cli.default_runner_factory(
        workflow=_workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None}),
        store=store,
        run_id=RUN_ID,
        story_id=STORY_ID,
        card_id=CARD,
    )

    assert isinstance(runner, dispatch.AgentRunner)
    assert runner.result_models == results.RESULT_MODELS
    assert set(runner.result_models) == {
        "ExploreResult",
        "CriticResult",
        "PlanResult",
        "ImplementResult",
        "ReviewResult",
    }
```

- [ ] **Step 2: Prove the tests red by temporarily breaking the default**

These two tests pass the moment Task 1 lands, so a bare run proves nothing. Mutate the default to show they bite. In `src/agent_manager/dispatch.py`, temporarily replace lines 368-370:

```python
    result_models: Mapping[str, type[BaseModel]] = field(
        default_factory=lambda: dict(results.RESULT_MODELS)
    )
```

with:

```python
    result_models: Mapping[str, type[BaseModel]] = field(default_factory=dict)
```

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
uv run pytest tests/test_dispatch.py -k "default_runner_carries or production_runner_factory" -v
```

Expected: both FAIL on `assert {} == {'ExploreResult': ...}`.

Now mutate the other way — restore lines 368-370 to their original three lines but drop the copy, i.e. `default_factory=lambda: results.RESULT_MODELS`. Run the same command.

Expected: `test_a_default_runner_carries_the_shipped_result_model_table` FAILS on `assert runner.result_models is not results.RESULT_MODELS`; the factory test passes. That is the aliasing bug pinned.

- [ ] **Step 3: Restore `dispatch.py` and confirm it is byte-identical**

Put lines 368-370 back to exactly:

```python
    result_models: Mapping[str, type[BaseModel]] = field(
        default_factory=lambda: dict(results.RESULT_MODELS)
    )
```

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
git diff --exit-code src/agent_manager/dispatch.py src/agent_manager/cli.py
```

Expected: no output, exit 0. Any diff here means the mutation was not fully reverted — this card changes neither file.

- [ ] **Step 4: Run the tests and watch them pass**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
uv run pytest tests/test_dispatch.py -v
```

Expected: PASS, the whole file — the two new tests and the 40-odd existing ones, which keep their `result_models={"FakeResult": FakeResult}` override.

- [ ] **Step 5: Run the whole suite**

Run:

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
uv run pytest
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
git add tests/test_dispatch.py
git commit -m "test: pin the production runner default to the shipped result-model table"
```

---

### Task 3: Re-run the addendum §1 smoke and record the new error

**Files:**
- No repository file is created or modified. The toy repo, board and driver script live in a throwaway directory outside the worktree.

**Interfaces:**
- Consumes: `cli.run_card(card_id, *, repo_dir, base_branch="master", branch_prefix="m1", allow_no_verification=False, commands=(), runner_factory=None, clock=_utcnow) -> dict[str, Any]` (`src/agent_manager/cli.py:633-643`); `cli.default_runner_factory(*, workflow, store, run_id, story_id, card_id)`.
- Produces: the verbatim new error text, for the card's completion note.

- [ ] **Step 1: Build a throwaway toy repo and board**

This mirrors the `project`/`cards` fixtures in `tests/test_cli.py:1011-1052`, so the wiring is the one the suite already trusts. Requires `git` and `brd` on PATH.

```bash
export SMOKE=$(mktemp -d)
export XDG_DATA_HOME="$SMOKE/xdg"
mkdir -p "$SMOKE/project" "$SMOKE/xdg"
cd "$SMOKE/project"
git init -b main .
git config user.email smoke@example.com
git config user.name "agent-manager smoke"
git config commit.gpgsign false
echo base > README.md
git add README.md
git commit -m base
brd init --name smoke-board
git add -A
git commit -m "brd init"
MILESTONE=$(brd add --title "Milestone: smoke" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])')
STORY=$(brd add --title "A story" --parent "$MILESTONE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])')
export SUBTASK=$(brd add --title "A subtask" --parent "$STORY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])')
echo "subtask: $SUBTASK"
```

Expected: a printed card id. `XDG_DATA_HOME` isolates both brd's database and `paths.data_dir()`, so no run artifact lands in the developer's home.

- [ ] **Step 2: Drive `run_card` with production wiring**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
XDG_DATA_HOME="$SMOKE/xdg" uv run python - "$SUBTASK" "$SMOKE/project" <<'PY' 2>&1 | tee "$SMOKE/smoke.out"
import dataclasses
import json
import sys
from pathlib import Path

from agent_manager import cli

card_id, repo_dir = sys.argv[1], Path(sys.argv[2])


def bounded_production_factory(**kwargs):
    """`cli.default_runner_factory` verbatim -- real adapters, real roles, the
    direct launcher, `AgentRunner`'s own `result_models` default -- with only
    the per-attempt timeout shortened so a live harness cannot sit here."""
    return dataclasses.replace(cli.default_runner_factory(**kwargs), timeout=60.0)


try:
    payload = cli.run_card(
        card_id,
        repo_dir=repo_dir,
        base_branch="main",
        branch_prefix="smoke",
        runner_factory=bounded_production_factory,
    )
    print(json.dumps(payload, indent=2, default=str))
except Exception as error:
    print(f"{type(error).__name__}: {error}")
PY
```

Expected: a failure, either as a raised exception line or as a payload whose `status` is not `done` and whose `failed_phase`/`detail` name what stopped it. The run cannot complete: the brief/prompt composition (R2) is the next story, so nothing yet tells the agent what to write or where.

- [ ] **Step 3: Judge the output against the success criterion**

Run, against the transcript Step 2 tee'd:

```bash
grep -c "registered: nothing" "$SMOKE/smoke.out"
grep -c "no result model is registered for" "$SMOKE/smoke.out"
cat "$SMOKE/smoke.out"
```

Expected: both `grep -c` print `0` (and exit 1, which is the pass here). The `cat` is what gets read in Step 4.

If either count is non-zero, this card did not land: stop, and re-check that `results.RESULT_MODELS` is populated (Task 1, Step 4) and that `uv run pytest tests/test_dispatch.py -k production_runner_factory` passes.

- [ ] **Step 4: Record the new error verbatim**

Copy the Step 2 output into the card's completion note, as: the phase it died on, the exception type, and the message, quoted exactly — plus the one line "the addendum §1 `registered: nothing` EngineError is gone; this is the next seam (R2, the brief)".

- [ ] **Step 5: Clean up the throwaway directory**

```bash
rm -rf "$SMOKE"
```

- [ ] **Step 6: Final full verification**

```bash
cd /home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-register-the-result-29d51ff8
uv run pytest
git status --porcelain
```

Expected: the suite green, and `git status --porcelain` showing nothing except the untracked pipeline documents (`docs/superpowers/plans/task-register-the-result-29d51ff8.md` and `docs/superpowers/specs/task-register-the-result-29d51ff8-design.md`, which this card does not commit) — Tasks 1 and 2 are committed and Task 3 changed no file in the repo.
