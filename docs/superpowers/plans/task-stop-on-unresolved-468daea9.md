<!-- task-pipeline: validated -->
# Stop on unresolved review blockers (card 468daea9)

Subtask of "Close the task.js gaps" (be007353), milestone 84c3b532. Narrows Task 2.1 of `docs/superpowers/plans/2026-09-25-pygents-engine.md` (lines 427-476) under `docs/superpowers/specs/2026-09-25-pygents-engine-design.md`. This card lands first in its story. ee3cc742, c7bea6a2 and cf8b3888 wait on it.

## Scope

Today the reviewer's `unresolved_blockers` list (`ReviewResult.unresolved_blockers: list[str]`, `src/agent_manager/results.py:118`) is recorded but no gate reads it. A review can report open blockers and the task still goes on to `verify`. This card adds a pure gate that stops the `review` phase when the reviewer reports a blocker that is still open. The gate is wired into both the YAML and Python declarations of the `task` workflow.

In scope:

- `src/agent_manager/steps/reducers.py`: add `review_blockers_gate(result: object) -> dict[str, str] | None`.
- `src/agent_manager/workflow/registry.py`: add `registry.register("review_blockers_gate", reducers.review_blockers_gate)` to `default_registry()`, registering the bare function with no adapter because it takes only `result`. Add `"review_blockers_gate"` to `BUILTIN_FUNCTION_NAMES`, keeping the tuple sorted the way the test compares it.
- `src/agent_manager/workflow/builtin/task.yaml:77`: change `review`'s gates to `[review_blockers_gate, review_gate, plan_hash_gate]`.
- `src/agent_manager/workflow/task.py:102`: in this worktree the `TASK` constant already exists. Change `review`'s gates to `(reducers.review_blockers_gate, reducers.review_gate, reducers.plan_hash_gate_adapter)` so both engines and `test_declared.py` agree.
- Update the existing tests that pin the old gate list or the old registry contents (listed below).

Out of scope: reviewer role prompt and policy (ee3cc742), critic split and `validate_spec`/`validate_plan` (c7bea6a2), typecheck/lint in `verify.run_suite` (cf8b3888). Do not change `ReviewResult` itself, `SubtaskSummary`, journal lines, phase/attempt rows or the shape of the escalation payload (G10). No pygents import outside `runtime/`.

## Observable behavior

`review_blockers_gate(result)` is pure. It does no I/O and keeps no module state. Malformed input returns a verdict and never raises. It reads the field through the existing `_field(result, "unresolved_blockers")` helper, so key-lookup rules match the other gates.

| Input | Result |
|---|---|
| `result` is not a `Mapping`, e.g. `None` | `{"blocked": "review", "detail": "the review stage returned nothing"}` |
| Mapping whose `unresolved_blockers` is missing, `None` or `[]` | `None` (pass) |
| Mapping with N ≥ 1 blockers | `{"blocked": "review", "detail": f"review left {N} unresolved blocker(s): {'; '.join(map(str, blockers))}"}` |

The gate is listed first in `review`'s gates, so a review with open blockers escalates at `review` before `review_gate` and `plan_hash_gate` run. The engine's own detail then names `review_blockers_gate`. `verify` and `mark_done` never run. Reviews with an empty blocker list behave exactly as they do today.

## Tests

Tier placement follows `2026-09-23-agent-manager-design.md` §14.

Pure-function tier, in `tests/steps/test_reducers.py`, using the plan's exact assertions:
- `test_no_unresolved_blockers_passes`: `{"unresolved_blockers": []}` → `None`.
- `test_unresolved_blockers_block_review`: two blockers → the exact "review left 2 unresolved blocker(s): …; …" verdict.
- `test_a_dead_reviewer_blocks`: `None` → the "returned nothing" verdict.

