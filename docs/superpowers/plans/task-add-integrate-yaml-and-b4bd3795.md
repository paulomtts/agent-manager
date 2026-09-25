<!-- task-pipeline: validated -->
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

---

# Integrate document and resolver inputs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `builtin/integrate.yaml` (an agent `resolve` phase judged by git, then a deterministic `verify`) and the two §7 prompt-table rows (`merge_tip`, `conflict_files`) the resolver needs, proven end to end against a real git conflict.

**Architecture:** Two production edits only: two rows appended to `prompt._TABLE`, and one new YAML document in `src/agent_manager/workflow/builtin/`. Everything else (the resolver role, `ResolveResult`, `steps/integrate.merge_tip`, `merge_completed_gate` and its registration, `engine.run_subtask`, `dispatch.AgentRunner`) already exists on this branch and is consumed unchanged. The engine-tier proof drives the real `dispatch.AgentRunner` as the injected `agent_runner` (that runner is what owns gates, retries and feedback, so a bare lambda would prove nothing about them); only the harness adapter and the launcher are doubles, and the launcher learns the conflict list solely from the brief file it is handed.

**Tech Stack:** Python 3, pytest, Pydantic, PyYAML, real `git` CLI in `tmp_path`, `uv run pytest`.

**Spec:** `docs/superpowers/specs/task-add-integrate-yaml-and-b4bd3795-design.md` (prepended verbatim above).

## Global Constraints

- Worktree: `/home/paulomtts/Code/agent-manager/.claude/worktrees/m5/task-add-integrate-yaml-and-b4bd3795`, branch `m5/task-add-integrate-yaml-and-b4bd3795`. Every path below is relative to that worktree.
- Prerequisites must already be present and must not be modified: `src/agent_manager/roles/bundles/resolver/`, `results.ResolveResult` in `RESULT_MODELS`, `src/agent_manager/steps/integrate.py` (`merge_tip`, `merge_completed_gate`), and `merge_completed_gate` in `default_registry()` / `BUILTIN_FUNCTION_NAMES`. Task 0 checks this; if any is missing, stop and report the gap, do not build it.
- No engine change: `src/agent_manager/engine.py` and `src/agent_manager/dispatch.py` are not edited. `merge_tip` and `conflict_files` enter only through `run_subtask(..., extra_context=...)`.
- No registry change: `src/agent_manager/workflow/registry.py` and `tests/workflow/test_registry.py` are not edited.
- `prompt.INPUT_PRODUCERS` must remain exactly `{"plan_hash": "docs_commit"}`.
- Rule 1: the fake resolver learns the conflict files only by parsing its prompt text; it never asks git for the list, never computes a plan hash, never commits docs.
- Rule 3: git judges the merge, never the resolver's `resolved` flag.
- Rule 4: nothing writes the base branch (`main` in the tests) or pushes; every engine-tier scenario asserts it against a bare `origin`.
- No test uses the `e2e` marker or a subprocess `claude`.
- Verification: `uv run pytest` must be green, including `tests/e2e` as collected by the default run.

## Review Focus

- A conflict path containing spaces, quotes or non-ASCII characters must render in the `conflict_files` section as exact JSON strings the resolver can parse back unchanged (test added to Task 1).
- A caller that supplies the `conflict_files` key as `None` must get the renderer's "never populated" `EngineError` naming the input, not a `null` in the brief (test added to Task 1).
- A caller that omits `merge_tip` from `extra_context` must get an `EngineError` naming phase `resolve` and parameter `merge_tip` out of `run_subtask` before any dispatch, with the merge still in progress (test added to Task 3).
- A resolver that twice writes a result failing `ResolveResult` validation must escalate at `resolve` after exactly two `schema_invalid` attempts, with feedback on the second brief and the merge left in progress (test added to Task 3).
- A clean resolve followed by a red suite must escalate at `verify` with the suite's diagnostic, never report `done` (test added to Task 3).

## File Structure

- Modify `src/agent_manager/prompt.py` (the `_TABLE` dict at lines 267-279): two new rows plus a docstring update. One responsibility: the fixed input-resolution table.
- Modify `tests/test_prompt.py`: rename and widen the table-size test (lines 423-459); add rendering tests for the two rows next to it.
- Create `src/agent_manager/workflow/builtin/integrate.yaml`: the resolver document.
- Create `tests/workflow/test_builtin_integrate.py`: document tier, sibling of `tests/workflow/test_builtin_task.py`.
- Create `tests/test_integrate_workflow.py`: engine tier with real git, sibling of `tests/test_engine.py`.

---

### Task 0: Confirm the prerequisites are present

**Files:**
- Read only: `src/agent_manager/steps/integrate.py`, `src/agent_manager/results.py`, `src/agent_manager/workflow/registry.py`, `src/agent_manager/roles/bundles/resolver/`

**Interfaces:**
- Consumes: nothing.
- Produces: confirmation that `merge_tip`, `merge_completed_gate`, `ResolveResult` and the `resolver` role exist. Every later task depends on them.

- [ ] **Step 1: Run the existing prerequisite tests**

Run: `uv run pytest tests/steps/test_integrate.py tests/workflow/test_registry.py tests/roles/test_loader.py tests/test_results.py -q`
Expected: PASS. If collection fails with an `ImportError` for `merge_tip`, `merge_completed_gate` or `ResolveResult`, or `tests/workflow/test_registry.py::test_default_registry_resolves_the_merge_gate_to_the_real_callable` fails, STOP and report the missing prerequisite. Do not build it.

