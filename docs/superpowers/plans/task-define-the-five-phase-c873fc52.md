<!-- task-pipeline: validated -->
# Define the five phase result models — design

Card: `c873fc52` (subtask) · Story: `5cc741ec` "The result contract: models, gates and bindings" · Milestone: `7aa00a90`
Implements: addendum R1 of `docs/superpowers/specs/2026-09-23-real-harness-design.md` §2 (table, lines 53-61)

## 1. Scope

Add the five pydantic result models named by `src/agent_manager/workflow/builtin/task.yaml` (lines 9, 39, 46, 53, 60, 66) to `src/agent_manager/results.py`, with exactly the fields R1 lists and nothing more, plus tests in `tests/test_results.py`.

The models are pure data shapes. This card defines them and reconciles the one spelling mismatch between R1's snake_case and the camelCase keys `steps/reducers.py` reads. It does not wire them anywhere.

Explicitly out of scope:

- **Registration.** `RESULT_MODELS` stays `{}` on this branch; sibling `29d51ff8` populates it and defaults `dispatch.AgentRunner`'s `result_models`. `tests/test_results.py::test_the_shipped_table_is_empty_and_says_why` must still pass when this card lands.
- **Gates and bindings.** No edits to `workflow/registry.py`, `workflow/builtin/task.yaml`, `dispatch.py`, or the semantics of `steps/reducers.py`. Sibling `e5c05fd2` implements `critic_blockers_gate` and makes every gate bindable.
- **Brief composition (R2), CLI `--verify` (R3), the fake-claude wiring test (R4)** — other stories.
- Addendum §4 exclusions: orchestration, parallel stories, integrate, non-Claude harnesses, deterministic measurement of porcelain/commit counts.

The module docstring currently explains that the table ships empty "because the design spec gives a field schema for none of them". That sentence is now false in its second half: the schemas exist, in this module. Reword only that clause; the "the table is empty, registration is the sibling's job" statement stays, and the reasons for `resolve_result_model`'s loud failure stay untouched.

## 2. The models

A shared private base in `results.py` — not imported from `models.py`, and not changing `models.py` — carries `ConfigDict(extra="forbid", strict=True)`. `extra="forbid"` for the reason `models._Model` gives: an unknown key in an agent-written file is a signal, not something to drop. `strict=True` for the boundary reason: a result file is produced by a language model, and `"3"` for `commit_count` or `1` for `refused` is exactly the sloppiness the validation step exists to catch. Model-wide strict config, rather than `Field(strict=True)` per field as `models.py:76` does, because every field here is equally untrusted.

Fields, exactly as R1 gives them (no `skill_invoked` on `PlanResult` — D6 inlines the methodology into the prompt, so there is no skill to detect):

| model | fields |
|---|---|
| `ExploreResult` | `refused: bool`, `reason: str \| None`, `summary: str`, `verification: Verification` |
| `Verification` | `full_suite: list[str]`, `typecheck: str`, `lint: list[str]` |
| `CriticResult` | `blockers: bool`, `reason: str \| None`, `summary: str` |
| `PlanResult` | `path: str`, `self_reviewed: bool`, `note: str \| None` |
| `ImplementResult` | `blocked: bool`, `blocked_reason: str \| None`, `resumed: bool`, `plan_hash: str`, `report: str` |
| `ReviewResult` | `findings: list[str]`, `unresolved_blockers: list[str]`, `fix_summary: str`, `porcelain: str`, `commit_count: int`, `tagged_count: int`, `plan_hash: str` |

`verification` is a nested model (also on the strict base), not a bare dict, so a malformed sub-object fails with a field path rather than passing as "some mapping". The three nullable fields are `str | None` and required — the agent must say "no reason" explicitly rather than omit the key; no defaults are introduced that R1 does not give. Every other field is required. No value constraints (`min_length`, `ge`) beyond the types: R1 specifies types, and inventing plausibility rules here would duplicate judgement the gates already own (`exploration_output_gate` is the thing that decides a summary is too short; `review_gate` is the thing that decides a count is unusable).

## 3. The spelling reconciliation

`steps/reducers.py` reads results as `Mapping`s through `_field`, using camelCase for three keys:

- `review_gate` reads `porcelain`, `commitCount` (line 187), `taggedCount` (line 188);
- `exploration_output_gate` reads `summary` and `verification` → `fullSuite` (line 272).

Those reducers are a faithful port with `task.test.mjs` as their behavioural specification, and `tests/steps/test_reducers.py` is that specification in this repo. They do not change.

