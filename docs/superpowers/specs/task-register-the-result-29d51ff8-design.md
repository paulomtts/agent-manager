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
