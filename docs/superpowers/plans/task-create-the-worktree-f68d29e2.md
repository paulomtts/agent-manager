<!-- task-pipeline: validated -->
# Spec (verbatim)

# f68d29e2 — Create the worktree before any agent phase runs

Parent story: 360cd141 "Close the seams the wiring test found" (milestone 7aa00a90). Amends real-harness addendum item R6.

## Problem

`src/agent_manager/workflow/builtin/task.yaml` currently opens with `explore` (`kind: agent`), then `mark_in_progress`, then `worktree`. Every agent phase is dispatched with the subtask's worktree as its cwd (`AgentRunner._worktree`, `src/agent_manager/dispatch.py:500-509`), but on the first run of a card that directory does not exist yet when `explore` is dispatched: the `worktree` key is present in the context from the very first phase (`engine.subtask_context`, `src/agent_manager/engine.py:89-98`, binds `subtask.worktree_path` unconditionally, and `_bind_result` refuses to overwrite it because `worktree` is a reserved key), so `_worktree` raises nothing and the harness is pointed at a path git has not created. The document must run `worktree.ensure` first.

## Scope

One document reorder plus the tests that pin the order.

### (a) Reorder `src/agent_manager/workflow/builtin/task.yaml`

New phase order, phase bodies unchanged:

`worktree`, `explore`, `mark_in_progress`, `plan_check`, `spec`, `validate_spec`, `plan`, `validate_plan`, `implement`, `review`, `verify`, `mark_done`.

Invariants that must survive the move:

- `worktree` stays `kind: deterministic`, `run: worktree.ensure`, no `args`, no `when`, no `gates`. `src/agent_manager/steps/worktree.py` is not touched: `ensure(branch, base, worktree, repo_dir)` binds by parameter name against keys `subtask_context` already supplies before any phase runs, and it is already idempotent (`worktree_existed` short-circuits the `worktree add`), which is what makes it safe as the first phase of a resumed run.
- `plan_check` keeps `skip_to: implement` and `when: plan_check.has_validated_plan`. `skip_to` is forward-only and validated at load time (`workflow/loader.py:251 _check_skip_to`); `implement` still follows `plan_check`, so the document still loads.
- Still twelve phases, same set of function names, so `default_registry()` resolution and `BUILTIN_FUNCTION_NAMES` are unaffected.
- Per design spec D2 the document stays declarative: no new `when`, no expression, no new registered function.

### (b) Update the tests that pin the old order

- `tests/workflow/test_builtin_task.py`: `EXPECTED_PHASES` (lines ~19-33) to the new order; the stale comment citing "design spec lines 146-225" and the test name `test_builtin_task_has_the_twelve_phases_in_spec_order` stay accurate only if the design-doc copy is updated too (see (d)).
- `tests/test_cli.py` `interrupted_phase` tests (~lines 453-515): reorder the `_recorded(...)` lists to the new order. The expectations do not change — `implement` is still the interrupted phase in the `started` case, `plan_check` is still the answer for the crash-between-phases and skipped-stretch cases (in the crash case the recorded prefix becomes `worktree`, `explore`, `mark_in_progress`). `AGENT_PHASE_NAMES` (~line 425) is a set, not a sequence: no change.
- `tests/test_cli.py:~2700`: `done=("explore", "mark_in_progress", "worktree", "plan_check")` becomes `("worktree", "explore", "mark_in_progress", "plan_check")`. The assertion that `"explore"` appears in the `EngineError` is unchanged: restarting at `plan_check` with no validated plan still drops into `spec`, whose `explore` input nothing supplies.
- `tests/test_engine.py:~1121` `test_the_builtin_task_document_walks_against_a_fake_registry`: the `calls` list becomes `worktree.ensure`, `agent:explore`, `rollup.set_status:in_progress`, `plan_check.find_validated_plan`, … and the projected-phase list becomes `worktree`, `mark_in_progress`, `plan_check`, `verify`, `mark_done`.

### (c) New regression test: no agent phase precedes the worktree