So the reconciliation lives entirely in `results.py`, in one place: the three (and only the three) fields the reducers read under a different spelling carry a camelCase serialisation alias — `commit_count` → `commitCount`, `tagged_count` → `taggedCount`, `full_suite` → `fullSuite`. Python attribute names stay snake_case per R1. Use `Field(serialization_alias=...)` only (not `alias=`, not `serialize_by_alias` config): validation stays snake_case, and callers opt in with an explicit `by_alias=True` (verified on pydantic 2.13.5: nested strict models validate from dicts and dump `fullSuite` correctly). The dump direction is what matters: `result.model_dump(by_alias=True)` produces a mapping whose keys `_field` finds.

The input direction is deliberately constrained: validation accepts snake_case only. The agent writes what the embedded JSON Schema (R2, `model_json_schema()`) tells it to write, and that schema must name one spelling, not two. Whether `model_json_schema()` shows the snake_case field names is therefore an assertion in the test list, not an incidental: an alias configuration that flipped the validation schema to camelCase would silently instruct future agents to write keys the models reject.

The reducers receive dicts, never model instances — nothing in this card changes that. The models simply guarantee that a dump of a valid result is a dict the ported gates can read.

## 4. Error paths

There is no new failure mode beyond pydantic's. A result file that is missing a required key, carries a wrong type, coerces (`"3"`, `1` for a bool), or carries an unknown key raises `ValidationError` at the point the engine validates — which on this branch is nowhere, since the models are unregistered. The message must name the offending field; that is the whole reason for `extra="forbid"` and strict mode, and the tests assert on field names appearing in the error rather than on exact prose.

`resolve_result_model` and its `EngineError` are unchanged.

## 5. Tests

All of the following go in `/home/paulomtts/Code/agent-manager/tests/test_results.py`, alongside the existing engine-tier tests, as plain pytest with no fakes, no IO and no harness.

**Tier, per design §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md` lines 477-492): pure-functions/unit tier, in the default suite.** Model validation is pure — no temp git repo (that is the steps tier), no fake adapter or canned result file (that is the engine tier), no marker (that is the single opt-in end-to-end test). The file already mixes tiers, labelled per test; the new tests are labelled pure tier in a module comment so the distinction from the existing engine-tier `resolve_result_model` tests stays legible. The combination test in the last row is also pure — `steps/reducers.py` gates are pure functions and are called directly.

| # | test | tier |
|---|---|---|
| 1 | Each of the five models accepts a fully populated valid payload and round-trips its values (parametrised over model + payload, or five tests — five named tests read better in failure output) | pure |
| 2 | Each of the five rejects a payload with one required field removed, and the error names that field | pure |
| 3 | Each of the five rejects a wrong-typed field, including the strictness cases: `"3"` for `ReviewResult.commit_count` and `1` for `ExploreResult.refused` are rejected, not coerced | pure |
| 4 | Each of the five rejects an extra unknown key | pure |
| 5 | `ExploreResult.verification` rejects a missing `full_suite` and a non-list `full_suite`, naming the nested path | pure |
| 6 | `ReviewResult(...).model_dump(by_alias=True)` fed to `reducers.review_gate` returns `None` for a clean, fully-tagged review — i.e. the gate finds `porcelain`, `commitCount`, `taggedCount` | pure |
| 7 | The same dump with `commit_count=0` drives `review_gate` to its `blocked: implement` verdict, proving the gate is reading the real key and not falling through `_field`'s `None` | pure |
| 8 | `ExploreResult(...).model_dump(by_alias=True)` fed to `reducers.exploration_output_gate` (with a summary over `MIN_SUMMARY_LENGTH`) returns `None` — i.e. the gate finds `summary` and `verification.fullSuite` | pure |
| 9 | `model_json_schema()` for each model exposes the snake_case field names as required properties, and `ReviewResult`'s schema does not require `commitCount` — pinning what R2 will embed in the prompt | pure |
| 10 | Existing `test_the_shipped_table_is_empty_and_says_why` still asserts `RESULT_MODELS == {}` | unchanged, engine tier |

## 6. Verification

- `full_suite`: `["uv run pytest"]`
- `typecheck`: `""`
- `lint`: `[]`

---

# Five Phase Result Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the five R1 result models (`ExploreResult` with nested `Verification`, `CriticResult`, `PlanResult`, `ImplementResult`, `ReviewResult`) to `src/agent_manager/results.py` on a strict, extra-forbidding private base, with the snake_case/camelCase reconciliation confined to three serialisation aliases.

**Architecture:** One private base class `_Result(BaseModel)` in `results.py` carrying `ConfigDict(extra="forbid", strict=True)` — declared locally, never imported from `models.py`, so `models._Model` is untouched. Every result model subclasses it. Validation is snake_case only; exactly three fields (`full_suite`, `commit_count`, `tagged_count`) carry `Field(serialization_alias=...)` so that `model_dump(by_alias=True)` produces the camelCase keys `steps/reducers.py`'s `_field` already reads. Nothing is registered in `RESULT_MODELS` and no other module changes.

**Tech Stack:** Python 3, pydantic v2 (`BaseModel`, `ConfigDict`, `Field`), pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-define-the-five-phase-c873fc52-design.md` (reproduced verbatim above)