- [ ] **Step 2: Confirm the resolver bundle loads**

Run: `uv run python -c "from agent_manager.roles.loader import load_role; print(load_role('resolver').name)"`
Expected: prints `resolver`. Anything else: STOP and report.

No commit: nothing changed.

---

### Task 1: Add `merge_tip` and `conflict_files` to the §7 table

**Files:**
- Modify: `src/agent_manager/prompt.py:267-279`
- Test: `tests/test_prompt.py` (replace lines 423-459, add tests after it)

**Interfaces:**
- Consumes: `prompt._verbatim(key: str) -> Resolver`, `prompt._inline_json(key: str, *, allow_empty: bool = False) -> Resolver` (both existing).
- Produces: `prompt.render_prompt` accepts the input names `"merge_tip"` (renders `str(context["merge_tip"])`) and `"conflict_files"` (renders `json.dumps(context["conflict_files"], indent=2, ensure_ascii=False, default=str)`). Tasks 2 and 3 rely on both names and on these exact renderings.

- [ ] **Step 1: Write the failing tests**

In `tests/test_prompt.py`, replace the whole function `test_the_table_carries_exactly_the_ten_names_section_7_fixes` (lines 423-459) with the following block (the renamed table test, then the new rendering tests):

```python
TWELVE_INPUTS = [
    "card",
    "parent_story",
    "repo_docs",
    "explore",
    "spec_path",
    "plan_path",
    "branch",
    "base_branch",
    "verification",
    "plan_hash",
    "merge_tip",
    "conflict_files",
]

MERGE_TIP = "m5/story-the-resolver-5216cbee"
CONFLICT_FILES = ["src/agent_manager/prompt.py", "tests/test_prompt.py"]


def test_the_table_carries_exactly_the_twelve_names_section_7_and_integrate_fix():
    """§7's table is fixed. A thirteenth name is a design change, not a code change.

    `plan_hash` is the tenth, added by card f26b377d together with its row in
    §7's table in `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.
    `merge_tip` and `conflict_files` are the eleventh and twelfth, the resolver's
    inputs, which the Integrate addendum
    (`docs/superpowers/specs/2026-09-25-integrate-design.md` §2) says must be
    added to this table (card b4bd3795).
    """
    rendered = prompt.render_prompt(
        _phase(TWELVE_INPUTS),
        _context(merge_tip=MERGE_TIP, conflict_files=list(CONFLICT_FILES)),
    )

    assert rendered.inputs == tuple(TWELVE_INPUTS)
    assert sorted(prompt._TABLE) == sorted(rendered.inputs)


def test_merge_tip_renders_the_tip_ref_verbatim():
    rendered = prompt.render_prompt(
        _phase(["merge_tip"], name="resolve", role="resolver"),
        _context(merge_tip=MERGE_TIP),
    )

    assert _section(rendered, "merge_tip") == MERGE_TIP
    assert f"\n## merge_tip\n{MERGE_TIP}\n" in rendered.text


def test_conflict_files_inlines_the_list_as_json():
    rendered = prompt.render_prompt(
        _phase(["conflict_files"], name="resolve", role="resolver"),
        _context(conflict_files=list(CONFLICT_FILES)),
    )

    body = _section(rendered, "conflict_files")
    assert json.loads(body) == CONFLICT_FILES
    assert body == json.dumps(CONFLICT_FILES, indent=2, ensure_ascii=False)


def test_conflict_file_names_with_spaces_quotes_and_non_ascii_round_trip_exactly():
    """Review Focus: the resolver parses this list back out of its brief, so a
    path git reports verbatim must come back out of the JSON verbatim."""
    files = ["docs/release notes.md", "src/café.py", 'notes/"quoted".txt']
    rendered = prompt.render_prompt(
        _phase(["conflict_files"], name="resolve", role="resolver"),
        _context(conflict_files=files),
    )

    body = _section(rendered, "conflict_files")
    assert json.loads(body) == files
    assert "café" in body  # ensure_ascii=False: no \u escapes in the brief


def test_a_conflict_files_key_that_was_never_populated_is_named_as_such():
    """Review Focus: `None` is a caller bug, never a `null` in the brief."""
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(
            _phase(["conflict_files"], name="resolve", role="resolver"),
            _context(conflict_files=None),
        )

    assert caught.value.phase == "resolve"
    assert caught.value.parameter == "conflict_files"
    assert "never populated" in str(caught.value)


def test_a_context_without_merge_tip_names_the_key_it_reads():
    """Spec error path: an `extra_context` that omits `merge_tip` fails with the
    resolver's existing missing-key error, nothing new."""
    with pytest.raises(EngineError) as caught:
        prompt.render_prompt(
            _phase(["merge_tip"], name="resolve", role="resolver"), _context()
        )

    assert caught.value.phase == "resolve"
    assert caught.value.parameter == "merge_tip"
    assert "nothing in the context supplies it" in str(caught.value)