Load the shipped document with `load_builtin("task")` and assert that the index of the phase named `worktree` is smaller than the index of every phase that `isinstance(..., AgentPhase)`. Written against the loaded document, not against the YAML text, so a future reorder of the file fails it.

### (d) Keep the design-doc copy in sync

`workflow/loader.py:38` states `task.yaml` ships byte-for-byte from the design spec, so the YAML block in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (lines 146-225) is reordered identically. Line numbers cited in comments/docstrings that refer to that block are adjusted only where they would otherwise be wrong.

## Observable behaviour

- A fresh `run --card` creates the worktree before the explorer is dispatched, so the explorer's cwd exists.
- A resumed run whose `interrupted_phase` is anything at or after `explore` does not re-run `worktree.ensure`; a resume that restarts at `worktree` re-runs it and it reports `worktree_existed: True, created: False`.
- `rollup.set_status: in_progress` now happens after `explore` rather than before it. This is accepted: it stays `best_effort: true`, and no consumer of the board status depends on it preceding the first agent phase.
- Nothing about the resume contract changes: `resume_start_phase` (`src/agent_manager/cli.py:~510`) still records the single edge `spec` needs `explore`'s output.

## Error paths

- Load-time: an ordering that put `plan_check` after `implement` would be rejected by `_check_skip_to` as a backwards jump — the new order does not.
- `worktree.ensure` raising `GitError` on the first phase fails the subtask with `failed_phase == "worktree"` and no agent ever dispatched; it is not `best_effort`, so the walk stops. Unchanged behaviour, earlier in the run.
- `AgentRunner._worktree`'s `EngineError` remains the backstop for a context with no `worktree` key; this card does not change it and does not add a test for it.

## Out of scope (sibling ownership)

Do not add `SpecResult` to `results.py`, do not put `result: SpecResult` on the `spec` phase, do not touch `dispatch` classification or `result_path` (be376902). Do not create `steps/rollup.py` or replace the `rollup.set_status` placeholder in `workflow/registry.py` (43008688). No milestone orchestration, parallel stories, `integrate`, non-Claude harnesses, or addendum section 4 deferrals.

## Test list