## Global Constraints

- Models live in `src/agent_manager/results.py`; tests live in `tests/test_results.py` (tests mirror `src/agent_manager/` under `tests/`, per CLAUDE.md).
- Test tier: pure-functions/unit, default suite. No fakes, no IO, no temp git repo, no pytest markers.
- Shared base config is exactly `ConfigDict(extra="forbid", strict=True)`, declared in `results.py`. Do **not** import from or modify `src/agent_manager/models.py`.
- Fields are exactly R1's. No extra fields (in particular **no `skill_invoked`** on `PlanResult`), no defaults, no value constraints (`min_length`, `ge`, `gt`) anywhere.
- The four nullable fields (`ExploreResult.reason`, `CriticResult.reason`, `PlanResult.note`, `ImplementResult.blocked_reason`) are `str | None` and **required** — no `= None` default.
- Aliases: `Field(serialization_alias=...)` only, on exactly `full_suite` → `fullSuite`, `commit_count` → `commitCount`, `tagged_count` → `taggedCount`. Never `alias=`, never `validation_alias=`, never `serialize_by_alias` in config.
- `RESULT_MODELS` stays `{}`. Do not touch `workflow/registry.py`, `workflow/builtin/task.yaml`, `dispatch.py`, or `steps/reducers.py`.
- `resolve_result_model` and its `EngineError` are unchanged.
- Verification command for every step that runs the whole suite: `uv run pytest`. There is no lint or typecheck command in this repo.

## Review Focus

Input classes the spec implies but whose tests are easy to omit. Each already has a step in the task that owns it:

1. **A dump that forgets `by_alias=True`.** `ReviewResult(...).model_dump()` gives `commit_count`, which `_field(review, "commitCount")` reads as `None` — `review_gate` then returns a `{"warn": ...}` dict, not `None`. If that silently looked like a pass, the alias would be dead weight nobody noticed. Pinned in Task 5, Step 1.
2. **`True` for an `int` field.** `bool` is an `int` subclass in Python; under strict mode `commit_count=True` must be rejected, or a `true` in an agent's JSON becomes the count `1`. Pinned in Task 4, Step 1.
3. **A non-`str` element inside `list[str]`.** `findings=["ok", None]` and `full_suite=["uv run pytest", 3]` are shapes an LLM produces; the list type must reject them rather than accept a heterogeneous list. Pinned in Task 4, Step 1 and Task 1, Step 5.
4. **`None` for a non-nullable `str`.** `summary=None` on `ExploreResult` must be a `ValidationError`, not a stringified `"None"` that then reads as a 4-character summary at the gate. Pinned in Task 1, Step 5.
5. **An empty `summary`/zero-length list passing validation.** The models deliberately carry no value constraints, so `summary=""` must *validate* and be stopped by `exploration_output_gate` instead — pinning it stops a later contributor from "helpfully" adding `min_length=1` and moving judgement out of the gates. Pinned in Task 5, Step 5.

---

### Task 1: The strict base, `Verification` and `ExploreResult`

**Files:**
- Modify: `src/agent_manager/results.py` (imports at lines 15-19; new code after line 22, before `resolve_result_model`)
- Test: `tests/test_results.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `results._Result` (private base, `model_config = ConfigDict(extra="forbid", strict=True)`); `results.Verification(full_suite: list[str], typecheck: str, lint: list[str])` with `full_suite` serialising as `fullSuite`; `results.ExploreResult(refused: bool, reason: str | None, summary: str, verification: Verification)`. Tasks 2-5 subclass the same `_Result` and import these names from `agent_manager.results`.

- [ ] **Step 1: Write the failing valid-payload test**

Append to `tests/test_results.py`:

```python
# --- The five R1 result models (design §2). Pure tier per design §14 lines
# --- 477-492: plain pydantic validation, no fake adapter and no canned result
# --- file, unlike the engine-tier resolve_result_model tests above.