```

Leave `test_the_producer_map_is_derived_from_the_table_it_describes` (which asserts `INPUT_PRODUCERS == {"plan_hash": "docs_commit"}`) exactly as it is.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompt.py -v -k "twelve_names or merge_tip or conflict_file"`
Expected: FAIL. `test_the_table_carries_exactly_the_twelve_names_section_7_and_integrate_fix`, `test_merge_tip_renders_the_tip_ref_verbatim`, `test_conflict_files_inlines_the_list_as_json`, `test_conflict_file_names_with_spaces_quotes_and_non_ascii_round_trip_exactly` fail with `EngineError: ... 'merge_tip' is not an input this engine knows how to resolve` (or `'conflict_files' ...`). The two error-path tests also fail, because the raised error is the unknown-input one, and its message says "is not an input this engine knows" rather than "never populated" or "nothing in the context supplies it".

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/prompt.py`, replace the `_TABLE` definition and its docstring (lines 267-279) with:

```python
_TABLE: dict[str, Resolver] = {
    "card": _inline_json("card_details"),
    "parent_story": _inline_json("parent_story_details", allow_empty=True),
    "repo_docs": _repo_docs,
    "explore": _inline_json("explore"),
    "verification": _inline_json("commands"),
    "spec_path": _verbatim("spec_path"),
    "plan_path": _verbatim("plan_path"),
    "branch": _verbatim("branch"),
    "base_branch": _verbatim("base"),
    "plan_hash": _phase_field("docs_commit", "plan_hash"),
    "merge_tip": _verbatim("merge_tip"),
    "conflict_files": _inline_json("conflict_files"),
}
"""The fixed §7 resolution table, keyed by the name a document may declare.

The last two rows are the resolver's (Integrate addendum §2 and I3,
`builtin/integrate.yaml`): the story tip being merged, inlined as a ref, and
the conflicting paths `steps.integrate.merge_tip` reported, inlined as JSON.
Neither reads another phase's result, so neither appears in `INPUT_PRODUCERS`:
the caller supplies both through `engine.run_subtask(extra_context=...)`.
"""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS, every test in the file, including `test_the_producer_map_is_derived_from_the_table_it_describes`.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/prompt.py tests/test_prompt.py
git commit -m "feat(prompt): resolve merge_tip and conflict_files for the resolver"
```

---

### Task 2: Ship `builtin/integrate.yaml` with its document-tier tests

**Files:**
- Create: `src/agent_manager/workflow/builtin/integrate.yaml`
- Test: `tests/workflow/test_builtin_integrate.py`

**Interfaces:**
- Consumes: Task 1's `merge_tip` and `conflict_files` table rows; existing `workflow.load_builtin(name, registry=None) -> Workflow`, `engine.subtask_context(subtask, repo_dir, commands) -> dict`, `engine.bind_arguments(fn, values, args=None, *, phase, function) -> dict`, `dispatch.gate_values(context, phase_name, result) -> dict`, `prompt.render_prompt(phase, context) -> RenderedPrompt`, `results.resolve_result_model(name, table, *, phase)`.
- Produces: `load_builtin("integrate")` returning a `Workflow` named `integrate` with `phase_names == ("resolve", "verify")`; `workflow.functions` keys exactly `{"merge_completed_gate", "verification_passed_gate", "verify.run_suite"}`. Task 3 drives this document.

- [ ] **Step 1: Write the failing tests**

Create `tests/workflow/test_builtin_integrate.py`:

```python
"""Pure-functions tier (design spec §14): the packaged `builtin/integrate.yaml`
loaded against the default registry (Integrate addendum I3, card b4bd3795).

Nothing outside this process is touched -- no git, no network, no filesystem
beyond the packaged YAML. `merge_completed_gate` is bound here but never
called: calling it runs git, and that is the engine tier's job
(`tests/test_integrate_workflow.py`)."""

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from agent_manager import dispatch, engine, models, prompt
from agent_manager.results import RESULT_MODELS, ResolveResult, resolve_result_model
from agent_manager.steps import integrate, reducers, verify
from agent_manager.workflow import load_builtin
from agent_manager.workflow.loader import AgentPhase, DeterministicPhase, builtin_path
from agent_manager.workflow.registry import default_registry

EXPECTED_PHASES = (("resolve", "agent"), ("verify", "deterministic"))
RESOLVE_INPUTS = ["branch", "base_branch", "merge_tip", "conflict_files", "verification"]
INTEGRATE_FUNCTIONS = {"merge_completed_gate", "verification_passed_gate", "verify.run_suite"}

REPO_DIR = Path("/repo")
SUITE = ["uv run pytest"]
INTEGRATION_BRANCH = "m5-integrate"
BASE_BRANCH = "main"
MERGE_TIP = "m5/story-the-resolver-5216cbee"
CONFLICT_FILES = ["src/agent_manager/prompt.py", "docs/notes with space.md"]

# Integrate addendum I3: card = the conflicting story's id, branch = the
# integration branch, base = the base branch, worktree = the integration worktree.
SUBTASK = models.SubtaskRun(
    card_id="5216cbee",
    branch=INTEGRATION_BRANCH,
    base_branch=BASE_BRANCH,
    status="started",
    worktree_path=Path("/repo/.claude/worktrees/m5-integrate"),
)
EXTRA_CONTEXT = {"merge_tip": MERGE_TIP, "conflict_files": list(CONFLICT_FILES)}


def test_builtin_integrate_loads_against_the_default_registry() -> None:
    workflow = load_builtin("integrate")
    assert workflow.name == "integrate"
    assert workflow.description
    assert builtin_path("integrate").is_file()