Tiers per design spec section 14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`); loading the packaged YAML and resolving it against a registry without executing anything is the pure-functions tier, per the `tests/workflow/test_builtin_task.py` module docstring. Tests mirror source layout (`CLAUDE.md`). Verification is `uv run pytest` only.

1. `tests/workflow/test_builtin_task.py` — updated `EXPECTED_PHASES` order assertion. **Pure-functions tier.**
2. `tests/workflow/test_builtin_task.py` — NEW: `worktree` precedes every `AgentPhase` in the loaded document. **Pure-functions tier** (mirrors `src/agent_manager/workflow/`; not engine or e2e, since nothing is executed).
3. `tests/workflow/test_builtin_task.py` — existing `plan_check` `skip_to`/`when` test, unchanged, re-confirming the forward jump under the new order. **Pure-functions tier.**
4. `tests/test_cli.py` — the three `interrupted_phase` tests with reordered recorded-phase lists and unchanged expectations. **Pure-functions tier** (they load the packaged document and build `PhaseRun` values by hand; no store, clock or subprocess) — they stay in their existing file and tier.
5. `tests/test_cli.py` — `test_a_restart_at_plan_check_that_finds_no_plan_is_an_engine_error_not_a_traceback` with the reordered `done=` tuple. **Engine tier** (real store, injected runner factory, `requires_git`/`requires_brd`) — unchanged tier.
6. `tests/test_engine.py` — `test_the_builtin_task_document_walks_against_a_fake_registry` with the reordered `calls` and projected-phase lists. **Engine tier** (fake adapter/registry, canned results).

No new fixtures, no network, no real harness test.

---

# Create the Worktree Before Any Agent Phase Runs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `worktree.ensure` the first phase of `builtin/task.yaml` so the subtask's worktree exists before any agent is dispatched into it, and re-pin every test that encodes the old phase order.

**Architecture:** This is a declarative-document change: the twelve phase bodies in `src/agent_manager/workflow/builtin/task.yaml` are unchanged, only their order moves to `worktree, explore, mark_in_progress, plan_check, spec, validate_spec, plan, validate_plan, implement, review, verify, mark_done`. No Python source file changes at all. Three test files pin the order and are corrected; three new tests are added first, as the RED driver — a loaded-document guard (no `AgentPhase` precedes `worktree`), a resume test proving `interrupted_phase` reads `worktree` as the first phase, and an engine-walk test proving a resume started at `explore` never re-runs `worktree.ensure`. Finally the byte-for-byte YAML copy in the design spec is resynced.

**Tech Stack:** Python 3, `uv`, pytest, PyYAML (via `agent_manager.workflow.loader`), Pydantic models in `agent_manager.models` / `agent_manager.results`.

**Spec:** `docs/superpowers/specs/task-create-the-worktree-f68d29e2-design.md` (reproduced verbatim at the top of this file)

## Global Constraints

- Branch `m2/task-create-the-worktree-f68d29e2`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-create-the-worktree-f68d29e2`, cut fresh from `origin/m2/task-complete-run-s-cli-19efcddc`. Assume no other subtask's code exists on this branch.
- Verification is exactly `uv run pytest`. There is no lint or typecheck command in this repo (`CLAUDE.md`).
- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (`CLAUDE.md`).
- Design spec D2: the workflow document stays declarative — every `when` / `gate` / `run` value is the name of a registered Python function, no expression language, no new registered function.
- Out of scope, owned by siblings: `SpecResult` in `results.py` and `result: SpecResult` on the `spec` phase (be376902); `steps/rollup.py` and the `rollup.set_status` placeholder in `workflow/registry.py` (43008688); `dispatch` classification and `result_path`. Do not touch `src/agent_manager/steps/worktree.py`.
- The twelve phase bodies are copied unchanged; the only edit to `task.yaml` is the position of the `worktree`, `explore` and `mark_in_progress` blocks.

## Review Focus

- **A resume that restarts at `worktree` re-runs `worktree.ensure` against a directory that already exists.** `worktree.ensure` must report `worktree_existed: True, created: False` rather than failing the first phase of every resumed run. Already pinned by the Steps tier at `tests/steps/test_worktree.py:547-566` (`fresh` / `resumed` / `again`); no new test needed, but the reorder is only safe because that idempotence holds.
- **A resume that restarts at `explore` must not re-run `worktree.ensure`.** Under the old order `worktree` came after `explore`, so a walk started at `explore` still created it; under the new order the step is behind the start phase and must simply not be called. Covered by the new engine test in Task 1, Step 3.
- **A crash that lost everything after the very first phase must restart at `explore`, not at `worktree`.** `interrupted_phase` answers with the first phase not recorded `done`, and the identity of "the first phase" changes with this card. Covered by the new CLI test in Task 1, Step 2.
- **A future reorder that puts an agent phase back in front of `worktree` must fail the suite.** Nothing today asserts the relationship as a property; `EXPECTED_PHASES` is a literal list that a reorderer would simply rewrite. Covered by the new loaded-document guard in Task 1, Step 1, including a non-vacuity assertion so a loader change that stopped yielding `AgentPhase` instances cannot make it pass emptily.
- **`plan_check`'s `skip_to: implement` must stay a forward jump under the new order.** A backwards `skip_to` is a load-time rejection in `workflow/loader.py:251 _check_skip_to`, which would break every document load, not just one test. Covered by the existing `test_plan_check_skips_forward_to_implement_when_a_plan_exists` plus `test_builtin_task_loads_against_the_default_registry`, both of which re-run against the reordered document in Task 1, Step 12.

---

### Task 1: Reorder the document and re-pin every order-sensitive test