def test_explore_result_accepts_a_full_payload():
    explore = results.ExploreResult(
        refused=False,
        reason=None,
        summary="read results.py and reducers.py; the gates read camelCase keys",
        verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
    )

    assert explore.refused is False
    assert explore.reason is None
    assert explore.summary.startswith("read results.py")
    assert explore.verification.full_suite == ["uv run pytest"]
    assert explore.verification.typecheck == ""
    assert explore.verification.lint == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_results.py::test_explore_result_accepts_a_full_payload -v`
Expected: FAIL with `AttributeError: module 'agent_manager.results' has no attribute 'ExploreResult'`

- [ ] **Step 3: Write the base and the two models**

In `src/agent_manager/results.py`, change the import line 17 from `from pydantic import BaseModel` to:

```python
from pydantic import BaseModel, ConfigDict, Field
```

and insert after the `RESULT_MODELS` docstring (line 22), before `def resolve_result_model`:

```python
class _Result(BaseModel):
    """Shared config for every phase result model (design §2).

    ``extra="forbid"`` for the reason ``models._Model`` gives: an unknown key in
    an agent-written file is a signal, not something to drop. ``strict=True``
    because a result file is written by a language model -- ``"3"`` for
    ``commit_count`` or ``1`` for ``refused`` is exactly the sloppiness this
    validation exists to catch. Model-wide rather than per-field, because every
    field here is equally untrusted.
    """

    model_config = ConfigDict(extra="forbid", strict=True)


class Verification(_Result):
    """What Explore reports about how this repo is verified (addendum R1)."""

    full_suite: list[str] = Field(serialization_alias="fullSuite")
    typecheck: str
    lint: list[str]


class ExploreResult(_Result):
    """The `explore` phase's result file (`builtin/task.yaml` line 9)."""

    refused: bool
    reason: str | None
    summary: str
    verification: Verification
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_results.py::test_explore_result_accepts_a_full_payload -v`
Expected: PASS

- [ ] **Step 5: Write the failing rejection tests for `ExploreResult` and `Verification`**

Append to `tests/test_results.py` (and add `from pydantic import BaseModel, ValidationError` in place of the existing `from pydantic import BaseModel` at line 9):

```python
def test_explore_result_rejects_a_missing_summary():
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
        )

    assert "summary" in str(caught.value)


def test_explore_result_does_not_coerce_one_into_refused():
    # A JSON `1` where the agent was asked for a boolean is the sloppiness
    # strict mode exists to catch, not something to read as True.
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=1,
            reason=None,
            summary="a summary long enough that the gate would not call it a placeholder",
            verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
        )

    assert "refused" in str(caught.value)


def test_explore_result_rejects_none_for_the_non_nullable_summary():
    # Reading None as "None" would hand the gate a 4-character summary.
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            summary=None,
            verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
        )

    assert "summary" in str(caught.value)


def test_explore_result_rejects_an_unknown_key():
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            summary="a summary long enough that the gate would not call it a placeholder",
            verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
            skill_invoked=True,
        )

    assert "skill_invoked" in str(caught.value)


def test_verification_rejects_a_missing_full_suite_by_its_nested_path():
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            summary="a summary long enough that the gate would not call it a placeholder",
            verification={"typecheck": "", "lint": []},
        )

    message = str(caught.value)
    assert "verification" in message
    assert "full_suite" in message


def test_verification_rejects_a_non_list_full_suite():
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            summary="a summary long enough that the gate would not call it a placeholder",
            verification={"full_suite": "uv run pytest", "typecheck": "", "lint": []},
        )

    message = str(caught.value)
    assert "verification" in message
    assert "full_suite" in message


def test_verification_rejects_a_non_string_inside_full_suite():
    with pytest.raises(ValidationError) as caught:
        results.ExploreResult(
            refused=False,
            reason=None,
            summary="a summary long enough that the gate would not call it a placeholder",
            verification={"full_suite": ["uv run pytest", 3], "typecheck": "", "lint": []},
        )

    assert "full_suite" in str(caught.value)
```

- [ ] **Step 6: Run them to verify they pass**

Run: `uv run pytest tests/test_results.py -v`
Expected: PASS for all of the above and for the three pre-existing tests, including `test_the_shipped_table_is_empty_and_says_why`.

These pass against the Step 3 implementation with no further code: `extra="forbid"`, `strict=True` and the nested model are what make each rejection happen. If any one of them fails, the base config or the field types are wrong — fix `results.py`, not the test.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/results.py tests/test_results.py
git commit -m "feat: add the strict result base, Verification and ExploreResult"
```

---

### Task 2: `CriticResult` and `PlanResult`

**Files:**
- Modify: `src/agent_manager/results.py` (append after `ExploreResult`)
- Test: `tests/test_results.py`

**Interfaces:**
- Consumes: `results._Result` from Task 1.
- Produces: `results.CriticResult(blockers: bool, reason: str | None, summary: str)`; `results.PlanResult(path: str, self_reviewed: bool, note: str | None)`. Neither carries an alias; `PlanResult` has **no** `skill_invoked` field.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_results.py`:

```python
def test_critic_result_accepts_a_full_payload():
    critic = results.CriticResult(
        blockers=True,
        reason="the spec's alias choice contradicts the reducers",
        summary="reviewed the design against reducers.py",
    )

    assert critic.blockers is True
    assert critic.reason == "the spec's alias choice contradicts the reducers"
    assert critic.summary == "reviewed the design against reducers.py"