def test_builtin_integrate_is_resolve_then_verify_with_no_worktree_phase() -> None:
    """The integration worktree arrives through `SubtaskRun.worktree_path`
    (context key `worktree`); `merge_tip` already made it, so no phase here may
    create or re-point one."""
    workflow = load_builtin("integrate")
    assert tuple((phase.name, phase.kind) for phase in workflow.phases) == EXPECTED_PHASES
    assert workflow.phase_names == ("resolve", "verify")
    assert "worktree" not in workflow.phase_names


def test_the_task_document_is_unchanged_by_the_new_builtin() -> None:
    assert load_builtin("task").phase_names[0] == "worktree"
    assert len(load_builtin("task").phases) == 14


def test_resolve_is_the_resolver_judged_by_the_merge_gate_with_two_attempts() -> None:
    phase = load_builtin("integrate").phase("resolve")
    assert isinstance(phase, AgentPhase)
    assert phase.role == "resolver"
    assert phase.inputs == RESOLVE_INPUTS
    assert phase.result == "ResolveResult"
    assert phase.gates == ["merge_completed_gate"]
    assert phase.writes is None
    assert phase.retry is not None
    assert phase.retry.max_attempts == 2
    assert phase.retry.on == ["gate_failed", "schema_invalid"]


def test_verify_is_shaped_exactly_like_the_task_documents_verify() -> None:
    phase = load_builtin("integrate").phase("verify")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "verify.run_suite"
    assert phase.gates == ["verification_passed_gate"]
    assert phase.args == {}
    assert phase.best_effort is False
    assert phase.when is None
    assert phase.skip_to is None
    assert phase == load_builtin("task").phase("verify")


def test_the_resolve_result_name_resolves_to_the_resolve_model() -> None:
    phase = load_builtin("integrate").phase("resolve")
    assert isinstance(phase, AgentPhase)
    model = resolve_result_model(phase.result, RESULT_MODELS, phase=phase.name)
    assert model is ResolveResult


def test_every_name_the_document_uses_is_a_registry_binding() -> None:
    workflow = load_builtin("integrate")
    registry = default_registry()
    assert set(workflow.functions) == INTEGRATE_FUNCTIONS
    assert set(workflow.functions) <= set(registry.names())
    for name, fn in workflow.functions.items():
        assert fn is registry.resolve(name)
    assert workflow.function("merge_completed_gate") is integrate.merge_completed_gate
    assert workflow.function("verification_passed_gate") is reducers.verification_passed_gate
    assert workflow.function("verify.run_suite") is verify.run_suite


def test_the_two_builtins_together_use_every_registered_name() -> None:
    """The registry pins exactly the names the builtin documents use
    (`tests/workflow/test_registry.py`); with this document shipped, that
    claim is now checkable against the documents themselves."""
    used = set(load_builtin("task").functions) | set(load_builtin("integrate").functions)
    assert used == set(default_registry().names())


def test_no_resolve_input_is_produced_by_another_phase_or_reserved() -> None:
    """Both new inputs come from the caller's `extra_context`: none is read out
    of a phase result (so resume needs no back-off) and none is a key the
    engine owns (so `run_subtask` accepts them)."""
    assert not set(RESOLVE_INPUTS) & set(prompt.INPUT_PRODUCERS)
    for key in EXTRA_CONTEXT:
        assert key not in engine.RESERVED_CONTEXT_KEYS


# ── every gate binds against real results ────────────────────────────────────


def _phase_results() -> dict[str, Any]:
    """What each phase leaves in the binding table on a healthy run: the JSON
    dump of a real `ResolveResult` (as `dispatch.classify` hands it on) and the
    mapping `verify.run_suite` returns."""
    return {
        "resolve": ResolveResult(
            resolved=True, summary="kept both stories' edits in both files"
        ).model_dump(mode="json"),
        "verify": {"passed": True, "verified": [], "detail": ""},
    }


def _context() -> dict[str, Any]:
    """`engine.subtask_context` plus the caller's `extra_context`, as
    `engine.run_subtask` builds it."""
    context = engine.subtask_context(SUBTASK, REPO_DIR, SUITE)
    context.update(EXTRA_CONTEXT)
    return context


def _values_for(phase_name: str) -> dict[str, Any]:
    """The binding table this phase's gates really see: the context, every
    earlier phase's result under its own name, then `dispatch.gate_values`'
    `result` / `<phase name>` overlay (`engine._gate_values` builds the same
    table for the deterministic `verify`)."""
    results = _phase_results()
    context = _context()
    for name in load_builtin("integrate").phase_names:
        if name == phase_name:
            break
        if name not in engine.RESERVED_CONTEXT_KEYS:
            context[name] = results[name]
    return dispatch.gate_values(context, phase_name, results[phase_name])


GATED_PHASES = [
    ("resolve", "merge_completed_gate"),
    ("verify", "verification_passed_gate"),
]


def test_the_document_still_names_exactly_the_gates_this_suite_covers() -> None:
    workflow = load_builtin("integrate")
    assert [(p.name, g) for p in workflow.phases for g in p.gates] == GATED_PHASES


def _required(fn: Any) -> set[str]:
    return {
        p.name
        for p in inspect.signature(fn).parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }


@pytest.mark.parametrize(("phase_name", "gate_name"), GATED_PHASES)
def test_every_gate_binds_every_required_parameter_against_real_results(
    phase_name: str, gate_name: str
) -> None:
    gate = load_builtin("integrate").function(gate_name)
    bound = engine.bind_arguments(
        gate, _values_for(phase_name), phase=phase_name, function=gate_name
    )
    assert set(bound) == _required(gate)