Engine tier, in `tests/test_engine.py`, with a fake registry and fake agent runner and no real git or harness:
- New test: walk the shipped `task` workflow (`load_builtin("task", ...)`) using a `_builtin_functions` variant in which `review_blockers_gate` is the real reducer and the fake reviewer returns a `ReviewResult`-shaped payload with `unresolved_blockers=["x"]`. Assert `summary.status == "escalated"`, `summary.failed_phase == "review"`, and that no `verify` call appears in the recorded calls. The fake reviewer returns only what its brief allows (rule 4).
- Existing fixtures: add `"review_blockers_gate": agent_only_gate` to `_builtin_functions` (around line 1669) and to the similar dict near line 1199, so the existing walks still resolve every gate name.

Registry and workflow-declaration tests, under `tests/workflow/`, which pin declared structure:
- `tests/workflow/test_registry.py`: add `"review_blockers_gate"` to `TASK_YAML_NAMES`. Assert `registry.resolve("review_blockers_gate") is reducers.review_blockers_gate`.
- `tests/workflow/test_builtin_task.py:207`: expect `["review_blockers_gate", "review_gate", "plan_hash_gate"]`. Extend the `("review", "review_gate")` style table near line 488 if it lists every gate.
- `tests/workflow/test_phases.py:268`: also assert that `reducers.review_blockers_gate` is `review.gates[0]`.
- `tests/workflow/test_declared.py` must stay green. This proves the YAML and `TASK` agree.

Existing e2e (fake-claude) tier. This is not a new test, but the whole suite must stay green:
- `tests/e2e/fake_claude.py:596-607`: the review-fail branch already emits `unresolved_blockers=list(findings)`, so the new first gate now trips before `review_gate`. `tests/e2e/test_milestone_run.py:163` (`assert "review_gate" in stopped["detail"]`) will fail because `"review_blockers_gate"` does not contain that substring. Change the assertion to expect `review_blockers_gate`. Keep the `"review-fail marker"` detail assertion. Rewrite the now-stale comment in `fake_claude.py` that says "`unresolved_blockers` alone would fail nothing, because no gate reads it". Do not add new e2e tests.

The canned `"phase 'review' gate 'review_gate' failed"` payload in `tests/test_cli.py:2871` is a fixture, not a walk, so leave it unchanged.

## Verification

`uv run pytest` passes in full, including `tests/e2e` and both engines while `--engine` exists. Commit message: `feat(review): stop on unresolved review blockers`.

---

# Stop on Unresolved Review Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the `task` workflow at `review` when the reviewer reports any still-open blocker, so `verify` and `mark_done` never run on a review that says work is unfinished.

**Architecture:** A new pure gate `reducers.review_blockers_gate(result)` reads `unresolved_blockers` through the existing `_field` helper and returns `None` or a `{"blocked": "review", ...}` verdict. It is registered bare in `default_registry()` and listed first in `review`'s gates in both declarations of the workflow (`builtin/task.yaml` and `workflow/task.py`'s `TASK`). The agent-phase gate loop that already exists in `dispatch.evaluate_gates` turns the verdict into `gate_failed`, and the engine escalates. No engine or dispatch code changes.

**Tech Stack:** Python 3, pytest, `uv`. Pydantic `ReviewResult` (unchanged).

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-stop-on-unresolved-468daea9/docs/superpowers/specs/task-stop-on-unresolved-468daea9-design.md` (prepended verbatim above).

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-stop-on-unresolved-468daea9`. Run every command from that directory. The branch is `m6/task-stop-on-unresolved-468daea9`; do not assume any sibling card's (ee3cc742, c7bea6a2, cf8b3888) code exists.

## Global Constraints

- Gates are pure: no filesystem, network, model calls or module-level mutable state; malformed content returns a verdict, never raises; `None` to pass, a verdict `dict` to fail.
- `review_blockers_gate` reads the field with `_field(result, "unresolved_blockers")` (snake_case only, no `_either_field`).
- Verdict texts are exact: `"the review stage returned nothing"` and `f"review left {N} unresolved blocker(s): {'; '.join(map(str, blockers))}"`, both with `"blocked": "review"`.
- Registered as the bare function (no adapter): `registry.resolve("review_blockers_gate") is reducers.review_blockers_gate`.
- `review`'s gate order is exactly `[review_blockers_gate, review_gate, plan_hash_gate]` in `task.yaml` and `(reducers.review_blockers_gate, reducers.review_gate, reducers.plan_hash_gate_adapter)` in `TASK`.
- Do not change `ReviewResult`, `SubtaskSummary`, journal lines, phase/attempt rows, or the escalation payload shape (G10).
- Only `src/agent_manager/runtime/` may import pygents; nothing here imports it.
- A fake `claude` in a test never knows more than its brief tells it (rule 4).
- Do not touch reviewer/critic role bundles or `steps/verify.run_suite`.
- Final commit message: `feat(review): stop on unresolved review blockers`.
- The whole default suite (`uv run pytest`, including `tests/e2e`) stays green after every task.

