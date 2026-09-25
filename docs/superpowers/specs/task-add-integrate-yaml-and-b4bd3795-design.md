# Subtask b4bd3795: add `integrate.yaml` and the resolver's inputs

Story 5216cbee, "The resolver: a role, a result and a document" (Integrate addendum decision I3). Milestone db5b5a3b. This narrows `docs/superpowers/specs/2026-09-25-integrate-design.md` (I3 at line 60, I6 at line 86) to one subtask. It extends the orchestration, parallel-stories and agent-manager design specs.

## Prerequisites (verified present in this worktree)

The exploration reported these as missing on `master` at 8e603d3. They are present on this worktree's branch, so the card can be delivered as worded. This card must not redo or change any of them:

- `roles/bundles/resolver/` and `results.ResolveResult` (registered in `RESULT_MODELS`) come from sibling 5d1e8ce2.
- `steps/integrate.py` comes from 9b04dd11 and its siblings. It provides `merge_tip(repo_dir, worktree, integration_branch, base_branch, tip, git_runner=run_git)` and `merge_completed_gate(result, worktree, *, git_runner=run_git) -> dict[str, str] | None`. The gate never reads `result`. It passes only when there is no `MERGE_HEAD`, `git status --porcelain --untracked-files=all` is clean, and no touched file still holds conflict markers. Otherwise it returns `{"detail": ...}`.
- `merge_completed_gate` is already registered in `default_registry()` and listed in `BUILTIN_FUNCTION_NAMES` in `workflow/registry.py`. `tests/workflow/test_registry.py` already pins it as `INTEGRATE_ONLY_NAMES`, and `tests/test_engine.py:203` already proves `bind_arguments` binds it. No registry change is in scope.

If a later stage finds any of these missing, it must stop and report the gap, not build it.

## Scope

1. **`src/agent_manager/prompt.py`**: add two rows to `_TABLE`.
   - `"merge_tip": _verbatim("merge_tip")`
   - `"conflict_files": _inline_json("conflict_files")`

   Input names are the document's vocabulary and context keys are the callees' names. `branch`, `base_branch` (which reads `base`) and `verification` (which reads `commands`) already exist. Neither new row reads another phase's result, so `INPUT_PRODUCERS` stays `{"plan_hash": "docs_commit"}`. The addendum (line 37) authorises growing the §7 table.
2. **New `src/agent_manager/workflow/builtin/integrate.yaml`**, with two phases and no `worktree` phase. The integration worktree arrives through `SubtaskRun.worktree_path`, which becomes context key `worktree`.
   - `resolve` is an agent phase: role `resolver`, inputs `[branch, base_branch, merge_tip, conflict_files, verification]`, result `ResolveResult`, gates `[merge_completed_gate]`, retry `{ max_attempts: 2, on: [gate_failed, schema_invalid] }`.
   - `verify` is a deterministic phase: `run: verify.run_suite`, gates `[verification_passed_gate]`. It is shaped exactly like `task.yaml`'s `verify`.
   - Add a top-level description in `task.yaml`'s style.
3. **No engine change.** `merge_tip` and `conflict_files` reach the context only through `run_subtask(..., extra_context={"merge_tip": ..., "conflict_files": [...]})`. Neither key is in `RESERVED_CONTEXT_KEYS`.

Out of scope: orchestrator-level Integrate wiring (I1, I2, I4, I5 and I7), the I6 payload, `--no-integrate`, a milestone-aware `am resume`, watch/retry/cancel, cost capture, the reviewer's Plan-Hash brief, marking slow tests, per-story readiness, and any change to the resolver role, `ResolveResult`, `steps/integrate.py` or the registry.

## Observable behaviour

- `load_builtin("integrate")` loads against `default_registry()`, with `phase_names == ("resolve", "verify")`. `load_builtin("task")` is unchanged.
- The rendered `resolve` prompt contains the branch, the base branch, the merge tip verbatim, the conflict file list as inline JSON, and the verification commands.
- Git alone decides whether `resolve` passes. The resolver's `resolved` flag is never consulted (rule 3).
- When `resolve` fails, the gate's `detail` is appended to the next brief under `prompt.FEEDBACK_HEADING`. A second failure escalates the subtask at `resolve`, and `verify` does not run.
- After a passing `resolve`, `verify` runs the given commands in the integration worktree. `verification_passed_gate` judges the result.
- Nothing in this flow writes the base branch or pushes (rule 4).