def test_the_merge_gate_binds_the_integration_worktree_and_never_the_git_runner() -> None:
    gate = load_builtin("integrate").function("merge_completed_gate")
    bound = engine.bind_arguments(
        gate, _values_for("resolve"), phase="resolve", function="merge_completed_gate"
    )
    assert bound["worktree"] == SUBTASK.worktree_path
    assert bound["result"] == _phase_results()["resolve"]
    assert "git_runner" not in bound


def test_the_verification_gate_passes_a_green_suite_on_the_verify_phase() -> None:
    gate = load_builtin("integrate").function("verification_passed_gate")
    bound = engine.bind_arguments(
        gate, _values_for("verify"), phase="verify", function="verification_passed_gate"
    )
    assert gate(**bound) is None


def test_every_resolve_input_renders_through_the_shipped_table() -> None:
    phase = load_builtin("integrate").phase("resolve")
    assert isinstance(phase, AgentPhase)
    rendered = prompt.render_prompt(phase, _context())

    assert rendered.inputs == tuple(RESOLVE_INPUTS)
    assert rendered.text.startswith("# phase: resolve\n# role: resolver\n")
    bodies = dict(rendered.sections)
    assert bodies["branch"] == INTEGRATION_BRANCH
    assert bodies["base_branch"] == BASE_BRANCH
    assert bodies["merge_tip"] == MERGE_TIP
    assert json.loads(bodies["conflict_files"]) == CONFLICT_FILES
    assert json.loads(bodies["verification"]) == SUITE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_builtin_integrate.py -v`
Expected: FAIL. Every test that calls `load_builtin("integrate")` fails with `WorkflowLoadError: no builtin workflow named 'integrate' (...integrate.yaml); builtins: task`, and `builtin_path("integrate").is_file()` is false. `test_the_task_document_is_unchanged_by_the_new_builtin` and `test_no_resolve_input_is_produced_by_another_phase_or_reserved` pass already; that is expected.

- [ ] **Step 3: Write the document**

Create `src/agent_manager/workflow/builtin/integrate.yaml`:

```yaml
name: integrate
description: Resolve one story tip's merge conflict in the integration worktree, then verify it.

phases:
  - name: resolve
    kind: agent
    role: resolver
    inputs: [branch, base_branch, merge_tip, conflict_files, verification]
    result: ResolveResult
    gates: [merge_completed_gate]
    retry: { max_attempts: 2, on: [gate_failed, schema_invalid] }

  - name: verify
    kind: deterministic
    run: verify.run_suite
    gates: [verification_passed_gate]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/ -v`
Expected: PASS, the new file and the unchanged `tests/workflow/test_builtin_task.py`, `tests/workflow/test_registry.py`, `tests/workflow/test_loader.py`.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/workflow/builtin/integrate.yaml tests/workflow/test_builtin_integrate.py
git commit -m "feat(workflow): ship builtin integrate.yaml for the resolver"
```

---

### Task 3: Prove the document end to end against a real git conflict

**Files:**
- Test: `tests/test_integrate_workflow.py` (new; engine tier, sibling of `tests/test_engine.py`)
- Temporarily edit and restore (mutation check only, not committed): `src/agent_manager/workflow/builtin/integrate.yaml`

**Interfaces:**
- Consumes: Task 2's `load_builtin("integrate")`; Task 1's rows (the fake parses the `## conflict_files` section, rendered as `json.dumps(list, indent=2, ensure_ascii=False)`); existing `steps.integrate.merge_tip(repo_dir, worktree, integration_branch, base_branch, tip, git_runner=run_git) -> dict` (keys `created`, `conflict`, `files`, `merged`, `already_merged`, `detail`); `engine.run_subtask(workflow, store, *, story_id, subtask, repo_dir, commands, extra_context, agent_runner) -> SubtaskSummary` (`status`, `results`, `failed_phase`, `detail`); `dispatch.AgentRunner(workflow=, store=, launcher=, run_id=, story_id=, card_id=, adapters=, harness_map=)`; `harness.base.Outcome(argv, exit_code, timed_out, duration, stdout_path)`; `store.Store.open(root, run_id)`, `Store.record_story`, `Store.record_subtask`, `Store.connection`.
- Produces: nothing later tasks use.

These tests add no production code: they prove that Tasks 1 and 2 compose with the existing engine, dispatcher and gate. Step 2 therefore expects PASS, and Step 3 is the RED check: it breaks the document on purpose and watches the tests catch it. If Step 2 fails, that is a real defect in the composition. Engine and dispatcher changes are out of scope, so stop and report it instead of patching `engine.py` or `dispatch.py`.

- [ ] **Step 1: Write the tests**

Create `tests/test_integrate_workflow.py`:

