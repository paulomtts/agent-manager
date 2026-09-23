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