def test_critic_result_rejects_a_missing_reason():
    # `reason` is nullable but required: the agent says "no reason" explicitly.
    with pytest.raises(ValidationError) as caught:
        results.CriticResult(blockers=False, summary="nothing blocking")

    assert "reason" in str(caught.value)


def test_critic_result_does_not_coerce_a_string_into_blockers():
    with pytest.raises(ValidationError) as caught:
        results.CriticResult(blockers="true", reason=None, summary="nothing blocking")

    assert "blockers" in str(caught.value)


def test_critic_result_rejects_an_unknown_key():
    with pytest.raises(ValidationError) as caught:
        results.CriticResult(
            blockers=False, reason=None, summary="nothing blocking", verdict="ok"
        )

    assert "verdict" in str(caught.value)


def test_plan_result_accepts_a_full_payload():
    plan = results.PlanResult(
        path="docs/superpowers/plans/task-define-the-five-phase-c873fc52.md",
        self_reviewed=True,
        note=None,
    )

    assert plan.path.endswith("c873fc52.md")
    assert plan.self_reviewed is True
    assert plan.note is None


def test_plan_result_rejects_a_missing_path():
    with pytest.raises(ValidationError) as caught:
        results.PlanResult(self_reviewed=True, note=None)

    assert "path" in str(caught.value)


def test_plan_result_does_not_coerce_a_path_object_into_str():
    with pytest.raises(ValidationError) as caught:
        results.PlanResult(path=["a", "b"], self_reviewed=True, note=None)

    assert "path" in str(caught.value)


def test_plan_result_has_no_skill_invoked_field():
    # D6 inlines the methodology into the prompt: there is no skill to detect,
    # so a result file claiming one is an unknown key.
    with pytest.raises(ValidationError) as caught:
        results.PlanResult(
            path="docs/superpowers/plans/p.md",
            self_reviewed=True,
            note=None,
            skill_invoked=True,
        )

    assert "skill_invoked" in str(caught.value)
    assert "skill_invoked" not in results.PlanResult.model_fields
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_results.py -v -k "critic_result or plan_result"`
Expected: FAIL — `AttributeError: module 'agent_manager.results' has no attribute 'CriticResult'`

- [ ] **Step 3: Write the two models**

Append to `src/agent_manager/results.py`, after `ExploreResult`:

```python
class CriticResult(_Result):
    """The `critic` phase's result file (`builtin/task.yaml` lines 39 and 53)."""

    blockers: bool
    reason: str | None
    summary: str


class PlanResult(_Result):
    """The `plan` phase's result file (`builtin/task.yaml` line 46).

    No `skill_invoked`: D6 inlines the planning methodology into the prompt, so
    there is no skill invocation left to report.
    """

    path: str
    self_reviewed: bool
    note: str | None
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_results.py -v -k "critic_result or plan_result"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/results.py tests/test_results.py
git commit -m "feat: add CriticResult and PlanResult"
```

---

### Task 3: `ImplementResult`

**Files:**
- Modify: `src/agent_manager/results.py` (append after `PlanResult`)
- Test: `tests/test_results.py`

**Interfaces:**
- Consumes: `results._Result` from Task 1.
- Produces: `results.ImplementResult(blocked: bool, blocked_reason: str | None, resumed: bool, plan_hash: str, report: str)`. No aliases: `blocked_reason` and `plan_hash` are not read by any reducer.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_results.py`:

```python
def test_implement_result_accepts_a_full_payload():
    implement = results.ImplementResult(
        blocked=False,
        blocked_reason=None,
        resumed=True,
        plan_hash="a1b2c3d4",
        report="tasks 1-3 done, suite green",
    )

    assert implement.blocked is False
    assert implement.blocked_reason is None
    assert implement.resumed is True
    assert implement.plan_hash == "a1b2c3d4"
    assert implement.report == "tasks 1-3 done, suite green"


def test_implement_result_rejects_a_missing_plan_hash():
    with pytest.raises(ValidationError) as caught:
        results.ImplementResult(
            blocked=False, blocked_reason=None, resumed=False, report="done"
        )

    assert "plan_hash" in str(caught.value)


def test_implement_result_does_not_coerce_a_number_into_plan_hash():
    with pytest.raises(ValidationError) as caught:
        results.ImplementResult(
            blocked=False,
            blocked_reason=None,
            resumed=False,
            plan_hash=12345678,
            report="done",
        )

    assert "plan_hash" in str(caught.value)


def test_implement_result_rejects_an_unknown_key():
    with pytest.raises(ValidationError) as caught:
        results.ImplementResult(
            blocked=False,
            blocked_reason=None,
            resumed=False,
            plan_hash="a1b2c3d4",
            report="done",
            commits=3,
        )

    assert "commits" in str(caught.value)


def test_implement_result_keeps_plan_hash_an_unconstrained_string():
    # No format constraint here: reducers.is_plan_hash owns that judgement, and
    # a short hash must reach it as data rather than dying as a ValidationError.
    assert (
        results.ImplementResult(
            blocked=False,
            blocked_reason=None,
            resumed=False,
            plan_hash="nope",
            report="done",
        ).plan_hash
        == "nope"
    )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_results.py -v -k implement_result`