## Review Focus

1. A non-mapping result other than `None` (a list, a string, an int, `True`) must return the "the review stage returned nothing" verdict, never raise. Pinned by a parametrized test in Task 1.
2. A mapping whose `unresolved_blockers` key is absent, or is `None`, must pass (`None`), so a clean hand-written or partial result does not block. Pinned in Task 1.
3. Blocker items that are not strings (an int, a dict) must render through `str` and still produce the verdict rather than raising. Pinned in Task 1.
4. Ordering: a review that is both dirty (non-empty `porcelain`) and has blockers must stop on `review_blockers_gate`, and `review_gate`/`plan_hash_gate` must not run. Pinned in Task 3 by the engine test (those two gates are fakes that raise if called, and the detail must name `review_blockers_gate`) and by the updated e2e assertion.
5. A clean review (`unresolved_blockers == []`) on the real shipped document must still pass every `review` gate. Pinned in Task 3 by adding `("review", "review_blockers_gate")` to `GATED_PHASES`, which feeds the existing `test_every_gate_passes_on_a_healthy_run` and `test_every_gate_binds_every_parameter_against_real_results`.

---

### Task 1: The pure `review_blockers_gate` reducer

**Files:**
- Modify: `src/agent_manager/steps/reducers.py` (insert after `critic_blockers_gate`, which ends at line 405, before `_plan_hash_of` at line 408)
- Test: `tests/steps/test_reducers.py` (import block lines 12-26; append at end of file after line 826)

**Interfaces:**
- Consumes: `reducers._field(mapping: object, name: str) -> object` (existing, line 83); `Mapping` from `collections.abc` (already imported, line 20).
- Produces: `reducers.review_blockers_gate(result: object) -> dict[str, str] | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/steps/test_reducers.py`, add `review_blockers_gate,` to the import block so it reads:

```python
from agent_manager.steps.reducers import (
    _is_integer,
    _js_text,
    count_of,
    critic_blockers_gate,
    exploration_output_gate,
    implement_blocked_gate,
    is_plan_hash,
    plan_hash_gate,
    plan_hash_gate_adapter,
    plan_hash_mismatch,
    review_blockers_gate,
    review_gate,
    verification_gate,
    verification_passed_gate,
)
```

Then append at the end of the file:

```python


# ── review_blockers_gate ─────────────────────────────────────────────────────
# task.js:842-855: only what the reviewer says is STILL standing gates done.


def test_no_unresolved_blockers_passes():
    assert review_blockers_gate({"unresolved_blockers": []}) is None


def test_unresolved_blockers_block_review():
    verdict = review_blockers_gate(
        {"unresolved_blockers": ["tests assert the mock", "no error path"]}
    )
    assert verdict == {
        "blocked": "review",
        "detail": "review left 2 unresolved blocker(s): tests assert the mock; no error path",
    }


def test_a_dead_reviewer_blocks():
    assert review_blockers_gate(None) == {
        "blocked": "review",
        "detail": "the review stage returned nothing",
    }


@pytest.mark.parametrize("dead", [["x"], "x", 7, True])
def test_any_non_mapping_review_result_is_the_dead_reviewer_verdict(dead):
    assert review_blockers_gate(dead) == {
        "blocked": "review",
        "detail": "the review stage returned nothing",
    }


@pytest.mark.parametrize("clean", [{}, {"unresolved_blockers": None}, {"findings": ["x"]}])
def test_a_review_with_no_blockers_key_or_a_null_one_passes(clean):
    # Findings the reviewer already fixed are not blockers: only
    # `unresolved_blockers` gates.
    assert review_blockers_gate(clean) is None


def test_a_single_blocker_is_counted_and_named():
    assert review_blockers_gate({"unresolved_blockers": ["x"]}) == {
        "blocked": "review",
        "detail": "review left 1 unresolved blocker(s): x",
    }


def test_non_string_blockers_are_rendered_not_raised_on():
    verdict = review_blockers_gate({"unresolved_blockers": [1, {"a": 1}]})
    assert verdict == {
        "blocked": "review",
        "detail": "review left 2 unresolved blocker(s): 1; {'a': 1}",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'review_blockers_gate' from 'agent_manager.steps.reducers'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/steps/reducers.py`, insert between the end of `critic_blockers_gate` (line 405) and `def _plan_hash_of` (line 408):