**Files:**
- Modify: `src/agent_manager/workflow/builtin/task.yaml` (whole file, phase order only)
- Test: `tests/workflow/test_builtin_task.py:41-55` (`EXPECTED_PHASES`) and a new test after line 67
- Test: `tests/test_cli.py:453-502` (three `_recorded(...)` lists), `tests/test_cli.py:2700` (`done=` tuple), plus a new test after line 502
- Test: `tests/test_engine.py:1183-1206` (`calls` and projected-phase lists) and a new test after the `_walk_builtin` helper (line 1631)

**Interfaces:**
- Consumes: `agent_manager.workflow.load_builtin(name, registry=None)` returning a workflow whose `.phases` is a sequence of `AgentPhase` / `DeterministicPhase` and whose `.phase_names` is the tuple of names in document order; `agent_manager.workflow.loader.AgentPhase`; `cli.interrupted_phase(subtask, workflow)`; `engine.run_subtask(workflow, store, *, story_id, subtask, repo_dir, commands=..., card=..., parent_story=..., agent_runner=..., start_phase=...)`.
- Produces: `builtin/task.yaml` with `worktree` as phase index 0. Nothing else in the package changes, so no later task consumes a new symbol.

- [ ] **Step 1: Write the failing loaded-document guard (pure-functions tier)**

In `tests/workflow/test_builtin_task.py`, insert this immediately after `test_builtin_task_has_the_twelve_phases_in_spec_order` (after line 67) and before `test_plan_check_skips_forward_to_implement_when_a_plan_exists`:

```python
def test_no_agent_phase_precedes_the_worktree_phase() -> None:
    """R6: every agent phase is dispatched with the subtask's worktree as its
    cwd (`dispatch.AgentRunner._worktree`), so `worktree.ensure` has to have run
    before the first of them. Asserted against the loaded document rather than
    the YAML text, so any future reorder of the file fails right here."""
    phases = load_builtin("task").phases
    names = [phase.name for phase in phases]
    agent_indexes = [
        index for index, phase in enumerate(phases) if isinstance(phase, AgentPhase)
    ]

    # Non-vacuity: a loader change that stopped yielding `AgentPhase` instances,
    # or a rename of the worktree phase, must fail here and not pass emptily.
    assert "worktree" in names
    assert len(agent_indexes) == 7

    assert names.index("worktree") < min(agent_indexes)
```

- [ ] **Step 2: Write the failing first-phase resume test (pure-functions tier)**

In `tests/test_cli.py`, insert this immediately after `test_a_skipped_stretch_restarts_at_the_phase_whose_when_decided_the_skip` (after line 502) and before `test_a_run_that_only_lost_its_last_phase_restarts_there_and_not_at_plan_check`:

```python
def test_a_run_that_lost_everything_after_the_first_phase_restarts_at_explore():
    """The first phase of the document is `worktree` (R6), so the cheapest real
    crash there is -- the process dying right after the worktree was created --
    must restart at `explore` and never re-create the worktree."""
    workflow = _task_workflow()
    assert workflow.phase_names[0] == "worktree"

    subtask = _pure_subtask("card-1", [_recorded("worktree", "done")])

    assert cli.interrupted_phase(subtask, workflow) == "explore"
```

- [ ] **Step 3: Write the failing resume-skips-the-worktree engine test (engine tier)**

In `tests/test_engine.py`, insert this immediately after the `_walk_builtin` helper (after line 1631) and before `test_every_document_path_input_renders_the_expanded_writes_template`:

```python
def test_a_resume_started_at_explore_never_re_runs_the_worktree_phase(store):
    """R6 moved `worktree` in front of `explore`, which makes it the one phase a
    resume can legitimately be *behind*. `worktree.ensure` is idempotent, but a
    walk started at `explore` must not call it at all -- the phase is done."""
    calls: list[str] = []
    workflow = load_builtin("task", _registry(_builtin_functions(calls, validated=False)))

    def agent_runner(phase, context, rendered):
        calls.append(f"agent:{phase.name}")
        return {"role": phase.role}

    summary = engine.run_subtask(
        workflow,
        store,
        story_id=STORY_ID,
        subtask=_subtask(),
        repo_dir=REPO,
        commands=["uv run pytest"],
        card=CARD,
        parent_story=PARENT,
        agent_runner=agent_runner,
        start_phase="explore",
    )

    assert "worktree.ensure" not in calls
    assert calls[0] == "agent:explore"  # the recording runner above appends it
    assert summary.status == "done"
    assert [name for name, _status in _projected_phases(store)] == [
        "mark_in_progress",
        "plan_check",
        "verify",
        "mark_done",
    ]
```

