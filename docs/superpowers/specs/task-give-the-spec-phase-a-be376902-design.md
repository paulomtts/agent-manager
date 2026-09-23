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
