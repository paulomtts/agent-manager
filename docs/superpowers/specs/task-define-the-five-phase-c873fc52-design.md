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