- [ ] **Step 4: Run the three new tests to verify they fail**

Run:

```bash
uv run pytest \
  tests/workflow/test_builtin_task.py::test_no_agent_phase_precedes_the_worktree_phase \
  tests/test_cli.py::test_a_run_that_lost_everything_after_the_first_phase_restarts_at_explore \
  tests/test_engine.py::test_a_resume_started_at_explore_never_re_runs_the_worktree_phase \
  -v
```

Expected: 3 failed.
- the guard fails on `assert names.index("worktree") < min(agent_indexes)` (`2 < 0` is false — `explore` is index 0 today),
- the CLI test fails on `assert workflow.phase_names[0] == "worktree"` (`'explore' == 'worktree'`),
- the engine test fails on `assert "worktree.ensure" not in calls` (the old order still runs it third).

- [ ] **Step 5: Reorder the shipped document**

Replace the whole of `src/agent_manager/workflow/builtin/task.yaml` with exactly this (phase bodies byte-identical to today's, only the first three blocks move):

```yaml
name: task
description: Drive one subtask card end to end in its own worktree.

phases:
  - name: worktree
    kind: deterministic
    run: worktree.ensure

  - name: explore
    kind: agent
    role: explorer
    inputs: [card, parent_story, repo_docs, verification]
    result: ExploreResult
    gates: [exploration_output_gate, verification_gate]
    retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }

  - name: mark_in_progress
    kind: deterministic
    run: rollup.set_status
    args: { status: in_progress }
    best_effort: true

  - name: plan_check
    kind: deterministic
    run: plan_check.find_validated_plan
    skip_to: implement
    when: plan_check.has_validated_plan

  - name: spec
    kind: agent
    role: spec_author
    inputs: [card, explore]
    writes: docs/superpowers/specs/{stem}.md

  - name: validate_spec
    kind: agent
    role: critic
    inputs: [card, spec_path]
    result: CriticResult
    gates: [critic_blockers_gate]

  - name: plan
    kind: agent
    role: planner
    inputs: [spec_path]
    result: PlanResult
    writes: docs/superpowers/plans/{stem}.md

  - name: validate_plan
    kind: agent
    role: critic
    inputs: [spec_path, plan_path]
    result: CriticResult
    gates: [critic_blockers_gate]

  - name: implement
    kind: agent
    role: coder
    inputs: [plan_path, spec_path, branch, base_branch]
    result: ImplementResult

  - name: review
    kind: agent
    role: reviewer
    inputs: [branch, base_branch, plan_path]
    result: ReviewResult
    gates: [review_gate, plan_hash_gate]

  - name: verify
    kind: deterministic
    run: verify.run_suite
    gates: [verification_passed_gate]

  - name: mark_done
    kind: deterministic
    run: rollup.set_status
    args: { status: done }
    best_effort: true
```

- [ ] **Step 6: Run the three new tests to verify they pass**

Run:

```bash
uv run pytest \
  tests/workflow/test_builtin_task.py::test_no_agent_phase_precedes_the_worktree_phase \
  tests/test_cli.py::test_a_run_that_lost_everything_after_the_first_phase_restarts_at_explore \
  tests/test_engine.py::test_a_resume_started_at_explore_never_re_runs_the_worktree_phase \
  -v
```

Expected: 3 passed.

- [ ] **Step 7: Run the full suite to see exactly which pinned-order tests the reorder broke**

Run: `uv run pytest`

Expected: FAIL, in these five places and nowhere else —
`tests/workflow/test_builtin_task.py::test_builtin_task_has_the_twelve_phases_in_spec_order`,
`tests/test_cli.py::test_the_interrupted_phase_is_the_one_recorded_started`,
`tests/test_cli.py::test_a_crash_between_phases_restarts_at_the_first_phase_not_done`,
`tests/test_cli.py::test_a_skipped_stretch_restarts_at_the_phase_whose_when_decided_the_skip`,
`tests/test_engine.py::test_the_builtin_task_document_walks_against_a_fake_registry`.
(`tests/test_cli.py::test_a_restart_at_plan_check_that_finds_no_plan_is_an_engine_error_not_a_traceback` also fails when git and brd are on this machine; it is skipped by `requires_git`/`requires_brd` otherwise. Fix it in Step 10 either way.) If anything *else* fails, stop and read it before editing — it means the reorder touched behaviour this plan did not predict.

- [ ] **Step 8: Re-pin `EXPECTED_PHASES`**

In `tests/workflow/test_builtin_task.py`, replace lines 41-55 with:

```python
# Design spec lines 146-225, in file order.
EXPECTED_PHASES = (
    ("worktree", "deterministic"),
    ("explore", "agent"),
    ("mark_in_progress", "deterministic"),
    ("plan_check", "deterministic"),
    ("spec", "agent"),
    ("validate_spec", "agent"),
    ("plan", "agent"),
    ("validate_plan", "agent"),
    ("implement", "agent"),
    ("review", "agent"),
    ("verify", "deterministic"),
    ("mark_done", "deterministic"),
)
```

(The `# Design spec lines 146-225` comment stays correct: Task 2 reorders the same three blocks inside the design-doc code fence, and the three blocks have the same total line count, so the fence still spans 146-225.)

- [ ] **Step 9: Re-pin the three `interrupted_phase` recorded lists**

In `tests/test_cli.py`, in `test_the_interrupted_phase_is_the_one_recorded_started`, replace the list passed to `_pure_subtask` (lines 456-466) with:

```python
        [
            _recorded("worktree", "done"),
            _recorded("explore", "done", [_pure_attempt(1)]),
            _recorded("mark_in_progress", "done"),
            _recorded("plan_check", "done"),
            _recorded("spec", "done", [_pure_attempt(1)]),
            _recorded("validate_spec", "done", [_pure_attempt(1)]),
            _recorded("plan", "done", [_pure_attempt(1)]),
            _recorded("validate_plan", "done", [_pure_attempt(1)]),
            _recorded("implement", "started", [_pure_attempt(1, status="started")]),
        ],
```

In `test_a_crash_between_phases_restarts_at_the_first_phase_not_done`, replace the list (lines 477-481) with:

```python
        [
            _recorded("worktree", "done"),
            _recorded("explore", "done", [_pure_attempt(1)]),
            _recorded("mark_in_progress", "done"),
        ],
```

In `test_a_skipped_stretch_restarts_at_the_phase_whose_when_decided_the_skip`, replace the list (lines 494-499) with:

```python
        [
            _recorded("worktree", "done"),
            _recorded("explore", "done", [_pure_attempt(1)]),
            _recorded("mark_in_progress", "done"),
            _recorded("plan_check", "done"),
        ],
```

The three assertions (`== "implement"`, `== "plan_check"`, `== "plan_check"`) are unchanged. `AGENT_PHASE_NAMES` at line 425 is a `frozenset`, not a sequence — do not touch it.

- [ ] **Step 10: Re-pin the recorded `done=` tuple in the restart-at-`plan_check` test**

In `tests/test_cli.py`, replace line 2700:

```python
        done=("explore", "mark_in_progress", "worktree", "plan_check"),
```

with:

```python
        done=("worktree", "explore", "mark_in_progress", "plan_check"),
```

The rest of `test_a_restart_at_plan_check_that_finds_no_plan_is_an_engine_error_not_a_traceback` is unchanged: `assert "explore" in str(caught.value)` still holds, because restarting at `plan_check` with no validated plan still falls into `spec`, whose `explore` input nothing supplies.

- [ ] **Step 11: Re-pin the fake-registry walk**

In `tests/test_engine.py`, in `test_the_builtin_task_document_walks_against_a_fake_registry`, replace the `calls` assertion (lines 1183-1197) with:

```python
    assert calls == [
        "worktree.ensure",
        "agent:explore",
        "rollup.set_status:in_progress",
        "plan_check.find_validated_plan",
        "agent:spec",
        "agent:validate_spec",
        "agent:plan",
        "agent:validate_plan",
        "agent:implement",
        "agent:review",
        "verify.run_suite",
        "verification_passed_gate",
        "rollup.set_status:done",
    ]
```

and the projected-phase assertion (lines 1200-1206) with:

```python
    assert [name for name, _status in _projected_phases(store)] == [
        "worktree",
        "mark_in_progress",
        "plan_check",
        "verify",
        "mark_done",
    ]
```

`assert summary.status == "done"` and `assert summary.warnings == []` between them are unchanged.

- [ ] **Step 12: Run the full suite**

Run: `uv run pytest`

Expected: PASS (no failures; `requires_git`/`requires_brd` skips are acceptable and unchanged in number from before this task).

- [ ] **Step 13: Commit**

```bash
git add src/agent_manager/workflow/builtin/task.yaml tests/workflow/test_builtin_task.py tests/test_cli.py tests/test_engine.py
git commit -m "fix: run worktree.ensure before any agent phase in builtin/task.yaml"
```

---

### Task 2: Resync the byte-for-byte YAML copy in the design spec

**Files:**
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:146-225` (the `task` YAML code fence in section 5)

**Interfaces:**
- Consumes: the reordered `src/agent_manager/workflow/builtin/task.yaml` from Task 1.
- Produces: nothing importable. `workflow/loader.py:38` documents that `task.yaml` ships byte-for-byte from this block, and `tests/workflow/test_builtin_task.py:41` and `tests/workflow/test_registry.py:79` cite "design spec lines 146-225"; this task keeps all three statements true.

- [ ] **Step 1: Confirm the copy is currently out of sync**

Run:

```bash
diff <(sed -n '147,224p' docs/superpowers/specs/2026-09-23-agent-manager-design.md) src/agent_manager/workflow/builtin/task.yaml
```

Expected: a diff showing the `worktree` / `explore` / `mark_in_progress` blocks in different positions (the spec copy still opens with `explore`). Note the surrounding fence: line 146 is ```` ```yaml ```` and line 225 is ```` ``` ````, so lines 147-224 are the document body.

- [ ] **Step 2: Reorder the design-spec copy**

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, replace lines 151-167 (the `explore`, `mark_in_progress` and `worktree` blocks, in that order, with the blank lines between them) with:

```yaml
  - name: worktree
    kind: deterministic
    run: worktree.ensure

  - name: explore
    kind: agent
    role: explorer
    inputs: [card, parent_story, repo_docs, verification]
    result: ExploreResult
    gates: [exploration_output_gate, verification_gate]
    retry: { max_attempts: 2, on: [schema_invalid, gate_failed] }

  - name: mark_in_progress
    kind: deterministic
    run: rollup.set_status
    args: { status: in_progress }
    best_effort: true
```

Nothing else in the fence changes: `plan_check` stays at line 169 and `mark_done` still ends at line 224, because the three moved blocks occupy the same seventeen lines they did before. That is why the "lines 146-225" comments in `tests/workflow/test_builtin_task.py:41` and `tests/workflow/test_registry.py:79` need no edit.

- [ ] **Step 3: Verify the copy is byte-for-byte again**

Run:

```bash
diff <(sed -n '147,224p' docs/superpowers/specs/2026-09-23-agent-manager-design.md) src/agent_manager/workflow/builtin/task.yaml && echo IDENTICAL
```

Expected: no diff output, then `IDENTICAL`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`

Expected: PASS. (Nothing loads the design doc at runtime; this run confirms the doc edit did not accidentally touch a source or test file.)

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-09-23-agent-manager-design.md
git commit -m "docs: resync the design-spec task.yaml copy with the reordered document"
```