Expected: FAIL — `AttributeError: module 'agent_manager.results' has no attribute 'ImplementResult'`

- [ ] **Step 3: Write the model**

Append to `src/agent_manager/results.py`, after `PlanResult`:

```python
class ImplementResult(_Result):
    """The `implement` phase's result file (`builtin/task.yaml` line 60).

    `plan_hash` carries no format constraint: `reducers.is_plan_hash` owns the
    "8 lowercase hex characters" judgement, and a malformed hash has to reach
    that gate as data rather than dying here.
    """

    blocked: bool
    blocked_reason: str | None
    resumed: bool
    plan_hash: str
    report: str
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_results.py -v -k implement_result`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/results.py tests/test_results.py
git commit -m "feat: add ImplementResult"
```

---

### Task 4: `ReviewResult` and its two serialisation aliases

**Files:**
- Modify: `src/agent_manager/results.py` (append after `ImplementResult`)
- Test: `tests/test_results.py`

**Interfaces:**
- Consumes: `results._Result` from Task 1.
- Produces: `results.ReviewResult(findings: list[str], unresolved_blockers: list[str], fix_summary: str, porcelain: str, commit_count: int, tagged_count: int, plan_hash: str)`, with `commit_count` serialising as `commitCount` and `tagged_count` as `taggedCount`. Task 5 dumps it with `by_alias=True`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_results.py`:

```python
def test_review_result_accepts_a_full_payload():
    review = results.ReviewResult(
        findings=["the alias only applies on dump"],
        unresolved_blockers=[],
        fix_summary="added the serialisation aliases",
        porcelain="",
        commit_count=3,
        tagged_count=3,
        plan_hash="a1b2c3d4",
    )

    assert review.findings == ["the alias only applies on dump"]
    assert review.unresolved_blockers == []
    assert review.fix_summary == "added the serialisation aliases"
    assert review.porcelain == ""
    assert review.commit_count == 3
    assert review.tagged_count == 3
    assert review.plan_hash == "a1b2c3d4"


def test_review_result_rejects_a_missing_tagged_count():
    with pytest.raises(ValidationError) as caught:
        results.ReviewResult(
            findings=[],
            unresolved_blockers=[],
            fix_summary="nothing to fix",
            porcelain="",
            commit_count=3,
            plan_hash="a1b2c3d4",
        )

    assert "tagged_count" in str(caught.value)


def test_review_result_does_not_coerce_a_numeric_string_into_commit_count():
    with pytest.raises(ValidationError) as caught:
        results.ReviewResult(
            findings=[],
            unresolved_blockers=[],
            fix_summary="nothing to fix",
            porcelain="",
            commit_count="3",
            tagged_count=3,
            plan_hash="a1b2c3d4",
        )

    assert "commit_count" in str(caught.value)


def test_review_result_does_not_read_true_as_the_commit_count_one():
    # bool is an int subclass in Python; a JSON `true` must not become 1 commit.
    with pytest.raises(ValidationError) as caught:
        results.ReviewResult(
            findings=[],
            unresolved_blockers=[],
            fix_summary="nothing to fix",
            porcelain="",
            commit_count=True,
            tagged_count=3,
            plan_hash="a1b2c3d4",
        )

    assert "commit_count" in str(caught.value)


def test_review_result_rejects_a_non_string_inside_findings():
    with pytest.raises(ValidationError) as caught:
        results.ReviewResult(
            findings=["ok", None],
            unresolved_blockers=[],
            fix_summary="nothing to fix",
            porcelain="",
            commit_count=3,
            tagged_count=3,
            plan_hash="a1b2c3d4",
        )

    assert "findings" in str(caught.value)


def test_review_result_rejects_an_unknown_key():
    with pytest.raises(ValidationError) as caught:
        results.ReviewResult(
            findings=[],
            unresolved_blockers=[],
            fix_summary="nothing to fix",
            porcelain="",
            commit_count=3,
            tagged_count=3,
            plan_hash="a1b2c3d4",
            commitCount=3,
        )

    assert "commitCount" in str(caught.value)


def test_review_result_dumps_the_two_counts_in_camel_case_only_under_by_alias():
    review = results.ReviewResult(
        findings=[],
        unresolved_blockers=[],
        fix_summary="nothing to fix",
        porcelain="",
        commit_count=3,
        tagged_count=3,
        plan_hash="a1b2c3d4",
    )

    aliased = review.model_dump(by_alias=True)
    assert aliased["commitCount"] == 3
    assert aliased["taggedCount"] == 3
    assert "commit_count" not in aliased
    # Only those two are renamed -- porcelain and plan_hash keep one spelling.
    assert aliased["porcelain"] == ""
    assert aliased["plan_hash"] == "a1b2c3d4"

    assert review.model_dump()["commit_count"] == 3
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_results.py -v -k review_result`
Expected: FAIL — `AttributeError: module 'agent_manager.results' has no attribute 'ReviewResult'`