## Error paths

- If `extra_context` omits `merge_tip` or `conflict_files`, rendering fails with the resolver's existing missing-key error. Nothing new is added for this.
- If `merge_completed_gate` hits a git failure, the error propagates as it already does. It is never counted as a pass.
- A `resolve` result that fails schema validation is retried once under `schema_invalid`, then escalates.

## Tests

The tiers follow the test-placement rule from the exploration. `uv run pytest` is the default run, and `pyproject.toml` deselects `e2e`-marked tests. By the existing layout, document tests go in `tests/workflow/test_builtin_*.py`, prompt-table tests in `tests/test_prompt.py`, and engine-level tests with an injected `agent_runner` at the `tests/` level. None of the tests below uses the `e2e` marker or a subprocess `claude`.

**`tests/test_prompt.py`** (prompt-table tier, default run)
- Update `test_the_table_carries_exactly_the_ten_names_section_7_fixes` to twelve names by adding `merge_tip` and `conflict_files`. Rename it to match, and point its docstring at the Integrate addendum.
- `merge_tip` renders the context's `merge_tip` verbatim.
- `conflict_files` renders the context's list as inline JSON.
- `INPUT_PRODUCERS` is still `{"plan_hash": "docs_commit"}`. The existing test already covers this and needs no change.

**New `tests/workflow/test_builtin_integrate.py`** (document tier, default run). Model it on `tests/workflow/test_builtin_task.py`.
- The document loads against `default_registry()`, and its phases are exactly `resolve` (agent) then `verify` (deterministic). There is no `worktree` phase.
- `resolve` has role `resolver`, the five inputs in order, result `ResolveResult`, gates `[merge_completed_gate]`, and retry `max_attempts == 2` on `gate_failed` and `schema_invalid`.
- `verify` has `run == "verify.run_suite"` and gates `[verification_passed_gate]`.
- Every run/gate name the document uses is in `default_registry().names()`.
- Every gate binds every parameter through `engine.bind_arguments` against a context built by `subtask_context` plus `extra_context` and real `ResolveResult` and verify results. Copy the pattern from `test_every_gate_binds_every_parameter_against_real_results` and `_values_for`.
- Every declared input of `resolve` resolves through `prompt.render_prompt` against that same context. Copy the pattern from `test_every_declared_result_name_resolves_through_the_shipped_table`.

**New `tests/test_integrate_workflow.py`** (engine tier, default run). It uses real temporary git, an injected fake `agent_runner`, and `engine.run_subtask(load_builtin("integrate"), store, story_id=, subtask=, repo_dir=, commands=, extra_context={"merge_tip", "conflict_files"}, agent_runner=)`. For setup, make a base branch and two branches that edit the same line. Merge the first, then call `steps.integrate.merge_tip` so the second conflicts and stays in progress. Build a synthetic `models.SubtaskRun` with card_id, branch set to the integration branch, base_branch, and worktree_path set to the integration worktree. Per rule 1, the fake finds the conflicting files only by parsing its prompt text. It never reads git for the list, never computes a plan hash, and never commits docs.
- A fake that rewrites the conflicted files, then runs `git add` and `git commit`, ends the subtask `done`. `verify` runs the given commands in the worktree, and `MERGE_HEAD` is gone.
- A fake that commits with markers still in place is retried once, and the second brief contains `prompt.FEEDBACK_HEADING` and the gate's detail naming the marked file. After the second failure the subtask escalates at `resolve` and `verify` never runs.
- A fake that returns `resolved=True` without touching the tree (`MERGE_HEAD` still present) is rejected by `merge_completed_gate` and escalates. This shows the flag is advisory.
- In every case the base branch ref is identical before and after, and nothing is pushed. Assert that no remote ref changed, or use a bare `origin` whose refs are compared.

**Whole suite:** `uv run pytest` must stay green, including `tests/e2e` (rule 2).