```python
"""The Integrate resolver document, driven end to end (Integrate addendum I3,
card b4bd3795).

Engine tier per design §14. Everything is real except the harness: the shipped
`builtin/integrate.yaml` against the default registry, `engine.run_subtask`,
`dispatch.AgentRunner` as the injected agent runner (it owns the attempt
directories, the gates, the retry and the feedback, so a bare lambda would
prove none of them), the store and journal, and temporary git repositories
with a conflict left in progress by `steps.integrate.merge_tip`. Only the
adapter and the launcher are doubles. The launcher plays the resolver and, per
rule 1, learns the conflicting files only by parsing the brief it is handed.
It never asks git for that list, never computes a plan hash and never commits
documents.

Every scenario asserts that the base branch, both story branches and the bare
`origin` are unchanged (rule 4): Integrate never writes the base and never
pushes.
"""

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent_manager import dispatch, engine, models, prompt
from agent_manager import store as store_module
from agent_manager.errors import EngineError
from agent_manager.harness.base import Outcome
from agent_manager.steps.integrate import merge_tip
from agent_manager.workflow import load_builtin

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the git CLI must be installed for the integrate workflow's engine-tier tests",
)

RUN_ID = "run-2026-09-25-integrate"
STORY_ID = "integrate"
"""The synthetic story "Integrate" (addendum I3) every resolver attempt hangs from."""
TIP_CARD = "b0b0b0b0"
"""The synthetic subtask's card: the conflicting story's id (addendum I3)."""
BASE = "main"
INTEGRATION_BRANCH = "m5-integrate"
STORY_A = "m5/story-a"
STORY_B = "m5/story-b"
CONFLICTED = ["a.txt", "b.txt"]
BOTH_SIDES = "story a\nstory b\n"
PWD_SUITE = [[sys.executable, "-c", "import os; print(os.getcwd())"]]
"""A verification command whose output proves which directory it ran in."""
RED_SUITE = [[sys.executable, "-c", "import sys; sys.exit(3)"]]


# ── git helpers ──────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in `cwd`, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _merge_head(worktree: Path) -> str | None:
    """MERGE_HEAD's sha while a merge is in progress in `worktree`, else None."""
    completed = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _story_branch(repo: Path, scratch_root: Path, branch: str, body: str) -> None:
    """Cut `branch` from the base in a throwaway worktree and overwrite the
    same line of every file in `CONFLICTED`, so two such branches conflict."""
    scratch = scratch_root / f"scratch-{branch.replace('/', '-')}"
    _git(repo, "worktree", "add", str(scratch), "-b", branch, BASE)
    for name in CONFLICTED:
        (scratch / name).write_text(body, encoding="utf-8")
    _git(scratch, "add", *CONFLICTED)
    _git(scratch, "commit", "-m", f"work on {branch}")
    _git(repo, "worktree", "remove", str(scratch))


def _protected_state(repo: Path) -> dict[str, str]:
    """Everything Integrate must never change: the base, both story tips, the
    main checkout and every ref on the bare `origin`."""
    return {
        "base": _git(repo, "rev-parse", f"refs/heads/{BASE}").strip(),
        STORY_A: _git(repo, "rev-parse", f"refs/heads/{STORY_A}").strip(),
        STORY_B: _git(repo, "rev-parse", f"refs/heads/{STORY_B}").strip(),
        "checked_out": _git(repo, "symbolic-ref", "HEAD").strip(),
        "status": _git(repo, "status", "--porcelain"),
        "origin_refs": _git(repo, "ls-remote", "origin"),
    }


# ── the scene: a real conflict left in progress ──────────────────────────────


@dataclass
class Scene:
    repo: Path
    worktree: Path
    conflict_files: list[str]
    protected: dict[str, str]


@pytest.fixture
def scene(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Scene:
    """A repo on `main` with a bare `origin`, two story branches that edit the
    same line of `a.txt` and `b.txt`, story A merged into the integration
    branch, and story B's merge left in progress by `merge_tip`."""
    # Store, journal and attempt directories all live under data_dir().
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", BASE, str(repo)], capture_output=True, text=True, check=True
    )
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "agent-manager tests")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    for name in CONFLICTED:
        (repo / name).write_text("shared line\n", encoding="utf-8")
    _git(repo, "add", "README.md", *CONFLICTED)
    _git(repo, "commit", "-m", "base")

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repo), str(origin)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "fetch", "origin")

    _story_branch(repo, tmp_path, STORY_A, "story a\n")
    _story_branch(repo, tmp_path, STORY_B, "story b\n")
    protected = _protected_state(repo)

    worktree = tmp_path / "integrate-wt"
    first = merge_tip(repo, worktree, INTEGRATION_BRANCH, BASE, STORY_A)
    assert first["conflict"] is False
    assert first["merged"] == STORY_A
    second = merge_tip(repo, worktree, INTEGRATION_BRANCH, BASE, STORY_B)
    assert second["conflict"] is True
    assert second["files"] == CONFLICTED
    assert _merge_head(worktree) is not None

    return Scene(
        repo=repo,
        worktree=worktree,
        conflict_files=list(second["files"]),
        protected=protected,
    )


def _assert_nothing_protected_moved(scene: Scene) -> None:
    after = _protected_state(scene.repo)
    assert after == scene.protected
    assert INTEGRATION_BRANCH not in after["origin_refs"]


# ── the harness doubles ──────────────────────────────────────────────────────


class _FakeAdapter:
    """A `HarnessAdapter` by shape: its argv names the brief and the result path."""

    name = "fake"
    capabilities = frozenset({"bash", "edit"})

    def build_command(self, d: models.Dispatch) -> list[str]:
        return [
            "fake-resolver",
            "--prompt",
            str(d.prompt_path),
            "--result",
            str(d.result_path),
        ]

    def parse_usage(self, stdout: str) -> None:
        return None


_CONFLICT_HEADING = "\n## conflict_files\n"


def _conflict_files_from(brief: str) -> list[str]:
    """Rule 1: the conflict list, parsed out of the brief text and nowhere else."""
    start = brief.index(_CONFLICT_HEADING) + len(_CONFLICT_HEADING)
    files, _end = json.JSONDecoder().raw_decode(brief, start)
    return files


Act = Callable[[int, Path, list[str]], dict[str, Any] | str]
"""What the fake resolver does on attempt `n` in `worktree` for `files`; returns
the result it writes (a dict is dumped as JSON, a str is written raw)."""


@dataclass
class _FakeResolver:
    """A `LauncherFn` double that plays the resolver in the worktree it is run in."""

    act: Act
    prompts: list[str] = field(default_factory=list)
    seen_files: list[list[str]] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        brief = Path(argv[argv.index("--prompt") + 1]).read_text(encoding="utf-8")
        self.prompts.append(brief)
        files = _conflict_files_from(brief)
        self.seen_files.append(files)
        result = self.act(len(self.prompts), Path(cwd), files)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        text = result if isinstance(result, str) else json.dumps(result)
        Path(argv[argv.index("--result") + 1]).write_text(text, encoding="utf-8")
        return Outcome(
            argv=list(argv),
            exit_code=0,
            timed_out=False,
            duration=0.5,
            stdout_path=stdout_path,
        )


def _resolve_properly(attempt: int, worktree: Path, files: list[str]) -> dict[str, Any]:
    for name in files:
        (worktree / name).write_text(BOTH_SIDES, encoding="utf-8")
    _git(worktree, "add", *files)
    _git(worktree, "commit", "--no-edit")
    return {"resolved": True, "summary": f"kept both stories' line in {', '.join(files)}"}


def _commit_with_markers(attempt: int, worktree: Path, files: list[str]) -> dict[str, Any]:
    """Finishes the merge commit on attempt 1 with the markers still in the
    files, then on attempt 2 changes nothing and claims success again."""
    if attempt == 1:
        _git(worktree, "add", *files)
        _git(worktree, "commit", "--no-edit")
    return {"resolved": True, "summary": "committed the merge"}


def _claim_without_touching(attempt: int, worktree: Path, files: list[str]) -> dict[str, Any]:
    return {"resolved": True, "summary": "all conflicts resolved"}


def _write_an_invalid_result(attempt: int, worktree: Path, files: list[str]) -> str:
    # `ResolveResult` is strict: a string is not a bool.
    return json.dumps({"resolved": "yes", "summary": "done"})


# ── driving the document ─────────────────────────────────────────────────────


@dataclass
class _Ran:
    summary: engine.SubtaskSummary
    attempts: list[tuple[str, str]]


def _run(
    scene: Scene,
    resolver: _FakeResolver,
    *,
    commands: list[Any] = PWD_SUITE,
    extra_context: dict[str, Any] | None = None,
) -> _Ran:
    """`engine.run_subtask(load_builtin("integrate"), ...)` for one synthetic
    subtask, with the real `dispatch.AgentRunner` as the agent runner."""
    workflow = load_builtin("integrate")
    subtask = models.SubtaskRun(
        card_id=TIP_CARD,
        branch=INTEGRATION_BRANCH,
        base_branch=BASE,
        status="started",
        worktree_path=scene.worktree,
    )
    context = (
        {"merge_tip": STORY_B, "conflict_files": list(scene.conflict_files)}
        if extra_context is None
        else extra_context
    )
    adapter = _FakeAdapter()
    store = store_module.Store.open(scene.repo, RUN_ID)
    try:
        store.record_story(
            models.StoryRun(card_id=STORY_ID, title="Integrate", level=0, status="started")
        )
        store.record_subtask(STORY_ID, subtask)
        runner = dispatch.AgentRunner(
            workflow=workflow,
            store=store,
            launcher=resolver,
            run_id=RUN_ID,
            story_id=STORY_ID,
            card_id=TIP_CARD,
            adapters={adapter.name: adapter},
            harness_map={
                "resolver": models.HarnessAssignment(harness=adapter.name, model="fake-model")
            },
        )
        summary = engine.run_subtask(
            workflow,
            store,
            story_id=STORY_ID,
            subtask=subtask,
            repo_dir=scene.repo,
            commands=commands,
            extra_context=context,
            agent_runner=runner,
        )
        attempts = [
            tuple(row)
            for row in store.connection.execute(
                "SELECT phase, status FROM attempts ORDER BY phase, n"
            ).fetchall()
        ]
    finally:
        store.close()
    return _Ran(summary=summary, attempts=attempts)


# ── the three scenarios the spec names ───────────────────────────────────────


def test_a_resolver_that_finishes_the_merge_ends_done_and_verify_runs_in_the_worktree(
    scene: Scene,
) -> None:
    resolver = _FakeResolver(_resolve_properly)

    ran = _run(scene, resolver)

    assert ran.summary.status == "done", ran.summary.detail
    assert ran.summary.failed_phase is None
    assert ran.attempts == [("resolve", "ok")]
    assert resolver.seen_files == [CONFLICTED]
    brief = resolver.prompts[0]
    assert f"\n## merge_tip\n{STORY_B}\n" in brief
    assert f"\n## branch\n{INTEGRATION_BRANCH}\n" in brief
    assert f"\n## base_branch\n{BASE}\n" in brief
    assert prompt.FEEDBACK_HEADING not in brief
    assert ran.summary.results["resolve"]["resolved"] is True

    verified = ran.summary.results["verify"]
    assert verified["passed"] is True
    assert Path(verified["verified"][0]["tail"]).resolve() == scene.worktree.resolve()

    assert _merge_head(scene.worktree) is None
    parents = _git(scene.worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    assert len(parents) == 2  # a real merge commit
    for name in CONFLICTED:
        assert (scene.worktree / name).read_text(encoding="utf-8") == BOTH_SIDES
    _assert_nothing_protected_moved(scene)


def test_leftover_markers_are_retried_once_with_the_gates_feedback_then_escalate(
    scene: Scene,
) -> None:
    resolver = _FakeResolver(_commit_with_markers)

    ran = _run(scene, resolver)

    assert ran.summary.status == "escalated"
    assert ran.summary.failed_phase == "resolve"
    assert ran.attempts == [("resolve", "gate_failed"), ("resolve", "gate_failed")]
    assert "Conflict markers remain in: a.txt, b.txt" in ran.summary.detail

    first, second = resolver.prompts
    assert prompt.FEEDBACK_HEADING not in first
    assert prompt.FEEDBACK_HEADING in second
    feedback = second.split(prompt.FEEDBACK_HEADING, 1)[1]
    assert "merge_completed_gate" in feedback
    assert "Conflict markers remain in: a.txt, b.txt" in feedback
    assert resolver.seen_files == [CONFLICTED, CONFLICTED]

    assert "verify" not in ran.summary.results
    _assert_nothing_protected_moved(scene)


def test_a_resolved_flag_without_a_finished_merge_is_rejected_by_git(
    scene: Scene,
) -> None:
    """Rule 3: the flag is advisory. The resolver says `resolved: true` twice
    and git, which still has MERGE_HEAD, says no both times."""
    resolver = _FakeResolver(_claim_without_touching)

    ran = _run(scene, resolver)

    assert ran.summary.status == "escalated"
    assert ran.summary.failed_phase == "resolve"
    assert ran.attempts == [("resolve", "gate_failed"), ("resolve", "gate_failed")]
    assert "MERGE_HEAD exists" in ran.summary.detail
    assert _merge_head(scene.worktree) is not None  # left for a human, never aborted
    assert "verify" not in ran.summary.results
    _assert_nothing_protected_moved(scene)


# ── review focus ─────────────────────────────────────────────────────────────


def test_a_result_that_fails_the_schema_twice_escalates_at_resolve(scene: Scene) -> None:
    resolver = _FakeResolver(_write_an_invalid_result)

    ran = _run(scene, resolver)

    assert ran.summary.status == "escalated"
    assert ran.summary.failed_phase == "resolve"
    assert ran.attempts == [("resolve", "schema_invalid"), ("resolve", "schema_invalid")]
    assert prompt.FEEDBACK_HEADING in resolver.prompts[1]
    assert _merge_head(scene.worktree) is not None
    assert "verify" not in ran.summary.results
    _assert_nothing_protected_moved(scene)


def test_a_clean_resolve_followed_by_a_red_suite_escalates_at_verify(scene: Scene) -> None:
    resolver = _FakeResolver(_resolve_properly)

    ran = _run(scene, resolver, commands=RED_SUITE)

    assert ran.summary.status == "escalated"
    assert ran.summary.failed_phase == "verify"
    assert ran.attempts == [("resolve", "ok")]
    assert "verification failed" in ran.summary.detail
    assert "verification_passed_gate" in ran.summary.detail
    _assert_nothing_protected_moved(scene)


def test_an_extra_context_without_merge_tip_fails_before_any_dispatch(scene: Scene) -> None:
    resolver = _FakeResolver(_resolve_properly)

    with pytest.raises(EngineError) as caught:
        _run(scene, resolver, extra_context={"conflict_files": list(scene.conflict_files)})

    assert caught.value.phase == "resolve"
    assert caught.value.parameter == "merge_tip"
    assert resolver.prompts == []
    assert _merge_head(scene.worktree) is not None
    _assert_nothing_protected_moved(scene)
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_integrate_workflow.py -v`
Expected: PASS, all six tests. If any fails, read the failure. A broken fixture (for example a git identity problem) is yours to fix inside the test file. A failure that points at `engine.py`, `dispatch.py`, `steps/integrate.py` or the registry is out of scope: stop and report it.

- [ ] **Step 3: RED check, proving the tests bite**

Edit `src/agent_manager/workflow/builtin/integrate.yaml` temporarily. In the `resolve` phase, change the line `    gates: [merge_completed_gate]` to `    gates: []`.

Run: `uv run pytest tests/test_integrate_workflow.py -v -k "rejected_by_git or leftover_markers"`
Expected: FAIL, both tests. Without the gate, the lying resolver's `resolve` counts as `ok`, so the summary is not `escalated` at `resolve` and the attempts are not `gate_failed`.

Then restore the line to `    gates: [merge_completed_gate]` exactly.

Run: `git status --porcelain src/agent_manager/workflow/builtin/integrate.yaml`
Expected: no output (the file matches the Task 2 commit again).

- [ ] **Step 4: Run the tests again after the restore**

Run: `uv run pytest tests/test_integrate_workflow.py -v`
Expected: PASS, all six.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, with no failures and no errors. This is the default run, with `-m "not e2e"` from `pyproject.toml` addopts, and it includes whatever `tests/e2e` collects there (rule 2).

- [ ] **Step 6: Commit**

```bash
git add tests/test_integrate_workflow.py
git commit -m "test: drive integrate.yaml end to end against a real git conflict"
```