- [ ] **Step 3: Write the model**

Append to `src/agent_manager/results.py`, after `ImplementResult`:

```python
class ReviewResult(_Result):
    """The `review` phase's result file (`builtin/task.yaml` line 66).

    `commit_count` and `tagged_count` carry camelCase serialisation aliases
    because `reducers.review_gate` reads `commitCount`/`taggedCount` off the
    dumped mapping (lines 187-188) -- that port is a behavioural specification
    and does not move. Serialisation only: validation stays snake_case, so the
    JSON Schema embedded in the agent's prompt names exactly one spelling.
    """

    findings: list[str]
    unresolved_blockers: list[str]
    fix_summary: str
    porcelain: str
    commit_count: int = Field(serialization_alias="commitCount")
    tagged_count: int = Field(serialization_alias="taggedCount")
    plan_hash: str
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_results.py -v -k review_result`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/results.py tests/test_results.py
git commit -m "feat: add ReviewResult with camelCase serialisation aliases"
```

---

### Task 5: The reconciliation, pinned against the real gates and the JSON Schema

**Files:**
- Modify: `src/agent_manager/results.py` (docstring lines 7-12 only)
- Test: `tests/test_results.py`
- Read-only reference: `src/agent_manager/steps/reducers.py:166-211` (`review_gate`), `:257-294` (`exploration_output_gate`)

**Interfaces:**
- Consumes: `results.ExploreResult`, `results.Verification`, `results.ReviewResult` (Tasks 1 and 4); `agent_manager.steps.reducers.review_gate(review, branch, base_branch)`, `reducers.exploration_output_gate(explore, provided_verification)`, `reducers.MIN_SUMMARY_LENGTH` — all unchanged.
- Produces: no new code symbols. This task is the proof that the alias choice is the right one, plus a docstring correction.

- [ ] **Step 1: Write the failing gate-combination tests**

Append to `tests/test_results.py`, and add `from agent_manager.steps import reducers` to the imports at the top of the file (after `from agent_manager import results`):

```python
# --- The one place the snake_case/camelCase mismatch is reconciled (design §3).
# --- Still pure tier: the reducers are pure functions, called directly.

_CLEAN_REVIEW = {
    "findings": [],
    "unresolved_blockers": [],
    "fix_summary": "nothing to fix",
    "porcelain": "",
    "commit_count": 2,
    "tagged_count": 2,
    "plan_hash": "a1b2c3d4",
}


def test_a_dumped_clean_review_passes_the_real_review_gate():
    dumped = results.ReviewResult(**_CLEAN_REVIEW).model_dump(by_alias=True)

    assert reducers.review_gate(dumped, "m2/task-x", "master") is None


def test_a_dump_without_by_alias_is_unusable_to_the_review_gate():
    # Proof the alias is load-bearing: without it the gate finds no counts and
    # warns (skipping the Plan-Hash half) rather than passing.
    dumped = results.ReviewResult(**_CLEAN_REVIEW).model_dump()

    verdict = reducers.review_gate(dumped, "m2/task-x", "master")
    assert verdict is not None
    assert "Plan-Hash gate skipped" in verdict["warn"]