```python


# The `review` phase's first gate (`builtin/task.yaml`), ported from task.js
# lines 842-855. The reviewer fixes what it can and REPORTS what is still
# standing in `unresolved_blockers`; before this gate nothing read that list,
# so a review that said "this is not done" still went on to `verify` and
# `done`. Only what is STILL standing gates: `findings` the reviewer already
# fixed do not. Listed before `review_gate` so the escalation names the
# reviewer's own judgement rather than a symptom of it.
def review_blockers_gate(result: object) -> dict[str, str] | None:
    """``None`` when the reviewer left nothing open, else a blocked verdict.

    A dead reviewer -- ``None``, or anything that is not a ``Mapping`` -- is
    itself a block, for the reason ``critic_blockers_gate`` gives: silence is
    not a clean review.
    """
    if not isinstance(result, Mapping):
        return {"blocked": "review", "detail": "the review stage returned nothing"}
    blockers = list(_field(result, "unresolved_blockers") or [])
    if not blockers:
        return None
    return {
        "blocked": "review",
        "detail": (
            f"review left {len(blockers)} unresolved blocker(s): "
            f"{'; '.join(map(str, blockers))}"
        ),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_reducers.py -v`
Expected: PASS, including the seven new test functions (twelve test ids once parametrization expands).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/steps/reducers.py tests/steps/test_reducers.py
git commit -m "feat(review): add the review_blockers_gate reducer"
```

---

### Task 2: Register `review_blockers_gate` in the default registry

**Files:**
- Modify: `src/agent_manager/workflow/registry.py:183-199` (`BUILTIN_FUNCTION_NAMES`), `:206-246` (`default_registry()` docstring and body)
- Test: `tests/workflow/test_registry.py:93-108` (`TASK_YAML_NAMES`), `:124-136` (resolution test)
- Modify (fixtures that must keep resolving every builtin name): `tests/test_engine.py:1187-1203` and `tests/test_engine.py:1657-1672`

**Interfaces:**
- Consumes: `reducers.review_blockers_gate` from Task 1.
- Produces: `default_registry().resolve("review_blockers_gate") is reducers.review_blockers_gate`; `"review_blockers_gate"` in `BUILTIN_FUNCTION_NAMES`, sorted between `"plan_hash_gate"` and `"review_gate"`.

- [ ] **Step 1: Write the failing tests**

In `tests/workflow/test_registry.py`, replace `TASK_YAML_NAMES` (lines 93-108) with:

```python
TASK_YAML_NAMES = (
    "critic_blockers_gate",
    "docs_commit.commit_documents",
    "exploration_output_gate",
    "implement_blocked_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_check.mark_validated",
    "plan_hash_gate",
    "review_blockers_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)
```

and in `test_default_registry_resolves_the_ported_reducers_to_the_real_callables`, directly after the line `assert registry.resolve("review_gate") is reducers.review_gate` (line 128), add:

```python
    # Takes only `result`, so it needs no adapter: the bare reducer.
    assert registry.resolve("review_blockers_gate") is reducers.review_blockers_gate
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/workflow/test_registry.py -v`
Expected: FAIL in `test_default_registry_holds_exactly_the_names_the_builtin_documents_use` (names tuple lacks `review_blockers_gate`) and in `test_default_registry_resolves_the_ported_reducers_to_the_real_callables` (`UnknownFunctionError: no function named 'review_blockers_gate' is registered ...`).

- [ ] **Step 3: Register the gate**

In `src/agent_manager/workflow/registry.py`, replace `BUILTIN_FUNCTION_NAMES` (lines 183-199) with:

```python
BUILTIN_FUNCTION_NAMES = (
    "critic_blockers_gate",
    "docs_commit.commit_documents",
    "exploration_output_gate",
    "implement_blocked_gate",
    "merge_completed_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_check.mark_validated",
    "plan_hash_gate",
    "review_blockers_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)
```

In the `default_registry()` docstring, change `The seven reducers and the five implemented steps` to `The eight reducers and the five implemented steps`.

In the body, directly after `registry.register("review_gate", reducers.review_gate)` (line 228), add:

```python
    # task.js:842-855: what the reviewer says is still standing stops `review`.
    registry.register("review_blockers_gate", reducers.review_blockers_gate)
```

- [ ] **Step 4: Keep the engine fixtures resolving every builtin name**

`tests/test_engine.py:1204` asserts `sorted(functions) == sorted(BUILTIN_FUNCTION_NAMES)`, so it now fails until its dict gains the name. In `tests/test_engine.py`, in `test_the_builtin_task_document_walks_against_a_fake_registry`, change the dict entry block (lines 1199-1200):

```python
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
```

to:

```python
        "review_blockers_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
```

In `_builtin_functions` (lines 1669-1670), make the same change to its return dict:

```python
        "review_blockers_gate": agent_only_gate,
        "review_gate": agent_only_gate,
        "plan_hash_gate": agent_only_gate,
```

(The engine never calls an agent phase's gates itself; the fake agent runners here return canned results without running gates, so `agent_only_gate` is never invoked on these walks.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/workflow/test_registry.py tests/test_engine.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/workflow/registry.py tests/workflow/test_registry.py tests/test_engine.py
git commit -m "feat(review): register review_blockers_gate"
```

---

### Task 3: List `review_blockers_gate` first on `review` in both declarations

**Files:**
- Modify: `src/agent_manager/workflow/builtin/task.yaml:77`
- Modify: `src/agent_manager/workflow/task.py:102`
- Test (new engine-tier walk): `tests/test_engine.py` (insert after `test_a_blocked_coder_escalates_the_subtask_at_implement_and_review_never_runs`, which ends at line 2212, before `test_a_subtask_summary_may_report_stopped` at line 2215)
- Test (declared structure): `tests/workflow/test_builtin_task.py:204-211` and `:479-491`; `tests/workflow/test_phases.py:262-274`
- Test (existing e2e fake-claude tier, update only): `tests/e2e/test_milestone_run.py:163`; comment in `tests/e2e/fake_claude.py:596-598`

**Interfaces:**
- Consumes: `reducers.review_blockers_gate` (Task 1); registry name `"review_blockers_gate"` (Task 2); existing test helpers in `tests/test_engine.py`: `_builtin_functions(calls, *, validated)` (line 1619), `_registry(functions)` (line 238), `_FakeAdapter` (line 2110), `_CannedLauncher` (line 2123), `_subtask()` (line 28), `RUN_ID`, `STORY_ID`, `REPO`, `CARD`, `PARENT`, the `store` fixture; `dispatch.AgentRunner` (same keyword arguments as the blocked-coder test at line 2169).
- Produces: `load_builtin("task").phase("review").gates == ["review_blockers_gate", "review_gate", "plan_hash_gate"]`; `TASK.phase("review").gates == (reducers.review_blockers_gate, reducers.review_gate, reducers.plan_hash_gate_adapter)`.

- [ ] **Step 1: Write the failing engine-tier test**

In `tests/test_engine.py`, insert after line 2212 (the end of `test_a_blocked_coder_escalates_the_subtask_at_implement_and_review_never_runs`):

```python


# A `ReviewResult` whose reviewer left one blocker standing. Clean in every
# other respect -- empty porcelain, one tagged commit, a well-formed hash -- so
# the only thing that can stop `review` is the blocker.
BLOCKED_REVIEW = json.dumps(
    {
        "findings": ["x"],
        "unresolved_blockers": ["x"],
        "fix_summary": "one finding is still open",
        "porcelain": "",
        "commit_count": 1,
        "tagged_count": 1,
        "plan_hash": "a1b2c3d4",
    }
)


def test_a_reviewer_reporting_a_blocker_escalates_at_review_and_verify_never_runs(store):
    calls: list[str] = []
    functions = _builtin_functions(calls, validated=True)
    # The real gate under test. `review_gate` and `plan_hash_gate` stay
    # `agent_only_gate`, which raises if called: listed first, the blockers
    # gate must stop the phase before either of them runs.
    functions["review_blockers_gate"] = reducers.review_blockers_gate
    workflow = load_builtin("task", _registry(functions))

    launcher = _CannedLauncher({"reviewer": BLOCKED_REVIEW})
    adapter = _FakeAdapter()
    subtask = _subtask()
    dispatching = dispatch.AgentRunner(
        workflow=workflow,
        store=store,
        launcher=launcher,
        run_id=RUN_ID,
        story_id=STORY_ID,
        card_id=subtask.card_id,
        adapters={adapter.name: adapter},
        harness_map={
            "reviewer": models.HarnessAssignment(harness=adapter.name, model="fake-model"),
        },
    )

    def agent_runner(phase, context, rendered):
        if phase.name == "review":
            return dispatching(phase, context, rendered)
        calls.append(f"agent:{phase.name}")
        return {"role": phase.role}

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=subtask,
        repo_dir=REPO,
        commands=["uv run pytest"],
        card=CARD,
        parent_story=PARENT,
        agent_runner=agent_runner,
    )

    assert summary.status == "escalated"
    assert summary.failed_phase == "review"
    assert "review_blockers_gate" in summary.detail
    assert "blocked=review" in summary.detail
    assert "review left 1 unresolved blocker(s): x" in summary.detail
    assert launcher.roles == ["reviewer"]
    attempts = store.connection.execute(
        "SELECT phase, status FROM attempts ORDER BY phase, n"
    ).fetchall()
    assert [tuple(row) for row in attempts] == [("review", "gate_failed")]
    assert "agent:implement" in calls
    assert "verify.run_suite" not in calls
    assert "rollup.set_status:done" not in calls
```

- [ ] **Step 2: Update the declared-structure tests to the new gate order**

In `tests/workflow/test_builtin_task.py`, rename and update `test_review_carries_both_of_its_gates` (lines 204-211) to:

```python
def test_review_carries_its_three_gates_blockers_first() -> None:
    phase = load_builtin("task").phase("review")
    assert isinstance(phase, AgentPhase)
    assert phase.gates == ["review_blockers_gate", "review_gate", "plan_hash_gate"]
    assert phase.inputs == ["branch", "base_branch", "plan_path"]
    # The reviewer recomputes the hash from the plan file; card f26b377d gives
    # the input to the coder only.
    assert "plan_hash" not in phase.inputs
```

In the same file, in `test_the_document_still_names_exactly_the_gates_this_suite_covers` (lines 482-491), replace the expected list with:

```python
    assert GATED_PHASES == [
        ("explore", "exploration_output_gate"),
        ("explore", "verification_gate"),
        ("validate_spec", "critic_blockers_gate"),
        ("validate_plan", "critic_blockers_gate"),
        ("implement", "implement_blocked_gate"),
        ("review", "review_blockers_gate"),
        ("review", "review_gate"),
        ("review", "plan_hash_gate"),
        ("verify", "verification_passed_gate"),
    ]
```

(`_phase_results()` at line 414 already builds the review result with `unresolved_blockers=[]`, so the parametrized `test_every_gate_binds_every_parameter_against_real_results` and `test_every_gate_passes_on_a_healthy_run` cover the new gate with no further change.)

In `tests/workflow/test_phases.py`, in `test_from_loader_resolves_every_name_of_the_shipped_task`, directly after `assert review.result is results.ReviewResult` (line 267), add:

```python
    assert review.gates[0] is reducers.review_blockers_gate
```

- [ ] **Step 3: Update the existing e2e assertion that pins which review gate fires**

The fake-claude review-fail branch (`tests/e2e/fake_claude.py:595-607`) already reports `unresolved_blockers=list(findings)`, so once the new gate is first the escalation names it. In `tests/e2e/test_milestone_run.py`, change line 163 from:

```python
    assert "review_gate" in stopped["detail"]
```

to:

```python
    assert "review_blockers_gate" in stopped["detail"]
```

Leave line 164 (`assert "review-fail marker" in stopped["detail"]`) as it is: the blocker text is `the review-fail marker names <branch>`, so the detail still carries it.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine.py::test_a_reviewer_reporting_a_blocker_escalates_at_review_and_verify_never_runs tests/workflow/test_builtin_task.py tests/workflow/test_phases.py tests/e2e/test_milestone_run.py::test_a_review_failure_stops_the_milestone_and_a_relaunch_finishes_it -v`
Expected: FAIL.
- The engine test fails on `assert "review_blockers_gate" in summary.detail`: the detail instead reads `phase 'review' gate 'review_gate' raised AssertionError: an agent phase's gate is the agent runner's business; ...` (the old first gate is the raising fake).
- `test_review_carries_its_three_gates_blockers_first` and `test_the_document_still_names_exactly_the_gates_this_suite_covers` fail on the list comparison.
- `test_from_loader_resolves_every_name_of_the_shipped_task` fails on `review.gates[0]`.
- The e2e test fails on `assert "review_blockers_gate" in stopped["detail"]` (detail names `review_gate`).

- [ ] **Step 5: Change the YAML declaration**

In `src/agent_manager/workflow/builtin/task.yaml`, change line 77 from:

```yaml
    gates: [review_gate, plan_hash_gate]
```

to:

```yaml
    gates: [review_blockers_gate, review_gate, plan_hash_gate]
```

- [ ] **Step 6: Make the same change in `TASK`**

`tests/workflow/test_declared.py::test_task_equals_the_shipped_yaml` compares `TASK.digest()` with the shipped YAML's, so this must land with Step 5. In `src/agent_manager/workflow/task.py`, change line 102 from:

```python
        gates=(reducers.review_gate, reducers.plan_hash_gate_adapter),
```

to:

```python
        gates=(
            reducers.review_blockers_gate,
            reducers.review_gate,
            reducers.plan_hash_gate_adapter,
        ),
```

- [ ] **Step 7: Rewrite the stale fake-claude comment**

In `tests/e2e/fake_claude.py`, replace the comment at lines 596-598:

```python
            # A review the production `review_gate` blocks: a non-empty
            # `porcelain`. `unresolved_blockers` alone would fail nothing,
            # because no gate reads it.
```

with:

```python
            # A review the production gates block. `review_blockers_gate`,
            # listed first on `review`, stops on the non-empty
            # `unresolved_blockers`; the non-empty `porcelain` is what
            # `review_gate` would block on if it ran.
```

(Comment only; the payload the fake returns is unchanged, and it still computes nothing it could not read from its brief.)

- [ ] **Step 8: Run the targeted tests to verify they pass**

Run: `uv run pytest tests/test_engine.py tests/workflow tests/e2e/test_milestone_run.py -v`
Expected: PASS, including `tests/workflow/test_declared.py::test_task_equals_the_shipped_yaml` and `test_task_callables_are_the_registry_bindings`.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no failures or errors (the default suite includes `tests/e2e`; the opt-in real-harness tests stay skipped as before).

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/workflow/builtin/task.yaml src/agent_manager/workflow/task.py tests/test_engine.py tests/workflow/test_builtin_task.py tests/workflow/test_phases.py tests/e2e/test_milestone_run.py tests/e2e/fake_claude.py
git commit -m "feat(review): stop on unresolved review blockers"
```