def test_a_dumped_review_with_no_commits_blocks_on_implement():
    # The gate is reading the real commitCount, not falling through _field's None.
    dumped = results.ReviewResult(**{**_CLEAN_REVIEW, "commit_count": 0}).model_dump(
        by_alias=True
    )

    verdict = reducers.review_gate(dumped, "m2/task-x", "master")
    assert verdict["blocked"] == "implement"
    assert "no commits on top of master" in verdict["detail"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_results.py -v -k "review_gate or by_alias"`
Expected: FAIL — `NameError`/`AttributeError` on `reducers` until the import is added; once the import is in place these pass against Task 4's aliases. If `test_a_dumped_clean_review_passes_the_real_review_gate` fails with a `warn` verdict, the aliases in `results.py` are wrong — fix `results.py`, never `reducers.py`.

- [ ] **Step 3: Run them to verify they pass**

Run: `uv run pytest tests/test_results.py -v -k "review_gate or by_alias"`
Expected: PASS

- [ ] **Step 4: Write the failing exploration-gate test**

Append to `tests/test_results.py`:

```python
_REAL_SUMMARY = (
    "results.py holds the result-name table; steps/reducers.py holds the ported "
    "gates and reads camelCase keys off the dumped result mapping"
)


def test_a_dumped_explore_result_passes_the_real_exploration_output_gate():
    assert len(_REAL_SUMMARY) > reducers.MIN_SUMMARY_LENGTH
    dumped = results.ExploreResult(
        refused=False,
        reason=None,
        summary=_REAL_SUMMARY,
        verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
    ).model_dump(by_alias=True)

    assert dumped["verification"]["fullSuite"] == ["uv run pytest"]
    assert reducers.exploration_output_gate(dumped, None) is None


def test_the_models_leave_an_empty_summary_for_the_gate_to_judge():
    # No min_length on the models on purpose: plausibility is the gate's job,
    # so an empty summary must validate and then be stopped by the gate.
    dumped = results.ExploreResult(
        refused=True,
        reason="refused to explore",
        summary="",
        verification={"full_suite": ["uv run pytest"], "typecheck": "", "lint": []},
    ).model_dump(by_alias=True)

    verdict = reducers.exploration_output_gate(dumped, None)
    assert "implausibly short/placeholder" in verdict["detail"]
```

- [ ] **Step 5: Run them to verify they pass**

Run: `uv run pytest tests/test_results.py -v -k exploration_output_gate`
Expected: PASS (the nested `fullSuite` alias from Task 1 is what makes the first one pass; if it fails with "did not return an array for verification.fullSuite", `Verification.full_suite`'s `serialization_alias` is missing).

- [ ] **Step 6: Write the failing JSON-Schema spelling test**

Append to `tests/test_results.py`:

```python
def test_the_embedded_json_schema_names_snake_case_only():
    # R2 embeds model_json_schema() in the agent's prompt, and validation
    # accepts snake_case only -- so the schema must name exactly that spelling.
    review_schema = results.ReviewResult.model_json_schema()
    assert "commit_count" in review_schema["properties"]
    assert "tagged_count" in review_schema["properties"]
    assert "commitCount" not in review_schema["properties"]
    assert "commitCount" not in review_schema["required"]
    assert set(review_schema["required"]) == {
        "findings",
        "unresolved_blockers",
        "fix_summary",
        "porcelain",
        "commit_count",
        "tagged_count",
        "plan_hash",
    }

    explore_schema = results.ExploreResult.model_json_schema()
    assert set(explore_schema["required"]) == {
        "refused",
        "reason",
        "summary",
        "verification",
    }
    verification_schema = explore_schema["$defs"]["Verification"]
    assert set(verification_schema["required"]) == {"full_suite", "typecheck", "lint"}

    assert set(results.CriticResult.model_json_schema()["required"]) == {
        "blockers",
        "reason",
        "summary",
    }
    assert set(results.PlanResult.model_json_schema()["required"]) == {
        "path",
        "self_reviewed",
        "note",
    }
    assert set(results.ImplementResult.model_json_schema()["required"]) == {
        "blocked",
        "blocked_reason",
        "resumed",
        "plan_hash",
        "report",
    }
```

- [ ] **Step 7: Run it to verify it passes**

Run: `uv run pytest tests/test_results.py::test_the_embedded_json_schema_names_snake_case_only -v`
Expected: PASS. A failure here means an alias leaked into the validation direction (`alias=` or `serialize_by_alias` instead of `serialization_alias=`) — fix `results.py`.

- [ ] **Step 8: Reword the one false clause in the module docstring**

In `src/agent_manager/results.py`, replace lines 7-12:

```python
The table ships empty, and that is deliberate. `builtin/task.yaml` names five
result models -- `ExploreResult`, `CriticResult`, `PlanResult`,
`ImplementResult`, `ReviewResult` -- and the design spec gives a field schema
for none of them, so inventing one here would be a design decision this card
was not given. An unresolved name fails loudly at dispatch time instead, which
is strictly better than validating nothing and calling the result `ok`.
```

with:

```python
The table ships empty, and that is deliberate. `builtin/task.yaml` names five
result models -- `ExploreResult`, `CriticResult`, `PlanResult`,
`ImplementResult`, `ReviewResult` -- and they are defined below, but wiring
them into the table is a separate card's decision, not this one's. An
unresolved name fails loudly at dispatch time instead, which is strictly
better than validating nothing and calling the result `ok`.
```

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with `tests/test_results.py::test_the_shipped_table_is_empty_and_says_why` and every test in `tests/steps/test_reducers.py` green and unmodified.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/results.py tests/test_results.py
git commit -m "test: pin the result-model spelling reconciliation against the real gates"
```
